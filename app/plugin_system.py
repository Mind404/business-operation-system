# -*- coding: utf-8 -*-
"""
插件化基底：插件注册表 + 用户订阅 + 动态导航 + 路由守卫
v10.5.0 只保留 WBS：核心常驻（个人中心/工作台）+ 唯一业务插件 WBS（所有登录用户可用）
"""
import json
import sqlite3
from datetime import datetime
from flask import g, session, current_app, redirect, url_for, request, flash

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


def get_db():
    if 'db' not in g:
        g.db = sqlite3.connect(current_app.config['DATABASE'])
        g.db.row_factory = sqlite3.Row
        g.db.execute('PRAGMA foreign_keys = ON')
    return g.db


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
    for p in PLUGINS:
        db.execute('''INSERT INTO plugin_registry
            (id, name, icon, grp, core, description, endpoint, tables) VALUES (?,?,?,?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET
                name=excluded.name, icon=excluded.icon, grp=excluded.grp, core=excluded.core,
                description=excluded.description, endpoint=excluded.endpoint, tables=excluded.tables''',
            (p['id'], p['name'], p['icon'], p['group'], p['core'], p['desc'], p['endpoint'],
             json.dumps(p.get('tables', []), ensure_ascii=False)))
    # v10.5.0：清理已下线插件的注册与订阅记录（业务数据表保留不动）
    valid_ids = [p['id'] for p in PLUGINS]
    placeholders = ','.join('?' * len(valid_ids))
    db.execute(f'DELETE FROM plugin_registry WHERE id NOT IN ({placeholders})', valid_ids)
    db.execute(f'DELETE FROM user_plugins WHERE plugin_id NOT IN ({placeholders})', valid_ids)
    db.commit()


def get_user_plugin_ids(user_id):
    """返回用户订阅的业务插件id集合；首次使用只给核心，不自动订阅任何业务插件。"""
    db = get_db()
    rows = db.execute('SELECT plugin_id FROM user_plugins WHERE user_id=?', (user_id,)).fetchall()
    return {r['plugin_id'] for r in rows}


def inject_nav_context():
    """注入模板导航：核心（个人中心/工作台）常驻 + WBS（所有登录用户可见，无需订阅）"""
    user_id = session.get('user_id')
    if not user_id:
        return {'nav_core': [], 'nav_biz': [], 'nav_all_plugins': PLUGINS, 'user_subscribed': set()}
    registered = set(current_app.view_functions.keys())
    available = [p for p in PLUGINS if p['endpoint'] in registered]
    core_plugins = [p for p in available if p['core']]
    # v10.5.0：WBS 为唯一业务插件，所有登录用户常驻可见（无需订阅）
    biz_plugins = [p for p in available if not p['core'] and p['id'] == 'wbs']
    return {'nav_core': core_plugins, 'nav_biz': biz_plugins,
            'nav_all_plugins': available, 'user_subscribed': set()}


def register_plugin_guard(app):
    """路由守卫（v10.5.0）：WBS 为唯一业务插件，所有登录用户可用，不设订阅墙"""
    @app.before_request
    def _guard():
        if request.path.startswith(_WHITELIST_PREFIX):
            return None
        # 静态资源、核心路由等放行
        ep = request.endpoint
        if not ep or ep in ('static', 'plugin_center', 'toggle_plugin', 'profile',
                            'login', 'logout', 'index', 'logs', 'api_stats', 'api_global_search'):
            return None
        # 前缀守卫兜底——子路由（URL 直连）也按路径判归属
        for free in _GUARD_FREE_PATHS:
            if request.path == free or request.path.startswith(free + '/'):
                return None
        # 该 endpoint 属于某个业务插件？（仅 wbs）
        pid = _BIZ_ENDPOINTS.get(ep)
        if not pid:
            pid = _plugin_id_for_path(request.path)
        if not pid:
            return None
        # 核心插件 endpoint 放行
        if pid in ('profile', 'wbs'):
            return None
        # 其他业务插件（已删除）不应再出现：直接拒绝
        user_id = session.get('user_id')
        if not user_id:
            return redirect(url_for('login'))
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
