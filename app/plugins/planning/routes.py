# -*- coding: utf-8 -*-
"""planning 插件路由（从 app.py 迁移，v10.4.0）"""
from flask import Blueprint, render_template, request, redirect, url_for, g, flash, jsonify, send_file, session
from datetime import datetime
import io, os, json, random
from plugin_system import log_operation
from plugins.base import make_blueprint


blueprint = make_blueprint('planning', __name__)

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

@blueprint.route('/planning')
def planning_list():
    """产品规划与版本规划合并页面"""
    db = get_db()
    # 产品列表（带统计）
    products = db.execute('''
        SELECT p.*,
               (SELECT COUNT(*) FROM software_versions sv WHERE sv.product_id = p.id) as version_count,
               (SELECT COUNT(*) FROM version_requirements vr
                JOIN software_versions sv ON vr.version_id = sv.id
                WHERE sv.product_id = p.id) as req_count,
               (SELECT COUNT(*) FROM requirements r
                WHERE r.product_model = p.model AND r.req_type = '用户需求' AND r.is_deleted=0) as user_req_count
        FROM products p ORDER BY p.created_at DESC
    ''').fetchall()
    # 版本列表（带统计，按产品分组）
    versions = db.execute('''
        SELECT sv.*, p.name as product_name, p.model,
               (SELECT COUNT(*) FROM dev_tasks dt WHERE dt.version_id = sv.id) as task_count,
               (SELECT COUNT(*) FROM dev_tasks dt WHERE dt.version_id = sv.id AND dt.status = '已完成') as task_done,
               (SELECT COUNT(*) FROM bug_records br WHERE br.version_id = sv.id) as bug_count,
               (SELECT COUNT(*) FROM version_requirements vr WHERE vr.version_id = sv.id) as req_count
        FROM software_versions sv LEFT JOIN products p ON sv.product_id=p.id
        ORDER BY sv.planned_release_date ASC
    ''').fetchall()
    # 按产品分组版本
    versions_by_product = {}
    for v in versions:
        pid = v['product_id']
        if pid not in versions_by_product:
            versions_by_product[pid] = []
        versions_by_product[pid].append(v)
    return render_template('planning.html', products=products, versions=versions,
                           versions_by_product=versions_by_product)

@blueprint.route('/planning/add', methods=['GET', 'POST'])
def product_add():
    if request.method == 'POST':
        db = get_db()
        cursor = db.execute('''INSERT INTO products (name, model, version, description, lifecycle_stage, stocking_strategy)
                      VALUES (?,?,?,?,?,?)''',
                   (request.form['name'], request.form.get('model',''), request.form.get('version',''),
                    request.form.get('description',''), request.form.get('lifecycle_stage','规划中'),
                    request.form.get('stocking_strategy','不囤货，按需生产')))
        product_id = cursor.lastrowid
        # 保存BOM
        bom_names = request.form.getlist('bom_material[]')
        bom_codes = request.form.getlist('bom_code[]')
        bom_key = request.form.getlist('bom_is_key[]')
        bom_qty = request.form.getlist('bom_qty[]')
        bom_supplier = request.form.getlist('bom_supplier[]')
        for i, name in enumerate(bom_names):
            if name.strip():
                is_key = 1 if str(i) in bom_key else 0
                db.execute('''INSERT INTO bom_items (product_id, material_name, material_code, is_key_material, quantity_per_unit, supplier)
                              VALUES (?,?,?,?,?,?)''',
                           (product_id, name.strip(), bom_codes[i] if i < len(bom_codes) else '',
                            is_key, float(bom_qty[i]) if i < len(bom_qty) and bom_qty[i] else 1,
                            bom_supplier[i] if i < len(bom_supplier) else ''))
        db.commit()
        log_operation('产品规划', '新增', f"新增产品: {request.form['name']}")
        return redirect(url_for('planning.planning_list'))
    return render_template('product_form.html', product=None, bom_items=[])

@blueprint.route('/planning/<int:pid>/edit', methods=['GET', 'POST'])
def product_edit(pid):
    db = get_db()
    product = db.execute('SELECT * FROM products WHERE id=?', (pid,)).fetchone()
    bom_items = db.execute('SELECT * FROM bom_items WHERE product_id=?', (pid,)).fetchall()
    if request.method == 'POST':
        db.execute('''UPDATE products SET name=?, model=?, version=?, description=?,
                      lifecycle_stage=?, stocking_strategy=? WHERE id=?''',
                   (request.form['name'], request.form.get('model',''), request.form.get('version',''),
                    request.form.get('description',''), request.form.get('lifecycle_stage','规划中'),
                    request.form.get('stocking_strategy','不囤货，按需生产'), pid))
        # 重建BOM
        db.execute('DELETE FROM bom_items WHERE product_id=?', (pid,))
        bom_names = request.form.getlist('bom_material[]')
        bom_codes = request.form.getlist('bom_code[]')
        bom_key = request.form.getlist('bom_is_key[]')
        bom_qty = request.form.getlist('bom_qty[]')
        bom_supplier = request.form.getlist('bom_supplier[]')
        for i, name in enumerate(bom_names):
            if name.strip():
                is_key = 1 if str(i) in bom_key else 0
                db.execute('''INSERT INTO bom_items (product_id, material_name, material_code, is_key_material, quantity_per_unit, supplier)
                              VALUES (?,?,?,?,?,?)''',
                           (pid, name.strip(), bom_codes[i] if i < len(bom_codes) else '',
                            is_key, float(bom_qty[i]) if i < len(bom_qty) and bom_qty[i] else 1,
                            bom_supplier[i] if i < len(bom_supplier) else ''))
        db.commit()
        log_operation('产品规划', '编辑', f"编辑产品: {request.form['name']}")
        return redirect(url_for('planning.planning_list'))
    return render_template('product_form.html', product=product, bom_items=bom_items)

@blueprint.route('/planning/<int:pid>/delete', methods=['POST'])
def product_delete(pid):
    from app import is_admin
    if not is_admin():
        flash('仅管理员可删除产品', 'error')
        return redirect(url_for('planning.planning_list'))
    db = get_db()
    db.execute('DELETE FROM products WHERE id=?', (pid,))
    db.commit()
    log_operation('产品规划', '删除', f'删除产品ID: {pid}')
    return redirect(url_for('planning.planning_list'))

# ============================================================
# 模块3：研发生产（库存+领用+生产订单）
# ============================================================

# === 版本管理 ===
@blueprint.route('/planning/versions')
def version_list():
    """软件版本列表"""
    db = get_db()
    products = db.execute('SELECT id, name, model FROM products').fetchall()
    search = request.args.get('search', '').strip()
    status = request.args.get('status', '')
    page = max(1, int(request.args.get('page', 1)))
    per_page = 15
    offset = (page - 1) * per_page
    where = []
    params = []
    if status:
        where.append('sv.status=?')
        params.append(status)
    if search:
        where.append('(sv.version_no LIKE ? OR sv.version_name LIKE ? OR p.name LIKE ?)')
        params.extend([f'%{search}%']*3)
    where_sql = (' WHERE ' + ' AND '.join(where)) if where else ''
    total = db.execute(f'SELECT COUNT(*) FROM software_versions sv LEFT JOIN products p ON sv.product_id=p.id{where_sql}', params).fetchone()[0]
    versions = db.execute(f'''SELECT sv.*, p.name as product_name, p.model,
                             (SELECT COUNT(*) FROM dev_tasks dt WHERE dt.version_id = sv.id) as task_count,
                             (SELECT COUNT(*) FROM bug_records br WHERE br.version_id = sv.id) as bug_count,
                             (SELECT COUNT(*) FROM version_requirements vr WHERE vr.version_id = sv.id) as req_count
                             FROM software_versions sv LEFT JOIN products p ON sv.product_id=p.id
                             {where_sql} ORDER BY sv.planned_release_date ASC LIMIT ? OFFSET ?''',
                             params + [per_page, offset]).fetchall()
    total_pages = (total + per_page - 1) // per_page
    return render_template('version_list.html', versions=versions, products=products,
                           search=search, current_status=status, page=page, total_pages=total_pages, total=total)

@blueprint.route('/planning/versions/add', methods=['GET', 'POST'])
def version_add():
    """新增软件版本"""
    db = get_db()
    products = db.execute('SELECT id, name, model FROM products').fetchall()
    requirements = db.execute('''SELECT id, title, source_dept, customer, description, quantity, priority, status, delivery_date, created_at
                                 FROM requirements WHERE status IN ("已评审","进行中") AND is_deleted=0
                                 ORDER BY CASE priority WHEN '高' THEN 1 WHEN '中' THEN 2 ELSE 3 END, created_at DESC''').fetchall()
    if request.method == 'POST':
        cursor = db.execute('''INSERT INTO software_versions (product_id, version_no, version_name, description, capabilities, planned_release_date, status)
                      VALUES (?,?,?,?,?,?,?)''',
                   (request.form.get('product_id'), request.form.get('version_no',''),
                    request.form.get('version_name',''), request.form.get('description',''),
                    request.form.get('capabilities',''), request.form.get('planned_release_date',''),
                    request.form.get('status','规划中')))
        vid = cursor.lastrowid
        # 关联需求
        req_ids = request.form.getlist('requirement_ids')
        for rid in req_ids:
            db.execute('INSERT INTO version_requirements (version_id, requirement_id, priority) VALUES (?,?,?)',
                       (vid, rid, '中'))
        db.commit()
        log_operation('版本规划', '新增', f'新增版本 {request.form.get("version_no")}')
        return redirect(url_for('planning.version_detail', vid=vid))
    return render_template('version_form.html', products=products, requirements=requirements, item=None)

@blueprint.route('/planning/versions/<int:vid>')
def version_detail(vid):
    from app import validation_error
    """版本详情（含功能、开发任务、关联需求、Bug、测试用例）"""
    db = get_db()
    version = db.execute('''SELECT sv.*, p.name as product_name, p.model
                            FROM software_versions sv LEFT JOIN products p ON sv.product_id=p.id
                            WHERE sv.id=?''', (vid,)).fetchone()
    if not version:
        return validation_error('版本不存在')
    tasks = db.execute('SELECT * FROM dev_tasks WHERE version_id=? ORDER BY priority, planned_start', (vid,)).fetchall()
    linked_reqs = db.execute('''SELECT r.*, vr.priority as link_priority
                                 FROM version_requirements vr
                                 JOIN requirements r ON vr.requirement_id=r.id
                                 WHERE vr.version_id=?''', (vid,)).fetchall()
    bugs = db.execute('SELECT * FROM bug_records WHERE version_id=? ORDER BY created_at DESC', (vid,)).fetchall()
    test_cases = db.execute('SELECT * FROM test_cases WHERE version_id=? ORDER BY created_at DESC', (vid,)).fetchall()
    features = db.execute('SELECT * FROM version_features WHERE version_id=? ORDER BY priority, created_at', (vid,)).fetchall()
    return render_template('version_detail.html', version=version, tasks=tasks,
                           linked_reqs=linked_reqs, bugs=bugs, test_cases=test_cases, features=features)

@blueprint.route('/planning/versions/<int:vid>/status', methods=['POST'])
def version_status_update(vid):
    from app import submit_status_change
    """版本状态人工流转（规划中→开发中→测试中→已发布，任意→已取消）"""
    new_status = request.form.get('status', '')
    valid = ['规划中', '开发中', '测试中', '已发布', '已取消']
    if new_status not in valid:
        return jsonify({'success': False, 'error': '无效的状态值'}), 400
    db = get_db()
    # v10.3.2 权限统一：普通用户提交审批
    perm, aid = submit_status_change('planning', 'software_version', vid, new_status)
    if perm == 'pending':
        return jsonify({'success': True, 'pending': True, 'msg': '状态变更申请已提交，等待管理员审批'})
    cur = db.execute('SELECT status FROM software_versions WHERE id=?', (vid,)).fetchone()
    if not cur:
        return jsonify({'success': False, 'error': '版本不存在'}), 404
    old_status = cur['status']
    if new_status == '已发布' and old_status != '已发布':
        db.execute('''UPDATE software_versions SET status=?, actual_release_date=datetime('now','localtime')
                      WHERE id=?''', (new_status, vid))
    else:
        db.execute('UPDATE software_versions SET status=? WHERE id=?', (new_status, vid))
    db.commit()
    log_operation('版本规划', '状态流转', f'版本ID {vid} 状态 {old_status} → {new_status}')
    return jsonify({'success': True, 'status': new_status})

@blueprint.route('/planning/versions/<int:vid>/tasks/add', methods=['POST'])
def version_task_add(vid):
    """添加开发任务"""
    db = get_db()
    requirement_id = request.form.get('requirement_id')
    requirement_id = int(requirement_id) if requirement_id else None
    db.execute('''INSERT INTO dev_tasks (version_id, requirement_id, task_name, description, priority, status, assignee, planned_start, planned_finish)
                  VALUES (?,?,?,?,?,?,?,?,?)''',
               (vid, requirement_id, request.form.get('task_name',''), request.form.get('description',''),
                request.form.get('priority','中'), '待开发', request.form.get('assignee',''),
                request.form.get('planned_start',''), request.form.get('planned_finish','')))
    db.commit()
    log_operation('版本规划', '新增任务', f'版本ID {vid} 新增任务: {request.form.get("task_name")}')
    return redirect(url_for('planning.version_detail', vid=vid))

@blueprint.route('/planning/versions/<int:vid>/tasks/<int:tid>/status', methods=['POST'])
def version_task_status(vid, tid):
    from app import submit_status_change
    """更新开发任务状态"""
    new_status = request.form.get('status', '')
    valid = ['待开发', '开发中', '已完成', '已取消']
    if new_status not in valid:
        return jsonify({'success': False}), 400
    db = get_db()
    # v10.3.2 权限统一：普通用户提交审批
    perm, aid = submit_status_change('planning', 'dev_task', tid, new_status)
    if perm == 'pending':
        return jsonify({'success': True, 'pending': True, 'msg': '状态变更申请已提交，等待管理员审批'})
    db.execute('UPDATE dev_tasks SET status=?, actual_finish=datetime("now","localtime") WHERE id=?',
               (new_status, tid))
    db.commit()
    return jsonify({'success': True, 'status': new_status})

# ============================================================
# 反向规划模块2：Bug登记与流转（含根因分析闭环）
# ============================================================
