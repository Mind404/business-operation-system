# -*- coding: utf-8 -*-
"""
4款产品全场景虚拟数据生成脚本（V2完整版）
覆盖所有39张业务表，验证完整业务链路和信息关联
4款产品：5G工业通信模组、4G Cat.1模组、NB-IoT低功耗模组、WiFi6+蓝牙二合一模组
"""
import os
import sqlite3
import random
from datetime import datetime, timedelta

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'business.db')

# 4款产品定义
PRODUCTS = [
    {'name': '5G工业通信模组', 'model': 'CM-5G-I01', 'version': 'V2.3.0',
     'description': '面向工业物联网的5G通信模组，支持NSA/SA双模，宽温设计',
     'lifecycle_stage': '量产中', 'stocking_strategy': '不囤货，按需生产'},
    {'name': '4G Cat.1模组', 'model': 'CM-4G-C01', 'version': 'V1.6.0',
     'description': '低功耗4G Cat.1 bis模组，适用于共享设备、追踪器',
     'lifecycle_stage': '量产中', 'stocking_strategy': '不囤货，按需生产'},
    {'name': 'NB-IoT低功耗模组', 'model': 'CM-NB-L01', 'version': 'V1.3.0',
     'description': 'NB-IoT低功耗广域网模组，电池供电可达5年，适用于水表气表',
     'lifecycle_stage': '维护中', 'stocking_strategy': '不囤货，按需生产'},
    {'name': 'WiFi6+蓝牙二合一模组', 'model': 'CM-WB-W01', 'version': 'V1.0.0',
     'description': 'WiFi6 + 蓝牙5.2二合一模组，面向智能家居和消费电子',
     'lifecycle_stage': '研发中', 'stocking_strategy': '不囤货，按需生产'},
]

DEPTS = ['华东市场团队', '华南市场团队', '华北市场团队', '西南市场团队', '产品部', '研发部']
CUSTOMERS = ['XX通信有限公司', 'YY科技股份公司', 'ZZ物联网科技', 'WW智能设备公司', 'VV车联网科技']
SCENES = ['智慧工厂改造', '智能电表部署', '共享单车运营', '车载前装项目', '智能家居生态', '农业物联网监测']
FEATURES = ['MQTT协议支持', 'OTA远程升级', 'GPS定位', '蓝牙配网', '多APN切换', '低功耗模式', '工业级宽温', '双SIM卡']
SUPPLIERS = ['深圳XX电子厂', '东莞YY科技', '苏州ZZ精密', '上海WW通信']

def rand_date(s=-60, e=60):
    return (datetime.now() + timedelta(days=random.randint(s, e))).strftime('%Y-%m-%d')

def rand_dt(s=-60, e=0):
    return (datetime.now() + timedelta(days=random.randint(s, e),
            hours=random.randint(0,23), minutes=random.randint(0,59))).strftime('%Y-%m-%d %H:%M:%S')

def generate():
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    # 执行schema
    with open(os.path.join(os.path.dirname(__file__), 'schema.sql'), 'r', encoding='utf-8') as f:
        c.executescript(f.read())

    print("=== 开始生成4款产品全场景测试数据 ===")

    # 1. 产品
    product_ids = []
    for p in PRODUCTS:
        c.execute('''INSERT INTO products (name, model, version, description, lifecycle_stage, stocking_strategy, created_at)
            VALUES (?,?,?,?,?,?,?)''', (p['name'], p['model'], p['version'], p['description'], p['lifecycle_stage'], p['stocking_strategy'], rand_dt(-90,-60)))
        product_ids.append(c.lastrowid)
    print(f"  ✓ 产品: {len(product_ids)}款")

    # 2. 需求（每款产品5-8条）
    req_ids = []
    for pid in product_ids:
        pname = PRODUCTS[pid-1]['name']
        for i in range(random.randint(5, 8)):
            status = random.choice(['待评审', '已评审', '进行中', '已完成', '已取消'])
            c.execute('''INSERT INTO requirements (title, source_dept, customer, description, quantity, priority, status, delivery_date, created_at)
                VALUES (?,?,?,?,?,?,?,?,?)''',
                (f'{pname}-{random.choice(FEATURES)}需求', random.choice(DEPTS), random.choice(CUSTOMERS),
                 f'客户需要{random.choice(SCENES)}，需求量{random.randint(100,5000)}套',
                 random.randint(100, 5000), random.choice(['高','中','低']), status, rand_date(0,90), rand_dt(-60,-10)))
            req_ids.append(c.lastrowid)
    print(f"  ✓ 需求: {len(req_ids)}条")

    # 3. BOM物料（每款产品6-8项）
    bom_ids = []
    for pid in product_ids:
        materials = [
            ('主芯片', 'IC-MAIN-001', 1, '个', 1), ('射频前端', 'IC-RF-001', 1, '个', 1),
            ('电源管理IC', 'IC-PMIC-001', 1, '个', 1), ('Flash存储器', 'IC-FLASH-001', 0, '个', 1),
            ('晶振', 'XTAL-001', 0, '个', 2), ('射频连接器', 'CON-RF-001', 0, '个', 1),
            ('PCB板', 'PCB-001', 0, '片', 1), ('屏蔽罩', 'SHIELD-001', 0, '个', 1),
        ]
        for mname, mcode, is_key, unit, qty in materials[:random.randint(6,8)]:
            c.execute('''INSERT INTO bom_items (product_id, material_name, material_code, is_key_material, unit, quantity_per_unit, supplier)
                VALUES (?,?,?,?,?,?,?)''', (pid, mname, mcode, is_key, unit, qty, random.choice(SUPPLIERS)))
            bom_ids.append(c.lastrowid)
    print(f"  ✓ BOM物料: {len(bom_ids)}项")

    # 4. 库存
    inv_ids = []
    for bid in bom_ids:
        c.execute('SELECT material_name, material_code, is_key_material, unit FROM bom_items WHERE id=?', (bid,))
        b = c.fetchone()
        c.execute('''INSERT INTO inventory (material_name, material_code, is_key_material, location, quantity, unit, status, remark)
            VALUES (?,?,?,?,?,?,?,?)''',
            (b[0], b[1], b[2], f'A区-{random.randint(1,20)}架', random.randint(0, 5000), b[3],
             random.choice(['充足','正常','偏低','缺货']), f'{PRODUCTS[0]["name"]}通用物料'))
        inv_ids.append(c.lastrowid)
    print(f"  ✓ 库存: {len(inv_ids)}条")

    # 5. 物料领用单
    req_ids_list = []
    for pid in product_ids[:3]:
        for i in range(2):
            c.execute('''INSERT INTO material_requisitions (requisition_no, product_id, requirement_id, applicant, status, total_quantity, approved_at, created_at)
                VALUES (?,?,?,?,?,?,?,?)''',
                (f'LQ{rand_dt(-30,0)[:10].replace("-","")}{i+1:03d}', pid, random.choice(req_ids),
                 'product', random.choice(['已审批','待审批','已领用']), random.randint(500, 3000), rand_dt(-20,-5), rand_dt(-30,-20)))
            rid = c.lastrowid
            req_ids_list.append(rid)
            for bid in random.sample(bom_ids, 3):
                c.execute('SELECT material_name, unit FROM bom_items WHERE id=?', (bid,))
                b = c.fetchone()
                c.execute('''INSERT INTO requisition_items (requisition_id, material_name, quantity, unit)
                    VALUES (?,?,?,?)''', (rid, b[0], random.randint(100, 1000), b[1]))
    print(f"  ✓ 物料领用单: {len(req_ids_list)}张")

    # 6. 生产订单
    order_ids = []
    for pid in product_ids[:3]:
        for i in range(2):
            status = random.choice(['生产中','已完成','待排产','已入库'])
            c.execute('''INSERT INTO production_orders (order_no, product_id, requirement_id, supplier, quantity, plan_start, plan_finish, actual_finish, status, wip_progress, remark, created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)''',
                (f'PO{rand_dt(-40,0)[:10].replace("-","")}{i+1:03d}', pid, random.choice(req_ids), random.choice(SUPPLIERS),
                 random.randint(500, 3000), rand_date(-30,-10), rand_date(-10,10),
                 rand_date(-5,0) if status=='已完成' else None, status,
                 random.randint(0,100) if status=='生产中' else (100 if status in ['已完成','已入库'] else 0),
                 '通信模组生产', rand_dt(-40,-30)))
            order_ids.append(c.lastrowid)
    print(f"  ✓ 生产订单: {len(order_ids)}张")

    # 7. 质量检测
    insp_ids = []
    for oid in order_ids:
        c.execute('SELECT product_id, quantity FROM production_orders WHERE id=?', (oid,))
        o = c.fetchone()
        sample = min(int(o[1]*0.1), 200)
        passed = sample - random.randint(0, int(sample*0.1))
        result = '合格' if passed == sample else random.choice(['合格','不合格','有条件合格'])
        c.execute('''INSERT INTO quality_inspections (inspection_no, inspection_type, production_order_id, product_id, inspector, sample_quantity, pass_quantity, fail_quantity, pass_rate, result, defect_description, inspection_date, created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)''',
            (f'QC{rand_dt(-20,0)[:10].replace("-","")}{oid:03d}', '出货检验', oid, o[0], 'quality',
             sample, passed, sample-passed, round(passed/sample*100, 1), result,
             '外观不良/焊接不良' if result!='合格' else '无', rand_date(-10,0), rand_dt(-10,-5)))
        insp_ids.append(c.lastrowid)
        if result != '合格':
            c.execute('''INSERT INTO defect_handling (inspection_id, defect_type, root_cause, handling_method, responsible_person, status, closed_at, created_at)
                VALUES (?,?,?,?,?,?,?,?)''',
                (c.lastrowid, random.choice(['外观不良','焊接不良','功能不良']), '生产工艺问题',
                 random.choice(['返工','返修','报废','特采']), '生产供应商', random.choice(['已关闭','处理中']),
                 rand_dt(-5,0), rand_dt(-10,-5)))
    print(f"  ✓ 质量检测: {len(insp_ids)}条")

    # 8. 入库单
    receipt_ids = []
    for oid in order_ids[:4]:
        c.execute('SELECT product_id, quantity FROM production_orders WHERE id=?', (oid,))
        o = c.fetchone()
        c.execute('''INSERT INTO warehouse_receipts (receipt_no, production_order_id, product_id, quantity, quality_status, approver, status, approved_at, created_at)
            VALUES (?,?,?,?,?,?,?,?,?)''',
            (f'RK{rand_dt(-15,0)[:10].replace("-","")}{oid:03d}', oid, o[0], o[1], '合格', 'quality',
             random.choice(['已入库','待审批']), rand_dt(-10,0), rand_dt(-15,-10)))
        receipt_ids.append(c.lastrowid)
    print(f"  ✓ 入库单: {len(receipt_ids)}张")

    # 9. 交付记录
    delivery_ids = []
    dl_counter = 0
    for pid in product_ids[:3]:
        for i in range(2):
            dl_counter += 1
            c.execute('''INSERT INTO deliveries (delivery_no, product_id, market_team, quantity, customer, purpose, delivery_date, status, feedback, created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?)''',
                (f'DL{datetime.now().strftime("%Y%m%d")}{dl_counter:04d}', pid, random.choice(DEPTS[:4]),
                 random.randint(100, 2000), random.choice(CUSTOMERS), random.choice(SCENES),
                 rand_date(-15,5), random.choice(['已交付','运输中','待发货']),
                 random.choice(['客户满意','正常交付','待反馈']), rand_dt(-20,-10)))
            delivery_ids.append(c.lastrowid)
    print(f"  ✓ 交付记录: {len(delivery_ids)}条")

    # 10. 软件版本（每款产品2-3个版本）
    version_ids = []
    for pid in product_ids:
        for i in range(random.randint(2, 3)):
            vno = f'V{random.randint(1,3)}.{random.randint(0,9)}.{random.randint(0,9)}'
            status = random.choice(['规划中','开发中','测试中','已发布','已归档'])
            c.execute('''INSERT INTO software_versions (product_id, version_no, version_name, description, capabilities, planned_release_date, actual_release_date, status, created_at)
                VALUES (?,?,?,?,?,?,?,?,?)''',
                (pid, vno, f'{PRODUCTS[pid-1]["name"]} {vno}', f'{vno}版本更新，修复已知问题',
                 ','.join(random.sample(FEATURES, 3)), rand_date(0,60),
                 rand_date(-30,0) if status in ['已发布','已归档'] else None, status, rand_dt(-60,-30)))
            version_ids.append(c.lastrowid)
    print(f"  ✓ 软件版本: {len(version_ids)}个")

    # 11. 版本关联需求
    for vid in version_ids:
        for rid in random.sample(req_ids, min(3, len(req_ids))):
            c.execute('''INSERT OR IGNORE INTO version_requirements (version_id, requirement_id, priority, created_at)
                VALUES (?,?,?,?)''', (vid, rid, random.choice(['高','中','低']), rand_dt(-30,-10)))

    # 12. 开发任务（每个版本3-5个）
    task_ids = []
    for vid in version_ids:
        for i in range(random.randint(3, 5)):
            status = random.choice(['待开发','开发中','已完成','已取消'])
            c.execute('''INSERT INTO dev_tasks (version_id, task_name, description, priority, status, assignee, planned_start, planned_finish, actual_finish, created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?)''',
                (vid, f'{random.choice(FEATURES)}开发', f'实现{random.choice(FEATURES)}功能',
                 random.choice(['高','中','低']), status, 'dev_test',
                 rand_date(-30,-10), rand_date(-10,20),
                 rand_date(-5,0) if status=='已完成' else None, rand_dt(-40,-30)))
            task_ids.append(c.lastrowid)
    print(f"  ✓ 开发任务: {len(task_ids)}个")

    # 13. Bug记录
    bug_ids = []
    for pid in product_ids:
        for i in range(random.randint(3, 5)):
            status = random.choice(['待处理','处理中','已关闭','已取消'])
            severity = random.choice(['致命','严重','一般','轻微'])
            c.execute('''INSERT INTO bug_records (bug_no, product_id, version_id, title, description, severity, status, reporter, assignee, discovered_at, closed_at, root_cause, corrective_action, prevention_measure, is_quality_issue, created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                (f'BUG{rand_dt(-30,0)[:10].replace("-","")}{i+1:03d}', pid, random.choice(version_ids),
                 f'{random.choice(FEATURES)}功能异常', f'{random.choice(FEATURES)}在特定场景下异常',
                 severity, status, 'dev_test', 'dev_test', rand_dt(-25,-5),
                 rand_dt(-5,0) if status=='已关闭' else None,
                 '代码逻辑问题' if status=='已关闭' else None,
                 '修复代码并增加测试用例' if status=='已关闭' else None,
                 '增加回归测试' if status=='已关闭' else None,
                 1 if severity in ['致命','严重'] else 0, rand_dt(-30,-25)))
            bug_id = c.lastrowid
            bug_ids.append(bug_id)
            # Bug工作流
            c.execute('''INSERT INTO bug_workflow (bug_id, action, from_status, to_status, operator, comment, created_at)
                VALUES (?,?,?,?,?,?,?)''', (bug_id, '创建', '', '待处理', 'dev_test', '发现Bug', rand_dt(-30,-25)))
            if status != '待处理':
                c.execute('''INSERT INTO bug_workflow (bug_id, action, from_status, to_status, operator, comment, created_at)
                    VALUES (?,?,?,?,?,?,?)''', (bug_id, '开始处理', '待处理', '处理中', 'dev_test', '开始分析', rand_dt(-25,-20)))
            if status == '已关闭':
                c.execute('''INSERT INTO bug_workflow (bug_id, action, from_status, to_status, operator, comment, created_at)
                    VALUES (?,?,?,?,?,?,?)''', (bug_id, '关闭', '处理中', '已关闭', 'dev_test', '修复完成', rand_dt(-5,0)))
    print(f"  ✓ Bug记录: {len(bug_ids)}条")

    # 14. 生产排程
    schedule_ids = []
    for pid in product_ids[:3]:
        for i in range(2):
            c.execute('''INSERT INTO production_schedules (schedule_no, product_id, requirement_id, quantity, plan_start, plan_finish, status, remark, created_at)
                VALUES (?,?,?,?,?,?,?,?,?)''',
                (f'PS{rand_dt(-20,0)[:10].replace("-","")}{i+1:03d}', pid, random.choice(req_ids),
                 random.randint(500, 3000), rand_date(-15,-5), rand_date(-5,15),
                 random.choice(['排产中','生产中','已完成']), '按需求排产', rand_dt(-20,-15)))
            sid = c.lastrowid
            schedule_ids.append(sid)
            # 6道工序
            processes = ['SMT贴片', '回流焊接', 'AOI检测', '功能测试', '外观检验', '包装入库']
            for idx, pname in enumerate(processes):
                pstatus = random.choice(['未开始','进行中','已完成'])
                c.execute('''INSERT INTO production_processes (schedule_id, process_name, sequence, planned_start, planned_finish, actual_finish, status, responsible, created_at)
                    VALUES (?,?,?,?,?,?,?,?,?)''',
                    (sid, pname, idx+1, rand_date(-15+idx*2,-13+idx*2), rand_date(-13+idx*2,-11+idx*2),
                     rand_date(-10+idx*2,-8+idx*2) if pstatus=='已完成' else None, pstatus,
                     random.choice(SUPPLIERS), rand_dt(-20,-15)))
            # 物料需求计划
            for bid in random.sample([b for b in bom_ids if b <= pid*8], min(4, 8)):
                c.execute('SELECT material_name, material_code, is_key_material, unit FROM bom_items WHERE id=?', (bid,))
                b = c.fetchone()
                needed = random.randint(500, 3000)
                available = random.randint(0, 5000)
                c.execute('''INSERT INTO material_plans (schedule_id, bom_item_id, material_name, material_code, is_key_material, quantity_needed, quantity_available, unit, status, created_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?)''',
                    (sid, bid, b[0], b[1], b[2], needed, available, b[3],
                     '充足' if available >= needed else '缺料', rand_dt(-20,-15)))
    print(f"  ✓ 生产排程: {len(schedule_ids)}张")

    # 15. 测试用例库（每款产品8-12条）
    case_ids = []
    for pid in product_ids:
        for i in range(random.randint(8, 12)):
            c.execute('''INSERT INTO test_cases (product_id, case_no, title, case_type, priority, precondition, test_steps, expected_result, status, creator, created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)''',
                (pid, f'TC{pid:02d}{i+1:03d}', f'{random.choice(FEATURES)}功能测试',
                 random.choice(['功能测试','性能测试','兼容性测试','稳定性测试']),
                 random.choice(['高','中','低']), '设备正常上电，连接测试环境',
                 f'1. 执行{random.choice(FEATURES)}\n2. 观察结果\n3. 记录数据',
                 '功能正常，无异常', random.choice(['启用','停用']), 'dev_test', rand_dt(-50,-30)))
            case_ids.append(c.lastrowid)
    print(f"  ✓ 测试用例: {len(case_ids)}条")

    # 16. 测试执行
    exec_ids = []
    for pid in product_ids[:3]:
        for i in range(2):
            c.execute('''INSERT INTO test_executions (execution_no, product_id, version_id, executor, status, total_cases, passed_cases, failed_cases, start_date, end_date, summary, created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)''',
                (f'TE{rand_dt(-20,0)[:10].replace("-","")}{i+1:03d}', pid, random.choice(version_ids),
                 'dev_test', random.choice(['进行中','已完成','已取消']),
                 random.randint(10, 30), random.randint(5, 25), random.randint(0, 5),
                 rand_dt(-15,-10), rand_dt(-10,-5), '回归测试', rand_dt(-20,-15)))
            eid = c.lastrowid
            exec_ids.append(eid)
            # 用例执行结果
            for cid in random.sample(case_ids[:10], min(5, 10)):
                c.execute('''INSERT INTO test_case_results (execution_id, case_id, result, actual_result, executed_at, executor)
                    VALUES (?,?,?,?,?,?)''',
                    (eid, cid, random.choice(['通过','失败','阻塞','未执行']),
                     '功能正常' if random.random()>0.3 else '发现异常', rand_dt(-12,-8), 'dev_test'))
    print(f"  ✓ 测试执行: {len(exec_ids)}次")

    # 17. 技术支持工单
    ticket_ids = []
    for pid in product_ids[:3]:
        for i in range(random.randint(2, 4)):
            status = random.choice(['待处理','处理中','已解决','已关闭'])
            c.execute('''INSERT INTO tech_support_tickets (ticket_no, customer, contact_person, contact_phone, product_id, title, description, category, priority, status, handler, solution, resolved_at, satisfaction, created_at, updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                (f'TK{rand_dt(-30,0)[:10].replace("-","")}{i+1:03d}', random.choice(CUSTOMERS),
                 f'张{random.choice(["工","经理","主管"])}', f'138{random.randint(10000000,99999999)}',
                 pid, f'{random.choice(FEATURES)}使用问题', f'客户反馈{random.choice(FEATURES)}无法正常使用',
                 random.choice(['硬件问题','软件问题','配置问题','使用咨询']), random.choice(['高','中','低']),
                 status, 'delivery', '远程指导解决' if status in ['已解决','已关闭'] else None,
                 rand_dt(-10,-5) if status in ['已解决','已关闭'] else None,
                 random.choice([5,4,3,None]) if status=='已关闭' else None,
                 rand_dt(-30,-25), rand_dt(-20,-10)))
            tid = c.lastrowid
            ticket_ids.append(tid)
            # 工单处理记录
            c.execute('''INSERT INTO ticket_workflow (ticket_id, action, operator, comment, created_at)
                VALUES (?,?,?,?,?)''', (tid, '创建工单', 'delivery', '客户反馈问题', rand_dt(-30,-25)))
            if status != '待处理':
                c.execute('''INSERT INTO ticket_workflow (ticket_id, action, operator, comment, created_at)
                    VALUES (?,?,?,?,?)''', (tid, '开始处理', 'delivery', '分析问题原因', rand_dt(-25,-20)))
            if status in ['已解决','已关闭']:
                c.execute('''INSERT INTO ticket_workflow (ticket_id, action, operator, comment, created_at)
                    VALUES (?,?,?,?,?)''', (tid, '解决问题', 'delivery', '远程指导客户解决', rand_dt(-10,-5)))
    print(f"  ✓ 技术支持工单: {len(ticket_ids)}条")

    # 18. 用户反馈
    feedback_ids = []
    for pid in product_ids:
        for i in range(random.randint(2, 4)):
            c.execute('''INSERT INTO user_feedbacks (customer, product_id, feedback_type, title, content, source, priority, status, handler, converted_requirement_id, created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)''',
                (random.choice(CUSTOMERS), pid,
                 random.choice(['功能建议','Bug反馈','使用吐槽','性能优化']),
                 f'希望增加{random.choice(FEATURES)}功能', f'客户在{random.choice(SCENES)}中需要{random.choice(FEATURES)}',
                 random.choice(['技术支持','市场反馈','客户拜访','在线客服']),
                 random.choice(['高','中','低']),
                 random.choice(['待评估','评估中','已转化','已关闭']), 'delivery',
                 random.choice(req_ids) if random.random()>0.5 else None, rand_dt(-40,-30)))
            feedback_ids.append(c.lastrowid)
    print(f"  ✓ 用户反馈: {len(feedback_ids)}条")

    # 19. 交付计划
    plan_ids = []
    for pid in product_ids[:3]:
        for i in range(2):
            c.execute('''INSERT INTO delivery_plans (plan_no, product_id, customer, quantity, planned_date, actual_date, status, owner, remark, created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?)''',
                (f'DP{rand_dt(-20,0)[:10].replace("-","")}{i+1:03d}', pid, random.choice(CUSTOMERS),
                 random.randint(500, 3000), rand_date(-10,30), rand_date(-5,5) if random.random()>0.5 else None,
                 random.choice(['计划中','待交付','运输中','已交付','已取消']), random.choice(DEPTS[:4]),
                 '按合同交付', rand_dt(-20,-15)))
            plan_ids.append(c.lastrowid)
    print(f"  ✓ 交付计划: {len(plan_ids)}条")

    # 20. 收入确认
    revenue_ids = []
    for pid in product_ids[:3]:
        for i in range(2):
            c.execute('''INSERT INTO revenue_confirmations (confirm_no, customer, contract_no, product_id, amount, confirmed_amount, confirm_date, status, period, voucher, handler, remark, created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                (f'RC{rand_dt(-30,0)[:10].replace("-","")}{i+1:03d}', random.choice(CUSTOMERS),
                 f'HT{rand_dt(-60,-30)[:10].replace("-","")}', pid,
                 random.randint(50000, 500000), random.randint(0, 500000),
                 rand_date(-20,0), random.choice(['待确认','已确认','已到账']),
                 f'{random.randint(2024,2026)}-{random.randint(1,12):02d}',
                 f'凭证{random.randint(10000,99999)}', 'delivery',
                 '按合同确认收入', rand_dt(-30,-25)))
            revenue_ids.append(c.lastrowid)
    print(f"  ✓ 收入确认: {len(revenue_ids)}条")

    # 21. 技术规格
    spec_ids = []
    for pid in product_ids:
        c.execute('''INSERT INTO tech_specs (product_id, spec_name, spec_type, content, status, author, created_at)
            VALUES (?,?,?,?,?,?,?)''',
            (pid, f'{PRODUCTS[pid-1]["name"]}技术规格书',
             random.choice(['功能规格','硬件规格','软件规格','接口规格','性能规格']),
             f'1. 产品概述\n2. 电气规格\n3. 机械规格\n4. 环境规格\n5. 接口定义\n6. 认证信息',
             random.choice(['草稿','评审中','已发布','已归档']), 'product', rand_dt(-60,-40)))
        spec_ids.append(c.lastrowid)
    print(f"  ✓ 技术规格: {len(spec_ids)}份")

    # 22. 合同
    contract_ids = []
    for pid in product_ids[:3]:
        for i in range(2):
            c.execute('''INSERT INTO contracts (contract_no, contract_type, supplier, product_id, amount, sign_date, delivery_date, status, owner, progress, payment_status, remark, created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                (f'HT{rand_dt(-60,-30)[:10].replace("-","")}{i+1:03d}',
                 random.choice(['生产合同','采购合同','服务合同']), random.choice(SUPPLIERS), pid,
                 random.randint(100000, 1000000), rand_date(-60,-30), rand_date(-30,30),
                 random.choice(['执行中','已完成','已取消']), 'product',
                 random.randint(0,100), random.choice(['未付款','部分付款','已付款']),
                 '通信模组生产合同', rand_dt(-60,-55)))
            contract_ids.append(c.lastrowid)
    print(f"  ✓ 合同: {len(contract_ids)}份")

    # 23. 资源调度
    resource_ids = []
    for i in range(6):
        c.execute('''INSERT INTO resource_schedules (resource_type, resource_name, project, task, start_date, end_date, allocation, status, owner, remark, created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)''',
            (random.choice(['人力资源','物料资源','设备资源','产线资源']),
             random.choice(['研发工程师A','测试工程师B','SMT产线1','关键物料批次']),
             f'{PRODUCTS[random.randint(0,3)]["name"]}项目',
             random.choice(['开发任务','测试任务','生产任务','物料准备']),
             rand_date(-20,-10), rand_date(-10,20), random.randint(50,100),
             random.choice(['已分配','使用中','已释放']), 'product',
             '项目资源调度', rand_dt(-30,-25)))
        resource_ids.append(c.lastrowid)
    print(f"  ✓ 资源调度: {len(resource_ids)}条")

    # 24. 设备台账
    device_ids = []
    for pid in product_ids[:3]:
        for i in range(random.randint(3, 5)):
            c.execute('''INSERT INTO devices (device_sn, product_id, customer, firmware_version, status, activate_date, last_online, location, remark, created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?)''',
                (f'DEV{pid:02d}{random.randint(10000,99999)}', pid, random.choice(CUSTOMERS),
                 f'V{random.randint(1,3)}.{random.randint(0,9)}.{random.randint(0,9)}',
                 random.choice(['在线','离线','故障','维护中']), rand_date(-90,-30),
                 rand_dt(-2,0), random.choice(['华东区','华南区','华北区','西南区']),
                 '客户现场设备', rand_dt(-90,-80)))
            device_ids.append(c.lastrowid)
    print(f"  ✓ 设备台账: {len(device_ids)}台")

    # 25. 设备告警
    alert_ids = []
    for did in device_ids[:8]:
        if random.random() > 0.4:
            c.execute('''INSERT INTO device_alerts (device_id, alert_type, level, content, status, handler, solution, created_at, resolved_at)
                VALUES (?,?,?,?,?,?,?,?,?)''',
                (did, random.choice(['离线告警','异常重启','信号弱','高温告警','电量低']),
                 random.choice(['紧急','重要','一般','提示']),
                 f'设备{random.choice(["离线超过24小时","异常重启3次","信号强度低于-100dBm"])}',
                 random.choice(['未处理','处理中','已解决']),
                 'delivery' if random.random()>0.5 else None,
                 '远程重启恢复' if random.random()>0.5 else None,
                 rand_dt(-10,-1), rand_dt(-5,0) if random.random()>0.5 else None))
            alert_ids.append(c.lastrowid)
    print(f"  ✓ 设备告警: {len(alert_ids)}条")

    # 26. OTA版本
    ota_ids = []
    for pid in product_ids[:3]:
        for i in range(2):
            c.execute('''INSERT INTO ota_versions (version_no, product_id, file_name, file_size, md5, release_notes, status, rollout_strategy, rollout_percentage, creator, created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)''',
                (f'OTA-V{random.randint(1,3)}.{random.randint(0,9)}.{random.randint(0,9)}', pid,
                 f'firmware_{pid}.bin', random.randint(500000, 5000000),
                 f'{random.randint(10000000,99999999):08x}',
                 f'1. 修复已知Bug\n2. 优化性能\n3. 增加{random.choice(FEATURES)}',
                 random.choice(['测试中','灰度发布','全量发布','已归档']),
                 random.choice(['全量','灰度','按地区','按客户']),
                 random.randint(10, 100), 'delivery', rand_dt(-30,-20)))
            oid = c.lastrowid
            ota_ids.append(oid)
            # OTA升级记录
            for did in random.sample(device_ids[:5], min(3, 5)):
                c.execute('''INSERT INTO ota_upgrades (ota_version_id, device_id, status, start_time, end_time, result, error_msg, created_at)
                    VALUES (?,?,?,?,?,?,?,?)''',
                    (oid, did, random.choice(['待升级','升级中','已成功','已失败']),
                     rand_dt(-15,-10), rand_dt(-10,-5) if random.random()>0.3 else None,
                     '升级成功' if random.random()>0.3 else '升级失败，设备离线',
                     '无' if random.random()>0.3 else '设备离线', rand_dt(-15,-10)))
    print(f"  ✓ OTA版本: {len(ota_ids)}个")

    # 27. 需求评审记录
    for rid in req_ids[:10]:
        c.execute('''INSERT INTO requirement_reviews (requirement_id, reviewer, review_date, conclusion, opinion, priority_suggested, version_suggested, created_at)
            VALUES (?,?,?,?,?,?,?,?)''',
            (rid, 'product', rand_dt(-30,-10),
             random.choice(['通过','驳回','有条件通过']),
             random.choice(['需求合理，纳入版本规划','需求不明确，需补充','优先级调整为中']),
             random.choice(['高','中','低']),
             random.choice(['V2.0','V2.1','V3.0']),
             rand_dt(-30,-25)))

    # 28. 操作日志
    for i in range(30):
        c.execute('''INSERT INTO operation_logs (module, action, detail, operator, created_at)
            VALUES (?,?,?,?,?)''',
            (random.choice(['需求管理','版本规划','生产管理','质量检测','交付服务','测试管理','技术支持','设备运维']),
             random.choice(['创建','更新','删除','审批','导出','导入']),
             f'{random.choice(["需求","版本","订单","工单","设备"])}#{random.randint(1,100)}',
             random.choice(['admin','product','dev_test','quality','delivery']), rand_dt(-30,0)))

    conn.commit()

    # 统计
    print("\n=== 数据生成完成 ===")
    tables = ['products','requirements','bom_items','inventory','material_requisitions','production_orders',
              'quality_inspections','warehouse_receipts','deliveries','software_versions','dev_tasks',
              'bug_records','production_schedules','test_cases','test_executions','tech_support_tickets',
              'user_feedbacks','delivery_plans','revenue_confirmations','tech_specs','contracts',
              'resource_schedules','devices','device_alerts','ota_versions','operation_logs']
    total = 0
    for t in tables:
        cnt = c.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0]
        total += cnt
        print(f"  {t}: {cnt}条")
    print(f"\n总计: {total}条数据，覆盖{len(tables)}张业务表")
    conn.close()

if __name__ == '__main__':
    random.seed(42)
    generate()
