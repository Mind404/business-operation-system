# -*- coding: utf-8 -*-
"""测试管理插件：测试用例库、测试执行、测试报告"""
from .routes import blueprint

manifest = {
    'id': 'test',
    'name': '测试管理',
    'icon': '🧪',
    'group': '业务插件',
    'core': False,
    'description': '测试用例、测试执行、测试记录、测试批次与报告',
    'endpoint': 'test.test_case_list',
    'tables': ['test_cases', 'test_executions', 'test_records', 'test_batches'],
    'menu': [
        {'name': '测试用例', 'endpoint': 'test.test_case_list', 'icon': '🧪'},
    ],
}
