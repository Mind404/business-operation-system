# -*- coding: utf-8 -*-
"""
插件化基底：插件注册表 + 用户订阅 + 动态导航 + 路由守卫
v10.5.0 只保留 WBS：核心常驻（个人中心/工作台）+ 唯一业务插件 WBS（所有登录用户可用）
"""
import json
import os
import sqlite3
from datetime import datetime
from flask import g, session, current_app, redirect, url_for, request, flash, has_request_context

# ============================================================
# 插件清单
# core=1 常驻不可关；core=0 业务插件
# ============================================================
PLUGINS = [
    # --- 核心常驻 ---
    {'id': 'profile',      'name': '个人中心',         'icon': '👤', 'group': '核心', 'core': 1,
     'desc': '我的信息、我的待办、修改密码', 'endpoint': 'profile', 'nav_path': '/profile'},
    {'id': 'workbench',    'name': '我的工作台',       'icon': '📊', 'group': '核心', 'core': 1,
     'desc': '系统首页：WBS统计与快捷入口', 'endpoint': 'index', 'nav_path': '/',
     'tables': ['tasks']},

    # --- 唯一业务插件：WBS（所有登录用户可用） ---
    {'id': 'wbs',          'name': 'WBS计划管理',      'icon': '🗓️', 'group': '业务', 'core': 0,
     'desc': '项目工作分解结构：父子任务树、负责人、协作人、时间窗、交付物', 'endpoint': 'wbs.wbs_projects', 'nav_path': '/wbs',
     'tables': ['wbs_projects','wbs_tasks']},
]

# 业务插件 endpoint → plugin_id 映射（用于路由守卫）
_BIZ_ENDPOINTS = {p['endpoint']: p['id'] for p in PLUGINS if not p['core']}

# v10.3.2 前缀守卫：插件子路由也纳入校验（防 URL 直连绕过）
_PLUGIN_PATH_PREFIXES = {
    'wbs': ('/wbs',),
}

# 免前缀守卫的全局路径（登录/静态/个人中心/插件中心/工作台）
_GUARD_FREE_PATHS = ('/login', '/static', '/plugins', '/profile', '/logout', '/approvals', '/', '/admin')


def _plugin_id_for_path(path):
    """按路径前缀判断归属插件（子路由直连也能拦）"""
    for pid, prefixes in _PLUGIN_PATH_PREFIXES.items():
        for pfx in prefixes:
            if path == pfx or path.startswith(pfx + '/'):
                return pid
    return None
# 免守卫路径（登录、静态、插件中心本身、个人中心、总览）
_WHITELIST_PREFIX = ('/login', '/static', '/plugins', '/profile', '/logout')


# ============================================================
# v12.7.0 数据隔离（方案B）：
#   内置/核心插件表留在主库；上传插件（install_type=upload）的数据表
#   落到 app/data/<插件id>.db 独立库；登记表 plugin_tables 仍在主库。
#   判定依据 = 当前请求 endpoint 前缀（blueprint 名 = 插件 id）。
# ============================================================

def _app_dir():
    """plugin_system.py 所在目录（= app/）"""
    return os.path.dirname(os.path.abspath(__file__))

_upload_ids_cache = None

def _upload_plugin_ids():
    """当前 install_type='upload' 的插件 id 集合（独立短连接查主库，避免递归 get_db）"""
    global _upload_ids_cache
    if _upload_ids_cache is None:
        try:
            con = sqlite3.connect(current_app.config['DATABASE'], timeout=3.0)
            _upload_ids_cache = {r[0] for r in con.execute(
                "SELECT id FROM plugin_registry WHERE install_type='upload'").fetchall()}
            con.close()
        except Exception:
            _upload_ids_cache = set()
    return _upload_ids_cache

def _invalidate_upload_cache():
    global _upload_ids_cache
    _upload_ids_cache = None

def _plugin_db_path(plugin_id):
    """upload 插件 → app/data/<id>.db；内置/核心/未登记 → None（主库）"""
    if not plugin_id or plugin_id not in _upload_plugin_ids():
        return None
    return os.path.join(_app_dir(), 'data', plugin_id + '.db')

def _endpoint_plugin_id():
    """当前请求所属插件 id（endpoint 前缀 = blueprint 名 = 插件 id）；非请求上下文返回 None"""
    if not has_request_context():
        return None
    ep = getattr(request, 'endpoint', None) or ''
    return ep.split('.')[0] if '.' in ep else None

def _main_db():
    """平台主库连接（系统表 / 登记表 plugin_tables / 内置插件数据）"""
    if 'db' not in g:
        g.db = sqlite3.connect(current_app.config['DATABASE'], timeout=5.0)
        g.db.row_factory = sqlite3.Row
        g.db.execute('PRAGMA foreign_keys = ON')
        g.db.execute('PRAGMA journal_mode = WAL')
        g.db.execute('PRAGMA busy_timeout = 5000')
    return g.db

def _db_for_plugin_id(plugin_id):
    """按插件返回连接：upload → 独立库 data/<id>.db（不存在自动建）；其余 → 主库
    注意：g 只支持属性式存取（g.db），不支持 g['key']= 赋值"""
    pdb = _plugin_db_path(plugin_id)
    if pdb is None:
        return _main_db()
    os.makedirs(os.path.dirname(pdb), exist_ok=True)
    key = 'pdb_' + plugin_id
    conn = getattr(g, key, None)
    if conn is None:
        conn = sqlite3.connect(pdb, timeout=5.0)
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA foreign_keys = ON')
        conn.execute('PRAGMA journal_mode = WAL')
        conn.execute('PRAGMA busy_timeout = 5000')
        setattr(g, key, conn)
    return conn

def get_db():
    """当前请求的数据库连接：上传插件的请求自动路由到其独立库，其余走主库"""
    return _db_for_plugin_id(_endpoint_plugin_id())


def init_plugin_tables():
    """建表并注册插件（幂等）。每个插件自己管自己的表。"""
    from db_migrations import init_core_tables, init_wbs_tables, run_migrations
    db = get_db()
    # 核心表（用户、插件注册、订阅、版本记录）
    init_core_tables()
    # WBS 业务表
    init_wbs_tables()
    # 跑增量迁移
    run_migrations()
    # 注册插件清单（UPSERT：新库插入，旧库已有行刷新 desc/tables，保证表清单登记生效）
    # 注意：不更新 enabled/install_type，避免覆盖平台管理员的手动启停状态
    for p in PLUGINS:
        db.execute('''INSERT INTO plugin_registry
            (id, name, icon, grp, core, description, endpoint, tables) VALUES (?,?,?,?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET
                name=excluded.name, icon=excluded.icon, grp=excluded.grp, core=excluded.core,
                description=excluded.description, endpoint=excluded.endpoint, tables=excluded.tables''',
            (p['id'], p['name'], p['icon'], p['group'], p['core'], p['desc'], p['endpoint'],
             json.dumps(p.get('tables', []), ensure_ascii=False)))
    # v12.1.0 目录自动发现：扫描 plugins/ 下带 manifest+blueprint 的插件，未登记则登记（放目录即注册）
    for pid, man in _manifest_map().items():
        if pid in [p['id'] for p in PLUGINS]:
            continue
        db.execute('''INSERT INTO plugin_registry
            (id, name, icon, grp, core, description, endpoint, tables, enabled, install_type)
            VALUES (?,?,?,?,?,?,?,?,1,'builtin')
            ON CONFLICT(id) DO NOTHING''',
            (man.get('id', pid), man.get('name', pid), man.get('icon', '🧩'),
             man.get('group', '业务'), 1 if man.get('core') else 0,
             man.get('description', ''), man.get('endpoint', ''),
             json.dumps(man.get('tables', []), ensure_ascii=False)))
    # v10.5.0：清理已下线的注册与订阅记录（静态表+目录中都不存在的插件，如已卸载/删除的插件包）
    static_ids = [p['id'] for p in PLUGINS]
    manifest_ids = list(_manifest_map().keys())
    valid_ids = list(dict.fromkeys(static_ids + manifest_ids))
    placeholders = ','.join('?' * len(valid_ids))
    db.execute(f'DELETE FROM plugin_registry WHERE id NOT IN ({placeholders})', valid_ids)
    db.execute(f'DELETE FROM user_plugins WHERE plugin_id NOT IN ({placeholders})', valid_ids)
    db.commit()
    _invalidate_upload_cache()  # v12.7.0：registry 可能已变化，失效 upload ids 缓存


def get_user_plugin_ids(user_id):
    """返回用户订阅的业务插件id集合；首次使用只给核心，不自动订阅任何业务插件。"""
    db = get_db()
    rows = db.execute('SELECT plugin_id FROM user_plugins WHERE user_id=?', (user_id,)).fetchall()
    return {r['plugin_id'] for r in rows}


def inject_nav_context():
    """注入模板导航：核心常驻 + 业务插件（仅启用可见）+ 系统管理入口由 base.html 按角色渲染"""
    user_id = session.get('user_id')
    if not user_id:
        return {'nav_core': [], 'nav_biz': [], 'nav_ext': [], 'nav_all_plugins': PLUGINS, 'user_subscribed': set()}
    registered = set(current_app.view_functions.keys())
    enabled_ids = _enabled_ids()
    available = [p for p in PLUGINS if p['endpoint'] in registered and p['id'] in enabled_ids]
    core_plugins = [p for p in available if p['core'] and p['id'] != 'profile']  # v12.4.4 合并：个人中心并入工作台，导航只留工作台
    # 业务插件：静态表（wbs）+ 目录自动发现的新插件（启用的）
    biz_plugins = [p for p in available if not p['core']]
    for pid, man in _manifest_map().items():
        if pid not in enabled_ids or pid in [p['id'] for p in biz_plugins]:
            continue
        if man.get('core'):
            continue
        ep = man.get('endpoint', '')
        if ep not in registered:
            continue
        biz_plugins.append({
            'id': pid, 'name': man.get('name', pid), 'icon': man.get('icon', '🧩'),
            'group': man.get('group', '业务'), 'core': 0, 'desc': man.get('description', ''),
            'endpoint': ep, 'nav_path': man.get('url_prefix', man.get('nav_path', '/' + pid)),
            # v12.7.10.10：仅"上传安装"的插件算外来插件，导航走平台外壳页（带"返回平台"）；
            # 平台自带的目录型插件（univer_sheet 等，install_type=builtin）仍直接进页面，避免双层导航
            'external': (pid in _upload_plugin_ids()),
        })
    # v12.7.10.15：外来插件（上传安装）单独成组，不再和平台内置功能混在同一分区
    nav_biz = [p for p in biz_plugins if not p.get('external')]
    nav_ext = [p for p in biz_plugins if p.get('external')]
    return {'nav_core': core_plugins, 'nav_biz': nav_biz, 'nav_ext': nav_ext,
            'nav_all_plugins': available, 'user_subscribed': set()}


def _plugin_enabled(pid):
    """读取插件启用状态（缺省视为启用）"""
    try:
        db = get_db()
        row = db.execute('SELECT enabled FROM plugin_registry WHERE id=?', (pid,)).fetchone()
        if row is None:
            return True
        return bool(row['enabled'])
    except Exception:
        return True


_manifest_prefix_cache = {}


def _manifest_prefix_plugin(path):
    """按 manifest.url_prefix 兜底识别上传/发现的新插件路径归属"""
    if not _manifest_prefix_cache:
        for pid, man in _manifest_map().items():
            up = man.get('url_prefix')
            if not up:
                continue
            for pfx in (up if isinstance(up, (list, tuple)) else [up]):
                _manifest_prefix_cache.setdefault(str(pfx), pid)
    for pfx, pid in _manifest_prefix_cache.items():
        if path == pfx or path.startswith(pfx + '/'):
            return pid
    return None


# v12.2.0 平台层角色拦截：插件 manifest 声明 min_role 时按路径前缀映射角色等级
_ROLE_LEVEL = {'platform_admin': 3, 'admin': 2, 'user': 1}
_min_role_prefix_cache = {}


def _min_role_for_path(path):
    """返回命中路径前缀所声明的最低角色；未声明返回 None（不拦截）"""
    if not _min_role_prefix_cache:
        for pid, man in _manifest_map().items():
            mr = man.get('min_role')
            if not mr or mr not in _ROLE_LEVEL:
                continue
            up = man.get('url_prefix')
            if not up:
                continue
            for pfx in (up if isinstance(up, (list, tuple)) else [up]):
                _min_role_prefix_cache.setdefault(str(pfx), mr)
    for pfx, mr in _min_role_prefix_cache.items():
        if path == pfx or path.startswith(pfx + '/'):
            return mr
    return None


def register_plugin_guard(app):
    """路由守卫（v12.1.0）：登录校验 + 平台级停用拦截；业务插件的操作权限由插件内部按角色校验
    v12.2.0：新增 manifest.min_role 平台层角色拦截（可选，不声明则不拦截）"""
    @app.before_request
    def _guard():
        if request.path.startswith(_WHITELIST_PREFIX):
            return None
        # 静态资源、核心路由等放行
        ep = request.endpoint
        if not ep or ep in ('static', 'plugin_center', 'toggle_plugin', 'profile',
                            'login', 'logout', 'index', 'logs', 'api_stats', 'api_global_search') \
                            or (ep or '').endswith('.static'):
            return None
        # 前缀守卫兜底——子路由（URL 直连）也按路径判归属
        for free in _GUARD_FREE_PATHS:
            if request.path == free or request.path.startswith(free + '/'):
                return None
        # 该 endpoint 属于某个业务插件？
        pid = _BIZ_ENDPOINTS.get(ep)
        if not pid:
            pid = _plugin_id_for_path(request.path)
        if not pid:
            pid = _manifest_prefix_plugin(request.path)
        if not pid:
            return None
        # 核心常驻插件放行（不可停用）
        core_ids = {p['id'] for p in PLUGINS if p['core']}
        if pid in core_ids:
            return None
        # v12.1.0 平台级停用拦截
        if not _plugin_enabled(pid):
            flash(f'插件「{pid}」已被平台停用', 'error')
            return redirect(url_for('index'))
        # 登录校验
        user_id = session.get('user_id')
        if not user_id:
            return redirect(url_for('login'))
        # v12.2.0 平台层角色拦截：插件 manifest 声明 min_role 时按角色等级校验
        need_role = _min_role_for_path(request.path)
        if need_role:
            role = session.get('user_role', '')
            if _ROLE_LEVEL.get(role, 0) < _ROLE_LEVEL.get(need_role, 1):
                role_name = {'platform_admin': '平台管理员', 'admin': '管理员', 'user': '普通用户'}.get(need_role, need_role)
                flash(f'无权限：该模块需要「{role_name}」及以上角色', 'error')
                return redirect(url_for('index'))
        return None


def log_operation(module, action, detail, operator='系统'):
    """统一操作日志入口（全系统共用，失败静默不影响业务）"""
    try:
        db = get_db()
        db.execute('INSERT INTO operation_logs (module, action, detail, operator) VALUES (?,?,?,?)',
                   (module, action, detail, operator))
        db.commit()
    except Exception:
        pass


def submit_approval(plugin_id, record_type, record_id, requester_id, action, payload=None):
    """提交审批申请（方案A统一审批表；WBS 暂保持自身审批流程）"""
    db = get_db()
    cur = db.execute(
        'INSERT INTO approval_requests (plugin_id, record_type, record_id, requester_id, action, payload) '
        'VALUES (?,?,?,?,?,?)',
        (plugin_id, record_type, record_id, requester_id, action,
         json.dumps(payload, ensure_ascii=False) if payload else None))
    db.commit()
    return cur.lastrowid


def review_approval(req_id, reviewer_id, approve, comment=''):
    """审批统一入口：approve=True 通过 / False 驳回"""
    db = get_db()
    row = db.execute('SELECT * FROM approval_requests WHERE id=?', (req_id,)).fetchone()
    if not row or row['status'] != '待审批':
        return False, '审批申请不存在或已处理'
    status = '已通过' if approve else '已驳回'
    db.execute('UPDATE approval_requests SET status=?, reviewer_id=?, review_comment=?, reviewed_at=? WHERE id=?',
               (status, reviewer_id, comment, datetime.now().strftime('%Y-%m-%d %H:%M:%S'), req_id))
    db.commit()
    log_operation(row['plugin_id'], '审批', f"#{req_id} {row['record_type']} → {status}{('：'+comment) if comment else ''}", '')
    return True, status


def toggle_plugin(user_id, plugin_id):
    """订阅/取消订阅切换。核心插件不可取消。"""
    db = get_db()
    p = db.execute('SELECT * FROM plugin_registry WHERE id=?', (plugin_id,)).fetchone()
    if not p:
        return False, '插件不存在'
    if p['core']:
        return False, '核心插件不可取消订阅'
    existing = db.execute('SELECT 1 FROM user_plugins WHERE user_id=? AND plugin_id=?',
                          (user_id, plugin_id)).fetchone()
    if existing:
        db.execute('DELETE FROM user_plugins WHERE user_id=? AND plugin_id=?', (user_id, plugin_id))
        msg = f'已取消订阅 {p["name"]}'
        log_operation('插件中心', '取消订阅', f'用户#{user_id} 取消订阅 {p["name"]}')
    else:
        db.execute('INSERT INTO user_plugins (user_id, plugin_id, subscribed_at) VALUES (?,?,?)',
                   (user_id, plugin_id, datetime.now().strftime('%Y-%m-%d %H:%M')))
        msg = f'已订阅 {p["name"]}'
        log_operation('插件中心', '订阅', f'用户#{user_id} 订阅 {p["name"]}')
    db.commit()
    return True, msg


# ============================================================
# v12.1.0 插件管理（平台管理员）：目录发现 / 启停 / 上传安装 / 卸载
# ============================================================
def _registry_rows():
    # 同上：登记表在主库，不受插件库路由影响
    try:
        con = sqlite3.connect(current_app.config['DATABASE'], timeout=3.0)
        con.row_factory = sqlite3.Row
        rows = con.execute('SELECT * FROM plugin_registry').fetchall()
        con.close()
    except Exception:
        return {}
    return {r['id']: r for r in rows}


_manifest_cache = None


def _manifest_map():
    """扫描 plugins/ 目录，返回 {plugin_id: manifest}（惰性导入避免循环依赖）。

    v12.5.28 性能修复：结果进程内缓存。原实现每次调用都做 pkgutil 目录扫描+模块导入，
    模板渲染时 inject_nav_context 每请求触发，高并发下成为主要瓶颈（100并发 P50≈3.9s）。
    插件上传/卸载/安装后调用 _invalidate_manifest_cache() 失效。
    """
    global _manifest_cache
    if _manifest_cache is None:
        try:
            from plugins import discover_plugins
            _manifest_cache = {mid: mod.manifest for mid, mod in discover_plugins()}
        except Exception:
            _manifest_cache = {}
    return _manifest_cache


def _invalidate_manifest_cache():
    """插件清单变更（上传/卸载/安装）后调用，使 _manifest_map 缓存失效"""
    global _manifest_cache
    _manifest_cache = None


# ============================================================
# v12.7.10.10 插件数据开放接口（OpenAPI v1）
# 约定：插件必须自带两个只读接口（路径固定，挂在插件自己的 url_prefix 下）：
#   GET <url_prefix>/__openapi/datasets        -> 声明可开放的数据集（表逻辑名/字段/行数/主键）
#   GET <url_prefix>/__openapi/rows?table=&limit=&offset= -> 分页返回该数据集的行
# 平台侧：进程内直接调用视图函数（不发 HTTP、不经过登录中间件），插件无需对外开放端口。
# ============================================================
OPENAPI_VERSION = 1
_OPENAPI_SUFFIX_DATASETS = '/__openapi/datasets'
_OPENAPI_SUFFIX_ROWS = '/__openapi/rows'
_OPENAPI_MAX_ROWS = 1000          # 单次调用行数硬上限（防止插件一次吐出整库）
_openapi_status_cache = {}


def invalidate_openapi_cache():
    """插件安装/卸载/启停后调用，重新探测开放接口。"""
    global _openapi_status_cache
    _openapi_status_cache = {}


def _plugin_url_prefix(pid):
    man = _manifest_map().get(pid) or {}
    up = man.get('url_prefix')
    if isinstance(up, (list, tuple)):
        up = up[0] if up else ''
    return str(up or '').rstrip('/')


def _openapi_endpoint(pid, suffix):
    """在当前 app 的 url_map 里找该插件开放接口的视图。返回 (view_func, rule) 或 (None, None)。"""
    try:
        app = current_app._get_current_object()
    except Exception:
        return None, None
    prefix = _plugin_url_prefix(pid)
    if not prefix:
        return None, None
    for rule in app.url_map.iter_rules():
        if rule.rule.endswith(suffix) and rule.rule.startswith(prefix):
            return app.view_functions.get(rule.endpoint), rule.rule
    return None, None


def _openapi_call(pid, suffix, query=None):
    """进程内直接调用插件开放接口视图（不发起 HTTP，不触发登录校验）。"""
    view, rule = _openapi_endpoint(pid, suffix)
    if not view:
        return None
    try:
        app = current_app._get_current_object()
        with app.test_request_context(rule, query_string=query or {}):
            rv = view()
        if isinstance(rv, tuple):
            rv = rv[0]
        if hasattr(rv, 'get_json'):
            return rv.get_json(silent=True)
        if isinstance(rv, (dict, list)):
            return rv
        if isinstance(rv, str):
            return json.loads(rv)
        return None
    except Exception as e:
        print('[插件开放接口] 调用失败 %s%s: %s' % (pid, suffix, e))
        return None


def plugin_openapi_status(pid):
    """探测插件开放接口是否可用，并取回数据集声明。结果按进程缓存。"""
    if pid in _openapi_status_cache:
        return _openapi_status_cache[pid]
    has_rows = _openapi_endpoint(pid, _OPENAPI_SUFFIX_ROWS)[0] is not None
    ds = _openapi_call(pid, _OPENAPI_SUFFIX_DATASETS, {})
    if not has_rows:
        st = {'supported': False, 'reason': '未实现 %s（数据开放接口缺失）' % _OPENAPI_SUFFIX_ROWS, 'tables': []}
    elif not isinstance(ds, dict) or not isinstance(ds.get('tables'), list):
        st = {'supported': False, 'reason': '未实现 %s 或返回格式不符' % _OPENAPI_SUFFIX_DATASETS, 'tables': []}
    else:
        st = {'supported': True, 'version': ds.get('openapi'), 'tables': ds['tables'],
              'plugin': ds.get('plugin', pid)}
    _openapi_status_cache[pid] = st
    return st


def plugin_openapi_rows(pid, table, limit=200, offset=0):
    """按开放接口分页取数。返回 {'rows': [...], 'total': int|None, 'fields': [...]}，失败返回 None。"""
    try:
        limit = max(1, min(int(limit), _OPENAPI_MAX_ROWS))
        offset = max(0, int(offset))
    except Exception:
        limit, offset = 200, 0
    r = _openapi_call(pid, _OPENAPI_SUFFIX_ROWS,
                      {'table': table, 'limit': str(limit), 'offset': str(offset)})
    if not isinstance(r, dict):
        return None
    return {'rows': r.get('rows') or [], 'total': r.get('total'), 'fields': r.get('fields') or []}


def plugin_data_sources():
    """外部插件数据源清单（供平台 AI 读取）。

    数据开放是**开发时的硬要求**：插件必须实现 OpenAPI v1 两个只读接口（见《插件开发规范》§13）。
    平台优先走开放接口（access='openapi'）；老插件/未实现接口的，回退直读 data/<插件id>.db
    （access='db'，AI 上下文会显式标注"直读兜底"，不假装接口已开放）。

    manifest 的 `data_open` 仍用于边界声明（可选）：
        'data_open': {'ai_readable': True, 'tables': [...], 'description': '...'}
    """
    out = []
    try:
        enabled = _enabled_ids()
    except Exception:
        enabled = set()
    builtin = {p['id'] for p in PLUGINS}
    data_dir = os.path.join(_app_dir(), 'data')
    for pid, man in _manifest_map().items():
        if pid in builtin:
            continue
        decl = man.get('data_open') or {}
        tables = list(decl.get('tables') or man.get('tables') or [])
        st = plugin_openapi_status(pid)
        if st.get('supported'):
            api_tables = [t.get('name') for t in st.get('tables') or [] if isinstance(t, dict) and t.get('name')]
            if api_tables:
                tables = api_tables
        out.append({
            'id': pid,
            'name': man.get('name', pid),
            'enabled': pid in enabled,
            'ai_readable': bool(decl.get('ai_readable', True)),
            'tables': tables,
            'db_path': os.path.join(data_dir, pid + '.db'),
            'note': decl.get('description', ''),
            'access': 'openapi' if st.get('supported') else 'db',
            'openapi': st,
        })
    return out


def plugin_manifest(pid):
    """取插件 manifest：内置静态表优先，其次目录发现的外部插件。"""
    for p in PLUGINS:
        if p['id'] == pid:
            return dict(p)
    return dict(_manifest_map().get(pid) or {})


def plugin_home_url(pid):
    """插件首页 URL（用于平台外壳页 iframe 嵌入）。"""
    man = plugin_manifest(pid)
    ep = man.get('endpoint')
    if ep:
        try:
            return url_for(ep)
        except Exception:
            pass
    return man.get('url_prefix') or ('/' + pid)


def plugin_is_external(pid):
    """是否外来插件：仅限上传安装（install_type=upload），平台自带插件不算。"""
    if any(p['id'] == pid for p in PLUGINS):
        return False
    return pid in _upload_plugin_ids()


def _enabled_ids():
    # v12.7.10.14：上传插件请求内 get_db() 会被路由到插件独立库，
    # 系统表 plugin_registry 必须走主库独立短连接，否则 no such table。
    try:
        con = sqlite3.connect(current_app.config['DATABASE'], timeout=3.0)
        con.row_factory = sqlite3.Row
        rows = con.execute('SELECT id, enabled FROM plugin_registry').fetchall()
        con.close()
    except Exception:
        return set()
    return {r['id'] for r in rows if r['enabled']}


def get_plugins_admin():
    """平台管理员插件管理视图：静态清单 + 目录发现的新插件，合并启停状态与表登记"""
    rows = _registry_rows()
    merged = {}
    for p in PLUGINS:
        item = dict(p)
        item['tables'] = p.get('tables', [])
        merged[p['id']] = item
    for pid, man in _manifest_map().items():
        if pid in merged:
            continue
        merged[pid] = {
            'id': man.get('id', pid), 'name': man.get('name', pid), 'icon': man.get('icon', '🧩'),
            'group': man.get('group', '业务'), 'core': 1 if man.get('core') else 0,
            'desc': man.get('description', ''), 'endpoint': man.get('endpoint', ''),
            'nav_path': man.get('url_prefix', man.get('nav_path', '/' + pid)),
            'tables': man.get('tables', []),
        }
    out = []
    for pid, p in merged.items():
        row = rows.get(pid)
        item = dict(p)
        item['registered'] = bool(row)
        item['install_type'] = row['install_type'] if row and row['install_type'] else 'builtin'
        item['enabled'] = bool(row['enabled']) if row else True
        # v12.4.5 表登记：该插件在 plugin_tables 中的 逻辑名→实际名 映射
        item['table_map'] = []
        try:
            db = get_db()
            for r in db.execute('SELECT logical_name, actual_name FROM plugin_tables '
                                'WHERE plugin_id=? ORDER BY logical_name', (pid,)).fetchall():
                item['table_map'].append({'logical': r['logical_name'], 'actual': r['actual_name']})
        except Exception:
            pass
        out.append(item)
    return sorted(out, key=lambda x: (0 if x.get('core') else 1, x['id']))


def set_plugin_enabled(plugin_id, enabled):
    """平台级启用/停用插件。核心插件不可停用。"""
    row = _registry_rows().get(plugin_id)
    if row and row['core']:
        return False, '核心插件不可停用'
    db = get_db()
    db.execute('UPDATE plugin_registry SET enabled=? WHERE id=?', (1 if enabled else 0, plugin_id))
    db.commit()
    log_operation('插件管理', '启用' if enabled else '停用', f'插件 {plugin_id}')
    return True, ('已启用' if enabled else '已停用')


def uninstall_plugin(plugin_id):
    """卸载插件：仅限上传安装的插件（install_type=upload）。停用 + 删除插件包目录，数据表保留不动。"""
    row = _registry_rows().get(plugin_id)
    if not row:
        return False, '插件未登记'
    if row['core']:
        return False, '核心插件不可卸载'
    if row['install_type'] != 'upload':
        return False, '内置插件不可卸载（仅管理页上传安装的插件可卸载）'
    db = get_db()
    db.execute('UPDATE plugin_registry SET enabled=0 WHERE id=?', (plugin_id,))
    db.execute('DELETE FROM user_plugins WHERE plugin_id=?', (plugin_id,))
    db.execute('DELETE FROM plugin_registry WHERE id=?', (plugin_id,))
    db.commit()
    import os, shutil
    target = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'plugins', plugin_id)
    if os.path.isdir(target) and not plugin_id.startswith('_'):
        shutil.rmtree(target, ignore_errors=True)
    log_operation('插件管理', '卸载', f'插件 {plugin_id}（数据表保留）')
    # 清理插件路径归属缓存，避免残留前缀误判
    for k in [k for k, v in _manifest_prefix_cache.items() if v == plugin_id]:
        _manifest_prefix_cache.pop(k, None)
    _invalidate_manifest_cache()  # v12.5.28 性能修复：目录已变，失效 manifest 缓存
    _invalidate_upload_cache()  # v12.7.0：上传/卸载改变 upload 集合，失效独立库路由缓存
    invalidate_openapi_cache()  # v12.7.10.10：新装的插件要重新探测数据开放接口
    return True, f'插件 {plugin_id} 已卸载（业务数据表保留）。重启系统后彻底移除。'


def register_uploaded_plugin(plugin_id, manifest, blueprint=None):
    """上传安装后登记：写入 registry（install_type=upload）+ 即时注册蓝图 + 更新守卫缓存"""
    db = get_db()
    db.execute('''INSERT INTO plugin_registry
        (id, name, icon, grp, core, description, endpoint, tables, enabled, install_type)
        VALUES (?,?,?,?,?,?,?,?,1,'upload')
        ON CONFLICT(id) DO UPDATE SET
            name=excluded.name, icon=excluded.icon, grp=excluded.grp, core=excluded.core,
            description=excluded.description, endpoint=excluded.endpoint, tables=excluded.tables,
            enabled=1, install_type='upload' ''',
        (plugin_id, manifest.get('name', plugin_id), manifest.get('icon', '🧩'),
         manifest.get('group', '业务'), 1 if manifest.get('core') else 0,
         manifest.get('description', ''), manifest.get('endpoint', ''),
         json.dumps(manifest.get('tables', []), ensure_ascii=False)))
    db.commit()
    # 即时注册蓝图（若当前 app 已有）
    try:
        from flask import current_app
        if blueprint is not None and plugin_id not in current_app.blueprints:
            current_app.register_blueprint(blueprint)
    except Exception as e:
        print(f'[插件管理] 即时注册蓝图失败（{plugin_id}）: {e}')
    # 更新守卫前缀缓存
    up = manifest.get('url_prefix')
    if up:
        for pfx in (up if isinstance(up, (list, tuple)) else [up]):
            _manifest_prefix_cache.setdefault(str(pfx), plugin_id)
    _invalidate_manifest_cache()  # v12.5.28 性能修复：目录已变，失效 manifest 缓存
    _invalidate_upload_cache()  # v12.7.0：上传/卸载改变 upload 集合，失效独立库路由缓存
    invalidate_openapi_cache()  # v12.7.10.10：新装插件的开放接口需重新探测
    return True


# ============================================================
# v12.4.5 插件表管理：统一建表入口 + 表名分配 + 一致性校验
# 目标：名字相近的插件分散开发也不会撞表；插件表名不写死，统一走平台 API 取实际名
# ============================================================

# 存量（v12.4.5 之前已存在）平台业务表前缀——白名单豁免，不提示未登记
_LEGACY_TABLE_PREFIXES = ('wbs_', 'reqcollect_', 'test_', 'ai_', 'production_', 'bug_',
                          'delivery_', 'device_', 'gantt_', 'material_', 'ota_', 'tech_',
                          'version_', 'approval_', 'bom_', 'contract_', 'defect_', 'dev_',
                          'milestone_', 'quality_', 'requirement_', 'requisition_', 'revenue_',
                          'software_', 'timeline_', 'warehouse_')
# 平台系统表（非插件业务表）
_PLATFORM_SYSTEM_TABLES = {'schema_version', 'plugin_registry', 'plugin_tables',
                           'user_plugins', 'operation_logs', 'ai_providers',
                           'ai_chat_logs', 'approval_requests',
                           # v12.7.10.10 平台自身新表（非插件建表，无需登记 plugin_tables）
                           'user_sessions', 'ui_prefs'}
# 平台早期无前缀遗留业务表（白名单豁免；v12.4.8 已删除 42 张 8 模块时代业务表，
# 仅剩 users/tasks 两张核心在用：登录与工作台待办）
_PLATFORM_LEGACY_TABLES = {'users', 'tasks'}


def _allocate_table_name(db, desired_name):
    """撞名自动分配：desired_name 已被占用时生成 desired_name_2 / _3 …（全局唯一）"""
    candidate = desired_name
    n = 1
    while True:
        hit = db.execute('SELECT 1 FROM plugin_tables WHERE actual_name=?', (candidate,)).fetchone()
        if not hit:
            exists = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                                (candidate,)).fetchone()
            if not exists:
                return candidate
        n += 1
        candidate = f'{desired_name}_{n}'


def create_plugin_table(plugin_id, desired_name, ddl, register_only=False):
    """统一建表入口（v12.4.5，插件必须通过本入口建表）：
    - 前缀校验：表名必须带业务前缀（形如 xxx_items），禁止裸表名；建议使用「插件id_」前缀（天然隔离）
    - 占用检查：同一表名被其他插件登记占用 → 自动分配 {name}_2/_3 后缀（插件无需反复改名）
    - 登记：写入 plugin_tables（plugin_id, logical_name=desired_name, actual_name=实际名）
    - 建表：DDL 中表名替换为实际名后执行（请写 CREATE TABLE IF NOT EXISTS 保证幂等）
    - 重装/重复调用：同一插件同一逻辑名 → 沿用已登记实际名，不重新分配（数据不丢）
    返回实际表名 actual_name（插件读写时统一通过 get_table_name() 获取）。
    """
    if '_' not in desired_name or desired_name.startswith('_') or desired_name.endswith('_'):
        raise ValueError(f'表名「{desired_name}」必须带业务前缀（如 {plugin_id}_items），'
                         f'禁止使用裸表名，请走平台统一建表入口 create_plugin_table()')
    main = _main_db()                       # 登记库（始终主库）
    target = _db_for_plugin_id(plugin_id)   # 建表目标库：upload → 独立库；内置 → 主库
    # 重装/重复调用：同一插件同一逻辑名 → 沿用已分配名（防表名漂移丢数据）
    row = main.execute('SELECT actual_name FROM plugin_tables WHERE plugin_id=? AND logical_name=?',
                       (plugin_id, desired_name)).fetchone()
    if row:
        actual = row['actual_name']
    else:
        hit = main.execute('SELECT plugin_id FROM plugin_tables WHERE actual_name=?',
                           (desired_name,)).fetchone()
        if hit and hit['plugin_id'] != plugin_id:
            actual = _allocate_table_name(main, desired_name)   # 被其他插件占用 → 自动分配
        else:
            actual = desired_name                              # 空闲或存量裸表 → 沿用并补登记
        main.execute('INSERT INTO plugin_tables (plugin_id, logical_name, actual_name) VALUES (?,?,?)',
                     (plugin_id, desired_name, actual))
        main.commit()
    # 执行 DDL（表名替换为实际名；IF NOT EXISTS 幂等；register_only 只登记不建表）
    # v12.5.39：改用 executescript，支持 CREATE TABLE + CREATE INDEX 等多语句 DDL（单条同样兼容）
    # v12.6.9：DDL 失败时回滚本次新建的登记行，避免失败安装残留脏登记（plugin_tables 与实际物理表不一致）
    # v12.7.0：DDL 目标库按插件路由（upload → 独立库），登记仍在主库
    inserted = False
    if ddl and not register_only:
        try:
            target.executescript(ddl.replace(desired_name, actual))
        except Exception:
            if row is None:  # 本次新插入的登记行（非重装沿用），建表失败即回滚
                main.execute('DELETE FROM plugin_tables WHERE plugin_id=? AND actual_name=?',
                             (plugin_id, actual))
                main.commit()
            raise
        inserted = True
    if ddl and register_only and row is None:
        inserted = True
    if not ddl and row is None:
        inserted = True
    return actual


def get_table_name(plugin_id, desired_name):
    """插件读写时获取实际表名（不写死表名，统一走本 API）：
    - 已登记：直接返回分配名
    - 未登记：按与 create_plugin_table 相同的规则补登记（沿用空闲表名或自动分配）
    返回实际表名 str。
    """
    main = _main_db()   # 登记表始终在主库
    row = main.execute('SELECT actual_name FROM plugin_tables WHERE plugin_id=? AND logical_name=?',
                       (plugin_id, desired_name)).fetchone()
    if row:
        return row['actual_name']
    hit = main.execute('SELECT plugin_id FROM plugin_tables WHERE actual_name=?',
                       (desired_name,)).fetchone()
    actual = _allocate_table_name(main, desired_name) if (hit and hit['plugin_id'] != plugin_id) else desired_name
    main.execute('INSERT INTO plugin_tables (plugin_id, logical_name, actual_name) VALUES (?,?,?)',
                 (plugin_id, desired_name, actual))
    main.commit()
    return actual


def suffix_table_name(plugin_id, base_name):
    """可选能力：动态建多张同构表时生成带时间戳后缀的表名（如 xxx_202610261730）。
    仅用于「一个插件按需动态生成多张结构相同的表」场景；常规单表请用 create_plugin_table()。
    表名会随调用时刻变化，不可用于重装后仍需恢复数据的持久业务表。
    """
    if not base_name.startswith(plugin_id + '_'):
        raise ValueError(f'表名必须以插件前缀「{plugin_id}_」开头')
    return f'{base_name}_{datetime.now().strftime("%Y%m%d%H%M")}'


def find_orphan_tables():
    """v12.6.6 孤儿表扫描：返回「没有任何启用中插件使用、也不在平台白名单」的表清单。
    每项：{name, rows, bytes, hint}
    判定规则：
    - 豁免：平台系统表/遗留表、旧 8 模块前缀、sqlite_* 内部表
    - 豁免：plugin_tables.actual_name（统一建表入口登记的）
    - 豁免：plugin_registry 中 enabled=1 的插件声明的 tables（JSON 数组）
    其余即为孤儿表。
    """
    import json
    db = get_db()
    # 1) plugin_tables 登记
    reg_actuals = {r['actual_name'] for r in db.execute(
        'SELECT actual_name FROM plugin_tables').fetchall()}
    # 2) 启用中插件声明的 tables（plugin_registry.tables 是 JSON 数组）
    for r in db.execute('SELECT id, tables FROM plugin_registry WHERE enabled=1').fetchall():
        try:
            for t in (json.loads(r['tables'] or '[]') or []):
                reg_actuals.add(t)
        except Exception:
            pass
    # 3) 所有用户表
    all_tabs = [x[0] for x in db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' "
        "ORDER BY name").fetchall()]
    out = []
    for t in all_tabs:
        if t in reg_actuals or t in _PLATFORM_SYSTEM_TABLES or t in _PLATFORM_LEGACY_TABLES:
            continue
        # 注意：不使用 _LEGACY_TABLE_PREFIXES 豁免——那个白名单是给「裸建表告警」用的，
        # 旧前缀插件卸载后，其前缀表正是要清理的孤儿（如 reqcollect_*）。
        # wbs_/univer_/ai_ 等在用表已通过 plugin_registry.tables 登记豁免。
        try:
            rows = db.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
        except Exception:
            rows = -1
        try:
            bytes_ = db.execute(
                "SELECT SUM(pgsize) FROM dbstat WHERE name=?", (t,)).fetchone()[0] or 0
        except Exception:
            bytes_ = 0
        prefix = t.split('_')[0] + '_' if '_' in t else '(无前缀)'
        out.append({'name': t, 'rows': rows, 'bytes': bytes_, 'hint': prefix})
    return out


def drop_orphan_table(table_name):
    """v12.6.6 删除指定孤儿表（带安全校验：必须在孤儿清单内才允许删）。
    返回 (ok, msg)。
    """
    db = get_db()
    orphans = {o['name'] for o in find_orphan_tables()}
    if table_name not in orphans:
        return False, f'表「{table_name}」不在孤儿清单内（可能是在用表或平台表），拒绝删除'
    try:
        db.execute(f'DROP TABLE IF EXISTS "{table_name}"')
        db.execute("DELETE FROM sqlite_sequence WHERE name=?", (table_name,))
        # 同步清掉 plugin_tables 里若有残留登记
        db.execute('DELETE FROM plugin_tables WHERE actual_name=?', (table_name,))
        db.commit()
        return True, f'已删除孤儿表「{table_name}」'
    except Exception as e:
        db.rollback()
        return False, f'删除失败：{e}'


def check_plugin_tables_integrity():
    """启动一致性校验（v12.4.5）：
    返回 (warnings, infos)：
    - warnings：登记的 actual_name 在 sqlite_master 中不存在（插件声明了表但未建/未走统一入口建错名）
    - infos：库中存在未登记的业务表（裸建表，建议改走 create_plugin_table() 补登记）
    存量平台表/存量插件表白名单豁免。
    """
    main = _main_db()
    warnings, infos = [], []
    try:
        rows = main.execute('SELECT plugin_id, logical_name, actual_name FROM plugin_tables '
                            'ORDER BY plugin_id, logical_name').fetchall()
        for r in rows:
            # v12.7.0：upload 插件的物理表在独立库，按插件路由目标库校验
            check_db = _db_for_plugin_id(r['plugin_id'])
            exists = check_db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                                      (r['actual_name'],)).fetchone()
            if not exists:
                warnings.append(f'插件「{r["plugin_id"]}」登记表 {r["logical_name"]} → '
                                f'实际 {r["actual_name"]} 尚未建表，请检查是否调用 create_plugin_table()')
        reg_actuals = {r['actual_name'] for r in rows}
        all_tabs = [x[0] for x in main.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall()]
        for t in all_tabs:
            if t in reg_actuals or t in _PLATFORM_SYSTEM_TABLES or t in _PLATFORM_LEGACY_TABLES:
                continue
            if t.startswith(_LEGACY_TABLE_PREFIXES):
                continue
            infos.append(f'表「{t}」未走平台统一建表入口（建议插件改用 create_plugin_table() 登记）')
    except Exception as e:
        warnings.append(f'表一致性校验执行异常：{e}')
    return warnings, infos
