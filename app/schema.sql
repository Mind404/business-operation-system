-- ============================================================
-- AI驱动的业务高效运转系统 - 数据库权威结构 (schema.sql)
-- 生成方式：从运行库 business.db 直接导出（v8.6.0）
-- 与当前系统运行逻辑完全一致，可用于重建完整库
-- 注意：requirements 表含 is_deleted（软删除标记，v8.6.0）
-- ============================================================
PRAGMA foreign_keys = ON;

-- ============================================================
-- AI驱动的业务高效运转系统 - 数据库权威结构 (schema.sql)
-- 生成方式：从运行库 business.db 直接导出（v8.6.0）
-- 与当前系统运行逻辑完全一致，可用于重建完整库
-- 注意：requirements 表含 is_deleted（软删除标记，v8.6.0）
-- ============================================================
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS operation_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    module TEXT,
    action TEXT,
    detail TEXT,
    operator TEXT DEFAULT '系统',
    created_at TEXT DEFAULT (datetime('now','localtime'))
);
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE,
    password TEXT NOT NULL,
    display_name TEXT,
    role TEXT DEFAULT 'user',
    is_active INTEGER DEFAULT 1,
    created_at TEXT DEFAULT (datetime('now','localtime')),
    deleted INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS plugin_registry (
    id TEXT PRIMARY KEY, name TEXT, icon TEXT, grp TEXT, core INTEGER DEFAULT 0,
    description TEXT, endpoint TEXT, tables TEXT DEFAULT '[]'
);
CREATE TABLE IF NOT EXISTS user_plugins (
    user_id INTEGER, plugin_id TEXT, subscribed_at TEXT,
    PRIMARY KEY (user_id, plugin_id)
);
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY, note TEXT, applied_at TEXT
);

-- WBS 插件表（含审批字段：approval_status/approver）
CREATE TABLE IF NOT EXISTS wbs_projects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT, description TEXT, owner TEXT,
    start_date TEXT, end_date TEXT, status TEXT DEFAULT '进行中', project_type TEXT DEFAULT '研发项目',
    created_at TEXT, updated_at TEXT, participants TEXT, approver TEXT DEFAULT '', approval_status TEXT DEFAULT '已通过'
);
CREATE TABLE IF NOT EXISTS wbs_tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER, wbs_code TEXT, parent_id INTEGER DEFAULT 0,
    name TEXT, work_content TEXT, owner TEXT, collaborators TEXT,
    plan_start TEXT, plan_end TEXT, deliverable TEXT,
    status TEXT DEFAULT '未开始', progress INTEGER DEFAULT 0,
    sort_order INTEGER DEFAULT 0, priority TEXT DEFAULT 'P2中', attachment_path TEXT, participants TEXT, notes TEXT, approval_status TEXT DEFAULT '已通过', remark TEXT,
    FOREIGN KEY (project_id) REFERENCES wbs_projects(id) ON DELETE CASCADE
);

-- 外部技术需求插件表

-- v12.7.10.10 单账号单设备登录（挤下线）：一个账号只保留一个有效登录令牌
CREATE TABLE IF NOT EXISTS user_sessions (
    user_id INTEGER PRIMARY KEY,
    token TEXT NOT NULL,
    ip TEXT,
    user_agent TEXT,
    login_at TEXT,
    last_seen TEXT
);

-- v12.7.10.10 平台级界面偏好（导航拖拽顺序等），键值存储
CREATE TABLE IF NOT EXISTS ui_prefs (
    k TEXT PRIMARY KEY,
    v TEXT,
    updated_at TEXT
);
