# -*- coding: utf-8 -*-
"""版本规划管理（version_plan_lt）

承接云文档里杂乱的原始需求登记 → 需求过滤 → 版本规划 → 开发/测试协同 → 发布与客户回告。
数据表全部由本插件自管，落库走平台统一建表入口，模板继承平台 base.html。
"""
from .routes import blueprint
from . import trains  # noqa: F401  车次/装车/评估/签核/回告路由，共用同一 blueprint

manifest = {
    'id': 'version_plan_lt',
    'name': '版本规划·原版风格',
    'icon': '🚆',
    'group': '业务插件',
    'core': False,
    'version': '13.1.0.2',
    'description': 'Leantime 原版风格平行版（青色外壳/卡片/门禁行式 1:1 还原）：需求归集→四维评估→车次日历→装车→版本清单+四方签核→发布→客户回告，与现有版本规划管理并存对照',
    'endpoint': 'version_plan_lt.index',
    'url_prefix': '/version_plan_lt',
    'source': 'external',
    'tables': [
        'version_plan_lt_products',
        'version_plan_lt_demands',
        'version_plan_lt_versions',
        'version_plan_lt_customers',
        'version_plan_lt_releases',
        'version_plan_lt_logs',
        'version_plan_lt_trains',
        'version_plan_lt_items',
        'version_plan_lt_load_changes',
        'version_plan_lt_assessments',
        'version_plan_lt_checks',
        'version_plan_lt_signoffs',
        'version_plan_lt_notices',
        'version_plan_lt_customer_products',
    ],
    'menu': [
        {'name': '工作台', 'endpoint': 'version_plan_lt.index', 'icon': '🏠'},
        {'name': '产品管理', 'endpoint': 'version_plan_lt.products', 'icon': '📦'},
        {'name': '需求与过滤', 'endpoint': 'version_plan_lt.demands', 'icon': '🧹'},
        {'name': '需求评估', 'endpoint': 'version_plan_lt.assess', 'icon': '⚖️'},
        {'name': '车次日历', 'endpoint': 'version_plan_lt.trains', 'icon': '🚂'},
        {'name': '装车工作台', 'endpoint': 'version_plan_lt.train_board', 'icon': '🏗️'},
        {'name': '版本规划', 'endpoint': 'version_plan_lt.versions', 'icon': '🗓️'},
        {'name': '客户矩阵', 'endpoint': 'version_plan_lt.customers', 'icon': '🏢'},
        {'name': '客户回告', 'endpoint': 'version_plan_lt.report', 'icon': '✉️'},
        {'name': '模块字典', 'endpoint': 'version_plan_lt.settings', 'icon': '⚙️'},
    ],
    'openapi': 1,
    'data_open': {
        'ai_readable': True,
        'tables': [
            'version_plan_lt_products',
            'version_plan_lt_demands',
            'version_plan_lt_versions',
            'version_plan_lt_customers',
            'version_plan_lt_releases',
            'version_plan_lt_trains',
            'version_plan_lt_items',
            'version_plan_lt_checks',
            'version_plan_lt_signoffs',
            'version_plan_lt_notices',
            'version_plan_lt_load_changes',
            'version_plan_lt_assessments',
            'version_plan_lt_customer_products',
        ],
        'description': ('version_plan_lt_products 一行=一个产品（code 产品编号，name 产品名，status 启用/停用）；'
                        'version_plan_lt_demands 一行=一条需求（code 需求编号，product_code 所属产品，status 状态，'
                        'version_no 排入版本，doc_ref 云文档溯源）；'
                        'version_plan_lt_versions 一行=一个版本（version_no 版本号，product_code 所属产品，status 版本状态）；'
                        'version_plan_lt_customers 一行=一个客户（product_code 所属产品，当前在用/集成中/定制分支版本）；'
                        'version_plan_lt_trains 一行=一个车次（code，product_code，month，status 计划/停发/已转版本/已发布，capacity 人日）；'
                        'version_plan_lt_items 一行=需求装载状态（train_code，demand_code，state 已装/候补/已移出）；'
                        'version_plan_lt_checks 一行=发布清单项（version_no+product_code 双键，item_no 1-7，result）；'
                        'version_plan_lt_signoffs 一行=签核（role 产品/测试/发布经理/客户代表，verdict）；'
                        'version_plan_lt_notices 一行=客户回告记录'),
    },
}
