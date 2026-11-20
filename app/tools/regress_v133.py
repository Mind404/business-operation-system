#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""v13.3.0.0 插件状态变更节点接入流程引擎回归脚本。

在临时 SQLite 库上执行，不写仓库主业务库。
运行：python tools/regress_v133.py
"""
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

APP = Path(__file__).resolve().parent.parent
REPO = APP.parent
sys.path.insert(0, str(APP))

failures = []
passes = []


def check(name, cond, detail=''):
    if cond:
        passes.append(name)
        print(f'PASS  {name}')
    else:
        failures.append((name, detail))
        print(f'FAIL  {name}  {detail}')


def main():
    cp = subprocess.run([sys.executable, '-m', 'compileall', '-q', str(APP)],
                        capture_output=True, text=True)
    check('Python 全量编译', cp.returncode == 0, cp.stderr[-800:])

    tmp = Path(tempfile.mkdtemp(prefix='v133_regress_'))
    db_path = tmp / 'business.db'
    shutil.copy2(APP / 'business.db', db_path)

    import app as appmod  # noqa: E402
    appmod.app.config['DATABASE'] = str(db_path)

    import workflow_engine as wf  # noqa: E402
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    wf.init_tables(conn)
    wf.seed_defaults(conn)
    conn.commit()

    # ---------- 1. 节点目录 ----------
    events = {e['key']: e for e in wf.event_options()}
    expect_events = {'wbs.project.created', 'wbs.project.approval', 'wbs.task.created',
                     'wbs.task.progress', 'wbs.task.completed'}
    check('WBS 已注册 5 个状态变更节点', expect_events <= set(events), str(sorted(events)))
    catalog = wf.event_catalog(conn)
    wbs_group = next((g for g in catalog if g['plugin_id'] == 'wbs'), None)
    check('目录按插件分组且带插件名', bool(wbs_group) and wbs_group['plugin_name'] == '计划管理',
          str([(g['plugin_id'], g['plugin_name']) for g in catalog]))
    check('节点含默认审批步骤', bool(events['wbs.project.created']['default_steps']))
    check('未绑定时节点 trigger 为空', events['wbs.task.created'].get('trigger') is None)

    # ---------- 2. 人工确认模式 ----------
    def mkdef(key, name, steps):
        p = wf.build_simple_definition(key, name, steps)
        wf.save_definition(conn, key, name, p, actor='admin', category='回归')
        return key

    mkdef('regress_trigger_confirm', '回归确认流程',
          [{'name': '负责人确认', 'assignee_type': 'role', 'assignee_value': 'admin', 'mode': 'any'}])
    wf.bind_trigger(conn, 'regress_trigger_confirm', 'wbs', 'wbs.task.created', mode='confirm', actor='admin')
    t = wf.get_trigger_for_def(conn, 'regress_trigger_confirm')
    check('节点可绑定流程', t and t['event_key'] == 'wbs.task.created' and t['mode'] == 'confirm', str(t))

    res = wf.emit_state_change(conn, 'wbs', 'wbs.task.created', business_key='REG-T-001',
                               title='任务「回归任务」创建', context={'task_name': '回归任务'},
                               record_type='task', record_id=1, initiator_id=1, initiator_name='admin')
    check('确认模式只入队不发起', res['matched'] and res['queue_id'] and not res['instance_id'], str(res))
    q = wf.get_queue_item(conn, res['queue_id'])
    check('队列项字段完整', q['status'] == 'pending' and q['def_key'] == 'regress_trigger_confirm'
          and json.loads(q['context_json'])['task_name'] == '回归任务', str(dict(q)))
    pending = wf.pending_starts(conn, 1, 'platform_admin')
    check('待我发起列表可见', any(r['id'] == q['id'] for r in pending))
    check('待发起计数正确', wf.stats(conn)['pending_starts'] >= 1)

    inst = wf.start_from_queue(conn, q['id'], 1, 'admin')
    check('一键发起生成流程实例', inst['status'] == 'running', inst['status'])
    check('发起后队列标记 started',
          wf.get_queue_item(conn, q['id'])['status'] == 'started'
          and wf.get_queue_item(conn, q['id'])['instance_id'] == inst['id'])
    # 收尾该实例，避免影响后续
    for tk in wf.my_tasks(conn, 1, 'platform_admin'):
        wf.approve_task(conn, tk['id'], 1, 'admin', 'platform_admin', 'approve', '回归收尾')

    # ---------- 3. 自动发起模式 + 幂等 ----------
    mkdef('regress_trigger_auto', '回归自动流程',
          [{'name': '自动确认', 'assignee_type': 'role', 'assignee_value': 'admin', 'mode': 'any'}])
    wf.bind_trigger(conn, 'regress_trigger_auto', 'wbs', 'wbs.project.created', mode='auto', actor='admin')
    r1 = wf.emit_state_change(conn, 'wbs', 'wbs.project.created', business_key='REG-P-001',
                              title='项目「回归项目」创建', context={'name': '回归项目'},
                              record_type='project', record_id=9, initiator_id=1, initiator_name='admin')
    check('自动模式直接创建实例', r1['matched'] and r1['instance_id'] and r1['mode'] in ('auto', 'started'), str(r1))
    r2 = wf.emit_state_change(conn, 'wbs', 'wbs.project.created', business_key='REG-P-001',
                              title='项目「回归项目」重复变更', context={},
                              record_type='project', record_id=9, initiator_id=1, initiator_name='admin')
    check('同一业务单号不重复发起', r2['mode'] == 'duplicate' and r2['instance_id'] == r1['instance_id'], str(r2))
    for tk in wf.my_tasks(conn, 1, 'platform_admin'):
        wf.approve_task(conn, tk['id'], 1, 'admin', 'platform_admin', 'approve', '回归收尾')

    # ---------- 4. 同一节点唯一绑定 ----------
    mkdef('regress_trigger_second', '回归第二流程',
          [{'name': '二次确认', 'assignee_type': 'role', 'assignee_value': 'admin', 'mode': 'any'}])
    wf.bind_trigger(conn, 'regress_trigger_second', 'wbs', 'wbs.task.created',
                    mode='confirm', actor='admin')
    rows = [r for r in wf.list_triggers(conn) if r['event_key'] == 'wbs.task.created']
    check('同一状态节点只保留一条绑定', len(rows) == 1 and rows[0]['def_key'] == 'regress_trigger_second', str([dict(r) for r in rows]))
    check('原流程绑定已自动让位', wf.get_trigger_for_def(conn, 'regress_trigger_confirm') is None)

    # 解绑后不再匹配
    wf.unbind_trigger(conn, rows[0]['id'])
    res2 = wf.emit_state_change(conn, 'wbs', 'wbs.task.created', business_key='REG-T-002',
                               title='解绑后', context={})
    check('解绑后不再触发', res2['matched'] is False, str(res2))

    # ---------- 5. 路由：节点目录 / 一键生成 / 设计器绑定 ----------
    client = appmod.app.test_client()
    login = client.post('/login', data={'username': 'admin', 'password': 'admin123'}, follow_redirects=False)
    check('管理员登录 302', login.status_code == 302, str(login.status_code))

    r = client.get('/workflow/admin/events')
    check('状态变更节点页 200', r.status_code == 200, str(r.status_code))
    html = r.data.decode('utf-8')
    check('页面展示插件分组与节点', '计划管理' in html and 'wbs.project.created' in html)
    check('页面展示一键生成入口', '一键生成推荐流程' in html)

    r = client.post('/workflow/admin/events/wbs/wbs.task.progress/quick', data={'mode': 'confirm'},
                    follow_redirects=False)
    check('一键生成流程 302', r.status_code == 302, str(r.status_code))
    made = conn.execute("SELECT def_key FROM wf_triggers WHERE event_key='wbs.task.progress'").fetchone()
    check('一键生成后已绑定流程', bool(made), str(made and made['def_key']))
    if made:
        row = conn.execute('SELECT current_version FROM wf_definitions WHERE def_key=?', (made['def_key'],)).fetchone()
        check('一键生成的流程已发布 v1', row and row['current_version'] == 1)

    designer = client.get('/workflow/admin/definition/new').data.decode('utf-8')
    check('设计器含触发方式区块', 'triggerEvent' in designer and '业务状态变更节点' in designer)
    check('设计器可选 WBS 节点', 'wbs.project.created' in designer)

    r = client.post('/workflow/admin/definition/new', data={
        'editor_mode': 'simple', 'def_key': 'regress_designer_bind', 'name': '设计器绑定回归',
        'category': '回归', 'description': 'v13.3 设计器绑定',
        'step_name': '负责人审批', 'step_assignee_type': 'role', 'step_assignee_value': 'admin',
        'step_mode': 'any', 'action_key': '',
        'trigger_event': 'wbs:wbs.project.approval', 'trigger_mode': 'auto'
    }, follow_redirects=False)
    check('设计器保存并绑定 302', r.status_code == 302, str(r.status_code))
    bt = wf.get_trigger_for_def(conn, 'regress_designer_bind')
    check('设计器保存后绑定已落库', bt and bt['event_key'] == 'wbs.project.approval' and bt['mode'] == 'auto', str(bt))

    # ---------- 6. WBS 业务动作真实上报 ----------
    r = client.post('/wbs/add', data={
        'name': 'v13.3 联动回归项目', 'description': '自动化回归', 'owner': ['admin'],
        'participants': [], 'start_date': '2026-11-01', 'end_date': '2026-12-01',
        'status': '进行中', 'approver': ''
    }, follow_redirects=False)
    check('WBS 新建项目 302', r.status_code == 302, str(r.status_code))
    # v13.5 语义：管理员建项目直接「已通过」，不触发审批流程
    insts = conn.execute("SELECT * FROM wf_instances WHERE plugin_id='wbs' AND record_type='project' "
                         "AND title LIKE '%v13.3%' AND def_key='regress_trigger_auto' LIMIT 1").fetchone()
    check('WBS 管理员建项目免流程（v13.5 语义）', not insts, str(insts and dict(insts)))

    # 新增任务 → 触发 wbs.task.created（当前该节点已解绑，不应入队）
    n_queue_before = conn.execute("SELECT COUNT(*) c FROM wf_trigger_queue").fetchone()['c']
    pid = conn.execute("SELECT id FROM wbs_projects ORDER BY id DESC LIMIT 1").fetchone()['id']
    r = client.post(f'/wbs/{pid}/task/add', data={
        'wbs_code': 'REG-1.1', 'name': '回归任务A', 'owner': 'admin', 'progress': '0',
        'status': '未开始', 'priority': 'P2中'
    }, follow_redirects=False)
    check('WBS 新增任务 302', r.status_code == 302, str(r.status_code))
    tid = conn.execute("SELECT id FROM wbs_tasks ORDER BY id DESC LIMIT 1").fetchone()['id']
    n_after = conn.execute("SELECT COUNT(*) c FROM wf_trigger_queue").fetchone()['c']
    check('未绑定节点不产生待发起', n_after == n_queue_before, '%d → %d' % (n_queue_before, n_after))

    # 绑定完成节点为人工确认，再把进度改到 100 → 应入队 wbs.task.completed
    r = client.post('/workflow/admin/trigger/bind', data={
        'event': 'wbs:wbs.task.completed', 'def_key': 'regress_trigger_confirm', 'mode': 'confirm'
    }, follow_redirects=False)
    check('后台绑定完成节点 302', r.status_code == 302, str(r.status_code))
    r = client.post(f'/wbs/task/{tid}/inline', json={'field': 'progress', 'value': 100})
    check('行内改进度 200', r.status_code == 200, str(r.status_code))
    rowq = conn.execute("SELECT * FROM wf_trigger_queue WHERE event_key='wbs.task.completed' "
                        "ORDER BY id DESC LIMIT 1").fetchone()
    check('任务完成已进入待发起队列', bool(rowq) and rowq['status'] == 'pending', str(rowq and dict(rowq)))
    check('待发起事项带业务单号', bool(rowq) and rowq['business_key'] == 'WBS-T%d' % tid, str(rowq and rowq['business_key']))

    # 工作台展示待发起
    home = client.get('/').data.decode('utf-8')
    check('工作台含「待我发起」页签', '待我发起' in home and 'wfQueue' in home)
    check('工作台展示发起审批按钮', '发起审批' in home)

    # ---------- 7. 页面与统计 ----------
    for path, expected in {'/workflow/admin': 200, '/workflow/admin/events': 200,
                           '/workflow/start': 200, '/': 200, '/wbs': 200}.items():
        rr = client.get(path)
        check(f'页面 {path} 状态 {expected}', rr.status_code == expected, f'实际 {rr.status_code}')

    st = wf.stats(conn)
    check('统计含待发起与节点数', st['pending_starts'] >= 1 and st['events'] >= 10, str(st))
    trs = wf.list_triggers(conn)
    check('绑定列表可查询且带流程名', all(r['def_name'] for r in trs) and len(trs) >= 2,
          str([dict(x) for x in trs]))

    print('\nSUMMARY %d PASS / %d FAIL' % (len(passes), len(failures)))
    if failures:
        for name, detail in failures:
            print('FAIL DETAIL', name, detail)
        sys.exit(1)


if __name__ == '__main__':
    main()
