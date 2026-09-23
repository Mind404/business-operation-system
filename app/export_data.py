# -*- coding: utf-8 -*-
"""
命令行数据导出工具
用法:
  python export_data.py --format json          # 导出全库JSON（适合AI训练）
  python export_data.py --format csv           # 导出全库CSV压缩包
  python export_data.py --format sql           # 导出SQL备份
  python export_data.py --table requirements --format csv  # 导出单表CSV
  python export_data.py --output ./backup      # 指定输出目录
"""
import os
import sys
import argparse
from datetime import datetime
from data_utils import (get_db, BUSINESS_TABLES, TABLE_NAMES_CN,
                         export_all_json, export_all_csv_zip, export_sql,
                         export_table_csv, export_table_json, get_data_statistics)

def main():
    parser = argparse.ArgumentParser(description='业务系统数据导出工具')
    parser.add_argument('--format', choices=['json', 'csv', 'sql'], default='json',
                        help='导出格式（默认json）')
    parser.add_argument('--table', choices=BUSINESS_TABLES, default=None,
                        help='导出指定表（不指定则导出全库）')
    parser.add_argument('--output', default='.', help='输出目录（默认当前目录）')
    args = parser.parse_args()

    # 确保输出目录存在
    os.makedirs(args.output, exist_ok=True)

    db = get_db()
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')

    if args.table:
        # 单表导出
        if args.format == 'csv':
            content = export_table_csv(db, args.table)
            ext = 'csv'
        else:
            content = export_table_json(db, args.table)
            ext = 'json'
        filename = f'{args.table}_{timestamp}.{ext}'
        filepath = os.path.join(args.output, filename)
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(content)
        print(f'[完成] 表 {TABLE_NAMES_CN.get(args.table, args.table)} 已导出: {filepath}')
        print(f'       文件大小: {os.path.getsize(filepath) / 1024:.1f} KB')
    else:
        # 全库导出
        if args.format == 'json':
            content = export_all_json(db)
            filename = f'business_data_{timestamp}.json'
            filepath = os.path.join(args.output, filename)
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(content)
        elif args.format == 'csv':
            content = export_all_csv_zip(db)
            filename = f'business_data_{timestamp}.zip'
            filepath = os.path.join(args.output, filename)
            with open(filepath, 'wb') as f:
                f.write(content)
        else:  # sql
            content = export_sql(db)
            filename = f'business_backup_{timestamp}.sql'
            filepath = os.path.join(args.output, filename)
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(content)

        stats = get_data_statistics(db)
        print(f'[完成] 全库{args.format.upper()}导出: {filepath}')
        print(f'       文件大小: {os.path.getsize(filepath) / 1024:.1f} KB')
        print(f'       数据表: {stats["overview"]["total_tables"]} 张')
        print(f'       总记录: {stats["overview"]["total_records"]:,} 条')

    db.close()

if __name__ == '__main__':
    main()
