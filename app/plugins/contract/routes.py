# -*- coding: utf-8 -*-
"""合同管理插件路由（从 ext_routes.py 迁移，v10.4.0 插件包化）"""
from flask import Blueprint, render_template, request, redirect, url_for, g, flash
from datetime import datetime
import random
from plugin_system import log_operation
from plugins.base import make_blueprint
from . import CONTRACT_TYPES_PROD, CONTRACT_TYPES_USER

blueprint = make_blueprint('contract', __name__)


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


@blueprint.route('/contracts')
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
        params.extend([f'%{search}%'] * 2)
    where_sql = ' WHERE ' + ' AND '.join(where)
    total = db.execute(f'SELECT COUNT(*) FROM contracts c{where_sql}', params).fetchone()[0]
    contracts = db.execute(f'''SELECT c.*, p.name as product_name FROM contracts c
        LEFT JOIN products p ON c.product_id=p.id
        {where_sql} ORDER BY c.created_at DESC LIMIT ? OFFSET ?''',
        params + [per_page, offset]).fetchall()
    total_pages = (total + per_page - 1) // per_page
    return render_template('contract_list.html', contracts=contracts, current_status=status,
                           search=search, page=page, total_pages=total_pages, total=total)


@blueprint.route('/contracts/add', methods=['GET', 'POST'])
def contract_add():
    """新建合同：type=user 为交付板块用户合同，否则为生产/物料合同"""
    db = get_db()
    ctype = request.args.get('type', 'prod')
    is_user = (ctype == 'user')
    contract_types = CONTRACT_TYPES_USER if is_user else CONTRACT_TYPES_PROD
    back_url = url_for('contract.contract_list')
    if request.method == 'POST':
        contract_no = request.form.get('contract_no', f'CT{datetime.now().strftime("%Y%m%d%H%M%S")}{random.randint(100, 999)}')
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
            (contract_no, contract_type, supplier, product_id, amount, sign_date, delivery_date,
             '执行中', owner, progress, '未付款', remark))
        db.commit()
        log_operation('合同', '新增', f'{contract_no}: {supplier} {amount}元', owner)
        return redirect(back_url)
    products = db.execute('SELECT id, name, model FROM products').fetchall()
    return render_template('contract_form.html', products=products, contract=None,
                           contract_types=contract_types, back_url=back_url, ctype=ctype)


@blueprint.route('/contracts/<int:cid>/edit', methods=['GET', 'POST'])
def contract_edit(cid):
    """编辑合同（所有字段可修改）；type=user 为交付板块用户合同"""
    db = get_db()
    contract = db.execute('SELECT * FROM contracts WHERE id=?', (cid,)).fetchone()
    if not contract:
        return '合同不存在', 404
    ctype = request.args.get('type', '')
    if not ctype and contract['contract_type'] == '用户合同':
        ctype = 'user'
    is_user = (ctype == 'user')
    contract_types = CONTRACT_TYPES_USER if is_user else CONTRACT_TYPES_PROD
    back_url = url_for('contract.contract_list')
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
        if paid_amount >= amount and amount > 0:
            payment_status = '已付款'
        elif paid_amount > 0:
            payment_status = '部分付款'
        else:
            payment_status = '未付款'
        db.execute('''UPDATE contracts SET contract_type=?, supplier=?, product_id=?, amount=?, paid_amount=?,
            sign_date=?, delivery_date=?, status=?, owner=?, progress=?, payment_status=?, remark=? WHERE id=?''',
            (contract_type, supplier, product_id, amount, paid_amount, sign_date, delivery_date,
             status, owner, progress, payment_status, remark, cid))
        db.commit()
        log_operation('合同', '编辑', f'{contract["contract_no"]}: 更新合同信息，已付{paid_amount}元')
        return redirect(url_for('contract.contract_edit', cid=cid, type=ctype))
    products = db.execute('SELECT id, name, model FROM products').fetchall()
    payments = db.execute('SELECT * FROM contract_payments WHERE contract_id=? ORDER BY plan_date', (cid,)).fetchall()
    return render_template('contract_form.html', products=products, contract=contract,
                           payments=payments, contract_types=contract_types, back_url=back_url, ctype=ctype)


@blueprint.route('/contracts/<int:cid>/payments/add', methods=['POST'])
def contract_payment_add(cid):
    """添加付款/收款节点"""
    db = get_db()
    contract = db.execute('SELECT * FROM contracts WHERE id=?', (cid,)).fetchone()
    if not contract:
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
        if status == '已付款':
            db.execute('UPDATE contracts SET paid_amount = COALESCE(paid_amount, 0) + ? WHERE id=?', (amount, cid))
        db.commit()
        log_operation('合同', '添加付款节点', f'{contract["contract_no"]}: {node_name} {amount}元')
    return redirect(url_for('contract.contract_edit', cid=cid))


@blueprint.route('/contracts/<int:cid>/payments/<int:pid>/update', methods=['POST'])
def contract_payment_update(cid, pid):
    """更新付款/收款节点状态"""
    db = get_db()
    payment = db.execute('SELECT * FROM contract_payments WHERE id=? AND contract_id=?', (pid, cid)).fetchone()
    if not payment:
        return '付款节点不存在', 404
    new_status = request.form.get('status', payment['status'])
    actual_date = request.form.get('actual_date', '')
    old_status = payment['status']
    db.execute('UPDATE contract_payments SET status=?, actual_date=? WHERE id=?',
               (new_status, actual_date or payment['actual_date'], pid))
    if old_status != '已付款' and new_status == '已付款':
        db.execute('UPDATE contracts SET paid_amount = COALESCE(paid_amount, 0) + ? WHERE id=?', (payment['amount'], cid))
    elif old_status == '已付款' and new_status != '已付款':
        db.execute('UPDATE contracts SET paid_amount = MAX(0, COALESCE(paid_amount, 0) - ?) WHERE id=?', (payment['amount'], cid))
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
    return redirect(url_for('contract.contract_edit', cid=cid))


@blueprint.route('/contracts/<int:cid>/payments/<int:pid>/delete', methods=['POST'])
def contract_payment_delete(cid, pid):
    """删除付款/收款节点"""
    from app import is_admin
    if not is_admin():
        flash('仅管理员可删除付款节点', 'error')
        return redirect(url_for('contract.contract_edit', cid=cid))
    db = get_db()
    payment = db.execute('SELECT * FROM contract_payments WHERE id=? AND contract_id=?', (pid, cid)).fetchone()
    if not payment:
        return '付款节点不存在', 404
    if payment['status'] == '已付款':
        db.execute('UPDATE contracts SET paid_amount = MAX(0, COALESCE(paid_amount, 0) - ?) WHERE id=?', (payment['amount'], cid))
    db.execute('DELETE FROM contract_payments WHERE id=?', (pid,))
    db.commit()
    log_operation('合同', '删除付款节点', f'节点{pid}: {payment["node_name"]}')
    return redirect(url_for('contract.contract_edit', cid=cid))


@blueprint.route('/contracts/<int:cid>/update-status', methods=['POST'])
def contract_update_status(cid):
    """快速更新合同状态和付款状态"""
    from app import submit_status_change
    db = get_db()
    contract = db.execute('SELECT * FROM contracts WHERE id=?', (cid,)).fetchone()
    if not contract:
        return '合同不存在', 404
    status = request.form.get('status', contract['status'])
    payment_status = request.form.get('payment_status', contract['payment_status'])
    progress = request.form.get('progress', contract['progress'])
    operator = request.form.get('operator', '')
    if status != contract['status']:
        perm, aid = submit_status_change('contract', 'contract', cid, status)
        if perm == 'pending':
            flash('状态变更申请已提交，等待管理员审批', 'info')
            return redirect(request.referrer or url_for('contract.contract_list'))
    db.execute('UPDATE contracts SET status=?, payment_status=?, progress=? WHERE id=?',
               (status, payment_status, progress, cid))
    db.commit()
    log_operation('合同', '状态更新', f'{contract["contract_no"]}: 状态→{status}, 付款→{payment_status}', operator)
    return redirect(request.referrer or url_for('contract.contract_list'))
