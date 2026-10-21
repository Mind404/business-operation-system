# -*- coding: utf-8 -*-
"""需求管理插件：需求库、外部技术需求统计、需求评审与变更"""
from .routes import blueprint

manifest = {
    'id': 'requirements',
    'name': '需求管理',
    'icon': '📋',
    'group': '业务插件',
    'core': False,
    'description': '需求收集、评审、变更、基线、追溯；外部技术需求统计',
    'endpoint': 'requirements.requirements_list',
    'tables': ['requirements', 'tech_requirements', 'tech_req_logs'],
    'menu': [
        {'name': '需求管理', 'endpoint': 'requirements.requirements_list', 'icon': '📋'},
        {'name': '技术需求', 'endpoint': 'requirements.tech_req_list', 'icon': '🔬'},
    ],
}
