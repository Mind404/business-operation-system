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
        'templates': ['wbs_project_approval', 'wbs_task_approval']
    },
}
