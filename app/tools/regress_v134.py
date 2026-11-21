#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""v13.4.0.0 流程设计器易用性回归：Key 自动生成、审批人搜索、节点说明子页。

在临时 SQLite 库上执行，不写仓库主业务库。
运行：python tools/regress_v134.py
"""
import json
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


def main():
    cp = subprocess.run([sys.executable, '-m', 'compileall', '-q', str(APP)],
                        capture_output=True, text=True)
    check('Python 全量编译', cp.returncode == 0, cp.stderr[-800:])

    tmp = Path(tempfile.mkdtemp(prefix='v134_regress_'))
    db_path = tmp / 'business.db'
    shutil.copy2(APP / 'business.db', db_path)

    import app as appmod  # noqa: E402
    appmod.app.config['DATABASE'] = str(db_path)

    import workflow_engine as wf  # noqa: E402
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    wf.init_tables(conn)
    wf.seed_defaults(conn)
    conn.execute("INSERT INTO users (username,password,display_name,role,is_active,department) "
                 "VALUES ('zhangsan','x','张三','user',1,'供应链部')")
    conn.commit()

    # ---------- 1. 流程 Key 自动生成 ----------
    check('绑定节点时 Key 取节点键', wf.generate_def_key(conn, event_key='wbs.task.completed') == 'wbs_task_completed')
    k = wf.generate_def_key(conn)
    check('未绑定节点时 Key 自动生成且以 flow_ 开头', k.startswith('flow_') and len(k) > 5, k)
    wf.save_definition(conn, 'flow_dup', '重名测试', {
        'key': 'flow_dup', 'name': '重名测试', 'category': '通用', 'description': '',
        'nodes': [{'id': 'start', 'type': 'start', 'name': '开始', 'next': 'a'},
                  {'id': 'a', 'type': 'approval', 'name': '审批', 'assignee': {'type': 'role', 'value': 'admin'},
                   'mode': 'any', 'next': {'approve': 'end_approve', 'reject': 'end_reject', 'return': 'end_return'}},
                  {'id': 'end_approve', 'type': 'end', 'name': '通过'},
                  {'id': 'end_reject', 'type': 'end', 'name': '驳回'},
                  {'id': 'end_return', 'type': 'end', 'name': '退回'}]})
    check('Key 重名自动加序号', wf.generate_def_key(conn, custom='flow_dup') == 'flow_dup_2')
    try:
        wf.generate_def_key(conn, custom='采购流程')
        check('非法自定义 Key 被拒绝', False)
    except ValueError:
        check('非法自定义 Key 被拒绝', True)

    a = wf.build_default_definition_for_event('wbs', 'wbs.task.created', db=conn)
    wf.save_definition(conn, a['key'], a['name'], a, actor='测试')
    b = wf.build_default_definition_for_event('wbs', 'wbs.task.created', db=conn)
    check('已存在的默认流程不会被同 Key 覆盖', a['key'] != b['key'], '%s / %s' % (a['key'], b['key']))

    # ---------- 2. 审批人数据源 ----------
    users = wf.actor_options(conn, 'user', '产品')
    check('按姓名搜索用户', len(users) == 1 and users[0]['value'] == 'product', str(users))
    users = wf.actor_options(conn, 'user', 'zhangsan')
    check('按账号搜索用户', len(users) == 1 and users[0]['label'] == '张三', str(users))
    users = wf.actor_options(conn, 'user', '供应链')
    check('按部门搜索用户', len(users) == 1 and users[0]['value'] == 'zhangsan', str(users))
    check('用户项带姓名+账号+部门副标题',
          'zhangsan' in (users[0]['sub'] if users else '') and '供应链部' in (users[0]['sub'] if users else ''))
    roles = {r['value']: r for r in wf.actor_options(conn, 'role', '')}
    check('角色列表含平台管理员', 'platform_admin' in roles, str(list(roles)))
    check('管理员角色人数合并 admin+platform_admin', roles['admin']['sub'] == '1 人', roles['admin']['sub'])
    check('角色可按关键词搜索', [r['value'] for r in wf.actor_options(conn, 'role', '普通')] == ['user'])

    # ---------- 3. 简易设计器 & 保存 ----------
    client = appmod.app.test_client()
    check('管理员登录 302', client.post('/login', data={'username': 'admin', 'password': 'admin123'}).status_code == 302)

    page = client.get('/workflow/admin/definition/new').get_data(as_text=True)
    check('新建流程页 200 且渲染', '新建流程模板' in page)
    check('触发节点选择在表单内（保存时能提交）', page.index('id="wfForm"') < page.index('name="trigger_event"'))
    check('流程 Key 默认自动生成', 'name="auto_key"' in page and '保存时自动生成' in page)
    check('审批人值是可搜索下拉', 'combo-panel' in page and 'api/actors' in page)
    check('高级模式仍保留 JSON 入口', 'definition_json' in page)

    data = {
        'editor_mode': 'simple', 'def_key': '', 'auto_key': '1',
        'name': '任务完成验收流程', 'category': '业务流程', 'description': '任务勾完成时走验收',
        'trigger_event': 'wbs:wbs.task.completed', 'trigger_mode': 'confirm',
        'step_name': ['负责人确认', '项目经理审批'],
        'step_assignee_type': ['user', 'record_field'],
        'step_assignee_value': ['product', 'owner'],
        'step_mode': ['any', 'any'], 'action_key': '',
    }
    r = client.post('/workflow/admin/definition/new', data=data, follow_redirects=False)
    check('不填 Key 也能创建', r.status_code == 302 and '/wbs_task_completed' in (r.headers.get('Location') or ''),
          str(r.status_code) + ' ' + str(r.headers.get('Location')))
    row = conn.execute("SELECT * FROM wf_definitions WHERE name='任务完成验收流程'").fetchone()
    check('创建后流程入库', bool(row))
    check('保存时自动绑定状态变更节点',
          bool(conn.execute('SELECT 1 FROM wf_triggers WHERE def_key=? AND event_key=?',
                            (row['def_key'], 'wbs.task.completed')).fetchone()))
    ver = conn.execute('SELECT graph_json FROM wf_definition_versions WHERE def_key=? '
                       'ORDER BY version DESC LIMIT 1', (row['def_key'],)).fetchone()
    graph = json.loads(ver['graph_json'])
    approvals = [n for n in graph['nodes'] if n['type'] == 'approval']
    check('指定用户审批人写入定义', approvals[0]['assignee'].get('value') == 'product', str(approvals[0]))
    check('业务字段审批人写入 field（v13.3 解析读 field）', approvals[1]['assignee'].get('field') == 'owner', str(approvals[1]))

    resolved = wf._resolve_assignees(conn, {'type': 'record_field', 'field': 'owner'}, {'owner': 'product'}, 1)
    check('业务字段能解析到真实用户', len(resolved) == 1 and resolved[0]['username'] == 'product',
          str([dict(x) for x in resolved]))

    data2 = dict(data)
    data2.update({'def_key': 'my_custom_flow', 'auto_key': '0', 'name': '自定义 Key 流程',
                  'trigger_event': '', 'trigger_mode': 'confirm'})
    r = client.post('/workflow/admin/definition/new', data=data2, follow_redirects=False)
    check('勾选自定义后按用户填写的 Key 保存',
          r.status_code == 302 and '/my_custom_flow' in (r.headers.get('Location') or ''), str(r.status_code))

    edit = client.get('/workflow/admin/definition/wbs_task_completed').get_data(as_text=True)
    check('编辑页 Key 只读', 'readonly' in edit and 'name="auto_key" value="0"' in edit)
    check('编辑页回显已绑定节点', '任务完成' in edit and 'wbs:wbs.task.completed' in edit)

    # ---------- 4. 审批人 API ----------
    api = client.get('/workflow/admin/api/actors?type=user&q=张三')
    js = api.get_json()
    check('审批人 API 200', api.status_code == 200 and js['ok'])
    check('审批人 API 返回匹配用户', len(js['items']) == 1 and js['items'][0]['value'] == 'zhangsan', str(js))
    api = client.get('/workflow/admin/api/actors?type=role')
    check('角色 API 返回角色', api.status_code == 200 and api.get_json()['ok'])

    # 普通用户不能读审批人数据
    c2 = appmod.app.test_client()
    c2.post('/login', data={'username': 'product', 'password': 'product123'})
    check('普通用户访问审批人 API 被拒', c2.get('/workflow/admin/api/actors?type=user').status_code == 403)

    # ---------- 5. 节点说明子页 ----------
    guide = client.get('/workflow/admin/events/guide')
    text = guide.get_data(as_text=True)
    check('节点说明页 200', guide.status_code == 200)
    check('说明页按插件分组', '计划管理' in text)
    for label in ['项目创建', '项目审批结果', '任务创建', '任务进度更新', '任务完成']:
        check('说明页列出节点「%s」' % label, label in text)
    check('说明页解释触发时机', '什么时候会发生' in text)
    check('说明页显示携带数据', '带进流程的业务数据' in text)
    check('说明页显示推荐审批链', '推荐审批链' in text)
    check('说明页给出三步上手指引', '三步上手' in text and '流程 Key' in text)

    ev = client.get('/workflow/admin/events').get_data(as_text=True)
    check('节点目录页有说明入口', '节点说明' in ev)
    check('节点目录页显示推荐审批链', '推荐审批链' in ev)
    check('节点目录页显示携带数据', '携带数据' in ev)
    home = client.get('/workflow/admin').get_data(as_text=True)
    check('审批中心合并页可打开', '运行记录' in home and '高级设置' in home)

    print('\n==== v13.4 回归结果：%d PASS / %d FAIL ====' % (len(passes), len(failures)))
    if failures:
        for name, detail in failures:
            print('FAILED:', name, detail)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
