# -*- coding: utf-8 -*-
"""计划管理插件（核心）：WBS项目拆解、任务管理、进度跟踪、交付物"""
from .routes import blueprint

manifest = {
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
}
