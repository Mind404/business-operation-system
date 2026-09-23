# -*- coding: utf-8 -*-
"""
2款产品全场景虚拟数据补充脚本（修正版）
使用正确的表结构字段名，覆盖全部18张空表
"""
import os
import sqlite3
import random
from datetime import datetime, timedelta

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'business.db')

def rand_date(s=-60, e=60):
    return (datetime.now() + timedelta(days=random.randint(s, e))).strftime('%Y-%m-%d')

def rand_dt(s=-60, e=0):
    return (datetime.now() + timedelta(days=random.randint(s, e),
            hours=random.randint(0,23), minutes=random.randint(0,59))).strftime('%Y-%m-%d %H:%M:%S')

def generate_supplement():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()

    print("=" * 60)
    print("  补充缺失业务表数据（修正版）")
    print("=" * 60)

    # 获取已有数据
    products = c.execute("SELECT id, name, model FROM products").fetchall()
    requirements = c.execute("SELECT id, title, customer, quantity FROM requirements").fetchall()
    versions = c.execute("SELECT id, version_no, product_id FROM software_versions").fetchall()
    deliveries = c.execute("SELECT id, product_id, quantity, customer FROM deliveries").fetchall()
    bugs = c.execute("SELECT id, title FROM bug_records").fetchall()

    product_ids = [p['id'] for p in products]
    req_ids = [r['id'] for r in requirements]
    version_ids = [v['id'] for v in versions]
    bug_ids = [b['id'] for b in bugs]

    # ===== 1. 更新需求表的req_type字段（确保每类都有数据） =====
    # 按顺序分配，确保每类都有数据
    num_reqs = len(requirements)
    req_type_list = []
    # 至少每类1条，剩余的随机分配
    base_types = ['用户需求', '集团系统侧需求', '集团应用侧需求', '领导任务课题']
    req_type_list.extend(base_types)
    remaining = num_reqs - 4
    extra_types = ['用户需求']*2 + ['集团系统侧需求'] + ['领导任务课题']*2
    req_type_list.extend(random.sample(extra_types, min(remaining, len(extra_types))))
    random.shuffle(req_type_list)
    for i, req in enumerate(requirements):
        req_type = req_type_list[i % len(req_type_list)]
        updates = {'req_type': req_type}
        if req_type == '用户需求':
            updates['product_model'] = random.choice(products)['model']
            updates['batch_delivery'] = random.choice(['是', '否'])
            updates['application_scenario'] = random.choice(['智慧工厂改造', '智能电表部署', '车载前装', '智能家居', '农业物联网'])
        elif req_type == '集团系统侧需求':
            updates['system_verify_point'] = f"{random.choice(['Q2','Q3','Q4'])}系统联调验证"
            updates['verify_function'] = random.choice(['MQTT协议兼容性', 'OTA升级流程', '数据上报稳定性', '网络注册性能'])
            updates['performance_req'] = random.choice(['吞吐量>=100Mbps', '时延<50ms', '并发连接>=1000', '功耗<5uA'])
        elif req_type == '集团应用侧需求':
            updates['certification_type'] = random.choice(['CCC认证', 'SRRC认证', '入网许可证', 'CE认证', 'FCC认证'])
            updates['spec_requirement'] = random.choice(['工业级宽温-40~85℃', '防护等级IP67', '电磁兼容Class B', '车规AEC-Q100'])
            updates['industry_standard'] = random.choice(['3GPP R15', 'IEEE 802.11ax', '蓝牙5.2规范', 'MQTT 3.1.1'])
        elif req_type == '领导任务课题':
            updates['task_progress'] = random.choice(['需求调研完成', '方案设计中', '原型开发中', '测试验证中', '已结题'])
            updates['time_plan'] = f"{rand_date(-30,0)} ~ {rand_date(30,90)}"
            updates['deliverable'] = random.choice(['研究报告', '原型系统', '技术方案', '测试报告', '专利申请'])
        set_clause = ', '.join([f"{k}=?" for k in updates.keys()])
        c.execute(f"UPDATE requirements SET {set_clause} WHERE id=?", list(updates.values()) + [req['id']])
    print(f"[1/18] 需求类型字段更新: {len(requirements)}条")

    # ===== 2. 需求评审记录 =====
    for req in requirements[:8]:
        c.execute('''INSERT INTO requirement_reviews (requirement_id, reviewer, review_date, conclusion, opinion, priority_suggested, version_suggested, created_at)
            VALUES (?,?,?,?,?,?,?,?)''',
            (req['id'], random.choice(['张工', '李工', '王总', '赵经理', '产品总监']),
             rand_date(-40, -10),
             random.choice(['通过', '有条件通过', '需修改', '通过']),
             random.choice(['需求明确，同意立项', '需补充技术可行性分析', '优先级调整为中', '同意，纳入下版本规划', '建议拆分需求']),
             random.choice(['高', '中', '低']),
             random.choice(['V2.2.0', 'V2.3.0', 'V1.6.0', 'V1.7.0']),
             rand_dt(-40, -10)))
    print(f"[2/18] 需求评审记录: 8条")

    # ===== 3. 技术规格 =====
    for p in products:
        for i in range(2):
            c.execute('''INSERT INTO tech_specs (product_id, version_id, spec_name, spec_type, content, parameters, interfaces, performance, status, author, created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)''',
                (p['id'], random.choice(version_ids) if version_ids else None,
                 f"{p['name']}-{random.choice(['射频指标','功耗指标','接口指标','环境指标','协议指标'])}-{i+1}",
                 random.choice(['射频', '功耗', '接口', '环境', '协议', '机械']),
                 f"{p['name']}的技术规格说明书，详细定义产品的技术参数和验收标准",
                 random.choice(['工作频段: Sub-6GHz', '发射功率: 23dBm', '接收灵敏度: -95dBm', '工作电压: 3.3V', '工作温度: -40~85℃']),
                 random.choice(['UART, USB, SPI, I2C', 'UART, USB, GPIO', 'UART, SPI, I2C', 'USB, UART, SDIO']),
                 random.choice(['吞吐量: 100Mbps', '时延: <50ms', '功耗: <5uA', '连接数: >=1000']),
                 random.choice(['已发布', '草稿', '评审中']),
                 random.choice(['架构师A', '架构师B', '资深工程师']),
                 rand_dt(-50, -20)))
    print(f"[3/18] 技术规格: 4条")

    # ===== 4. 测试用例库 =====
    test_case_ids = []
    for p in products:
        for i in range(8):
            c.execute('''INSERT INTO test_cases (case_no, product_id, version_id, title, module, precondition, test_steps, expected_result, priority, case_type, status, creator, created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                (f"TC-{p['model']}-{i+1:03d}",
                 p['id'], random.choice(version_ids) if version_ids else None,
                 f"{p['name']}-{random.choice(['开机启动','网络注册','数据上报','OTA升级','低功耗','异常恢复','接口通信','稳定性'])}-{i+1}",
                 random.choice(['网络模块', '电源管理', '接口通信', 'OTA升级', '系统稳定性', '射频性能']),
                 random.choice(['设备正常供电', 'SIM卡正常插入', '网络信号良好', '固件版本正确', '测试环境就绪']),
                 '1. 准备测试环境\n2. 执行测试步骤\n3. 记录测试结果\n4. 对比预期结果',
                 random.choice(['设备正常启动并注册网络', '数据上报成功率>=99%', 'OTA升级成功且功能正常', '功耗符合规格要求', '异常恢复后功能正常']),
                 random.choice(['P0', 'P1', 'P2']),
                 random.choice(['功能测试', '性能测试', '稳定性测试', '兼容性测试', '可靠性测试']),
                 random.choice(['已启用', '已启用', '已废弃']),
                 random.choice(['测试工程师A', '测试工程师B', '测试工程师C']),
                 rand_dt(-45, -15)))
            test_case_ids.append(c.lastrowid)
    print(f"[4/18] 测试用例: {len(test_case_ids)}条")

    # ===== 5. 测试执行 =====
    test_exec_ids = []
    for v in versions:
        for i in range(2):
            total = 16
            passed = random.randint(12, 16)
            failed = random.randint(0, 3)
            blocked = total - passed - failed
            c.execute('''INSERT INTO test_executions (execution_no, product_id, version_id, round_no, executor, start_date, end_date, total_cases, passed_cases, failed_cases, blocked_cases, pass_rate, status, summary, created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                (f"TE-{v['version_no']}-R{i+1}",
                 v['product_id'], v['id'], i+1,
                 random.choice(['测试工程师A', '测试工程师B']),
                 rand_date(-20, -10), rand_date(-10, -1),
                 total, passed, failed, blocked,
                 round(passed/total*100, 1),
                 random.choice(['已完成', '进行中', '已完成']),
                 random.choice(['本轮测试整体通过，发现2个低优先级Bug', '测试通过，功能符合预期', '发现1个阻塞性Bug，已修复后复测通过', '性能测试达标，稳定性良好']),
                 rand_dt(-20, -10)))
            test_exec_ids.append(c.lastrowid)
    print(f"[5/18] 测试执行: {len(test_exec_ids)}条")

    # ===== 6. 测试用例结果 =====
    for exec_id in test_exec_ids:
        for tc_id in random.sample(test_case_ids, min(8, len(test_case_ids))):
            result = random.choice(['通过', '通过', '通过', '通过', '失败', '阻塞'])
            c.execute('''INSERT INTO test_case_results (execution_id, case_id, result, actual_result, bug_id, executed_at, executor)
                VALUES (?,?,?,?,?,?,?)''',
                (exec_id, tc_id, result,
                 random.choice(['测试通过，符合预期', '功能正常', '性能达标', '发现异常，已记录Bug', '环境问题，暂时阻塞']),
                 random.choice(bug_ids) if result == '失败' and bug_ids else None,
                 rand_dt(-15, -1),
                 random.choice(['测试工程师A', '测试工程师B'])))
    print(f"[6/18] 测试用例结果: 约80条")

    # ===== 7. 合同管理 =====
    contract_ids = []
    for i in range(6):
        contract_type = random.choice(['生产合同', '物料合同', '用户合同', '用户合同', '服务合同'])
        amount = random.choice([50000, 100000, 200000, 500000, 1000000])
        paid_amount = int(amount * random.uniform(0.3, 1.0))
        c.execute('''INSERT INTO contracts (contract_no, contract_type, supplier, product_id, amount, paid_amount, sign_date, delivery_date, status, owner, progress, payment_status, remark, created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
            (f"HT{datetime.now().strftime('%Y%m%d')}{i+1:03d}",
             contract_type,
             random.choice(['深圳XX电子厂', '东莞YY科技', '苏州ZZ精密', 'XX通信有限公司', 'YY科技股份公司']),
             random.choice(product_ids),
             amount, paid_amount,
             rand_date(-60, -10), rand_date(30, 180),
             random.choice(['执行中', '已完成', '待签订', '执行中']),
             random.choice(['张经理', '李经理', '王经理']),
             random.choice(['已完成', '进行中50%', '进行中80%', '待开始']),
             random.choice(['已结清', '部分付款', '待付款', '已开票']),
             f"{contract_type}，涉及{random.choice(['通信模组生产','关键物料采购','产品销售','技术服务'])}",
             rand_dt(-50, -10)))
        contract_ids.append(c.lastrowid)
    print(f"[7/18] 合同: {len(contract_ids)}条")

    # ===== 8. 合同付款节点 =====
    for cid in contract_ids:
        contract = c.execute("SELECT amount, status FROM contracts WHERE id=?", (cid,)).fetchone()
        num_payments = random.randint(2, 4)
        paid_amount = 0
        for j in range(num_payments):
            # 前两期大概率已付款，后期待付款，模拟真实付款进度
            if j < 2:
                is_paid = random.choice([True, True, True, False])
            else:
                is_paid = random.choice([True, False, False])
            amount = int(contract['amount'] * random.uniform(0.2, 0.4))
            if j == num_payments - 1:
                amount = contract['amount'] - paid_amount
            if is_paid:
                paid_amount += amount
            c.execute('''INSERT INTO contract_payments (contract_id, node_name, plan_date, amount, status, actual_date, remark, created_at)
                VALUES (?,?,?,?,?,?,?,?)''',
                (cid, f"第{j+1}期{random.choice(['预付款','进度款','验收款','质保金'])}",
                 rand_date(-30, 90), amount,
                 '已付款' if is_paid else '待付款',
                 rand_date(-20, 10) if is_paid else None,
                 random.choice(['预付款30%', '进度款40%', '验收款20%', '质保金10%']),
                 rand_dt(-40, -5)))
    print(f"[8/18] 合同付款节点: 约18条")

    # 更新合同表的已付金额和付款状态
    for cid in contract_ids:
        contract = c.execute("SELECT amount FROM contracts WHERE id=?", (cid,)).fetchone()
        paid = c.execute("SELECT SUM(amount) FROM contract_payments WHERE contract_id=? AND status='已付款'", (cid,)).fetchone()[0] or 0
        if paid >= contract['amount']:
            payment_status = '已结清'
        elif paid > 0:
            payment_status = '部分付款'
        else:
            payment_status = '待付款'
        c.execute("UPDATE contracts SET paid_amount=?, payment_status=? WHERE id=?", (paid, payment_status, cid))

    # ===== 9. 交付计划 =====
    for i, req in enumerate(requirements[:6]):
        c.execute('''INSERT INTO delivery_plans (plan_no, customer, product_id, quantity, planned_date, actual_date, status, owner, remark, created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?)''',
            (f"JH{datetime.now().strftime('%Y%m%d')}{i+1:03d}",
             req['customer'], random.choice(product_ids), req['quantity'],
             rand_date(0, 60),
             rand_date(-10, 10) if random.random() > 0.5 else None,
             random.choice(['待交付', '交付中', '已完成', '已完成']),
             random.choice(['华东交付组', '华南交付组', '华北交付组']),
             f"交付给{req['customer']}",
             rand_dt(-30, -5)))
    print(f"[9/18] 交付计划: 6条")

    # ===== 10. 收入确认 =====
    for i, delivery in enumerate(deliveries[:5]):
        amount = delivery['quantity'] * random.randint(50, 200)
        c.execute('''INSERT INTO revenue_confirmations (confirm_no, customer, contract_no, product_id, amount, confirmed_amount, confirm_date, status, period, voucher, handler, remark, created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)''',
            (f"SR{datetime.now().strftime('%Y%m%d')}{i+1:03d}",
             delivery['customer'],
             f"HT{datetime.now().strftime('%Y%m%d')}{random.randint(1,6):03d}",
             delivery['product_id'], amount,
             int(amount * random.uniform(0.5, 1.0)),
             rand_date(-20, 10),
             random.choice(['已确认', '待确认', '已确认']),
             random.choice(['2026Q1', '2026Q2', '2026Q3']),
             f"FP{random.randint(10000,99999)}",
             random.choice(['财务李姐', '财务王哥']),
             f"{delivery['customer']}订单收入确认",
             rand_dt(-20, -1)))
    print(f"[10/18] 收入确认: 5条")

    # ===== 11. 技术支持工单 =====
    ticket_ids = []
    for i in range(8):
        c.execute('''INSERT INTO tech_support_tickets (ticket_no, customer, contact_person, contact_phone, product_id, title, description, category, priority, status, handler, solution, resolved_at, satisfaction, created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
            (f"TS{datetime.now().strftime('%Y%m%d')}{i+1:03d}",
             random.choice(['XX通信有限公司', 'YY科技股份公司', 'ZZ物联网科技', 'WW智能设备公司']),
             random.choice(['张工', '李工', '王工', '赵工']),
             f"138{random.randint(10000000,99999999)}",
             random.choice(product_ids),
             f"{random.choice(products)['name']}-{random.choice(['无法联网','数据异常','固件升级失败','接口通信异常','功耗偏高','稳定性问题'])}-{i+1}",
             f"客户反馈{random.choice(['设备无法正常联网','数据上报异常','OTA升级后无法启动','UART通信丢包','低温环境下偶发重启'])}，需要技术支持",
             random.choice(['网络问题', '固件问题', '硬件问题', '接口问题', '其他']),
             random.choice(['高', '中', '低']),
             random.choice(['待处理', '处理中', '已解决', '已关闭', '处理中']),
             random.choice(['技术支持A', '技术支持B', '技术支持C']),
             random.choice(['已提供固件升级包', '已指导客户配置参数', '已安排现场支持', '问题已定位并修复']),
             rand_dt(-10, 0) if random.random() > 0.3 else None,
             random.randint(3, 5),
             rand_dt(-30, -1)))
        ticket_ids.append(c.lastrowid)
    print(f"[11/18] 技术支持工单: {len(ticket_ids)}条")

    # ===== 12. 工单流转记录 =====
    for tid in ticket_ids:
        num_records = random.randint(2, 4)
        for j in range(num_records):
            c.execute('''INSERT INTO ticket_workflow (ticket_id, action, operator, comment, created_at)
                VALUES (?,?,?,?,?)''',
                (tid, random.choice(['创建工单', '分配处理', '客户沟通', '问题定位', '提供方案', '验证解决', '关闭工单']),
                 random.choice(['技术支持A', '技术支持B', '技术支持C', '研发工程师']),
                 random.choice(['已收到客户反馈，开始处理', '已联系客户确认问题现象', '问题已复现，正在分析原因', '已提供临时解决方案', '问题已解决，客户确认', '工单关闭']),
                 rand_dt(-25, 0)))
    print(f"[12/18] 工单流转记录: 约24条")

    # ===== 13. 用户反馈 =====
    for i in range(10):
        c.execute('''INSERT INTO user_feedbacks (customer, product_id, feedback_type, title, content, source, priority, status, converted_requirement_id, handler, evaluation, created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)''',
            (random.choice(['XX通信有限公司', 'YY科技股份公司', 'ZZ物联网科技', 'WW智能设备公司']),
             random.choice(product_ids),
             random.choice(['功能建议', 'Bug反馈', '使用体验', '性能优化', '其他']),
             random.choice([
                 '建议增加蓝牙配网功能',
                 '低温环境下偶发重启',
                 'OTA升级流程不够友好',
                 '数据上报时延偏高',
                 '整体使用体验不错',
                 '希望支持更多AT指令'
             ]),
             random.choice([
                 '建议增加蓝牙配网功能，提升用户体验',
                 '设备在-20℃环境下偶发重启，希望优化',
                 'OTA升级需要多次确认，希望简化流程',
                 '数据上报时延约200ms，希望优化到100ms以内',
                 '整体使用体验不错，稳定性很好，希望继续保持',
                 '希望支持更多AT指令，方便二次开发'
             ]),
             random.choice(['客户拜访', '电话反馈', '邮件反馈', '技术支持工单', '市场调研']),
             random.choice(['高', '中', '低']),
             random.choice(['待处理', '处理中', '已采纳', '已回复', '已关闭']),
             random.choice(req_ids) if random.random() > 0.5 else None,
             random.choice(['产品经理A', '产品经理B', '研发工程师']),
             random.choice(['已纳入需求池', '已安排下个版本优化', '已回复客户', '暂不采纳，已说明原因']),
             rand_dt(-40, -1)))
    print(f"[13/18] 用户反馈: 10条")

    # ===== 14. 资源排程 =====
    for i in range(8):
        c.execute('''INSERT INTO resource_schedules (resource_type, resource_name, project, task, start_date, end_date, allocation, status, owner, remark, created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)''',
            (random.choice(['设备', '场地', '人员']),
             random.choice(['测试实验室A', '测试实验室B', '频谱仪', '综测仪', '生产流水线1', '生产流水线2']),
             random.choice(['5G模组项目', '4G模组项目', 'NB-IoT项目', 'WiFi6项目']),
             random.choice(['射频测试', '可靠性测试', '功耗测试', '小批量试产', '认证测试']),
             rand_date(-10, 5), rand_date(5, 30),
             random.randint(50, 100),
             random.choice(['已排程', '进行中', '已完成']),
             random.choice(['张工', '李工', '王工']),
             '资源排程，确保项目进度',
             rand_dt(-15, -1)))
    print(f"[14/18] 资源排程: 8条")

    # ===== 15. 设备管理（运维） =====
    device_ids = []
    for i in range(15):
        c.execute('''INSERT INTO devices (device_sn, product_id, customer, firmware_version, activate_date, last_online, status, ip_address, location, remark, created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)''',
            (f"DEV{datetime.now().strftime('%Y%m%d')}{i+1:04d}",
             random.choice(product_ids),
             random.choice(['XX通信有限公司', 'YY科技股份公司', 'ZZ物联网科技', 'WW智能设备公司']),
             random.choice(['V2.1.0', 'V2.0.5', 'V1.5.2', 'V1.5.0']),
             rand_date(-60, -30),
             rand_dt(-2, 0),
             random.choice(['在线', '离线', '在线', '在线', '异常']),
             f"192.168.{random.randint(0,255)}.{random.randint(1,254)}",
             random.choice(['上海工厂', '深圳园区', '北京机房', '广州仓库', '成都测试场']),
             random.choice(['正常运行', '客户现场设备', '测试设备', '演示设备']),
             rand_dt(-60, -30)))
        device_ids.append(c.lastrowid)
    print(f"[15/18] 设备管理: {len(device_ids)}台")

    # ===== 16. 设备告警 =====
    for i in range(10):
        is_resolved = random.random() > 0.3
        c.execute('''INSERT INTO device_alerts (device_id, alert_type, level, content, status, handler, solution, resolved_at, created_at)
            VALUES (?,?,?,?,?,?,?,?,?)''',
            (random.choice(device_ids),
             random.choice(['离线告警', '信号弱', '低电量', '异常重启', '数据上报异常']),
             random.choice(['紧急', '重要', '一般']),
             random.choice([
                 '设备连续24小时未上线，请检查',
                 '信号强度低于-95dBm，影响通信',
                 '电池电量低于20%，请及时更换',
                 '设备发生异常重启，需排查原因',
                 '数据上报成功率低于80%'
             ]),
             random.choice(['待处理', '处理中', '已处理', '已忽略']) if not is_resolved else '已处理',
             random.choice(['运维工程师A', '运维工程师B']),
             random.choice(['已通知客户', '正在排查', '已恢复正常', '客户现场问题', '已升级固件']) if is_resolved else None,
             rand_dt(-10, 0) if is_resolved else None,
             rand_dt(-15, -1)))
    print(f"[16/18] 设备告警: 10条")

    # ===== 17. OTA版本管理 =====
    ota_version_ids = []
    for p in products:
        for i in range(2):
            c.execute('''INSERT INTO ota_versions (version_no, product_id, file_name, file_size, md5, release_notes, status, rollout_strategy, rollout_percentage, creator, created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)''',
                (f"V{random.randint(1,3)}.{random.randint(0,9)}.{random.randint(0,9)}",
                 p['id'],
                 f"{p['model']}_firmware_v{i+1}.bin",
                 random.randint(500, 2000),
                 ''.join(random.choices('0123456789abcdef', k=32)),
                 random.choice([
                     '1. 修复已知Bug\n2. 优化网络注册速度\n3. 增加低功耗模式',
                     '1. 新增MQTT协议支持\n2. 优化OTA升级流程\n3. 提升稳定性',
                     '1. 修复低温重启问题\n2. 优化功耗\n3. 增加AT指令'
                 ]),
                 random.choice(['已发布', '测试中', '已发布', '灰度发布']),
                 random.choice(['全量发布', '灰度发布', '指定设备']),
                 random.randint(10, 100),
                 random.choice(['研发工程师A', '研发工程师B']),
                 rand_dt(-30, -10)))
            ota_version_ids.append(c.lastrowid)
    print(f"[17/18] OTA版本: {len(ota_version_ids)}个")

    # ===== 18. OTA升级记录 =====
    for i in range(20):
        is_success = random.random() > 0.2
        c.execute('''INSERT INTO ota_upgrades (ota_version_id, device_id, status, start_time, end_time, result, error_msg, created_at)
            VALUES (?,?,?,?,?,?,?,?)''',
            (random.choice(ota_version_ids), random.choice(device_ids),
             random.choice(['升级成功', '升级成功', '升级中', '升级失败', '升级成功']),
             rand_dt(-20, -1), rand_dt(-20, -1),
             '成功' if is_success else '失败',
             '' if is_success else random.choice(['网络中断导致升级失败', '固件校验失败', '设备电量不足']),
             rand_dt(-20, -1)))
    print(f"[18/18] OTA升级记录: 20条")

    # ===== 补充：通用任务表 =====
    for i in range(10):
        c.execute('''INSERT INTO tasks (task_no, title, description, module, source_type, source_id, assignee, creator, priority, status, due_date, expected_result, actual_result, progress, created_at, updated_at, completed_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
            (f"TASK{datetime.now().strftime('%Y%m%d')}{i+1:03d}",
             f"{random.choice(products)['name']}-{random.choice(['需求分析','方案设计','代码开发','测试验证','文档编写','客户沟通'])}-{i+1}",
             random.choice(['完成需求分析并输出文档', '进行技术方案设计评审', '完成核心功能开发', '执行系统测试并输出报告', '编写用户手册和技术文档', '与客户确认需求细节']),
             random.choice(['研发模块', '测试模块', '产品模块', '交付模块']),
             random.choice(['需求', '版本', 'Bug', '其他']),
             random.choice(req_ids + version_ids + bug_ids) if (req_ids or version_ids or bug_ids) else None,
             random.choice(['张工', '李工', '王工', '赵工']),
             random.choice(['产品经理', '项目经理', '研发主管']),
             random.choice(['高', '中', '低']),
             random.choice(['待开始', '进行中', '已完成', '已完成', '进行中']),
             rand_date(-10, 30),
             random.choice(['输出需求文档', '方案评审通过', '功能开发完成', '测试报告输出', '文档交付']),
             random.choice(['已完成', '进行中', '待开始']) if random.random() > 0.5 else None,
             random.randint(0, 100),
             rand_dt(-30, -5),
             rand_dt(-20, -1),
             rand_dt(-10, 0) if random.random() > 0.5 else None))
    print(f"[补充] 通用任务: 10条")

    conn.commit()

    # 统计
    print("\n" + "=" * 60)
    print("  补充数据生成完成！统计如下：")
    print("=" * 60)
    tables = c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name").fetchall()
    total = 0
    empty_tables = []
    for t in tables:
        count = c.execute(f"SELECT COUNT(*) FROM {t[0]}").fetchone()[0]
        if count > 0:
            print(f"  {t[0]:35s} {count:5d} 条")
            total += count
        else:
            empty_tables.append(t[0])
    print(f"  {'合计':35s} {total:5d} 条")
    if empty_tables:
        print(f"\n  空表: {', '.join(empty_tables)}")
    conn.close()
    print(f"\n数据库文件: {DB_PATH}")

if __name__ == '__main__':
    generate_supplement()
