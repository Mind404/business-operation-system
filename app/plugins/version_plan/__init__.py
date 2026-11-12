# -*- coding: utf-8 -*-
"""版本规划管理（version_plan）

承接云文档里杂乱的原始需求登记 → 需求过滤 → 版本规划 → 开发/测试协同 → 发布与客户回告。
数据表全部由本插件自管，落库走平台统一建表入口，模板继承平台 base.html。
"""
from .routes import blueprint
from . import trains  # noqa: F401  车次/装车/评估/签核/回告路由，共用同一 blueprint

manifest = {
    'id': 'version_plan',
    'name': '版本规划管理',
    'icon': '🗺️',
    'group': '业务插件',
    'core': False,
    'version': '13.0.1.0',
    'description': '多产品按月版本火车：需求评估→装车（人日容量/停发/加车/一步改挂）→版本→7项确认清单+四方签核门禁→发布→客户回告',
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
        'version_plan_trains',
        'version_plan_items',
        'version_plan_load_changes',
        'version_plan_assessments',
        'version_plan_checks',
        'version_plan_signoffs',
        'version_plan_notices',
        'version_plan_customer_products',
    ],
    'menu': [
        {'name': '工作台', 'endpoint': 'version_plan.index', 'icon': '🏠'},
        {'name': '产品管理', 'endpoint': 'version_plan.products', 'icon': '📦'},
        {'name': '需求与过滤', 'endpoint': 'version_plan.demands', 'icon': '🧹'},
        {'name': '需求评估', 'endpoint': 'version_plan.assess', 'icon': '⚖️'},
        {'name': '车次日历', 'endpoint': 'version_plan.trains', 'icon': '🚂'},
        {'name': '装车工作台', 'endpoint': 'version_plan.train_board', 'icon': '🏗️'},
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
            'version_plan_trains',
            'version_plan_items',
            'version_plan_checks',
            'version_plan_signoffs',
            'version_plan_notices',
        ],
        'description': ('version_plan_products 一行=一个产品（code 产品编号，name 产品名，status 启用/停用）；'
                        'version_plan_demands 一行=一条需求（code 需求编号，product_code 所属产品，status 状态，'
                        'version_no 排入版本，doc_ref 云文档溯源）；'
                        'version_plan_versions 一行=一个版本（version_no 版本号，product_code 所属产品，status 版本状态）；'
                        'version_plan_customers 一行=一个客户（product_code 所属产品，当前在用/集成中/定制分支版本）'),
    },
}
