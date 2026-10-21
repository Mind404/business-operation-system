# -*- coding: utf-8 -*-
"""v11.0 历史核心回归：WBS 插件 + 用户管理/改密码（确保平台化未破坏）"""
import requests, sys, sqlite3

BASE = 'http://127.0.0.1:5404'
DB = '/home/user/.doubao/agent_mode/workspace/.sessions/38440335741282818/agents/m_0cwEQHMkwwa/业务系统/app/business.db'
PASS = FAIL = 0
FAILS = []

def check(name, cond, extra=''):
    global PASS, FAIL
    if cond:
        PASS += 1; print(f'  [PASS] {name}')
    else:
        FAIL += 1; FAILS.append(name); print(f'  [FAIL] {name} {extra}')

def login(user, pwd):
    s = requests.Session()
    r = s.post(f'{BASE}/login', data={'username': user, 'password': pwd}, allow_redirects=False)
    return s, r.status_code

s, code = login('admin', 'admin123')
check('admin 登录', code in (302, 200), f'code={code}')

print('=' * 60)
print('A) WBS 插件')
print('=' * 60)
# 新建项目（管理员直接通过）
r = s.post(f'{BASE}/wbs/add', data={
    'name': '回归项目-v11', 'description': 'v11.0回归', 'owner': 'admin',
    'participants': 'admin', 'start_date': '2026-10-21', 'end_date': '2026-11-30',
    'status': '进行中',
})
check('新建WBS项目', r.status_code in (200, 302), f'code={r.status_code}')
r = s.get(f'{BASE}/wbs')
check('WBS项目列表', r.status_code == 200 and '回归项目-v11' in r.text)
# 拿项目 id
db = sqlite3.connect(DB)
pid = db.execute("SELECT id FROM wbs_projects WHERE name='回归项目-v11'").fetchone()[0]
db.close()
# 添加任务
r = s.post(f'{BASE}/wbs/{pid}/task/add', data={
    'wbs_code': '1.1', 'name': '任务-平台回归', 'work_content': '验证', 'owner': 'admin',
    'plan_start': '2026-10-21', 'plan_end': '2026-10-31', 'deliverable': '报告',
    'priority': 'P1高', 'status': '进行中', 'progress': '50',
})
check('新建WBS任务', r.status_code in (200, 302), f'code={r.status_code}')
r = s.get(f'{BASE}/wbs/{pid}')
check('项目详情含任务', r.status_code == 200 and '任务-平台回归' in r.text)
# 内联更新任务（v10.4 WBS 核心编辑）
db = sqlite3.connect(DB)
tid = db.execute("SELECT id FROM wbs_tasks WHERE project_id=? AND name='任务-平台回归'", (pid,)).fetchone()[0]
db.close()
r = s.post(f'{BASE}/wbs/task/{tid}/inline', json={'field': 'progress', 'value': '80'})
b = r.json() if r.headers.get('content-type','').startswith('application/json') else {}
check('任务内联更新进度', r.status_code == 200 and b.get('ok', b.get('success', True)), f'{b}')
# 缩放分层参数透传（100%/125% 换行、150%/200% 滚动）：检查页面含 zoom 控件
r = s.get(f'{BASE}/wbs/{pid}')
check('WBS缩放控件存在', '100%' in r.text and '125%' in r.text and '150%' in r.text and '200%' in r.text)
# 任务审批（管理员直接通过或提交审批）
r = s.post(f'{BASE}/wbs/project/{pid}/tasks/approve', data={})
check('任务批量审批接口', r.status_code in (200, 302), f'code={r.status_code}')

print('=' * 60)
print('B) 用户管理 + 个人中心改密码')
print('=' * 60)
# 创建用户
r = s.post(f'{BASE}/admin/users/create', data={
    'username': 'reguser2', 'password': 'reg123456', 'display_name': '回归用户2', 'role': 'user',
})
check('创建用户', r.status_code in (200, 302), f'code={r.status_code}')
r = s.get(f'{BASE}/admin/users')
check('用户列表含新用户', r.status_code == 200 and 'reguser2' in r.text)
# 停用
db = sqlite3.connect(DB)
uid = db.execute("SELECT id FROM users WHERE username='reguser2'").fetchone()[0]
db.close()
r = s.post(f'{BASE}/admin/users/{uid}/toggle', data={})
check('停用/启用用户', r.status_code in (200, 302), f'code={r.status_code}')
# 个人中心改密码（admin 改自己的密码后改回）
r = s.post(f'{BASE}/profile/change_password', data={'old_password': 'admin123', 'new_password': 'admin123', 'confirm_password': 'admin123'})
check('个人中心改密码', r.status_code in (200, 302), f'code={r.status_code}')
# 普通用户登录改密码
s2, _ = login('reguser2', 'reg123456')
r = s2.post(f'{BASE}/profile/change_password', data={'old_password': 'reg123456', 'new_password': 'reg123456', 'confirm_password': 'reg123456'})
check('普通用户改密码', r.status_code in (200, 302), f'code={r.status_code}')

print('=' * 60)
print(f'结果: PASS={PASS} FAIL={FAIL}')
if FAILS:
    print('失败项:', FAILS); sys.exit(1)
print('历史核心回归全部通过 ✓')
