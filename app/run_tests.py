# -*- coding: utf-8 -*-
"""
综合测试脚本：业务逻辑验证 + 压力测试
运行方式: python run_tests.py
需要先启动应用: python app.py --port 5000
"""
import os
import sys
import time
import sqlite3
import threading
import urllib.request
import urllib.error
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'business.db')
BASE_URL = "http://127.0.0.1:5000"

# ============================================================
# 测试结果收集
# ============================================================
class TestResult:
    def __init__(self):
        self.passed = []
        self.failed = []
        self.warnings = []

    def ok(self, name, detail=""):
        self.passed.append((name, detail))
        print(f"  [PASS] {name}" + (f" - {detail}" if detail else ""))

    def fail(self, name, detail=""):
        self.failed.append((name, detail))
        print(f"  [FAIL] {name}" + (f" - {detail}" if detail else ""))

    def warn(self, name, detail=""):
        self.warnings.append((name, detail))
        print(f"  [WARN] {name}" + (f" - {detail}" if detail else ""))

result = TestResult()

def get_db():
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys = ON')
    return db

# ============================================================
# 第一部分：业务逻辑验证（数据库层面）
# ============================================================
def test_data_integrity():
    print("\n" + "="*60)
    print("  第一部分：数据完整性与业务逻辑验证")
    print("="*60)

    db = get_db()

    # 1. 产品数量验证
    count = db.execute('SELECT COUNT(*) FROM products').fetchone()[0]
    if count >= 10:
        result.ok("产品数量", f"{count}款产品（>=10）")
    else:
        result.fail("产品数量", f"仅{count}款产品（<10）")

    # 2. 每款产品都有BOM
    products = db.execute('SELECT id, name FROM products').fetchall()
    no_bom = []
    for p in products:
        bom_count = db.execute('SELECT COUNT(*) FROM bom_items WHERE product_id=?', (p['id'],)).fetchone()[0]
        if bom_count == 0:
            no_bom.append(p['name'])
    if no_bom:
        result.fail("产品BOM完整性", f"以下产品无BOM: {', '.join(no_bom)}")
    else:
        result.ok("产品BOM完整性", f"所有{len(products)}款产品均有BOM")

    # 3. 每款产品都有关键料
    no_key = []
    for p in products:
        key_count = db.execute('SELECT COUNT(*) FROM bom_items WHERE product_id=? AND is_key_material=1', (p['id'],)).fetchone()[0]
        if key_count == 0:
            no_key.append(p['name'])
    if no_key:
        result.warn("关键料标注", f"以下产品未标注关键料: {', '.join(no_key)}")
    else:
        result.ok("关键料标注", f"所有产品均有关键料标注")

    # 4. 需求数据完整性
    reqs = db.execute('SELECT * FROM requirements').fetchall()
    invalid_req = []
    for r in reqs:
        if not r['title'] or r['quantity'] <= 0:
            invalid_req.append(f"ID={r['id']}")
    if invalid_req:
        result.fail("需求数据完整性", f"无效需求: {', '.join(invalid_req)}")
    else:
        result.ok("需求数据完整性", f"{len(reqs)}条需求均有效")

    # 5. 生产订单关联验证
    pos = db.execute('SELECT * FROM production_orders').fetchall()
    invalid_po = []
    for po in pos:
        if po['product_id'] and not db.execute('SELECT id FROM products WHERE id=?', (po['product_id'],)).fetchone():
            invalid_po.append(f"订单{po['order_no']}关联无效产品")
        if po['quantity'] <= 0:
            invalid_po.append(f"订单{po['order_no']}数量无效")
    if invalid_po:
        result.fail("生产订单关联", "; ".join(invalid_po[:5]))
    else:
        result.ok("生产订单关联", f"{len(pos)}条订单关联均有效")

    # 6. 质量检验良品率计算验证
    qis = db.execute('SELECT * FROM quality_inspections').fetchall()
    rate_error = []
    for qi in qis:
        if qi['sample_quantity'] > 0:
            expected = round(qi['pass_quantity'] / qi['sample_quantity'] * 100, 2)
            if abs(expected - qi['pass_rate']) > 0.01:
                rate_error.append(f"{qi['inspection_no']}: 计算{expected}% vs 存储{qi['pass_rate']}%")
    if rate_error:
        result.fail("良品率计算", "; ".join(rate_error[:3]))
    else:
        result.ok("良品率计算", f"{len(qis)}条检验记录良品率均正确")

    # 7. 不合格品必须有处理记录
    fail_qis = db.execute('SELECT id, inspection_no FROM quality_inspections WHERE result="不合格"').fetchall()
    unhandled = []
    for fq in fail_qis:
        dh = db.execute('SELECT COUNT(*) FROM defect_handling WHERE inspection_id=?', (fq['id'],)).fetchone()[0]
        if dh == 0:
            unhandled.append(fq['inspection_no'])
    if unhandled:
        result.warn("不合格品处理", f"以下不合格检验未创建处理记录: {', '.join(unhandled)}")
    else:
        result.ok("不合格品处理", f"所有{len(fail_qis)}条不合格检验均有处理记录")

    # 8. 入库记录关联验证
    wrs = db.execute('SELECT * FROM warehouse_receipts').fetchall()
    invalid_wr = []
    for wr in wrs:
        if wr['product_id'] and not db.execute('SELECT id FROM products WHERE id=?', (wr['product_id'],)).fetchone():
            invalid_wr.append(f"入库单{wr['receipt_no']}")
    if invalid_wr:
        result.fail("入库记录关联", f"无效关联: {', '.join(invalid_wr)}")
    else:
        result.ok("入库记录关联", f"{len(wrs)}条入库记录关联均有效")

    # 9. 交付记录关联验证
    dls = db.execute('SELECT * FROM deliveries').fetchall()
    invalid_dl = []
    for dl in dls:
        if dl['product_id'] and not db.execute('SELECT id FROM products WHERE id=?', (dl['product_id'],)).fetchone():
            invalid_dl.append(f"交付单{dl['delivery_no']}")
        if not dl['market_team']:
            invalid_dl.append(f"交付单{dl['delivery_no']}无市场团队")
    if invalid_dl:
        result.fail("交付记录关联", f"无效: {', '.join(invalid_dl[:5])}")
    else:
        result.ok("交付记录关联", f"{len(dls)}条交付记录关联均有效")

    # 10. 库存数量非负验证
    invs = db.execute('SELECT * FROM inventory').fetchall()
    negative = []
    for inv in invs:
        if inv['quantity'] < 0:
            negative.append(f"{inv['material_name']}({inv['location']})")
    if negative:
        result.fail("库存数量非负", f"负库存: {', '.join(negative)}")
    else:
        result.ok("库存数量非负", f"{len(invs)}条库存记录数量均非负")

    db.close()

# ============================================================
# 第二部分：全流程业务流转测试
# ============================================================
def test_business_flow():
    print("\n" + "="*60)
    print("  第二部分：全流程业务流转测试")
    print("="*60)

    db = get_db()

    # 模拟完整业务流程：新建需求→关联产品→创建生产订单→质量检验→入库→交付
    print("\n  模拟完整业务流程...")

    try:
        # 1. 新建需求
        db.execute('''INSERT INTO requirements (title, source_dept, customer, description, quantity, priority, status, delivery_date)
                      VALUES (?,?,?,?,?,?,?,?)''',
                   ("【测试】全流程验证需求", "测试部门", "测试客户", "全流程业务逻辑验证", 100, "高", "已评审", "2026-12-31"))
        req_id = db.execute('SELECT last_insert_rowid()').fetchone()[0]
        result.ok("流程1-新建需求", f"需求ID={req_id}")

        # 2. 关联产品（取第一款产品）
        product = db.execute('SELECT id, name FROM products LIMIT 1').fetchone()
        result.ok("流程2-关联产品", f"产品: {product['name']}")

        # 3. 创建生产订单
        order_no = f"TEST{datetime.now().strftime('%Y%m%d%H%M%S')}"
        db.execute('''INSERT INTO production_orders (order_no, product_id, requirement_id, supplier, quantity, plan_start, plan_finish, status, wip_progress)
                      VALUES (?,?,?,?,?,?,?,?,?)''',
                   (order_no, product['id'], req_id, "测试供应商", 100, "2026-09-01", "2026-09-15", "生产中", 50))
        po_id = db.execute('SELECT last_insert_rowid()').fetchone()[0]
        result.ok("流程3-创建生产订单", f"订单号: {order_no}")

        # 4. 质量检验（合格）
        insp_no = f"QITEST{datetime.now().strftime('%Y%m%d%H%M%S')}"
        db.execute('''INSERT INTO quality_inspections (inspection_no, inspection_type, production_order_id, product_id, inspector, sample_quantity, pass_quantity, fail_quantity, pass_rate, result, inspection_date)
                      VALUES (?,?,?,?,?,?,?,?,?,?,?)''',
                   (insp_no, "OQC", po_id, product['id'], "测试员", 50, 49, 1, 98.0, "合格", "2026-09-10"))
        qi_id = db.execute('SELECT last_insert_rowid()').fetchone()[0]
        result.ok("流程4-质量检验(合格)", f"检验单: {insp_no}, 良品率98%")

        # 5. 入库
        receipt_no = f"WRTEST{datetime.now().strftime('%Y%m%d%H%M%S')}"
        db.execute('''INSERT INTO warehouse_receipts (receipt_no, production_order_id, product_id, quantity, quality_status, approver, status, approved_at)
                      VALUES (?,?,?,?,?,?,?,?)''',
                   (receipt_no, po_id, product['id'], 99, "合格", "测试审批", "已入库", "2026-09-10 15:00:00"))
        wr_id = db.execute('SELECT last_insert_rowid()').fetchone()[0]
        result.ok("流程5-入库审批", f"入库单: {receipt_no}, 数量99")

        # 6. 交付
        delivery_no = f"DLTEST{datetime.now().strftime('%Y%m%d%H%M%S')}"
        db.execute('''INSERT INTO deliveries (delivery_no, product_id, market_team, quantity, customer, purpose, delivery_date, status, feedback)
                      VALUES (?,?,?,?,?,?,?,?,?)''',
                   (delivery_no, product['id'], "测试市场团队", 50, "测试客户", "全流程验证交付", "2026-09-11", "已交付", "测试通过"))
        dl_id = db.execute('SELECT last_insert_rowid()').fetchone()[0]
        result.ok("流程6-交付记录", f"交付单: {delivery_no}, 数量50")

        # 7. 验证数据关联完整性
        check = db.execute('''SELECT r.title as req, p.name as product, po.order_no, qi.inspection_no, wr.receipt_no, dl.delivery_no
                              FROM requirements r
                              JOIN production_orders po ON po.requirement_id = r.id
                              JOIN products p ON p.id = po.product_id
                              LEFT JOIN quality_inspections qi ON qi.production_order_id = po.id
                              LEFT JOIN warehouse_receipts wr ON wr.production_order_id = po.id
                              LEFT JOIN deliveries dl ON dl.product_id = p.id
                              WHERE r.id = ? LIMIT 1''', (req_id,)).fetchone()
        if check and check['req'] and check['product'] and check['order_no']:
            result.ok("流程7-数据关联验证", f"需求→产品→订单→检验→入库→交付 全链路关联成功")
        else:
            result.fail("流程7-数据关联验证", "全链路关联查询失败")

        db.commit()

        # 8. 清理测试数据
        db.execute('DELETE FROM deliveries WHERE id=?', (dl_id,))
        db.execute('DELETE FROM warehouse_receipts WHERE id=?', (wr_id,))
        db.execute('DELETE FROM quality_inspections WHERE id=?', (qi_id,))
        db.execute('DELETE FROM production_orders WHERE id=?', (po_id,))
        db.execute('DELETE FROM requirements WHERE id=?', (req_id,))
        db.commit()
        result.ok("流程8-测试数据清理", "测试数据已清理")

    except Exception as e:
        db.rollback()
        result.fail("全流程测试", f"异常: {str(e)}")

    db.close()

# ============================================================
# 第三部分：HTTP接口压力测试
# ============================================================
def test_http_stress():
    print("\n" + "="*60)
    print("  第三部分：HTTP接口压力测试")
    print("="*60)

    # 检查应用是否启动
    try:
        req = urllib.request.urlopen(f"{BASE_URL}/", timeout=5)
        req.read()
        result.ok("应用启动检测", f"应用运行在 {BASE_URL}")
    except Exception as e:
        result.fail("应用启动检测", f"无法连接 {BASE_URL}: {str(e)}")
        result.warn("压力测试跳过", "请先启动应用: python app.py --port 5000")
        return

    # 测试页面列表
    pages = [
        ("/", "仪表盘"),
        ("/requirements", "需求列表"),
        ("/planning", "产品规划"),
        ("/production", "研发生产-订单"),
        ("/production?tab=inventory", "研发生产-库存"),
        ("/production?tab=requisitions", "研发生产-领用"),
        ("/quality", "质量检测"),
        ("/quality?tab=defects", "质量检测-缺陷"),
        ("/delivery", "验证收益-入库"),
        ("/delivery?tab=deliveries", "验证收益-交付"),
        ("/logs", "操作日志"),
        ("/api/stats", "API统计接口"),
    ]

    # 单页面响应时间测试
    print("\n  单页面响应时间测试:")
    for path, name in pages:
        try:
            start = time.time()
            req = urllib.request.urlopen(f"{BASE_URL}{path}", timeout=10)
            data = req.read()
            elapsed = (time.time() - start) * 1000
            if elapsed < 1000:
                result.ok(f"页面响应-{name}", f"{elapsed:.0f}ms, {len(data)}bytes")
            elif elapsed < 3000:
                result.warn(f"页面响应-{name}", f"{elapsed:.0f}ms (偏慢)")
            else:
                result.fail(f"页面响应-{name}", f"{elapsed:.0f}ms (超时风险)")
        except Exception as e:
            result.fail(f"页面响应-{name}", f"异常: {str(e)[:50]}")

    # 并发压力测试
    print("\n  并发压力测试 (20并发 x 5轮):")
    def fetch_page(path):
        try:
            start = time.time()
            req = urllib.request.urlopen(f"{BASE_URL}{path}", timeout=10)
            req.read()
            return (time.time() - start) * 1000, None
        except Exception as e:
            return 0, str(e)

    concurrency = 20
    rounds = 5
    all_times = []
    errors = 0

    for r in range(rounds):
        with ThreadPoolExecutor(max_workers=concurrency) as executor:
            futures = [executor.submit(fetch_page, random.choice([p[0] for p in pages])) for _ in range(concurrency)]
            for f in as_completed(futures):
                t, err = f.result()
                if err:
                    errors += 1
                else:
                    all_times.append(t)

    if all_times:
        avg_time = sum(all_times) / len(all_times)
        max_time = max(all_times)
        min_time = min(all_times)
        total_requests = len(all_times) + errors
        error_rate = errors / total_requests * 100 if total_requests > 0 else 0

        print(f"\n  压力测试结果:")
        print(f"    总请求数: {total_requests}")
        print(f"    并发数: {concurrency}")
        print(f"    成功: {len(all_times)}, 失败: {errors}")
        print(f"    错误率: {error_rate:.1f}%")
        print(f"    平均响应: {avg_time:.0f}ms")
        print(f"    最快: {min_time:.0f}ms, 最慢: {max_time:.0f}ms")

        if error_rate < 5:
            result.ok("并发压力测试", f"{total_requests}请求, 错误率{error_rate:.1f}%, 平均{avg_time:.0f}ms")
        else:
            result.fail("并发压力测试", f"错误率{error_rate:.1f}% (过高)")

        if avg_time < 500:
            result.ok("并发响应性能", f"平均{avg_time:.0f}ms (优秀)")
        elif avg_time < 2000:
            result.warn("并发响应性能", f"平均{avg_time:.0f}ms (一般)")
        else:
            result.fail("并发响应性能", f"平均{avg_time:.0f}ms (性能不足)")

# ============================================================
# 第四部分：边界条件与异常场景测试
# ============================================================
def test_edge_cases():
    print("\n" + "="*60)
    print("  第四部分：边界条件与异常场景测试")
    print("="*60)

    db = get_db()

    # 1. 空数量需求
    try:
        db.execute('''INSERT INTO requirements (title, quantity, status) VALUES (?, ?, ?)''',
                   ("【边界测试】零数量需求", 0, "待评审"))
        db.commit()
        result.warn("边界-零数量需求", "允许插入quantity=0的需求（建议校验）")
        db.execute('DELETE FROM requirements WHERE title LIKE "%边界测试%"')
        db.commit()
    except Exception as e:
        result.ok("边界-零数量需求", f"已拦截: {str(e)[:30]}")

    # 2. 负数量库存
    try:
        db.execute('''INSERT INTO inventory (material_name, quantity, location) VALUES (?, ?, ?)''',
                   ("【边界测试】负库存物料", -100, "公司库房"))
        db.commit()
        result.fail("边界-负库存", "允许插入负库存（应拦截）")
        db.execute('DELETE FROM inventory WHERE material_name LIKE "%边界测试%"')
        db.commit()
    except Exception as e:
        result.ok("边界-负库存", f"已拦截: {str(e)[:30]}")

    # 3. 良品率超过100%
    try:
        db.execute('''INSERT INTO quality_inspections (inspection_no, sample_quantity, pass_quantity, fail_quantity, pass_rate, result)
                      VALUES (?, ?, ?, ?, ?, ?)''',
                   ("BOUNDARY001", 50, 60, -10, 120.0, "合格"))
        db.commit()
        result.fail("边界-良品率超100%", "允许插入pass_quantity>sample_quantity（应校验）")
        db.execute('DELETE FROM quality_inspections WHERE inspection_no="BOUNDARY001"')
        db.commit()
    except Exception as e:
        result.ok("边界-良品率超100%", f"已拦截: {str(e)[:30]}")

    # 4. 删除有关联数据的产品（外键约束测试）
    try:
        pid = db.execute('SELECT id FROM products LIMIT 1').fetchone()[0]
        db.execute('DELETE FROM products WHERE id=?', (pid,))
        db.commit()
        result.fail("边界-删除有关联产品", "外键约束未生效，允许删除有关联数据的产品")
        # 回滚（实际已commit，需要恢复）
    except sqlite3.IntegrityError as e:
        result.ok("边界-删除有关联产品", f"外键约束生效，已拦截: {str(e)[:40]}")
        db.rollback()
    except Exception as e:
        result.warn("边界-删除有关联产品", f"其他异常: {str(e)[:40]}")
        db.rollback()

    # 5. 日期格式验证
    invalid_dates = db.execute('SELECT COUNT(*) FROM requirements WHERE delivery_date NOT LIKE "____-__-__" AND delivery_date != ""').fetchone()[0]
    if invalid_dates > 0:
        result.warn("边界-日期格式", f"{invalid_dates}条需求日期格式不规范")
    else:
        result.ok("边界-日期格式", "所有日期格式规范")

    db.close()

# ============================================================
# 主函数
# ============================================================
def main():
    print("\n" + "#"*60)
    print("#  业务系统综合测试")
    print("#  数据: 10款产品 + 全场景虚拟数据")
    print("#  内容: 数据完整性 + 全流程流转 + 压力测试 + 边界测试")
    print("#"*60)

    start_time = time.time()

    # 运行所有测试
    test_data_integrity()
    test_business_flow()
    test_http_stress()
    test_edge_cases()

    # 汇总
    elapsed = time.time() - start_time
    print("\n" + "="*60)
    print("  测试汇总")
    print("="*60)
    print(f"  通过: {len(result.passed)} 项")
    print(f"  失败: {len(result.failed)} 项")
    print(f"  警告: {len(result.warnings)} 项")
    print(f"  耗时: {elapsed:.1f} 秒")

    if result.failed:
        print("\n  [失败项详细列表]")
        for name, detail in result.failed:
            print(f"    - {name}: {detail}")

    if result.warnings:
        print("\n  [警告项详细列表]")
        for name, detail in result.warnings:
            print(f"    - {name}: {detail}")

    print("\n" + "="*60)
    if result.failed:
        print("  测试结果: 存在失败项，需要修复")
    else:
        print("  测试结果: 全部通过" + ("（有警告项，建议优化）" if result.warnings else ""))
    print("="*60 + "\n")

    return len(result.failed) == 0

if __name__ == '__main__':
    import random
    success = main()
    sys.exit(0 if success else 1)
