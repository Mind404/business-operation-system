# -*- coding: utf-8 -*-
from .routes import blueprint
manifest = {
    'id': 'contract_ledger', 'source': 'external',  # 外部插件（模拟第三方上传/目录接入）
    'name': '合同台账', 'icon': '📑', 'group': '业务插件', 'core': False,
    'description': '销售合同台账：签约、交付、回款状态与金额，支持筛选、分页、CSV 导入导出',
    'endpoint': 'contract_ledger.index', 'url_prefix': '/contract_ledger',
    'tables': ['contract_items'],
    'data_open': {'ai_readable': True, 'tables': ['contract_items'],
                  'description': '一行=一份销售合同，amount 为合同金额（元），status 为履约状态'},
}
