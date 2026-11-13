# -*- coding: utf-8 -*-
"""版本规划管理插件 · 车次火车 / 装车工作台 / 评估 / 四方签核 / 客户回告

本模块与 routes.py 共用同一个 blueprint，随 __init__.py 顺序导入自动注册路由。
业务口径（与人日容量、按月发车一致）：
  - 车次（train）：一个产品一个月的一次发车窗口，容量单位=人日；
  - 装车（load）：把「规划池」需求装进车次，需求状态 → 已排期；
  - 停发（skip）：必须填原因，已装需求转「候补」，不占版本号，可恢复；
  - 改挂（move）：一步从旧车次移到新车次，单事务，两端都留痕；
  - 四方签核：产品 / 测试 / 发布经理（+ 定制版客户代表），与 7 项确认清单一并构成发布门禁。
"""
import threading
from datetime import date, datetime

from flask import flash, redirect, render_template, request, session, url_for

from .routes import (PID, _can_manage, _deny, _ensure_product, _log, _now,
                     _product_map, _product_name, _product_rows, _t, _today,
                     _ulog, _vrow, blueprint)

# 装车临界区：SQLite 单写者，平台单进程多线程，用进程内锁把「读容量→判断→写」串成一步
LOCK = threading.RLock()

TRAIN_STATUS = ['计划', '停发', '已转版本', '已发布']
SIGN_ROLES = ['产品负责人', '测试负责人', '发布经理']
CUSTOM_ROLE = '客户代表'
CHECK_ITEMS = [
    '范围完成 100%（装车需求全部交付）',
    '阻断/严重缺陷 = 0',
    '回归测试通过率 100%',
    '兼容性验证通过（现网版本/客户环境）',
    '文档齐备（发布说明/操作手册/变更记录）',
    '回滚方案可执行并演练过',
    '客户承诺确认（定制版必签）',
]
VERDICT_POOL, VERDICT_HOLD, VERDICT_NO = '进规划池', '挂起', '不做'


# ------------------------------------------------------------------ 基础查询
def _train(db, code):
    return db.execute(f'SELECT * FROM {_t("version_plan_lt_trains")} WHERE code=?', (code,)).fetchone()


def _items(db, code, state=None):
    sql = f'SELECT i.*, d.code dcode, d.description, d.status dstatus, d.estimate, d.score, ' \
          f'd.product_code, d.customer_name, d.demand_type FROM {_t("version_plan_lt_items")} i ' \
          f'LEFT JOIN {_t("version_plan_lt_demands")} d ON d.code=i.demand_code WHERE i.train_code=?'
    args = [code]
    if state:
        sql += ' AND i.state=?'
        args.append(state)
    sql += ' ORDER BY d.score DESC, i.id ASC'
    return [dict(r) for r in db.execute(sql, args).fetchall()]


def _used(db, code):
    r = db.execute(
        f'SELECT COALESCE(SUM(d.estimate),0) s, COUNT(*) c FROM {_t("version_plan_lt_items")} i '
        f'JOIN {_t("version_plan_lt_demands")} d ON d.code=i.demand_code '
        f'WHERE i.train_code=? AND i.state=?', (code, '已装')).fetchone()
    return float(r['s'] or 0), int(r['c'] or 0)


def _train_rows(db, prod='', month=''):
    sql = f'SELECT * FROM {_t("version_plan_lt_trains")} WHERE 1=1'
    args = []
    if prod:
        sql += ' AND product_code=?'
        args.append(prod)
    if month:
        sql += ' AND month=?'
        args.append(month)
    sql += ' ORDER BY month ASC, code ASC'
    rows = []
    for r in db.execute(sql, args).fetchall():
        d = dict(r)
        d['used'], d['cnt'] = _used(db, d['code'])
        d['pct'] = min(100, int(d['used'] / d['capacity'] * 100)) if d['capacity'] else 0
        rows.append(d)
    return rows


def _months(db, prod=''):
    sql = f'SELECT DISTINCT month FROM {_t("version_plan_lt_trains")}'
    args = []
    if prod:
        sql += ' WHERE product_code=?'
        args.append(prod)
    sql += ' ORDER BY month'
    return [r['month'] for r in db.execute(sql, args).fetchall() if r['month']]


def _record(db, demand, frm, to, action):
    db.execute(f'INSERT INTO {_t("version_plan_lt_load_changes")} '
               '(demand_code, from_train, to_train, action, operator, created_at) VALUES (?,?,?,?,?,?)',
               (demand, frm or '', to or '', action, session.get('username', ''), _now()))


# ------------------------------------------------------------------ 车次日历
@blueprint.route('/version_plan_lt/trains')
def trains():
    """车次日历：按月看板，支持停发/恢复/加车，容量条直观显示装载率。"""
    db = get_db_safe()
    prod = (request.args.get('product') or '').strip()
    month = (request.args.get('month') or '').strip()
    rows = _train_rows(db, prod, month)
    groups = {}
    for r in rows:
        groups.setdefault(r['month'] or '未排月', []).append(r)
    months = _months(db, prod)
    return render_template('vpl_trains.html', groups=groups, months=months, month=month,
                           product=prod, products=_product_rows(db), prod_map=_product_map(db),
                           can_manage=_can_manage(), today=_today(),
                           statuses=TRAIN_STATUS)


@blueprint.route('/version_plan_lt/trains/new', methods=['POST'])
def train_new():
    """建车次（加车）：编号自动按 产品-年月 递增，容量默认 40 人日。"""
    if not _can_manage():
        return _deny()
    db = get_db_safe()
    f = request.form
    prod = (f.get('product_code') or '').strip()
    month = (f.get('month') or '').strip()
    if not _ensure_product(db, prod):
        flash('请选择有效的产品', 'error')
        return redirect(url_for('version_plan_lt.trains'))
    if not month or not month.startswith('20'):
        flash('请填写发车月份（如 2026-11）', 'error')
        return redirect(url_for('version_plan_lt.trains', product=prod))
    try:
        cap = float(f.get('capacity') or 40)
    except ValueError:
        cap = 40.0
    ym = month.replace('-', '')[2:]
    base = f'{prod}-{ym}'
    n = 1
    while db.execute(f'SELECT 1 FROM {_t("version_plan_lt_trains")} WHERE code=?',
                     (f'{base}-{n:02d}',)).fetchone():
        n += 1
    code = (f.get('code') or '').strip() or f'{base}-{n:02d}'
    if db.execute(f'SELECT 1 FROM {_t("version_plan_lt_trains")} WHERE code=?', (code,)).fetchone():
        flash(f'车次 {code} 已存在', 'error')
        return redirect(url_for('version_plan_lt.trains', product=prod))
    now = _now()
    db.execute(f'INSERT INTO {_t("version_plan_lt_trains")} '
               '(code, product_code, month, status, release_day, capacity, capacity_unit, created_at, updated_at) '
               'VALUES (?,?,?,?,?,?,?,?,?)',
               (code, prod, month, '计划', (f.get('release_day') or '').strip(),
                cap, '人日', now, now))
    db.commit()
    _ulog('车次', code, '加车', f'{prod} {month} 容量 {cap} 人日')
    _log('车次加车', code)
    flash(f'已加开车次 {code}（容量 {cap:g} 人日）', 'success')
    return redirect(url_for('version_plan_lt.train_board', code=code))


@blueprint.route('/version_plan_lt/trains/<code>/skip', methods=['POST'])
def train_skip(code):
    """停发：原因必填；已装需求全部转候补，不占版本号；可随时恢复。"""
    if not _can_manage():
        return _deny()
    db = get_db_safe()
    t = _train(db, code)
    if not t:
        flash('车次不存在', 'error')
        return redirect(url_for('version_plan_lt.trains'))
    if t['status'] in ('已转版本', '已发布'):
        flash('该车次已转成版本或已发布，不能停发；如需停止发布请走版本解冻流程', 'error')
        return redirect(url_for('version_plan_lt.train_board', code=code))
    reason = (request.form.get('reason') or '').strip()
    if not reason:
        flash('停发必须填写原因（对客户与内部都要有交代）', 'error')
        return redirect(url_for('version_plan_lt.train_board', code=code))
    with LOCK:
        try:
            db.execute(f'UPDATE {_t("version_plan_lt_trains")} SET status="停发", skip_reason=?, updated_at=? '
                       'WHERE code=?', (reason, _now(), code))
            moved = 0
            for it in _items(db, code, '已装'):
                db.execute(f'UPDATE {_t("version_plan_lt_items")} SET state="候补", removed_at=?, reason=? '
                           'WHERE id=?', (_now(), '车次停发：' + reason, it['id']))
                db.execute(f'UPDATE {_t("version_plan_lt_demands")} SET status="规划池", version_no="", updated_at=? '
                           'WHERE code=?', (_now(), it['demand_code']))
                _record(db, it['demand_code'], code, '', '停发转候补')
                moved += 1
            db.commit()
        except Exception as e:
            db.rollback()
            flash('停发失败：' + str(e), 'error')
            return redirect(url_for('version_plan_lt.train_board', code=code))
    _ulog('车次', code, '停发', f'原因：{reason}；转候补 {moved} 条')
    _log('车次停发', f'{code} 原因：{reason}')
    flash(f'{code} 已停发（原因已记录），{moved} 条需求转候补，不占版本号', 'warning')
    return redirect(url_for('version_plan_lt.train_board', code=code))


@blueprint.route('/version_plan_lt/trains/<code>/resume', methods=['POST'])
def train_resume(code):
    """恢复发车：候补需求按容量重新装回，装不下的留候补。"""
    if not _can_manage():
        return _deny()
    db = get_db_safe()
    t = _train(db, code)
    if not t:
        flash('车次不存在', 'error')
        return redirect(url_for('version_plan_lt.trains'))
    with LOCK:
        try:
            db.execute(f'UPDATE {_t("version_plan_lt_trains")} SET status="计划", skip_reason="", updated_at=? '
                       'WHERE code=?', (_now(), code))
            used, _ = _used(db, code)
            back = 0
            for it in _items(db, code, '候补'):
                est = float(it.get('estimate') or 0)
                if used + est <= float(t['capacity']):
                    db.execute(f'UPDATE {_t("version_plan_lt_items")} SET state="已装", removed_at="" WHERE id=?',
                               (it['id'],))
                    db.execute(f'UPDATE {_t("version_plan_lt_demands")} SET status="已排期", version_no=?, updated_at=? '
                               'WHERE code=?', (code, _now(), it['demand_code']))
                    _record(db, it['demand_code'], code, code, '恢复回装')
                    used += est
                    back += 1
            db.commit()
        except Exception as e:
            db.rollback()
            flash('恢复失败：' + str(e), 'error')
            return redirect(url_for('version_plan_lt.train_board', code=code))
    _ulog('车次', code, '恢复', f'回装 {back} 条')
    _log('车次恢复', code)
    flash(f'{code} 已恢复发车，{back} 条候补需求装回，其余仍候补（容量不足）', 'success')
    return redirect(url_for('version_plan_lt.train_board', code=code))


@blueprint.route('/version_plan_lt/trains/<code>/to-version', methods=['POST'])
def train_to_version(code):
    """车次转版本：把整列车的装载清单固化成版本，后续走冻结/签核/发布。"""
    if not _can_manage():
        return _deny()
    db = get_db_safe()
    t = _train(db, code)
    if not t:
        flash('车次不存在', 'error')
        return redirect(url_for('version_plan_lt.trains'))
    if t['status'] in ('已转版本', '已发布'):
        flash('该车次已转过版本或已发布', 'error')
        return redirect(url_for('version_plan_lt.train_board', code=code))
    no = (request.form.get('version_no') or '').strip()
    if not no:
        flash('请填写版本号（主.次.修订）', 'error')
        return redirect(url_for('version_plan_lt.train_board', code=code))
    if db.execute(f'SELECT 1 FROM {_t("version_plan_lt_versions")} WHERE version_no=? AND product_code=?',
                  (no, t['product_code'])).fetchone():
        flash(f'{_product_name(db, t["product_code"])} 下版本号 {no} 已存在（同产品内唯一）', 'error')
        return redirect(url_for('version_plan_lt.train_board', code=code))
    with LOCK:
        try:
            now = _now()
            db.execute(f'INSERT INTO {_t("version_plan_lt_versions")} '
                       '(version_no, vtype, product_code, release_date, status, created_at, updated_at) '
                       'VALUES (?,?,?,?,?,?,?)',
                       (no, '迭代', t['product_code'], t['release_day'] or '', '规划中', now, now))
            for it in _items(db, code, '已装'):
                db.execute(f'UPDATE {_t("version_plan_lt_demands")} SET version_no=?, updated_at=? WHERE code=?',
                           (no, now, it['demand_code']))
            db.execute(f'UPDATE {_t("version_plan_lt_trains")} SET status="已转版本", merged_into=?, updated_at=? '
                       'WHERE code=?', (no, now, code))
            db.commit()
        except Exception as e:
            db.rollback()
            flash('转版本失败：' + str(e), 'error')
            return redirect(url_for('version_plan_lt.train_board', code=code))
    _ulog('车次', code, '转版本', no)
    _log('车次转版本', f'{code} → {no}')
    flash(f'{code} 已转成版本 {no}，去版本详情做冻结与签核', 'success')
    return redirect(url_for('version_plan_lt.version_detail', no=no))


# ------------------------------------------------------------- 装车工作台
@blueprint.route('/version_plan_lt/train-board')
def train_board():
    """装车工作台：车次时间轴 + 待装池 + 装载槽 + 留痕（三视图一屏可维护）。"""
    db = get_db_safe()
    prod = (request.args.get('product') or '').strip()
    code = (request.args.get('code') or '').strip()
    rows = _train_rows(db, prod)
    if not code and rows:
        code = next((r['code'] for r in rows if r['status'] == '计划'), rows[0]['code'])
    t = _train(db, code) if code else None
    loaded = _items(db, code, '已装') if t else []
    waiting = _items(db, code, '候补') if t else []
    pool_sql = (f'SELECT * FROM {_t("version_plan_lt_demands")} WHERE status="规划池"'
                f'{"" if not t else " AND (product_code=? OR product_code=\'\' OR product_code IS NULL)"} '
                'ORDER BY score DESC, id ASC')
    pool = [dict(r) for r in db.execute(pool_sql, [t['product_code']] if t else []).fetchall()]
    changes = []
    if t:
        changes = [dict(r) for r in db.execute(
            f'SELECT * FROM {_t("version_plan_lt_load_changes")} WHERE from_train=? OR to_train=? '
            'ORDER BY id DESC LIMIT 30', (code, code)).fetchall()]
    used, cnt = _used(db, code) if t else (0, 0)
    pct = min(100, int(used / float(t['capacity']) * 100)) if t and t['capacity'] else 0
    open_trains = [r for r in rows if r['status'] not in ('已转版本', '已发布') and r['code'] != code]
    return render_template('vpl_train_board.html', rows=rows, t=t, code=code, loaded=loaded,
                           waiting=waiting, pool=pool, changes=changes, used=used, cnt=cnt,
                           pct=pct, product=prod, products=_product_rows(db),
                           prod_map=_product_map(db), open_trains=open_trains,
                           can_manage=_can_manage(), today=_today())


@blueprint.route('/version_plan_lt/train-board/load', methods=['POST'])
def board_load():
    """装车：临界区内重算容量再写入，装不下直接拒绝（不超卖）。"""
    if not _can_manage():
        return _deny()
    db = get_db_safe()
    code = (request.form.get('code') or '').strip()
    dcode = (request.form.get('demand') or '').strip()
    t = _train(db, code)
    if not t or t['status'] in ('已转版本', '已发布'):
        flash('车次不存在、已转版本或已发布，不能再装', 'error')
        return redirect(url_for('version_plan_lt.train_board', code=code))
    if t['status'] == '停发':
        flash('该车次已停发，先恢复发车再装车', 'error')
        return redirect(url_for('version_plan_lt.train_board', code=code))
    with LOCK:
        try:
            d = db.execute(f'SELECT * FROM {_t("version_plan_lt_demands")} WHERE code=?', (dcode,)).fetchone()
            if not d:
                flash(f'需求 {dcode} 不存在', 'error')
                return redirect(url_for('version_plan_lt.train_board', code=code))
            if d['product_code'] and d['product_code'] != t['product_code']:
                flash(f'{dcode} 属于其它产品，不能装进本车次', 'error')
                return redirect(url_for('version_plan_lt.train_board', code=code))
            dup = db.execute(f'SELECT 1 FROM {_t("version_plan_lt_items")} WHERE train_code=? AND demand_code=? '
                             'AND state=?', (code, dcode, '已装')).fetchone()
            if dup:
                flash(f'{dcode} 已在本车次上，不用重复装', 'warning')
                return redirect(url_for('version_plan_lt.train_board', code=code))
            used, _ = _used(db, code)
            est = float(d['estimate'] or 0)
            if used + est > float(t['capacity']):
                flash(f'装不下：{code} 已装 {used:g}/{t["capacity"]:g} 人日，{dcode} 需要 {est:g} 人日 '
                      f'（还差 {used + est - float(t["capacity"]):g} 人日）', 'error')
                return redirect(url_for('version_plan_lt.train_board', code=code))
            # 已排期但挂在同产品其它未发布车次 → 允许一步改挂（先移出再装入）
            old = db.execute(
                f'SELECT i.* FROM {_t("version_plan_lt_items")} i JOIN {_t("version_plan_lt_trains")} tr '
                'ON tr.code=i.train_code WHERE i.demand_code=? AND i.state=? AND tr.status!=?',
                (dcode, '已装', '已转版本')).fetchone()
            if old:
                db.execute(f'UPDATE {_t("version_plan_lt_items")} SET state="已移出", removed_at=?, reason=? WHERE id=?',
                           (_now(), '改挂到 ' + code, old['id']))
                _record(db, dcode, old['train_code'], code, '改挂移出')
            db.execute(f'INSERT INTO {_t("version_plan_lt_items")} (train_code, demand_code, state, added_at) '
                       'VALUES (?,?,?,?)', (code, dcode, '已装', _now()))
            db.execute(f'UPDATE {_t("version_plan_lt_demands")} SET status="已排期", version_no=?, updated_at=? '
                       'WHERE code=?', (code, _now(), dcode))
            _record(db, dcode, old['train_code'] if old else '', code, '装车')
            db.commit()
        except Exception as e:
            db.rollback()
            flash('装车失败：' + str(e), 'error')
            return redirect(url_for('version_plan_lt.train_board', code=code))
    _ulog('车次', code, '装车', f'{dcode} {est:g} 人日')
    flash(f'{dcode} 已装进 {code}（{used + est:g}/{float(t["capacity"]):g} 人日）', 'success')
    return redirect(url_for('version_plan_lt.train_board', code=code))


@blueprint.route('/version_plan_lt/train-board/unload', methods=['POST'])
def board_unload():
    """移出：需求回到规划池，车次留痕。"""
    if not _can_manage():
        return _deny()
    db = get_db_safe()
    code = (request.form.get('code') or '').strip()
    dcode = (request.form.get('demand') or '').strip()
    t = _train(db, code)
    if not t:
        flash('车次不存在', 'error')
        return redirect(url_for('version_plan_lt.trains'))
    if t['status'] in ('已转版本', '已发布'):
        flash('车次已转版本或已发布，装载清单已固化，不能再移出', 'error')
        return redirect(url_for('version_plan_lt.train_board', code=code))
    with LOCK:
        try:
            it = db.execute(f'SELECT * FROM {_t("version_plan_lt_items")} WHERE train_code=? AND demand_code=? '
                            'AND state=?', (code, dcode, '已装')).fetchone()
            if not it:
                flash(f'{dcode} 不在本车次上', 'error')
                return redirect(url_for('version_plan_lt.train_board', code=code))
            db.execute(f'UPDATE {_t("version_plan_lt_items")} SET state="已移出", removed_at=?, reason=? WHERE id=?',
                       (_now(), (request.form.get('reason') or '人工移出').strip(), it['id']))
            db.execute(f'UPDATE {_t("version_plan_lt_demands")} SET status="规划池", version_no="", updated_at=? '
                       'WHERE code=?', (_now(), dcode))
            _record(db, dcode, code, '', '移出')
            db.commit()
        except Exception as e:
            db.rollback()
            flash('移出失败：' + str(e), 'error')
            return redirect(url_for('version_plan_lt.train_board', code=code))
    _ulog('车次', code, '移出', dcode)
    flash(f'{dcode} 已移出 {code}，回到规划池', 'success')
    return redirect(url_for('version_plan_lt.train_board', code=code))


@blueprint.route('/version_plan_lt/train-board/move', methods=['POST'])
def board_move():
    """一步改挂：同一事务内「移出旧车次 + 装入新车次」，任一失败整体回滚。"""
    if not _can_manage():
        return _deny()
    db = get_db_safe()
    frm = (request.form.get('from_train') or '').strip()
    to = (request.form.get('to_train') or '').strip()
    dcode = (request.form.get('demand') or '').strip()
    t2 = _train(db, to)
    if not t2 or t2['status'] != '计划':
        flash('目标车次不存在或已停发/已转版本', 'error')
        return redirect(url_for('version_plan_lt.train_board', code=frm))
    with LOCK:
        try:
            d = db.execute(f'SELECT * FROM {_t("version_plan_lt_demands")} WHERE code=?', (dcode,)).fetchone()
            if not d:
                flash(f'需求 {dcode} 不存在', 'error')
                return redirect(url_for('version_plan_lt.train_board', code=frm))
            it = db.execute(f'SELECT * FROM {_t("version_plan_lt_items")} WHERE train_code=? AND demand_code=? '
                            'AND state=?', (frm, dcode, '已装')).fetchone()
            if not it:
                flash(f'{dcode} 不在 {frm} 上，无法改挂', 'error')
                return redirect(url_for('version_plan_lt.train_board', code=frm))
            used, _ = _used(db, to)
            est = float(d['estimate'] or 0)
            if used + est > float(t2['capacity']):
                flash(f'目标车次装不下：{to} 已装 {used:g}/{t2["capacity"]:g}，{dcode} 需 {est:g} 人日', 'error')
                return redirect(url_for('version_plan_lt.train_board', code=frm))
            db.execute(f'UPDATE {_t("version_plan_lt_items")} SET state="已移出", removed_at=?, reason=? WHERE id=?',
                       (_now(), f'改挂到 {to}', it['id']))
            db.execute(f'INSERT INTO {_t("version_plan_lt_items")} (train_code, demand_code, state, added_at) '
                       'VALUES (?,?,?,?)', (to, dcode, '已装', _now()))
            db.execute(f'UPDATE {_t("version_plan_lt_demands")} SET version_no=?, updated_at=? WHERE code=?',
                       (to, _now(), dcode))
            _record(db, dcode, frm, to, '改挂')
            db.commit()
        except Exception as e:
            db.rollback()
            flash('改挂失败，已回滚：' + str(e), 'error')
            return redirect(url_for('version_plan_lt.train_board', code=frm))
    _ulog('车次', to, '改挂', f'{dcode} 从 {frm}')
    flash(f'{dcode} 已从 {frm} 改挂到 {to}（两端都留痕）', 'success')
    return redirect(url_for('version_plan_lt.train_board', code=to))


# ------------------------------------------------------------------ 需求评估
@blueprint.route('/version_plan_lt/assess')
def assess():
    """评估队列：按四维（价值/客户/紧急/成本）打分，自动排序并给出建议判定。"""
    db = get_db_safe()
    prod = (request.args.get('product') or '').strip()
    where = 'status="待过滤"'
    args = []
    if prod:
        where += ' AND (product_code=? OR product_code="" OR product_code IS NULL)'
        args.append(prod)
    rows = [dict(r) for r in db.execute(
        f'SELECT * FROM {_t("version_plan_lt_demands")} WHERE {where} ORDER BY id', args).fetchall()]
    hist = {}
    for r in rows:
        h = db.execute(f'SELECT * FROM {_t("version_plan_lt_assessments")} WHERE demand_code=? '
                       'ORDER BY round DESC LIMIT 1', (r['code'],)).fetchone()
        if h:
            hist[r['code']] = dict(h)
    stats = db.execute(
        f'SELECT COUNT(*) c, COALESCE(AVG(score),0) a FROM {_t("version_plan_lt_demands")} '
        'WHERE score>0').fetchone()
    return render_template('vpl_assess.html', rows=rows, hist=hist, product=prod,
                           products=_product_rows(db), prod_map=_product_map(db),
                           can_manage=_can_manage(), cnt=len(rows),
                           avg=round(float(stats['a'] or 0), 1), scored=int(stats['c'] or 0))


@blueprint.route('/version_plan_lt/assess/<code>', methods=['POST'])
def assess_submit(code):
    """提交评估：四维 1-5 分，加权得分 ≥70 进规划池、40-69 挂起、<40 不做，全过程留痕。"""
    if not _can_manage():
        return _deny()
    db = get_db_safe()
    d = db.execute(f'SELECT * FROM {_t("version_plan_lt_demands")} WHERE code=?', (code,)).fetchone()
    if not d:
        flash('需求不存在', 'error')
        return redirect(url_for('version_plan_lt.assess'))
    try:
        w = [int(request.form.get(k) or 3) for k in ('value', 'client', 'urgent', 'cost')]
    except ValueError:
        flash('评分必须是 1-5 的整数', 'error')
        return redirect(url_for('version_plan_lt.assess'))
    if any(x < 1 or x > 5 for x in w):
        flash('评分必须在 1-5 之间', 'error')
        return redirect(url_for('version_plan_lt.assess'))
    v, cl, ur, cost = w
    # 权重对齐规则 R3：业务价值 35% / 客户影响 25% / 紧急度 20% / 成本反向 20%
    score = round((v * 0.35 + cl * 0.25 + ur * 0.20 + (6 - cost) * 0.20) / 5 * 100, 1)
    verdict = VERDICT_POOL if score >= 70 else (VERDICT_HOLD if score >= 40 else VERDICT_NO)
    with LOCK:
        try:
            rnd = db.execute(f'SELECT COALESCE(MAX(round),0) m FROM {_t("version_plan_lt_assessments")} '
                             'WHERE demand_code=?', (code,)).fetchone()['m'] + 1
            db.execute(f'INSERT INTO {_t("version_plan_lt_assessments")} '
                       '(demand_code, round, w_value, w_client, w_urgent, w_cost, score, verdict, comment, '
                       'operator, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                       (code, rnd, v, cl, ur, cost, score, verdict,
                        (request.form.get('comment') or '').strip(), session.get('username', ''), _now()))
            db.execute(f'UPDATE {_t("version_plan_lt_demands")} SET score=?, filter_result=?, status=?, '
                       'filtered_by=?, filtered_at=?, updated_at=? WHERE code=?',
                       (score, verdict, '规划池' if verdict == VERDICT_POOL else
                        ('挂起' if verdict == VERDICT_HOLD else '不做'),
                        session.get('username', ''), _now(), _now(), code))
            db.commit()
        except Exception as e:
            db.rollback()
            flash('评估提交失败：' + str(e), 'error')
            return redirect(url_for('version_plan_lt.assess'))
    _ulog('需求', code, '评估', f'{score} 分 → {verdict}（第 {rnd} 轮）')
    _log('需求评估', f'{code} {score} 分 → {verdict}')
    flash(f'{code} 评估完成：{score} 分，判定「{verdict}」', 'success')
    return redirect(url_for('version_plan_lt.assess', product=request.form.get('product') or None))


# ------------------------------------------------------------ 确认清单与签核
def ensure_checks(db, no, prod):
    """惰性初始化 7 项清单（页面与接口都调用，保证直接调接口也能写）。"""
    n = db.execute(f'SELECT COUNT(*) c FROM {_t("version_plan_lt_checks")} '
                   'WHERE version_no=? AND product_code=?', (no, prod or '')).fetchone()['c']
    if n >= len(CHECK_ITEMS):
        return
    for i in range(n + 1, len(CHECK_ITEMS) + 1):
        db.execute(f'INSERT INTO {_t("version_plan_lt_checks")} (version_no, product_code, item_no, result) '
                   'VALUES (?,?,?,?)', (no, prod or '', i, 'pending'))
    db.commit()


def ensure_signoffs(db, no, prod, vtype):
    roles = SIGN_ROLES + ([CUSTOM_ROLE] if vtype == '客户定制' else [])
    for role in roles:
        if not db.execute(f'SELECT 1 FROM {_t("version_plan_lt_signoffs")} WHERE version_no=? AND product_code=? '
                          'AND role=?', (no, prod or '', role)).fetchone():
            db.execute(f'INSERT INTO {_t("version_plan_lt_signoffs")} (version_no, product_code, role, verdict) '
                       'VALUES (?,?,?,?)', (no, prod or '', role, ''))
    db.commit()
    return roles


def release_gate(db, no, prod, vtype):
    """发布门禁：7 项清单全通过 + 应签角色全部同意，缺一不可（按 产品+版本号 双键）。"""
    ensure_checks(db, no, prod)
    roles = ensure_signoffs(db, no, prod, vtype)
    checks = [dict(r) for r in db.execute(
        f'SELECT * FROM {_t("version_plan_lt_checks")} WHERE version_no=? AND product_code=? ORDER BY item_no',
        (no, prod or '')).fetchall()]
    signs = {r['role']: dict(r) for r in db.execute(
        f'SELECT * FROM {_t("version_plan_lt_signoffs")} WHERE version_no=? AND product_code=?',
        (no, prod or '')).fetchall()}
    reasons = []
    bad = [c for c in checks if (c['result'] or '') != '通过']
    if bad:
        reasons.append(f'确认清单 {len(checks) - len(bad)}/{len(checks)} 通过，缺：' +
                       '、'.join(f'#{c["item_no"]}' for c in bad))
    miss = [r for r in roles if (signs.get(r, {}).get('verdict') or '') != '同意']
    if miss:
        reasons.append('未签核：' + '、'.join(miss))
    return {'ok': not reasons, 'reasons': reasons, 'checks': checks, 'signs': signs, 'roles': roles}


@blueprint.route('/version_plan_lt/versions/<no>/check', methods=['POST'])
def version_check(no):
    """保存单项确认结果（含证据），清单不存在时自动补齐。"""
    if not _can_manage():
        return _deny()
    db = get_db_safe()
    prod = (request.form.get('product') or '').strip()
    v = _vrow(db, no, prod)
    if not v:
        flash('版本不存在', 'error')
        return redirect(url_for('version_plan_lt.versions'))
    ensure_checks(db, no, v['product_code'])
    item_no = int(request.form.get('item_no') or 0)
    result = (request.form.get('result') or 'pending').strip()
    evidence = (request.form.get('evidence') or '').strip()
    db.execute(f'UPDATE {_t("version_plan_lt_checks")} SET result=?, evidence=?, checked_by=?, checked_at=? '
               'WHERE version_no=? AND product_code=? AND item_no=?',
               (result, evidence, session.get('username', ''), _now(), no, v['product_code'] or '', item_no))
    db.commit()
    _ulog('版本', no, '确认清单', f'#{item_no} → {result}')
    flash(f'{no} 确认项 #{item_no} 已保存为「{result}」', 'success')
    return redirect(url_for('version_plan_lt.version_detail', no=no, product=v['product_code'] or None))


@blueprint.route('/version_plan_lt/versions/<no>/sign', methods=['POST'])
def version_sign(no):
    """四方签核：产品/测试/发布经理（定制版加客户代表），可同意或驳回。"""
    if not _can_manage():
        return _deny()
    db = get_db_safe()
    prod = (request.form.get('product') or '').strip()
    v = _vrow(db, no, prod)
    if not v:
        flash('版本不存在', 'error')
        return redirect(url_for('version_plan_lt.versions'))
    roles = ensure_signoffs(db, no, v['product_code'], v['vtype'])
    role = (request.form.get('role') or '').strip()
    if role not in roles:
        flash('签核角色不在本版本应签范围内', 'error')
        return redirect(url_for('version_plan_lt.version_detail', no=no))
    verdict = (request.form.get('verdict') or '').strip()
    if verdict not in ('同意', '驳回'):
        flash('请选择同意或驳回', 'error')
        return redirect(url_for('version_plan_lt.version_detail', no=no))
    db.execute(f'UPDATE {_t("version_plan_lt_signoffs")} SET verdict=?, comment=?, signed_at=? '
               'WHERE version_no=? AND product_code=? AND role=?',
               (verdict, (request.form.get('comment') or '').strip(), _now(), no, v['product_code'] or '', role))
    db.commit()
    _ulog('版本', no, '签核', f'{role}：{verdict}')
    _log('版本签核', f'{no} {role} {verdict}')
    flash(f'{no} {role} 已{verdict}', 'success' if verdict == '同意' else 'warning')
    return redirect(url_for('version_plan_lt.version_detail', no=no))


# ------------------------------------------------------------- 客户回告记录
@blueprint.route('/version_plan_lt/customers/notice', methods=['POST'])
def customer_notice():
    """给客户发版本回告：落一条可追溯记录（客户 / 版本 / 正文 / 时间）。"""
    if not _can_manage():
        return _deny()
    db = get_db_safe()
    cid = int(request.form.get('customer_id') or 0)
    c = db.execute(f'SELECT * FROM {_t("version_plan_lt_customers")} WHERE id=?', (cid,)).fetchone()
    if not c:
        flash('客户不存在', 'error')
        return redirect(url_for('version_plan_lt.customers'))
    no = (request.form.get('version_no') or '').strip()
    content = (request.form.get('content') or '').strip()
    if not content:
        flash('回告正文不能为空', 'error')
        return redirect(url_for('version_plan_lt.customers'))
    db.execute(f'INSERT INTO {_t("version_plan_lt_notices")} '
               '(customer_id, customer_name, version_no, content, sent_at, created_at) VALUES (?,?,?,?,?,?)',
               (cid, c['name'], no, content, _now(), _now()))
    # 回告联动：按客户所属产品 + 版本号回写「升级中版本」（对齐 Leantime upgrading_version 口径，
    # Python 客户表对应列为 integrating_version_no；遗留 customer_products 表为空结构）
    if no:
        vprod = (c['product_code'] or '').strip()
        if vprod:
            vexists = db.execute(
                f'SELECT 1 FROM {_t("version_plan_lt_versions")} WHERE version_no=? AND product_code=?',
                (no, vprod)).fetchone()
            if not vexists:
                flash(f'版本 {no} 不属于 {vprod}（{c["name"]} 所属产品），请核对后再回告', 'error')
                return redirect(url_for('version_plan_lt.customers'))
            db.execute(
                f'UPDATE {_t("version_plan_lt_customers")} SET integrating_version_no=? '
                'WHERE id=? AND product_code=?', (no, cid, vprod))
    db.commit()
    _ulog('客户', c['name'], '发回告', f'{no}：{content[:40]}')
    _log('客户回告', f'{c["name"]} {no}')
    flash(f'已记录对「{c["name"]}」的回告（{no or "未指定版本"}）', 'success')
    return redirect(url_for('version_plan_lt.customers'))


# ------------------------------------------------------------------ 工具函数
def get_db_safe():
    """统一取库并确保本次请求内建表完成（幂等，请求上下文内调用）。"""
    from .routes import _ensure_tables, _T
    from plugin_system import get_db
    _ensure_tables()
    return get_db()
