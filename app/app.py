# -*- coding: utf-8 -*-
"""
AI驱动的业务高效运转系统 - Flask Web应用
适用于内网Windows部署，SQLite轻量数据库
"""
import os
import sqlite3
import random
from datetime import datetime, date, timedelta
from flask import Flask, render_template, request, redirect, url_for, jsonify, g, session, flash, send_file
from werkzeug.utils import secure_filename
import excel_utils

app = Flask(__name__)
app.config['SECRET_KEY'] = 'business-system-2024'
app.config['DATABASE'] = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'business.db')

# 自定义tojson过滤器（兼容旧版Werkzeug的ensure_ascii参数）
import json
@app.template_filter('tojson')
def tojson_filter(value, indent=None, ensure_ascii=False):
    """自定义JSON序列化过滤器，支持ensure_ascii参数"""
    return json.dumps(value, indent=indent, ensure_ascii=ensure_ascii, default=str)


@app.template_filter('from_json')
def from_json_filter(value):
    """JSON字符串转对象"""
    if not value:
        return []
    try:
        return json.loads(value)
    except Exception:
        return []

# 注册扩展模块路由蓝图
from ext_routes import ext_bp
app.register_blueprint(ext_bp)

# v10.4.0 插件包化：自动加载 plugins/ 下的独立插件包
from plugins import load_plugins
load_plugins(app)

# 插件化基底：建表 + 注册插件 + 路由守卫
import plugin_system
from plugin_system import log_operation
with app.app_context():
    plugin_system.init_plugin_tables()
plugin_system.register_plugin_guard(app)

# v11.0 平台内置 AI 辅助服务（本地小模型，只读，不写业务库）
from ai_service import ai_bp
app.register_blueprint(ai_bp, url_prefix='/api/ai')

# ============================================================
# 用户认证
# ============================================================
@app.before_request
def require_login():
    """所有页面需要登录，除了登录页和静态资源"""
    if request.endpoint in ('login', 'static'):
        return None
    if 'user_id' not in session:
        return redirect(url_for('login', next=request.path))
    # 权限校验：检查当前角色是否有权限访问该模块
    endpoint = request.endpoint or ''
    # 工作台和登录相关页面所有角色都可以访问
    if endpoint in ('index', 'logout'):
        return None
    # 【权限放开】v8.5.0 起取消全部模块权限与只读限制，所有登录用户均可访问与编辑
    # 保留登录校验；gantt/模块权限/只读拦截全部放行
    return None

@app.context_processor
def inject_user():
    """所有模板可访问当前用户 + 插件导航上下文"""
    ctx = {'current_user': session.get('user_name'), 'current_role': session.get('user_role')}
    try:
        ctx.update(plugin_system.inject_nav_context())
    except Exception as e:
        import traceback
        print('=== inject_nav_context error ===')
        traceback.print_exc()
        ctx.update({'nav_core': [], 'nav_biz': [], 'nav_all_plugins': [], 'user_subscribed': set()})
    return ctx

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        next_url = request.form.get('next', '/')
        db = get_db()
        user = db.execute('SELECT * FROM users WHERE username=? AND password=? AND is_active=1',
                         (username, password)).fetchone()
        if user:
            session['user_id'] = user['id']
            session['user_name'] = user['display_name'] or user['username']
            session['user_role'] = user['role']
            session['username'] = user['username']
            log_operation('系统', '登录', f'{user["username"]} 登录系统', user['username'])
            return redirect(next_url if next_url else '/')
        else:
            error = '用户名或密码错误'
            return render_template('login.html', error=error, next=next_url)
    return render_template('login.html', next=request.args.get('next', '/'))

@app.route('/logout')
def logout():
    username = session.get('username', 'unknown')
    log_operation('系统', '登出', f'{username} 退出系统', username)
    session.clear()
    return redirect(url_for('login'))

# ============================================================
# v10.5.0 用户管理中心（仅管理员）
# ============================================================
def _require_admin():
    """管理员校验：非管理员返回 False"""
    return session.get('user_role') == 'admin'

@app.route('/admin/users')
def admin_users():
    """用户管理：用户列表（仅管理员）"""
    if not _require_admin():
        flash('无权限访问用户管理', 'error')
        return redirect(url_for('index'))
    db = get_db()
    users = db.execute('SELECT * FROM users ORDER BY id').fetchall()
    return render_template('user_manage.html', users=users)

@app.route('/admin/users/create', methods=['POST'])
def admin_user_create():
    """创建用户（仅管理员）"""
    if not _require_admin():
        flash('无权限', 'error')
        return redirect(url_for('index'))
    username = request.form.get('username', '').strip()
    password = request.form.get('password', '').strip()
    display_name = request.form.get('display_name', '').strip() or username
    role = request.form.get('role', 'user')
    department = request.form.get('department', '').strip()
    if not username or not password:
        flash('用户名和密码不能为空', 'error')
        return redirect(url_for('admin_users'))
    if len(password) < 4:
        flash('密码至少4位', 'error')
        return redirect(url_for('admin_users'))
    if role not in ('admin', 'user'):
        role = 'user'
    db = get_db()
    exists = db.execute('SELECT 1 FROM users WHERE username=?', (username,)).fetchone()
    if exists:
        flash(f'用户名 {username} 已存在', 'error')
        return redirect(url_for('admin_users'))
    db.execute('INSERT INTO users (username, password, display_name, role, is_active, created_at, department) '
               'VALUES (?,?,?,?,1,?,?)',
               (username, password, display_name, role,
                datetime.now().strftime('%Y-%m-%d %H:%M:%S'), department))
    db.commit()
    log_operation('用户管理', '创建用户', f'创建账号 {username}（{display_name}，角色：{role}）', session.get('username'))
    flash(f'用户 {display_name} 创建成功', 'success')
    return redirect(url_for('admin_users'))

@app.route('/admin/users/<int:uid>/update', methods=['POST'])
def admin_user_update(uid):
    """编辑用户：显示名/角色/部门/启用状态（仅管理员）"""
    if not _require_admin():
        flash('无权限', 'error')
        return redirect(url_for('index'))
    db = get_db()
    user = db.execute('SELECT * FROM users WHERE id=?', (uid,)).fetchone()
    if not user:
        flash('用户不存在', 'error')
        return redirect(url_for('admin_users'))
    display_name = request.form.get('display_name', '').strip() or user['username']
    role = request.form.get('role', user['role'])
    department = request.form.get('department', '').strip()
    is_active = 1 if request.form.get('is_active') == '1' else 0
    if role not in ('admin', 'user'):
        role = user['role']
    # 不允许停用最后一个管理员
    if user['role'] == 'admin' and is_active == 0:
        admins = db.execute("SELECT COUNT(*) AS c FROM users WHERE role='admin' AND is_active=1").fetchone()['c']
        if admins <= 1:
            flash('不能停用最后一个管理员', 'error')
            return redirect(url_for('admin_users'))
    db.execute('UPDATE users SET display_name=?, role=?, department=?, is_active=? WHERE id=?',
               (display_name, role, department, is_active, uid))
    db.commit()
    log_operation('用户管理', '编辑用户', f'编辑用户#{uid} {user["username"]} → {display_name}（角色：{role}）', session.get('username'))
    flash(f'用户 {display_name} 已更新', 'success')
    return redirect(url_for('admin_users'))

@app.route('/admin/users/<int:uid>/toggle', methods=['POST'])
def admin_user_toggle(uid):
    """启用/停用用户（仅管理员）"""
    if not _require_admin():
        flash('无权限', 'error')
        return redirect(url_for('index'))
    db = get_db()
    user = db.execute('SELECT * FROM users WHERE id=?', (uid,)).fetchone()
    if not user:
        flash('用户不存在', 'error')
        return redirect(url_for('admin_users'))
    new_state = 0 if user['is_active'] else 1
    if user['role'] == 'admin' and new_state == 0:
        admins = db.execute("SELECT COUNT(*) AS c FROM users WHERE role='admin' AND is_active=1").fetchone()['c']
        if admins <= 1:
            flash('不能停用最后一个管理员', 'error')
            return redirect(url_for('admin_users'))
    db.execute('UPDATE users SET is_active=? WHERE id=?', (new_state, uid))
    db.commit()
    log_operation('用户管理', '启停用户', f'{"停用" if new_state == 0 else "启用"}账号 {user["username"]}', session.get('username'))
    flash(f'账号 {user["username"]} 已{"停用" if new_state == 0 else "启用"}', 'success')
    return redirect(url_for('admin_users'))

@app.route('/admin/users/<int:uid>/reset_pwd', methods=['POST'])
def admin_user_reset_pwd(uid):
    """管理员重置用户密码（仅管理员）"""
    if not _require_admin():
        flash('无权限', 'error')
        return redirect(url_for('index'))
    new_pwd = request.form.get('new_password', '').strip()
    if len(new_pwd) < 4:
        flash('新密码至少4位', 'error')
        return redirect(url_for('admin_users'))
    db = get_db()
    user = db.execute('SELECT * FROM users WHERE id=?', (uid,)).fetchone()
    if not user:
        flash('用户不存在', 'error')
        return redirect(url_for('admin_users'))
    db.execute('UPDATE users SET password=? WHERE id=?', (new_pwd, uid))
    db.commit()
    log_operation('用户管理', '重置密码', f'重置账号 {user["username"]} 的密码', session.get('username'))
    flash(f'账号 {user["username"]} 密码已重置', 'success')
    return redirect(url_for('admin_users'))

# ============================================================
# 插件中心：浏览与订阅业务插件
# ============================================================
@app.route('/plugins')
def plugin_center():
    db = get_db()
    user_id = session.get('user_id')
    subscribed = plugin_system.get_user_plugin_ids(user_id)
    plugins = db.execute('SELECT * FROM plugin_registry ORDER BY core DESC, id').fetchall()
    return render_template('plugin_center.html', plugins=plugins, subscribed=subscribed)

@app.route('/plugins/toggle/<plugin_id>', methods=['POST'])
def plugin_toggle(plugin_id):
    user_id = session.get('user_id')
    ok, msg = plugin_system.toggle_plugin(user_id, plugin_id)
    if not ok:
        flash(msg, 'error')
    else:
        flash(msg, 'success')
    return redirect(url_for('plugin_center'))


# ============================================================
# 个人中心（核心插件）：用户信息 + 我的订阅
# ============================================================
@app.route('/profile')
def profile():
    db = get_db()
    user_id = session.get('user_id')
    # v10.5.0：订阅机制下线，个人中心改为展示账号信息 + 常用功能（WBS）
    user_info = db.execute('SELECT * FROM users WHERE id=?', (user_id,)).fetchone()
    todo_count = 0
    try:
        todo_count = db.execute(
            "SELECT COUNT(*) FROM tasks WHERE assignee_id=? AND status NOT IN ('已完成','已关闭')",
            (user_id,)).fetchone()[0]
    except Exception:
        pass
    # 可用功能 = 核心 + WBS（所有登录用户）
    my_plugins = [p for p in plugin_system.PLUGINS if p['core'] or p['id'] == 'wbs']
    return render_template('profile.html',
                           user_info=user_info,
                           todo_count=todo_count,
                           my_plugins=my_plugins)

@app.route('/profile/change_password', methods=['POST'])
def profile_change_password():
    """普通用户自行修改密码：校验旧密码 + 新密码确认"""
    db = get_db()
    user_id = session.get('user_id')
    user = db.execute('SELECT * FROM users WHERE id=?', (user_id,)).fetchone()
    if not user:
        flash('用户不存在', 'error')
        return redirect(url_for('login'))
    old_pwd = request.form.get('old_password', '')
    new_pwd = request.form.get('new_password', '')
    confirm_pwd = request.form.get('confirm_password', '')
    if old_pwd != user['password']:
        flash('原密码错误', 'error')
        return redirect(url_for('profile'))
    if len(new_pwd) < 4:
        flash('新密码至少4位', 'error')
        return redirect(url_for('profile'))
    if new_pwd != confirm_pwd:
        flash('两次输入的新密码不一致', 'error')
        return redirect(url_for('profile'))
    if new_pwd == old_pwd:
        flash('新密码不能与原密码相同', 'error')
        return redirect(url_for('profile'))
    db.execute('UPDATE users SET password=? WHERE id=?', (new_pwd, user_id))
    db.commit()
    log_operation('个人中心', '修改密码', f'账号 {user["username"]} 修改了登录密码', user['username'])
    flash('密码修改成功，请牢记新密码', 'success')
    return redirect(url_for('profile'))

# ============================================================
# 角色权限管理
# ============================================================
# 权限定义：每个角色可以访问的模块（endpoint前缀）和操作权限
ROLE_PERMISSIONS = {
    'admin': {
        'name': '管理组',
        'modules': ['all'],  # 所有模块
        'readonly_modules': [],  # 没有只读模块
    },
    'product': {
        'name': '产品组',
        'modules': ['all'],  # 所有模块可见
        'readonly_modules': ['quality', 'delivery', 'ext.delivery_plan',
                            'ext.revenue', 'executive', 'ext.test_case',
                            'ext.test_execution', 'ext.ticket', 'ext.feedback',
                            'ext.device', 'ext.ota'],
    },
    'dev_test': {
        'name': '研发测试组',
        'modules': ['all'],  # 所有模块可见
        'readonly_modules': ['requirement', 'version', 'schedule', 'executive',
                            'quality', 'delivery', 'ext.delivery_plan', 'ext.revenue',
                            'ext.contract', 'ext.tech_spec', 'ext.ticket',
                            'ext.feedback', 'ext.device', 'ext.ota', 'planning'],
    },
    'quality': {
        'name': '质量组',
        'modules': ['all'],  # 所有模块可见
        'readonly_modules': ['requirement', 'version', 'production', 'delivery',
                            'executive', 'schedule', 'ext.contract', 'ext.tech_spec',
                            'ext.ticket', 'ext.feedback', 'ext.device', 'ext.ota',
                            'ext.delivery_plan', 'ext.revenue', 'planning',
                            'ext.resource'],
    },
    'delivery': {
        'name': '交付服务组',
        'modules': ['all'],  # 所有模块可见
        'readonly_modules': ['requirement', 'version', 'quality', 'executive',
                            'production', 'schedule', 'bug', 'ext.test_case',
                            'ext.test_execution', 'ext.resource', 'ext.tech_spec',
                            'planning'],
    },
}

def get_current_role():
    """获取当前用户角色"""
    return session.get('user_role', 'admin')

def _normalize_module(name):
    """规范化模块名，去掉ext.前缀"""
    if name.startswith('ext.'):
        return name[4:]
    return name

def has_module_access(module_prefix):
    """【权限放开】v8.5.0 起所有登录用户均可访问所有模块"""
    return True
    # 保留原逻辑供审计参考：
    role = get_current_role()
    perms = ROLE_PERMISSIONS.get(role, ROLE_PERMISSIONS['admin'])
    if 'all' in perms['modules']:
        return True
    # 规范化模块名，去掉ext.前缀
    module_prefix = _normalize_module(module_prefix)
    # 检查模块前缀是否匹配
    for allowed in perms['modules']:
        allowed = _normalize_module(allowed)
        if module_prefix.startswith(allowed) or allowed.startswith(module_prefix):
            return True
    return False

# ============================================================
# v11.0 平台级：审批流配置 + 审批中心 + 外部系统接入管理
# ============================================================
@app.route('/admin/approval-flows', methods=['GET'])
def admin_approval_flows():
    if not is_admin():
        flash('仅管理员可配置审批流', 'error')
        return redirect(url_for('index'))
    db = get_db()
    flows = db.execute('SELECT * FROM approval_flows ORDER BY plugin_id, record_type').fetchall()
    # 插件-记录类型清单（供配置）
    plugins_info = [('requirements', '需求收集', ['req_status', 'requirement']),
                    ('wbs', 'WBS计划管理', ['task_approve', 'project_approve']),
                    ('planning', '规划管理', ['software_version', 'dev_task']),
                    ('bug', 'Bug管理', ['bug']),
                    ('contract', '合同管理', ['contract']),
                    ('device', '设备管理', ['device'])]
    return render_template('approval_flows.html', flows=flows, plugins_info=plugins_info)


@app.route('/admin/approval-flows/save', methods=['POST'])
def admin_approval_flow_save():
    if not is_admin():
        return jsonify({'success': False, 'error': '仅管理员可配置'}), 403
    db = get_db()
    name = request.form.get('name', '').strip()
    plugin_id = request.form.get('plugin_id', '')
    record_type = request.form.get('record_type', '')
    step_roles = request.form.getlist('step_role')
    step_roles = [r for r in step_roles if r]
    if not name or not plugin_id or not record_type or not step_roles:
        return jsonify({'success': False, 'error': '名称/插件/记录类型/审批步骤均必填'}), 400
    # 角色枚举校验：平台角色体系为 admin(管理员)/user(普通用户)
    for r in step_roles:
        if r not in ('admin', 'user'):
            return jsonify({'success': False, 'error': f'审批步骤角色不合法：{r}（仅支持 admin/user）'}), 400
    steps = json.dumps([{'step': i + 1, 'role': r} for i, r in enumerate(step_roles)], ensure_ascii=False)
    db.execute('''INSERT INTO approval_flows (name, plugin_id, record_type, steps, enabled)
                  VALUES (?,?,?,?,1)''', (name, plugin_id, record_type, steps))
    db.commit()
    log_operation('平台', '审批流配置', f'新建审批流 {name}（{plugin_id}/{record_type}，{len(step_roles)}级）')
    return jsonify({'success': True})


@app.route('/admin/approval-flows/<int:fid>/toggle', methods=['POST'])
def admin_approval_flow_toggle(fid):
    if not is_admin():
        return jsonify({'success': False, 'error': '仅管理员可操作'}), 403
    db = get_db()
    row = db.execute('SELECT * FROM approval_flows WHERE id=?', (fid,)).fetchone()
    if not row:
        return jsonify({'success': False, 'error': '审批流不存在'}), 404
    new_enabled = 0 if row['enabled'] else 1
    db.execute('UPDATE approval_flows SET enabled=? WHERE id=?', (new_enabled, fid))
    db.commit()
    log_operation('平台', '审批流配置', f'{"启用" if new_enabled else "停用"}审批流 {row["name"]}')
    return jsonify({'success': True})


@app.route('/admin/approval-flows/<int:fid>/delete', methods=['POST'])
def admin_approval_flow_delete(fid):
    if not is_admin():
        return jsonify({'success': False, 'error': '仅管理员可操作'}), 403
    db = get_db()
    db.execute('DELETE FROM approval_flows WHERE id=?', (fid,))
    db.commit()
    log_operation('平台', '审批流配置', f'删除审批流#{fid}')
    return jsonify({'success': True})


@app.route('/approvals')
def approval_center():
    """平台统一审批中心：待审批（按当前用户角色可审步骤）+ 全部记录"""
    db = get_db()
    my_role = get_current_role()
    pending = db.execute('''SELECT a.*, u.username AS requester FROM approval_requests a
        LEFT JOIN users u ON a.requester_id=u.id WHERE a.status='待审批'
        ORDER BY a.created_at DESC LIMIT 100''').fetchall()
    # 当前用户可审：管理员可审所有；普通用户按审批流当前步骤角色匹配
    can_review = []
    for row in pending:
        row = dict(row)
        steps = plugin_system.get_flow_steps(row['flow_id'])
        cur = row['current_step'] or 1
        need_role = None
        for s in steps:
            if s.get('step') == cur:
                need_role = s.get('role', 'admin')
                break
        if need_role is None:
            need_role = 'admin'
        if my_role == 'admin' or my_role == need_role:
            can_review.append(row)
    done = db.execute('''SELECT a.*, u.username AS requester FROM approval_requests a
        LEFT JOIN users u ON a.requester_id=u.id WHERE a.status!='待审批'
        ORDER BY a.reviewed_at DESC LIMIT 50''').fetchall()
    done = [dict(r) for r in done]
    # 展示名映射
    def _record_title(row):
        try:
            payload = json.loads(row['payload']) if row['payload'] else {}
            if row['plugin_id'] == 'requirements' and row['record_type'] == 'req_status':
                item = db.execute('SELECT code, title FROM req_items WHERE id=?', (row['record_id'],)).fetchone()
                return f"需求 {item['code']} · {item['title']}" if item else f"需求#{row['record_id']}"
            if row['plugin_id'] == 'wbs':
                t = db.execute('SELECT name FROM wbs_tasks WHERE id=?', (row['record_id'],)).fetchone()
                return f"WBS任务：{t['name']}" if t else f"WBS任务#{row['record_id']}"
            return payload.get('title', row['action'])
        except Exception:
            return row['action']
    for r in can_review:
        r['_title'] = _record_title(r)
    for r in done:
        r['_title'] = _record_title(r)
    return render_template('approval_center.html', pending=can_review, done=done,
                           is_admin=is_admin(), my_role=my_role)


@app.route('/approvals/<int:aid>/review', methods=['POST'])
def approval_review(aid):
    """审批处理：approve=1 通过 / 0 驳回；末级通过后按插件分派落地"""
    db = get_db()
    row = db.execute('SELECT * FROM approval_requests WHERE id=?', (aid,)).fetchone()
    if not row:
        return jsonify({'success': False, 'error': '审批单不存在'}), 404
    # 权限：管理员可审所有；普通用户仅可审当前步骤角色匹配
    steps = plugin_system.get_flow_steps(row['flow_id'])
    cur = row['current_step'] or 1
    need_role = 'admin'
    for s in steps:
        if s.get('step') == cur:
            need_role = s.get('role', 'admin')
            break
    my_role = get_current_role()
    if my_role != 'admin' and my_role != need_role:
        return jsonify({'success': False, 'error': '当前步骤需由角色「%s」审批' % need_role}), 403
    approve = request.form.get('approve') == '1'
    comment = request.form.get('comment', '').strip()
    ok, msg, completed = plugin_system.review_approval(aid, session.get('user_id', 0), approve, comment)
    if not ok:
        return jsonify({'success': False, 'error': msg}), 400
    if completed:
        # 审批流全部通过 → 分派落地
        payload = {}
        try:
            payload = json.loads(row['payload']) if row['payload'] else {}
        except Exception:
            pass
        apply_fn = _APPROVAL_APPLY.get((row['plugin_id'], row['record_type']))
        if apply_fn:
            try:
                apply_fn(db, row['record_id'], payload)
            except Exception as e:
                log_operation('平台', '审批', f'#{aid} 落地回调失败: {e}')
    return jsonify({'success': True, 'msg': msg, 'completed': completed})


@app.route('/admin/external')
def admin_external():
    """外部系统接入管理（v11.0 插件接入平台登记/启停/聚合缓存）"""
    if not is_admin():
        flash('仅管理员可管理外部系统接入', 'error')
        return redirect(url_for('index'))
    db = get_db()
    systems = db.execute('''SELECT s.*, (SELECT COUNT(*) FROM external_data_cache c WHERE c.system_id=s.id) AS cache_cnt
        FROM external_systems s ORDER BY s.created_at DESC''').fetchall()
    return render_template('external.html', systems=systems)


@app.route('/admin/external/create', methods=['POST'])
def admin_external_create():
    if not is_admin():
        return jsonify({'success': False, 'error': '仅管理员可操作'}), 403
    db = get_db()
    name = request.form.get('name', '').strip()
    if not name:
        return jsonify({'success': False, 'error': '系统名称必填'}), 400
    db.execute('''INSERT INTO external_systems (name, plugin_id, system_type, access_method, conn_config, status, note)
        VALUES (?,?,?,?,?,?,?)''',
        (name, request.form.get('plugin_id', '') or None,
         request.form.get('system_type', '业务系统'), request.form.get('access_method', 'api'),
         request.form.get('conn_config', ''), '已登记', request.form.get('note', '')))
    db.commit()
    log_operation('平台', '外部接入', f'登记外部系统 {name}')
    return jsonify({'success': True})


@app.route('/admin/external/<int:sid>/toggle', methods=['POST'])
def admin_external_toggle(sid):
    if not is_admin():
        return jsonify({'success': False, 'error': '仅管理员可操作'}), 403
    db = get_db()
    row = db.execute('SELECT * FROM external_systems WHERE id=?', (sid,)).fetchone()
    if not row:
        return jsonify({'success': False, 'error': '系统不存在'}), 404
    new_status = '已停用' if row['status'] == '已启用' else '已启用'
    db.execute("UPDATE external_systems SET status=?, updated_at=datetime('now','localtime') WHERE id=?",
               (new_status, sid))
    db.commit()
    log_operation('平台', '外部接入', f'{"启用" if new_status=="已启用" else "停用"} {row["name"]}')
    return jsonify({'success': True})


@app.route('/admin/external/<int:sid>/test', methods=['POST'])
def admin_external_test(sid):
    """接入测试（v11.0 占位：验证登记信息/连接配置可达性；外部系统真实联通后校验 API）"""
    if not is_admin():
        return jsonify({'success': False, 'error': '仅管理员可操作'}), 403
    db = get_db()
    row = db.execute('SELECT * FROM external_systems WHERE id=?', (sid,)).fetchone()
    if not row:
        return jsonify({'success': False, 'error': '系统不存在'}), 404
    conn = row['conn_config'] or ''
    ok = bool(conn.strip())
    msg = '登记信息完整，等待按《插件接入规范 v1.0》完成接口联调' if ok else '缺少连接配置，请补充后重试'
    return jsonify({'success': ok, 'msg': msg})


@app.route('/admin/external/<int:sid>/cache', methods=['GET'])
def admin_external_cache(sid):
    """聚合缓存视图：查看外部系统上报的数据快照"""
    db = get_db()
    row = db.execute('SELECT * FROM external_systems WHERE id=?', (sid,)).fetchone()
    if not row:
        flash('系统不存在', 'error')
        return redirect(url_for('admin_external'))
    caches = db.execute('SELECT * FROM external_data_cache WHERE system_id=? ORDER BY updated_at DESC',
                        (sid,)).fetchall()
    return render_template('external_cache.html', system=row, caches=caches)



def is_readonly_module(module_prefix):
    """【权限放开】v8.5.0 起取消只读限制"""
    return False

def can_write(module_prefix):
    """检查当前角色是否对指定模块有写权限"""
    return has_module_access(module_prefix) and not is_readonly_module(module_prefix)


# ============================================================
# v10.3.2 阶段2 权限统一：WBS式两级权限（管理员+普通用户）
# 通用规则：普通用户可改自己创建记录的业务字段；状态类字段变更需提交审批（统一审批表）
# WBS 插件保持自身审批流程，不走此表
# ============================================================
def is_admin():
    """当前用户是否为管理员"""
    return get_current_role() == 'admin'


def submit_status_change(plugin_id, record_type, record_id, new_status, payload_extra=None):
    """状态类变更统一入口（v11.0 恢复审批流模式）：
    管理员 → 直接生效；普通用户 → 写入 approval_requests 走平台统一审批流（可多级）。
    审批通过后由审批中心按 _APPROVAL_APPLY 分派落地。
    """
    if is_admin():
        return 'direct', None
    payload = {'new_status': new_status}
    if payload_extra:
        payload.update(payload_extra)
    from plugin_system import submit_approval, get_flow_steps
    db = get_db()
    flow = db.execute('''SELECT * FROM approval_flows
        WHERE plugin_id=? AND record_type=? AND enabled=1 ORDER BY id DESC LIMIT 1''',
        (plugin_id, record_type)).fetchone()
    if not flow:
        steps = [{'step': 1, 'role': 'admin'}]
        flow_id = None
    else:
        steps = get_flow_steps(flow['id'])
        flow_id = flow['id']
    action = f'状态变更：{new_status}'
    aid = submit_approval(plugin_id, record_type, record_id, session.get('user_id', 0),
                          action, payload, flow_id=flow_id, current_step=1)
    return 'pending', f'状态变更申请已提交（审批单#{aid}），等待审批'


# ---- 审批落地函数：审批通过后按插件/记录类型分派执行状态变更 ----
def _apply_req_status_new(db, rid, payload):
    """v11.0 需求收集（req_items 新表）审批落地"""
    from plugins.requirements.routes import _apply_req_status as req_apply
    req_apply(db, rid, payload.get('new_status'), '系统（审批通过）')


def _apply_requirement_status(db, rid, payload):
    from plugins.requirements.routes import sync_requirement_status
    ns = payload.get('new_status')
    db.execute("UPDATE requirements SET status=?, updated_at=datetime('now','localtime') WHERE id=?", (ns, rid))
    db.commit()
    sync_requirement_status(db, rid, ns)


def _apply_version_status(db, vid, payload):
    ns = payload.get('new_status')
    if ns == '已发布':
        db.execute("UPDATE software_versions SET status=?, actual_release_date=datetime('now','localtime') WHERE id=?", (ns, vid))
    else:
        db.execute("UPDATE software_versions SET status=? WHERE id=?", (ns, vid))
    db.commit()


def _apply_task_status(db, tid, payload):
    ns = payload.get('new_status')
    db.execute("UPDATE dev_tasks SET status=? WHERE id=?", (ns, tid))
    db.commit()


def _apply_bug_status(db, bid, payload):
    fields, params = ['status=?'], [payload.get('new_status', '新建')]
    for f in ('assignee', 'fix_solution', 'test_result', 'close_reason', 'root_cause', 'corrective_action', 'preventive_action'):
        if payload.get(f):
            fields.append(f'{f}=?')
            params.append(payload[f])
    if payload.get('new_status') == '已关闭':
        fields.append('closed_at=datetime("now","localtime")')
    db.execute(f'UPDATE bug_records SET {",".join(fields)} WHERE id=?', (*params, bid))
    db.commit()


def _apply_contract_status(db, cid, payload):
    db.execute("UPDATE contracts SET status=? WHERE id=?", (payload.get('new_status'), cid))
    db.commit()


def _apply_device_status(db, did, payload):
    db.execute("UPDATE devices SET status=? WHERE id=?", (payload.get('new_status'), did))
    db.commit()


_APPROVAL_APPLY = {
    ('requirements', 'req_status'): _apply_req_status_new,
    ('requirements', 'requirement'): _apply_requirement_status,
    ('planning', 'software_version'): _apply_version_status,
    ('planning', 'dev_task'): _apply_task_status,
    ('bug', 'bug'): _apply_bug_status,
    ('contract', 'contract'): _apply_contract_status,
    ('device', 'device'): _apply_device_status,
}


@app.context_processor
def inject_permissions():
    """向模板注入权限相关变量"""
    return {
        'current_role': get_current_role(),
        'role_name': ROLE_PERMISSIONS.get(get_current_role(), {}).get('name', '未知'),
        'has_module_access': has_module_access,
        'is_readonly_module': is_readonly_module,
        'can_write': can_write,
    }

# ============================================================
# 数据库工具
# ============================================================
def get_db():
    if 'db' not in g:
        g.db = sqlite3.connect(app.config['DATABASE'])
        g.db.row_factory = sqlite3.Row
        g.db.execute('PRAGMA foreign_keys = ON')
    return g.db

@app.teardown_appcontext
def close_db(exception):
    db = g.pop('db', None)
    if db is not None:
        db.close()

def init_db():
    """初始化数据库表结构（从schema.sql读取）"""
    schema_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'schema.sql')
    db = sqlite3.connect(app.config['DATABASE'])
    with open(schema_path, 'r', encoding='utf-8') as f:
        db.executescript(f.read())
    # 首次运行（users 为空）：写入默认账号，保证解压即用
    # 管理员 admin/admin123，普通用户 product/product123
    if db.execute('SELECT COUNT(*) FROM users').fetchone()[0] == 0:
        db.executescript("""
            INSERT INTO users (username, password, display_name, role, is_active)
            VALUES ('admin','admin123','系统管理员','admin',1);
            INSERT INTO users (username, password, display_name, role, is_active)
            VALUES ('product','product123','产品经理','user',1);
        """)
    db.commit()

# ============================================================
# 输入校验工具
# ============================================================
def validate_positive_int(value, field_name, allow_zero=False):
    """校验正整数，返回(int值, 错误信息)"""
    try:
        val = int(value)
        if allow_zero:
            if val < 0:
                return None, f"{field_name}不能为负数"
        else:
            if val <= 0:
                return None, f"{field_name}必须大于0"
        return val, None
    except (ValueError, TypeError):
        return None, f"{field_name}必须是整数"

def validate_range(value, field_name, min_val, max_val):
    """校验数值范围，返回(float值, 错误信息)"""
    try:
        val = float(value)
        if val < min_val or val > max_val:
            return None, f"{field_name}必须在{min_val}~{max_val}之间"
        return val, None
    except (ValueError, TypeError):
        return None, f"{field_name}必须是数字"

def validation_error(message, back_url=None):
    """返回校验错误页面"""
    return render_template('error.html', message=message, back_url=back_url or request.referrer or '/')

# ============================================================
# 首页 / 仪表盘
# ============================================================
@app.route('/')
def index():
    db = get_db()
    # v11.0 聚合视图：WBS + 需求收集 + 外部系统 + 审批待办 + 预警（平台级聚合）
    stats = {
        # WBS
        'wbs_projects_total': db.execute('SELECT COUNT(*) FROM wbs_projects').fetchone()[0],
        'wbs_projects_active': db.execute("SELECT COUNT(*) FROM wbs_projects WHERE status IN ('进行中','规划中')").fetchone()[0],
        'wbs_projects_done': db.execute("SELECT COUNT(*) FROM wbs_projects WHERE status='已完成'").fetchone()[0],
        'wbs_tasks_total': db.execute('SELECT COUNT(*) FROM wbs_tasks').fetchone()[0],
        'wbs_tasks_pending': db.execute("SELECT COUNT(*) FROM wbs_tasks WHERE approval_status='待审批'").fetchone()[0],
        'wbs_tasks_active': db.execute("SELECT COUNT(*) FROM wbs_tasks WHERE status IN ('进行中','待开始') AND approval_status='已通过'").fetchone()[0],
        'wbs_tasks_done': db.execute("SELECT COUNT(*) FROM wbs_tasks WHERE status='已完成'").fetchone()[0],
        'wbs_tasks_overdue': db.execute("SELECT COUNT(*) FROM wbs_tasks WHERE status NOT IN ('已完成','已取消') AND plan_end < date('now','localtime')").fetchone()[0],
        # 需求收集（新表 req_items）
        'req_total': db.execute('SELECT COUNT(*) FROM req_items WHERE is_deleted=0').fetchone()[0],
        'req_pending_review': db.execute("SELECT COUNT(*) FROM req_items WHERE is_deleted=0 AND status='待评审'").fetchone()[0],
        'req_doing': db.execute("SELECT COUNT(*) FROM req_items WHERE is_deleted=0 AND status IN ('评审通过','已采纳','开发中')").fetchone()[0],
        'req_done': db.execute("SELECT COUNT(*) FROM req_items WHERE is_deleted=0 AND status IN ('已完成','已关闭')").fetchone()[0],
        'req_due_soon': db.execute("SELECT COUNT(*) FROM req_items WHERE is_deleted=0 AND status NOT IN ('已完成','已关闭') AND delivery_date != '' AND delivery_date <= date('now','localtime','+7 day')").fetchone()[0],
        'req_overdue': db.execute("SELECT COUNT(*) FROM req_items WHERE is_deleted=0 AND status NOT IN ('已完成','已关闭') AND delivery_date != '' AND delivery_date < date('now','localtime')").fetchone()[0],
        # 平台
        'external_total': db.execute('SELECT COUNT(*) FROM external_systems').fetchone()[0],
        'external_active': db.execute("SELECT COUNT(*) FROM external_systems WHERE status='已启用'").fetchone()[0],
        'approval_pending_total': db.execute("SELECT COUNT(*) FROM approval_requests WHERE status='待审批'").fetchone()[0],
    }
    recent_logs = db.execute('SELECT * FROM operation_logs ORDER BY created_at DESC LIMIT 10').fetchall()
    # 待我审批（当前用户可审）
    my_role = get_current_role()
    import plugin_system as _ps
    pendings = db.execute("SELECT * FROM approval_requests WHERE status='待审批' ORDER BY created_at DESC LIMIT 50").fetchall()
    my_approvals = []
    for p in pendings:
        steps = _ps.get_flow_steps(p['flow_id'])
        cur = p['current_step'] or 1
        need_role = 'admin'
        for s in steps:
            if s.get('step') == cur:
                need_role = s.get('role', 'admin')
                break
        if my_role == 'admin' or my_role == need_role:
            my_approvals.append(p)
    # 预警区：WBS 逾期任务 + 需求临期/逾期
    wbs_overdue_tasks = db.execute('''SELECT id, name, plan_end FROM wbs_tasks
        WHERE status NOT IN ('已完成','已取消') AND plan_end < date('now','localtime')
        ORDER BY plan_end LIMIT 10''').fetchall()
    req_alerts = db.execute('''SELECT id, code, title, delivery_date, status FROM req_items
        WHERE is_deleted=0 AND status NOT IN ('已完成','已关闭') AND delivery_date != ''
        AND delivery_date <= date('now','localtime','+7 day')
        ORDER BY delivery_date LIMIT 10''').fetchall()
    now = datetime.now()
    week_map = {0:'周一',1:'周二',2:'周三',3:'周四',4:'周五',5:'周六',6:'周日'}
    return render_template('index.html', stats=stats,
                           recent_logs=recent_logs,
                           my_approvals=my_approvals,
                           wbs_overdue_tasks=wbs_overdue_tasks,
                           req_alerts=req_alerts,
                           now_day=now.day, now_month=now.month, now_week=week_map[now.weekday()],
                           now_date=now.strftime('%Y-%m-%d'))

# ============================================================
# 启动
# ============================================================
def get_config():
    """读取配置：优先级 命令行参数 > 环境变量 > config.py > 默认值"""
    import argparse
    default_port = 5000
    default_debug = True

    # 尝试从 config.py 读取
    try:
        from config import PORT as cfg_port, DEBUG as cfg_debug
        default_port = cfg_port
        default_debug = cfg_debug
    except ImportError:
        pass

    # 环境变量
    env_port = os.environ.get('BUSINESS_PORT')
    if env_port and env_port.isdigit():
        default_port = int(env_port)
    env_debug = os.environ.get('BUSINESS_DEBUG')
    if env_debug is not None:
        default_debug = env_debug.lower() in ('1', 'true', 'yes')

    # 命令行参数（最高优先级）
    parser = argparse.ArgumentParser(description='AI驱动的业务高效运转系统')
    parser.add_argument('--port', type=int, default=None, help='服务端口号（默认6000）')
    parser.add_argument('--host', type=str, default='0.0.0.0', help='监听地址（默认0.0.0.0）')
    parser.add_argument('--debug', action='store_true', default=None, help='开启调试模式')
    parser.add_argument('--no-debug', action='store_true', help='关闭调试模式（生产环境）')
    args = parser.parse_args()

    port = args.port if args.port is not None else default_port
    debug = default_debug
    if args.debug:
        debug = True
    if args.no_debug:
        debug = False

    return args.host, port, debug


# ============================================================
# 操作日志
# ============================================================
@app.route('/logs')
def logs():
    db = get_db()
    logs = db.execute('SELECT * FROM operation_logs ORDER BY created_at DESC LIMIT 100').fetchall()
    return render_template('logs.html', logs=logs)

# ============================================================
# API接口（供后续扩展）
# ============================================================
@app.route('/api/stats')
def api_stats():
    db = get_db()
    return jsonify({
        'wbs_projects': db.execute('SELECT COUNT(*) FROM wbs_projects').fetchone()[0],
        'wbs_tasks': db.execute('SELECT COUNT(*) FROM wbs_tasks').fetchone()[0],
        'wbs_tasks_pending': db.execute("SELECT COUNT(*) FROM wbs_tasks WHERE approval_status='待审批'").fetchone()[0],
        'req_total': db.execute('SELECT COUNT(*) FROM req_items WHERE is_deleted=0').fetchone()[0],
        'req_pending': db.execute("SELECT COUNT(*) FROM req_items WHERE is_deleted=0 AND status='待评审'").fetchone()[0],
        'req_done': db.execute("SELECT COUNT(*) FROM req_items WHERE is_deleted=0 AND status IN ('已完成','已关闭')").fetchone()[0],
        'approval_pending': db.execute("SELECT COUNT(*) FROM approval_requests WHERE status='待审批'").fetchone()[0],
        'external_total': db.execute('SELECT COUNT(*) FROM external_systems').fetchone()[0],
    })

@app.route('/api/global-search')
def api_global_search():
    """全局搜索API（v11.0 跨插件聚合）：WBS 项目/任务 + 需求 + 外部系统"""
    q = request.args.get('q', '').strip()
    if len(q) < 2:
        return jsonify({'success': False, 'results': []})
    db = get_db()
    results = []
    like = f'%{q}%'
    # 搜索 WBS 项目
    try:
        projs = db.execute('SELECT id, name, owner, status FROM wbs_projects WHERE name LIKE ? OR owner LIKE ? OR description LIKE ? LIMIT 10', (like, like, like)).fetchall()
        for p in projs:
            results.append({
                'type': 'wbs项目',
                'title': p['name'],
                'subtitle': '负责人：' + (p['owner'] or '') + ' · ' + (p['status'] or ''),
                'url': '/wbs/' + str(p['id'])
            })
    except Exception:
        pass
    # 搜索 WBS 任务
    try:
        tasks = db.execute('SELECT id, project_id, name, wbs_code, owner, status FROM wbs_tasks WHERE name LIKE ? OR wbs_code LIKE ? OR owner LIKE ? LIMIT 10', (like, like, like)).fetchall()
        for t in tasks:
            results.append({
                'type': 'wbs任务',
                'title': (t['wbs_code'] or '') + ' ' + t['name'],
                'subtitle': '负责人：' + (t['owner'] or '') + ' · ' + (t['status'] or ''),
                'url': '/wbs/' + str(t['project_id'])
            })
    except Exception:
        pass
    # 搜索需求（req_items）
    try:
        reqs = db.execute('SELECT id, code, title, status, owner FROM req_items WHERE is_deleted=0 AND (title LIKE ? OR code LIKE ? OR customer LIKE ? OR source_dept LIKE ?) LIMIT 10', (like, like, like, like)).fetchall()
        for r in reqs:
            results.append({
                'type': '需求',
                'title': (r['code'] or '') + ' ' + r['title'],
                'subtitle': '状态：' + (r['status'] or '') + ' · 负责人：' + (r['owner'] or ''),
                'url': '/requirements/' + str(r['id'])
            })
    except Exception:
        pass
    # 搜索外部系统
    try:
        exts = db.execute('SELECT id, name, system_type, status FROM external_systems WHERE name LIKE ? OR plugin_id LIKE ? LIMIT 5', (like, like)).fetchall()
        for e in exts:
            results.append({
                'type': '外部系统',
                'title': e['name'],
                'subtitle': (e['system_type'] or '') + ' · ' + (e['status'] or ''),
                'url': '/admin/external'
            })
    except Exception:
        pass
    return jsonify({'success': True, 'results': results[:20]})


# ============================================================
# 反向规划模块1：软件版本规划（根据用户需求反向规划版本升级）
# ============================================================

if __name__ == '__main__':
    # 初始化数据库（CREATE TABLE IF NOT EXISTS，兼容旧数据库）
    init_db()
    # 数据库迁移：为旧数据库添加缺失字段
    def migrate_db():
        db = sqlite3.connect(app.config['DATABASE'])
        db.row_factory = sqlite3.Row
        # 检查bug_records表的字段
        cols = [r[1] for r in db.execute('PRAGMA table_info(bug_records)').fetchall()]
        bug_migrations = [
            ('bug_type', "TEXT DEFAULT '功能Bug'"),
            ('priority', "TEXT DEFAULT '中'"),
            ('close_reason', 'TEXT'),
            ('fix_solution', 'TEXT'),
            ('test_result', 'TEXT'),
        ]
        for col, definition in bug_migrations:
            if col not in cols:
                try:
                    db.execute(f'ALTER TABLE bug_records ADD COLUMN {col} {definition}')
                    print(f'  数据库迁移：bug_records表添加字段 {col}')
                except Exception as e:
                    print(f'  数据库迁移警告：添加{col}失败 - {e}')
        # 检查bug_workflow表的字段
        wf_cols = [r[1] for r in db.execute('PRAGMA table_info(bug_workflow)').fetchall()]
        wf_migrations = [
            ('fix_solution', 'TEXT'),
            ('test_result', 'TEXT'),
            ('close_reason', 'TEXT'),
        ]
        for col, definition in wf_migrations:
            if col not in wf_cols:
                try:
                    db.execute(f'ALTER TABLE bug_workflow ADD COLUMN {col} {definition}')
                    print(f'  数据库迁移：bug_workflow表添加字段 {col}')
                except Exception as e:
                    print(f'  数据库迁移警告：添加{col}失败 - {e}')
        # 将旧状态"待处理"迁移为"新建"
        try:
            db.execute("UPDATE bug_records SET status='新建' WHERE status='待处理'")
            db.execute("UPDATE bug_records SET status='已修复' WHERE status='待验证'")
        except Exception as e:
            print(f'  数据库迁移警告：状态迁移失败 - {e}')
        # 检查contracts表的字段
        try:
            contract_cols = [r[1] for r in db.execute('PRAGMA table_info(contracts)').fetchall()]
            if 'paid_amount' not in contract_cols:
                db.execute('ALTER TABLE contracts ADD COLUMN paid_amount REAL DEFAULT 0')
                print('  数据库迁移：contracts表添加字段 paid_amount')
            # 将旧合同类型"采购合同"迁移为"物料合同"
            db.execute("UPDATE contracts SET contract_type='物料合同' WHERE contract_type='采购合同'")
        except Exception as e:
            print(f'  数据库迁移警告：合同表迁移失败 - {e}')
        # 创建contract_payments表（如果不存在）
        try:
            db.execute('''CREATE TABLE IF NOT EXISTS contract_payments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                contract_id INTEGER NOT NULL,
                node_name TEXT NOT NULL,
                plan_date TEXT,
                amount REAL CHECK (amount >= 0),
                status TEXT DEFAULT '待付款',
                actual_date TEXT,
                remark TEXT,
                created_at TEXT DEFAULT (datetime('now','localtime')),
                FOREIGN KEY (contract_id) REFERENCES contracts(id) ON DELETE CASCADE
            )''')
        except Exception as e:
            print(f'  数据库迁移警告：创建contract_payments表失败 - {e}')
        # 业务流程打通：dev_tasks表增加requirement_id字段
        try:
            dt_cols = [r[1] for r in db.execute('PRAGMA table_info(dev_tasks)').fetchall()]
            if 'requirement_id' not in dt_cols:
                db.execute('ALTER TABLE dev_tasks ADD COLUMN requirement_id INTEGER')
                print('  数据库迁移：dev_tasks表添加字段 requirement_id（打通需求与任务关联）')
        except Exception as e:
            print(f'  数据库迁移警告：dev_tasks表迁移失败 - {e}')
        # 业务流程打通：production_orders表增加schedule_id字段
        try:
            po_cols = [r[1] for r in db.execute('PRAGMA table_info(production_orders)').fetchall()]
            if 'schedule_id' not in po_cols:
                db.execute('ALTER TABLE production_orders ADD COLUMN schedule_id INTEGER')
                print('  数据库迁移：production_orders表添加字段 schedule_id（打通规划与执行关联）')
        except Exception as e:
            print(f'  数据库迁移警告：production_orders表迁移失败 - {e}')
        # 业务流程打通：requisition_items表增加material_plan_id字段
        try:
            ri_cols = [r[1] for r in db.execute('PRAGMA table_info(requisition_items)').fetchall()]
            if 'material_plan_id' not in ri_cols:
                db.execute('ALTER TABLE requisition_items ADD COLUMN material_plan_id INTEGER')
                print('  数据库迁移：requisition_items表添加字段 material_plan_id（打通物料与领用关联）')
        except Exception as e:
            print(f'  数据库迁移警告：requisition_items表迁移失败 - {e}')
        # 业务流程打通：inventory表增加product_id和is_product字段（统一管理物料和成品库存）
        try:
            inv_cols = [r[1] for r in db.execute('PRAGMA table_info(inventory)').fetchall()]
            if 'product_id' not in inv_cols:
                db.execute('ALTER TABLE inventory ADD COLUMN product_id INTEGER')
                # v8.8.0：交付管理简化——生产完成/入库完成/是否已交付
                dp_cols = [r[1] for r in db.execute('PRAGMA table_info(delivery_plans)').fetchall()]
                for col, ddl in [('production_date', 'TEXT'), ('stock_date', 'TEXT'), ('delivered', 'INTEGER DEFAULT 0')]:
                    if col not in dp_cols:
                        db.execute(f'ALTER TABLE delivery_plans ADD COLUMN {col} {ddl}')
                print('  数据库迁移：inventory表添加字段 product_id（支持成品库存管理）')
            if 'is_product' not in inv_cols:
                db.execute('ALTER TABLE inventory ADD COLUMN is_product INTEGER DEFAULT 0')
                print('  数据库迁移：inventory表添加字段 is_product（区分物料与成品）')
        except Exception as e:
            print(f'  数据库迁移警告：inventory表迁移失败 - {e}')
        # 需求表迁移：增加4类需求的特有字段
        try:
            req_cols = [r[1] for r in db.execute('PRAGMA table_info(requirements)').fetchall()]
            req_new_cols = {
                'req_type': "TEXT DEFAULT '用户需求'",
                'product_model': 'TEXT',
                'batch_delivery': "TEXT DEFAULT '否'",
                'application_scenario': 'TEXT',
                'system_verify_point': 'TEXT',
                'verify_function': 'TEXT',
                'performance_req': 'TEXT',
                'support_product': 'TEXT',
                'certification_type': 'TEXT',
                'spec_requirement': 'TEXT',
                'industry_standard': 'TEXT',
                'baseline_product_type': 'TEXT',
                'task_progress': 'TEXT',
                'time_plan': 'TEXT',
                'deliverable': 'TEXT',
            }
            for col, col_type in req_new_cols.items():
                if col not in req_cols:
                    db.execute(f'ALTER TABLE requirements ADD COLUMN {col} {col_type}')
                    print(f'  数据库迁移：requirements表添加字段 {col}（4类需求重构）')
        except Exception as e:
            print(f'  数据库迁移警告：requirements表迁移失败 - {e}')

        # gantt_settings 迁移：添加 synced 字段
        try:
            gs_cols = [r[1] for r in db.execute('PRAGMA table_info(gantt_settings)').fetchall()]
            if gs_cols and 'synced' not in gs_cols:
                db.execute('ALTER TABLE gantt_settings ADD COLUMN synced INTEGER DEFAULT 0')
                print('  数据库迁移：gantt_settings表添加字段 synced')
        except Exception:
            pass

        db.commit()
    migrate_db()
    host, port, debug = get_config()
    print('='*60)
    print('  AI驱动的业务高效运转系统')
    print(f'  本机访问: http://127.0.0.1:{port}')
    print(f'  内网访问: http://<本机IP>:{port}')
    print(f'  调试模式: {"开启(Flask开发服务器)" if debug else "关闭(waitress生产服务器)"}')
    print('  端口配置: 命令行--port > 环境变量BUSINESS_PORT > config.py > 默认5000')
    print('='*60)
    if debug:
        app.run(host=host, port=port, debug=True)
    else:
        from waitress import serve
        print(f'  waitress生产服务器启动中... 线程数: 8')
        print('  按 Ctrl+C 停止服务')
        print('='*60)
        serve(app, host=host, port=port, threads=8, connection_limit=32)
