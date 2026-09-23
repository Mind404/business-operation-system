# -*- coding: utf-8 -*-
"""
数据导出导入核心工具
支持格式：CSV、JSON、SQL
用途：数据沉淀、AI训练、数据分析、备份恢复
"""
import os
import csv
import json
import sqlite3
import zipfile
from datetime import datetime
from io import StringIO, BytesIO

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'business.db')

# 业务表列表（按业务流程排序）
BUSINESS_TABLES = [
    'requirements',        # 需求数据库
    'products',            # 产品规划
    'bom_items',           # BOM物料清单
    'inventory',           # 库存管理
    'material_requisitions',  # 物料领用单
    'requisition_items',   # 领用单明细
    'production_orders',   # 生产订单
    'quality_inspections', # 质量检验
    'defect_handling',     # 不合格品处理
    'warehouse_receipts',  # 入库记录
    'deliveries',          # 交付记录
    'software_versions',   # 软件版本
    'version_requirements', # 版本需求关联
    'dev_tasks',           # 开发任务
    'bug_records',         # Bug记录
    'bug_workflow',        # Bug流转记录
    'production_schedules', # 生产排程
    'production_processes', # 生产工序
    'material_plans',      # 物料需求计划
    'operation_logs',      # 操作日志
]

# 表中文名称映射
TABLE_NAMES_CN = {
    'requirements': '需求数据库',
    'products': '产品规划',
    'bom_items': 'BOM物料清单',
    'inventory': '库存管理',
    'material_requisitions': '物料领用单',
    'requisition_items': '领用单明细',
    'production_orders': '生产订单',
    'quality_inspections': '质量检验',
    'defect_handling': '不合格品处理',
    'warehouse_receipts': '入库记录',
    'deliveries': '交付记录',
    'software_versions': '软件版本',
    'version_requirements': '版本需求关联',
    'dev_tasks': '开发任务',
    'bug_records': 'Bug记录',
    'bug_workflow': 'Bug流转记录',
    'production_schedules': '生产排程',
    'production_processes': '生产工序',
    'material_plans': '物料需求计划',
    'operation_logs': '操作日志',
}

# 字段中文说明（供数据字典和AI理解）
FIELD_DESCRIPTIONS = {
    'id': '唯一标识ID（自增主键）',
    'title': '标题/名称',
    'source_dept': '需求来源部门',
    'customer': '客户名称',
    'description': '详细描述',
    'quantity': '数量',
    'priority': '优先级（高/中/低）',
    'status': '状态',
    'delivery_date': '交付日期',
    'created_at': '创建时间',
    'updated_at': '更新时间',
    'name': '名称',
    'model': '产品型号',
    'version': '版本号',
    'lifecycle_stage': '生命周期阶段（规划中/研发中/量产中/维护中）',
    'stocking_strategy': '备货策略',
    'product_id': '关联产品ID',
    'material_name': '物料名称',
    'material_code': '物料编码',
    'is_key_material': '是否关键物料（1=是，0=否）',
    'unit': '计量单位',
    'quantity_per_unit': '单台用量',
    'supplier': '供应商',
    'location': '库存位置（公司库房/供应商暂存）',
    'remark': '备注',
    'requisition_no': '领用单号',
    'requirement_id': '关联需求ID',
    'applicant': '申请人',
    'total_quantity': '领用总数量',
    'approved_at': '审批时间',
    'requisition_id': '关联领用单ID',
    'order_no': '生产订单号',
    'plan_start': '计划开始日期',
    'plan_finish': '计划完成日期',
    'actual_finish': '实际完成日期',
    'wip_progress': '生产进度（0-100%）',
    'inspection_no': '检验单号',
    'inspection_type': '检验类型（IQC来料/IPQC制程/OQC出货）',
    'production_order_id': '关联生产订单ID',
    'inspector': '检验员',
    'sample_quantity': '抽样数量',
    'pass_quantity': '合格数量',
    'fail_quantity': '不合格数量',
    'pass_rate': '良品率（百分比）',
    'result': '检验结果（合格/不合格）',
    'defect_description': '缺陷描述',
    'inspection_date': '检验日期',
    'inspection_id': '关联检验单ID',
    'defect_type': '缺陷类型',
    'root_cause': '根本原因',
    'handling_method': '处理方式',
    'responsible_person': '责任人',
    'closed_at': '关闭时间',
    'receipt_no': '入库单号',
    'quality_status': '质量状态（合格/让步接收）',
    'approver': '审批人',
    'delivery_no': '交付单号',
    'market_team': '市场团队',
    'purpose': '用途',
    'delivery_date': '交付日期',
    'feedback': '客户反馈',
    'module': '操作模块',
    'action': '操作类型',
    'detail': '操作详情',
    'operator': '操作人',
}

def get_field_desc(table_name, field_name):
    """获取字段中文说明"""
    return FIELD_DESCRIPTIONS.get(field_name, field_name)

def get_db():
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    return db

def get_table_columns(db, table_name):
    """获取表的字段信息"""
    cols = db.execute(f'PRAGMA table_info({table_name})').fetchall()
    return [{'name': c['name'], 'type': c['type'], 'notnull': c['notnull'], 'default': c['dflt_value']} for c in cols]

def get_table_count(db, table_name):
    """获取表记录数"""
    return db.execute(f'SELECT COUNT(*) FROM {table_name}').fetchone()[0]

# ============================================================
# 导出功能
# ============================================================
def export_table_csv(db, table_name):
    """导出单表为CSV字符串"""
    columns = get_table_columns(db, table_name)
    col_names = [c['name'] for c in columns]
    rows = db.execute(f'SELECT * FROM {table_name} ORDER BY id').fetchall()

    output = StringIO()
    writer = csv.writer(output)
    writer.writerow(col_names)  # 表头
    for row in rows:
        writer.writerow([row[c] if row[c] is not None else '' for c in col_names])
    return output.getvalue()

def export_table_json(db, table_name):
    """导出单表为JSON字符串"""
    columns = get_table_columns(db, table_name)
    col_names = [c['name'] for c in columns]
    rows = db.execute(f'SELECT * FROM {table_name} ORDER BY id').fetchall()

    data = []
    for row in rows:
        item = {}
        for c in col_names:
            val = row[c]
            # 处理日期等特殊类型
            if isinstance(val, bytes):
                val = val.hex()
            item[c] = val
        data.append(item)
    return json.dumps(data, ensure_ascii=False, indent=2)

def export_all_json(db):
    """导出全库为结构化JSON（适合AI训练）"""
    result = {
        'export_info': {
            'export_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'database': 'business.db',
            'system': 'AI驱动的业务高效运转系统',
            'table_count': len(BUSINESS_TABLES),
        },
        'data_dictionary': {},
        'tables': {}
    }

    total_records = 0
    for table in BUSINESS_TABLES:
        columns = get_table_columns(db, table)
        count = get_table_count(db, table)
        total_records += count

        # 数据字典
        result['data_dictionary'][table] = {
            'name_cn': TABLE_NAMES_CN.get(table, table),
            'record_count': count,
            'fields': [{'name': c['name'], 'type': c['type'], 'required': bool(c['notnull'])} for c in columns]
        }

        # 数据
        result['tables'][table] = json.loads(export_table_json(db, table))

    result['export_info']['total_records'] = total_records
    return json.dumps(result, ensure_ascii=False, indent=2)

def export_all_csv_zip(db):
    """导出全库为CSV压缩包（每个表一个CSV文件）"""
    zip_buffer = BytesIO()
    with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
        # 导出说明
        readme = f"""业务系统数据导出
导出时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
数据库: business.db

包含表（{len(BUSINESS_TABLES)}个）:
"""
        for table in BUSINESS_TABLES:
            count = get_table_count(db, table)
            readme += f"  - {table}.csv ({TABLE_NAMES_CN.get(table, table)}): {count}条记录\n"
        zf.writestr('README.txt', readme)

        # 每个表导出CSV
        for table in BUSINESS_TABLES:
            csv_content = export_table_csv(db, table)
            zf.writestr(f'{table}.csv', csv_content)

    return zip_buffer.getvalue()

def export_sql(db):
    """导出全库为SQL（备份用）"""
    output = []
    output.append('-- ============================================================')
    output.append('-- AI驱动的业务高效运转系统 - 数据库备份')
    output.append(f'-- 导出时间: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}')
    output.append('-- ============================================================')
    output.append('')
    output.append('PRAGMA foreign_keys = OFF;')
    output.append('BEGIN TRANSACTION;')
    output.append('')

    for table in BUSINESS_TABLES:
        # 建表语句
        create_sql = db.execute(f"SELECT sql FROM sqlite_master WHERE type='table' AND name='{table}'").fetchone()
        if create_sql and create_sql[0]:
            output.append(f'-- 表: {table} ({TABLE_NAMES_CN.get(table, table)})')
            output.append(f'DROP TABLE IF EXISTS {table};')
            output.append(create_sql[0] + ';')
            output.append('')

            # 数据
            columns = get_table_columns(db, table)
            col_names = [c['name'] for c in columns]
            rows = db.execute(f'SELECT * FROM {table}').fetchall()
            for row in rows:
                values = []
                for c in col_names:
                    val = row[c]
                    if val is None:
                        values.append('NULL')
                    elif isinstance(val, (int, float)):
                        values.append(str(val))
                    else:
                        escaped = str(val).replace("'", "''")
                        values.append(f"'{escaped}'")
                output.append(f"INSERT INTO {table} ({', '.join(col_names)}) VALUES ({', '.join(values)});")
            output.append('')

    output.append('COMMIT;')
    output.append('PRAGMA foreign_keys = ON;')
    return '\n'.join(output)

# ============================================================
# 导入功能
# ============================================================
def import_table_csv(db, table_name, csv_content):
    """从CSV导入单表数据"""
    reader = csv.DictReader(StringIO(csv_content))
    columns = get_table_columns(db, table_name)
    col_names = [c['name'] for c in columns]

    imported = 0
    errors = []
    for i, row in enumerate(reader, 1):
        try:
            # 只保留表中存在的字段
            values = {}
            for c in col_names:
                if c in row and row[c] != '':
                    values[c] = row[c]
                elif c == 'id':
                    continue  # id自增
            if not values:
                continue

            cols = list(values.keys())
            placeholders = ', '.join(['?' for _ in cols])
            db.execute(f"INSERT INTO {table_name} ({', '.join(cols)}) VALUES ({placeholders})",
                       [values[c] for c in cols])
            imported += 1
        except Exception as e:
            errors.append(f'第{i}行: {str(e)}')

    db.commit()
    return imported, errors

def import_table_json(db, table_name, json_content):
    """从JSON导入单表数据"""
    data = json.loads(json_content)
    if not isinstance(data, list):
        raise ValueError('JSON格式错误，应为数组')

    columns = get_table_columns(db, table_name)
    col_names = [c['name'] for c in columns]

    imported = 0
    errors = []
    for i, item in enumerate(data, 1):
        try:
            values = {}
            for c in col_names:
                if c in item and item[c] is not None:
                    values[c] = item[c]
            if not values:
                continue
            cols = list(values.keys())
            placeholders = ', '.join(['?' for _ in cols])
            db.execute(f"INSERT INTO {table_name} ({', '.join(cols)}) VALUES ({placeholders})",
                       [values[c] for c in cols])
            imported += 1
        except Exception as e:
            errors.append(f'第{i}条: {str(e)}')

    db.commit()
    return imported, errors

def import_sql(db, sql_content):
    """从SQL恢复全库"""
    db.executescript(sql_content)
    db.commit()
    return True

# ============================================================
# 数据统计（供AI分析）
# ============================================================
def get_data_statistics(db):
    """获取全库数据统计概览"""
    stats = {
        'overview': {},
        'tables': {},
        'business_metrics': {}
    }

    total = 0
    for table in BUSINESS_TABLES:
        count = get_table_count(db, table)
        total += count
        stats['tables'][table] = {
            'name_cn': TABLE_NAMES_CN.get(table, table),
            'record_count': count
        }
    stats['overview']['total_tables'] = len(BUSINESS_TABLES)
    stats['overview']['total_records'] = total

    # 业务指标
    stats['business_metrics'] = {
        'requirements_total': get_table_count(db, 'requirements'),
        'requirements_pending': db.execute("SELECT COUNT(*) FROM requirements WHERE status='待评审'").fetchone()[0],
        'products_total': get_table_count(db, 'products'),
        'products_in_production': db.execute("SELECT COUNT(*) FROM products WHERE lifecycle_stage='量产中'").fetchone()[0],
        'inventory_items': get_table_count(db, 'inventory'),
        'inventory_low_stock': db.execute("SELECT COUNT(*) FROM inventory WHERE quantity < 50").fetchone()[0],
        'production_orders_total': get_table_count(db, 'production_orders'),
        'production_active': db.execute("SELECT COUNT(*) FROM production_orders WHERE status IN ('生产中','待排产')").fetchone()[0],
        'quality_inspections_total': get_table_count(db, 'quality_inspections'),
        'quality_pass_rate': db.execute("SELECT ROUND(AVG(pass_rate), 2) FROM quality_inspections WHERE pass_rate IS NOT NULL").fetchone()[0] or 0,
        'defects_total': get_table_count(db, 'defect_handling'),
        'defects_open': db.execute("SELECT COUNT(*) FROM defect_handling WHERE status != '已关闭'").fetchone()[0],
        'warehouse_receipts_total': get_table_count(db, 'warehouse_receipts'),
        'deliveries_total': get_table_count(db, 'deliveries'),
        'operation_logs_total': get_table_count(db, 'operation_logs'),
    }

    return stats

if __name__ == '__main__':
    # 快速测试
    db = get_db()
    stats = get_data_statistics(db)
    print(json.dumps(stats, ensure_ascii=False, indent=2))
    db.close()
