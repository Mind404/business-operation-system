# -*- coding: utf-8 -*-
"""需求管理插件路由：外部技术需求统计（从 ext_routes.py 迁移）+ 需求库（从 app.py 迁移）"""
from flask import Blueprint, render_template, request, redirect, url_for, g, flash, jsonify, current_app, session
from datetime import datetime
import io, json, random
from plugin_system import log_operation

from plugins.base import make_blueprint
blueprint = make_blueprint('requirements', __name__)

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

# ============================================================
# 外部技术需求统计（个人工作台核心）
# ============================================================
@blueprint.route('/tech-reqs')
def tech_req_list():
    db = get_db()
    stage = request.args.get('stage', '')
    status = request.args.get('status', '')
    q = request.args.get('q', '')
    sql = 'SELECT t.*, (SELECT COUNT(*) FROM tech_req_logs l WHERE l.tech_req_id=t.id) AS log_count FROM tech_requirements t WHERE 1=1'
    params = []
    if stage:
        sql += ' AND t.stage=?'
        params.append(stage)
    if status:
        sql += ' AND t.status=?'
        params.append(status)
    if q:
        sql += ' AND (t.item LIKE ? OR t.description LIKE ? OR t.category LIKE ?)'
        params.extend([f'%{q}%']*3)
    sql += ' ORDER BY t.sort_order, t.id'
    rows = db.execute(sql, params).fetchall()
    stages = [r['stage'] for r in db.execute('SELECT DISTINCT stage FROM tech_requirements WHERE stage IS NOT NULL AND stage!=""').fetchall()]
    return render_template('tech_req_list.html', rows=rows, stages=stages,
                           stage=stage, status=status, q=q)


@blueprint.route('/tech-reqs/add', methods=['GET', 'POST'])
def tech_req_add():
    from app import is_admin
    db = get_db()
    if request.method == 'POST':
        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        db.execute('''INSERT INTO tech_requirements
            (stage, category, item, planned_date, description, version_response, product_id, status, created_at, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?)''',
            (request.form.get('stage',''), request.form.get('category',''), request.form.get('item',''),
             request.form.get('planned_date',''), request.form.get('description',''),
             request.form.get('version_response',''), request.form.get('product_id',1),
             request.form.get('status','进行中'), now, now))
        db.commit()
        log_operation('外部技术需求', '新增', request.form.get('item',''), session.get('username','admin'))
        return redirect(url_for('requirements.tech_req_list'))
    return render_template('tech_req_form.html', r=None)


@blueprint.route('/tech-reqs/<int:rid>/edit', methods=['GET', 'POST'])
def tech_req_edit(rid):
    from app import is_admin
    db = get_db()
    r = db.execute('SELECT * FROM tech_requirements WHERE id=?', (rid,)).fetchone()
    if not r:
        return redirect(url_for('requirements.tech_req_list'))
    if request.method == 'POST':
        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        db.execute('''UPDATE tech_requirements SET stage=?, category=?, item=?, planned_date=?,
            description=?, version_response=?, status=?, updated_at=? WHERE id=?''',
            (request.form.get('stage',''), request.form.get('category',''), request.form.get('item',''),
             request.form.get('planned_date',''), request.form.get('description',''),
             request.form.get('version_response',''), request.form.get('status','进行中'), now, rid))
        db.commit()
        log_operation('外部技术需求', '编辑', f'#{rid} {request.form.get("item","")}', session.get('username','admin'))
        return redirect(url_for('requirements.tech_req_detail', rid=rid))
    return render_template('tech_req_form.html', r=r)


@blueprint.route('/tech-reqs/<int:rid>')
def tech_req_detail(rid):
    db = get_db()
    r = db.execute('SELECT * FROM tech_requirements WHERE id=?', (rid,)).fetchone()
    if not r:
        return redirect(url_for('requirements.tech_req_list'))
    logs = db.execute('SELECT * FROM tech_req_logs WHERE tech_req_id=? ORDER BY log_date DESC, id DESC', (rid,)).fetchall()
    return render_template('tech_req_detail.html', r=r, logs=logs)


@blueprint.route('/tech-reqs/<int:rid>/log', methods=['POST'])
def tech_req_add_log(rid):
    from app import is_admin
    db = get_db()
    content = request.form.get('content','').strip()
    log_date = request.form.get('log_date','') or datetime.now().strftime('%Y-%m-%d')
    if content:
        db.execute('''INSERT INTO tech_req_logs (tech_req_id, log_date, content, author, created_at)
            VALUES (?,?,?,?,?)''',
            (rid, log_date, content, session.get('user_name','admin'),
             datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
        db.execute('UPDATE tech_requirements SET updated_at=? WHERE id=?',
                   (datetime.now().strftime('%Y-%m-%d %H:%M:%S'), rid))
        db.commit()
        log_operation('外部技术需求', '进展', f'#{rid} 追加进展', session.get('username','admin'))
    return redirect(url_for('requirements.tech_req_detail', rid=rid))


@blueprint.route('/tech-reqs/<int:rid>/delete', methods=['POST'])
def tech_req_delete(rid):
    from app import is_admin
    if not is_admin():
        flash('仅管理员可删除技术需求', 'error')
        return redirect(url_for('requirements.tech_req_list'))
    db = get_db()
    db.execute('DELETE FROM tech_req_logs WHERE tech_req_id=?', (rid,))
    db.execute('DELETE FROM tech_requirements WHERE id=?', (rid,))
    db.commit()
    log_operation('外部技术需求', '删除', f'#{rid}', session.get('username','admin'))
    return redirect(url_for('requirements.tech_req_list'))


@blueprint.route('/tech-reqs/import', methods=['POST'])
def tech_req_import():
    """按图1格式导入：序号|类型|事项|计划完成时间|描述|优化事项及进展|版本响应要求"""
    f = request.files.get('file')
    if not f:
        flash('请选择文件', 'error')
        return redirect(url_for('requirements.tech_req_list'))
    import openpyxl
    wb = openpyxl.load_workbook(f, data_only=True)
    ws = wb.active
    db = get_db()
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    count = 0
    current_category = ''
    for row in ws.iter_rows(min_row=3, values_only=True):  # 第1行标题，第2行表头
        if not row or not row[2]:  # 事项列为空跳过
            continue
        # 类型分组列（B列）合并单元格，空则继承上一个
        cat = str(row[1] or '').strip() if row[1] else ''
        if cat:
            current_category = cat
        item = str(row[2] or '').strip()
        if not item:
            continue
        planned = str(row[3] or '').strip() if row[3] else ''
        desc = str(row[4] or '').strip() if row[4] else ''
        progress = str(row[5] or '').strip() if row[5] else ''
        version_resp = str(row[6] or '').strip() if row[6] else ''
        stage = str(row[0] or '').strip() if row[0] else ''
        # stage 可能在标题行，用第一行A1
        if not stage:
            stage = str(ws.cell(1,1).value or '').strip()
        db.execute('''INSERT INTO tech_requirements
            (stage, category, item, planned_date, description, version_response, product_id, status, created_at, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?)''',
            (stage, current_category, item, planned, desc, version_resp, 1, '进行中', now, now))
        rid = db.execute('SELECT last_insert_rowid()').fetchone()[0]
        # 进展列拆成多条日志（按换行或日期分段）
        if progress:
            for line in progress.split('\n'):
                line = line.strip()
                if line:
                    db.execute('''INSERT INTO tech_req_logs (tech_req_id, log_date, content, author, created_at)
                        VALUES (?,?,?,?,?)''', (rid, now[:10], line, '导入', now))
        count += 1
    db.commit()
    flash(f'成功导入 {count} 条技术需求', 'success')
    return redirect(url_for('requirements.tech_req_list'))


# ============================================================
# 插件B：WBS计划管理（项目工作分解结构）
# 字段：WBS编号|阶段/任务|工作内容|负责人|协作支撑|计划开始|计划结束|交付物

# ============================================================
# 需求库（从 app.py 迁移）
# ============================================================
# ============================================================
# 需求库（从 app.py 迁移）
# ============================================================
@blueprint.route('/requirements')
def requirements_list():
    from app import is_admin, submit_status_change
    db = get_db()
    status = request.args.get('status', '')
    req_type = request.args.get('req_type', '')
    search = request.args.get('search', '').strip()
    page = max(1, int(request.args.get('page', 1)))
    per_page = 15
    offset = (page - 1) * per_page

    where = ['is_deleted=0']
    params = []
    if status:
        where.append('status=?')
        params.append(status)
    if req_type:
        where.append('req_type=?')
        params.append(req_type)
    if search:
        where.append('(title LIKE ? OR customer LIKE ? OR source_dept LIKE ? OR description LIKE ? OR product_model LIKE ?)')
        params.extend([f'%{search}%']*5)

    where_sql = (' WHERE ' + ' AND '.join(where)) if where else ''
    total = db.execute(f'SELECT COUNT(*) FROM requirements{where_sql}', params).fetchone()[0]
    items = db.execute(f'SELECT * FROM requirements{where_sql} ORDER BY created_at DESC LIMIT ? OFFSET ?',
                       params + [per_page, offset]).fetchall()
    total_pages = (total + per_page - 1) // per_page
    # 统计各类型数量
    type_stats = db.execute('''SELECT req_type, COUNT(*) as cnt FROM requirements WHERE is_deleted=0 GROUP BY req_type''').fetchall()
    type_count = {t['req_type']: t['cnt'] for t in type_stats}
    return render_template('requirements.html', items=items, current_status=status,
                           current_type=req_type, type_count=type_count,
                           search=search, page=page, total_pages=total_pages, total=total)

@blueprint.route('/requirements/add', methods=['GET', 'POST'])
def requirement_add():
    from app import validate_positive_int, validation_error
    if request.method == 'POST':
        # 输入校验
        qty, err = validate_positive_int(request.form.get('quantity', 0), '需求数量')
        if err:
            return validation_error(err)
        if not request.form.get('title', '').strip():
            return validation_error('需求标题不能为空')

        db = get_db()
        db.execute('''INSERT INTO requirements (title, req_type, source_dept, customer, description, quantity, priority, delivery_date,
                      product_model, batch_delivery, application_scenario,
                      system_verify_point, verify_function, performance_req, support_product,
                      certification_type, spec_requirement, industry_standard, baseline_product_type,
                      task_progress, time_plan, deliverable)
                      VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                   (request.form['title'].strip(), request.form.get('req_type','用户需求'),
                    request.form.get('source_dept',''), request.form.get('customer',''),
                    request.form.get('description',''), qty,
                    request.form.get('priority','中'), request.form.get('delivery_date',''),
                    request.form.get('product_model',''), request.form.get('batch_delivery','否'),
                    request.form.get('application_scenario',''),
                    request.form.get('system_verify_point',''), request.form.get('verify_function',''),
                    request.form.get('performance_req',''), request.form.get('support_product',''),
                    request.form.get('certification_type',''), request.form.get('spec_requirement',''),
                    request.form.get('industry_standard',''), request.form.get('baseline_product_type',''),
                    request.form.get('task_progress',''), request.form.get('time_plan',''),
                    request.form.get('deliverable','')))
        db.commit()
        log_operation('需求管理', '新增', f"新增需求[{request.form.get('req_type','用户需求')}]: {request.form['title']}")
        return redirect(url_for('requirements.requirements_list'))
    return render_template('requirement_form.html', item=None)

@blueprint.route('/requirements/<int:req_id>/edit', methods=['GET', 'POST'])
def requirement_edit(req_id):
    from app import validate_positive_int, validation_error
    db = get_db()
    item = db.execute('SELECT * FROM requirements WHERE id=? AND is_deleted=0', (req_id,)).fetchone()
    if request.method == 'POST':
        # 输入校验
        qty, err = validate_positive_int(request.form.get('quantity', 0), '需求数量')
        if err:
            return validation_error(err)
        if not request.form.get('title', '').strip():
            return validation_error('需求标题不能为空')

        db.execute('''UPDATE requirements SET title=?, req_type=?, source_dept=?, customer=?, description=?,
                      quantity=?, priority=?, status=?, delivery_date=?,
                      product_model=?, batch_delivery=?, application_scenario=?,
                      system_verify_point=?, verify_function=?, performance_req=?, support_product=?,
                      certification_type=?, spec_requirement=?, industry_standard=?, baseline_product_type=?,
                      task_progress=?, time_plan=?, deliverable=?, updated_at=datetime('now','localtime')
                      WHERE id=?''',
                   (request.form['title'].strip(), request.form.get('req_type','用户需求'),
                    request.form.get('source_dept',''), request.form.get('customer',''),
                    request.form.get('description',''), qty,
                    request.form.get('priority','中'), request.form.get('status','待评审'),
                    request.form.get('delivery_date',''),
                    request.form.get('product_model',''), request.form.get('batch_delivery','否'),
                    request.form.get('application_scenario',''),
                    request.form.get('system_verify_point',''), request.form.get('verify_function',''),
                    request.form.get('performance_req',''), request.form.get('support_product',''),
                    request.form.get('certification_type',''), request.form.get('spec_requirement',''),
                    request.form.get('industry_standard',''), request.form.get('baseline_product_type',''),
                    request.form.get('task_progress',''), request.form.get('time_plan',''),
                    request.form.get('deliverable',''), req_id))
        db.commit()
        log_operation('需求管理', '编辑', f"编辑需求[{request.form.get('req_type','用户需求')}]: {request.form['title']}")
        return redirect(url_for('requirements.requirements_list'))
    return render_template('requirement_form.html', item=item)

@blueprint.route('/requirements/<int:req_id>/delete', methods=['POST'])
def requirement_delete(req_id):
    from app import is_admin, validation_error
    """软删除需求（v8.6.0）：标记 is_deleted=1，保留历史追溯，防止外键崩溃"""
    if not is_admin():
        flash('仅管理员可删除需求', 'error')
        return redirect(url_for('requirements.requirements_list'))
    db = get_db()
    item = db.execute('SELECT id FROM requirements WHERE id=? AND is_deleted=0', (req_id,)).fetchone()
    if not item:
        return validation_error('需求不存在或已删除')
    db.execute('UPDATE requirements SET is_deleted=1, updated_at=datetime("now","localtime") WHERE id=?', (req_id,))
    db.commit()
    log_operation('需求管理', '删除', f'软删除需求ID: {req_id}')
    return redirect(url_for('requirements.requirements_list'))

@blueprint.route('/requirements/<int:req_id>/status', methods=['POST'])
def requirement_update_status(req_id):
    from app import submit_status_change
    """需求状态快速更新（AJAX）"""
    new_status = request.form.get('status', '')
    valid_statuses = ['待评审', '已评审', '进行中', '已完成', '已取消', '已交付']
    if new_status not in valid_statuses:
        return jsonify({'success': False, 'error': '无效的状态值'}), 400
    db = get_db()
    # v10.3.2 权限统一：普通用户提交审批，管理员直接改
    perm, aid = submit_status_change('requirements', 'requirement', req_id, new_status)
    if perm == 'pending':
        return jsonify({'success': True, 'pending': True, 'msg': '状态变更申请已提交，等待管理员审批'})
    db.execute('''UPDATE requirements SET status=?, updated_at=datetime('now','localtime') WHERE id=?''',
               (new_status, req_id))
    db.commit()
    log_operation('需求管理', '状态更新', f'需求ID {req_id} 状态更新为 {new_status}')
    # 状态联动：当需求状态变更时，自动同步关联版本的状态
    sync_requirement_status(db, req_id, new_status)
    return jsonify({'success': True, 'status': new_status})

def sync_requirement_status(db, req_id, new_status):
    """状态联动：需求状态变更时同步关联版本状态"""
    try:
        # 查询关联的版本
        versions = db.execute('''
            SELECT sv.id, sv.status FROM software_versions sv
            JOIN version_requirements vr ON sv.id = vr.version_id
            WHERE vr.requirement_id = ?
        ''', (req_id,)).fetchall()
        for v in versions:
            if new_status == '已交付' and v['status'] != '已发布':
                db.execute('UPDATE software_versions SET status=? WHERE id=?', ('已发布', v['id']))
                log_operation('版本规划', '状态联动', f'版本ID {v["id"]} 因需求交付自动更新为已发布')
            elif new_status == '进行中' and v['status'] == '规划中':
                db.execute('UPDATE software_versions SET status=? WHERE id=?', ('开发中', v['id']))
                log_operation('版本规划', '状态联动', f'版本ID {v["id"]} 因需求进行中自动更新为开发中')
        db.commit()
    except Exception as e:
        db.rollback()
        current_app.logger.error(f'状态联动失败: {e}')

@blueprint.route('/requirements/<int:req_id>')
def requirement_detail(req_id):
    from app import validation_error
    """需求详情（端到端追溯：需求→版本→任务→Bug→测试→排程→订单→交付→收入）"""
    db = get_db()
    requirement = db.execute('SELECT * FROM requirements WHERE id=? AND is_deleted=0', (req_id,)).fetchone()
    if not requirement:
        return validation_error('需求不存在')
    # 1. 关联的软件版本（关联产品表获取产品名称，字段名映射为模板期望的名称）
    versions = db.execute('''
        SELECT sv.*, p.name as product_name, 
               sv.planned_release_date as planned_release,
               sv.actual_release_date as actual_release,
               sv.capabilities as core_features,
               '固件版本' as version_type,
               vr.priority as link_priority
        FROM version_requirements vr
        JOIN software_versions sv ON vr.version_id=sv.id
        LEFT JOIN products p ON sv.product_id=p.id
        WHERE vr.requirement_id=?
        ORDER BY sv.created_at DESC
    ''', (req_id,)).fetchall()
    version_ids = [v['id'] for v in versions]
    # 2. 关联的开发任务（直接关联requirement_id + 通过版本间接关联）
    tasks = db.execute('SELECT * FROM dev_tasks WHERE requirement_id=? ORDER BY priority, planned_start', (req_id,)).fetchall()
    if version_ids:
        placeholders = ','.join('?' * len(version_ids))
        version_tasks = db.execute(f'SELECT * FROM dev_tasks WHERE version_id IN ({placeholders}) AND requirement_id IS NULL ORDER BY priority, planned_start', version_ids).fetchall()
        tasks = list(tasks) + list(version_tasks)
    # 3. 关联的Bug（通过版本间接关联）
    bugs = []
    if version_ids:
        placeholders = ','.join('?' * len(version_ids))
        bugs = db.execute(f'SELECT * FROM bug_records WHERE version_id IN ({placeholders}) ORDER BY created_at DESC', version_ids).fetchall()
    # 4. 关联的测试用例（通过版本间接关联）
    test_cases = []
    if version_ids:
        placeholders = ','.join('?' * len(version_ids))
        test_cases = db.execute(f'SELECT * FROM test_cases WHERE version_id IN ({placeholders}) ORDER BY created_at DESC', version_ids).fetchall()
    # 5. 关联的生产排程
    schedules = db.execute('SELECT * FROM production_schedules WHERE requirement_id=? ORDER BY created_at DESC', (req_id,)).fetchall()
    # 6. 关联的生产订单
    orders = db.execute('SELECT * FROM production_orders WHERE requirement_id=? ORDER BY created_at DESC', (req_id,)).fetchall()
    # 7. 关联的交付记录（通过需求ID或产品间接关联）
    deliveries = []
    try:
        deliveries = db.execute('SELECT * FROM deliveries WHERE requirement_id=? ORDER BY created_at DESC', (req_id,)).fetchall()
    except Exception:
        pass
    # 8. 关联的收入确认（通过需求ID关联）
    revenues = []
    try:
        revenues = db.execute('SELECT * FROM revenue_confirmations WHERE requirement_id=? ORDER BY created_at DESC', (req_id,)).fetchall()
    except Exception:
        pass
    # 9. 关联的需求评审记录
    reviews = db.execute('SELECT * FROM requirement_reviews WHERE requirement_id=? ORDER BY created_at DESC', (req_id,)).fetchall()
    # 10. 分批交付明细
    delivery_batches = []
    try:
        delivery_batches = db.execute('SELECT * FROM delivery_batches WHERE requirement_id=? ORDER BY batch_no', (req_id,)).fetchall()
    except Exception:
        pass
    return render_template('requirement_detail.html', requirement=requirement,
                           versions=versions, tasks=tasks, bugs=bugs, test_cases=test_cases,
                           schedules=schedules, orders=orders, deliveries=deliveries,
                           revenues=revenues, reviews=reviews, delivery_batches=delivery_batches)

# ============================================================
# 模块2：产品规划与版本规划（合并页面，全面但简洁）
# ============================================================


# ============================================================
# 状态联动辅助函数
