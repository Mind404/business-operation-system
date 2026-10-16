# -*- coding: utf-8 -*-
"""
数据库迁移管理：schema_version 记录版本号，按版本跑增量迁移。
每个插件自己负责自己的表，不往公共文件堆字段。
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
    db.execute('''CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE, password TEXT, name TEXT,
        role TEXT DEFAULT 'user', department TEXT,
        created_at TEXT)''')
    db.execute('''CREATE TABLE IF NOT EXISTS plugin_registry (
        id TEXT PRIMARY KEY, name TEXT, icon TEXT, grp TEXT, core INTEGER DEFAULT 0,
        description TEXT, endpoint TEXT)''')
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
        FOREIGN KEY (project_id) REFERENCES wbs_projects(id) ON DELETE CASCADE)''')
    db.commit()


# ============================================================
# 外部技术需求插件表（自己管）
# ============================================================
def init_tech_req_tables():
    db = get_db()
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
    db.commit()


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
]


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
        _set_version(db, ver, note)
    db.commit()


def get_schema_version():
    """返回当前 schema 版本号"""
    return _get_version(get_db())
