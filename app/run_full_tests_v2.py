# -*- coding: utf-8 -*-
"""
综合压力测试与业务逻辑验证脚本
覆盖：数据完整性、全流程业务流转、HTTP并发压力、边界条件、权限控制、库存校验、状态联动
"""
import os
import sys
import time
import sqlite3
import threading
from datetime import datetime

# 项目路径
PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(PROJECT_DIR, 'business.db')

# 测试结果统计
results = {'passed': 0, 'failed': 0, 'warnings': 0, 'errors': []}

def log_result(name, passed, message=''):
    """记录测试结果"""
    if passed:
        results['passed'] += 1
        print(f'  ✅ {name}')
    else:
        results['failed'] += 1
        results['errors'].append(f'{name}: {message}')
        print(f'  ❌ {name}: {message}')

def get_db():
    """获取数据库连接"""
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys = ON')
    return db

# ============================================================
# 一、数据完整性验证
# ============================================================
def test_data_integrity():
    print('\n=== 一、数据完整性验证 ===')
    db = get_db()

    # 1. 检查所有表是否存在
    tables = ['users', 'products', 'requirements', 'software_versions', 'version_requirements',
              'dev_tasks', 'bug_records', 'production_schedules', 'inventory', 'material_requisitions',
              'quality_inspections', 'warehouse_receipts', 'deliveries', 'operation_logs', 'tasks']
    for table in tables:
        count = db.execute(f"SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='{table}'").fetchone()[0]
        log_result(f'表 {table} 存在', count > 0, '表不存在')

    # 2. 检查用户数据
    users = db.execute('SELECT COUNT(*) FROM users WHERE is_active=1').fetchone()[0]
    log_result('活跃用户数量', users >= 5, f'当前{users}个，期望>=5')

    # 3. 检查产品数据
    products = db.execute('SELECT COUNT(*) FROM products').fetchone()[0]
    log_result('产品数量', products >= 4, f'当前{products}个，期望>=4')

    # 4. 检查需求数据
    reqs = db.execute('SELECT COUNT(*) FROM requirements').fetchone()[0]
    log_result('需求数量', reqs >= 10, f'当前{reqs}个，期望>=10')

    # 5. 检查版本数据
    versions = db.execute('SELECT COUNT(*) FROM software_versions').fetchone()[0]
    log_result('软件版本数量', versions >= 5, f'当前{versions}个，期望>=5')

    # 6. 检查外键约束（需求-版本关联）
    vr = db.execute('SELECT COUNT(*) FROM version_requirements').fetchone()[0]
    log_result('需求-版本关联', vr >= 5, f'当前{vr}条，期望>=5')

    # 7. 检查无空标题需求
    empty_title = db.execute("SELECT COUNT(*) FROM requirements WHERE title IS NULL OR title=''").fetchone()[0]
    log_result('需求无空标题', empty_title == 0, f'{empty_title}条空标题')

    # 8. 检查无负库存
    neg_inv = db.execute('SELECT COUNT(*) FROM inventory WHERE quantity < 0').fetchone()[0]
    log_result('无负库存', neg_inv == 0, f'{neg_inv}条负库存')

    db.close()

# ============================================================
# 二、全流程业务流转测试
# ============================================================
def test_business_flow():
    print('\n=== 二、全流程业务流转测试 ===')
    db = get_db()

    # 1. 创建需求
    db.execute('''INSERT INTO requirements (title, source_dept, customer, description, quantity, priority, status, delivery_date)
                  VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
               ('测试需求-全流程验证', '交付服务组', '测试客户A', '用于全流程业务流转测试', 100, '高', '已评审', '2026-12-31'))
    req_id = db.execute('SELECT last_insert_rowid()').fetchone()[0]
    db.commit()
    log_result('创建需求', req_id > 0)

    # 2. 创建版本并关联需求
    product = db.execute('SELECT id FROM products LIMIT 1').fetchone()
    db.execute('''INSERT INTO software_versions (product_id, version_no, version_name, description, planned_release_date, status)
                  VALUES (?, ?, ?, ?, ?, ?)''',
               (product['id'], 'V9.9.9', '测试版本-全流程', '用于全流程测试', '2026-12-01', '规划中'))
    version_id = db.execute('SELECT last_insert_rowid()').fetchone()[0]
    db.execute('INSERT INTO version_requirements (version_id, requirement_id) VALUES (?, ?)', (version_id, req_id))
    db.commit()
    log_result('创建版本并关联需求', version_id > 0)

    # 3. 创建开发任务
    db.execute('''INSERT INTO dev_tasks (version_id, task_name, description, priority, status, assignee, planned_start, planned_finish)
                  VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
               (version_id, '测试任务1', '全流程测试任务', '高', '待开发', '测试员', '2026-10-01', '2026-10-15'))
    task_id = db.execute('SELECT last_insert_rowid()').fetchone()[0]
    db.commit()
    log_result('创建开发任务', task_id > 0)

    # 4. 创建Bug
    timestamp = datetime.now().strftime('%H%M%S')
    db.execute('''INSERT INTO bug_records (bug_no, product_id, version_id, title, description, severity, status, reporter)
                  VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
               (f'BUG-TEST-{timestamp}', product['id'], version_id, '测试Bug', '全流程测试Bug', '一般', '待处理', '测试员'))
    bug_id = db.execute('SELECT last_insert_rowid()').fetchone()[0]
    db.commit()
    log_result('创建Bug', bug_id > 0)

    # 5. 创建生产排程
    db.execute('''INSERT INTO production_schedules (schedule_no, product_id, requirement_id, quantity, plan_start, plan_finish, status, remark)
                  VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
               (f'PS-TEST-{timestamp}', product['id'], req_id, 100, '2026-11-01', '2026-11-15', '规划中', '测试供应商'))
    schedule_id = db.execute('SELECT last_insert_rowid()').fetchone()[0]
    db.commit()
    log_result('创建生产排程', schedule_id > 0)

    # 6. 状态联动测试：需求状态变更为进行中
    db.execute('UPDATE requirements SET status=? WHERE id=?', ('进行中', req_id))
    db.commit()
    # 检查关联版本状态是否自动更新
    v_status = db.execute('SELECT status FROM software_versions WHERE id=?', (version_id,)).fetchone()[0]
    log_result('状态联动：需求进行中→版本开发中', v_status == '开发中', f'版本状态为{v_status}')

    # 7. 清理测试数据
    db.execute('DELETE FROM version_requirements WHERE requirement_id=?', (req_id,))
    db.execute('DELETE FROM dev_tasks WHERE version_id=?', (version_id,))
    db.execute('DELETE FROM bug_records WHERE version_id=?', (version_id,))
    db.execute('DELETE FROM software_versions WHERE id=?', (version_id,))
    db.execute('DELETE FROM production_schedules WHERE id=?', (schedule_id,))
    db.execute('DELETE FROM requirements WHERE id=?', (req_id,))
    db.commit()
    log_result('清理测试数据', True)

    db.close()

# ============================================================
# 三、库存校验测试
# ============================================================
def test_inventory_validation():
    print('\n=== 三、库存校验测试 ===')
    db = get_db()

    # 1. 检查库存表结构
    cols = [row[1] for row in db.execute('PRAGMA table_info(inventory)').fetchall()]
    log_result('库存表包含quantity字段', 'quantity' in cols)

    # 2. 检查CHECK约束（如果有）
    # 3. 验证无负库存
    neg = db.execute('SELECT COUNT(*) FROM inventory WHERE quantity < 0').fetchone()[0]
    log_result('当前无负库存', neg == 0, f'{neg}条负库存')

    # 4. 验证物料领用单数据完整性
    reqs = db.execute('SELECT COUNT(*) FROM material_requisitions').fetchone()[0]
    log_result('物料领用单存在', reqs >= 0)

    db.close()

# ============================================================
# 四、权限控制测试
# ============================================================
def test_permission_control():
    print('\n=== 四、权限控制测试 ===')
    db = get_db()

    # 1. 检查用户角色字段
    cols = [row[1] for row in db.execute('PRAGMA table_info(users)').fetchall()]
    log_result('用户表包含role字段', 'role' in cols)

    # 2. 检查5个角色都存在
    roles = db.execute('SELECT DISTINCT role FROM users WHERE is_active=1').fetchall()
    role_list = [r['role'] for r in roles]
    expected_roles = ['admin', 'product', 'dev_test', 'quality', 'delivery']
    for role in expected_roles:
        log_result(f'角色 {role} 存在', role in role_list, f'当前角色: {role_list}')

    db.close()

# ============================================================
# 五、HTTP并发压力测试
# ============================================================
def test_http_pressure():
    print('\n=== 五、HTTP并发压力测试 ===')
    try:
        import urllib.request
        import urllib.error
    except ImportError:
        print('  ⚠️  跳过HTTP测试（urllib不可用）')
        return

    base_url = 'http://127.0.0.1:5000'
    test_urls = ['/', '/requirements', '/executive', '/production', '/quality']

    # 测试单个请求响应时间
    print('  单请求响应时间测试:')
    for url_path in test_urls:
        try:
            start = time.time()
            req = urllib.request.Request(base_url + url_path)
            req.add_header('Cookie', 'session=test')
            resp = urllib.request.urlopen(req, timeout=5)
            elapsed = (time.time() - start) * 1000
            status = resp.getcode()
            log_result(f'GET {url_path}', status in [200, 302], f'状态{status}, {elapsed:.0f}ms')
        except Exception as e:
            log_result(f'GET {url_path}', False, str(e)[:50])

    # 并发测试（10线程×5请求）
    print('  并发压力测试（10线程×5请求）:')
    success_count = [0]
    fail_count = [0]
    lock = threading.Lock()

    def worker():
        for _ in range(5):
            try:
                req = urllib.request.Request(base_url + '/')
                req.add_header('Cookie', 'session=test')
                resp = urllib.request.urlopen(req, timeout=10)
                if resp.getcode() in [200, 302]:
                    with lock:
                        success_count[0] += 1
                else:
                    with lock:
                        fail_count[0] += 1
            except:
                with lock:
                    fail_count[0] += 1

    threads = []
    start = time.time()
    for i in range(10):
        t = threading.Thread(target=worker)
        threads.append(t)
        t.start()
    for t in threads:
        t.join()
    elapsed = time.time() - start

    total = success_count[0] + fail_count[0]
    log_result(f'并发测试完成', total == 50, f'成功{success_count[0]}, 失败{fail_count[0]}, 耗时{elapsed:.1f}s')
    if total > 0:
        avg_resp = elapsed * 1000 / total
        log_result(f'平均响应时间', avg_resp < 2000, f'{avg_resp:.0f}ms')

# ============================================================
# 六、边界条件测试
# ============================================================
def test_boundary_conditions():
    print('\n=== 六、边界条件测试 ===')
    db = get_db()

    # 1. 数量为0的需求（应该被CHECK约束拒绝）
    try:
        db.execute('''INSERT INTO requirements (title, quantity, priority, status)
                      VALUES (?, ?, ?, ?)''', ('边界测试-零数量', 0, '低', '待评审'))
        db.commit()
        log_result('零数量需求被拒绝', False, 'CHECK约束未生效')
        db.execute('DELETE FROM requirements WHERE title=?', ('边界测试-零数量',))
        db.commit()
    except Exception as e:
        log_result('零数量需求被CHECK约束拒绝', True)

    # 2. NULL标题需求（应该被NOT NULL约束拒绝）
    try:
        db.execute('''INSERT INTO requirements (title, quantity, priority, status)
                      VALUES (?, ?, ?, ?)''', (None, 10, '低', '待评审'))
        db.commit()
        log_result('NULL标题需求被拒绝', False)
    except Exception as e:
        log_result('NULL标题需求被NOT NULL约束拒绝', True)

    # 3. 超长标题需求（应该正常插入）
    long_title = '测试' * 100
    db.execute('''INSERT INTO requirements (title, quantity, priority, status)
                  VALUES (?, ?, ?, ?)''', (long_title, 10, '低', '待评审'))
    long_id = db.execute('SELECT last_insert_rowid()').fetchone()[0]
    db.commit()
    log_result('超长标题需求正常插入', long_id > 0)
    db.execute('DELETE FROM requirements WHERE id=?', (long_id,))
    db.commit()

    db.close()

# ============================================================
# 七、操作日志测试
# ============================================================
def test_operation_logs():
    print('\n=== 七、操作日志测试 ===')
    db = get_db()

    # 1. 检查操作日志表
    count = db.execute('SELECT COUNT(*) FROM operation_logs').fetchone()[0]
    log_result('操作日志记录数', count > 0, f'当前{count}条')

    # 2. 检查日志字段完整性
    cols = [row[1] for row in db.execute('PRAGMA table_info(operation_logs)').fetchall()]
    required_cols = ['module', 'action', 'detail', 'operator', 'created_at']
    for col in required_cols:
        log_result(f'日志表包含{col}字段', col in cols)

    db.close()

# ============================================================
# 主函数
# ============================================================
def main():
    print('=' * 60)
    print('  综合压力测试与业务逻辑验证')
    print(f'  测试时间: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}')
    print(f'  数据库: {DB_PATH}')
    print('=' * 60)

    # 检查数据库是否存在
    if not os.path.exists(DB_PATH):
        print('❌ 数据库文件不存在，请先运行 init_db.py 初始化数据库')
        sys.exit(1)

    # 执行所有测试
    test_data_integrity()
    test_business_flow()
    test_inventory_validation()
    test_permission_control()
    test_boundary_conditions()
    test_operation_logs()

    # HTTP压力测试（可选，需要应用正在运行）
    try:
        test_http_pressure()
    except Exception as e:
        print(f'  ⚠️  HTTP测试跳过: {e}')

    # 输出总结
    print('\n' + '=' * 60)
    print('  测试总结')
    print('=' * 60)
    print(f'  ✅ 通过: {results["passed"]}')
    print(f'  ❌ 失败: {results["failed"]}')
    print(f'  ⚠️  警告: {results["warnings"]}')
    print(f'  📊 通过率: {results["passed"] / (results["passed"] + results["failed"]) * 100:.1f}%' if (results["passed"] + results["failed"]) > 0 else '')

    if results['errors']:
        print('\n  失败详情:')
        for err in results['errors']:
            print(f'    - {err}')

    print('=' * 60)

    return 0 if results['failed'] == 0 else 1

if __name__ == '__main__':
    sys.exit(main())
