#!/usr/bin/env python3
"""综合业务逻辑测试脚本 - 验证4款产品测试数据下所有功能"""
import requests
import sys

BASE = 'http://127.0.0.1:5001'
s = requests.Session()

def test(name, method, url, data=None, expected=200):
    try:
        if method == 'GET':
            r = s.get(url, allow_redirects=False)
        else:
            r = s.post(url, data=data, allow_redirects=False)
        status = r.status_code
        ok = status == expected or (expected == 302 and status in [301, 302])
        result = 'PASS' if ok else 'FAIL'
        print(f'[{result}] {name}: HTTP {status} (expected {expected})')
        if not ok:
            print(f'       URL: {url}')
            print(f'       Response: {r.text[:200]}')
        return ok
    except Exception as e:
        print(f'[FAIL] {name}: Exception - {e}')
        return False

print('='*60)
print('  综合业务逻辑测试 - 4款产品测试数据验证')
print('='*60)

passed = 0
failed = 0

# 1. 登录测试
print('\n--- 1. 登录认证 ---')
if test('登录页面', 'GET', f'{BASE}/login'):
    passed += 1
else:
    failed += 1

if test('管理员登录', 'POST', f'{BASE}/login', 
        {'username': 'admin', 'password': 'admin123'}, expected=302):
    passed += 1
else:
    failed += 1

# 2. 首页和角色化首页
print('\n--- 2. 首页与角色化 ---')
if test('首页(管理组)', 'GET', f'{BASE}/'):
    passed += 1
else:
    failed += 1

# 测试5个大组别登录
roles = [
    ('product', 'product123', '产品组'),
    ('dev_test', 'devtest123', '研发测试组'),
    ('quality', 'quality123', '质量组'),
    ('delivery', 'delivery123', '交付服务组'),
]
for username, password, role_name in roles:
    s2 = requests.Session()
    s2.post(f'{BASE}/login', data={'username': username, 'password': password})
    r = s2.get(f'{BASE}/')
    ok = r.status_code == 200
    print(f'[{"PASS" if ok else "FAIL"}] {role_name}首页: HTTP {r.status_code}')
    if ok: passed += 1
    else: failed += 1

# 重新用admin登录
s = requests.Session()
s.post(f'{BASE}/login', data={'username': 'admin', 'password': 'admin123'})

# 3. 需求数据库模块
print('\n--- 3. 需求数据库 ---')
pages = [
    ('需求列表', '/requirements'),
    ('需求新增', '/requirements/add'),
    ('需求编辑#1', '/requirements/1/edit'),
]
for name, path in pages:
    if test(name, 'GET', f'{BASE}{path}'):
        passed += 1
    else:
        failed += 1

# 4. 产品规划模块
print('\n--- 4. 产品规划(反向规划) ---')
pages = [
    ('软件版本列表', '/planning/versions'),
    ('软件版本新增', '/planning/versions/add'),
    ('版本详情#1', '/planning/versions/1'),
    ('Bug列表', '/bugs'),
    ('Bug新增', '/bugs/add'),
    ('Bug详情#1', '/bugs/1'),
    ('生产排程列表', '/production/schedules'),
    ('生产排程新增', '/production/schedules/add'),
    ('排程详情#1', '/production/schedules/1'),
]
for name, path in pages:
    if test(name, 'GET', f'{BASE}{path}'):
        passed += 1
    else:
        failed += 1

# 5. 研发生产模块
print('\n--- 5. 研发生产 ---')
pages = [
    ('生产订单(含库存/物料/BOM Tab)', '/production'),
    ('生产订单新增', '/production/order/add'),
    ('物料领用新增', '/production/requisition/add'),
    ('库存新增', '/production/inventory/add'),
]
for name, path in pages:
    if test(name, 'GET', f'{BASE}{path}'):
        passed += 1
    else:
        failed += 1

# 6. 质量检测模块
print('\n--- 6. 质量检测 ---')
pages = [
    ('质量检测(含不合格品处理)', '/quality'),
    ('质量检测新增', '/quality/add'),
]
for name, path in pages:
    if test(name, 'GET', f'{BASE}{path}'):
        passed += 1
    else:
        failed += 1

# 7. 验证收益模块
print('\n--- 7. 验证收益 ---')
pages = [
    ('交付管理(含入库/交付)', '/delivery'),
    ('入库新增', '/delivery/receipt/add'),
    ('交付新增', '/delivery/deliver/add'),
]
for name, path in pages:
    if test(name, 'GET', f'{BASE}{path}'):
        passed += 1
    else:
        failed += 1

# 8. 扩展模块
print('\n--- 8. 扩展模块(部门组织架构) ---')
ext_pages = [
    ('测试用例库', '/test/cases'),
    ('测试执行', '/test/executions'),
    ('技术支持工单', '/support/tickets'),
    ('用户反馈库', '/feedback'),
    ('交付计划', '/delivery/plans'),
    ('收入确认', '/revenue'),
    ('技术规格', '/tech-specs'),
    ('合同管理', '/contracts'),
    ('资源调度', '/resources'),
    ('设备台账', '/devices'),
    ('OTA版本', '/ota'),
    ('我的任务', '/my-tasks'),
    ('新建任务', '/my-tasks/add'),
]
for name, path in ext_pages:
    if test(name, 'GET', f'{BASE}{path}'):
        passed += 1
    else:
        failed += 1

# 9. 领导看板
print('\n--- 9. 领导看板 ---')
if test('领导看板', 'GET', f'{BASE}/executive'):
    passed += 1
else:
    failed += 1

# 10. 数据管理
print('\n--- 10. 数据管理 ---')
pages = [
    ('数据管理首页', '/data'),
    ('数据字典', '/data/dictionary'),
]
for name, path in pages:
    if test(name, 'GET', f'{BASE}{path}'):
        passed += 1
    else:
        failed += 1

# 11. 业务逻辑测试
print('\n--- 11. 业务逻辑验证 ---')

# 测试需求状态快速更新
r = s.post(f'{BASE}/requirements/1/status', data={'status': '进行中'}, allow_redirects=False)
ok = r.status_code in [200, 302]
print(f'[{"PASS" if ok else "FAIL"}] 需求状态快速更新: HTTP {r.status_code}')
if ok: passed += 1
else: failed += 1

# 测试Bug更新(关闭)
r = s.post(f'{BASE}/bugs/1/update', data={
    'status': '已关闭',
    'root_cause': '测试根因',
    'corrective_action': '测试纠正措施',
    'prevention_measure': '测试预防措施'
}, allow_redirects=False)
ok = r.status_code in [200, 302]
print(f'[{"PASS" if ok else "FAIL"}] Bug关闭(闭环): HTTP {r.status_code}')
if ok: passed += 1
else: failed += 1

# 12. 数据完整性验证
print('\n--- 12. 数据完整性验证 ---')
import sqlite3
conn = sqlite3.connect('business.db')
c = conn.cursor()

# 验证4款产品
count = c.execute('SELECT COUNT(*) FROM products').fetchone()[0]
ok = count == 4
print(f'[{"PASS" if ok else "FAIL"}] 产品数量: {count} (expected 4)')
if ok: passed += 1
else: failed += 1

# 验证需求数据
count = c.execute('SELECT COUNT(*) FROM requirements').fetchone()[0]
ok = count >= 20
print(f'[{"PASS" if ok else "FAIL"}] 需求数量: {count} (expected >=20)')
if ok: passed += 1
else: failed += 1

# 验证版本数据
count = c.execute('SELECT COUNT(*) FROM software_versions').fetchone()[0]
ok = count >= 8
print(f'[{"PASS" if ok else "FAIL"}] 软件版本数量: {count} (expected >=8)')
if ok: passed += 1
else: failed += 1

# 验证Bug数据
count = c.execute('SELECT COUNT(*) FROM bug_records').fetchone()[0]
ok = count >= 10
print(f'[{"PASS" if ok else "FAIL"}] Bug记录数量: {count} (expected >=10)')
if ok: passed += 1
else: failed += 1

# 验证外键关联完整性
tables_fk = [
    ('requirements', 'product_id', 'products'),
    ('software_versions', 'product_id', 'products'),
    ('dev_tasks', 'version_id', 'software_versions'),
    ('bug_records', 'product_id', 'products'),
    ('production_schedules', 'product_id', 'products'),
    ('test_cases', 'product_id', 'products'),
    ('tech_support_tickets', 'product_id', 'products'),
    ('user_feedbacks', 'product_id', 'products'),
    ('delivery_plans', 'product_id', 'products'),
    ('revenue_confirmations', 'product_id', 'products'),
    ('tech_specs', 'product_id', 'products'),
    ('devices', 'product_id', 'products'),
]
fk_ok = 0
fk_fail = 0
for table, fk_col, ref_table in tables_fk:
    try:
        result = c.execute(f'''
            SELECT COUNT(*) FROM {table} t 
            WHERE t.{fk_col} IS NOT NULL 
            AND NOT EXISTS (SELECT 1 FROM {ref_table} r WHERE r.id = t.{fk_col})
        ''').fetchone()[0]
        if result == 0:
            fk_ok += 1
        else:
            print(f'  [WARN] {table}.{fk_col} 有 {result} 条无效外键')
            fk_fail += 1
    except Exception as e:
        print(f'  [SKIP] {table}: {e}')

print(f'[{"PASS" if fk_fail == 0 else "FAIL"}] 外键关联完整性: {fk_ok}通过, {fk_fail}失败')
if fk_fail == 0: passed += 1
else: failed += 1

conn.close()

# 总结
print('\n' + '='*60)
print(f'  测试总结: {passed} 通过, {failed} 失败, 共 {passed+failed} 项')
print('='*60)

if failed > 0:
    print('\n⚠️  存在失败项，请查看上方详情')
    sys.exit(1)
else:
    print('\n✅ 所有测试通过！')
    sys.exit(0)
