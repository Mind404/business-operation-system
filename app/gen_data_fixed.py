import sqlite3
from datetime import datetime

conn = sqlite3.connect('business.db')
cursor = conn.cursor()
now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

print("=" * 60)
print("开始生成2款新产品的虚拟数据...")
print("=" * 60)

req_fields = ['title','req_type','source_dept','customer','description','quantity','priority','status','delivery_date',
              'product_model','batch_delivery','application_scenario','system_verify_point','verify_function','performance_req','support_product',
              'certification_type','spec_requirement','industry_standard','baseline_product_type','task_progress','time_plan','deliverable','created_at','updated_at']

# ========== 产品1: 5G工业网关模组 ==========
print("\n【产品1】5G工业网关模组 (CM-5G-GW01)")
cursor.execute('INSERT INTO products (name, model, version, description, lifecycle_stage, stocking_strategy, created_at) VALUES (?,?,?,?,?,?,?)',
               ('5G工业网关模组', 'CM-5G-GW01', 'V1.0.0', '工业级5G通信网关，支持SA/NSA双模，适用于工业物联网、智能制造、远程监控等场景。', '研发中', '不囤货，按需生产', now))
p1 = cursor.lastrowid
print(f"  ✓ 产品 ID={p1}")

for name, code, is_key, qty, supplier in [
    ('5G通信模组','BOM-5G-001',1,1,'移远通信'),('主控芯片','BOM-MCU-001',1,1,'意法半导体'),
    ('DDR4内存','BOM-DDR-001',1,2,'三星'),('eMMC存储','BOM-EMMC-001',1,1,'江波龙'),
    ('以太网PHY芯片','BOM-ETH-001',1,2,'微芯科技'),('电源管理芯片','BOM-PMU-001',1,1,'德州仪器'),
    ('工业级外壳','BOM-CASE-001',0,1,'本地供应商'),('PCB主板','BOM-PCB-001',0,1,'深南电路')]:
    cursor.execute('INSERT INTO bom_items (product_id, material_name, material_code, is_key_material, quantity_per_unit, supplier) VALUES (?,?,?,?,?,?)', (p1, name, code, is_key, qty, supplier))
print(f"  ✓ BOM表 8项")

reqs1 = [
    {'title':'华东智能制造客户-5G网关批量需求','req_type':'用户需求','source_dept':'华东市场团队','customer':'XX智能制造有限公司','description':'客户智能工厂改造项目，需要5G工业网关实现设备联网和数据采集。','quantity':500,'priority':'高','status':'已评审','delivery_date':'2026-12-15','product_model':'CM-5G-GW01','batch_delivery':'是','application_scenario':'工业智能制造-设备联网与数据采集'},
    {'title':'集团5G工业互联网平台联调验证需求','req_type':'集团系统侧需求','source_dept':'集团系统部','customer':'集团5G工业互联网平台项目组','description':'集团5G工业互联网平台二期建设，需要5G网关模组参与平台联调验证。','quantity':50,'priority':'高','status':'进行中','delivery_date':'2026-11-30','product_model':'CM-5G-GW01','system_verify_point':'2026年Q4平台联调验证','verify_function':'设备接入认证、数据上报、远程配置、OTA升级、边缘计算','performance_req':'数据上报时延<100ms，支持1000+数据点并发采集','support_product':'5G工业网关模组CM-5G-GW01'},
    {'title':'工业级通信设备入网认证与规范要求','req_type':'集团应用侧需求','source_dept':'集团质量部','customer':'集团入网认证中心','description':'根据集团工业通信设备入网规范，5G网关模组需要通过CCC认证、SRRC型号核准。','quantity':0,'priority':'中','status':'已评审','delivery_date':'2026-10-31','product_model':'CM-5G-GW01','certification_type':'CCC认证、SRRC型号核准、入网许可证','spec_requirement':'工业级宽温(-40℃~85℃)、宽压(9~36VDC)、IP30防护','industry_standard':'YD/T 1595(5G)、GB/T 17626(EMC)、GB 4943.1(安全)','baseline_product_type':'工业级通信网关'},
    {'title':'5G+工业互联网应用示范课题','req_type':'领导任务课题','source_dept':'总经办','customer':'公司战略发展委员会','description':'公司战略级课题，探索5G技术在工业互联网领域的应用。','quantity':0,'priority':'高','status':'进行中','delivery_date':'2027-03-31','product_model':'CM-5G-GW01','task_progress':'需求调研完成，方案设计中','time_plan':'2026Q3需求调研、2026Q4方案设计、2027Q1原型开发、2027Q2现场验证','deliverable':'5G工业互联网应用解决方案报告、原型系统、测试报告、专利2-3项'},
]
rids1 = []
for req in reqs1:
    vals = [req.get(f,'') for f in req_fields[:-2]] + [now, now]
    ph = ','.join(['?']*len(req_fields))
    cursor.execute(f'INSERT INTO requirements ({",".join(req_fields)}) VALUES ({ph})', vals)
    rids1.append(cursor.lastrowid)
print(f"  ✓ 4类需求 {len(reqs1)}条")

vids1 = []
for v in [
    {'no':'V0.9.0','name':'Alpha测试版','desc':'基础功能验证版本','cap':'5G联网、基础数据采集、Web配置界面','date':'2026-10-15','status':'测试中'},
    {'no':'V1.0.0','name':'正式发布版','desc':'首个正式版本','cap':'5G联网、Modbus/OPC UA协议、边缘计算、远程配置、OTA升级','date':'2026-12-01','status':'开发中'},
    {'no':'V1.1.0','name':'功能增强版','desc':'增加AI算法支持','cap':'AI边缘推理、Profinet协议、MQTT协议','date':'2027-02-28','status':'规划中'}]:
    cursor.execute('INSERT INTO software_versions (product_id, version_no, version_name, description, capabilities, planned_release_date, status, created_at) VALUES (?,?,?,?,?,?,?,?)', (p1, v['no'], v['name'], v['desc'], v['cap'], v['date'], v['status'], now))
    vids1.append(cursor.lastrowid)
print(f"  ✓ 软件版本 3个")

for vid in vids1:
    for rid in rids1[:2]:
        cursor.execute('INSERT OR IGNORE INTO version_requirements (version_id, requirement_id) VALUES (?,?)', (vid, rid))
print(f"  ✓ 关联需求到版本")

for t in [
    (vids1[0],rids1[0],'5G通信模组驱动开发','基于移远5G模组开发Linux驱动','高','已完成','张工','2026-08-01','2026-09-15','2026-09-15'),
    (vids1[0],rids1[0],'基础数据采集功能','实现Modbus RTU/TCP数据采集','高','已完成','李工','2026-08-15','2026-09-30','2026-09-30'),
    (vids1[0],rids1[1],'Web配置界面开发','开发Web管理界面','中','进行中','王工','2026-09-01','2026-10-10',None),
    (vids1[1],rids1[0],'OPC UA协议支持','实现OPC UA Server和Client','高','进行中','赵工','2026-09-15','2026-11-15',None),
    (vids1[1],rids1[1],'边缘计算框架','集成边缘计算框架','高','待开始','张工','2026-10-01','2026-11-30',None),
    (vids1[1],rids1[0],'OTA升级功能','实现固件OTA升级','中','待开始','李工','2026-10-15','2026-11-30',None),
    (vids1[2],rids1[0],'AI边缘推理引擎','集成轻量化AI推理引擎','中','待开始','赵工','2026-12-01','2027-02-15',None)]:
    cursor.execute('INSERT INTO dev_tasks (version_id, requirement_id, task_name, description, priority, status, assignee, planned_start, planned_finish, actual_finish, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)', (*t, now))
print(f"  ✓ 开发任务 7个")

for b in [
    ('BUG-2026-001',p1,vids1[0],'5G拨号偶发失败','弱信号环境下5G模组拨号偶发失败','功能缺陷','严重','高','已修复','测试组-刘工','张工','2026-09-20','2026-10-05','已修复并验证通过','增加拨号重试机制','通过','模组状态机异常未恢复','增加异常恢复机制','增加弱信号测试用例',0),
    ('BUG-2026-002',p1,vids1[0],'Modbus配置丢失','重启后部分Modbus配置丢失','功能缺陷','一般','高','已修复','测试组-陈工','李工','2026-09-25','2026-10-08','已修复并验证通过','修复配置保存逻辑','通过','配置文件写入时断电损坏','增加原子写入和备份','增加异常断电测试',0),
    ('BUG-2026-003',p1,vids1[0],'Web界面内存泄漏','长时间运行Web界面内存持续增长','性能问题','严重','高','处理中','测试组-刘工','王工','2026-10-02',None,None,'','','','','',1),
    ('BUG-2026-004',p1,vids1[1],'OPC UA连接数超限','超过50个客户端时连接拒绝','性能问题','一般','中','待处理','测试组-陈工','赵工','2026-10-10',None,None,'','','','','',0)]:
    cursor.execute('INSERT INTO bug_records (bug_no, product_id, version_id, title, description, bug_type, severity, priority, status, reporter, assignee, discovered_at, closed_at, close_reason, fix_solution, test_result, root_cause, corrective_action, prevention_measure, is_quality_issue, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)', (*b, now))
print(f"  ✓ BUG记录 4个")

cursor.execute('INSERT INTO production_schedules (schedule_no, product_id, requirement_id, quantity, plan_start, plan_finish, status, remark, created_at) VALUES (?,?,?,?,?,?,?,?,?)', ('PS-2026-001', p1, rids1[0], 200, '2026-11-01', '2026-11-20', '生产中', '首批试生产200套', now))
s1 = cursor.lastrowid
print(f"  ✓ 生产排程 ID={s1}")

cursor.execute('SELECT id, material_name, material_code, is_key_material, quantity_per_unit FROM bom_items WHERE product_id=?', (p1,))
for item in cursor.fetchall():
    qty = item[4] * 200
    avail = qty if item[3] else int(qty * 0.8)
    cursor.execute('INSERT INTO material_plans (schedule_id, bom_item_id, material_name, material_code, is_key_material, quantity_needed, quantity_available, unit, status, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)', (s1, item[0], item[1], item[2], item[3], qty, avail, '个', '已齐套' if avail>=qty else '缺料', now))
print(f"  ✓ 物料计划")

cursor.execute('INSERT INTO quality_inspections (inspection_no, inspection_type, product_id, sample_quantity, pass_quantity, fail_quantity, inspector, inspection_date, result, defect_description, pass_rate, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)', ('QI-2026-001', '出厂检验', p1, 50, 48, 2, '质量组-孙工', '2026-10-20', '合格', '2套外观瑕疵已返修', 96.0, now))
cursor.execute('INSERT INTO deliveries (delivery_no, product_id, market_team, quantity, customer, purpose, delivery_date, status, feedback, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)', ('DL-2026-001', p1, '华东市场团队', 50, 'XX智能制造有限公司', '智能工厂改造项目', '2026-10-25', '已交付', '客户验证中，反馈良好', now))
cursor.execute('INSERT INTO revenue_confirmations (confirm_no, customer, contract_no, product_id, amount, confirmed_amount, confirm_date, status, period, voucher, handler, remark, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)', ('RC-2026-001', 'XX智能制造有限公司', 'HT-2026-001', p1, 60000, 60000, '2026-10-30', '已确认', '2026Q4', 'PZ-2026-1001', '财务部-李会计', '首批样品款已到账', now))
print(f"  ✓ 质量检测/交付/收入确认")

# ========== 产品2: 低功耗蓝牙信标模组 ==========
print("\n【产品2】低功耗蓝牙信标模组 (CM-BLE-B01)")
cursor.execute('INSERT INTO products (name, model, version, description, lifecycle_stage, stocking_strategy, created_at) VALUES (?,?,?,?,?,?,?)',
               ('低功耗蓝牙信标模组', 'CM-BLE-B01', 'V2.0.0', '低功耗蓝牙5.0信标模组，支持iBeacon/Eddystone协议，电池续航可达3年。', '量产中', '不囤货，按需生产', now))
p2 = cursor.lastrowid
print(f"  ✓ 产品 ID={p2}")

for name, code, is_key, qty, supplier in [
    ('BLE5.0芯片','BOM-BLE-001',1,1,'Nordic'),('纽扣电池','BOM-BAT-001',1,1,'松下'),
    ('PCB天线','BOM-ANT-001',0,1,'本地供应商'),('塑料外壳','BOM-CASE-002',0,1,'本地供应商'),
    ('3M胶贴','BOM-TAPE-001',0,1,'3M')]:
    cursor.execute('INSERT INTO bom_items (product_id, material_name, material_code, is_key_material, quantity_per_unit, supplier) VALUES (?,?,?,?,?,?)', (p2, name, code, is_key, qty, supplier))
print(f"  ✓ BOM表 5项")

reqs2 = [
    {'title':'大型商场室内定位项目-蓝牙信标批量需求','req_type':'用户需求','source_dept':'华南市场团队','customer':'XX商业管理集团','description':'客户旗下10个大型商场室内定位项目，需要蓝牙信标实现商场内导航。','quantity':5000,'priority':'高','status':'进行中','delivery_date':'2026-11-30','product_model':'CM-BLE-B01','batch_delivery':'是','application_scenario':'大型商场室内定位与精准营销'},
    {'title':'集团智慧园区定位系统验证需求','req_type':'集团系统侧需求','source_dept':'集团信息部','customer':'集团智慧园区项目组','description':'集团智慧园区建设，需要蓝牙信标参与人员定位和资产追踪系统验证。','quantity':200,'priority':'中','status':'已完成','delivery_date':'2026-09-30','product_model':'CM-BLE-B01','system_verify_point':'2026年Q3智慧园区系统验证','verify_function':'人员定位、资产追踪、电子围栏、考勤打卡','performance_req':'定位精度<3米，设备在线率>99%','support_product':'低功耗蓝牙信标CM-BLE-B01'},
    {'title':'蓝牙设备SRRC认证与环保要求','req_type':'集团应用侧需求','source_dept':'集团质量部','customer':'集团认证中心','description':'蓝牙信标产品需要通过SRRC型号核准，满足RoHS环保要求。','quantity':0,'priority':'中','status':'已完成','delivery_date':'2026-08-31','product_model':'CM-BLE-B01','certification_type':'SRRC型号核准、RoHS、UN38.3','spec_requirement':'发射功率≤+4dBm，工作温度-20℃~60℃，IP65防护','industry_standard':'蓝牙5.0规范、SRRC无线电管理规定、RoHS 2.0','baseline_product_type':'低功耗蓝牙设备'},
    {'title':'蓝牙AoA高精度定位技术预研课题','req_type':'领导任务课题','source_dept':'研发中心','customer':'公司技术委员会','description':'探索蓝牙5.1 AoA到达角高精度定位技术，实现厘米级定位。','quantity':0,'priority':'中','status':'进行中','delivery_date':'2027-06-30','product_model':'CM-BLE-B01','task_progress':'技术调研完成，方案论证中','time_plan':'2026Q3技术调研、2026Q4方案论证、2027Q1原型开发、2027Q2测试验证','deliverable':'蓝牙AoA定位技术研究报告、原型系统、测试报告、专利3-5项'},
]
rids2 = []
for req in reqs2:
    vals = [req.get(f,'') for f in req_fields[:-2]] + [now, now]
    ph = ','.join(['?']*len(req_fields))
    cursor.execute(f'INSERT INTO requirements ({",".join(req_fields)}) VALUES ({ph})', vals)
    rids2.append(cursor.lastrowid)
print(f"  ✓ 4类需求 {len(reqs2)}条")

vids2 = []
for v in [
    {'no':'V2.0.0','name':'量产稳定版','desc':'量产稳定版本','cap':'iBeacon、Eddystone、可配置发射功率','date':'2026-06-01','actual':'2026-06-15','status':'已发布'},
    {'no':'V2.1.0','name':'功能增强版','desc':'增加传感器支持','cap':'温湿度传感器、加速度传感器、低功耗优化','date':'2026-12-01','actual':None,'status':'开发中'}]:
    cursor.execute('INSERT INTO software_versions (product_id, version_no, version_name, description, capabilities, planned_release_date, actual_release_date, status, created_at) VALUES (?,?,?,?,?,?,?,?,?)', (p2, v['no'], v['name'], v['desc'], v['cap'], v['date'], v['actual'], v['status'], now))
    vids2.append(cursor.lastrowid)
print(f"  ✓ 软件版本 2个")

for vid in vids2:
    for rid in rids2[:2]:
        cursor.execute('INSERT OR IGNORE INTO version_requirements (version_id, requirement_id) VALUES (?,?)', (vid, rid))
print(f"  ✓ 关联需求到版本")

for t in [
    (vids2[1],rids2[0],'温湿度传感器驱动','集成SHT30温湿度传感器','高','进行中','陈工','2026-09-01','2026-10-15',None),
    (vids2[1],rids2[0],'低功耗模式优化','优化电源管理，延长电池续航','高','进行中','林工','2026-09-15','2026-10-31',None),
    (vids2[1],rids2[1],'加速度传感器支持','集成LIS2DH加速度传感器','中','待开始','陈工','2026-10-01','2026-11-15',None)]:
    cursor.execute('INSERT INTO dev_tasks (version_id, requirement_id, task_name, description, priority, status, assignee, planned_start, planned_finish, actual_finish, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)', (*t, now))
print(f"  ✓ 开发任务 3个")

for b in [
    ('BUG-2026-005',p2,vids2[0],'电池续航不达标','实测续航仅2.5年，未达3年目标','性能问题','一般','高','已修复','测试组-吴工','林工','2026-05-10','2026-05-25','已修复并验证通过','优化默认参数和休眠电流','通过','默认参数设置偏保守','优化默认参数','增加续航自动化测试',0),
    ('BUG-2026-006',p2,vids2[1],'温湿度数据漂移','长时间运行后温湿度数据漂移','功能缺陷','一般','中','处理中','测试组-吴工','陈工','2026-10-05',None,None,'','','','','',0)]:
    cursor.execute('INSERT INTO bug_records (bug_no, product_id, version_id, title, description, bug_type, severity, priority, status, reporter, assignee, discovered_at, closed_at, close_reason, fix_solution, test_result, root_cause, corrective_action, prevention_measure, is_quality_issue, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)', (*b, now))
print(f"  ✓ BUG记录 2个")

cursor.execute('INSERT INTO production_schedules (schedule_no, product_id, requirement_id, quantity, plan_start, plan_finish, status, remark, created_at) VALUES (?,?,?,?,?,?,?,?,?)', ('PS-2026-002', p2, rids2[0], 2000, '2026-10-15', '2026-11-10', '生产中', '首批2000套，供应商场项目', now))
s2 = cursor.lastrowid
print(f"  ✓ 生产排程 ID={s2}")

cursor.execute('SELECT id, material_name, material_code, is_key_material, quantity_per_unit FROM bom_items WHERE product_id=?', (p2,))
for item in cursor.fetchall():
    qty = item[4] * 2000
    avail = qty if item[3] else int(qty * 0.9)
    cursor.execute('INSERT INTO material_plans (schedule_id, bom_item_id, material_name, material_code, is_key_material, quantity_needed, quantity_available, unit, status, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)', (s2, item[0], item[1], item[2], item[3], qty, avail, '个', '已齐套' if avail>=qty else '缺料', now))
print(f"  ✓ 物料计划")

cursor.execute('INSERT INTO quality_inspections (inspection_no, inspection_type, product_id, sample_quantity, pass_quantity, fail_quantity, inspector, inspection_date, result, defect_description, pass_rate, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)', ('QI-2026-002', '出厂检验', p2, 500, 495, 5, '质量组-孙工', '2026-10-20', '合格', '5套外观不良已剔除', 99.0, now))
cursor.execute('INSERT INTO deliveries (delivery_no, product_id, market_team, quantity, customer, purpose, delivery_date, status, feedback, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)', ('DL-2026-002', p2, '华南市场团队', 1000, 'XX商业管理集团', '商场室内定位项目', '2026-10-28', '已交付', '3个商场已部署，运行稳定', now))
cursor.execute('INSERT INTO revenue_confirmations (confirm_no, customer, contract_no, product_id, amount, confirmed_amount, confirm_date, status, period, voucher, handler, remark, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)', ('RC-2026-002', 'XX商业管理集团', 'HT-2026-002', p2, 35000, 35000, '2026-11-05', '已确认', '2026Q4', 'PZ-2026-1101', '财务部-王会计', '首批货款已到账', now))
print(f"  ✓ 质量检测/交付/收入确认")

conn.commit()

print("\n" + "=" * 60)
print("数据生成完成！")
print("=" * 60)
print(f"\n统计：产品2款 | 需求8条 | 版本5个 | 任务10个 | BUG6个 | 排程2个 | 检测2条 | 交付2条 | 收入2条")

# 验证
cursor.execute('SELECT COUNT(*) FROM products WHERE id IN (?,?)', (p1, p2))
print(f"\n验证 - 产品: {cursor.fetchone()[0]}")
cursor.execute('SELECT COUNT(*) FROM requirements WHERE product_model IN (?,?)', ('CM-5G-GW01', 'CM-BLE-B01'))
print(f"验证 - 需求: {cursor.fetchone()[0]}")
cursor.execute('SELECT COUNT(*) FROM software_versions WHERE product_id IN (?,?)', (p1, p2))
print(f"验证 - 版本: {cursor.fetchone()[0]}")
cursor.execute('SELECT COUNT(*) FROM bug_records WHERE product_id IN (?,?)', (p1, p2))
print(f"验证 - BUG: {cursor.fetchone()[0]}")

conn.close()
print("\n✓ 所有数据已成功写入数据库！")
