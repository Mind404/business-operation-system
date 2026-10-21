# -*- coding: utf-8 -*-
"""合同管理插件（归入文档管理域）：生产/物料合同、用户合同、付款节点"""
# 常量必须在导入 routes 之前定义，避免循环导入
CONTRACT_TYPES_PROD = ['生产合同', '物料合同']
CONTRACT_TYPES_USER = ['用户合同']

from .routes import blueprint

manifest = {
    'id': 'contract',
    'name': '合同管理',
    'icon': '📄',
    'group': '业务插件',
    'core': False,
    'description': '生产/物料合同、用户合同、付款节点与状态管理',
    'endpoint': 'contract.contract_list',
    'tables': ['contracts', 'contract_payments'],
    'menu': [
        {'name': '合同管理', 'endpoint': 'contract.contract_list', 'icon': '📄'},
    ],
}
