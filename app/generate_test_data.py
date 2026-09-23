# -*- coding: utf-8 -*-
"""
综合测试数据生成脚本
生成10款产品 + 全场景虚拟数据，用于业务逻辑验证和压力测试
运行方式: python generate_test_data.py
"""
import os
import sqlite3
import random
from datetime import datetime, timedelta

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'business.db')

# 固定随机种子，保证数据可复现
random.seed(42)

# ============================================================
# 基础数据定义
# ============================================================
PRODUCTS = [
    {"name": "5G通信模组", "model": "CM-5G-001", "version": "V1.0", "stage": "量产中", "desc": "支持5G NSA/SA双模，Sub-6GHz，适用于智慧城市、工业互联网"},
    {"name": "5G通信模组Pro", "model": "CM-5G-002", "version": "V2.0", "stage": "量产中", "desc": "5G毫米波+Sub-6，支持网络切片，适用于高端路由器"},
    {"name": "4G通信模组", "model": "CM-4G-001", "version": "V2.1", "stage": "量产中", "desc": "LTE Cat.4，支持FDD/TDD，适用于POS机、车载终端"},
    {"name": "4G工业版模组", "model": "CM-4G-002", "version": "V1.5", "stage": "维护中", "desc": "工业级宽温-40~85℃，支持双SIM卡，适用于工业网关"},
    {"name": "NB-IoT模组", "model": "CM-NB-001", "version": "V1.2", "stage": "量产中", "desc": "NB-IoT Cat.NB2，超低功耗，适用于智能水表、燃气表"},
    {"name": "LoRa模组", "model": "CM-LORA-001", "version": "V1.0", "stage": "研发中", "desc": "LoRaWAN 1.0.4，远距离低功耗，适用于智慧农业、环境监测"},
    {"name": "WiFi6模组", "model": "CM-WIFI-001", "version": "V1.0", "stage": "规划中", "desc": "WiFi6 2x2 MIMO，支持OFDMA，适用于智能家居、高清视频"},
    {"name": "蓝牙5.2模组", "model": "CM-BT-001", "version": "V1.1", "stage": "量产中", "desc": "蓝牙5.2双模，支持BLE Mesh，适用于可穿戴设备、定位标签"},
    {"name": "车载通信模组", "model": "CM-AUTO-001", "version": "V0.9", "stage": "研发中", "desc": "车规级5G+C-V2X，支持eCall，适用于智能网联汽车"},
    {"name": "边缘计算网关", "model": "GW-EDGE-001", "version": "V1.0", "stage": "量产中", "desc": "ARM Cortex-A76，支持5G+WiFi6，本地AI推理，适用于边缘计算"},
]

# 物料池（名称、编码、是否关键料、单位、供应商）
MATERIALS_POOL = [
    ("5G主芯片", "IC-5G-MAIN", 1, "个", "高通代理商"),
    ("4G主芯片", "IC-4G-MAIN", 1, "个", "紫光展锐代理商"),
    ("NB-IoT芯片", "IC-NB-MAIN", 1, "个", "华为海思代理商"),
    ("射频前端模块", "IC-RF-FEM", 1, "个", "Skyworks代理商"),
    ("基带处理器", "IC-BB-PROC", 1, "个", "联发科代理商"),
    ("电源管理IC", "IC-PMIC", 1, "个", "TI代理商"),
    ("Flash存储芯片", "IC-FLASH", 0, "个", "旺宏代理商"),
    ("DDR内存", "IC-DDR", 0, "个", "三星代理商"),
    ("PCB板-4层", "PCB-4L", 0, "片", "深圳PCB厂A"),
    ("PCB板-6层", "PCB-6L", 0, "片", "深圳PCB厂B"),
    ("PCB板-8层HDI", "PCB-8HDI", 0, "片", "珠海PCB厂"),
    ("射频连接器", "CON-RF", 0, "个", "东莞连接器厂"),
    ("板对板连接器", "CON-BTB", 0, "个", "苏州连接器厂"),
    ("SIM卡座", "CON-SIM", 0, "个", "深圳连接器厂"),
    ("天线座", "CON-ANT", 0, "个", "东莞连接器厂"),
    ("晶振", "CMP-XTAL", 0, "个", "日本NDK代理商"),
    ("屏蔽罩", "MECH-SHIELD", 0, "个", "深圳五金厂"),
    ("外壳-注塑", "MECH-CASE", 0, "个", "东莞注塑厂"),
    ("散热片", "MECH-HEATSINK", 0, "个", "深圳五金厂"),
    ("螺丝包", "MECH-SCREW", 0, "套", "标准件"),
    ("电阻电容包", "PASS-RC", 0, "套", "国巨代理商"),
    ("电感磁珠包", "PASS-IND", 0, "套", "顺络代理商"),
    ("ESD保护器件", "PASS-ESD", 0, "个", "安森美代理商"),
    ("LED指示灯", "PASS-LED", 0, "个", "国星光电"),
    ("SIM卡-测试卡", "TEST-SIM", 0, "张", "运营商测试卡"),
]

DEPARTMENTS = ["华东市场团队", "华南市场团队", "华北市场团队", "西南市场团队", "产品经理部", "售前技术部"]
CUSTOMERS = ["XX通信有限公司", "YY科技股份有限公司", "ZZ智能科技", "WW物联网公司", "VV汽车集团", "UU电子科技", "TT智慧城市", "SS能源集团", "RR医疗科技", "QQ安防科技"]
SUPPLIERS = ["深圳XX电子厂", "东莞YY电子厂", "苏州ZZ电子厂", "惠州WW电子厂", "中山UU电子厂"]
INSPECTORS = ["张工", "李工", "王工", "赵工", "陈工"]
MARKET_TEAMS = ["华东市场团队", "华南市场团队", "华北市场团队", "西南市场团队"]
PURPOSES = ["客户样机测试", "批量订单交付", "项目试点", "展会演示", "客户试用", "售后替换", "研发测试", "认证测试"]
DEFECT_TYPES = ["虚焊", "物料错件", "功能不良", "外观不良", "射频指标不达标", "功耗超标", "通信掉线", "接口接触不良"]
ROOT_CAUSES = ["锡膏印刷偏移", "物料来料不良", "SMT贴装偏移", "回流焊温度异常", "设计余量不足", "供应商工艺波动", "测试夹具接触不良", "静电损伤"]
HANDLING_METHODS = ["返工重焊", "更换物料", "调整工艺参数", "供应商整改", "设计变更", "全检筛选", "报废处理", "让步接收"]

def rand_date(start_days_ago=180, end_days_ago=0):
    """生成随机日期字符串"""
    start, end = min(start_days_ago, end_days_ago), max(start_days_ago, end_days_ago)
    days = random.randint(start, end)
    return (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')

def rand_datetime(start_days_ago=180, end_days_ago=0):
    """生成随机日期时间字符串"""
    start, end = min(start_days_ago, end_days_ago), max(start_days_ago, end_days_ago)
    days = random.randint(start, end)
    hours = random.randint(8, 18)
    minutes = random.randint(0, 59)
    return (datetime.now() - timedelta(days=days, hours=hours, minutes=minutes)).strftime('%Y-%m-%d %H:%M:%S')

def generate_data():
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)

    db = sqlite3.connect(DB_PATH)
    db.execute('PRAGMA foreign_keys = ON')

    # 创建表结构
    with open(os.path.join(os.path.dirname(__file__), 'schema.sql'), 'r', encoding='utf-8') as f:
        db.executescript(f.read())

    print("=" * 60)
    print("  开始生成综合测试数据...")
    print("=" * 60)

    # ============================================================
    # 1. 生成10款产品
    # ============================================================
    product_ids = []
    for p in PRODUCTS:
        cursor = db.execute('''INSERT INTO products (name, model, version, description, lifecycle_stage, stocking_strategy, created_at)
                      VALUES (?,?,?,?,?,?,?)''',
                   (p["name"], p["model"], p["version"], p["desc"], p["stage"],
                    "不囤货，按需生产；关键物料自供，通用物料供应商自采",
                    rand_datetime(365, 30)))
        product_ids.append(cursor.lastrowid)
    print(f"  [1/10] 产品: {len(product_ids)} 款")

    # ============================================================
    # 2. 生成每款产品的BOM（5-8种物料）
    # ============================================================
    bom_count = 0
    for pid in product_ids:
        num_materials = random.randint(5, 8)
        selected = random.sample(MATERIALS_POOL, min(num_materials, len(MATERIALS_POOL)))
        for mat in selected:
            db.execute('''INSERT INTO bom_items (product_id, material_name, material_code, is_key_material, unit, quantity_per_unit, supplier)
                          VALUES (?,?,?,?,?,?,?)''',
                       (pid, mat[0], mat[1], mat[2], mat[3],
                        round(random.uniform(0.5, 4.0), 1), mat[4]))
            bom_count += 1
    print(f"  [2/10] BOM物料: {bom_count} 条")

    # ============================================================
    # 3. 生成库存数据（所有BOM物料 + 公司库房/供应商暂存）
    # ============================================================
    all_materials = db.execute('SELECT DISTINCT material_name, material_code, is_key_material, unit FROM bom_items').fetchall()
    inv_count = 0
    for mat in all_materials:
        # 公司库房
        qty = random.choice([0, 10, 50, 100, 200, 500, 1000, 2000, 5000])
        status = random.choice(["正常", "正常", "正常", "待检", "冻结"])
        db.execute('''INSERT INTO inventory (material_name, material_code, is_key_material, location, quantity, unit, status, remark, updated_at)
                      VALUES (?,?,?,?,?,?,?,?,?)''',
                   (mat[0], mat[1], mat[2], "公司库房", qty, mat[3], status,
                    "关键物料" if mat[2] else "通用物料", rand_datetime(30, 0)))
        inv_count += 1
        # 供应商暂存（部分物料）
        if random.random() > 0.3:
            qty2 = random.choice([0, 100, 500, 1000, 3000, 5000, 10000])
            db.execute('''INSERT INTO inventory (material_name, material_code, is_key_material, location, quantity, unit, status, remark, updated_at)
                          VALUES (?,?,?,?,?,?,?,?,?)''',
                       (mat[0], mat[1], mat[2], "供应商暂存", qty2, mat[3], "正常",
                        "供应商备货", rand_datetime(30, 0)))
            inv_count += 1
    print(f"  [3/10] 库存记录: {inv_count} 条")

    # ============================================================
    # 4. 生成需求数据（30条）
    # ============================================================
    req_count = 0
    req_statuses = ["待评审", "已评审", "进行中", "已完成", "已取消"]
    priorities = ["高", "中", "低"]
    for i in range(30):
        pid = random.choice(product_ids)
        pname = db.execute('SELECT name FROM products WHERE id=?', (pid,)).fetchone()[0]
        status = random.choices(req_statuses, weights=[2, 3, 4, 8, 1])[0]
        priority = random.choices(priorities, weights=[3, 5, 2])[0]
        qty = random.choice([10, 20, 50, 100, 200, 500, 1000, 2000, 5000])
        db.execute('''INSERT INTO requirements (title, source_dept, customer, description, quantity, priority, status, delivery_date, created_at, updated_at)
                      VALUES (?,?,?,?,?,?,?,?,?,?)''',
                   (f"{random.choice(CUSTOMERS)}{pname}需求",
                    random.choice(DEPARTMENTS),
                    random.choice(CUSTOMERS),
                    f"客户需要{qty}套{pname}，用于{random.choice(PURPOSES)}",
                    qty, priority, status, rand_date(60, 180),
                    rand_datetime(120, 1), rand_datetime(30, 0)))
        req_count += 1
    print(f"  [4/10] 需求: {req_count} 条")

    # ============================================================
    # 5. 生成物料领用单（15条）
    # ============================================================
    req_ids = [r[0] for r in db.execute('SELECT id FROM requirements').fetchall()]
    requisition_count = 0
    for i in range(15):
        pid = random.choice(product_ids)
        rid = random.choice(req_ids) if req_ids else None
        status = random.choice(["待审批", "已审批", "已审批", "已审批"])
        total_qty = random.choice([50, 100, 200, 500, 1000])
        req_no = f"RL{datetime.now().strftime('%Y%m%d')}{i+1:04d}"
        cursor = db.execute('''INSERT INTO material_requisitions (requisition_no, product_id, requirement_id, applicant, status, total_quantity, approved_at, created_at)
                      VALUES (?,?,?,?,?,?,?,?)''',
                   (req_no, pid, rid, random.choice(["王经理", "李主管", "张工", "赵工"]),
                    status, total_qty,
                    rand_datetime(60, 1) if status == "已审批" else None,
                    rand_datetime(90, 1)))
        req_id_new = cursor.lastrowid
        # 领用明细（3-5种物料）
        boms = db.execute('SELECT material_name, unit FROM bom_items WHERE product_id=?', (pid,)).fetchall()
        num_items = random.randint(3, min(5, len(boms)))
        for bom in random.sample(boms, num_items):
            db.execute('''INSERT INTO requisition_items (requisition_id, material_name, quantity, unit)
                          VALUES (?,?,?,?)''',
                       (req_id_new, bom[0], random.randint(10, 500), bom[1]))
        requisition_count += 1
    print(f"  [5/10] 物料领用单: {requisition_count} 条")

    # ============================================================
    # 6. 生成生产订单（25条）
    # ============================================================
    po_statuses = ["待排产", "生产中", "已完成", "已入库", "已取消"]
    po_count = 0
    for i in range(25):
        pid = random.choice(product_ids)
        rid = random.choice(req_ids) if req_ids and random.random() > 0.3 else None
        status = random.choices(po_statuses, weights=[2, 4, 8, 8, 1])[0]
        qty = random.choice([50, 100, 200, 500, 1000, 2000])
        plan_start = rand_date(60, 30)
        plan_finish = (datetime.strptime(plan_start, '%Y-%m-%d') + timedelta(days=random.randint(7, 30))).strftime('%Y-%m-%d')
        actual_finish = rand_date(30, 0) if status in ["已完成", "已入库"] else None
        progress = 0 if status == "待排产" else (random.randint(10, 90) if status == "生产中" else 100)
        order_no = f"PO{datetime.now().strftime('%Y%m%d')}{i+1:04d}"
        db.execute('''INSERT INTO production_orders (order_no, product_id, requirement_id, supplier, quantity, plan_start, plan_finish, actual_finish, status, wip_progress, remark, created_at)
                      VALUES (?,?,?,?,?,?,?,?,?,?,?,?)''',
                   (order_no, pid, rid, random.choice(SUPPLIERS), qty, plan_start, plan_finish,
                    actual_finish, status, progress,
                    "加急订单" if random.random() > 0.7 else "",
                    rand_datetime(90, 30)))
        po_count += 1
    print(f"  [6/10] 生产订单: {po_count} 条")

    # ============================================================
    # 7. 生成质量检验记录（40条）
    # ============================================================
    po_ids = [p[0] for p in db.execute('SELECT id FROM production_orders').fetchall()]
    insp_types = ["IQC", "IPQC", "OQC"]
    qi_count = 0
    for i in range(40):
        pid = random.choice(product_ids)
        poid = random.choice(po_ids) if po_ids and random.random() > 0.2 else None
        insp_type = random.choices(insp_types, weights=[3, 3, 6])[0]
        sample = random.choice([10, 20, 32, 50, 80, 100, 200])
        # 80%合格，20%不合格
        if random.random() > 0.2:
            passed = sample - random.randint(0, max(1, sample // 20))
            result = "合格"
        else:
            passed = sample - random.randint(max(1, sample // 10), max(2, sample // 3))
            result = "不合格"
        failed = sample - passed
        rate = round(passed / sample * 100, 2) if sample > 0 else 0
        insp_no = f"QI{datetime.now().strftime('%Y%m%d')}{i+1:04d}"
        defect_desc = ""
        if result == "不合格":
            defect_desc = f"主要缺陷：{random.choice(DEFECT_TYPES)}，不良率{round(failed/sample*100,1)}%"
        db.execute('''INSERT INTO quality_inspections (inspection_no, inspection_type, production_order_id, product_id, inspector, sample_quantity, pass_quantity, fail_quantity, pass_rate, result, defect_description, inspection_date, created_at)
                      VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                   (insp_no, insp_type, poid, pid, random.choice(INSPECTORS),
                    sample, passed, failed, rate, result, defect_desc,
                    rand_date(60, 0), rand_datetime(60, 0)))
        qi_count += 1
    print(f"  [7/10] 质量检验: {qi_count} 条 (合格{sum(1 for _ in range(qi_count) if random.random()>0.2)}条，不合格{qi_count-sum(1 for _ in range(qi_count) if random.random()>0.2)}条)")

    # ============================================================
    # 8. 生成不合格品处理记录（10条）
    # ============================================================
    fail_insps = db.execute("SELECT id FROM quality_inspections WHERE result='不合格'").fetchall()
    dh_count = 0
    for insp in fail_insps[:10]:
        status = random.choice(["待处理", "处理中", "已关闭", "已关闭"])
        db.execute('''INSERT INTO defect_handling (inspection_id, defect_type, root_cause, handling_method, responsible_person, status, closed_at, created_at)
                      VALUES (?,?,?,?,?,?,?,?)''',
                   (insp[0], random.choice(DEFECT_TYPES), random.choice(ROOT_CAUSES),
                    random.choice(HANDLING_METHODS), random.choice(INSPECTORS),
                    status, rand_datetime(30, 0) if status == "已关闭" else None,
                    rand_datetime(30, 0)))
        dh_count += 1
    print(f"  [8/10] 不合格品处理: {dh_count} 条")

    # ============================================================
    # 9. 生成入库记录（20条）
    # ============================================================
    completed_pos = db.execute("SELECT id, product_id FROM production_orders WHERE status IN ('已完成','已入库')").fetchall()
    wr_count = 0
    for i in range(20):
        po = random.choice(completed_pos) if completed_pos else None
        pid = po[1] if po else random.choice(product_ids)
        poid = po[0] if po else None
        status = random.choice(["待审批", "已入库", "已入库", "已入库"])
        qty = random.choice([50, 100, 200, 500, 1000])
        receipt_no = f"WR{datetime.now().strftime('%Y%m%d')}{i+1:04d}"
        db.execute('''INSERT INTO warehouse_receipts (receipt_no, production_order_id, product_id, quantity, quality_status, approver, status, approved_at, created_at)
                      VALUES (?,?,?,?,?,?,?,?,?)''',
                   (receipt_no, poid, pid, qty,
                    random.choice(["合格", "合格", "合格", "让步接收"]),
                    random.choice(["王经理", "李经理", "张经理"]),
                    status, rand_datetime(30, 0) if status == "已入库" else None,
                    rand_datetime(45, 0)))
        wr_count += 1
    print(f"  [9/10] 入库记录: {wr_count} 条")

    # ============================================================
    # 10. 生成交付记录（25条）
    # ============================================================
    dl_count = 0
    for i in range(25):
        pid = random.choice(product_ids)
        qty = random.choice([5, 10, 20, 50, 100, 200, 500])
        delivery_no = f"DL{datetime.now().strftime('%Y%m%d')}{i+1:04d}"
        feedback = ""
        if random.random() > 0.5:
            feedback = random.choice(["客户反馈良好，后续有复购意向", "测试通过，等待批量订单", "客户提出功能优化建议", "交付及时，质量稳定", "客户反馈功耗偏高，需优化", "客户满意，已签订长期合作"])
        db.execute('''INSERT INTO deliveries (delivery_no, product_id, market_team, quantity, customer, purpose, delivery_date, status, feedback, created_at)
                      VALUES (?,?,?,?,?,?,?,?,?,?)''',
                   (delivery_no, pid, random.choice(MARKET_TEAMS), qty,
                    random.choice(CUSTOMERS), random.choice(PURPOSES),
                    rand_date(45, 0), "已交付", feedback, rand_datetime(45, 0)))
        dl_count += 1
    print(f"  [10/10] 交付记录: {dl_count} 条")

    # ============================================================
    # 生成操作日志
    # ============================================================
    log_modules = ["需求管理", "产品规划", "研发生产", "质量检测", "验证收益", "系统"]
    log_actions = ["新增", "编辑", "删除", "审批", "更新", "导出", "登录"]
    for i in range(50):
        db.execute('''INSERT INTO operation_logs (module, action, detail, operator, created_at)
                      VALUES (?,?,?,?,?)''',
                   (random.choice(log_modules), random.choice(log_actions),
                    f"操作记录#{i+1}", random.choice(["admin", "张工", "李工", "王经理"]),
                    rand_datetime(60, 0)))

    db.commit()
    db.close()

    # 统计汇总
    db = sqlite3.connect(DB_PATH)
    print("\n" + "=" * 60)
    print("  数据生成完成！统计汇总：")
    print("=" * 60)
    tables = ['products', 'bom_items', 'inventory', 'requirements', 'material_requisitions',
              'requisition_items', 'production_orders', 'quality_inspections', 'defect_handling',
              'warehouse_receipts', 'deliveries', 'operation_logs']
    total = 0
    for t in tables:
        count = db.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0]
        print(f"    {t:30s}: {count:5d} 条")
        total += count
    print(f"    {'合计':30s}: {total:5d} 条")
    db.close()
    print(f"\n数据库文件: {DB_PATH}")
    print(f"文件大小: {os.path.getsize(DB_PATH) / 1024:.1f} KB")

if __name__ == '__main__':
    generate_data()
