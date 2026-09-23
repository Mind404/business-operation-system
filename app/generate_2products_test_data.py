# -*- coding: utf-8 -*-
"""
2款产品全场景虚拟数据生成脚本
覆盖所有20张业务表，验证完整业务链路和信息关联
"""
import os
import sqlite3
import random
from datetime import datetime, timedelta

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'business.db')

# 2款产品定义
PRODUCTS = [
    {
        'name': '5G工业通信模组', 'model': 'CM-5G-I01', 'version': 'V2.1.0',
        'description': '面向工业物联网的5G通信模组，支持NSA/SA双模，宽温设计',
        'lifecycle_stage': '量产中', 'stocking_strategy': '不囤货，按需生产'
    },
    {
        'name': '4G Cat.1模组', 'model': 'CM-4G-C01', 'version': 'V1.5.2',
        'description': '低功耗4G Cat.1 bis模组，适用于共享设备、追踪器',
        'lifecycle_stage': '量产中', 'stocking_strategy': '不囤货，按需生产'
    },
]

# 需求模板
REQ_TEMPLATES = [
    ('{dept}批量采购{product}', '{customer}', '需要{qty}套{product}，用于{scene}，交付时间{date}', '高'),
    ('{product}新功能需求', '{customer}', '客户反馈需要增加{feature}功能，提升产品竞争力', '中'),
    ('{product}固件升级需求', '研发部', '修复已知问题，优化性能，需要发布新版本固件', '中'),
    ('{product}认证测试需求', '质量部', '需要{qty}套样品用于{cert}认证测试', '高'),
    ('{product}客户定制需求', '{customer}', '客户要求定制{feature}，预计需求量{qty}套/年', '高'),
]

DEPTS = ['华东市场团队', '华南市场团队', '华北市场团队', '西南市场团队', '产品部', '研发部', '质量部']
CUSTOMERS = ['XX通信有限公司', 'YY科技股份公司', 'ZZ物联网科技', 'WW智能设备公司', 'VV车联网科技', 'UU电子有限公司']
SCENES = ['智慧工厂改造项目', '智能电表批量部署', '共享单车运营', '车载前装项目', '智能家居生态', '农业物联网监测']
FEATURES = ['MQTT协议支持', 'OTA远程升级', 'GPS定位功能', '蓝牙配网', '多APN切换', '低功耗模式', '工业级宽温', '双SIM卡']
CERTS = ['CE认证', 'FCC认证', 'SRRC认证', 'CCC认证', '车规AEC-Q100']

# 物料模板
MATERIALS = [
    ('主芯片', 'IC-MAIN-001', 1, '个', 1.0, '高通/联发科'),
    ('射频前端模块', 'IC-RF-001', 1, '个', 1.0, 'Skyworks/Qorvo'),
    ('电源管理IC', 'IC-PMIC-001', 1, '个', 1.0, 'TI/ADI'),
    ('Flash存储器', 'IC-FLASH-001', 0, '个', 1.0, '旺宏/华邦'),
    ('晶振', 'XTAL-001', 0, '个', 2.0, '爱普生/泰艺'),
    ('射频连接器', 'CON-RF-001', 0, '个', 1.0, '村田/安费诺'),
    ('PCB板', 'PCB-001', 0, '片', 1.0, '深南电路/沪电股份'),
    ('屏蔽罩', 'SHIELD-001', 0, '个', 1.0, '国产'),
]

def rand_date(start_days=-60, end_days=30):
    return (datetime.now() + timedelta(days=random.randint(start_days, end_days))).strftime('%Y-%m-%d')

def rand_datetime(start_days=-60, end_days=0):
    return (datetime.now() + timedelta(days=random.randint(start_days, end_days),
                                        hours=random.randint(0,23), minutes=random.randint(0,59))).strftime('%Y-%m-%d %H:%M:%S')

def generate():
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys = ON')
    c = conn.cursor()

    # 执行schema
    with open(os.path.join(os.path.dirname(__file__), 'schema.sql'), 'r', encoding='utf-8') as f:
        conn.executescript(f.read())

    print("=" * 60)
    print("  2款产品全场景虚拟数据生成")
    print("=" * 60)

    # ===== 1. 产品 =====
    product_ids = []
    for p in PRODUCTS:
        c.execute('''INSERT INTO products (name, model, version, description, lifecycle_stage, stocking_strategy)
                     VALUES (?,?,?,?,?,?)''',
                  (p['name'], p['model'], p['version'], p['description'], p['lifecycle_stage'], p['stocking_strategy']))
        product_ids.append(c.lastrowid)
    print(f"[1/20] 产品: {len(product_ids)}套")

    # ===== 2. 需求 =====
    req_ids = []
    for i, pid in enumerate(product_ids):
        p = PRODUCTS[i]
        num_reqs = random.randint(4, 7)
        for j in range(num_reqs):
            tpl = random.choice(REQ_TEMPLATES)
            dept = random.choice(DEPTS)
            customer = random.choice(CUSTOMERS)
            qty = random.choice([100, 500, 1000, 2000, 5000, 10000])
            scene = random.choice(SCENES)
            feature = random.choice(FEATURES)
            cert = random.choice(CERTS)
            date = rand_date(10, 90)
            title = tpl[0].format(dept=dept, product=p['name'], feature=feature, cert=cert)
            desc = tpl[2].format(product=p['name'], qty=qty, scene=scene, feature=feature, cert=cert, date=date)
            status = random.choices(['待评审', '已评审', '进行中', '已完成', '已取消'],
                                     weights=[1, 2, 3, 3, 1])[0]
            priority = tpl[3] if random.random() > 0.3 else random.choice(['高', '中', '低'])
            c.execute('''INSERT INTO requirements (title, source_dept, customer, description, quantity, priority, status, delivery_date, created_at)
                         VALUES (?,?,?,?,?,?,?,?,?)''',
                      (title, dept, customer, desc, qty, priority, status, date, rand_datetime(-50, -5)))
            req_ids.append(c.lastrowid)
    print(f"[2/20] 需求: {len(req_ids)}条")

    # ===== 3. BOM物料清单 =====
    bom_ids = []
    for i, pid in enumerate(product_ids):
        num_materials = random.randint(5, 8)
        selected = random.sample(MATERIALS, num_materials)
        for m in selected:
            c.execute('''INSERT INTO bom_items (product_id, material_name, material_code, is_key_material, unit, quantity_per_unit, supplier)
                         VALUES (?,?,?,?,?,?,?)''',
                      (pid, m[0], m[1], m[2], m[3], m[4], m[5]))
            bom_ids.append(c.lastrowid)
    print(f"[3/20] BOM物料: {len(bom_ids)}条")

    # ===== 4. 库存 =====
    inv_ids = []
    for m in MATERIALS:
        for loc in ['公司库房', '供应商暂存']:
            qty = random.choice([0, 50, 100, 200, 500, 1000, 2000, 5000])
            status = '正常' if qty > 0 else '缺货'
            c.execute('''INSERT INTO inventory (material_name, material_code, is_key_material, location, quantity, unit, status, remark)
                         VALUES (?,?,?,?,?,?,?,?)''',
                      (m[0], m[1], m[2], loc, qty, m[3], status, f'{loc}库存'))
            inv_ids.append(c.lastrowid)
    print(f"[4/20] 库存: {len(inv_ids)}条")

    # ===== 5. 物料领用单 =====
    req_ids_by_product = [req_ids[i*6:(i+1)*6+1] for i in range(5)]
    requisition_ids = []
    for i, pid in enumerate(product_ids):
        num_req = random.randint(2, 4)
        for j in range(num_req):
            req_id = random.choice(req_ids_by_product[i]) if req_ids_by_product[i] else None
            total_qty = random.choice([100, 500, 1000, 2000])
            status = random.choices(['待审批', '已审批', '已领用', '已取消'], weights=[1, 2, 5, 1])[0]
            approved_at = rand_datetime(-30, -1) if status != '待审批' else None
            req_no = f"MR{datetime.now().strftime('%Y%m%d')}{len(requisition_ids)+1:03d}"
            c.execute('''INSERT INTO material_requisitions (requisition_no, product_id, requirement_id, applicant, status, total_quantity, approved_at, created_at)
                         VALUES (?,?,?,?,?,?,?,?)''',
                      (req_no, pid, req_id, random.choice(['张三', '李四', '王五']), status, total_qty, approved_at, rand_datetime(-40, -5)))
            requisition_ids.append(c.lastrowid)
    print(f"[5/20] 物料领用单: {len(requisition_ids)}张")

    # ===== 6. 领用单明细 =====
    req_item_count = 0
    for rid in requisition_ids:
        num_items = random.randint(2, 4)
        selected_mats = random.sample(MATERIALS, num_items)
        for m in selected_mats:
            qty = random.choice([50, 100, 200, 500, 1000])
            c.execute('''INSERT INTO requisition_items (requisition_id, material_name, quantity, unit)
                         VALUES (?,?,?,?)''', (rid, m[0], qty, m[3]))
            req_item_count += 1
    print(f"[6/20] 领用明细: {req_item_count}条")

    # ===== 7. 生产订单 =====
    po_ids = []
    for i, pid in enumerate(product_ids):
        num_po = random.randint(2, 4)
        for j in range(num_po):
            req_id = random.choice(req_ids_by_product[i]) if req_ids_by_product[i] else None
            qty = random.choice([100, 500, 1000, 2000, 5000])
            status = random.choices(['待排产', '生产中', '已完成', '已取消'], weights=[1, 3, 5, 1])[0]
            progress = 0 if status == '待排产' else (random.randint(10, 90) if status == '生产中' else 100)
            plan_start = rand_date(-30, 5)
            plan_finish = rand_date(5, 30)
            actual_finish = rand_datetime(-20, -1) if status == '已完成' else None
            po_no = f"PO{datetime.now().strftime('%Y%m%d')}{len(po_ids)+1:03d}"
            c.execute('''INSERT INTO production_orders (order_no, product_id, requirement_id, supplier, quantity, plan_start, plan_finish, actual_finish, status, wip_progress, remark, created_at)
                         VALUES (?,?,?,?,?,?,?,?,?,?,?,?)''',
                      (po_no, pid, req_id, random.choice(['深圳华创电子', '东莞精创科技', '苏州信达电子']),
                       qty, plan_start, plan_finish, actual_finish, status, progress, '委外生产', rand_datetime(-35, -5)))
            po_ids.append(c.lastrowid)
    print(f"[7/20] 生产订单: {len(po_ids)}张")

    # ===== 8. 质量检验 =====
    qi_ids = []
    for po_id in po_ids:
        po = c.execute('SELECT product_id, quantity, status FROM production_orders WHERE id=?', (po_id,)).fetchone()
        if po[2] in ['生产中', '已完成']:
            num_qi = random.randint(1, 2)
            for k in range(num_qi):
                itype = random.choice(['IQC', 'IPQC', 'OQC'])
                sample = random.choice([20, 50, 100])
                fail = random.choice([0, 0, 0, 1, 2, 5])
                passed = sample - fail
                rate = round(passed / sample * 100, 2)
                result = '合格' if fail == 0 else ('让步接收' if fail <= 2 else '不合格')
                c.execute('''INSERT INTO quality_inspections (inspection_no, inspection_type, production_order_id, product_id, inspector, sample_quantity, pass_quantity, fail_quantity, pass_rate, result, defect_description, inspection_date, created_at)
                             VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                          (f"QI{datetime.now().strftime('%Y%m%d')}{len(qi_ids)+1:03d}",
                           itype, po_id, po[0], random.choice(['质检A', '质检B', '质检C']),
                           sample, passed, fail, rate, result,
                           '外观不良' if fail > 0 else '', rand_date(-20, -1), rand_datetime(-20, -1)))
                qi_ids.append(c.lastrowid)
    print(f"[8/20] 质量检验: {len(qi_ids)}条")

    # ===== 9. 不合格品处理 =====
    dh_count = 0
    for qi_id in qi_ids:
        qi = c.execute('SELECT result, fail_quantity FROM quality_inspections WHERE id=?', (qi_id,)).fetchone()
        if qi[0] in ['让步接收', '不合格']:
            c.execute('''INSERT INTO defect_handling (inspection_id, defect_type, root_cause, handling_method, responsible_person, status, closed_at, created_at)
                         VALUES (?,?,?,?,?,?,?,?)''',
                      (qi_id, random.choice(['外观不良', '焊接不良', '功能异常', '尺寸超差']),
                       random.choice(['供应商来料问题', '生产工艺问题', '设计缺陷']),
                       random.choice(['返工', '返修', '让步接收', '报废']),
                       random.choice(['生产主管', '质量工程师']),
                       random.choice(['待处理', '处理中', '已关闭']),
                       rand_datetime(-10, -1) if random.random() > 0.3 else None,
                       rand_datetime(-15, -2)))
            dh_count += 1
    print(f"[9/20] 不合格品处理: {dh_count}条")

    # ===== 10. 入库记录 =====
    wr_ids = []
    for po_id in po_ids:
        po = c.execute('SELECT product_id, quantity, status FROM production_orders WHERE id=?', (po_id,)).fetchone()
        if po[2] == '已完成':
            status = random.choices(['待审批', '已入库'], weights=[1, 4])[0]
            c.execute('''INSERT INTO warehouse_receipts (receipt_no, production_order_id, product_id, quantity, quality_status, approver, status, approved_at, created_at)
                         VALUES (?,?,?,?,?,?,?,?,?)''',
                      (f"WR{datetime.now().strftime('%Y%m%d')}{len(wr_ids)+1:03d}",
                       po_id, po[0], po[1], '合格', random.choice(['仓库主管', '质量经理']),
                       status, rand_datetime(-15, -1) if status == '已入库' else None, rand_datetime(-18, -2)))
            wr_ids.append(c.lastrowid)
    print(f"[10/20] 入库记录: {len(wr_ids)}条")

    # ===== 11. 交付记录 =====
    dl_count = 0
    for i, pid in enumerate(product_ids):
        num_dl = random.randint(2, 5)
        for j in range(num_dl):
            req_id = random.choice(req_ids_by_product[i]) if req_ids_by_product[i] else None
            req = c.execute('SELECT customer FROM requirements WHERE id=?', (req_id,)).fetchone() if req_id else None
            c.execute('''INSERT INTO deliveries (delivery_no, product_id, market_team, quantity, customer, purpose, delivery_date, status, feedback, created_at)
                         VALUES (?,?,?,?,?,?,?,?,?,?)''',
                      (f"DL{datetime.now().strftime('%Y%m%d')}{dl_count+1:03d}",
                       pid, random.choice(DEPTS[:4]), random.choice([50, 100, 500, 1000, 2000]),
                       req[0] if req else random.choice(CUSTOMERS),
                       random.choice(SCENES), rand_date(-25, -1), '已交付',
                       random.choice(['客户反馈良好', '运行稳定', '待客户反馈', '']), rand_datetime(-25, -2)))
            dl_count += 1
    print(f"[11/20] 交付记录: {dl_count}条")

    # ===== 12. 软件版本 =====
    sv_ids = []
    for i, pid in enumerate(product_ids):
        p = PRODUCTS[i]
        num_versions = random.randint(2, 4)
        for v in range(num_versions):
            major = v + 1
            minor = random.randint(0, 3)
            patch = random.randint(0, 5)
            version_no = f"V{major}.{minor}.{patch}"
            status = random.choices(['规划中', '开发中', '测试中', '已发布', '已取消'],
                                     weights=[2, 3, 2, 3, 1])[0]
            planned = rand_date(10, 60) if status in ['规划中', '开发中', '测试中'] else rand_date(-60, -10)
            actual = rand_datetime(-30, -1) if status == '已发布' else None
            capabilities = '\n'.join(random.sample(FEATURES, random.randint(2, 4)))
            c.execute('''INSERT INTO software_versions (product_id, version_no, version_name, description, capabilities, planned_release_date, actual_release_date, status, created_at)
                         VALUES (?,?,?,?,?,?,?,?,?)''',
                      (pid, version_no, f"{p['name']} {version_no}",
                       f"{p['name']}固件版本{version_no}，包含功能优化和Bug修复",
                       capabilities, planned, actual, status, rand_datetime(-50, -5)))
            sv_ids.append(c.lastrowid)
    print(f"[12/20] 软件版本: {len(sv_ids)}个")

    # ===== 13. 版本需求关联 =====
    vr_count = 0
    for i, pid in enumerate(product_ids):
        product_versions = [sv for sv, p in zip(sv_ids, [s for s in sv_ids]) if True]  # simplified
        # 简化：每个版本关联1-3个需求
        for sv_id in sv_ids:
            sv = c.execute('SELECT product_id FROM software_versions WHERE id=?', (sv_id,)).fetchone()
            if sv[0] == pid:
                num_links = random.randint(1, 3)
                linked = random.sample(req_ids_by_product[i], min(num_links, len(req_ids_by_product[i])))
                for rid in linked:
                    c.execute('''INSERT INTO version_requirements (version_id, requirement_id, priority, created_at)
                                 VALUES (?,?,?,?)''', (sv_id, rid, random.choice(['高', '中', '低']), rand_datetime(-40, -5)))
                    vr_count += 1
    print(f"[13/20] 版本需求关联: {vr_count}条")

    # ===== 14. 开发任务 =====
    dt_count = 0
    for sv_id in sv_ids:
        sv = c.execute('SELECT status FROM software_versions WHERE id=?', (sv_id,)).fetchone()
        if sv[0] in ['开发中', '测试中', '已发布']:
            num_tasks = random.randint(3, 6)
            for t in range(num_tasks):
                feature = random.choice(FEATURES)
                status = random.choices(['待开发', '开发中', '已完成', '已取消'], weights=[1, 3, 5, 1])[0]
                c.execute('''INSERT INTO dev_tasks (version_id, task_name, description, priority, status, assignee, planned_start, planned_finish, actual_finish, created_at)
                             VALUES (?,?,?,?,?,?,?,?,?,?)''',
                          (sv_id, f"实现{feature}功能", f"开发{feature}相关功能，包括驱动、协议栈、应用层接口",
                           random.choice(['高', '中', '低']), status,
                           random.choice(['开发A', '开发B', '开发C', '开发D']),
                           rand_date(-30, 5), rand_date(5, 30),
                           rand_datetime(-15, -1) if status == '已完成' else None, rand_datetime(-35, -5)))
                dt_count += 1
    print(f"[14/20] 开发任务: {dt_count}条")

    # ===== 15. Bug记录 =====
    bug_ids = []
    for i, pid in enumerate(product_ids):
        product_versions = [sv for sv in sv_ids if c.execute('SELECT product_id FROM software_versions WHERE id=?', (sv,)).fetchone()[0] == pid]
        num_bugs = random.randint(3, 6)
        for b in range(num_bugs):
            severity = random.choices(['致命', '严重', '一般', '轻微', '建议'], weights=[1, 2, 4, 2, 1])[0]
            status = random.choices(['待处理', '处理中', '待验证', '已关闭', '已取消'], weights=[1, 2, 2, 4, 1])[0]
            is_quality = 1 if (severity in ['致命', '严重'] and random.random() > 0.5) else 0
            sv_id = random.choice(product_versions) if product_versions else None
            title = random.choice([
                '模组上电后偶发无法注册网络', 'OTA升级后配置丢失', '低功耗模式下唤醒异常',
                'MQTT连接频繁断开重连', '蓝牙配网成功率低', '高温环境下通信不稳定',
                'GPS定位漂移严重', '固件版本号显示错误', 'AT指令响应超时', 'SIM卡识别失败'
            ])
            c.execute('''INSERT INTO bug_records (bug_no, product_id, version_id, title, description, severity, status, reporter, assignee, discovered_at, closed_at, root_cause, corrective_action, prevention_measure, is_quality_issue, created_at)
                         VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                      (f"BUG{datetime.now().strftime('%Y%m%d')}{len(bug_ids)+1:04d}",
                       pid, sv_id, title,
                       f"复现步骤：1.xxx 2.xxx 3.xxx\n预期：正常\n实际：{title}",
                       severity, status, random.choice(['测试A', '测试B', '客户反馈']),
                       random.choice(['开发A', '开发B', '开发C']),
                       rand_date(-25, -5),
                       rand_datetime(-10, -1) if status == '已关闭' else None,
                       random.choice(['驱动程序内存泄漏', '协议栈状态机异常', '硬件设计缺陷', '供应商来料问题']) if status == '已关闭' else None,
                       '修复相关代码，增加异常处理' if status == '已关闭' else None,
                       '增加自动化测试用例，代码评审加强' if status == '已关闭' else None,
                       is_quality, rand_datetime(-25, -5)))
            bug_ids.append(c.lastrowid)
    print(f"[15/20] Bug记录: {len(bug_ids)}条")

    # ===== 16. Bug流转记录 =====
    bw_count = 0
    for bug_id in bug_ids:
        bug = c.execute('SELECT status FROM bug_records WHERE id=?', (bug_id,)).fetchone()
        # 创建记录
        c.execute('''INSERT INTO bug_workflow (bug_id, action, from_status, to_status, operator, comment, created_at)
                     VALUES (?,?,?,?,?,?,?)''',
                  (bug_id, '创建', None, '待处理', '测试A', 'Bug登记', rand_datetime(-25, -20)))
        bw_count += 1
        if bug[0] in ['处理中', '待验证', '已关闭']:
            c.execute('''INSERT INTO bug_workflow (bug_id, action, from_status, to_status, operator, comment, created_at)
                         VALUES (?,?,?,?,?,?,?)''',
                      (bug_id, '开始处理', '待处理', '处理中', '开发A', '开始分析问题', rand_datetime(-20, -15)))
            bw_count += 1
        if bug[0] in ['待验证', '已关闭']:
            c.execute('''INSERT INTO bug_workflow (bug_id, action, from_status, to_status, operator, comment, created_at)
                         VALUES (?,?,?,?,?,?,?)''',
                      (bug_id, '提交验证', '处理中', '待验证', '开发A', '修复完成，提交测试验证', rand_datetime(-15, -10)))
            bw_count += 1
        if bug[0] == '已关闭':
            c.execute('''INSERT INTO bug_workflow (bug_id, action, from_status, to_status, operator, comment, created_at)
                         VALUES (?,?,?,?,?,?,?)''',
                      (bug_id, '验证通过关闭', '待验证', '已关闭', '测试A', '验证通过，Bug关闭', rand_datetime(-10, -5)))
            bw_count += 1
    print(f"[16/20] Bug流转记录: {bw_count}条")

    # ===== 17. 生产排程 =====
    ps_ids = []
    for i, pid in enumerate(product_ids):
        num_schedules = random.randint(1, 3)
        for s in range(num_schedules):
            req_id = random.choice(req_ids_by_product[i]) if req_ids_by_product[i] else None
            qty = random.choice([500, 1000, 2000, 5000])
            status = random.choices(['规划中', '已确认', '生产中', '已完成'], weights=[2, 2, 3, 3])[0]
            plan_start = rand_date(-10, 10)
            plan_finish = rand_date(10, 40)
            c.execute('''INSERT INTO production_schedules (schedule_no, product_id, requirement_id, quantity, plan_start, plan_finish, status, remark, created_at)
                         VALUES (?,?,?,?,?,?,?,?,?)''',
                      (f"PS{datetime.now().strftime('%Y%m%d')}{len(ps_ids)+1:03d}",
                       pid, req_id, qty, plan_start, plan_finish, status,
                       '根据客户需求排产', rand_datetime(-20, -5)))
            ps_ids.append(c.lastrowid)
    print(f"[17/20] 生产排程: {len(ps_ids)}张")

    # ===== 18. 生产工序 =====
    pp_count = 0
    processes = ['SMT贴片', '回流焊接', 'AOI检测', '功能测试', '外观检验', '包装入库']
    for ps_id in ps_ids:
        ps = c.execute('SELECT status, plan_start, plan_finish FROM production_schedules WHERE id=?', (ps_id,)).fetchone()
        for idx, proc in enumerate(processes):
            seq = idx + 1
            if ps[0] == '规划中':
                pstatus = '待开始'
            elif ps[0] == '已确认':
                pstatus = '待开始'
            elif ps[0] == '生产中':
                pstatus = random.choices(['待开始', '进行中', '已完成'], weights=[2, 2, seq])[0] if seq <= 4 else '待开始'
            else:
                pstatus = '已完成'
            actual = rand_datetime(-10, -1) if pstatus == '已完成' else None
            c.execute('''INSERT INTO production_processes (schedule_id, process_name, sequence, planned_start, planned_finish, actual_finish, status, responsible, created_at)
                         VALUES (?,?,?,?,?,?,?,?,?)''',
                      (ps_id, proc, seq, ps[1], ps[2], actual, pstatus,
                       random.choice(['产线A', '产线B', '测试组']), rand_datetime(-18, -5)))
            pp_count += 1
    print(f"[18/20] 生产工序: {pp_count}道")

    # ===== 19. 物料需求计划 =====
    mp_count = 0
    for ps_id in ps_ids:
        ps = c.execute('SELECT product_id, quantity FROM production_schedules WHERE id=?', (ps_id,)).fetchone()
        boms = c.execute('SELECT * FROM bom_items WHERE product_id=?', (ps[0],)).fetchall()
        for bom in boms:
            needed = int(bom['quantity_per_unit'] * ps[1])
            available = c.execute('SELECT COALESCE(SUM(quantity),0) FROM inventory WHERE material_code=?',
                                   (bom['material_code'],)).fetchone()[0]
            mstatus = '已确认' if available >= needed else '待采购'
            c.execute('''INSERT INTO material_plans (schedule_id, bom_item_id, material_name, material_code, is_key_material, quantity_needed, quantity_available, unit, status, created_at)
                         VALUES (?,?,?,?,?,?,?,?,?,?)''',
                      (ps_id, bom['id'], bom['material_name'], bom['material_code'],
                       bom['is_key_material'], needed, available, bom['unit'], mstatus, rand_datetime(-18, -5)))
            mp_count += 1
    print(f"[19/20] 物料需求计划: {mp_count}项")

    # ===== 20. 操作日志 =====
    log_actions = [
        ('需求管理', '新增', '新增客户需求'),
        ('需求管理', '状态更新', '需求状态更新'),
        ('产品规划', '新增', '新增产品定义'),
        ('研发生产', '入库', '物料入库'),
        ('研发生产', '领用', '物料领用审批'),
        ('研发生产', '排产', '生产订单创建'),
        ('质量检测', '检验', '质量检验完成'),
        ('质量检测', '处理', '不合格品处理'),
        ('验证收益', '入库', '产品入库审批'),
        ('验证收益', '交付', '产品交付客户'),
        ('版本规划', '新增', '新增软件版本'),
        ('版本规划', '任务', '新增开发任务'),
        ('Bug管理', '新增', 'Bug登记'),
        ('Bug管理', '流转', 'Bug状态流转'),
        ('生产排程', '新增', '新增生产排程'),
        ('数据管理', '导出', '数据导出'),
    ]
    for _ in range(80):
        action = random.choice(log_actions)
        c.execute('''INSERT INTO operation_logs (module, action, detail, operator, created_at)
                     VALUES (?,?,?,?,?)''',
                  (action[0], action[1], action[2], random.choice(['系统', '管理员', '张三', '李四']),
                   rand_datetime(-50, 0)))
    print(f"[20/20] 操作日志: 80条")

    conn.commit()

    # 统计
    print("\n" + "=" * 60)
    print("  数据生成完成！统计如下：")
    print("=" * 60)
    tables = ['requirements', 'products', 'bom_items', 'inventory', 'material_requisitions',
              'requisition_items', 'production_orders', 'quality_inspections', 'defect_handling',
              'warehouse_receipts', 'deliveries', 'software_versions', 'version_requirements',
              'dev_tasks', 'bug_records', 'bug_workflow', 'production_schedules',
              'production_processes', 'material_plans', 'operation_logs']
    total = 0
    for t in tables:
        cnt = c.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0]
        total += cnt
        print(f"  {t:30s}: {cnt:5d} 条")
    print(f"  {'合计':30s}: {total:5d} 条")
    print(f"\n数据库文件: {DB_PATH}")
    print(f"文件大小: {os.path.getsize(DB_PATH) / 1024:.1f} KB")
    conn.close()

if __name__ == '__main__':
    generate()
