# -*- coding: utf-8 -*-
"""版本规划管理插件 · 路由实现

业务链路：云文档原始登记 → 需求过滤 → 版本规划 → 冻结/开发/测试 → 发布 → 客户回告。
硬约束 R1~R7 全部在服务端校验，模板只做展示。
"""
import csv
import difflib
import io
import json
import re
from datetime import date, datetime, timedelta

from flask import (Response, flash, jsonify, redirect, render_template,
                   request, session, url_for)

from plugin_system import create_plugin_table, get_db, log_operation
from plugins.base import make_blueprint

blueprint = make_blueprint('version_plan', __name__)

PID = 'version_plan'
# 逻辑表名（真实表名由平台分配，统一走 _T 缓存）
L_DEMANDS = 'version_plan_demands'
L_VERSIONS = 'version_plan_versions'
L_CUSTOMERS = 'version_plan_customers'
L_RELEASES = 'version_plan_releases'
L_LOGS = 'version_plan_logs'
L_META = 'version_plan_meta'

_T = {}

DEMAND_STATUS = ['待过滤', '规划池', '挂起', '已排期', '已发布', '不做']
FILTER_RESULTS = ['进规划池', '挂起', '不做']
VERSION_STATUS = ['规划中', '已冻结', '开发中', '测试中', '已发布', '已归档']
VERSION_TYPE = ['迭代', '热修复', '客户定制']
DEMAND_TYPE = ['新增功能', '功能优化', '缺陷修复']
VERSION_FLOW = {
    '规划中': ['已冻结'],
    '已冻结': ['开发中'],
    '开发中': ['测试中'],
    '测试中': ['已发布'],
    '已发布': ['已归档'],
    '已归档': [],
}

DDL = [
    (L_CUSTOMERS, L_CUSTOMERS, '''CREATE TABLE IF NOT EXISTS version_plan_customers (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL UNIQUE,
        current_version_no TEXT DEFAULT '',
        integrating_version_no TEXT DEFAULT '',
        custom_branch_no TEXT DEFAULT '',
        merge_target_no TEXT DEFAULT '',
        merge_deadline TEXT DEFAULT '',
        contact TEXT DEFAULT '',
        note TEXT DEFAULT '',
        created_at TEXT,
        updated_at TEXT)'''),
    (L_VERSIONS, L_VERSIONS, '''CREATE TABLE IF NOT EXISTS version_plan_versions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        version_no TEXT NOT NULL UNIQUE,
        vtype TEXT DEFAULT '迭代',
        base_version_no TEXT DEFAULT '',
        freeze_date TEXT DEFAULT '',
        test_date TEXT DEFAULT '',
        release_date TEXT DEFAULT '',
        modules TEXT DEFAULT '',
        interface_change TEXT DEFAULT '',
        regression_scope TEXT DEFAULT '',
        known_issues TEXT DEFAULT '',
        target_customers TEXT DEFAULT '',
        upgrade_path TEXT DEFAULT '',
        merge_target_no TEXT DEFAULT '',
        merge_deadline TEXT DEFAULT '',
        status TEXT DEFAULT '规划中',
        frozen_at TEXT DEFAULT '',
        frozen_by TEXT DEFAULT '',
        unfreeze_log TEXT DEFAULT '',
        released_at TEXT DEFAULT '',
        created_at TEXT,
        updated_at TEXT)'''),
    (L_DEMANDS, L_DEMANDS, '''CREATE TABLE IF NOT EXISTS version_plan_demands (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        code TEXT NOT NULL UNIQUE,
        doc_ref TEXT NOT NULL,
        raised_at TEXT DEFAULT '',
        customer_name TEXT DEFAULT '',
        description TEXT NOT NULL,
        business_value TEXT DEFAULT '',
        acceptance TEXT DEFAULT '',
        demand_type TEXT DEFAULT '新增功能',
        filter_result TEXT DEFAULT '',
        filter_note TEXT DEFAULT '',
        version_no TEXT DEFAULT '',
        is_custom INTEGER DEFAULT 0,
        custom_customer TEXT DEFAULT '',
        related_codes TEXT DEFAULT '',
        relation_type TEXT DEFAULT '',
        status TEXT DEFAULT '待过滤',
        note TEXT DEFAULT '',
        multi_customer TEXT DEFAULT '',
        filtered_by TEXT DEFAULT '',
        filtered_at TEXT DEFAULT '',
        created_at TEXT,
        updated_at TEXT)'''),
    (L_RELEASES, L_RELEASES, '''CREATE TABLE IF NOT EXISTS version_plan_releases (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        version_no TEXT NOT NULL UNIQUE,
        content TEXT DEFAULT '',
        status TEXT DEFAULT '草稿',
        created_at TEXT,
        updated_at TEXT,
        finalized_by TEXT DEFAULT '')'''),
    (L_LOGS, L_LOGS, '''CREATE TABLE IF NOT EXISTS version_plan_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        obj_type TEXT DEFAULT '',
        obj_no TEXT DEFAULT '',
        action TEXT DEFAULT '',
        detail TEXT DEFAULT '',
        operator TEXT DEFAULT '',
        created_at TEXT)'''),
    (L_META, L_META, '''CREATE TABLE IF NOT EXISTS version_plan_meta (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        kind TEXT DEFAULT '',
        value TEXT DEFAULT '',
        created_at TEXT)'''),
]


def _now():
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def _today():
    return date.today().strftime('%Y-%m-%d')


def _ensure_tables():
    """惰性建表（必须在请求上下文内调用，幂等）。同时把真实表名缓存下来。"""
    for logical, desired, ddl in DDL:
        if logical in _T:
            continue
        _T[logical] = create_plugin_table(PID, desired, ddl)
    try:
        _migrate()
    except Exception:
        pass  # 迁移失败不阻断页面，下一轮建表会重试


def _migrate(db=None):
    """老库补列（插件自管迁移）：版本表新增定制版合回目标/期限。"""
    db = db or get_db()
    cols = [r[1] for r in db.execute(f'PRAGMA table_info({_t(L_VERSIONS)})').fetchall()]
    for col in ('merge_target_no', 'merge_deadline'):
        if col not in cols:
            db.execute(f'ALTER TABLE {_t(L_VERSIONS)} ADD COLUMN {col} TEXT DEFAULT ""')
    db.commit()


def _t(logical):
    if logical not in _T:
        _ensure_tables()
    return _T.get(logical, logical)


def _can_manage():
    return session.get('user_role') in ('platform_admin', 'admin')


def _is_platform():
    return session.get('user_role') == 'platform_admin'


def _deny():
    flash('无权限：该操作仅管理员可执行', 'error')
    return redirect(url_for('version_plan.index'))


def _ulog(obj_type, obj_no, action, detail=''):
    try:
        db = get_db()
        db.execute(f'INSERT INTO {_t(L_LOGS)} (obj_type, obj_no, action, detail, operator, created_at) '
                   'VALUES (?,?,?,?,?,?)',
                   (obj_type, obj_no, action, detail, session.get('username', ''), _now()))
        db.commit()
    except Exception:
        pass  # 留痕失败不影响主流程


def _log(action, detail):
    try:
        log_operation('版本规划', action, detail, session.get('username', '系统'))
    except Exception:
        pass


def _next_code(db):
    rows = db.execute(f'SELECT code FROM {_t(L_DEMANDS)}').fetchall()
    mx = 0
    for r in rows:
        m = re.match(r'R(\d+)', r['code'] or '')
        if m:
            mx = max(mx, int(m.group(1)))
    return 'R%03d' % (mx + 1)


def _norm(s):
    return re.sub(r'[^\w\u4e00-\u9fff]+', '', (s or '')).lower()


def _similar(db, desc, exclude_id=None, limit=300):
    """描述相似度检测（疑似重复/同源）。返回 [(code, description, score, customer)]"""
    base = _norm(desc)
    if not base or len(base) < 6:
        return []
    rows = db.execute(f'SELECT id, code, description, customer_name FROM {_t(L_DEMANDS)} '
                      'ORDER BY id DESC LIMIT ?', (limit,)).fetchall()
    out = []
    for r in rows:
        if exclude_id and r['id'] == exclude_id:
            continue
        other = _norm(r['description'])
        if not other:
            continue
        if abs(len(other) - len(base)) > max(len(base), len(other)) * 0.6:
            continue
        ratio = difflib.SequenceMatcher(None, base, other).quick_ratio()
        if ratio >= 0.8:
            out.append((r['code'], r['description'], round(ratio, 2), r['customer_name'] or ''))
    out.sort(key=lambda x: -x[2])
    return out[:5]


# ---------------------------------------------------------------- 工作台
@blueprint.route('/version_plan/')
def index():
    _ensure_tables()
    db = get_db()
    stat = {}
    for st in DEMAND_STATUS:
        stat[st] = db.execute(f'SELECT COUNT(*) c FROM {_t(L_DEMANDS)} WHERE status=?', (st,)).fetchone()['c']
    total = db.execute(f'SELECT COUNT(*) c FROM {_t(L_DEMANDS)}').fetchone()['c']
    vstat = {}
    for st in VERSION_STATUS:
        vstat[st] = db.execute(f'SELECT COUNT(*) c FROM {_t(L_VERSIONS)} WHERE status=?', (st,)).fetchone()['c']
    # 本期过滤率 = 已判定（非待过滤）占比
    judged = total - stat.get('待过滤', 0)
    rate = round(judged * 100.0 / total, 1) if total else 0.0
    # 定制分支超期
    overdue_branches = [r for r in db.execute(
        f'SELECT * FROM {_t(L_CUSTOMERS)} WHERE custom_branch_no<>"" AND merge_deadline<>""').fetchall()
        if r['merge_deadline'] and r['merge_deadline'] < _today()]
    # 里程碑 7 天内
    horizon = (date.today() + timedelta(days=7)).strftime('%Y-%m-%d')
    milestones = []
    for r in db.execute(f'SELECT * FROM {_t(L_VERSIONS)} '
                        'WHERE status NOT IN ("已发布","已归档")').fetchall():
        for label, key in (('需求冻结日', 'freeze_date'), ('提测日', 'test_date'), ('发布日', 'release_date')):
            val = (r[key] or '').strip()
            if val and val <= horizon:
                milestones.append({'version_no': r['version_no'], 'label': label,
                                   'date': val, 'state': r['status']})
    milestones.sort(key=lambda x: x['date'])
    recent = db.execute(f'SELECT * FROM {_t(L_LOGS)} ORDER BY id DESC LIMIT 12').fetchall()
    versions = db.execute(f'SELECT * FROM {_t(L_VERSIONS)} ORDER BY id DESC LIMIT 6').fetchall()
    pending = db.execute(f'SELECT * FROM {_t(L_DEMANDS)} WHERE status="待过滤" '
                         'ORDER BY id DESC LIMIT 8').fetchall()
    return render_template('vp_index.html', stat=stat, total=total, rate=rate, vstat=vstat,
                           overdue_branches=overdue_branches, milestones=milestones,
                           recent=recent, versions=versions, pending=pending,
                           can_manage=_can_manage(), today=_today())


# ---------------------------------------------------------------- 需求
@blueprint.route('/version_plan/demands')
def demands():
    _ensure_tables()
    db = get_db()
    q = (request.args.get('q') or '').strip()
    st = (request.args.get('status') or '').strip()
    ver = (request.args.get('version') or '').strip()
    cust = (request.args.get('customer') or '').strip()
    try:
        page = max(1, int(request.args.get('page', 1)))
    except Exception:
        page = 1
    size = 40
    where, args = [], []
    if q:
        where.append('(code LIKE ? OR description LIKE ? OR doc_ref LIKE ? OR customer_name LIKE ?)')
        args += ['%' + q + '%'] * 4
    if st:
        where.append('status=?')
        args.append(st)
    if ver:
        where.append('version_no=?')
        args.append(ver)
    if cust:
        where.append('customer_name=?')
        args.append(cust)
    wsql = ('WHERE ' + ' AND '.join(where)) if where else ''
    total = db.execute(f'SELECT COUNT(*) c FROM {_t(L_DEMANDS)} {wsql}', args).fetchone()['c']
    rows = db.execute(f'SELECT * FROM {_t(L_DEMANDS)} {wsql} ORDER BY id DESC LIMIT ? OFFSET ?',
                      args + [size, (page - 1) * size]).fetchall()
    customers = db.execute(f'SELECT name FROM {_t(L_CUSTOMERS)} ORDER BY name').fetchall()
    versions = db.execute(f'SELECT version_no FROM {_t(L_VERSIONS)} ORDER BY id DESC').fetchall()
    counts = {k: db.execute(f'SELECT COUNT(*) c FROM {_t(L_DEMANDS)} WHERE status=?', (k,)).fetchone()['c']
              for k in DEMAND_STATUS}
    return render_template('vp_demands.html', rows=rows, total=total, page=page, size=size,
                           pages=max(1, (total + size - 1) // size), q=q, st=st, ver=ver, cust=cust,
                           customers=customers, versions=versions, counts=counts,
                           statuses=DEMAND_STATUS, dtypes=DEMAND_TYPE, can_manage=_can_manage())


@blueprint.route('/version_plan/demands/new', methods=['GET', 'POST'])
def demand_new():
    _ensure_tables()
    if not _can_manage():
        return _deny()
    db = get_db()
    if request.method == 'GET':
        customers = db.execute(f'SELECT name FROM {_t(L_CUSTOMERS)} ORDER BY name').fetchall()
        versions = db.execute(f'SELECT version_no FROM {_t(L_VERSIONS)} ORDER BY id DESC').fetchall()
        return render_template('vp_demand_form.html', d=None, customers=customers,
                               versions=versions, dtypes=DEMAND_TYPE, statuses=DEMAND_STATUS,
                               sims=[], today=_today())
    f = request.form
    doc_ref = (f.get('doc_ref') or '').strip()
    desc = (f.get('description') or '').strip()
    if not doc_ref:
        flash('云文档溯源必填：请填写原始登记的行号 / 原始编号 / 原文摘要（R6）', 'error')
        return redirect(url_for('version_plan.demand_new'))
    if not desc:
        flash('需求描述必填', 'error')
        return redirect(url_for('version_plan.demand_new'))
    code = _next_code(db)
    now = _now()
    db.execute(
        f'''INSERT INTO {_t(L_DEMANDS)}
        (code, doc_ref, raised_at, customer_name, description, business_value, acceptance, demand_type,
         filter_result, filter_note, version_no, is_custom, custom_customer, status, note,
         created_at, updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
        (code, doc_ref, (f.get('raised_at') or _today()), (f.get('customer_name') or '').strip(),
         desc, (f.get('business_value') or '').strip(), (f.get('acceptance') or '').strip(),
         (f.get('demand_type') or '新增功能'), '', '', (f.get('version_no') or ''),
         1 if f.get('is_custom') else 0, (f.get('custom_customer') or '').strip(),
         '待过滤', (f.get('note') or '').strip(), now, now))
    db.commit()
    sims = _similar(db, desc)
    _log('新增需求', f'{code} {desc[:30]}')
    tip = f'已录入需求 {code}，当前状态「待过滤」。'
    if sims:
        tip += ' 疑似重复：' + '、'.join(f'{s[0]}({s[2]})' for s in sims) + '，请在过滤时确认。'
    flash(tip, 'warning' if sims else 'success')
    return redirect(url_for('version_plan.demands', status='待过滤'))


@blueprint.route('/version_plan/demands/<code>', methods=['GET', 'POST'])
def demand_edit(code):
    _ensure_tables()
    if not _can_manage():
        return _deny()
    db = get_db()
    d = db.execute(f'SELECT * FROM {_t(L_DEMANDS)} WHERE code=?', (code,)).fetchone()
    if not d:
        flash('需求不存在', 'error')
        return redirect(url_for('version_plan.demands'))
    if request.method == 'GET':
        customers = db.execute(f'SELECT name FROM {_t(L_CUSTOMERS)} ORDER BY name').fetchall()
        versions = db.execute(f'SELECT version_no, status FROM {_t(L_VERSIONS)} ORDER BY id DESC').fetchall()
        logs = db.execute(f'SELECT * FROM {_t(L_LOGS)} WHERE obj_no=? ORDER BY id DESC LIMIT 20',
                          (code,)).fetchall()
        return render_template('vp_demand_form.html', d=d, customers=customers, versions=versions,
                               dtypes=DEMAND_TYPE, statuses=DEMAND_STATUS,
                               sims=_similar(db, d['description'], exclude_id=d['id']),
                               logs=logs, today=_today())
    f = request.form
    doc_ref = (f.get('doc_ref') or '').strip()
    if not doc_ref:
        flash('云文档溯源不可清空（R6）', 'error')
        return redirect(url_for('version_plan.demand_edit', code=code))
    # 状态推进需按规则校验（R3：已发布必须其版本已发布）
    new_status = (f.get('status') or d['status']).strip()
    if new_status == '已发布' and d['status'] != '已发布':
        ver = db.execute(f'SELECT status FROM {_t(L_VERSIONS)} WHERE version_no=?',
                         (d['version_no'],)).fetchone()
        if not ver or ver['status'] != '已发布':
            flash('不能把需求标为「已发布」：它排入的版本还不是「已发布」状态（R3）', 'error')
            return redirect(url_for('version_plan.demand_edit', code=code))
    db.execute(
        f'''UPDATE {_t(L_DEMANDS)} SET doc_ref=?, raised_at=?, customer_name=?, description=?,
        business_value=?, acceptance=?, demand_type=?, is_custom=?, custom_customer=?, note=?, updated_at=?
        WHERE code=?''',
        (doc_ref, (f.get('raised_at') or '').strip(), (f.get('customer_name') or '').strip(),
         (f.get('description') or '').strip(), (f.get('business_value') or '').strip(),
         (f.get('acceptance') or '').strip(), (f.get('demand_type') or '新增功能'),
         1 if f.get('is_custom') else 0, (f.get('custom_customer') or '').strip(),
         (f.get('note') or '').strip(), _now(), code))
    db.commit()
    _ulog('需求', code, '编辑', '更新需求信息')
    flash(f'已保存 {code}', 'success')
    return redirect(url_for('version_plan.demand_edit', code=code))


@blueprint.route('/version_plan/demands/<code>/filter', methods=['POST'])
def demand_filter(code):
    _ensure_tables()
    if not _can_manage():
        return _deny()
    db = get_db()
    d = db.execute(f'SELECT * FROM {_t(L_DEMANDS)} WHERE code=?', (code,)).fetchone()
    if not d:
        flash('需求不存在', 'error')
        return redirect(url_for('version_plan.demands'))
    if d['status'] not in ('待过滤', '挂起', '规划池'):
        flash(f'{code} 已是「{d["status"]}」状态，不能再做过滤判定', 'error')
        return redirect(url_for('version_plan.demands'))
    result = (request.form.get('filter_result') or '').strip()
    note = (request.form.get('filter_note') or '').strip()
    if result not in FILTER_RESULTS:
        flash('请选择过滤结论：进规划池 / 挂起 / 不做', 'error')
        return redirect(url_for('version_plan.demands', status='待过滤'))
    if result == '进规划池':
        missing = []
        if not (d['customer_name'] or '').strip():
            missing.append('来源客户')
        if not (d['business_value'] or '').strip():
            missing.append('业务价值')
        if not (d['acceptance'] or '').strip():
            missing.append('验收标准')
        if missing:
            flash(f'进规划池前必须补全：{"、".join(missing)}。请先点开 {code} 补齐再过滤。', 'error')
            return redirect(url_for('version_plan.demands', status='待过滤'))
        new_status = '规划池'
    elif result == '挂起':
        if not note:
            flash('挂起必须填写原因（例如"等第二家客户再提"）', 'error')
            return redirect(url_for('version_plan.demands', status='待过滤'))
        new_status = '挂起'
    else:
        if not note:
            flash('不做必须填写原因', 'error')
            return redirect(url_for('version_plan.demands', status='待过滤'))
        new_status = '不做'
    db.execute(f'''UPDATE {_t(L_DEMANDS)} SET filter_result=?, filter_note=?, status=?,
                filtered_by=?, filtered_at=?, updated_at=? WHERE code=?''',
               (result, note, new_status, session.get('username', ''), _now(), _now(), code))
    db.commit()
    _ulog('需求', code, f'过滤→{result}', note)
    _log('需求过滤', f'{code} → {result}')
    # 挂起复活提示：同源需求被多个客户提出
    tips = []
    if new_status in ('规划池', '挂起'):
        sims = _similar(db, d['description'], exclude_id=d['id'])
        for s in sims:
            if s[1] and s[3] and s[3] != (d['customer_name'] or ''):
                tips.append(f'与 {s[0]}（客户 {s[3]}）描述相似度 {s[2]}，属多个客户提出，建议重新评估优先级')
    flash(f'{code} 已判定为「{result}」。' + (' '.join(tips) if tips else ''), 'success')
    return redirect(url_for('version_plan.demands', status='待过滤'))


@blueprint.route('/version_plan/demands/<code>/link', methods=['POST'])
def demand_link(code):
    _ensure_tables()
    if not _can_manage():
        return _deny()
    db = get_db()
    other = (request.form.get('other_code') or '').strip().upper()
    rtype = (request.form.get('relation_type') or '重复').strip()
    if rtype not in ('重复', '冲突'):
        rtype = '重复'
    if not other or other == code:
        flash('请填写另一条需求的编号（不能是自己）', 'error')
        return redirect(url_for('version_plan.demand_edit', code=code))
    o = db.execute(f'SELECT * FROM {_t(L_DEMANDS)} WHERE code=?', (other,)).fetchone()
    if not o:
        flash(f'需求 {other} 不存在', 'error')
        return redirect(url_for('version_plan.demand_edit', code=code))
    d = db.execute(f'SELECT * FROM {_t(L_DEMANDS)} WHERE code=?', (code,)).fetchone()
    cur = [x for x in (d['related_codes'] or '').split(',') if x]
    if other not in cur:
        cur.append(other)
    db.execute(f'UPDATE {_t(L_DEMANDS)} SET related_codes=?, relation_type=?, updated_at=? WHERE code=?',
               (','.join(cur), rtype, _now(), code))
    oc = [x for x in (o['related_codes'] or '').split(',') if x]
    if code not in oc:
        oc.append(code)
    db.execute(f'UPDATE {_t(L_DEMANDS)} SET related_codes=?, relation_type=?, updated_at=? WHERE code=?',
               (','.join(oc), rtype, _now(), other))
    db.commit()
    _ulog('需求', code, f'标记{rtype}', f'与 {other}')
    flash(f'已标记 {code} 与 {other} 为「{rtype}」关系（双向关联）', 'success')
    return redirect(url_for('version_plan.demand_edit', code=code))


@blueprint.route('/version_plan/demands/import', methods=['POST'])
def demand_import():
    _ensure_tables()
    if not _can_manage():
        return _deny()
    db = get_db()
    f = request.files.get('file')
    if not f or not f.filename:
        flash('请选择要导入的 CSV / Excel 导出的 CSV 文件', 'error')
        return redirect(url_for('version_plan.demands'))
    raw = f.read()
    text = None
    for enc in ('utf-8-sig', 'utf-8', 'gbk', 'gb18030'):
        try:
            text = raw.decode(enc)
            break
        except Exception:
            continue
    if text is None:
        flash('文件编码无法识别，请用 Excel 另存为「CSV UTF-8」再导入', 'error')
        return redirect(url_for('version_plan.demands'))
    rows = list(csv.DictReader(io.StringIO(text)))
    if not rows:
        flash('文件里没有数据行', 'error')
        return redirect(url_for('version_plan.demands'))
    alias = {
        'doc_ref': ['云文档溯源', '溯源', '原始位置', '原始编号', 'doc_ref'],
        'raised_at': ['提出日期', '登记日期', 'raised_at'],
        'customer_name': ['来源客户', '客户', '客户名称', 'customer_name'],
        'description': ['需求描述', '描述', '需求', 'description'],
        'business_value': ['业务价值', '价值', 'business_value'],
        'acceptance': ['验收标准', 'acceptance'],
        'demand_type': ['需求类型', '类型', 'demand_type'],
        'is_custom': ['是否定制', '定制', 'is_custom'],
    }

    def pick(row, key):
        for a in alias[key]:
            if a in row and (row[a] or '').strip():
                return row[a].strip()
        return ''

    ok, bad, dup, no_ref = 0, 0, [], 0
    now = _now()
    for row in rows:
        if not any((v or '').strip() for v in row.values()):
            continue
        doc_ref = pick(row, 'doc_ref')
        desc = pick(row, 'description')
        if not desc:
            bad += 1
            continue
        if not doc_ref:
            no_ref += 1
            doc_ref = '导入时未填（R6 要求补全后过滤）'
        code = _next_code(db)
        sims = _similar(db, desc)
        db.execute(
            f'''INSERT INTO {_t(L_DEMANDS)}
            (code, doc_ref, raised_at, customer_name, description, business_value, acceptance,
             demand_type, status, is_custom, note, created_at, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)''',
            (code, doc_ref, pick(row, 'raised_at') or _today(), pick(row, 'customer_name'),
             desc, pick(row, 'business_value'), pick(row, 'acceptance'),
             pick(row, 'demand_type') or '新增功能', '待过滤',
             1 if pick(row, 'is_custom') in ('是', '1', 'Y', 'y', 'true') else 0,
             'CSV 导入', now, now))
        ok += 1
        if sims:
            dup.append(f'{code}≈{sims[0][0]}')
    db.commit()
    msg = f'导入完成：新增 {ok} 条待过滤需求'
    if bad:
        msg += f'，{bad} 行因缺「需求描述」被跳过'
    if no_ref:
        msg += f'，{no_ref} 条没填云文档溯源（过滤前必须补全，否则无法进规划池）'
    if dup:
        msg += f'，疑似重复 {len(dup)} 条：' + '、'.join(dup[:8])
    _log('需求导入', f'新增 {ok} 条')
    flash(msg, 'warning' if (no_ref or dup) else 'success')
    return redirect(url_for('version_plan.demands', status='待过滤'))


@blueprint.route('/version_plan/demands/export')
def demand_export():
    _ensure_tables()
    db = get_db()
    rows = db.execute(f'SELECT * FROM {_t(L_DEMANDS)} ORDER BY id').fetchall()
    out = io.StringIO()
    w = csv.writer(out)
    head = ['需求编号', '云文档溯源', '提出日期', '来源客户', '需求描述', '业务价值', '验收标准',
            '需求类型', '过滤结论', '过滤备注', '排入版本', '是否定制', '定制客户',
            '关联需求', '关系', '状态', '备注']
    w.writerow(head)
    for r in rows:
        w.writerow([r['code'], r['doc_ref'], r['raised_at'], r['customer_name'], r['description'],
                    r['business_value'], r['acceptance'], r['demand_type'], r['filter_result'],
                    r['filter_note'], r['version_no'], '是' if r['is_custom'] else '否',
                    r['custom_customer'], r['related_codes'], r['relation_type'], r['status'], r['note']])
    csv_text = out.getvalue()
    return Response(csv_text.encode('utf-8-sig'), mimetype='text/csv',
                    headers={'Content-Disposition': 'attachment; filename=version_plan_demands.csv'})


# ---------------------------------------------------------------- 版本
@blueprint.route('/version_plan/versions')
def versions():
    _ensure_tables()
    db = get_db()
    rows = db.execute(f'SELECT * FROM {_t(L_VERSIONS)} ORDER BY id DESC').fetchall()
    counts = {}
    for r in rows:
        counts[r['version_no']] = db.execute(
            f'SELECT COUNT(*) c FROM {_t(L_DEMANDS)} WHERE version_no=?', (r['version_no'],)).fetchone()['c']
    pool = db.execute(f'SELECT * FROM {_t(L_DEMANDS)} WHERE status="规划池" ORDER BY id').fetchall()
    customers = db.execute(f'SELECT name, integrating_version_no FROM {_t(L_CUSTOMERS)} ORDER BY name').fetchall()
    return render_template('vp_versions.html', rows=rows, counts=counts, pool=pool,
                           customers=customers, statuses=VERSION_STATUS, vtypes=VERSION_TYPE,
                           can_manage=_can_manage(), today=_today(), modules=_modules())


@blueprint.route('/version_plan/versions/new', methods=['POST'])
def version_new():
    _ensure_tables()
    if not _can_manage():
        return _deny()
    db = get_db()
    f = request.form
    no = (f.get('version_no') or '').strip()
    vtype = (f.get('vtype') or '迭代').strip()
    if not re.fullmatch(r'[0-9]+(?:\.[0-9A-Za-z]+){2,}(?:-[0-9A-Za-z._]+)?', no):
        flash(f'版本号「{no}」格式不对：请用 主.次.修订（如 1.2.0），定制版可加客户后缀（如 1.2.0-A）', 'error')
        return redirect(url_for('version_plan.versions'))
    exists = db.execute(f'SELECT 1 FROM {_t(L_VERSIONS)} WHERE version_no=?', (no,)).fetchone()
    if exists:
        flash(f'版本号 {no} 已存在（版本号全库唯一且创建后不可改，R4）', 'error')
        return redirect(url_for('version_plan.versions'))
    # R2：定制版本必须填合回目标与期限
    if vtype == '客户定制':
        if not (f.get('merge_target_no') or '').strip() or not (f.get('merge_deadline') or '').strip():
            flash('客户定制版本必须填写「计划合回主线的目标版本」和「合回期限」才能创建（R2）', 'error')
            return redirect(url_for('version_plan.versions'))
    now = _now()
    db.execute(
        f'''INSERT INTO {_t(L_VERSIONS)}
        (version_no, vtype, base_version_no, freeze_date, test_date, release_date, modules,
         interface_change, regression_scope, known_issues, target_customers, upgrade_path,
         merge_target_no, merge_deadline, status, created_at, updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
        (no, vtype, (f.get('base_version_no') or '').strip(), (f.get('freeze_date') or '').strip(),
         (f.get('test_date') or '').strip(), (f.get('release_date') or '').strip(),
         ','.join(request.form.getlist('modules')), (f.get('interface_change') or '').strip(),
         (f.get('regression_scope') or '').strip(), (f.get('known_issues') or '').strip(),
         ','.join(request.form.getlist('customers')), (f.get('upgrade_path') or '').strip(),
         (f.get('merge_target_no') or '').strip() if vtype == '客户定制' else '',
         (f.get('merge_deadline') or '').strip() if vtype == '客户定制' else '',
         '规划中', now, now))
    db.commit()
    _ulog('版本', no, '创建', f'类型 {vtype}')
    _log('创建版本', no)
    flash(f'已创建版本 {no}（规划中）。去版本详情勾选规划池需求拉进来。', 'success')
    return redirect(url_for('version_plan.versions'))


@blueprint.route('/version_plan/versions/<no>', methods=['GET', 'POST'])
def version_detail(no):
    _ensure_tables()
    db = get_db()
    v = db.execute(f'SELECT * FROM {_t(L_VERSIONS)} WHERE version_no=?', (no,)).fetchone()
    if not v:
        flash('版本不存在', 'error')
        return redirect(url_for('version_plan.versions'))
    if request.method == 'POST':
        if not _can_manage():
            return _deny()
        f = request.form
        db.execute(
            f'''UPDATE {_t(L_VERSIONS)} SET freeze_date=?, test_date=?, release_date=?, modules=?,
            interface_change=?, regression_scope=?, known_issues=?, target_customers=?, upgrade_path=?,
            base_version_no=?, updated_at=? WHERE version_no=?''',
            ((f.get('freeze_date') or '').strip(), (f.get('test_date') or '').strip(),
             (f.get('release_date') or '').strip(), ','.join(request.form.getlist('modules')),
             (f.get('interface_change') or '').strip(), (f.get('regression_scope') or '').strip(),
             (f.get('known_issues') or '').strip(), ','.join(request.form.getlist('customers')),
             (f.get('upgrade_path') or '').strip(), (f.get('base_version_no') or '').strip(),
             _now(), no))
        db.commit()
        _ulog('版本', no, '编辑', '更新里程碑/元数据（版本号不可改）')
        flash(f'{no} 已保存（版本号不可修改，R4）', 'success')
        return redirect(url_for('version_plan.version_detail', no=no))
    ds = db.execute(f'SELECT * FROM {_t(L_DEMANDS)} WHERE version_no=? ORDER BY id', (no,)).fetchall()
    pool = db.execute(f'SELECT * FROM {_t(L_DEMANDS)} WHERE status="规划池" ORDER BY id').fetchall()
    customers = db.execute(f'SELECT name FROM {_t(L_CUSTOMERS)} ORDER BY name').fetchall()
    release = db.execute(f'SELECT * FROM {_t(L_RELEASES)} WHERE version_no=?', (no,)).fetchone()
    logs = db.execute(f'SELECT * FROM {_t(L_LOGS)} WHERE obj_no=? ORDER BY id DESC LIMIT 20',
                      (no,)).fetchall()
    frozen = v['status'] == '已冻结'
    can_edit = _can_manage() and v['status'] in ('规划中', '已冻结')
    return render_template('vp_version_detail.html', v=v, ds=ds, pool=pool, customers=customers,
                           release=release, logs=logs, frozen=frozen, can_edit=can_edit,
                           can_manage=_can_manage(), is_platform=_is_platform(),
                           statuses=VERSION_STATUS, flow=VERSION_FLOW.get(v['status'], []),
                           today=_today(),
                           modules=[m for m in (v['modules'] or '').split(',') if m],
                           targets=[c for c in (v['target_customers'] or '').split(',') if c])


@blueprint.route('/version_plan/versions/<no>/add', methods=['POST'])
def version_add(no):
    _ensure_tables()
    if not _can_manage():
        return _deny()
    db = get_db()
    v = db.execute(f'SELECT * FROM {_t(L_VERSIONS)} WHERE version_no=?', (no,)).fetchone()
    if not v:
        flash('版本不存在', 'error')
        return redirect(url_for('version_plan.versions'))
    # R1：冻结后不可增删
    if v['status'] == '已冻结':
        flash(f'{no} 已冻结，不能再增删需求（R1）。确需调整请由平台管理员强制解冻并留痕。', 'error')
        return redirect(url_for('version_plan.version_detail', no=no))
    if v['status'] not in ('规划中',):
        flash(f'{no} 当前状态「{v["status"]}」，只有「规划中」的版本能调整需求构成', 'error')
        return redirect(url_for('version_plan.version_detail', no=no))
    codes = [c.strip() for c in request.form.getlist('codes') if c.strip()]
    if not codes:
        flash('请先勾选要拉入的规划池需求', 'error')
        return redirect(url_for('version_plan.version_detail', no=no))
    added, blocked = [], []
    for code in codes:
        d = db.execute(f'SELECT * FROM {_t(L_DEMANDS)} WHERE code=?', (code,)).fetchone()
        if not d:
            continue
        if d['status'] != '规划池':          # R7
            blocked.append(f'{code}({d["status"]})')
            continue
        db.execute(f'UPDATE {_t(L_DEMANDS)} SET version_no=?, status="已排期", updated_at=? WHERE code=?',
                   (no, _now(), code))
        added.append(code)
    db.commit()
    msg = (f'已拉入 {len(added)} 条需求：{"、".join(added)}' if added else '没有可拉入的需求')
    if blocked:
        msg += f'；{len(blocked)} 条被阻止（只有「规划池」的需求能进版本，R7）：{"、".join(blocked)}'
    _ulog('版本', no, '拉入需求', ','.join(added))
    flash(msg, 'warning' if blocked else 'success')
    return redirect(url_for('version_plan.version_detail', no=no))


@blueprint.route('/version_plan/versions/<no>/remove', methods=['POST'])
def version_remove(no):
    _ensure_tables()
    if not _can_manage():
        return _deny()
    db = get_db()
    v = db.execute(f'SELECT * FROM {_t(L_VERSIONS)} WHERE version_no=?', (no,)).fetchone()
    if not v:
        flash('版本不存在', 'error')
        return redirect(url_for('version_plan.versions'))
    if v['status'] == '已冻结':
        flash(f'{no} 已冻结，不能再增删需求（R1）', 'error')
        return redirect(url_for('version_plan.version_detail', no=no))
    if v['status'] != '规划中':
        flash(f'{no} 当前状态「{v["status"]}」，不能调整需求构成', 'error')
        return redirect(url_for('version_plan.version_detail', no=no))
    code = (request.form.get('code') or '').strip()
    db.execute(f'UPDATE {_t(L_DEMANDS)} SET version_no="", status="规划池", updated_at=? WHERE code=?',
               (_now(), code))
    db.commit()
    _ulog('版本', no, '移出需求', code)
    flash(f'已把 {code} 移出 {no}，回到规划池', 'success')
    return redirect(url_for('version_plan.version_detail', no=no))


@blueprint.route('/version_plan/versions/<no>/status', methods=['POST'])
def version_status(no):
    _ensure_tables()
    if not _can_manage():
        return _deny()
    db = get_db()
    v = db.execute(f'SELECT * FROM {_t(L_VERSIONS)} WHERE version_no=?', (no,)).fetchone()
    if not v:
        flash('版本不存在', 'error')
        return redirect(url_for('version_plan.versions'))
    target = (request.form.get('to') or '').strip()

    # 解冻：仅平台管理员，且留痕（R1）
    if target == '规划中' and v['status'] == '已冻结':
        if not _is_platform():
            flash('解冻只能由平台管理员执行（R1）', 'error')
            return redirect(url_for('version_plan.version_detail', no=no))
        reason = (request.form.get('reason') or '').strip()
        if not reason:
            flash('强制解冻必须填写原因，便于留痕（R1）', 'error')
            return redirect(url_for('version_plan.version_detail', no=no))
        log = (v['unfreeze_log'] or '')
        log += f'[{_now()}] {session.get("username")} 强制解冻：{reason}\n'
        db.execute(f'UPDATE {_t(L_VERSIONS)} SET status="规划中", unfreeze_log=?, updated_at=? '
                   'WHERE version_no=?', (log, _now(), no))
        db.commit()
        _ulog('版本', no, '强制解冻', reason)
        _log('版本解冻', f'{no} 原因：{reason}')
        flash(f'{no} 已强制解冻，原因已记入留痕（R1）', 'warning')
        return redirect(url_for('version_plan.version_detail', no=no))

    allowed = VERSION_FLOW.get(v['status'], [])
    if target not in allowed:
        flash(f'状态不能从「{v["status"]}」直接到「{target}」；下一步只能是：{"、".join(allowed) or "无"}', 'error')
        return redirect(url_for('version_plan.version_detail', no=no))
    extra = ''
    if target == '已冻结':
        cnt = db.execute(f'SELECT COUNT(*) c FROM {_t(L_DEMANDS)} WHERE version_no=?', (no,)).fetchone()['c']
        if cnt == 0:
            flash('版本里还没有需求，不能冻结', 'error')
            return redirect(url_for('version_plan.version_detail', no=no))
        db.execute(f'UPDATE {_t(L_VERSIONS)} SET status="已冻结", frozen_at=?, frozen_by=?, updated_at=? '
                   'WHERE version_no=?', (_now(), session.get('username', ''), _now(), no))
        extra = '冻结后需求构成不可再增删（R1）。'
    elif target == '已发布':
        db.execute(f'UPDATE {_t(L_VERSIONS)} SET status="已发布", released_at=?, updated_at=? '
                   'WHERE version_no=?', (_now(), _now(), no))
        # 需求状态自动推进到已发布（R3 前置已满足：版本此刻就是已发布）
        n = db.execute(f'SELECT COUNT(*) c FROM {_t(L_DEMANDS)} WHERE version_no=?', (no,)).fetchone()['c']
        db.execute(f'UPDATE {_t(L_DEMANDS)} SET status="已发布", updated_at=? WHERE version_no=?',
                   (_now(), no))
        # 自动生成 Release Note 草稿
        draft = _build_release(db, no)
        rel = db.execute(f'SELECT * FROM {_t(L_RELEASES)} WHERE version_no=?', (no,)).fetchone()
        if rel:
            db.execute(f'UPDATE {_t(L_RELEASES)} SET content=?, updated_at=? WHERE version_no=?',
                       (draft, _now(), no))
        else:
            db.execute(f'INSERT INTO {_t(L_RELEASES)} (version_no, content, status, created_at, updated_at) '
                       'VALUES (?,?,?,?,?)', (no, draft, '草稿', _now(), _now()))
        # 客户在用版本自动更新
        for cust in [c for c in (v['target_customers'] or '').split(',') if c]:
            c0 = db.execute(f'SELECT * FROM {_t(L_CUSTOMERS)} WHERE name=?', (cust,)).fetchone()
            if c0:
                db.execute(f'UPDATE {_t(L_CUSTOMERS)} SET current_version_no=?, updated_at=? WHERE name=?',
                           (no, _now(), cust))
        extra = f'{n} 条需求已自动变为「已发布」，Release Note 草稿已生成，客户在用版本已更新。'
    else:
        db.execute(f'UPDATE {_t(L_VERSIONS)} SET status=?, updated_at=? WHERE version_no=?',
                   (target, _now(), no))
    db.commit()
    _ulog('版本', no, f'状态→{target}', extra)
    _log('版本状态', f'{no} → {target}')
    flash(f'{no} 状态已变为「{target}」。{extra}', 'success')
    return redirect(url_for('version_plan.version_detail', no=no))


def _build_release(db, no):
    """根据版本数据自动生成 Release Note 草稿"""
    v = db.execute(f'SELECT * FROM {_t(L_VERSIONS)} WHERE version_no=?', (no,)).fetchone()
    ds = db.execute(f'SELECT * FROM {_t(L_DEMANDS)} WHERE version_no=? ORDER BY id', (no,)).fetchall()
    groups = {k: [] for k in DEMAND_TYPE}
    for d in ds:
        groups.setdefault(d['demand_type'] or '新增功能', []).append(d)
    lines = [f'# {no} 版本发布说明', '']
    if v:
        lines.append(f'- 版本类型：{v["vtype"]}　版本号：{no}')
        if v['release_date']:
            lines.append(f'- 发布日：{v["release_date"]}')
        if v['target_customers']:
            lines.append(f'- 目标客户：{v["target_customers"]}')
        lines.append('')
    lines.append('## 一、新增功能')
    for d in groups.get('新增功能', []):
        who = f'（{d["customer_name"]}）' if d['customer_name'] else ''
        lines.append(f'- [{d["code"]}] {d["description"]}{who}')
    if not groups.get('新增功能'):
        lines.append('- 无')
    lines += ['', '## 二、功能优化']
    for d in groups.get('功能优化', []):
        who = f'（{d["customer_name"]}）' if d['customer_name'] else ''
        lines.append(f'- [{d["code"]}] {d["description"]}{who}')
    if not groups.get('功能优化'):
        lines.append('- 无')
    lines += ['', '## 三、修复列表']
    for d in groups.get('缺陷修复', []):
        who = f'（{d["customer_name"]}）' if d['customer_name'] else ''
        lines.append(f'- [{d["code"]}] {d["description"]}{who}')
    if not groups.get('缺陷修复'):
        lines.append('- 无')
    lines += ['', '## 四、接口变更说明',
              (v['interface_change'] if v and v['interface_change'] else '- 无'), '']
    lines += ['## 五、回归范围',
              (v['regression_scope'] if v and v['regression_scope'] else '- 按版本回归清单执行'), '']
    lines += ['## 六、已知问题及规避',
              (v['known_issues'] if v and v['known_issues'] else '- 无'), '']
    lines += ['## 七、升级与回退说明',
              (v['upgrade_path'] if v and v['upgrade_path'] else '- 按常规 FOTA 升级，可从上一版本升级'), '']
    return '\n'.join(lines)


@blueprint.route('/version_plan/versions/<no>/release', methods=['POST'])
def version_release(no):
    _ensure_tables()
    if not _can_manage():
        return _deny()
    db = get_db()
    v = db.execute(f'SELECT * FROM {_t(L_VERSIONS)} WHERE version_no=?', (no,)).fetchone()
    if not v:
        flash('版本不存在', 'error')
        return redirect(url_for('version_plan.versions'))
    act = (request.form.get('act') or 'save').strip()
    rel = db.execute(f'SELECT * FROM {_t(L_RELEASES)} WHERE version_no=?', (no,)).fetchone()
    if act == 'regen':
        if rel and rel['status'] == '定稿':
            flash('已定稿的发布说明不能被覆盖，如需重生成请先取消定稿', 'error')
            return redirect(url_for('version_plan.version_detail', no=no))
        draft = _build_release(db, no)
        if rel:
            db.execute(f'UPDATE {_t(L_RELEASES)} SET content=?, updated_at=? WHERE version_no=?',
                       (draft, _now(), no))
        else:
            db.execute(f'INSERT INTO {_t(L_RELEASES)} (version_no, content, status, created_at, updated_at) '
                       'VALUES (?,?,?,?,?)', (no, draft, '草稿', _now(), _now()))
        db.commit()
        flash('已按当前版本数据重新生成草稿', 'success')
    elif act == 'save':
        content = request.form.get('content') or ''
        if rel:
            if rel['status'] == '定稿':
                flash('已定稿，不能再改；如需修改请先「取消定稿」', 'error')
                return redirect(url_for('version_plan.version_detail', no=no))
            db.execute(f'UPDATE {_t(L_RELEASES)} SET content=?, updated_at=? WHERE version_no=?',
                       (content, _now(), no))
        else:
            db.execute(f'INSERT INTO {_t(L_RELEASES)} (version_no, content, status, created_at, updated_at) '
                       'VALUES (?,?,?,?,?)', (no, content, '草稿', _now(), _now()))
        db.commit()
        _ulog('版本', no, '保存发布说明', '草稿')
        flash('发布说明草稿已保存', 'success')
    elif act == 'finalize':
        if not rel or not (rel['content'] or '').strip():
            flash('还没有内容，不能定稿', 'error')
            return redirect(url_for('version_plan.version_detail', no=no))
        db.execute(f'UPDATE {_t(L_RELEASES)} SET status="定稿", finalized_by=?, updated_at=? '
                   'WHERE version_no=?', (session.get('username', ''), _now(), no))
        db.commit()
        _ulog('版本', no, '定稿发布说明', '')
        flash('发布说明已定稿，可导出 Markdown 发给客户', 'success')
    elif act == 'unfinalize':
        if not _is_platform() and not _can_manage():
            return _deny()
        db.execute(f'UPDATE {_t(L_RELEASES)} SET status="草稿", updated_at=? WHERE version_no=?',
                   (_now(), no))
        db.commit()
        _ulog('版本', no, '取消定稿', '')
        flash('已取消定稿，可继续编辑', 'warning')
    return redirect(url_for('version_plan.version_detail', no=no))


@blueprint.route('/version_plan/versions/<no>/release.md')
def release_md(no):
    _ensure_tables()
    db = get_db()
    rel = db.execute(f'SELECT * FROM {_t(L_RELEASES)} WHERE version_no=?', (no,)).fetchone()
    if not rel:
        flash('这个版本还没有发布说明，先在版本详情里生成草稿', 'error')
        return redirect(url_for('version_plan.version_detail', no=no))
    text = rel['content'] or ''
    return Response(text.encode('utf-8'), mimetype='text/markdown',
                    headers={'Content-Disposition': f'attachment; filename=ReleaseNote_{no}.md'})


# ---------------------------------------------------------------- 客户与矩阵
@blueprint.route('/version_plan/customers', methods=['GET', 'POST'])
def customers():
    _ensure_tables()
    db = get_db()
    if request.method == 'POST':
        if not _can_manage():
            return _deny()
        f = request.form
        name = (f.get('name') or '').strip()
        if not name:
            flash('客户名称必填', 'error')
            return redirect(url_for('version_plan.customers'))
        itg = (f.get('integrating_version_no') or '').strip()
        if itg:
            row = db.execute(f'SELECT name, integrating_version_no FROM {_t(L_CUSTOMERS)} '
                             'WHERE integrating_version_no=? AND name<>?', (itg, name)).fetchall()
            if row:
                flash(f'「{itg}」已被客户 {row[0]["name"]} 标为集成中，同一版本同时只能给一个客户做集成（R4）', 'error')
                return redirect(url_for('version_plan.customers'))
        # 定制分支必须带合回目标与期限（R2）
        cb = (f.get('custom_branch_no') or '').strip()
        if cb and (not (f.get('merge_target_no') or '').strip() or not (f.get('merge_deadline') or '').strip()):
            flash('填了定制分支版本，就必须同时填「合回目标版本」和「合回期限」（R2）', 'error')
            return redirect(url_for('version_plan.customers'))
        now = _now()
        old = db.execute(f'SELECT * FROM {_t(L_CUSTOMERS)} WHERE name=?', (name,)).fetchone()
        vals = (itg, cb, (f.get('merge_target_no') or '').strip(),
                (f.get('merge_deadline') or '').strip(), (f.get('contact') or '').strip(),
                (f.get('note') or '').strip(), (f.get('current_version_no') or '').strip(), now, name)
        if old:
            db.execute(f'''UPDATE {_t(L_CUSTOMERS)} SET integrating_version_no=?, custom_branch_no=?,
                        merge_target_no=?, merge_deadline=?, contact=?, note=?, current_version_no=?,
                        updated_at=? WHERE name=?''', vals)
        else:
            db.execute(f'''INSERT INTO {_t(L_CUSTOMERS)}
                        (integrating_version_no, custom_branch_no, merge_target_no, merge_deadline,
                         contact, note, current_version_no, updated_at, name, created_at)
                        VALUES (?,?,?,?,?,?,?,?,?,?)''', vals + (now,))
        db.commit()
        _ulog('客户', name, '保存', '')
        flash(f'客户「{name}」已保存', 'success')
        return redirect(url_for('version_plan.customers'))
    rows = db.execute(f'SELECT * FROM {_t(L_CUSTOMERS)} ORDER BY name').fetchall()
    versions = db.execute(f'SELECT version_no, status, release_date FROM {_t(L_VERSIONS)} '
                          'ORDER BY id DESC').fetchall()
    warn = [r for r in rows if r['custom_branch_no'] and r['merge_deadline'] and r['merge_deadline'] < _today()]
    edit_name = (request.args.get('edit') or '').strip()
    edit_row = db.execute(f'SELECT * FROM {_t(L_CUSTOMERS)} WHERE name=?', (edit_name,)).fetchone() if edit_name else None
    return render_template('vp_customers.html', rows=rows, versions=versions, warn=warn,
                           can_manage=_can_manage(), today=_today(), edit_row=edit_row)


# ---------------------------------------------------------------- 客户回告
@blueprint.route('/version_plan/report')
def report():
    _ensure_tables()
    db = get_db()
    cust = (request.args.get('customer') or '').strip()
    customers = db.execute(f'SELECT name FROM {_t(L_CUSTOMERS)} ORDER BY name').fetchall()
    if not cust and customers:
        cust = customers[0]['name']
    rows = []
    if cust:
        rows = db.execute(f'SELECT * FROM {_t(L_DEMANDS)} WHERE customer_name=? ORDER BY id',
                          (cust,)).fetchall()
    # 汇总
    summary = {}
    for r in rows:
        summary[r['status']] = summary.get(r['status'], 0) + 1
    text = _report_text(db, cust, rows) if cust else ''
    release_map = {r['version_no']: (r['release_date'] or '待定')
                   for r in db.execute(f'SELECT version_no, release_date FROM {_t(L_VERSIONS)}').fetchall()}
    return render_template('vp_report.html', customers=customers, cust=cust, rows=rows,
                           summary=summary, text=text, can_manage=_can_manage(), release_map=release_map)


def _report_text(db, cust, rows):
    lines = [f'{cust} 需求兑现情况（{_today()}）', '']
    if not rows:
        lines.append('截至今日，暂无登记的需求。')
        return '\n'.join(lines)
    pub = [r for r in rows if r['status'] == '已发布']
    sch = [r for r in rows if r['status'] == '已排期']
    other = [r for r in rows if r['status'] not in ('已发布', '已排期')]
    lines.append(f'合计登记 {len(rows)} 条：已发布 {len(pub)} 条、已排期 {len(sch)} 条、其它状态 {len(other)} 条。')
    lines.append('')
    if pub:
        lines.append('一、已交付')
        for r in pub:
            lines.append(f'  • {r["code"]}（溯源 {r["doc_ref"]}）{r["description"]}｜版本 {r["version_no"]}')
        lines.append('')
    if sch:
        lines.append('二、已排期（未发布）')
        for r in sch:
            v = db.execute(f'SELECT release_date, status FROM {_t(L_VERSIONS)} WHERE version_no=?',
                           (r['version_no'],)).fetchone()
            eta = (v['release_date'] if v and v['release_date'] else '待定')
            lines.append(f'  • {r["code"]}（溯源 {r["doc_ref"]}）{r["description"]}｜版本 {r["version_no"]}｜预计发布 {eta}')
        lines.append('')
    if other:
        lines.append('三、其它状态')
        for r in other:
            note = f'：{r["filter_note"]}' if r['filter_note'] else ''
            lines.append(f'  • {r["code"]}（溯源 {r["doc_ref"]}）{r["description"]}｜当前「{r["status"]}」{note}')
        lines.append('')
    cust_row = db.execute(f'SELECT * FROM {_t(L_CUSTOMERS)} WHERE name=?', (cust,)).fetchone()
    if cust_row:
        lines.append('当前在用版本：' + (cust_row['current_version_no'] or '未登记'))
        if cust_row['integrating_version_no']:
            lines.append('集成中版本：' + cust_row['integrating_version_no'])
        if cust_row['custom_branch_no']:
            lines.append(f'定制分支：{cust_row["custom_branch_no"]}（计划合回 {cust_row["merge_target_no"]}，'
                         f'期限 {cust_row["merge_deadline"]}）')
    return '\n'.join(lines)


@blueprint.route('/version_plan/report/text')
def report_text():
    _ensure_tables()
    db = get_db()
    cust = (request.args.get('customer') or '').strip()
    rows = db.execute(f'SELECT * FROM {_t(L_DEMANDS)} WHERE customer_name=? ORDER BY id',
                      (cust,)).fetchall() if cust else []
    return jsonify({'ok': True, 'customer': cust, 'text': _report_text(db, cust, rows)})


# ---------------------------------------------------------------- 设置（模块字典）
@blueprint.route('/version_plan/settings', methods=['GET', 'POST'])
def settings():
    _ensure_tables()
    db = get_db()
    if request.method == 'POST':
        if not _can_manage():
            return _deny()
        raw = (request.form.get('modules') or '').replace('\r', '')
        items = [x.strip() for x in re.split(r'[,\n，]', raw) if x.strip()]
        db.execute(f'DELETE FROM {_t(L_META)} WHERE kind="module"')
        for it in items:
            db.execute(f'INSERT INTO {_t(L_META)} (kind, value, created_at) VALUES ("module",?,?)',
                       (it, _now()))
        db.commit()
        flash(f'模块字典已保存（{len(items)} 项）', 'success')
        return redirect(url_for('version_plan.settings'))
    mods = [r['value'] for r in db.execute(f'SELECT value FROM {_t(L_META)} WHERE kind="module" '
                                           'ORDER BY id').fetchall()]
    if not mods:
        mods = ['MQTT', 'FOTA', '低功耗', 'AT 命令', '网络注册', 'SIM 管理', '定位']
    return render_template('vp_settings.html', mods=mods, can_manage=_can_manage())


def _modules():
    try:
        db = get_db()
        rows = db.execute(f'SELECT value FROM {_t(L_META)} WHERE kind="module" ORDER BY id').fetchall()
        if rows:
            return [r['value'] for r in rows]
    except Exception:
        pass
    return ['MQTT', 'FOTA', '低功耗', 'AT 命令', '网络注册', 'SIM 管理', '定位']


# ==================== 数据开放接口 OpenAPI v1 ====================
# 平台 AI 用「进程内直接调用」读本插件数据，约定：
#   GET /version_plan/__openapi/datasets                       → 声明数据集
#   GET /version_plan/__openapi/rows?table=&limit=&offset=     → 分页取行
OPENAPI_TABLES = {
    L_DEMANDS: ('需求（过滤+排期）', 'id', 'raised_at',
                '一行=一条需求；code 需求编号；status：待过滤/规划池/挂起/已排期/已发布/不做；'
                'version_no 排入版本；doc_ref 云文档溯源；filter_result 过滤结论；is_custom 是否定制'),
    L_VERSIONS: ('版本', 'id', 'release_date',
                 '一行=一个版本；version_no 版本号；status：规划中/已冻结/开发中/测试中/已发布/已归档；'
                 'vtype：迭代/热修复/客户定制；freeze_date/test_date/release_date 里程碑'),
    L_CUSTOMERS: ('客户与客户-版本矩阵', 'id', 'updated_at',
                  '一行=一个客户；current_version_no 当前在用版本；integrating_version_no 集成中版本；'
                  'custom_branch_no 定制分支；merge_target_no/merge_deadline 计划合回与期限'),
    L_RELEASES: ('发布说明 Release Notes', 'id', 'updated_at',
                 '一行=一个版本的发布说明；status：草稿/定稿；content 为 Markdown 正文'),
}


def _openapi_meta(db, logical):
    """返回 (rows, fields)；rows 为总行数"""
    tbl = _t(logical)
    cnt = db.execute(f'SELECT COUNT(*) FROM {tbl}').fetchone()[0]
    cols = [r[1] for r in db.execute(f'PRAGMA table_info({tbl})').fetchall()]
    return cnt, cols


@blueprint.route('/version_plan/__openapi/datasets')
def __openapi_datasets():
    _ensure_tables()
    db = get_db()
    out = []
    for logical, (label, pk, timef, desc) in OPENAPI_TABLES.items():
        cnt, cols = _openapi_meta(db, logical)
        out.append({
            'name': _t(logical), 'label': label, 'primary_key': pk, 'time_field': timef,
            'rows': cnt, 'sensitive': False,
            'fields': [{'name': c, 'type': 'text', 'label': c} for c in cols],
            'description': desc,
        })
    return jsonify({'openapi': 1, 'plugin': PID, 'tables': out})


@blueprint.route('/version_plan/__openapi/rows')
def __openapi_rows():
    _ensure_tables()
    table = (request.args.get('table') or '').strip()
    logical = None
    for lg in OPENAPI_TABLES:
        if _t(lg) == table or lg == table:
            logical = lg
            break
    if not logical:
        return jsonify({'openapi': 1, 'error': 'unknown table', 'table': table}), 400
    try:
        limit = max(1, min(int(request.args.get('limit', 100)), 1000))
        offset = max(0, int(request.args.get('offset', 0)))
    except Exception:
        limit, offset = 100, 0
    db = get_db()
    tbl = _t(logical)
    total = db.execute(f'SELECT COUNT(*) FROM {tbl}').fetchone()[0]
    cols = [r[1] for r in db.execute(f'PRAGMA table_info({tbl})').fetchall()]
    rows = [dict(r) for r in db.execute(f'SELECT * FROM {tbl} ORDER BY id LIMIT ? OFFSET ?',
                                       (limit, offset)).fetchall()]
    return jsonify({'openapi': 1, 'table': table, 'offset': offset, 'limit': limit,
                    'total': total, 'fields': cols, 'rows': rows})
