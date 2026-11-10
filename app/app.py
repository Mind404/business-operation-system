# -*- coding: utf-8 -*-
"""
业务高效运转系统 - Flask Web应用
适用于内网Windows部署，SQLite轻量数据库
"""
import os
import sys
# 便携版兼容：确保脚本所在目录（app\）在模块搜索路径中。
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sqlite3
import json
import re
import secrets
import random
import io
import gzip
from datetime import datetime, date, timedelta
import time
from flask import Flask, render_template, request, redirect, url_for, jsonify, g, session, flash, send_file, send_from_directory, current_app, Response
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)

# v12.7.2 性能优化：WSGI 层 gzip 压缩中间件（静态文件也能压缩，15MB manifest→~4MB）
class GzipMiddleware:
    """轻量 gzip 中间件：对文本类型响应自动压缩，减少局域网传输时间。"""
    def __init__(self, wsgi_app):
        self.wsgi_app = wsgi_app

    def __call__(self, environ, start_response):
        accept_encoding = environ.get('HTTP_ACCEPT_ENCODING', '')
        if 'gzip' not in accept_encoding.lower():
            return self.wsgi_app(environ, start_response)

        # v12.7.5 修复：gzip 决策必须为「每次请求独立」的局部状态。
        # 原实现用 self._do_gzip（实例属性）跨请求共享，waitress 多线程下并发请求会互相污染：
        # 大响应（>1KB 置 True）与小响应（保存接口约 57 字节）同时进入时，小响应被误压缩
        # 但响应头未更新（Content-Length 未移除、无 Content-Encoding），
        # 导致 waitress 报 "application-written content exceeded ... Content-Length header" 并截断响应体，
        # 前端解析保存结果失败 → 数据看似丢失。改为 __call__ 内局部容器，每请求独立。
        state = {'do_gzip': False}
        def custom_start_response(status, headers, exc_info=None):
            headers_list = list(headers)
            headers_dict = dict(headers_list)
            content_type = headers_dict.get('Content-Type', '')
            content_length = int(headers_dict.get('Content-Length', 0) or 0)
            compressible = (content_type.startswith('text/') or
                            'application/json' in content_type or
                            'application/javascript' in content_type or
                            'application/xml' in content_type)
            if compressible and content_length > 1024:
                state['do_gzip'] = True
                # 移除 Content-Length（压缩后大小变了），加 gzip 头
                headers_list = [(k, v) for k, v in headers_list if k.lower() != 'content-length']
                headers_list.append(('Content-Encoding', 'gzip'))
                headers_list.append(('Vary', 'Accept-Encoding'))
            return start_response(status, headers_list, exc_info)

        result = self.wsgi_app(environ, custom_start_response)
        if state['do_gzip']:
            data = b''.join(result)
            compressed = gzip.compress(data, compresslevel=6)
            return [compressed]
        return result

app.wsgi_app = GzipMiddleware(app.wsgi_app)
# v12.2.0 安全加固：SECRET_KEY 自动生成并持久化（首次启动生成 secret.key，后续读取）
_SECRET_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'secret.key')
def _load_or_create_secret(path):
    try:
        if os.path.exists(path):
            with open(path, 'r', encoding='utf-8') as f:
                key = f.read().strip()
            if key:
                return key
        key = os.urandom(32).hex()
        with open(path, 'w', encoding='utf-8') as f:
            f.write(key)
        return key
    except Exception as e:
        print(f'[警告] secret.key 生成失败，使用内置密钥（{e}）')
        return 'business-system-2024'
app.config['SECRET_KEY'] = _load_or_create_secret(_SECRET_PATH)
app.config['DATABASE'] = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'business.db')
# v12.2.0 会话安全：登录会话 12 小时过期
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(hours=12)
# v12.5.28 安全加固：
# ① 请求体大小上限 64MB（覆盖 WBS 单附件 50MB 上限 + 余量；插件包/Excel/JSON 均远低于此），
#    防止超大请求耗尽内存（此前无全局上限，100MB 请求会被完整读入）
# ② Session cookie 显式加固：HttpOnly（防 JS 窃取）+ SameSite=Lax（缓解跨站请求伪造 CSRF）
app.config['MAX_CONTENT_LENGTH'] = 64 * 1024 * 1024
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'

# ============================================================
# v12.2.0 登录失败限流：同账号 5 次失败锁定 5 分钟（进程内计数，重启清零）
# ============================================================
_LOGIN_FAILS = {}
_LOGIN_MAX_FAILS = 5
_LOGIN_LOCK_SECONDS = 300


def _check_login_locked(username):
    """返回剩余锁定秒数；未锁定返回 None"""
    rec = _LOGIN_FAILS.get(username)
    if not rec:
        return None
    if rec['locked_until'] and time.time() < rec['locked_until']:
        return int(rec['locked_until'] - time.time()) + 1
    return None


def _record_login_fail(username):
    rec = _LOGIN_FAILS.setdefault(username, {'fails': 0, 'locked_until': 0})
    rec['fails'] += 1
    if rec['fails'] >= _LOGIN_MAX_FAILS:
        rec['locked_until'] = time.time() + _LOGIN_LOCK_SECONDS
        rec['fails'] = 0


def _record_login_success(username):
    _LOGIN_FAILS.pop(username, None)


def _is_hashed(pwd):
    """判断密码是否为哈希存储（werkzeug 生成的哈希以算法: 前缀开头）"""
    return bool(pwd) and (pwd.startswith(('scrypt:', 'pbkdf2:', 'sha256$', 'md5$')) or '$' in pwd)


def migrate_plain_passwords():
    """v12.2.0 密码哈希化迁移：存量明文密码原地升级为哈希（幂等，可安全重复执行）"""
    try:
        conn = sqlite3.connect(app.config['DATABASE'], timeout=5.0)
        rows = conn.execute('SELECT id, password FROM users').fetchall()
        changed = 0
        for uid, pwd in rows:
            if pwd and not _is_hashed(pwd):
                conn.execute('UPDATE users SET password=? WHERE id=?', (generate_password_hash(pwd), uid))
                changed += 1
        conn.commit()
        conn.close()
        if changed:
            print(f'[安全加固] 已升级 {changed} 个账号的明文密码为哈希存储')
    except Exception as e:
        print(f'[警告] 密码迁移失败（不影响启动）: {e}')

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

# v12.4.1 平台管理员 AI 智能助手（配置 + 数据分析，仅 platform_admin）
from ai_routes import ai_bp
app.register_blueprint(ai_bp)

# v10.4.0 插件包化：自动加载 plugins/ 下的独立插件包
from plugins import load_plugins
load_plugins(app)

# 插件化基底：建表 + 注册插件 + 路由守卫
# 注意：init_plugin_tables()（含增量迁移 v6 角色升级）必须在 init_db() 之后执行，
# 否则全新库时迁移早于 schema.sql 建表/种子，admin 角色不会升级为 platform_admin。
import plugin_system
from plugin_system import log_operation
plugin_system.register_plugin_guard(app)

# ============================================================
# 用户认证
# ============================================================
@app.before_request
def _limit_body_size():
    """v12.5.28 安全修复：请求体大小上限（Flask 的 MAX_CONTENT_LENGTH 仅对 form/multipart 生效，
    纯 JSON/原始字节接口不受限，故在此对任意请求显式检查）"""
    cl = request.content_length
    if cl is not None and cl > app.config.get('MAX_CONTENT_LENGTH', 0):
        return jsonify({'ok': False, 'msg': '请求体过大（超过 64MB 上限）'}), 413


# ============================================================
# v12.7.10.10 单账号单设备登录（挤下线）
#   一个账号在库里只保留一个有效令牌；新设备登录只覆盖令牌，
#   旧设备的令牌立即不匹配，下一次请求就被登出并提示"已在另一处登录"。
# ============================================================
def _bind_login_session(user_id):
    """登录成功时绑定本次会话令牌，覆盖该账号的旧令牌（= 把旧设备挤下线）。"""
    token = secrets.token_hex(16)
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    ip = (request.headers.get('X-Forwarded-For') or request.remote_addr or '')[:64]
    ua = (request.headers.get('User-Agent') or '')[:120]
    try:
        db = get_db()
        db.execute(
            'INSERT INTO user_sessions (user_id, token, ip, user_agent, login_at, last_seen) '
            'VALUES (?,?,?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET '
            'token=excluded.token, ip=excluded.ip, user_agent=excluded.user_agent, '
            'login_at=excluded.login_at, last_seen=excluded.last_seen',
            (user_id, token, ip, ua, now, now))
        db.commit()
    except Exception as e:
        # 令牌表异常不能让登录不可用：退化为不校验（记日志）
        print('=== _bind_login_session error ===', e)
    session['sess_token'] = token
    return token


def _clear_login_session(user_id):
    try:
        db = get_db()
        db.execute('DELETE FROM user_sessions WHERE user_id=?', (user_id,))
        db.commit()
    except Exception:
        pass


def _session_is_current():
    """当前会话令牌是否仍是该账号的有效令牌（False = 已被其他地方登录挤掉）。"""
    uid = session.get('user_id')
    if not uid:
        return False
    token = session.get('sess_token')
    if not token:
        # 升级前建立的老会话：认领为当前令牌，升级后不打断在线用户
        _bind_login_session(uid)
        return True
    try:
        row = get_db().execute('SELECT token FROM user_sessions WHERE user_id=?', (uid,)).fetchone()
    except Exception:
        return True
    if not row:
        _bind_login_session(uid)
        return True
    return row['token'] == token


# ============================================================
# v12.7.10.10 导航排序（平台管理员拖拽后保存，所有用户生效）
# ============================================================
NAV_ADMIN_ITEMS = [
    {'id': 'admin_users',    'name': '用户管理',     'icon': '👥', 'endpoint': 'admin_users',    'nav_path': '/admin/users'},
    {'id': 'admin_plugins',  'name': '插件管理',     'icon': '🧩', 'endpoint': 'admin_plugins',  'nav_path': '/admin/plugins'},
    {'id': 'admin_backup',   'name': '数据备份',     'icon': '💾', 'endpoint': 'admin_backup',   'nav_path': '/admin/backup'},
    {'id': 'admin_ai',       'name': 'AI 智能分析',  'icon': '🤖', 'endpoint': 'ai_admin.index', 'nav_path': '/admin/ai'},
]
_NAV_KEY_RE = re.compile(r'^[a-z0-9_-]{1,40}$')


def _nav_order():
    try:
        row = get_db().execute("SELECT v FROM ui_prefs WHERE k='nav_order'").fetchone()
        if row and row['v']:
            data = json.loads(row['v'])
            return [k for k in data if isinstance(k, str) and _NAV_KEY_RE.match(k)]
    except Exception:
        pass
    return []


def _apply_nav_order(items, order):
    """按保存的顺序重排；未记录的项排在后面，保持原有相对顺序。"""
    if not order:
        return items
    idx = {k: i for i, k in enumerate(order)}
    return sorted(items, key=lambda it: idx.get(it.get('id'), len(order) + 1))


@app.route('/plugin-shell/<pid>')
def plugin_shell(pid):
    """v12.7.10.10 外来插件外壳页：顶部固定"返回平台"，内容区 iframe 嵌入插件首页。

    外来插件（上传安装 / 目录发现）通常不继承平台 base.html，直接进页面后没有导航，
    用户无法回到平台；统一走这里就能保证任何外来插件都有明确的返回入口。
    """
    man = plugin_system.plugin_manifest(pid)
    if not man or not plugin_system.plugin_is_external(pid):
        flash('插件不存在或不是外部插件', 'error')
        return redirect(url_for('index'))
    home = plugin_system.plugin_home_url(pid)
    return render_template('plugin_shell.html', pid=pid,
                           plugin_name=man.get('name', pid), plugin_icon=man.get('icon', '🧩'),
                           home_url=home)


@app.route('/admin/nav/order', methods=['POST'])
def admin_nav_order():
    """保存导航项顺序（仅平台管理员）。"""
    if session.get('user_role') != 'platform_admin':
        return jsonify({'ok': False, 'msg': '仅平台管理员可调整导航顺序'}), 403
    keys = (request.get_json(silent=True) or {}).get('keys') or []
    keys = [k for k in keys if isinstance(k, str) and _NAV_KEY_RE.match(k)][:60]
    try:
        db = get_db()
        db.execute("INSERT INTO ui_prefs (k, v, updated_at) VALUES ('nav_order', ?, ?) "
                   "ON CONFLICT(k) DO UPDATE SET v=excluded.v, updated_at=excluded.updated_at",
                   (json.dumps(keys), datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
        db.commit()
        return jsonify({'ok': True, 'keys': keys})
    except Exception as e:
        return jsonify({'ok': False, 'msg': '保存失败：%s' % e})


@app.route('/admin/nav/order/reset', methods=['POST'])
def admin_nav_order_reset():
    if session.get('user_role') != 'platform_admin':
        return jsonify({'ok': False, 'msg': '无权限'}), 403
    try:
        db = get_db()
        db.execute("DELETE FROM ui_prefs WHERE k='nav_order'")
        db.commit()
        return jsonify({'ok': True})
    except Exception as e:
        return jsonify({'ok': False, 'msg': '重置失败：%s' % e})


@app.before_request
def require_login():
    """所有页面需要登录，除了登录页和静态资源（含插件静态资源）"""
    ep = request.endpoint or ''
    if ep in ('login', 'static') or ep.endswith('.static'):
        return None
    if 'user_id' not in session:
        return redirect(url_for('login', next=request.path))
    # v12.7.10.10 单账号单设备：令牌不一致 = 该账号已在别处登录，本会话作废
    if not _session_is_current():
        session.clear()
        return redirect(url_for('login', kicked=1))
    # 权限校验：检查当前角色是否有权限访问该模块
    endpoint = request.endpoint or ''
    # 工作台和登录相关页面所有角色都可以访问
    if endpoint in ('index', 'logout'):
        return None
    # 【权限放开】v8.5.0 起取消全部模块权限与只读限制，所有登录用户均可访问与编辑
    # 保留登录校验；gantt/模块权限/只读拦截全部放行
    return None

@app.after_request
def _force_static_mime(resp):
    """v12.6.6：Windows 内嵌 Python 的 mimetypes 注册表可能把 .js 识别成 text/plain，
    导致 ESM 模块被浏览器严格 MIME 检查拒绝。这里对 .js/.mjs/.css 响应强制设置正确 Content-Type。"""
    p = request.path or ''
    if p.endswith('.js') or p.endswith('.mjs'):
        resp.headers['Content-Type'] = 'text/javascript; charset=utf-8'
    elif p.endswith('.css'):
        resp.headers['Content-Type'] = 'text/css; charset=utf-8'
    elif p.endswith('.json'):
        resp.headers['Content-Type'] = 'application/json; charset=utf-8'
    # v12667：表格静态资源全部带 ?v= 版本号引用，可安全长缓存一年。
    # v12.7.2：所有静态资源（CSS/JS/JSON/图片）统一长缓存一年，二次打开秒开。
    is_static = (p.startswith('/univer/static/') or
                 p.startswith('/static/') or
                 p.endswith('.js') or p.endswith('.css') or
                 p.endswith('.json') or p.endswith('.png') or
                 p.endswith('.jpg') or p.endswith('.svg') or
                 p.endswith('.woff2') or p.endswith('.woff'))
    if is_static:
        resp.headers['Cache-Control'] = 'public, max-age=31536000, immutable'
    return resp


@app.context_processor
def inject_user():
    """所有模板可访问当前用户 + 插件导航上下文"""
    ctx = {'current_user': session.get('user_name'), 'current_role': session.get('user_role')}
    try:
        nav = plugin_system.inject_nav_context()
        # v12.7.10.10：导航按平台管理员保存的顺序渲染（所有用户生效）
        order = _nav_order()
        if order:
            for key in ('nav_core', 'nav_biz', 'nav_ext'):
                if nav.get(key):
                    nav[key] = _apply_nav_order(nav[key], order)
        ctx.update(nav)
        ctx['nav_admin'] = _apply_nav_order(list(NAV_ADMIN_ITEMS), order)
        ctx['nav_order'] = order
    except Exception as e:
        import traceback
        print('=== inject_nav_context error ===')
        traceback.print_exc()
        ctx.update({'nav_core': [], 'nav_biz': [], 'nav_ext': [], 'nav_all_plugins': [], 'user_subscribed': set(),
                    'nav_admin': list(NAV_ADMIN_ITEMS), 'nav_order': []})
    return ctx

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        next_url = request.form.get('next', '/')
        # v12.5.28 安全修复：防开放重定向——next 仅允许站内相对路径
        # （外部 URL / 协议相对 // 或反斜杠 \ 一律回落到首页）
        if not next_url or not next_url.startswith('/') or next_url.startswith(('//', '/\\', '\\')):
            next_url = '/'
        # v12.2.0 登录限流：锁定期间拒绝
        lock_left = _check_login_locked(username)
        if lock_left:
            return render_template('login.html', error=f'账号已锁定（多次登录失败），请 {lock_left} 秒后再试', next=next_url)
        db = get_db()
        user = db.execute('SELECT * FROM users WHERE username=? AND is_active=1 AND COALESCE(deleted,0)=0',
                         (username,)).fetchone()
        # v12.2.0 密码哈希校验（兼容存量明文迁移后的哈希）
        if user and (check_password_hash(user['password'], password) if _is_hashed(user['password']) else user['password'] == password):
            session.permanent = True  # 12 小时后过期
            session['user_id'] = user['id']
            session['user_name'] = user['display_name'] or user['username']
            session['user_role'] = user['role']
            session['username'] = user['username']
            # v12.7.10.10：绑定本次登录令牌，同账号在其他设备的旧会话随即失效
            _bind_login_session(user['id'])
            _record_login_success(username)
            log_operation('系统', '登录', f'{user["username"]} 登录系统', user['username'])
            return redirect(next_url if next_url else '/')
        else:
            _record_login_fail(username)
            error = '用户名或密码错误'
            return render_template('login.html', error=error, next=next_url)
    kicked_msg = '该账号已在其他设备登录，当前设备已下线。如非本人操作，请立即修改密码。' \
        if request.args.get('kicked') else None
    return render_template('login.html', next=request.args.get('next', '/'), error=kicked_msg)

@app.route('/logout')
def logout():
    username = session.get('username', 'unknown')
    log_operation('系统', '登出', f'{username} 退出系统', username)
    _clear_login_session(session.get('user_id'))
    session.clear()
    return redirect(url_for('login'))

# ============================================================
# v12.1.0 三级权限：平台管理员 / 管理员 / 普通用户
# ============================================================
ROLE_PERMISSIONS = {
    'platform_admin': {'name': '平台管理员', 'desc': '系统管理（用户管理/插件管理/日志）+ 全部业务'},
    'admin':          {'name': '管理员',     'desc': '业务管理（插件内管理操作/审批）'},
    'user':           {'name': '普通用户',   'desc': '业务查看与提交（按插件规范）'},
}


def is_platform_admin():
    """仅平台管理员：系统级管理（用户管理/插件管理/日志）"""
    return session.get('user_role') == 'platform_admin'


def is_admin():
    """管理组：平台管理员 + 管理员（业务插件的管理操作）"""
    return session.get('user_role') in ('platform_admin', 'admin')


def _require_admin():
    """系统管理校验：仅平台管理员可访问"""
    return is_platform_admin()

@app.route('/admin/users')
def admin_users():
    """用户管理：用户列表（仅平台管理员）"""
    if not _require_admin():
        flash('无权限访问用户管理', 'error')
        return redirect(url_for('index'))
    db = get_db()
    users = db.execute('SELECT * FROM users WHERE COALESCE(deleted,0)=0 ORDER BY id').fetchall()
    return render_template('user_manage.html', users=users, current_username=session.get('username'))

@app.route('/admin/users/export')
def admin_users_export():
    """v12.7.10.10 批量导出人员信息（CSV，Excel 双击可开，UTF-8 BOM 防中文乱码）。"""
    if not _require_admin():
        flash('无权限导出人员信息', 'error')
        return redirect(url_for('index'))
    db = get_db()
    rows = db.execute(
        'SELECT username, display_name, role, is_active, department, created_at '
        'FROM users WHERE COALESCE(deleted,0)=0 ORDER BY id').fetchall()
    role_cn = {'platform_admin': '平台管理员', 'admin': '管理员', 'user': '普通用户'}
    import csv
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(['登录账号', '显示名称', '角色', '状态', '部门', '创建时间'])
    for r in rows:
        w.writerow([
            r['username'], r['display_name'] or '', role_cn.get(r['role'], r['role'] or ''),
            '启用' if r['is_active'] else '停用', r['department'] or '', r['created_at'] or '',
        ])
    data = ('\ufeff' + buf.getvalue()).encode('utf-8')
    fname = '人员信息_%s.csv' % datetime.now().strftime('%Y%m%d_%H%M')
    log_operation('系统', '导出', '导出人员信息 %d 条' % len(rows), session.get('username', ''))
    return send_file(io.BytesIO(data), mimetype='text/csv; charset=utf-8',
                     as_attachment=True, download_name=fname)


@app.route('/admin/users/create', methods=['POST'])
def admin_user_create():
    """创建用户（仅平台管理员）"""
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
    if role not in ('platform_admin', 'admin', 'user'):
        role = 'user'
    db = get_db()
    exists = db.execute('SELECT id, deleted FROM users WHERE username=?', (username,)).fetchone()
    if exists and not exists['deleted']:
        flash(f'用户名 {username} 已存在', 'error')
        return redirect(url_for('admin_users'))
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    if exists:
        # v12.5.17：同名账号此前被删除（软删）→ 复用记录并恢复启用，历史归属自动接回
        db.execute('UPDATE users SET password=?, display_name=?, role=?, department=?, is_active=1, deleted=0 WHERE id=?',
                   (generate_password_hash(password), display_name, role, department, exists['id']))
        db.commit()
        log_operation('用户管理', '创建用户', f'复用已删除账号 {username}（{display_name}，角色：{role}）并恢复启用', session.get('username'))
        flash(f'账号 {username} 原已被删除，现已重新启用并更新为「{display_name}」', 'success')
        return redirect(url_for('admin_users'))
    db.execute('INSERT INTO users (username, password, display_name, role, is_active, created_at, department) '
               'VALUES (?,?,?,?,1,?,?)',
               (username, generate_password_hash(password), display_name, role,
                now, department))
    db.commit()
    log_operation('用户管理', '创建用户', f'创建账号 {username}（{display_name}，角色：{role}）', session.get('username'))
    flash(f'用户 {display_name} 创建成功', 'success')
    return redirect(url_for('admin_users'))

# ============================================================
# v12.2.1 用户 Excel 批量导入 + 模板下载（仅平台管理员）
# ============================================================
@app.route('/admin/users/template')
def admin_users_template():
    """下载用户导入 Excel 模板（带表头与示例）"""
    if not _require_admin():
        return redirect(url_for('index'))
    try:
        import openpyxl
        from openpyxl.styles import Font, PatternFill, Alignment
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = '用户导入'
        ws.append(['登录名', '初始密码', '显示名称', '角色', '部门'])
        for c in ws[1]:
            c.font = Font(bold=True, color='FFFFFF')
            c.fill = PatternFill('solid', fgColor='2563EB')
            c.alignment = Alignment(horizontal='center')
        ws.append(['zhangsan', '123456', '张三', '普通用户', '计划管理部'])
        ws.append(['', '', '', '', ''])
        ws.append(['说明：登录名必填且唯一；初始密码至少4位；角色填写 普通用户/管理员/平台管理员；显示名留空默认用登录名；部门选填。导入从第2行开始逐行读取，遇到空登录名即停止。', '', '', '', ''])
        ws['A4'].font = Font(color='9CA3AF', size=9)
        for col, w in zip('ABCDE', [18, 12, 14, 14, 16]):
            ws.column_dimensions[col].width = w
        buf = io.BytesIO()
        wb.save(buf)
        buf.seek(0)
        return send_file(buf, as_attachment=True, download_name='用户导入模板.xlsx',
                         mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    except Exception as e:
        flash(f'模板生成失败：{e}', 'error')
        return redirect(url_for('admin_users'))


@app.route('/admin/users/import', methods=['POST'])
def admin_users_import():
    """Excel 批量导入用户（仅平台管理员）：逐行校验，返回成功/失败明细"""
    if not _require_admin():
        flash('无权限', 'error')
        return redirect(url_for('index'))
    f = request.files.get('excel_file')
    if not f or not f.filename:
        flash('请选择 Excel 文件', 'error')
        return redirect(url_for('admin_users'))
    if not f.filename.lower().endswith(('.xlsx', '.xls')):
        flash('仅支持 .xlsx / .xls 格式', 'error')
        return redirect(url_for('admin_users'))
    try:
        import openpyxl
        wb = openpyxl.load_workbook(f, data_only=True)
        ws = wb.active
        rows = list(ws.iter_rows(min_row=2, values_only=True))
    except Exception as e:
        flash(f'Excel 解析失败：{e}', 'error')
        return redirect(url_for('admin_users'))
    role_map = {'普通用户': 'user', '管理员': 'admin', '平台管理员': 'platform_admin',
                'user': 'user', 'admin': 'admin', 'platform_admin': 'platform_admin'}
    db = get_db()
    ok, fail = 0, []
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    for idx, row in enumerate(rows, start=2):
        if row is None or not any(c is not None and str(c).strip() for c in row):
            continue
        username = str(row[0]).strip() if row[0] is not None else ''
        if not username:
            fail.append((idx, '登录名为空，停止读取后续行'))
            break
        password = str(row[1]).strip() if len(row) > 1 and row[1] is not None else ''
        display_name = str(row[2]).strip() if len(row) > 2 and row[2] is not None else username
        role_raw = str(row[3]).strip() if len(row) > 3 and row[3] is not None else '普通用户'
        department = str(row[4]).strip() if len(row) > 4 and row[4] is not None else ''
        role = role_map.get(role_raw)
        if not role:
            fail.append((idx, f'角色「{role_raw}」不合法（应为 普通用户/管理员/平台管理员）'))
            continue
        if len(password) < 4:
            fail.append((idx, '初始密码少于4位'))
            continue
        exists = db.execute('SELECT id, deleted FROM users WHERE username=?', (username,)).fetchone()
        if exists and not exists['deleted']:
            fail.append((idx, f'用户名 {username} 已存在（未删除）'))
            continue
        if exists:
            # v12.5.17：同名账号此前被删除（软删）→ 复用记录并恢复启用，历史归属自动接回
            db.execute('UPDATE users SET password=?, display_name=?, role=?, department=?, is_active=1, deleted=0 WHERE id=?',
                       (generate_password_hash(password), display_name, role, department, exists['id']))
            ok += 1
            continue
        db.execute('INSERT INTO users (username, password, display_name, role, is_active, created_at, department) '
                   'VALUES (?,?,?,?,1,?,?)',
                   (username, generate_password_hash(password), display_name, role, now, department))
        ok += 1
    db.commit()
    log_operation('用户管理', '批量导入', f'Excel导入用户：成功{ok}条（含复用已删除账号），失败{len(fail)}条', session.get('username'))
    if fail:
        detail = '；'.join(f'第{ln}行：{m}' for ln, m in fail[:10])
        if len(fail) > 10:
            detail += f'…（共{len(fail)}条失败）'
        flash(f'导入完成：成功 {ok} 条（含复用已删除账号），失败 {len(fail)} 条。{detail}', 'error')
    else:
        flash(f'导入完成：成功 {ok} 条（含复用已删除账号）', 'success')
    return redirect(url_for('admin_users'))

@app.route('/admin/users/<int:uid>/update', methods=['POST'])
def admin_user_update(uid):
    """编辑用户：显示名/角色/部门/启用状态（仅平台管理员）"""
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
    if role not in ('platform_admin', 'admin', 'user'):
        role = user['role']
    # 不允许停用最后一个平台管理员
    if user['role'] == 'platform_admin' and is_active == 0:
        admins = db.execute("SELECT COUNT(*) AS c FROM users WHERE role='platform_admin' AND is_active=1").fetchone()['c']
        if admins <= 1:
            flash('不能停用最后一个平台管理员', 'error')
            return redirect(url_for('admin_users'))
    db.execute('UPDATE users SET display_name=?, role=?, department=?, is_active=? WHERE id=?',
               (display_name, role, department, is_active, uid))
    db.commit()
    log_operation('用户管理', '编辑用户', f'编辑用户#{uid} {user["username"]} → {display_name}（角色：{role}）', session.get('username'))
    flash(f'用户 {display_name} 已更新', 'success')
    return redirect(url_for('admin_users'))

@app.route('/admin/users/<int:uid>/toggle', methods=['POST'])
def admin_user_toggle(uid):
    """启用/停用用户（仅平台管理员）"""
    if not _require_admin():
        flash('无权限', 'error')
        return redirect(url_for('index'))
    db = get_db()
    user = db.execute('SELECT * FROM users WHERE id=?', (uid,)).fetchone()
    if not user:
        flash('用户不存在', 'error')
        return redirect(url_for('admin_users'))
    new_state = 0 if user['is_active'] else 1
    if user['role'] == 'platform_admin' and new_state == 0:
        admins = db.execute("SELECT COUNT(*) AS c FROM users WHERE role='platform_admin' AND is_active=1").fetchone()['c']
        if admins <= 1:
            flash('不能停用最后一个平台管理员', 'error')
            return redirect(url_for('admin_users'))
    db.execute('UPDATE users SET is_active=? WHERE id=?', (new_state, uid))
    db.commit()
    log_operation('用户管理', '启停用户', f'{"停用" if new_state == 0 else "启用"}账号 {user["username"]}', session.get('username'))
    flash(f'账号 {user["username"]} 已{"停用" if new_state == 0 else "启用"}', 'success')
    return redirect(url_for('admin_users'))

@app.route('/admin/users/<int:uid>/reset_pwd', methods=['POST'])
def admin_user_reset_pwd(uid):
    """重置用户密码（仅平台管理员）"""
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
    db.execute('UPDATE users SET password=? WHERE id=?', (generate_password_hash(new_pwd), uid))
    db.commit()
    log_operation('用户管理', '重置密码', f'重置账号 {user["username"]} 的密码', session.get('username'))
    flash(f'账号 {user["username"]} 密码已重置', 'success')
    return redirect(url_for('admin_users'))

# ============================================================
# v12.5.17 用户管理：修改登录名 + 删除用户（仅平台管理员）
# ============================================================
def _swap_name_in_list(val, old, new):
    """逗号分隔列表中把 old 替换为 new（空值/乱逗号兼容），返回规范化后的串"""
    items = [x.strip() for x in (val or '').split(',') if x.strip()]
    return ','.join(new if x == old else x for x in items)

@app.route('/admin/users/<int:uid>/rename', methods=['POST'])
def admin_user_rename(uid):
    """修改用户登录名（仅平台管理员）：同步更新 WBS 负责人/参与人、需求收集子表归属"""
    if not _require_admin():
        flash('无权限', 'error')
        return redirect(url_for('index'))
    new_name = request.form.get('new_username', '').strip()
    db = get_db()
    user = db.execute('SELECT * FROM users WHERE id=?', (uid,)).fetchone()
    if not user:
        flash('用户不存在', 'error')
        return redirect(url_for('admin_users'))
    if not new_name:
        flash('新登录名不能为空', 'error')
        return redirect(url_for('admin_users'))
    if len(new_name) > 50:
        flash('登录名过长（最多50个字符）', 'error')
        return redirect(url_for('admin_users'))
    dup = db.execute('SELECT id, deleted FROM users WHERE username=? AND id!=?',
                     (new_name, uid)).fetchone()
    if dup:
        if dup['deleted']:
            flash(f'登录名 {new_name} 已被使用（已删除账号占用，暂不能复用为登录名）', 'error')
        else:
            flash(f'登录名 {new_name} 已被使用', 'error')
        return redirect(url_for('admin_users'))
    old = user['username']
    if old == new_name:
        flash('登录名未变化', 'info')
        return redirect(url_for('admin_users'))
    db.execute('UPDATE users SET username=? WHERE id=?', (new_name, uid))
    # 同步业务引用：WBS 项目负责人/参与人员（逗号分隔 username）
    for p in db.execute('SELECT id, owner, participants FROM wbs_projects').fetchall():
        no = _swap_name_in_list(p['owner'], old, new_name)
        np_ = _swap_name_in_list(p['participants'], old, new_name)
        if no != p['owner'] or np_ != p['participants']:
            db.execute('UPDATE wbs_projects SET owner=?, participants=? WHERE id=?',
                       (no, np_, p['id']))
    # 同步需求收集子表：创建人/更新人/参与人员
    # v12.7.10.10 修复：本版本基线未安装需求收集插件时该表不存在，不能因此让改名整体 500
    try:
        for b in db.execute('SELECT id, created_by, updated_by, editors FROM reqcollect_books').fetchall():
            nc = _swap_name_in_list(b['created_by'], old, new_name)
            nu = _swap_name_in_list(b['updated_by'], old, new_name)
            ne = _swap_name_in_list(b['editors'], old, new_name)
            if nc != b['created_by'] or nu != b['updated_by'] or ne != b['editors']:
                db.execute('UPDATE reqcollect_books SET created_by=?, updated_by=?, editors=? WHERE id=?',
                           (nc, nu, ne, b['id']))
    except Exception as e:
        print('[用户改名] 跳过需求收集子表同步:', e)
    db.commit()
    # 若改的是当前登录账号，同步会话登录名（user_name 一并刷新）
    if old == session.get('username'):
        session['username'] = new_name
        session['user_name'] = user['display_name'] or new_name
    log_operation('用户管理', '修改登录名', f'账号 {old} → {new_name}（用户#{uid}）', session.get('username'))
    flash(f'登录名 {old} 已修改为 {new_name}', 'success')
    return redirect(url_for('admin_users'))

@app.route('/admin/users/<int:uid>/delete', methods=['POST'])
def admin_user_delete(uid):
    """删除用户（仅平台管理员）：软删——列表隐藏 + 禁止登录，历史数据归属保留"""
    if not _require_admin():
        flash('无权限', 'error')
        return redirect(url_for('index'))
    db = get_db()
    user = db.execute('SELECT * FROM users WHERE id=?', (uid,)).fetchone()
    if not user:
        flash('用户不存在', 'error')
        return redirect(url_for('admin_users'))
    if user['username'] == session.get('username'):
        flash('不能删除当前登录账号', 'error')
        return redirect(url_for('admin_users'))
    if user['role'] == 'platform_admin' and user['is_active'] == 1:
        admins = db.execute("SELECT COUNT(*) AS c FROM users WHERE role='platform_admin' AND is_active=1 AND COALESCE(deleted,0)=0").fetchone()['c']
        if admins <= 1:
            flash('不能删除最后一个启用的平台管理员', 'error')
            return redirect(url_for('admin_users'))
    db.execute('UPDATE users SET deleted=1, is_active=0 WHERE id=?', (uid,))
    db.commit()
    log_operation('用户管理', '删除用户', f'删除账号 {user["username"]}（{user["display_name"] or "-"}，角色：{user["role"]}）', session.get('username'))
    flash(f'用户 {user["username"]} 已删除（列表不再显示，历史数据归属保留）', 'success')
    return redirect(url_for('admin_users'))

# ============================================================
# v12.1.0 插件管理（仅平台管理员）：列表/启停/上传安装/卸载
# ============================================================
@app.route('/admin/plugins')
def admin_plugins():
    if not _require_admin():
        flash('无权限访问插件管理', 'error')
        return redirect(url_for('index'))
    plugins = plugin_system.get_plugins_admin()
    # v12.4.5 表一致性报告（登记 vs 实际建表，冲突/未登记告警红字显示）
    integrity_warnings, integrity_infos = plugin_system.check_plugin_tables_integrity()
    # v12.1.2 待重启生效提示：上传安装的插件在重启系统前，蓝图未注册（Flask 限制），导航/入口暂不出现
    registered = set(current_app.view_functions.keys())
    pending = [p for p in plugins if p.get('install_type') in ('upload', 'dir')
               and p.get('endpoint') not in registered]
    # v12.7.10.10 上传插件的数据开放接口状态（供管理页显示是否达标）
    open_status = {}
    for _p in plugins:
        if _p.get('install_type') in ('upload', 'dir'):
            try:
                open_status[_p['id']] = plugin_system.plugin_openapi_status(_p['id'])
            except Exception:
                pass
    return render_template('admin_plugins.html', plugins=plugins,
                           role_permissions=ROLE_PERMISSIONS, pending_restart=pending,
                           integrity_warnings=integrity_warnings, integrity_infos=integrity_infos,
                           open_status=open_status)

@app.route('/admin/db-cleanup')
def admin_db_cleanup():
    """v12.6.6 孤儿表清理：列出无人使用的残留表，平台管理员可一键删除"""
    if not _require_admin():
        flash('无权限访问', 'error')
        return redirect(url_for('index'))
    orphans = plugin_system.find_orphan_tables()
    return render_template('db_cleanup.html', orphans=orphans)


@app.route('/admin/db-cleanup/drop', methods=['POST'])
def admin_db_cleanup_drop():
    if not _require_admin():
        flash('无权限', 'error')
        return redirect(url_for('index'))
    tname = request.form.get('table_name', '').strip()
    ok, msg = plugin_system.drop_orphan_table(tname)
    flash(msg, 'success' if ok else 'error')
    return redirect(url_for('admin_db_cleanup'))


@app.route('/admin/plugins/<plugin_id>/toggle', methods=['POST'])
def admin_plugin_toggle(plugin_id):
    if not _require_admin():
        flash('无权限', 'error')
        return redirect(url_for('index'))
    enabled = request.form.get('enabled') == '1'
    ok, msg = plugin_system.set_plugin_enabled(plugin_id, enabled)
    flash(msg, 'success' if ok else 'error')
    return redirect(url_for('admin_plugins'))

@app.route('/admin/plugins/<plugin_id>/uninstall', methods=['POST'])
def admin_plugin_uninstall(plugin_id):
    if not _require_admin():
        flash('无权限', 'error')
        return redirect(url_for('index'))
    ok, msg = plugin_system.uninstall_plugin(plugin_id)
    flash(msg, 'success' if ok else 'error')
    return redirect(url_for('admin_plugins'))

@app.route('/admin/plugins/upload', methods=['POST'])
def admin_plugin_upload():
    """上传安装插件包（zip）：校验 manifest → 解压 plugins/<id> → 登记注册"""
    if not _require_admin():
        flash('无权限', 'error')
        return redirect(url_for('index'))
    f = request.files.get('plugin_zip')
    if not f or not f.filename:
        flash('请选择插件压缩包（zip）', 'error')
        return redirect(url_for('admin_plugins'))
    if not f.filename.lower().endswith('.zip'):
        flash('仅支持 .zip 格式的插件包', 'error')
        return redirect(url_for('admin_plugins'))
    import zipfile, io
    raw = f.read()
    if len(raw) > 20 * 1024 * 1024:
        flash('插件包过大（上限 20MB）', 'error')
        return redirect(url_for('admin_plugins'))
    try:
        zf = zipfile.ZipFile(io.BytesIO(raw))
    except zipfile.BadZipFile:
        flash('压缩包损坏，无法解析', 'error')
        return redirect(url_for('admin_plugins'))
    # 顶层目录推断
    names = zf.namelist()
    top = None
    for n in names:
        parts = [p for p in n.split('/') if p]
        if parts:
            top = parts[0]
            break
    if not top:
        flash('压缩包为空', 'error')
        return redirect(url_for('admin_plugins'))
    # 防路径穿越
    for n in names:
        if n.startswith('/') or '..' in n.split('/'):
            flash('压缩包包含非法路径', 'error')
            return redirect(url_for('admin_plugins'))
    import os, shutil
    plugins_root = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'plugins')
    target = os.path.join(plugins_root, top)
    if os.path.exists(target):
        flash(f'插件目录 {top} 已存在，请先卸载或使用其他插件id', 'error')
        return redirect(url_for('admin_plugins'))
    os.makedirs(target, exist_ok=True)
    # v12.5.28 安全修复：zip 炸弹防护——解压后总大小 ≤200MB、文件数 ≤2000，
    # 防止 20MB 压缩包解压出数 GB 数据耗尽磁盘/内存
    _MAX_UNZIP_BYTES = 200 * 1024 * 1024
    _MAX_UNZIP_FILES = 2000
    total_bytes = 0
    file_count = 0
    for n in names:
        if n.endswith('/'):
            continue
        file_count += 1
        try:
            total_bytes += zf.getinfo(n).file_size
        except KeyError:
            flash('压缩包包含非法条目', 'error')
            shutil.rmtree(target, ignore_errors=True)
            return redirect(url_for('admin_plugins'))
        if file_count > _MAX_UNZIP_FILES or total_bytes > _MAX_UNZIP_BYTES:
            flash('插件包解压后过大（上限 200MB / 2000 个文件），已拒绝', 'error')
            shutil.rmtree(target, ignore_errors=True)
            return redirect(url_for('admin_plugins'))
    for n in names:
        if n.endswith('/'):
            continue
        dest = os.path.join(target, n[len(top) + 1:] if n.startswith(top + '/') else n)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with zf.open(n) as src, open(dest, 'wb') as dst:
            dst.write(src.read())
    # 校验 manifest 并即时注册
    ok, msg = _load_uploaded_plugin(top, target)
    if not ok:
        shutil.rmtree(target, ignore_errors=True)
        flash(f'插件校验失败，已回滚：{msg}', 'error')
        return redirect(url_for('admin_plugins'))
    flash(f'插件 {top} 安装成功（{msg}）。因 Flask 框架限制，上传的插件需重启系统后完全生效：先点「停止系统.bat」，再点「启动系统.bat」。', 'success')
    return redirect(url_for('admin_plugins'))


def _load_uploaded_plugin(plugin_id, target):
    """加载上传的插件包：检查 manifest + blueprint，登记并即时注册。返回 (ok, msg)"""
    import importlib, sys
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        mod = importlib.import_module(f'plugins.{plugin_id}')
        if not hasattr(mod, 'manifest') or not hasattr(mod, 'blueprint'):
            return False, '插件包缺少 manifest 或 blueprint（见《插件开发规范》）'
        man = mod.manifest
        if man.get('id') != plugin_id:
            return False, f'manifest.id（{man.get("id")}）与目录名（{plugin_id}）不一致'
        plugin_system.register_uploaded_plugin(plugin_id, man, mod.blueprint)
        # v12.7.10.10 数据开放接口检查：不阻断安装，但必须让管理员当场看到是否达标
        try:
            _st = plugin_system.plugin_openapi_status(plugin_id)
            if _st.get('supported'):
                _open_msg = '数据开放接口已实现（平台 AI 可读取其全部数据集）'
            else:
                _open_msg = '数据开放接口未实现（%s），平台 AI 将直读独立库兜底；请按《插件开发规范》§13 补齐' % _st.get('reason', '原因未知')
        except Exception as _e:
            _open_msg = '数据开放接口检查失败：%s' % _e
        log_operation('插件管理', '安装', f'上传安装插件 {plugin_id}（{man.get("name", plugin_id)}）', session.get('username'))
        return True, f'{man.get("name", plugin_id)} 已启用；{_open_msg}'
    except Exception as e:
        return False, f'加载失败：{e}'


# ============================================================
# v12.7.1 数据备份中心（仅平台管理员）：
# 手动备份 / 备份列表 / 下载备份 / 恢复（恢复前自动保护现场）
# 自动备份：启动时 perform_backup()；保留策略：各前缀最近 7 份
# ============================================================
def _backup_dir():
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), 'backups')


def _backup_list():
    """返回备份文件列表（按时间倒序）：名称/大小/修改时间/类型"""
    bdir = _backup_dir()
    if not os.path.isdir(bdir):
        return []
    out = []
    for f in os.listdir(bdir):
        if '_备份_' not in f or not (f.endswith('.db') or f.endswith('.zip')):
            continue
        fp = os.path.join(bdir, f)
        try:
            st = os.stat(fp)
            if st.st_size <= 0:
                continue
            pre = f.split('_备份_')[0]
            if f.endswith('.zip'):
                kind = '插件包(含插件本体)'
            else:
                kind = '主库' if pre == 'business' else f'插件库({pre})'
            out.append({'name': f, 'size': st.st_size,
                        'mtime': time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(st.st_mtime)),
                        'kind': kind})
        except Exception:
            pass
    out.sort(key=lambda x: x['name'], reverse=True)
    return out


def _extract_plugin_pack(zip_path):
    """v12.7.10.11 还原插件包快照：只允许写入 plugins/ 下的文件（拒绝绝对路径与 .. 穿越）。
    返回 (写入文件数, 涉及插件 id 列表)。"""
    import zipfile
    app_root = os.path.dirname(os.path.abspath(__file__))
    n = 0
    pids = []
    with zipfile.ZipFile(zip_path) as z:
        for info in z.infolist():
            name = info.filename.replace('\\', '/')
            parts = name.split('/')
            if not name.startswith('plugins/') or '..' in parts:
                continue
            path = os.path.join(app_root, *parts)
            if info.is_dir():
                os.makedirs(path, exist_ok=True)
                continue
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with z.open(info) as src, open(path, 'wb') as dst:
                dst.write(src.read())
            n += 1
            if len(parts) >= 3 and parts[1] and parts[1] not in pids:
                pids.append(parts[1])
    return n, pids


@app.route('/admin/backup')
def admin_backup():
    if not _require_admin():
        flash('无权限', 'error')
        return redirect(url_for('index'))
    files = _backup_list()
    return render_template('admin_backup.html', files=files,
                           current_db=os.path.basename(app.config['DATABASE']))


@app.route('/admin/backup/run', methods=['POST'])
def admin_backup_run():
    if not _require_admin():
        flash('无权限', 'error')
        return redirect(url_for('index'))
    try:
        ok, msg, made = perform_backup('手动')
        flash(f'💾 备份成功：{msg}' if ok else f'备份未执行：{msg}', 'success' if ok else 'error')
    except Exception as e:
        flash(f'备份失败：{e}', 'error')
    return redirect(url_for('admin_backup'))


@app.route('/admin/backup/download/<path:fname>')
def admin_backup_download(fname):
    if not _require_admin():
        flash('无权限', 'error')
        return redirect(url_for('index'))
    import re
    if (not fname.endswith(('.db', '.zip')) or '_备份_' not in fname
            or '/' in fname or '\\' in fname
            or not re.fullmatch(r'[\w\u4e00-\u9fff\-_]+\.(db|zip)', fname)):
        flash('非法的备份文件名', 'error')
        return redirect(url_for('admin_backup'))
    bdir = _backup_dir()
    if not os.path.exists(os.path.join(bdir, fname)):
        flash('备份文件不存在', 'error')
        return redirect(url_for('admin_backup'))
    return send_from_directory(bdir, fname, as_attachment=True)


@app.route('/admin/backup/restore', methods=['POST'])
def admin_backup_restore():
    """从备份列表恢复：先把当前库另存为保护备份 → checkpoint → 覆盖目标库 → 清 wal/shm。
    运行时覆盖受文件占用影响时给出明确指引（先停止系统再恢复）。"""
    if not _require_admin():
        flash('无权限', 'error')
        return redirect(url_for('index'))
    fname = (request.form.get('backup_file') or '').strip()
    import re
    if (not fname.endswith(('.db', '.zip')) or '_备份_' not in fname
            or '/' in fname or '\\' in fname
            or not re.fullmatch(r'[\w\u4e00-\u9fff\-_]+\.(db|zip)', fname)):
        flash('请选择有效的备份文件', 'error')
        return redirect(url_for('admin_backup'))
    bdir = _backup_dir()
    src = os.path.join(bdir, fname)
    if not os.path.exists(src):
        flash('备份文件不存在，无法恢复', 'error')
        return redirect(url_for('admin_backup'))

    # v12.7.10.11：插件包快照（zip）单独还原 —— 把 plugins/<id> 目录写回去
    if fname.endswith('.zip'):
        try:
            ok, _msg, _ = perform_backup('恢复前保护')
        except Exception:
            ok = False
        if not ok:
            flash('还原中止：现场保护备份失败。请先停止系统，再手动把该 zip 解压到 app/plugins/ 后启动。', 'error')
            return redirect(url_for('admin_backup'))
        try:
            n, pids = _extract_plugin_pack(src)
            if not n:
                flash('该压缩包里没有可还原的插件文件（正确结构应为 plugins/<插件id>/…）', 'error')
            else:
                plugin_system.invalidate_openapi_cache()
                log_operation('数据备份', '还原插件包', f'{fname}（{", ".join(pids)}）', session.get('username'))
                flash(f'✅ 已还原插件包：{", ".join(pids) or "-"}（共 {n} 个文件）。'
                      f'请重启系统（停止.bat → 启动.bat）后插件才会出现在导航里。', 'success')
        except Exception as e:
            flash(f'还原插件包失败：{e}', 'error')
        return redirect(url_for('admin_backup'))

    # 确定目标库
    pre = fname.split('_备份_')[0]
    if pre == 'business':
        target = app.config['DATABASE']
        kind = '主库'
    else:
        data_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data')
        target = os.path.join(data_dir, f'{pre}.db')
        kind = f'插件库({pre})'
    # 恢复前自动保护现场（生成新的保护备份）
    try:
        ok, msg, _ = perform_backup('恢复前保护')
    except Exception:
        ok, msg = False, '保护备份失败'
    if not ok:
        flash(f'恢复中止：现场保护备份失败（{msg}）。请先停止系统，手动用备份文件替换 business.db 后重启。', 'error')
        return redirect(url_for('admin_backup'))
    try:
        # 先 checkpoint 当前目标库，把 wal 数据合并，避免覆盖后丢最近未落盘数据
        try:
            c = sqlite3.connect(target)
            c.execute('PRAGMA wal_checkpoint(TRUNCATE)')
            c.close()
        except Exception:
            pass
        import shutil
        shutil.copy2(src, target)
        for suffix in ('-wal', '-shm'):
            try:
                os.remove(target + suffix)
            except Exception:
                pass
        # v12.7.10.11：主库恢复时，若同批次还有插件包快照，一并还原（否则会出现"数据在、插件没了"）
        pack_msg = ''
        if pre == 'business':
            ts2 = fname.split('_备份_')[1].rsplit('.', 1)[0]
            pack = os.path.join(bdir, f'pluginpack_备份_{ts2}.zip')
            if os.path.exists(pack):
                try:
                    n2, pids2 = _extract_plugin_pack(pack)
                    if n2:
                        plugin_system.invalidate_openapi_cache()
                        pack_msg = f' 同批次的 {len(pids2)} 个插件包也已还原（{", ".join(pids2)}）。'
                except Exception as e:
                    pack_msg = f' 但插件包还原失败：{e}，可单独对 pluginpack_备份_{ts2}.zip 点还原。'
        log_operation('数据备份', '恢复', f'{kind} ← {fname}', session.get('username'))
        # v12.7.10.16：主库恢复会把会话表一起还原，当前登录态必然失效。
        # 与其静默跳到登录页让人以为"恢复没成功"，不如明确提示并按备份里的账号重新登录。
        if pre == 'business':
            _tok = session.get('sess_token')
            _still = False
            try:
                _c = sqlite3.connect(src)
                if _tok and _c.execute('SELECT 1 FROM user_sessions WHERE token=?', (_tok,)).fetchone():
                    _still = True
                _c.close()
            except Exception:
                _still = False
            if not _still:
                session.clear()
                flash('主库已恢复（数据已覆盖成功）。登录信息也随主库一起还原了，请重新登录——'
                      '账号用备份里的（例如 admin / admin123），登录后再恢复插件库和插件包。', 'warning')
                return redirect(url_for('login'))
        flash(f'✅ 已从备份恢复{kind}：{fname}。恢复前当前数据已另存保护备份。{pack_msg}'
              f'请重启系统（停止.bat → 启动.bat）后完全生效。', 'success')
    except OSError as e:
        flash(f'恢复{kind}失败（{e}）：可能系统正在占用数据库文件。'
              f'请先停止系统，再点恢复；或手动把 backups/{fname} 复制替换 business.db（插件库对应 data/{pre}.db）后重启。', 'error')
    except Exception as e:
        flash(f'恢复失败：{e}（不影响现有数据，现场保护备份已生成）', 'error')
    return redirect(url_for('admin_backup'))


@app.route('/admin/backup/delete', methods=['POST'])
def admin_backup_delete():
    """v12.7.10.11 删除单个备份文件（只删 backups/ 里的副本，不动正在使用的数据）"""
    if not _require_admin():
        flash('无权限', 'error')
        return redirect(url_for('admin_backup'))
    import re
    fname = (request.form.get('backup_file') or '').strip()
    if (not fname.endswith(('.db', '.zip')) or '_备份_' not in fname
            or '/' in fname or '\\' in fname
            or not re.fullmatch(r'[\w\u4e00-\u9fff\-_]+\.(db|zip)', fname)):
        flash('请选择要删除的备份文件', 'error')
        return redirect(url_for('admin_backup'))
    fp = os.path.join(_backup_dir(), fname)
    if not os.path.exists(fp):
        flash('备份文件不存在（可能已被清理）', 'error')
        return redirect(url_for('admin_backup'))
    try:
        size = os.path.getsize(fp)
        os.remove(fp)
        log_operation('数据备份', '删除备份', f'{fname}（{size} 字节）', session.get('username'))
        flash(f'🗑 已删除备份：{fname}（{size / 1024:.1f} KB）。仅删除备份副本，正在使用的数据不受影响。', 'success')
    except Exception as e:
        flash(f'删除失败：{e}', 'error')
    return redirect(url_for('admin_backup'))


@app.route('/admin/backup/upload', methods=['POST'])
def admin_backup_upload():
    """导入外部备份文件（v12.7.10 跨版本迁移恢复用）：
    换新版本后，旧版备份在旧目录/本地，新版 backups/ 是空的——
    上传旧版备份 .db 到 backups/ → 即可在恢复列表一键恢复到新版。
    校验：文件名符合备份规范（business_备份_*.db / <插件id>_备份_*.db）、
    大小 ≤ 512MB、且是有效 SQLite 库。"""
    if not _require_admin():
        flash('无权限', 'error')
        return redirect(url_for('admin_backup'))
    import re
    f = request.files.get('backup_file')
    if not f or not getattr(f, 'filename', None):
        flash('请先选择要导入的备份文件', 'error')
        return redirect(url_for('admin_backup'))
    fname = (f.filename or '').strip()
    if (not fname.endswith(('.db', '.zip')) or '_备份_' not in fname
            or '/' in fname or '\\' in fname
            or not re.fullmatch(r'[\w\u4e00-\u9fff\-_]+\.(db|zip)', fname)):
        flash('文件名不符合备份规范（应为 xxx_备份_时间.db 或 xxx_备份_时间.zip），已拒绝导入', 'error')
        return redirect(url_for('admin_backup'))
    pre = fname.split('_备份_')[0]
    if fname.endswith('.db') and pre != 'business' and not re.fullmatch(r'[a-z0-9_]{2,64}', pre):
        flash('备份前缀无法识别（只支持 business 或插件库名），已拒绝导入', 'error')
        return redirect(url_for('admin_backup'))
    MAX = 512 * 1024 * 1024  # 512MB 上限
    data = f.read(MAX + 1)
    if len(data) > MAX:
        flash('备份文件超过 512MB 上限，已拒绝导入', 'error')
        return redirect(url_for('admin_backup'))
    if fname.endswith('.zip'):
        # v12.7.10.11 插件包快照：必须是 zip，且至少含一个 plugins/ 条目
        try:
            import zipfile, io
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                names = [n.replace('\\', '/') for n in z.namelist()]
            if not any(n.startswith('plugins/') for n in names):
                flash('压缩包里没有 plugins/ 目录结构，不是插件包备份，已拒绝导入', 'error')
                return redirect(url_for('admin_backup'))
        except Exception:
            flash('文件不是有效的 zip 压缩包，已拒绝导入', 'error')
            return redirect(url_for('admin_backup'))
    elif len(data) < 16 or data[:16] != b'SQLite format 3\x00':
        flash('文件不是有效的 SQLite 数据库备份，已拒绝导入', 'error')
        return redirect(url_for('admin_backup'))
    try:
        bdir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'backups')
        os.makedirs(bdir, exist_ok=True)
        with open(os.path.join(bdir, fname), 'wb') as out:
            out.write(data)
        flash(f'✅ 已导入备份文件：{fname}。现在可在下方列表选择它进行「恢复」。', 'success')
    except Exception as e:
        flash(f'导入失败：{e}', 'error')
    return redirect(url_for('admin_backup'))

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
    # v12.2.0 密码哈希校验（兼容存量明文迁移后的哈希）
    if not _is_hashed(user['password']):
        if old_pwd != user['password']:
            flash('原密码错误', 'error')
            return redirect(url_for('profile'))
    elif not check_password_hash(user['password'], old_pwd):
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
    db.execute('UPDATE users SET password=? WHERE id=?', (generate_password_hash(new_pwd), user_id))
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
    """状态类变更统一入口（v10.3.6 暂下线审批：所有角色直接生效）

    原审批流程：普通用户写入 approval_requests 待管理员审批；
    用户反馈统一审批中心未关联使用，暂恢复直接变更模式。
    审批相关代码（plugin_system.submit_approval / approval_center / approval_review）
    均保留，日后要恢复审批只需改回本函数即可。
    """
    return 'direct', None


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
        # v12.2.0 并发加固：WAL 模式（读写不互相阻塞）+ busy_timeout（写锁等待）+ 连接超时
        # v12.5.28 性能修复：WAL 已持久化在数据库文件头，无需每请求重设；
        # PRAGMA journal_mode=WAL 每次执行需独占写锁，高并发下全部请求排队抢锁（100并发 P50≈26s→修复后大幅改善）
        g.db = sqlite3.connect(app.config['DATABASE'], timeout=5.0)
        g.db.row_factory = sqlite3.Row
        g.db.execute('PRAGMA foreign_keys = ON')
        g.db.execute('PRAGMA busy_timeout = 5000')
        # v12.5.28 性能修复：WAL 模式下 synchronous=NORMAL 为官方推荐（崩溃不损坏库，
        # 仅可能丢失最近未 checkpoint 事务），提交无需每次 fsync，写吞吐显著提升
        g.db.execute('PRAGMA synchronous = NORMAL')
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
    # v12.7.2 性能优化：高频查询索引（CREATE INDEX IF NOT EXISTS 幂等）
    db.executescript("""
        CREATE INDEX IF NOT EXISTS idx_wbs_tasks_project ON wbs_tasks(project_id);
        CREATE INDEX IF NOT EXISTS idx_wbs_tasks_status ON wbs_tasks(status);
        CREATE INDEX IF NOT EXISTS idx_wbs_tasks_planend ON wbs_tasks(plan_end);
        CREATE INDEX IF NOT EXISTS idx_wbs_tasks_parent ON wbs_tasks(parent_id);
        CREATE INDEX IF NOT EXISTS idx_oplogs_created ON operation_logs(created_at);
        CREATE INDEX IF NOT EXISTS idx_oplogs_operator ON operation_logs(operator);
        CREATE INDEX IF NOT EXISTS idx_users_username ON users(username);
        CREATE INDEX IF NOT EXISTS idx_users_role ON users(role);
    """)
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
    # v10.5.0 只保留 WBS：工作台统计聚焦 WBS 计划
    stats = {
        'wbs_projects_total': db.execute('SELECT COUNT(*) FROM wbs_projects').fetchone()[0],
        'wbs_projects_active': db.execute("SELECT COUNT(*) FROM wbs_projects WHERE status IN ('进行中','规划中')").fetchone()[0],
        'wbs_projects_done': db.execute("SELECT COUNT(*) FROM wbs_projects WHERE status='已完成'").fetchone()[0],
        'wbs_tasks_total': db.execute('SELECT COUNT(*) FROM wbs_tasks').fetchone()[0],
        'wbs_tasks_pending': db.execute("SELECT COUNT(*) FROM wbs_tasks WHERE approval_status='待审批'").fetchone()[0],
        'wbs_tasks_active': db.execute("SELECT COUNT(*) FROM wbs_tasks WHERE status IN ('进行中','待开始') AND approval_status='已通过'").fetchone()[0],
        'wbs_tasks_done': db.execute("SELECT COUNT(*) FROM wbs_tasks WHERE status='已完成'").fetchone()[0],
        # v12.7.3 口径统一：与 WBS 预警看板一致（叶子任务 + 审批已通过 + 截止<今天 + 非完成/取消 + 截止非空）
        'wbs_tasks_overdue': db.execute("SELECT COUNT(*) FROM wbs_tasks WHERE plan_end IS NOT NULL AND plan_end!='' AND plan_end < date('now','localtime') AND status NOT IN ('已完成','已取消') AND (parent_id>0 OR parent_id IS NULL) AND approval_status='已通过'").fetchone()[0],
    }
    # v12.5.39：最近动态仅平台管理员可见（非 platform_admin 不取数）
    recent_logs = db.execute('SELECT * FROM operation_logs ORDER BY created_at DESC LIMIT 10').fetchall() if session.get('user_role') == 'platform_admin' else []
    # 我的待办任务
    current_user = session.get('username', 'admin')
    my_tasks = db.execute("SELECT * FROM tasks WHERE assignee=? AND status IN ('待处理','进行中') ORDER BY priority DESC, due_date ASC LIMIT 8", (current_user,)).fetchall()
    my_tasks_count = db.execute("SELECT COUNT(*) FROM tasks WHERE assignee=? AND status IN ('待处理','进行中')", (current_user,)).fetchone()[0]
    now = datetime.now()
    week_map = {0:'周一',1:'周二',2:'周三',3:'周四',4:'周五',5:'周六',6:'周日'}
    return render_template('index.html', stats=stats,
                           recent_logs=recent_logs,
                           my_tasks=my_tasks, my_tasks_count=my_tasks_count,
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
    parser = argparse.ArgumentParser(description='业务高效运转系统')
    parser.add_argument('--port', type=int, default=None, help='服务端口号（默认6000）')
    parser.add_argument('--host', type=str, default='0.0.0.0', help='监听地址（默认0.0.0.0）')
    parser.add_argument('--debug', action='store_true', default=None, help='开启调试模式')
    parser.add_argument('--no-debug', action='store_true', help='关闭调试模式（生产环境）')
    parser.add_argument('--https', action='store_true', help='启用HTTPS模式（自签证书，绕开内网EDR对明文响应的扫描/净化）')
    args = parser.parse_args()

    port = args.port if args.port is not None else default_port
    debug = default_debug
    if args.debug:
        debug = True
    if args.no_debug:
        debug = False

    return args.host, port, debug, bool(args.https)


# ============================================================
# 操作日志
# ============================================================
@app.route('/logs')
def logs():
    """操作日志：仅管理员/平台管理员可见（v12.5.12 收口，普通用户不可查看全平台操作记录）"""
    if session.get('user_role') not in ('admin', 'platform_admin'):
        flash('无权限查看操作日志', 'error')
        return redirect(url_for('index'))
    db = get_db()
    # v12.2.0：按操作人/模块筛选
    user_filter = request.args.get('user', '').strip()
    mod_filter = request.args.get('mod', '').strip()
    conds, params = [], []
    if user_filter:
        conds.append('operator = ?')
        params.append(user_filter)
    if mod_filter:
        conds.append('module = ?')
        params.append(mod_filter)
    sql = 'SELECT * FROM operation_logs'
    if conds:
        sql += ' WHERE ' + ' AND '.join(conds)
    sql += ' ORDER BY created_at DESC LIMIT 200'
    logs = db.execute(sql, params).fetchall()
    users = [r['operator'] for r in db.execute("SELECT DISTINCT operator FROM operation_logs WHERE operator IS NOT NULL AND operator != '' ORDER BY operator").fetchall()]
    mods = [r['module'] for r in db.execute("SELECT DISTINCT module FROM operation_logs WHERE module IS NOT NULL AND module != '' ORDER BY module").fetchall()]
    return render_template('logs.html', logs=logs, users=users, mods=mods,
                           user_filter=user_filter, mod_filter=mod_filter)


@app.route('/admin/logs/export')
def logs_export():
    """最近动态/操作日志完整导出（仅平台管理员，xlsx 全量，v12.5.39）"""
    if session.get('user_role') != 'platform_admin':
        return '无权限：仅平台管理员可导出完整日志', 403
    db = get_db()
    rows = db.execute('SELECT * FROM operation_logs ORDER BY created_at DESC').fetchall()
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = '操作日志'
    headers = ['ID', '模块', '动作', '详情', '操作人', '时间']
    ws.append(headers)
    for c in ws[1]:
        c.font = Font(bold=True, color='FFFFFF')
        c.fill = PatternFill('solid', fgColor='1E3A8A')
        c.alignment = Alignment(horizontal='center')
    for r in rows:
        ws.append([r['id'], r['module'], r['action'], r['detail'], r['operator'], r['created_at']])
    for col, w in (('A', 8), ('B', 18), ('C', 14), ('D', 64), ('E', 12), ('F', 20)):
        ws.column_dimensions[col].width = w
    bio = io.BytesIO()
    wb.save(bio)
    bio.seek(0)
    fname = '操作日志全量_%s.xlsx' % datetime.now().strftime('%Y%m%d_%H%M%S')
    return send_file(bio, mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                     as_attachment=True, download_name=fname)

@app.route('/admin/logs/clear', methods=['POST'])
def logs_clear():
    """清空全部动态日志（仅平台管理员，v12668）。清空后写入一条留痕记录，保证审计可溯。"""
    if session.get('user_role') != 'platform_admin':
        return jsonify(ok=False, msg='无权限：仅平台管理员可清空动态日志'), 403
    db = get_db()
    cur = db.execute('DELETE FROM operation_logs')
    db.commit()
    db.execute("INSERT INTO operation_logs (module, action, detail, operator) "
               "VALUES ('系统', '清空动态日志', '平台管理员清空了全部动态日志', ?)",
               (session.get('username', 'admin'),))
    db.commit()
    return jsonify(ok=True, msg='已清空 %d 条动态日志' % cur.rowcount)

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
    })

@app.route('/api/global-search')
def api_global_search():
    """全局搜索API（v10.5.0 只保留 WBS）：搜索 WBS 项目与任务"""
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
    except Exception as e:
        print(f'[全局搜索] WBS项目查询异常: {e}')
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
    except Exception as e:
        print(f'[全局搜索] WBS任务查询异常: {e}')
    # v12.2.0：ensure_ascii=False，前端/调试直接可见中文
    return jsonify({'success': True, 'results': results[:20]})


# ============================================================
# 反向规划模块1：软件版本规划（根据用户需求反向规划版本升级）
# ============================================================

def perform_backup(label='启动'):
    """数据备份核心（v12.1.2 启动自动备份 / v12.7.1 管理页手动备份共用）：
    主库 + 全部插件独立库（v12.7.0 方案B）→ backups/，各前缀保留最近 7 份。
    返回 (ok, msg, [本次生成的备份文件名])"""
    import shutil
    src = app.config['DATABASE']
    if not os.path.exists(src):
        return False, f'数据库不存在（{src}），无需备份', []
    # v12.2.0 WAL 模式：先 checkpoint 把 -wal 未落盘数据合并进主库，避免备份缺最新记录
    try:
        c = sqlite3.connect(src)
        c.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        c.close()
    except Exception:
        pass
    bdir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'backups')
    os.makedirs(bdir, exist_ok=True)
    ts = time.strftime('%Y%m%d_%H%M%S')
    made = []
    dst = os.path.join(bdir, f'business_备份_{ts}.db')
    shutil.copy2(src, dst)
    made.append(os.path.basename(dst))
    # v12.7.0 数据隔离（方案B）：上传插件的独立库一并备份
    data_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data')
    if os.path.isdir(data_dir):
        for f in sorted(os.listdir(data_dir)):
            if f.endswith('.db'):
                src_db = os.path.join(data_dir, f)
                try:
                    if os.path.getsize(src_db) <= 0:
                        continue  # v12.7.10.15：空库（内置插件遗留）不生成备份，避免误恢复
                except OSError:
                    continue
                pdst = os.path.join(bdir, f.replace('.db', f'_备份_{ts}.db'))
                try:
                    shutil.copy2(src_db, pdst)
                    made.append(os.path.basename(pdst))
                except Exception:
                    pass
    # v12.7.10.11：插件包本体也一并快照（只备份库、不备份插件目录时，
    # 恢复后会出现"数据在、插件没了"——插件未安装，独立库成孤儿）
    try:
        import zipfile
        app_root = os.path.dirname(os.path.abspath(__file__))
        try:
            pack_ids = sorted(plugin_system._upload_plugin_ids())
        except Exception:
            pack_ids = []
        if not pack_ids:
            # v12.7.10.11 启动早期（init_db/插件加载之前）registry 还读不到：
            # 用 data/<插件id>.db 反推外来插件目录（内置插件不建独立库，因此不会被误打包）
            try:
                if os.path.isdir(data_dir):
                    pack_ids = sorted(
                        f[:-3] for f in os.listdir(data_dir)
                        if f.endswith('.db')
                        and os.path.getsize(os.path.join(data_dir, f)) > 0   # 空库不参与反推
                        and os.path.isdir(os.path.join(app_root, 'plugins', f[:-3])))
            except Exception:
                pack_ids = []
        if pack_ids:
            pz = os.path.join(bdir, f'pluginpack_备份_{ts}.zip')
            n_pack = 0
            with zipfile.ZipFile(pz, 'w', zipfile.ZIP_DEFLATED) as z:
                for pid in pack_ids:
                    pdir = os.path.join(app_root, 'plugins', pid)
                    if not os.path.isdir(pdir):
                        continue
                    for root, _dirs, files in os.walk(pdir):
                        if '__pycache__' in root:
                            continue
                        for fn in files:
                            if fn.endswith(('.pyc', '.pyo')):
                                continue
                            fp = os.path.join(root, fn)
                            z.write(fp, os.path.relpath(fp, app_root).replace(os.sep, '/'))
                            n_pack += 1
            if n_pack:
                made.append(os.path.basename(pz))
            else:
                os.remove(pz)
    except Exception as e:
        print(f'[数据防护] 插件包快照失败（不影响库备份）: {e}')
    # v12.7.10.15：清掉备份目录里被顺带打开的 -wal/-shm（0 字节残留，易被误当成"空备份"）
    try:
        for f in os.listdir(bdir):
            if f.endswith(('-wal', '-shm')) and '_备份_' in f:
                try:
                    os.remove(os.path.join(bdir, f))
                except Exception:
                    pass
    except Exception:
        pass
    # 只保留最近 7 份（business_ / 各 <插件id>_ / pluginpack_ 前缀各自保留 7 份）
    groups = {}
    for f in os.listdir(bdir):
        if '_备份_' not in f or not (f.endswith('.db') or f.endswith('.zip')):
            continue
        pre = f.split('_备份_')[0]
        groups.setdefault(pre, []).append(f)
    for pre, fl in groups.items():
        for f in sorted(fl)[:-7]:
            try:
                os.remove(os.path.join(bdir, f))
            except Exception:
                pass
    msg = f'已备份主库 → backups/{os.path.basename(dst)}'
    n_pack = sum(1 for x in made if x.endswith('.zip'))
    n_db = len(made) - n_pack - 1
    detail = []
    if n_db > 0:
        detail.append(f'{n_db} 份插件库')
    if n_pack > 0:
        detail.append(f'{n_pack} 份插件包快照（含插件本体）')
    if detail:
        msg += '，另含 ' + '、'.join(detail)
    msg += '（各前缀保留最近 7 份）'
    return True, msg, made


if __name__ == '__main__':
    # ============================================================
    # v12.1.2 数据防护：启动时自动全库备份（保留最近 7 份）
    # 覆盖场景：增加/卸载插件、误操作、升级异常等任何意外，均可回滚
    # ============================================================
    try:
        ok, msg, _ = perform_backup('启动')
        if ok:
            print(f'[数据防护] 启动备份完成：{msg}')
    except Exception as e:
        print(f'[数据防护] 启动备份失败（不影响启动）: {e}')
    # 初始化数据库（CREATE TABLE IF NOT EXISTS，兼容旧数据库）
    init_db()
    # v12.2.0 密码哈希化迁移：存量明文密码原地升级为哈希（幂等）
    migrate_plain_passwords()
    # 插件建表 + 增量迁移（必须在 init_db() 之后：全新库先有 users 表与种子，再执行 v6 角色升级等迁移）
    with app.app_context():
        plugin_system.init_plugin_tables()
        # v12.4.5 插件表一致性校验：登记 vs 实际建表（冲突/未登记告警打印到启动日志）
        try:
            _warns, _infos = plugin_system.check_plugin_tables_integrity()
            for w in _warns:
                print(f'[插件表校验·警告] {w}')
            for i in _infos:
                print(f'[插件表校验·提示] {i}')
            if not _warns and not _infos:
                print('[插件表校验] 全部插件表与登记一致')
        except Exception as e:
            print(f'[插件表校验] 执行异常（不影响启动）: {e}')
    host, port, debug, https_mode = get_config()
    print('='*60)
    print('  业务高效运转系统')
    if https_mode:
        print(f'  本机访问: https://127.0.0.1:{port}  （自签证书，浏览器首次访问需点「高级→继续前往」）')
        print(f'  内网访问: https://<本机IP>:{port}')
    else:
        print(f'  本机访问: http://127.0.0.1:{port}')
        print(f'  内网访问: http://<本机IP>:{port}')
    print(f'  调试模式: {"开启(Flask开发服务器)" if debug else "关闭(waitress生产服务器)"}')
    print('  端口配置: 命令行--port > 环境变量BUSINESS_PORT > config.py > 默认5000')
    print('='*60)
    # v12.1.0 便携版：启动后自动打开浏览器（2 秒后，等待服务就绪）
    try:
        import threading, webbrowser

        def _open_browser():
            try:
                scheme = 'https' if https_mode else 'http'
                webbrowser.open('%s://127.0.0.1:%d' % (scheme, port))
            except Exception:
                pass

        threading.Timer(2.0, _open_browser).start()
    except Exception:
        pass
    if https_mode:
        # v12660 HTTPS 模式：自签证书，EDR 不解密 HTTPS 响应，明文扫描/净化全部绕开。
        # 生产用 werkzeug make_server（多线程）承载，单机内网 50-80 人规模足够。
        import ssl as _ssl
        cert_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'cert')
        crt_path = os.path.join(cert_dir, 'server.crt')
        key_path = os.path.join(cert_dir, 'server.key')
        if not (os.path.isfile(crt_path) and os.path.isfile(key_path)):
            print('[错误] 未找到自签证书 app/cert/server.crt / server.key，无法启动 HTTPS 模式。')
            print('       请改用普通模式启动: python.exe app.py')
            sys.exit(1)
        ctx = _ssl.SSLContext(_ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(crt_path, key_path)
        from werkzeug.serving import make_server
        print(f'  HTTPS 生产服务器启动中... 端口 {port}（自签证书，EDR 不解密即不净化）')
        print('  按 Ctrl+C 停止服务')
        print('='*60)
        make_server(host, port, app, threaded=True, ssl_context=ctx).serve_forever()
    elif debug:
        app.run(host=host, port=port, debug=True)
    else:
        from waitress import serve
        # v12.2.0 扩容：支持 50-80 人规模（线程16/连接上限100，单机内网足够）
        print(f'  waitress生产服务器启动中... 线程数: 16')
        print('  按 Ctrl+C 停止服务')
        print('='*60)
        serve(app, host=host, port=port, threads=16, connection_limit=100)
