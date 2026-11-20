#!/usr/bin/env python3
"""清空当前版本业务数据，保留账号、插件注册与系统结构。

用法：
  python tools/reset_business_data.py --dry-run
  python tools/reset_business_data.py --apply
  python tools/reset_business_data.py --apply --app-dir /path/to/app
"""
import argparse
import sqlite3
from pathlib import Path

MAIN_KEEP = {
    'users', 'plugin_registry', 'plugin_tables', 'schema_version', 'ui_prefs',
    'user_plugins',
}
MAIN_CLEAR = {
    'operation_logs', 'user_sessions', 'tasks', 'approval_requests',
    'wbs_projects', 'wbs_tasks', 'ai_chat_logs',
}
WF_CLEAR = {
    'wf_tasks', 'wf_notifications', 'wf_instances', 'wf_events',
    'wf_outbox', 'wf_trigger_queue', 'wf_event_log',
}


def tables(db):
    with sqlite3.connect(db) as c:
        return {r[0] for r in c.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}


def count(db, table):
    with sqlite3.connect(db) as c:
        return c.execute('SELECT COUNT(*) FROM "%s"' % table).fetchone()[0]


def reset_main(db, apply):
    present = tables(db)
    targets = sorted((MAIN_CLEAR | WF_CLEAR) & present)
    print('[主库] %s' % db)
    for t in targets:
        print('  %-28s %6d 行' % (t, count(db, t)))
    if not apply:
        return targets
    with sqlite3.connect(db) as c:
        c.execute('PRAGMA foreign_keys=OFF')
        for t in targets:
            c.execute('DELETE FROM "%s"' % t)
        c.commit()
        c.execute('VACUUM')
    return targets


def reset_version(db, apply):
    if not Path(db).exists():
        print('[版本库] 不存在，跳过 %s' % db)
        return []
    present = tables(db)
    targets = sorted(t for t in present if t.startswith('version_plan_') and t != 'version_plan_meta')
    print('[版本库] %s' % db)
    for t in targets:
        print('  %-36s %6d 行' % (t, count(db, t)))
    if not apply:
        return targets
    with sqlite3.connect(db) as c:
        c.execute('PRAGMA foreign_keys=OFF')
        for t in targets:
            c.execute('DELETE FROM "%s"' % t)
        c.commit()
        c.execute('VACUUM')
    return targets


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--app-dir', default=str(Path(__file__).resolve().parents[1]))
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument('--dry-run', action='store_true')
    g.add_argument('--apply', action='store_true')
    a = ap.parse_args()
    app = Path(a.app_dir)
    main_db = app / 'business.db'
    print('模式：%s' % ('执行清空' if a.apply else '仅盘点'))
    reset_main(main_db, a.apply)
    for vdb in [app / 'data' / 'version_plan.db', app / 'data' / '_seed' / 'version_plan.db']:
        reset_version(vdb, a.apply)
    print('完成。账号、插件注册、表结构与版本模块字典保留。')


if __name__ == '__main__':
    main()
