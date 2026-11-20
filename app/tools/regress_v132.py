#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""v13.2.0.0 流程工作台 + 简易设计器回归脚本。

在临时 SQLite 库上执行，不写仓库主业务库。
运行：python tools/regress_v132.py
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
    check('Python 全量编译', cp.returncode == 0, cp.stderr[-500:])

    tmp = Path(tempfile.mkdtemp(prefix='v132_regress_'))
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

    defs = {r['def_key'] for r in wf.list_definitions(conn)}
    check('默认 4 个流程模板已写入', {'generic_approval', 'wbs_project_approval',
                                      'wbs_task_approval', 'demo_countersign'} <= defs, str(defs))
    actions = {a['key']: a for a in wf.action_options()}
    check('动作注册表含标签与插件元数据',
          actions.get('wbs.project.set_approval', {}).get('plugin') == 'wbs' and
          actions.get('demo.record_decision', {}).get('label'), str(actions))

    simple_def = wf.build_simple_definition(
        'regress_simple', '回归双节点流程',
        [{'name': '项目经理审批', 'assignee_type': 'record_field', 'assignee_value': 'owner', 'mode': 'any'},
         {'name': '管理员会签', 'assignee_type': 'role', 'assignee_value': 'admin', 'mode': 'countersign'}],
        action={'key': 'demo.record_decision', 'label': '记录审批结果', 'plugin': 'platform'},
        category='回归', description='v13.2 简易模式')
    approvals = [n for n in simple_def['nodes'] if n['type'] == 'approval']
    check('简易流程生成两个审批节点', len(approvals) == 2, str(approvals))
    parsed = wf.parse_simple_definition(simple_def)
    check('简易定义可回显两个节点', len(parsed['steps']) == 2 and parsed['action']['key'] == 'demo.record_decision',
          str(parsed))
    try:
        wf.build_simple_definition('Bad Key!', '非法流程', [{'name': 'x', 'assignee_type': 'role', 'assignee_value': 'admin'}])
        check('非法 Key 应被拒绝', False, '未抛异常')
    except Exception:
        check('非法 Key 应被拒绝', True)

    inst = wf.start_instance(conn, 'generic_approval', title='v13.2 同意回归',
                            initiator_id=1, initiator_name='admin', context={})
    check('流程可发起并进入 running', inst['status'] == 'running', inst['status'])
    tasks = wf.my_tasks(conn, 1, 'platform_admin')
    check('管理员能看到待审批任务', len(tasks) >= 1, str([t['name'] for t in tasks]))
    task = tasks[0]
    wf.approve_task(conn, task['id'], 1, 'admin', 'platform_admin', 'approve', '回归同意')
    final = wf.get_instance(conn, inst['id'])
    check('同意后流程完成', final['status'] == 'completed', final['status'])

    inst2 = wf.start_instance(conn, 'generic_approval', title='v13.2 驳回回归',
                             initiator_id=1, initiator_name='admin', context={})
    task2 = wf.my_tasks(conn, 1, 'platform_admin')[0]
    wf.approve_task(conn, task2['id'], 1, 'admin', 'platform_admin', 'reject', '回归驳回')
    final2 = wf.get_instance(conn, inst2['id'])
    check('驳回后流程结束为 rejected', final2['status'] == 'rejected', final2['status'])

    client = appmod.app.test_client()
    login = client.post('/login', data={'username': 'admin', 'password': 'admin123'}, follow_redirects=False)
    check('管理员登录 302', login.status_code == 302, str(login.status_code))

    pages = {
        '/': 200, '/workflow/': 302, '/workflow/start': 200, '/workflow/admin': 200,
        '/workflow/admin/definition/generic_approval': 200, '/workflow/admin/definition/new': 200,
        '/wbs': 200, '/wbs/alerts': 200, '/version_plan/': 200, '/admin/users': 200, '/healthz': 200,
    }
    for path, expected in pages.items():
        r = client.get(path)
        check(f'页面 {path} 状态 {expected}', r.status_code == expected, f'实际 {r.status_code}')

    home = client.get('/').data.decode('utf-8')
    check('工作台包含流程与审批面板', '流程与审批' in home and 'wfTab' in home)
    check('旧流程中心导航已移除', '流程中心' not in home)

    designer = client.get('/workflow/admin/definition/generic_approval').data.decode('utf-8')
    check('设计器为卡片式简易模式', 'step-card' in designer and 'advancedToggle' in designer)
    check('设计器动作下拉含 WBS 动作', 'wbs.project.set_approval' in designer)

    start_page = client.get('/workflow/start').data.decode('utf-8')
    check('发起页高级上下文默认折叠', 'advancedBox' in start_page and '业务上下文 JSON' in start_page)

    r = client.post('/workflow/admin/definition/generic_approval', data={
        'editor_mode': 'simple', 'def_key': 'generic_approval', 'name': '通用业务审批',
        'category': '通用', 'description': 'v13.2 回归保存',
        'step_name': '负责人审批', 'step_assignee_type': 'role', 'step_assignee_value': 'admin',
        'step_mode': 'any', 'action_key': 'demo.record_decision'
    }, follow_redirects=False)
    check('既有模板简易保存 302', r.status_code == 302, str(r.status_code))

    r = client.post('/workflow/admin/definition/new', data={
        'editor_mode': 'simple', 'def_key': 'regress_route_simple', 'name': '路由回归流程',
        'category': '回归', 'description': 'v13.2 路由新建',
        'step_name': ['项目经理审批', '管理员会签'],
        'step_assignee_type': ['record_field', 'role'],
        'step_assignee_value': ['owner', 'admin'],
        'step_mode': ['any', 'countersign'], 'action_key': ''
    }, follow_redirects=False)
    check('新建简易流程 302', r.status_code == 302, str(r.status_code))
    row = conn.execute('SELECT current_version FROM wf_definitions WHERE def_key=?',
                       ('regress_route_simple',)).fetchone()
    check('新建流程已落库', row is not None and row['current_version'] == 1)

    r = client.post('/workflow/start', data={
        'def_key': 'generic_approval', 'title': 'v13.2 简化发起回归', 'business_key': 'REG-SIMPLE-001'
    }, follow_redirects=False)
    check('简化表单可发起流程', r.status_code == 302, str(r.status_code))

    stats = wf.stats(conn)
    check('流程统计可读取', isinstance(stats, dict) and 'running' in stats, str(stats))
    mine = wf.my_instances(conn, 1)
    check('我发起的流程可查询', len(mine) >= 3, str(len(mine)))
    open_rows = wf.open_instances(conn)
    check('运行中/异常流程可查询', isinstance(open_rows, list))

    print('\nSUMMARY %d PASS / %d FAIL' % (len(passes), len(failures)))
    if failures:
        for name, detail in failures:
            print('FAIL DETAIL', name, detail)
        sys.exit(1)


if __name__ == '__main__':
    main()
