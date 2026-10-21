# -*- coding: utf-8 -*-
"""Bug/质量管理插件：Bug流转、严重问题升级、质量闭环"""
from .routes import blueprint

manifest = {
    'id': 'bugs',
    'name': 'Bug/质量管理',
    'icon': '🐛',
    'group': '业务插件',
    'core': False,
    'description': 'Bug流转、严重问题升级、质量闭环',
    'endpoint': 'bugs.bug_list',
    'tables': ['bugs'],
    'menu': [
        {'name': 'Bug管理', 'endpoint': 'bugs.bug_list', 'icon': '🐛'},
    ],
}
