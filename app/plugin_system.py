# -*- coding: utf-8 -*-
"""
插件化基底：插件注册表 + 用户订阅 + 动态导航 + 路由守卫
核心插件（总览看板/个人中心）常驻；其余全部业务插件，未订阅不显示也不可访问。
"""
import sqlite3
from datetime import datetime
from flask import g, session, current_app, redirect, url_for, request, flash

# ============================================================
# 插件清单
# core=1 常驻不可关；core=0 业务插件，用户自行订阅
# ============================================================
PLUGINS = [
    # --- 核心常驻（基底只看个人中心） ---
    {'id': 'profile',      'name': '个人中心',         'icon': '👤', 'group': '核心', 'core': 1,
     'desc': '我的信息、我的待办、我的插件订阅', 'endpoint': 'profile', 'nav_path': '/profile'},

    # --- 业务插件（默认不订阅，去插件中心开启） ---
    {'id': 'overview',    'name': '总览看板',         'icon': '🗺️', 'group': '业务', 'core': 0,
     'desc': '领导视角：需求满足时间、版本/测试/生产进度、甘特图', 'endpoint': 'executive_dashboard', 'nav_path': '/executive'},
    {'id': 'workbench',   'name': '我的工作台',       'icon': '📊', 'group': '业务', 'core': 0,
     'desc': '个人待办与快捷入口', 'endpoint': 'index', 'nav_path': '/'},
    {'id': 'requirements','name': '需求数据库',       'icon': '📋', 'group': '业务', 'core': 0,
     'desc': '用户需求/集团系统侧/应用侧/领导任务，全流程源头', 'endpoint': 'requirements_list', 'nav_path': '/requirements'},
    {'id': 'tech_req',    'name': '外部技术需求统计', 'icon': '🔬', 'group': '业务', 'core': 0,
     'desc': '集团系统侧技术特性跟踪：问题项 + 每日进展日志 + 版本响应要求', 'endpoint': 'ext.tech_req_list', 'nav_path': '/tech-reqs'},
    {'id': 'wbs',         'name': 'WBS计划管理',      'icon': '🗓️', 'group': '业务', 'core': 0,
     'desc': '项目工作分解结构：父子任务树、负责人、协作人、时间窗、交付物', 'endpoint': 'ext.wbs_projects', 'nav_path': '/wbs'},
    {'id': 'planning',    'name': '产品与版本规划',   'icon': '📐', 'group': '业务', 'core': 0,
     'desc': '产品定义、软件版本规划、开发任务分解', 'endpoint': 'planning_list', 'nav_path': '/planning'},
    {'id': 'production',  'name': '研发生产',         'icon': '🏭', 'group': '业务', 'core': 0,
     'desc': '生产排程、工序、物料BOM', 'endpoint': 'production_dashboard', 'nav_path': '/production'},
    {'id': 'bug',         'name': 'Bug管理',         'icon': '🐛', 'group': '业务', 'core': 0,
     'desc': 'Bug全生命周期流转、质量问题根因闭环', 'endpoint': 'bug_list', 'nav_path': '/bugs'},
    {'id': 'test',        'name': '测试管理',         'icon': '🧪', 'group': '业务', 'core': 0,
     'desc': '测试用例库、用例执行记录', 'endpoint': 'ext.test_case_list', 'nav_path': '/test'},
    {'id': 'delivery',    'name': '交付管理',         'icon': '📦', 'group': '业务', 'core': 0,
     'desc': '交付跟踪、交付计划', 'endpoint': 'ext.delivery_plan_list', 'nav_path': '/delivery'},
    {'id': 'device',      'name': '设备运维与OTA',    'icon': '📡', 'group': '业务', 'core': 0,
     'desc': '设备状态监控、版本变更记录、OTA版本发布', 'endpoint': 'ext.device_center', 'nav_path': '/device'},
    {'id': 'contract',    'name': '合同跟进',         'icon': '📄', 'group': '业务', 'core': 0,
     'desc': '生产合同、物料采购合同、付款节点', 'endpoint': 'ext.contract_list', 'nav_path': '/contracts'},
    {'id': 'data_mgmt',   'name': '数据管理',         'icon': '💾', 'group': '业务', 'core': 0,
     'desc': 'Excel导入导出、操作日志查询', 'endpoint': 'data_management', 'nav_path': '/data'},
]

# 业务插件 endpoint → plugin_id 映射（用于路由守卫）
_BIZ_ENDPOINTS = {p['endpoint']: p['id'] for p in PLUGINS if not p['core']}
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
    from db_migrations import init_core_tables, init_wbs_tables, init_tech_req_tables, run_migrations
    db = get_db()
    # 核心表（用户、插件注册、订阅、版本记录）
    init_core_tables()
    # 两个业务插件的表
    init_wbs_tables()
    init_tech_req_tables()
    # 跑增量迁移
    run_migrations()
    # 注册插件清单
    for p in PLUGINS:
        db.execute('''INSERT OR IGNORE INTO plugin_registry
            (id, name, icon, grp, core, description, endpoint) VALUES (?,?,?,?,?,?,?)''',
            (p['id'], p['name'], p['icon'], p['group'], p['core'], p['desc'], p['endpoint']))
    db.commit()


def get_user_plugin_ids(user_id):
    """返回用户订阅的业务插件id集合；首次使用只给核心，不自动订阅任何业务插件。"""
    db = get_db()
    rows = db.execute('SELECT plugin_id FROM user_plugins WHERE user_id=?', (user_id,)).fetchall()
    return {r['plugin_id'] for r in rows}


def inject_nav_context():
    """注入模板导航：核心2个 + 用户订阅的业务插件（仅含路由已注册的）"""
    user_id = session.get('user_id')
    if not user_id:
        return {'nav_core': [], 'nav_biz': [], 'nav_all_plugins': PLUGINS, 'user_subscribed': set()}
    subscribed = get_user_plugin_ids(user_id)
    registered = set(current_app.view_functions.keys())
    available = [p for p in PLUGINS if p['endpoint'] in registered]
    core_plugins = [p for p in available if p['core']]
    biz_plugins = [p for p in available if not p['core'] and p['id'] in subscribed]
    return {'nav_core': core_plugins, 'nav_biz': biz_plugins,
            'nav_all_plugins': available, 'user_subscribed': subscribed}


def register_plugin_guard(app):
    """路由守卫：未订阅的业务插件路由直接跳插件中心，不进视图。"""
    @app.before_request
    def _guard():
        if request.path.startswith(_WHITELIST_PREFIX):
            return None
        # 静态资源、POST 表单提交里的 toggle 等放行
        ep = request.endpoint
        if not ep or ep in ('static', 'plugin_center', 'toggle_plugin', 'profile',
                            'login', 'logout'):
            return None
        # 该 endpoint 属于某个业务插件？
        pid = _BIZ_ENDPOINTS.get(ep)
        if not pid:
            return None
        # 核心插件 endpoint 放行（只有个人中心常驻）
        if pid in ('profile',):
            return None
        user_id = session.get('user_id')
        if not user_id:
            return redirect(url_for('login'))
        if pid not in get_user_plugin_ids(user_id):
            flash('请先在插件中心开启该功能', 'info')
            return redirect(url_for('plugin_center'))
        return None


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
    else:
        db.execute('INSERT INTO user_plugins (user_id, plugin_id, subscribed_at) VALUES (?,?,?)',
                   (user_id, plugin_id, datetime.now().strftime('%Y-%m-%d %H:%M')))
        msg = f'已订阅 {p["name"]}'
    db.commit()
    return True, msg
