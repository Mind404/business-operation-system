# -*- coding: utf-8 -*-
"""测试管理插件路由（从 ext_routes.py 迁移，v10.4.0 插件包化）"""
from flask import Blueprint, render_template, request, redirect, url_for, g
from datetime import datetime
import random
from plugin_system import log_operation
from plugins.base import make_blueprint

blueprint = make_blueprint('test', __name__)


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
# 测试用例库
# ============================================================
@blueprint.route('/test/cases')
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
        params.extend([f'%{search}%'] * 3)
    where_sql = (' WHERE ' + ' AND '.join(where)) if where else ''
    total = db.execute(f'SELECT COUNT(*) FROM test_cases tc{where_sql}', params).fetchone()[0]
    cases = db.execute(f'''SELECT tc.*, p.name as product_name FROM test_cases tc
        LEFT JOIN products p ON tc.product_id=p.id
        {where_sql} ORDER BY tc.created_at DESC LIMIT ? OFFSET ?''',
        params + [per_page, offset]).fetchall()
    products = db.execute('SELECT id, name, model FROM products').fetchall()
    total_pages = (total + per_page - 1) // per_page
    return render_template('test_case_list.html', cases=cases, products=products,
                           search=search, current_product=product_id, page=page,
                           total_pages=total_pages, total=total)


@blueprint.route('/test/cases/add', methods=['GET', 'POST'])
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
            case_no = f'TC{datetime.now().strftime("%Y%m%d%H%M%S")}{random.randint(100, 999)}'
        db.execute('''INSERT INTO test_cases (case_no, product_id, title, module, precondition,
            test_steps, expected_result, priority, case_type, creator)
            VALUES (?,?,?,?,?,?,?,?,?,?)''',
            (case_no, product_id, title, module, precondition, test_steps,
             expected_result, priority, case_type, 'tester'))
        db.commit()
        log_operation('测试用例', '新增', f'新增用例 {case_no}: {title}', 'tester')
        return redirect(url_for('test.test_case_list'))
    products = db.execute('SELECT id, name, model FROM products').fetchall()
    return render_template('test_case_form.html', products=products)


@blueprint.route('/test/cases/<int:cid>')
def test_case_detail(cid):
    db = get_db()
    case = db.execute('''SELECT tc.*, p.name as product_name FROM test_cases tc
        LEFT JOIN products p ON tc.product_id=p.id WHERE tc.id=?''', (cid,)).fetchone()
    return render_template('test_case_detail.html', case=case)
