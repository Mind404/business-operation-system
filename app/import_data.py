# -*- coding: utf-8 -*-
"""
命令行数据导入工具
用法:
  python import_data.py --table requirements --file data.csv    # 从CSV导入到指定表
  python import_data.py --table products --file data.json       # 从JSON导入
  python import_data.py --sql backup.sql                        # 从SQL恢复全库
"""
import os
import sys
import argparse
from data_utils import (get_db, BUSINESS_TABLES, TABLE_NAMES_CN,
                         import_table_csv, import_table_json, import_sql)

def main():
    parser = argparse.ArgumentParser(description='业务系统数据导入工具')
    parser.add_argument('--table', choices=BUSINESS_TABLES, default=None,
                        help='目标表名（CSV/JSON导入时必填）')
    parser.add_argument('--file', required=True, help='导入文件路径')
    parser.add_argument('--sql', action='store_true', help='从SQL文件恢复全库')
    args = parser.parse_args()

    if not os.path.exists(args.file):
        print(f'[错误] 文件不存在: {args.file}')
        sys.exit(1)

    db = get_db()

    if args.sql:
        # SQL全库恢复
        print('[警告] SQL恢复将覆盖现有数据，建议先备份！')
        confirm = input('确认继续？(yes/no): ')
        if confirm.lower() != 'yes':
            print('已取消')
            db.close()
            return
        with open(args.file, 'r', encoding='utf-8') as f:
            sql_content = f.read()
        try:
            import_sql(db, sql_content)
            print(f'[完成] 全库已从 {args.file} 恢复')
        except Exception as e:
            print(f'[失败] SQL恢复出错: {e}')
            sys.exit(1)
    else:
        # CSV/JSON单表导入
        if not args.table:
            print('[错误] 请使用 --table 指定目标表')
            sys.exit(1)

        with open(args.file, 'r', encoding='utf-8') as f:
            content = f.read()

        ext = os.path.splitext(args.file)[1].lower()
        try:
            if ext == '.csv':
                imported, errors = import_table_csv(db, args.table, content)
            elif ext == '.json':
                imported, errors = import_table_json(db, args.table, content)
            else:
                print(f'[错误] 不支持的文件格式: {ext}（仅支持 .csv / .json）')
                sys.exit(1)

            print(f'[完成] 已导入 {imported} 条记录到 {TABLE_NAMES_CN.get(args.table, args.table)}')
            if errors:
                print(f'[警告] {len(errors)} 条记录导入失败:')
                for err in errors[:5]:
                    print(f'  - {err}')
                if len(errors) > 5:
                    print(f'  ... 还有 {len(errors) - 5} 条错误')
        except Exception as e:
            print(f'[失败] 导入出错: {e}')
            sys.exit(1)

    db.close()

if __name__ == '__main__':
    main()
