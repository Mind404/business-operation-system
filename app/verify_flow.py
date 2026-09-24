# -*- coding: utf-8 -*-
"""业务逻辑动态验证脚本：模拟完整业务流（正常/异常/权限），输出 PASS/FAIL"""
import sys, io, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
import sqlite3
from app import app

app.config['TESTING'] = True
client = app.test_client()

results = []
def record(name, ok, detail=''):
    results.append((name, ok, detail))
    print(f"[{'✅' if ok else '❌'}] {name} {detail}")

DB = 'business.db'
def q(sql, args=()):
    db = sqlite3.connect(DB); db.row_factory = sqlite3.Row
    r = db.execute(sql, args).fetchall(); db.close()
    return r

def login(username, password):
    return client.post('/login', data={'username': username, 'password': password}, follow_redirects=True)

print("========== 登录 ==========")
r = login('admin', 'admin123')
record('admin 登录', r.status_code == 200)
client.get('/')  # 触发会话

print("\n========== 场景A：完整正常业务流 ==========")
# A1 新增需求（用户需求）
r = client.post('/requirements/add', data={
    'title': '【流程验证】测试用户-智能路灯控制模组需求', 'req_type': '用户需求',
    'source_dept': '交付服务组A', 'customer': '测试用户', 'description': '流程验证用虚拟需求',
    'quantity': 300, 'priority': '高', 'delivery_date': '2026-11-15',
    'product_model': 'SAM302', 'batch_delivery': '否', 'application_scenario': '测试场景'})
req_row = q("SELECT id FROM requirements WHERE title LIKE '%流程验证%' ORDER BY id DESC LIMIT 1")
record('A1 新增需求', r.status_code in (200, 302) and len(req_row) == 1, f"id={req_row[0]['id'] if req_row else '无'}")
rid = req_row[0]['id'] if req_row else None

# A2 评审通过 → 需求状态联动
r = client.post(f'/requirements/{rid}/reviews/add', data={
    'reviewer': 'product_b', 'conclusion': '通过', 'opinion': '同意',
    'priority_suggested': '高', 'version_suggested': 'V1.6.0'})
st = q("SELECT status FROM requirements WHERE id=?", (rid,))[0]['status']
record('A2 评审通过→需求已评审', st == '已评审', f"需求状态={st}")

# A3 新增版本并关联需求
r = client.post('/planning/versions/add', data={
    'product_id': '1', 'version_no': 'V1.6.0', 'version_name': '流程验证版本',
    'description': '验证用', 'capabilities': '测试能力', 'planned_release_date': '2026-10-30',
    'status': '规划中', 'requirement_ids': [str(rid)]})
vid = q("SELECT id FROM software_versions WHERE version_no='V1.6.0' ORDER BY id DESC LIMIT 1")
link = q("SELECT COUNT(*) c FROM version_requirements WHERE version_id=? AND requirement_id=?", (vid[0]['id'], rid))[0]['c'] if vid else 0
record('A3 新增版本+关联需求', r.status_code in (200, 302) and link == 1, f"version_id={vid[0]['id'] if vid else '无'}, 关联={link}")
vid_id = vid[0]['id'] if vid else None

# A4 版本任务新增（关联需求）
r = client.post(f'/planning/versions/{vid_id}/tasks/add', data={
    'task_name': 'V1.6.0测试功能开发', 'description': '开发任务', 'priority': '高',
    'assignee': '研发组', 'planned_start': '2026-09-25', 'planned_finish': '2026-10-15',
    'requirement_id': str(rid)})
t = q("SELECT id, requirement_id FROM dev_tasks WHERE version_id=? ORDER BY id DESC LIMIT 1", (vid_id,))
record('A4 任务新增（含需求关联）', t and t[0]['requirement_id'] == rid, f"req关联={t[0]['requirement_id'] if t else '无'}")
tid = t[0]['id'] if t else None

# A5 任务状态流转
r = client.post(f'/planning/versions/{vid_id}/tasks/{tid}/status', data={'status': '已完成'})
js = r.get_json() if r.is_json else {}
record('A5 任务状态→已完成', r.status_code == 200 and js.get('status') == '已完成', f"resp={js}")

# A6 生产排程（关联需求+版本）→ 自动生成物料计划+工序
# 前置：空库时补一条 BOM（真实场景由数据管理导入），保证物料计划可生成
if q("SELECT COUNT(*) c FROM bom_items WHERE product_id=1")[0]['c'] == 0:
    db2 = sqlite3.connect(DB)
    db2.execute("INSERT INTO bom_items (product_id, material_name, quantity_per_unit, unit, supplier, is_key_material) VALUES (?,?,?,?,?,?)",
                (1, '主控芯片', 1, '颗', '验证供应商', 1))
    db2.commit(); db2.close()
r = client.post('/production/schedules/add', data={
    'product_id': '1', 'requirement_id': str(rid), 'version_id': str(vid_id),
    'quantity': '300', 'plan_start': '2026-10-01', 'plan_finish': '2026-10-20', 'remark': '验证排程'})
sched = q("SELECT id FROM production_schedules WHERE requirement_id=? ORDER BY id DESC LIMIT 1", (rid,))
mats = q("SELECT COUNT(*) c FROM material_plans WHERE schedule_id=?", (sched[0]['id'],))[0]['c'] if sched else 0
procs = q("SELECT COUNT(*) c FROM production_processes WHERE schedule_id=?", (sched[0]['id'],))[0]['c'] if sched else 0
flash = q("SELECT flash_version_no FROM production_schedules WHERE id=?", (sched[0]['id'],))[0]['flash_version_no'] if sched else None
record('A6 排程生成', bool(sched) and mats > 0 and procs > 0, f"物料计划={mats}, 工序={procs}, 烧录版本={flash}")
sid = sched[0]['id'] if sched else None

# A7 工序推进 → 排程状态是否联动
proc = q("SELECT id FROM production_processes WHERE schedule_id=? AND sequence=1", (sid,))
r = client.post(f'/production/schedules/{sid}/processes/{proc[0]["id"]}/status', data={'status': '已完成'})
sched_st = q("SELECT status FROM production_schedules WHERE id=?", (sid,))[0]['status']
record('A7 首工序完成', r.status_code == 200, f"排程状态={sched_st}（观察是否联动）")

# A8 测试执行新增
r = client.post('/test/executions/add', data={
    'product_id': '1', 'version_id': str(vid_id), 'round_no': '1', 'executor': '测试组'})
te = q("SELECT id FROM test_executions WHERE version_id=? ORDER BY id DESC LIMIT 1", (vid_id,))
record('A8 测试执行新增', r.status_code in (200, 302) and bool(te), f"execution_id={te[0]['id'] if te else '无'}")
teid = te[0]['id'] if te else None

# A9 测试用例结果
if q("SELECT COUNT(*) c FROM test_cases WHERE product_id=1")[0]['c'] == 0:
    db2 = sqlite3.connect(DB)
    db2.execute("INSERT INTO test_cases (case_no, title, product_id, module, precondition, test_steps, expected_result, status, creator) VALUES (?,?,?,?,?,?,?,?,?)",
                ('TC-V880-001', '流程验证用例-基础功能', 1, '功能', '无', '执行基本流程', '流程正常', '已启用', '测试组'))
    db2.commit(); db2.close()
tc = q("SELECT id FROM test_cases WHERE product_id=1 AND status='已启用' LIMIT 1")
if tc and teid:
    r = client.post(f'/test/executions/{teid}/result', data={'case_id': str(tc[0]['id']), 'result': '通过'})
    js = r.get_json() if r.is_json else {}
    record('A9 用例结果提交', r.status_code == 200 and js.get('pass_rate') is not None, f"pass_rate={js.get('pass_rate')}")
else:
    record('A9 用例结果提交', False, '无可用用例')

# A10 Bug 新增（关联版本）→ bug_workflow 生成
r = client.post('/bugs/add', data={
    'product_id': '1', 'version_id': str(vid_id), 'title': '验证Bug-测试项失败',
    'description': '测试描述', 'bug_type': '功能Bug', 'severity': '中', 'priority': '高',
    'reporter': '测试组', 'assignee': '研发组', 'discovered_at': '2026-09-24'})
bg = q("SELECT id FROM bug_records WHERE version_id=? ORDER BY id DESC LIMIT 1", (vid_id,))
wf = q("SELECT COUNT(*) c FROM bug_workflow WHERE bug_id=?", (bg[0]['id'],))[0]['c'] if bg else 0
record('A10 Bug新增+流转记录', bool(bg) and wf >= 1, f"bug_id={bg[0]['id'] if bg else '无'}, 流转记录={wf}")
bid = bg[0]['id'] if bg else None

# A11 Bug 流转到已关闭
r = client.post(f'/bugs/{bid}/update', data={'status': '已关闭', 'comment': '验证修复'})
record('A11 Bug状态→已关闭', r.status_code in (200, 302), f"resp={r.get_json() if r.is_json else r.status_code}")

# A12 交付计划新增 —— 验证 requirement_id 关联（预期卡点）
r = client.post('/delivery/plans/add', data={
    'customer': '测试用户', 'product_id': '1', 'quantity': '300',
    'planned_date': '2026-11-15', 'owner': 'delivery_a', 'remark': '验证交付',
    'requirement_id': str(rid)})
dp = q("SELECT id, requirement_id FROM delivery_plans WHERE customer='测试用户' ORDER BY id DESC LIMIT 1")
record('A12 交付计划新增（需求关联）', bool(dp) and dp[0]['requirement_id'] == rid,
       f"requirement_id={dp[0]['requirement_id'] if dp else '无'}（空=卡点：表单未关联需求）")

# A13 收入确认新增
r = client.post('/revenue/add', data={
    'customer': '测试用户', 'contract_no': 'HT-VERIFY-001', 'product_id': '1',
    'amount': '300000', 'period': '2026-11', 'handler': 'delivery_a', 'remark': '验证'})
rc = q("SELECT id FROM revenue_confirmations WHERE customer='测试用户' ORDER BY id DESC LIMIT 1")
record('A13 收入确认新增', r.status_code in (200, 302) and bool(rc), f"id={rc[0]['id'] if rc else '无'}")

# A14 需求状态→已交付 → 版本联动已发布
r = client.post(f'/requirements/{rid}/status', data={'status': '已交付'})
js = r.get_json() if r.is_json else {}
ver_st = q("SELECT status FROM software_versions WHERE id=?", (vid_id,))[0]['status']
record('A14 需求交付→版本联动发布', js.get('status') == '已交付' and ver_st == '已发布', f"版本状态={ver_st}")

# A15 需求状态→进行中 → 版本联动开发中（反向测试，先重置）
r = client.post(f'/requirements/{rid}/status', data={'status': '进行中'})
ver_st = q("SELECT status FROM software_versions WHERE id=?", (vid_id,))[0]['status']
record('A15 已发布版本防回退（终态保护）', ver_st == '已发布', f"版本状态={ver_st}（已发布不受需求回退影响，符合设计）")

print("\n========== 场景B：异常/边界/权限 ==========")
# B1 空标题需求
r = client.post('/requirements/add', data={'title': ' ', 'quantity': '10'})
txt = r.get_data(as_text=True)
record('B1 空标题拦截', '需求标题不能为空' in txt, f"status={r.status_code}, 含错误提示={'需求标题不能为空' in txt}")

# B2 非法任务状态
r = client.post(f'/planning/versions/{vid_id}/tasks/{tid}/status', data={'status': '乱写'})
record('B2 非法任务状态拦截', r.status_code == 400)

# B3 非法 Bug 状态
r = client.post(f'/bugs/{bid}/update', data={'status': '乱写'})
record('B3 非法Bug状态拦截', r.status_code in (400, 200) and (r.get_json() or {}).get('success') is not True)

# B4 权限：quality 用户写生产排程（应只读拦截）
login('quality', '123456') if q("SELECT COUNT(*) c FROM users WHERE username='quality'") else None
# 用真实密码
import itertools
ok_login = False
for pw in ['123456', 'quality', 'quality123']:
    rr = client.post('/login', data={'username': 'quality', 'password': pw}, follow_redirects=True)
    if rr.status_code == 200 and '退出' in rr.get_data(as_text=True):
        ok_login = True
        break
record('B4 quality登录', ok_login, f"pwd={pw if ok_login else '未找到'}")

print("\n========== 汇总 ==========")
fails = [r for r in results if not r[1]]
print(f"总用例 {len(results)}，通过 {len(results)-len(fails)}，失败 {len(fails)}")
for f in fails:
    print("  FAIL:", f[0], f[2])

print("\n========== 场景C：权限拦截与排程联动 ==========")
# C1: quality（只读生产/排程）POST 排程新增 → 应拦截
login('quality', 'quality123')
before_cnt = q('SELECT COUNT(*) c FROM production_schedules')[0]['c']
r = client.post('/production/schedules/add', data={'product_id':'1','quantity':'10'})
after_cnt = q('SELECT COUNT(*) c FROM production_schedules')[0]['c']
writable = after_cnt > before_cnt  # 权限放开（v8.5.0）：可写 = 产生新记录
record('C1 quality 可写排程（权限放开）', writable, f"前={before_cnt} 后={after_cnt} status={r.status_code}")

# C2: quality POST Bug 新增 → 应拦截（bug 不在 quality 只读列表？查）
r = client.post('/bugs/add', data={'title':'x','product_id':'1'})
# quality 对 bug 模块可写（业务：质量组负责巡检bug与评判等级）
record('C2 quality Bug可写（符合设计）', r.status_code in (200, 302), f"status={r.status_code}")

# C3: product POST 交付计划新增 → 应拦截（交付只读）
login('product', '123456') or login('product', 'product123')
for pw in ['123456','product','product123']:
    rr = client.post('/login', data={'username':'product','password':pw}, follow_redirects=True)
    if '退出' in rr.get_data(as_text=True): break
before_dp = q('SELECT COUNT(*) c FROM delivery_plans')[0]['c']
r = client.post('/delivery/plans/add', data={'requirement_id': str(rid), 'product_id':'1','quantity':'1'})
after_dp = q('SELECT COUNT(*) c FROM delivery_plans')[0]['c']
record('C3 product 可写交付（权限放开）', after_dp > before_dp, f"前={before_dp} 后={after_dp} status={r.status_code}")

# C4: dev_test 角色登录 & Bug 写权限（dev_test 应可写 bug）
login('dev_test', '123456') or login('dev_test', 'dev_test123')
for pw in ['123456','dev_test','dev_test123']:
    rr = client.post('/login', data={'username':'dev_test','password':pw}, follow_redirects=True)
    if '退出' in rr.get_data(as_text=True): break
r = client.post('/bugs/add', data={'title':'权限验证Bug','product_id':'1','version_id':'1'})
record('C4 dev_test Bug写权限', r.status_code in (200, 302), f"status={r.status_code}")

# C5: 排程工序全部完成 → 排程自动已完成（用测试排程 sid）
login('admin', 'admin123')
procs = q("SELECT id FROM production_processes WHERE schedule_id=?", (sid,))
all_ok = True
for pp in procs:
    r = client.post(f'/production/schedules/{sid}/processes/{pp["id"]}/status', data={'status':'已完成'})
    if r.status_code != 200: all_ok = False
st = q("SELECT status FROM production_schedules WHERE id=?", (sid,))[0]['status']
record('C5 工序全完成→排程自动已完成', all_ok and st == '已完成', f"排程状态={st}")

print("\n========== 最终汇总 ==========")
fails = [r for r in results if not r[1]]
print(f"总用例 {len(results)}，通过 {len(results)-len(fails)}，失败 {len(fails)}")
for f in fails:
    print("  FAIL:", f[0], f[2])
