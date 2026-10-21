# -*- coding: utf-8 -*-
"""交付管理插件（归入文档管理域）：交付计划、交付跟踪、交付状态"""
from .routes import blueprint

manifest = {
    'id': 'delivery',
    'name': '交付管理',
    'icon': '📦',
    'group': '业务插件',
    'core': False,
    'description': '交付计划、交付跟踪、需求→生产→入库→交付全链路',
    'endpoint': 'delivery.delivery_plan_list',
    'tables': ['delivery_plans'],
    'menu': [
        {'name': '交付管理', 'endpoint': 'delivery.delivery_plan_list', 'icon': '📦'},
    ],
}
