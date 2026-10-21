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
    # users 表结构与 schema.sql 权威定义保持一致（display_name/is_active/department）
    db.execute('''CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT NOT NULL UNIQUE, password TEXT NOT NULL,
        display_name TEXT, role TEXT DEFAULT 'user', is_active INTEGER DEFAULT 1,
        department TEXT,
        created_at TEXT DEFAULT (datetime('now','localtime')))''')
    # 兼容历史库（旧结构为 name/department）：补齐 display_name/is_active/department
    cols = [r[1] for r in db.execute('PRAGMA table_info(users)').fetchall()]
    if 'display_name' not in cols:
        db.execute("ALTER TABLE users ADD COLUMN display_name TEXT")
    if 'is_active' not in cols:
        db.execute("ALTER TABLE users ADD COLUMN is_active INTEGER DEFAULT 1")
    if 'department' not in cols:
        try:
            db.execute("ALTER TABLE users ADD COLUMN department TEXT")
        except Exception:
            pass
    db.execute('''CREATE TABLE IF NOT EXISTS plugin_registry (
        id TEXT PRIMARY KEY, name TEXT, icon TEXT, grp TEXT, core INTEGER DEFAULT 0,
        description TEXT, endpoint TEXT, tables TEXT)''')
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
# v11.0 平台级表：统一审批流 + 外部系统接入登记与聚合缓存
# ============================================================
def init_platform_tables():
    db = get_db()
    # 审批流定义（可配置多级：steps 为 JSON 数组 [{"step":1,"role":"admin"},...]）
    db.execute('''CREATE TABLE IF NOT EXISTS approval_flows (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT, plugin_id TEXT, record_type TEXT,
        steps TEXT,
        enabled INTEGER DEFAULT 1,
        created_at TEXT DEFAULT (datetime('now','localtime')))''')
    # 外部系统接入登记（插件接入平台的登记表：内置插件/独立系统均可登记）
    db.execute('''CREATE TABLE IF NOT EXISTS external_systems (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL, plugin_id TEXT, system_type TEXT DEFAULT '业务系统',
        access_method TEXT DEFAULT 'api', conn_config TEXT,
        status TEXT DEFAULT '已登记', note TEXT,
        created_at TEXT DEFAULT (datetime('now','localtime')),
        updated_at TEXT)''')
    # 外部系统数据聚合缓存（只存上报数据快照，展示用；不反向写业务库）
    db.execute('''CREATE TABLE IF NOT EXISTS external_data_cache (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        system_id INTEGER NOT NULL, category TEXT NOT NULL,
        payload TEXT, updated_at TEXT DEFAULT (datetime('now','localtime')),
        FOREIGN KEY (system_id) REFERENCES external_systems(id) ON DELETE CASCADE)''')
    db.commit()


# ============================================================
# v11.0 需求收集插件（全新设计，强管控）：主表/评审/变更留痕/字段配置
# ============================================================
def init_req_tables():
    db = get_db()
    db.execute('''CREATE TABLE IF NOT EXISTS req_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        code TEXT, title TEXT NOT NULL,
        req_type TEXT DEFAULT '用户需求', source_dept TEXT, customer TEXT,
        description TEXT, quantity INTEGER DEFAULT 0, priority TEXT DEFAULT '中',
        delivery_date TEXT, owner TEXT,
        status TEXT DEFAULT '待评审',
        wbs_project_id INTEGER, wbs_task_id INTEGER,
        created_by TEXT, created_at TEXT,
        updated_at TEXT, is_deleted INTEGER DEFAULT 0)''')
    db.execute('''CREATE TABLE IF NOT EXISTS req_reviews (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        req_id INTEGER NOT NULL, reviewer TEXT, review_date TEXT,
        conclusion TEXT DEFAULT '待评审', opinion TEXT,
        created_at TEXT DEFAULT (datetime('now','localtime')),
        FOREIGN KEY (req_id) REFERENCES req_items(id) ON DELETE CASCADE)''')
    db.execute('''CREATE TABLE IF NOT EXISTS req_changes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        req_id INTEGER NOT NULL, field_name TEXT, old_value TEXT, new_value TEXT,
        change_by TEXT, approval_id INTEGER, change_at TEXT,
        FOREIGN KEY (req_id) REFERENCES req_items(id) ON DELETE CASCADE)''')
    # 字段配置（管理员可配：必填/显示）——平台"双层配置灵活"第一层
    db.execute('''CREATE TABLE IF NOT EXISTS req_field_config (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        field_key TEXT UNIQUE, field_label TEXT,
        required INTEGER DEFAULT 0, visible INTEGER DEFAULT 1)''')
    # AI 模型配置（key-value：mode/base_url/api_key/model；管理员在设置页维护）
    db.execute('''CREATE TABLE IF NOT EXISTS ai_config (
        key TEXT PRIMARY KEY,
        value TEXT)''')
    # 默认字段配置（仅首启写入一次）
    default_fields = [
        ('title', '需求标题', 1, 1), ('req_type', '需求类型', 0, 1),
        ('source_dept', '来源部门', 0, 1), ('customer', '客户/提出方', 0, 1),
        ('description', '需求描述', 1, 1), ('quantity', '需求数量', 0, 1),
        ('priority', '优先级', 0, 1), ('delivery_date', '期望交付日期', 0, 1),
        ('owner', '负责人', 0, 1),
    ]
    cnt = db.execute('SELECT COUNT(*) FROM req_field_config').fetchone()[0]
    if cnt == 0:
        db.executemany(
            'INSERT INTO req_field_config (field_key, field_label, required, visible) VALUES (?,?,?,?)',
            default_fields)
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
    # v4: wbs_tasks 加 approval_status / wbs_projects 加 approver+approval_status / wbs_tasks 加 remark
    (4, '两级审批字段+任务备注', None),
    # v5: 方案A数据库地基 —— plugin_registry 加 tables 列（表清单登记）+ 新建 approval_requests 统一审批表
    (5, 'v10.3.1: plugin_registry.tables + approval_requests 统一审批表', None),
    # v6: v11.0 平台化 —— approval_requests 加 flow_id/current_step（多级审批流）
    (6, 'v11.0: approval_requests 支持多级审批流', None),
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
            # v11.0 多级审批流：approval_requests 加 flow_id（审批流）+ current_step（当前步骤）
            cols = [r[1] for r in db.execute('PRAGMA table_info(approval_requests)').fetchall()]
            if 'flow_id' not in cols:
                try:
                    db.execute('ALTER TABLE approval_requests ADD COLUMN flow_id INTEGER')
                except sqlite3.OperationalError:
                    pass
            if 'current_step' not in cols:
                try:
                    db.execute('ALTER TABLE approval_requests ADD COLUMN current_step INTEGER DEFAULT 1')
                except sqlite3.OperationalError:
                    pass
        _set_version(db, ver, note)
    db.commit()


def get_schema_version():
    """返回当前 schema 版本号"""
    return _get_version(get_db())
