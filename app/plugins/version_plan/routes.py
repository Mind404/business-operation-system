# -*- coding: utf-8 -*-
"""版本规划管理插件 · 路由实现

业务链路：云文档原始登记 → 需求过滤 → 版本规划 → 冻结/开发/测试 → 发布 → 客户回告。
硬约束 R1~R7 全部在服务端校验，模板只做展示。
"""
import csv
import difflib
import io
import json
import os
import re
from datetime import date, datetime, timedelta

from flask import (Response, flash, jsonify, redirect, render_template,
                   request, session, url_for)

from plugin_system import create_plugin_table, get_db, log_operation
from plugins.base import make_blueprint

blueprint = make_blueprint('version_plan', __name__)

PID = 'version_plan'
# 逻辑表名（真实表名由平台分配，统一走 _T 缓存）
L_PRODUCTS = 'version_plan_products'
L_DEMANDS = 'version_plan_demands'
L_VERSIONS = 'version_plan_versions'
L_CUSTOMERS = 'version_plan_customers'
L_RELEASES = 'version_plan_releases'
L_LOGS = 'version_plan_logs'
L_META = 'version_plan_meta'
L_TRAINS = 'version_plan_trains'
L_ITEMS = 'version_plan_items'
L_LOADCHG = 'version_plan_load_changes'
L_ASSESS = 'version_plan_assessments'
L_CHECKS = 'version_plan_checks'
L_SIGNOFFS = 'version_plan_signoffs'
L_NOTICES = 'version_plan_notices'
L_CUSTPROD = 'version_plan_customer_products'

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
    (L_PRODUCTS, L_PRODUCTS, '''CREATE TABLE IF NOT EXISTS version_plan_products (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        code TEXT NOT NULL UNIQUE,
        name TEXT NOT NULL UNIQUE,
        owner TEXT DEFAULT '',
        status TEXT DEFAULT '启用',
        description TEXT DEFAULT '',
        created_at TEXT,
        updated_at TEXT)'''),
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
        version_no TEXT NOT NULL,
        vtype TEXT DEFAULT '迭代',
        product_code TEXT DEFAULT '',
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
        rollback_plan TEXT DEFAULT '',
        status TEXT DEFAULT '规划中',
        frozen_at TEXT DEFAULT '',
        frozen_by TEXT DEFAULT '',
        unfreeze_log TEXT DEFAULT '',
        released_at TEXT DEFAULT '',
        created_at TEXT,
        updated_at TEXT,
        UNIQUE(product_code, version_no))'''),
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
        version_no TEXT NOT NULL,
        product_code TEXT DEFAULT '',
        content TEXT DEFAULT '',
        status TEXT DEFAULT '草稿',
        rollback_plan TEXT DEFAULT '',
        created_at TEXT,
        updated_at TEXT,
        finalized_by TEXT DEFAULT '',
        UNIQUE(product_code, version_no))'''),
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
    # ---- v13.0.1 版本火车 / 装车 / 评估 / 签核 / 回告 ----
    (L_TRAINS, L_TRAINS, '''CREATE TABLE IF NOT EXISTS version_plan_trains (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        code TEXT NOT NULL UNIQUE,
        product_code TEXT DEFAULT '',
        month TEXT DEFAULT '',
        status TEXT DEFAULT '计划',
        skip_reason TEXT DEFAULT '',
        release_day TEXT DEFAULT '',
        capacity REAL DEFAULT 40,
        capacity_unit TEXT DEFAULT '人日',
        merged_into TEXT DEFAULT '',
        created_at TEXT,
        updated_at TEXT)'''),
    (L_ITEMS, L_ITEMS, '''CREATE TABLE IF NOT EXISTS version_plan_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        train_code TEXT NOT NULL,
        demand_code TEXT NOT NULL,
        state TEXT DEFAULT '已装',
        added_at TEXT,
        removed_at TEXT,
        reason TEXT DEFAULT '')''' ),
    (L_LOADCHG, L_LOADCHG, '''CREATE TABLE IF NOT EXISTS version_plan_load_changes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        demand_code TEXT NOT NULL,
        from_train TEXT DEFAULT '',
        to_train TEXT DEFAULT '',
        action TEXT DEFAULT '',
        operator TEXT DEFAULT '',
        created_at TEXT)''' ),
    (L_ASSESS, L_ASSESS, '''CREATE TABLE IF NOT EXISTS version_plan_assessments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        demand_code TEXT NOT NULL,
        round INTEGER DEFAULT 1,
        w_value INTEGER DEFAULT 3,
        w_client INTEGER DEFAULT 3,
        w_urgent INTEGER DEFAULT 3,
        w_cost INTEGER DEFAULT 3,
        score REAL DEFAULT 0,
        verdict TEXT DEFAULT '',
        comment TEXT DEFAULT '',
        operator TEXT DEFAULT '',
        created_at TEXT)''' ),
    (L_CHECKS, L_CHECKS, '''CREATE TABLE IF NOT EXISTS version_plan_checks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        version_no TEXT NOT NULL,
        product_code TEXT DEFAULT '',
        item_no INTEGER NOT NULL,
        result TEXT DEFAULT 'pending',
        evidence TEXT DEFAULT '',
        checked_by TEXT DEFAULT '',
        checked_at TEXT DEFAULT '')''' ),
    (L_SIGNOFFS, L_SIGNOFFS, '''CREATE TABLE IF NOT EXISTS version_plan_signoffs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        version_no TEXT NOT NULL,
        product_code TEXT DEFAULT '',
        role TEXT NOT NULL,
        verdict TEXT DEFAULT '',
        comment TEXT DEFAULT '',
        signed_at TEXT DEFAULT '')''' ),
    (L_NOTICES, L_NOTICES, '''CREATE TABLE IF NOT EXISTS version_plan_notices (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        customer_id INTEGER DEFAULT 0,
        customer_name TEXT DEFAULT '',
        version_no TEXT DEFAULT '',
        content TEXT DEFAULT '',
        sent_at TEXT DEFAULT '',
        created_at TEXT)''' ),
    (L_CUSTPROD, L_CUSTPROD, '''CREATE TABLE IF NOT EXISTS version_plan_customer_products (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        customer_id INTEGER DEFAULT 0,
        product_code TEXT DEFAULT '',
        in_use_version TEXT DEFAULT '',
        upgrading_version TEXT DEFAULT '')''' ),
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
    """老库补列（插件自管迁移）：版本表新增定制版合回目标/期限；
    需求/版本/客户表新增产品归属列 product_code（空串归入未分配）。"""
    db = db or get_db()
    cols = [r[1] for r in db.execute(f'PRAGMA table_info({_t(L_VERSIONS)})').fetchall()]
    for col in ('merge_target_no', 'merge_deadline'):
        if col not in cols:
            db.execute(f'ALTER TABLE {_t(L_VERSIONS)} ADD COLUMN {col} TEXT DEFAULT ""')
    for logical in (L_DEMANDS, L_VERSIONS, L_CUSTOMERS):
        cols = [r[1] for r in db.execute(f'PRAGMA table_info({_t(logical)})').fetchall()]
        if 'product_code' not in cols:
            db.execute(f'ALTER TABLE {_t(logical)} ADD COLUMN product_code TEXT DEFAULT ""')
    # v13.0.1：需求补「人日 / 评分 / 来源」三列，装车与评估要用
    for col, ddl in (('estimate', 'REAL DEFAULT 0'), ('score', 'REAL DEFAULT 0'),
                     ('source_type', 'TEXT DEFAULT ""'), ('source_ref', 'TEXT DEFAULT ""')):
        cols = [r[1] for r in db.execute(f'PRAGMA table_info({_t(L_DEMANDS)})').fetchall()]
        if col not in cols:
            db.execute(f'ALTER TABLE {_t(L_DEMANDS)} ADD COLUMN {col} {ddl}')
    # 回滚方案：版本表留编辑态，发布时复制到发布说明表
    for logical in (L_VERSIONS, L_RELEASES):
        cols = [r[1] for r in db.execute(f'PRAGMA table_info({_t(logical)})').fetchall()]
        if 'rollback_plan' not in cols:
            db.execute(f'ALTER TABLE {_t(logical)} ADD COLUMN rollback_plan TEXT DEFAULT ""')
    # G10 历史回填：已发布版本的关联车次联动置「已发布」（幂等）
    db.execute(f'''UPDATE {_t(L_TRAINS)} SET status="已发布" WHERE code IN (
        SELECT DISTINCT i.train_code FROM {_t(L_ITEMS)} i
        JOIN {_t(L_DEMANDS)} d ON d.code=i.demand_code
        JOIN {_t(L_VERSIONS)} v ON v.version_no=d.version_no AND v.product_code=d.product_code
        WHERE v.status="已发布" AND i.state="已装")''')
    _migrate_version_key(db)
    _migrate_release_key(db)
    db.commit()


def _migrate_version_key(db):
    """v13.0.1 缺陷修复：版本号由「全局唯一」改为「产品内唯一」。

    多产品场景下第二个产品无法使用 1.0.0（唯一索引冲突直接 500），
    老库需要重建表：去掉 version_no 单列唯一索引，改为 (product_code, version_no) 联合唯一。
    """
    tbl = _t(L_VERSIONS)
    global_unique = False
    for idx in db.execute(f'PRAGMA index_list({tbl})').fetchall():
        if idx['unique'] and idx['origin'] == 'u':
            cols = [c['name'] for c in db.execute(f"PRAGMA index_info('{idx['name']}')").fetchall()]
            if cols == ['version_no']:
                global_unique = True
                break
    if not global_unique:
        return
    tmp = tbl + '_rk_mig'
    cols = [r[1] for r in db.execute(f'PRAGMA table_info({tbl})').fetchall()]
    db.execute(f'ALTER TABLE {tbl} RENAME TO {tmp}')
    for logical, desired, ddl in DDL:
        if logical == L_VERSIONS:
            db.execute(ddl)
            break
    col_sql = ', '.join(cols)
    db.execute(f'INSERT INTO {tbl} ({col_sql}) SELECT {col_sql} FROM {tmp}')
    db.execute(f'DROP TABLE {tmp}')


def _migrate_release_key(db):
    """v13.0.1 缺陷修复：发布说明由「版本号全局唯一」改为「(产品, 版本号) 唯一」。

    否则多个产品的 1.0.0 会共用同一份 Release Note（A 产品的发布说明被 B 产品覆盖）。
    """
    tbl = _t(L_RELEASES)
    hit = False
    for idx in db.execute(f'PRAGMA index_list({tbl})').fetchall():
        if idx['unique'] and idx['origin'] == 'u':
            cols = [c['name'] for c in db.execute(f"PRAGMA index_info('{idx['name']}')").fetchall()]
            if cols == ['version_no']:
                hit = True
                break
    if not hit:
        return
    tmp = tbl + '_rk_mig'
    cols = [r[1] for r in db.execute(f'PRAGMA table_info({tbl})').fetchall()]
    db.execute(f'ALTER TABLE {tbl} RENAME TO {tmp}')
    for logical, desired, ddl in DDL:
        if logical == L_RELEASES:
            db.execute(ddl)
            break
    if 'product_code' not in cols:
        cols = cols + ['product_code']
    db.execute(f'INSERT INTO {tbl} ({", ".join(cols)}) SELECT {", ".join(cols)} FROM {tmp}')
    db.execute(f'DROP TABLE {tmp}')


def _t(logical):
    if logical not in _T:
        _ensure_tables()
    return _T.get(logical, logical)


def _req_product():
    """当前请求选中的产品编号（空串=全部产品/未分配）。支持 query 与 form。"""
    v = (request.args.get('product') or request.form.get('product') or '').strip()
    return v


def _product_rows(db, only_active=False):
    """产品列表：启用优先，再按名称排序。返回 dict 列表。"""
    sql = f'SELECT * FROM {_t(L_PRODUCTS)}'
    if only_active:
        sql += " WHERE status='启用'"
    sql += ' ORDER BY CASE status WHEN "启用" THEN 0 ELSE 1 END, name ASC'
    return [dict(r) for r in db.execute(sql).fetchall()]


def _product_map(db):
    """product_code -> 产品行 dict。"""
    return {r['code']: dict(r) for r in db.execute(f'SELECT * FROM {_t(L_PRODUCTS)}').fetchall()}


def _product_name(db, code):
    if not code:
        return ''
    r = db.execute(f'SELECT name FROM {_t(L_PRODUCTS)} WHERE code=?', (code,)).fetchone()
    return r['name'] if r else code


def _prod_where(product, table_alias=''):
    """按产品拼 WHERE 条件。返回 (sql 片段, args)；空产品返回 ('', [])。
    table_alias 用于多表 join 时限定列名。"""
    if not product:
        return '', []
    col = f'{table_alias}.product_code' if table_alias else 'product_code'
    return f'{col}=?', [product]


def _safe_estimate(db, code, raw):
    """写预估人日：非数字或负数一律按 0 处理，避免容量统计被脏值污染。"""
    try:
        v = float(raw or 0)
        if v < 0 or v != v:
            v = 0.0
    except (TypeError, ValueError):
        v = 0.0
    db.execute(f'UPDATE {_t(L_DEMANDS)} SET estimate=? WHERE code=?', (v, code))


def _ensure_product(db, code):
    """产品存在且启用则返回行；否则 None。"""
    if not code:
        return None
    return db.execute(f'SELECT * FROM {_t(L_PRODUCTS)} WHERE code=?', (code,)).fetchone()


def _rel_row(db, no, prod):
    """发布说明定位：优先取本产品记录；老库（改造前写入）只有 product_code='' 的全局记录，做一次兼容回退。"""
    rel = db.execute(f'SELECT * FROM {_t(L_RELEASES)} WHERE version_no=? AND product_code=?',
                     (no, prod or '')).fetchone()
    if rel:
        return rel
    return db.execute(f'SELECT * FROM {_t(L_RELEASES)} WHERE version_no=? AND product_code=""',
                      (no,)).fetchone()


def _vrow(db, no, prod=''):
    """按版本号定位版本；版本号全局唯一，带产品编号时再按归属加固定位，无产品时回退全库取第一条（兼容老链接）。"""
    if prod:
        return db.execute(f'SELECT * FROM {_t(L_VERSIONS)} WHERE version_no=? AND product_code=?',
                          (no, prod)).fetchone()
    return db.execute(f'SELECT * FROM {_t(L_VERSIONS)} WHERE version_no=?', (no,)).fetchone()


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


def _similar(db, desc, exclude_id=None, limit=300, product=''):
    """描述相似度检测（疑似重复/同源）。返回 [(code, description, score, customer)]；同产品内比对。"""
    base = _norm(desc)
    if not base or len(base) < 6:
        return []
    pwhere = 'WHERE product_code=?' if product else ''
    rows = db.execute(
        f'SELECT id, code, description, customer_name, product_code FROM {_t(L_DEMANDS)} '
        f'{pwhere} ORDER BY id DESC LIMIT ?',
        ([product] if product else []) + [limit]).fetchall()
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
STAGE_MAP = {'规划中': 0, '已冻结': 1, '开发中': 2, '测试中': 2, '已发布': 3, '已归档': 3}


def _overview(db, product=''):
    """计算工作台概览统计。product 为空=全部产品；非空=只看该产品。"""
    where, args = _prod_where(product)
    dwsql = ('WHERE ' + where) if where else ''
    stat = {}
    for st in DEMAND_STATUS:
        if where:
            stat[st] = db.execute(
                f'SELECT COUNT(*) c FROM {_t(L_DEMANDS)} WHERE status=? AND {where}',
                [st] + args).fetchone()['c']
        else:
            stat[st] = db.execute(
                f'SELECT COUNT(*) c FROM {_t(L_DEMANDS)} WHERE status=?', (st,)).fetchone()['c']
    total = db.execute(f'SELECT COUNT(*) c FROM {_t(L_DEMANDS)} {dwsql}', args).fetchone()['c']
    vstat = {}
    for st in VERSION_STATUS:
        if where:
            vstat[st] = db.execute(
                f'SELECT COUNT(*) c FROM {_t(L_VERSIONS)} WHERE status=? AND {where}',
                [st] + args).fetchone()['c']
        else:
            vstat[st] = db.execute(
                f'SELECT COUNT(*) c FROM {_t(L_VERSIONS)} WHERE status=?', (st,)).fetchone()['c']
    judged = total - stat.get('待过滤', 0)
    rate = round(judged * 100.0 / total, 1) if total else 0.0
    # 定制分支超期（按产品）
    overdue = [r for r in db.execute(f'SELECT * FROM {_t(L_CUSTOMERS)} {dwsql}', args).fetchall()
               if r['custom_branch_no'] and r['merge_deadline'] and r['merge_deadline'] < _today()]
    # 里程碑 7 天内
    horizon = (date.today() + timedelta(days=7)).strftime('%Y-%m-%d')
    milestones = []
    vrows = db.execute(f'SELECT * FROM {_t(L_VERSIONS)} {dwsql}', args).fetchall()
    for r in vrows:
        if r['status'] in ('已发布', '已归档'):
            continue
        for label, key in (('需求冻结日', 'freeze_date'), ('提测日', 'test_date'), ('发布日', 'release_date')):
            val = (r[key] or '').strip()
            if val and val <= horizon:
                milestones.append({'version_no': r['version_no'], 'label': label,
                                   'date': val, 'state': r['status'],
                                   'product_code': r['product_code']})
    milestones.sort(key=lambda x: x['date'])
    # 每个版本的需求数 / 已交付数
    vneed = {}
    for r in db.execute(
            f'SELECT version_no, COUNT(*) c, '
            'SUM(CASE WHEN status="已发布" THEN 1 ELSE 0 END) d '
            f'FROM {_t(L_DEMANDS)} WHERE version_no<>"" GROUP BY version_no').fetchall():
        vneed[r['version_no']] = (r['c'], r['d'] or 0)
    versions = [dict(r) for r in vrows]
    versions.sort(key=lambda v: (
        {'测试中': 0, '开发中': 1, '已冻结': 2, '规划中': 3, '已发布': 4}.get(v['status'], 5),
        v['release_date'] or '9999-12-31'))
    versions = versions[:6]
    for v in versions:
        c, d = vneed.get(v['version_no'], (0, 0))
        v['needs'], v['needs_done'] = c, d
        v['stage'] = STAGE_MAP.get(v['status'], 0)
    month = _today()[:7]
    if where:
        month_release = db.execute(
            f'SELECT COUNT(*) c FROM {_t(L_VERSIONS)} WHERE substr(release_date,1,7)=? AND {where}',
            [month] + args).fetchone()['c']
        week_new = db.execute(
            f'SELECT COUNT(*) c FROM {_t(L_DEMANDS)} WHERE substr(created_at,1,10)>=? AND {where}',
            [(date.today() - timedelta(days=30)).strftime('%Y-%m-%d')] + args).fetchone()['c']
        pending = db.execute(
            f'SELECT * FROM {_t(L_DEMANDS)} WHERE status="待过滤" AND {where} ORDER BY id DESC LIMIT 8',
            args).fetchall()
    else:
        month_release = db.execute(
            f'SELECT COUNT(*) c FROM {_t(L_VERSIONS)} WHERE substr(release_date,1,7)=?',
            (month,)).fetchone()['c']
        week_new = db.execute(
            f'SELECT COUNT(*) c FROM {_t(L_DEMANDS)} WHERE substr(created_at,1,10)>=?',
            ((date.today() - timedelta(days=30)).strftime('%Y-%m-%d'),)).fetchone()['c']
        pending = db.execute(
            f'SELECT * FROM {_t(L_DEMANDS)} WHERE status="待过滤" ORDER BY id DESC LIMIT 8').fetchall()
    recent = db.execute(f'SELECT * FROM {_t(L_LOGS)} ORDER BY id DESC LIMIT 12').fetchall()
    return dict(stat=stat, total=total, rate=rate, vstat=vstat, overdue_branches=overdue,
                milestones=milestones, recent=recent, versions=versions, pending=pending,
                month_release=month_release, week_new=week_new)


@blueprint.route('/version_plan/')
def index():
    _ensure_tables()
    db = get_db()
    ov = _overview(db)
    products = _product_rows(db)
    # 每个产品的概览小统计
    prod_cards = []
    for p in products:
        pc = p['code']
        dstat = {st: db.execute(
            f'SELECT COUNT(*) c FROM {_t(L_DEMANDS)} WHERE product_code=? AND status=?',
            (pc, st)).fetchone()['c'] for st in DEMAND_STATUS}
        vrows = db.execute(f'SELECT status FROM {_t(L_VERSIONS)} WHERE product_code=?', (pc,)).fetchall()
        active = sum(1 for v in vrows if v['status'] in ('规划中', '已冻结', '开发中', '测试中'))
        prod_cards.append({**p, 'demands': sum(dstat.values()), 'pending': dstat['待过滤'],
                           'pool': dstat['规划池'], 'versions': len(vrows), 'active': active,
                           'released': dstat['已发布']})
    return render_template('vp_index.html', products=prod_cards, **ov,
                           can_manage=_can_manage(), today=_today())


@blueprint.route('/version_plan/products/<code>')
def product_home(code):
    _ensure_tables()
    db = get_db()
    p = db.execute(f'SELECT * FROM {_t(L_PRODUCTS)} WHERE code=?', (code,)).fetchone()
    if not p:
        flash('产品不存在或已停用', 'error')
        return redirect(url_for('version_plan.index'))
    ov = _overview(db, code)
    return render_template('vp_product.html', p=dict(p), **ov,
                           can_manage=_can_manage(), today=_today())


# ---------------------------------------------------------------- 产品管理
@blueprint.route('/version_plan/products')
def products():
    _ensure_tables()
    db = get_db()
    prows = _product_rows(db)
    cards = []
    for p in prows:
        pc = p['code']
        dstat = {st: db.execute(
            f'SELECT COUNT(*) c FROM {_t(L_DEMANDS)} WHERE product_code=? AND status=?',
            (pc, st)).fetchone()['c'] for st in DEMAND_STATUS}
        vrows = db.execute(f'SELECT status FROM {_t(L_VERSIONS)} WHERE product_code=?', (pc,)).fetchall()
        cust_n = db.execute(f'SELECT COUNT(*) c FROM {_t(L_CUSTOMERS)} WHERE product_code=?',
                            (pc,)).fetchone()['c']
        active = sum(1 for v in vrows if v['status'] in ('规划中', '已冻结', '开发中', '测试中'))
        cards.append({**p, 'demands': sum(dstat.values()), 'pending': dstat['待过滤'],
                      'pool': dstat['规划池'], 'released': dstat['已发布'],
                      'versions': len(vrows), 'active': active, 'customers': cust_n})
    return render_template('vp_products.html', products=cards, can_manage=_can_manage())


@blueprint.route('/version_plan/products/new', methods=['POST'])
def product_new():
    _ensure_tables()
    if not _can_manage():
        return _deny()
    db = get_db()
    f = request.form
    code = (f.get('code') or '').strip()
    name = (f.get('name') or '').strip()
    if not code or not name:
        flash('产品编号与名称都必填', 'error')
        return redirect(url_for('version_plan.products'))
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,20}', code):
        flash('产品编号仅限字母/数字/中划线/下划线（最长 20），如 P1、MODEM-A', 'error')
        return redirect(url_for('version_plan.products'))
    if db.execute(f'SELECT 1 FROM {_t(L_PRODUCTS)} WHERE code=?', (code,)).fetchone():
        flash(f'产品编号 {code} 已存在', 'error')
        return redirect(url_for('version_plan.products'))
    if db.execute(f'SELECT 1 FROM {_t(L_PRODUCTS)} WHERE name=?', (name,)).fetchone():
        flash(f'产品名称「{name}」已存在', 'error')
        return redirect(url_for('version_plan.products'))
    now = _now()
    db.execute(
        f'''INSERT INTO {_t(L_PRODUCTS)} (code, name, owner, status, description, created_at, updated_at)
        VALUES (?,?,?,?,?,?,?)''',
        (code, name, (f.get('owner') or '').strip(), '启用',
         (f.get('description') or '').strip(), now, now))
    db.commit()
    _ulog('产品', code, '新建', name)
    _log('新建产品', f'{code} {name}')
    flash(f'产品「{name}」已创建，可在其空间内过滤需求、规划版本', 'success')
    return redirect(url_for('version_plan.product_home', code=code))


@blueprint.route('/version_plan/products/<code>/edit', methods=['POST'])
def product_edit(code):
    _ensure_tables()
    if not _can_manage():
        return _deny()
    db = get_db()
    p = db.execute(f'SELECT * FROM {_t(L_PRODUCTS)} WHERE code=?', (code,)).fetchone()
    if not p:
        flash('产品不存在', 'error')
        return redirect(url_for('version_plan.products'))
    f = request.form
    name = (f.get('name') or '').strip()
    status = (f.get('status') or '启用').strip()
    if not name:
        flash('产品名称必填', 'error')
        return redirect(url_for('version_plan.products'))
    if db.execute(f'SELECT 1 FROM {_t(L_PRODUCTS)} WHERE name=? AND code<>?', (name, code)).fetchone():
        flash(f'产品名称「{name}」已被其它产品占用', 'error')
        return redirect(url_for('version_plan.products'))
    if status not in ('启用', '停用'):
        status = '启用'
    db.execute(
        f'''UPDATE {_t(L_PRODUCTS)} SET name=?, owner=?, status=?, description=?, updated_at=?
        WHERE code=?''',
        (name, (f.get('owner') or '').strip(), status, (f.get('description') or '').strip(), _now(), code))
    db.commit()
    _ulog('产品', code, '编辑', f'{name} / {status}')
    flash(f'产品「{name}」已更新', 'success')
    return redirect(url_for('version_plan.products'))


# ---------------------------------------------------------------- 需求
@blueprint.route('/version_plan/demands')
def demands():
    _ensure_tables()
    db = get_db()
    q = (request.args.get('q') or '').strip()
    st = (request.args.get('status') or '').strip()
    ver = (request.args.get('version') or '').strip()
    cust = (request.args.get('customer') or '').strip()
    prod = _req_product()
    try:
        page = max(1, int(request.args.get('page', 1)))
    except Exception:
        page = 1
    size = 40
    where, args = [], []
    if prod:
        where.append('product_code=?')
        args.append(prod)
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
    # 下拉候选按当前产品收敛
    cwhere, cargs = _prod_where(prod)
    cwsql = ('WHERE ' + cwhere) if cwhere else ''
    customers = db.execute(f'SELECT name FROM {_t(L_CUSTOMERS)} {cwsql} ORDER BY name', cargs).fetchall()
    versions = db.execute(f'SELECT version_no FROM {_t(L_VERSIONS)} {cwsql} ORDER BY id DESC', cargs).fetchall()
    counts = {k: db.execute(
        f'SELECT COUNT(*) c FROM {_t(L_DEMANDS)} WHERE status=?{" AND product_code=?" if prod else ""}',
        [k] + ([prod] if prod else [])).fetchone()['c'] for k in DEMAND_STATUS}
    prod_map = _product_map(db)
    return render_template('vp_demands.html', rows=rows, total=total, page=page, size=size,
                           pages=max(1, (total + size - 1) // size), q=q, st=st, ver=ver, cust=cust,
                           product=prod, products=_product_rows(db), prod_map=prod_map,
                           customers=customers, versions=versions, counts=counts,
                           statuses=DEMAND_STATUS, dtypes=DEMAND_TYPE, can_manage=_can_manage())


@blueprint.route('/version_plan/demands/new', methods=['GET', 'POST'])
def demand_new():
    _ensure_tables()
    if not _can_manage():
        return _deny()
    db = get_db()
    products = _product_rows(db)
    if request.method == 'GET':
        prod = _req_product()
        cwhere, cargs = _prod_where(prod)
        cwsql = ('WHERE ' + cwhere) if cwhere else ''
        customers = db.execute(f'SELECT name FROM {_t(L_CUSTOMERS)} {cwsql} ORDER BY name', cargs).fetchall()
        versions = db.execute(f'SELECT version_no FROM {_t(L_VERSIONS)} {cwsql} ORDER BY id DESC', cargs).fetchall()
        return render_template('vp_demand_form.html', d=None, customers=customers,
                               versions=versions, products=products, product=prod,
                               dtypes=DEMAND_TYPE, statuses=DEMAND_STATUS,
                               sims=[], today=_today())
    f = request.form
    doc_ref = (f.get('doc_ref') or '').strip()
    desc = (f.get('description') or '').strip()
    if not doc_ref:
        flash('云文档溯源必填：请填写原始登记的行号 / 原始编号 / 原文摘要（R6）', 'error')
        return redirect(url_for('version_plan.demand_new', product=_req_product()))
    if not desc:
        flash('需求描述必填', 'error')
        return redirect(url_for('version_plan.demand_new', product=_req_product()))
    prod = (f.get('product_code') or '').strip()
    if prod and not _ensure_product(db, prod):
        flash('所选产品不存在，请重新选择', 'error')
        return redirect(url_for('version_plan.demand_new'))
    code = _next_code(db)
    now = _now()
    db.execute(
        f'''INSERT INTO {_t(L_DEMANDS)}
        (code, doc_ref, raised_at, customer_name, description, business_value, acceptance, demand_type,
         filter_result, filter_note, version_no, is_custom, custom_customer, status, note, product_code,
         created_at, updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
        (code, doc_ref, (f.get('raised_at') or _today()), (f.get('customer_name') or '').strip(),
         desc, (f.get('business_value') or '').strip(), (f.get('acceptance') or '').strip(),
         (f.get('demand_type') or '新增功能'), '', '', (f.get('version_no') or ''),
         1 if f.get('is_custom') else 0, (f.get('custom_customer') or '').strip(),
         '待过滤', (f.get('note') or '').strip(), prod, now, now))
    _safe_estimate(db, code, f.get('estimate'))
    db.commit()
    sims = _similar(db, desc, product=prod)
    _log('新增需求', f'{code} {desc[:30]}')
    tip = f'已录入需求 {code}，当前状态「待过滤」。'
    if sims:
        tip += ' 疑似重复：' + '、'.join(f'{s[0]}({s[2]})' for s in sims) + '，请在过滤时确认。'
    flash(tip, 'warning' if sims else 'success')
    return redirect(url_for('version_plan.demands', status='待过滤', product=prod))


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
        cwhere, cargs = _prod_where(d['product_code'] or '')
        cwsql = ('WHERE ' + cwhere) if cwhere else ''
        customers = db.execute(f'SELECT name FROM {_t(L_CUSTOMERS)} {cwsql} ORDER BY name', cargs).fetchall()
        versions = db.execute(f'SELECT version_no, status FROM {_t(L_VERSIONS)} {cwsql} ORDER BY id DESC', cargs).fetchall()
        logs = db.execute(f'SELECT * FROM {_t(L_LOGS)} WHERE obj_no=? ORDER BY id DESC LIMIT 20',
                          (code,)).fetchall()
        return render_template('vp_demand_form.html', d=d, customers=customers, versions=versions,
                               products=_product_rows(db), product=d['product_code'] or '',
                               dtypes=DEMAND_TYPE, statuses=DEMAND_STATUS,
                               sims=_similar(db, d['description'], exclude_id=d['id'],
                                             product=d['product_code'] or ''),
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
    # 产品归属：未排期前可调整；已排期/已发布锁定，避免跨产品串版本
    if d['status'] in ('已排期', '已发布'):
        new_prod = d['product_code'] or ''
    else:
        new_prod = (f.get('product_code') or '').strip()
        if new_prod != (d['product_code'] or ''):
            if new_prod and not _ensure_product(db, new_prod):
                flash('所选产品不存在，请重新选择', 'error')
                return redirect(url_for('version_plan.demand_edit', code=code))
    db.execute(
        f'''UPDATE {_t(L_DEMANDS)} SET doc_ref=?, raised_at=?, customer_name=?, description=?,
        business_value=?, acceptance=?, demand_type=?, is_custom=?, custom_customer=?, note=?,
        product_code=?, updated_at=?
        WHERE code=?''',
        (doc_ref, (f.get('raised_at') or '').strip(), (f.get('customer_name') or '').strip(),
         (f.get('description') or '').strip(), (f.get('business_value') or '').strip(),
         (f.get('acceptance') or '').strip(), (f.get('demand_type') or '新增功能'),
         1 if f.get('is_custom') else 0, (f.get('custom_customer') or '').strip(),
         (f.get('note') or '').strip(), new_prod, _now(), code))
    _safe_estimate(db, code, f.get('estimate'))
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
    # 挂起复活提示：同源需求被多个客户提出（同产品内比对）
    tips = []
    if new_status in ('规划池', '挂起'):
        sims = _similar(db, d['description'], exclude_id=d['id'], product=d['product_code'] or '')
        for s in sims:
            if s[1] and s[3] and s[3] != (d['customer_name'] or ''):
                tips.append(f'与 {s[0]}（客户 {s[3]}）描述相似度 {s[2]}，属多个客户提出，建议重新评估优先级')
    flash(f'{code} 已判定为「{result}」。' + (' '.join(tips) if tips else ''), 'success')
    return redirect(url_for('version_plan.demands', status='待过滤', product=d['product_code'] or ''))


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
    if (o['product_code'] or '') != (d['product_code'] or ''):
        flash(f'{other} 属于另一产品，只能在同一产品内标记重复/冲突关系', 'error')
        return redirect(url_for('version_plan.demand_edit', code=code))
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
        'product_code': ['产品', '产品名称', '所属产品', 'product_code'],
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

    default_prod = _req_product()
    pmap = _product_map(db)
    name2code = {p['name']: p['code'] for p in pmap.values()}

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
        # 产品归属：CSV 产品名/编号优先，否则沿用当前筛选产品
        prod_raw = pick(row, 'product_code')
        prod = name2code.get(prod_raw, prod_raw) if prod_raw else default_prod
        if prod and prod not in pmap:
            prod = default_prod
        code = _next_code(db)
        sims = _similar(db, desc, product=prod)
        db.execute(
            f'''INSERT INTO {_t(L_DEMANDS)}
            (code, doc_ref, raised_at, customer_name, description, business_value, acceptance,
             demand_type, status, is_custom, note, product_code, created_at, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
            (code, doc_ref, pick(row, 'raised_at') or _today(), pick(row, 'customer_name'),
             desc, pick(row, 'business_value'), pick(row, 'acceptance'),
             pick(row, 'demand_type') or '新增功能', '待过滤',
             1 if pick(row, 'is_custom') in ('是', '1', 'Y', 'y', 'true') else 0,
             'CSV 导入', prod, now, now))
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
    return redirect(url_for('version_plan.demands', status='待过滤', product=default_prod))


@blueprint.route('/version_plan/demands/export')
def demand_export():
    _ensure_tables()
    db = get_db()
    prod = _req_product()
    where, args = _prod_where(prod)
    wsql = ('WHERE ' + where) if where else ''
    rows = db.execute(f'SELECT * FROM {_t(L_DEMANDS)} {wsql} ORDER BY id', args).fetchall()
    pmap = _product_map(db)
    out = io.StringIO()
    w = csv.writer(out)
    head = ['产品', '需求编号', '云文档溯源', '提出日期', '来源客户', '需求描述', '业务价值', '验收标准',
            '需求类型', '过滤结论', '过滤备注', '排入版本', '是否定制', '定制客户',
            '关联需求', '关系', '状态', '备注']
    w.writerow(head)
    for r in rows:
        w.writerow([(pmap.get(r['product_code'], {}) or {}).get('name', '未分配'),
                    r['code'], r['doc_ref'], r['raised_at'], r['customer_name'], r['description'],
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
    prod = _req_product()
    where, args = _prod_where(prod)
    wsql = ('WHERE ' + where) if where else ''
    rows = [dict(r) for r in db.execute(
        f'SELECT * FROM {_t(L_VERSIONS)} {wsql} '
        "ORDER BY CASE status WHEN '测试中' THEN 0 WHEN '开发中' THEN 1 WHEN '已冻结' THEN 2 "
        "WHEN '规划中' THEN 3 WHEN '已发布' THEN 4 ELSE 5 END, release_date ASC", args).fetchall()]
    counts = {}
    for r in rows:
        counts[r['version_no']] = db.execute(
            f'SELECT COUNT(*) c FROM {_t(L_DEMANDS)} WHERE version_no=? AND product_code=?',
            (r['version_no'], r['product_code'] or '')).fetchone()['c']
        r['stage'] = STAGE_MAP.get(r['status'], 0)
    # 规划池只列同产品
    pool = db.execute(
        f'SELECT * FROM {_t(L_DEMANDS)} WHERE status="规划池"{" AND product_code=?" if prod else ""} ORDER BY id',
        [prod] if prod else []).fetchall()
    customers = db.execute(
        f'SELECT name, integrating_version_no FROM {_t(L_CUSTOMERS)} {wsql} ORDER BY name', args).fetchall()
    return render_template('vp_versions.html', rows=rows, counts=counts, pool=pool,
                           customers=customers, statuses=VERSION_STATUS, vtypes=VERSION_TYPE,
                           product=prod, products=_product_rows(db), prod_map=_product_map(db),
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
    prod = (f.get('product_code') or '').strip()
    if not prod:
        flash('请先选择该版本所属的产品', 'error')
        return redirect(url_for('version_plan.versions', product=_req_product()))
    if not _ensure_product(db, prod):
        flash('所选产品不存在，请重新选择', 'error')
        return redirect(url_for('version_plan.versions', product=prod))
    if not re.fullmatch(r'[0-9]+(?:\.[0-9A-Za-z]+){2,}(?:-[0-9A-Za-z._]+)?', no):
        flash(f'版本号「{no}」格式不对：请用 主.次.修订（如 1.2.0），定制版可加客户后缀（如 1.2.0-A）', 'error')
        return redirect(url_for('version_plan.versions', product=prod))
    # 版本号「产品内唯一」（DB 层 UNIQUE(product_code, version_no) 兜底），创建后不可改（R4）
    exists = db.execute(
        f'SELECT 1 FROM {_t(L_VERSIONS)} WHERE version_no=? AND product_code=?', (no, prod)).fetchone()
    if exists:
        flash(f'产品 {_product_name(db, prod)} 下版本号 {no} 已存在（产品内唯一且创建后不可改，R4）', 'error')
        return redirect(url_for('version_plan.versions', product=prod))
    # R2：定制版本必须填合回目标与期限
    if vtype == '客户定制':
        if not (f.get('merge_target_no') or '').strip() or not (f.get('merge_deadline') or '').strip():
            flash('客户定制版本必须填写「计划合回主线的目标版本」和「合回期限」才能创建（R2）', 'error')
            return redirect(url_for('version_plan.versions', product=prod))
    now = _now()
    db.execute(
        f'''INSERT INTO {_t(L_VERSIONS)}
        (version_no, vtype, product_code, base_version_no, freeze_date, test_date, release_date, modules,
         interface_change, regression_scope, known_issues, target_customers, upgrade_path,
         merge_target_no, merge_deadline, status, created_at, updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
        (no, vtype, prod, (f.get('base_version_no') or '').strip(), (f.get('freeze_date') or '').strip(),
         (f.get('test_date') or '').strip(), (f.get('release_date') or '').strip(),
         ','.join(request.form.getlist('modules')), (f.get('interface_change') or '').strip(),
         (f.get('regression_scope') or '').strip(), (f.get('known_issues') or '').strip(),
         ','.join(request.form.getlist('customers')), (f.get('upgrade_path') or '').strip(),
         (f.get('merge_target_no') or '').strip() if vtype == '客户定制' else '',
         (f.get('merge_deadline') or '').strip() if vtype == '客户定制' else '',
         '规划中', now, now))
    db.commit()
    _ulog('版本', no, '创建', f'类型 {vtype}（产品 {_product_name(db, prod)}）')
    _log('创建版本', no)
    flash(f'已创建版本 {no}（规划中）。去版本详情勾选规划池需求拉进来。', 'success')
    return redirect(url_for('version_plan.versions', product=prod))


@blueprint.route('/version_plan/versions/<no>', methods=['GET', 'POST'])
def version_detail(no):
    _ensure_tables()
    db = get_db()
    v = _vrow(db, no, _req_product())
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
            rollback_plan=?, base_version_no=?, updated_at=? WHERE version_no=? AND product_code=?''',
            ((f.get('freeze_date') or '').strip(), (f.get('test_date') or '').strip(),
             (f.get('release_date') or '').strip(), ','.join(request.form.getlist('modules')),
             (f.get('interface_change') or '').strip(), (f.get('regression_scope') or '').strip(),
             (f.get('known_issues') or '').strip(), ','.join(request.form.getlist('customers')),
             (f.get('upgrade_path') or '').strip(), (f.get('rollback_plan') or '').strip(),
             (f.get('base_version_no') or '').strip(),
             _now(), no, v['product_code'] or ''))
        db.commit()
        _ulog('版本', no, '编辑', '更新里程碑/元数据（版本号不可改）')
        flash(f'{no} 已保存（版本号不可修改，R4）', 'success')
        return redirect(url_for('version_plan.version_detail', no=no, product=v['product_code'] or None))
    ds = db.execute(f'SELECT * FROM {_t(L_DEMANDS)} WHERE version_no=? AND product_code=? ORDER BY id',
                    (no, v['product_code'] or '')).fetchall()
    pool = db.execute(
        f'SELECT * FROM {_t(L_DEMANDS)} WHERE status="规划池" AND product_code=? ORDER BY id',
        (v['product_code'] or '',)).fetchall()
    customers = db.execute(
        f'SELECT name FROM {_t(L_CUSTOMERS)} WHERE product_code=? ORDER BY name',
        (v['product_code'] or '',)).fetchall()
    release = _rel_row(db, no, v['product_code'] or '')
    logs = db.execute(f'SELECT * FROM {_t(L_LOGS)} WHERE obj_no=? ORDER BY id DESC LIMIT 20',
                      (no,)).fetchall()
    frozen = v['status'] == '已冻结'
    can_edit = _can_manage() and v['status'] in ('规划中', '已冻结')
    from .trains import CHECK_ITEMS, ensure_checks, ensure_signoffs, release_gate
    ensure_checks(db, no, v['product_code'] or '')
    roles = ensure_signoffs(db, no, v['product_code'] or '', v['vtype'])
    gate = release_gate(db, no, v['product_code'] or '', v['vtype'])
    return render_template('vp_version_detail.html', v=v, ds=ds, pool=pool, customers=customers,
                           gate=gate, checks=gate['checks'], signs=gate['signs'],
                           sign_roles=roles, check_items=CHECK_ITEMS,
                           release=release, logs=logs, frozen=frozen, can_edit=can_edit,
                           prod_map=_product_map(db), prod_name=_product_name(db, v['product_code'] or ''),
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
    v = _vrow(db, no, (request.form.get('product') or request.args.get('product') or '').strip())
    if not v:
        flash('版本不存在', 'error')
        return redirect(url_for('version_plan.versions'))
    # R1：冻结后不可增删
    if v['status'] == '已冻结':
        flash(f'{no} 已冻结，不能再增删需求（R1）。确需调整请由平台管理员强制解冻并留痕。', 'error')
        return redirect(url_for('version_plan.version_detail', no=no, product=v['product_code'] or None))
    if v['status'] not in ('规划中',):
        flash(f'{no} 当前状态「{v["status"]}」，只有「规划中」的版本能调整需求构成', 'error')
        return redirect(url_for('version_plan.version_detail', no=no, product=v['product_code'] or None))
    codes = [c.strip() for c in request.form.getlist('codes') if c.strip()]
    if not codes:
        flash('请先勾选要拉入的规划池需求', 'error')
        return redirect(url_for('version_plan.version_detail', no=no, product=v['product_code'] or None))
    added, blocked, blocked_prod = [], [], []
    for code in codes:
        d = db.execute(f'SELECT * FROM {_t(L_DEMANDS)} WHERE code=?', (code,)).fetchone()
        if not d:
            continue
        if (d['product_code'] or '') != (v['product_code'] or ''):   # 同产品隔离
            blocked_prod.append(code)
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
    if blocked_prod:
        msg += f'；{len(blocked_prod)} 条属于其他产品，不能拉入本版本：{"、".join(blocked_prod)}'
    _ulog('版本', no, '拉入需求', ','.join(added))
    flash(msg, 'warning' if blocked else 'success')
    return redirect(url_for('version_plan.version_detail', no=no, product=v['product_code'] or None))


@blueprint.route('/version_plan/versions/<no>/remove', methods=['POST'])
def version_remove(no):
    _ensure_tables()
    if not _can_manage():
        return _deny()
    db = get_db()
    v = _vrow(db, no, (request.form.get('product') or request.args.get('product') or '').strip())
    if not v:
        flash('版本不存在', 'error')
        return redirect(url_for('version_plan.versions'))
    if v['status'] == '已冻结':
        flash(f'{no} 已冻结，不能再增删需求（R1）', 'error')
        return redirect(url_for('version_plan.version_detail', no=no, product=v['product_code'] or None))
    if v['status'] != '规划中':
        flash(f'{no} 当前状态「{v["status"]}」，不能调整需求构成', 'error')
        return redirect(url_for('version_plan.version_detail', no=no, product=v['product_code'] or None))
    code = (request.form.get('code') or '').strip()
    db.execute(f'UPDATE {_t(L_DEMANDS)} SET version_no="", status="规划池", updated_at=? WHERE code=?',
               (_now(), code))
    db.commit()
    _ulog('版本', no, '移出需求', code)
    flash(f'已把 {code} 移出 {no}，回到规划池', 'success')
    return redirect(url_for('version_plan.version_detail', no=no, product=v['product_code'] or None))


@blueprint.route('/version_plan/versions/<no>/status', methods=['POST'])
def version_status(no):
    _ensure_tables()
    if not _can_manage():
        return _deny()
    db = get_db()
    v = _vrow(db, no, (request.form.get('product') or request.args.get('product') or '').strip())
    if not v:
        flash('版本不存在', 'error')
        return redirect(url_for('version_plan.versions'))
    target = (request.form.get('to') or '').strip()

    # 解冻：仅平台管理员，且留痕（R1）
    if target == '规划中' and v['status'] == '已冻结':
        if not _is_platform():
            flash('解冻只能由平台管理员执行（R1）', 'error')
            return redirect(url_for('version_plan.version_detail', no=no, product=v['product_code'] or None))
        reason = (request.form.get('reason') or '').strip()
        if not reason:
            flash('强制解冻必须填写原因，便于留痕（R1）', 'error')
            return redirect(url_for('version_plan.version_detail', no=no, product=v['product_code'] or None))
        log = (v['unfreeze_log'] or '')
        log += f'[{_now()}] {session.get("username")} 强制解冻：{reason}\n'
        db.execute(f'UPDATE {_t(L_VERSIONS)} SET status="规划中", unfreeze_log=?, updated_at=? '
                   'WHERE version_no=? AND product_code=?', (log, _now(), no, v['product_code'] or ''))
        db.commit()
        _ulog('版本', no, '强制解冻', reason)
        _log('版本解冻', f'{no} 原因：{reason}')
        flash(f'{no} 已强制解冻，原因已记入留痕（R1）', 'warning')
        return redirect(url_for('version_plan.version_detail', no=no, product=v['product_code'] or None))

    allowed = VERSION_FLOW.get(v['status'], [])
    if target not in allowed:
        flash(f'状态不能从「{v["status"]}」直接到「{target}」；下一步只能是：{"、".join(allowed) or "无"}', 'error')
        return redirect(url_for('version_plan.version_detail', no=no, product=v['product_code'] or None))
    extra = ''
    if target == '已冻结':
        cnt = db.execute(f'SELECT COUNT(*) c FROM {_t(L_DEMANDS)} WHERE version_no=?', (no,)).fetchone()['c']
        if cnt == 0:
            flash('版本里还没有需求，不能冻结', 'error')
            return redirect(url_for('version_plan.version_detail', no=no, product=v['product_code'] or None))
        db.execute(f'UPDATE {_t(L_VERSIONS)} SET status="已冻结", frozen_at=?, frozen_by=?, updated_at=? '
                   'WHERE version_no=? AND product_code=?',
                   (_now(), session.get('username', ''), _now(), no, v['product_code'] or ''))
        extra = '冻结后需求构成不可再增删（R1）。'
    elif target == '已发布':
        # v13.0.1：发布受控 —— 确认清单 7 项全通过 + 三方（定制版四方）签核，缺一不可
        from .trains import release_gate
        gate = release_gate(db, no, v['product_code'] or '', v['vtype'])
        if not gate['ok']:
            flash('发布门禁未通过：' + '；'.join(gate['reasons']), 'error')
            return redirect(url_for('version_plan.version_detail', no=no, product=v['product_code'] or None))
        db.execute(f'UPDATE {_t(L_VERSIONS)} SET status="已发布", released_at=?, updated_at=? '
                   'WHERE version_no=? AND product_code=?',
                   (_now(), _now(), no, v['product_code'] or ''))
        # 需求状态自动推进到已发布（仅本产品本版本，不波及其它产品的同号版本）
        prod = v['product_code'] or ''
        n = db.execute(f'SELECT COUNT(*) c FROM {_t(L_DEMANDS)} WHERE version_no=? AND product_code=?',
                       (no, prod)).fetchone()['c']
        db.execute(f'UPDATE {_t(L_DEMANDS)} SET status="已发布", updated_at=? '
                   'WHERE version_no=? AND product_code=?', (_now(), no, prod))
        # 自动生成 Release Note 草稿；回滚方案随发布留档（缺省「按发布方案回滚」）
        draft = _build_release(db, no, prod)
        rb = (v['rollback_plan'] or '').strip() or '按发布方案回滚'
        rel = _rel_row(db, no, prod)
        if rel:
            db.execute(f'UPDATE {_t(L_RELEASES)} SET content=?, rollback_plan=?, updated_at=? '
                       'WHERE version_no=? AND product_code=?', (draft, rb, _now(), no, prod))
        else:
            db.execute(f'INSERT INTO {_t(L_RELEASES)} '
                       '(version_no, product_code, content, status, rollback_plan, created_at, updated_at) '
                       'VALUES (?,?,?,?,?,?,?)', (no, prod, draft, '草稿', rb, _now(), _now()))
        # 关联车次联动置「已发布」（按需求-车次装载记录反查）
        train_codes = [r['train_code'] for r in db.execute(
            f'SELECT DISTINCT i.train_code FROM {_t(L_ITEMS)} i '
            f'JOIN {_t(L_DEMANDS)} d ON d.code=i.demand_code '
            'WHERE d.version_no=? AND d.product_code=? AND i.state=?',
            (no, prod, '已装')).fetchall()]
        for tc in train_codes:
            db.execute(f'UPDATE {_t(L_TRAINS)} SET status="已发布", updated_at=? WHERE code=?',
                       (_now(), tc))
        # 客户在用版本自动更新（同产品内定位客户）
        for cust in [c for c in (v['target_customers'] or '').split(',') if c]:
            c0 = db.execute(
                f'SELECT * FROM {_t(L_CUSTOMERS)} WHERE name=? AND product_code=?',
                (cust, v['product_code'] or '')).fetchone()
            if c0:
                db.execute(f'UPDATE {_t(L_CUSTOMERS)} SET current_version_no=?, updated_at=? WHERE name=? AND product_code=?',
                           (no, _now(), cust, v['product_code'] or ''))
        extra = f'{n} 条需求已自动变为「已发布」，Release Note 草稿已生成，客户在用版本已更新。'
    else:
        db.execute(f'UPDATE {_t(L_VERSIONS)} SET status=?, updated_at=? '
                   'WHERE version_no=? AND product_code=?', (target, _now(), no, v['product_code'] or ''))
    db.commit()
    _ulog('版本', no, f'状态→{target}', extra)
    _log('版本状态', f'{no} → {target}')
    flash(f'{no} 状态已变为「{target}」。{extra}', 'success')
    return redirect(url_for('version_plan.version_detail', no=no, product=v['product_code'] or None))


def _build_release(db, no, prod=''):
    """根据版本数据自动生成 Release Note 草稿"""
    v = db.execute(f'SELECT * FROM {_t(L_VERSIONS)} WHERE version_no=? AND product_code=?',
                   (no, prod or '')).fetchone()
    ds = db.execute(f'SELECT * FROM {_t(L_DEMANDS)} WHERE version_no=? AND product_code=? ORDER BY id',
                    (no, prod or '')).fetchall()
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
    lines += ['## 八、回滚方案',
              (v['rollback_plan'] if v and v['rollback_plan'] else '- 按发布方案回滚'), '']
    return '\n'.join(lines)


@blueprint.route('/version_plan/versions/<no>/release', methods=['POST'])
def version_release(no):
    _ensure_tables()
    if not _can_manage():
        return _deny()
    db = get_db()
    v = _vrow(db, no, (request.form.get('product') or request.args.get('product') or '').strip())
    if not v:
        flash('版本不存在', 'error')
        return redirect(url_for('version_plan.versions'))
    act = (request.form.get('act') or 'save').strip()
    rel = _rel_row(db, no, v['product_code'] or '')
    if act == 'regen':
        if rel and rel['status'] == '定稿':
            flash('已定稿的发布说明不能被覆盖，如需重生成请先取消定稿', 'error')
            return redirect(url_for('version_plan.version_detail', no=no, product=v['product_code'] or None))
        draft = _build_release(db, no, v['product_code'] or '')
        if rel:
            db.execute(f'UPDATE {_t(L_RELEASES)} SET content=?, updated_at=? WHERE version_no=? AND product_code=?',
                       (draft, _now(), no, v['product_code'] or ''))
        else:
            db.execute(f'INSERT INTO {_t(L_RELEASES)} (version_no, product_code, content, status, created_at, updated_at) '
                       'VALUES (?,?,?,?,?,?)', (no, v['product_code'] or '', draft, '草稿', _now(), _now()))
        db.commit()
        flash('已按当前版本数据重新生成草稿', 'success')
    elif act == 'save':
        content = request.form.get('content') or ''
        if rel:
            if rel['status'] == '定稿':
                flash('已定稿，不能再改；如需修改请先「取消定稿」', 'error')
                return redirect(url_for('version_plan.version_detail', no=no, product=v['product_code'] or None))
            db.execute(f'UPDATE {_t(L_RELEASES)} SET content=?, updated_at=? WHERE version_no=? AND product_code=?',
                       (content, _now(), no, v['product_code'] or ''))
        else:
            db.execute(f'INSERT INTO {_t(L_RELEASES)} (version_no, product_code, content, status, created_at, updated_at) '
                       'VALUES (?,?,?,?,?,?)', (no, v['product_code'] or '', content, '草稿', _now(), _now()))
        db.commit()
        _ulog('版本', no, '保存发布说明', '草稿')
        flash('发布说明草稿已保存', 'success')
    elif act == 'finalize':
        if not rel or not (rel['content'] or '').strip():
            flash('还没有内容，不能定稿', 'error')
            return redirect(url_for('version_plan.version_detail', no=no, product=v['product_code'] or None))
        db.execute(f'UPDATE {_t(L_RELEASES)} SET status="定稿", finalized_by=?, updated_at=? '
                   'WHERE version_no=? AND product_code=?', (session.get('username', ''), _now(), no, v['product_code'] or ''))
        db.commit()
        _ulog('版本', no, '定稿发布说明', '')
        flash('发布说明已定稿，可导出 Markdown 发给客户', 'success')
    elif act == 'unfinalize':
        if not _is_platform() and not _can_manage():
            return _deny()
        db.execute(f'UPDATE {_t(L_RELEASES)} SET status="草稿", updated_at=? WHERE version_no=? AND product_code=?',
                   (_now(), no, v['product_code'] or ''))
        db.commit()
        _ulog('版本', no, '取消定稿', '')
        flash('已取消定稿，可继续编辑', 'warning')
    return redirect(url_for('version_plan.version_detail', no=no, product=v['product_code'] or None))


@blueprint.route('/version_plan/versions/<no>/release.md')
def release_md(no):
    _ensure_tables()
    db = get_db()
    v = _vrow(db, no, (request.args.get('product') or request.form.get('product') or '').strip())
    if not v:
        flash('版本不存在', 'error')
        return redirect(url_for('version_plan.versions'))
    rel = _rel_row(db, no, v['product_code'] or '')
    if not rel:
        flash('这个版本还没有发布说明，先在版本详情里生成草稿', 'error')
        return redirect(url_for('version_plan.version_detail', no=no, product=v['product_code'] or None))
    text = rel['content'] or ''
    return Response(text.encode('utf-8'), mimetype='text/markdown',
                    headers={'Content-Disposition': f'attachment; filename=ReleaseNote_{no}.md'})


# ---------------------------------------------------------------- 客户与矩阵
@blueprint.route('/version_plan/customers', methods=['GET', 'POST'])
def customers():
    _ensure_tables()
    db = get_db()
    prod = _req_product()
    if request.method == 'POST':
        if not _can_manage():
            return _deny()
        f = request.form
        name = (f.get('name') or '').strip()
        prod = (f.get('product_code') or '').strip()
        if not name:
            flash('客户名称必填', 'error')
            return redirect(url_for('version_plan.customers', product=prod))
        if prod and not _ensure_product(db, prod):
            flash('所选产品不存在，请重新选择', 'error')
            return redirect(url_for('version_plan.customers'))
        itg = (f.get('integrating_version_no') or '').strip()
        if itg:
            row = db.execute(
                f'SELECT name, integrating_version_no FROM {_t(L_CUSTOMERS)} '
                'WHERE integrating_version_no=? AND name<>? AND product_code=?',
                (itg, name, prod)).fetchall()
            if row:
                flash(f'「{itg}」已被客户 {row[0]["name"]} 标为集成中，同一版本同时只能给一个客户做集成（R4）', 'error')
                return redirect(url_for('version_plan.customers', product=prod))
        # 定制分支必须带合回目标与期限（R2）
        cb = (f.get('custom_branch_no') or '').strip()
        if cb and (not (f.get('merge_target_no') or '').strip() or not (f.get('merge_deadline') or '').strip()):
            flash('填了定制分支版本，就必须同时填「合回目标版本」和「合回期限」（R2）', 'error')
            return redirect(url_for('version_plan.customers', product=prod))
        now = _now()
        is_edit = f.get('mode') == 'edit'
        old = db.execute(
            f'SELECT * FROM {_t(L_CUSTOMERS)} WHERE name=?',
            (name,)).fetchone()
        vals = (itg, cb, (f.get('merge_target_no') or '').strip(),
                (f.get('merge_deadline') or '').strip(), (f.get('contact') or '').strip(),
                (f.get('note') or '').strip(), (f.get('current_version_no') or '').strip(), now, name, prod)
        if is_edit:
            if not old:
                flash(f'客户「{name}」不存在，无法编辑', 'error')
                return redirect(url_for('version_plan.customers', product=prod))
            db.execute(f'''UPDATE {_t(L_CUSTOMERS)} SET integrating_version_no=?, custom_branch_no=?,
                        merge_target_no=?, merge_deadline=?, contact=?, note=?, current_version_no=?,
                        product_code=?, updated_at=? WHERE name=?''',
                       (itg, cb, (f.get('merge_target_no') or '').strip(),
                        (f.get('merge_deadline') or '').strip(), (f.get('contact') or '').strip(),
                        (f.get('note') or '').strip(), (f.get('current_version_no') or '').strip(),
                        prod, now, name))
        else:
            # 客户名全局唯一校验（DB 层 UNIQUE 兜底）；新建重名一律拦截，不静默转成编辑
            if old:
                flash(f'已有客户「{name}」，请改名或在列表里点「编辑」更新', 'error')
                return redirect(url_for('version_plan.customers', product=prod))
            db.execute(f'''INSERT INTO {_t(L_CUSTOMERS)}
                        (integrating_version_no, custom_branch_no, merge_target_no, merge_deadline,
                         contact, note, current_version_no, updated_at, name, product_code, created_at)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?)''', vals + (now,))
        db.commit()
        _ulog('客户', name, '保存', f'产品 {_product_name(db, prod)}')
        flash(f'客户「{name}」已保存', 'success')
        return redirect(url_for('version_plan.customers', product=prod))
    where, args = _prod_where(prod)
    wsql = ('WHERE ' + where) if where else ''
    rows = db.execute(f'SELECT * FROM {_t(L_CUSTOMERS)} {wsql} ORDER BY name', args).fetchall()
    versions = db.execute(
        f'SELECT version_no, status, release_date FROM {_t(L_VERSIONS)} {wsql} ORDER BY id DESC', args).fetchall()
    warn = [r for r in rows if r['custom_branch_no'] and r['merge_deadline'] and r['merge_deadline'] < _today()]
    edit_name = (request.args.get('edit') or '').strip()
    edit_row = db.execute(
        f'SELECT * FROM {_t(L_CUSTOMERS)} WHERE name=?{" AND product_code=?" if prod else ""}',
        [edit_name] + ([prod] if prod else [])).fetchone() if edit_name else None
    return render_template('vp_customers.html', rows=rows, versions=versions, warn=warn,
                           products=_product_rows(db), product=prod, prod_map=_product_map(db),
                           can_manage=_can_manage(), today=_today(), edit_row=edit_row)


# ---------------------------------------------------------------- 客户回告
@blueprint.route('/version_plan/report')
def report():
    _ensure_tables()
    db = get_db()
    cust = (request.args.get('customer') or '').strip()
    prod = _req_product()
    where, args = _prod_where(prod)
    cwhere, cargs = _prod_where(prod)
    cwsql = ('WHERE ' + cwhere) if cwhere else ''
    customers = db.execute(f'SELECT name FROM {_t(L_CUSTOMERS)} {cwsql} ORDER BY name', cargs).fetchall()
    if not cust and customers:
        cust = customers[0]['name']
    rows = []
    if cust:
        q = f'SELECT * FROM {_t(L_DEMANDS)} WHERE customer_name=?{" AND product_code=?" if prod else ""} ORDER BY id'
        rows = db.execute(q, [cust] + ([prod] if prod else [])).fetchall()
    # 汇总
    summary = {}
    for r in rows:
        summary[r['status']] = summary.get(r['status'], 0) + 1
    text = _report_text(db, cust, rows) if cust else ''
    release_map = {r['version_no']: (r['release_date'] or '待定')
                   for r in db.execute(f'SELECT version_no, release_date FROM {_t(L_VERSIONS)} {cwsql}', cargs).fetchall()}
    return render_template('vp_report.html', customers=customers, cust=cust, rows=rows,
                           product=prod, products=_product_rows(db), prod_map=_product_map(db),
                           summary=summary, text=text, can_manage=_can_manage(), release_map=release_map)


def _report_text(db, cust, rows, prod=''):
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
            v = db.execute(
                f'SELECT release_date, status FROM {_t(L_VERSIONS)} WHERE version_no=?'
                ' AND product_code=?', (r['version_no'], r['product_code'] or '')).fetchone()
            eta = (v['release_date'] if v and v['release_date'] else '待定')
            lines.append(f'  • {r["code"]}（溯源 {r["doc_ref"]}）{r["description"]}｜版本 {r["version_no"]}｜预计发布 {eta}')
        lines.append('')
    if other:
        lines.append('三、其它状态')
        for r in other:
            note = f'：{r["filter_note"]}' if r['filter_note'] else ''
            lines.append(f'  • {r["code"]}（溯源 {r["doc_ref"]}）{r["description"]}｜当前「{r["status"]}」{note}')
        lines.append('')
    if prod:
        cust_row = db.execute(
            f'SELECT * FROM {_t(L_CUSTOMERS)} WHERE name=? AND product_code=?', (cust, prod)).fetchone()
    else:
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
    prod = _req_product()
    rows = db.execute(
        f'SELECT * FROM {_t(L_DEMANDS)} WHERE customer_name=?{" AND product_code=?" if prod else ""} ORDER BY id',
        [cust] + ([prod] if prod else [])).fetchall() if cust else []
    return jsonify({'ok': True, 'customer': cust, 'text': _report_text(db, cust, rows, prod)})


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
    L_PRODUCTS: ('产品', 'id', 'created_at',
                 '一行=一个产品；code 产品编号；name 产品名；owner 负责人；status 启用/停用；'
                 'demands/versions/customers 均通过 product_code 归属到产品'),
    L_DEMANDS: ('需求（过滤+排期）', 'id', 'raised_at',
                '一行=一条需求；code 需求编号；product_code 所属产品；status：待过滤/规划池/挂起/已排期/已发布/不做；'
                'version_no 排入版本；doc_ref 云文档溯源；filter_result 过滤结论；is_custom 是否定制'),
    L_VERSIONS: ('版本', 'id', 'release_date',
                 '一行=一个版本；version_no 版本号；product_code 所属产品；status：规划中/已冻结/开发中/测试中/已发布/已归档；'
                 'vtype：迭代/热修复/客户定制；freeze_date/test_date/release_date 里程碑'),
    L_CUSTOMERS: ('客户与客户-版本矩阵', 'id', 'updated_at',
                  '一行=一个客户；product_code 所属产品；current_version_no 当前在用版本；integrating_version_no 集成中版本；'
                  'custom_branch_no 定制分支；merge_target_no/merge_deadline 计划合回与期限'),
    L_RELEASES: ('发布说明 Release Notes', 'id', 'updated_at',
                 '一行=一个版本的发布说明；同一版本号按 product_code 区分；status：草稿/定稿；content 为 Markdown 正文'),
    L_TRAINS: ('月版本火车（车次）', 'id', 'created_at',
               '一行=一个车次；code 如 P1-2607-01；product_code 所属产品；month 发车月；status：计划/停发/已转版本；'
               'capacity 人日容量；skip_reason 停发原因'),
    L_ITEMS: ('车次装载清单', 'id', 'added_at',
              '一行=一个需求在某车次的装载状态；train_code 车次；demand_code 需求；state：已装/候补/已移出；reason 原因'),
    L_LOADCHG: ('装车变更留痕', 'id', 'created_at',
                '一行=一次装车/移出/改挂操作；demand_code 需求；from_train/to_train 车次；action 动作；operator 操作人'),
    L_ASSESS: ('需求四维评估', 'id', 'created_at',
               '一行=一次评估；demand_code 需求；round 轮次；w_value/w_client/w_urgent/w_cost 四维 1-5 分；'
               'score 加权分；verdict：规划池/挂起/不做'),
    L_CHECKS: ('发布确认清单（7 项）', 'id', 'checked_at',
               '一行=某版本某清单项；version_no+product_code 双键；item_no 1-7；result：pending/通过/不通过；evidence 证据'),
    L_SIGNOFFS: ('发布签核', 'id', 'signed_at',
                 '一行=某版本某角色的签核；version_no+product_code 双键；role：产品负责人/测试负责人/发布经理/客户代表；verdict：同意/驳回'),
    L_NOTICES: ('客户回告记录', 'id', 'sent_at',
                '一行=一次客户回告；customer_name 客户；version_no 回告版本；content 回告正文；sent_at 发送时间'),
    L_CUSTPROD: ('客户-产品版本占用', 'id', 'updated_at',
                 '一行=某客户在某产品的版本占用；customer_id 客户；product_code 产品；in_use_version 在用版本；upgrading_version 升级中版本'),
}


def _openapi_meta(db, logical):
    """返回 (rows, fields)；rows 为总行数"""
    tbl = _t(logical)
    cnt = db.execute(f'SELECT COUNT(*) FROM {tbl}').fetchone()[0]
    cols = [r[1] for r in db.execute(f'PRAGMA table_info({tbl})').fetchall()]
    return cnt, cols


@blueprint.route('/version_plan/vp.css')
def vp_css():
    """插件样式独立路由：蓝图默认静态路径是 /static，与平台冲突会 404。"""
    from flask import send_from_directory
    return send_from_directory(os.path.join(os.path.dirname(__file__), 'static'), 'vp.css',
                               mimetype='text/css')


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


def ensure_schema():
    """v13.0.0.1：供平台启动自愈调用（app context 内执行，幂等）——
    插件表不再依赖"页面首次访问"才建，避免管理页出现"尚未建表"告警。"""
    _ensure_tables()
