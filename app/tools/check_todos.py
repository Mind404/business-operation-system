#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""「我的待办」自查工具（只读，不改任何数据）

用途：任务明明归我，工作台却看不到，跑一下就知道卡在哪一步。

用法：
    python tools/check_todos.py --db business.db            # 全部用户逐个检查
    python tools/check_todos.py --db business.db --user lisa # 只看某个人

判定口径（与 WBS 插件 my_todos 完全一致）：
    1) 负责人 = 该用户的账号，或 = 该用户的显示名
    2) 状态不是「已完成 / 已取消」
    3) 审批状态为「已通过」（空值也按已通过）
    4) 该行没有子任务（有子任务的视为阶段行，不算个人待办）
"""
import argparse
import sqlite3
import sys
from collections import Counter


def build_parser():
    p = argparse.ArgumentParser(description='检查「我的待办」为什么没有数据')
    p.add_argument('--db', default='business.db', help='数据库文件路径（默认 business.db）')
    p.add_argument('--user', default='', help='只看某个账号（username），留空则检查全部用户')
    return p


def fetch_user(c, username):
    return c.execute(
        "SELECT username, COALESCE(display_name,'') AS display_name "
        "FROM users WHERE username=?", (username,)).fetchone()


def todo_rows(c, aliases):
    ph = ' OR '.join(['t.owner = ?'] * len(aliases))
    sql = (
        "SELECT t.id, t.wbs_code, t.name, t.owner, t.status, t.approval_status, t.plan_end "
        "FROM wbs_tasks t "
        "WHERE (%s) "
        "  AND t.status NOT IN ('已完成','已取消') "
        "  AND COALESCE(NULLIF(t.approval_status,''),'已通过') = '已通过' "
        "  AND NOT EXISTS (SELECT 1 FROM wbs_tasks c2 WHERE c2.parent_id = t.id) "
        "ORDER BY t.plan_end" % ph
    )
    return c.execute(sql, tuple(aliases)).fetchall()


def diagnose(c, username, display_name):
    aliases = [x for x in {username.strip(), display_name.strip()} if x]
    print('=' * 68)
    print('账号: %s    显示名: %s' % (username, display_name or '（未设置）'))
    print('匹配写法: %s' % ' / '.join(aliases))

    matched_total = 0
    for a in aliases:
        n = c.execute("SELECT COUNT(*) FROM wbs_tasks WHERE owner=?", (a,)).fetchone()[0]
        print('  负责人写「%s」的任务: %d 条' % (a, n))
        matched_total += n

    if matched_total == 0:
        print('  ✗ 库里没有任何任务的负责人是这个账号或显示名 —— 待办为空属于正常结果。')
        print('    常见原因：任务是别人名下、负责人写成了别的叫法（如英文名/工号/带空格）。')
        # 给出最像的几个负责人写法，方便比对
        top = c.execute(
            "SELECT owner, COUNT(*) FROM wbs_tasks "
            "WHERE owner IS NOT NULL AND owner != '' "
            "GROUP BY owner ORDER BY COUNT(*) DESC LIMIT 8").fetchall()
        print('    库里负责人高频写法（供比对）: ' + '、'.join('%s(%d)' % (o, n) for o, n in top))
        return 0

    rows = todo_rows(c, aliases)
    print('  ✓ 按待办口径应显示: %d 条' % len(rows))

    # 统计被口径过滤掉的部分
    by_status = Counter(r['status'] or '空' for r in c.execute(
        "SELECT status FROM wbs_tasks WHERE (%s)" % ' OR '.join(['owner = ?'] * len(aliases)),
        tuple(aliases)).fetchall())
    print('    你名下任务的状态分布: ' + '、'.join('%s:%d' % (k, v) for k, v in by_status.items()))

    if rows:
        for r in rows[:20]:
            print('      [%s] %s（%s）截止 %s' % (
                r['wbs_code'] or '-', r['name'] or '', r['status'] or '-', r['plan_end'] or '未设'))
        if len(rows) > 20:
            print('      … 另有 %d 条' % (len(rows) - 20))
    return len(rows)


def main():
    args = build_parser().parse_args()
    try:
        c = sqlite3.connect('file:%s?mode=ro' % args.db, uri=True)
    except Exception as e:
        print('打不开数据库: %s' % e)
        return 2
    c.row_factory = sqlite3.Row

    if args.user:
        u = fetch_user(c, args.user)
        if not u:
            print('用户表中没有账号「%s」' % args.user)
            return 1
        n = diagnose(c, u['username'], u['display_name'])
        print('\n结论: 该账号当前应显示 %d 条待办。' % n)
        return 0

    users = c.execute(
        "SELECT username, COALESCE(display_name,'') AS display_name FROM users "
        "WHERE COALESCE(deleted,0)=0 ORDER BY username").fetchall()
    print('在册用户 %d 人，逐个检查：\n' % len(users))
    total = 0
    none_users = []
    for u in users:
        n = diagnose(c, u['username'], u['display_name'])
        total += n
        if n == 0:
            none_users.append(u['username'])
    print('\n' + '=' * 68)
    print('全部用户应显示待办合计 %d 条；无待办账号 %d 个：%s' % (
        total, len(none_users), '、'.join(none_users) or '无'))
    return 0


if __name__ == '__main__':
    sys.exit(main())
