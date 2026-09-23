#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
生成2款新产品的完整虚拟数据，覆盖全业务流程
产品1: 5G工业网关模组 (CM-5G-GW01)
产品2: 低功耗蓝牙信标模组 (CM-BLE-B01)
"""
import sqlite3
import os
from datetime import datetime, timedelta
import random

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'business.db')

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def generate_product_data():
    """生成2款新产品的完整数据"""
    conn = get_db()
    cursor = conn.cursor()
    
    now = datetime.now()
    print("=" * 60)
    print("开始生成2款新产品的虚拟数据...")
    print("=" * 60)
    
    # ============================================================
    # 产品1: 5G工业网关模组
    # ============================================================
    print("\n【产品1】5G工业网关模组 (CM-5G-GW01)")
    
    # 1. 创建产品
    cursor.execute('''INSERT INTO products (name, model, version, description, lifecycle_stage, stocking_strategy, created_at)
                      VALUES (?,?,?,?,?,?,?)''',
                   ('5G工业网关模组', 'CM-5G-GW01', 'V1.0.0',
                    '工业级5G通信网关，支持SA/NSA双模，适用于工业物联网、智能制造、远程监控等场景。支持多协议转换，边缘计算能力，宽温宽压设计。',
                    '研发中', '不囤货，按需生产', now.strftime('%Y-%m-%d %H:%M:%S')))
    product1_id = cursor.lastrowid
    print(f"  ✓ 创建产品 ID={product1_id}")
    
    # 2. 创建BOM表
    bom_items_p1 = [
        ('5G通信模组', 'BOM-5G-001', 1, 1, '移远通信'),
        ('主控芯片', 'BOM-MCU-001', 1, 1, '意法半导体'),
        ('DDR4内存', 'BOM-DDR-001', 1, 2, '三星'),
        ('eMMC存储', 'BOM-EMMC-001', 1, 1, '江波龙'),
        ('以太网PHY芯片', 'BOM-ETH-001', 1, 2, '微芯科技'),
        ('电源管理芯片', 'BOM-PMU-001', 1, 1, '德州仪器'),
        ('工业级外壳', 'BOM-CASE-001', 0, 1, '本地供应商'),
        ('PCB主板', 'BOM-PCB-001', 0, 1, '深南电路'),
    ]
    for name, code, is_key, qty, supplier in bom_items_p1:
        cursor.execute('''INSERT INTO bom_items (product_id, material_name, material_code, is_key_material, quantity_per_unit, supplier)
                          VALUES (?,?,?,?,?,?)''',
                       (product1_id, name, code, is_key, qty, supplier))
    print(f"  ✓ 创建BOM表 {len(bom_items_p1)}项")
    
    # 3. 创建4类需求
    reqs_p1 = [
        # 用户需求
        {
            'title': '华东智能制造客户-5G网关批量需求',
            'req_type': '用户需求',
            'source_dept': '华东市场团队',
            'customer': 'XX智能制造有限公司',
            'description': '客户智能工厂改造项目，需要5G工业网关实现设备联网和数据采集，要求支持Modbus、OPC UA等工业协议。',
            'quantity': 500,
            'priority': '高',
            'status': '已评审',
            'delivery_date': '2026-12-15',
            'product_model': 'CM-5G-GW01',
            'batch_delivery': '是',
            'application_scenario': '工业智能制造-设备联网与数据采集',
        },
        # 集团系统侧需求
        {
            'title': '集团5G工业互联网平台联调验证需求',
            'req_type': '集团系统侧需求',
            'source_dept': '集团系统部',
            'customer': '集团5G工业互联网平台项目组',
            'description': '集团5G工业互联网平台二期建设，需要5G网关模组参与平台联调验证，验证设备接入、数据上报、远程配置等功能。',
            'quantity': 50,
            'priority': '高',
            'status': '进行中',
            'delivery_date': '2026-11-30',
            'product_model': 'CM-5G-GW01',
            'system_verify_point': '2026年Q4平台联调验证',
            'verify_function': '设备接入认证、数据上报、远程配置、OTA升级、边缘计算',
            'performance_req': '数据上报时延<100ms，支持1000+数据点并发采集，设备在线率>99.5%',
            'support_product': '5G工业网关模组CM-5G-GW01',
        },
        # 集团应用侧需求
        {
            'title': '工业级通信设备入网认证与规范要求',
            'req_type': '集团应用侧需求',
            'source_dept': '集团质量部',
            'customer': '集团入网认证中心',
            'description': '根据集团工业通信设备入网规范，5G网关模组需要通过CCC认证、SRRC型号核准、入网许可证，满足工业级宽温宽压要求。',
            'quantity': 0,
            'priority': '中',
            'status': '已评审',
            'delivery_date': '2026-10-31',
            'product_model': 'CM-5G-GW01',
            'certification_type': 'CCC认证、SRRC型号核准、入网许可证',
            'spec_requirement': '工业级宽温(-40℃~85℃)、宽压(9~36VDC)、IP30防护、防震抗干扰',
            'industry_standard': 'YD/T 1595(5G)、GB/T 17626(EMC)、GB 4943.1(安全)',
            'baseline_product_type': '工业级通信网关',
        },
        # 领导任务课题
        {
            'title': '5G+工业互联网应用示范课题',
            'req_type': '领导任务课题',
            'source_dept': '总经办',
            'customer': '公司战略发展委员会',
            'description': '公司战略级课题，探索5G技术在工业互联网领域的应用，形成可复制的解决方案，申报省部级科技进步奖。',
            'quantity': 0,
            'priority': '高',
            'status': '进行中',
            'delivery_date': '2027-03-31',
            'product_model': 'CM-5G-GW01',
            'task_progress': '需求调研完成，方案设计中，原型开发启动',
            'time_plan': '2026Q3需求调研、2026Q4方案设计、2027Q1原型开发、2027Q2现场验证、2027Q3课题验收',
            'deliverable': '5G工业互联网应用解决方案报告、原型系统、测试报告、专利2-3项、论文1-2篇',
        },
    ]
    
    req_ids_p1 = []
    for req in reqs_p1:
        cursor.execute('''INSERT INTO requirements (title, req_type, source_dept, customer, description, quantity, priority, status, delivery_date,
                          product_model, batch_delivery, application_scenario, system_verify_point, verify_function, performance_req, support_product,
                          certification_type, spec_requirement, industry_standard, baseline_product_type, task_progress, time_plan, deliverable, created_at, updated_at)
                          VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                       (req['title'], req['req_type'], req['source_dept'], req['customer'], req['description'],
                        req['quantity'], req['priority'], req['status'], req['delivery_date'],
                        req.get('product_model',''), req.get('batch_delivery',''), req.get('application_scenario',''),
                        req.get('system_verify_point',''), req.get('verify_function',''), req.get('performance_req',''),
                        req.get('support_product',''), req.get('certification_type',''), req.get('spec_requirement',''),
                        req.get('industry_standard',''), req.get('baseline_product_type',''), req.get('task_progress',''),
                        req.get('time_plan',''), req.get('deliverable',''), now.strftime('%Y-%m-%d %H:%M:%S'),
                        now.strftime('%Y-%m-%d %H:%M:%S')))
        req_ids_p1.append(cursor.lastrowid)
    print(f"  ✓ 创建4类需求 {len(reqs_p1)}条")
    
    # 4. 创建软件版本
    versions_p1 = [
        {'version_no': 'V0.9.0', 'version_name': 'Alpha测试版', 'description': '基础功能验证版本，支持5G联网和基础数据采集',
         'capabilities': '5G联网、基础数据采集、Web配置界面', 'planned_release_date': '2026-10-15', 'status': '测试中'},
        {'version_no': 'V1.0.0', 'version_name': '正式发布版', 'description': '首个正式版本，支持完整工业协议和边缘计算',
         'capabilities': '5G联网、Modbus/OPC UA协议、边缘计算、远程配置、OTA升级', 'planned_release_date': '2026-12-01', 'status': '开发中'},
        {'version_no': 'V1.1.0', 'version_name': '功能增强版', 'description': '增加AI算法支持和更多工业协议',
         'capabilities': 'AI边缘推理、Profinet协议、MQTT协议、增强安全特性', 'planned_release_date': '2027-02-28', 'status': '规划中'},
    ]
    
    version_ids_p1 = []
    for ver in versions_p1:
        cursor.execute('''INSERT INTO software_versions (product_id, version_no, version_name, description, capabilities, planned_release_date, status, created_at)
                          VALUES (?,?,?,?,?,?,?,?)''',
                       (product1_id, ver['version_no'], ver['version_name'], ver['description'],
                        ver['capabilities'], ver['planned_release_date'], ver['status'], now.strftime('%Y-%m-%d %H:%M:%S')))
        version_ids_p1.append(cursor.lastrowid)
    print(f"  ✓ 创建软件版本 {len(versions_p1)}个")
    
    # 5. 关联需求到版本
    for i, vid in enumerate(version_ids_p1):
        for rid in req_ids_p1[:2]:  # 每个版本关联前2个需求
            cursor.execute('INSERT OR IGNORE INTO version_requirements (version_id, requirement_id) VALUES (?,?)', (vid, rid))
    print(f"  ✓ 关联需求到版本")
    
    # 6. 创建开发任务
    tasks_p1 = [
        {'version_idx': 0, 'req_idx': 0, 'name': '5G通信模组驱动开发', 'desc': '基于移远5G模组开发Linux驱动，实现拨号、联网、状态监控',
         'priority': '高', 'status': '已完成', 'assignee': '张工', 'start': '2026-08-01', 'finish': '2026-09-15'},
        {'version_idx': 0, 'req_idx': 0, 'name': '基础数据采集功能', 'desc': '实现Modbus RTU/TCP数据采集，支持寄存器配置和数据上报',
         'priority': '高', 'status': '已完成', 'assignee': '李工', 'start': '2026-08-15', 'finish': '2026-09-30'},
        {'version_idx': 0, 'req_idx': 1, 'name': 'Web配置界面开发', 'desc': '开发Web管理界面，支持网络配置、数据点配置、系统状态查看',
         'priority': '中', 'status': '进行中', 'assignee': '王工', 'start': '2026-09-01', 'finish': '2026-10-10'},
        {'version_idx': 1, 'req_idx': 0, 'name': 'OPC UA协议支持', 'desc': '实现OPC UA Server和Client，支持数据订阅和方法调用',
         'priority': '高', 'status': '进行中', 'assignee': '赵工', 'start': '2026-09-15', 'finish': '2026-11-15'},
        {'version_idx': 1, 'req_idx': 1, 'name': '边缘计算框架', 'desc': '集成边缘计算框架，支持Python脚本运行和数据预处理',
         'priority': '高', 'status': '待开始', 'assignee': '张工', 'start': '2026-10-01', 'finish': '2026-11-30'},
        {'version_idx': 1, 'req_idx': 0, 'name': 'OTA升级功能', 'desc': '实现固件OTA升级，支持差分升级、断点续传、升级回滚',
         'priority': '中', 'status': '待开始', 'assignee': '李工', 'start': '2026-10-15', 'finish': '2026-11-30'},
        {'version_idx': 2, 'req_idx': 0, 'name': 'AI边缘推理引擎', 'desc': '集成轻量化AI推理引擎，支持异常检测和预测性维护算法',
         'priority': '中', 'status': '待开始', 'assignee': '赵工', 'start': '2026-12-01', 'finish': '2027-02-15'},
    ]
    
    for task in tasks_p1:
        vid = version_ids_p1[task['version_idx']]
        rid = req_ids_p1[task['req_idx']]
        actual_finish = task['finish'] if task['status'] == '已完成' else None
        cursor.execute('''INSERT INTO dev_tasks (version_id, requirement_id, task_name, description, priority, status, assignee, planned_start, planned_finish, actual_finish, created_at)
                          VALUES (?,?,?,?,?,?,?,?,?,?,?)''',
                       (vid, rid, task['name'], task['desc'], task['priority'], task['status'],
                        task['assignee'], task['start'], task['finish'], actual_finish, now.strftime('%Y-%m-%d %H:%M:%S')))
    print(f"  ✓ 创建开发任务 {len(tasks_p1)}个")
    
    # 7. 创建BUG记录
    bugs_p1 = [
        {'bug_no': 'BUG-2026-001', 'version_idx': 0, 'title': '5G拨号偶发失败', 'desc': '在弱信号环境下，5G模组拨号偶发失败，需要重启才能恢复',
         'type': '功能缺陷', 'severity': '严重', 'priority': '高', 'status': '已修复', 'reporter': '测试组-刘工', 'assignee': '张工',
         'discovered': '2026-09-20', 'closed': '2026-10-05', 'close_reason': '已修复并验证通过',
         'fix_solution': '增加拨号重试机制和超时处理，优化模组状态监控', 'test_result': '通过', 'root_cause': '模组状态机异常未恢复',
         'corrective_action': '增加异常恢复机制', 'prevention_measure': '增加弱信号场景测试用例', 'is_quality': 0},
        {'bug_no': 'BUG-2026-002', 'version_idx': 0, 'title': 'Modbus数据点配置丢失', 'desc': '重启设备后，部分Modbus数据点配置丢失，需要重新配置',
         'type': '功能缺陷', 'severity': '一般', 'priority': '高', 'status': '已修复', 'reporter': '测试组-陈工', 'assignee': '李工',
         'discovered': '2026-09-25', 'closed': '2026-10-08', 'close_reason': '已修复并验证通过',
         'fix_solution': '修复配置保存逻辑，增加配置校验和备份机制', 'test_result': '通过', 'root_cause': '配置文件写入时断电导致损坏',
         'corrective_action': '增加原子写入和备份恢复', 'prevention_measure': '增加异常断电测试', 'is_quality': 0},
        {'bug_no': 'BUG-2026-003', 'version_idx': 0, 'title': 'Web界面内存泄漏', 'desc': '长时间运行Web配置界面后，设备内存占用持续增长，最终导致系统重启',
         'type': '性能问题', 'severity': '严重', 'priority': '高', 'status': '处理中', 'reporter': '测试组-刘工', 'assignee': '王工',
         'discovered': '2026-10-02', 'closed': None, 'close_reason': None,
         'fix_solution': '', 'test_result': '', 'root_cause': '', 'corrective_action': '', 'prevention_measure': '', 'is_quality': 1},
        {'bug_no': 'BUG-2026-004', 'version_idx': 1, 'title': 'OPC UA连接数超限', 'desc': 'OPC UA Server同时连接超过50个客户端时，出现连接拒绝和数据延迟',
         'type': '性能问题', 'severity': '一般', 'priority': '中', 'status': '待处理', 'reporter': '测试组-陈工', 'assignee': '赵工',
         'discovered': '2026-10-10', 'closed': None, 'close_reason': None,
         'fix_solution': '', 'test_result': '', 'root_cause': '', 'corrective_action': '', 'prevention_measure': '', 'is_quality': 0},
    ]
    
    for bug in bugs_p1:
        vid = version_ids_p1[bug['version_idx']]
        cursor.execute('''INSERT INTO bug_records (bug_no, product_id, version_id, title, description, bug_type, severity, priority, status,
                          reporter, assignee, discovered_at, closed_at, close_reason, fix_solution, test_result, root_cause,
                          corrective_action, prevention_measure, is_quality_issue, created_at)
                          VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                       (bug['bug_no'], product1_id, vid, bug['title'], bug['desc'], bug['type'], bug['severity'],
                        bug['priority'], bug['status'], bug['reporter'], bug['assignee'], bug['discovered'],
                        bug['closed'], bug['close_reason'], bug['fix_solution'], bug['test_result'], bug['root_cause'],
                        bug['corrective_action'], bug['prevention_measure'], bug['is_quality'], now.strftime('%Y-%m-%d %H:%M:%S')))
    print(f"  ✓ 创建BUG记录 {len(bugs_p1)}个")
    
    # 8. 创建生产排程
    cursor.execute('''INSERT INTO production_schedules (schedule_no, product_id, requirement_id, quantity, plan_start, plan_finish, status, remark, created_at)
                      VALUES (?,?,?,?,?,?,?,?,?)''',
                   ('PS-2026-001', product1_id, req_ids_p1[0], 200, '2026-11-01', '2026-11-20', '生产中',
                    '首批试生产200套，验证生产工艺', now.strftime('%Y-%m-%d %H:%M:%S')))
    schedule1_id = cursor.lastrowid
    print(f"  ✓ 创建生产排程 ID={schedule1_id}")
    
    # 9. 创建物料计划
    cursor.execute('SELECT id, material_name, material_code, is_key_material, quantity_per_unit FROM bom_items WHERE product_id=?', (product1_id,))
    bom_items = cursor.fetchall()
    for item in bom_items:
        qty_needed = item['quantity_per_unit'] * 200
        qty_available = qty_needed if item['is_key_material'] else int(qty_needed * 0.8)
        status = '已齐套' if qty_available >= qty_needed else '缺料'
        cursor.execute('''INSERT INTO material_plans (schedule_id, bom_item_id, material_name, material_code, is_key_material, quantity_needed, quantity_available, unit, status, created_at)
                          VALUES (?,?,?,?,?,?,?,?,?,?)''',
                       (schedule1_id, item['id'], item['material_name'], item['material_code'], item['is_key_material'],
                        qty_needed, qty_available, '个', status, now.strftime('%Y-%m-%d %H:%M:%S')))
    print(f"  ✓ 创建物料计划 {len(bom_items)}项")
    
    # 10. 创建质量检测
    cursor.execute('''INSERT INTO quality_inspections (inspection_no, inspection_type, product_id, quantity_inspected, quantity_passed, quantity_failed, inspector, inspection_date, result, remark, created_at)
                      VALUES (?,?,?,?,?,?,?,?,?,?,?)''',
                   ('QI-2026-001', '出厂检验', product1_id, 50, 48, 2, '质量组-孙工', '2026-10-20', '合格',
                    '首批试生产50套，2套外观瑕疵已返修', now.strftime('%Y-%m-%d %H:%M:%S')))
    print(f"  ✓ 创建质量检测记录")
    
    # 11. 创建交付记录
    cursor.execute('''INSERT INTO deliveries (delivery_no, requirement_id, product_id, quantity, delivery_date, customer, delivery_team, status, remark, created_at)
                      VALUES (?,?,?,?,?,?,?,?,?,?)''',
                   ('DL-2026-001', req_ids_p1[0], product1_id, 50, '2026-10-25', 'XX智能制造有限公司',
                    '交付服务组-周工', '已交付', '首批样品交付，客户验证中', now.strftime('%Y-%m-%d %H:%M:%S')))
    print(f"  ✓ 创建交付记录")
    
    # 12. 创建收入确认
    cursor.execute('''INSERT INTO revenue_confirmations (confirmation_no, requirement_id, product_id, quantity, unit_price, total_amount, confirmation_date, customer, status, remark, created_at)
                      VALUES (?,?,?,?,?,?,?,?,?,?,?)''',
                   ('RC-2026-001', req_ids_p1[0], product1_id, 50, 1200, 60000, '2026-10-30', 'XX智能制造有限公司',
                    '已确认', '首批样品款已到账', now.strftime('%Y-%m-%d %H:%M:%S')))
    print(f"  ✓ 创建收入确认记录")
    
    # ============================================================
    # 产品2: 低功耗蓝牙信标模组
    # ============================================================
    print("\n【产品2】低功耗蓝牙信标模组 (CM-BLE-B01)")
    
    # 1. 创建产品
    cursor.execute('''INSERT INTO products (name, model, version, description, lifecycle_stage, stocking_strategy, created_at)
                      VALUES (?,?,?,?,?,?,?)''',
                   ('低功耗蓝牙信标模组', 'CM-BLE-B01', 'V2.0.0',
                    '低功耗蓝牙5.0信标模组，支持iBeacon/Eddystone协议，电池续航可达3年，适用于室内定位、 proximity营销、资产追踪等场景。',
                    '量产中', '不囤货，按需生产', now.strftime('%Y-%m-%d %H:%M:%S')))
    product2_id = cursor.lastrowid
    print(f"  ✓ 创建产品 ID={product2_id}")
    
    # 2. 创建BOM表
    bom_items_p2 = [
        ('BLE5.0芯片', 'BOM-BLE-001', 1, 1, 'Nordic'),
        ('纽扣电池', 'BOM-BAT-001', 1, 1, '松下'),
        ('PCB天线', 'BOM-ANT-001', 0, 1, '本地供应商'),
        ('塑料外壳', 'BOM-CASE-002', 0, 1, '本地供应商'),
        ('3M胶贴', 'BOM-TAPE-001', 0, 1, '3M'),
    ]
    for name, code, is_key, qty, supplier in bom_items_p2:
        cursor.execute('''INSERT INTO bom_items (product_id, material_name, material_code, is_key_material, quantity_per_unit, supplier)
                          VALUES (?,?,?,?,?,?)''',
                       (product2_id, name, code, is_key, qty, supplier))
    print(f"  ✓ 创建BOM表 {len(bom_items_p2)}项")
    
    # 3. 创建4类需求
    reqs_p2 = [
        {
            'title': '大型商场室内定位项目-蓝牙信标批量需求',
            'req_type': '用户需求',
            'source_dept': '华南市场团队',
            'customer': 'XX商业管理集团',
            'description': '客户旗下10个大型商场室内定位项目，需要蓝牙信标实现商场内导航和精准营销，要求支持iBeacon协议，电池续航3年以上。',
            'quantity': 5000,
            'priority': '高',
            'status': '进行中',
            'delivery_date': '2026-11-30',
            'product_model': 'CM-BLE-B01',
            'batch_delivery': '是',
            'application_scenario': '大型商场室内定位与精准营销',
        },
        {
            'title': '集团智慧园区定位系统验证需求',
            'req_type': '集团系统侧需求',
            'source_dept': '集团信息部',
            'customer': '集团智慧园区项目组',
            'description': '集团智慧园区建设，需要蓝牙信标参与人员定位和资产追踪系统验证，验证定位精度和系统稳定性。',
            'quantity': 200,
            'priority': '中',
            'status': '已完成',
            'delivery_date': '2026-09-30',
            'product_model': 'CM-BLE-B01',
            'system_verify_point': '2026年Q3智慧园区系统验证',
            'verify_function': '人员定位、资产追踪、电子围栏、考勤打卡',
            'performance_req': '定位精度<3米，设备在线率>99%，支持1000+设备同时接入',
            'support_product': '低功耗蓝牙信标CM-BLE-B01',
        },
        {
            'title': '蓝牙设备SRRC认证与环保要求',
            'req_type': '集团应用侧需求',
            'source_dept': '集团质量部',
            'customer': '集团认证中心',
            'description': '蓝牙信标产品需要通过SRRC型号核准，满足RoHS环保要求，电池需要符合UN38.3运输安全标准。',
            'quantity': 0,
            'priority': '中',
            'status': '已完成',
            'delivery_date': '2026-08-31',
            'product_model': 'CM-BLE-B01',
            'certification_type': 'SRRC型号核准、RoHS、UN38.3',
            'spec_requirement': '发射功率≤+4dBm，工作温度-20℃~60℃，IP65防护',
            'industry_standard': '蓝牙5.0规范、SRRC无线电管理规定、RoHS 2.0',
            'baseline_product_type': '低功耗蓝牙设备',
        },
        {
            'title': '蓝牙AoA高精度定位技术预研课题',
            'req_type': '领导任务课题',
            'source_dept': '研发中心',
            'customer': '公司技术委员会',
            'description': '探索蓝牙5.1 AoA到达角高精度定位技术，实现厘米级定位，形成技术储备和专利布局。',
            'quantity': 0,
            'priority': '中',
            'status': '进行中',
            'delivery_date': '2027-06-30',
            'product_model': 'CM-BLE-B01',
            'task_progress': '技术调研完成，方案论证中，原型开发准备',
            'time_plan': '2026Q3技术调研、2026Q4方案论证、2027Q1原型开发、2027Q2测试验证',
            'deliverable': '蓝牙AoA定位技术研究报告、原型系统、测试报告、专利3-5项',
        },
    ]
    
    req_ids_p2 = []
    for req in reqs_p2:
        cursor.execute('''INSERT INTO requirements (title, req_type, source_dept, customer, description, quantity, priority, status, delivery_date,
                          product_model, batch_delivery, application_scenario, system_verify_point, verify_function, performance_req, support_product,
                          certification_type, spec_requirement, industry_standard, baseline_product_type, task_progress, time_plan, deliverable, created_at, updated_at)
                          VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                       (req['title'], req['req_type'], req['source_dept'], req['customer'], req['description'],
                        req['quantity'], req['priority'], req['status'], req['delivery_date'],
                        req.get('product_model',''), req.get('batch_delivery',''), req.get('application_scenario',''),
                        req.get('system_verify_point',''), req.get('verify_function',''), req.get('performance_req',''),
                        req.get('support_product',''), req.get('certification_type',''), req.get('spec_requirement',''),
                        req.get('industry_standard',''), req.get('baseline_product_type',''), req.get('task_progress',''),
                        req.get('time_plan',''), req.get('deliverable',''), now.strftime('%Y-%m-%d %H:%M:%S'),
                        now.strftime('%Y-%m-%d %H:%M:%S')))
        req_ids_p2.append(cursor.lastrowid)
    print(f"  ✓ 创建4类需求 {len(reqs_p2)}条")
    
    # 4. 创建软件版本
    versions_p2 = [
        {'version_no': 'V2.0.0', 'version_name': '量产稳定版', 'description': '量产稳定版本，支持iBeacon/Eddystone双协议',
         'capabilities': 'iBeacon、Eddystone、可配置发射功率、可配置广播间隔', 'planned_release_date': '2026-06-01', 'actual_release_date': '2026-06-15', 'status': '已发布'},
        {'version_no': 'V2.1.0', 'version_name': '功能增强版', 'description': '增加传感器支持和低功耗优化',
         'capabilities': '温湿度传感器、加速度传感器、低功耗模式优化', 'planned_release_date': '2026-12-01', 'status': '开发中'},
    ]
    
    version_ids_p2 = []
    for ver in versions_p2:
        cursor.execute('''INSERT INTO software_versions (product_id, version_no, version_name, description, capabilities, planned_release_date, actual_release_date, status, created_at)
                          VALUES (?,?,?,?,?,?,?,?,?)''',
                       (product2_id, ver['version_no'], ver['version_name'], ver['description'],
                        ver['capabilities'], ver['planned_release_date'], ver.get('actual_release_date'), ver['status'],
                        now.strftime('%Y-%m-%d %H:%M:%S')))
        version_ids_p2.append(cursor.lastrowid)
    print(f"  ✓ 创建软件版本 {len(versions_p2)}个")
    
    # 5. 关联需求到版本
    for vid in version_ids_p2:
        for rid in req_ids_p2[:2]:
            cursor.execute('INSERT OR IGNORE INTO version_requirements (version_id, requirement_id) VALUES (?,?)', (vid, rid))
    print(f"  ✓ 关联需求到版本")
    
    # 6. 创建开发任务
    tasks_p2 = [
        {'version_idx': 1, 'req_idx': 0, 'name': '温湿度传感器驱动', 'desc': '集成SHT30温湿度传感器，实现数据采集和上报',
         'priority': '高', 'status': '进行中', 'assignee': '陈工', 'start': '2026-09-01', 'finish': '2026-10-15'},
        {'version_idx': 1, 'req_idx': 0, 'name': '低功耗模式优化', 'desc': '优化电源管理，实现多级低功耗模式，延长电池续航',
         'priority': '高', 'status': '进行中', 'assignee': '林工', 'start': '2026-09-15', 'finish': '2026-10-31'},
        {'version_idx': 1, 'req_idx': 1, 'name': '加速度传感器支持', 'desc': '集成LIS2DH加速度传感器，实现运动检测和防拆报警',
         'priority': '中', 'status': '待开始', 'assignee': '陈工', 'start': '2026-10-01', 'finish': '2026-11-15'},
    ]
    
    for task in tasks_p2:
        vid = version_ids_p2[task['version_idx']]
        rid = req_ids_p2[task['req_idx']]
        actual_finish = task['finish'] if task['status'] == '已完成' else None
        cursor.execute('''INSERT INTO dev_tasks (version_id, requirement_id, task_name, description, priority, status, assignee, planned_start, planned_finish, actual_finish, created_at)
                          VALUES (?,?,?,?,?,?,?,?,?,?,?)''',
                       (vid, rid, task['name'], task['desc'], task['priority'], task['status'],
                        task['assignee'], task['start'], task['finish'], actual_finish, now.strftime('%Y-%m-%d %H:%M:%S')))
    print(f"  ✓ 创建开发任务 {len(tasks_p2)}个")
    
    # 7. 创建BUG记录
    bugs_p2 = [
        {'bug_no': 'BUG-2026-005', 'version_idx': 0, 'title': '电池续航不达标', 'desc': '实测电池续航仅2.5年，未达到3年设计目标',
         'type': '性能问题', 'severity': '一般', 'priority': '高', 'status': '已修复', 'reporter': '测试组-吴工', 'assignee': '林工',
         'discovered': '2026-05-10', 'closed': '2026-05-25', 'close_reason': '已修复并验证通过',
         'fix_solution': '优化广播间隔和发射功率默认值，优化休眠电流', 'test_result': '通过', 'root_cause': '默认参数设置偏保守',
         'corrective_action': '优化默认参数', 'prevention_measure': '增加续航自动化测试', 'is_quality': 0},
        {'bug_no': 'BUG-2026-006', 'version_idx': 1, 'title': '温湿度数据漂移', 'desc': '长时间运行后，温湿度数据出现漂移，需要重新校准',
         'type': '功能缺陷', 'severity': '一般', 'priority': '中', 'status': '处理中', 'reporter': '测试组-吴工', 'assignee': '陈工',
         'discovered': '2026-10-05', 'closed': None, 'close_reason': None,
         'fix_solution': '', 'test_result': '', 'root_cause': '', 'corrective_action': '', 'prevention_measure': '', 'is_quality': 0},
    ]
    
    for bug in bugs_p2:
        vid = version_ids_p2[bug['version_idx']]
        cursor.execute('''INSERT INTO bug_records (bug_no, product_id, version_id, title, description, bug_type, severity, priority, status,
                          reporter, assignee, discovered_at, closed_at, close_reason, fix_solution, test_result, root_cause,
                          corrective_action, prevention_measure, is_quality_issue, created_at)
                          VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                       (bug['bug_no'], product2_id, vid, bug['title'], bug['desc'], bug['type'], bug['severity'],
                        bug['priority'], bug['status'], bug['reporter'], bug['assignee'], bug['discovered'],
                        bug['closed'], bug['close_reason'], bug['fix_solution'], bug['test_result'], bug['root_cause'],
                        bug['corrective_action'], bug['prevention_measure'], bug['is_quality'], now.strftime('%Y-%m-%d %H:%M:%S')))
    print(f"  ✓ 创建BUG记录 {len(bugs_p2)}个")
    
    # 8. 创建生产排程
    cursor.execute('''INSERT INTO production_schedules (schedule_no, product_id, requirement_id, quantity, plan_start, plan_finish, status, remark, created_at)
                      VALUES (?,?,?,?,?,?,?,?,?)''',
                   ('PS-2026-002', product2_id, req_ids_p2[0], 2000, '2026-10-15', '2026-11-10', '生产中',
                    '首批2000套，供应商场项目', now.strftime('%Y-%m-%d %H:%M:%S')))
    schedule2_id = cursor.lastrowid
    print(f"  ✓ 创建生产排程 ID={schedule2_id}")
    
    # 9. 创建物料计划
    cursor.execute('SELECT id, material_name, material_code, is_key_material, quantity_per_unit FROM bom_items WHERE product_id=?', (product2_id,))
    bom_items2 = cursor.fetchall()
    for item in bom_items2:
        qty_needed = item['quantity_per_unit'] * 2000
        qty_available = qty_needed if item['is_key_material'] else int(qty_needed * 0.9)
        status = '已齐套' if qty_available >= qty_needed else '缺料'
        cursor.execute('''INSERT INTO material_plans (schedule_id, bom_item_id, material_name, material_code, is_key_material, quantity_needed, quantity_available, unit, status, created_at)
                          VALUES (?,?,?,?,?,?,?,?,?,?)''',
                       (schedule2_id, item['id'], item['material_name'], item['material_code'], item['is_key_material'],
                        qty_needed, qty_available, '个', status, now.strftime('%Y-%m-%d %H:%M:%S')))
    print(f"  ✓ 创建物料计划 {len(bom_items2)}项")
    
    # 10. 创建质量检测
    cursor.execute('''INSERT INTO quality_inspections (inspection_no, inspection_type, product_id, quantity_inspected, quantity_passed, quantity_failed, inspector, inspection_date, result, remark, created_at)
                      VALUES (?,?,?,?,?,?,?,?,?,?,?)''',
                   ('QI-2026-002', '出厂检验', product2_id, 500, 495, 5, '质量组-孙工', '2026-10-20', '合格',
                    '抽检500套，5套外观不良已剔除', now.strftime('%Y-%m-%d %H:%M:%S')))
    print(f"  ✓ 创建质量检测记录")
    
    # 11. 创建交付记录
    cursor.execute('''INSERT INTO deliveries (delivery_no, requirement_id, product_id, quantity, delivery_date, customer, delivery_team, status, remark, created_at)
                      VALUES (?,?,?,?,?,?,?,?,?,?)''',
                   ('DL-2026-002', req_ids_p2[0], product2_id, 1000, '2026-10-28', 'XX商业管理集团',
                    '交付服务组-吴工', '已交付', '首批1000套交付，3个商场已部署', now.strftime('%Y-%m-%d %H:%M:%S')))
    print(f"  ✓ 创建交付记录")
    
    # 12. 创建收入确认
    cursor.execute('''INSERT INTO revenue_confirmations (confirmation_no, requirement_id, product_id, quantity, unit_price, total_amount, confirmation_date, customer, status, remark, created_at)
                      VALUES (?,?,?,?,?,?,?,?,?,?,?)''',
                   ('RC-2026-002', req_ids_p2[0], product2_id, 1000, 35, 35000, '2026-11-05', 'XX商业管理集团',
                    '已确认', '首批货款已到账', now.strftime('%Y-%m-%d %H:%M:%S')))
    print(f"  ✓ 创建收入确认记录")
    
    # ============================================================
    # 提交事务
    # ============================================================
    conn.commit()
    
    print("\n" + "=" * 60)
    print("数据生成完成！")
    print("=" * 60)
    print(f"\n统计汇总：")
    print(f"  产品: 2款 (5G工业网关、蓝牙信标)")
    print(f"  需求: 8条 (4类×2款)")
    print(f"  软件版本: 5个")
    print(f"  开发任务: 10个")
    print(f"  BUG记录: 6个")
    print(f"  生产排程: 2个")
    print(f"  物料计划: 13项")
    print(f"  质量检测: 2条")
    print(f"  交付记录: 2条")
    print(f"  收入确认: 2条")
    
    # 验证数据
    print(f"\n数据验证：")
    cursor.execute('SELECT COUNT(*) FROM products WHERE id IN (?,?)', (product1_id, product2_id))
    print(f"  产品数: {cursor.fetchone()[0]}")
    cursor.execute('SELECT COUNT(*) FROM requirements WHERE product_model IN (?,?)', ('CM-5G-GW01', 'CM-BLE-B01'))
    print(f"  需求数: {cursor.fetchone()[0]}")
    cursor.execute('SELECT COUNT(*) FROM software_versions WHERE product_id IN (?,?)', (product1_id, product2_id))
    print(f"  版本数: {cursor.fetchone()[0]}")
    cursor.execute('SELECT COUNT(*) FROM dev_tasks WHERE version_id IN (SELECT id FROM software_versions WHERE product_id IN (?,?))', (product1_id, product2_id))
    print(f"  任务数: {cursor.fetchone()[0]}")
    cursor.execute('SELECT COUNT(*) FROM bug_records WHERE product_id IN (?,?)', (product1_id, product2_id))
    print(f"  BUG数: {cursor.fetchone()[0]}")
    
    conn.close()
    print("\n✓ 所有数据已成功写入数据库！")

if __name__ == '__main__':
    generate_product_data()
