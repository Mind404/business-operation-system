#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""v13.6.0.0 工作台插件化 + 驳回通知 + 用户关联 回归测试。

覆盖：旧计数板块移除、插件模块聚合、WBS 账号/姓名双识别、版本规划
产品负责人待办、通知分组/已读/越权、驳回通知幂等、产品负责人必须来自
用户管理、wf_event_log 保留期清理。
全部在临时 SQLite 副本上运行，不写生产库。
运行：python tools/regress_v136.py
"""
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

APP = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(APP))

failures = []
passes = []


def check(name, cond, detail=''):
    if cond:
        passes.append(name)
        print('PASS  %s' % name)
    else:
        failures.append((name, detail))
        print('FAIL  %s  %s' % (name, detail))


def login(client, username, password='admin123'):
    return client.post('/login', data={'username': username, 'password': password},
                       follow_redirects=False)


def main():
    cp = subprocess.run([sys.executable, '-m', 'compileall', '-q', str(APP)],
                        capture_output=True, text=True)
    check('Python 全量编译', cp.returncode == 0, cp.stderr[-800:])

    tmp = Path(tempfile.mkdtemp(prefix='v136_regress_'))
    db_path = tmp / 'business.db'
    shutil.copy2(APP / 'business.db', db_path)
    plug_dir = tmp / 'plugin_data'
    plug_dir.mkdir()
    shutil.copy2(APP / 'data' / 'version_plan.db', plug_dir / 'version_plan.db')

    import app as appmod
    appmod.app.config['DATABASE'] = str(db_path)
    appmod.app.config['PLUGIN_DB_DIR'] = str(plug_dir)

    import plugin_system
    import workflow_engine as wf
    from werkzeug.security import generate_password_hash

    with appmod.app.app_context():
        plugin_system.init_plugin_tables()

    maindb = sqlite3.connect(str(db_path))
    maindb.row_factory = sqlite3.Row
    wf.init_tables(maindb)
    wf.seed_defaults(maindb)

    maindb.execute("INSERT OR IGNORE INTO users (username,password,display_name,role,is_active,department) "
                   "VALUES ('wbtester',?,'待办测试员','user',1,'计划部')",
                   (generate_password_hash('admin123'),))
    maindb.execute("INSERT OR IGNORE INTO users (username,password,display_name,role,is_active,department) "
                   "VALUES ('other',?,'其他同事','user',1,'质量部')",
                   (generate_password_hash('admin123'),))
    maindb.commit()
    wb = maindb.execute("SELECT * FROM users WHERE username='wbtester'").fetchone()
    other = maindb.execute("SELECT * FROM users WHERE username='other'").fetchone()
    admin = maindb.execute("SELECT * FROM users WHERE username='admin'").fetchone()

    # ---------- 虚拟 WBS：账号负责人 1 条、姓名负责人 1 条、他人 1 条 ----------
    cur = maindb.execute("INSERT INTO wbs_projects(name,status,owner,created_at) "
                         "VALUES('工作台插件化验证项目','进行中','wbtester','2026-11-20')")
    pid = cur.lastrowid
    rows = [
        ('T-ACC', '账号负责人任务', 'wbtester', '进行中', '2026-11-21'),
        ('T-NAME', '姓名负责人任务', '待办测试员', '进行中', '2026-11-25'),
        ('T-OTHER', '他人的任务', 'other', '进行中', '2026-11-22'),
    ]
    for code, name, owner, status, due in rows:
        maindb.execute("INSERT INTO wbs_tasks(project_id,wbs_code,name,owner,status,approval_status,"
                       "plan_end,progress) VALUES(?,?,?,?,?,?,?,?)",
                       (pid, code, name, owner, status, '已通过', due, 10))
    # 遗留 tasks 表应已在迁移中删除
    old_tables = {r[0] for r in maindb.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    check('遗留 tasks/approval_requests 表已删除',
          'tasks' not in old_tables and 'approval_requests' not in old_tables)
    maindb.commit()

    # ---------- 版本规划：产品负责人用账号/姓名，版本待发布 ----------
    vpdb = sqlite3.connect(str(plug_dir / 'version_plan.db'))
    vpdb.row_factory = sqlite3.Row
    # v13.6.1 起生产库默认空数据，回归自建最小产品/版本，避免依赖演示数据
    if not vpdb.execute("SELECT 1 FROM version_plan_products LIMIT 1").fetchone():
        vpdb.executemany("INSERT INTO version_plan_products(code,name,owner,status,created_at) "
                         "VALUES(?,?,?,'启用','2026-11-20')",
                         [('P1', '车载网关 GW-500', 'wbtester'),
                          ('P2', 'T-BOX 通信模组', '待办测试员'),
                          ('P3', '座舱域控 CDC', 'other')])
    if not vpdb.execute("SELECT 1 FROM version_plan_versions LIMIT 1").fetchone():
        for code in ('P1', 'P2'):
            vpdb.execute("INSERT INTO version_plan_versions(version_no,vtype,product_code,status,created_at) "
                         "VALUES('1.1.0','迭代',?,'测试中','2026-11-20')", (code,))
    vpdb.execute("UPDATE version_plan_products SET owner='wbtester' WHERE code='P1'")
    vpdb.execute("UPDATE version_plan_products SET owner='待办测试员' WHERE code='P2'")
    vpdb.execute("UPDATE version_plan_versions SET status='测试中', release_date='2026-11-25' "
                 "WHERE product_code='P1' AND version_no='1.1.0'")
    vpdb.commit()

    # ---------- 1. 用户管理归一 ----------
    with appmod.app.app_context():
        ctx = plugin_system.resolve_workbench_user(maindb, {
            'id': wb['id'], 'username': 'wbtester', 'display_name': '待办测试员',
            'role': 'user'})
        check('用户身份归一命中用户管理', ctx['id'] == wb['id'] and ctx['username'] == 'wbtester',
              str(ctx))
        ctx_by_display = plugin_system.resolve_workbench_user(maindb, '待办测试员')
        check('按显示名也能归一', ctx_by_display['username'] == 'wbtester', str(ctx_by_display))
        check('版本产品负责人账号解析', appmod and True)
        import plugins.version_plan.routes as vpr
        check('产品负责人姓名解析为账号', vpr._resolve_system_user('待办测试员') == 'wbtester')
        check('不存在的负责人拒绝解析', vpr._resolve_system_user('不存在的人') is None)
        check('历史未关联负责人显示提示', '未关联账号' in vpr._owner_display('张工'))

        groups = plugin_system.collect_workbench_modules(maindb, ctx)
        by_id = {g['plugin_id']: g for g in groups}
        check('工作台按插件分组返回 WBS', 'wbs' in by_id, str(list(by_id)))
        check('工作台返回版本规划模块', 'version_plan' in by_id, str(list(by_id)))
        wbs_items = by_id.get('wbs', {}).get('items', [])
        titles = {x['title'] for x in wbs_items}
        check('账号负责人任务进入 WBS 待办', '账号负责人任务' in titles, str(titles))
        check('姓名负责人任务进入 WBS 待办', '姓名负责人任务' in titles, str(titles))
        check('他人任务不进入本人待办', '他人的任务' not in titles, str(titles))
        check('WBS 待办附带用户管理身份',
              all(x.get('owner_username') == 'wbtester' for x in wbs_items), str(wbs_items[:1]))
        vp_items = by_id.get('version_plan', {}).get('items', [])
        check('版本待办按产品负责人归集', any('版本 1.1.0' in x['title'] for x in vp_items),
              str(vp_items))
        check('版本待办支持产品负责人姓名写法',
              any(x['project'] == 'T-BOX 通信模组' for x in vp_items), str(vp_items))
        check('版本待办不串到其他负责人产品',
              all(x['project'] != '座舱域控 CDC' for x in vp_items), str(vp_items))

    # 同名显示名不能让待办串到别人名下
    with appmod.app.app_context():
        import plugins.wbs.routes as wbr
        maindb.execute("INSERT INTO users (username,password,display_name,role,is_active,department) "
                       "VALUES ('wbtester2',?,'待办测试员','user',1,'计划部')",
                       (generate_password_hash('admin123'),))
        maindb.commit()
        aliases = wbr._owner_aliases(maindb, 'wbtester')
        check('同名显示名不作为负责人匹配别名',
              '待办测试员' not in aliases and 'wbtester' in aliases, str(aliases))
        check('重名时 WBS 待办不猜测归属', wbr.my_todos(maindb, '待办测试员') == [])
        amb = plugin_system.resolve_workbench_user(maindb, '待办测试员')
        check('重名时工作台身份不猜测', amb.get('id') is None and not amb.get('username'),
              str(amb))
        import plugins.version_plan.routes as vpr
        check('同名显示名不解析为任一账号', vpr._resolve_system_user('待办测试员') is None)
        check('同名显示名不误显示为某个人', '重名' in vpr._owner_display('待办测试员'),
              vpr._owner_display('待办测试员'))
        maindb.execute("DELETE FROM users WHERE username='wbtester2'")
        maindb.commit()

    # ---------- 2. 工作台页面：旧计数板块废弃 ----------
    client = appmod.app.test_client()
    login(client, 'wbtester')
    resp = client.get('/')
    html = resp.data.decode('utf-8')
    check('工作台页面 200', resp.status_code == 200)
    check('页面无旧 WBS 项目总数计数', 'WBS 项目总数' not in html)
    check('页面无旧待办总数计数', '项未完成' not in html)
    check('页面有插件待办模块', '插件待办' in html)
    check('页面有插件通知模块', '插件通知' in html)
    check('页面展示关联用户账号', 'wbtester' in html and '待办测试员' in html)
    check('WBS 任务正常展示', '账号负责人任务' in html and '姓名负责人任务' in html)
    check('版本规划待办正常展示', '版本 1.1.0' in html)

    # ---------- 3. 通知：分组、幂等、已读、越权 ----------
    with appmod.app.app_context():
        n1 = wf.notify_user(maindb, wb['id'], '测试通知一', '正文一', '%s' % 'version_plan',
                            event_key='evt1', instance_id='inst-x', business_key='BK-X')
        n2 = wf.notify_user(maindb, wb['id'], '测试通知二', '正文二', 'wbs',
                            event_key='evt2', instance_id='inst-y', business_key='BK-Y')
        n_dup = wf.notify_user(maindb, wb['id'], '测试通知一', '正文一', 'version_plan',
                               event_key='evt1', instance_id='inst-x', business_key='BK-X')
        check('通知写入成功', bool(n1 and n2 and n_dup))
        check('同实例同事件通知幂等', n1['id'] == n_dup['id'], '%s / %s' % (n1['id'], n_dup['id']))
        groups = wf.collect_notification_groups(maindb, wb['id'])
        check('通知按来源插件分组', {g['plugin_id'] for g in groups} == {'version_plan', 'wbs'},
              str([(g['plugin_id'], len(g['items'])) for g in groups]))
        check('未读数正确', wf.unread_notification_count(maindb, wb['id']) == 2,
              str(wf.unread_notification_count(maindb, wb['id'])))
        check('其他用户看不到该通知', wf.notifications_for_user(maindb, other['id']) == [])

    # 工作台展示通知
    html = client.get('/').data.decode('utf-8')
    check('工作台展示通知标题', '测试通知一' in html and '测试通知二' in html)

    # 通知注入的 HTML 必须转义
    with appmod.app.app_context():
        n3 = wf.notify_user(maindb, wb['id'], '<script>alert(1)</script>', 'x', 'wbs',
                            event_key='evt3', instance_id='inst-z')
    html = client.get('/').data.decode('utf-8')
    check('通知标题 HTML 转义', '&lt;script&gt;' in html and '<script>alert(1)</script>' not in html)

    # 协议相对地址不能把用户带出站
    with appmod.app.app_context():
        bad = wf.notify_user(maindb, wb['id'], '恶意跳转', 'x', 'wbs',
                             event_key='evt-bad', instance_id='inst-bad',
                             url='//evil.example.com/a')
    resp = client.get('/workflow/notification/%d/open' % bad['id'])
    loc = resp.headers.get('Location', '')
    check('通知跳转拒绝协议相对地址',
          resp.status_code == 302 and 'evil.example.com' not in loc, loc)

    # 打开通知：标为已读并跳转
    with appmod.app.app_context():
        nid = wf.notifications_for_user(maindb, wb['id'])[0]['id']
    resp = client.get('/workflow/notification/%d/open' % nid)
    check('通知打开重定向', resp.status_code == 302, str(resp.status_code))
    row = maindb.execute('SELECT * FROM wf_notifications WHERE id=?', (nid,)).fetchone()
    check('通知打开后已读', bool(row['read_at']), str(dict(row)))

    # 他人不能打开我的通知
    other_client = appmod.app.test_client()
    login(other_client, 'other')
    resp = other_client.get('/workflow/notification/%d/open' % nid)
    check('他人不能打开我的通知', resp.status_code == 302,
          '%s %s' % (resp.status_code, resp.headers.get('Location')))
    check('越权打开不改变已读状态',
          maindb.execute('SELECT read_at FROM wf_notifications WHERE id=?', (nid,)).fetchone()['read_at'] == row['read_at'])

    # 全部标为已读
    login(client, 'wbtester')
    resp = client.post('/workflow/notifications/read-all', follow_redirects=True)
    check('全部标为已读 200', resp.status_code == 200)
    check('全部已读后未读为 0', wf.unread_notification_count(maindb, wb['id']) == 0)

    # ---------- 4. 流程驳回通知 ----------
    inst = wf.emit_state_change(maindb, 'version_plan', 'version_plan.version.freeze',
                                'VP-P1-1.1.0', '驳回通知验证版本', {}, start_now=True,
                                initiator_id=wb['id'], initiator_name=wb['display_name'])
    check('驳回测试流程已创建', bool(inst['instance_id']), str(inst))
    iid = inst['instance_id']
    task = maindb.execute("SELECT * FROM wf_tasks WHERE instance_id=? AND status='pending'", (iid,)).fetchone()
    check('驳回测试有待审批任务', bool(task))
    before = maindb.execute('SELECT COUNT(*) c FROM wf_notifications WHERE user_id=?', (wb['id'],)).fetchone()['c']
    if task:
        wf.approve_task(maindb, task['id'], admin['id'], admin['display_name'],
                        'platform_admin', 'reject', '验证驳回通知')
        maindb.commit()
    check('流程最终状态 rejected',
          maindb.execute('SELECT status FROM wf_instances WHERE id=?', (iid,)).fetchone()['status'] == 'rejected')
    rows = maindb.execute('SELECT * FROM wf_notifications WHERE user_id=? ORDER BY id DESC', (wb['id'],)).fetchall()
    check('驳回后发起人收到通知', len(rows) == before + 1, '%s -> %s' % (before, len(rows)))
    reject_note = next((r for r in rows if (r['instance_id'] or '') == iid), None)
    check('驳回通知关联流程实例', bool(reject_note), str([dict(r) for r in rows[:2]]))
    check('驳回通知带驳回意见', reject_note and '验证驳回通知' in (reject_note['body'] or ''),
          reject_note['body'] if reject_note else '')
    check('驳回通知来源插件正确', reject_note and reject_note['plugin_id'] == 'version_plan',
          reject_note['plugin_id'] if reject_note else '')
    # 幂等：再次写同一实例同一事件不新增
    if reject_note:
        wf.notify_user(maindb, wb['id'], '重复驳回', '', 'version_plan', reject_note['event_key'],
                       instance_id=iid, business_key='VP-P1-1.1.0')
        cnt = maindb.execute('SELECT COUNT(*) c FROM wf_notifications WHERE instance_id=? AND user_id=?',
                             (iid, wb['id'])).fetchone()['c']
        check('驳回通知幂等不重复', cnt == 1, str(cnt))

    # ---------- 5. 产品负责人必须来自用户管理 ----------
    admin_client = appmod.app.test_client()
    login(admin_client, 'admin')
    resp = admin_client.post('/version_plan/products/new', data={
        'code': 'P9', 'name': '非法负责人产品', 'owner': '不存在的人'}, follow_redirects=True)
    check('非法产品负责人被拒绝', '负责人必须从' in resp.data.decode('utf-8'), resp.status_code)
    check('非法负责人未创建产品',
          vpdb.execute("SELECT COUNT(*) c FROM version_plan_products WHERE code='P9'").fetchone()['c'] == 0)
    resp = admin_client.post('/version_plan/products/new', data={
        'code': 'P9', 'name': '合法负责人产品', 'owner': '待办测试员'}, follow_redirects=False)
    check('合法负责人可创建产品', resp.status_code == 302, str(resp.status_code))
    prow = vpdb.execute("SELECT * FROM version_plan_products WHERE code='P9'").fetchone()
    check('产品负责人保存为账号', prow and prow['owner'] == 'wbtester', dict(prow) if prow else '')

    # ---------- 6. 事件流水保留期 ----------
    with appmod.app.app_context():
        wf.prune_event_log(maindb, force=True)  # 先清一次，避免历史残留干扰
        maindb.execute("INSERT INTO wf_event_log(plugin_id,event_key,created_at) "
                       "VALUES('x','old','2020-01-01 00:00:00')")
        maindb.execute("INSERT INTO wf_event_log(plugin_id,event_key,created_at) "
                       "VALUES('x','new','2026-11-20 10:00:00')")
        maindb.commit()
        deleted = wf.prune_event_log(maindb, force=True)
        check('超 180 天流水被清理', deleted >= 1, str(deleted))
        check('保留期内流水保留',
              maindb.execute("SELECT COUNT(*) c FROM wf_event_log WHERE event_key='new'").fetchone()['c'] == 1)
        check('超期流水已删除',
              maindb.execute("SELECT COUNT(*) c FROM wf_event_log WHERE event_key='old'").fetchone()['c'] == 0)
        # 最大条数
        for i in range(8):
            maindb.execute("INSERT INTO wf_event_log(plugin_id,event_key,created_at) "
                           "VALUES('x','cap','2026-11-20 10:00:00')")
        maindb.commit()
        wf.prune_event_log(maindb, keep_days=999, max_rows=5, force=True)
        check('超过 50 万条等上限时按最旧删除',
              maindb.execute('SELECT COUNT(*) c FROM wf_event_log').fetchone()['c'] == 5,
              str(maindb.execute('SELECT COUNT(*) c FROM wf_event_log').fetchone()['c']))

    # ---------- 7. 禁用插件后模块消失 ----------
    with appmod.app.app_context():
        maindb.execute("UPDATE plugin_registry SET enabled=0 WHERE id='version_plan'")
        maindb.commit()
        groups = plugin_system.collect_workbench_modules(maindb, {
            'id': wb['id'], 'username': 'wbtester', 'display_name': '待办测试员'})
        check('插件停用后不返回其工作台模块',
              all(g['plugin_id'] != 'version_plan' for g in groups), str([g['plugin_id'] for g in groups]))
        maindb.execute("UPDATE plugin_registry SET enabled=1 WHERE id='version_plan'")
        maindb.commit()

    print('\n===== v13.6 回归：%d 通过 / %d 失败 =====' % (len(passes), len(failures)))
    if failures:
        for f in failures:
            print('  FAILED: %s  %s' % f)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
