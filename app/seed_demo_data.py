# -*- coding: utf-8 -*-
"""
演示数据播种脚本（v11.1.2）
- 默认执行：向当前 business.db 写入带【演示】标记的虚拟数据（WBS 项目/任务树、需求、待审批），
  覆盖 AI 助手 5 类预警（WBS逾期 / 需求逾期 / 需求临期 / 呆滞需求 / 待审批）。
- 幂等：先删除既有【演示】数据再插入，重复运行不会产生重复。
- --clean：仅删除全部【演示】数据，恢复干净库。
用法：
    python3 seed_demo_data.py            # 播种
    python3 seed_demo_data.py --clean    # 清除演示数据
"""
import os
import sys
import sqlite3
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get('BIZ_DB', os.path.join(HERE, 'business.db'))


def today_iso(offset_days=0):
    return (datetime.now() + timedelta(days=offset_days)).strftime('%Y-%m-%d')


def now_sql(offset_days=0):
    return (datetime.now() + timedelta(days=offset_days)).strftime('%Y-%m-%d %H:%M:%S')


def clean_demo(db):
    n = 0
    # 先删 WBS 任务（外键关联项目）
    rows = db.execute("SELECT id FROM wbs_projects WHERE name LIKE '%【演示】%'").fetchall()
    ids = [r[0] for r in rows]
    if ids:
        marks = ','.join('?' * len(ids))
        n += db.execute(f"DELETE FROM wbs_tasks WHERE project_id IN ({marks})", ids).rowcount
        n += db.execute(f"DELETE FROM wbs_projects WHERE id IN ({marks})", ids).rowcount
    req_ids = [r[0] for r in db.execute("SELECT id FROM req_items WHERE title LIKE '%【演示】%'").fetchall()]
    if req_ids:
        marks = ','.join('?' * len(req_ids))
        n += db.execute(f"DELETE FROM req_reviews WHERE req_id IN ({marks})", req_ids).rowcount
        n += db.execute(f"DELETE FROM req_changes WHERE req_id IN ({marks})", req_ids).rowcount
        n += db.execute(f"DELETE FROM req_items WHERE id IN ({marks})", req_ids).rowcount
    n += db.execute("DELETE FROM approval_requests WHERE payload LIKE '%【演示】%'").rowcount
    db.commit()
    return n


def seed(db):
    # —— WBS 项目（含逾期任务）——
    db.execute('''INSERT INTO wbs_projects (name, description, owner, start_date, end_date, status,
                  created_at, updated_at, participants, approver, approval_status)
                  VALUES (?,?,?,?,?,?,?,?,?,?,?)''',
               (f'SAM302模组软件专题【演示】', '演示数据：软件专题项目，用于试验 AI 助手预警与问答（可在列表中删除）。',
                'admin', today_iso(-30), today_iso(30), '进行中',
                now_sql(-30), now_sql(-1), 'admin、product、quality', 'admin', '已通过'))
    pid = db.execute('SELECT last_insert_rowid()').fetchone()[0]
    tasks = [
        # (wbs_code, parent, name, work_content, owner, collab, start, end, deliverable, status, progress, sort, priority)
        ('1', None, '需求与架构设计', '模块需求梳理与总体架构设计', 'admin', 'product', today_iso(-20), today_iso(-1), '需求规格书', '进行中', 80, 1, '高'),
        ('1.1', '1', '需求调研', '客户需求调研与用例整理', 'product', 'admin', today_iso(-18), today_iso(-3), '调研报告', '进行中', 90, 1, '高'),
        ('1.2', '1', '架构评审', '总体架构方案评审', 'admin', 'quality', today_iso(-15), today_iso(2), '评审纪要', '未开始', 0, 2, '中'),
        ('2', None, '软件编码开发', '固件开发与接口实现', 'admin', 'product', today_iso(-10), today_iso(15), '固件版本', '进行中', 45, 2, '高'),
        ('2.1', '2', '通信协议实现', 'AT 指令集与透传协议实现', 'product', 'admin', today_iso(-8), today_iso(10), '协议代码', '进行中', 60, 1, '高'),
        ('2.2', '2', '业务逻辑开发', '核心业务逻辑与状态机', 'admin', 'product', today_iso(1), today_iso(15), '业务代码', '未开始', 0, 2, '中'),
        ('3', None, '测试验证', '单元/集成/验收测试', 'quality', 'admin', today_iso(-5), today_iso(20), '测试报告', '未开始', 0, 3, '中'),
    ]
    parent_map = {}
    for wbs_code, parent_code, name, wc, owner, collab, ps, pe, dlv, st, prog, so, pri in tasks:
        db.execute('''INSERT INTO wbs_tasks (project_id, wbs_code, parent_id, name, work_content, owner,
                      collaborators, plan_start, plan_end, deliverable, status, progress, sort_order, priority,
                      participants, notes, approval_status)
                      VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                   (pid, wbs_code, parent_map.get(parent_code), name, wc, owner, collab,
                    ps, pe, dlv, st, prog, so, pri, collab, '演示任务', '已通过'))
        parent_map[wbs_code] = db.execute('SELECT last_insert_rowid()').fetchone()[0]

    # —— 需求（覆盖 逾期 / 临期7天 / 呆滞>14天 / 正常）——
    reqs = [
        # (title, type, source, customer, desc, qty, pri, delivery, owner, status, created_by, created_off, updated_off)
        ('量产固件升级需求【演示】', '客户需求', '应用侧', '客户A', '客户反馈 V3.2 固件存在偶发断链，需升级版本。', 120,
         '高', today_iso(-1), 'admin', '进行中', 'admin', -25, -1),
        ('测试工装改进需求【演示】', '内部需求', '测试部', '测试组', '现有测试工装不支持批量压测，需改进。', 2,
         '中', today_iso(3), 'product', '待评审', 'product', -3, -3),
        ('SAM302 样机送样【演示】', '领导任务', '管理组', '领导层', '向重点客户提供 SAM302 样机及配套文档。', 10,
         '高', today_iso(15), 'admin', '进行中', 'admin', -22, -20),
        ('产测软件对接【演示】', '集团系统侧', '研发部', '集团平台组', '产测软件与集团数据平台对接联调。', 1,
         '中', today_iso(12), 'product', '进行中', 'product', -6, -2),
        ('历史需求归档【演示】', '用户需求', '应用侧', '客户B', '已完成并关闭的历史需求（不触发预警）。', 5,
         '低', today_iso(-40), 'admin', '已完成', 'admin', -60, -50),
    ]
    year = datetime.now().strftime('%Y')
    for i, (title, rtype, dept, cust, desc, qty, pri, dlv, owner, st, cb, co, uo) in enumerate(reqs, 1):
        # 用真实编号格式 RQ-YYYY-XXX，保证 AI 引用正则与原始记录链接可命中
        code = f'RQ-{year}-{i:03d}'
        db.execute('''INSERT INTO req_items (code, title, req_type, source_dept, customer, description,
                      quantity, priority, delivery_date, owner, status, created_by, created_at, updated_at, is_deleted)
                      VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,0)''',
                   (code, title, rtype, dept, cust, desc, qty, pri,
                    dlv, owner, st, cb, now_sql(co), now_sql(uo)))
        rid = db.execute('SELECT last_insert_rowid()').fetchone()[0]
        # 评审记录（req_reviews 实际列：reviewer/review_date/conclusion/opinion/created_at）
        db.execute('''INSERT INTO req_reviews (req_id, reviewer, review_date, conclusion, opinion, created_at)
                      VALUES (?,?,?,?,?,?)''',
                   (rid, 'admin', today_iso(0), '通过', f'{title} 演示评审意见', now_sql(co)))
        # 状态变更历史（req_changes 实际列：field_name/old_value/new_value/change_by/approval_id/change_at）
        db.execute('''INSERT INTO req_changes (req_id, field_name, old_value, new_value, change_by, approval_id, change_at)
                      VALUES (?,?,?,?,?,?,?)''',
                   (rid, 'status', '待评审', st, cb, None, now_sql(co)))

    # —— 待审批请求 ——
    uid = db.execute("SELECT id FROM users WHERE username='admin'").fetchone()[0]
    db.execute('''INSERT INTO approval_requests (plugin_id, record_type, record_id, requester_id, action,
                  payload, status, reviewer_id, created_at, current_step, flow_id)
                  VALUES (?,?,?,?,?,?,?,?,?,?,?)''',
               ('requirements', 'req', 2, uid, 'status_change',
                '{"title": "测试工装改进需求【演示】", "from": "待评审", "to": "已通过"}',
                '待审批', uid, now_sql(-1), 1, None))
    db.commit()
    return len(tasks), len(reqs)


def main():
    db = sqlite3.connect(DB_PATH)
    try:
        if '--clean' in sys.argv:
            n = clean_demo(db)
            print(f'已清除演示数据 {n} 行，数据库恢复干净状态。')
            return
        removed = clean_demo(db)  # 幂等：先清后插
        n_tasks, n_reqs = seed(db)
        print(f'演示数据播种完成（清除旧演示 {removed} 行）')
        print(f'  - WBS 项目 1 个，任务 {n_tasks} 条（含 2 条逾期）')
        print(f'  - 需求 {n_reqs} 条（含 逾期1 / 临期1 / 呆滞1 / 正常2）')
        print(f'  - 待审批请求 1 条')
        print(f'  数据库: {DB_PATH}')
        print('提示：所有演示数据带【演示】标记，正式使用时可在对应列表删除，或运行 python3 seed_demo_data.py --clean 一键清除。')
    finally:
        db.close()


if __name__ == '__main__':
    main()
