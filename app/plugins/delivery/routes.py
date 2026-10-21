# -*- coding: utf-8 -*-
"""交付管理插件路由（从 app.py 迁移，v10.4.0）"""
from flask import Blueprint, render_template, request, redirect, url_for, g, flash, jsonify
from datetime import datetime, date
import io, os, json, random
from plugin_system import log_operation
from plugins.base import make_blueprint

blueprint = make_blueprint('delivery', __name__)

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

@blueprint.route('/delivery')
def delivery_plan_list():
    """交付管理·交付跟踪"""
    db = get_db()
    # 检查 delivery_plans 表是否存在
    table_exists = db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='delivery_plans'").fetchone()
    plans = []
    stats = {'total': 0, 'undone': 0, 'done': 0}
    if table_exists:
        plans = db.execute('SELECT * FROM delivery_plans ORDER BY created_at DESC LIMIT 100').fetchall()
        stats['total'] = len(plans)
        stats['undone'] = sum(1 for p in plans if p['status'] not in ('已完成', '已交付'))
        stats['done'] = stats['total'] - stats['undone']
    return render_template('delivery_plan_list.html', plans=plans, stats=stats, page=1, total_pages=1, search='', current_status='')

@blueprint.route('/delivery/receipt/add', methods=['GET', 'POST'])
def receipt_add():
    from app import validate_positive_int, validation_error
    db = get_db()
    products = db.execute('SELECT id, name, model FROM products').fetchall()
    orders = db.execute("SELECT id, order_no FROM production_orders WHERE status='已完成'").fetchall()
    if request.method == 'POST':
        # 输入校验
        qty, err = validate_positive_int(request.form.get('quantity', 0), '入库数量')
        if err:
            return validation_error(err)

        receipt_no = f"WR{datetime.now().strftime('%Y%m%d%H%M%S')}{random.randint(100,999)}"
        db.execute('''INSERT INTO warehouse_receipts (receipt_no, production_order_id, product_id, quantity,
                      quality_status, approver, status)
                      VALUES (?,?,?,?,?,?,?)''',
                   (receipt_no, request.form.get('production_order_id') or None,
                    request.form.get('product_id') or None,
                    qty, request.form.get('quality_status','合格'),
                    request.form.get('approver',''), '待审批'))
        db.commit()
        log_operation('验证收益', '入库申请', f"入库单 {receipt_no} 已创建")
        return redirect(url_for('delivery.delivery_dashboard', tab='receipts'))
    return render_template('receipt_form.html', products=products, orders=orders)

@blueprint.route('/delivery/receipt/<int:rid>/approve', methods=['POST'])
def receipt_approve(rid):
    from app import is_admin
    if not is_admin():
        flash('仅管理员可审批入库', 'error')
        return redirect(url_for('delivery.delivery_dashboard', tab='receipts'))
    db = get_db()
    receipt = db.execute('SELECT * FROM warehouse_receipts WHERE id=?', (rid,)).fetchone()
    if receipt:
        db.execute('''UPDATE warehouse_receipts SET status=?, approved_at=datetime('now','localtime') WHERE id=?''',
                   ('已入库', rid))
        # 更新生产订单状态
        if receipt['production_order_id']:
            db.execute("UPDATE production_orders SET status='已入库' WHERE id=?", (receipt['production_order_id'],))
        # 打通入库与库存：入库审批通过后自动增加成品库存
        if receipt['product_id'] and receipt['quantity'] > 0:
            product = db.execute('SELECT name, model FROM products WHERE id=?', (receipt['product_id'],)).fetchone()
            if product:
                product_name = f"{product['name']}（{product['model']}）" if product['model'] else product['name']
                # 检查是否已有该成品的库存记录
                inv = db.execute('SELECT id, quantity FROM inventory WHERE product_id=? AND is_product=1', (receipt['product_id'],)).fetchone()
                if inv:
                    db.execute('UPDATE inventory SET quantity=quantity+?, updated_at=datetime("now","localtime") WHERE id=?',
                               (receipt['quantity'], inv['id']))
                else:
                    db.execute('''INSERT INTO inventory (material_name, product_id, is_product, is_key_material, location, quantity, unit, status, remark)
                                  VALUES (?,?,?,0,'成品库',?, '套','正常',?)''',
                               (product_name, receipt['product_id'], 1, receipt['quantity'], f"入库单{receipt['receipt_no']}入库"))
        db.commit()
        log_operation('验证收益', '入库审批', f"入库单 {receipt['receipt_no']} 已审批入库，成品库存已自动增加")
    return redirect(url_for('delivery.delivery_dashboard', tab='receipts'))

@blueprint.route('/delivery/deliver/add', methods=['GET', 'POST'])
def deliver_add():
    from app import is_admin, validate_positive_int, validation_error
    db = get_db()
    products = db.execute('SELECT id, name, model FROM products').fetchall()
    if request.method == 'POST':
        # 输入校验
        qty, err = validate_positive_int(request.form.get('quantity', 0), '交付数量')
        if err:
            return validation_error(err)
        if not request.form.get('market_team', '').strip():
            return validation_error('市场团队不能为空')

        # 打通交付与库存：交付前校验成品库存是否足够
        product_id = request.form.get('product_id')
        if product_id:
            product_id = int(product_id)
            inv = db.execute('SELECT quantity FROM inventory WHERE product_id=? AND is_product=1', (product_id,)).fetchone()
            current_qty = inv['quantity'] if inv else 0
            if current_qty < qty:
                product = db.execute('SELECT name FROM products WHERE id=?', (product_id,)).fetchone()
                product_name = product['name'] if product else '该产品'
                return validation_error(f'成品库存不足：{product_name}当前库存{current_qty}套，需交付{qty}套，请先入库')

        delivery_no = f"DL{datetime.now().strftime('%Y%m%d%H%M%S')}{random.randint(100,999)}"
        db.execute('''INSERT INTO deliveries (delivery_no, product_id, market_team, quantity, customer,
                      purpose, delivery_date, status, feedback)
                      VALUES (?,?,?,?,?,?,?,?,?)''',
                   (delivery_no, product_id,
                    request.form.get('market_team','').strip(), qty,
                    request.form.get('customer',''), request.form.get('purpose',''),
                    request.form.get('delivery_date', date.today().isoformat()),
                    '已交付', request.form.get('feedback','')))
        # 打通交付与库存：交付后自动扣减成品库存
        if product_id and qty > 0:
            db.execute('UPDATE inventory SET quantity=quantity-?, updated_at=datetime("now","localtime") WHERE product_id=? AND is_product=1 AND quantity>=?',
                       (qty, product_id, qty))
        db.commit()
        log_operation('验证收益', '交付', f"交付单 {delivery_no} 客户: {request.form.get('customer','')}，成品库存已自动扣减")
        return redirect(url_for('delivery.delivery_dashboard', tab='deliveries'))
    return render_template('deliver_form.html', products=products)

# ============================================================
# 系统日志
# ============================================================


@blueprint.route('/delivery/plan/add', methods=['GET', 'POST'])
def delivery_plan_add():
    """新增交付计划（空壳，待完善）"""
    flash('交付计划功能待完善', 'info')
    return redirect(url_for('delivery.delivery_plan_list'))

@blueprint.route('/delivery/plan/<int:plan_id>/edit', methods=['GET', 'POST'])
def delivery_plan_edit(plan_id):
    """编辑交付计划（空壳，待完善）"""
    flash('交付计划功能待完善', 'info')
    return redirect(url_for('delivery.delivery_plan_list'))

@blueprint.route('/delivery/plan/<int:plan_id>/delete', methods=['POST'])
def delivery_plan_delete(plan_id):
    """删除交付计划（空壳，待完善）"""
    flash('交付计划功能待完善', 'info')
    return redirect(url_for('delivery.delivery_plan_list'))
