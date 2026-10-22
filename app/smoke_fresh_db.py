# -*- coding: utf-8 -*-
"""新库冒烟测试：模拟用户首次启动（全新 business.db），遍历核心路由，抓所有 500/报错。
验证 db_migrations 建库后所有功能可用（举一反三查图1同类问题）。"""
import os, sys, sqlite3, tempfile, shutil, requests, traceback

BASE = '/home/user/.doubao/agent_mode/workspace/.sessions/38440335741282818/agents/m_0cwEQHMkwwa/business-system'
os.chdir(BASE)
sys.path.insert(0, BASE)

PASS = FAIL = 0
FAILS = []

def check(name, cond, extra=''):
    global PASS, FAIL
    if cond:
        PASS += 1; print(f'  [PASS] {name}')
    else:
        FAIL += 1; FAILS.append(name); print(f'  [FAIL] {name} {extra}')

# ============ 1) 全新库初始化（临时目录，不碰运行库） ============
TMP = tempfile.mkdtemp(prefix='biz_smoke_')
DBPATH = os.path.join(TMP, 'business.db')
os.environ['BIZ_TEST_DB'] = DBPATH  # 供 app 使用独立库

import app as appmod
# 强制 app 指向测试库
appmod.app.config['DATABASE'] = DBPATH
# 初始化 schema（与 app 首启 init_db 同路径：schema.sql 全量建表 + 默认账号）
appmod.init_db()
print('== 全新库初始化完成 ==')
db = sqlite3.connect(DBPATH)
tables = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
print('   表数量:', len(tables))
users_cols = [r[1] for r in db.execute('PRAGMA table_info(users)').fetchall()]
print('   users 列:', users_cols)
# 初始化管理员（模拟 app 首启逻辑 + v11.0.3 平台管理员）
with appmod.app.app_context():
    if db.execute('SELECT COUNT(*) FROM users').fetchone()[0] == 0:
        from datetime import datetime
        db.execute("INSERT INTO users (username, password, display_name, role, is_active, department) VALUES (?,?,?,?,1,?)",
                   ('admin', 'admin123', '系统管理员', 'platform_admin', '平台管理部'))
        db.commit()
    # 补充：普通用户 + 管理员（三级角色）
    db.execute("INSERT OR IGNORE INTO users (username, password, display_name, role, is_active, department) VALUES (?,?,?,?,1,?)",
               ('smoke_admin', 'admin1234', '冒烟管理员', 'admin', '测试部'))
    db.execute("INSERT OR IGNORE INTO users (username, password, display_name, role, is_active, department) VALUES (?,?,?,?,1,?)",
               ('smoke_user', 'user1234', '冒烟普通用户', 'user', '测试部'))
    db.commit()
db.close()

# ============ 2) 用 Flask test client 遍历路由（真渲染、真数据库） ============
client = appmod.app.test_client()
def login():
    r = client.post('/login', data={'username': 'admin', 'password': 'admin123'}, follow_redirects=False)
    return r.status_code in (302, 200)

check('管理员登录', login(), '')

# --- 用户管理（图1根因验证） ---
r = client.post('/admin/users/create', data={'username': 'smoke1', 'password': 'smoke123', 'display_name': '冒烟用户', 'role': 'user', 'department': '测试部'}, follow_redirects=False)
check('创建用户(带部门)', r.status_code in (302, 200), f'code={r.status_code}')
db = sqlite3.connect(DBPATH)
uid = db.execute("SELECT id FROM users WHERE username='smoke1'").fetchone()[0]
dept = db.execute("SELECT department FROM users WHERE id=?", (uid,)).fetchone()[0]
db.close()
check('用户部门已保存', dept == '测试部', f'实际={dept}')
r = client.post(f'/admin/users/{uid}/update', data={'display_name': '冒烟用户2', 'role': 'user', 'department': '计划部', 'is_active': '1'}, follow_redirects=False)
check('编辑用户(带部门)', r.status_code in (302, 200), f'code={r.status_code}')
r = client.post(f'/admin/users/{uid}/toggle', follow_redirects=False)
check('启停用户', r.status_code in (302, 200), f'code={r.status_code}')
r = client.get('/admin/users')
check('用户列表页', r.status_code == 200 and '冒烟用户2' in r.get_data(as_text=True))

# --- 个人中心改密码 ---
r = client.post('/profile/change_password', data={'old_password': 'admin123', 'new_password': 'admin123', 'confirm_password': 'admin123'}, follow_redirects=False)
check('改密码', r.status_code in (302, 200), f'code={r.status_code}')

# --- 需求插件（新库首用） ---
r = client.post('/requirements/add', data={
    'title': '新库冒烟需求', 'req_type': '功能需求', 'source_dept': '测试部',
    'customer': '内部', 'description': 'smoke', 'quantity': '1',
    'priority': '高', 'delivery_date': '2026-12-31', 'owner': 'admin',
}, follow_redirects=False)
check('需求新增', r.status_code in (302, 200), f'code={r.status_code}')
r = client.get('/requirements')
check('需求列表页', r.status_code == 200 and '新库冒烟需求' in r.get_data(as_text=True))
db = sqlite3.connect(DBPATH)
rid = db.execute("SELECT id FROM req_items WHERE title='新库冒烟需求'").fetchone()[0]
db.close()
r = client.post(f'/requirements/{rid}/status', data={'status': '评审通过'})
check('管理员需求状态流转', r.status_code == 200, f'code={r.status_code}')
r = client.get(f'/requirements/{rid}')
check('需求详情页', r.status_code == 200, f'code={r.status_code}')
r = client.get('/requirements/fields')
check('需求字段配置页', r.status_code == 200, f'code={r.status_code}')

# --- 审批流配置（平台管理员，v11.0.3 暂时入口停用但平台管理员可直达）+ 审批中心并入个人中心 ---
r = client.post('/admin/approval-flows/save', data={
    'plugin_id': 'requirements', 'record_type': 'req_status',
    'name': '需求审批-两级', 'step_role': ['user', 'admin'], 'enabled': '1',
})
check('审批流保存', r.status_code == 200, f'code={r.status_code}')
r = client.get('/admin/approval-flows')
check('审批流配置页(平台管理员可直达)', r.status_code == 200, f'code={r.status_code}')
r = client.get('/approvals')
check('审批中心路由重定向到个人中心', r.status_code == 302 and '/profile' in r.headers.get('Location', ''), f'code={r.status_code} loc={r.headers.get("Location")}')
r = client.get('/profile')
html = r.get_data(as_text=True)
check('个人中心含审批板块', r.status_code == 200 and '审批中心' in html and '待我审批' in html, f'code={r.status_code}')
check('个人中心无审批流配置按钮', '审批流配置' not in html, '')

# --- v11.0.3 三级角色权限 ---
# 管理员（admin）：可编辑业务/审批，但无平台配置权限
client2 = appmod.app.test_client()
r = client2.post('/login', data={'username': 'smoke_admin', 'password': 'admin1234'}, follow_redirects=False)
check('管理员登录', r.status_code in (302, 200), f'code={r.status_code}')
r = client2.get('/admin/users', follow_redirects=True)
check('管理员不可访问用户管理', '无权限' in r.get_data(as_text=True), f'code={r.status_code}')
r = client2.get('/admin/ai-settings', follow_redirects=True)
check('管理员不可访问AI设置', '仅平台管理员' in r.get_data(as_text=True), f'code={r.status_code}')
r = client2.get('/profile')
check('管理员个人中心可审', r.status_code == 200 and '审批中心' in r.get_data(as_text=True), f'code={r.status_code}')
# 普通用户（user）：无平台配置权限，可查看个人中心
client3 = appmod.app.test_client()
r = client3.post('/login', data={'username': 'smoke_user', 'password': 'user1234'}, follow_redirects=False)
check('普通用户登录', r.status_code in (302, 200), f'code={r.status_code}')
r = client3.get('/admin/users', follow_redirects=True)
check('普通用户不可访问用户管理', '无权限' in r.get_data(as_text=True), f'code={r.status_code}')
r = client3.get('/profile')
check('普通用户个人中心', r.status_code == 200 and '修改密码' in r.get_data(as_text=True), f'code={r.status_code}')
# 平台管理员可创建平台管理员角色用户
r = client.post('/admin/users/create', data={'username': 'smoke_pm2', 'password': 'pm12345', 'display_name': '平台管理员2', 'role': 'platform_admin', 'department': '平台部'}, follow_redirects=False)
check('创建平台管理员', r.status_code in (302, 200), f'code={r.status_code}')
r = client.get('/admin/users')
check('用户列表含平台管理员徽章', '平台管理员' in r.get_data(as_text=True), f'code={r.status_code}')

# --- 外部系统接入（新库） ---
r = client.post('/admin/external/create', data={
    'name': '冒烟外部系统', 'plugin_id': 'smoke', 'system_type': '业务系统',
    'access_method': 'api', 'conn_config': '{"base_url":"http://x"}', 'note': '',
}, follow_redirects=False)
check('外部系统登记', r.status_code in (302, 200), f'code={r.status_code}')
r = client.get('/admin/external')
check('外部系统页', r.status_code == 200, f'code={r.status_code}')

# --- WBS（新库） ---
r = client.post('/wbs/add', data={
    'name': '新库冒烟项目', 'description': '', 'owner': 'admin', 'participants': 'admin',
    'start_date': '2026-10-21', 'end_date': '2026-11-30', 'status': '进行中',
}, follow_redirects=False)
check('WBS项目新增', r.status_code in (302, 200), f'code={r.status_code}')
db = sqlite3.connect(DBPATH)
pid = db.execute("SELECT id FROM wbs_projects WHERE name='新库冒烟项目'").fetchone()[0]
db.close()
r = client.post(f'/wbs/{pid}/task/add', data={
    'wbs_code': '1.1', 'name': '冒烟任务', 'owner': 'admin', 'plan_start': '2026-10-21',
    'plan_end': '2026-10-31', 'priority': 'P1高', 'status': '进行中', 'progress': '50',
}, follow_redirects=False)
check('WBS任务新增', r.status_code in (302, 200), f'code={r.status_code}')
r = client.get(f'/wbs/{pid}')
check('WBS详情页', r.status_code == 200 and '冒烟任务' in r.get_data(as_text=True), f'code={r.status_code}')
r = client.get('/wbs')
check('WBS列表页', r.status_code == 200, f'code={r.status_code}')
r = client.get('/wbs/alerts')
check('WBS预警页', r.status_code == 200, f'code={r.status_code}')
r = client.get(f'/wbs/{pid}/export')
check('WBS导出', r.status_code == 200, f'code={r.status_code}')

# --- 聚合视图 / AI ---
r = client.get('/')
check('聚合视图', r.status_code == 200, f'code={r.status_code}')
r = client.get('/api/ai/health')
check('AI health 接口', r.status_code == 200, f'code={r.status_code}')
r = client.get('/api/ai/alert-summary')
check('AI 预警接口', r.status_code == 200, f'code={r.status_code}')
r = client.post('/api/ai/test', json={'mode': 'local'})
check('AI 连接测试接口', r.status_code == 200, f'code={r.status_code}')
r = client.get('/admin/ai-settings')
check('AI 设置页', r.status_code == 200 and 'AI 设置' in r.get_data(as_text=True), f'code={r.status_code}')
r = client.post('/admin/ai-settings', data={
    'mode': 'remote', 'base_url': 'https://api.x-aio.com', 'api_key': 'sk-test1234567890',
    'model': 'qwen2.5-7b-instruct', 'enabled': '1',
}, follow_redirects=False)
check('AI 配置保存', r.status_code in (302, 200), f'code={r.status_code}')
db = sqlite3.connect(DBPATH)
ai_cfg = dict(db.execute('SELECT key, value FROM ai_config').fetchall())
db.close()
check('AI 配置已落库', ai_cfg.get('mode') == 'remote' and ai_cfg.get('model') == 'qwen2.5-7b-instruct', f'cfg={ai_cfg}')
r = client.get('/api/ai/health')
body = r.get_json() if r.is_json else {}
check('AI health 反映配置', body.get('mode') == 'remote' and body.get('api_key_masked', '').endswith('7890'), f'health={body}')
# --- 本地 OpenAI 兼容（LM Studio 等）配置 ---
r = client.post('/admin/ai-settings', data={
    'mode': 'local', 'provider_type': 'openai_compat', 'base_url': 'http://127.0.0.1:1234/v1',
    'api_key': '', 'model': 'qwen2.5-3b-instruct', 'enabled': '1',
}, follow_redirects=False)
check('本地OpenAI兼容配置保存', r.status_code in (302, 200), f'code={r.status_code}')
db = sqlite3.connect(DBPATH)
ai_cfg2 = dict(db.execute('SELECT key, value FROM ai_config').fetchall())
db.close()
check('本地OpenAI兼容配置落库', ai_cfg2.get('provider_type') == 'openai_compat' and ai_cfg2.get('base_url') == 'http://127.0.0.1:1234/v1', f'cfg={ai_cfg2}')
r = client.get('/api/ai/health')
body = r.get_json() if r.is_json else {}
check('health反映本地兼容模式', body.get('mode') == 'local' and body.get('provider_type') == 'openai_compat' and body.get('base_url') == 'http://127.0.0.1:1234/v1', f'health={body}')
# --- 本地 Ollama 默认地址 ---
r = client.post('/admin/ai-settings', data={
    'mode': 'local', 'provider_type': 'ollama', 'base_url': '',
    'api_key': '', 'model': 'qwen2.5:0.5b', 'enabled': '1',
}, follow_redirects=False)
check('本地Ollama配置保存(留空地址)', r.status_code in (302, 200), f'code={r.status_code}')
r = client.get('/api/ai/health')
body = r.get_json() if r.is_json else {}
check('health本地Ollama默认地址', body.get('mode') == 'local' and body.get('base_url') == 'http://127.0.0.1:11434', f'health={body}')
r = client.post('/api/ai/test', json={'mode': 'local', 'provider_type': 'openai_compat', 'base_url': 'http://127.0.0.1:1234/v1'})
check('本地兼容连接测试接口', r.status_code == 200 and 'msg' in (r.get_json() or {}), f'code={r.status_code}')

# --- 清理测试库 ---
shutil.rmtree(TMP, ignore_errors=True)

print('=' * 60)
print(f'结果: PASS={PASS} FAIL={FAIL}')
if FAILS:
    print('失败项:', FAILS); sys.exit(1)
print('新库冒烟全部通过 ✓')
