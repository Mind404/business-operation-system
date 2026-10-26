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
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_no TEXT UNIQUE,
    title TEXT NOT NULL,
    description TEXT,
    module TEXT,
    source_type TEXT DEFAULT '手动创建',
    source_id INTEGER,
    assignee TEXT,
    creator TEXT,
    priority TEXT DEFAULT '中',
    status TEXT DEFAULT '待处理',
    due_date TEXT,
    expected_result TEXT,
    actual_result TEXT,
    progress INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now','localtime')),
    updated_at TEXT DEFAULT (datetime('now','localtime')),
    completed_at TEXT
);
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE,
    password TEXT NOT NULL,
    display_name TEXT,
    role TEXT DEFAULT 'user',
    is_active INTEGER DEFAULT 1,
    created_at TEXT DEFAULT (datetime('now','localtime'))
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

-- 统一审批表（方案A：其他插件第一阶段统一走此表，WBS 保持现有审批）
CREATE TABLE IF NOT EXISTS approval_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    plugin_id TEXT NOT NULL,
    record_type TEXT NOT NULL,
    record_id INTEGER NOT NULL,
    requester_id INTEGER NOT NULL,
    action TEXT NOT NULL,
    payload TEXT,
    status TEXT DEFAULT '待审批',
    reviewer_id INTEGER,
    review_comment TEXT,
    created_at TEXT DEFAULT (datetime('now','localtime')),
    reviewed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_approval_status ON approval_requests(status);

-- WBS 插件表（含审批字段：approval_status/approver）
CREATE TABLE IF NOT EXISTS wbs_projects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT, description TEXT, owner TEXT,
    start_date TEXT, end_date TEXT, status TEXT DEFAULT '进行中',
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
