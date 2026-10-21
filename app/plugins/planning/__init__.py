# -*- coding: utf-8 -*-
"""计划管理插件：产品规划与版本规划"""
from .routes import blueprint

manifest = {
    'id': 'planning',
    'name': '计划管理',
    'icon': '📅',
    'group': '业务插件',
    'core': False,
    'description': '产品规划、版本规划、里程碑管理',
    'endpoint': 'planning.planning_list',
    'tables': ['product_plans', 'software_versions', 'version_requirements'],
    'menu': [
        {'name': '计划管理', 'endpoint': 'planning.planning_list', 'icon': '📅'},
    ],
}
