# -*- coding: utf-8 -*-
"""v11.0 平台化改造 全量回归测试（API 层）"""
import requests, json, sys, traceback

BASE = 'http://127.0.0.1:5404'
PASS = 0
FAIL = 0
FAILS = []

def check(name, cond, extra=''):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f'  [PASS] {name}')
    else:
        FAIL += 1
        FAILS.append(name)
        print(f'  [FAIL] {name} {extra}')

def login(user, pwd):
    s = requests.Session()
    r = s.post(f'{BASE}/login', data={'username': user, 'password': pwd}, allow_redirects=False)
    return s, r.status_code

print('=' * 60)
print('1) 登录与页面可达性')
print('=' * 60)
s, code = login('admin', 'admin123')
check('admin 登录', code in (302, 200), f'code={code}')
for path, name in [('/', '聚合视图'), ('/profile', '个人中心'), ('/admin/users', '用户管理'),
                   ('/admin/approval-flows', '审批流配置'), ('/approvals', '审批中心'),
                   ('/admin/external', '外部系统接入'), ('/requirements', '需求列表'),
                   ('/wbs', 'WBS项目')]:
    r = s.get(f'{BASE}{path}')
    check(f'页面可达 {name}', r.status_code == 200, f'{path} -> {r.status_code}')

print('=' * 60)
print('2) 需求收集插件（全新表 req_items）')
print('=' * 60)
# 新增需求
r = s.post(f'{BASE}/requirements/add', data={
    'title': '回归测试需求-平台化', 'req_type': '功能需求', 'source_dept': '测试部',
    'customer': '内部', 'description': 'v11.0回归验证用', 'quantity': '1',
    'priority': '高', 'delivery_date': '2026-12-31', 'owner': 'admin',
})
check('需求新增', r.status_code in (200, 302), f'code={r.status_code}')
r = s.get(f'{BASE}/requirements')
check('需求列表', r.status_code == 200 and '回归测试需求-平台化' in r.text)
# 状态流转（管理员直接生效，动态取最新需求 id）
import sqlite3 as _sq
_db = _sq.connect('/home/user/.doubao/agent_mode/workspace/.sessions/38440335741282818/agents/m_0cwEQHMkwwa/业务系统/app/business.db')
_rid = _db.execute('SELECT id FROM req_items WHERE title LIKE %s ORDER BY id DESC LIMIT 1' % "'回归测试需求%'").fetchone()[0]
_db.close()
r = s.post(f'{BASE}/requirements/{_rid}/status', data={'status': '评审通过'})
body = r.json() if r.headers.get('content-type','').startswith('application/json') else {}
check('管理员状态流转(评审通过)', r.status_code == 200 and body.get('ok', True), f'{body}')

print('=' * 60)
print('3) 审批流配置 + 审批中心（多级审批）')
print('=' * 60)
# 配置需求状态多级审批流：需求负责人 -> 部门经理 -> admin
r = s.post(f'{BASE}/admin/approval-flows/save', data={
    'plugin_id': 'requirements', 'record_type': 'req_status',
    'name': '需求状态变更-两级审批',
    'step_role': ['user', 'admin'],
    'enabled': '1',
})
check('审批流保存', r.status_code == 200 and r.json().get('success'), f'code={r.status_code} {r.text[:120]}')
# 需求字段配置检查
r = s.get(f'{BASE}/requirements/fields')
check('需求字段配置页', r.status_code == 200)

print('=' * 60)
print('4) 外部系统接入管理')
print('=' * 60)
r = s.post(f'{BASE}/admin/external/create', data={
    'name': '测试管理系统', 'plugin_id': 'testing', 'system_type': 'external',
    'access_method': 'api', 'conn_config': '{"base_url":"http://192.168.1.10:8080","token":"demo"}',
    'note': '回归测试接入',
})
check('外部系统登记', r.status_code in (200, 302), f'code={r.status_code}')
r = s.get(f'{BASE}/admin/external')
check('外部系统列表', r.status_code == 200 and '测试管理系统' in r.text)
# 缓存视图
r = s.get(f'{BASE}/admin/external/1/cache')
check('外部缓存视图', r.status_code == 200)

print('=' * 60)
print('5) AI 接口（只读）')
print('=' * 60)
r = s.get(f'{BASE}/api/ai/health')
try:
    h = r.json()
    check('AI health', r.status_code == 200 and 'ollama_available' in h, f'{h}')
except Exception:
    check('AI health', False, r.text[:200])
r = s.get(f'{BASE}/api/ai/alert-summary')
try:
    a = r.json()
    check('AI 预警摘要', r.status_code == 200 and a.get('ok'), f'{a}')
except Exception:
    check('AI 预警摘要', False, r.text[:200])
r = s.post(f'{BASE}/api/ai/ask', json={'question': '当前项目进度如何？'})
try:
    q = r.json()
    check('AI 问答', r.status_code == 200 and q.get('ok') and 'answer' in q, f'{q}')
except Exception:
    check('AI 问答', False, r.text[:200])

print('=' * 60)
print('6) 聚合视图数据完整性')
print('=' * 60)
r = s.get(f'{BASE}/')
check('聚合视图含 WBS KPI', 'WBS' in r.text or 'wbs' in r.text.lower())
check('聚合视图含 需求 KPI', '需求' in r.text)
check('聚合视图含 AI 助手', 'AI 助手' in r.text)

print('=' * 60)
print(f'结果: PASS={PASS} FAIL={FAIL}')
if FAILS:
    print('失败项:', FAILS)
    sys.exit(1)
print('全部通过 ✓')
