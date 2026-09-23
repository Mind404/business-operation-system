# -*- coding: utf-8 -*-
"""
数据库初始化脚本
运行方式: python init_db.py
"""
import os
import sqlite3
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'business.db')

def init_database():
    if os.path.exists(DB_PATH):
        print(f'数据库已存在: {DB_PATH}')
        print('如需重新初始化，请先删除 business.db 文件')
        return

    db = sqlite3.connect(DB_PATH)
    db.executescript(open(os.path.join(os.path.dirname(__file__), 'schema.sql'), encoding='utf-8').read())
    db.commit()

    # 插入示例数据
    print('正在插入示例数据...')
    insert_sample_data(db)
    db.commit()
    db.close()
    print(f'数据库初始化完成: {DB_PATH}')

def insert_sample_data(db):
    # 示例需求
    db.execute('''INSERT INTO requirements (title, source_dept, customer, description, quantity, priority, status, delivery_date)
                  VALUES (?,?,?,?,?,?,?,?)''',
               ('XX通信公司5G模组批量采购', '华东市场团队', 'XX通信有限公司', '客户需要500套5G通信模组，用于智慧城市项目', 500, '高', '已评审', '2026-10-15'))
    db.execute('''INSERT INTO requirements (title, source_dept, customer, description, quantity, priority, status, delivery_date)
                  VALUES (?,?,?,?,?,?,?,?)''',
               ('YY科技4G模组样机需求', '华南市场团队', 'YY科技有限公司', '客户需要20套4G模组样机用于测试验证', 20, '中', '进行中', '2026-09-30'))

    # 示例产品
    db.execute('''INSERT INTO products (name, model, version, description, lifecycle_stage, stocking_strategy)
                  VALUES (?,?,?,?,?,?)''',
               ('5G通信模组', 'CM-5G-001', 'V1.0', '支持5G NSA/SA双模，适用于物联网场景', '量产中', '不囤货，按需生产；关键物料自供，通用物料供应商自采'))
    db.execute('''INSERT INTO products (name, model, version, description, lifecycle_stage, stocking_strategy)
                  VALUES (?,?,?,?,?,?)''',
               ('4G通信模组', 'CM-4G-002', 'V2.1', '支持LTE Cat.4，适用于工业控制场景', '量产中', '不囤货，按需生产'))

    # 示例BOM
    db.execute('''INSERT INTO bom_items (product_id, material_name, material_code, is_key_material, unit, quantity_per_unit, supplier)
                  VALUES (?,?,?,?,?,?,?)''', (1, '5G主芯片', 'IC-5G-001', 1, '个', 1, '高通代理商'))
    db.execute('''INSERT INTO bom_items (product_id, material_name, material_code, is_key_material, unit, quantity_per_unit, supplier)
                  VALUES (?,?,?,?,?,?,?)''', (1, '射频前端模块', 'IC-RF-002', 1, '个', 1, 'Skyworks代理商'))
    db.execute('''INSERT INTO bom_items (product_id, material_name, material_code, is_key_material, unit, quantity_per_unit, supplier)
                  VALUES (?,?,?,?,?,?,?)''', (1, 'PCB板', 'PCB-001', 0, '片', 1, '深圳PCB厂'))
    db.execute('''INSERT INTO bom_items (product_id, material_name, material_code, is_key_material, unit, quantity_per_unit, supplier)
                  VALUES (?,?,?,?,?,?,?)''', (1, '连接器', 'CON-001', 0, '个', 2, '东莞连接器厂'))

    # 示例库存
    db.execute('''INSERT INTO inventory (material_name, material_code, is_key_material, location, quantity, unit, status)
                  VALUES (?,?,?,?,?,?,?)''', ('5G主芯片', 'IC-5G-001', 1, '公司库房', 300, '个', '正常'))
    db.execute('''INSERT INTO inventory (material_name, material_code, is_key_material, location, quantity, unit, status)
                  VALUES (?,?,?,?,?,?,?)''', ('射频前端模块', 'IC-RF-002', 1, '公司库房', 280, '个', '正常'))
    db.execute('''INSERT INTO inventory (material_name, material_code, is_key_material, location, quantity, unit, status)
                  VALUES (?,?,?,?,?,?,?)''', ('PCB板', 'PCB-001', 0, '供应商暂存', 1000, '片', '正常'))
    db.execute('''INSERT INTO inventory (material_name, material_code, is_key_material, location, quantity, unit, status)
                  VALUES (?,?,?,?,?,?,?)''', ('连接器', 'CON-001', 0, '供应商暂存', 2000, '个', '正常'))

    # 示例生产订单
    db.execute('''INSERT INTO production_orders (order_no, product_id, requirement_id, supplier, quantity, plan_start, plan_finish, status, wip_progress)
                  VALUES (?,?,?,?,?,?,?,?,?)''',
               ('PO20260901001', 1, 1, '深圳XX电子厂', 200, '2026-09-05', '2026-09-20', '生产中', 60))

    # 示例检验记录
    db.execute('''INSERT INTO quality_inspections (inspection_no, inspection_type, production_order_id, product_id, inspector, sample_quantity, pass_quantity, fail_quantity, pass_rate, result, inspection_date)
                  VALUES (?,?,?,?,?,?,?,?,?,?,?)''',
               ('QI20260901001', 'OQC', 1, 1, '张工', 50, 49, 1, 98.0, '合格', '2026-09-06'))

    print('  - 示例需求 2 条')
    print('  - 示例产品 2 个')
    print('  - 示例BOM 4 条')
    print('  - 示例库存 4 条')
    print('  - 示例生产订单 1 条')
    print('  - 示例检验记录 1 条')

if __name__ == '__main__':
    init_database()
