#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""数据库迁移：适配真实业务场景"""

import sqlite3

DB_PATH = 'business.db'

def migrate():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    print('开始数据库迁移...')

    # 1. software_versions表添加字段
    print('\n1. software_versions表添加字段...')
    try:
        cursor.execute("ALTER TABLE software_versions ADD COLUMN system_version TEXT")
        print('  - 添加 system_version 字段')
    except Exception as e:
        print(f'  - system_version 已存在: {e}')

    try:
        cursor.execute("ALTER TABLE software_versions ADD COLUMN is_forward_compatible INTEGER DEFAULT 1")
        print('  - 添加 is_forward_compatible 字段')
    except Exception as e:
        print(f'  - is_forward_compatible 已存在: {e}')

    try:
        cursor.execute("ALTER TABLE software_versions ADD COLUMN ota_supported INTEGER DEFAULT 0")
        print('  - 添加 ota_supported 字段')
    except Exception as e:
        print(f'  - ota_supported 已存在: {e}')

    # 2. 创建version_features表（版本功能清单）
    print('\n2. 创建version_features表...')
    cursor.execute('''
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
        )
    ''')
    print('  - version_features表创建完成')

    # 3. production_schedules表添加字段
    print('\n3. production_schedules表添加字段...')
    try:
        cursor.execute("ALTER TABLE production_schedules ADD COLUMN version_id INTEGER")
        print('  - 添加 version_id 字段')
    except Exception as e:
        print(f'  - version_id 已存在: {e}')

    try:
        cursor.execute("ALTER TABLE production_schedules ADD COLUMN flash_version_no TEXT")
        print('  - 添加 flash_version_no 字段')
    except Exception as e:
        print(f'  - flash_version_no 已存在: {e}')

    # 4. 创建delivery_batches表（分批交付明细）
    print('\n4. 创建delivery_batches表...')
    cursor.execute('''
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
        )
    ''')
    print('  - delivery_batches表创建完成')

    # 5. dev_tasks表添加字段
    print('\n5. dev_tasks表添加字段...')
    try:
        cursor.execute("ALTER TABLE dev_tasks ADD COLUMN task_type TEXT DEFAULT '功能开发'")
        print('  - 添加 task_type 字段')
    except Exception as e:
        print(f'  - task_type 已存在: {e}')

    # 6. 创建test_plans表（测试计划）
    print('\n6. 创建test_plans表...')
    cursor.execute('''
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
        )
    ''')
    print('  - test_plans表创建完成')

    # 7. 创建timeline_events表（跨团队时间线事件）
    print('\n7. 创建timeline_events表...')
    cursor.execute('''
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
        )
    ''')
    print('  - timeline_events表创建完成')

    conn.commit()
    print('\n=== 数据库迁移完成 ===')
    conn.close()

if __name__ == '__main__':
    migrate()
