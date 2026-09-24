# -*- coding: utf-8 -*-
"""v8.8.0 交付管理简化：重写交付路由（patch 脚本，可重复执行）"""
p = 'ext_routes.py'
src = open(p, encoding='utf-8').read()

start = src.find("@ext_bp.route('/delivery/plans')")
end = src.find("# ============================================================\n# 交付服务组A：收入确认")
assert start > 0 and end > start, f'定位失败 start={start} end={end}'

new_block = """@ext_bp.route('/delivery/plans')
def delivery_plan_list():
    \"\"\"交付管理·交付跟踪：一页看清 需求→数量→生产→入库→可交付→是否已交付\"\"\"
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
        product_id = request.form.get('product_id') or req['product_id']
        quantity = int(request.form.get('quantity') or req['quantity'] or 1)
        planned_date = request.form.get('planned_date') or req['delivery_date'] or ''
        owner = request.form.get('owner', 'delivery_a')
        remark = request.form.get('remark', '')
        plan_no = 'DP{}'.format(datetime.now().strftime('%Y%m%d%H%M%S'))
        db.execute('''INSERT INTO delivery_plans (plan_no, customer, product_id, quantity, planned_date,
            status, owner, remark, requirement_id) VALUES (?,?,?,?,?,?,?,?,?)''',
            (plan_no, customer, product_id, quantity, planned_date, '计划中', owner, remark, requirement_id))
        db.commit()
        log_operation('交付管理', '新增交付', '{}: {} {}套'.format(plan_no, customer, quantity), owner)
        flash('交付计划已创建（关联需求：{}）'.format(req['title']), 'success')
        return redirect(url_for('ext.delivery_plan_list'))
    products = db.execute('SELECT id, name, model FROM products').fetchall()
    requirements = db.execute('''SELECT id, title, customer, quantity, delivery_date, product_id, status
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


"""

src = src[:start] + new_block + src[end:]
open(p, 'w', encoding='utf-8').write(src)
print('交付路由重写完成 ✓')
