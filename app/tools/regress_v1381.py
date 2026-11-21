#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""v13.8.0.1 备份恢复：缺失账号合并 + 用户 ID 重映射专项回归。

运行：python tools/regress_v1381.py
全部在临时副本上跑，不写生产库。
"""
import hashlib
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

APP = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(APP))

passes = []
failures = []


def check(name, cond, detail=''):
    if cond:
        passes.append(name)
        print('PASS  %s' % name)
    else:
        failures.append((name, detail))
        print('FAIL  %s  %s' % (name, detail))


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def users(db):
    c = sqlite3.connect(str(db))
    c.row_factory = sqlite3.Row
    rows = [dict(r) for r in c.execute('SELECT * FROM users ORDER BY id')]
    c.close()
    return rows


def by_name(rows):
    return {r['username']: r for r in rows}


def scalar(db, sql, params=()):
    c = sqlite3.connect(str(db))
    v = c.execute(sql, params).fetchone()[0]
    c.close()
    return v


def prepare_source(tmp):
    src = tmp / 'source_business.db'
    shutil.copy2(APP / 'business.db', src)
    from werkzeug.security import generate_password_hash
    c = sqlite3.connect(str(src))
    pwd = generate_password_hash('123456')
    for i, (name, disp) in enumerate([
            ('zhangwei', '张伟'), ('lina', '李娜'), ('wangfang', '王芳'),
            ('chenchen', '陈晨'), ('liuyang', '刘洋'), ('zhaolei', '赵磊'),
            ('sunqian', '孙倩'), ('hejun', '何军'), ('zhoumin', '周敏'),
            ('yangfan', '杨帆')], start=3):
        c.execute('INSERT INTO users (id,username,password,display_name,role,is_active,deleted,department) '
                  'VALUES (?,?,?,?,?,1,0,?)',
                  (i, name, pwd, disp, 'user', '演示部门'))
    c.execute("INSERT INTO wbs_projects (id,name,description,owner,start_date,end_date,status,"
              "created_at,updated_at,participants,approver,approval_status,project_type) "
              "VALUES (1,'源库项目','回归','zhangwei','2026-11-01','2026-11-30','进行中',"
              "'2026-11-21 12:18:30','2026-11-21 12:18:30','lina,chenchen','系统管理员','已通过','交付项目')")
    c.execute("INSERT INTO wf_instances (id,def_key,def_version,plugin_id,record_type,record_id,"
              "business_key,title,initiator_id,initiator_name,status,current_node,decision,"
              "context_json,created_at,updated_at) "
              "VALUES ('inst-1','auto_wbs_project_created',1,'wbs','project','1','WBS-P1',"
              "'源库项目创建',5,'王芳','running','approve_2','', '{}','2026-11-21 12:18:30','2026-11-21 12:18:30')")
    for tid, aid in [(1, 3), (2, 4), (3, 12), (4, 99)]:
        c.execute("INSERT INTO wf_tasks (id,instance_id,node_id,name,assignee_id,assignee_name,status) "
                  "VALUES (?, 'inst-1', ?, ?, ?, ?, 'pending')",
                  (tid, 'approve_%d' % tid, '审批%d' % tid, aid, '用户%d' % aid))
    c.execute("INSERT INTO wf_events (instance_id,seq,event_type,actor_id,actor_name,detail_json,hash) "
              "VALUES ('inst-1',1,'instance_started',6,'陈晨','{}','h')")
    c.execute("INSERT INTO wf_notifications (user_id,username,display_name,title,instance_id) "
              "VALUES (7,'liuyang','刘洋','待审批','inst-1')")
    c.execute("INSERT INTO wf_trigger_queue (def_key,plugin_id,event_key,record_type,record_id,"
              "business_key,title,initiator_id,initiator_name,status) "
              "VALUES ('auto_wbs_project_created','wbs','wbs.project.created','project','1',"
              "'WBS-P1','源库项目创建',8,'赵磊','pending')")
    # 新账号的插件授权
    c.execute("INSERT OR IGNORE INTO user_plugins (user_id, plugin_id, subscribed_at) "
              "VALUES (3, 'wbs', '2026-11-21 12:18:30')")
    c.commit()
    c.close()
    return src


def prepare_target(tmp, swap=False):
    t = tmp / ('target_swap.db' if swap else 'target.db')
    shutil.copy2(APP / 'business.db', t)
    c = sqlite3.connect(str(t))
    # 已有账号：wangwu 占 id=3，lina 被放在 id=7（与备份里的 id=4 不同）
    if swap:
        c.execute("INSERT INTO users (id,username,password,display_name,role,is_active,deleted,department) "
                  "VALUES (3,'sunqian','old-sun','旧孙倩','user',1,0,'测试部')")
        c.execute("INSERT INTO users (id,username,password,display_name,role,is_active,deleted,department) "
                  "VALUES (8,'zhangwei','old-zhang','旧张伟','user',1,0,'测试部')")
    else:
        c.execute("INSERT INTO users (id,username,password,display_name,role,is_active,deleted,department) "
                  "VALUES (3,'wangwu','old-wang','王五','admin',1,0,'测试部')")
        c.execute("INSERT INTO users (id,username,password,display_name,role,is_active,deleted,department) "
                  "VALUES (7,'lina','old-lina','旧李娜','user',1,0,'研发部')")
    c.execute("INSERT INTO user_plugins (user_id, plugin_id, subscribed_at) "
              "VALUES (3,'version_plan','2026-11-20 10:00:00')")
    c.execute("INSERT INTO wbs_projects (id,name,owner,status,project_type,created_at,updated_at) "
              "VALUES (101,'目标库已有项目','wangwu','进行中','内部项目','2026-11-20 10:00:00','2026-11-20 10:00:00')")
    c.commit()
    c.close()
    return t


def main():
    tmp = Path(tempfile.mkdtemp(prefix='v1381_regress_'))
    import app as appmod

    src = prepare_source(tmp)
    src_hash = sha(src)
    check('备份源文件已生成', src.exists() and src.stat().st_size > 300000, src.stat().st_size)

    # ---------- 场景 1：非空库合并恢复 + ID 冲突重映射 ----------
    target = prepare_target(tmp)
    before_users = by_name(users(target))
    old_admin = before_users['admin']['password']
    old_wang = before_users['wangwu']['password']
    old_lina = before_users['lina']['password']

    moved, no_such, defaulted, stats = appmod._restore_data_only(
        src, target, include_users='merge')
    check('合并恢复搬迁业务表', len(moved) >= 15, len(moved))
    check('补回缺失账号 9 个', stats['users_added'] == 9, stats['users_added'])
    check('保留已有账号 3 个', stats['users_kept'] == 3, stats['users_kept'])
    check('返回账号合并摘要', '补回缺失账号 9 个' in appmod._restore_user_summary(stats),
          appmod._restore_user_summary(stats))

    after = by_name(users(target))
    check('当前账号总数 13（12 个备份账号 + 目标库独有的 wangwu）', len(after) == 13, len(after))
    check('admin 密码未被回退', after['admin']['password'] == old_admin)
    check('wangwu 角色/密码未变', after['wangwu']['password'] == old_wang
          and after['wangwu']['role'] == 'admin')
    check('lina 密码未变', after['lina']['password'] == old_lina)
    check('缺失账号带中文显示名', after['zhangwei']['display_name'] == '张伟'
          and after['yangfan']['display_name'] == '杨帆')
    check('ID 冲突后 zhangwei 使用新 ID', after['zhangwei']['id'] != 3,
          after['zhangwei']['id'])
    check('同名不同 ID 的 lina 映射到现有 ID', after['lina']['id'] == 7,
          after['lina']['id'])

    # 3(zhangwei)->8, 4(lina)->7, 12(yangfan)->16
    check('wf_tasks 旧ID 3 重映射', scalar(target, 'SELECT assignee_id FROM wf_tasks WHERE id=1') == after['zhangwei']['id'])
    check('wf_tasks 旧ID 4 重映射', scalar(target, 'SELECT assignee_id FROM wf_tasks WHERE id=2') == 7)
    check('wf_tasks 旧ID 12 重映射', scalar(target, 'SELECT assignee_id FROM wf_tasks WHERE id=3') == after['yangfan']['id'])
    check('wf_instances initiator_id 重映射',
          scalar(target, "SELECT initiator_id FROM wf_instances WHERE id='inst-1'") == after['wangfang']['id'])
    check('wf_events actor_id 重映射',
          scalar(target, "SELECT actor_id FROM wf_events WHERE instance_id='inst-1'") == after['chenchen']['id'])
    check('wf_notifications user_id 重映射',
          scalar(target, 'SELECT user_id FROM wf_notifications WHERE rowid=1') == after['liuyang']['id'])
    check('wf_trigger_queue initiator_id 重映射',
          scalar(target, "SELECT initiator_id FROM wf_trigger_queue WHERE business_key='WBS-P1'") == after['zhaolei']['id'])
    check('新账号插件授权补回',
          scalar(target, "SELECT COUNT(*) FROM user_plugins WHERE user_id=? AND plugin_id='wbs'",
                 (after['zhangwei']['id'],)) == 1)
    check('已有账号插件授权未被覆盖',
          scalar(target, "SELECT COUNT(*) FROM user_plugins WHERE user_id=3 AND plugin_id='version_plan'") == 1)
    check('孤儿引用被识别', stats['orphan_user_refs'] == ['wf_tasks.assignee_id=1'],
          stats['orphan_user_refs'])
    check('孤儿引用进入摘要', '仍有 1 处用户引用未匹配' in appmod._restore_user_summary(stats),
          appmod._restore_user_summary(stats))
    check('备份自身孤儿被识别', stats['backup_orphan_refs'] == ['wf_tasks.assignee_id=1'],
          stats['backup_orphan_refs'])
    check('源备份文件未被修改', sha(src) == src_hash)

    # ---------- 场景 2：ID 交换（3→8, 8→3） ----------
    target2 = prepare_target(tmp, swap=True)
    moved2, _, _, stats2 = appmod._restore_data_only(src, target2, include_users='merge')
    u2 = by_name(users(target2))
    check('交换场景账号总数 12（目标库 4 个 + 补回 8 个）', len(u2) == 12, len(u2))
    check('zhangwei 保留现有 ID 8', u2['zhangwei']['id'] == 8, u2['zhangwei']['id'])
    check('sunqian 保留现有 ID 3', u2['sunqian']['id'] == 3, u2['sunqian']['id'])
    # 备份 wf_tasks id1 指向 3(zhangwei) -> 8；id2 指向 4(lina)->新建 id
    check('交换场景 3→8', scalar(target2, 'SELECT assignee_id FROM wf_tasks WHERE id=1') == 8)
    check('交换场景 4→新 ID', scalar(target2, 'SELECT assignee_id FROM wf_tasks WHERE id=2') == u2['lina']['id'])
    check('交换场景无重复账号',
          scalar(target2, 'SELECT COUNT(*) FROM users WHERE username IN ("zhangwei","sunqian")') == 2)
    check('交换场景孤儿检查通过', stats2['orphan_user_refs'] == ['wf_tasks.assignee_id=1'],
          stats2['orphan_user_refs'])

    # ---------- 场景 3：空库整库导入仍可用 ----------
    target3 = tmp / 'target_fresh.db'
    shutil.copy2(APP / 'business.db', target3)
    moved3, _, _, stats3 = appmod._restore_data_only(src, target3, include_users='full')
    check('空库整库导入 12 个账号', len(users(target3)) == 12, len(users(target3)))
    check('整库导入模式标记', stats3['users_mode'] == 'full', stats3['users_mode'])
    check('整库导入账号计数', stats3.get('users_total') == 12 and '12 个' in appmod._restore_user_summary(stats3),
          (stats3.get('users_total'), appmod._restore_user_summary(stats3)))
    check('整库导入项目数据 1 个',
          scalar(target3, 'SELECT COUNT(*) FROM wbs_projects') == 1)

    # ---------- 场景 4：插件库恢复不碰账号表 ----------
    vp_src = tmp / 'version_plan_source.db'
    vp_dst = tmp / 'version_plan_target.db'
    shutil.copy2(APP / 'data' / 'version_plan.db', vp_src)
    shutil.copy2(APP / 'data' / 'version_plan.db', vp_dst)
    vc = sqlite3.connect(str(vp_src))
    vc.execute("INSERT INTO version_plan_products (code,name,owner,status) VALUES ('RCP','RedCap 通信模组','zhangwei','启用')")
    vc.execute("INSERT INTO version_plan_demands (code,doc_ref,description,status,product_code) VALUES ('DM-2026-101','DOC-1','回归需求','待过滤','RCP')")
    vc.commit(); vc.close()
    moved4, _, _, stats4 = appmod._restore_data_only(vp_src, vp_dst, include_users=False)
    check('插件库恢复不进入账号合并', stats4['users_mode'] == 'none', stats4['users_mode'])
    check('插件库恢复保留需求数据',
          scalar(vp_dst, 'SELECT COUNT(*) FROM version_plan_demands') == 1)

    # ---------- 场景 5：旧备份没有 users 表也不报错 ----------
    src5 = tmp / 'source_old.db'
    shutil.copy2(src, src5)
    c = sqlite3.connect(str(src5))
    c.execute('PRAGMA foreign_keys=OFF')
    c.execute('DROP TABLE users')
    c.commit()
    c.close()
    target5 = prepare_target(tmp)
    moved5, _, _, stats5 = appmod._restore_data_only(src5, target5, include_users='merge')
    check('旧备份无 users 表可继续搬迁', len(moved5) >= 15 and stats5['users_mode'] == 'none',
          (len(moved5), stats5['users_mode']))
    check('旧备份无账号表给出提示', '备份中没有账号表' in appmod._restore_user_summary(stats5),
          appmod._restore_user_summary(stats5))

    print('\n===== v13.8.0.1 专项回归：%d PASS / %d FAIL =====' % (len(passes), len(failures)))
    if failures:
        for name, detail in failures:
            print('FAIL %s :: %s' % (name, detail))
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
