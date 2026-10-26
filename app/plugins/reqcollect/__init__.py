# -*- coding: utf-8 -*-
"""需求收集插件（Luckysheet 云文档版）"""
from .routes import blueprint

manifest = {
    'id': 'reqcollect',
    'name': '需求收集管理',
    'icon': '📥',
    'group': '业务插件',
    'core': False,
    'description': '内外部需求收集管理：基于 Luckysheet 的类 Excel 云文档表格，可建子表/多工作表，全员协作填写',
    'endpoint': 'reqcollect.index',
    'url_prefix': '/reqcollect',
    'tables': ['reqcollect_books'],
    'menu': [
        {'name': '需求收集管理', 'endpoint': 'reqcollect.index', 'icon': '📥'},
    ],
}
