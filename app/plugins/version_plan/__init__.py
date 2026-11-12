# -*- coding: utf-8 -*-
"""版本规划管理（version_plan）

承接云文档里杂乱的原始需求登记 → 需求过滤 → 版本规划 → 开发/测试协同 → 发布与客户回告。
数据表全部由本插件自管，落库走平台统一建表入口，模板继承平台 base.html。
"""
from .routes import blueprint

manifest = {
    'id': 'version_plan',
    'name': '版本规划管理',
    'icon': '🗺️',
    'group': '业务插件',
    'core': False,
    'description': '多产品：分产品过滤需求、规划版本、流转受控；版本规划单 + 客户-版本矩阵 + Release Notes + 客户回告',
    'endpoint': 'version_plan.index',
    'url_prefix': '/version_plan',
    'source': 'external',
    'tables': [
        'version_plan_products',
        'version_plan_demands',
        'version_plan_versions',
        'version_plan_customers',
        'version_plan_releases',
        'version_plan_logs',
    ],
    'menu': [
        {'name': '工作台', 'endpoint': 'version_plan.index', 'icon': '🏠'},
        {'name': '产品管理', 'endpoint': 'version_plan.products', 'icon': '📦'},
        {'name': '需求与过滤', 'endpoint': 'version_plan.demands', 'icon': '🧹'},
        {'name': '版本规划', 'endpoint': 'version_plan.versions', 'icon': '🗓️'},
        {'name': '客户矩阵', 'endpoint': 'version_plan.customers', 'icon': '🏢'},
        {'name': '客户回告', 'endpoint': 'version_plan.report', 'icon': '✉️'},
    ],
    'openapi': 1,
    'data_open': {
        'ai_readable': True,
        'tables': [
            'version_plan_products',
            'version_plan_demands',
            'version_plan_versions',
            'version_plan_customers',
            'version_plan_releases',
        ],
        'description': ('version_plan_products 一行=一个产品（code 产品编号，name 产品名，status 启用/停用）；'
                        'version_plan_demands 一行=一条需求（code 需求编号，product_code 所属产品，status 状态，'
                        'version_no 排入版本，doc_ref 云文档溯源）；'
                        'version_plan_versions 一行=一个版本（version_no 版本号，product_code 所属产品，status 版本状态）；'
                        'version_plan_customers 一行=一个客户（product_code 所属产品，当前在用/集成中/定制分支版本）'),
    },
}
