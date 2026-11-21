# -*- coding: utf-8 -*-
"""流程中心路由：我的审批、实例详情、模板管理、发起流程与 JSON API。"""
import csv
import io
import json
import sqlite3
from datetime import datetime

from flask import Blueprint, current_app, flash, jsonify, redirect, render_template, request, session, url_for

from plugin_system import get_db, get_plugin_db
import workflow_engine as wf

workflow_bp = Blueprint('workflow', __name__, url_prefix='/workflow')


def _current_user():
    return {
        'id': session.get('user_id'),
        'name': session.get('user_name', ''),
        'role': session.get('user_role', ''),
        'username': session.get('username', ''),
    }


def _user_labels(db):
    try:
        rows = db.execute("SELECT username, display_name FROM users WHERE COALESCE(deleted,0)=0").fetchall()
    except Exception:
        rows = []
    return {str(r['username']): (r['display_name'] or r['username']) for r in rows}


def _decorate_catalog(db, catalog):
    """给每条规则补上“人能看懂”的审批链，规则中心不再只显示流程名。"""
    users = _user_labels(db)
    role_labels = {'admin': '平台管理员', 'platform_admin': '平台管理员', 'user': '普通用户'}
    for group in catalog:
        for ev in group.get('events') or []:
            for rule in ev.get('rules') or []:
                steps = []
                try:
                    row, ver = wf.get_current_version(db, rule['def_key'])
                    definition = json.loads(ver['graph_json']) if ver else {}
                    parsed = wf.parse_simple_definition(definition) or {}
                    for st in parsed.get('steps') or []:
                        typ = st.get('assignee_type') or ''
                        val = str(st.get('assignee_value') or '')
                        if typ == 'user':
                            approver = users.get(val, val)
                        elif typ == 'role':
                            approver = role_labels.get(val, val)
                        elif typ == 'record_field':
                            field_label = next((f.get('label') for f in (ev.get('context_fields') or [])
                                                if f.get('key') == val), val)
                            approver = field_label or '业务负责人'
                        elif typ == 'initiator':
                            approver = '发起人'
                        else:
                            approver = val or '待定'
                        backup = ''
                        if st.get('backup_assignee_type') == 'user' and st.get('backup_assignee_value'):
                            bv = str(st['backup_assignee_value'])
                            backup = users.get(bv, bv)
                        steps.append({'name': st.get('name') or '审批',
                                      'approver': approver,
                                      'backup': backup})
                except Exception:
                    steps = []
                rule['chain'] = steps
            # 节点级预览：如果是“全部”规则，取第一条可展示的链
            ev['preview_chain'] = (ev.get('rules') or [{}])[0].get('chain') if ev.get('rules') else []
    return catalog


def _render_admin_center(default_tab='rules'):
    deny = _admin_required()
    if deny:
        return deny
    db = _db()
    tab = (request.args.get('tab') or default_tab or 'rules').strip()
    if tab not in ('rules', 'runtime', 'advanced'):
        tab = 'rules'
    catalog = _decorate_catalog(db, wf.event_catalog(db))
    total_events = sum(len(g['events']) for g in catalog)
    configured_events = sum(1 for g in catalog for e in g['events'] if e.get('rules'))
    direct_count = db.execute(
        "SELECT COUNT(*) c FROM wf_event_log WHERE matched=0 AND mode='unmatched'").fetchone()['c']
    direct_rows = db.execute(
        "SELECT * FROM wf_event_log WHERE matched=0 AND mode='unmatched' "
        "ORDER BY id DESC LIMIT 30").fetchall()
    defs = wf.list_definitions(db)
    instances = wf.list_instances(db, limit=80)
    st = wf.stats(db)
    outbox = wf.outbox_rows(db)
    subscriptions = wf.list_subscriptions(db)
    plugin_rows = []
    try:
        from plugin_system import _manifest_map
        for pid, man in (_manifest_map() or {}).items():
            events = ((man or {}).get('workflow') or {}).get('events') or []
            if not events:
                continue
            plugin_rows.append({
                'id': pid,
                'name': (man or {}).get('name') or pid,
                'token': wf.plugin_event_token(pid),
                'event_count': len(events),
                'source': (man or {}).get('source') or 'builtin',
            })
    except Exception:
        plugin_rows = []
    return render_template('workflow_rules.html',
                           catalog=catalog, total_events=total_events,
                           configured_events=configured_events,
                           unconfigured_events=total_events - configured_events,
                           direct_count=direct_count, direct_rows=direct_rows,
                           scopes=wf.SCOPE_TYPES, modes=wf.EVENT_MODES,
                           definitions=defs, instances=instances, stats=st,
                           outbox=outbox, subscriptions=subscriptions,
                           plugin_rows=plugin_rows, tab=tab,
                           scope_options=_scope_options(), user=_current_user())


def _login_required():
    if not session.get('user_id'):
        return redirect(url_for('login', next=request.path))
    return None


def _admin_required():
    if session.get('user_role') != 'platform_admin':
        flash('仅平台管理员可以管理流程模板', 'error')
        return redirect(url_for('index'))
    return None


def _db():
    return get_db()


def _simple_payload_from_form(def_key, name, category, description):
    names = request.form.getlist('step_name')
    types = request.form.getlist('step_assignee_type')
    values = request.form.getlist('step_assignee_value')
    modes = request.form.getlist('step_mode')
    steps = []
    for i, raw_name in enumerate(names):
        step_type = types[i] if i < len(types) else ''
        if not (raw_name.strip() or step_type):
            continue
        steps.append({
            'name': raw_name.strip(),
            'assignee_type': step_type,
            'assignee_value': values[i].strip() if i < len(values) else '',
            'mode': modes[i] if i < len(modes) and modes[i] else 'any'
        })
    action_key = (request.form.get('action_key') or '').strip()
    action = None
    if action_key:
        meta = next((a for a in wf.action_options() if a['key'] == action_key), None)
        if not meta:
            raise ValueError('所选动作不存在，请刷新后重试')
        action = {'key': action_key, 'label': meta['label'], 'plugin': meta['plugin']}
    return wf.build_simple_definition(def_key, name, steps, action, category, description)


def _instance_visible(inst, user):
    if not inst:
        return False
    if user['role'] == 'platform_admin':
        return True
    if int(inst['initiator_id'] or 0) == int(user['id'] or 0):
        return True
    tasks = wf.get_tasks(_db(), inst['id'])
    return any((t['assignee_id'] and int(t['assignee_id']) == int(user['id']))
               or (t['candidate_role'] and t['candidate_role'] == user['role']) for t in tasks)


@workflow_bp.app_context_processor
def _inject_workflow_nav():
    try:
        if not session.get('user_id'):
            return {'workflow_pending_count': 0}
        db = sqlite3.connect(current_app.config['DATABASE'], timeout=3.0)
        c = db.execute(
            "SELECT COUNT(*) c FROM wf_tasks t JOIN wf_instances i ON i.id=t.instance_id "
            "WHERE t.status='pending' AND i.status IN ('running','waiting') "
            "AND ((t.assignee_id IS NOT NULL AND t.assignee_id=?) OR (t.candidate_role<>'' AND t.candidate_role=?))",
            (session.get('user_id'), session.get('user_role', ''))).fetchone()['c']
        db.close()
        return {'workflow_pending_count': c}
    except Exception:
        return {'workflow_pending_count': 0}


@workflow_bp.before_request
def _workflow_before():
    # v13.8：外部插件事件接口走令牌校验，不要求登录会话
    if request.path.rstrip('/') == '/workflow/api/plugin-events':
        return None
    auth = _login_required()
    if auth:
        return auth
    try:
        db = _db()
        wf.init_tables(db)
        wf.seed_defaults(db)
        # 每次进入流程页面顺手推进待投递事件；失败不影响页面
        try:
            wf.dispatch_outbox(db, limit=10)
        except Exception:
            pass
    except Exception as e:
        current_app.logger.warning('workflow init failed: %s', e)
    return None


@workflow_bp.route('/')
def my_tasks():
    # v13.2：流程待办已并入我的工作台；保留入口做兼容跳转。
    return redirect(url_for('index'))


@workflow_bp.route('/instance/<instance_id>')
def instance(instance_id):
    user = _current_user()
    db = _db()
    inst = wf.get_instance(db, instance_id)
    if not inst or not _instance_visible(inst, user):
        flash('流程不存在或无权查看', 'error')
        return redirect(url_for('index'))
    definition_row = db.execute(
        'SELECT * FROM wf_definition_versions WHERE def_key=? AND version=?',
        (inst['def_key'], inst['def_version'])).fetchone()
    definition = json.loads(definition_row['graph_json']) if definition_row else {}
    tasks = wf.get_tasks(db, instance_id)
    events = wf.get_events(db, instance_id)
    my_pending = [t for t in tasks if t['status'] == 'pending'
                  and ((t['assignee_id'] and int(t['assignee_id']) == int(user['id']))
                       or (t['candidate_role'] and t['candidate_role'] == user['role']))]
    context = {}
    try:
        context = json.loads(inst['context_json'] or '{}')
    except Exception:
        pass
    return render_template('workflow_instance.html', inst=inst, definition=definition,
                           tasks=tasks, events=events, my_pending=my_pending,
                           context=context, user=user, is_admin=user['role'] == 'platform_admin')


@workflow_bp.route('/task/<int:task_id>/action', methods=['POST'])
def task_action(task_id):
    user = _current_user()
    db = _db()
    decision = (request.form.get('decision') or '').strip()
    comment = (request.form.get('comment') or '').strip()
    try:
        inst = wf.approve_task(db, task_id, user['id'], user['name'], user['role'], decision, comment)
        flash('审批已提交：%s' % inst['status'], 'success')
        return redirect(url_for('workflow.instance', instance_id=inst['id']))
    except Exception as e:
        flash('审批失败：%s' % e, 'error')
        task = db.execute('SELECT * FROM wf_tasks WHERE id=?', (task_id,)).fetchone()
        if task:
            return redirect(url_for('workflow.instance', instance_id=task['instance_id']))
        return redirect(url_for('index'))


@workflow_bp.route('/start', methods=['GET', 'POST'])
def start():
    user = _current_user()
    db = _db()
    defs = wf.list_definitions(db)
    def_key = (request.values.get('def_key') or 'generic_approval').strip()
    error = None
    if request.method == 'POST':
        try:
            context_raw = (request.form.get('context_json') or '{}').strip()
            context = json.loads(context_raw) if context_raw else {}
            if not isinstance(context, dict):
                raise ValueError('上下文必须是 JSON 对象')
            inst = wf.start_instance(
                db, def_key,
                plugin_id=(request.form.get('plugin_id') or 'platform').strip(),
                record_type=(request.form.get('record_type') or '').strip(),
                record_id=(request.form.get('record_id') or '').strip(),
                business_key=(request.form.get('business_key') or '').strip(),
                title=(request.form.get('title') or '').strip(),
                context=context,
                initiator_id=user['id'], initiator_name=user['name'])
            flash('流程已发起', 'success')
            return redirect(url_for('workflow.instance', instance_id=inst['id']))
        except Exception as e:
            error = str(e)
    return render_template('workflow_start.html', definitions=defs, def_key=def_key, error=error)


@workflow_bp.route('/start/wbs/task/<int:tid>', methods=['POST'])
def start_wbs_task(tid):
    user = _current_user()
    db = _db()
    t = db.execute('SELECT * FROM wbs_tasks WHERE id=?', (tid,)).fetchone()
    if not t:
        flash('WBS 任务不存在', 'error')
        return redirect(url_for('wbs.wbs_projects'))
    p = db.execute('SELECT * FROM wbs_projects WHERE id=?', (t['project_id'],)).fetchone()
    try:
        open_inst = db.execute(
            "SELECT id FROM wf_instances WHERE business_key=? AND status IN ('running','waiting','error') "
            "ORDER BY created_at DESC LIMIT 1", ('WBS-TASK-%s' % tid,)).fetchone()
        if open_inst:
            flash('该任务已有进行中的平台审批流', 'success')
            return redirect(url_for('workflow.instance', instance_id=open_inst['id']))
        old_status = t['approval_status']
        db.execute("UPDATE wbs_tasks SET approval_status='待审批' WHERE id=?", (tid,))
        db.commit()
        inst = wf.start_instance(
            db, 'wbs_task_approval', plugin_id='wbs', record_type='task', record_id=str(tid),
            business_key='WBS-TASK-%s' % tid, title='%s / %s' % ((p['name'] if p else ''), t['name'] or ('任务 #%s' % tid)),
            context={'owner': t['owner'] or '', 'participants': t['participants'] or '',
                     'project_id': t['project_id'], 'wbs_code': t['wbs_code'] or '',
                     'task_name': t['name'] or '', 'current_status': t['approval_status'] or ''},
            initiator_id=user['id'], initiator_name=user['name'])
        flash('已发起平台审批流', 'success')
        return redirect(url_for('workflow.instance', instance_id=inst['id']))
    except Exception as e:
        try:
            db.execute("UPDATE wbs_tasks SET approval_status=? WHERE id=?", (old_status, tid))
            db.commit()
        except Exception:
            pass
        flash('发起失败：%s' % e, 'error')
        return redirect(url_for('wbs.wbs_project_detail', pid=t['project_id']))


# ============================================================
# 管理员
# ============================================================

@workflow_bp.route('/admin')
def admin_home():
    # v13.8：并入“审批中心”，旧地址保留并默认打开运行记录页签
    return _render_admin_center('runtime')


@workflow_bp.route('/admin/definition/<def_key>', methods=['GET', 'POST'])
def admin_definition(def_key):
    deny = _admin_required()
    if deny:
        return deny
    db = _db()
    row, ver = wf.get_current_version(db, def_key)
    if not row:
        flash('流程模板不存在', 'error')
        return redirect(url_for('workflow.admin_home'))
    error = None
    definition = json.loads(ver['graph_json']) if ver else {}
    if request.method == 'POST':
        try:
            name = (request.form.get('name') or row['name']).strip()
            category = (request.form.get('category') or row['category']).strip()
            description = (request.form.get('description') or row['description']).strip()
            if request.form.get('editor_mode') == 'simple':
                payload = _simple_payload_from_form(def_key, name, category, description)
            else:
                payload = json.loads(request.form.get('definition_json') or '{}')
            wf.save_definition(db, def_key, name, payload, actor=_current_user()['name'],
                               category=category, description=description)
            _apply_trigger_from_form(db, def_key)
            flash('新版本已发布；运行中的实例仍使用旧版本', 'success')
            return redirect(url_for('workflow.admin_definition', def_key=def_key))
        except Exception as e:
            error = str(e)
            definition = json.loads(json.dumps(payload)) if 'payload' in locals() else definition
    return render_template('workflow_definition.html', def_row=row, ver=ver,
                           definition_json=json.dumps(definition, ensure_ascii=False, indent=2),
                           simple=wf.parse_simple_definition(definition),
                           actions=wf.action_options(),
                           events=wf.event_options(), event_groups=wf.event_catalog(db), modes=wf.EVENT_MODES,
                           trigger=wf.get_trigger_for_def(db, def_key),
                           versions=wf.list_versions(db, def_key), error=error)


@workflow_bp.route('/admin/definition/new', methods=['GET', 'POST'])
def admin_definition_new():
    deny = _admin_required()
    if deny:
        return deny
    db = _db()
    error = None
    prefill = {}
    starter = {
        'key': 'new_process', 'name': '新流程',
        'nodes': [
            {'id': 'start', 'type': 'start', 'name': '开始', 'next': 'approve'},
            {'id': 'approve', 'type': 'approval', 'name': '负责人审批',
             'assignee': {'type': 'role', 'value': 'admin'}, 'mode': 'any',
             'next': {'approve': 'end_approve', 'reject': 'end_reject', 'return': 'end_return'}},
            {'id': 'end_approve', 'type': 'end', 'name': '已通过'},
            {'id': 'end_reject', 'type': 'end', 'name': '已驳回'},
            {'id': 'end_return', 'type': 'end', 'name': '已退回'},
        ],
    }
    # 从状态变更节点进入时，自动带出推荐流程（审批人可再调整）
    event_key = (request.args.get('event') or '').strip()
    if request.method == 'GET' and event_key:
        ev = wf.get_event(event_key)
        if ev:
            bound = db.execute('SELECT def_key FROM wf_triggers WHERE plugin_id=? AND event_key=?',
                               (ev['plugin'], event_key)).fetchone()
            if bound and wf.get_current_version(db, bound['def_key'])[0]:
                flash('该状态变更节点已绑定流程「%s」，可直接修改' % bound['def_key'], 'info')
                return redirect(url_for('workflow.admin_definition', def_key=bound['def_key']))
            try:
                starter = wf.build_default_definition_for_event(ev['plugin'], event_key, db=db)
                prefill = {'def_key': starter['key'], 'name': starter['name'],
                           'category': '业务流程',
                           'description': ev.get('description') or ev.get('label')}
            except Exception as e:
                error = '自动生成失败：%s' % e
    if request.method == 'POST':
        try:
            name = (request.form.get('name') or '').strip()
            def_key = (request.form.get('def_key') or '').strip()
            auto_key = request.form.get('auto_key') == '1'
            if auto_key or not def_key:
                def_key = wf.generate_def_key(db, name=name,
                                              event_key=(request.form.get('trigger_event') or '').split(':')[-1])
            category = (request.form.get('category') or '通用').strip()
            description = (request.form.get('description') or '').strip()
            if request.form.get('editor_mode') == 'simple':
                payload = _simple_payload_from_form(def_key, name, category, description)
            else:
                payload = json.loads(request.form.get('definition_json') or '{}')
                payload['key'] = def_key
                payload['name'] = name
            wf.save_definition(db, def_key, name, payload, actor=_current_user()['name'],
                               category=category, description=description)
            _apply_trigger_from_form(db, def_key)
            flash('流程模板已创建', 'success')
            return redirect(url_for('workflow.admin_definition', def_key=def_key))
        except Exception as e:
            error = str(e)
            starter = payload if 'payload' in locals() else starter
    return render_template('workflow_definition.html', def_row=None, ver=None,
                           prefill=prefill,
                           definition_json=json.dumps(starter, ensure_ascii=False, indent=2),
                           simple=wf.parse_simple_definition(starter),
                           actions=wf.action_options(),
                           events=wf.event_options(), event_groups=wf.event_catalog(db), modes=wf.EVENT_MODES,
                           event_key_param=event_key,
                           trigger=wf.get_trigger_for_def(db, (prefill or {}).get('def_key') or ''),
                           versions=[], error=error, is_new=True)


@workflow_bp.route('/admin/instance/<instance_id>/retry', methods=['POST'])
def admin_retry(instance_id):
    deny = _admin_required()
    if deny:
        return deny
    try:
        wf.retry_instance(_db(), instance_id, _current_user()['id'], _current_user()['name'])
        flash('已重试流程', 'success')
    except Exception as e:
        flash('重试失败：%s' % e, 'error')
    return redirect(url_for('workflow.instance', instance_id=instance_id))


@workflow_bp.route('/admin/instance/<instance_id>/cancel', methods=['POST'])
def admin_cancel(instance_id):
    deny = _admin_required()
    if deny:
        return deny
    try:
        wf.cancel_instance(_db(), instance_id, _current_user()['id'], _current_user()['name'],
                           request.form.get('reason') or '管理员取消')
        flash('流程已取消', 'success')
    except Exception as e:
        flash('取消失败：%s' % e, 'error')
    return redirect(url_for('workflow.instance', instance_id=instance_id))


@workflow_bp.route('/admin/subscription', methods=['POST'])
def admin_subscription_add():
    deny = _admin_required()
    if deny:
        return deny
    try:
        wf.add_subscription(_db(), request.form.get('plugin_id'), request.form.get('event_type'),
                            request.form.get('action'))
        flash('事件订阅已保存', 'success')
    except Exception as e:
        flash('订阅保存失败：%s' % e, 'error')
    return redirect(url_for('workflow.admin_home'))


@workflow_bp.route('/admin/subscription/<int:sub_id>/delete', methods=['POST'])
def admin_subscription_delete(sub_id):
    deny = _admin_required()
    if deny:
        return deny
    try:
        wf.delete_subscription(_db(), sub_id)
        flash('订阅已删除', 'success')
    except Exception as e:
        flash('删除失败：%s' % e, 'error')
    return redirect(url_for('workflow.admin_home'))


@workflow_bp.route('/admin/audit.csv')
def admin_audit_csv():
    deny = _admin_required()
    if deny:
        return deny
    db = _db()
    rows = db.execute(
        'SELECT i.id, i.def_key, i.business_key, i.title, i.status, i.initiator_name, i.created_at, i.ended_at, '
        'e.seq, e.event_type, e.actor_name, e.detail_json, e.created_at AS event_at '
        'FROM wf_instances i LEFT JOIN wf_events e ON e.instance_id=i.id ORDER BY i.created_at DESC, e.seq'
    ).fetchall()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(['实例ID', '流程', '业务单号', '标题', '实例状态', '发起人', '发起时间', '结束时间',
                '事件序号', '事件', '操作人', '详情', '事件时间'])
    for r in rows:
        w.writerow([r['id'], r['def_key'], r['business_key'], r['title'], r['status'], r['initiator_name'],
                    r['created_at'], r['ended_at'], r['seq'], r['event_type'], r['actor_name'],
                    r['detail_json'], r['event_at']])
    data = ('\ufeff' + buf.getvalue()).encode('utf-8')
    return current_app.response_class(data, mimetype='text/csv',
                                      headers={'Content-Disposition': 'attachment; filename=workflow_audit.csv'})


# ============================================================
# JSON API（供插件或页面调用）
# ============================================================

@workflow_bp.route('/api/instances', methods=['POST'])
def api_start_instance():
    user = _current_user()
    if not user['id']:
        return jsonify({'ok': False, 'msg': '未登录'}), 401
    data = request.get_json(silent=True) or {}
    try:
        inst = wf.start_instance(
            _db(), (data.get('def_key') or '').strip(),
            plugin_id=(data.get('plugin_id') or 'platform').strip(),
            record_type=(data.get('record_type') or '').strip(),
            record_id=str(data.get('record_id') or ''),
            business_key=(data.get('business_key') or '').strip(),
            title=(data.get('title') or '').strip(),
            context=data.get('context') or {},
            initiator_id=user['id'], initiator_name=user['name'])
        return jsonify({'ok': True, 'instance_id': inst['id'], 'status': inst['status']})
    except Exception as e:
        return jsonify({'ok': False, 'msg': str(e)}), 400


@workflow_bp.route('/api/instances/<instance_id>')
def api_instance(instance_id):
    user = _current_user()
    db = _db()
    inst = wf.get_instance(db, instance_id)
    if not inst or not _instance_visible(inst, user):
        return jsonify({'ok': False, 'msg': 'not found'}), 404
    return jsonify({
        'ok': True,
        'instance': {
            'id': inst['id'], 'def_key': inst['def_key'], 'def_version': inst['def_version'],
            'status': inst['status'], 'current_node': inst['current_node'], 'decision': inst['decision'],
            'business_key': inst['business_key'], 'title': inst['title'],
            'initiator': inst['initiator_name'], 'created_at': inst['created_at'], 'ended_at': inst['ended_at'],
            'context': json.loads(inst['context_json'] or '{}'),
        },
        'tasks': [dict(r) for r in wf.get_tasks(db, instance_id)],
        'events': [dict(r) for r in wf.get_events(db, instance_id)],
    })


@workflow_bp.route('/api/tasks/mine')
def api_my_tasks():
    user = _current_user()
    if not user['id']:
        return jsonify({'ok': False, 'msg': '未登录'}), 401
    return jsonify({'ok': True, 'tasks': [dict(r) for r in wf.my_tasks(_db(), user['id'], user['role'])]})


# ============================================================
# v13.3 状态变更节点：目录 / 绑定 / 待发起队列
# ============================================================
def _apply_trigger_from_form(db, def_key):
    """保存流程时同步节点绑定：选了节点就绑定，选空则解绑。"""
    raw = (request.form.get('trigger_event') or '').strip()
    mode = (request.form.get('trigger_mode') or 'confirm').strip()
    actor = _current_user()['name']
    if not raw:
        wf.unbind_by_def(db, def_key)
        return
    if ':' not in raw:
        return
    plugin_id, event_key = raw.split(':', 1)
    # 容错：只写了对象段（project.approval）时补全插件前缀
    if not wf.get_event(event_key) and wf.get_event('%s.%s' % (plugin_id, event_key)):
        event_key = '%s.%s' % (plugin_id, event_key)
    # v13.7：已有规则改流程时保留原适用范围，避免误把“指定项目类型”扩成“全部”
    current = wf.get_trigger_for_def(db, def_key) or {}
    scope_type = (request.form.get('trigger_scope_type') or current.get('scope_type') or 'all').strip()
    scope_value = (request.form.get('trigger_scope_value') or current.get('scope_value') or '').strip()
    wf.bind_trigger(db, def_key, plugin_id, event_key, mode=mode, actor=actor,
                    scope_type=scope_type, scope_value=scope_value)


@workflow_bp.route('/admin/events/guide')
def admin_events_guide():
    """节点说明子页（v13.4）：给流程搭建者看的业务解释，不出现技术术语。"""
    deny = _admin_required()
    if deny:
        return deny
    db = _db()
    catalog = wf.event_catalog(db)
    bound_count = sum(1 for g in catalog for e in g['events'] if e.get('trigger'))
    return render_template('workflow_events_guide.html',
                           catalog=catalog,
                           modes=wf.EVENT_MODES,
                           stats=wf.stats(db),
                           bound_count=bound_count,
                           user=_current_user())


@workflow_bp.route('/api/plugin-events', methods=['POST'])
def plugin_events():
    """外部插件上报状态变更（服务间调用，不走登录会话）。

    请求头 X-Plugin-Token；请求体：
    {plugin_id, event_key, business_key, title, context, record_type, record_id}
    """
    data = request.get_json(silent=True) or {}
    plugin_id = str(data.get('plugin_id') or '').strip()
    event_key = str(data.get('event_key') or '').strip()
    token = request.headers.get('X-Plugin-Token') or data.get('token') or ''
    if not plugin_id or not event_key:
        return jsonify({'ok': False, 'msg': 'plugin_id 和 event_key 必填'}), 400
    if not wf.verify_plugin_event_token(plugin_id, token):
        return jsonify({'ok': False, 'msg': '插件令牌无效'}), 403
    ev = wf.get_event(event_key)
    if not ev or ev.get('plugin') != plugin_id:
        return jsonify({'ok': False, 'msg': '事件未注册或不属于该插件；请先在 manifest.workflow.events 声明'}), 400
    context = data.get('context') or {}
    if not isinstance(context, dict):
        return jsonify({'ok': False, 'msg': 'context 必须是对象'}), 400
    try:
        result = wf.emit_state_change(
            _db(), plugin_id, event_key,
            business_key=str(data.get('business_key') or ''),
            title=str(data.get('title') or ev.get('label') or event_key),
            context=context,
            record_type=str(data.get('record_type') or ev.get('record_type') or ''),
            record_id=str(data.get('record_id') or ''),
            initiator_name=str(data.get('initiator_name') or '外部插件'),
        )
    except Exception as e:
        return jsonify({'ok': False, 'msg': '事件处理失败：%s' % e}), 500
    return jsonify({'ok': True, **result})


@workflow_bp.route('/api/rule-preview')
def rule_preview():
    """普通用户看：这次提交会按哪条规则、送谁审批。不改任何数据。"""
    if not session.get('user_id'):
        return jsonify({'ok': False, 'msg': '未登录'}), 401
    plugin_id = (request.args.get('plugin') or '').strip()
    event_key = (request.args.get('event') or '').strip()
    context = {}
    for k in ('project_type', 'product_code', 'product'):
        v = request.args.get(k)
        if v:
            context[k] = v
    if not plugin_id or not event_key:
        return jsonify({'ok': False, 'msg': '参数不完整'}), 400
    db = _db()
    info = wf.event_managed(db, plugin_id, event_key, context)
    if not info:
        return jsonify({'ok': True, 'configured': False,
                        'scope': '', 'steps': []})
    trg = info.get('trigger') or {}
    row, ver = wf.get_current_version(db, trg.get('def_key'))
    definition = json.loads(ver['graph_json']) if ver else {}
    labels = {a['value']: a['label'] for a in wf.actor_options(db, kind='user')}
    role_labels = {a['value']: a['label'] for a in wf.actor_options(db, kind='role')}
    steps = []
    for st in wf.parse_simple_definition(definition).get('steps') or []:
        typ = st.get('assignee_type') or ''
        val = st.get('assignee_value') or ''
        if typ == 'user':
            approver = labels.get(val, val)
        elif typ == 'role':
            approver = role_labels.get(val, val)
        elif typ == 'record_field':
            approver = {'owner': '项目负责人', 'participants': '项目参与人'}.get(val, val)
        elif typ == 'initiator':
            approver = '发起人'
        else:
            approver = val or '待定'
        backup = ''
        if st.get('backup_assignee_type') == 'user' and st.get('backup_assignee_value'):
            backup = labels.get(st.get('backup_assignee_value'), st.get('backup_assignee_value'))
        steps.append({'name': st.get('name') or '审批', 'approver': approver, 'backup': backup})
    scope_type = trg.get('scope_type') or 'all'
    scope_value = trg.get('scope_value') or ''
    return jsonify({'ok': True, 'configured': True,
                    'scope': wf.SCOPE_TYPES.get(scope_type, scope_type) + (('：' + scope_value) if scope_value else ''),
                    'mode': trg.get('mode') or 'auto', 'steps': steps})


@workflow_bp.route('/admin/api/actors')
def admin_actors():
    """审批人搜索数据源：来自平台「用户管理」，支持姓名/账号/部门模糊搜索。"""
    user = _current_user()
    if not user['id']:
        return jsonify({'ok': False, 'msg': '未登录'}), 401
    if user['role'] != 'platform_admin':
        return jsonify({'ok': False, 'msg': '仅管理员可用'}), 403
    try:
        items = wf.actor_options(_db(), kind=request.args.get('type') or 'user',
                                 q=request.args.get('q') or '')
        return jsonify({'ok': True, 'items': items})
    except Exception as e:
        return jsonify({'ok': False, 'msg': str(e)}), 500


@workflow_bp.route('/admin/events')
def admin_events():
    # 旧版“状态变更节点”页保留为高级入口；日常配置走 /admin/rules 审批规则中心
    deny = _admin_required()
    if deny:
        return deny
    db = _db()
    return render_template('workflow_events.html',
                           catalog=wf.event_catalog(db),
                           definitions=wf.list_definitions(db),
                           modes=wf.EVENT_MODES,
                           bound_count=len([t for t in wf.list_triggers(db) if int(t['enabled'] or 0)]),
                           queue=wf.queue_rows(db, limit=60),
                           stats=wf.stats(db),
                           user=_current_user())


def _scope_options():
    """可选范围值：产品来自版本规划插件，项目类型来自 WBS。"""
    products = []
    try:
        pdb = get_plugin_db('version_plan')
        try:
            rows = pdb.execute('SELECT code, name FROM version_plan_products ORDER BY code').fetchall()
            products = [{'value': r['code'], 'label': '%s（%s）' % (r['name'] or r['code'], r['code'])} for r in rows]
        finally:
            pdb.close()
    except Exception:
        products = []
    return {'products': products, 'project_types': ['研发项目', '交付项目', '内部项目', '其他项目']}


@workflow_bp.route('/admin/rules')
def admin_rules():
    return _render_admin_center('rules')


@workflow_bp.route('/admin/rules/<plugin_id>/<event_key>', methods=['GET', 'POST'])
def admin_rule_edit(plugin_id, event_key):
    deny = _admin_required()
    if deny:
        return deny
    db = _db()
    ev = wf.get_event(event_key)
    if not ev or ev.get('plugin') != plugin_id:
        flash('状态变更点不存在', 'error')
        return redirect(url_for('workflow.admin_rules'))
    trigger_id = request.args.get('rule', type=int) or 0
    trigger = db.execute('SELECT * FROM wf_triggers WHERE id=?', (trigger_id,)).fetchone() if trigger_id else None
    def_key = trigger['def_key'] if trigger else ''
    trigger_name = ''
    existing = None
    if def_key:
        row, ver = wf.get_current_version(db, def_key)
        if row:
            trigger_name = row['name'] or def_key
        if ver:
            existing = wf.parse_simple_definition(json.loads(ver['graph_json']))
    error = None
    if request.method == 'POST':
        try:
            name = (request.form.get('rule_name') or '').strip()
            scope_type = (request.form.get('scope_type') or 'all').strip()
            scope_value = (request.form.get('scope_value') or '').strip()
            mode = (request.form.get('mode') or 'auto').strip()
            names = request.form.getlist('step_name')
            values = request.form.getlist('step_assignee_value')
            backups = request.form.getlist('step_backup_value')
            steps = []
            for i, raw_name in enumerate(names):
                value = values[i].strip() if i < len(values) else ''
                if not (raw_name.strip() or value):
                    continue
                if not value:
                    raise ValueError('第 %d 步请选择主审批人' % (i + 1))
                steps.append({'name': raw_name.strip() or '审批', 'assignee_type': 'user',
                              'assignee_value': value, 'mode': 'any',
                              'backup_assignee_type': 'user' if (i < len(backups) and backups[i].strip()) else '',
                              'backup_assignee_value': backups[i].strip() if i < len(backups) else ''})
            if not steps:
                raise ValueError('至少添加一个审批节点')
            if not name:
                raise ValueError('规则名称不能为空')
            action_key = (request.form.get('action_key') or '').strip()
            action = None
            if action_key:
                meta = next((a for a in wf.action_options() if a['key'] == action_key), None)
                if not meta:
                    raise ValueError('写回动作不存在，请刷新重试')
                action = {'key': action_key, 'label': meta['label'], 'plugin': meta['plugin']}
            if trigger:
                new_key = def_key
                old_trigger_id = trigger['id']
            else:
                new_key = wf.generate_def_key(db, name=name, event_key=event_key)
                old_trigger_id = None
            payload = wf.build_simple_definition(new_key, name, steps, action,
                                                 '业务流程', ev.get('description') or ev.get('label'))
            wf.save_definition(db, new_key, name, payload, actor=_current_user()['name'],
                               category='业务流程', description=ev.get('description') or ev.get('label'))
            if old_trigger_id:
                db.execute('DELETE FROM wf_triggers WHERE id=?', (old_trigger_id,))
                db.commit()
            wf.bind_trigger(db, new_key, plugin_id, event_key, mode=mode,
                            actor=_current_user()['name'], scope_type=scope_type, scope_value=scope_value,
                            replace=False)
            flash('审批规则已保存：%s' % ('仅影响之后新提交的业务' if trigger else '已对新提交业务生效'), 'success')
            return redirect(url_for('workflow.admin_rules'))
        except Exception as e:
            error = str(e)
            existing = {'steps': [{'name': n, 'assignee_type': 'user',
                                   'assignee_value': values[i] if i < len(values) else '',
                                   'backup_assignee_type': 'user' if i < len(backups) and backups[i] else '',
                                   'backup_assignee_value': backups[i] if i < len(backups) else '',
                                   'mode': 'any'} for i, n in enumerate(names)], 'action': None}
    # GET prefill
    if not existing:
        existing = {'steps': [{'name': '负责人审批', 'assignee_type': 'user', 'assignee_value': '',
                               'backup_assignee_type': '', 'backup_assignee_value': '', 'mode': 'any'}],
                    'action': {'key': (ev.get('write_back') or {}).get('key') or '',
                               'label': (ev.get('write_back') or {}).get('label') or ''}}
    return render_template('workflow_rule_edit.html', ev=ev, plugin_id=plugin_id,
                           event_key=event_key, trigger=trigger, def_key=def_key,
                           trigger_name=trigger_name,
                           existing=existing, error=error, scopes=wf.SCOPE_TYPES,
                           modes=wf.EVENT_MODES, scope_options=_scope_options(),
                           action_options=wf.action_options(), user=_current_user())


@workflow_bp.route('/admin/trigger/bind', methods=['POST'])
def admin_trigger_bind():
    deny = _admin_required()
    if deny:
        return deny
    db = _db()
    try:
        raw = (request.form.get('event') or '').strip()
        plugin_id, event_key = raw.split(':', 1)
        def_key = (request.form.get('def_key') or '').strip()
        if not def_key:
            raise ValueError('请选择要绑定的流程')
        wf.bind_trigger(db, def_key, plugin_id, event_key,
                        mode=request.form.get('mode') or 'confirm', actor=_current_user()['name'])
        flash('已绑定：业务状态变更后按配置进入流程', 'success')
    except Exception as e:
        flash('绑定失败：%s' % e, 'error')
    return redirect(url_for('workflow.admin_events'))


@workflow_bp.route('/admin/events/<plugin_id>/<event_key>/quick', methods=['POST'])
def admin_event_quick(plugin_id, event_key):
    """一键生成推荐流程并绑定，业务人员可直接用。"""
    deny = _admin_required()
    if deny:
        return deny
    db = _db()
    try:
        ev = wf.get_event(event_key)
        if not ev or ev['plugin'] != plugin_id:
            raise ValueError('状态变更节点不存在')
        bound = db.execute('SELECT * FROM wf_triggers WHERE plugin_id=? AND event_key=?',
                           (plugin_id, event_key)).fetchone()
        if bound and wf.get_current_version(db, bound['def_key'])[0]:
            return redirect(url_for('workflow.admin_definition', def_key=bound['def_key']))
        payload = wf.build_default_definition_for_event(plugin_id, event_key, db=db)
        wf.save_definition(db, payload['key'], payload['name'], payload,
                           actor=_current_user()['name'], category='业务流程',
                           description=ev.get('description') or ev.get('label'))
        wf.bind_trigger(db, payload['key'], plugin_id, event_key,
                        mode=request.form.get('mode') or 'confirm', actor=_current_user()['name'])
        flash('已生成流程「%s」并绑定，可继续调整审批人' % payload['name'], 'success')
        return redirect(url_for('workflow.admin_definition', def_key=payload['key']))
    except Exception as e:
        flash('生成失败：%s' % e, 'error')
        return redirect(url_for('workflow.admin_events'))


@workflow_bp.route('/admin/trigger/<int:trigger_id>/mode', methods=['POST'])
def admin_trigger_mode(trigger_id):
    deny = _admin_required()
    if deny:
        return deny
    wf.set_trigger_mode(_db(), trigger_id, request.form.get('mode') or 'confirm')
    flash('触发方式已更新', 'success')
    return redirect(url_for('workflow.admin_events'))


@workflow_bp.route('/admin/trigger/<int:trigger_id>/toggle', methods=['POST'])
def admin_trigger_toggle(trigger_id):
    deny = _admin_required()
    if deny:
        return deny
    db = _db()
    row = db.execute('SELECT * FROM wf_triggers WHERE id=?', (trigger_id,)).fetchone()
    if row:
        wf.set_trigger_enabled(db, trigger_id, not int(row['enabled'] or 0))
        flash('已%s该节点的自动接入' % ('启用' if not int(row['enabled'] or 0) else '停用'), 'success')
    return redirect(url_for('workflow.admin_events'))


@workflow_bp.route('/admin/trigger/<int:trigger_id>/delete', methods=['POST'])
def admin_trigger_delete(trigger_id):
    deny = _admin_required()
    if deny:
        return deny
    wf.unbind_trigger(_db(), trigger_id)
    flash('已解绑；该流程仍可在工作台手动发起', 'success')
    return redirect(url_for('workflow.admin_events'))


@workflow_bp.route('/queue/<int:queue_id>/start', methods=['POST'])
def queue_start(queue_id):
    user = _current_user()
    if not user['id']:
        return redirect(url_for('workflow.my_tasks'))
    db = _db()
    item = wf.get_queue_item(db, queue_id)
    if not item:
        flash('待发起记录不存在', 'error')
        return redirect(url_for('workflow.my_tasks'))
    if user['role'] != 'platform_admin' and item['initiator_id'] and item['initiator_id'] != user['id']:
        flash('只能发起与自己相关的业务审批', 'error')
        return redirect(url_for('workflow.my_tasks'))
    try:
        inst = wf.start_from_queue(db, queue_id, user['id'], user['name'])
        flash('流程已发起，等待审批', 'success')
        return redirect(url_for('workflow.instance', instance_id=inst['id']))
    except Exception as e:
        flash('发起失败：%s' % e, 'error')
        return redirect(url_for('workflow.my_tasks'))


@workflow_bp.route('/queue/<int:queue_id>/ignore', methods=['POST'])
def queue_ignore(queue_id):
    user = _current_user()
    db = _db()
    try:
        wf.ignore_queue(db, queue_id, user['name'])
        flash('已忽略该待发起项', 'success')
    except Exception as e:
        flash('操作失败：%s' % e, 'error')
    return redirect(url_for('workflow.my_tasks'))


@workflow_bp.route('/notification/<int:nid>/open')
def notification_open(nid):
    """工作台通知：标为已读后跳到通知指向的业务页面。"""
    user = _current_user()
    deny = _login_required()
    if deny:
        return deny
    db = _db()
    row = wf.mark_notification_read(db, nid, user['id'])
    if not row:
        flash('通知不存在或不属于当前用户', 'error')
        return redirect(url_for('index'))
    target = (row['url'] or '').strip()
    # 只允许站内单斜杠路径；排除 //evil.com、反斜杠绕过等协议相对跳转。
    if (not target.startswith('/') or target.startswith('//')
            or '\\' in target or '\r' in target or '\n' in target):
        target = url_for('workflow.my_tasks')
    return redirect(target)


@workflow_bp.route('/notifications/read-all', methods=['POST'])
def notifications_read_all():
    user = _current_user()
    deny = _login_required()
    if deny:
        return deny
    db = _db()
    n = wf.mark_all_notifications_read(db, user['id'])
    flash('已将 %d 条通知标为已读' % n, 'success')
    return redirect(url_for('index'))
