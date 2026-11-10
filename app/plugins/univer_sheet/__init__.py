# -*- coding: utf-8 -*-
"""需求统计管理插件（基于 Univer 开源项目官方 SDK）"""
from .routes import blueprint

manifest = {
'source': 'builtin',  # 平台自带
        'id': 'univer_sheet', 'bundled': True,  # 平台自带，不属于外部插件
    'name': '需求统计管理',
    'icon': '📊',
    'group': '业务插件',
    'core': False,
    'description': '基于 Univer 开源项目（字节跳动开源电子表格 SDK，v1.3.1）的需求统计管理：官方 Excel 级体验（公式/样式/合并单元格/筛选/多工作表），快照落库、版本轮询同步、乐观锁防覆盖',
    'endpoint': 'univer_sheet.index',
    'url_prefix': '/univer',
    'tables': ['univer_rooms'],
    'menu': [
        {'name': '需求统计管理', 'endpoint': 'univer_sheet.index', 'icon': '📊'},
    ],
}
