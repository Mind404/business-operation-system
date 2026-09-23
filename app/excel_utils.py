# -*- coding: utf-8 -*-
"""
Excel批量导入导出工具模块
支持：根据表配置生成Excel模板、解析Excel批量导入、数据校验
"""
import os
import re
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side

# ============================================================
# 表配置：定义每个表的可导入字段、必填字段、字段说明
# ============================================================
TABLE_CONFIG = {
    'requirements': {
        'name': '需求数据库',
        'import_fields': ['title', 'source_dept', 'customer', 'description', 'quantity', 'priority', 'delivery_date', 'status'],
        'required_fields': ['title', 'quantity'],
        'field_labels': {
            'title': '需求标题*',
            'source_dept': '来源部门',
            'customer': '关联客户',
            'description': '需求描述',
            'quantity': '需求数量*',
            'priority': '优先级(高/中/低)',
            'delivery_date': '要求交付日期(YYYY-MM-DD)',
            'status': '状态(待评审/已评审/进行中/已完成/已交付/已取消)',
        },
        'field_defaults': {'priority': '中', 'status': '待评审'},
        'field_validators': {
            'quantity': lambda v: int(v) > 0,
            'priority': lambda v: v in ('高', '中', '低', ''),
            'status': lambda v: v in ('待评审', '已评审', '进行中', '已完成', '已交付', '已取消', ''),
        },
    },
    'products': {
        'name': '产品管理',
        'import_fields': ['product_name', 'product_model', 'category', 'specification', 'status', 'description'],
        'required_fields': ['product_name', 'product_model'],
        'field_labels': {
            'product_name': '产品名称*',
            'product_model': '产品型号*',
            'category': '产品类别',
            'specification': '技术规格',
            'status': '状态(研发中/量产中/维护中/已停产)',
            'description': '产品描述',
        },
        'field_defaults': {'status': '研发中'},
        'field_validators': {
            'status': lambda v: v in ('研发中', '量产中', '维护中', '已停产', ''),
        },
    },
    'software_versions': {
        'name': '软件版本',
        'import_fields': ['product_id', 'version_no', 'version_name', 'description', 'planned_release_date', 'status'],
        'required_fields': ['product_id', 'version_no'],
        'field_labels': {
            'product_id': '产品ID*',
            'version_no': '版本号*',
            'version_name': '版本名称',
            'description': '版本描述',
            'planned_release_date': '计划发布日期(YYYY-MM-DD)',
            'status': '状态(规划中/开发中/测试中/已发布/已归档)',
        },
        'field_defaults': {'status': '规划中'},
        'field_validators': {
            'status': lambda v: v in ('规划中', '开发中', '测试中', '已发布', '已归档', ''),
        },
    },
    'dev_tasks': {
        'name': '开发任务',
        'import_fields': ['version_id', 'task_name', 'description', 'priority', 'status', 'assignee', 'planned_start', 'planned_finish'],
        'required_fields': ['version_id', 'task_name'],
        'field_labels': {
            'version_id': '版本ID*',
            'task_name': '任务名称*',
            'description': '任务描述',
            'priority': '优先级(高/中/低)',
            'status': '状态(待开发/开发中/已完成/已取消)',
            'assignee': '负责人',
            'planned_start': '计划开始日期',
            'planned_finish': '计划完成日期',
        },
        'field_defaults': {'priority': '中', 'status': '待开发'},
        'field_validators': {
            'priority': lambda v: v in ('高', '中', '低', ''),
            'status': lambda v: v in ('待开发', '开发中', '已完成', '已取消', ''),
        },
    },
    'bug_records': {
        'name': 'Bug管理',
        'import_fields': ['bug_no', 'product_id', 'version_id', 'title', 'description', 'severity', 'status', 'reporter', 'assignee'],
        'required_fields': ['bug_no', 'title'],
        'field_labels': {
            'bug_no': 'Bug编号*',
            'product_id': '产品ID',
            'version_id': '版本ID',
            'title': 'Bug标题*',
            'description': 'Bug描述',
            'severity': '严重程度(致命/严重/一般/轻微)',
            'status': '状态(待处理/处理中/已解决/已关闭/已拒绝)',
            'reporter': '报告人',
            'assignee': '处理人',
        },
        'field_defaults': {'severity': '一般', 'status': '待处理'},
        'field_validators': {
            'severity': lambda v: v in ('致命', '严重', '一般', '轻微', ''),
            'status': lambda v: v in ('待处理', '处理中', '已解决', '已关闭', '已拒绝', ''),
        },
    },
    'production_schedules': {
        'name': '生产排程',
        'import_fields': ['schedule_no', 'product_id', 'requirement_id', 'quantity', 'plan_start', 'plan_finish', 'status', 'remark'],
        'required_fields': ['schedule_no', 'product_id', 'quantity'],
        'field_labels': {
            'schedule_no': '排程编号*',
            'product_id': '产品ID*',
            'requirement_id': '关联需求ID',
            'quantity': '生产数量*',
            'plan_start': '计划开始日期',
            'plan_finish': '计划完成日期',
            'status': '状态(规划中/待排产/生产中/已完成/已取消)',
            'remark': '备注',
        },
        'field_defaults': {'status': '规划中'},
        'field_validators': {
            'quantity': lambda v: int(v) > 0,
            'status': lambda v: v in ('规划中', '待排产', '生产中', '已完成', '已取消', ''),
        },
    },
    'inventory': {
        'name': '库存管理',
        'import_fields': ['material_name', 'material_type', 'specification', 'quantity', 'unit', 'location', 'supplier', 'status'],
        'required_fields': ['material_name', 'quantity'],
        'field_labels': {
            'material_name': '物料名称*',
            'material_type': '物料类型(关键物料/通用物料)',
            'specification': '规格型号',
            'quantity': '库存数量*',
            'unit': '单位',
            'location': '存放位置',
            'supplier': '供应商',
            'status': '状态(正常/预警/缺货)',
        },
        'field_defaults': {'material_type': '通用物料', 'unit': '个', 'status': '正常'},
        'field_validators': {
            'quantity': lambda v: int(v) >= 0,
            'material_type': lambda v: v in ('关键物料', '通用物料', ''),
            'status': lambda v: v in ('正常', '预警', '缺货', ''),
        },
    },
    'quality_inspections': {
        'name': '质量检测',
        'import_fields': ['inspection_no', 'product_id', 'production_order_id', 'inspection_type', 'sample_size', 'passed_count', 'failed_count', 'result', 'status', 'inspector'],
        'required_fields': ['inspection_no', 'product_id'],
        'field_labels': {
            'inspection_no': '检测编号*',
            'product_id': '产品ID*',
            'production_order_id': '生产订单ID',
            'inspection_type': '检测类型(来料检验/过程检验/成品检验)',
            'sample_size': '抽样数量',
            'passed_count': '合格数量',
            'failed_count': '不合格数量',
            'result': '检测结果(合格/不合格/有条件合格)',
            'status': '状态(待检测/检测中/已完成)',
            'inspector': '检测人',
        },
        'field_defaults': {'inspection_type': '成品检验', 'status': '待检测'},
        'field_validators': {
            'inspection_type': lambda v: v in ('来料检验', '过程检验', '成品检验', ''),
            'result': lambda v: v in ('合格', '不合格', '有条件合格', ''),
            'status': lambda v: v in ('待检测', '检测中', '已完成', ''),
        },
    },
    'deliveries': {
        'name': '交付管理',
        'import_fields': ['delivery_no', 'product_id', 'requirement_id', 'customer', 'quantity', 'delivery_date', 'status', 'delivery_person', 'remark'],
        'required_fields': ['delivery_no', 'product_id', 'quantity'],
        'field_labels': {
            'delivery_no': '交付编号*',
            'product_id': '产品ID*',
            'requirement_id': '关联需求ID',
            'customer': '客户名称',
            'quantity': '交付数量*',
            'delivery_date': '交付日期',
            'status': '状态(待交付/交付中/已交付/已取消)',
            'delivery_person': '交付人',
            'remark': '备注',
        },
        'field_defaults': {'status': '待交付'},
        'field_validators': {
            'quantity': lambda v: int(v) > 0,
            'status': lambda v: v in ('待交付', '交付中', '已交付', '已取消', ''),
        },
    },
    'delivery_plans': {
        'name': '交付计划',
        'import_fields': ['plan_no', 'requirement_id', 'product_id', 'customer', 'planned_quantity', 'planned_date', 'status', 'owner', 'remark'],
        'required_fields': ['plan_no', 'planned_quantity'],
        'field_labels': {
            'plan_no': '计划编号*',
            'requirement_id': '需求ID',
            'product_id': '产品ID',
            'customer': '客户',
            'planned_quantity': '计划数量*',
            'planned_date': '计划日期',
            'status': '状态(规划中/执行中/已完成/已取消)',
            'owner': '负责人',
            'remark': '备注',
        },
        'field_defaults': {'status': '规划中'},
        'field_validators': {
            'planned_quantity': lambda v: int(v) > 0,
            'status': lambda v: v in ('规划中', '执行中', '已完成', '已取消', ''),
        },
    },
    'revenue_confirmations': {
        'name': '收入确认',
        'import_fields': ['confirmation_no', 'delivery_id', 'customer', 'amount', 'confirmation_date', 'status', 'remark'],
        'required_fields': ['confirmation_no', 'amount'],
        'field_labels': {
            'confirmation_no': '确认编号*',
            'delivery_id': '交付ID',
            'customer': '客户',
            'amount': '确认金额*',
            'confirmation_date': '确认日期',
            'status': '状态(待确认/已确认/已驳回)',
            'remark': '备注',
        },
        'field_defaults': {'status': '待确认'},
        'field_validators': {
            'amount': lambda v: float(v) >= 0,
            'status': lambda v: v in ('待确认', '已确认', '已驳回', ''),
        },
    },
    'test_cases': {
        'name': '测试用例',
        'import_fields': ['case_no', 'product_id', 'version_id', 'title', 'precondition', 'steps', 'expected_result', 'priority', 'status', 'author'],
        'required_fields': ['case_no', 'title'],
        'field_labels': {
            'case_no': '用例编号*',
            'product_id': '产品ID',
            'version_id': '版本ID',
            'title': '用例标题*',
            'precondition': '前置条件',
            'steps': '测试步骤',
            'expected_result': '预期结果',
            'priority': '优先级(高/中/低)',
            'status': '状态(草稿/已评审/已废弃)',
            'author': '作者',
        },
        'field_defaults': {'priority': '中', 'status': '草稿'},
        'field_validators': {
            'priority': lambda v: v in ('高', '中', '低', ''),
            'status': lambda v: v in ('草稿', '已评审', '已废弃', ''),
        },
    },
    'tech_support_tickets': {
        'name': '技术支持工单',
        'import_fields': ['ticket_no', 'customer', 'product_id', 'title', 'description', 'priority', 'status', 'reporter', 'assignee'],
        'required_fields': ['ticket_no', 'title'],
        'field_labels': {
            'ticket_no': '工单编号*',
            'customer': '客户',
            'product_id': '产品ID',
            'title': '工单标题*',
            'description': '问题描述',
            'priority': '优先级(高/中/低)',
            'status': '状态(待处理/处理中/已解决/已关闭)',
            'reporter': '报告人',
            'assignee': '处理人',
        },
        'field_defaults': {'priority': '中', 'status': '待处理'},
        'field_validators': {
            'priority': lambda v: v in ('高', '中', '低', ''),
            'status': lambda v: v in ('待处理', '处理中', '已解决', '已关闭', ''),
        },
    },
    'user_feedbacks': {
        'name': '用户反馈',
        'import_fields': ['feedback_no', 'customer', 'product_id', 'feedback_type', 'title', 'content', 'status', 'handler'],
        'required_fields': ['feedback_no', 'title'],
        'field_labels': {
            'feedback_no': '反馈编号*',
            'customer': '客户',
            'product_id': '产品ID',
            'feedback_type': '反馈类型(功能建议/Bug反馈/使用问题/其他)',
            'title': '反馈标题*',
            'content': '反馈内容',
            'status': '状态(待处理/处理中/已解决/已关闭)',
            'handler': '处理人',
        },
        'field_defaults': {'feedback_type': '其他', 'status': '待处理'},
        'field_validators': {
            'feedback_type': lambda v: v in ('功能建议', 'Bug反馈', '使用问题', '其他', ''),
            'status': lambda v: v in ('待处理', '处理中', '已解决', '已关闭', ''),
        },
    },
    'devices': {
        'name': '设备管理',
        'import_fields': ['device_no', 'device_name', 'device_type', 'product_id', 'customer', 'status', 'location', 'firmware_version'],
        'required_fields': ['device_no', 'device_name'],
        'field_labels': {
            'device_no': '设备编号*',
            'device_name': '设备名称*',
            'device_type': '设备类型',
            'product_id': '产品ID',
            'customer': '所属客户',
            'status': '状态(在线/离线/故障/维护中)',
            'location': '部署位置',
            'firmware_version': '固件版本',
        },
        'field_defaults': {'status': '离线'},
        'field_validators': {
            'status': lambda v: v in ('在线', '离线', '故障', '维护中', ''),
        },
    },
    'contracts': {
        'name': '合同管理',
        'import_fields': ['contract_no', 'contract_type', 'supplier', 'product_id', 'amount', 'sign_date', 'delivery_date', 'status', 'owner', 'progress', 'payment_status', 'remark'],
        'required_fields': ['contract_type', 'supplier', 'amount'],
        'field_labels': {
            'contract_no': '合同编号(留空自动生成)',
            'contract_type': '合同类型*(生产合同/物料合同/用户合同/服务合同/技术合作/其他)',
            'supplier': '供应商/客户*',
            'product_id': '产品ID(选填)',
            'amount': '合同金额(元)*',
            'sign_date': '签订日期',
            'delivery_date': '交付日期',
            'status': '状态(执行中/已完成/已终止)',
            'owner': '负责人',
            'progress': '进度跟进',
            'payment_status': '付款状态(未付款/部分付款/已付款)',
            'remark': '备注',
        },
        'field_defaults': {'status': '执行中', 'payment_status': '未付款', 'owner': 'product_a'},
        'field_validators': {
            'status': lambda v: v in ('执行中', '已完成', '已终止', ''),
            'contract_type': lambda v: v in ('生产合同', '物料合同', '用户合同', '服务合同', '技术合作', '其他', ''),
            'payment_status': lambda v: v in ('未付款', '部分付款', '已付款', ''),
        },
    },
    'resource_schedules': {
        'name': '资源调度',
        'import_fields': ['schedule_no', 'resource_type', 'resource_name', 'task', 'start_time', 'end_time', 'status', 'owner'],
        'required_fields': ['schedule_no', 'resource_name'],
        'field_labels': {
            'schedule_no': '调度编号*',
            'resource_type': '资源类型(人员/设备/物料)',
            'resource_name': '资源名称*',
            'task': '任务描述',
            'start_time': '开始时间',
            'end_time': '结束时间',
            'status': '状态(规划中/执行中/已完成/已取消)',
            'owner': '负责人',
        },
        'field_defaults': {'resource_type': '人员', 'status': '规划中'},
        'field_validators': {
            'resource_type': lambda v: v in ('人员', '设备', '物料', ''),
            'status': lambda v: v in ('规划中', '执行中', '已完成', '已取消', ''),
        },
    },
}


def get_supported_tables():
    """获取支持批量导入的表列表"""
    return list(TABLE_CONFIG.keys())


def generate_template(table_name, output_path):
    """
    根据表配置生成Excel模板
    :param table_name: 表名
    :param output_path: 输出文件路径
    :return: 生成的文件路径
    """
    if table_name not in TABLE_CONFIG:
        raise ValueError(f'不支持的表名: {table_name}')

    config = TABLE_CONFIG[table_name]
    wb = Workbook()
    ws = wb.active
    ws.title = config['name']

    # 样式定义
    header_font = Font(name='微软雅黑', size=11, bold=True, color='FFFFFF')
    header_fill = PatternFill(start_color='4472C4', end_color='4472C4', fill_type='solid')
    header_alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
    thin_border = Border(
        left=Side(style='thin'),
        right=Side(style='thin'),
        top=Side(style='thin'),
        bottom=Side(style='thin')
    )

    # 写入表头
    for col_idx, field in enumerate(config['import_fields'], 1):
        cell = ws.cell(row=1, column=col_idx)
        cell.value = config['field_labels'].get(field, field)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = header_alignment
        cell.border = thin_border
        # 设置列宽
        ws.column_dimensions[cell.column_letter].width = 20

    # 写入示例数据行（第2行）
    example_data = {
        'title': '示例：5G工业通信模组需求',
        'source_dept': '交付服务组',
        'customer': '示例客户',
        'description': '示例描述',
        'quantity': 100,
        'priority': '中',
        'delivery_date': '2026-12-31',
        'status': '待评审',
        'product_name': '示例产品',
        'product_model': 'CM-EXAMPLE-01',
        'version_no': 'V1.0.0',
        'task_name': '示例任务',
        'bug_no': 'BUG-EXAMPLE-001',
        'schedule_no': 'PS-EXAMPLE-001',
        'material_name': '示例物料',
        'inspection_no': 'QI-EXAMPLE-001',
        'delivery_no': 'DL-EXAMPLE-001',
        'plan_no': 'DP-EXAMPLE-001',
        'confirmation_no': 'RC-EXAMPLE-001',
        'case_no': 'TC-EXAMPLE-001',
        'ticket_no': 'TK-EXAMPLE-001',
        'feedback_no': 'FB-EXAMPLE-001',
        'device_no': 'DEV-EXAMPLE-001',
        'device_name': '示例设备',
        'contract_no': 'CT-EXAMPLE-001',
        'contract_name': '示例合同',
    }
    for col_idx, field in enumerate(config['import_fields'], 1):
        cell = ws.cell(row=2, column=col_idx)
        cell.value = example_data.get(field, '')
        cell.border = thin_border
        cell.alignment = Alignment(horizontal='left', vertical='center')

    # 冻结首行
    ws.freeze_panes = 'A2'

    # 添加说明sheet
    ws_info = wb.create_sheet('填写说明')
    ws_info['A1'] = '填写说明'
    ws_info['A1'].font = Font(name='微软雅黑', size=14, bold=True)
    ws_info['A3'] = '1. 带 * 号的字段为必填项'
    ws_info['A4'] = '2. 日期格式请使用 YYYY-MM-DD（如 2026-12-31）'
    ws_info['A5'] = '3. 状态字段请从下拉选项中选择，或填写说明中的有效值'
    ws_info['A6'] = '4. 第2行为示例数据，导入前请删除或替换'
    ws_info['A7'] = '5. 数量字段请填写正整数'
    ws_info['A8'] = '6. ID字段（如product_id、version_id）请填写系统中已存在的ID'
    ws_info.column_dimensions['A'].width = 60

    wb.save(output_path)
    return output_path


def import_from_excel(table_name, file_path, db):
    """
    解析Excel文件并批量导入数据
    :param table_name: 表名
    :param file_path: Excel文件路径
    :param db: 数据库连接
    :return: (成功数量, 失败数量, 错误信息列表)
    """
    if table_name not in TABLE_CONFIG:
        raise ValueError(f'不支持的表名: {table_name}')

    config = TABLE_CONFIG[table_name]
    wb = load_workbook(file_path, read_only=True, data_only=True)
    ws = wb.active

    # 读取表头，建立字段映射
    headers = {}
    for col_idx, cell in enumerate(ws[1], 1):
        header_text = str(cell.value or '').strip()
        # 去掉 * 号
        header_text = header_text.replace('*', '')
        # 查找对应的字段名
        for field, label in config['field_labels'].items():
            label_clean = label.replace('*', '')
            if header_text == label_clean or header_text == field:
                headers[col_idx] = field
                break

    success_count = 0
    fail_count = 0
    errors = []

    # 从第2行开始读取数据（跳过表头和示例行？不，示例行也可能是用户修改后的数据）
    for row_idx, row in enumerate(ws.iter_rows(min_row=2, values_only=True), 2):
        # 跳过空行
        if all(v is None or str(v).strip() == '' for v in row):
            continue

        # 构建行数据
        row_data = {}
        for col_idx, value in enumerate(row, 1):
            if col_idx in headers:
                field = headers[col_idx]
                if value is not None:
                    row_data[field] = str(value).strip()
                else:
                    row_data[field] = ''

        # 校验必填字段
        missing_fields = []
        for field in config['required_fields']:
            if not row_data.get(field):
                missing_fields.append(config['field_labels'].get(field, field))
        if missing_fields:
            fail_count += 1
            errors.append(f'第{row_idx}行：缺少必填字段 {", ".join(missing_fields)}')
            continue

        # 应用默认值
        for field, default in config.get('field_defaults', {}).items():
            if not row_data.get(field):
                row_data[field] = default

        # 字段校验
        validation_error = None
        for field, validator in config.get('field_validators', {}).items():
            if row_data.get(field):
                try:
                    if not validator(row_data[field]):
                        validation_error = f'字段 {config["field_labels"].get(field, field)} 值无效: {row_data[field]}'
                        break
                except Exception as e:
                    validation_error = f'字段 {config["field_labels"].get(field, field)} 格式错误: {e}'
                    break
        if validation_error:
            fail_count += 1
            errors.append(f'第{row_idx}行：{validation_error}')
            continue

        # 构建INSERT语句
        fields = [f for f in config['import_fields'] if f in row_data]
        placeholders = ', '.join(['?' for _ in fields])
        field_names = ', '.join(fields)
        values = [row_data[f] for f in fields]

        try:
            db.execute(f'INSERT INTO {table_name} ({field_names}) VALUES ({placeholders})', values)
            success_count += 1
        except Exception as e:
            fail_count += 1
            errors.append(f'第{row_idx}行：数据库错误 - {str(e)[:100]}')

    db.commit()
    wb.close()
    return success_count, fail_count, errors
