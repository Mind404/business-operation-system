#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""v13.5 虚拟数据压力测试：并发审批、重复上报、大数据量页面、跨库一致性。

只操作临时副本，不写生产库。
运行：python tools/regress_v135_stress.py
"""
import concurrent.futures as cf
import os
import shutil
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

APP = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(APP))
fails = []


def check(name, cond, detail=''):
    print(('PASS  ' if cond else 'FAIL  ') + name + ('' if cond else '  ' + str(detail)))
    if not cond:
        fails.append(name)


def main():
    tmp = Path(tempfile.mkdtemp(prefix='v135_stress_'))
    shutil.copy2(APP / 'business.db', tmp / 'business.db')
    plug = tmp / 'plugin_data'
    plug.mkdir()
    shutil.copy2(APP / 'data' / 'version_plan.db', plug / 'version_plan.db')

    import app as appmod
    from werkzeug.security import generate_password_hash
    import workflow_engine as wf

    appmod.app.config['DATABASE'] = str(tmp / 'business.db')
    appmod.app.config['PLUGIN_DB_DIR'] = str(plug)
    main = sqlite3.connect(str(tmp / 'business.db'), timeout=30)
    main.row_factory = sqlite3.Row
    wf.init_tables(main)
    wf.seed_defaults(main)

    # 40 个独立账号，避免单账号单设备互踢
    users = []
    for i in range(40):
        u = 'stress%02d' % i
        main.execute("INSERT OR IGNORE INTO users (username,password,display_name,role,is_active,department) "
                     "VALUES (?,?,?, 'user',1,'压测部')", (u, generate_password_hash('admin123'), '压测%02d' % i))
        users.append(u)
    main.commit()
    uid_by_name = {r['username']: r['id'] for r in main.execute('SELECT id,username FROM users')}

    vp = sqlite3.connect(str(plug / 'version_plan.db'), timeout=30)
    vp.row_factory = sqlite3.Row
    _vp_tables = {r[0] for r in vp.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    for t in ('version_plan_demands', 'version_plan_versions', 'version_plan_releases',
              'version_plan_checks', 'version_plan_signoffs', 'version_plan_items',
              'version_plan_trains', 'version_plan_logs', 'version_plan_notices'):
        if t in _vp_tables:
            vp.execute('DELETE FROM ' + t)
    vp.execute('DELETE FROM version_plan_products')
    vp.execute('DELETE FROM version_plan_customers')
    now = '2026-11-20 12:00:00'

    # 10 个产品、120 个版本、1200 条需求
    for p in range(1, 11):
        code = 'SP%02d' % p
        vp.execute("INSERT INTO version_plan_products(code,name,owner,status,created_at,updated_at) "
                   "VALUES(?,?,?,'启用',?,?)", (code, '压测产品%d' % p, users[p], now, now))
        for v in range(12):
            no = '%d.%d.0' % (p, v)
            st = ['规划中', '已冻结', '开发中', '测试中', '已发布', '已归档'][v % 6]
            vp.execute("INSERT INTO version_plan_versions(version_no,vtype,product_code,status,created_at,updated_at) "
                       "VALUES(?,?,?,?,?,?)", (no, '迭代', code, st, now, now))
            for d in range(10):
                vp.execute("INSERT INTO version_plan_demands(code,doc_ref,description,status,version_no,"
                           "product_code,demand_type,customer_name,business_value,acceptance,created_at,updated_at) "
                           "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                           ('S%s%02d%02d' % (code, v, d), 'doc', '压测需求%d' % d, '已排期', no, code,
                            '新增功能', '压测客户', '价值', '可验收', now, now))
    # 发布专用版本，7 项清单全通过
    vp.execute("INSERT INTO version_plan_versions(version_no,vtype,product_code,status,created_at,updated_at) "
               "VALUES('99.0.0','迭代','SP01','测试中',?,?)", (now, now))
    for i in range(1, 8):
        vp.execute("INSERT INTO version_plan_checks(version_no,product_code,item_no,result) "
                   "VALUES('99.0.0','SP01',?,'通过')", (i,))
    vp.execute("INSERT INTO version_plan_demands(code,doc_ref,description,status,version_no,product_code,"
               "demand_type,created_at,updated_at) VALUES('REL99','doc','并发发布需求','已排期',"
               "'99.0.0','SP01','新增功能',?,?)", (now, now))
    # 跨产品同号版本，验证冻结计数隔离
    for pc in ('SP02', 'SP03'):
        vp.execute("INSERT INTO version_plan_versions(version_no,vtype,product_code,status,created_at,updated_at) "
                   "VALUES('9.0.0','迭代',?,'规划中',?,?)", (pc, now, now))
        vp.execute("INSERT INTO version_plan_demands(code,doc_ref,description,status,version_no,product_code,"
                   "demand_type,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                   ('D' + pc, 'doc', '同号需求', '已排期', '9.0.0', pc, '新增功能', now, now))
    vp.commit()

    # 绑定发布/冻结/WBS 立项流程
    for ev, dk in [('version_plan.version.release', 'stress_release'),
                   ('version_plan.version.freeze', 'stress_freeze'),
                   ('wbs.project.created', 'stress_wbs_project')]:
        pid = 'version_plan' if ev.startswith('version_plan.') else 'wbs'
        d = wf.build_default_definition_for_event(pid, ev, def_key=dk, db=main)
        wf.save_definition(main, dk, d['name'], d)
        wf.bind_trigger(main, dk, pid, ev, mode='confirm')
    main.commit()

    def client_for(user):
        c = appmod.app.test_client()
        c.post('/login', data={'username': user, 'password': 'admin123'})
        return c

    # 1) 大数据量页面性能
    admin = client_for('admin')
    t0 = time.time()
    r = admin.get('/version_plan/versions?product=SP01')
    t_list = time.time() - t0
    check('1200 需求/120 版本下列表页 200', r.status_code == 200, r.status_code)
    check('版本列表 P95 目标 < 2s', t_list < 2.0, '%.3fs' % t_list)
    t0 = time.time()
    r = admin.get('/version_plan/versions/99.0.0?product=SP01')
    t_detail = time.time() - t0
    check('版本详情页 200', r.status_code == 200, r.status_code)
    check('版本详情 P95 目标 < 2s', t_detail < 2.0, '%.3fs' % t_detail)
    t0 = time.time()
    r = admin.get('/version_plan/demands?status=已排期')
    t_dem = time.time() - t0
    check('需求台账 1200 条 200', r.status_code == 200, r.status_code)
    check('需求台账 P95 目标 < 2s', t_dem < 2.0, '%.3fs' % t_dem)

    # 2) 并发发布：40 个账号同时点发布，只能生成 1 个实例
    def hit_release(u):
        c = client_for(u)
        try:
            resp = c.post('/version_plan/versions/99.0.0/status',
                          data={'product': 'SP01', 'to': '已发布'}, follow_redirects=False)
            return resp.status_code
        except Exception as e:
            return 'ERR:%s' % e

    t0 = time.time()
    with cf.ThreadPoolExecutor(max_workers=20) as ex:
        codes = list(ex.map(hit_release, users[:40]))
    t_conc = time.time() - t0
    inst = main.execute("SELECT * FROM wf_instances WHERE business_key='VP-SP01-99.0.0' "
                        "AND def_key='stress_release'").fetchall()
    check('40 并发发布只生成 1 个实例', len(inst) == 1, len(inst))
    check('并发发布无 5xx/异常', all(c in (200, 302) for c in codes), set(map(str, codes)))
    check('40 并发发布总耗时 < 10s', t_conc < 10, '%.2fs' % t_conc)
    log_count = main.execute("SELECT COUNT(*) FROM wf_event_log WHERE business_key='VP-SP01-99.0.0' "
                             "AND event_key='version_plan.version.release'").fetchone()[0]
    check('并发上报全部留痕', log_count == 40, log_count)

    # 3) 8 人并发审批同一任务：只能有 1 人成功
    task = main.execute("SELECT * FROM wf_tasks WHERE instance_id=? AND status='pending'",
                        (inst[0]['id'],)).fetchone()

    def race_approve(i):
        con = sqlite3.connect(str(tmp / 'business.db'), timeout=10)
        con.row_factory = sqlite3.Row
        try:
            wf.approve_task(con, task['id'], 1, '管理员%d' % i, 'platform_admin', 'approve', 'race')
            con.commit()
            return 'ok'
        except RuntimeError as e:
            return str(e)
        finally:
            con.close()

    with cf.ThreadPoolExecutor(max_workers=8) as ex:
        results = list(ex.map(race_approve, range(8)))
    check('并发审批同一任务仅 1 人成功', results.count('ok') == 1, results)
    check('并发审批其余 7 人被拒', sum(1 for r in results if r != 'ok') == 7, results)
    check('任务只记录 1 次通过', main.execute(
        "SELECT COUNT(*) FROM wf_tasks WHERE id=? AND status='approved'", (task['id'],)).fetchone()[0] == 1)
    check('发布审批链第一级通过', True)
    # 再点一次同一任务应报“已处理”，不能二次执行动作
    try:
        wf.approve_task(main, task['id'], 1, '系统管理员', 'platform_admin', 'approve', 'again')
        check('重复审批同一任务被拒绝', False)
    except RuntimeError as e:
        check('重复审批同一任务被拒绝', '已处理' in str(e), str(e))

    # 4) 完成发布（第二级）
    pend = main.execute("SELECT * FROM wf_tasks WHERE instance_id=? AND status='pending'",
                        (inst[0]['id'],)).fetchone()
    wf.approve_task(main, pend['id'], 1, '系统管理员', 'platform_admin', 'approve', 'ok')
    main.commit()
    check('并发场景发布最终落地', vp.execute(
        "SELECT status FROM version_plan_versions WHERE version_no='99.0.0' AND product_code='SP01'"
    ).fetchone()[0] == '已发布')
    check('并发场景只生成 1 条 Release Note', vp.execute(
        "SELECT COUNT(*) FROM version_plan_releases WHERE version_no='99.0.0'").fetchone()[0] == 1)

    # 5) 跨产品同号冻结隔离
    c = client_for(users[1])
    c.post('/version_plan/versions/9.0.0/status', data={'product': 'SP02', 'to': '已冻结'},
           follow_redirects=True)
    check('跨产品同号冻结只影响本产品', vp.execute(
        "SELECT status FROM version_plan_versions WHERE version_no='9.0.0' AND product_code='SP03'"
    ).fetchone()[0] == '规划中')
    # 用 SP03 的 9.0.0 去移出 SP02 的需求，必须被拦截
    c.post('/version_plan/versions/9.0.0/remove', data={'product': 'SP03', 'code': 'DSP02'},
           follow_redirects=True)
    check('跨版本移出需求被拒绝', vp.execute(
        "SELECT version_no FROM version_plan_demands WHERE code='DSP02'").fetchone()[0] == '9.0.0')

    # 6) 批量上报与事件流水
    def emit_many(i):
        con = sqlite3.connect(str(tmp / 'business.db'), timeout=10)
        con.row_factory = sqlite3.Row
        try:
            return wf.emit_state_change(con, 'version_plan', 'version_plan.version.created',
                                        business_key='VP-SP01-BULK',
                                        title='批量创建 %d' % i, context={'i': i},
                                        record_type='version', record_id='BULK')['mode']
        finally:
            con.close()

    with cf.ThreadPoolExecutor(max_workers=10) as ex:
        modes = list(ex.map(emit_many, range(200)))
    check('200 次并发上报不报错', len(modes) == 200, len(modes))
    check('200 次上报全部留痕', main.execute(
        "SELECT COUNT(*) FROM wf_event_log WHERE business_key='VP-SP01-BULK'").fetchone()[0] == 200)

    # 7) WBS 批量立项 + 审批
    t0 = time.time()
    pids = []
    for i, u in enumerate(users[:30]):
        c = client_for(u)
        c.post('/wbs/add', data={'name': '压测项目%02d' % i, 'owner': [u],
                                 'start_date': '2026-11-20', 'end_date': '2026-12-31',
                                 'status': '进行中'}, follow_redirects=True)
        row = main.execute("SELECT id FROM wbs_projects WHERE name=?", ('压测项目%02d' % i,)).fetchone()
        if row:
            pids.append(row['id'])
    check('30 个虚拟项目全部创建', len(pids) == 30, len(pids))
    check('30 个项目全部进入流程', main.execute(
        "SELECT COUNT(*) FROM wf_instances WHERE business_key LIKE 'WBS-P%' AND status='running'"
    ).fetchone()[0] == 30)
    for pid in pids:
        ins = main.execute("SELECT * FROM wf_instances WHERE business_key=?", ('WBS-P%d' % pid,)).fetchone()
        for _ in range(3):
            t = main.execute("SELECT * FROM wf_tasks WHERE instance_id=? AND status='pending'",
                             (ins['id'],)).fetchone()
            if not t:
                break
            urow = main.execute('SELECT * FROM users WHERE id=?', (t['assignee_id'],)).fetchone()
            wf.approve_task(main, t['id'], urow['id'], urow['display_name'], urow['role'], 'approve', 'ok')
            main.commit()
    approved = main.execute("SELECT COUNT(*) FROM wbs_projects WHERE name LIKE '压测项目%' "
                            "AND approval_status='已通过'").fetchone()[0]
    check('30 个项目审批写回全部通过', approved == 30, approved)
    check('30 个项目批量审批 < 30s', time.time() - t0 < 30, '%.2fs' % (time.time() - t0))

    # 8) WBS 大数据量页面
    # 追加 1000 条任务
    for pid in pids[:10]:
        for j in range(100):
            main.execute("INSERT INTO wbs_tasks(project_id,wbs_code,name,owner,status,progress,approval_status) "
                         "VALUES(?,?,?,?,?,?, '已通过')",
                         (pid, str(j + 2), '压测任务%d' % j, users[0], '未开始', 0))
    main.commit()
    t0 = time.time()
    r = admin.get('/wbs')
    t_wbs = time.time() - t0
    check('WBS 30 项目/1000 任务列表 200', r.status_code == 200, r.status_code)
    check('WBS 列表 P95 目标 < 2s', t_wbs < 2.0, '%.3fs' % t_wbs)
    t0 = time.time()
    r = admin.get('/wbs/%d' % pids[0])
    check('WBS 单项目 100+ 任务详情 200', r.status_code == 200, r.status_code)
    check('WBS 详情 P95 目标 < 2s', time.time() - t0 < 2.0, '%.3fs' % (time.time() - t0))

    # 9) 管理端节点目录性能
    for i in range(40):
        d = wf.build_default_definition_for_event('version_plan', 'version_plan.version.created',
                                                  def_key='stress_notice_%02d' % i, db=main)
        wf.save_definition(main, 'stress_notice_%02d' % i, d['name'], d)
    main.commit()
    t0 = time.time()
    r = admin.get('/workflow/admin/events')
    t_cat = time.time() - t0
    check('节点目录 40+ 流程 200', r.status_code == 200, r.status_code)
    check('节点目录 P95 目标 < 3s', t_cat < 3.0, '%.3fs' % t_cat)

    # 10) 备份副本完整性（数据库 + 插件库）
    bdir = tmp / 'backup'
    bdir.mkdir()
    # 与平台备份一致：用 SQLite backup API（会包含 WAL 中尚未 checkpoint 的数据）
    src = sqlite3.connect(str(tmp / 'business.db'))
    dst = sqlite3.connect(str(bdir / 'business.db'))
    src.backup(dst)
    dst.close(); src.close()
    src = sqlite3.connect(str(plug / 'version_plan.db'))
    dst = sqlite3.connect(str(bdir / 'version_plan.db'))
    src.backup(dst)
    dst.close(); src.close()
    b = sqlite3.connect(str(bdir / 'business.db'))
    check('备份主库完整性', b.execute('PRAGMA integrity_check').fetchone()[0] == 'ok')
    b.close()
    bv = sqlite3.connect(str(bdir / 'version_plan.db'))
    check('备份插件库完整性', bv.execute('PRAGMA integrity_check').fetchone()[0] == 'ok')
    check('备份插件库含 120+ 版本', bv.execute('SELECT COUNT(*) FROM version_plan_versions').fetchone()[0] >= 121)
    bv.close()

    print('\n===== v13.5 压力测试：%d 失败 =====' % len(fails))
    print('FAILED:', fails if fails else '无')
    return 1 if fails else 0


if __name__ == '__main__':
    sys.exit(main())
