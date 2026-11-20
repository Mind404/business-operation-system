#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""v13.5 全系统冷审查：权限、页面可达性、数据完整性、默认审批托管。

全部在临时数据库副本上运行，不读写生产库。
运行：python tools/audit_v135_system.py
"""
import shutil, sqlite3, sys, tempfile
from pathlib import Path

APP = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(APP))
fail = []
pass_ = []


def check(name, cond, detail=''):
    (pass_ if cond else fail).append(name)
    print(('PASS  ' if cond else 'FAIL  ') + name + ('' if cond else '  ' + str(detail)))


tmp = Path(tempfile.mkdtemp(prefix='v135_audit_'))
shutil.copy2(APP / 'business.db', tmp / 'business.db')
shutil.copytree(APP / 'data', tmp / 'data', dirs_exist_ok=True)

import app as appmod
appmod.app.config['DATABASE'] = str(tmp / 'business.db')
appmod.app.config['PLUGIN_DB_DIR'] = str(tmp / 'data')
appmod.app.config['TESTING'] = True
import workflow_engine as wf
main = sqlite3.connect(str(tmp / 'business.db'))
main.row_factory = sqlite3.Row
wf.init_tables(main); wf.seed_defaults(main)

# 增加一个业务管理员账号，覆盖 admin 角色
main.execute("INSERT OR IGNORE INTO users(username,display_name,password,role,is_active,created_at,deleted) "
             "SELECT 'bizadmin','业务管理员',password,'admin',1,datetime('now','localtime'),0 "
             "FROM users WHERE username='admin'")
main.execute("UPDATE users SET password=(SELECT password FROM users WHERE username='admin') "
             "WHERE username='product'")
main.commit()


def login(username):
    c = appmod.app.test_client()
    r = c.post('/login', data={'username': username, 'password': 'admin123'}, follow_redirects=False)
    return c, r.status_code


admin, st = login('admin')
biz, st2 = login('bizadmin')
user, st3 = login('product')
check('三类账号登录', st == 302 and st2 == 302 and st3 == 302, (st, st2, st3))

# 匿名与权限边界
anon = appmod.app.test_client()
r = anon.get('/wbs', follow_redirects=False)
check('匿名访问业务页跳登录', r.status_code == 302 and '/login' in (r.headers.get('Location') or ''))
for path in ('/admin/users', '/admin/plugins', '/admin/backup', '/admin/db-cleanup'):
    r = user.get(path, follow_redirects=False)
    check('普通用户被拒 %s' % path, r.status_code == 302 and '/admin' not in (r.headers.get('Location') or ''))
    r = biz.get(path, follow_redirects=False)
    check('业务管理员被拒平台页 %s' % path, r.status_code == 302 and '/admin' not in (r.headers.get('Location') or ''))
r = user.get('/workflow/admin', follow_redirects=False)
check('普通用户被拒流程管理页', r.status_code == 302)
r = biz.get('/workflow/admin', follow_redirects=False)
check('流程管理页仅平台管理员（业务管理员被拒）', r.status_code == 302)

# 页面可达性（状态 200 且无异常堆栈）
pages = [
    '/', '/plugins', '/profile', '/logs',
    '/wbs', '/wbs/alerts', '/wbs/template',
    '/version_plan/', '/version_plan/products', '/version_plan/demands',
    '/version_plan/versions', '/version_plan/customers', '/version_plan/report',
    '/version_plan/settings', '/workflow/admin', '/workflow/admin/events',
    '/workflow/admin/events/guide',
]
for path in pages:
    r = admin.get(path, follow_redirects=False)
    body = r.data.decode('utf-8', 'replace')[:2000]
    check('管理员页面 %s' % path, r.status_code == 200 and 'Traceback' not in body
          and 'Internal Server Error' not in body, r.status_code)

# 普通用户业务页
for path in ('/wbs', '/version_plan/', '/version_plan/versions', '/version_plan/demands',
             '/version_plan/products', '/version_plan/report'):
    r = user.get(path, follow_redirects=False)
    check('普通用户业务页 %s' % path, r.status_code == 200, r.status_code)

# 默认托管：5 个门禁节点全部绑定且含写回
cat = wf.event_catalog(main)
gates = {
    'version_plan.version.freeze', 'version_plan.version.release',
    'version_plan.version.unfreeze', 'wbs.project.created', 'wbs.task.created',
}
by_key = {e['key']: e for g in cat for e in g['events']}
for k in sorted(gates):
    e = by_key[k]
    check('门禁默认托管 %s' % k, e['managed'] and e['managed_ready'],
          'managed=%s ready=%s' % (e['managed'], e['managed_ready']))
for k, e in by_key.items():
    if k not in gates:
        check('通知节点不误托管 %s' % k, not e['managed'], e['managed'])

# WBS 任务默认流程必须含管理员关口，不能只有任务负责人自批
row = main.execute("SELECT graph_json FROM wf_definition_versions WHERE def_key='auto_wbs_task_created' "
                   "ORDER BY version DESC LIMIT 1").fetchone()
if row:
    import json
    nodes = json.loads(row[0]).get('nodes', [])
    approvals = [n for n in nodes if n.get('type') == 'approval']
    admin_steps = [n for n in approvals if (n.get('assignee') or {}).get('value') == 'admin']
    check('WBS 任务默认流程含管理员审批', len(approvals) >= 2 and bool(admin_steps),
          [(n.get('id'), n.get('assignee')) for n in approvals])
else:
    check('WBS 任务默认流程存在', False)

# 数据完整性
for path in (tmp / 'business.db', tmp / 'data' / 'version_plan.db'):
    con = sqlite3.connect(str(path))
    check('%s integrity_check' % path.name, con.execute('PRAGMA integrity_check').fetchone()[0] == 'ok')
    fk = con.execute('PRAGMA foreign_key_check').fetchall()
    check('%s foreign_key_check' % path.name, not fk, fk[:5])
    con.close()

vp = sqlite3.connect(str(tmp / 'data' / 'version_plan.db'))
vp.row_factory = sqlite3.Row
orphan_v = vp.execute(
    "SELECT COUNT(*) FROM version_plan_demands d WHERE d.version_no<>'' AND NOT EXISTS("
    "SELECT 1 FROM version_plan_versions v WHERE v.version_no=d.version_no AND v.product_code=d.product_code)"
).fetchone()[0]
check('需求指向的版本均存在', orphan_v == 0, orphan_v)
orphan_rel = vp.execute(
    "SELECT COUNT(*) FROM version_plan_releases r WHERE NOT EXISTS("
    "SELECT 1 FROM version_plan_versions v WHERE v.version_no=r.version_no AND v.product_code=r.product_code)"
).fetchone()[0]
check('ReleaseNote 指向的版本均存在', orphan_rel == 0, orphan_rel)

orphan_task = main.execute(
    "SELECT COUNT(*) FROM wbs_tasks t WHERE NOT EXISTS(SELECT 1 FROM wbs_projects p WHERE p.id=t.project_id)"
).fetchone()[0]
check('WBS 任务无孤儿项目', orphan_task == 0, orphan_task)
orphan_dep = main.execute(
    "SELECT COUNT(*) FROM wbs_task_deps d WHERE NOT EXISTS(SELECT 1 FROM wbs_tasks t WHERE t.id=d.pre_task_id)"
    " OR NOT EXISTS(SELECT 1 FROM wbs_tasks t WHERE t.id=d.post_task_id)"
).fetchone()[0] if main.execute("SELECT 1 FROM sqlite_master WHERE name='wbs_task_deps'").fetchone() else 0
check('WBS 依赖无孤儿任务', orphan_dep == 0, orphan_dep)

# 已结束实例不应残留 pending 任务
open_pending = main.execute(
    "SELECT COUNT(*) FROM wf_tasks t JOIN wf_instances i ON i.id=t.instance_id "
    "WHERE t.status='pending' AND i.status IN ('completed','rejected','cancelled')"
).fetchone()[0]
check('已结束流程无待办残留', open_pending == 0, open_pending)

print('\n===== v13.5 系统审查：%d 通过 / %d 失败 =====' % (len(pass_), len(fail)))
if fail:
    print('FAILED: ' + ', '.join(fail))
    sys.exit(1)
