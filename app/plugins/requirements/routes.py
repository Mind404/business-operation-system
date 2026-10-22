# -*- coding: utf-8 -*-
"""需求收集插件路由（v11.0 全新设计，强管控）"""
from flask import Blueprint, render_template, request, redirect, url_for, g, jsonify, session, flash
from datetime import datetime
import json

from plugins.base import make_blueprint
from plugin_system import log_operation, submit_approval

blueprint = make_blueprint('requirements', __name__)

# 需求状态机（强管控：单向推进 + 评审驳回回退）
REQ_STATUS_FLOW = ['待评审', '评审通过', '已采纳', '开发中', '已完成', '已关闭']
REQ_VALID_TRANSITIONS = {
    '待评审': ['评审通过'],          # 评审通过（走审批流）
    '评审通过': ['已采纳', '待评审'],  # 采纳 或 驳回回待评审
    '已采纳': ['开发中'],
    '开发中': ['已完成'],
    '已完成': ['已关闭'],
    '已关闭': [],
}
# 类型选项（可扩展）
REQ_TYPES = ['用户需求', '内部需求', '领导任务', '集团系统侧', '应用侧']


def get_db():
    if 'db' not in g:
        import sqlite3
        from flask import current_app
        g.db = sqlite3.connect(current_app.config['DATABASE'])
        g.db.row_factory = sqlite3.Row
        g.db.execute('PRAGMA foreign_keys = ON')
    return g.db


@blueprint.teardown_request
def close_db(exception):
    db = g.pop('db', None)
    if db is not None:
        db.close()


def _is_admin():
    # 与平台角色体系一致（v11.0.3 三级角色）：管理组 = 平台管理员 + 管理员
    return session.get('user_role') in ('admin', 'platform_admin')


def _now():
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def _gen_code(db):
    """自动生成需求编号：RQ-YYYY-XXX（按当年序号）"""
    year = datetime.now().strftime('%Y')
    row = db.execute("SELECT COUNT(*) AS c FROM req_items WHERE code LIKE ?", (f'RQ-{year}-%',)).fetchone()
    return f'RQ-{year}-{row["c"] + 1:03d}'


def _field_config_map(db):
    """返回字段配置 {key: {label, required, visible}}，供表单按配置渲染/校验"""
    rows = db.execute('SELECT * FROM req_field_config').fetchall()
    return {r['field_key']: {'label': r['field_label'], 'required': r['required'], 'visible': r['visible']} for r in rows}


def _check_required(form, cfg_map):
    """按字段配置校验必填"""
    for key, cfg in cfg_map.items():
        if cfg['required'] and not form.get(key, '').strip():
            return f"「{cfg['label']}」为必填项"
    return None


def _log_change(db, req_id, field_name, old_value, new_value, change_by, approval_id=None):
    db.execute('''INSERT INTO req_changes (req_id, field_name, old_value, new_value, change_by, approval_id, change_at)
                  VALUES (?,?,?,?,?,?,?)''',
               (req_id, field_name, old_value, new_value, change_by, approval_id, _now()))


def _record_review(db, req_id, reviewer, conclusion, opinion):
    db.execute('''INSERT INTO req_reviews (req_id, reviewer, review_date, conclusion, opinion)
                  VALUES (?,?,?,?,?)''',
               (req_id, reviewer, _now(), conclusion, opinion))


def _apply_req_status(db, req_id, new_status, reviewer, opinion=''):
    """审批通过后的状态落地：更新状态 + 变更留痕 + 评审记录"""
    item = db.execute('SELECT * FROM req_items WHERE id=?', (req_id,)).fetchone()
    if not item:
        return
    old = item['status']
    db.execute("UPDATE req_items SET status=?, updated_at=? WHERE id=?",
               (new_status, _now(), req_id))
    _log_change(db, req_id, 'status', old, new_status, reviewer)
    _record_review(db, req_id, reviewer, '已通过', opinion or f'状态流转：{old} → {new_status}')
    db.commit()
    log_operation('需求收集', '状态流转', f'需求 {item["code"]} {old} → {new_status}（审批通过）', reviewer)


# ============================================================
# 列表
# ============================================================
@blueprint.route('/requirements')
def req_list():
    db = get_db()
    status = request.args.get('status', '')
    req_type = request.args.get('req_type', '')
    search = request.args.get('search', '').strip()
    page = max(1, int(request.args.get('page', 1)))
    per_page = 15

    where = ['is_deleted=0']
    params = []
    if status:
        where.append('status=?')
        params.append(status)
    if req_type:
        where.append('req_type=?')
        params.append(req_type)
    if search:
        where.append('(title LIKE ? OR code LIKE ? OR customer LIKE ? OR source_dept LIKE ?)')
        params.extend([f'%{search}%'] * 4)

    where_sql = ' WHERE ' + ' AND '.join(where)
    total = db.execute(f'SELECT COUNT(*) FROM req_items{where_sql}', params).fetchone()[0]
    items = db.execute(f'''SELECT * FROM req_items{where_sql}
        ORDER BY created_at DESC LIMIT ? OFFSET ?''', params + [per_page, (page - 1) * per_page]).fetchall()
    total_pages = (total + per_page - 1) // per_page
    # 状态分布
    status_stats = db.execute('''SELECT status, COUNT(*) c FROM req_items
        WHERE is_deleted=0 GROUP BY status''').fetchall()
    stat_map = {r['status']: r['c'] for r in status_stats}
    return render_template('req_list.html', items=items, total=total,
                           current_status=status, current_type=req_type, search=search,
                           page=page, total_pages=total_pages,
                           status_stats=stat_map, req_types=REQ_TYPES,
                           flow=REQ_STATUS_FLOW)


# ============================================================
# 新增 / 编辑
# ============================================================
@blueprint.route('/requirements/add', methods=['GET', 'POST'])
def req_add():
    db = get_db()
    cfg_map = _field_config_map(db)
    if request.method == 'POST':
        err = _check_required(request.form, cfg_map)
        if err:
            flash(err, 'error')
            return render_template('req_form.html', item=None, cfg_map=cfg_map, req_types=REQ_TYPES)
        code = _gen_code(db)
        now = _now()
        db.execute('''INSERT INTO req_items
            (code, title, req_type, source_dept, customer, description, quantity,
             priority, delivery_date, owner, status, created_by, created_at, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
            (code, request.form['title'].strip(), request.form.get('req_type', '用户需求'),
             request.form.get('source_dept', ''), request.form.get('customer', ''),
             request.form.get('description', ''), int(request.form.get('quantity', 0) or 0),
             request.form.get('priority', '中'), request.form.get('delivery_date', ''),
             request.form.get('owner', ''), '待评审',
             session.get('username', ''), now, now))
        db.commit()
        log_operation('需求收集', '新增', f'{code}: {request.form["title"]}', session.get('username', ''))
        flash('需求已创建（待评审）', 'success')
        return redirect(url_for('requirements.req_list'))
    return render_template('req_form.html', item=None, cfg_map=cfg_map, req_types=REQ_TYPES)


@blueprint.route('/requirements/<int:rid>/edit', methods=['GET', 'POST'])
def req_edit(rid):
    db = get_db()
    item = db.execute('SELECT * FROM req_items WHERE id=? AND is_deleted=0', (rid,)).fetchone()
    if not item:
        flash('需求不存在', 'error')
        return redirect(url_for('requirements.req_list'))
    cfg_map = _field_config_map(db)
    if request.method == 'POST':
        err = _check_required(request.form, cfg_map)
        if err:
            flash(err, 'error')
            return render_template('req_form.html', item=item, cfg_map=cfg_map, req_types=REQ_TYPES)
        # 变更留痕（关键字段）
        change_fields = {
            'title': '需求标题', 'req_type': '需求类型', 'source_dept': '来源部门',
            'customer': '客户/提出方', 'description': '需求描述', 'quantity': '需求数量',
            'priority': '优先级', 'delivery_date': '期望交付日期', 'owner': '负责人',
        }
        for fk, label in change_fields.items():
            old = str(item[fk] if item[fk] is not None else '')
            new = str(request.form.get(fk, ''))
            if old != new:
                _log_change(db, rid, fk, old, new, session.get('username', ''))
        db.execute('''UPDATE req_items SET title=?, req_type=?, source_dept=?, customer=?,
            description=?, quantity=?, priority=?, delivery_date=?, owner=?, updated_at=?
            WHERE id=?''',
            (request.form['title'].strip(), request.form.get('req_type', '用户需求'),
             request.form.get('source_dept', ''), request.form.get('customer', ''),
             request.form.get('description', ''), int(request.form.get('quantity', 0) or 0),
             request.form.get('priority', '中'), request.form.get('delivery_date', ''),
             request.form.get('owner', ''), _now(), rid))
        db.commit()
        log_operation('需求收集', '编辑', f'#{rid} {request.form["title"]}', session.get('username', ''))
        flash('需求已更新', 'success')
        return redirect(url_for('requirements.req_detail', rid=rid))
    return render_template('req_form.html', item=item, cfg_map=cfg_map, req_types=REQ_TYPES)


# ============================================================
# 详情（含评审/变更/关联WBS）
# ============================================================
@blueprint.route('/requirements/<int:rid>')
def req_detail(rid):
    db = get_db()
    item = db.execute('SELECT * FROM req_items WHERE id=? AND is_deleted=0', (rid,)).fetchone()
    if not item:
        flash('需求不存在', 'error')
        return redirect(url_for('requirements.req_list'))
    reviews = db.execute('SELECT * FROM req_reviews WHERE req_id=? ORDER BY created_at DESC', (rid,)).fetchall()
    changes = db.execute('SELECT * FROM req_changes WHERE req_id=? ORDER BY change_at DESC', (rid,)).fetchall()
    # 关联 WBS 项目/任务
    wbs_project = None
    wbs_task = None
    if item['wbs_project_id']:
        wbs_project = db.execute('SELECT * FROM wbs_projects WHERE id=?', (item['wbs_project_id'],)).fetchone()
    if item['wbs_task_id']:
        wbs_task = db.execute('SELECT * FROM wbs_tasks WHERE id=?', (item['wbs_task_id'],)).fetchone()
    wbs_projects = db.execute('SELECT id, name, status FROM wbs_projects ORDER BY created_at DESC').fetchall()
    # 可流转目标状态
    next_statuses = REQ_VALID_TRANSITIONS.get(item['status'], [])
    return render_template('req_detail.html', item=item, reviews=reviews, changes=changes,
                           wbs_project=wbs_project, wbs_task=wbs_task, wbs_projects=wbs_projects,
                           next_statuses=next_statuses, is_admin=_is_admin())


# ============================================================
# 状态变更（走平台统一审批流）
# ============================================================
@blueprint.route('/requirements/<int:rid>/status', methods=['POST'])
def req_status_change(rid):
    db = get_db()
    item = db.execute('SELECT * FROM req_items WHERE id=? AND is_deleted=0', (rid,)).fetchone()
    if not item:
        return jsonify({'success': False, 'error': '需求不存在'}), 404
    new_status = request.form.get('status', '') or (request.get_json(silent=True) or {}).get('status', '')
    valid_targets = REQ_VALID_TRANSITIONS.get(item['status'], [])
    if new_status not in valid_targets:
        return jsonify({'success': False, 'error': f'不允许从「{item["status"]}」流转到「{new_status}」'}), 400

    # 平台统一审批流：普通用户提交审批单；管理员直接生效（留痕）
    if _is_admin():
        _apply_req_status(db, rid, new_status, session.get('username', 'admin'),
                          opinion='管理员直接流转')
        return jsonify({'success': True, 'status': new_status})
    # 查询审批流配置（默认单级 admin）
    flow = db.execute('''SELECT * FROM approval_flows
        WHERE plugin_id='requirements' AND record_type='req_status' AND enabled=1
        ORDER BY id DESC LIMIT 1''').fetchone()
    if not flow:
        steps = [{'step': 1, 'role': 'admin'}]
        flow_id = None
    else:
        steps = json.loads(flow['steps'] or '[{"step":1,"role":"admin"}]')
        flow_id = flow['id']
    payload = {'new_status': new_status, 'req_code': item['code'], 'title': item['title']}
    aid = submit_approval('requirements', 'req_status', rid, session.get('user_id', 0),
                          f'需求状态变更：{item["status"]} → {new_status}', payload,
                          flow_id=flow_id, current_step=1)
    log_operation('需求收集', '提交审批', f'需求 {item["code"]} 状态变更待审批（单#{aid}）', session.get('username', ''))
    return jsonify({'success': True, 'pending': True,
                    'msg': f'状态变更申请已提交（审批单#{aid}），等待审批'}), 202


# ============================================================
# 关联 WBS 项目/任务
# ============================================================
@blueprint.route('/requirements/<int:rid>/link', methods=['POST'])
def req_link(rid):
    db = get_db()
    item = db.execute('SELECT * FROM req_items WHERE id=? AND is_deleted=0', (rid,)).fetchone()
    if not item:
        return jsonify({'success': False, 'error': '需求不存在'}), 404
    pid = request.form.get('project_id', '') or ''
    tid = request.form.get('task_id', '') or ''
    pid = int(pid) if pid else None
    tid = int(tid) if tid else None
    db.execute('UPDATE req_items SET wbs_project_id=?, wbs_task_id=?, updated_at=? WHERE id=?',
               (pid, tid, _now(), rid))
    db.commit()
    log_operation('需求收集', '关联WBS', f'需求 {item["code"]} 关联项目#{pid} 任务#{tid}', session.get('username', ''))
    return jsonify({'success': True})


# ============================================================
# 删除（软删，仅管理员）
# ============================================================
@blueprint.route('/requirements/<int:rid>/delete', methods=['POST'])
def req_delete(rid):
    if not _is_admin():
        flash('仅管理员可删除需求', 'error')
        return redirect(url_for('requirements.req_list'))
    db = get_db()
    item = db.execute('SELECT * FROM req_items WHERE id=? AND is_deleted=0', (rid,)).fetchone()
    if not item:
        flash('需求不存在', 'error')
        return redirect(url_for('requirements.req_list'))
    db.execute('UPDATE req_items SET is_deleted=1, updated_at=? WHERE id=?', (_now(), rid))
    db.commit()
    log_operation('需求收集', '删除', f'软删除需求 {item["code"]}', session.get('username', ''))
    flash('需求已删除（软删可追溯）', 'success')
    return redirect(url_for('requirements.req_list'))


# ============================================================
# 字段配置（管理员：必填/显示）——平台"双层配置灵活"第一层
# ============================================================
@blueprint.route('/requirements/fields')
def req_fields():
    if not _is_admin():
        flash('仅管理员可配置字段', 'error')
        return redirect(url_for('requirements.req_list'))
    db = get_db()
    fields = db.execute('SELECT * FROM req_field_config ORDER BY id').fetchall()
    return render_template('req_fields.html', fields=fields)


@blueprint.route('/requirements/fields/update', methods=['POST'])
def req_fields_update():
    if not _is_admin():
        return jsonify({'success': False, 'error': '仅管理员可配置'}), 403
    db = get_db()
    for row in db.execute('SELECT id, field_key FROM req_field_config').fetchall():
        fid = row['id']
        required = 1 if request.form.get(f'required_{fid}') else 0
        visible = 1 if request.form.get(f'visible_{fid}') else 0
        db.execute('UPDATE req_field_config SET required=?, visible=? WHERE id=?',
                   (required, visible, fid))
    db.commit()
    log_operation('需求收集', '字段配置', '更新必填/显示配置', session.get('username', ''))
    flash('字段配置已保存', 'success')
    return redirect(url_for('requirements.req_fields'))
