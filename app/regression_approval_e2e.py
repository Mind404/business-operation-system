# -*- coding: utf-8 -*-
"""v11.0 多级审批流端到端测试：普通用户提交 → 普通用户(需求负责人)审第1级 → admin 终审 → 需求状态落地
平台角色体系：admin(管理员) / user(普通用户)；审批流步骤角色只能取 admin/user"""
import requests, re, sys, sqlite3

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

def req_status(rid):
    db = sqlite3.connect(DB)
    st = db.execute('SELECT status FROM req_items WHERE id=?', (rid,)).fetchone()
    db.close()
    return st[0] if st else None

print('=' * 60)
print('步骤0: 管理员创建测试用户 requser(普通用户)')
print('=' * 60)
s, _ = login('admin', 'admin123')
# 创建本轮专用测试需求（动态 id，避免历史 id 残留）
import sqlite3 as _sq
_db = _sq.connect(DB)
_db.execute("UPDATE req_items SET is_deleted=1 WHERE title='E2E-审批测试需求'")
_db.commit(); _db.close()
r = s.post(f'{BASE}/requirements/add', data={
    'title': 'E2E-审批测试需求', 'req_type': '功能需求', 'source_dept': '测试部',
    'customer': '内部', 'description': 'e2e', 'quantity': '1',
    'priority': '高', 'delivery_date': '2026-12-31', 'owner': 'admin'})
_db = _sq.connect(DB)
RID = _db.execute("SELECT id FROM req_items WHERE title='E2E-审批测试需求' AND is_deleted=0 ORDER BY id DESC LIMIT 1").fetchone()[0]
_db.close()
r = s.post(f'{BASE}/admin/users/create', data={
    'username': 'requser', 'password': 'req123456', 'display_name': '需求测试员', 'role': 'user',
})
check('创建 requser', r.status_code in (200, 302), f'code={r.status_code}')

print('=' * 60)
print('步骤1: requser 提交需求1(待评审)状态变更 → 评审通过（走审批流）')
print('=' * 60)
s2, _ = login('requser', 'req123456')
check('requser 登录', s2 is not None)
before_status = req_status(RID)
print(f'  提交前需求状态 = {before_status}')
r = s2.post(f'{BASE}/requirements/{RID}/status', data={'status': '评审通过'})
try:
    body = r.json()
    check('提交进入审批流', body.get('pending') is True, f'{body}')
    m = re.search(r'#(\d+)', body.get('msg', ''))
    aid = int(m.group(1)) if m else None
    check('拿到审批单号', aid is not None, f'{body}')
except Exception:
    aid = None
    check('提交进入审批流', False, r.text[:300])
check('提交后需求状态未变', req_status(RID) == before_status, f'实际={req_status(RID)}')

print('=' * 60)
print('步骤2: requser 视角审批中心（第1级=user，普通用户可审）')
print('=' * 60)
r = s2.get(f'{BASE}/approvals')
check('requser 审批中心可见待审批', r.status_code == 200 and '待审批' in r.text, '')

print('=' * 60)
print('步骤3: 第1级审批——requser(普通用户) 通过')
print('=' * 60)
if aid:
    r = s2.post(f'{BASE}/approvals/{aid}/review', data={'approve': '1', 'comment': '需求负责人审核通过'})
    b = r.json()
    check('user 级审批通过', r.status_code == 200 and b.get('success'), f'{b}')
    check('未完成(还有admin终审)', b.get('completed') is False, f'{b}')
check('第1级通过后需求仍未落地', req_status(RID) == before_status, f'实际={req_status(RID)}')

print('=' * 60)
print('步骤4: admin 终审通过 → 需求状态落地为评审通过')
print('=' * 60)
if aid:
    r = s.post(f'{BASE}/approvals/{aid}/review', data={'approve': '1', 'comment': '管理员终审通过'})
    b = r.json()
    check('admin 终审通过', r.status_code == 200 and b.get('success'), f'{b}')
    check('审批流完成', b.get('completed') is True, f'{b}')
check('需求状态已落地为评审通过', req_status(RID) == '评审通过', f'实际={req_status(RID)}')

print('=' * 60)
print('步骤5: 驳回链路——提交流转到已采纳，user 级驳回即终止')
print('=' * 60)
r = s2.post(f'{BASE}/requirements/{RID}/status', data={'status': '已采纳'})
try:
    body = r.json()
    m2 = re.search(r'#(\d+)', body.get('msg', ''))
    aid2 = int(m2.group(1)) if m2 else None
    check('再次提交进入审批', body.get('pending') is True, f'{body}')
except Exception:
    aid2 = None
    check('再次提交进入审批', False, r.text[:300])
if aid2:
    r = s2.post(f'{BASE}/approvals/{aid2}/review', data={'approve': '0', 'comment': '驳回'})
    b = r.json()
    check('user 级驳回成功', r.status_code == 200 and b.get('success'), f'{b}')
    db = sqlite3.connect(DB)
    st = db.execute('SELECT status FROM approval_requests WHERE id=?', (aid2,)).fetchone()[0]
    db.close()
    check('驳回后审批单终止(已驳回)', st == '已驳回', f'实际={st}')
check('驳回后需求状态未变(仍评审通过)', req_status(RID) == '评审通过', f'实际={req_status(RID)}')

print('=' * 60)
print(f'结果: PASS={PASS} FAIL={FAIL}')
if FAILS:
    print('失败项:', FAILS); sys.exit(1)
print('多级审批端到端全部通过 ✓')
