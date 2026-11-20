# -*- coding: utf-8 -*-
"""流程中心路由：我的审批、实例详情、模板管理、发起流程与 JSON API。"""
import csv
import io
import json
import sqlite3
from datetime import datetime

from flask import Blueprint, current_app, flash, jsonify, redirect, render_template, request, session, url_for

from plugin_system import get_db
import workflow_engine as wf

workflow_bp = Blueprint('workflow', __name__, url_prefix='/workflow')


def _current_user():
    return {
        'id': session.get('user_id'),
        'name': session.get('user_name', ''),
        'role': session.get('user_role', ''),
        'username': session.get('username', ''),
    }


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


@workflow_bp.route('/start/wbs/project/<int:pid>', methods=['POST'])
def start_wbs_project(pid):
    user = _current_user()
    db = _db()
    p = db.execute('SELECT * FROM wbs_projects WHERE id=?', (pid,)).fetchone()
    if not p:
        flash('WBS 项目不存在', 'error')
        return redirect(url_for('wbs.wbs_projects'))
    try:
        open_inst = db.execute(
            "SELECT id FROM wf_instances WHERE business_key=? AND status IN ('running','waiting','error') "
            "ORDER BY created_at DESC LIMIT 1", ('WBS-PROJECT-%s' % pid,)).fetchone()
        if open_inst:
            flash('该项目已有进行中的平台审批流', 'success')
            return redirect(url_for('workflow.instance', instance_id=open_inst['id']))
        old_status = p['approval_status']
        db.execute("UPDATE wbs_projects SET approval_status='待审批', updated_at=datetime('now','localtime') WHERE id=?", (pid,))
        db.commit()
        inst = wf.start_instance(
            db, 'wbs_project_approval', plugin_id='wbs', record_type='project', record_id=str(pid),
            business_key='WBS-PROJECT-%s' % pid, title=p['name'] or ('WBS 项目 #%s' % pid),
            context={'owner': p['owner'] or '', 'participants': p['participants'] or '',
                     'project_name': p['name'] or '', 'current_status': p['approval_status'] or ''},
            initiator_id=user['id'], initiator_name=user['name'])
        flash('已发起平台审批流', 'success')
        return redirect(url_for('workflow.instance', instance_id=inst['id']))
    except Exception as e:
        try:
            db.execute("UPDATE wbs_projects SET approval_status=?, updated_at=datetime('now','localtime') WHERE id=?",
                       (old_status, pid))
            db.commit()
        except Exception:
            pass
        flash('发起失败：%s' % e, 'error')
        return redirect(url_for('wbs.wbs_project_detail', pid=pid))


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
    deny = _admin_required()
    if deny:
        return deny
    db = _db()
    defs = wf.list_definitions(db)
    instances = wf.list_instances(db, limit=80)
    st = wf.stats(db)
    outbox = wf.outbox_rows(db)
    subscriptions = wf.list_subscriptions(db)
    triggers = wf.list_triggers(db)
    queue = wf.queue_rows(db, limit=60)
    return render_template('workflow_admin.html', definitions=defs, instances=instances,
                           stats=st, outbox=outbox, subscriptions=subscriptions,
                           triggers=triggers, queue=queue, user=_current_user())


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
                starter = wf.build_default_definition_for_event(ev['plugin'], event_key)
                prefill = {'def_key': starter['key'], 'name': starter['name'],
                           'category': '业务流程',
                           'description': ev.get('description') or ev.get('label')}
            except Exception as e:
                error = '自动生成失败：%s' % e
    if request.method == 'POST':
        try:
            def_key = (request.form.get('def_key') or '').strip()
            name = (request.form.get('name') or '').strip()
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
    wf.bind_trigger(db, def_key, plugin_id, event_key, mode=mode, actor=actor)


@workflow_bp.route('/admin/events')
def admin_events():
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
        payload = wf.build_default_definition_for_event(plugin_id, event_key)
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
        return redirect(url_for('workflow.index'))
    db = _db()
    item = wf.get_queue_item(db, queue_id)
    if not item:
        flash('待发起记录不存在', 'error')
        return redirect(url_for('workflow.index'))
    if user['role'] != 'platform_admin' and item['initiator_id'] and item['initiator_id'] != user['id']:
        flash('只能发起与自己相关的业务审批', 'error')
        return redirect(url_for('workflow.index'))
    try:
        inst = wf.start_from_queue(db, queue_id, user['id'], user['name'])
        flash('流程已发起，等待审批', 'success')
        return redirect(url_for('workflow.instance', instance_id=inst['id']))
    except Exception as e:
        flash('发起失败：%s' % e, 'error')
        return redirect(url_for('workflow.index'))


@workflow_bp.route('/queue/<int:queue_id>/ignore', methods=['POST'])
def queue_ignore(queue_id):
    user = _current_user()
    db = _db()
    try:
        wf.ignore_queue(db, queue_id, user['name'])
        flash('已忽略该待发起项', 'success')
    except Exception as e:
        flash('操作失败：%s' % e, 'error')
    return redirect(url_for('workflow.index'))
