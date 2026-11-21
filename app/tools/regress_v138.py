#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""v13.8.0.0 审批中心单入口 + 规则可视化 + 外部插件事件 API 回归测试。

全部在临时 SQLite 副本上运行，不写生产库。
运行：python tools/regress_v138.py
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


def login(client, username, password='admin123'):
    return client.post('/login', data={'username': username, 'password': password},
                       follow_redirects=False)


def main():
    cp = subprocess.run([sys.executable, '-m', 'compileall', '-q', str(APP)],
                        capture_output=True, text=True)
    check('Python 全量编译', cp.returncode == 0, cp.stderr[-800:])

    tmp = Path(tempfile.mkdtemp(prefix='v138_regress_'))
    db_path = tmp / 'business.db'
    shutil.copy2(APP / 'business.db', db_path)
    plug_dir = tmp / 'plugin_data'
    plug_dir.mkdir()
    shutil.copy2(APP / 'data' / 'version_plan.db', plug_dir / 'version_plan.db')

    import app as appmod
    appmod.app.config['DATABASE'] = str(db_path)
    appmod.app.config['PLUGIN_DB_DIR'] = str(plug_dir)
    appmod.app.config['TESTING'] = True

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
                   "VALUES ('v138product',?,'回归普通用户','user',1,'计划部')",
                   (generate_password_hash('admin123'),))
    maindb.commit()

    # ---------- manifest 事件自动登记 ----------
    manifest = {'id': 'crmx', 'name': '客户管理回归插件', 'source': 'external',
                'workflow': {'events': [
                    {'key': 'crmx.contract.submitted', 'label': '合同提交',
                     'context_fields': [{'key': 'owner', 'label': '负责人'}]},
                    {'key': 'crmx.contract.approved', 'label': '合同批准'},
                ]}}
    added = wf.register_manifest_events('crmx', manifest)
    check('manifest 事件自动登记 2 条', added == 2, 'added=%s' % added)
    ev = wf.get_event('crmx.contract.submitted')
    check('manifest 事件名称/字段生效', ev and ev['label'] == '合同提交' and ev['context_fields'],
          str(ev)[:200])
    # 不覆盖代码里已登记的真实元数据
    wf.register_manifest_events('wbs', {'id': 'wbs', 'name': '计划管理',
                                        'workflow': {'events': [{'key': 'wbs.project.created',
                                                                 'label': '错误覆盖名'}]}})
    check('不覆盖已有真实事件元数据', wf.get_event('wbs.project.created')['label'] != '错误覆盖名',
          wf.get_event('wbs.project.created')['label'])
    # 同一插件两次登记幂等
    check('重复登记不重复计数', wf.register_manifest_events('crmx', manifest) == 0)

    # ---------- 令牌 ----------
    with appmod.app.app_context():
        t1 = wf.plugin_event_token('crmx')
        t2 = wf.plugin_event_token('crmx')
        tw = wf.plugin_event_token('wbs')
        check('同插件令牌稳定', t1 == t2 and len(t1) == 64)
        check('不同插件令牌不同', t1 != tw)
        check('错误令牌校验失败', not wf.verify_plugin_event_token('crmx', 'bad-token'))
        check('正确令牌校验通过', wf.verify_plugin_event_token('crmx', t1))

    client = appmod.app.test_client()
    check('管理员登录成功', login(client, 'admin').status_code == 302)

    # ---------- 单入口三页签 ----------
    rules = client.get('/workflow/admin/rules')
    html = rules.get_data(as_text=True)
    check('规则配置页 200', rules.status_code == 200, str(rules.status_code))
    check('合并页含三个页签', all(x in html for x in ('规则配置', '运行记录', '高级设置')))
    check('侧栏不再有独立流程运行记录', '流程运行记录' not in html)
    check('规则页展示外部插件分组', '客户管理回归插件' in html and '合同提交' in html)
    runtime = client.get('/workflow/admin/rules?tab=runtime')
    rhtml = runtime.get_data(as_text=True)
    check('运行记录页 200', runtime.status_code == 200, str(runtime.status_code))
    check('运行记录页有运行态统计', all(x in rhtml for x in ('流程模板', '运行中实例', '待审批任务')))
    adv = client.get('/workflow/admin/rules?tab=advanced')
    ahtml = adv.get_data(as_text=True)
    check('高级设置页 200', adv.status_code == 200, str(adv.status_code))
    check('高级页有外部令牌表', 'X-Plugin-Token' in ahtml and 'version_plan' in ahtml)
    check('高级页不再自跳完整运行入口', '打开完整运行记录' not in ahtml)
    old = client.get('/workflow/admin')
    check('旧 /workflow/admin 仍可用', old.status_code == 200)
    check('旧地址默认运行页签', '运行记录' in old.get_data(as_text=True))
    node = client.get('/workflow/admin/events')
    check('旧高级节点页仍可用', node.status_code == 200)

    # ---------- 编辑页 ----------
    edit = client.get('/workflow/admin/rules/wbs/wbs.task.created')
    ehtml = edit.get_data(as_text=True)
    check('规则编辑页 200', edit.status_code == 200, str(edit.status_code))
    check('三步向导齐全', all(x in ehtml for x in ('哪个业务要审批', '哪些业务需要审批', '排审批人')))
    check('范围三卡片齐全', all(x in ehtml for x in ('全部（默认规则）', '指定产品', '指定项目类型')))
    check('审批链实时预览', '审批链预览' in ehtml and '审批通过后' in ehtml)
    check('已有规则名称显示人话而非 def_key',
          'value="任务创建审批"' in ehtml and 'value="wbs_task_created"' not in ehtml)

    # ---------- 规则保存：指定项目类型 + 两级审批 ----------
    form = {
        'rule_name': '交付任务进度确认',
        'scope_type': 'project_type',
        'scope_value': '交付项目',
        'mode': 'auto',
        'step_name': ['项目经理确认', '管理员复核'],
        'step_assignee_value': ['admin', 'admin'],
        'step_backup_value': ['', ''],
    }
    post = client.post('/workflow/admin/rules/wbs/wbs.task.progress', data=form,
                       headers={'Referer': 'http://127.0.0.1'}, follow_redirects=False)
    check('规则保存成功', post.status_code == 302, str(post.status_code))
    trg = maindb.execute("SELECT * FROM wf_triggers WHERE plugin_id='wbs' "
                         "AND event_key='wbs.task.progress'").fetchone()
    check('规则按项目类型落库', trg and trg['scope_type'] == 'project_type'
          and trg['scope_value'] == '交付项目', str(dict(trg) if trg else None))
    check('规则默认自动发起', trg and trg['mode'] == 'auto', str(trg['mode'] if trg else ''))
    check('规则审批链两步落库',
          trg and len(wf.parse_simple_definition(json.loads(
              maindb.execute('SELECT graph_json FROM wf_definition_versions WHERE def_key=? ORDER BY version DESC LIMIT 1',
                             (trg['def_key'],)).fetchone()['graph_json']))['steps']) == 2)
    rules2 = client.get('/workflow/admin/rules').get_data(as_text=True)
    check('规则卡片显示项目类型范围', '项目类型：交付项目' in rules2)
    check('规则卡片显示审批人', '平台管理员' in rules2)
    check('规则卡片显示审批链箭头', 'chain-arrow' in rules2 or '→' in rules2)

    # ---------- 外部插件 API ----------
    api = '/workflow/api/plugin-events'
    no_token = client.post(api, json={'plugin_id': 'crmx', 'event_key': 'crmx.contract.submitted',
                                      'business_key': 'EXT-NOTOKEN'})
    check('无令牌返回 403', no_token.status_code == 403, str(no_token.status_code))
    wrong = client.post(api, headers={'X-Plugin-Token': 'wrong'},
                        json={'plugin_id': 'crmx', 'event_key': 'crmx.contract.submitted',
                              'business_key': 'EXT-WRONG'})
    check('错误令牌返回 403', wrong.status_code == 403, str(wrong.status_code))
    bad_ev = client.post(api, headers={'X-Plugin-Token': t1},
                         json={'plugin_id': 'crmx', 'event_key': 'crmx.not.registered',
                               'business_key': 'EXT-BAD'})
    check('未登记事件返回 400', bad_ev.status_code == 400 and bad_ev.get_json().get('ok') is False,
          str(bad_ev.get_json()))
    bad_ctx = client.post(api, headers={'X-Plugin-Token': t1},
                          json={'plugin_id': 'crmx', 'event_key': 'crmx.contract.submitted',
                                'business_key': 'EXT-CTX', 'context': ['x']})
    check('context 非对象返回 400', bad_ctx.status_code == 400, str(bad_ctx.get_json()))
    anon = appmod.app.test_client()
    anon_r = anon.post(api, headers={'X-Plugin-Token': t1},
                       json={'plugin_id': 'crmx', 'event_key': 'crmx.contract.submitted',
                             'business_key': 'EXT-ANON', 'title': '服务间无登录上报'})
    check('服务间无登录也可凭令牌上报', anon_r.status_code == 200
          and (anon_r.get_json() or {}).get('mode') == 'unmatched', str(anon_r.get_json()))
    direct = client.post(api, headers={'X-Plugin-Token': t1},
                         json={'plugin_id': 'crmx', 'event_key': 'crmx.contract.submitted',
                               'business_key': 'EXT-DIRECT', 'title': '合同直通回归',
                               'context': {'owner': 'admin'}})
    body = direct.get_json() or {}
    check('未配置规则直通不阻塞', direct.status_code == 200 and body.get('ok')
          and body.get('matched') is False and body.get('mode') == 'unmatched', str(body))
    log = maindb.execute("SELECT * FROM wf_event_log WHERE business_key='EXT-DIRECT'").fetchone()
    check('直通写留痕流水', log and log['mode'] == 'unmatched', str(dict(log) if log else None))
    matched = client.post(api, headers={'X-Plugin-Token': tw},
                          json={'plugin_id': 'wbs', 'event_key': 'wbs.project.created',
                                'business_key': 'EXT-MATCH', 'title': '外部来单回归'})
    mbody = matched.get_json() or {}
    check('有规则时进入待发起队列', matched.status_code == 200 and mbody.get('matched') is True
          and mbody.get('queue_id'), str(mbody))

    # ---------- 权限 ----------
    pc = appmod.app.test_client()
    check('普通用户登录成功', login(pc, 'v138product').status_code == 302)
    deny_rules = pc.get('/workflow/admin/rules')
    check('普通用户不能进规则配置', deny_rules.status_code in (302, 403), str(deny_rules.status_code))
    deny_edit = pc.get('/workflow/admin/rules/wbs/wbs.task.created')
    check('普通用户不能进规则编辑', deny_edit.status_code in (302, 403), str(deny_edit.status_code))
    plain = pc.get('/wbs')
    check('普通用户业务页面不受影响', plain.status_code == 200, str(plain.status_code))

    # ---------- WBS 项目类型 ----------
    cols = [r['name'] for r in maindb.execute('PRAGMA table_info(wbs_projects)').fetchall()]
    check('WBS 表有 project_type', 'project_type' in cols, ','.join(cols))
    proj_form = pc.get('/wbs/add')
    pf = proj_form.get_data(as_text=True)
    check('WBS 新建表单有项目类型', proj_form.status_code == 200
          and all(x in pf for x in ('研发项目', '交付项目', '内部项目', '其他项目')),
          str(proj_form.status_code))

    # ---------- 清理 ----------
    maindb.execute("DELETE FROM wf_event_log WHERE business_key IN "
                   "('EXT-ANON','EXT-DIRECT','EXT-MATCH','EXT-NOTOKEN','EXT-WRONG','EXT-BAD','EXT-CTX')")
    maindb.execute("DELETE FROM wf_trigger_queue WHERE business_key IN "
                   "('EXT-DIRECT','EXT-MATCH')")
    for row in maindb.execute("SELECT id FROM wf_instances WHERE business_key='EXT-MATCH'").fetchall():
        maindb.execute('DELETE FROM wf_tasks WHERE instance_id=?', (row['id'],))
        maindb.execute('DELETE FROM wf_instance_events WHERE instance_id=?', (row['id'],))
        maindb.execute('DELETE FROM wf_instances WHERE id=?', (row['id'],))
    if trg:
        maindb.execute('DELETE FROM wf_triggers WHERE id=?', (trg['id'],))
        maindb.execute('DELETE FROM wf_definitions WHERE def_key=?', (trg['def_key'],))
        maindb.execute('DELETE FROM wf_definition_versions WHERE def_key=?', (trg['def_key'],))
    maindb.commit()
    maindb.close()

    print('\n===== v13.8 回归：%d 通过 / %d 失败 =====' % (len(passes), len(failures)))
    if failures:
        for name, detail in failures:
            print('  FAIL %s | %s' % (name, detail))
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
