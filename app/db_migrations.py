# -*- coding: utf-8 -*-
"""
数据库迁移管理：schema_version 记录版本号，按版本跑增量迁移。
【v10.3.0 方案A】schema.sql 已是唯一权威建表源（51张表全量定义）；
本模块仅保留两职责：
  1) 兼容历史库的幂等兜底（CREATE TABLE IF NOT EXISTS，重复执行无副作用）
  2) 增量迁移 run_migrations（未来新版本只在此追加 ALTER/新增列，不回改 schema.sql）
"""
import sqlite3
from datetime import datetime


def get_db():
    from flask import g, current_app
    if 'db' not in g:
        g.db = sqlite3.connect(current_app.config['DATABASE'])
        g.db.row_factory = sqlite3.Row
        g.db.execute('PRAGMA foreign_keys = ON')
    return g.db


def _get_version(db):
    """读当前 schema 版本，没有就返回 0"""
    try:
        row = db.execute('SELECT version FROM schema_version ORDER BY applied_at DESC LIMIT 1').fetchone()
        return row['version'] if row else 0
    except Exception:
        return 0


def _set_version(db, version, note=''):
    db.execute('INSERT OR IGNORE INTO schema_version (version, note, applied_at) VALUES (?,?,?)',
                (version, note, datetime.now().strftime('%Y-%m-%d %H:%M:%S')))


# ============================================================
# 核心表（基底常驻：用户、插件注册、订阅关系、版本记录）
# ============================================================
def init_core_tables():
    db = get_db()
    # users 表结构与 schema.sql 权威定义保持一致（display_name/is_active）
    db.execute('''CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT NOT NULL UNIQUE, password TEXT NOT NULL,
        display_name TEXT, role TEXT DEFAULT 'user', is_active INTEGER DEFAULT 1,
        created_at TEXT DEFAULT (datetime('now','localtime')))''')
    # 兼容历史库（旧结构为 name/department）：补齐 display_name/is_active
    cols = [r[1] for r in db.execute('PRAGMA table_info(users)').fetchall()]
    if 'display_name' not in cols:
        db.execute("ALTER TABLE users ADD COLUMN display_name TEXT")
    if 'is_active' not in cols:
        db.execute("ALTER TABLE users ADD COLUMN is_active INTEGER DEFAULT 1")
    db.execute('''CREATE TABLE IF NOT EXISTS plugin_registry (
        id TEXT PRIMARY KEY, name TEXT, icon TEXT, grp TEXT, core INTEGER DEFAULT 0,
        description TEXT, endpoint TEXT, tables TEXT, enabled INTEGER DEFAULT 1,
        install_type TEXT DEFAULT 'builtin')''')
    db.execute('''CREATE TABLE IF NOT EXISTS user_plugins (
        user_id INTEGER, plugin_id TEXT, subscribed_at TEXT,
        PRIMARY KEY (user_id, plugin_id))''')
    db.execute('''CREATE TABLE IF NOT EXISTS schema_version (
        version INTEGER PRIMARY KEY, note TEXT, applied_at TEXT)''')
    db.commit()


# ============================================================
# WBS 插件表（自己管，不污染其他插件）
# ============================================================
def init_wbs_tables():
    db = get_db()
    db.execute('''CREATE TABLE IF NOT EXISTS wbs_projects (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT, description TEXT, owner TEXT, participants TEXT,
        start_date TEXT, end_date TEXT, status TEXT DEFAULT '进行中',
        created_at TEXT, updated_at TEXT)''')
    db.execute('''CREATE TABLE IF NOT EXISTS wbs_tasks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        project_id INTEGER, wbs_code TEXT, parent_id INTEGER DEFAULT 0,
        name TEXT, work_content TEXT, owner TEXT, collaborators TEXT,
        plan_start TEXT, plan_end TEXT, deliverable TEXT,
        priority TEXT DEFAULT 'P2中', attachment_path TEXT,
        notes TEXT,
        status TEXT DEFAULT '未开始', progress INTEGER DEFAULT 0,
        sort_order INTEGER DEFAULT 0,
        approval_status TEXT DEFAULT '已通过',
        remark TEXT,
        FOREIGN KEY (project_id) REFERENCES wbs_projects(id) ON DELETE CASCADE)''')
    db.commit()


# v12.4.8 清理：外部技术需求插件表（tech_requirements/tech_req_logs）已随 8 模块业务表一并删除，
# 原 init_tech_req_tables() 建表函数移除（避免新库迁移重建已废弃表）


# ============================================================
# 迁移脚本：版本号递增，每步幂等
# 以后加字段就在这里加一个 _migrate_vX 函数
# ============================================================
MIGRATIONS = [
    # (版本号, 说明, 函数)
    # v1: 初始 schema
    (1, '初始表结构', None),
    # v2: wbs_tasks 加 priority/attachment_path/notes 字段
    (2, 'wbs_tasks 加 priority/attachment_path/notes', None),
    # v3: wbs_projects 加 participants 字段
    (3, 'wbs_projects 加 participants', None),
    # v4: wbs_tasks 加 approval_status / wbs_projects 加 approver+approval_status / wbs_tasks 加 remark
    (4, '两级审批字段+任务备注', None),
    # v5: 方案A数据库地基 —— plugin_registry 加 tables 列（表清单登记）+ 新建 approval_requests 统一审批表
    (5, 'v10.3.1: plugin_registry.tables + approval_requests 统一审批表', None),
    # v6: v12.1.0 平台定位 —— plugin_registry 加 enabled 列（平台级启停）+ 三级角色（老库 admin → platform_admin）
    (6, 'v12.1.0: plugin_registry.enabled + 三级角色', None),
    # v7: v12.4.1 平台 AI 助手 —— ai_providers（模型服务商配置）+ ai_chat_logs（问答记录，仅平台管理员）
    (7, 'v12.4.1: ai_providers + ai_chat_logs', None),
    # v8: v12.4.5 插件表管理 —— plugin_tables（插件逻辑表名 → 平台分配实际表名的登记表）
    (8, 'v12.4.5: plugin_tables 表名登记（统一建表入口 + 撞名自动分配）', None),
    # v9: v12.5.17 用户管理 —— users 加 deleted 列（删除用户=软删标记，列表隐藏/禁登录，历史归属保留）
    (9, 'v12.5.17: users.deleted 软删标记', None),
]


def _clean_wbs_date_format(db):
    """v12.5.11：历史数据中 plan_start/plan_end 带 '00:00:00' 的统一归一化为 YYYY-MM-DD（幂等，无条件执行）"""
    for col in ('plan_start', 'plan_end'):
        db.execute(f"UPDATE wbs_tasks SET {col}=substr({col},1,10) WHERE {col} LIKE '____-__-__ __:__:__'")


def run_migrations():
    """按版本号跑迁移，已跑过的跳过"""
    db = get_db()
    current = _get_version(db)
    for ver, note, _fn in MIGRATIONS:
        if ver <= current:
            continue
        # 每个迁移的具体 ALTER TABLE 在这里写（幂等：重复跑不报错）
        if ver == 2:
            for col, ddl in [('priority', "TEXT DEFAULT 'P2中'"),
                             ('attachment_path', 'TEXT'),
                             ('notes', 'TEXT')]:
                try:
                    db.execute(f'ALTER TABLE wbs_tasks ADD COLUMN {col} {ddl}')
                except sqlite3.OperationalError:
                    pass  # 字段已存在
        elif ver == 3:
            try:
                db.execute("ALTER TABLE wbs_projects ADD COLUMN participants TEXT")
            except sqlite3.OperationalError:
                pass
        elif ver == 4:
            # 两级审批：任务审批状态；项目审批人+审批状态；任务备注
            for col, ddl in [('approval_status', "TEXT DEFAULT '已通过'"),
                             ('remark', 'TEXT')]:
                try:
                    db.execute(f'ALTER TABLE wbs_tasks ADD COLUMN {col} {ddl}')
                except sqlite3.OperationalError:
                    pass
            for col, ddl in [('approver', 'TEXT'),
                             ('approval_status', "TEXT DEFAULT '已通过'")]:
                try:
                    db.execute(f'ALTER TABLE wbs_projects ADD COLUMN {col} {ddl}')
                except sqlite3.OperationalError:
                    pass
        elif ver == 5:
            # 方案A地基：plugin_registry 加 tables 列（表清单登记）
            cols = [r[1] for r in db.execute('PRAGMA table_info(plugin_registry)').fetchall()]
            if 'tables' not in cols:
                try:
                    db.execute('ALTER TABLE plugin_registry ADD COLUMN tables TEXT')
                except sqlite3.OperationalError:
                    pass
            # 统一审批表（旧库升级时补建，幂等）
            db.execute('''CREATE TABLE IF NOT EXISTS approval_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                plugin_id TEXT, record_type TEXT, record_id INTEGER,
                requester_id INTEGER, action TEXT, payload TEXT,
                status TEXT DEFAULT '待审批', reviewer_id INTEGER,
                review_comment TEXT,
                created_at TEXT DEFAULT (datetime('now','localtime')),
                reviewed_at TEXT)''')
        elif ver == 6:
            # v12.1.0 三级角色：老库两级 'admin' 全部升级为 'platform_admin'
            try:
                db.execute("UPDATE users SET role='platform_admin' WHERE role='admin'")
            except sqlite3.OperationalError:
                pass
            # users 补 department 列（用户管理创建/编辑引用，v10.5.0 缺失会导致建用户报错）
            ucols = [r[1] for r in db.execute('PRAGMA table_info(users)').fetchall()]
            if 'department' not in ucols:
                try:
                    db.execute('ALTER TABLE users ADD COLUMN department TEXT')
                except sqlite3.OperationalError:
                    pass
            # plugin_registry 加 enabled 列（平台级启用/停用）
            cols = [r[1] for r in db.execute('PRAGMA table_info(plugin_registry)').fetchall()]
            if 'enabled' not in cols:
                try:
                    db.execute('ALTER TABLE plugin_registry ADD COLUMN enabled INTEGER DEFAULT 1')
                except sqlite3.OperationalError:
                    pass
            # install_type：builtin=平台内置 / upload=管理页上传安装（仅 upload 可卸载）
            if 'install_type' not in cols:
                try:
                    db.execute("ALTER TABLE plugin_registry ADD COLUMN install_type TEXT DEFAULT 'builtin'")
                except sqlite3.OperationalError:
                    pass
        elif ver == 7:
            # v12.4.1 平台 AI 助手：模型服务商配置 + 问答记录（仅平台管理员使用）
            db.execute('''CREATE TABLE IF NOT EXISTS ai_providers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                api_url TEXT,
                api_key TEXT DEFAULT '',
                models TEXT DEFAULT '',
                default_model TEXT DEFAULT '',
                enabled INTEGER DEFAULT 0,
                created_at TEXT DEFAULT (datetime('now','localtime')),
                updated_at TEXT DEFAULT (datetime('now','localtime')))''')
            db.execute('''CREATE TABLE IF NOT EXISTS ai_chat_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                provider_id INTEGER,
                model TEXT,
                question TEXT,
                answer TEXT,
                created_at TEXT DEFAULT (datetime('now','localtime')))''')
        elif ver == 8:
            # v12.4.5 插件表名登记：插件声明的逻辑表名 → 平台分配的实际表名
            # - 同一插件同一逻辑名唯一（重装沿用，不漂移）
            # - 实际表名全局唯一（撞名自动分配 _2/_3 后缀）
            db.execute('''CREATE TABLE IF NOT EXISTS plugin_tables (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                plugin_id TEXT NOT NULL,
                logical_name TEXT NOT NULL,
                actual_name TEXT NOT NULL,
                created_at TEXT DEFAULT (datetime('now','localtime')),
                UNIQUE (plugin_id, logical_name),
                UNIQUE (actual_name))''')
        elif ver == 9:
            # v12.5.17 用户管理：users 加 deleted 软删标记（删除用户=隐藏列表+禁登录，保留历史归属）
            ucols = [r[1] for r in db.execute('PRAGMA table_info(users)').fetchall()]
            if 'deleted' not in ucols:
                try:
                    db.execute('ALTER TABLE users ADD COLUMN deleted INTEGER DEFAULT 0')
                except sqlite3.OperationalError:
                    pass
        _set_version(db, ver, note)
    # v12.5.11：无条件清洗存量日期格式
    _clean_wbs_date_format(db)
    db.commit()


def get_schema_version():
    """返回当前 schema 版本号"""
    return _get_version(get_db())
