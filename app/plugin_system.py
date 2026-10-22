# -*- coding: utf-8 -*-
"""
插件化基底：插件注册表 + 用户订阅 + 动态导航 + 路由守卫 + 平台统一审批服务
v11.0.0 平台化：核心平台（聚合视图/个人中心/用户管理/统一审批/外部系统接入）
+ 强管控业务插件：WBS计划管理、需求收集（所有登录用户可用）
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
    # --- 核心常驻（平台能力） ---
    {'id': 'profile',      'name': '个人中心',         'icon': '👤', 'group': '核心', 'core': 1,
     'desc': '我的信息、我的待办、修改密码', 'endpoint': 'profile', 'nav_path': '/profile'},
    {'id': 'workbench',    'name': '聚合视图',         'icon': '📊', 'group': '核心', 'core': 1,
     'desc': '平台聚合视图：WBS/需求/外部接入数据统一总览', 'endpoint': 'index', 'nav_path': '/',
     'tables': [{'table': '平台总览', 'desc': 'WBS/需求/外部系统数据统一聚合视图，不做独立存储'}]},

    # --- 强管控业务插件（v11.0：平台只做强管控设计这两个方向） ---
    {'id': 'wbs',          'name': 'WBS计划管理',      'icon': '🗓️', 'group': '业务', 'core': 0,
     'desc': '项目工作分解结构：父子任务树、负责人、协作人、时间窗、交付物', 'endpoint': 'wbs.wbs_projects', 'nav_path': '/wbs',
     'tables': [
         {'table': 'wbs_projects', 'desc': '项目主表（名称/负责人/时间窗/审批状态）'},
         {'table': 'wbs_tasks',    'desc': '任务树（父子级/负责人/协作人/时间窗/进度/审批）'}]},
    {'id': 'requirements', 'name': '需求收集',         'icon': '📋', 'group': '业务', 'core': 0,
     'desc': '需求强管控：收集、评审、状态流转、审批、追溯', 'endpoint': 'requirements.req_list', 'nav_path': '/requirements',
     'tables': [
         {'table': 'req_items',        'desc': '需求主表（编号/来源部门/优先级/交付日期/状态流转）'},
         {'table': 'req_reviews',      'desc': '评审记录'},
         {'table': 'req_changes',      'desc': '状态变更历史'},
         {'table': 'req_field_config', 'desc': '自定义字段配置'}]},
]

# 业务插件 endpoint → plugin_id 映射（用于路由守卫）
_BIZ_ENDPOINTS = {p['endpoint']: p['id'] for p in PLUGINS if not p['core']}

# v10.3.2 前缀守卫：插件子路由也纳入校验（防 URL 直连绕过）
_PLUGIN_PATH_PREFIXES = {
    'wbs': ('/wbs',),
    'requirements': ('/requirements', '/tech-reqs'),
}

# 免前缀守卫的全局路径（登录/静态/个人中心/插件中心/工作台/统一审批/用户管理）
_GUARD_FREE_PATHS = ('/login', '/static', '/plugins', '/profile', '/logout', '/approvals', '/', '/admin')

# 平台强管控插件（所有登录用户常驻可见，无需订阅）
_ALWAYS_ON_BIZ = ('wbs', 'requirements')


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
    from db_migrations import (init_core_tables, init_wbs_tables, init_req_tables,
                               init_platform_tables, run_migrations)
    db = get_db()
    # 核心表（用户、插件注册、订阅、版本记录）
    init_core_tables()
    # WBS 业务表
    init_wbs_tables()
    # 需求收集业务表（v11.0 全新设计强管控）
    init_req_tables()
    # 平台级表：统一审批流、外部系统接入
    init_platform_tables()
    # 跑增量迁移（旧库升级：approval_requests 加多级审批字段）
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
    """注入模板导航：核心（个人中心/聚合视图）常驻 + 业务插件（强管控常驻 + 已订阅插件）"""
    user_id = session.get('user_id')
    if not user_id:
        return {'nav_core': [], 'nav_biz': [], 'nav_all_plugins': PLUGINS, 'user_subscribed': set()}
    registered = set(current_app.view_functions.keys())
    available = [p for p in PLUGINS if p['endpoint'] in registered]
    core_plugins = [p for p in available if p['core']]
    # v11.1.1：强管控（WBS/需求）常驻 + 已订阅的业务插件显示在「我的工作」；后续新插件默认需订阅
    subscribed_ids = get_user_plugin_ids(user_id)
    biz_plugins = [p for p in available if not p['core'] and
                   (p['id'] in _ALWAYS_ON_BIZ or p['id'] in subscribed_ids)]
    return {'nav_core': core_plugins, 'nav_biz': biz_plugins,
            'nav_all_plugins': available, 'user_subscribed': subscribed_ids}


def register_plugin_guard(app):
    """路由守卫（v11.0）：强管控业务插件（WBS/需求）所有登录用户可用，不设订阅墙"""
    @app.before_request
    def _guard():
        if request.path.startswith(_WHITELIST_PREFIX):
            return None
        # 静态资源、核心路由等放行
        ep = request.endpoint
        if not ep or ep in ('static', 'plugin_center', 'toggle_plugin', 'profile',
                            'login', 'logout', 'index', 'logs', 'api_stats', 'api_global_search',
                            'approval_center', 'approval_review', 'admin_external',
                            'assistant',
                            'admin_users', 'admin_users_create', 'admin_users_update',
                            'admin_users_toggle', 'admin_users_reset_pwd'):
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
            return None
        # 核心插件 + 强管控业务插件 endpoint 放行
        if pid in ('profile', 'wbs', 'requirements'):
            return None
        # 其他业务插件（未接入）不应再出现：直接拒绝
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


def submit_approval(plugin_id, record_type, record_id, requester_id, action, payload=None,
                    flow_id=None, current_step=1):
    """提交审批申请（v11.0 统一审批流：可指定审批流与当前步骤）"""
    db = get_db()
    cur = db.execute(
        'INSERT INTO approval_requests (plugin_id, record_type, record_id, requester_id, action, payload, flow_id, current_step) '
        'VALUES (?,?,?,?,?,?,?,?)',
        (plugin_id, record_type, record_id, requester_id, action,
         json.dumps(payload, ensure_ascii=False) if payload else None, flow_id, current_step))
    db.commit()
    return cur.lastrowid


def get_flow_steps(flow_id):
    """返回审批流步骤列表 [{'step':1,'role':'admin'},...]；无配置默认单级 admin"""
    db = get_db()
    if flow_id:
        row = db.execute('SELECT steps FROM approval_flows WHERE id=?', (flow_id,)).fetchone()
        if row and row['steps']:
            try:
                return json.loads(row['steps'])
            except Exception:
                pass
    return [{'step': 1, 'role': 'admin'}]


def review_approval(req_id, reviewer_id, approve, comment=''):
    """平台统一审批（v11.0 多级审批流）：
    approve=True：非末级 → 进入下一级待审批；末级 → 完成（completed=True 供回调落地）
    approve=False：终止驳回
    """
    db = get_db()
    row = db.execute('SELECT * FROM approval_requests WHERE id=?', (req_id,)).fetchone()
    if not row or row['status'] != '待审批':
        return False, '审批申请不存在或已处理', False
    steps = get_flow_steps(row['flow_id'])
    current = row['current_step'] or 1
    if not approve:
        db.execute('UPDATE approval_requests SET status=?, reviewer_id=?, review_comment=?, reviewed_at=? WHERE id=?',
                   ('已驳回', reviewer_id, comment, datetime.now().strftime('%Y-%m-%d %H:%M:%S'), req_id))
        db.commit()
        log_operation(row['plugin_id'], '审批', f"#{req_id} {row['record_type']} 已驳回{('：'+comment) if comment else ''}", '')
        return True, '已驳回', False
    # 通过
    next_step = current + 1
    if next_step <= len(steps):
        db.execute('UPDATE approval_requests SET current_step=?, reviewer_id=?, review_comment=? WHERE id=?',
                   (next_step, reviewer_id, comment or f'第{current}级通过', req_id))
        db.commit()
        log_operation(row['plugin_id'], '审批', f"#{req_id} {row['record_type']} 第{current}级通过，进入第{next_step}级", '')
        return True, f'已通过第{current}级', False
    # 全部通过
    db.execute('UPDATE approval_requests SET status=?, reviewer_id=?, review_comment=?, reviewed_at=? WHERE id=?',
               ('已通过', reviewer_id, comment, datetime.now().strftime('%Y-%m-%d %H:%M:%S'), req_id))
    db.commit()
    log_operation(row['plugin_id'], '审批', f"#{req_id} {row['record_type']} 审批通过", '')
    return True, '已通过', True


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
