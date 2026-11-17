#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
WBS 负责人存量规范化脚本（配合 v13.0.0.13）

用途：
  把历史任务里以人名填写的 wbs_tasks.owner，转换成平台账号 username，
  使「我的待办」能按人精确聚合。顺带修补 parent_id=0 导致的预警页隐身任务。

只依赖 Python 标准库，Windows 内网可直接运行。

用法（先备份数据库文件，再执行）：
  1) 只读检查，不改任何数据（默认模式）：
       python migrate_owner.py --db business.db

  2) 确认清单无误后执行转换（自动建备份表，可回滚）：
       python migrate_owner.py --db business.db --apply

  3) 如需回滚：
       python migrate_owner.py --db business.db --rollback

分类说明：
  account    已是平台账号，无需处理
  display    人名唯一命中，可自动转换
  multi      含顿号/逗号等分隔符，视为多人填写，不自动转换
  unmatched  平台查无此人（外部人员/离职/昵称），保留原文
  empty      空值

安全边界：
  - deleted=1 的用户不参与匹配
  - 停用账号（is_active=0）照常匹配（保留历史事实）
  - 优先匹配 username，再匹配 display_name
"""

import argparse
import os
import sqlite3
import sys

SEPS = ('，', ',', '、', ';', '；', '/')


def load_users(conn):
    """返回 (username集合, display_name→[username] 映射)"""
    rows = conn.execute(
        "SELECT username, display_name FROM users WHERE COALESCE(deleted,0)=0"
    ).fetchall()
    usernames = set()
    name_map = {}
    for r in rows:
        u = (r[0] or '').strip()
        d = (r[1] or '').strip()
        if u:
            usernames.add(u)
        if d:
            name_map.setdefault(d, []).append(u)
    return usernames, name_map


def classify(text, usernames, name_map):
    """返回 (状态, 转换结果)"""
    raw = (text or '').strip()
    if not raw:
        return 'empty', raw
    if any(ch in raw for ch in SEPS):
        return 'multi', raw
    if raw in usernames:
        return 'account', raw
    hit = name_map.get(raw, [])
    if len(hit) == 1:
        return 'display', hit[0]
    if len(hit) > 1:
        return 'multi', raw
    return 'unmatched', raw


def scan(conn, usernames, name_map):
    """扫描全部任务负责人，返回分类结果"""
    rows = conn.execute(
        "SELECT id, project_id, wbs_code, name, owner FROM wbs_tasks ORDER BY project_id, id"
    ).fetchall()
    buckets = {'account': [], 'display': [], 'multi': [], 'unmatched': [], 'empty': []}
    for tid, pid, code, name, owner in rows:
        stat, target = classify(owner, usernames, name_map)
        buckets[stat].append({
            'id': tid, 'pid': pid, 'code': code or '', 'name': name or '',
            'owner': owner or '', 'target': target,
        })
    return buckets


def print_report(conn, buckets, name_map):
    total = sum(len(v) for v in buckets.values())
    print('=' * 78)
    print('WBS 负责人存量检查（只读，未修改任何数据）')
    print('=' * 78)
    print(f'任务总数：{total}')
    print(f'  已是账号 account   ：{len(buckets["account"])}')
    print(f'  可自动转换 display ：{len(buckets["display"])}')
    print(f'  多人/多义 multi    ：{len(buckets["multi"])}')
    print(f'  未匹配 unmatched   ：{len(buckets["unmatched"])}')
    print(f'  空值 empty         ：{len(buckets["empty"])}')

    # 重名检查（影响能否自动转换）
    dup = {k: v for k, v in name_map.items() if len(v) > 1}
    if dup:
        print('\n[警告] 发现重名，这些人名不会自动转换：')
        for k, v in dup.items():
            print(f'  {k} → {", ".join(v)}')
    else:
        print('\n[通过] 未发现重名，人名可唯一对应账号')

    if buckets['unmatched']:
        print('\n未匹配到平台用户的负责人（需人工判断是外部人员还是笔误）：')
        for it in buckets['unmatched']:
            print(f'  P{it["pid"]} · {it["code"]} · {it["name"][:20]} → 「{it["owner"]}」')

    if buckets['multi']:
        print('\n多人填写或多义（不自动转换）：')
        for it in buckets['multi']:
            print(f'  P{it["pid"]} · {it["code"]} · {it["name"][:20]} → 「{it["owner"]}」')

    if buckets['display']:
        print('\n将自动转换的明细（前 30 条）：')
        for it in buckets['display'][:30]:
            print(f'  P{it["pid"]} · {it["code"]} · {it["name"][:20]} → {it["owner"]} → {it["target"]}')
        if len(buckets['display']) > 30:
            print(f'  …共 {len(buckets["display"])} 条')

    # parent_id=0 且编号含点位的任务（预警页隐身）
    hidden = conn.execute(
        "SELECT id, project_id, wbs_code, name FROM wbs_tasks "
        "WHERE (parent_id=0 OR parent_id IS NULL OR parent_id='') AND wbs_code LIKE '%.%'"
    ).fetchall()
    print(f'\n父级待修补（parent_id=0 且编号含点位）：{len(hidden)} 条')
    for tid, pid, code, name in hidden[:20]:
        print(f'  P{pid} · {code} · {name or ""}')
    if len(hidden) > 20:
        print(f'  …共 {len(hidden)} 条')
    elif not hidden:
        print('  无')

    print('\n如需执行转换，请先备份数据库文件，然后运行：')
    print(f'  python {os.path.basename(__file__)} --db <库文件> --apply')


def apply_changes(conn, buckets):
    """备份 → 转换 → 修补 parent_id"""
    # 1) 备份（幂等：已存在则追加覆盖语义 —— 用新表名带时间戳更安全）
    bak = 'wbs_tasks_owner_bak'
    exists = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (bak,)
    ).fetchone()
    if exists:
        n = conn.execute(f'SELECT COUNT(*) FROM {bak}').fetchone()[0]
        print(f'[备份] 已存在 {bak}（{n} 行），本次不再覆盖，沿用原备份')
    else:
        conn.execute(
            f'CREATE TABLE {bak} AS SELECT id, owner, parent_id AS old_parent_id FROM wbs_tasks'
        )
        conn.commit()
        print(f'[备份] 已创建 {bak}（可回滚）')

    # 2) 转换
    changed = 0
    for it in buckets['display']:
        conn.execute('UPDATE wbs_tasks SET owner=? WHERE id=?', (it['target'], it['id']))
        changed += 1
    conn.commit()
    print(f'[转换] 已转换 {changed} 条人名 → username')

    # 3) parent_id 修补
    rows = conn.execute(
        "SELECT id, project_id, wbs_code FROM wbs_tasks "
        "WHERE (parent_id=0 OR parent_id IS NULL OR parent_id='') AND wbs_code LIKE '%.%'"
    ).fetchall()
    fixed = 0
    for tid, pid, code in rows:
        parent_code = str(code).rsplit('.', 1)[0]
        if not parent_code:
            continue
        parent = conn.execute(
            'SELECT id FROM wbs_tasks WHERE project_id=? AND wbs_code=? AND id!=?',
            (pid, parent_code, tid)
        ).fetchone()
        if parent and parent[0] != tid:
            conn.execute('UPDATE wbs_tasks SET parent_id=? WHERE id=?', (parent[0], tid))
            fixed += 1
    conn.commit()
    print(f'[修补] 已补挂父级 {fixed} 条')

    print('\n完成。验证方法：登录后打开「我的待办」，确认任务条数与实际任务一致。')
    print(f'如需回滚：python {os.path.basename(__file__)} --db <库文件> --rollback')


def rollback(conn):
    bak = 'wbs_tasks_owner_bak'
    exists = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (bak,)
    ).fetchone()
    if not exists:
        print('[回滚] 未找到备份表，无法回滚')
        return
    rows = conn.execute(f'SELECT id, owner, old_parent_id FROM {bak}').fetchall()
    for tid, owner, old_pid in rows:
        conn.execute('UPDATE wbs_tasks SET owner=?, parent_id=? WHERE id=?',
                     (owner, old_pid, tid))
    conn.commit()
    print(f'[回滚] 已还原 {len(rows)} 条')


def main():
    ap = argparse.ArgumentParser(description='WBS 负责人存量规范化')
    ap.add_argument('--db', required=True, help='business.db 路径')
    ap.add_argument('--apply', action='store_true', help='执行转换（默认只读）')
    ap.add_argument('--rollback', action='store_true', help='从备份表回滚')
    args = ap.parse_args()

    if not os.path.exists(args.db):
        print(f'找不到数据库：{args.db}')
        sys.exit(1)

    conn = sqlite3.connect(args.db)
    usernames, name_map = load_users(conn)

    if args.rollback:
        rollback(conn)
        return

    buckets = scan(conn, usernames, name_map)
    print_report(conn, buckets, name_map)

    if args.apply:
        print('\n开始执行转换…')
        apply_changes(conn, buckets)
    else:
        print('\n（当前为只读模式，未修改任何数据）')


if __name__ == '__main__':
    main()
