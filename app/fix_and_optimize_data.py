#!/usr/bin/env python3
"""数据修复与优化脚本 - 按照真实业务逻辑重构数据"""
import sqlite3
from datetime import datetime

DB_PATH = 'business.db'

def fix_data():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    print("=" * 60)
    print("开始数据修复与优化")
    print("=" * 60)

    # 1. 清理无效的测试执行记录（version_id不存在的）
    print("\n1. 清理无效测试执行记录...")
    cur.execute("SELECT id FROM software_versions")
    valid_version_ids = [r['id'] for r in cur.fetchall()]
    print(f"   有效版本ID: {valid_version_ids}")

    cur.execute("SELECT id, execution_no, version_id FROM test_executions")
    all_tests = cur.fetchall()
    invalid_count = 0
    for t in all_tests:
        if t['version_id'] not in valid_version_ids:
            print(f"   删除无效记录: {t['execution_no']} (version_id={t['version_id']})")
            cur.execute("DELETE FROM test_executions WHERE id=?", (t['id'],))
            invalid_count += 1
    print(f"   共删除 {invalid_count} 条无效记录")

    # 2. 优化V1.5版本(SAM302, version_id=2)的测试任务，贴合真实业务时间线
    print("\n2. 优化V1.5版本测试任务时间线...")
    # 真实业务：
    # - 9月13日研发提供V1.5模组版本，进行黑盒测试与bug提单
    # - 9月18日完成催熟版本并通过测试确认可发布状态

    # 先删除version_id=2的旧测试记录，重新创建符合时间线的记录
    cur.execute("DELETE FROM test_executions WHERE version_id=2")
    print("   已删除V1.5旧测试记录")

    # 创建新的测试执行记录
    v15_tests = [
        {
            'execution_no': 'TE-V1.5-R1-黑盒测试',
            'round_no': 1,
            'start_date': '2026-09-13',
            'end_date': '2026-09-15',
            'total_cases': 48,
            'passed_cases': 42,
            'failed_cases': 6,
            'status': '已完成',
            'version_id': 2,
            'summary': 'V1.5版本第一轮黑盒测试，9月13日研发提供版本，执行48个测试用例，通过42个，发现6个bug（含鉴权失败、低功耗丢包、功率控制精度等问题），已提交bug单'
        },
        {
            'execution_no': 'TE-V1.5-R2-回归测试',
            'round_no': 2,
            'start_date': '2026-09-16',
            'end_date': '2026-09-17',
            'total_cases': 48,
            'passed_cases': 46,
            'failed_cases': 2,
            'status': '进行中',
            'version_id': 2,
            'summary': 'V1.5版本第二轮回归测试，针对第一轮发现的bug进行修复验证，48个用例通过46个，剩余2个低优先级问题待最终确认'
        },
        {
            'execution_no': 'TE-V1.5-R3-版本催熟验证',
            'round_no': 3,
            'start_date': '2026-09-17',
            'end_date': '2026-09-18',
            'total_cases': 48,
            'passed_cases': 48,
            'failed_cases': 0,
            'status': '待开始',
            'version_id': 2,
            'summary': 'V1.5版本催熟验证，实网环境下72小时稳定性测试，确认版本具备可发布状态，计划9月18日完成并通过测试确认'
        }
    ]

    for t in v15_tests:
        cur.execute("""
            INSERT INTO test_executions (execution_no, round_no, start_date, end_date,
                total_cases, passed_cases, status, version_id, summary, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (t['execution_no'], t['round_no'], t['start_date'], t['end_date'],
              t['total_cases'], t['passed_cases'], t['status'], t['version_id'],
              t['summary'], datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
        print(f"   创建: {t['execution_no']} ({t['start_date']} ~ {t['end_date']})")

    # 3. 优化V1.3版本(SAM302, version_id=1)的测试记录
    print("\n3. 优化V1.3版本测试记录...")
    cur.execute("DELETE FROM test_executions WHERE version_id=1")
    v13_tests = [
        {
            'execution_no': 'TE-V1.3-R1-功能测试',
            'round_no': 1,
            'start_date': '2026-08-25',
            'end_date': '2026-09-05',
            'total_cases': 32,
            'passed_cases': 32,
            'status': '已完成',
            'version_id': 1,
            'summary': 'V1.3版本功能测试，32个用例全部通过，验证基础通信协议、数据上报、设备管理、OTA升级等功能'
        },
        {
            'execution_no': 'TE-V1.3-R2-发布验证',
            'round_no': 2,
            'start_date': '2026-09-08',
            'end_date': '2026-09-10',
            'total_cases': 32,
            'passed_cases': 32,
            'status': '已完成',
            'version_id': 1,
            'summary': 'V1.3版本发布验证，实网环境测试通过，9月10日正式发布，具备V1.3功能和OTA远程升级功能'
        }
    ]
    for t in v13_tests:
        cur.execute("""
            INSERT INTO test_executions (execution_no, round_no, start_date, end_date,
                total_cases, passed_cases, status, version_id, summary, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (t['execution_no'], t['round_no'], t['start_date'], t['end_date'],
              t['total_cases'], t['passed_cases'], t['status'], t['version_id'],
              t['summary'], datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
        print(f"   创建: {t['execution_no']}")

    # 4. 关联delivery_batches与production_schedules
    print("\n4. 关联分批交付与生产排程...")
    cur.execute("UPDATE delivery_batches SET production_schedule_id=1 WHERE id=1")
    cur.execute("UPDATE delivery_batches SET production_schedule_id=2 WHERE id=2")
    cur.execute("UPDATE delivery_batches SET production_schedule_id=3 WHERE id=3")
    print("   已关联3条分批交付记录与生产排程")

    # 5. 更新生产排程第2批状态为"待生产"（因为9月20日才开始）
    print("\n5. 更新生产排程状态...")
    cur.execute("UPDATE production_schedules SET status='待生产' WHERE id=2")
    cur.execute("UPDATE production_schedules SET status='规划中' WHERE id=3")
    print("   第2批更新为'待生产'，第3批更新为'规划中'")

    # 6. 确保研发任务状态与时间线一致
    print("\n6. 校准研发任务状态...")
    # 今天是2026-09-14
    # V1.5新功能开发（闭环功率控制、空闲态调度）应该已完成
    cur.execute("UPDATE dev_tasks SET status='已完成' WHERE id IN (5,6)")
    # 紧急呼叫、异网漫游应该进行中
    cur.execute("UPDATE dev_tasks SET status='进行中' WHERE id IN (7,8)")
    print("   闭环功率控制、空闲态调度 -> 已完成")
    print("   紧急呼叫、异网漫游 -> 进行中")

    # 7. 更新V1.5版本状态为"测试中"
    cur.execute("UPDATE software_versions SET status='测试中' WHERE id=2")
    print("   V1.5版本状态 -> 测试中")

    conn.commit()
    conn.close()

    print("\n" + "=" * 60)
    print("数据修复与优化完成！")
    print("=" * 60)

if __name__ == '__main__':
    fix_data()
