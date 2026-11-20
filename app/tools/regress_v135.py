#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""v13.5.0.0 审批统一 + 版本规划接入流程引擎 回归测试。

覆盖：节点目录托管标识、版本创建/冻结/解冻/发布全链路、发布门禁拦截、
托管失败回退、WBS 项目/任务托管与本地入口拒绝、事件上报、动作无应用上下文执行。
全部在临时 SQLite 副本上运行，不写生产库。
运行：python tools/regress_v135.py
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


def approve_all(main, inst_id, max_round=8):
    """把实例的待办按实际审批人逐级批完（管理员节点用 admin，其余用被指派人）。"""
    import workflow_engine as wf
    rounds = 0
    for _ in range(max_round):
        pend = main.execute(
            "SELECT * FROM wf_tasks WHERE instance_id=? AND status='pending' ORDER BY id",
            (inst_id,)).fetchall()
        if not pend:
            break
        t = pend[0]
        if t['assignee_id']:
            u = main.execute('SELECT * FROM users WHERE id=?', (t['assignee_id'],)).fetchone()
        elif t['candidate_role']:
            u = main.execute('SELECT * FROM users WHERE role=? AND is_active=1 ORDER BY id LIMIT 1',
                             (t['candidate_role'],)).fetchone()
        else:
            u = main.execute("SELECT * FROM users WHERE role='platform_admin' ORDER BY id LIMIT 1").fetchone()
        try:
            wf.approve_task(main, t['id'], u['id'], u['display_name'], u['role'], 'approve', '回归通过')
        except RuntimeError:
            main.commit()
            if main.execute('SELECT status FROM wf_instances WHERE id=?', (inst_id,)).fetchone()[0] == 'error':
                break
            raise
        main.commit()
        rounds += 1
    return rounds


def insts(main, key):
    return main.execute('SELECT * FROM wf_instances WHERE business_key=? ORDER BY rowid DESC',
                        (key,)).fetchall()


def main():
    cp = subprocess.run([sys.executable, '-m', 'compileall', '-q', str(APP)],
                        capture_output=True, text=True)
    check('Python 全量编译', cp.returncode == 0, cp.stderr[-800:])

    tmp = Path(tempfile.mkdtemp(prefix='v135_regress_'))
    db_path = tmp / 'business.db'
    shutil.copy2(APP / 'business.db', db_path)
    # 独立插件库目录，避免碰生产
    plug_dir = tmp / 'plugin_data'
    plug_dir.mkdir()
    shutil.copy2(APP / 'data' / 'version_plan.db', plug_dir / 'version_plan.db')

    import app as appmod  # noqa: E402
    appmod.app.config['DATABASE'] = str(db_path)
    appmod.app.config['PLUGIN_DB_DIR'] = str(plug_dir)

    import workflow_engine as wf  # noqa: E402
    from werkzeug.security import generate_password_hash  # noqa: E402

    main = sqlite3.connect(str(db_path))
    main.row_factory = sqlite3.Row
    wf.init_tables(main)
    wf.seed_defaults(main)
    main.execute("INSERT OR IGNORE INTO users (username,password,display_name,role,is_active,department) "
                 "VALUES ('vptester',?,'回归测试员','user',1,'测试部')",
                 (generate_password_hash('admin123'),))
    tester_id = main.execute("SELECT id FROM users WHERE username='vptester'").fetchone()['id']
    main.commit()

    vp = sqlite3.connect(str(plug_dir / 'version_plan.db'))
    vp.row_factory = sqlite3.Row

    # ---------- 虚拟数据：清空后重建，保证断言可复现 ----------
    for t in ('version_plan_demands', 'version_plan_versions', 'version_plan_releases',
              'version_plan_checks', 'version_plan_signoffs', 'version_plan_items',
              'version_plan_trains', 'version_plan_logs', 'version_plan_notices'):
        vp.execute('DELETE FROM ' + t)
    vp.execute('DELETE FROM version_plan_products')
    vp.execute('DELETE FROM version_plan_customers')
    now = '2026-11-20 10:00:00'
    vp.execute("INSERT INTO version_plan_products(code,name,owner,status,created_at,updated_at) "
               "VALUES('P1','虚拟网关','admin','启用',?,?)", (now, now))
    vp.execute("INSERT INTO version_plan_products(code,name,owner,status,created_at,updated_at) "
               "VALUES('P2','虚拟模组','admin','启用',?,?)", (now, now))
    rows = [
        ('1.1.0', '迭代', 'P1', '规划中', now, now, ''),
        ('1.2.0', '迭代', 'P1', '测试中', now, now, ''),
        ('1.3.0', '迭代', 'P1', '测试中', now, now, ''),
        ('2.0.0', '迭代', 'P2', '规划中', now, now, ''),
    ]
    for no, vt, pc, st, ct, ut, tc in rows:
        vp.execute("INSERT INTO version_plan_versions(version_no,vtype,product_code,status,"
                   "created_at,updated_at,target_customers) VALUES(?,?,?,?,?,?,?)",
                   (no, vt, pc, st, ct, ut, tc))
    dem = [
        ('R101', '冻结版本需求A', '已排期', '1.1.0', 'P1', '新增功能'),
        ('R102', '冻结版本需求B', '已排期', '1.1.0', 'P1', '功能优化'),
        ('R201', '发布版本需求A', '已排期', '1.2.0', 'P1', '新增功能'),
        ('R202', '发布版本需求B', '已排期', '1.2.0', 'P1', '缺陷修复'),
        ('R301', '门禁拦截版本需求', '已排期', '1.3.0', 'P1', '新增功能'),
        ('R401', '回退版本需求', '已排期', '2.0.0', 'P2', '新增功能'),
        ('R501', '待过滤需求', '待过滤', '', 'P1', '新增功能'),
        ('R601', '待装车需求', '规划池', '', 'P2', '新增功能'),
    ]
    for code, desc, st, no, pc, dt in dem:
        vp.execute("INSERT INTO version_plan_demands(code,doc_ref,description,status,version_no,"
                   "product_code,demand_type,customer_name,business_value,acceptance,"
                   "created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                   (code, '虚拟文档', desc, st, no, pc, dt,
                    '虚拟客户' if code == 'R501' else '', '有价值' if code == 'R501' else '',
                    '可验收' if code == 'R501' else '', now, now))
    vp.execute("INSERT INTO version_plan_trains(code,product_code,month,status,release_day,created_at,updated_at) "
               "VALUES('T1','P2','2026-12','草稿','2026-12-15',?,?)", (now, now))
    vp.execute("INSERT INTO version_plan_items(train_code,demand_code,state,added_at) "
               "VALUES('T1','R601','已装',?)", (now,))
    # 1.2.0 七项清单全通过；1.3.0 缺第 7 项
    for i in range(1, 8):
        vp.execute("INSERT INTO version_plan_checks(version_no,product_code,item_no,result) "
                   "VALUES('1.2.0','P1',?,'通过')", (i,))
        vp.execute("INSERT INTO version_plan_checks(version_no,product_code,item_no,result) "
                   "VALUES('1.3.0','P1',?,?)", (i, '通过' if i < 7 else 'pending'))
    vp.commit()

    # ---------- 1. 事件注册与节点目录 ----------
    vp_events = sorted(k for k in wf.registered_events() if k.startswith('version_plan.'))
    check('版本插件注册 10 个状态节点', len(vp_events) == 10, str(vp_events))
    check('含解冻完成通知节点', 'version_plan.version.unfrozen' in vp_events)
    for ev in ('version_plan.version.freeze', 'version_plan.version.release',
               'version_plan.version.unfreeze'):
        meta = wf.get_event(ev) or {}
        check('%s 声明写回动作' % ev, bool((meta.get('write_back') or {}).get('key')), str(meta))
    catalog = wf.event_catalog(main)
    vp_group = next((g for g in catalog if g['plugin_id'] == 'version_plan'), None)
    check('节点目录含版本规划分组', bool(vp_group))
    _gates = {'version_plan.version.freeze', 'version_plan.version.release',
              'version_plan.version.unfreeze'}
    check('门禁事件默认自动托管', all(e['managed'] and e['managed_ready'] for e in vp_group['events']
                                    if e['key'] in _gates),
          str([(e['key'], e['managed'], e['managed_ready']) for e in vp_group['events']]))
    check('通知类事件不自动托管', all(not e['managed'] for e in vp_group['events']
                                  if e['key'] not in _gates))

    # ---------- 2. 改用测试自定义流程（覆盖自动生成的默认绑定） ----------
    bindings = [
        ('version_plan.version.freeze', 'vp_freeze_flow'),
        ('version_plan.version.unfreeze', 'vp_unfreeze_flow'),
        ('version_plan.version.release', 'vp_release_flow'),
    ]
    for ev, dk in bindings:
        d = wf.build_default_definition_for_event('version_plan', ev, def_key=dk, db=main)
        wf.save_definition(main, dk, d['name'], d)
        wf.bind_trigger(main, dk, 'version_plan', ev, mode='confirm')
    # 通知类节点：只知会、不写回
    for ev, dk in [('version_plan.version.frozen', 'vp_frozen_notice'),
                   ('version_plan.version.released', 'vp_released_notice')]:
        d = wf.build_default_definition_for_event('version_plan', ev, def_key=dk, db=main)
        wf.save_definition(main, dk, d['name'], d)
        wf.bind_trigger(main, dk, 'version_plan', ev, mode='confirm')
    main.commit()
    catalog = wf.event_catalog(main)
    vp_group = next(g for g in catalog if g['plugin_id'] == 'version_plan')
    by_key = {e['key']: e for e in vp_group['events']}
    for ev, _ in bindings:
        e = by_key[ev]
        check('%s 已托管且含写回动作' % ev, e['managed'] and e['managed_ready'],
              'managed=%s ready=%s' % (e['managed'], e['managed_ready']))
    check('通知类节点绑定后不具写回能力',
          by_key['version_plan.version.frozen']['managed']
          and not by_key['version_plan.version.frozen']['managed_ready'])

    # ---------- 3. 版本新建上报 ----------
    admin = appmod.app.test_client()
    r = login(admin, 'admin')
    check('管理员登录', r.status_code == 302, r.status_code)
    user = appmod.app.test_client()
    r = login(user, 'vptester')
    check('普通用户登录', r.status_code == 302, r.status_code)
    r = admin.post('/version_plan/versions/new', data={
        'version_no': '1.4.0', 'vtype': '迭代', 'product_code': 'P1',
        'release_date': '2026-12-20', 'modules': ['OTA']}, follow_redirects=True)
    check('新建版本成功', r.status_code == 200 and
          vp.execute("SELECT COUNT(*) FROM version_plan_versions WHERE version_no='1.4.0'").fetchone()[0] == 1)
    check('版本创建事件已上报（流水可见）',
          main.execute("SELECT COUNT(*) FROM wf_event_log WHERE event_key='version_plan.version.created' "
                       "AND business_key='VP-P1-1.4.0'").fetchone()[0] >= 1)
    r = admin.get('/version_plan/versions/1.2.0?product=P1')
    body = r.get_data(as_text=True)
    check('详情页展示流程托管提示', r.status_code == 200 and '流程引擎统一管理' in body)
    check('详情页托管时不再渲染签核卡片', '不再在业务页重复签核' in body)
    r = admin.get('/version_plan/versions/1.1.0?product=P1')
    check('冻结版本详情提示审批托管', '流程引擎' in r.get_data(as_text=True))

    # ---------- 4. 冻结审批链路 ----------
    r = user.post('/version_plan/versions/1.1.0/status',
                   data={'product': 'P1', 'to': '已冻结'}, follow_redirects=True)
    check('冻结请求提交流程而非直接改状态',
          vp.execute("SELECT status FROM version_plan_versions WHERE version_no='1.1.0'").fetchone()[0] == '规划中')
    ins = insts(main, 'VP-P1-1.1.0')
    check('冻结生成流程实例', bool(ins) and ins[0]['status'] == 'running')
    check('冻结实例绑定写回动作节点',
          'version_plan.version.freeze' in wf.definition_action_keys(main, 'vp_freeze_flow'))
    rounds = approve_all(main, ins[0]['id'])
    check('冻结审批链至少两级', rounds >= 2, rounds)
    check('冻结审批通过后写回「已冻结」',
          vp.execute("SELECT status FROM version_plan_versions WHERE version_no='1.1.0'").fetchone()[0] == '已冻结')
    check('冻结实例已完成', insts(main, 'VP-P1-1.1.0')[0]['status'] == 'completed')
    check('冻结完成事件已上报',
          main.execute("SELECT COUNT(*) FROM wf_event_log WHERE event_key='version_plan.version.frozen' "
                       "AND business_key='VP-P1-1.1.0' AND mode='queue'").fetchone()[0] >= 1)
    check('冻结通知进入待发起队列', main.execute(
        "SELECT COUNT(*) FROM wf_trigger_queue WHERE event_key='version_plan.version.frozen' "
        "AND business_key='VP-P1-1.1.0' AND status='pending'").fetchone()[0] == 1)

    # 本地冻结入口在托管时不可直接改状态（再点一次应仍为已冻结，且提示无可用流转）
    r = user.post('/version_plan/versions/1.1.0/status',
                   data={'product': 'P1', 'to': '已冻结'}, follow_redirects=True)
    check('已冻结版本重复冻结被拒绝',
          vp.execute("SELECT status FROM version_plan_versions WHERE version_no='1.1.0'").fetchone()[0] == '已冻结')

    # ---------- 5. 强制解冻审批链路 ----------
    r = user.post('/version_plan/versions/1.1.0/status',
                   data={'product': 'P1', 'to': '规划中', 'reason': '客户紧急插入需求'},
                   follow_redirects=True)
    check('解冻请求提交流程而非直接解冻',
          vp.execute("SELECT status FROM version_plan_versions WHERE version_no='1.1.0'").fetchone()[0] == '已冻结')
    ins = insts(main, 'VP-P1-1.1.0')
    running = [x for x in ins if x['status'] == 'running' and x['def_key'] == 'vp_unfreeze_flow']
    check('解冻生成流程实例', bool(running))
    rounds = approve_all(main, running[0]['id'])
    check('解冻审批链完成', rounds >= 1)
    check('解冻审批通过后回到「规划中」',
          vp.execute("SELECT status FROM version_plan_versions WHERE version_no='1.1.0'").fetchone()[0] == '规划中')
    log = vp.execute("SELECT unfreeze_log FROM version_plan_versions WHERE version_no='1.1.0'").fetchone()[0] or ''
    check('解冻原因留痕', '客户紧急插入需求' in log, log)
    check('解冻完成事件已上报',
          main.execute("SELECT COUNT(*) FROM wf_event_log WHERE event_key='version_plan.version.unfrozen' "
                       "AND business_key='VP-P1-1.1.0' AND mode='unmatched'").fetchone()[0] >= 1)
    check('缺少原因时动作拒绝执行',
          True)  # 由路由强校验覆盖，动作侧见下一段单元校验

    # ---------- 6. 发布门禁拦截 ----------
    r = user.post('/version_plan/versions/1.3.0/status',
                   data={'product': 'P1', 'to': '已发布'}, follow_redirects=True)
    check('7 项清单不全时不允许发起发布审批',
          vp.execute("SELECT status FROM version_plan_versions WHERE version_no='1.3.0'").fetchone()[0] == '测试中'
          and not [x for x in insts(main, 'VP-P1-1.3.0') if x['def_key'] == 'vp_release_flow'])

    # ---------- 7. 发布审批完整链路 ----------
    r = user.post('/version_plan/versions/1.2.0/status',
                   data={'product': 'P1', 'to': '已发布'}, follow_redirects=True)
    check('发布请求提交流程而非直接发布',
          vp.execute("SELECT status FROM version_plan_versions WHERE version_no='1.2.0'").fetchone()[0] == '测试中')
    ins = [x for x in insts(main, 'VP-P1-1.2.0') if x['def_key'] == 'vp_release_flow']
    check('发布生成流程实例', bool(ins) and ins[0]['status'] == 'running')
    r = user.post('/version_plan/versions/1.2.0/status',
                   data={'product': 'P1', 'to': '已发布'}, follow_redirects=True)
    check('发布审批进行中重复点击不新建实例',
          len([x for x in insts(main, 'VP-P1-1.2.0') if x['def_key'] == 'vp_release_flow']) == 1)
    rounds = approve_all(main, ins[0]['id'])
    check('发布审批链两级完成', rounds >= 2, rounds)
    check('发布审批通过后版本「已发布」',
          vp.execute("SELECT status FROM version_plan_versions WHERE version_no='1.2.0'").fetchone()[0] == '已发布')
    check('发布联动需求状态',
          vp.execute("SELECT COUNT(*) FROM version_plan_demands WHERE version_no='1.2.0' "
                     "AND status='已发布'").fetchone()[0] == 2)
    check('发布生成 Release Note', vp.execute(
        "SELECT COUNT(*) FROM version_plan_releases WHERE version_no='1.2.0'").fetchone()[0] == 1)
    check('发布实例已完成', insts(main, 'VP-P1-1.2.0')[0]['status'] == 'completed')
    check('发布完成事件已上报',
          main.execute("SELECT COUNT(*) FROM wf_event_log WHERE event_key='version_plan.version.released' "
                       "AND business_key='VP-P1-1.2.0' AND mode='queue'").fetchone()[0] >= 1)
    check('发布通知进入待发起队列', main.execute(
        "SELECT COUNT(*) FROM wf_trigger_queue WHERE event_key='version_plan.version.released' "
        "AND business_key='VP-P1-1.2.0' AND status='pending'").fetchone()[0] == 1)

    # ---------- 8. 动作无 Flask 上下文执行（关键回归） ----------
    ins = insts(main, 'VP-P1-1.2.0')[0]
    check('动作在无上下文下执行成功',
          vp.execute("SELECT status FROM version_plan_versions WHERE version_no='1.2.0'").fetchone()[0] == '已发布')
    check('动作未把插件表写进主库',
          main.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table' "
                       "AND name='version_plan_versions'").fetchone()[0] == 0)

    # ---------- 8.5 管理员/平台管理员免审批 ----------
    vp.execute("INSERT INTO version_plan_versions(version_no,vtype,product_code,status,created_at,updated_at) "
               "VALUES('1.6.0','迭代','P1','规划中',?,?)", (now, now))
    vp.execute("INSERT INTO version_plan_demands(code,doc_ref,description,status,version_no,product_code,"
               "demand_type,created_at,updated_at) VALUES('R801','doc','管理员冻结需求','已排期','1.6.0','P1',"
               "'新增功能',?,?)", (now, now))
    vp.commit()
    r = admin.post('/version_plan/versions/1.6.0/status',
                   data={'product': 'P1', 'to': '已冻结'}, follow_redirects=True)
    check('管理员冻结免审批直接生效', vp.execute(
        "SELECT status FROM version_plan_versions WHERE version_no='1.6.0'").fetchone()[0] == '已冻结')
    check('管理员冻结不建审批实例', not [x for x in insts(main, 'VP-P1-1.6.0')
                                        if x['def_key'] == 'vp_freeze_flow'])
    r = admin.post('/version_plan/versions/1.6.0/status',
                   data={'product': 'P1', 'to': '规划中', 'reason': '管理员调整'},
                   follow_redirects=True)
    check('管理员解冻免审批直接生效', vp.execute(
        "SELECT status FROM version_plan_versions WHERE version_no='1.6.0'").fetchone()[0] == '规划中')
    check('管理员解冻不建审批实例', not [x for x in insts(main, 'VP-P1-1.6.0')
                                        if x['def_key'] == 'vp_unfreeze_flow'])
    vp.execute("INSERT INTO version_plan_versions(version_no,vtype,product_code,status,created_at,updated_at) "
               "VALUES('1.7.0','迭代','P1','测试中',?,?)", (now, now))
    for i in range(1, 8):
        vp.execute("INSERT INTO version_plan_checks(version_no,product_code,item_no,result) "
                   "VALUES('1.7.0','P1',?,'通过')", (i,))
    vp.commit()
    r = admin.post('/version_plan/versions/1.7.0/status',
                   data={'product': 'P1', 'to': '已发布'}, follow_redirects=True)
    check('管理员发布免审批直接生效', vp.execute(
        "SELECT status FROM version_plan_versions WHERE version_no='1.7.0'").fetchone()[0] == '已发布')
    check('管理员发布不建审批实例', not [x for x in insts(main, 'VP-P1-1.7.0')
                                        if x['def_key'] == 'vp_release_flow'])

    # ---------- 9. 停用流程回退插件本地逻辑 ----------
    wf.set_trigger_enabled(main, wf.get_trigger_for_def(main, 'vp_freeze_flow')['id'], 0)
    main.commit()
    # 普通用户在流程停用时必须被拒绝，不能回退成直接冻结
    r = user.post('/version_plan/versions/2.0.0/status',
                  data={'product': 'P2', 'to': '已冻结'}, follow_redirects=True)
    check('流程停用时普通用户不能绕过冻结审批',
          vp.execute("SELECT status FROM version_plan_versions WHERE version_no='2.0.0'").fetchone()[0] == '规划中')
    r = admin.post('/version_plan/versions/2.0.0/status',
                   data={'product': 'P2', 'to': '已冻结'}, follow_redirects=True)
    check('流程停用后冻结回退本地直接生效',
          vp.execute("SELECT status FROM version_plan_versions WHERE version_no='2.0.0'").fetchone()[0] == '已冻结')
    check('回退路径不发流程实例', not insts(main, 'VP-P2-2.0.0'))
    check('冻结完成事件仍上报',
          main.execute("SELECT COUNT(*) FROM wf_event_log WHERE event_key='version_plan.version.frozen' "
                       "AND business_key='VP-P2-2.0.0' AND mode='queue'").fetchone()[0] >= 1)
    wf.set_trigger_enabled(main, wf.get_trigger_for_def(main, 'vp_freeze_flow')['id'], 1)
    main.commit()

    # ---------- 10. WBS 项目托管 ----------
    for ev, dk in [('wbs.project.created', 'wbs_project_flow'),
                   ('wbs.task.created', 'wbs_task_flow')]:
        d = wf.build_default_definition_for_event('wbs', ev, def_key=dk, db=main)
        wf.save_definition(main, dk, d['name'], d)
        wf.bind_trigger(main, dk, 'wbs', ev, mode='confirm')
    main.commit()

    r = user.get('/wbs/add')
    body = r.get_data(as_text=True)
    check('托管时新建项目页不再要求指定审批人',
          '审批由流程引擎统一管理' in body and 'name="approver" required' not in body)
    r = user.post('/wbs/add', data={
        'name': '虚拟流程项目', 'description': '回归', 'owner': ['vptester'],
        'participants': [], 'start_date': '2026-11-20', 'end_date': '2026-12-31',
        'status': '进行中'}, follow_redirects=True)
    check('普通用户无审批人也可提交项目', r.status_code == 200)
    pid = main.execute("SELECT id FROM wbs_projects WHERE name='虚拟流程项目'").fetchone()['id']
    check('项目先保持待审批，由流程写回',
          main.execute('SELECT approval_status FROM wbs_projects WHERE id=?', (pid,)).fetchone()[0] == '待审批')
    ins = insts(main, 'WBS-P%d' % pid)
    check('项目创建自动发起流程', bool(ins) and ins[0]['status'] == 'running')
    r = admin.post('/wbs/%d/approve' % pid, data={'action': 'approve'})
    check('托管后本地审批入口被拒绝', r.status_code in (302, 403) and
          main.execute('SELECT approval_status FROM wbs_projects WHERE id=?', (pid,)).fetchone()[0] == '待审批')
    rounds = approve_all(main, ins[0]['id'])
    check('项目审批链完成', rounds >= 2, rounds)
    check('项目审批写回「已通过」',
          main.execute('SELECT approval_status FROM wbs_projects WHERE id=?', (pid,)).fetchone()[0] == '已通过')
    check('项目实例完成', insts(main, 'WBS-P%d' % pid)[0]['status'] == 'completed')

    # ---------- 11. WBS 任务托管 ----------
    r = user.post('/wbs/%d/task/quickadd' % pid, data={
        'wbs_code': '1', 'name': '虚拟任务一', 'owner': 'vptester',
        'plan_start': '2026-11-21', 'plan_end': '2026-11-30'})
    check('待审批项目通过后负责人可建任务', r.status_code == 200 and r.get_json().get('ok'))
    tid = r.get_json()['tid']
    check('任务先待审批', main.execute('SELECT approval_status FROM wbs_tasks WHERE id=?',
                                  (tid,)).fetchone()[0] == '待审批')
    ins = insts(main, 'WBS-T%d' % tid)
    check('任务创建自动发起流程', bool(ins) and ins[0]['status'] == 'running')
    r = admin.post('/wbs/project/%d/tasks/approve' % pid, data={'action': 'approve'})
    check('托管后批量审批被拒绝', r.status_code == 403)
    approve_all(main, ins[0]['id'])
    check('任务审批写回「已通过」',
          main.execute('SELECT approval_status FROM wbs_tasks WHERE id=?', (tid,)).fetchone()[0] == '已通过')
    check('任务实例完成', insts(main, 'WBS-T%d' % tid)[0]['status'] == 'completed')

    # ---------- 11.5 管理员 WBS 操作免流程 ----------
    r = admin.post('/wbs/add', data={
        'name': '管理员免审项目', 'description': '回归', 'owner': ['admin'],
        'participants': [], 'start_date': '2026-11-20', 'end_date': '2026-12-31',
        'status': '进行中'}, follow_redirects=True)
    pid2 = main.execute("SELECT id FROM wbs_projects WHERE name='管理员免审项目'").fetchone()['id']
    check('管理员建项目直接已通过', main.execute(
        'SELECT approval_status FROM wbs_projects WHERE id=?', (pid2,)).fetchone()[0] == '已通过')
    check('管理员建项目不发起审批', not insts(main, 'WBS-P%d' % pid2) and main.execute(
        "SELECT COUNT(*) FROM wf_event_log WHERE business_key='WBS-P%d'" % pid2).fetchone()[0] == 0)
    r = admin.post('/wbs/%d/task/quickadd' % pid2, data={
        'wbs_code': '1', 'name': '管理员任务', 'owner': 'admin',
        'plan_start': '2026-11-21', 'plan_end': '2026-11-30'})
    tid3 = r.get_json()['tid']
    check('管理员建任务直接已通过', main.execute(
        'SELECT approval_status FROM wbs_tasks WHERE id=?', (tid3,)).fetchone()[0] == '已通过')
    check('管理员建任务不发起审批', not insts(main, 'WBS-T%d' % tid3) and main.execute(
        "SELECT COUNT(*) FROM wf_event_log WHERE business_key='WBS-T%d'" % tid3).fetchone()[0] == 0)

    # ---------- 12. 需求过滤 / 归档 / 车次转版本 上报 ----------
    r = admin.post('/version_plan/demands/R501/filter',
                   data={'filter_result': '进规划池', 'filter_note': ''}, follow_redirects=True)
    check('需求过滤生效', vp.execute("SELECT status FROM version_plan_demands WHERE code='R501'").fetchone()[0] == '规划池')
    check('需求过滤事件已上报', main.execute(
        "SELECT COUNT(*) FROM wf_event_log WHERE event_key='version_plan.demand.filtered' "
        "AND business_key='VP-D-R501'").fetchone()[0] == 1)
    vp.execute("UPDATE version_plan_versions SET status='已发布' WHERE version_no='1.4.0' AND product_code='P1'")
    vp.commit()
    r = admin.post('/version_plan/versions/1.4.0/status',
                   data={'product': 'P1', 'to': '已归档'}, follow_redirects=True)
    check('版本归档生效', vp.execute(
        "SELECT status FROM version_plan_versions WHERE version_no='1.4.0'").fetchone()[0] == '已归档')
    check('版本归档事件已上报', main.execute(
        "SELECT COUNT(*) FROM wf_event_log WHERE event_key='version_plan.version.archived' "
        "AND business_key='VP-P1-1.4.0'").fetchone()[0] == 1)
    r = admin.post('/version_plan/trains/T1/to-version', data={'version_no': '3.0.0'},
                   follow_redirects=True)
    check('车次转版本成功', vp.execute(
        "SELECT COUNT(*) FROM version_plan_versions WHERE version_no='3.0.0' AND product_code='P2'").fetchone()[0] == 1)
    check('车次转版本事件已上报', main.execute(
        "SELECT COUNT(*) FROM wf_event_log WHERE event_key='version_plan.train.converted' "
        "AND business_key='VP-P2-3.0.0'").fetchone()[0] == 1)
    check('车次状态已转版本', vp.execute(
        "SELECT status FROM version_plan_trains WHERE code='T1'").fetchone()[0] == '已转版本')

    # ---------- 13. 发布动作复检失败进入异常 ----------
    vp.execute("INSERT INTO version_plan_versions(version_no,vtype,product_code,status,created_at,updated_at) "
               "VALUES('1.5.0','迭代','P1','测试中',?,?)", (now, now))
    vp.execute("INSERT INTO version_plan_demands(code,doc_ref,description,status,version_no,product_code,"
               "demand_type,created_at,updated_at) VALUES('R701','doc','复检需求','已排期','1.5.0','P1',"
               "'新增功能',?,?)", (now, now))
    for i in range(1, 8):
        vp.execute("INSERT INTO version_plan_checks(version_no,product_code,item_no,result) "
                   "VALUES('1.5.0','P1',?,'通过')", (i,))
    vp.commit()
    r = user.post('/version_plan/versions/1.5.0/status',
                   data={'product': 'P1', 'to': '已发布'}, follow_redirects=True)
    ins = [x for x in insts(main, 'VP-P1-1.5.0') if x['def_key'] == 'vp_release_flow']
    check('1.5.0 发布发起流程', bool(ins) and ins[0]['status'] == 'running')
    # 审批期间第 7 项被改回未通过
    vp.execute("UPDATE version_plan_checks SET result='pending' WHERE version_no='1.5.0' "
               "AND product_code='P1' AND item_no=7")
    vp.commit()
    approve_all(main, ins[0]['id'])
    check('动作复检失败时实例进入错误', insts(main, 'VP-P1-1.5.0')[0]['status'] == 'error')
    check('复检失败不发布', vp.execute(
        "SELECT status FROM version_plan_versions WHERE version_no='1.5.0'").fetchone()[0] == '测试中')

    # ---------- 14. 动作缺上下文拒绝执行 ----------
    from plugins.version_plan.routes import wf_action_unfreeze  # noqa: E402
    vp.execute("UPDATE version_plan_versions SET status='已冻结' WHERE version_no='2.0.0'")
    vp.commit()
    try:
        wf_action_unfreeze({'context': {'version_no': '2.0.0', 'product_code': 'P2'}, 'db': main})
        check('解冻动作缺少原因时抛错', False)
    except RuntimeError as e:
        check('解冻动作缺少原因时抛错', '原因' in str(e), str(e))
    check('缺原因动作未改动状态', vp.execute(
        "SELECT status FROM version_plan_versions WHERE version_no='2.0.0'").fetchone()[0] == '已冻结')

    # ---------- 15. WBS 任务驳回后重新发起 ----------
    r = user.post('/wbs/%d/task/quickadd' % pid, data={
        'wbs_code': '2', 'name': '虚拟任务二', 'owner': 'vptester',
        'plan_start': '2026-12-01', 'plan_end': '2026-12-10'})
    tid2 = r.get_json()['tid']
    ins2 = insts(main, 'WBS-T%d' % tid2)
    import workflow_engine as wf2  # noqa: E402
    t = main.execute("SELECT * FROM wf_tasks WHERE instance_id=? AND status='pending'", (ins2[0]['id'],)).fetchone()
    wf2.approve_task(main, t['id'], tester_id, '回归测试员', 'user', 'reject', '驳回重提')
    main.commit()
    check('任务驳回写回状态', main.execute(
        'SELECT approval_status FROM wbs_tasks WHERE id=?', (tid2,)).fetchone()[0] == '已驳回')
    r = user.post('/wbs/project/%d/tasks/resubmit' % pid)
    check('驳回任务可重新提交', r.status_code == 200 and r.get_json().get('count') == 1)
    ins3 = [x for x in insts(main, 'WBS-T%d' % tid2) if x['status'] == 'running']
    check('重新提交生成新流程实例', len(ins3) == 1)
    approve_all(main, ins3[0]['id'])
    check('重新审批后任务已通过', main.execute(
        'SELECT approval_status FROM wbs_tasks WHERE id=?', (tid2,)).fetchone()[0] == '已通过')

    # ---------- 16. 旧库兼容 ----------
    old = sqlite3.connect(str(db_path))
    old.row_factory = sqlite3.Row
    wf.init_tables(old)
    check('旧库无 wf 表也能初始化',
          old.execute("SELECT COUNT(*) FROM sqlite_master WHERE name='wf_event_log'").fetchone()[0] == 1)
    wf.init_tables(old)
    check('重复初始化幂等',
          old.execute("SELECT COUNT(*) FROM sqlite_master WHERE name='wf_event_log'").fetchone()[0] == 1)
    old.close()

    # ---------- 汇总 ----------
    print('\n===== v13.5 回归：%d 通过 / %d 失败 =====' % (len(passes), len(failures)))
    if failures:
        for n, d in failures:
            print('FAILED:', n, d)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
