# -*- coding: utf-8 -*-
"""需求收集插件（v11.0 全新设计，强管控）：
需求全生命周期（待评审→评审通过→已采纳→开发中→已完成→已关闭）、
评审记录、变更留痕、字段必填/显示可配置、关联WBS、状态变更走平台统一审批流。
"""
from .routes import blueprint

manifest = {
    'id': 'requirements',
    'name': '需求收集',
    'icon': '📋',
    'group': '业务插件',
    'core': False,
    'description': '需求强管控：收集、评审、状态流转、审批、追溯、关联WBS',
    'endpoint': 'requirements.req_list',
    'tables': ['req_items', 'req_reviews', 'req_changes', 'req_field_config'],
    'menu': [
        {'name': '需求收集', 'endpoint': 'requirements.req_list', 'icon': '📋'},
    ],
}
