"""压力测试与全链路业务逻辑验证脚本
- 阶段1：注入虚拟数据（VT-前缀，覆盖 需求/版本/任务/Bug/合同/排程/工序/测试/交付/收入/设备/OTA/工单/反馈）
- 阶段2：全流程业务逻辑验证（创建→关联→状态流转→汇总）
- 阶段3：并发压力测试（多线程打 GET/POST）
- 阶段4：页面渲染完整性检查
输出 PASS/FAIL 报告；虚拟数据保留在库中供人工复核
"""
import sys, io, time, sqlite3, threading, random
from concurrent.futures import ThreadPoolExecutor
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

sys.path.insert(0, '.')
from app import app
app.config['TESTING'] = True

RESULTS = []
def record(name, ok, info=''):
    RESULTS.append((name, ok, info))
    print(f"[{'✓' if ok else '✗'}] {name} {info}")

DB = 'business.db'
def dbq(sql, args=()):
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.execute(sql, args)
        conn.commit()
        return cur
    finally:
        conn.close()

def dbfetch(sql, args=()):
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(sql, args).fetchall()
    conn.close()
    return rows

# ===== 幂等清理：每次运行前重置业务表（保留 products/users） =====
def clean_business_data():
    conn = sqlite3.connect(DB)
    conn.execute('PRAGMA foreign_keys=OFF')
    tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
    keep = {'products', 'users'}
    for t in tables:
        if t in keep: continue
        conn.execute(f'DELETE FROM "{t}"')
    try:
        conn.execute('DELETE FROM sqlite_sequence')
    except Exception:
        pass
    conn.commit()
    conn.close()

client = app.test_client()
def login():
    r = client.post('/login', data={'username':'admin','password':'admin123'}, follow_redirects=False)
    return r.status_code in (200, 302)
LOGIN_OK = login()
clean_business_data()  # 幂等重置，保证每次从干净态开始
# 压测环境：给 admin 全订阅业务插件，绕过插件守卫
_conn = sqlite3.connect(DB)
for _pid in ['workbench','requirements','tech_req','wbs','planning','production','bug','test','delivery','device','contract','data_mgmt']:
    _conn.execute('INSERT OR IGNORE INTO user_plugins (user_id,plugin_id,subscribed_at) VALUES (1,?,?)', (_pid,'2026-10-14 18:00'))
_conn.commit(); _conn.close()

# ============================================================
# 阶段1：注入虚拟数据（真实业务场景，VT-前缀可辨识）
# ============================================================
record('A0 登录态', LOGIN_OK, f"login={LOGIN_OK}")
print("\n========== 阶段1：注入虚拟数据 ==========")
# 1) 需求（园林局 SAM302 分批交付 / 两江卫通 SAM203 / 集团系统侧 V1.3+V1.5 / 集团应用侧 / 领导任务）
reqs = [
    ('园林局 SAM302 采购需求', '用户需求', '交付服务组A', '园林局', '分批交付1500套SAM302：9/10 V1.3基础版200套；9/27 V1.5版500套；10/20 V1.5版800套', 1500, '高', '已评审', '2026-10-20', 'SAM302', '是', '智慧园林监测'),
    ('两江卫通 SAM203 采购需求', '用户需求', '交付服务组A', '两江卫通', '一次性交付210套SAM203，10/12前完成', 210, '高', '已评审', '2026-10-12', 'SAM203', '否', '应急通信'),
    ('集团系统侧 V1.3 版本能力约束', '集团系统侧需求', '产品总体组B', '集团系统', '系统V1.3需模组具备V1.3功能（约束性需求，无合同刚性，仅影响软件版本）', 1, '中', '已评审', '2026-02-01', 'SAM302', '否', '系统版本升级'),
    ('集团系统侧 V1.5 版本能力约束', '集团系统侧需求', '产品总体组B', '集团系统', '系统V1.5不前向兼容，需闭环功率控制/空闲态调度/紧急呼叫/异网漫游新功能', 1, '高', '已评审', '2026-08-01', 'SAM302', '否', '系统版本升级'),
    ('入网认证规范要求', '集团应用侧需求', '交付服务组B', '集团应用', '入网认证与规范要求，行业通用功能性能指标基础线', 1, '中', '待评审', '2026-12-31', 'SAM302', '否', '入网认证'),
    ('领导交办：无线模组应用示范项目', '其他', '领导', '公司领导', '示范项目整体任务进展与时间计划', 1, '高', '进行中', '2026-12-31', 'SAM203', '否', '示范项目'),
]
req_ids = {}
for i, r in enumerate(reqs):
    resp = client.post('/requirements/add', data={
        'title': r[0], 'req_type': r[1], 'source_dept': r[2], 'customer': r[3],
        'description': r[4], 'quantity': r[5], 'priority': r[6], 'status': r[7],
        'delivery_date': r[8], 'product_model': r[9], 'batch_delivery': r[10],
        'application_scenario': r[11],
    }, follow_redirects=False)
    row = dbfetch("SELECT id FROM requirements WHERE title=?", (r[0],))
    if resp.status_code in (200,302) and row:
        req_ids[r[0]] = row[0]['id']
        print(f"  ✓ 需求: {r[0]} id={row[0]['id']}")
    else:
        print(f"  ✗ 需求创建失败: {r[0]} status={resp.status_code}")
record('A1 创建6类需求', len(req_ids) == len(reqs), f"{len(req_ids)}/{len(reqs)}")

# 2) 软件版本（SAM302 V1.3/V1.5 + SAM203 V1.3）
prod_sam302 = dbfetch("SELECT id FROM products WHERE model='SAM302'")[0]['id']
prod_sam203 = dbfetch("SELECT id FROM products WHERE model='SAM203'")[0]['id']
versions = [
    (prod_sam302, 'V1.3', '基础版本', 'V1.3功能+OTA远程升级', 'V1.3基础能力', '2026-02-01', '2026-09-10', '已发布', '1', '0'),
    (prod_sam302, 'V1.5', '用户需求版本', 'V1.3全量+V1.5新功能（闭环功率控制/空闲态调度/紧急呼叫/异网漫游）', 'V1.5新能力', '2026-08-01', None, '开发中', '0', '1'),
    (prod_sam203, 'V1.3', '基础版本', 'SAM203基础能力', '基础功能', '2026-02-01', '2026-08-20', '已发布', '1', '0'),
]
ver_ids = {}
for i, v in enumerate(versions):
    payload = {
        'product_id': v[0], 'version_no': v[1], 'version_name': v[2],
        'description': v[3], 'capabilities': v[4], 'planned_release_date': v[5],
        'status': v[7],
    }
    # V1.5 关联园林局需求（多选关联）
    if v[1] == 'V1.5' and req_ids.get('园林局 SAM302 采购需求'):
        payload['requirement_ids'] = str(req_ids['园林局 SAM302 采购需求'])
    resp = client.post('/planning/versions/add', data=payload, follow_redirects=False)
    row = dbfetch("SELECT id FROM software_versions WHERE version_no=? AND product_id=?", (v[1], v[0]))
    if row:
        vid = row[0]['id']
        ver_ids[f'{v[0]}-{v[1]}'] = vid
        if v[1] == 'V1.5':
            for cap in ['闭环功率控制','空闲态调度','紧急呼叫','异网漫游']:
                dbq("INSERT OR IGNORE INTO version_features (version_id, feature_name, status) VALUES (?,?,?)", (vid, cap, '规划中'))
        print(f"  ✓ 版本: {v[1]} id={vid}")
record('A2 创建软件版本', len(ver_ids) == 3, f"{len(ver_ids)}/3")

# 3) 开发任务（版本关联任务）
tasks = [
    ('V1.5版本开发-核心功能实现', ver_ids.get(f'{prod_sam302}-V1.5'), '研发组', '开发中', '实现闭环功率控制等四项新功能', '2026-09-05', '2026-09-18'),
    ('V1.5版本催熟与实网测试配合', ver_ids.get(f'{prod_sam302}-V1.5'), '测试组', '进行中', '实网测试与bug修复', '2026-09-10', '2026-09-18'),
]
task_ids = []
for t in tasks:
    vid = ver_ids.get(f'{prod_sam302}-V1.5')
    resp = client.post(f'/planning/versions/{vid}/tasks/add', data={
        'task_name': t[0], 'assignee': t[2], 'status': t[3],
        'description': t[4], 'planned_start': t[5], 'planned_finish': t[6],
        'priority': '高',
    }, follow_redirects=False)
    row = dbfetch("SELECT id FROM dev_tasks WHERE task_name=?", (t[0],))
    if row:
        task_ids.append(row[0]['id'])
        print(f"  ✓ 开发任务: {t[0]} id={row[0]['id']}")
record('A3 创建开发任务', len(task_ids) == len(tasks), f"{len(task_ids)}/{len(tasks)}")

# 4) Bug（多状态流转样本）
bugs = [
    ('V1.5空闲态调度异常', ver_ids.get(f'{prod_sam302}-V1.5'), '功能Bug', '严重', '高', '新建', '测试组', '研发组'),
    ('V1.5紧急呼叫语音延迟', ver_ids.get(f'{prod_sam302}-V1.5'), '功能Bug', '一般', '中', '处理中', '测试组', '研发组'),
    ('OTA升级后版本号显示异常', ver_ids.get(f'{prod_sam302}-V1.3'), '兼容Bug', '一般', '中', '已解决', '测试组', '研发组'),
    ('V1.5异网漫游注册失败', ver_ids.get(f'{prod_sam302}-V1.5'), '功能Bug', '致命', '高', '新建', '测试组', '研发组'),
    ('功耗异常偏高', ver_ids.get(f'{prod_sam302}-V1.3'), '性能Bug', '严重', '高', '已关闭', '测试组', '研发组'),
]
bug_ids = []
for b in bugs:
    resp = client.post('/bugs/add', data={
        'title': b[0], 'product_id': prod_sam302, 'version_id': b[1] or '',
        'bug_type': b[2], 'severity': b[3], 'priority': b[4], 'status': b[5],
        'reporter': b[6], 'assignee': b[7], 'description': '压力测试Bug：' + b[0],
    }, follow_redirects=False)
    row = dbfetch("SELECT id FROM bug_records WHERE title=?", (b[0],))
    if row:
        bug_ids.append(row[0]['id'])
        print(f"  ✓ Bug: {b[0]} id={row[0]['id']}")
record('A4 创建5个Bug', len(bug_ids) == len(bugs), f"{len(bug_ids)}/{len(bugs)}")

# 5) 合同（生产/物料走合同跟进入口；用户合同走交付管理-用户合同入口 type=user）
contracts = [
    ('VT-HT-2026-001', '生产合同', '重庆华信智造', '600000', '执行中', '首付款已付30%'),
    ('VT-HT-2026-002', '物料合同', '深圳芯联电子', '180000', '执行中', '关键物料采购'),
    ('VT-HT-2026-003', '用户合同', '园林局', '1500000', '执行中', '分批交付1500套'),
]
for c in contracts:
    url = '/contracts/add?type=user' if c[1] == '用户合同' else '/contracts/add'
    resp = client.post(url, data={
        'contract_no': c[0], 'contract_type': c[1], 'supplier': c[2],
        'amount': c[3], 'owner': 'product_a', 'remark': c[5],
    }, follow_redirects=False)
    print(f"  {'✓' if resp.status_code in (200,302) else '✗'} 合同: {c[0]} status={resp.status_code}")
ct = dbfetch("SELECT COUNT(*) c FROM contracts WHERE contract_no LIKE 'VT-%'")
record('A5 创建3类合同', ct[0]['c'] == 3, f"{ct[0]['c']}/3")

# 6) 生产排程 + 工序
rid_yl = req_ids.get('园林局 SAM302 采购需求')
resp = client.post('/production/schedules/add', data={
    'product_id': prod_sam302, 'requirement_id': rid_yl, 'version_id': ver_ids.get(f'{prod_sam302}-V1.5'),
    'quantity': '1500', 'start_date': '2026-09-11', 'end_date': '2026-09-25',
    'supplier': '重庆华信智造', 'status': '进行中',
}, follow_redirects=False)
sched = dbfetch("SELECT id FROM production_schedules WHERE requirement_id=?", (rid_yl,))
if sched:
    sid = sched[0]['id']
    steps = ['物料齐套', 'SMT贴片', '模组组装', '烧录V1.5', '测试校准', '包装入库']
    for si, st in enumerate(steps):
        dbq("INSERT OR IGNORE INTO production_processes (schedule_id, process_name, sequence, planned_start, planned_finish, status) VALUES (?,?,?,?,?,?)",
            (sid, st, si, f'2026-09-{11+si:02d}', f'2026-09-{11+si+1:02d}', '已完成' if si < 3 else '待开始'))
    print(f"  ✓ 排程 id={sid} + 6道工序")
record('A6 创建排程与工序', bool(sched))

# 7) 测试用例 + 执行
cases = [
    ('V1.5新功能-闭环功率控制用例', prod_sam302, '功能', '闭环功率控制验证', '设置闭环功率，验证实际输出', '测试组'),
    ('V1.5新功能-紧急呼叫用例', prod_sam302, '功能', '紧急呼叫验证', '触发紧急呼叫，验证接通', '测试组'),
    ('V1.5兼容性回归用例', prod_sam302, '兼容', 'V1.3全量回归', '回归V1.3全部能力', '测试组'),
]
for c in cases:
    resp = client.post('/test/cases/add', data={
        'title': c[0], 'product_id': prod_sam302, 'module': c[1],
        'test_steps': c[3], 'expected_result': c[4], 'creator': c[5], 'status': '已启用',
    }, follow_redirects=False)
    print(f"  {'✓' if resp.status_code in (200,302) else '✗'} 用例: {c[0]} status={resp.status_code}")
cc = dbfetch("SELECT COUNT(*) c FROM test_cases")
record('A7 创建测试用例', cc[0]['c'] >= 3, f"{cc[0]['c']}个")

# 8) 交付计划 + 收入确认
deliveries = [
    (rid_yl, 200, '2026-09-10', '2026-09-10', '2026-09-10', 1),
    (rid_yl, 500, '2026-09-25', '2026-09-26', '2026-09-27', 0),
    (rid_yl, 800, '2026-10-20', '', '', 0),
    (req_ids.get('两江卫通 SAM203 采购需求'), 210, '2026-10-10', '2026-10-11', '2026-10-12', 0),
]
for d in deliveries:
    resp = client.post('/delivery/plans/add', data={
        'requirement_id': d[0], 'product_id': prod_sam302 if d[0]==rid_yl else prod_sam203,
        'quantity': d[1], 'production_date': d[2] or '', 'stock_date': d[3] or '',
        'planned_date': d[4], 'delivered': d[5],
    }, follow_redirects=False)
    print(f"  {'✓' if resp.status_code in (200,302) else '✗'} 交付: {d[1]}套 status={resp.status_code}")
dp = dbfetch("SELECT COUNT(*) c FROM delivery_plans WHERE requirement_id IN (?,?)", (rid_yl, req_ids.get('两江卫通 SAM203 采购需求')))
record('A8 创建交付计划', dp[0]['c'] == 4, f"{dp[0]['c']}/4")

# 收入确认（对首批200套）
dl = dbfetch("SELECT id FROM delivery_plans WHERE quantity=200 AND delivered=1 LIMIT 1")
if dl:
    resp = client.post('/revenue/add', data={
        'delivery_plan_id': dl[0]['id'], 'amount': '200000', 'confirm_date': '2026-09-10',
        'remark': '首批200套收入确认',
    }, follow_redirects=False)
    rv = dbfetch("SELECT COUNT(*) c FROM revenue_confirmations")
    record('A9 收入确认', resp.status_code in (200,302) and rv[0]['c'] >= 1, f"status={resp.status_code} 共{rv[0]['c']}条")

# 9) 设备 + OTA
devices = [
    ('VT-SN-302-0001', prod_sam302, '园林局', 'V1.3', '在线'),
    ('VT-SN-302-0002', prod_sam302, '园林局', 'V1.3', '在线'),
    ('VT-SN-203-0001', prod_sam203, '两江卫通', 'V1.3', '离线'),
]
for d in devices:
    resp = client.post('/devices/add', data={
        'device_sn': d[0], 'product_id': d[1], 'customer': d[2],
        'firmware_version': d[3], 'status': d[4],
    }, follow_redirects=False)
    print(f"  {'✓' if resp.status_code in (200,302) else '✗'} 设备: {d[0]} status={resp.status_code}")
od = dbfetch("SELECT COUNT(*) c FROM devices WHERE device_sn LIKE 'VT-%'")
record('A10 创建设备', od[0]['c'] == 3, f"{od[0]['c']}/3")

# OTA版本
resp = client.post('/ota/add', data={
    'product_id': prod_sam302, 'version_no': 'V1.5', 'file_name': 'SAM302_V1.5.bin',
    'description': 'V1.5正式版本', 'status': '已发布',
}, follow_redirects=False)
ov = dbfetch("SELECT COUNT(*) c FROM ota_versions WHERE version_no='V1.5'")
record('A11 创建OTA版本', resp.status_code in (200,302) and ov[0]['c'] >= 1, f"status={resp.status_code}")

# 10) 工单 + 反馈
resp = client.post('/support/tickets/add', data={
    'title': '园林局现场部署技术支持', 'customer': '园林局', 'category': '技术支持',
    'priority': '高', 'description': '现场部署指导与网络配置',
}, follow_redirects=False)
tk = dbfetch("SELECT COUNT(*) c FROM tech_support_tickets WHERE title LIKE '园林局%'")
record('A12 创建工单', resp.status_code in (200,302) and tk[0]['c'] >= 1, f"status={resp.status_code}")

resp = client.post('/feedback/add', data={
    'title': '客户反馈：希望增加低功耗模式', 'customer': '园林局',
    'category': '功能建议', 'description': '希望V1.5增加超低功耗模式',
}, follow_redirects=False)
fb = dbfetch("SELECT COUNT(*) c FROM user_feedbacks WHERE title LIKE '客户反馈%'")
record('A13 创建反馈', resp.status_code in (200,302) and fb[0]['c'] >= 1, f"status={resp.status_code}")

# 11) 资源调度 + 库存领用
resp = client.post('/resources/add', data={
    'resource_type': '设备', 'resource_name': '综测仪-01', 'quantity': '1',
    'status': '占用', 'assignee': '测试组', 'requirement_id': rid_yl or '',
}, follow_redirects=False)
rs = dbfetch("SELECT COUNT(*) c FROM resource_schedules WHERE resource_name='综测仪-01'")
record('A14 资源调度', resp.status_code in (200,302) and rs[0]['c'] >= 1, f"status={resp.status_code}")

# 12) BOM 关键物料
bom = [
    (prod_sam302, '主控芯片', '深圳芯联电子', 1, 1, 1500),
    (prod_sam302, '射频前端模组', '上海锐芯', 1, 1, 1500),
    (prod_sam203, '窄带SoC', '深圳芯联电子', 1, 1, 210),
]
for b in bom:
    dbq("INSERT OR IGNORE INTO bom_items (product_id, material_name, supplier, quantity_per_unit, is_key_material) VALUES (?,?,?,?,?)", b[:5])
bk = dbfetch("SELECT COUNT(*) c FROM bom_items")
record('A15 BOM物料', bk[0]['c'] >= 3, f"{bk[0]['c']}条")

# ============================================================
# 阶段2：全流程业务逻辑验证（状态流转与关联）
# ============================================================
print("\n========== 阶段2：业务逻辑验证 ==========")
# B1 Bug 流转：新建→处理中→已解决→已关闭
if bug_ids:
    b1 = bug_ids[0]
    r = client.post(f'/bugs/{b1}/update', data={'status':'处理中'}, follow_redirects=False)
    ok1 = r.status_code in (200,302)
    # 系统状态机：新建→处理中→已修复→验证中→已关闭（注意不是"已解决"，已修复阶段直接提交修复方案）
    r = client.post(f'/bugs/{b1}/update', data={'status':'已修复', 'fix_solution':'修复空闲态调度逻辑'}, follow_redirects=False)
    ok2 = r.status_code in (200,302)
    r = client.post(f'/bugs/{b1}/update', data={'status':'验证中'}, follow_redirects=False)
    ok3 = r.status_code in (200,302)
    r = client.post(f'/bugs/{b1}/update', data={'status':'已关闭', 'close_reason':'已修复', 'root_cause':'调度状态机竞争条件', 'corrective_action':'增加互斥锁', 'prevention_measure':'补充调度并发用例'}, follow_redirects=False)
    ok4 = r.status_code in (200,302)
    st = dbfetch("SELECT status FROM bug_records WHERE id=?", (b1,))[0]['status']
    record('B1 Bug状态流转闭环(新建→处理中→已修复→验证中→已关闭)', ok1 and ok2 and ok3 and ok4 and st=='已关闭', f"终态={st}")
    wf = dbfetch("SELECT COUNT(*) c FROM bug_workflow WHERE bug_id=?", (b1,))
    record('B2 Bug流转留痕', wf[0]['c'] >= 4, f"{wf[0]['c']}条流转记录")

# B3 需求状态更新（园林局→进行中→已完成前段）
r = client.post(f'/requirements/{rid_yl}/status', data={'status':'进行中'}, follow_redirects=False)
st = dbfetch("SELECT status FROM requirements WHERE id=?", (rid_yl,))[0]['status']
record('B3 需求状态更新', r.status_code in (200,302) and st=='进行中', f"status={st}")

# B4 版本状态更新 V1.5：开发中→已发布
r = client.post(f'/planning/versions/{ver_ids[f'{prod_sam302}-V1.5']}/status', data={'status':'已发布'}, follow_redirects=False)
st = dbfetch("SELECT status FROM software_versions WHERE id=?", (ver_ids[f'{prod_sam302}-V1.5'],))[0]['status']
record('B4 版本状态更新', r.status_code in (200,302) and st=='已发布', f"status={st}")

# B5 排程工序状态流转
ps = dbfetch("SELECT id FROM production_processes WHERE schedule_id=? AND sequence=3 LIMIT 1", (sid,))
if ps:
    r = client.post(f'/production/schedules/{sid}/processes/{ps[0]["id"]}/status', data={'status':'已完成'}, follow_redirects=False)
    st = dbfetch("SELECT status FROM production_processes WHERE id=?", (ps[0]['id'],))[0]['status']
    record('B5 工序状态流转', r.status_code in (200,302) and st=='已完成', f"status={st}")

# B6 交付状态联动（第二批发货）
dp2 = dbfetch("SELECT id FROM delivery_plans WHERE quantity=500 LIMIT 1")
if dp2:
    r = client.post(f'/delivery/plans/{dp2[0]["id"]}/edit', data={
        'requirement_id': rid_yl, 'product_id': prod_sam302, 'quantity': '500',
        'production_date': '2026-09-25', 'stock_date': '2026-09-26', 'planned_date': '2026-09-27', 'delivered': '0',
    }, follow_redirects=False)
    st = dbfetch("SELECT stock_date FROM delivery_plans WHERE id=?", (dp2[0]['id'],))[0]['stock_date']
    record('B6 交付计划编辑联动', r.status_code in (200,302) and st=='2026-09-26', f"stock_date={st}")

# B7 端到端追溯：需求详情页应显示关联版本/Bug/排程/交付
r = client.get(f'/requirements/{rid_yl}')
html = r.get_data(as_text=True)
checks = ['V1.5', '1500', '园林局', '排程']
ok = r.status_code == 200 and all(c in html for c in checks)
record('B7 需求详情端到端追溯', ok, f"含版本/数量/排程，status={r.status_code}")

# B8 总览页（领导看板）渲染与关键数据
r = client.get('/executive')
html = r.get_data(as_text=True)
ok = r.status_code == 200 and ('园林局' in html or '1500' in html or 'SAM302' in html)
record('B8 领导总览渲染', ok, f"status={r.status_code}")

# B9 生产管理看板
r = client.get('/production')
record('B9 生产管理看板', r.status_code == 200, f"status={r.status_code}")

# B10 交付管理跟踪表
r = client.get('/delivery/plans')
html = r.get_data(as_text=True)
ok = r.status_code == 200 and '园林局' in html
record('B10 交付跟踪表', ok, f"status={r.status_code}")

# B11 收入确认列表
r = client.get('/revenue')
record('B11 收入确认列表', r.status_code == 200, f"status={r.status_code}")

# B12 设备中心
r = client.get('/device-center')
record('B12 设备运维中心', r.status_code == 200, f"status={r.status_code}")

# B13 全局搜索：跨模块命中
r = client.get('/api/global-search?q=' + '园林局')
import json as _json
data = _json.loads(r.get_data(as_text=True))
types = set(x['type'] for x in data.get('results', []))
ok = r.status_code == 200 and 'requirement' in types
record('B13 全局搜索需求命中', ok, f"类型={types}")

# B14 需求-版本关联查询
va = dbfetch("SELECT COUNT(*) c FROM version_requirements WHERE version_id=?", (ver_ids[f'{prod_sam302}-V1.5'],))
record('B14 版本-需求关联', va[0]['c'] >= 1, f"{va[0]['c']}条")

# B15 汇总KPI：总览统计卡（需求总数/进行中任务/Bug未关闭）
r = client.get('/')
html = r.get_data(as_text=True)
ok = r.status_code == 200 and ('园林局' in html or '任务' in html)
record('B15 工作台KPI', ok, f"status={r.status_code}")

# B16 合同板块边界（v8.10）：合同跟进仅生产/物料；用户合同在交付管理
r = client.get('/contracts')
html = r.get_data(as_text=True)
ok1 = r.status_code == 200 and 'VT-HT-2026-001' in html and 'VT-HT-2026-002' in html and 'VT-HT-2026-003' not in html
r = client.get('/user-contracts')
html2 = r.get_data(as_text=True)
ok2 = r.status_code == 200 and 'VT-HT-2026-003' in html2 and '园林局' in html2
record('B16 合同板块边界(跟进不含用户合同/用户合同在交付管理)', ok1 and ok2, f"跟进过滤={ok1} 用户页={ok2}")

# B17 用户合同编辑页：类型固定+收款语义
uc = dbfetch("SELECT id FROM contracts WHERE contract_type='用户合同' LIMIT 1")
if uc:
    r = client.get(f'/contracts/{uc[0]["id"]}/edit?type=user')
    html = r.get_data(as_text=True)
    ok = r.status_code == 200 and '用户合同（销售合同）' in html and '收款进度与节点管理' in html
    record('B17 用户合同编辑收款语义', ok, f"status={r.status_code}")
else:
    record('B17 用户合同编辑收款语义', False, "无用户合同数据")

# B18 交付数量守恒校验：累计交付不得超过需求数量
rid_b18 = req_ids.get('园林局 SAM302 采购需求')  # 需求总量1500，已有200+500+800
if rid_b18:
    r = client.post('/delivery/plans/add', data={
        'requirement_id': rid_b18, 'product_id': prod_sam302, 'quantity': '200',
        'planned_date': '2026-10-21',
    }, follow_redirects=False)
    body = r.get_data(as_text=True)
    # 拦截：应回到新增页（302重定向回add）或提示超额
    over = dbfetch("SELECT COUNT(*) c FROM delivery_plans WHERE quantity=200 AND planned_date='2026-10-21'")
    ok = over[0]['c'] == 0
    record('B18 交付超量拦截(需求1500已排1500再交200应拒绝)', ok, f"status={r.status_code} 超额记录数={over[0]['c']}")
else:
    record('B18 交付超量拦截', False, "缺园林局需求")

# B19 收入确认金额校验：确认金额不得大于该条待确认金额
rc = dbfetch("SELECT id, amount FROM revenue_confirmations WHERE status='待确认' LIMIT 1")
if rc:
    r = client.post(f'/revenue/{rc[0]["id"]}/confirm', data={
        'confirmed_amount': str(rc[0]['amount'] + 10000), 'confirm_date': '2026-09-10',
    }, follow_redirects=False)
    st = dbfetch("SELECT status FROM revenue_confirmations WHERE id=?", (rc[0]['id'],))[0]['status']
    ok = r.status_code in (400,) or st != '已确认'
    record('B19 收入确认超额拦截', ok, f"status={r.status_code} 状态={st}")
else:
    record('B19 收入确认超额拦截', False, "无待确认收入")

# ============================================================
# 阶段3：并发压力测试
# ============================================================
print("\n========== 阶段3：并发压力测试 ==========")
urls = ['/', '/requirements', '/planning', '/production', '/bugs', '/delivery/plans',
        '/executive', '/device-center', '/contracts', '/test/cases', '/revenue',
        '/api/global-search?q=V1.5', '/feedback', '/support/tickets']
start_t = time.time()
ok_count = 0
failures = []
lock = threading.Lock()

def hit(u):
    global ok_count
    try:
        c2 = app.test_client()
        c2.post('/login', data={'username':'admin','password':'admin123'})
        r = c2.get(u)
        if r.status_code == 200:
            with lock:
                ok_count += 1
        else:
            with lock:
                failures.append((u, r.status_code))
    except Exception as e:
        with lock:
            failures.append((u, str(e)))

with ThreadPoolExecutor(max_workers=8) as ex:
    futs = [ex.submit(hit, random.choice(urls)) for _ in range(120)]
    for f in futs:
        f.result()
dur = time.time() - start_t
record('C1 并发压测120请求', ok_count >= 110, f"成功{ok_count}/120，耗时{dur:.2f}s，失败{len(failures)}")
if failures:
    for f in failures[:5]:
        print(f"    失败样例: {f}")

# 并发写：同时提交Bug（检查并发安全）
start_t = time.time()
def add_bug(i):
    try:
        c2 = app.test_client()
        c2.post('/login', data={'username':'admin','password':'admin123'})
        r = c2.post('/bugs/add', data={
            'title': f'VT-并发Bug-{i}', 'product_id': prod_sam302, 'severity': '一般',
            'status': '新建', 'reporter': '测试组',
        }, follow_redirects=False)
        return r.status_code in (200,302)
    except Exception:
        return False
with ThreadPoolExecutor(max_workers=5) as ex:
    res = list(ex.map(add_bug, range(20)))
ok_w = sum(res)
cb = dbfetch("SELECT COUNT(*) c FROM bug_records WHERE title LIKE 'VT-并发Bug-%'")
record('C2 并发写20Bug', ok_w >= 18 and cb[0]['c'] == 20, f"成功{ok_w}/20，入库{cb[0]['c']}/20（检查唯一编号冲突）")

# ============================================================
# 阶段4：页面渲染完整性
# ============================================================
print("\n========== 阶段4：页面渲染完整性 ==========")
pages = ['/', '/my-tasks', '/executive', '/requirements', '/requirements/add', '/feedback', '/feedback/add',
         '/support/tickets', '/support/tickets/add', '/planning', '/tech-specs', '/contracts', '/contracts/add',
         '/user-contracts', '/production', '/resources', '/resources/add', '/bugs', '/bugs/add', '/test/cases', '/test/cases/add',
         '/delivery/plans', '/delivery/plans/add', '/revenue', '/revenue/add', '/device-center',
         '/devices/add', '/ota/add', '/data', '/logs']
bad = []
for p in pages:
    try:
        r = client.get(p)
        if r.status_code not in (200, 302):
            bad.append(f'{p}:{r.status_code}')
    except Exception as e:
        bad.append(f'{p}:EXC')
record('D1 全部页面渲染', len(bad) == 0, f"{len(pages)-len(bad)}/{len(pages)} 正常")
if bad:
    for b in bad[:8]:
        print(f"    ✗ {b}")

# ============================================================
print("\n========== 汇总 ==========")
total = len(RESULTS)
fails = [(n, i) for n, ok, i in RESULTS if not ok]
print(f"总用例 {total}，通过 {total-len(fails)}，失败 {len(fails)}")
for n, i in fails:
    print(f"  FAIL: {n} {i}")
print(f"耗时合计: {time.time()-start_t:.1f}s（含并发）")
