#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""v13.6 工作台插件化 / 通知并发压力测试（临时库，不碰生产）。

场景：
- 60 个用户、每个用户 200 条 WBS 待办（共 1.2 万条）；
- 每用户 300 条插件通知；
- 40 线程并发写同一个实例/事件的驳回通知，验证唯一索引兜底；
- 20 次工作台页面渲染，统计 P95。
"""
import shutil
import sqlite3
import statistics
import sys
import tempfile
import threading
import time
from pathlib import Path

APP = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(APP))

fail = []


def check(name, cond, detail=''):
    print(('PASS  ' if cond else 'FAIL  ') + name + ('' if cond else '  ' + str(detail)))
    if not cond:
        fail.append(name)


def main():
    tmp = Path(tempfile.mkdtemp(prefix='v136_stress_'))
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
    maindb = sqlite3.connect(str(db_path), timeout=30)
    maindb.row_factory = sqlite3.Row
    wf.init_tables(maindb)
    wf.seed_defaults(maindb)
    maindb.execute('PRAGMA busy_timeout=30000')

    # 60 用户
    users = []
    for i in range(60):
        un = 'stress%02d' % i
        dn = '压测用户%02d' % i
        maindb.execute("INSERT OR IGNORE INTO users(username,password,display_name,role,is_active,department) "
                       "VALUES(?,?,?,'user',1,'压测部')", (un, generate_password_hash('admin123'), dn))
        users.append((un, dn))
    maindb.commit()
    urows = {r['username']: r for r in maindb.execute("SELECT * FROM users WHERE username LIKE 'stress%'")}

    # 1.2 万条 WBS 待办
    t0 = time.time()
    for pi in range(60):
        cur = maindb.execute("INSERT INTO wbs_projects(name,status,owner,created_at) VALUES(?,?,?,?)",
                             ('压测项目%02d' % pi, '进行中', users[pi][0], '2026-11-20'))
        pid = cur.lastrowid
        rows = []
        for ti in range(200):
            rows.append((pid, 'T%03d' % ti, '压测任务%02d-%03d' % (pi, ti), users[pi][0],
                         '进行中', '已通过', '2026-11-2%d' % (ti % 8 + 1), ti % 100))
        maindb.executemany("INSERT INTO wbs_tasks(project_id,wbs_code,name,owner,status,approval_status,"
                           "plan_end,progress) VALUES(?,?,?,?,?,?,?,?)", rows)
    maindb.commit()
    print('WBS 造数 %.2fs' % (time.time() - t0))

    # 通知：每用户 300 条
    t0 = time.time()
    batch = []
    for i, (un, dn) in enumerate(users):
        uid = urows[un]['id']
        for j in range(300):
            batch.append((uid, un, dn, 'wbs', 'note%03d' % j, '压测通知%03d' % j, '内容',
                          '/workflow/instance/x', 'inst-%02d-%03d' % (i, j), 'BK'))
    maindb.executemany("INSERT INTO wf_notifications(user_id,username,display_name,plugin_id,event_key,"
                       "title,body,url,instance_id,business_key) VALUES(?,?,?,?,?,?,?,?,?,?)", batch)
    maindb.commit()
    print('通知造数 %.2fs' % (time.time() - t0))

    # 并发写同一通知：唯一索引应该只留 1 条
    uid = urows['stress00']['id']
    errors = []
    def worker(k):
        con = sqlite3.connect(str(db_path), timeout=30)
        con.row_factory = sqlite3.Row
        try:
            wf.init_tables(con)
            for _ in range(10):
                wf.notify_user(con, uid, '并发通知', '同一实例', 'wbs',
                               event_key='race', instance_id='race-inst', business_key='RACE')
        except Exception as e:
            errors.append(str(e))
        finally:
            con.close()
    threads = [threading.Thread(target=worker, args=(k,)) for k in range(40)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    cnt = con.execute("SELECT COUNT(*) c FROM wf_notifications WHERE instance_id='race-inst' AND event_key='race'").fetchone()['c']
    check('40 线程并发通知无异常', not errors, errors[:3])
    check('并发同一通知只保留 1 条', cnt == 1, cnt)
    con.close()

    # 工作台渲染 P95
    times = []
    failed = 0
    for i in range(20):
        client = appmod.app.test_client()
        client.post('/login', data={'username': users[i % 60][0], 'password': 'admin123'})
        start = time.perf_counter()
        r = client.get('/')
        times.append((time.perf_counter() - start) * 1000)
        if r.status_code != 200:
            failed += 1
    times.sort()
    p95 = times[int(len(times) * 0.95) - 1]
    check('工作台 20 次渲染全部 200', failed == 0, failed)
    check('工作台 P95 < 800ms', p95 < 800, '%.1fms' % p95)
    print('工作台 P50=%.1fms P95=%.1fms max=%.1fms' % (
        statistics.median(times), p95, max(times)))

    # 每个用户只应看到自己的 200 条 WBS 待办
    con = sqlite3.connect(str(db_path)); con.row_factory = sqlite3.Row
    u0 = urows['stress00']['id']
    with appmod.app.app_context():
        mods = plugin_system.collect_workbench_modules(con, {'id': u0, 'username': 'stress00',
                                                             'display_name': '压测用户00'})
    wbs_items = next((m['items'] for m in mods if m['plugin_id'] == 'wbs'), [])
    check('压测下个人待办仍只取本人 200 条', len(wbs_items) == 200, len(wbs_items))
    con.close()

    print('\n===== v13.6 压测：%s =====' % ('ALL PASS' if not fail else '%d FAILED' % len(fail)))
    return 1 if fail else 0


if __name__ == '__main__':
    sys.exit(main())
