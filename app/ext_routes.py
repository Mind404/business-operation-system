# -*- coding: utf-8 -*-
"""
扩展模块路由：测试组、交付服务组、产品总体组、质量组强化、研发组
每个模块遵循认知负荷优化原则：列表简洁、表单分步、必填优先
"""
from flask import Blueprint, render_template, request, redirect, url_for, jsonify, g, Response, flash, session, send_from_directory
from datetime import datetime
import io, os, json, random

ext_bp = Blueprint('ext', __name__)

def get_db():
    if 'db' not in g:
        import sqlite3, os
        from flask import current_app
        g.db = sqlite3.connect(current_app.config['DATABASE'])
        g.db.row_factory = sqlite3.Row
        g.db.execute('PRAGMA foreign_keys = ON')
    return g.db

@ext_bp.teardown_request
def close_db(exception):
    db = g.pop('db', None)
    if db is not None:
        db.close()

def log_operation(module, action, detail, operator='系统'):
    try:
        db = get_db()
        db.execute('INSERT INTO operation_logs (module, action, detail, operator) VALUES (?,?,?,?)',
                   (module, action, detail, operator))
        db.commit()
    except:
        pass

# ============================================================
# 测试组：测试用例库
# ============================================================
@ext_bp.route('/test/cases')
def test_case_list():
    db = get_db()
    search = request.args.get('search', '').strip()
    product_id = request.args.get('product_id', '')
    page = max(1, int(request.args.get('page', 1)))
    per_page = 15
    offset = (page - 1) * per_page
    where = []
    params = []
    if product_id:
        where.append('tc.product_id=?')
        params.append(product_id)
    if search:
        where.append('(tc.title LIKE ? OR tc.module LIKE ? OR tc.case_no LIKE ?)')
        params.extend([f'%{search}%']*3)
    where_sql = (' WHERE ' + ' AND '.join(where)) if where else ''
    total = db.execute(f'SELECT COUNT(*) FROM test_cases tc{where_sql}', params).fetchone()[0]
    cases = db.execute(f'''SELECT tc.*, p.name as product_name FROM test_cases tc
        LEFT JOIN products p ON tc.product_id=p.id
        {where_sql} ORDER BY tc.created_at DESC LIMIT ? OFFSET ?''',
        params + [per_page, offset]).fetchall()
    products = db.execute('SELECT id, name, model FROM products').fetchall()
    total_pages = (total + per_page - 1) // per_page
    return render_template('ext/test_case_list.html', cases=cases, products=products,
                           search=search, current_product=product_id, page=page, total_pages=total_pages, total=total)

@ext_bp.route('/test/cases/add', methods=['GET', 'POST'])
def test_case_add():
    db = get_db()
    if request.method == 'POST':
        case_no = request.form.get('case_no', '').strip()
        product_id = request.form.get('product_id')
        title = request.form.get('title', '').strip()
        module = request.form.get('module', '')
        precondition = request.form.get('precondition', '')
        test_steps = request.form.get('test_steps', '')
        expected_result = request.form.get('expected_result', '')
        priority = request.form.get('priority', '中')
        case_type = request.form.get('case_type', '功能测试')
        if not case_no:
            case_no = f'TC{datetime.now().strftime("%Y%m%d%H%M%S")}{random.randint(100,999)}'
        db.execute('''INSERT INTO test_cases (case_no, product_id, title, module, precondition,
            test_steps, expected_result, priority, case_type, creator)
            VALUES (?,?,?,?,?,?,?,?,?,?)''',
            (case_no, product_id, title, module, precondition, test_steps, expected_result, priority, case_type, 'tester'))
        db.commit()
        log_operation('测试用例', '新增', f'新增用例 {case_no}: {title}', 'tester')
        return redirect(url_for('ext.test_case_list'))
    products = db.execute('SELECT id, name, model FROM products').fetchall()
    return render_template('ext/test_case_form.html', products=products)

@ext_bp.route('/test/cases/<int:cid>')
def test_case_detail(cid):
    db = get_db()
    case = db.execute('''SELECT tc.*, p.name as product_name FROM test_cases tc
        LEFT JOIN products p ON tc.product_id=p.id WHERE tc.id=?''', (cid,)).fetchone()
    return render_template('ext/test_case_detail.html', case=case)

# ============================================================
# 测试组：测试执行
# ============================================================
@ext_bp.route('/test/executions')
def test_execution_list():
    db = get_db()
    executions = db.execute('''SELECT te.*, p.name as product_name FROM test_executions te
        LEFT JOIN products p ON te.product_id=p.id ORDER BY te.created_at DESC''').fetchall()
    return render_template('ext/test_execution_list.html', executions=executions)

@ext_bp.route('/test/executions/add', methods=['GET', 'POST'])
def test_execution_add():
    db = get_db()
    if request.method == 'POST':
        product_id = request.form.get('product_id')
        version_id = request.form.get('version_id')
        round_no = int(request.form.get('round_no', 1))
        executor = request.form.get('executor', 'tester')
        execution_no = f'TE{datetime.now().strftime("%Y%m%d%H%M%S")}{random.randint(100,999)}'
        # 统计该产品的用例数
        total = db.execute('SELECT COUNT(*) FROM test_cases WHERE product_id=? AND status="已启用"', (product_id,)).fetchone()[0]
        db.execute('''INSERT INTO test_executions (execution_no, product_id, version_id, round_no,
            executor, total_cases, status) VALUES (?,?,?,?,?,?,?)''',
            (execution_no, product_id, version_id, round_no, executor, total, '进行中'))
        db.commit()
        log_operation('测试执行', '新增', f'新增执行 {execution_no}，用例数 {total}', executor)
        return redirect(url_for('ext.test_execution_detail', eid=db.execute('SELECT last_insert_rowid()').fetchone()[0]))
    products = db.execute('SELECT id, name, model FROM products').fetchall()
    versions = db.execute('SELECT id, version_no, product_id FROM software_versions ORDER BY planned_release_date').fetchall()
    return render_template('ext/test_execution_form.html', products=products, versions=versions)

@ext_bp.route('/test/executions/<int:eid>')
def test_execution_detail(eid):
    db = get_db()
    execution = db.execute('''SELECT te.*, p.name as product_name FROM test_executions te
        LEFT JOIN products p ON te.product_id=p.id WHERE te.id=?''', (eid,)).fetchone()
    # 获取该产品的所有用例及执行结果
    cases = db.execute('''SELECT tc.*, tcr.result as exec_result, tcr.id as result_id, tcr.bug_id
        FROM test_cases tc
        LEFT JOIN test_case_results tcr ON tcr.case_id=tc.id AND tcr.execution_id=?
        WHERE tc.product_id=? AND tc.status="已启用"
        ORDER BY tc.module, tc.id''', (eid, execution['product_id'])).fetchall()
    return render_template('ext/test_execution_detail.html', execution=execution, cases=cases)

@ext_bp.route('/test/executions/<int:eid>/result', methods=['POST'])
def test_execution_result(eid):
    db = get_db()
    case_id = request.form.get('case_id')
    result = request.form.get('result')
    actual_result = request.form.get('actual_result', '')
    bug_id = request.form.get('bug_id') or None
    # 检查是否已有结果
    existing = db.execute('SELECT id FROM test_case_results WHERE execution_id=? AND case_id=?', (eid, case_id)).fetchone()
    if existing:
        db.execute('UPDATE test_case_results SET result=?, actual_result=?, bug_id=?, executed_at=?, executor=? WHERE id=?',
                   (result, actual_result, bug_id, datetime.now().strftime('%Y-%m-%d %H:%M:%S'), 'tester', existing['id']))
    else:
        db.execute('''INSERT INTO test_case_results (execution_id, case_id, result, actual_result, bug_id, executed_at, executor)
            VALUES (?,?,?,?,?,?,?)''', (eid, case_id, result, actual_result, bug_id, datetime.now().strftime('%Y-%m-%d %H:%M:%S'), 'tester'))
    # 更新执行统计
    passed = db.execute('SELECT COUNT(*) FROM test_case_results WHERE execution_id=? AND result="通过"', (eid,)).fetchone()[0]
    failed = db.execute('SELECT COUNT(*) FROM test_case_results WHERE execution_id=? AND result="失败"', (eid,)).fetchone()[0]
    blocked = db.execute('SELECT COUNT(*) FROM test_case_results WHERE execution_id=? AND result="阻塞"', (eid,)).fetchone()[0]
    total_done = passed + failed + blocked
    total = db.execute('SELECT total_cases FROM test_executions WHERE id=?', (eid,)).fetchone()[0]
    pass_rate = round(passed / total_done * 100, 1) if total_done > 0 else 0
    status = '已完成' if total_done >= total else '进行中'
    db.execute('UPDATE test_executions SET passed_cases=?, failed_cases=?, blocked_cases=?, pass_rate=?, status=? WHERE id=?',
               (passed, failed, blocked, pass_rate, status, eid))
    db.commit()
    return jsonify({'success': True, 'passed': passed, 'failed': failed, 'pass_rate': pass_rate, 'status': status})

# ============================================================
# 交付服务组B：技术支持工单
# ============================================================
@ext_bp.route('/support/tickets')
def ticket_list():
    db = get_db()
    status = request.args.get('status', '')
    search = request.args.get('search', '').strip()
    page = max(1, int(request.args.get('page', 1)))
    per_page = 15
    offset = (page - 1) * per_page
    where = []
    params = []
    if status:
        where.append('status=?')
        params.append(status)
    if search:
        where.append('(title LIKE ? OR customer LIKE ? OR ticket_no LIKE ?)')
        params.extend([f'%{search}%']*3)
    where_sql = (' WHERE ' + ' AND '.join(where)) if where else ''
    total = db.execute(f'SELECT COUNT(*) FROM tech_support_tickets{where_sql}', params).fetchone()[0]
    tickets = db.execute(f'SELECT * FROM tech_support_tickets{where_sql} ORDER BY created_at DESC LIMIT ? OFFSET ?',
                         params + [per_page, offset]).fetchall()
    total_pages = (total + per_page - 1) // per_page
    return render_template('ext/ticket_list.html', tickets=tickets, current_status=status,
                           search=search, page=page, total_pages=total_pages, total=total)

@ext_bp.route('/support/tickets/add', methods=['GET', 'POST'])
def ticket_add():
    db = get_db()
    if request.method == 'POST':
        customer = request.form.get('customer', '')
        contact_person = request.form.get('contact_person', '')
        contact_phone = request.form.get('contact_phone', '')
        product_id = request.form.get('product_id')
        title = request.form.get('title', '').strip()
        description = request.form.get('description', '')
        category = request.form.get('category', '技术咨询')
        priority = request.form.get('priority', '中')
        ticket_no = f'TS{datetime.now().strftime("%Y%m%d%H%M%S")}{random.randint(100,999)}'
        db.execute('''INSERT INTO tech_support_tickets (ticket_no, customer, contact_person, contact_phone,
            product_id, title, description, category, priority, status, handler)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)''',
            (ticket_no, customer, contact_person, contact_phone, product_id, title, description, category, priority, '待处理', 'delivery_b'))
        db.commit()
        log_operation('技术支持', '新增工单', f'{ticket_no}: {title}', 'delivery_b')
        return redirect(url_for('ext.ticket_list'))
    products = db.execute('SELECT id, name, model FROM products').fetchall()
    return render_template('ext/ticket_form.html', products=products)

@ext_bp.route('/support/tickets/<int:tid>')
def ticket_detail(tid):
    db = get_db()
    ticket = db.execute('''SELECT t.*, p.name as product_name FROM tech_support_tickets t
        LEFT JOIN products p ON t.product_id=p.id WHERE t.id=?''', (tid,)).fetchone()
    workflow = db.execute('SELECT * FROM ticket_workflow WHERE ticket_id=? ORDER BY created_at', (tid,)).fetchall()
    return render_template('ext/ticket_detail.html', ticket=ticket, workflow=workflow)

@ext_bp.route('/support/tickets/<int:tid>/update', methods=['POST'])
def ticket_update(tid):
    db = get_db()
    action = request.form.get('action')
    comment = request.form.get('comment', '')
    handler = request.form.get('handler', '')
    solution = request.form.get('solution', '')
    ticket = db.execute('SELECT * FROM tech_support_tickets WHERE id=?', (tid,)).fetchone()
    if action == 'assign':
        db.execute('UPDATE tech_support_tickets SET status="处理中", handler=? WHERE id=?', (handler, tid))
        db.execute('INSERT INTO ticket_workflow (ticket_id, action, operator, comment) VALUES (?,?,?,?)', (tid, '分配', 'delivery_b', f'分配给 {handler}'))
    elif action == 'resolve':
        db.execute('UPDATE tech_support_tickets SET status="已解决", solution=?, resolved_at=? WHERE id=?',
                   (solution, datetime.now().strftime('%Y-%m-%d %H:%M:%S'), tid))
        db.execute('INSERT INTO ticket_workflow (ticket_id, action, operator, comment) VALUES (?,?,?,?)', (tid, '解决', handler or 'delivery_b', solution))
    elif action == 'close':
        db.execute('UPDATE tech_support_tickets SET status="已关闭" WHERE id=?', (tid,))
        db.execute('INSERT INTO ticket_workflow (ticket_id, action, operator, comment) VALUES (?,?,?,?)', (tid, '关闭', 'delivery_b', comment))
    db.commit()
    return redirect(url_for('ext.ticket_detail', tid=tid))

# ============================================================
# 交付服务组B：用户反馈库
# ============================================================
@ext_bp.route('/feedback')
def feedback_list():
    db = get_db()
    feedback_type = request.args.get('type', '')
    status = request.args.get('status', '')
    search = request.args.get('search', '').strip()
    page = max(1, int(request.args.get('page', 1)))
    per_page = 15
    offset = (page - 1) * per_page
    where = []
    params = []
    if feedback_type:
        where.append('feedback_type=?')
        params.append(feedback_type)
    if status:
        where.append('status=?')
        params.append(status)
    if search:
        where.append('(title LIKE ? OR content LIKE ? OR customer LIKE ?)')
        params.extend([f'%{search}%']*3)
    where_sql = (' WHERE ' + ' AND '.join(where)) if where else ''
    total = db.execute(f'SELECT COUNT(*) FROM user_feedbacks{where_sql}', params).fetchone()[0]
    feedbacks = db.execute(f'''SELECT uf.*, p.name as product_name, r.title as req_title
        FROM user_feedbacks uf
        LEFT JOIN products p ON uf.product_id=p.id
        LEFT JOIN requirements r ON uf.converted_requirement_id=r.id
        {where_sql} ORDER BY uf.created_at DESC LIMIT ? OFFSET ?''',
        params + [per_page, offset]).fetchall()
    total_pages = (total + per_page - 1) // per_page
    return render_template('ext/feedback_list.html', feedbacks=feedbacks, current_type=feedback_type,
                           current_status=status, search=search, page=page, total_pages=total_pages, total=total)

@ext_bp.route('/feedback/add', methods=['GET', 'POST'])
def feedback_add():
    db = get_db()
    if request.method == 'POST':
        customer = request.form.get('customer', '')
        product_id = request.form.get('product_id')
        feedback_type = request.form.get('feedback_type', '功能建议')
        title = request.form.get('title', '').strip()
        content = request.form.get('content', '')
        source = request.form.get('source', '客户反馈')
        priority = request.form.get('priority', '中')
        db.execute('''INSERT INTO user_feedbacks (customer, product_id, feedback_type, title, content,
            source, priority, status, handler) VALUES (?,?,?,?,?,?,?,?,?)''',
            (customer, product_id, feedback_type, title, content, source, priority, '待评估', 'delivery_b'))
        db.commit()
        log_operation('用户反馈', '新增', f'{feedback_type}: {title}', 'delivery_b')
        return redirect(url_for('ext.feedback_list'))
    products = db.execute('SELECT id, name, model FROM products').fetchall()
    return render_template('ext/feedback_form.html', products=products)

@ext_bp.route('/feedback/<int:fid>/convert', methods=['POST'])
def feedback_convert(fid):
    db = get_db()
    feedback = db.execute('SELECT * FROM user_feedbacks WHERE id=?', (fid,)).fetchone()
    # 转化为正式需求
    db.execute('''INSERT INTO requirements (title, source_dept, customer, description, quantity, priority, status)
        VALUES (?,?,?,?,?,?,?)''',
        (feedback['title'], '交付服务组B', feedback['customer'], feedback['content'], 1, feedback['priority'], '待评审'))
    req_id = db.execute('SELECT last_insert_rowid()').fetchone()[0]
    db.execute('UPDATE user_feedbacks SET status="已转化", converted_requirement_id=? WHERE id=?', (req_id, fid))
    db.commit()
    log_operation('用户反馈', '转化需求', f'反馈#{fid} 转化为需求#{req_id}', 'delivery_b')
    return redirect(url_for('ext.feedback_list'))

# ============================================================
# 交付服务组A：交付计划
# ============================================================
@ext_bp.route('/delivery/plans')
def delivery_plan_list():
    """交付管理·交付跟踪：一页看清 需求→数量→生产→入库→可交付→是否已交付"""
    db = get_db()
    status = request.args.get('status', '')
    search = request.args.get('search', '').strip()
    page = max(1, int(request.args.get('page', 1)))
    per_page = 20
    offset = (page - 1) * per_page
    where = []
    params = []
    if status:
        where.append('dp.status=?')
        params.append(status)
    if search:
        where.append('(r.title LIKE ? OR r.customer LIKE ? OR p.name LIKE ?)')
        params.extend(['%{}%'.format(search)]*3)
    where_sql = (' WHERE ' + ' AND '.join(where)) if where else ''
    total = db.execute('SELECT COUNT(*) FROM delivery_plans dp ' + where_sql, params).fetchone()[0]
    plans = db.execute('''SELECT dp.*, p.name as product_name, p.model as product_model,
        r.title as req_title, r.customer as req_customer, r.delivery_date as req_delivery_date,
        r.quantity as req_quantity
        FROM delivery_plans dp
        LEFT JOIN products p ON dp.product_id=p.id
        LEFT JOIN requirements r ON dp.requirement_id=r.id
        ''' + where_sql + ''' ORDER BY dp.planned_date IS NULL, dp.planned_date ASC LIMIT ? OFFSET ?''',
        params + [per_page, offset]).fetchall()
    total_pages = (total + per_page - 1) // per_page
    stats = {
        'total': db.execute('SELECT COUNT(*) FROM delivery_plans').fetchone()[0],
        'undone': db.execute('SELECT COUNT(*) FROM delivery_plans WHERE delivered=0').fetchone()[0],
        'in_stock': db.execute('SELECT COUNT(*) FROM delivery_plans WHERE delivered=0 AND stock_date IS NOT NULL').fetchone()[0],
        'done': db.execute('SELECT COUNT(*) FROM delivery_plans WHERE delivered=1').fetchone()[0],
    }
    return render_template('ext/delivery_plan_list.html', plans=plans, current_status=status,
                           search=search, page=page, total_pages=total_pages, total=total, stats=stats)


@ext_bp.route('/delivery/plans/add', methods=['GET', 'POST'])
def delivery_plan_add():
    db = get_db()
    if request.method == 'POST':
        requirement_id = request.form.get('requirement_id')
        requirement_id = int(requirement_id) if requirement_id else None
        if not requirement_id:
            flash('请选择关联需求', 'error')
            return redirect(url_for('ext.delivery_plan_add'))
        req = db.execute('SELECT * FROM requirements WHERE id=? AND is_deleted=0', (requirement_id,)).fetchone()
        if not req:
            flash('关联需求不存在', 'error')
            return redirect(url_for('ext.delivery_plan_add'))
        customer = req['customer'] or request.form.get('customer', '')
        product_id = request.form.get('product_id')
        if not product_id:
            flash('请选择交付产品', 'error')
            return redirect(url_for('ext.delivery_plan_add'))
        quantity = int(request.form.get('quantity') or req['quantity'] or 1)
        # 校验：累计交付数量不得超过需求数量（分批交付总量守恒）
        if req['quantity']:
            done = db.execute('SELECT COALESCE(SUM(quantity),0) s FROM delivery_plans WHERE requirement_id=?', (requirement_id,)).fetchone()['s']
            if done + quantity > req['quantity']:
                flash(f'交付数量超出需求总量：已有{done}套，本次{quantity}套，需求总量{req["quantity"]}套', 'error')
                return redirect(url_for('ext.delivery_plan_add'))
        planned_date = request.form.get('planned_date') or req['delivery_date'] or ''
        owner = request.form.get('owner', 'delivery_a')
        remark = request.form.get('remark', '')
        production_date = request.form.get('production_date', '') or ''
        stock_date = request.form.get('stock_date', '') or ''
        delivered = 1 if request.form.get('delivered') else 0
        if delivered:
            status = '已交付'
        elif stock_date:
            status = '已入库'
        elif production_date:
            status = '生产中'
        else:
            status = '计划中'
        plan_no = 'DP{}'.format(datetime.now().strftime('%Y%m%d%H%M%S')) + str(random.randint(100,999))
        db.execute('''INSERT INTO delivery_plans (plan_no, customer, product_id, quantity, planned_date,
            production_date, stock_date, delivered, status, owner, remark, requirement_id)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)''',
            (plan_no, customer, product_id, quantity, planned_date, production_date, stock_date,
             delivered, status, owner, remark, requirement_id))
        db.commit()
        log_operation('交付管理', '新增交付', '{}: {} {}套 状态={}'.format(plan_no, customer, quantity, status), owner)
        flash('交付计划已创建（关联需求：{}）'.format(req['title']), 'success')
        return redirect(url_for('ext.delivery_plan_list'))
    products = db.execute('SELECT id, name, model FROM products').fetchall()
    requirements = db.execute('''SELECT id, title, customer, quantity, delivery_date, status
        FROM requirements WHERE is_deleted=0 ORDER BY delivery_date''').fetchall()
    return render_template('ext/delivery_plan_form.html', products=products, requirements=requirements, plan=None)


@ext_bp.route('/delivery/plans/<int:plan_id>/edit', methods=['GET', 'POST'])
def delivery_plan_edit(plan_id):
    db = get_db()
    plan = db.execute('''SELECT dp.*, p.name as product_name, r.title as req_title, r.customer as req_customer
        FROM delivery_plans dp
        LEFT JOIN products p ON dp.product_id=p.id
        LEFT JOIN requirements r ON dp.requirement_id=r.id
        WHERE dp.id=?''', (plan_id,)).fetchone()
    if not plan:
        flash('交付记录不存在', 'error')
        return redirect(url_for('ext.delivery_plan_list'))
    if request.method == 'POST':
        quantity = int(request.form.get('quantity', plan['quantity']))
        # 校验：累计交付数量不得超过需求数量（编辑时排除自身）
        if plan['requirement_id']:
            req_q = db.execute('SELECT quantity FROM requirements WHERE id=?', (plan['requirement_id'],)).fetchone()
            if req_q and req_q['quantity']:
                done = db.execute('SELECT COALESCE(SUM(quantity),0) s FROM delivery_plans WHERE requirement_id=? AND id!=?', (plan['requirement_id'], plan_id)).fetchone()['s']
                if done + quantity > req_q['quantity']:
                    flash(f'交付数量超出需求总量：已有{done}套，本次{quantity}套，需求总量{req_q["quantity"]}套', 'error')
                    return redirect(url_for('ext.delivery_plan_edit', plan_id=plan_id))
        planned_date = request.form.get('planned_date', '') or plan['planned_date']
        production_date = request.form.get('production_date', '') or plan['production_date']
        stock_date = request.form.get('stock_date', '') or plan['stock_date']
        delivered = 1 if request.form.get('delivered') else 0
        remark = request.form.get('remark', '') or plan['remark']
        if delivered:
            status = '已交付'
        elif stock_date:
            status = '已入库'
        elif production_date:
            status = '生产中'
        else:
            status = '计划中'
        db.execute('''UPDATE delivery_plans SET quantity=?, planned_date=?, production_date=?,
            stock_date=?, delivered=?, status=?, remark=? WHERE id=?''',
            (quantity, planned_date, production_date, stock_date, delivered, status, remark, plan_id))
        db.commit()
        log_operation('交付管理', '更新交付', '{}: 状态={}'.format(plan['plan_no'], status), plan['owner'])
        flash('交付记录已更新', 'success')
        return redirect(url_for('ext.delivery_plan_list'))
    products = db.execute('SELECT id, name, model FROM products').fetchall()
    return render_template('ext/delivery_plan_form.html', products=products, requirements=None, plan=plan)


@ext_bp.route('/delivery/plans/<int:plan_id>/delete', methods=['POST'])
def delivery_plan_delete(plan_id):
    db = get_db()
    plan = db.execute('SELECT * FROM delivery_plans WHERE id=?', (plan_id,)).fetchone()
    if plan:
        db.execute('DELETE FROM delivery_plans WHERE id=?', (plan_id,))
        db.commit()
        log_operation('交付管理', '删除交付', plan['plan_no'], plan['owner'])
        flash('交付记录已删除', 'success')
    return redirect(url_for('ext.delivery_plan_list'))


# ============================================================
# 交付服务组A：收入确认
# ============================================================
@ext_bp.route('/revenue')
def revenue_list():
    db = get_db()
    status = request.args.get('status', '')
    search = request.args.get('search', '').strip()
    page = max(1, int(request.args.get('page', 1)))
    per_page = 15
    offset = (page - 1) * per_page
    where = []
    params = []
    if status:
        where.append('status=?')
        params.append(status)
    if search:
        where.append('(customer LIKE ? OR contract_no LIKE ? OR confirm_no LIKE ?)')
        params.extend([f'%{search}%']*3)
    where_sql = (' WHERE ' + ' AND '.join(where)) if where else ''
    total = db.execute(f'SELECT COUNT(*) FROM revenue_confirmations{where_sql}', params).fetchone()[0]
    revenues = db.execute(f'''SELECT rc.*, p.name as product_name FROM revenue_confirmations rc
        LEFT JOIN products p ON rc.product_id=p.id
        {where_sql} ORDER BY rc.created_at DESC LIMIT ? OFFSET ?''',
        params + [per_page, offset]).fetchall()
    total_pages = (total + per_page - 1) // per_page
    # 统计
    total_amount = db.execute('SELECT COALESCE(SUM(amount),0) FROM revenue_confirmations').fetchone()[0]
    confirmed_amount = db.execute('SELECT COALESCE(SUM(confirmed_amount),0) FROM revenue_confirmations').fetchone()[0]
    return render_template('ext/revenue_list.html', revenues=revenues, current_status=status,
                           search=search, page=page, total_pages=total_pages, total=total,
                           total_amount=total_amount, confirmed_amount=confirmed_amount)

@ext_bp.route('/revenue/add', methods=['GET', 'POST'])
def revenue_add():
    db = get_db()
    if request.method == 'POST':
        customer = request.form.get('customer', '')
        contract_no = request.form.get('contract_no', '')
        product_id = request.form.get('product_id')
        amount = float(request.form.get('amount', 0))
        period = request.form.get('period', '')
        handler = request.form.get('handler', 'delivery_a')
        remark = request.form.get('remark', '')
        confirm_no = f'RC{datetime.now().strftime("%Y%m%d%H%M%S")}{random.randint(100,999)}'
        db.execute('''INSERT INTO revenue_confirmations (confirm_no, customer, contract_no, product_id,
            amount, period, status, handler, remark) VALUES (?,?,?,?,?,?,?,?,?)''',
            (confirm_no, customer, contract_no, product_id, amount, period, '待确认', handler, remark))
        db.commit()
        log_operation('收入确认', '新增', f'{confirm_no}: {customer} {amount}元', handler)
        return redirect(url_for('ext.revenue_list'))
    products = db.execute('SELECT id, name, model FROM products').fetchall()
    return render_template('ext/revenue_form.html', products=products)

@ext_bp.route('/revenue/<int:rid>/confirm', methods=['POST'])
def revenue_confirm(rid):
    db = get_db()
    confirmed_amount = float(request.form.get('confirmed_amount', 0))
    confirm_date = request.form.get('confirm_date', datetime.now().strftime('%Y-%m-%d'))
    voucher = request.form.get('voucher', '')
    rec = db.execute('SELECT amount FROM revenue_confirmations WHERE id=?', (rid,)).fetchone()
    if not rec:
        db.close()
        return '收入确认记录不存在', 404
    if confirmed_amount < 0 or confirmed_amount > rec['amount']:
        db.close()
        return f'确认金额无效：应在 0 ~ {rec["amount"]:.2f} 元之间', 400
    db.execute('UPDATE revenue_confirmations SET status="已确认", confirmed_amount=?, confirm_date=?, voucher=? WHERE id=?',
               (confirmed_amount, confirm_date, voucher, rid))
    db.commit()
    log_operation('收入确认', '确认', f'#{rid} 确认收入 {confirmed_amount}元', 'delivery_a')
    return redirect(url_for('ext.revenue_list'))

# ============================================================
# 产品总体组B：需求评审
# ============================================================
@ext_bp.route('/requirements/<int:rid>/reviews')
def requirement_reviews(rid):
    db = get_db()
    requirement = db.execute('SELECT * FROM requirements WHERE id=? AND is_deleted=0', (rid,)).fetchone()
    reviews = db.execute('SELECT * FROM requirement_reviews WHERE requirement_id=? ORDER BY created_at DESC', (rid,)).fetchall()
    return render_template('ext/requirement_reviews.html', requirement=requirement, reviews=reviews)

@ext_bp.route('/requirements/<int:rid>/reviews/add', methods=['POST'])
def requirement_review_add(rid):
    db = get_db()
    reviewer = request.form.get('reviewer', 'product_b')
    conclusion = request.form.get('conclusion', '待评审')
    opinion = request.form.get('opinion', '')
    priority_suggested = request.form.get('priority_suggested', '')
    version_suggested = request.form.get('version_suggested', '')
    db.execute('''INSERT INTO requirement_reviews (requirement_id, reviewer, review_date, conclusion,
        opinion, priority_suggested, version_suggested) VALUES (?,?,?,?,?,?,?)''',
        (rid, reviewer, datetime.now().strftime('%Y-%m-%d'), conclusion, opinion, priority_suggested, version_suggested))
    # 如果评审通过，更新需求状态
    if conclusion == '通过':
        db.execute('UPDATE requirements SET status="已评审" WHERE id=?', (rid,))
    db.commit()
    log_operation('需求评审', '新增', f'需求#{rid} 评审结论: {conclusion}', reviewer)
    return redirect(url_for('ext.requirement_reviews', rid=rid))

# ============================================================
# 产品总体组B：技术规格
# ============================================================
@ext_bp.route('/tech-specs')
def tech_spec_list():
    db = get_db()
    product_id = request.args.get('product_id', '')
    search = request.args.get('search', '').strip()
    where = []
    params = []
    if product_id:
        where.append('ts.product_id=?')
        params.append(product_id)
    if search:
        where.append('(ts.spec_name LIKE ? OR ts.content LIKE ?)')
        params.extend([f'%{search}%']*2)
    where_sql = (' WHERE ' + ' AND '.join(where)) if where else ''
    specs = db.execute(f'''SELECT ts.*, p.name as product_name FROM tech_specs ts
        LEFT JOIN products p ON ts.product_id=p.id
        {where_sql} ORDER BY ts.created_at DESC''', params).fetchall()
    products = db.execute('SELECT id, name, model FROM products').fetchall()
    return render_template('ext/tech_spec_list.html', specs=specs, products=products, current_product=product_id, search=search)

@ext_bp.route('/tech-specs/add', methods=['GET', 'POST'])
def tech_spec_add():
    db = get_db()
    if request.method == 'POST':
        product_id = request.form.get('product_id')
        version_id = request.form.get('version_id') or None
        spec_name = request.form.get('spec_name', '').strip()
        spec_type = request.form.get('spec_type', '功能规格')
        content = request.form.get('content', '')
        parameters = request.form.get('parameters', '')
        interfaces = request.form.get('interfaces', '')
        performance = request.form.get('performance', '')
        author = request.form.get('author', 'product_b')
        db.execute('''INSERT INTO tech_specs (product_id, version_id, spec_name, spec_type, content,
            parameters, interfaces, performance, status, author) VALUES (?,?,?,?,?,?,?,?,?,?)''',
            (product_id, version_id, spec_name, spec_type, content, parameters, interfaces, performance, '草稿', author))
        db.commit()
        log_operation('技术规格', '新增', f'{spec_name}', author)
        return redirect(url_for('ext.tech_spec_list'))
    products = db.execute('SELECT id, name, model FROM products').fetchall()
    versions = db.execute('SELECT id, version_no, product_id FROM software_versions ORDER BY planned_release_date').fetchall()
    return render_template('ext/tech_spec_form.html', products=products, versions=versions)

@ext_bp.route('/tech-specs/<int:sid>')
def tech_spec_detail(sid):
    db = get_db()
    spec = db.execute('''SELECT ts.*, p.name as product_name, sv.version_no FROM tech_specs ts
        LEFT JOIN products p ON ts.product_id=p.id
        LEFT JOIN software_versions sv ON ts.version_id=sv.id WHERE ts.id=?''', (sid,)).fetchone()
    return render_template('ext/tech_spec_detail.html', spec=spec)

# ============================================================
# 产品总体组A：合同跟进
# ============================================================
# 合同跟进：仅关联生产合同与物料采购合同（用户合同已整合至交付管理）
# ============================================================
CONTRACT_TYPES_PROD = ['生产合同', '物料合同']
CONTRACT_TYPES_USER = ['用户合同']

@ext_bp.route('/contracts')
def contract_list():
    db = get_db()
    status = request.args.get('status', '')
    search = request.args.get('search', '').strip()
    page = max(1, int(request.args.get('page', 1)))
    per_page = 15
    offset = (page - 1) * per_page
    where = ["c.contract_type IN ('生产合同','物料合同')"]
    params = []
    if status:
        where.append('c.status=?')
        params.append(status)
    if search:
        where.append('(c.supplier LIKE ? OR c.contract_no LIKE ?)')
        params.extend([f'%{search}%']*2)
    where_sql = ' WHERE ' + ' AND '.join(where)
    total = db.execute(f'SELECT COUNT(*) FROM contracts c{where_sql}', params).fetchone()[0]
    contracts = db.execute(f'''SELECT c.*, p.name as product_name FROM contracts c
        LEFT JOIN products p ON c.product_id=p.id
        {where_sql} ORDER BY c.created_at DESC LIMIT ? OFFSET ?''',
        params + [per_page, offset]).fetchall()
    total_pages = (total + per_page - 1) // per_page
    return render_template('ext/contract_list.html', contracts=contracts, current_status=status,
                           search=search, page=page, total_pages=total_pages, total=total)

# ============================================================
# 用户合同（交付管理板块）：contract_type 固定为 '用户合同'
# ============================================================
@ext_bp.route('/user-contracts')
def user_contract_list():
    """用户合同（销售合同）列表：整合在交付管理板块"""
    db = get_db()
    status = request.args.get('status', '')
    search = request.args.get('search', '').strip()
    where = ["c.contract_type='用户合同'"]
    params = []
    if status:
        where.append('c.status=?')
        params.append(status)
    if search:
        where.append('(c.supplier LIKE ? OR c.contract_no LIKE ?)')
        params.extend([f'%{search}%']*2)
    where_sql = ' WHERE ' + ' AND '.join(where)
    contracts = db.execute(f'''SELECT c.*, p.name as product_name,
        (SELECT COUNT(*) FROM contract_payments cp WHERE cp.contract_id=c.id) as node_count
        FROM contracts c LEFT JOIN products p ON c.product_id=p.id
        {where_sql} ORDER BY c.created_at DESC''', params).fetchall()
    total_amount = sum(c['amount'] for c in contracts)
    paid_amount = sum(c['paid_amount'] or 0 for c in contracts)
    db.close()
    return render_template('ext/user_contract_list.html', contracts=contracts,
                           current_status=status, search=search, total=len(contracts),
                           total_amount=total_amount, paid_amount=paid_amount)

@ext_bp.route('/contracts/add', methods=['GET', 'POST'])
def contract_add():
    """新建合同：type=user 为交付板块用户合同，否则为生产/物料合同"""
    db = get_db()
    ctype = request.args.get('type', 'prod')
    is_user = (ctype == 'user')
    contract_types = CONTRACT_TYPES_USER if is_user else CONTRACT_TYPES_PROD
    back_url = url_for('ext.user_contract_list') if is_user else url_for('ext.contract_list')
    if request.method == 'POST':
        contract_no = request.form.get('contract_no', f'CT{datetime.now().strftime("%Y%m%d%H%M%S")}{random.randint(100,999)}')
        contract_type = '用户合同' if is_user else request.form.get('contract_type', '生产合同')
        supplier = request.form.get('supplier', '')
        product_id = request.form.get('product_id')
        amount = float(request.form.get('amount', 0))
        sign_date = request.form.get('sign_date', '')
        delivery_date = request.form.get('delivery_date', '')
        owner = request.form.get('owner', 'product_a')
        progress = request.form.get('progress', '')
        remark = request.form.get('remark', '')
        db.execute('''INSERT INTO contracts (contract_no, contract_type, supplier, product_id, amount,
            sign_date, delivery_date, status, owner, progress, payment_status, remark)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)''',
            (contract_no, contract_type, supplier, product_id, amount, sign_date, delivery_date, '执行中', owner, progress, '未付款', remark))
        db.commit()
        log_operation('合同', '新增', f'{contract_no}: {supplier} {amount}元', owner)
        return redirect(back_url)
    products = db.execute('SELECT id, name, model FROM products').fetchall()
    return render_template('ext/contract_form.html', products=products, contract=None,
                           contract_types=contract_types, back_url=back_url, ctype=ctype)

@ext_bp.route('/contracts/<int:cid>/edit', methods=['GET', 'POST'])
def contract_edit(cid):
    """编辑合同（所有字段可修改）；type=user 为交付板块用户合同"""
    db = get_db()
    contract = db.execute('SELECT * FROM contracts WHERE id=?', (cid,)).fetchone()
    if not contract:
        db.close()
        return '合同不存在', 404
    ctype = request.args.get('type', '')
    if not ctype and contract['contract_type'] == '用户合同':
        ctype = 'user'
    is_user = (ctype == 'user')
    contract_types = CONTRACT_TYPES_USER if is_user else CONTRACT_TYPES_PROD
    back_url = url_for('ext.user_contract_list') if is_user else url_for('ext.contract_list')
    if request.method == 'POST':
        contract_type = '用户合同' if is_user else request.form.get('contract_type', contract['contract_type'])
        supplier = request.form.get('supplier', contract['supplier'])
        product_id = request.form.get('product_id') or contract['product_id']
        amount = float(request.form.get('amount', contract['amount']))
        paid_amount = float(request.form.get('paid_amount', contract['paid_amount'] or 0))
        sign_date = request.form.get('sign_date', contract['sign_date'])
        delivery_date = request.form.get('delivery_date', contract['delivery_date'])
        status = request.form.get('status', contract['status'])
        owner = request.form.get('owner', contract['owner'])
        progress = request.form.get('progress', contract['progress'])
        payment_status = request.form.get('payment_status', contract['payment_status'])
        remark = request.form.get('remark', contract['remark'])
        # 自动计算付款状态
        if paid_amount >= amount and amount > 0:
            payment_status = '已付款'
        elif paid_amount > 0:
            payment_status = '部分付款'
        else:
            payment_status = '未付款'
        db.execute('''UPDATE contracts SET contract_type=?, supplier=?, product_id=?, amount=?, paid_amount=?,
            sign_date=?, delivery_date=?, status=?, owner=?, progress=?, payment_status=?, remark=? WHERE id=?''',
            (contract_type, supplier, product_id, amount, paid_amount, sign_date, delivery_date, status, owner, progress, payment_status, remark, cid))
        db.commit()
        log_operation('合同', '编辑', f'{contract["contract_no"]}: 更新合同信息，已付{paid_amount}元')
        db.close()
        return redirect(url_for('ext.contract_edit', cid=cid, type=ctype))
    products = db.execute('SELECT id, name, model FROM products').fetchall()
    payments = db.execute('SELECT * FROM contract_payments WHERE contract_id=? ORDER BY plan_date', (cid,)).fetchall()
    db.close()
    return render_template('ext/contract_form.html', products=products, contract=contract,
                           payments=payments, contract_types=contract_types, back_url=back_url, ctype=ctype)

@ext_bp.route('/contracts/<int:cid>/payments/add', methods=['POST'])
def contract_payment_add(cid):
    """添加付款/收款节点"""
    db = get_db()
    contract = db.execute('SELECT * FROM contracts WHERE id=?', (cid,)).fetchone()
    if not contract:
        db.close()
        return '合同不存在', 404
    node_name = request.form.get('node_name', '')
    plan_date = request.form.get('plan_date', '')
    amount = float(request.form.get('amount', 0))
    status = request.form.get('status', '待付款')
    actual_date = request.form.get('actual_date', '')
    remark = request.form.get('remark', '')
    if node_name and amount > 0:
        db.execute('''INSERT INTO contract_payments (contract_id, node_name, plan_date, amount, status, actual_date, remark)
            VALUES (?,?,?,?,?,?,?)''', (cid, node_name, plan_date, amount, status, actual_date, remark))
        # 如果节点状态是已付款，更新合同已付款金额
        if status == '已付款':
            db.execute('UPDATE contracts SET paid_amount = COALESCE(paid_amount, 0) + ? WHERE id=?', (amount, cid))
        db.commit()
        log_operation('合同', '添加付款节点', f'{contract["contract_no"]}: {node_name} {amount}元')
    db.close()
    return redirect(url_for('ext.contract_edit', cid=cid))

@ext_bp.route('/contracts/<int:cid>/payments/<int:pid>/update', methods=['POST'])
def contract_payment_update(cid, pid):
    """更新付款/收款节点状态"""
    db = get_db()
    payment = db.execute('SELECT * FROM contract_payments WHERE id=? AND contract_id=?', (pid, cid)).fetchone()
    if not payment:
        db.close()
        return '付款节点不存在', 404
    new_status = request.form.get('status', payment['status'])
    actual_date = request.form.get('actual_date', '')
    old_status = payment['status']
    # 更新节点状态
    db.execute('UPDATE contract_payments SET status=?, actual_date=? WHERE id=?', (new_status, actual_date or payment['actual_date'], pid))
    # 如果从非已付款变为已付款，增加合同已付款金额
    if old_status != '已付款' and new_status == '已付款':
        db.execute('UPDATE contracts SET paid_amount = COALESCE(paid_amount, 0) + ? WHERE id=?', (payment['amount'], cid))
    # 如果从已付款变为非已付款，减少合同已付款金额
    elif old_status == '已付款' and new_status != '已付款':
        db.execute('UPDATE contracts SET paid_amount = MAX(0, COALESCE(paid_amount, 0) - ?) WHERE id=?', (payment['amount'], cid))
    # 自动更新合同付款状态
    contract = db.execute('SELECT * FROM contracts WHERE id=?', (cid,)).fetchone()
    if contract:
        if contract['paid_amount'] >= contract['amount'] and contract['amount'] > 0:
            db.execute('UPDATE contracts SET payment_status=? WHERE id=?', ('已付款', cid))
        elif contract['paid_amount'] > 0:
            db.execute('UPDATE contracts SET payment_status=? WHERE id=?', ('部分付款', cid))
        else:
            db.execute('UPDATE contracts SET payment_status=? WHERE id=?', ('未付款', cid))
    db.commit()
    log_operation('合同', '更新付款节点', f'节点{pid}: {old_status}→{new_status}')
    db.close()
    return redirect(url_for('ext.contract_edit', cid=cid))

@ext_bp.route('/contracts/<int:cid>/payments/<int:pid>/delete', methods=['POST'])
def contract_payment_delete(cid, pid):
    """删除付款/收款节点"""
    db = get_db()
    payment = db.execute('SELECT * FROM contract_payments WHERE id=? AND contract_id=?', (pid, cid)).fetchone()
    if not payment:
        db.close()
        return '付款节点不存在', 404
    # 如果节点已付款，减少合同已付款金额
    if payment['status'] == '已付款':
        db.execute('UPDATE contracts SET paid_amount = MAX(0, COALESCE(paid_amount, 0) - ?) WHERE id=?', (payment['amount'], cid))
    db.execute('DELETE FROM contract_payments WHERE id=?', (pid,))
    db.commit()
    log_operation('合同', '删除付款节点', f'节点{pid}: {payment["node_name"]}')
    db.close()
    return redirect(url_for('ext.contract_edit', cid=cid))

@ext_bp.route('/contracts/<int:cid>/update-status', methods=['POST'])
def contract_update_status(cid):
    """快速更新合同状态和付款状态"""
    db = get_db()
    contract = db.execute('SELECT * FROM contracts WHERE id=?', (cid,)).fetchone()
    if not contract:
        db.close()
        return '合同不存在', 404
    status = request.form.get('status', contract['status'])
    payment_status = request.form.get('payment_status', contract['payment_status'])
    progress = request.form.get('progress', contract['progress'])
    operator = request.form.get('operator', '')
    db.execute('UPDATE contracts SET status=?, payment_status=?, progress=? WHERE id=?',
               (status, payment_status, progress, cid))
    db.commit()
    log_operation('合同', '状态更新', f'{contract["contract_no"]}: 状态→{status}, 付款→{payment_status}', operator)
    db.close()
    return redirect(request.referrer or url_for('ext.contract_list'))

# ============================================================
# 产品总体组A：资源调度
# ============================================================
@ext_bp.route('/resources')
def resource_list():
    db = get_db()
    resource_type = request.args.get('type', '')
    status = request.args.get('status', '')
    where = []
    params = []
    if resource_type:
        where.append('resource_type=?')
        params.append(resource_type)
    if status:
        where.append('status=?')
        params.append(status)
    where_sql = (' WHERE ' + ' AND '.join(where)) if where else ''
    resources = db.execute(f'SELECT * FROM resource_schedules{where_sql} ORDER BY start_date DESC', params).fetchall()
    return render_template('ext/resource_list.html', resources=resources, current_type=resource_type, current_status=status)

@ext_bp.route('/resources/add', methods=['GET', 'POST'])
def resource_add():
    db = get_db()
    if request.method == 'POST':
        resource_type = request.form.get('resource_type', '人力资源')
        resource_name = request.form.get('resource_name', '').strip()
        project = request.form.get('project', '')
        task = request.form.get('task', '')
        start_date = request.form.get('start_date', '')
        end_date = request.form.get('end_date', '')
        allocation = int(request.form.get('allocation', 100))
        owner = request.form.get('owner', 'product_a')
        remark = request.form.get('remark', '')
        db.execute('''INSERT INTO resource_schedules (resource_type, resource_name, project, task,
            start_date, end_date, allocation, status, owner, remark) VALUES (?,?,?,?,?,?,?,?,?,?)''',
            (resource_type, resource_name, project, task, start_date, end_date, allocation, '已分配', owner, remark))
        db.commit()
        log_operation('资源调度', '新增', f'{resource_type}: {resource_name} 分配给 {project}', owner)
        return redirect(url_for('ext.resource_list'))
    return render_template('ext/resource_form.html')

# ============================================================
# 交付服务组C：设备运维 + OTA版本管理（合并页，双子页）
# ============================================================
def _get_product_id_by_model(model):
    """按产品型号/名称匹配 product_id"""
    if not model:
        return None
    db = get_db()
    m = str(model).strip()
    row = db.execute("SELECT id FROM products WHERE model=? OR name=? LIMIT 1", (m, m)).fetchone()
    return row['id'] if row else None

@ext_bp.route('/device-center')
def device_center():
    """设备运维与OTA版本管理合并页（tab: device / ota）"""
    db = get_db()
    tab = request.args.get('tab', 'device')
    # ---- 设备运维数据 ----
    d_status = request.args.get('d_status', '')
    d_search = request.args.get('d_search', '').strip()
    d_page = max(1, int(request.args.get('d_page', 1)))
    per_page = 12
    offset = (d_page - 1) * per_page
    where = []
    params = []
    if d_status:
        where.append('d.status=?')
        params.append(d_status)
    if d_search:
        where.append('(d.device_sn LIKE ? OR d.customer LIKE ? OR d.firmware_version LIKE ?)')
        params.extend([f'%{d_search}%'] * 3)
    where_sql = (' WHERE ' + ' AND '.join(where)) if where else ''
    d_total = db.execute(f'SELECT COUNT(*) FROM devices d{where_sql}', params).fetchone()[0]
    devices = db.execute(f'''SELECT d.*, p.name as product_name FROM devices d
        LEFT JOIN products p ON d.product_id=p.id
        {where_sql} ORDER BY d.created_at DESC LIMIT ? OFFSET ?''',
        params + [per_page, offset]).fetchall()
    d_pages = max(1, (d_total + per_page - 1) // per_page)
    online = db.execute("SELECT COUNT(*) FROM devices WHERE status='在线'").fetchone()[0]
    offline = db.execute("SELECT COUNT(*) FROM devices WHERE status='离线'").fetchone()[0]
    inactive = db.execute("SELECT COUNT(*) FROM devices WHERE status='未激活'").fetchone()[0]
    alerts = db.execute("SELECT COUNT(*) FROM device_alerts WHERE status='未处理'").fetchone()[0]
    # 最近版本变更（全局监控视图）
    recent_changes = db.execute('''SELECT vc.*, d.device_sn as sn FROM device_version_changes vc
        LEFT JOIN devices d ON vc.device_id=d.id
        ORDER BY vc.changed_at DESC LIMIT 8''').fetchall()
    # ---- OTA版本数据 ----
    o_status = request.args.get('o_status', '')
    o_search = request.args.get('o_search', '').strip()
    o_where, o_params = [], []
    if o_status:
        o_where.append('ov.status=?')
        o_params.append(o_status)
    if o_search:
        o_where.append('(ov.version_no LIKE ? OR ov.file_name LIKE ?)')
        o_params.extend([f'%{o_search}%'] * 2)
    o_where_sql = (' WHERE ' + ' AND '.join(o_where)) if o_where else ''
    otas = db.execute(f'''SELECT ov.*, p.name as product_name FROM ota_versions ov
        LEFT JOIN products p ON ov.product_id=p.id
        {o_where_sql} ORDER BY ov.created_at DESC''', o_params).fetchall()
    o_total = len(otas)
    o_released = sum(1 for x in otas if x['status'] == '已发布')
    o_testing = sum(1 for x in otas if x['status'] == '测试中')
    o_rolled = sum(1 for x in otas if x['status'] == '已回滚')
    return render_template('ext/device_center.html', tab=tab,
                           devices=devices, d_total=d_total, d_pages=d_pages,
                           d_status=d_status, d_search=d_search,
                           online=online, offline=offline, inactive=inactive, alerts=alerts,
                           recent_changes=recent_changes,
                           otas=otas, o_status=o_status, o_search=o_search,
                           o_total=o_total, o_released=o_released, o_testing=o_testing, o_rolled=o_rolled)

# ---- Excel 模板下载 ----
def _make_template(filename, sheet_title, headers, sample_rows, note_lines):
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Font, PatternFill, Alignment
    except ImportError:
        return '服务端缺少openpyxl', 500
    wb = Workbook()
    ws = wb.active
    ws.title = sheet_title
    head_fill = PatternFill('solid', fgColor='1D4ED8')
    head_font = Font(color='FFFFFF', bold=True)
    ws.append(headers)
    for c in ws[1]:
        c.fill = head_fill
        c.font = head_font
        c.alignment = Alignment(horizontal='center', vertical='center')
    for row in sample_rows:
        ws.append(row)
    ws2 = wb.create_sheet('填写说明')
    for line in note_lines:
        ws2.append([line])
    ws2.column_dimensions['A'].width = 100
    widths = [max(len(str(h)) * 2 + 6, 14) for h in headers]
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[chr(64 + i) if i <= 26 else 'A'].width = min(w, 40)
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    resp = Response(buf.getvalue(), mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    # 中文文件名需RFC5987编码，避免latin-1编码错误
    from urllib.parse import quote
    resp.headers['Content-Disposition'] = "attachment; filename*=UTF-8''" + quote(filename)
    return resp

@ext_bp.route('/device-center/template/devices')
def device_template():
    return _make_template(
        '设备登记模板.xlsx', '设备台账',
        ['设备SN(必填)', '产品型号', '客户', '固件版本', '状态', '最后在线', '位置', '备注'],
        [['SN-DEMO-0001', 'SAM302', '示例客户', 'V1.3.0', '在线', '2026-09-20 10:00', '重庆', '示例行，可删除'],
         ['SN-DEMO-0002', 'SAM203', '', 'V1.2.1', '离线', '', '成都', '']],
        ['1. 首行表头请勿修改；蓝色表头为固定字段。',
         '2. 设备SN必填且不重复；重复SN将更新该设备信息（固件变更自动记录）。',
         '3. 产品型号填写产品名称或型号（如 SAM302 / SAM203），留空则该设备不关联产品。',
         '4. 状态取值：在线 / 离线 / 未激活（默认未激活）。',
         '5. 最后在线格式：YYYY-MM-DD HH:MM。'])

@ext_bp.route('/device-center/template/ota')
def ota_template():
    return _make_template(
        'OTA版本模板.xlsx', 'OTA版本台账',
        ['版本号(必填)', '产品型号', '文件名', '文件大小(MB)', 'MD5', '发布说明', '发布策略', '发布比例(%)', '状态', '创建人'],
        [['V1.5.0', 'SAM302', 'SAM302_V1.5.0.bin', 2.4, 'a1b2c3d4e5f6', '新增闭环功率控制', '灰度发布', 30, '测试中', '张三'],
         ['V1.3.0', 'SAM302', 'SAM302_V1.3.0.bin', 2.1, 'f6e5d4c3b2a1', '基础版本', '全量发布', 100, '已发布', '张三']],
        ['1. 首行表头请勿修改；版本号必填且不重复（重复版本号将被跳过）。',
         '2. 产品型号填写产品名称或型号（如 SAM302 / SAM203），留空则不关联产品。',
         '3. 发布策略：全量发布 / 灰度发布；发布比例1-100。',
         '4. 状态取值：测试中 / 已发布 / 已回滚（默认测试中）。',
         '5. 文件大小单位为MB（数字）。'])

# ---- Excel 导入 ----
@ext_bp.route('/device-center/import/devices', methods=['POST'])
def device_import():
    from app import can_write
    if not can_write('ext.device'):
        flash('无权限：设备运维当前为只读', 'error')
        return redirect(url_for('ext.device_center', tab='device'))
    file = request.files.get('file')
    if not file or not file.filename:
        flash('请选择要导入的Excel文件', 'error')
        return redirect(url_for('ext.device_center', tab='device'))
    try:
        from openpyxl import load_workbook
    except ImportError:
        flash('服务端缺少openpyxl，无法导入', 'error')
        return redirect(url_for('ext.device_center', tab='device'))
    try:
        wb = load_workbook(io.BytesIO(file.read()), data_only=True)
        ws = wb.worksheets[0]
        rows = list(ws.iter_rows(values_only=True))
    except Exception:
        flash('Excel解析失败，请使用系统提供的模板', 'error')
        return redirect(url_for('ext.device_center', tab='device'))
    db = get_db()
    added = updated = skipped = 0
    err_lines = []
    for idx, r in enumerate(rows[1:], 2):
        if not r or all(v is None or str(v).strip() == '' for v in r[:3]):
            continue
        sn = str(r[0]).strip() if r[0] is not None else ''
        if not sn:
            err_lines.append(f'第{idx}行：设备SN为空')
            continue
        model = str(r[1]).strip() if r[1] is not None else ''
        customer = str(r[2]).strip() if r[2] is not None else ''
        fw = str(r[3]).strip() if r[3] is not None else ''
        status = str(r[4]).strip() if r[4] is not None else '未激活'
        if status not in ('在线', '离线', '未激活'):
            status = '未激活'
        last_online = str(r[5]).strip() if r[5] is not None else ''
        location = str(r[6]).strip() if r[6] is not None else ''
        remark = str(r[7]).strip() if r[7] is not None else ''
        pid = _get_product_id_by_model(model)
        exist = db.execute('SELECT * FROM devices WHERE device_sn=?', (sn,)).fetchone()
        if exist:
            # 更新，固件变化则记录变更
            old_fw = exist['firmware_version'] or ''
            db.execute('''UPDATE devices SET product_id=?, customer=?, firmware_version=?, status=?,
                          last_online=?, location=?, remark=? WHERE id=?''',
                       (pid or exist['product_id'], customer or exist['customer'],
                        fw or old_fw, status, last_online or exist['last_online'],
                        location or exist['location'], remark, exist['id']))
            if fw and old_fw != fw:
                db.execute('''INSERT INTO device_version_changes (device_id, device_sn, old_version,
                              new_version, change_type, reason, operator)
                              VALUES (?,?,?,?,?,?,?)''',
                           (exist['id'], sn, old_fw or '-', fw, '批量导入更新', 'Excel批量导入', '导入'))
            updated += 1
        else:
            cur = db.execute('''INSERT INTO devices (device_sn, product_id, customer, firmware_version,
                                status, last_online, location, remark)
                                VALUES (?,?,?,?,?,?,?,?)''',
                             (sn, pid, customer, fw, status, last_online, location, remark))
            db.execute('''INSERT INTO device_version_changes (device_id, device_sn, old_version,
                          new_version, change_type, reason, operator)
                          VALUES (?,?,?,?,?,?,?)''',
                       (cur.lastrowid, sn, '-', fw or '-', '出厂登记', '批量导入', '导入'))
            added += 1
    db.commit()
    log_operation('设备', '批量导入', f'新增{added}台，更新{updated}台，跳过{skipped}，异常{len(err_lines)}行', '导入')
    msg = f'设备导入完成：新增 {added} 台，更新 {updated} 台' + (f'，{len(err_lines)} 行异常' if err_lines else '')
    flash(msg, 'success')
    return redirect(url_for('ext.device_center', tab='device'))

@ext_bp.route('/device-center/import/ota', methods=['POST'])
def ota_import():
    from app import can_write
    if not can_write('ext.ota'):
        flash('无权限：OTA版本管理当前为只读', 'error')
        return redirect(url_for('ext.device_center', tab='ota'))
    file = request.files.get('file')
    if not file or not file.filename:
        flash('请选择要导入的Excel文件', 'error')
        return redirect(url_for('ext.device_center', tab='ota'))
    try:
        from openpyxl import load_workbook
    except ImportError:
        flash('服务端缺少openpyxl，无法导入', 'error')
        return redirect(url_for('ext.device_center', tab='ota'))
    try:
        wb = load_workbook(io.BytesIO(file.read()), data_only=True)
        ws = wb.worksheets[0]
        rows = list(ws.iter_rows(values_only=True))
    except Exception:
        flash('Excel解析失败，请使用系统提供的模板', 'error')
        return redirect(url_for('ext.device_center', tab='ota'))
    db = get_db()
    added = skipped = 0
    err_lines = []
    for idx, r in enumerate(rows[1:], 2):
        if not r or all(v is None or str(v).strip() == '' for v in r[:2]):
            continue
        ver = str(r[0]).strip() if r[0] is not None else ''
        if not ver:
            err_lines.append(f'第{idx}行：版本号为空')
            continue
        model = str(r[1]).strip() if r[1] is not None else ''
        file_name = str(r[2]).strip() if r[2] is not None else ''
        file_size = None
        try:
            file_size = int(float(r[3]) * 1024 * 1024) if r[3] not in (None, '') else None
        except Exception:
            pass
        md5 = str(r[4]).strip() if r[4] is not None else ''
        notes = str(r[5]).strip() if r[5] is not None else ''
        strategy = str(r[6]).strip() if r[6] is not None else '全量发布'
        pct = 100
        try:
            pct = min(100, max(1, int(float(r[7])))) if r[7] not in (None, '') else 100
        except Exception:
            pct = 100
        status = str(r[8]).strip() if r[8] is not None else '测试中'
        if status not in ('测试中', '已发布', '已回滚'):
            status = '测试中'
        creator = str(r[9]).strip() if r[9] is not None else '导入'
        pid = _get_product_id_by_model(model)
        exist = db.execute('SELECT id FROM ota_versions WHERE version_no=?', (ver,)).fetchone()
        if exist:
            skipped += 1
            continue
        cur = db.execute('''INSERT INTO ota_versions (version_no, product_id, file_name, file_size, md5,
                            release_notes, status, rollout_strategy, rollout_percentage, creator,
                            published_at, published_by)
                            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)''',
                         (ver, pid, file_name, file_size, md5, notes, status, strategy, pct, creator,
                          datetime.now().strftime('%Y-%m-%d %H:%M') if status == '已发布' else None, creator if status == '已发布' else None))
        added += 1
    db.commit()
    log_operation('OTA', '批量导入', f'新增{added}个版本，跳过重复{skipped}个', '导入')
    flash(f'OTA版本导入完成：新增 {added} 个版本，跳过重复 {skipped} 个', 'success')
    return redirect(url_for('ext.device_center', tab='ota'))

# ---- Excel 导出 ----
@ext_bp.route('/device-center/export/devices')
def device_export():
    db = get_db()
    rows = db.execute('''SELECT d.device_sn, p.name as product_name, d.customer, d.firmware_version,
                         d.status, d.last_online, d.location, d.remark, d.activate_date
                         FROM devices d LEFT JOIN products p ON d.product_id=p.id
                         ORDER BY d.created_at DESC''').fetchall()
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Font, PatternFill
    except ImportError:
        return '服务端缺少openpyxl', 500
    wb = Workbook()
    ws = wb.active
    ws.title = '设备台账'
    ws.append(['设备SN', '产品型号', '客户', '固件版本', '状态', '最后在线', '位置', '激活日期', '备注'])
    for c in ws[1]:
        c.font = Font(bold=True, color='FFFFFF')
        c.fill = PatternFill('solid', fgColor='1D4ED8')
    for r in rows:
        ws.append([r['device_sn'], r['product_name'], r['customer'], r['firmware_version'],
                   r['status'], r['last_online'], r['location'], r['activate_date'], r['remark']])
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    resp = Response(buf.getvalue(), mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    from urllib.parse import quote
    resp.headers['Content-Disposition'] = "attachment; filename*=UTF-8''" + quote('devices.xlsx')
    return resp

@ext_bp.route('/device-center/export/ota')
def ota_export():
    db = get_db()
    rows = db.execute('''SELECT ov.version_no, p.name as product_name, ov.file_name,
                         ROUND(ov.file_size/1024.0/1024.0, 2) as size_mb, ov.md5, ov.release_notes,
                         ov.rollout_strategy, ov.rollout_percentage, ov.status, ov.creator,
                         ov.created_at, ov.published_at
                         FROM ota_versions ov LEFT JOIN products p ON ov.product_id=p.id
                         ORDER BY ov.created_at DESC''').fetchall()
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Font, PatternFill
    except ImportError:
        return '服务端缺少openpyxl', 500
    wb = Workbook()
    ws = wb.active
    ws.title = 'OTA版本台账'
    ws.append(['版本号', '产品型号', '文件名', '大小(MB)', 'MD5', '发布说明', '发布策略',
               '发布比例(%)', '状态', '创建人', '创建时间', '发布时间'])
    for c in ws[1]:
        c.font = Font(bold=True, color='FFFFFF')
        c.fill = PatternFill('solid', fgColor='1D4ED8')
    for r in rows:
        ws.append([r['version_no'], r['product_name'], r['file_name'], r['size_mb'], r['md5'],
                   r['release_notes'], r['rollout_strategy'], r['rollout_percentage'], r['status'],
                   r['creator'], r['created_at'], r['published_at']])
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    resp = Response(buf.getvalue(), mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    from urllib.parse import quote
    resp.headers['Content-Disposition'] = "attachment; filename*=UTF-8''" + quote('ota_versions.xlsx')
    return resp

@ext_bp.route('/devices')
def device_list():
    """旧入口：跳转到合并页设备运维tab"""
    return redirect(url_for('ext.device_center', tab='device'))

@ext_bp.route('/devices/add', methods=['GET', 'POST'])
def device_add():
    db = get_db()
    if request.method == 'POST':
        device_sn = request.form.get('device_sn', '').strip()
        product_id = request.form.get('product_id')
        customer = request.form.get('customer', '')
        firmware_version = request.form.get('firmware_version', '')
        location = request.form.get('location', '')
        remark = request.form.get('remark', '')
        cur = db.execute('''INSERT INTO devices (device_sn, product_id, customer, firmware_version,
            status, location, remark) VALUES (?,?,?,?,?,?,?)''',
            (device_sn, product_id, customer, firmware_version, '未激活', location, remark))
        db.execute('''INSERT INTO device_version_changes (device_id, device_sn, old_version,
                      new_version, change_type, reason, operator)
                      VALUES (?,?,?,?,?,?,?)''',
                   (cur.lastrowid, device_sn, '-', firmware_version or '-', '出厂登记', '设备登记', '登记'))
        db.commit()
        log_operation('设备', '新增', f'{device_sn}: {customer}', 'delivery_c')
        return redirect(url_for('ext.device_center', tab='device'))
    products = db.execute('SELECT id, name, model FROM products').fetchall()
    return render_template('ext/device_form.html', products=products)

@ext_bp.route('/devices/<int:did>')
def device_detail(did):
    db = get_db()
    device = db.execute('''SELECT d.*, p.name as product_name FROM devices d
        LEFT JOIN products p ON d.product_id=p.id WHERE d.id=?''', (did,)).fetchone()
    if not device:
        return '设备不存在', 404
    alerts = db.execute('SELECT * FROM device_alerts WHERE device_id=? ORDER BY created_at DESC LIMIT 10', (did,)).fetchall()
    upgrades = db.execute('''SELECT ou.*, ov.version_no, ov.status as ota_status FROM ota_upgrades ou
        LEFT JOIN ota_versions ov ON ou.ota_version_id=ov.id
        WHERE ou.device_id=? ORDER BY ou.created_at DESC LIMIT 10''', (did,)).fetchall()
    changes = db.execute('''SELECT * FROM device_version_changes WHERE device_id=?
                            ORDER BY changed_at DESC, id DESC LIMIT 20''', (did,)).fetchall()
    return render_template('ext/device_detail.html', device=device, alerts=alerts,
                           upgrades=upgrades, changes=changes)

# ============================================================
# 交付服务组C：OTA版本管理
# ============================================================
@ext_bp.route('/ota')
def ota_list():
    """旧入口：跳转到合并页OTA tab"""
    return redirect(url_for('ext.device_center', tab='ota'))

@ext_bp.route('/ota/add', methods=['GET', 'POST'])
def ota_add():
    db = get_db()
    if request.method == 'POST':
        version_no = request.form.get('version_no', '').strip()
        product_id = request.form.get('product_id')
        file_name = request.form.get('file_name', '')
        file_size = int(request.form.get('file_size', 0))
        md5 = request.form.get('md5', '')
        release_notes = request.form.get('release_notes', '')
        rollout_strategy = request.form.get('rollout_strategy', '全量发布')
        rollout_percentage = int(request.form.get('rollout_percentage', 100))
        creator = request.form.get('creator', 'delivery_c')
        db.execute('''INSERT INTO ota_versions (version_no, product_id, file_name, file_size, md5,
            release_notes, status, rollout_strategy, rollout_percentage, creator)
            VALUES (?,?,?,?,?,?,?,?,?,?)''',
            (version_no, product_id, file_name, file_size, md5, release_notes, '测试中', rollout_strategy, rollout_percentage, creator))
        db.commit()
        log_operation('OTA', '新增版本', f'{version_no}: {file_name}', creator)
        return redirect(url_for('ext.device_center', tab='ota'))
    products = db.execute('SELECT id, name, model FROM products').fetchall()
    return render_template('ext/ota_form.html', products=products)

@ext_bp.route('/ota/<int:oid>/publish', methods=['POST'])
def ota_publish(oid):
    db = get_db()
    status = request.form.get('status', '已发布')
    now = datetime.now().strftime('%Y-%m-%d %H:%M')
    if status == '已发布':
        db.execute('UPDATE ota_versions SET status=?, published_at=?, published_by=? WHERE id=?',
                   (status, now, request.form.get('operator') or 'delivery_c', oid))
    else:
        db.execute('UPDATE ota_versions SET status=? WHERE id=?', (status, oid))
    db.commit()
    log_operation('OTA', '发布', f'版本#{oid} 状态改为 {status}', 'delivery_c')
    return redirect(url_for('ext.device_center', tab='ota'))

# ---- 设备状态监控与版本变更 ----
@ext_bp.route('/devices/<int:did>/status', methods=['POST'])
def device_status_update(did):
    """更新设备在线状态"""
    from app import can_write
    if not can_write('ext.device'):
        return jsonify(ok=False, msg='只读权限'), 403
    db = get_db()
    status = request.form.get('status', '在线')
    db.execute("UPDATE devices SET status=? WHERE id=?", (status, did))
    db.commit()
    log_operation('设备', '状态更新', f'设备#{did} → {status}', 'delivery_c')
    return redirect(url_for('ext.device_detail', did=did))

@ext_bp.route('/devices/<int:did>/firmware', methods=['POST'])
def device_firmware_update(did):
    """登记设备固件版本变更（自动留痕）"""
    from app import can_write
    if not can_write('ext.device'):
        return jsonify(ok=False, msg='只读权限'), 403
    db = get_db()
    dev = db.execute('SELECT * FROM devices WHERE id=?', (did,)).fetchone()
    if not dev:
        return '设备不存在', 404
    new_fw = request.form.get('new_version', '').strip()
    change_type = request.form.get('change_type', 'OTA升级')
    reason = request.form.get('reason', '').strip()
    operator = request.form.get('operator', 'delivery_c').strip()
    if not new_fw:
        return '新固件版本不能为空', 400
    old_fw = dev['firmware_version'] or '-'
    db.execute('UPDATE devices SET firmware_version=? WHERE id=?', (new_fw, did))
    db.execute('''INSERT INTO device_version_changes (device_id, device_sn, old_version,
                  new_version, change_type, reason, operator)
                  VALUES (?,?,?,?,?,?,?)''',
               (did, dev['device_sn'], old_fw, new_fw, change_type, reason, operator))
    db.commit()
    log_operation('设备', '版本变更', f'{dev["device_sn"]}: {old_fw} → {new_fw} ({change_type})', operator)
    return redirect(url_for('ext.device_detail', did=did))

# ============================================================
# 统一任务追踪（个人工作台核心）
# ============================================================
@ext_bp.route('/my-tasks')
def my_tasks():
    """我的任务看板：待处理/进行中/已完成 三列布局"""
    from flask import session
    db = get_db()
    current_user = session.get('username', 'admin')
    current_role = session.get('role', 'admin')
    status = request.args.get('status', '')
    search = request.args.get('search', '').strip()

    # 自动同步各模块待办事项
    sync_auto_tasks(db, current_user, current_role)

    where = ['assignee=?']
    params = [current_user]
    if status:
        where.append('status=?')
        params.append(status)
    if search:
        where.append('(title LIKE ? OR description LIKE ? OR expected_result LIKE ?)')
        params.extend([f'%{search}%']*3)
    where_sql = ' AND '.join(where)

    todo = db.execute(f'SELECT * FROM tasks WHERE {where_sql} AND status="待处理" ORDER BY priority DESC, due_date ASC', params).fetchall()
    doing = db.execute(f'SELECT * FROM tasks WHERE {where_sql} AND status="进行中" ORDER BY priority DESC, due_date ASC', params).fetchall()
    done = db.execute(f'SELECT * FROM tasks WHERE {where_sql} AND status="已完成" ORDER BY completed_at DESC LIMIT 20', params).fetchall()

    # 统计
    stats = {
        'todo': len(todo),
        'doing': len(doing),
        'done': len(done),
        'overdue': db.execute(f'SELECT COUNT(*) FROM tasks WHERE {where_sql} AND status IN ("待处理","进行中") AND due_date < date("now","localtime") AND due_date != ""', params).fetchone()[0],
    }
    return render_template('ext/my_tasks.html', todo=todo, doing=doing, done=done,
                           stats=stats, current_status=status, search=search,
                           now_date=datetime.now().strftime('%Y-%m-%d'))

def sync_auto_tasks(db, current_user, current_role):
    """自动同步各模块待办事项到任务表（避免重复创建）"""
    auto_tasks = []
    # 产品组：需求评审 + 合同跟进
    if current_role in ('product', 'admin'):
        reqs = db.execute("SELECT id, title FROM requirements WHERE status='待评审' AND is_deleted=0").fetchall()
        for r in reqs:
            auto_tasks.append(('需求评审', f'评审需求：{r["title"]}', 'requirements', r['id'], '评审通过后纳入版本规划', 7))
    # 研发测试组：测试执行 + 开发任务
    if current_role in ('dev_test', 'admin'):
        execs = db.execute("SELECT id, execution_no FROM test_executions WHERE status='进行中'").fetchall()
        for e in execs:
            auto_tasks.append(('测试执行', f'执行测试：{e["execution_no"]}', 'test_executions', e['id'], '完成所有用例执行并记录结果', 3))
        tasks_dev = db.execute("SELECT id, task_name as title FROM dev_tasks WHERE status IN ('待开发','开发中') AND assignee=?", (current_user,)).fetchall()
        for t in tasks_dev:
            auto_tasks.append(('开发任务', f'开发：{t["title"]}', 'dev_tasks', t['id'], '完成开发并自测通过', 7))
    # 质量组：Bug处理 + 入库审批
    if current_role in ('quality', 'admin'):
        bugs = db.execute("SELECT id, bug_no, title FROM bug_records WHERE status IN ('待处理','处理中') AND assignee=?", (current_user,)).fetchall()
        for b in bugs:
            auto_tasks.append(('Bug处理', f'处理Bug {b["bug_no"]}：{b["title"]}', 'bugs', b['id'], '分析根因并修复，必要时升级质量问题', 5))
        receipts = db.execute("SELECT id, receipt_no FROM warehouse_receipts WHERE status='待审批'").fetchall()
        for r in receipts:
            auto_tasks.append(('入库审批', f'审批入库单：{r["receipt_no"]}', 'warehouse', r['id'], '检验合格后批准入库', 2))
    # 交付服务组：收入确认 + 技术支持 + 设备告警
    if current_role in ('delivery', 'admin'):
        revenues = db.execute("SELECT id, confirm_no, customer FROM revenue_confirmations WHERE status='待确认'").fetchall()
        for r in revenues:
            auto_tasks.append(('收入确认', f'确认收入：{r["confirm_no"]} ({r["customer"]})', 'revenue', r['id'], '核对合同与交付，确认收入金额', 3))
        tickets = db.execute("SELECT id, ticket_no, title FROM tech_support_tickets WHERE status IN ('待处理','处理中') AND handler=?", (current_user,)).fetchall()
        for t in tickets:
            auto_tasks.append(('技术支持', f'处理工单 {t["ticket_no"]}：{t["title"]}', 'tickets', t['id'], '解决客户问题并关闭工单', 2))
        alerts = db.execute("SELECT id, alert_type, content FROM device_alerts WHERE status='未处理'").fetchall()
        for a in alerts:
            auto_tasks.append(('设备告警', f'处理告警：{a["alert_type"]} - {a["content"][:30]}', 'devices', a['id'], '排查告警原因并恢复设备正常', 1))

    # 批量插入（避免重复）
    for module, title, source_type, source_id, expected, days in auto_tasks:
        existing = db.execute("SELECT id FROM tasks WHERE source_type=? AND source_id=? AND assignee=? AND status!='已完成'",
                              (source_type, source_id, current_user)).fetchone()
        if not existing:
            from datetime import datetime, timedelta
            due = (datetime.now() + timedelta(days=days)).strftime('%Y-%m-%d')
            task_no = f'TASK{datetime.now().strftime("%Y%m%d%H%M%S")}{source_id}'
            db.execute('''INSERT OR IGNORE INTO tasks (task_no, title, module, source_type, source_id,
                assignee, creator, priority, status, due_date, expected_result)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)''',
                (task_no, title, module, source_type, source_id, current_user, '系统', '中', '待处理', due, expected))
    db.commit()

@ext_bp.route('/my-tasks/add', methods=['GET', 'POST'])
def task_add():
    from flask import session
    db = get_db()
    if request.method == 'POST':
        title = request.form.get('title', '').strip()
        description = request.form.get('description', '')
        module = request.form.get('module', '其他')
        assignee = request.form.get('assignee', session.get('username', 'admin'))
        priority = request.form.get('priority', '中')
        due_date = request.form.get('due_date', '')
        expected_result = request.form.get('expected_result', '')
        task_no = f'TASK{datetime.now().strftime("%Y%m%d%H%M%S")}{random.randint(100,999)}'
        db.execute('''INSERT INTO tasks (task_no, title, description, module, assignee, creator,
            priority, status, due_date, expected_result) VALUES (?,?,?,?,?,?,?,?,?,?)''',
            (task_no, title, description, module, assignee, session.get('username', 'admin'), priority, '待处理', due_date, expected_result))
        db.commit()
        log_operation('任务', '新增', f'{task_no}: {title}', session.get('username', 'admin'))
        return redirect(url_for('ext.my_tasks'))
    return render_template('ext/task_form.html')

@ext_bp.route('/my-tasks/<int:tid>')
def task_detail(tid):
    db = get_db()
    task = db.execute('SELECT * FROM tasks WHERE id=?', (tid,)).fetchone()
    return render_template('ext/task_detail.html', task=task)

@ext_bp.route('/my-tasks/<int:tid>/update', methods=['POST'])
def task_update(tid):
    from flask import session
    db = get_db()
    status = request.form.get('status')
    progress = request.form.get('progress')
    actual_result = request.form.get('actual_result', '')
    task = db.execute('SELECT * FROM tasks WHERE id=?', (tid,)).fetchone()
    updates = []
    params = []
    if status:
        updates.append('status=?')
        params.append(status)
        if status == '已完成':
            updates.append('completed_at=?')
            params.append(datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
            updates.append('progress=100')
    if progress:
        updates.append('progress=?')
        params.append(int(progress))
    if actual_result:
        updates.append('actual_result=?')
        params.append(actual_result)
    updates.append('updated_at=?')
    params.append(datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
    params.append(tid)
    db.execute(f'UPDATE tasks SET {", ".join(updates)} WHERE id=?', params)
    db.commit()
    log_operation('任务', '更新', f'#{tid} 状态改为 {status or task["status"]}', session.get('username', 'admin'))
    return redirect(url_for('ext.my_tasks'))


# ============================================================
# 插件A：外部技术需求统计（集团系统侧技术特性跟踪）
# 字段：阶段/类型分组/事项/计划完成时间/问题描述/版本响应要求
# 进展：滚动日志子表（tech_req_logs），每条带日期+记录人
# ============================================================
@ext_bp.route('/tech-reqs')
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
    return render_template('ext/tech_req_list.html', rows=rows, stages=stages,
                           stage=stage, status=status, q=q)


@ext_bp.route('/tech-reqs/add', methods=['GET', 'POST'])
def tech_req_add():
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
        return redirect(url_for('ext.tech_req_list'))
    return render_template('ext/tech_req_form.html', r=None)


@ext_bp.route('/tech-reqs/<int:rid>/edit', methods=['GET', 'POST'])
def tech_req_edit(rid):
    db = get_db()
    r = db.execute('SELECT * FROM tech_requirements WHERE id=?', (rid,)).fetchone()
    if not r:
        return redirect(url_for('ext.tech_req_list'))
    if request.method == 'POST':
        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        db.execute('''UPDATE tech_requirements SET stage=?, category=?, item=?, planned_date=?,
            description=?, version_response=?, status=?, updated_at=? WHERE id=?''',
            (request.form.get('stage',''), request.form.get('category',''), request.form.get('item',''),
             request.form.get('planned_date',''), request.form.get('description',''),
             request.form.get('version_response',''), request.form.get('status','进行中'), now, rid))
        db.commit()
        log_operation('外部技术需求', '编辑', f'#{rid} {request.form.get("item","")}', session.get('username','admin'))
        return redirect(url_for('ext.tech_req_detail', rid=rid))
    return render_template('ext/tech_req_form.html', r=r)


@ext_bp.route('/tech-reqs/<int:rid>')
def tech_req_detail(rid):
    db = get_db()
    r = db.execute('SELECT * FROM tech_requirements WHERE id=?', (rid,)).fetchone()
    if not r:
        return redirect(url_for('ext.tech_req_list'))
    logs = db.execute('SELECT * FROM tech_req_logs WHERE tech_req_id=? ORDER BY log_date DESC, id DESC', (rid,)).fetchall()
    return render_template('ext/tech_req_detail.html', r=r, logs=logs)


@ext_bp.route('/tech-reqs/<int:rid>/log', methods=['POST'])
def tech_req_add_log(rid):
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
    return redirect(url_for('ext.tech_req_detail', rid=rid))


@ext_bp.route('/tech-reqs/<int:rid>/delete', methods=['POST'])
def tech_req_delete(rid):
    db = get_db()
    db.execute('DELETE FROM tech_req_logs WHERE tech_req_id=?', (rid,))
    db.execute('DELETE FROM tech_requirements WHERE id=?', (rid,))
    db.commit()
    log_operation('外部技术需求', '删除', f'#{rid}', session.get('username','admin'))
    return redirect(url_for('ext.tech_req_list'))


@ext_bp.route('/tech-reqs/import', methods=['POST'])
def tech_req_import():
    """按图1格式导入：序号|类型|事项|计划完成时间|描述|优化事项及进展|版本响应要求"""
    f = request.files.get('file')
    if not f:
        flash('请选择文件', 'error')
        return redirect(url_for('ext.tech_req_list'))
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
    return redirect(url_for('ext.tech_req_list'))


# ============================================================
# 插件B：WBS计划管理（项目工作分解结构）
# 字段：WBS编号|阶段/任务|工作内容|负责人|协作支撑|计划开始|计划结束|交付物
# ============================================================
@ext_bp.route('/wbs')
def wbs_projects():
    db = get_db()
    rows = db.execute('''SELECT p.*,
        (SELECT COUNT(*) FROM wbs_tasks t WHERE t.project_id=p.id) AS task_count,
        (SELECT COUNT(*) FROM wbs_tasks t WHERE t.project_id=p.id AND t.parent_id=0) AS phase_count
        FROM wbs_projects p ORDER BY p.id DESC''').fetchall()
    return render_template('ext/wbs_projects.html', rows=rows)


@ext_bp.route('/wbs/add', methods=['GET', 'POST'])
def wbs_project_add():
    db = get_db()
    if request.method == 'POST':
        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        db.execute('''INSERT INTO wbs_projects (name, description, owner, start_date, end_date, status, created_at, updated_at)
            VALUES (?,?,?,?,?,?,?,?)''',
            (request.form.get('name',''), request.form.get('description',''), request.form.get('owner',''),
             request.form.get('start_date',''), request.form.get('end_date',''),
             request.form.get('status','进行中'), now, now))
        pid = db.execute('SELECT last_insert_rowid()').fetchone()[0]
        db.commit()
        log_operation('WBS', '新建项目', request.form.get('name',''), session.get('username','admin'))
        return redirect(url_for('ext.wbs_project_detail', pid=pid))
    return render_template('ext/wbs_project_form.html', p=None)


@ext_bp.route('/wbs/<int:pid>')
def wbs_project_detail(pid):
    db = get_db()
    p = db.execute('SELECT * FROM wbs_projects WHERE id=?', (pid,)).fetchone()
    if not p:
        return redirect(url_for('ext.wbs_projects'))
    tasks = db.execute('''SELECT * FROM wbs_tasks WHERE project_id=? ORDER BY wbs_code, id''', (pid,)).fetchall()
    # 分组：父任务（阶段）和子任务
    phases = [t for t in tasks if t['parent_id'] == 0]
    return render_template('ext/wbs_project_detail.html', p=p, tasks=tasks, phases=phases)


def _save_upload(file_storage, subdir='wbs'):
    """保存上传文件到 uploads/<subdir>/，返回相对路径"""
    if not file_storage or not file_storage.filename:
        return ''
    import os, time
    upload_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'uploads', subdir)
    os.makedirs(upload_dir, exist_ok=True)
    fname = time.strftime('%Y%m%d%H%M%S_') + os.path.basename(file_storage.filename)
    safe = ''.join(c for c in fname if c.isalnum() or c in '._-（）')
    file_storage.save(os.path.join(upload_dir, safe))
    return f'{subdir}/{safe}'


@ext_bp.route('/wbs/<int:pid>/task/add', methods=['POST'])
def wbs_task_add(pid):
    db = get_db()
    parent_id = request.form.get('parent_id', 0) or 0
    attach = _save_upload(request.files.get('attachment'), 'wbs')
    db.execute('''INSERT INTO wbs_tasks
        (project_id, wbs_code, parent_id, name, work_content, owner, collaborators,
         plan_start, plan_end, deliverable, priority, attachment_path, status, progress)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
        (pid, request.form.get('wbs_code',''), parent_id, request.form.get('name',''),
         request.form.get('work_content',''), request.form.get('owner',''), request.form.get('collaborators',''),
         request.form.get('plan_start',''), request.form.get('plan_end',''),
         request.form.get('deliverable',''), request.form.get('priority','P2中'),
         attach, request.form.get('status','未开始'), int(request.form.get('progress',0))))
    db.commit()
    log_operation('WBS', '新增任务', f'P{pid} {request.form.get("name","")}', session.get('username','admin'))
    return redirect(url_for('ext.wbs_project_detail', pid=pid))


@ext_bp.route('/wbs/task/<int:tid>/edit', methods=['GET', 'POST'])
def wbs_task_edit(tid):
    if session.get('user_role') != 'admin':
        flash('只有管理员可以修改任务，如需调整请联系管理员', 'error')
        return redirect(request.referrer or url_for('ext.wbs_projects'))
    db = get_db()
    t = db.execute('SELECT * FROM wbs_tasks WHERE id=?', (tid,)).fetchone()
    if not t:
        return redirect(url_for('ext.wbs_projects'))
    if request.method == 'POST':
        attach = _save_upload(request.files.get('attachment'), 'wbs') or t['attachment_path']
        db.execute('''UPDATE wbs_tasks SET wbs_code=?, name=?, work_content=?, owner=?, collaborators=?,
            plan_start=?, plan_end=?, deliverable=?, priority=?, attachment_path=?, status=?, progress=? WHERE id=?''',
            (request.form.get('wbs_code',t['wbs_code']), request.form.get('name',t['name']),
             request.form.get('work_content',t['work_content']), request.form.get('owner',t['owner']),
             request.form.get('collaborators',t['collaborators']), request.form.get('plan_start',t['plan_start']),
             request.form.get('plan_end',t['plan_end']), request.form.get('deliverable',t['deliverable']),
             request.form.get('priority',t['priority']), attach,
             request.form.get('status',t['status']), int(request.form.get('progress',t['progress'])), tid))
        db.commit()
        return redirect(url_for('ext.wbs_project_detail', pid=t['project_id']))
    # GET：渲染编辑表单
    phases = db.execute('SELECT * FROM wbs_tasks WHERE project_id=? AND parent_id=0 ORDER BY wbs_code',
                        (t['project_id'],)).fetchall()
    return render_template('ext/wbs_task_form.html', t=t, phases=phases)


@ext_bp.route('/wbs/task/<int:tid>/delete', methods=['POST'])
def wbs_task_delete(tid):
    if session.get('user_role') != 'admin':
        flash('只有管理员可以删除任务', 'error')
        return redirect(request.referrer or url_for('ext.wbs_projects'))
    db = get_db()
    t = db.execute('SELECT * FROM wbs_tasks WHERE id=?', (tid,)).fetchone()
    if t:
        db.execute('DELETE FROM wbs_tasks WHERE id=? OR parent_id=?', (tid, tid))
        db.commit()
    return redirect(url_for('ext.wbs_project_detail', pid=t['project_id'] if t else 0))


@ext_bp.route('/wbs/download/<path:filename>')
def wbs_download(filename):
    import os
    upload_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'uploads')
    return send_from_directory(upload_dir, filename)


@ext_bp.route('/wbs/<int:pid>/import', methods=['POST'])
def wbs_import(pid):
    """按图2格式导入：WBS编号|阶段/任务|工作内容|负责人|协作支撑|计划开始|计划结束|交付物"""
    f = request.files.get('file')
    if not f:
        flash('请选择文件', 'error')
        return redirect(url_for('ext.wbs_project_detail', pid=pid))
    import openpyxl
    wb = openpyxl.load_workbook(f, data_only=True)
    ws = wb.active
    db = get_db()
    count = 0
    for row in ws.iter_rows(min_row=2, values_only=True):  # 第1行表头
        if not row or not row[0]:
            continue
        code = str(row[0]).strip()
        name = str(row[1] or '').strip()
        if not name:
            continue
        # 判断是否为阶段（父任务）：wbs_code 不含小数点
        is_phase = '.' not in code
        parent_id = 0
        if not is_phase:
            # 找父任务（wbs_code 前缀，如 1.1 的父是 1）
            parent_code = code.split('.')[0]
            parent = db.execute('SELECT id FROM wbs_tasks WHERE project_id=? AND wbs_code=?',
                                (pid, parent_code)).fetchone()
            if parent:
                parent_id = parent['id']
        db.execute('''INSERT INTO wbs_tasks
            (project_id, wbs_code, parent_id, name, work_content, owner, collaborators,
             plan_start, plan_end, deliverable, status, progress)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)''',
            (pid, code, parent_id, name,
             str(row[2] or '').strip(), str(row[3] or '').strip(), str(row[4] or '').strip(),
             str(row[5] or '').strip(), str(row[6] or '').strip(), str(row[7] or '').strip(),
             '未开始', 0))
        count += 1
    db.commit()
    flash(f'成功导入 {count} 条WBS任务', 'success')
    return redirect(url_for('ext.wbs_project_detail', pid=pid))
