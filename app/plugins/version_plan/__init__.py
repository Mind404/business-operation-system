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
    'version': '13.0.1.6',
    'min_role': 'user',
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
    'workflow': {
        'actions': [
            {'key': 'version_plan.version.freeze', 'label': '写回版本冻结状态'},
            {'key': 'version_plan.version.release', 'label': '执行版本发布（唯一发布入口）'},
            {'key': 'version_plan.version.unfreeze', 'label': '执行强制解冻并留痕'},
        ],
        'events': [
            'version_plan.version.created',
            'version_plan.version.freeze',
            'version_plan.version.frozen',
            'version_plan.version.unfrozen',
            'version_plan.version.release',
            'version_plan.version.released',
            'version_plan.version.archived',
            'version_plan.version.unfreeze',
            'version_plan.demand.filtered',
            'version_plan.train.converted',
        ],
    },
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
            'version_plan_load_changes',
            'version_plan_assessments',
            'version_plan_customer_products',
        ],
        'description': ('version_plan_products 一行=一个产品（code 产品编号，name 产品名，status 启用/停用）；'
                        'version_plan_demands 一行=一条需求（code 需求编号，product_code 所属产品，status 状态，'
                        'version_no 排入版本，doc_ref 云文档溯源）；'
                        'version_plan_versions 一行=一个版本（version_no 版本号，product_code 所属产品，status 版本状态）；'
                        'version_plan_customers 一行=一个客户（product_code 所属产品，当前在用/集成中/定制分支版本）；'
                        'version_plan_trains 一行=一个车次（code，product_code，month，status 计划/停发/已转版本/已发布，capacity 人日）；'
                        'version_plan_items 一行=需求装载状态（train_code，demand_code，state 已装/候补/已移出）；'
                        'version_plan_checks 一行=发布清单项（version_no+product_code 双键，item_no 1-7，result）；'
                        'version_plan_signoffs 一行=签核（role 产品/测试/发布经理/客户代表，verdict）；'
                        'version_plan_notices 一行=客户回告记录'),
    },
}

# ============================================================
# v13.5：版本规划接入流程引擎
#   - 审批类节点（冻结/发布/解冻）带 write_back：一键生成的流程通过后自动写回业务
#   - 通知类节点（创建/已冻结/已发布/归档/需求过滤/车次转版本）只上报，不做二次审批
# ============================================================
try:
    from workflow_engine import register_event, register_action
    from .routes import (wf_action_freeze, wf_action_release, wf_action_unfreeze)

    _WF_EVENTS = [
        dict(plugin_id='version_plan', key='version_plan.version.created', label='版本创建',
             description='版本创建成功（状态=规划中）后触发，可用于立项知会或补充确认',
             icon='🗓️', record_type='version',
             context_fields=[{'key': 'version_no', 'label': '版本号'},
                             {'key': 'product_code', 'label': '产品编号'},
                             {'key': 'product_owner', 'label': '产品负责人'},
                             {'key': 'vtype', 'label': '版本类型'},
                             {'key': 'release_date', 'label': '计划发布日'}],
             default_steps=[
                 {'name': '产品负责人确认', 'assignee_type': 'record_field',
                  'assignee_value': 'product_owner', 'mode': 'any'},
             ]),
        dict(plugin_id='version_plan', key='version_plan.version.freeze', label='版本冻结审批',
             description='点「冻结版本」时触发；审批通过后才锁定需求构成（R1）',
             icon='🔒', record_type='version',
             write_back={'key': 'version_plan.version.freeze', 'label': '写回版本冻结状态',
                         'plugin': 'version_plan'},
             context_fields=[{'key': 'version_no', 'label': '版本号'},
                             {'key': 'product_code', 'label': '产品编号'},
                             {'key': 'product_owner', 'label': '产品负责人'},
                             {'key': 'version_status', 'label': '当前状态'}],
             default_steps=[
                 {'name': '产品负责人确认', 'assignee_type': 'record_field',
                  'assignee_value': 'product_owner', 'mode': 'any'},
                 {'name': '管理员审批', 'assignee_type': 'role',
                  'assignee_value': 'admin', 'mode': 'any'},
             ]),
        dict(plugin_id='version_plan', key='version_plan.version.frozen', label='版本已冻结',
             description='版本冻结生效后触发，可用于知会研发/测试开始排期', icon='✅',
             record_type='version',
             context_fields=[{'key': 'version_no', 'label': '版本号'},
                             {'key': 'product_code', 'label': '产品编号'},
                             {'key': 'version_status', 'label': '当前状态'}],
             default_steps=[
                 {'name': '研发负责人确认', 'assignee_type': 'role',
                  'assignee_value': 'admin', 'mode': 'any'},
             ]),
        dict(plugin_id='version_plan', key='version_plan.version.unfrozen', label='版本已解冻',
             description='强制解冻审批通过或直接解冻后触发，用于知会研发/测试恢复变更', icon='🔓',
             record_type='version',
             context_fields=[{'key': 'version_no', 'label': '版本号'},
                             {'key': 'product_code', 'label': '产品编号'},
                             {'key': 'version_status', 'label': '当前状态'}],
             default_steps=[
                 {'name': '研发负责人确认', 'assignee_type': 'role',
                  'assignee_value': 'admin', 'mode': 'any'},
             ]),
        dict(plugin_id='version_plan', key='version_plan.version.release', label='版本发布审批',
             description='点「发布」且 7 项确认清单全通过时触发；审批通过后才真正发布',
             icon='🚀', record_type='version',
             write_back={'key': 'version_plan.version.release', 'label': '执行版本发布',
                         'plugin': 'version_plan'},
             context_fields=[{'key': 'version_no', 'label': '版本号'},
                             {'key': 'product_code', 'label': '产品编号'},
                             {'key': 'product_owner', 'label': '产品负责人'},
                             {'key': 'vtype', 'label': '版本类型'},
                             {'key': 'release_date', 'label': '计划发布日'},
                             {'key': 'target_customers', 'label': '目标客户'}],
             default_steps=[
                 {'name': '产品负责人确认', 'assignee_type': 'record_field',
                  'assignee_value': 'product_owner', 'mode': 'any'},
                 {'name': '发布经理审批', 'assignee_type': 'role',
                  'assignee_value': 'admin', 'mode': 'any'},
             ]),
        dict(plugin_id='version_plan', key='version_plan.version.released', label='版本已发布',
             description='发布生效（含流程审批通过）后触发，可用于客户回告/交付确认', icon='📣',
             record_type='version',
             context_fields=[{'key': 'version_no', 'label': '版本号'},
                             {'key': 'product_code', 'label': '产品编号'},
                             {'key': 'target_customers', 'label': '目标客户'},
                             {'key': 'version_status', 'label': '当前状态'}],
             default_steps=[
                 {'name': '客户回告确认', 'assignee_type': 'role',
                  'assignee_value': 'admin', 'mode': 'any'},
             ]),
        dict(plugin_id='version_plan', key='version_plan.version.archived', label='版本归档',
             description='版本归档后触发，用于归档知会', icon='📦', record_type='version',
             context_fields=[{'key': 'version_no', 'label': '版本号'},
                             {'key': 'product_code', 'label': '产品编号'}],
             default_steps=[
                 {'name': '归档确认', 'assignee_type': 'role', 'assignee_value': 'admin', 'mode': 'any'},
             ]),
        dict(plugin_id='version_plan', key='version_plan.version.unfreeze', label='强制解冻审批',
             description='管理员点「强制解冻」时触发；审批通过后才解冻并保留原因',
             icon='🔓', record_type='version',
             write_back={'key': 'version_plan.version.unfreeze', 'label': '执行强制解冻并留痕',
                         'plugin': 'version_plan'},
             context_fields=[{'key': 'version_no', 'label': '版本号'},
                             {'key': 'product_code', 'label': '产品编号'},
                             {'key': 'reason', 'label': '解冻原因'},
                             {'key': 'version_status', 'label': '当前状态'}],
             default_steps=[
                 {'name': '管理员审批', 'assignee_type': 'role',
                  'assignee_value': 'admin', 'mode': 'any'},
             ]),
        dict(plugin_id='version_plan', key='version_plan.demand.filtered', label='需求过滤完成',
             description='原始需求被判定为进规划池/挂起/不做后触发，可用于知会提出人',
             icon='🧹', record_type='demand',
             context_fields=[{'key': 'demand_code', 'label': '需求编号'},
                             {'key': 'result', 'label': '过滤结论'},
                             {'key': 'product_code', 'label': '产品编号'},
                             {'key': 'customer_name', 'label': '来源客户'},
                             {'key': 'filter_note', 'label': '过滤说明'}],
             default_steps=[
                 {'name': '提出人确认', 'assignee_type': 'role', 'assignee_value': 'admin', 'mode': 'any'},
             ]),
        dict(plugin_id='version_plan', key='version_plan.train.converted', label='车次转版本',
             description='车次被转为正式版本（或一键改挂）后触发，用于版本立项知会',
             icon='🚂', record_type='version',
             context_fields=[{'key': 'train_code', 'label': '车次'},
                             {'key': 'version_no', 'label': '生成版本号'},
                             {'key': 'product_code', 'label': '产品编号'}],
             default_steps=[
                 {'name': '产品负责人确认', 'assignee_type': 'role',
                  'assignee_value': 'admin', 'mode': 'any'},
             ]),
    ]
    for _e in _WF_EVENTS:
        register_event(plugin_name='版本规划管理', **_e)

    register_action('version_plan.version.freeze', wf_action_freeze,
                    label='写回版本冻结状态', plugin='version_plan')
    register_action('version_plan.version.release', wf_action_release,
                    label='执行版本发布（唯一发布入口）', plugin='version_plan')
    register_action('version_plan.version.unfreeze', wf_action_unfreeze,
                    label='执行强制解冻并留痕', plugin='version_plan')
except Exception as _e:  # 流程引擎未就绪时不影响插件加载
    import logging as _logging
    _logging.getLogger(__name__).warning('version_plan workflow register failed: %s', _e)
