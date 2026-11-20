# -*- coding: utf-8 -*-
"""平台级流程内核：以 SpiffWorkflow 为执行层，平台负责审批任务、待办、动作回调与审计。

设计边界：
- 流程定义存平台主库，实例钉住定义版本；
- 插件业务数据仍在各自库，平台只保存 record_id / business_key / 上下文快照；
- 审批任务由平台生成，审批通过后由 manualTask 动作节点回调插件；
- 动作失败时实例停在 error，可人工重试，不把“审批通过”直接当成“业务已生效”。
"""
import copy
import hashlib
import importlib
import json
import os
import re
import sqlite3
import uuid
from datetime import datetime, timedelta

from flask import current_app, has_request_context, session

try:
    from SpiffWorkflow.bpmn import BpmnWorkflow
    from SpiffWorkflow.bpmn.parser import BpmnParser
    from SpiffWorkflow.bpmn.serializer.workflow import BpmnWorkflowSerializer
    from SpiffWorkflow.task import TaskState
    SPIFF_AVAILABLE = True
except Exception as _spiff_err:  # pragma: no cover - 启动时给出可读提示
    BpmnWorkflow = None
    BpmnParser = None
    BpmnWorkflowSerializer = None
    TaskState = None
    SPIFF_AVAILABLE = False
    _SPIFF_ERROR = str(_spiff_err)

BPMN_NS = 'http://www.omg.org/spec/BPMN/20100524/MODEL'
XSI_NS = 'http://www.w3.org/2001/XMLSchema-instance'
SERVICE_NODE_TYPES = {'action'}
APPROVAL_NODE_TYPES = {'approval'}
NODE_TYPES = {'start', 'end', 'approval', 'action', 'exclusive', 'parallel'}


def _now():
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def _today():
    return datetime.now().strftime('%Y-%m-%d')


def _connect(db=None):
    if db is not None:
        return db
    con = sqlite3.connect(current_app.config['DATABASE'], timeout=5.0)
    con.row_factory = sqlite3.Row
    con.execute('PRAGMA busy_timeout = 5000')
    return con


WORKFLOW_SCHEMA = """
CREATE TABLE IF NOT EXISTS wf_definitions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    def_key TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    category TEXT DEFAULT '通用',
    description TEXT DEFAULT '',
    enabled INTEGER DEFAULT 1,
    current_version INTEGER DEFAULT 0,
    created_by TEXT DEFAULT '系统',
    created_at TEXT DEFAULT (datetime('now','localtime')),
    updated_at TEXT DEFAULT (datetime('now','localtime'))
);
CREATE TABLE IF NOT EXISTS wf_definition_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    def_key TEXT NOT NULL,
    version INTEGER NOT NULL,
    name TEXT NOT NULL,
    graph_json TEXT NOT NULL,
    bpmn_xml TEXT NOT NULL,
    checksum TEXT,
    published_by TEXT DEFAULT '系统',
    published_at TEXT DEFAULT (datetime('now','localtime')),
    UNIQUE(def_key, version)
);
CREATE INDEX IF NOT EXISTS idx_wf_def_versions ON wf_definition_versions(def_key, version);
CREATE TABLE IF NOT EXISTS wf_instances (
    id TEXT PRIMARY KEY,
    def_key TEXT NOT NULL,
    def_version INTEGER NOT NULL,
    plugin_id TEXT DEFAULT 'platform',
    record_type TEXT DEFAULT '',
    record_id TEXT DEFAULT '',
    business_key TEXT DEFAULT '',
    title TEXT DEFAULT '',
    initiator_id INTEGER,
    initiator_name TEXT DEFAULT '',
    status TEXT DEFAULT 'running',
    current_node TEXT DEFAULT '',
    decision TEXT DEFAULT '',
    context_json TEXT DEFAULT '{}',
    workflow_json TEXT DEFAULT '',
    last_error TEXT DEFAULT '',
    created_at TEXT DEFAULT (datetime('now','localtime')),
    updated_at TEXT DEFAULT (datetime('now','localtime')),
    ended_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_wf_instances_status ON wf_instances(status, updated_at);
CREATE INDEX IF NOT EXISTS idx_wf_instances_business ON wf_instances(plugin_id, record_type, record_id);
CREATE TABLE IF NOT EXISTS wf_tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    instance_id TEXT NOT NULL,
    node_id TEXT NOT NULL,
    spiff_task_id TEXT DEFAULT '',
    name TEXT DEFAULT '',
    assignee_id INTEGER,
    assignee_name TEXT DEFAULT '',
    candidate_role TEXT DEFAULT '',
    mode TEXT DEFAULT 'single',
    status TEXT DEFAULT 'pending',
    decision TEXT DEFAULT '',
    comment TEXT DEFAULT '',
    due_at TEXT DEFAULT '',
    created_at TEXT DEFAULT (datetime('now','localtime')),
    completed_at TEXT,
    FOREIGN KEY (instance_id) REFERENCES wf_instances(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_wf_tasks_assignee ON wf_tasks(assignee_id, status, due_at);
CREATE INDEX IF NOT EXISTS idx_wf_tasks_role ON wf_tasks(candidate_role, status, due_at);
CREATE INDEX IF NOT EXISTS idx_wf_tasks_instance ON wf_tasks(instance_id, status);
CREATE TABLE IF NOT EXISTS wf_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    instance_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    actor_id INTEGER,
    actor_name TEXT DEFAULT '',
    detail_json TEXT DEFAULT '{}',
    prev_hash TEXT DEFAULT '',
    hash TEXT DEFAULT '',
    created_at TEXT DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS idx_wf_events_instance ON wf_events(instance_id, seq);
CREATE TABLE IF NOT EXISTS wf_subscriptions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    plugin_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    action TEXT NOT NULL,
    filter_json TEXT DEFAULT '{}',
    enabled INTEGER DEFAULT 1,
    created_at TEXT DEFAULT (datetime('now','localtime')),
    UNIQUE(plugin_id, event_type, action)
);
CREATE TABLE IF NOT EXISTS wf_outbox (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    instance_id TEXT NOT NULL,
    event_id INTEGER,
    target_plugin TEXT NOT NULL,
    action TEXT NOT NULL,
    payload_json TEXT DEFAULT '{}',
    status TEXT DEFAULT 'pending',
    retry_count INTEGER DEFAULT 0,
    next_retry_at TEXT DEFAULT '',
    last_error TEXT DEFAULT '',
    created_at TEXT DEFAULT (datetime('now','localtime')),
    updated_at TEXT DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS idx_wf_outbox_status ON wf_outbox(status, next_retry_at);
"""


def init_tables(db=None):
    con = _connect(db)
    con.executescript(WORKFLOW_SCHEMA)
    con.commit()
    return con


# ============================================================
# 定义校验 / BPMN 生成
# ============================================================

def _clean_id(raw, prefix='n'):
    s = re.sub(r'[^A-Za-z0-9_\-]', '_', str(raw or '').strip())
    if not s or not re.match(r'^[A-Za-z_]', s):
        s = prefix + '_' + s
    return s[:80]


def _node_map(definition):
    return {n.get('id'): n for n in definition.get('nodes', []) if n.get('id')}


def validate_definition(definition):
    if not isinstance(definition, dict):
        raise ValueError('流程定义必须是 JSON 对象')
    key = str(definition.get('key') or '').strip()
    name = str(definition.get('name') or '').strip()
    if not key or not re.match(r'^[a-z0-9][a-z0-9_\-]{1,60}$', key):
        raise ValueError('def_key 只能使用小写字母、数字、下划线、短横线，长度 2-61')
    if not name:
        raise ValueError('流程名称不能为空')
    nodes = definition.get('nodes')
    if not isinstance(nodes, list) or len(nodes) < 2:
        raise ValueError('至少需要两个节点（开始 + 结束）')
    ids = set()
    for n in nodes:
        if not isinstance(n, dict):
            raise ValueError('节点必须是对象')
        nid = str(n.get('id') or '').strip()
        if not nid or nid in ids:
            raise ValueError('节点 id 不能为空且不能重复：%s' % nid)
        if not re.match(r'^[A-Za-z_][A-Za-z0-9_\-]{0,79}$', nid):
            raise ValueError('节点 id 只能使用字母、数字、下划线、短横线，且必须以字母或下划线开头：%s' % nid)
        ids.add(nid)
        ntype = n.get('type')
        if ntype not in NODE_TYPES:
            raise ValueError('节点 %s 的 type 不支持：%s' % (nid, ntype))
        if ntype == 'approval':
            rule = n.get('assignee') or {'type': 'initiator'}
            if not isinstance(rule, dict) or rule.get('type') not in ('initiator', 'user', 'role', 'record_field', 'project_participants'):
                raise ValueError('节点 %s 的审批人规则不合法' % nid)
            mode = n.get('mode') or 'single'
            if mode not in ('single', 'any', 'countersign'):
                raise ValueError('节点 %s 的审批模式不支持：%s' % (nid, mode))
        if ntype == 'action':
            if not n.get('action'):
                raise ValueError('动作节点 %s 缺少 action' % nid)
            if not n.get('plugin'):
                n['plugin'] = 'platform'
    starts = [n for n in nodes if n.get('type') == 'start']
    ends = [n for n in nodes if n.get('type') == 'end']
    if len(starts) != 1:
        raise ValueError('必须有且只有一个开始节点')
    if not ends:
        raise ValueError('至少需要一个结束节点')
    # next 目标存在性
    for n in nodes:
        nxt = n.get('next')
        if isinstance(nxt, dict):
            for v in nxt.values():
                if v and str(v) not in ids:
                    raise ValueError('节点 %s 的 next 指向不存在的节点：%s' % (n.get('id'), v))
        elif isinstance(nxt, str) and nxt and nxt not in ids:
            raise ValueError('节点 %s 的 next 指向不存在的节点：%s' % (n.get('id'), nxt))
        if n.get('type') != 'end' and not nxt:
            raise ValueError('节点 %s 缺少 next' % n.get('id'))
        if n.get('type') == 'approval':
            if not isinstance(nxt, dict):
                raise ValueError('审批节点 %s 的 next 必须是 approve/reject 映射' % n.get('id'))
            for need in ('approve', 'reject'):
                if need not in nxt:
                    raise ValueError('审批节点 %s 的 next 缺少 %s 分支' % (n.get('id'), need))
    # 简单可达性：从 start 出发
    reachable = set()
    queue = [starts[0].get('id')]
    while queue:
        cur = queue.pop(0)
        if cur in reachable:
            continue
        reachable.add(cur)
        node = next((x for x in nodes if x.get('id') == cur), None)
        if not node:
            continue
        nxt = node.get('next')
        targets = list(nxt.values()) if isinstance(nxt, dict) else ([nxt] if isinstance(nxt, str) else [])
        for t in targets:
            if t and t not in reachable:
                queue.append(t)
    missing = ids - reachable
    if missing:
        raise ValueError('存在从开始节点不可达的节点：%s' % ', '.join(sorted(missing)))
    return definition


def _build_bpmn(definition):
    import xml.etree.ElementTree as ET
    ET.register_namespace('bpmn', BPMN_NS)
    ET.register_namespace('xsi', XSI_NS)
    key = definition['key']
    version = int(definition.get('version') or 1)
    process_id = 'Process_%s_%s' % (_clean_id(key, 'p'), version)
    root = ET.Element('{%s}definitions' % BPMN_NS, {
        'id': 'Definitions_%s' % process_id,
        'targetNamespace': 'http://bpmn.io/schema/bpmn',
    })
    proc = ET.SubElement(root, '{%s}process' % BPMN_NS, {'id': process_id, 'isExecutable': 'true'})
    nodes = definition.get('nodes', [])
    nmap = _node_map(definition)

    def tag(name):
        return '{%s}%s' % (BPMN_NS, name)

    def add_flow(fid, src, tgt, condition=None):
        f = ET.SubElement(proc, tag('sequenceFlow'), {
            'id': _clean_id(fid, 'flow'), 'sourceRef': _clean_id(src), 'targetRef': _clean_id(tgt)
        })
        if condition:
            c = ET.SubElement(f, tag('conditionExpression'), {
                '{%s}type' % XSI_NS: 'bpmn:tFormalExpression'
            })
            c.text = condition
        return f

    # 节点
    for n in nodes:
        nid = _clean_id(n['id'])
        ntype = n['type']
        if ntype == 'start':
            ET.SubElement(proc, tag('startEvent'), {'id': nid, 'name': n.get('name') or '开始'})
        elif ntype == 'end':
            ET.SubElement(proc, tag('endEvent'), {'id': nid, 'name': n.get('name') or '结束'})
        elif ntype == 'approval':
            ET.SubElement(proc, tag('userTask'), {'id': nid, 'name': n.get('name') or '审批'})
        elif ntype == 'action':
            ET.SubElement(proc, tag('manualTask'), {'id': nid, 'name': n.get('name') or '平台动作'})
        elif ntype == 'exclusive':
            ET.SubElement(proc, tag('exclusiveGateway'), {'id': nid, 'name': n.get('name') or '条件分支'})
        elif ntype == 'parallel':
            ET.SubElement(proc, tag('parallelGateway'), {'id': nid, 'name': n.get('name') or '并行'})

    # 连线：显式 flows 优先；否则按 next 生成
    explicit = definition.get('flows')
    if isinstance(explicit, list) and explicit:
        for i, f in enumerate(explicit):
            add_flow(f.get('id') or 'flow_%s' % i, f.get('from'), f.get('to'), f.get('condition'))
    else:
        for n in nodes:
            nxt = n.get('next')
            if not nxt:
                continue
            if isinstance(nxt, str):
                add_flow('flow_%s_%s' % (n['id'], nxt), n['id'], nxt)
            elif isinstance(nxt, dict):
                # 审批/动作节点多出口：自动插入排他网关，用 decision 条件分流
                gw = _clean_id(n['id'] + '_gw')
                if gw not in [x.get('id') for x in nodes]:
                    ET.SubElement(proc, tag('exclusiveGateway'), {'id': gw, 'name': (n.get('name') or n['id']) + ' · 分支'})
                add_flow('flow_%s_%s' % (n['id'], gw), n['id'], gw)
                for decision, target in nxt.items():
                    if not target:
                        continue
                    cond = "decision == '%s'" % str(decision).replace("'", "\\'")
                    add_flow('flow_%s_%s_%s' % (n['id'], decision, target), gw, target, cond)
    xml = ET.tostring(root, encoding='utf-8', xml_declaration=True).decode('utf-8')
    return process_id, xml


# ============================================================
# 动作注册
# ============================================================
_ACTION_HANDLERS = {}
_ACTION_META = {}


def register_action(name, handler, label=None, plugin='platform'):
    """插件注册动作：handler(ctx) -> dict|None；ctx 含 db/instance/node/task/decision/context。"""
    if not name or not callable(handler):
        raise ValueError('动作名和 handler 必填')
    _ACTION_HANDLERS[name] = handler
    _ACTION_META[name] = {'label': label or name, 'plugin': plugin or 'platform'}


def registered_actions():
    return sorted(_ACTION_HANDLERS.keys())


def action_options():
    """供管理端/简易设计器下拉展示；元数据可被插件覆盖。"""
    return [
        {'key': key, 'label': _ACTION_META.get(key, {}).get('label') or key,
         'plugin': _ACTION_META.get(key, {}).get('plugin') or 'platform'}
        for key in sorted(_ACTION_HANDLERS.keys())
    ]


def build_simple_definition(def_key, name, steps, action=None, category='通用', description=''):
    """把业务人员填写的线性审批步骤编译成平台流程定义。"""
    def_key = str(def_key or '').strip()
    name = str(name or '').strip()
    if not re.match(r'^[a-z0-9][a-z0-9_\-]{1,60}$', def_key):
        raise ValueError('流程 Key 只能使用小写字母、数字、下划线、短横线')
    if not name:
        raise ValueError('流程名称不能为空')
    if not isinstance(steps, list) or not steps:
        raise ValueError('至少需要一个审批节点')

    nodes = []
    approvals = []
    for i, step in enumerate(steps):
        nid = 'approve_%d' % (i + 1)
        rule_type = step.get('assignee_type') or 'role'
        rule_value = str(step.get('assignee_value') or '').strip()
        if rule_type not in ('initiator', 'user', 'role', 'record_field', 'project_participants'):
            raise ValueError('审批人类型不合法')
        if rule_type in ('user', 'role', 'record_field') and not rule_value:
            raise ValueError('请填写审批人/角色/字段')
        assignee = {'type': rule_type}
        if rule_type in ('user', 'role', 'record_field'):
            assignee['value'] = rule_value
        mode = step.get('mode') or 'any'
        if mode not in ('single', 'any', 'countersign'):
            mode = 'any'
        approvals.append({'id': nid, 'name': str(step.get('name') or '审批').strip(),
                           'assignee': assignee, 'mode': mode})

    action = action or {}
    action_key = str(action.get('key') or '').strip()
    action_node = None
    if action_key:
        action_node = {
            'id': 'apply', 'type': 'action',
            'name': str(action.get('label') or '执行动作').strip(),
            'plugin': str(action.get('plugin') or 'platform'),
            'action': action_key, 'next': 'end_approve'
        }

    for i, node in enumerate(approvals):
        last = i == len(approvals) - 1
        if last and action_node:
            approve_next = 'apply'
        elif last:
            approve_next = 'end_approve'
        else:
            approve_next = approvals[i + 1]['id']
        nodes.append({
            'id': node['id'], 'type': 'approval', 'name': node['name'],
            'assignee': node['assignee'], 'mode': node['mode'],
            'next': {'approve': approve_next, 'reject': 'end_reject', 'return': 'end_return'}
        })
    if action_node:
        nodes.append(action_node)
    nodes.extend([
        {'id': 'end_approve', 'type': 'end', 'name': '已通过'},
        {'id': 'end_reject', 'type': 'end', 'name': '已驳回'},
        {'id': 'end_return', 'type': 'end', 'name': '已退回'},
    ])
    nodes.insert(0, {'id': 'start', 'type': 'start', 'name': '开始', 'next': approvals[0]['id']})
    return {'key': def_key, 'name': name, 'category': category, 'description': description, 'nodes': nodes}


def parse_simple_definition(definition):
    """从已有定义中提取线性审批步骤和动作，供简易设计器回显。"""
    approvals, action = [], None
    for node in (definition or {}).get('nodes', []):
        if node.get('type') == 'approval':
            rule = node.get('assignee') or {'type': 'initiator'}
            approvals.append({
                'name': node.get('name') or '审批',
                'assignee_type': rule.get('type') or 'role',
                'assignee_value': rule.get('value') or '',
                'mode': node.get('mode') or 'any'
            })
        elif node.get('type') == 'action':
            action = {'key': node.get('action') or '', 'label': node.get('name') or '',
                      'plugin': node.get('plugin') or 'platform'}
    return {'steps': approvals, 'action': action}


def _resolve_action_handler(action, plugin_id=''):
    handler = _ACTION_HANDLERS.get(action)
    if handler:
        return handler
    if not plugin_id:
        return None
    try:
        mod = importlib.import_module('plugins.%s.routes' % plugin_id)
    except Exception:
        return None
    fn = getattr(mod, 'workflow_action', None)
    if callable(fn):
        return fn
    short = str(action).split('.')[-1]
    fn = getattr(mod, short, None)
    return fn if callable(fn) else None


def _demo_record_decision(ctx):
    return {'ok': True, 'message': '审批结果已记录'}


def _wbs_project_apply(ctx):
    db = ctx['db']
    pid = int(ctx['instance'].get('record_id') or 0)
    status = '已通过' if ctx['decision'] == 'approve' else '已驳回'
    row = db.execute('SELECT id FROM wbs_projects WHERE id=?', (pid,)).fetchone()
    if not row:
        raise RuntimeError('WBS 项目不存在：%s' % pid)
    db.execute("UPDATE wbs_projects SET approval_status=?, approver=?, updated_at=datetime('now','localtime') WHERE id=?",
               (status, ctx.get('actor_name') or '平台流程', pid))
    db.commit()
    return {'ok': True, 'status': status}


def _wbs_task_apply(ctx):
    db = ctx['db']
    tid = int(ctx['instance'].get('record_id') or 0)
    status = '已通过' if ctx['decision'] == 'approve' else '已驳回'
    row = db.execute('SELECT id FROM wbs_tasks WHERE id=?', (tid,)).fetchone()
    if not row:
        raise RuntimeError('WBS 任务不存在：%s' % tid)
    db.execute("UPDATE wbs_tasks SET approval_status=? WHERE id=?", (status, tid))
    db.commit()
    return {'ok': True, 'status': status}


register_action('demo.record_decision', _demo_record_decision, label='记录审批结果', plugin='platform')
register_action('wbs.project.set_approval', _wbs_project_apply, label='写回 WBS 项目审批状态', plugin='wbs')
register_action('wbs.task.set_approval', _wbs_task_apply, label='写回 WBS 任务审批状态', plugin='wbs')


# ============================================================
# 定义存取
# ============================================================

def _hash_graph(graph_json):
    return hashlib.sha256(graph_json.encode('utf-8')).hexdigest()


def get_current_version(db, def_key):
    row = db.execute(
        'SELECT * FROM wf_definitions WHERE def_key=? AND enabled=1', (def_key,)).fetchone()
    if not row:
        return None
    ver = db.execute(
        'SELECT * FROM wf_definition_versions WHERE def_key=? AND version=?',
        (def_key, row['current_version'])).fetchone()
    return row, ver


def save_definition(db, def_key, name, definition, actor='系统', category='通用', description=''):
    definition = copy.deepcopy(definition)
    definition['key'] = def_key
    definition['name'] = name
    validate_definition(definition)
    row = db.execute('SELECT * FROM wf_definitions WHERE def_key=?', (def_key,)).fetchone()
    next_ver = int(row['current_version'] or 0) + 1 if row else 1
    definition['version'] = next_ver
    process_id, xml = _build_bpmn(definition)
    graph_json = json.dumps(definition, ensure_ascii=False, sort_keys=True)
    checksum = _hash_graph(graph_json)
    db.execute(
        'INSERT INTO wf_definition_versions (def_key, version, name, graph_json, bpmn_xml, checksum, published_by) '
        'VALUES (?,?,?,?,?,?,?)',
        (def_key, next_ver, name, graph_json, xml, checksum, actor))
    if row:
        db.execute(
            'UPDATE wf_definitions SET name=?, category=?, description=?, current_version=?, enabled=1, updated_at=? WHERE def_key=?',
            (name, category, description, next_ver, _now(), def_key))
    else:
        db.execute(
            'INSERT INTO wf_definitions (def_key, name, category, description, current_version, created_by) '
            'VALUES (?,?,?,?,?,?)',
            (def_key, name, category, description, next_ver, actor))
    db.commit()
    return next_ver


def seed_defaults(db):
    if not SPIFF_AVAILABLE:
        return
    existing = {r['def_key'] for r in db.execute('SELECT def_key FROM wf_definitions').fetchall()}
    defaults = [
        ('generic_approval', '通用业务审批', '通用', '单节点审批，审批后由平台记录结果，适合验证审批入口', {
            'key': 'generic_approval', 'name': '通用业务审批',
            'nodes': [
                {'id': 'start', 'type': 'start', 'name': '开始', 'next': 'approve'},
                {'id': 'approve', 'type': 'approval', 'name': '负责人审批', 'assignee': {'type': 'role', 'value': 'admin'},
                 'mode': 'any', 'next': {'approve': 'apply', 'reject': 'end_reject', 'return': 'end_return'}},
                {'id': 'apply', 'type': 'action', 'name': '记录审批结果', 'plugin': 'platform', 'action': 'demo.record_decision', 'next': 'end_approve'},
                {'id': 'end_approve', 'type': 'end', 'name': '已通过'},
                {'id': 'end_reject', 'type': 'end', 'name': '已驳回'},
                {'id': 'end_return', 'type': 'end', 'name': '已退回'},
            ],
        }),
        ('wbs_project_approval', 'WBS 项目立项审批', 'WBS', '管理员会签/或签项目立项，通过后写回 WBS 项目审批状态', {
            'key': 'wbs_project_approval', 'name': 'WBS 项目立项审批',
            'nodes': [
                {'id': 'start', 'type': 'start', 'name': '开始', 'next': 'approve'},
                {'id': 'approve', 'type': 'approval', 'name': '项目审批', 'assignee': {'type': 'role', 'value': 'admin'},
                 'mode': 'any', 'next': {'approve': 'apply', 'reject': 'end_reject', 'return': 'end_return'}},
                {'id': 'apply', 'type': 'action', 'name': '写回项目审批状态', 'plugin': 'wbs', 'action': 'wbs.project.set_approval', 'next': 'end_approve'},
                {'id': 'end_approve', 'type': 'end', 'name': '已通过'},
                {'id': 'end_reject', 'type': 'end', 'name': '已驳回'},
                {'id': 'end_return', 'type': 'end', 'name': '已退回'},
            ],
        }),
        ('wbs_task_approval', 'WBS 任务审批', 'WBS', '任务负责人发起、管理员审批，通过后写回任务审批状态', {
            'key': 'wbs_task_approval', 'name': 'WBS 任务审批',
            'nodes': [
                {'id': 'start', 'type': 'start', 'name': '开始', 'next': 'approve'},
                {'id': 'approve', 'type': 'approval', 'name': '任务审批', 'assignee': {'type': 'role', 'value': 'admin'},
                 'mode': 'any', 'next': {'approve': 'apply', 'reject': 'end_reject', 'return': 'end_return'}},
                {'id': 'apply', 'type': 'action', 'name': '写回任务审批状态', 'plugin': 'wbs', 'action': 'wbs.task.set_approval', 'next': 'end_approve'},
                {'id': 'end_approve', 'type': 'end', 'name': '已通过'},
                {'id': 'end_reject', 'type': 'end', 'name': '已驳回'},
                {'id': 'end_return', 'type': 'end', 'name': '已退回'},
            ],
        }),
        ('demo_countersign', '演示 · 管理员会签', '演示', '两名管理员会签，全部通过后才执行动作', {
            'key': 'demo_countersign', 'name': '演示 · 管理员会签',
            'nodes': [
                {'id': 'start', 'type': 'start', 'name': '开始', 'next': 'countersign'},
                {'id': 'countersign', 'type': 'approval', 'name': '管理员会签', 'assignee': {'type': 'role', 'value': 'admin'},
                 'mode': 'countersign', 'next': {'approve': 'apply', 'reject': 'end_reject', 'return': 'end_return'}},
                {'id': 'apply', 'type': 'action', 'name': '记录会签结果', 'plugin': 'platform', 'action': 'demo.record_decision', 'next': 'end_approve'},
                {'id': 'end_approve', 'type': 'end', 'name': '会签通过'},
                {'id': 'end_reject', 'type': 'end', 'name': '会签驳回'},
                {'id': 'end_return', 'type': 'end', 'name': '已退回'},
            ],
        }),
    ]
    for key, name, category, desc, definition in defaults:
        if key in existing:
            continue
        try:
            save_definition(db, key, name, definition, actor='系统', category=category, description=desc)
        except Exception as e:
            print('[流程初始化] %s 失败：%s' % (key, e))


# ============================================================
# 实例与任务
# ============================================================

def _serializer():
    if not SPIFF_AVAILABLE:
        raise RuntimeError('SpiffWorkflow 未安装：%s' % _SPIFF_ERROR)
    return BpmnWorkflowSerializer()


def _workflow_from_version(ver, context, instance_id):
    process_id, xml = _build_bpmn(json.loads(ver['graph_json']))
    parser = BpmnParser()
    parser.add_bpmn_str(xml.encode('utf-8'))
    spec = parser.get_spec(process_id)
    wf = BpmnWorkflow(spec)
    wf.data.update({
        'instance_id': instance_id,
        'business_key': context.get('business_key', ''),
        'context': context,
        'decision': '',
    })
    return wf


def _save_workflow(db, instance_id, wf):
    payload = _serializer().serialize_json(wf)
    db.execute('UPDATE wf_instances SET workflow_json=?, updated_at=? WHERE id=?',
               (payload, _now(), instance_id))


def _load_workflow(row):
    return _serializer().deserialize_json(row['workflow_json'])


def _event(db, instance_id, event_type, actor_id=None, actor_name='', detail=None):
    detail_json = json.dumps(detail or {}, ensure_ascii=False, sort_keys=True)
    prev = db.execute('SELECT hash FROM wf_events WHERE instance_id=? ORDER BY seq DESC LIMIT 1',
                      (instance_id,)).fetchone()
    prev_hash = prev['hash'] if prev and prev['hash'] else ''
    seq = (db.execute('SELECT COALESCE(MAX(seq),0)+1 AS s FROM wf_events WHERE instance_id=?',
                      (instance_id,)).fetchone()['s'])
    raw = '%s|%s|%s|%s|%s|%s' % (instance_id, seq, event_type, actor_id or '', detail_json, prev_hash)
    h = hashlib.sha256(raw.encode('utf-8')).hexdigest()
    db.execute(
        'INSERT INTO wf_events (instance_id, seq, event_type, actor_id, actor_name, detail_json, prev_hash, hash) '
        'VALUES (?,?,?,?,?,?,?,?)',
        (instance_id, seq, event_type, actor_id, actor_name, detail_json, prev_hash, h))
    return h


def _user_by_username_or_name(db, value):
    v = str(value or '').strip()
    if not v:
        return None
    return db.execute(
        "SELECT * FROM users WHERE (username=? OR display_name=?) AND is_active=1 AND COALESCE(deleted,0)=0 LIMIT 1",
        (v, v)).fetchone()


def _resolve_assignees(db, rule, context, initiator_id):
    rule = rule or {'type': 'initiator'}
    typ = rule.get('type')
    if typ == 'initiator':
        u = db.execute('SELECT * FROM users WHERE id=?', (initiator_id,)).fetchone()
        return [u] if u else []
    if typ == 'user':
        vals = rule.get('value')
        if isinstance(vals, str):
            vals = [vals]
        users = []
        for v in vals or []:
            u = _user_by_username_or_name(db, v)
            if u:
                users.append(u)
        return users
    if typ == 'role':
        role = str(rule.get('value') or 'admin')
        if role == 'admin':
            # 平台管理员是超管角色；按“管理员”配置时应同时命中 admin 与 platform_admin
            return db.execute(
                "SELECT * FROM users WHERE role IN ('admin','platform_admin') AND is_active=1 AND COALESCE(deleted,0)=0 ORDER BY id").fetchall()
        return db.execute(
            "SELECT * FROM users WHERE role=? AND is_active=1 AND COALESCE(deleted,0)=0 ORDER BY id",
            (role,)).fetchall()
    if typ == 'record_field':
        field = str(rule.get('field') or 'owner')
        raw = context.get(field) or ''
        if isinstance(raw, (list, tuple)):
            vals = [str(x) for x in raw]
        else:
            vals = re.split(r'[,，;；\s]+', str(raw))
        users = []
        for v in [x.strip() for x in vals if x and x.strip()]:
            u = _user_by_username_or_name(db, v)
            if u and u['id'] not in [x['id'] for x in users]:
                users.append(u)
        return users
    if typ == 'project_participants':
        field = str(rule.get('field') or 'participants')
        raw = context.get(field) or ''
        vals = raw if isinstance(raw, (list, tuple)) else re.split(r'[,，;；\s]+', str(raw))
        users = []
        for v in [str(x).strip() for x in vals if str(x).strip()]:
            u = _user_by_username_or_name(db, v)
            if u and u['id'] not in [x['id'] for x in users]:
                users.append(u)
        return users
    return []


def _fallback_admin(db):
    return db.execute(
        "SELECT * FROM users WHERE role IN ('platform_admin','admin') AND is_active=1 AND COALESCE(deleted,0)=0 ORDER BY role DESC, id LIMIT 1"
    ).fetchone()


def _upsert_approval_tasks(db, inst, node, spiff_task_id, context):
    existing = db.execute(
        'SELECT COUNT(*) c FROM wf_tasks WHERE instance_id=? AND spiff_task_id=? AND node_id=?',
        (inst['id'], spiff_task_id, node['id'])).fetchone()['c']
    if existing:
        return False
    users = _resolve_assignees(db, node.get('assignee'), context, inst['initiator_id'])
    if not users:
        fallback = _fallback_admin(db)
        users = [fallback] if fallback else []
    mode = node.get('mode') or 'single'
    due_hours = int(node.get('due_hours') or 72)
    due_at = (datetime.now() + timedelta(hours=due_hours)).strftime('%Y-%m-%d %H:%M:%S')
    if mode == 'single':
        users = users[:1]
    if not users:
        raise RuntimeError('审批节点 %s 没有可用的审批人' % node.get('name'))
    for u in users:
        db.execute(
            'INSERT INTO wf_tasks (instance_id, node_id, spiff_task_id, name, assignee_id, assignee_name, candidate_role, mode, due_at) '
            'VALUES (?,?,?,?,?,?,?,?,?)',
            (inst['id'], node['id'], spiff_task_id, node.get('name') or '审批',
             u['id'], u['display_name'] or u['username'], node.get('candidate_role') or '', mode, due_at))
    return True


def _render_payload(payload, context, decision):
    if not isinstance(payload, dict):
        return payload or {}
    text = json.dumps(payload, ensure_ascii=False)
    text = text.replace('{{decision}}', str(decision))
    for k, v in (context or {}).items():
        text = text.replace('{{context.%s}}' % k, str(v))
    try:
        return json.loads(text)
    except Exception:
        return payload


def _execute_action(db, inst, node, wf_task, decision, context):
    action = node.get('action')
    handler = _resolve_action_handler(action, node.get('plugin') or '')
    if not handler:
        raise RuntimeError('动作未注册：%s（插件 %s 也未实现 workflow_action）' % (action, node.get('plugin') or '-'))
    ctx = {
        'db': db,
        'instance': dict(inst),
        'node': node,
        'task': wf_task,
        'decision': decision,
        'context': context,
        'actor_name': wf_task.data.get('last_actor') if wf_task is not None else '',
        'payload': _render_payload(node.get('payload'), context, decision),
    }
    result = handler(ctx)
    return result or {'ok': True}


def _definition_node(definition, node_id):
    return _node_map(definition).get(node_id)


def _ready_tasks(wf):
    return [t for t in wf.get_tasks() if t.state == TaskState.READY]


def sync_runtime(db, inst, wf, definition):
    """推进引擎：执行就绪动作节点、创建审批任务；返回是否有状态变化。"""
    changed = True
    loops = 0
    while changed and loops < 100:
        loops += 1
        changed = False
        for t in _ready_tasks(wf):
            node = _definition_node(definition, t.task_spec.bpmn_id)
            if not node:
                continue
            if node['type'] == 'approval':
                if _upsert_approval_tasks(db, inst, node, str(t.id), json.loads(inst['context_json'] or '{}')):
                    changed = True
            elif node['type'] == 'action':
                # 先把“审批已完成、动作待执行”的状态落盘，动作失败后可从 error 重试
                _save_workflow(db, inst['id'], wf)
                result = _execute_action(db, inst, node, t, wf.data.get('decision', ''), json.loads(inst['context_json'] or '{}'))
                _event(db, inst['id'], 'action_executed', inst['initiator_id'], inst['initiator_name'], {
                    'node': node['id'], 'action': node.get('action'), 'result': result
                })
                t.complete()
                changed = True
        if changed:
            wf.do_engine_steps()
    wf.do_engine_steps()
    _save_workflow(db, inst['id'], wf)
    _sync_current_node(db, inst['id'], wf, definition)
    _finalize_if_complete(db, inst, wf)


def _sync_current_node(db, instance_id, wf, definition):
    ready = _ready_tasks(wf)
    names = []
    for t in ready:
        node = _definition_node(definition, t.task_spec.bpmn_id)
        if node:
            names.append(node.get('name') or node['id'])
    db.execute('UPDATE wf_instances SET current_node=?, updated_at=? WHERE id=?',
               (' / '.join(names), _now(), instance_id))


def _finalize_if_complete(db, inst, wf):
    if not wf.is_completed():
        return
    decision = wf.data.get('decision', '')
    status = 'returned' if decision == 'return' else ('rejected' if decision == 'reject' else 'completed')
    db.execute('UPDATE wf_instances SET status=?, decision=?, ended_at=?, updated_at=? WHERE id=?',
               (status, decision, _now(), _now(), inst['id']))
    _event(db, inst['id'], 'instance_completed', inst['initiator_id'], inst['initiator_name'],
           {'status': status, 'decision': decision})
    _queue_event(db, inst['id'], 'workflow.%s' % status, {'instance_id': inst['id'], 'decision': decision})


def _queue_event(db, instance_id, event_type, payload):
    subs = db.execute('SELECT * FROM wf_subscriptions WHERE event_type=? AND enabled=1', (event_type,)).fetchall()
    event_id = db.execute('SELECT MAX(id) id FROM wf_events WHERE instance_id=?', (instance_id,)).fetchone()['id']
    for s in subs:
        db.execute(
            'INSERT INTO wf_outbox (instance_id, event_id, target_plugin, action, payload_json) VALUES (?,?,?,?,?)',
            (instance_id, event_id, s['plugin_id'], s['action'], json.dumps(payload, ensure_ascii=False)))


def start_instance(db, def_key, plugin_id='platform', record_type='', record_id='', business_key='',
                   title='', context=None, initiator_id=None, initiator_name='', version=None):
    if not SPIFF_AVAILABLE:
        raise RuntimeError('SpiffWorkflow 未安装：%s' % _SPIFF_ERROR)
    init_tables(db)
    seed_defaults(db)
    row, ver = get_current_version(db, def_key)
    if not row:
        raise RuntimeError('流程不存在：%s' % def_key)
    if version:
        ver = db.execute('SELECT * FROM wf_definition_versions WHERE def_key=? AND version=?',
                         (def_key, int(version))).fetchone()
    if not ver:
        raise RuntimeError('流程版本不存在：%s v%s' % (def_key, version))
    definition = json.loads(ver['graph_json'])
    context = context or {}
    context.setdefault('business_key', business_key)
    context.setdefault('title', title)
    if initiator_id is None and has_request_context():
        initiator_id = session.get('user_id')
        initiator_name = initiator_name or session.get('user_name', '')
    if not initiator_id:
        raise RuntimeError('缺少发起人')
    instance_id = uuid.uuid4().hex
    db.execute(
        'INSERT INTO wf_instances (id, def_key, def_version, plugin_id, record_type, record_id, business_key, title, '
        'initiator_id, initiator_name, status, context_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
        (instance_id, def_key, ver['version'], plugin_id, str(record_type or ''), str(record_id or ''),
         business_key or '', title or business_key or def_key, initiator_id, initiator_name, 'running',
         json.dumps(context, ensure_ascii=False)))
    inst = db.execute('SELECT * FROM wf_instances WHERE id=?', (instance_id,)).fetchone()
    _event(db, instance_id, 'instance_started', initiator_id, initiator_name, {
        'def_key': def_key, 'version': ver['version'], 'business_key': business_key
    })
    db.commit()
    try:
        wf = _workflow_from_version(ver, context, instance_id)
        wf.do_engine_steps()
        _save_workflow(db, instance_id, wf)
        sync_runtime(db, inst, wf, definition)
        db.commit()
    except Exception as e:
        db.execute("UPDATE wf_instances SET status='error', last_error=?, updated_at=? WHERE id=?",
                   (str(e)[:2000], _now(), instance_id))
        _event(db, instance_id, 'instance_error', initiator_id, initiator_name, {'error': str(e)})
        db.commit()
        raise
    return db.execute('SELECT * FROM wf_instances WHERE id=?', (instance_id,)).fetchone()


def _task_allowed(task, user_id, role):
    # 平台管理员是最终兜底审批人，可处理任意待审批任务（但仍不能重复处理已完成任务）
    if role == 'platform_admin':
        return True
    if task['assignee_id'] and int(task['assignee_id']) == int(user_id):
        return True
    if task['candidate_role'] and task['candidate_role'] == role:
        return True
    return False


def approve_task(db, task_id, user_id, user_name, role, decision, comment=''):
    init_tables(db)
    task = db.execute('SELECT * FROM wf_tasks WHERE id=?', (task_id,)).fetchone()
    if not task:
        raise RuntimeError('审批任务不存在')
    if task['status'] != 'pending':
        raise RuntimeError('该任务已处理：%s' % task['status'])
    if not _task_allowed(task, user_id, role):
        raise RuntimeError('无权处理该审批任务')
    if decision not in ('approve', 'reject', 'return'):
        raise RuntimeError('不支持的审批动作')
    inst = db.execute('SELECT * FROM wf_instances WHERE id=?', (task['instance_id'],)).fetchone()
    if not inst or inst['status'] in ('completed', 'rejected', 'cancelled'):
        raise RuntimeError('流程已结束，不能继续审批')
    ver = db.execute('SELECT * FROM wf_definition_versions WHERE def_key=? AND version=?',
                     (inst['def_key'], inst['def_version'])).fetchone()
    definition = json.loads(ver['graph_json'])
    node = _definition_node(definition, task['node_id'])
    wf = _load_workflow(inst)

    db.execute('UPDATE wf_tasks SET status=?, decision=?, comment=?, completed_at=? WHERE id=?',
               ({'approve': 'approved', 'reject': 'rejected', 'return': 'returned'}[decision], decision,
                comment or '', _now(), task_id))
    _event(db, inst['id'], 'task_decided', user_id, user_name, {
        'task_id': task_id, 'node': task['node_id'], 'decision': decision, 'comment': comment or ''
    })

    spiff_task = next((t for t in wf.get_tasks() if str(t.id) == task['spiff_task_id']), None)
    if not spiff_task:
        # 有可能已经被其它会签任务完成
        db.commit()
        return inst

    mode = task['mode'] or 'single'
    complete_it = True
    if mode == 'countersign' and decision == 'approve':
        siblings = db.execute(
            'SELECT * FROM wf_tasks WHERE instance_id=? AND spiff_task_id=? AND id<>?',
            (inst['id'], task['spiff_task_id'], task_id)).fetchall()
        complete_it = all(s['status'] == 'approved' for s in siblings)
    if mode == 'countersign' and decision == 'reject':
        db.execute("UPDATE wf_tasks SET status='cancelled', completed_at=? WHERE instance_id=? AND spiff_task_id=? AND status='pending'",
                   (_now(), inst['id'], task['spiff_task_id']))
    if mode == 'any':
        db.execute("UPDATE wf_tasks SET status='cancelled', completed_at=? WHERE instance_id=? AND spiff_task_id=? AND status='pending' AND id<>?",
                   (_now(), inst['id'], task['spiff_task_id'], task_id))
    if complete_it:
        choice = 'approve' if decision == 'approve' else ('reject' if decision == 'reject' else 'return')
        wf.data['decision'] = choice
        wf.data['last_actor'] = user_name
        spiff_task.data['decision'] = choice
        spiff_task.data['last_actor'] = user_name
        spiff_task.complete()
        try:
            wf.do_engine_steps()
            _save_workflow(db, inst['id'], wf)
            sync_runtime(db, inst, wf, definition)
        except Exception as e:
            db.execute("UPDATE wf_instances SET status='error', last_error=?, updated_at=? WHERE id=?",
                       (str(e)[:2000], _now(), inst['id']))
            _event(db, inst['id'], 'instance_error', user_id, user_name, {'error': str(e)})
            db.commit()
            raise
    _save_workflow(db, inst['id'], wf)
    db.commit()
    return db.execute('SELECT * FROM wf_instances WHERE id=?', (inst['id'],)).fetchone()


def cancel_instance(db, instance_id, actor_id, actor_name, reason=''):
    inst = db.execute('SELECT * FROM wf_instances WHERE id=?', (instance_id,)).fetchone()
    if not inst:
        raise RuntimeError('流程不存在')
    if inst['status'] in ('completed', 'rejected', 'cancelled'):
        return inst
    db.execute("UPDATE wf_instances SET status='cancelled', ended_at=?, updated_at=? WHERE id=?",
               (_now(), _now(), instance_id))
    db.execute("UPDATE wf_tasks SET status='cancelled', completed_at=? WHERE instance_id=? AND status='pending'",
               (_now(), instance_id))
    _event(db, instance_id, 'instance_cancelled', actor_id, actor_name, {'reason': reason})
    db.commit()
    return db.execute('SELECT * FROM wf_instances WHERE id=?', (instance_id,)).fetchone()


def retry_instance(db, instance_id, actor_id, actor_name):
    inst = db.execute('SELECT * FROM wf_instances WHERE id=?', (instance_id,)).fetchone()
    if not inst:
        raise RuntimeError('流程不存在')
    if inst['status'] != 'error':
        raise RuntimeError('只有 error 状态可以重试')
    ver = db.execute('SELECT * FROM wf_definition_versions WHERE def_key=? AND version=?',
                     (inst['def_key'], inst['def_version'])).fetchone()
    definition = json.loads(ver['graph_json'])
    wf = _load_workflow(inst)
    db.execute("UPDATE wf_instances SET status='running', last_error='', updated_at=? WHERE id=?",
               (_now(), instance_id))
    _event(db, instance_id, 'instance_retry', actor_id, actor_name, {})
    db.commit()
    try:
        sync_runtime(db, inst, wf, definition)
        db.commit()
    except Exception as e:
        db.execute("UPDATE wf_instances SET status='error', last_error=?, updated_at=? WHERE id=?",
                   (str(e)[:2000], _now(), instance_id))
        db.commit()
        raise
    return db.execute('SELECT * FROM wf_instances WHERE id=?', (instance_id,)).fetchone()


# ============================================================
# 查询 / 待办
# ============================================================

def list_definitions(db):
    return db.execute(
        'SELECT d.*, (SELECT COUNT(*) FROM wf_instances i WHERE i.def_key=d.def_key) AS instance_count '
        'FROM wf_definitions d ORDER BY d.updated_at DESC').fetchall()


def list_versions(db, def_key):
    return db.execute(
        'SELECT version, name, checksum, published_by, published_at FROM wf_definition_versions '
        'WHERE def_key=? ORDER BY version DESC', (def_key,)).fetchall()


def list_instances(db, status='', plugin_id='', limit=100):
    sql = 'SELECT * FROM wf_instances WHERE 1=1'
    params = []
    if status:
        sql += ' AND status=?'
        params.append(status)
    if plugin_id:
        sql += ' AND plugin_id=?'
        params.append(plugin_id)
    sql += ' ORDER BY created_at DESC LIMIT ?'
    params.append(int(limit))
    return db.execute(sql, params).fetchall()


def my_instances(db, user_id, status='', limit=20):
    sql = 'SELECT * FROM wf_instances WHERE initiator_id=?'
    params = [user_id]
    if status:
        sql += ' AND status=?'
        params.append(status)
    sql += ' ORDER BY created_at DESC LIMIT ?'
    params.append(int(limit))
    return db.execute(sql, params).fetchall()


def open_instances(db, limit=20):
    return db.execute(
        "SELECT * FROM wf_instances WHERE status IN ('running','waiting','error') "
        "ORDER BY CASE status WHEN 'error' THEN 0 ELSE 1 END, created_at DESC LIMIT ?",
        (limit,)).fetchall()


def get_instance(db, instance_id):
    return db.execute('SELECT * FROM wf_instances WHERE id=?', (instance_id,)).fetchone()


def get_tasks(db, instance_id):
    return db.execute('SELECT * FROM wf_tasks WHERE instance_id=? ORDER BY id', (instance_id,)).fetchall()


def get_events(db, instance_id):
    return db.execute('SELECT * FROM wf_events WHERE instance_id=? ORDER BY seq', (instance_id,)).fetchall()


def my_tasks(db, user_id, role):
    return db.execute(
        "SELECT t.*, i.def_key, i.title AS instance_title, i.business_key, i.plugin_id, i.initiator_name, i.status AS instance_status "
        "FROM wf_tasks t JOIN wf_instances i ON i.id=t.instance_id "
        "WHERE t.status='pending' AND i.status IN ('running','waiting') "
        "AND ((t.assignee_id IS NOT NULL AND t.assignee_id=?) OR (t.candidate_role<>'' AND t.candidate_role=?)) "
        "ORDER BY CASE WHEN t.due_at='' THEN 1 ELSE 0 END, t.due_at, t.id", (user_id, role)).fetchall()


def _user_for_name(db, username):
    return db.execute(
        "SELECT * FROM users WHERE (username=? OR display_name=?) AND is_active=1 AND COALESCE(deleted,0)=0 LIMIT 1",
        (username, username)).fetchone()


def collect_my_todos(db, username):
    """供工作台 collect_my_todos 聚合使用，返回与 WBS 一致的待办结构。"""
    u = _user_for_name(db, username)
    if not u:
        return []
    rows = my_tasks(db, u['id'], u['role'])
    items = []
    for r in rows:
        due = (r['due_at'] or '')[:10]
        days = None
        if due:
            try:
                days = (datetime.strptime(due, '%Y-%m-%d').date() - datetime.now().date()).days
            except Exception:
                days = None
        if days is None:
            level = 'none'
        elif days < 0:
            level = 'overdue'
        elif days <= 1:
            level = 'soon'
        elif days <= 3:
            level = 'near'
        else:
            level = 'later'
        items.append({
            'title': '%s：%s' % (r['instance_title'] or r['def_key'], r['name']),
            'url': '/workflow/instance/%s' % r['instance_id'],
            'due': due,
            'days_left': days if days is not None else 9999,
            'level': level,
            'status': '待审批',
            'wbs_code': '',
            'project': r['instance_title'] or r['def_key'],
            'plugin_id': r['plugin_id'] or 'platform',
            'instance_id': r['instance_id'],
        })
    return items


def list_subscriptions(db):
    return db.execute('SELECT * FROM wf_subscriptions ORDER BY id DESC').fetchall()


def add_subscription(db, plugin_id, event_type, action):
    plugin_id = (plugin_id or '').strip()
    event_type = (event_type or '').strip()
    action = (action or '').strip()
    if not plugin_id or not event_type or not action:
        raise ValueError('插件、事件、动作都不能为空')
    if not re.match(r'^[a-z0-9][a-z0-9_\-]*$', plugin_id):
        raise ValueError('插件 ID 只能使用小写字母、数字、下划线、短横线')
    db.execute(
        'INSERT INTO wf_subscriptions (plugin_id, event_type, action) VALUES (?,?,?) '
        'ON CONFLICT(plugin_id,event_type,action) DO UPDATE SET enabled=1',
        (plugin_id, event_type, action))
    db.commit()


def delete_subscription(db, sub_id):
    db.execute('DELETE FROM wf_subscriptions WHERE id=?', (int(sub_id),))
    db.commit()


def outbox_rows(db, status=''):
    sql = 'SELECT * FROM wf_outbox WHERE 1=1'
    params = []
    if status:
        sql += ' AND status=?'
        params.append(status)
    sql += ' ORDER BY id DESC LIMIT 200'
    return db.execute(sql, params).fetchall()


def dispatch_outbox(db, limit=20):
    rows = db.execute(
        "SELECT * FROM wf_outbox WHERE status IN ('pending','failed') AND (next_retry_at='' OR next_retry_at<=?) "
        "ORDER BY id LIMIT ?", (_now(), int(limit))).fetchall()
    done = 0
    for r in rows:
        handler = _resolve_action_handler(r['action'], r['target_plugin'] or '')
        if not handler:
            db.execute("UPDATE wf_outbox SET status='dead', last_error=?, updated_at=? WHERE id=?",
                       ('动作未注册：%s' % r['action'], _now(), r['id']))
            continue
        try:
            db.execute("UPDATE wf_outbox SET status='processing', updated_at=? WHERE id=?", (_now(), r['id']))
            db.commit()
            inst = get_instance(db, r['instance_id'])
            handler({'db': db, 'instance': dict(inst) if inst else {}, 'node': {},
                     'task': None, 'decision': (inst['decision'] if inst else ''), 'context': {},
                     'payload': json.loads(r['payload_json'] or '{}')})
            db.execute("UPDATE wf_outbox SET status='success', updated_at=? WHERE id=?", (_now(), r['id']))
            done += 1
        except Exception as e:
            retry = int(r['retry_count'] or 0) + 1
            status = 'dead' if retry >= 5 else 'failed'
            next_at = (datetime.now() + timedelta(minutes=min(60, 2 ** retry))).strftime('%Y-%m-%d %H:%M:%S')
            db.execute('UPDATE wf_outbox SET status=?, retry_count=?, next_retry_at=?, last_error=?, updated_at=? WHERE id=?',
                       (status, retry, next_at, str(e)[:1000], _now(), r['id']))
    db.commit()
    return done


def stats(db):
    return {
        'definitions': db.execute('SELECT COUNT(*) c FROM wf_definitions').fetchone()['c'],
        'running': db.execute("SELECT COUNT(*) c FROM wf_instances WHERE status IN ('running','waiting')").fetchone()['c'],
        'error': db.execute("SELECT COUNT(*) c FROM wf_instances WHERE status='error'").fetchone()['c'],
        'pending_tasks': db.execute("SELECT COUNT(*) c FROM wf_tasks WHERE status='pending'").fetchone()['c'],
        'dead_outbox': db.execute("SELECT COUNT(*) c FROM wf_outbox WHERE status='dead'").fetchone()['c'],
    }
