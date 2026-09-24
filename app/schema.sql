-- ============================================================
-- AI驱动的业务高效运转系统 - 数据库权威结构 (schema.sql)
-- 生成方式：从运行库 business.db 直接导出（v8.6.0）
-- 与当前系统运行逻辑完全一致，可用于重建完整库
-- 注意：requirements 表含 is_deleted（软删除标记，v8.6.0）
-- ============================================================
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS bom_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id INTEGER NOT NULL,
    material_name TEXT NOT NULL,
    material_code TEXT,
    is_key_material INTEGER DEFAULT 0,
    unit TEXT DEFAULT '个',
    quantity_per_unit REAL DEFAULT 1,
    supplier TEXT,
    FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS bug_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bug_no TEXT UNIQUE,
    product_id INTEGER,
    version_id INTEGER,
    title TEXT NOT NULL,
    description TEXT,
    bug_type TEXT DEFAULT '功能Bug',
    severity TEXT DEFAULT '一般',
    priority TEXT DEFAULT '中',
    status TEXT DEFAULT '新建',
    reporter TEXT,
    assignee TEXT,
    discovered_at TEXT,
    closed_at TEXT,
    close_reason TEXT,
    fix_solution TEXT,
    test_result TEXT,
    root_cause TEXT,
    corrective_action TEXT,
    prevention_measure TEXT,
    is_quality_issue INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now','localtime')),
    FOREIGN KEY (product_id) REFERENCES products(id),
    FOREIGN KEY (version_id) REFERENCES software_versions(id)
);
CREATE TABLE IF NOT EXISTS bug_workflow (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bug_id INTEGER NOT NULL,
    action TEXT NOT NULL,
    from_status TEXT,
    to_status TEXT,
    operator TEXT,
    comment TEXT,
    fix_solution TEXT,
    test_result TEXT,
    close_reason TEXT,
    created_at TEXT DEFAULT (datetime('now','localtime')),
    FOREIGN KEY (bug_id) REFERENCES bug_records(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS contract_payments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    contract_id INTEGER NOT NULL,
    node_name TEXT NOT NULL,
    plan_date TEXT,
    amount REAL CHECK (amount >= 0),
    status TEXT DEFAULT '待付款',
    actual_date TEXT,
    remark TEXT,
    created_at TEXT DEFAULT (datetime('now','localtime')),
    FOREIGN KEY (contract_id) REFERENCES contracts(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS contracts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    contract_no TEXT UNIQUE,
    contract_type TEXT DEFAULT '生产合同',
    supplier TEXT,
    product_id INTEGER,
    amount REAL CHECK (amount >= 0),
    paid_amount REAL DEFAULT 0,
    sign_date TEXT,
    delivery_date TEXT,
    status TEXT DEFAULT '执行中',
    owner TEXT,
    progress TEXT,
    payment_status TEXT DEFAULT '未付款',
    remark TEXT,
    created_at TEXT DEFAULT (datetime('now','localtime')),
    FOREIGN KEY (product_id) REFERENCES products(id)
);
CREATE TABLE IF NOT EXISTS defect_handling (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    inspection_id INTEGER NOT NULL,
    defect_type TEXT,
    root_cause TEXT,
    handling_method TEXT,
    responsible_person TEXT,
    status TEXT DEFAULT '待处理',
    closed_at TEXT,
    created_at TEXT DEFAULT (datetime('now','localtime')),
    FOREIGN KEY (inspection_id) REFERENCES quality_inspections(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS deliveries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    delivery_no TEXT UNIQUE,
    product_id INTEGER,
    market_team TEXT,
    quantity INTEGER CHECK (quantity > 0),
    customer TEXT,
    purpose TEXT,
    delivery_date TEXT,
    status TEXT DEFAULT '已交付',
    feedback TEXT,
    created_at TEXT DEFAULT (datetime('now','localtime')),
    FOREIGN KEY (product_id) REFERENCES products(id)
);
CREATE TABLE IF NOT EXISTS delivery_batches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            requirement_id INTEGER NOT NULL,
            batch_no INTEGER NOT NULL,
            delivery_date TEXT NOT NULL,
            quantity INTEGER NOT NULL,
            version_id INTEGER,
            version_no TEXT,
            status TEXT DEFAULT '待交付',
            production_schedule_id INTEGER,
            remark TEXT,
            created_at TEXT,
            updated_at TEXT,
            FOREIGN KEY (requirement_id) REFERENCES requirements(id),
            FOREIGN KEY (version_id) REFERENCES software_versions(id),
            FOREIGN KEY (production_schedule_id) REFERENCES production_schedules(id)
        );
CREATE TABLE IF NOT EXISTS delivery_plans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_no TEXT UNIQUE,
    customer TEXT,
    product_id INTEGER,
    quantity INTEGER CHECK (quantity > 0),
    planned_date TEXT,
    actual_date TEXT,
    status TEXT DEFAULT '计划中',
    owner TEXT,
    remark TEXT,
    created_at TEXT DEFAULT (datetime('now','localtime')), requirement_id INTEGER,
    FOREIGN KEY (product_id) REFERENCES products(id)
);
CREATE TABLE IF NOT EXISTS dev_tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    version_id INTEGER NOT NULL,
    requirement_id INTEGER,
    task_name TEXT NOT NULL,
    description TEXT,
    priority TEXT DEFAULT '中',
    status TEXT DEFAULT '待开发',
    assignee TEXT,
    planned_start TEXT,
    planned_finish TEXT,
    actual_finish TEXT,
    created_at TEXT DEFAULT (datetime('now','localtime')), task_type TEXT DEFAULT '功能开发',
    FOREIGN KEY (version_id) REFERENCES software_versions(id) ON DELETE CASCADE,
    FOREIGN KEY (requirement_id) REFERENCES requirements(id)
);
CREATE TABLE IF NOT EXISTS device_alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id INTEGER NOT NULL,
    alert_type TEXT DEFAULT '离线告警',
    level TEXT DEFAULT '一般',
    content TEXT,
    status TEXT DEFAULT '未处理',
    handler TEXT,
    solution TEXT,
    created_at TEXT DEFAULT (datetime('now','localtime')),
    resolved_at TEXT,
    FOREIGN KEY (device_id) REFERENCES devices(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS device_version_changes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id INTEGER NOT NULL,
    device_sn TEXT,
    old_version TEXT,
    new_version TEXT,
    change_type TEXT DEFAULT 'OTA升级',
    reason TEXT,
    operator TEXT,
    changed_at TEXT DEFAULT (datetime('now','localtime')),
    FOREIGN KEY (device_id) REFERENCES devices(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS devices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    device_sn TEXT UNIQUE,
    product_id INTEGER,
    customer TEXT,
    firmware_version TEXT,
    activate_date TEXT,
    last_online TEXT,
    status TEXT DEFAULT '未激活',
    ip_address TEXT,
    location TEXT,
    remark TEXT,
    created_at TEXT DEFAULT (datetime('now','localtime')),
    FOREIGN KEY (product_id) REFERENCES products(id)
);
CREATE TABLE IF NOT EXISTS gantt_projects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    start_date TEXT,
    end_date TEXT,
    category TEXT DEFAULT '产品研发情况',
    done INTEGER DEFAULT 0,
    source_table TEXT,
    source_id INTEGER,
    created_at TEXT DEFAULT (datetime('now','localtime')),
    updated_at TEXT DEFAULT (datetime('now','localtime'))
);
CREATE TABLE IF NOT EXISTS gantt_settings (
    id INTEGER PRIMARY KEY CHECK (id=1),
    start_date TEXT DEFAULT '',
    end_date TEXT DEFAULT '',
    auto_range INTEGER DEFAULT 1,
    updated_at TEXT DEFAULT (datetime('now','localtime'))
, synced INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS inventory (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    material_name TEXT NOT NULL,
    material_code TEXT,
    product_id INTEGER,
    is_product INTEGER DEFAULT 0,
    is_key_material INTEGER DEFAULT 0,
    location TEXT DEFAULT '公司库房',
    quantity INTEGER DEFAULT 0 CHECK (quantity >= 0),
    unit TEXT DEFAULT '个',
    status TEXT DEFAULT '正常',
    remark TEXT,
    updated_at TEXT DEFAULT (datetime('now','localtime')),
    FOREIGN KEY (product_id) REFERENCES products(id)
);
CREATE TABLE IF NOT EXISTS material_plans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    schedule_id INTEGER NOT NULL,
    bom_item_id INTEGER,
    material_name TEXT NOT NULL,
    material_code TEXT,
    is_key_material INTEGER DEFAULT 0,
    quantity_needed INTEGER CHECK (quantity_needed >= 0),
    quantity_available INTEGER DEFAULT 0 CHECK (quantity_available >= 0),
    unit TEXT DEFAULT '个',
    status TEXT DEFAULT '待确认',
    created_at TEXT DEFAULT (datetime('now','localtime')),
    FOREIGN KEY (schedule_id) REFERENCES production_schedules(id) ON DELETE CASCADE,
    FOREIGN KEY (bom_item_id) REFERENCES bom_items(id)
);
CREATE TABLE IF NOT EXISTS material_requisitions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    requisition_no TEXT UNIQUE,
    product_id INTEGER,
    requirement_id INTEGER,
    applicant TEXT,
    status TEXT DEFAULT '待审批',
    total_quantity INTEGER DEFAULT 0 CHECK (total_quantity >= 0),
    approved_at TEXT,
    created_at TEXT DEFAULT (datetime('now','localtime')),
    FOREIGN KEY (product_id) REFERENCES products(id),
    FOREIGN KEY (requirement_id) REFERENCES requirements(id)
);
CREATE TABLE IF NOT EXISTS milestone_checkpoints (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    requirement_id INTEGER NOT NULL,
    version_id INTEGER,
    direction TEXT NOT NULL DEFAULT '研发',
    name TEXT NOT NULL,
    standard TEXT,
    planned_date TEXT,
    actual_date TEXT,
    status TEXT NOT NULL DEFAULT '未开始',
    blocker TEXT,
    owner TEXT,
    sort_order INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now','localtime')),
    updated_at TEXT DEFAULT (datetime('now','localtime'))
);
CREATE TABLE IF NOT EXISTS operation_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    module TEXT,
    action TEXT,
    detail TEXT,
    operator TEXT DEFAULT '系统',
    created_at TEXT DEFAULT (datetime('now','localtime'))
);
CREATE TABLE IF NOT EXISTS ota_upgrades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ota_version_id INTEGER NOT NULL,
    device_id INTEGER NOT NULL,
    status TEXT DEFAULT '待升级',
    start_time TEXT,
    end_time TEXT,
    result TEXT,
    error_msg TEXT,
    created_at TEXT DEFAULT (datetime('now','localtime')),
    FOREIGN KEY (ota_version_id) REFERENCES ota_versions(id) ON DELETE CASCADE,
    FOREIGN KEY (device_id) REFERENCES devices(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS ota_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    version_no TEXT NOT NULL,
    product_id INTEGER,
    file_name TEXT,
    file_size INTEGER,
    md5 TEXT,
    release_notes TEXT,
    status TEXT DEFAULT '测试中',
    rollout_strategy TEXT DEFAULT '全量发布',
    rollout_percentage INTEGER DEFAULT 100,
    creator TEXT,
    created_at TEXT DEFAULT (datetime('now','localtime')), published_at TEXT, published_by TEXT,
    FOREIGN KEY (product_id) REFERENCES products(id)
);
CREATE TABLE IF NOT EXISTS production_orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_no TEXT UNIQUE,
    product_id INTEGER,
    requirement_id INTEGER,
    schedule_id INTEGER,
    supplier TEXT,
    quantity INTEGER CHECK (quantity > 0),
    plan_start TEXT,
    plan_finish TEXT,
    actual_finish TEXT,
    status TEXT DEFAULT '待排产',
    wip_progress INTEGER DEFAULT 0 CHECK (wip_progress >= 0 AND wip_progress <= 100),
    remark TEXT,
    created_at TEXT DEFAULT (datetime('now','localtime')),
    FOREIGN KEY (product_id) REFERENCES products(id),
    FOREIGN KEY (requirement_id) REFERENCES requirements(id),
    FOREIGN KEY (schedule_id) REFERENCES production_schedules(id)
);
CREATE TABLE IF NOT EXISTS production_processes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    schedule_id INTEGER NOT NULL,
    process_name TEXT NOT NULL,
    sequence INTEGER DEFAULT 1,
    planned_start TEXT,
    planned_finish TEXT,
    actual_finish TEXT,
    status TEXT DEFAULT '待开始',
    responsible TEXT,
    created_at TEXT DEFAULT (datetime('now','localtime')),
    FOREIGN KEY (schedule_id) REFERENCES production_schedules(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS production_schedules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    schedule_no TEXT UNIQUE,
    product_id INTEGER NOT NULL,
    requirement_id INTEGER,
    quantity INTEGER CHECK (quantity > 0),
    plan_start TEXT,
    plan_finish TEXT,
    status TEXT DEFAULT '规划中',
    remark TEXT,
    created_at TEXT DEFAULT (datetime('now','localtime')), version_id INTEGER, flash_version_no TEXT,
    FOREIGN KEY (product_id) REFERENCES products(id),
    FOREIGN KEY (requirement_id) REFERENCES requirements(id)
);
CREATE TABLE IF NOT EXISTS products (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    model TEXT,
    version TEXT,
    description TEXT,
    lifecycle_stage TEXT DEFAULT '规划中',
    stocking_strategy TEXT DEFAULT '不囤货，按需生产',
    created_at TEXT DEFAULT (datetime('now','localtime'))
);
CREATE TABLE IF NOT EXISTS quality_inspections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    inspection_no TEXT UNIQUE,
    inspection_type TEXT DEFAULT 'OQC',
    production_order_id INTEGER,
    product_id INTEGER,
    inspector TEXT,
    sample_quantity INTEGER CHECK (sample_quantity > 0),
    pass_quantity INTEGER DEFAULT 0 CHECK (pass_quantity >= 0),
    fail_quantity INTEGER DEFAULT 0 CHECK (fail_quantity >= 0),
    pass_rate REAL CHECK (pass_rate >= 0 AND pass_rate <= 100),
    result TEXT DEFAULT '待检验',
    defect_description TEXT,
    inspection_date TEXT DEFAULT (date('now','localtime')),
    created_at TEXT DEFAULT (datetime('now','localtime')),
    FOREIGN KEY (production_order_id) REFERENCES production_orders(id),
    FOREIGN KEY (product_id) REFERENCES products(id),
    CHECK (pass_quantity + fail_quantity = sample_quantity)
);
CREATE TABLE IF NOT EXISTS requirement_reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    requirement_id INTEGER NOT NULL,
    reviewer TEXT,
    review_date TEXT,
    conclusion TEXT DEFAULT '待评审',
    opinion TEXT,
    priority_suggested TEXT,
    version_suggested TEXT,
    created_at TEXT DEFAULT (datetime('now','localtime')),
    FOREIGN KEY (requirement_id) REFERENCES requirements(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS "requirements" (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    req_type TEXT DEFAULT '用户需求',
    source_dept TEXT,
    customer TEXT,
    description TEXT,
    quantity INTEGER DEFAULT 0 CHECK (quantity >= 0),
    priority TEXT DEFAULT '中',
    status TEXT DEFAULT '待评审',
    delivery_date TEXT,
    product_model TEXT,
    batch_delivery TEXT,
    application_scenario TEXT,
    system_verify_point TEXT,
    verify_function TEXT,
    performance_req TEXT,
    support_product TEXT,
    certification_type TEXT,
    spec_requirement TEXT,
    industry_standard TEXT,
    baseline_product_type TEXT,
    task_progress TEXT,
    time_plan TEXT,
    deliverable TEXT,
    created_at TEXT,
    updated_at TEXT
, batch_detail TEXT, is_deleted INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS requisition_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    requisition_id INTEGER NOT NULL,
    material_plan_id INTEGER,
    material_name TEXT NOT NULL,
    quantity INTEGER NOT NULL CHECK (quantity > 0),
    unit TEXT DEFAULT '个',
    FOREIGN KEY (requisition_id) REFERENCES material_requisitions(id) ON DELETE CASCADE,
    FOREIGN KEY (material_plan_id) REFERENCES material_plans(id)
);
CREATE TABLE IF NOT EXISTS resource_schedules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    resource_type TEXT DEFAULT '人力资源',
    resource_name TEXT NOT NULL,
    project TEXT,
    task TEXT,
    start_date TEXT,
    end_date TEXT,
    allocation INTEGER DEFAULT 100,
    status TEXT DEFAULT '已分配',
    owner TEXT,
    remark TEXT,
    created_at TEXT DEFAULT (datetime('now','localtime'))
);
CREATE TABLE IF NOT EXISTS revenue_confirmations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    confirm_no TEXT UNIQUE,
    customer TEXT,
    contract_no TEXT,
    product_id INTEGER,
    amount REAL CHECK (amount >= 0),
    confirmed_amount REAL DEFAULT 0,
    confirm_date TEXT,
    status TEXT DEFAULT '待确认',
    period TEXT,
    voucher TEXT,
    handler TEXT,
    remark TEXT,
    created_at TEXT DEFAULT (datetime('now','localtime')),
    FOREIGN KEY (product_id) REFERENCES products(id)
);
CREATE TABLE IF NOT EXISTS software_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id INTEGER NOT NULL,
    version_no TEXT NOT NULL,
    version_name TEXT,
    description TEXT,
    capabilities TEXT,
    planned_release_date TEXT,
    actual_release_date TEXT,
    status TEXT DEFAULT '规划中',
    created_at TEXT DEFAULT (datetime('now','localtime')), system_version TEXT, is_forward_compatible INTEGER DEFAULT 1, ota_supported INTEGER DEFAULT 0,
    FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE CASCADE
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
CREATE TABLE IF NOT EXISTS tech_specs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id INTEGER NOT NULL,
    version_id INTEGER,
    spec_name TEXT NOT NULL,
    spec_type TEXT DEFAULT '功能规格',
    content TEXT,
    parameters TEXT,
    interfaces TEXT,
    performance TEXT,
    status TEXT DEFAULT '草稿',
    author TEXT,
    created_at TEXT DEFAULT (datetime('now','localtime')),
    updated_at TEXT DEFAULT (datetime('now','localtime')),
    FOREIGN KEY (product_id) REFERENCES products(id),
    FOREIGN KEY (version_id) REFERENCES software_versions(id)
);
CREATE TABLE IF NOT EXISTS tech_support_tickets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_no TEXT UNIQUE,
    customer TEXT,
    contact_person TEXT,
    contact_phone TEXT,
    product_id INTEGER,
    title TEXT NOT NULL,
    description TEXT,
    category TEXT DEFAULT '技术咨询',
    priority TEXT DEFAULT '中',
    status TEXT DEFAULT '待处理',
    handler TEXT,
    solution TEXT,
    resolved_at TEXT,
    satisfaction INTEGER,
    created_at TEXT DEFAULT (datetime('now','localtime')),
    updated_at TEXT DEFAULT (datetime('now','localtime')),
    FOREIGN KEY (product_id) REFERENCES products(id)
);
CREATE TABLE IF NOT EXISTS test_batches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_no TEXT UNIQUE,
    product_id INTEGER,
    product_name TEXT,
    schedule_id INTEGER,
    total_count INTEGER DEFAULT 0,
    pass_count INTEGER DEFAULT 0,
    fail_count INTEGER DEFAULT 0,
    test_date TEXT,
    spec TEXT,
    status TEXT DEFAULT '已完成',
    remark TEXT,
    created_at TEXT DEFAULT (datetime('now','localtime'))
);
CREATE TABLE IF NOT EXISTS test_case_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    execution_id INTEGER NOT NULL,
    case_id INTEGER NOT NULL,
    result TEXT DEFAULT '未执行',
    actual_result TEXT,
    bug_id INTEGER,
    executed_at TEXT,
    executor TEXT,
    FOREIGN KEY (execution_id) REFERENCES test_executions(id) ON DELETE CASCADE,
    FOREIGN KEY (case_id) REFERENCES test_cases(id),
    FOREIGN KEY (bug_id) REFERENCES bug_records(id)
);
CREATE TABLE IF NOT EXISTS test_cases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    case_no TEXT UNIQUE,
    product_id INTEGER,
    version_id INTEGER,
    title TEXT NOT NULL,
    module TEXT,
    precondition TEXT,
    test_steps TEXT,
    expected_result TEXT,
    priority TEXT DEFAULT '中',
    case_type TEXT DEFAULT '功能测试',
    status TEXT DEFAULT '启用',
    creator TEXT,
    created_at TEXT DEFAULT (datetime('now','localtime')),
    FOREIGN KEY (product_id) REFERENCES products(id),
    FOREIGN KEY (version_id) REFERENCES software_versions(id)
);
CREATE TABLE IF NOT EXISTS test_executions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    execution_no TEXT UNIQUE,
    product_id INTEGER,
    version_id INTEGER,
    round_no INTEGER DEFAULT 1,
    executor TEXT,
    start_date TEXT,
    end_date TEXT,
    total_cases INTEGER DEFAULT 0,
    passed_cases INTEGER DEFAULT 0,
    failed_cases INTEGER DEFAULT 0,
    blocked_cases INTEGER DEFAULT 0,
    pass_rate REAL DEFAULT 0,
    status TEXT DEFAULT '进行中',
    summary TEXT,
    created_at TEXT DEFAULT (datetime('now','localtime')),
    FOREIGN KEY (product_id) REFERENCES products(id),
    FOREIGN KEY (version_id) REFERENCES software_versions(id)
);
CREATE TABLE IF NOT EXISTS test_plans (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            plan_no TEXT NOT NULL,
            version_id INTEGER NOT NULL,
            product_id INTEGER,
            title TEXT NOT NULL,
            description TEXT,
            test_type TEXT DEFAULT '黑盒测试',
            planned_start TEXT,
            planned_finish TEXT,
            actual_start TEXT,
            actual_finish TEXT,
            status TEXT DEFAULT '待开始',
            tester TEXT,
            total_cases INTEGER DEFAULT 0,
            passed_cases INTEGER DEFAULT 0,
            failed_cases INTEGER DEFAULT 0,
            blocked_cases INTEGER DEFAULT 0,
            bug_count INTEGER DEFAULT 0,
            created_at TEXT,
            updated_at TEXT,
            FOREIGN KEY (version_id) REFERENCES software_versions(id),
            FOREIGN KEY (product_id) REFERENCES products(id)
        );
CREATE TABLE IF NOT EXISTS test_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id INTEGER NOT NULL,
    sn TEXT NOT NULL,
    is_pass INTEGER DEFAULT 1,
    afc INTEGER,
    call_power REAL,
    standby_power REAL,
    evm_9 REAL, evm_29 REAL, evm_47 REAL,
    freq_9 REAL, freq_29 REAL, freq_47 REAL,
    pwr_9 REAL, pwr_29 REAL, pwr_47 REAL,
    aclr1_worst REAL, aclr2_worst REAL, aclr3_worst REAL, aclr4_worst REAL,
    station_total_sec REAL,
    status TEXT DEFAULT 'PASS',
    remark TEXT,
    created_at TEXT DEFAULT (datetime('now','localtime'))
, station_data TEXT);
CREATE TABLE IF NOT EXISTS ticket_workflow (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_id INTEGER NOT NULL,
    action TEXT NOT NULL,
    operator TEXT,
    comment TEXT,
    created_at TEXT DEFAULT (datetime('now','localtime')),
    FOREIGN KEY (ticket_id) REFERENCES tech_support_tickets(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS timeline_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_date TEXT NOT NULL,
            event_type TEXT NOT NULL,
            title TEXT NOT NULL,
            description TEXT,
            team TEXT,
            status TEXT DEFAULT '计划中',
            related_id INTEGER,
            related_type TEXT,
            product_id INTEGER,
            version_id INTEGER,
            requirement_id INTEGER,
            created_at TEXT,
            FOREIGN KEY (product_id) REFERENCES products(id),
            FOREIGN KEY (version_id) REFERENCES software_versions(id),
            FOREIGN KEY (requirement_id) REFERENCES requirements(id)
        );
CREATE TABLE IF NOT EXISTS user_feedbacks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    customer TEXT,
    product_id INTEGER,
    feedback_type TEXT DEFAULT '功能建议',
    title TEXT NOT NULL,
    content TEXT,
    source TEXT DEFAULT '客户反馈',
    priority TEXT DEFAULT '中',
    status TEXT DEFAULT '待评估',
    converted_requirement_id INTEGER,
    handler TEXT,
    evaluation TEXT,
    created_at TEXT DEFAULT (datetime('now','localtime')),
    FOREIGN KEY (product_id) REFERENCES products(id),
    FOREIGN KEY (converted_requirement_id) REFERENCES requirements(id)
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
CREATE TABLE IF NOT EXISTS version_features (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            version_id INTEGER NOT NULL,
            feature_name TEXT NOT NULL,
            feature_desc TEXT,
            status TEXT DEFAULT '未开始',
            priority TEXT DEFAULT '中',
            related_requirement_id INTEGER,
            dev_task_id INTEGER,
            created_at TEXT,
            updated_at TEXT,
            FOREIGN KEY (version_id) REFERENCES software_versions(id),
            FOREIGN KEY (related_requirement_id) REFERENCES requirements(id)
        );
CREATE TABLE IF NOT EXISTS version_requirements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    version_id INTEGER NOT NULL,
    requirement_id INTEGER NOT NULL,
    priority TEXT DEFAULT '中',
    created_at TEXT DEFAULT (datetime('now','localtime')),
    FOREIGN KEY (version_id) REFERENCES software_versions(id) ON DELETE CASCADE,
    FOREIGN KEY (requirement_id) REFERENCES requirements(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS warehouse_receipts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    receipt_no TEXT UNIQUE,
    production_order_id INTEGER,
    product_id INTEGER,
    quantity INTEGER CHECK (quantity > 0),
    quality_status TEXT DEFAULT '合格',
    approver TEXT,
    status TEXT DEFAULT '待审批',
    approved_at TEXT,
    created_at TEXT DEFAULT (datetime('now','localtime')),
    FOREIGN KEY (production_order_id) REFERENCES production_orders(id),
    FOREIGN KEY (product_id) REFERENCES products(id)
);

CREATE INDEX IF NOT EXISTS idx_bom_product ON bom_items(product_id);
CREATE INDEX IF NOT EXISTS idx_bug_product ON bug_records(product_id);
CREATE INDEX IF NOT EXISTS idx_bug_status ON bug_records(status);
CREATE INDEX IF NOT EXISTS idx_dev_ver_change ON device_version_changes(device_id);
CREATE INDEX IF NOT EXISTS idx_device_status ON devices(status);
CREATE INDEX IF NOT EXISTS idx_dp_customer ON delivery_plans(customer);
CREATE INDEX IF NOT EXISTS idx_dt_version ON dev_tasks(version_id);
CREATE INDEX IF NOT EXISTS idx_feedback_type ON user_feedbacks(feedback_type);
CREATE INDEX IF NOT EXISTS idx_inv_material ON inventory(material_name);
CREATE INDEX IF NOT EXISTS idx_logs_time ON operation_logs(created_at);
CREATE INDEX IF NOT EXISTS idx_mp_schedule ON material_plans(schedule_id);
CREATE INDEX IF NOT EXISTS idx_ota_product ON ota_versions(product_id);
CREATE INDEX IF NOT EXISTS idx_po_status ON production_orders(status);
CREATE INDEX IF NOT EXISTS idx_pp_schedule ON production_processes(schedule_id);
CREATE INDEX IF NOT EXISTS idx_ps_product ON production_schedules(product_id);
CREATE INDEX IF NOT EXISTS idx_qi_date ON quality_inspections(inspection_date);
CREATE INDEX IF NOT EXISTS idx_rc_status ON revenue_confirmations(status);
CREATE INDEX IF NOT EXISTS idx_req_status ON requirements(status);
CREATE INDEX IF NOT EXISTS idx_sv_product ON software_versions(product_id);
CREATE INDEX IF NOT EXISTS idx_sv_status ON software_versions(status);
CREATE INDEX IF NOT EXISTS idx_tasks_assignee ON tasks(assignee);
CREATE INDEX IF NOT EXISTS idx_tasks_due ON tasks(due_date);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_tc_product ON test_cases(product_id);
CREATE INDEX IF NOT EXISTS idx_te_version ON test_executions(version_id);
CREATE INDEX IF NOT EXISTS idx_test_records_batch ON test_records(batch_id);
CREATE INDEX IF NOT EXISTS idx_test_records_sn ON test_records(sn);
CREATE INDEX IF NOT EXISTS idx_ticket_status ON tech_support_tickets(status);
