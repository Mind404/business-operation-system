# -*- coding: utf-8 -*-
"""bugs 插件路由（从 app.py 迁移，v10.4.0）"""
from flask import Blueprint, render_template, request, redirect, url_for, g, flash, jsonify, send_file, session
from datetime import datetime
import io, os, json, random
from plugin_system import log_operation
from plugins.base import make_blueprint


blueprint = make_blueprint('bugs', __name__)

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

@blueprint.route('/bugs')
def bug_list():
    """Bug列表"""
    db = get_db()
    tab = request.args.get('tab', 'all')
    query = '''SELECT b.*, p.name as product_name, sv.version_no
               FROM bug_records b
               LEFT JOIN products p ON b.product_id=p.id
               LEFT JOIN software_versions sv ON b.version_id=sv.id'''
    if tab == 'open':
        query += " WHERE b.status NOT IN ('已关闭','已取消')"
    elif tab == 'quality':
        query += " WHERE b.is_quality_issue=1"
    query += " ORDER BY b.created_at DESC"
    bugs = db.execute(query).fetchall()
    products = db.execute('SELECT id, name FROM products').fetchall()
    return render_template('bug_list.html', bugs=bugs, tab=tab, products=products)

@blueprint.route('/bugs/add', methods=['GET', 'POST'])
def bug_add():
    """新增Bug"""
    db = get_db()
    products = db.execute('SELECT id, name FROM products').fetchall()
    versions = db.execute('SELECT id, version_no, product_id FROM software_versions').fetchall()
    if request.method == 'POST':
        bug_no = f"BUG{datetime.now().strftime('%Y%m%d%H%M%S')}{random.randint(100,999)}"
        cursor = db.execute('''INSERT INTO bug_records (bug_no, product_id, version_id, title, description,
                              bug_type, severity, priority, status, reporter, assignee, discovered_at)
                              VALUES (?,?,?,?,?,?,?,?,?,?,?,?)''',
                   (bug_no, request.form.get('product_id') or None, request.form.get('version_id') or None,
                    request.form.get('title',''), request.form.get('description',''),
                    request.form.get('bug_type','功能Bug'), request.form.get('severity','一般'),
                    request.form.get('priority','中'), '新建',
                    request.form.get('reporter',''), request.form.get('assignee',''),
                    request.form.get('discovered_at','') or datetime.now().strftime('%Y-%m-%d')))
        bid = cursor.lastrowid
        db.execute('''INSERT INTO bug_workflow (bug_id, action, from_status, to_status, operator, comment)
                      VALUES (?,?,?,?,?,?)''', (bid, '创建', None, '新建', request.form.get('reporter',''), 'Bug登记'))
        db.commit()
        log_operation('Bug管理', '新增', f'新增Bug {bug_no}: {request.form.get("title")}')
        return redirect(url_for('bugs.bug_detail', bid=bid))
    return render_template('bug_form.html', products=products, versions=versions)

@blueprint.route('/bugs/<int:bid>')
def bug_detail(bid):
    from app import validation_error
    """Bug详情（含流转记录）"""
    db = get_db()
    bug = db.execute('''SELECT b.*, p.name as product_name, sv.version_no
                        FROM bug_records b
                        LEFT JOIN products p ON b.product_id=p.id
                        LEFT JOIN software_versions sv ON b.version_id=sv.id
                        WHERE b.id=?''', (bid,)).fetchone()
    if not bug:
        return validation_error('Bug不存在')
    workflow = db.execute('SELECT * FROM bug_workflow WHERE bug_id=? ORDER BY created_at', (bid,)).fetchall()
    return render_template('bug_detail.html', bug=bug, workflow=workflow)

@blueprint.route('/bugs/<int:bid>/update', methods=['POST'])
def bug_update(bid):
    from app import validation_error, submit_status_change
    """Bug状态流转（完整生命周期：新建→已分配→处理中→已修复→验证中→已关闭/重新打开，可取消）"""
    new_status = request.form.get('status', '')
    valid = ['新建', '已分配', '处理中', '已修复', '验证中', '已关闭', '重新打开', '已取消']
    if new_status not in valid:
        return validation_error('无效的状态')
    db = get_db()
    # v10.3.2 权限统一：普通用户提交审批（携带完整变更字段）
    payload_extra = {}
    for f in ('assignee', 'fix_solution', 'test_result', 'close_reason', 'root_cause', 'corrective_action', 'preventive_action'):
        v = request.form.get(f)
        if v:
            payload_extra[f] = v
    perm, aid = submit_status_change('bug', 'bug', bid, new_status, payload_extra)
    if perm == 'pending':
        flash('状态变更申请已提交，等待管理员审批', 'info')
        return redirect(url_for('bugs.bug_detail', bid=bid))
    bug = db.execute('SELECT status FROM bug_records WHERE id=?', (bid,)).fetchone()
    old_status = bug['status'] if bug else None
    update_fields = ['status=?']
    params = [new_status]
    # 分配负责人时更新assignee
    if new_status == '已分配' and request.form.get('assignee'):
        update_fields.append('assignee=?')
        params.append(request.form.get('assignee'))
    # 提交修复时记录修复方案
    if new_status == '已修复' and request.form.get('fix_solution'):
        update_fields.append('fix_solution=?')
        params.append(request.form.get('fix_solution'))
    # 开始验证时记录验证结果（可选）
    if new_status == '验证中' and request.form.get('test_result'):
        update_fields.append('test_result=?')
        params.append(request.form.get('test_result'))
    # 关闭时记录关闭原因、根因分析、纠正措施、预防措施
    if new_status == '已关闭':
        update_fields.append('closed_at=datetime("now","localtime")')
        if request.form.get('close_reason'):
            update_fields.append('close_reason=?')
            params.append(request.form.get('close_reason'))
        if request.form.get('root_cause'):
            update_fields.append('root_cause=?')
            params.append(request.form.get('root_cause'))
        if request.form.get('corrective_action'):
            update_fields.append('corrective_action=?')
            params.append(request.form.get('corrective_action'))
        if request.form.get('prevention_measure'):
            update_fields.append('prevention_measure=?')
            params.append(request.form.get('prevention_measure'))
    # 重新打开时清除关闭时间
    if new_status == '重新打开':
        update_fields.append('closed_at=NULL')
        update_fields.append('close_reason=NULL')
    # 取消时记录关闭时间和关闭原因
    if new_status == '已取消':
        update_fields.append('closed_at=datetime("now","localtime")')
        if request.form.get('close_reason'):
            update_fields.append('close_reason=?')
            params.append(request.form.get('close_reason'))
    params.append(bid)
    db.execute(f'UPDATE bug_records SET {",".join(update_fields)} WHERE id=?', params)
    # 记录流转历史（含修复方案、验证结果、关闭原因）
    db.execute('''INSERT INTO bug_workflow (bug_id, action, from_status, to_status, operator, comment, fix_solution, test_result, close_reason)
                  VALUES (?,?,?,?,?,?,?,?,?)''',
               (bid, '状态变更', old_status, new_status,
                request.form.get('operator','系统'), request.form.get('comment',''),
                request.form.get('fix_solution'), request.form.get('test_result'),
                request.form.get('close_reason')))
    db.commit()
    log_operation('Bug管理', '流转', f'BugID {bid} 状态从 {old_status} 变更为 {new_status}')
    return redirect(url_for('bugs.bug_detail', bid=bid))

@blueprint.route('/bugs/<int:bid>/escalate', methods=['POST'])
def bug_escalate(bid):
    from app import submit_status_change
    """Bug升级为质量问题"""
    db = get_db()
    db.execute('UPDATE bug_records SET is_quality_issue=1 WHERE id=?', (bid,))
    db.execute('''INSERT INTO bug_workflow (bug_id, action, operator, comment)
                  VALUES (?,?,?,?)''', (bid, '升级质量问题', request.form.get('operator','系统'),
                request.form.get('reason','严重Bug，升级为质量问题进行根因分析')))
    db.commit()
    log_operation('Bug管理', '升级', f'BugID {bid} 升级为质量问题')
    return redirect(url_for('bugs.bug_detail', bid=bid))

# ============================================================
# 反向规划模块3：生产排程与工序管理（含物料需求计划）
# ============================================================
