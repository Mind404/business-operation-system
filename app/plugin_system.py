# -*- coding: utf-8 -*-
"""
插件化基底：插件注册表 + 用户订阅 + 动态导航
每个业务模块是一个插件；核心插件常驻，业务插件按用户订阅显示。
"""
import sqlite3
from flask import g, session, current_app

# ============================================================
# 插件清单（注册到 plugin_registry）
# core=True 表示常驻核心，不可取消订阅
# ============================================================
PLUGINS = [
    # --- 核心常驻 ---
    {'id': 'overview',    'name': '总览看板',         'icon': '🗺️', 'group': '核心', 'core': 1,
     'desc': '领导视角：需求满足时间、版本/测试/生产进度、甘特图', 'endpoint': 'executive_dashboard', 'nav_path': '/executive'},
    {'id': 'workbench',   'name': '我的工作台',       'icon': '📊', 'group': '核心', 'core': 1,
     'desc': '个人待办与快捷入口', 'endpoint': 'index', 'nav_path': '/'},
    {'id': 'requirements','name': '需求数据库',       'icon': '📋', 'group': '核心', 'core': 1,
     'desc': '用户需求/集团系统侧/应用侧/领导任务，全流程源头', 'endpoint': 'requirements_list', 'nav_path': '/requirements'},
    # --- 业务插件（可订阅） ---
    {'id': 'tech_req',    'name': '外部技术需求统计', 'icon': '🔬', 'group': '业务', 'core': 0,
     'desc': '集团系统侧技术特性跟踪：问题项 + 每日进展日志 + 版本响应要求', 'endpoint': 'ext.tech_req_list', 'nav_path': '/tech-reqs'},
    {'id': 'wbs',         'name': 'WBS计划管理',      'icon': '🗓️', 'group': '业务', 'core': 0,
     'desc': '项目工作分解结构：父子任务树、负责人、协作人、时间窗、交付物、甘特视图', 'endpoint': 'ext.wbs_projects', 'nav_path': '/wbs'},
    {'id': 'planning',    'name': '产品与版本规划',   'icon': '📐', 'group': '业务', 'core': 0,
     'desc': '产品定义、软件版本规划、开发任务分解、技术规格', 'endpoint': 'planning_list', 'nav_path': '/planning'},
    {'id': 'production',  'name': '研发生产',         'icon': '🏭', 'group': '业务', 'core': 0,
     'desc': '生产排程、工序、物料BOM、资源调度', 'endpoint': 'production_dashboard', 'nav_path': '/production'},
    {'id': 'bug',         'name': 'Bug管理',         'icon': '🐛', 'group': '业务', 'core': 0,
     'desc': 'Bug全生命周期流转、质量问题根因闭环', 'endpoint': 'bug_list', 'nav_path': '/bugs'},
    {'id': 'test',        'name': '测试管理',         'icon': '🧪', 'group': '业务', 'core': 0,
     'desc': '测试用例库、用例执行记录', 'endpoint': 'ext.test_case_list', 'nav_path': '/test'},
    {'id': 'delivery',    'name': '交付管理',         'icon': '📦', 'group': '业务', 'core': 0,
     'desc': '交付跟踪、收入确认、用户合同', 'endpoint': 'ext.delivery_plan_list', 'nav_path': '/delivery'},
    {'id': 'device',      'name': '设备运维与OTA',    'icon': '📡', 'group': '业务', 'core': 0,
     'desc': '设备状态监控、版本变更记录、OTA版本发布', 'endpoint': 'ext.device_center', 'nav_path': '/device'},
    {'id': 'contract',    'name': '合同跟进',         'icon': '📄', 'group': '业务', 'core': 0,
     'desc': '生产合同、物料采购合同、付款节点', 'endpoint': 'ext.contract_list', 'nav_path': '/contracts'},
    {'id': 'data_mgmt',   'name': '数据管理',         'icon': '💾', 'group': '业务', 'core': 0,
     'desc': 'Excel导入导出、操作日志查询', 'endpoint': 'data_management', 'nav_path': '/data'},
]


def get_db():
    if 'db' not in g:
        g.db = sqlite3.connect(current_app.config['DATABASE'])
        g.db.row_factory = sqlite3.Row
        g.db.execute('PRAGMA foreign_keys = ON')
    return g.db


def init_plugin_tables():
    """建表并注册插件（幂等）"""
    db = get_db()
    db.execute('''CREATE TABLE IF NOT EXISTS plugin_registry (
        id TEXT PRIMARY KEY, name TEXT, icon TEXT, grp TEXT, core INTEGER DEFAULT 0,
        description TEXT, endpoint TEXT)''')
    db.execute('''CREATE TABLE IF NOT EXISTS user_plugins (
        user_id INTEGER, plugin_id TEXT, subscribed_at TEXT,
        PRIMARY KEY (user_id, plugin_id))''')
    db.execute('''CREATE TABLE IF NOT EXISTS tech_requirements (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        stage TEXT, category TEXT, item TEXT, planned_date TEXT,
        description TEXT, version_response TEXT,
        product_id INTEGER DEFAULT 1, status TEXT DEFAULT '进行中',
        sort_order INTEGER DEFAULT 0,
        created_at TEXT, updated_at TEXT)''')
    db.execute('''CREATE TABLE IF NOT EXISTS tech_req_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        tech_req_id INTEGER, log_date TEXT, content TEXT, author TEXT,
        created_at TEXT,
        FOREIGN KEY (tech_req_id) REFERENCES tech_requirements(id) ON DELETE CASCADE)''')
    db.execute('''CREATE TABLE IF NOT EXISTS wbs_projects (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT, description TEXT, owner TEXT,
        start_date TEXT, end_date TEXT, status TEXT DEFAULT '进行中',
        created_at TEXT, updated_at TEXT)''')
    db.execute('''CREATE TABLE IF NOT EXISTS wbs_tasks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        project_id INTEGER, wbs_code TEXT, parent_id INTEGER DEFAULT 0,
        name TEXT, work_content TEXT, owner TEXT, collaborators TEXT,
        plan_start TEXT, plan_end TEXT, deliverable TEXT,
        status TEXT DEFAULT '未开始', progress INTEGER DEFAULT 0,
        sort_order INTEGER DEFAULT 0,
        FOREIGN KEY (project_id) REFERENCES wbs_projects(id) ON DELETE CASCADE)''')
    # 注册插件
    for p in PLUGINS:
        db.execute('''INSERT OR IGNORE INTO plugin_registry
            (id, name, icon, grp, core, description, endpoint) VALUES (?,?,?,?,?,?,?)''',
            (p['id'], p['name'], p['icon'], p['group'], p['core'], p['desc'], p['endpoint']))
    db.commit()


def get_user_plugin_ids(user_id):
    """返回用户订阅的插件id集合；未订阅过任何插件时默认全选（平滑过渡）"""
    db = get_db()
    rows = db.execute('SELECT plugin_id FROM user_plugins WHERE user_id=?', (user_id,)).fetchall()
    if not rows:
        # 首次使用：默认订阅全部非核心插件（保持现有界面不突变）
        all_biz = [p['id'] for p in PLUGINS]
        for pid in all_biz:
            db.execute('INSERT OR IGNORE INTO user_plugins (user_id, plugin_id, subscribed_at) VALUES (?,?,?)',
                       (user_id, pid, datetime.now().strftime('%Y-%m-%d %H:%M')))
        db.commit()
        return set(all_biz)
    return {r['plugin_id'] for r in rows}


def inject_nav_context():
    """注入模板导航上下文：核心插件 + 用户订阅的业务插件（仅含路由已注册的）"""
    from flask import current_app
    user_id = session.get('user_id')
    if not user_id:
        return {'nav_core': [], 'nav_biz': [], 'nav_all_plugins': PLUGINS, 'user_subscribed': set()}
    subscribed = get_user_plugin_ids(user_id)
    # 只保留路由已注册的插件（未开发完的不在导航里显示）
    registered = set(current_app.view_functions.keys())
    available = [p for p in PLUGINS if p['endpoint'] in registered]
    core_plugins = [p for p in available if p['core']]
    biz_plugins = [p for p in available if not p['core'] and p['id'] in subscribed]
    return {'nav_core': core_plugins, 'nav_biz': biz_plugins,
            'nav_all_plugins': available, 'user_subscribed': subscribed}


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


from datetime import datetime
