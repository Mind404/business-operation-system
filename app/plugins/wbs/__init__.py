# -*- coding: utf-8 -*-
"""计划管理插件（核心）：WBS项目拆解、任务管理、进度跟踪、交付物"""
from .routes import blueprint

manifest = {
'source': 'builtin',  # 平台自带
        'id': 'wbs',
    'name': '计划管理',
    'icon': '📊',
    'group': '业务插件',
    'core': False,
    'description': 'WBS计划、里程碑、审批、进度、交付物、进展流水',
    'endpoint': 'wbs.wbs_projects',
    'tables': ['wbs_projects', 'wbs_tasks', 'wbs_task_notes', 'wbs_task_attachments'],
    'menu': [
        {'name': 'WBS计划', 'endpoint': 'wbs.wbs_projects', 'icon': '📊'},
    ],
    'workflow': {
        'actions': [
            {'key': 'wbs.project.set_approval', 'label': '写回 WBS 项目审批状态'},
            {'key': 'wbs.task.set_approval', 'label': '写回 WBS 任务审批状态'},
        ],
        'templates': ['wbs_project_approval', 'wbs_task_approval'],
        'events': [
            'wbs.project.created',
            'wbs.project.approval',
            'wbs.task.created',
            'wbs.task.approval',
            'wbs.task.progress',
            'wbs.task.completed',
        ],
    },
}

# v13.3：把 WBS 的关键业务状态变更登记为流程引擎可绑定的节点
try:
    from workflow_engine import register_event
    _WF_EVENTS = [
        dict(plugin_id='wbs', key='wbs.project.created', label='项目创建',
             description='新建 WBS 项目并提交后触发，可用于立项确认', icon='📁',
             record_type='project',
             context_fields=[{'key': 'name', 'label': '项目名称'},
                             {'key': 'owner', 'label': '项目负责人'},
                             {'key': 'project_type', 'label': '项目类型'},
                             {'key': 'approval_status', 'label': '审批状态'}],
             write_back={'key': 'wbs.project.set_approval', 'label': '写回 WBS 项目审批状态', 'plugin': 'wbs'},
             on_reject={'key': 'wbs.project.set_approval', 'label': '驳回后写回项目审批状态', 'plugin': 'wbs'},
             default_steps=[
                 {'name': '项目负责人确认', 'assignee_type': 'record_field',
                  'assignee_value': 'owner', 'mode': 'any'},
                 {'name': '管理员审批', 'assignee_type': 'role',
                  'assignee_value': 'admin', 'mode': 'any'},
             ]),
        dict(plugin_id='wbs', key='wbs.project.approval', label='项目审批结果',
             description='管理员通过或驳回项目后触发，可用于归档/通知确认', icon='✅',
             record_type='project',
             context_fields=[{'key': 'name', 'label': '项目名称'},
                             {'key': 'approval_status', 'label': '审批结果'}],
             default_steps=[
                 {'name': '归档确认', 'assignee_type': 'role', 'assignee_value': 'admin', 'mode': 'any'},
             ]),
        dict(plugin_id='wbs', key='wbs.task.created', label='任务创建',
             description='新增或快速新建任务后触发，可用于任务派发确认', icon='📝',
             record_type='task',
             context_fields=[{'key': 'task_name', 'label': '任务名称'},
                             {'key': 'owner', 'label': '任务负责人'},
                             {'key': 'project_name', 'label': '所属项目'}],
             write_back={'key': 'wbs.task.set_approval', 'label': '写回 WBS 任务审批状态', 'plugin': 'wbs'},
             on_reject={'key': 'wbs.task.set_approval', 'label': '驳回后写回任务审批状态', 'plugin': 'wbs'},
             default_steps=[
                 {'name': '任务负责人确认', 'assignee_type': 'record_field',
                  'assignee_value': 'owner', 'mode': 'any'},
                 # v13.5 审查修复：任务审批最终必须由管理员把关，不能让负责人自批自过
                 {'name': '管理员审批', 'assignee_type': 'role',
                  'assignee_value': 'admin', 'mode': 'any'},
             ]),
        dict(plugin_id='wbs', key='wbs.task.approval', label='任务审批结果',
             description='任务审批通过或驳回后触发，用于通知任务负责人', icon='✅',
             record_type='task',
             context_fields=[{'key': 'task_name', 'label': '任务名称'},
                             {'key': 'owner', 'label': '任务负责人'},
                             {'key': 'approval_status', 'label': '审批结果'}],
             default_steps=[
                 {'name': '任务负责人知会', 'assignee_type': 'role',
                  'assignee_value': 'admin', 'mode': 'any'},
             ]),
        dict(plugin_id='wbs', key='wbs.task.progress', label='任务进度更新',
             description='任务状态或进度发生变化时触发，可用于关键节点汇报', icon='📈',
             record_type='task',
             context_fields=[{'key': 'task_name', 'label': '任务名称'},
                             {'key': 'owner', 'label': '任务负责人'},
                             {'key': 'status', 'label': '新状态'},
                             {'key': 'progress', 'label': '新进度'}],
             default_steps=[
                 {'name': '进度确认', 'assignee_type': 'record_field',
                  'assignee_value': 'owner', 'mode': 'any'},
             ]),
        dict(plugin_id='wbs', key='wbs.task.completed', label='任务完成',
             description='任务进度到 100% 或状态改为已完成时触发', icon='🏁',
             record_type='task',
             context_fields=[{'key': 'task_name', 'label': '任务名称'},
                             {'key': 'owner', 'label': '任务负责人'},
                             {'key': 'project_name', 'label': '所属项目'},
                             {'key': 'deliverable', 'label': '交付物'}],
             default_steps=[
                 {'name': '交付物验收', 'assignee_type': 'record_field',
                  'assignee_value': 'owner', 'mode': 'any'},
             ]),
    ]
    for _e in _WF_EVENTS:
        register_event(plugin_name='计划管理', **_e)
except Exception as _e:  # 流程引擎未就绪时不影响插件加载
    import logging as _logging
    _logging.getLogger(__name__).warning('workflow event register failed: %s', _e)
