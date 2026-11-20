from plugins.base import make_blueprint
from flask import g, render_template, request, redirect, url_for, flash, send_from_directory, jsonify, session
import sqlite3
from datetime import datetime, date, timedelta
import os, json, time, random, io, re as _re
from plugin_system import log_operation

blueprint = make_blueprint('wbs', __name__)

# 上传限制：单文件 50MB，单任务最多 10 个附件
MAX_UPLOAD_BYTES = 50 * 1024 * 1024
MAX_TASK_ATTACHMENTS = 10

def get_db():
    if 'db' not in g:
        from flask import current_app
        g.db = sqlite3.connect(current_app.config['DATABASE'])
        g.db.row_factory = sqlite3.Row
        g.db.execute('PRAGMA foreign_keys = ON')
    return g.db


def _emit_wf(event_key, business_key='', title='', context=None, record_type='', record_id='',
             start_now=False):
    """v13.3：把业务状态变更上报流程引擎。失败只记日志，绝不影响业务提交。"""
    try:
        import workflow_engine as wf
        return wf.emit_state_change(get_db(), 'wbs', event_key, business_key=business_key,
                                    title=title, context=context or {},
                                    record_type=record_type, record_id=str(record_id or ''),
                                    start_now=start_now)
    except Exception as e:
        try:
            from flask import current_app
            current_app.logger.warning('workflow emit failed (%s): %s', event_key, e)
        except Exception:
            pass
        return None


def _wf_managed(event_key):
    """v13.5：该审批节点是否已由流程引擎托管。

    托管后插件内的「通过/驳回」按钮与接口一律停用，审批只在流程中心做，
    通过后由流程动作写回业务状态，避免同一件事被审批两次。
    """
    try:
        import workflow_engine as wf
        info = wf.event_managed(get_db(), 'wbs', event_key)
        # 只有流程里真的配了写回动作才算托管，避免审批通过后业务状态永远不变
        if info and (info.get('definition') or {}).get('has_write_back'):
            return info
        return None
    except Exception:
        return None

@blueprint.teardown_request
def close_db(exception):
    db = g.pop('db', None)
    if db is not None:
        db.close()


# ============ 权限辅助（v12.5.9：项目成员制） ============
def _wbs_is_admin():
    """管理员 / 平台管理员"""
    return session.get('user_role') in ('admin', 'platform_admin')


def _wbs_project_editable(db, pid):
    """项目级编辑权限：管理员 / 项目负责人 / 项目参与人员。
    owner/participants 存逗号分隔的 username（旧数据可能是姓名），
    按「当前用户名」或「当前用户显示名」匹配，兼容旧文本数据。"""
    if _wbs_is_admin():
        return True, ''
    user = session.get('username') or ''
    display = session.get('user_name') or user
    if not user:
        return False, '未登录'
    p = db.execute('SELECT owner, participants FROM wbs_projects WHERE id=?', (pid,)).fetchone()
    if not p:
        return False, '项目不存在'
    names = set()
    for v in (p['owner'], p['participants']):
        for n in str(v or '').replace('，', ',').split(','):
            n = n.strip()
            if n:
                names.add(n)
    if user in names or display in names:
        return True, ''
    return False, '仅项目负责人、参与人员或管理员可编辑'


def _wbs_is_project_owner(db, pid):
    """v13.0.0.21：项目负责人判定（用于「悬浮球新建任务」这类只给负责人的能力）。
    管理员/平台管理员恒为 True；否则要求当前用户名/显示名出现在项目 owner 中。
    项目没设负责人（owner 为空）时，只有管理员可见。"""
    if _wbs_is_admin():
        return True
    user = session.get('username') or ''
    display = session.get('user_name') or user
    if not user:
        return False
    p = db.execute('SELECT owner FROM wbs_projects WHERE id=?', (pid,)).fetchone()
    if not p:
        return False
    for n in str(p['owner'] or '').replace('，', ',').split(','):
        n = n.strip()
        if n and (n == user or n == display):
            return True
    return False


def _wbs_project_approved(db, pid):
    """项目审批态校验（v13.0.0.11）：非管理员写任务前，项目本身必须是「已通过」。

    驳回/待审批的项目，其任务可能仍保留此前的「已通过」标记，
    只看任务审批态会让成员继续写入已驳回项目，这里按项目态再兜一层。"""
    p = db.execute('SELECT name, approval_status FROM wbs_projects WHERE id=?', (pid,)).fetchone()
    if not p:
        return False, '项目不存在'
    st = (p['approval_status'] or '').strip()
    if st != '已通过':
        return False, '项目《%s》当前审批状态为「%s」，任务不可编辑' % (p['name'], st or '未提交')
    return True, ''


def _fmt_date(v):
    """日期归一化（v12.7.6 多格式容错）：任意常见格式 → YYYY-MM-DD；无法解析返回 None。

    支持：
    - datetime / date 对象
    - Excel 数值日期序列号（1900 系，如 45701）
    - 字符串：2026-11-05 / 2026/11/05 / 2026.11.05 / 2026年11月5日 /
      20261105 / 2026-11-05 10:30（自动截取日期部分）
    非法值（如 2026-13-99、abc）返回 None，由调用方给出明确错误提示。
    """
    from datetime import datetime, date, timedelta
    if isinstance(v, datetime):
        return v.strftime('%Y-%m-%d')
    if isinstance(v, date):
        return v.strftime('%Y-%m-%d')
    if isinstance(v, (int, float)):
        try:
            return (date(1899, 12, 30) + timedelta(days=int(v))).strftime('%Y-%m-%d')
        except Exception:
            return None
    s = str(v or '').strip().strip('\'"').strip()
    if not s:
        return None
    if _re.fullmatch(r'\d{8}', s):  # 20261105
        try:
            return date(int(s[0:4]), int(s[4:6]), int(s[6:8])).strftime('%Y-%m-%d')
        except Exception:
            return None
    # 年在前：2026-11-05 / 2026/11/05 / 2026.11.05 / 2026年11月5日 / 2026-11-5
    m = _re.search(r'(\d{4})\s*[-/年月.]\s*(\d{1,2})\s*[-/月.]\s*(\d{1,2})(?:日)?', s)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3))).strftime('%Y-%m-%d')
        except Exception:
            return None
    for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M', '%Y/%m/%d %H:%M:%S', '%Y/%m/%d'):
        try:
            return datetime.strptime(s, fmt).strftime('%Y-%m-%d')
        except Exception:
            continue
    # 美式 MM/DD/YYYY（避免歧义：仅在年月无法匹配时尝试）
    m = _re.fullmatch(r'(\d{1,2})/(\d{1,2})/(\d{4})', s)
    if m and 1 <= int(m.group(1)) <= 12:
        try:
            return date(int(m.group(3)), int(m.group(1)), int(m.group(2))).strftime('%Y-%m-%d')
        except Exception:
            return None
    return None


def _all_active_users(db):
    """平台所有启用用户（供负责人/参与人员多选下拉）"""
    return db.execute(
        'SELECT username, display_name FROM users WHERE is_active=1 ORDER BY username').fetchall()


# ============ 回收站（v12.5.14：快照式，保留7天自动清空） ============
RECYCLE_DAYS = 7

def _init_recycle_tables():
    db = get_db()
    db.execute('CREATE TABLE IF NOT EXISTS wbs_recycle ('
               'id INTEGER PRIMARY KEY AUTOINCREMENT, obj_type TEXT NOT NULL, obj_id INTEGER NOT NULL, '
               'name TEXT NOT NULL, snapshot TEXT NOT NULL, file_dir TEXT DEFAULT \'\', '
               'operator TEXT DEFAULT \'\', deleted_at TEXT, expire_at TEXT)')

def _recycle_root():
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), 'recycle_files')

def _recycle_cleanup():
    """惰性清理：删除已过期回收站记录及其附件文件（访问回收站时调用）"""
    db = get_db()
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    rows = db.execute('SELECT id, file_dir FROM wbs_recycle WHERE expire_at <= ?', (now,)).fetchall()
    for r in rows:
        _rm_recycle_files(r['id'])
        db.execute('DELETE FROM wbs_recycle WHERE id=?', (r['id'],))
    if rows:
        db.commit()
    return len(rows)

def _insert_recycle(db, obj_type, obj_id, name, snapshot, operator):
    now = datetime.now()
    expire = (now + timedelta(days=RECYCLE_DAYS)).strftime('%Y-%m-%d %H:%M:%S')
    db.execute('INSERT INTO wbs_recycle (obj_type,obj_id,name,snapshot,file_dir,operator,deleted_at,expire_at) VALUES (?,?,?,?,?,?,?,?)',
               (obj_type, obj_id, name, json.dumps(snapshot, ensure_ascii=False), '', operator,
                now.strftime('%Y-%m-%d %H:%M:%S'), expire))
    rid = db.execute('SELECT last_insert_rowid()').fetchone()[0]
    db.execute('UPDATE wbs_recycle SET file_dir=? WHERE id=?', (f'r{rid}', rid))
    return rid

def _move_attachments_to_recycle(db, task_ids, rid):
    """把任务附件物理移到 recycle_files/r{rid}/wbs/，attachment_path 原样留在快照里"""
    src = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'uploads', 'wbs')
    dst = os.path.join(_recycle_root(), f'r{rid}', 'wbs')
    os.makedirs(dst, exist_ok=True)
    moved = 0
    for tid in task_ids:
        row = db.execute('SELECT attachment_path FROM wbs_tasks WHERE id=?', (tid,)).fetchone()
        if not row or not row['attachment_path']:
            continue
        for f in (row['attachment_path'] or '').split(';'):
            f = f.strip()
            if not f:
                continue
            base = os.path.basename(f.replace('\\', '/'))
            sp = os.path.join(src, base)
            if os.path.exists(sp):
                try:
                    os.replace(sp, os.path.join(dst, base))
                    moved += 1
                except OSError:
                    pass
    return moved

def _restore_attachments(rid):
    """把回收站 r{rid}/wbs/ 的文件移回 uploads/wbs/"""
    src = os.path.join(_recycle_root(), f'r{rid}', 'wbs')
    dst = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'uploads', 'wbs')
    os.makedirs(dst, exist_ok=True)
    n = 0
    if os.path.isdir(src):
        for f in os.listdir(src):
            try:
                os.replace(os.path.join(src, f), os.path.join(dst, f))
                n += 1
            except OSError:
                pass
    return n

def _rm_recycle_files(rid):
    d = os.path.join(_recycle_root(), f'r{rid}')
    if os.path.isdir(d):
        import shutil
        shutil.rmtree(d, ignore_errors=True)

def _insert_dup_rows(db, table, rows):
    """按原 id 原样插回快照行（复用原 id，父行已先插入保证外键）"""
    for row in rows:
        cur = db.execute(f'SELECT id FROM {table} WHERE id=?', (row['id'],)).fetchone()
        if cur:
            raise RuntimeError(f'{table}#{row["id"]} 原ID已被占用，无法自动恢复')
        cols = [c for c in row.keys()]
        db.execute(f'INSERT INTO {table} (%s) VALUES (%s)' % (','.join(cols), ','.join('?' * len(cols))),
                   [row[c] for c in cols])


# ============================================================
def _user_disp_map(db):
    """v13.0.0.31：一次查出全部用户显示名并挂在请求上下文上。
    此前每渲染一行任务都要单独查一次 users 表，2000 行的项目详情页就是 2000 次查询。"""
    m = getattr(g, '_wbs_user_disp_map', None)
    if m is None:
        try:
            m = {r['username']: (r['display_name'] or r['username'])
                 for r in db.execute('SELECT username, display_name FROM users').fetchall()}
        except Exception:
            m = {}
        g._wbs_user_disp_map = m
    return m


def _resolve_display(text, db=None):
    """登录名串（逗号分隔）→ 显示名串；查不到的登录名原样保留（v12.7.7）"""
    if not text:
        return text
    names = [x.strip() for x in str(text).split(',') if x.strip()]
    if not names:
        return text
    m = _user_disp_map(db or get_db())
    if not m:
        return text
    return ','.join(m.get(n, n) for n in names)


def _to_username_info(text):
    """人名/账号 → (username, 状态)。状态：empty / account / display / multi / unmatched
    v13.0.0.13：把自由文本负责人规范化为平台账号，供存档与个人待办按人聚合。
    规则：① 已是 username 直接返回（幂等）② display_name 唯一命中才转 ③ 多义/未命中原样返回（不猜）
    安全边界：含顿号/逗号视为多人填写不转；deleted=1 不参与匹配；停用账号照常匹配（保留历史事实）"""
    raw = (text or '').strip()
    if not raw:
        return raw, 'empty'
    if any(ch in raw for ch in ('，', ',', '、', ';', '；', '/')):
        return raw, 'multi'
    # v13.0.0.13 兼容加固：查库失败（老库缺 deleted 列 / 表不存在 / 连接异常）时
    # 一律降级为「按原文保存」，绝不阻断导入与新建——导入失败比负责人没转换严重得多
    try:
        db = get_db()
        row = db.execute('SELECT username FROM users WHERE username=? AND COALESCE(deleted,0)=0',
                         (raw,)).fetchone()
        if row:
            return row['username'], 'account'
        rows = db.execute('SELECT username FROM users WHERE display_name=? AND COALESCE(deleted,0)=0',
                          (raw,)).fetchall()
    except Exception:
        return raw, 'unmatched'
    if len(rows) == 1:
        return rows[0]['username'], 'display'
    return raw, 'unmatched'


def _to_username(text):
    """_to_username_info 的简化包装，返回转换后的 username 字符串"""
    return _to_username_info(text)[0]


def _with_owner_disp(row):
    """任务行 → dict + owner_disp（预警看板负责人列显示名）"""
    d = dict(row)
    d['owner_disp'] = _resolve_display(row['owner'])
    return d


# ===== v13.0.0.13 跨插件「我的待办」契约（平台侧按需发现，未实现即跳过） =====
# 契约：插件 routes 模块实现 my_todos(db, username) -> list[dict]
# 每个 dict：title / url / due / level(overdue|soon|near|later|none) / status
# 平台不规定排序，各插件区块内自排。
TODO_LEVEL_ORDER = {'overdue': 0, 'soon': 1, 'near': 2, 'later': 3, 'none': 4}


def _todo_days_left(plan_end, today):
    """plan_end → 距今天数（负数=已逾期）；无法解析返回 None"""
    s = str(plan_end or '').strip()
    if not s:
        return None
    m = _re.match(r'(\d{4})[-/.年](\d{1,2})[-/.月](\d{1,2})', s)
    if not m:
        return None
    try:
        d = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None
    return (d - today).days


def _owner_aliases(db, u):
    """一个用户可能出现在 owner 字段的写法：账号 + 唯一显示名。

    v13.0.0.14：存量任务负责人很多还是人名（没跑过迁移脚本），
    只按账号精确匹配会让这些人的待办全部为空。这里把两种写法都收进来，
    不要求用户先做数据转换也能看到自己的任务。

    v13.6：显示名只有在用户管理里唯一时才纳入匹配。两人同名时，
    按姓名归属无法判断任务到底是谁的，宁可只按账号匹配，也不能把
    别人的任务串到当前用户名下。
    """
    rows = []
    try:
        rows = db.execute(
            "SELECT username, display_name FROM users "
            "WHERE (username=? OR display_name=?) AND is_active=1 AND COALESCE(deleted,0)=0 "
            "ORDER BY id", (u, u)).fetchall()
    except Exception:
        return [u] if u else []

    # 账号是精确身份；有账号命中时以账号为准。
    exact = [r for r in rows if (r['username'] or '').strip() == u]
    if exact:
        row = exact[0]
    elif len(rows) == 1:
        row = rows[0]
    elif rows:
        # 只命中多人同名显示名 → 归属有歧义，不做姓名匹配
        return []
    else:
        # 用户管理里查不到：兼容历史自由填写的负责人字符串
        return [u] if u else []

    names = set()
    un = (row['username'] or '').strip()
    dn = (row['display_name'] or '').strip()
    if un:
        names.add(un)
    if dn:
        try:
            dup = db.execute(
                "SELECT COUNT(*) c FROM users WHERE display_name=? AND is_active=1 "
                "AND COALESCE(deleted,0)=0", (dn,)).fetchone()['c']
        except Exception:
            dup = 1
        if dup == 1:
            names.add(dn)
    return [n for n in names if n]

def my_todos(db, user):
    """我负责的、尚未完成的 WBS 任务（跨项目）。

    v13.6：user 可以是用户上下文 dict（含 id/username/display_name），
    也可以继续传账号字符串；统一通过用户管理解析账号 + 姓名别名。
    """
    if isinstance(user, dict):
        u = (user.get('username') or user.get('display_name') or '').strip()
        owner_meta = user
    else:
        u = (user or '').strip()
        owner_meta = {}
    if not u:
        return []
    aliases = _owner_aliases(db, u)
    if not aliases:
        # 显示名重名且没有账号可锚定：无法安全判断归属，宁可不出待办也不能串号。
        return []
    owner_where = ' OR '.join(['t.owner = ?'] * len(aliases))
    today = date.today()
    rows = db.execute(
        "SELECT t.id, t.project_id, t.wbs_code, t.name, t.work_content, "
        "       t.status, t.plan_end, t.progress, "
        "       COALESCE(p.name,'') AS project_name "
        "FROM wbs_tasks t LEFT JOIN wbs_projects p ON p.id = t.project_id "
        "WHERE (%s) "
        "  AND t.status NOT IN ('已完成','已取消') "
        # 老数据 approval_status 可能是 NULL 或空串（迁移前的行），按已通过对待
        "  AND COALESCE(NULLIF(t.approval_status,''),'已通过') = '已通过' "
        # v13.0.0.14：用「没有子任务」判断任务行，取代 parent_id>0。
        # 导入时父编号不存在会留下 parent_id=0 的孤立行，旧口径会把这类任务
        # 当成阶段行过滤掉，导致导入了却永远进不了待办。
        "  AND NOT EXISTS (SELECT 1 FROM wbs_tasks c WHERE c.parent_id = t.id)" % owner_where,
        tuple(aliases)).fetchall()

    items = []
    for r in rows:
        days = _todo_days_left(r['plan_end'], today)
        if days is None:
            level = 'none'
        elif days < 0:
            level = 'overdue'
        elif days <= 3:
            level = 'soon'
        elif days <= 7:
            level = 'near'
        else:
            level = 'later'
        items.append({
            'id': r['id'],
            'title': r['name'] or '',
            'project': r['project_name'],
            'wbs_code': r['wbs_code'] or '',
            'status': r['status'] or '',
            'progress': r['progress'],
            'due': str(r['plan_end'] or ''),
            'days_left': days,
            'level': level,
            # 与「到期预警」同一跳转：进项目详情并定位高亮该任务行
            'url': '/wbs/%d?task_highlight=%d' % (r['project_id'], r['id']),
            'owner_user_id': owner_meta.get('id'),
            'owner_username': owner_meta.get('username') or u,
            'owner_display_name': owner_meta.get('display_name') or u,
        })
    items.sort(key=lambda x: (TODO_LEVEL_ORDER.get(x['level'], 9),
                              x['days_left'] if x['days_left'] is not None else 9999,
                              x['id']))
    return items


def workbench_modules(db, user):
    """工作台插件模块：WBS 个人待办。"""
    items = my_todos(db, user)
    if not items:
        return []
    return [{'plugin_id': 'wbs', 'plugin_name': 'WBS 计划管理', 'icon': '🗓️',
             'kind': 'todo', 'title': '我的任务', 'items': items}]


@blueprint.route('/wbs', strict_slashes=False)
def wbs_projects():
    from datetime import date, timedelta
    db = get_db()
    # v12.7.2 性能优化：批量统计任务数，消除 N+1 子查询
    rows = db.execute('SELECT p.* FROM wbs_projects p ORDER BY p.id DESC').fetchall()
    counts = db.execute('''SELECT project_id,
        COUNT(*) AS task_count,
        SUM(CASE WHEN parent_id=0 THEN 1 ELSE 0 END) AS phase_count
        FROM wbs_tasks GROUP BY project_id''').fetchall()
    count_map = {r['project_id']: (r['task_count'], r['phase_count']) for r in counts}
    rows = [dict(r, task_count=count_map.get(r['id'], (0,0))[0], phase_count=count_map.get(r['id'], (0,0))[1],
                 owner_disp=_resolve_display(r['owner']), participants_disp=_resolve_display(r['participants'])) for r in rows]
    # 预警统计
    today = date.today().isoformat()
    soon3 = (date.today() + timedelta(days=3)).isoformat()
    soon7 = (date.today() + timedelta(days=7)).isoformat()
    # 口径与预警看板统一：只统计任务级(parent_id>0或NULL)审批已通过的任务，待审批/已驳回不纳入
    overdue = db.execute("SELECT COUNT(*) FROM wbs_tasks WHERE plan_end IS NOT NULL AND plan_end!='' AND plan_end<? AND status NOT IN ('已完成','已取消') AND (parent_id>0 OR parent_id IS NULL) AND approval_status='已通过'", (today,)).fetchone()[0]
    due3 = db.execute("SELECT COUNT(*) FROM wbs_tasks WHERE plan_end>=? AND plan_end<=? AND status NOT IN ('已完成','已取消') AND (parent_id>0 OR parent_id IS NULL) AND approval_status='已通过'", (today, soon3)).fetchone()[0]
    due7 = db.execute("SELECT COUNT(*) FROM wbs_tasks WHERE plan_end>? AND plan_end<=? AND status NOT IN ('已完成','已取消') AND (parent_id>0 OR parent_id IS NULL) AND approval_status='已通过'", (soon3, soon7)).fetchone()[0]
    return render_template('wbs_projects.html', rows=rows, overdue=overdue, due3=due3, due7=due7, today=today,
                           wf_project_managed=_wf_managed('wbs.project.created'))


@blueprint.route('/wbs/alerts')
def wbs_alerts():
    """跨项目到期预警看板"""
    from datetime import date, timedelta
    db = get_db()
    today = date.today()
    today_s = today.isoformat()
    soon3 = (today + timedelta(days=3)).isoformat()
    soon7 = (today + timedelta(days=7)).isoformat()
    # 逾期（只统计任务级审批通过的任务，待审批/已驳回的不纳入预警）
    overdue = [_with_owner_disp(r) for r in db.execute('''SELECT t.*, p.name as project_name, p.id as project_id FROM wbs_tasks t
        JOIN wbs_projects p ON t.project_id=p.id
        WHERE t.plan_end!='' AND t.plan_end<? AND t.status NOT IN ('已完成','已取消') AND (t.parent_id>0 OR t.parent_id IS NULL)
          AND t.approval_status='已通过'
        ORDER BY t.plan_end ASC''', (today_s,)).fetchall()]
    # 3天内
    due3 = [_with_owner_disp(r) for r in db.execute('''SELECT t.*, p.name as project_name, p.id as project_id FROM wbs_tasks t
        JOIN wbs_projects p ON t.project_id=p.id
        WHERE t.plan_end>=? AND t.plan_end<=? AND t.status NOT IN ('已完成','已取消') AND (t.parent_id>0 OR t.parent_id IS NULL)
          AND t.approval_status='已通过'
        ORDER BY t.plan_end ASC''', (today_s, soon3)).fetchall()]
    # 7天内
    due7 = [_with_owner_disp(r) for r in db.execute('''SELECT t.*, p.name as project_name, p.id as project_id FROM wbs_tasks t
        JOIN wbs_projects p ON t.project_id=p.id
        WHERE t.plan_end>? AND t.plan_end<=? AND t.status NOT IN ('已完成','已取消') AND (t.parent_id>0 OR t.parent_id IS NULL)
          AND t.approval_status='已通过'
        ORDER BY t.plan_end ASC''', (soon3, soon7)).fetchall()]
    return render_template('wbs_alerts.html', overdue=overdue, due3=due3, due7=due7, today=today_s)


@blueprint.route('/wbs/add', methods=['GET', 'POST'])
def wbs_project_add():
    db = get_db()
    admins = db.execute("SELECT username, display_name FROM users WHERE role IN ('admin','platform_admin') AND is_active=1 ORDER BY username").fetchall()
    users = _all_active_users(db)
    if request.method == 'POST':
        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        is_admin = session.get('user_role') in ('admin', 'platform_admin')
        approval_status = '已通过' if is_admin else '待审批'
        approval_managed = (not is_admin) and _wf_managed('wbs.project.created')
        approver = '' if (is_admin or approval_managed) else (request.form.get('approver') or '').strip()
        # v12.5.26 漏洞修复：普通用户提交审批必须选择审批人（托管给流程引擎后不再要求）
        if not is_admin and not approval_managed and not approver:
            flash('请选择审批管理员', 'error')
            return redirect(url_for('wbs.wbs_projects'))
        # v12.5.26 漏洞修复：日期格式强校验（YYYY-MM-DD 且为真实日期）
        for f in ('start_date', 'end_date'):
            v = (request.form.get(f) or '').strip()
            if v:
                try:
                    datetime.strptime(v, '%Y-%m-%d')
                except ValueError:
                    flash(f'{f} 日期格式无效，应为 YYYY-MM-DD', 'error')
                    return redirect(url_for('wbs.wbs_projects'))
        owner = ','.join(request.form.getlist('owner'))
        participants = ','.join(request.form.getlist('participants'))
        db.execute('''INSERT INTO wbs_projects (name, description, owner, participants, start_date, end_date, status, approver, approval_status, created_at, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)''',
            (request.form.get('name',''), request.form.get('description',''), owner, participants,
             request.form.get('start_date',''), request.form.get('end_date',''),
             request.form.get('status','进行中'), approver, approval_status, now, now))
        pid = db.execute('SELECT last_insert_rowid()').fetchone()[0]
        db.commit()
        log_operation('WBS', '新建项目', request.form.get('name',''), session.get('username','admin'))
        wf_res = None
        if not is_admin:   # v13.5：管理员/平台管理员自建项目直接通过，不发起审批
            wf_res = _emit_wf('wbs.project.created',
                              business_key='WBS-P%d' % pid,
                              title='项目「%s」创建' % request.form.get('name', ''),
                              context={'name': request.form.get('name', ''), 'owner': owner,
                                       'approval_status': approval_status, 'project_id': pid},
                              record_type='project', record_id=pid, start_now=approval_managed)
            if approval_managed and not wf_res:
                flash('项目已保存，但流程发起失败；请联系管理员在流程中心补发起。', 'warning')
        if is_admin:
            return redirect(url_for('wbs.wbs_project_detail', pid=pid))
        elif approval_managed:
            flash('项目已提交，审批已在流程中心发起，通过后自动写回项目状态。', 'success')
            return redirect(url_for('wbs.wbs_projects'))
        else:
            flash('项目已提交，等待管理员审批通过后才能导入任务。', 'success')
            return redirect(url_for('wbs.wbs_projects'))
    return render_template('wbs_project_form.html', p=None, admins=admins, users=users,
                           can_change_members=True,
                           wf_project_managed=_wf_managed('wbs.project.created'),
                           owner_sel=[], part_sel=[])


@blueprint.route('/wbs/<int:pid>/edit', methods=['GET', 'POST'])
def wbs_project_edit(pid):
    db = get_db()
    p = db.execute('SELECT * FROM wbs_projects WHERE id=?', (pid,)).fetchone()
    if not p:
        return redirect(url_for('wbs.wbs_projects'))
    ok, msg = _wbs_project_editable(db, pid)
    if not ok:
        flash(msg, 'error')
        return redirect(url_for('wbs.wbs_projects'))
    users = _all_active_users(db)
    if request.method == 'POST':
        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        # v13.0.0.35：项目名必填、日期必须真实且结束不早于开始，避免脏数据破坏预警与统计
        name = (request.form.get('name') or '').strip()
        if not name:
            flash('项目名称不能为空', 'error')
            return redirect(url_for('wbs.wbs_project_edit', pid=pid))
        start_date = (request.form.get('start_date') or '').strip()
        end_date = (request.form.get('end_date') or '').strip()
        for f, v in (('开始日期', start_date), ('结束日期', end_date)):
            if v:
                try:
                    datetime.strptime(v, '%Y-%m-%d')
                except ValueError:
                    flash(f'{f}格式无效，应为 YYYY-MM-DD', 'error')
                    return redirect(url_for('wbs.wbs_project_edit', pid=pid))
        if start_date and end_date and end_date < start_date:
            flash('结束日期不能早于开始日期', 'error')
            return redirect(url_for('wbs.wbs_project_edit', pid=pid))
        if session.get('user_role') in ('admin', 'platform_admin'):
            owner = ','.join(request.form.getlist('owner')) or (p['owner'] or '')
            participants = ','.join(request.form.getlist('participants')) or (p['participants'] or '')
        else:
            # v12.5.11：人员名单变更仅管理员，防止成员自行拉人造成权限扩散
            owner = p['owner'] or ''
            participants = p['participants'] or ''
        db.execute('''UPDATE wbs_projects SET name=?, description=?, owner=?, participants=?,
            start_date=?, end_date=?, updated_at=? WHERE id=?''',
            (name, request.form.get('description',p['description']),
             owner, participants, start_date, end_date,
             now, pid))
        db.commit()
        return redirect(url_for('wbs.wbs_project_detail', pid=pid))
    owner_sel = [n.strip() for n in (p['owner'] or '').replace('，', ',').split(',') if n.strip()]
    part_sel = [n.strip() for n in (p['participants'] or '').replace('，', ',').split(',') if n.strip()]
    return render_template('wbs_project_form.html', p=p, admins=[],
                           users=users, owner_sel=owner_sel, part_sel=part_sel,
                           can_change_members=session.get('user_role') in ('admin', 'platform_admin'))


@blueprint.route('/wbs/<int:pid>/approve', methods=['POST'])
def wbs_project_approve(pid):
    if session.get('user_role') not in ('admin', 'platform_admin'):
        flash('只有管理员可以审批项目。', 'error')
        return redirect(url_for('wbs.wbs_projects'))
    # v13.5：审批已由流程引擎托管时，禁止在业务页再点一次「通过」
    if _wf_managed('wbs.project.created'):
        flash('该项目审批已由流程引擎接管，请在「流程中心」处理，通过后会自动写回项目状态。', 'error')
        return redirect(url_for('wbs.wbs_project_detail', pid=pid))
    db = get_db()
    action = request.form.get('action','')
    status = '已通过' if action == 'approve' else '已驳回'
    db.execute('UPDATE wbs_projects SET approval_status=?, updated_at=datetime("now","localtime") WHERE id=?', (status, pid))
    db.commit()
    log_operation('WBS', '项目审批', f'项目#{pid} → {status}', session.get('username','admin'))
    _p = db.execute('SELECT name, owner FROM wbs_projects WHERE id=?', (pid,)).fetchone()
    _emit_wf('wbs.project.approval', business_key='WBS-P%d' % pid,
             title='项目「%s」审批%s' % (_p['name'] if _p else pid, status),
             context={'name': _p['name'] if _p else '', 'owner': _p['owner'] if _p else '',
                      'approval_status': status, 'project_id': pid},
             record_type='project', record_id=pid)
    flash(f'项目已{status}。', 'success')
    return redirect(url_for('wbs.wbs_projects'))


@blueprint.route('/wbs/<int:pid>/resubmit', methods=['POST'])
def wbs_project_resubmit(pid):
    """普通用户：项目被驳回后重新提交审批"""
    if session.get('user_role') in ('admin', 'platform_admin'):
        return jsonify({'ok': False, 'msg': '管理员无需重新提交'}), 400
    db = get_db()
    p = db.execute('SELECT * FROM wbs_projects WHERE id=?', (pid,)).fetchone()
    if not p:
        return jsonify({'ok': False, 'msg': '项目不存在'}), 404
    ok, msg = _wbs_project_editable(db, pid)
    if not ok:
        return jsonify({'ok': False, 'msg': '只有项目成员可以重新提交'}), 403
    if p['approval_status'] != '已驳回':
        return jsonify({'ok': False, 'msg': '只有被驳回的项目可以重新提交'}), 400
    db.execute('''UPDATE wbs_projects SET approval_status='待审批', updated_at=datetime("now","localtime") WHERE id=?''', (pid,))
    db.commit()
    log_operation('WBS', '重新提交', f'项目#{pid} 重新提交审批', session.get('username','admin'))
    # v13.5：托管审批时，重新提交必须重新发起一条新流程（旧流程已结束，幂等检查不会误判）
    if _wf_managed('wbs.project.created'):
        _emit_wf('wbs.project.created', business_key='WBS-P%d' % pid,
                 title='项目「%s」重新提交审批' % (p['name'] or pid),
                 context={'name': p['name'] or '', 'owner': p['owner'] or '',
                          'approval_status': '待审批', 'project_id': pid},
                 record_type='project', record_id=pid, start_now=True)
    return jsonify({'ok': True})


@blueprint.route('/wbs/recycle')
def wbs_recycle():
    """回收站：仅管理员/平台管理员可见；访问时惰性清理过期项"""
    if not _wbs_is_admin():
        flash('无权限访问回收站', 'error')
        return redirect(url_for('wbs.wbs_projects'))
    db = get_db()
    _init_recycle_tables()
    cleared = _recycle_cleanup()
    items = db.execute('SELECT * FROM wbs_recycle ORDER BY id DESC').fetchall()
    return render_template('wbs_recycle.html', items=items, cleared=cleared)


@blueprint.route('/wbs/recycle/<int:rid>/restore', methods=['POST'])
def wbs_recycle_restore(rid):
    """从回收站恢复（复用原 id；附件文件一并放回）"""
    if not _wbs_is_admin():
        flash('无权限', 'error')
        return redirect(url_for('wbs.wbs_projects'))
    db = get_db()
    _init_recycle_tables()
    r = db.execute('SELECT * FROM wbs_recycle WHERE id=?', (rid,)).fetchone()
    if not r:
        flash('回收站记录不存在或已过期清理', 'error')
        return redirect(url_for('wbs.wbs_recycle'))
    try:
        snap = json.loads(r['snapshot'])
        if r['obj_type'] == 'project':
            _insert_dup_rows(db, 'wbs_projects', [snap['project']])
            _insert_dup_rows(db, 'wbs_tasks', snap['tasks'])
        elif r['obj_type'] == 'task':
            t = snap['task']
            if not db.execute('SELECT id FROM wbs_projects WHERE id=?', (t['project_id'],)).fetchone():
                raise RuntimeError('原项目已被删除，请先恢复该项目，再恢复此任务')
            _insert_dup_rows(db, 'wbs_tasks', [t] + snap.get('children', []))
        db.commit()
        n = _restore_attachments(rid)
        db.execute('DELETE FROM wbs_recycle WHERE id=?', (rid,))
        db.commit()
        log_operation('WBS', '恢复', f'从回收站恢复{("项目" if r["obj_type"]=="project" else "任务")}「{r["name"]}」(附件{n}个)',
                      session.get('username', ''))
        flash(f'已恢复「{r["name"]}」（附件{n}个一并还原）', 'success')
    except Exception as e:
        db.rollback()
        flash(f'恢复失败：{e}', 'error')
    return redirect(url_for('wbs.wbs_recycle'))


@blueprint.route('/wbs/recycle/<int:rid>/purge', methods=['POST'])
def wbs_recycle_purge(rid):
    """彻底删除回收站记录（含附件文件，不可恢复）"""
    if not _wbs_is_admin():
        flash('无权限', 'error')
        return redirect(url_for('wbs.wbs_projects'))
    db = get_db()
    _init_recycle_tables()
    r = db.execute('SELECT * FROM wbs_recycle WHERE id=?', (rid,)).fetchone()
    if not r:
        flash('回收站记录不存在', 'error')
    else:
        _rm_recycle_files(rid)
        db.execute('DELETE FROM wbs_recycle WHERE id=?', (rid,))
        db.commit()
        log_operation('WBS', '彻底删除', f'从回收站彻底删除「{r["name"]}」(rid={rid})', session.get('username', ''))
        flash(f'「{r["name"]}」已彻底删除，不可恢复', 'success')
    return redirect(url_for('wbs.wbs_recycle'))


@blueprint.route('/wbs/<int:pid>/delete', methods=['POST'])
def wbs_project_delete(pid):
    """管理员删除项目（级联删除其下所有任务）"""
    if session.get('user_role') not in ('admin', 'platform_admin'):
        flash('只有管理员可以删除项目', 'error')
        return redirect(url_for('wbs.wbs_projects'))
    db = get_db()
    p = db.execute('SELECT * FROM wbs_projects WHERE id=?', (pid,)).fetchone()
    if not p:
        flash('项目不存在', 'error')
        return redirect(url_for('wbs.wbs_projects'))
    _init_recycle_tables()
    tasks = db.execute('SELECT * FROM wbs_tasks WHERE project_id=? ORDER BY id', (pid,)).fetchall()
    rid = _insert_recycle(db, 'project', pid, p['name'],
                          {'project': dict(p), 'tasks': [dict(t) for t in tasks]},
                          session.get('username', ''))
    moved = _move_attachments_to_recycle(db, [t['id'] for t in tasks], rid)
    db.execute('DELETE FROM wbs_tasks WHERE project_id=?', (pid,))
    db.execute('DELETE FROM wbs_projects WHERE id=?', (pid,))
    db.commit()
    log_operation('WBS', '删除项目', f'项目#{pid}「{p["name"]}」移入回收站(rid={rid}, 附件{moved}个)',
                  session.get('username', ''))
    flash(f'项目「{p["name"]}」已移入回收站（保留7天，可恢复）', 'success')
    return redirect(url_for('wbs.wbs_projects'))


@blueprint.route('/wbs/<int:pid>')
def wbs_project_detail(pid):
    db = get_db()
    p = db.execute('SELECT * FROM wbs_projects WHERE id=?', (pid,)).fetchone()
    if not p:
        return redirect(url_for('wbs.wbs_projects'))
    p = dict(p)
    p['owner_disp'] = _resolve_display(p.get('owner'))
    p['participants_disp'] = _resolve_display(p.get('participants'))
    # v13.0.0.29：进详情页先把父任务的「状态/进度」按子任务对账一次，自愈历史不一致数据
    _reconcile_project_rollups(db, pid)
    tasks = db.execute('SELECT * FROM wbs_tasks WHERE project_id=?', (pid,)).fetchall()
    # 自然排序：按 WBS 编号的数字分段排序（1.10 排在 1.2 之后），同编号按 id 稳定
    tasks = sorted(tasks, key=lambda t: (_natural_key(t['wbs_code'] or ''), t['id']))
    # v13.0.0.13：任务负责人显示名（v13 起 owner 存 username，展示统一转人名）
    tasks = [_with_owner_disp(t) for t in tasks]
    # v13.0.0.30：标出「有子任务的行」，这类行的进度由子任务自动汇总，页面不再给可拖的进度条
    _pids = set(x['parent_id'] for x in tasks if x['parent_id'])
    for x in tasks:
        x['has_kids'] = x['id'] in _pids
    # 分组：父任务（阶段）和子任务
    phases = [t for t in tasks if t['parent_id'] == 0]
    pending_count = db.execute("SELECT COUNT(*) FROM wbs_tasks WHERE project_id=? AND approval_status='待审批'", (pid,)).fetchone()[0]
    rejected_count = db.execute("SELECT COUNT(*) FROM wbs_tasks WHERE project_id=? AND approval_status='已驳回'", (pid,)).fetchone()[0]
    can_edit, _ = _wbs_project_editable(db, pid)
    # v13.0.0.21：悬浮球新建任务 = 管理员 / 项目负责人（项目没设负责人则仅管理员）
    can_quickadd = _wbs_is_project_owner(db, pid)
    # 系统附件总占用（uploads/wbs 目录）
    attach_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'uploads', 'wbs')
    attach_bytes = 0
    attach_count = 0
    if os.path.isdir(attach_dir):
        for fn in os.listdir(attach_dir):
            fp = os.path.join(attach_dir, fn)
            if os.path.isfile(fp):
                try:
                    attach_bytes += os.path.getsize(fp)
                    attach_count += 1
                except OSError:
                    pass
    attach_mb = round(attach_bytes / 1024 / 1024, 1)
    # v13.0.0.13：负责人下拉候选（在册全部用户）
    # 注意必须转成普通 dict 再传模板：sqlite3.Row 无法被 tojson 序列化，
    # 渲染出的字符串带 HTML 实体（&#34;），会让整个 <script> 语法错误、页面所有按钮失效。
    try:
        pick_rows = db.execute(
            "SELECT username, display_name FROM users WHERE COALESCE(deleted,0)=0 "
            "ORDER BY COALESCE(NULLIF(display_name,''), username)"
        ).fetchall()
        pick_users = [{'username': (r['username'] or ''),
                       'display_name': (r['display_name'] or '')} for r in pick_rows]
    except Exception:
        # 老库结构差异（如缺 deleted 列）时不让整页挂掉：下拉退化为纯手输
        pick_users = []
    # 直接生成已做 HTML 转义的 JSON 文本：Flask 的 tojson 返回普通字符串，
    # 会被 autoescape 二次转义成 &#34;，在 <script> 里不解码 → 整段脚本语法错误。
    pick_users_json = (json.dumps(pick_users, ensure_ascii=False)
                       .replace('<', '\\u003c').replace('>', '\\u003e')
                       .replace('&', '\\u0026').replace("'", '\\u0027'))
    # v13.0.0.21：项目名要进 JS 常量，按 pick_users_json 同样的方式预转义，避免 autoescape 变成 &#34;
    project_name_json = (json.dumps(p['name'] or '', ensure_ascii=False)
                         .replace('<', '\\u003c').replace('>', '\\u003e').replace('&', '\\u0026'))
    return render_template('wbs_project_detail.html', p=p, tasks=tasks, phases=phases,
                           project_name_json=project_name_json,
                           pending_count=pending_count, rejected_count=rejected_count,
                           can_edit=can_edit, can_quickadd=can_quickadd,
                           can_import=can_quickadd,
                           attach_mb=attach_mb, attach_count=attach_count,
                           pick_users=pick_users, pick_users_json=pick_users_json,
                           wf_project_managed=_wf_managed('wbs.project.created'),
                           wf_task_managed=_wf_managed('wbs.task.created'))


def _natural_key(code):
    """WBS 编号自然排序键：'1.10' → [1, '.', 10]，使 1.10 排在 1.2 之后（字典序会误排）"""
    return [int(p) if p.isdigit() else p for p in _re.split(r'(\d+)', str(code))]


def _save_upload(file_storage, subdir='wbs'):
    """保存上传文件到 uploads/<subdir>/，返回相对路径"""
    if not file_storage or not file_storage.filename:
        return ''
    import os, time
    upload_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'uploads', subdir)
    os.makedirs(upload_dir, exist_ok=True)
    fname = time.strftime('%Y%m%d%H%M%S_') + os.path.basename(file_storage.filename)
    safe = ''.join(c for c in fname if c.isalnum() or c in '._-（）')
    file_storage.save(os.path.join(upload_dir, safe))
    return f'{subdir}/{safe}'


@blueprint.route('/wbs/<int:pid>/task/add', methods=['POST'])
def wbs_task_add(pid):
    """新增任务（v12.5.10）：仅管理员/平台管理员可操作（普通用户通过导入Excel添加）；
    同项目内 WBS 编号不允许重复。"""
    if session.get('user_role') not in ('admin', 'platform_admin'):
        flash('仅管理员和平台管理员可添加任务，普通用户可通过导入Excel添加任务', 'error')
        return redirect(request.referrer or url_for('wbs.wbs_projects'))
    db = get_db()
    code = (request.form.get('wbs_code') or '').strip()
    name = (request.form.get('name') or '').strip()
    # v13.0.0.35：编号与名称必填；日期必须真实且结束不早于开始；进度非数字不再 500
    if not code or not name:
        flash('WBS编号和任务名称不能为空', 'error')
        return redirect(url_for('wbs.wbs_project_detail', pid=pid))
    start_date = (request.form.get('plan_start') or '').strip()
    end_date = (request.form.get('plan_end') or '').strip()
    for f, v in (('计划开始', start_date), ('计划结束', end_date)):
        if v:
            try:
                datetime.strptime(v, '%Y-%m-%d')
            except ValueError:
                flash(f'{f}日期格式无效，应为 YYYY-MM-DD', 'error')
                return redirect(url_for('wbs.wbs_project_detail', pid=pid))
    if start_date and end_date and end_date < start_date:
        flash('计划结束不能早于计划开始', 'error')
        return redirect(url_for('wbs.wbs_project_detail', pid=pid))
    try:
        progress = int(float((request.form.get('progress') or '0').strip() or 0))
    except (TypeError, ValueError):
        progress = 0
    progress = max(0, min(100, progress))
    try:
        parent_id = int(request.form.get('parent_id') or 0)
    except (TypeError, ValueError):
        parent_id = 0
    dup = db.execute('SELECT id, name FROM wbs_tasks WHERE project_id=? AND wbs_code=?',
                     (pid, code)).fetchone()
    if dup:
        flash(f'WBS编号「{code}」已存在（任务：{dup["name"]}），请使用不同编号', 'error')
        return redirect(request.referrer or url_for('wbs.wbs_projects'))
    attach_file = request.files.get('attachment')
    req_len = request.content_length or 0
    if attach_file and attach_file.filename and req_len > MAX_UPLOAD_BYTES + 2048:
        flash(f'附件超过 50MB 上限（约 {req_len // 1024 // 1024}MB），未上传，请压缩后再试', 'error')
        return redirect(url_for('wbs.wbs_project_detail', pid=pid))
    attach = _save_upload(attach_file, 'wbs')
    db.execute('''INSERT INTO wbs_tasks
        (project_id, wbs_code, parent_id, name, work_content, owner, collaborators,
         plan_start, plan_end, deliverable, priority, attachment_path, status, progress)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
        (pid, code, parent_id, name,
         request.form.get('work_content',''), _to_username(request.form.get('owner','')),
         request.form.get('collaborators',''),
         start_date, end_date,
         request.form.get('deliverable',''), request.form.get('priority','P2中'),
         attach, request.form.get('status','未开始'), progress))
    db.commit()
    if parent_id:
        try:
            _rollup_by_parent(db, int(parent_id))
        except Exception as e:
            print('=== rollup after add ===', e)
    log_operation('WBS', '新增任务', f'P{pid} {request.form.get("name","")}', session.get('username','admin'))
    _tid = db.execute('SELECT last_insert_rowid()').fetchone()[0]
    _proj = db.execute('SELECT name FROM wbs_projects WHERE id=?', (pid,)).fetchone()
    # v13.5：/task/add 仅管理员可用，管理员建任务直接「已通过」，不走审批
    return redirect(url_for('wbs.wbs_project_detail', pid=pid))


@blueprint.route('/wbs/task/<int:tid>/delete', methods=['POST'])
def wbs_task_delete(tid):
    db = get_db()
    t = db.execute('SELECT * FROM wbs_tasks WHERE id=?', (tid,)).fetchone()
    if not t:
        flash('任务不存在', 'error')
        return redirect(url_for('wbs.wbs_projects'))
    if session.get('user_role') not in ('admin', 'platform_admin'):
        flash('只有管理员和平台管理员可以删除任务', 'error')
        return redirect(request.referrer or url_for('wbs.wbs_projects'))
    if t:
        # 递归收集所有子孙任务id一并删除，避免留下孤儿任务
        def collect(pid):
            ids = [pid]
            for row in db.execute('SELECT id FROM wbs_tasks WHERE parent_id=?', (pid,)).fetchall():
                ids.extend(collect(row['id']))
            return ids
        ids = collect(tid)
        _init_recycle_tables()
        child_rows = db.execute('SELECT * FROM wbs_tasks WHERE id IN (%s) AND id != ? ORDER BY id'
                                % ','.join('?' * len(ids)), tuple(ids) + (tid,)).fetchall()
        rid = _insert_recycle(db, 'task', tid, t['name'],
                              {'task': dict(t), 'children': [dict(c) for c in child_rows]},
                              session.get('username', ''))
        moved = _move_attachments_to_recycle(db, ids, rid)
        db.execute('DELETE FROM wbs_tasks WHERE id IN (%s)' % ','.join('?' * len(ids)), tuple(ids))
        db.commit()
        # v13.0.0.22：删掉子任务后，父任务状态按剩下的子任务重新上卷
        if t['parent_id']:
            _rollup_by_parent(db, t['parent_id'])
        log_operation('WBS', '删除任务', f'删除任务#{tid}「{t["name"]}」及{len(ids) - 1}个子任务→回收站(rid={rid}, 附件{moved}个)',
                      session.get('username', ''))
        flash(f'任务「{t["name"]}」已移入回收站（保留7天，可恢复）', 'success')
    return redirect(url_for('wbs.wbs_project_detail', pid=t['project_id'] if t else 0))


@blueprint.route('/wbs/download/<path:filename>')
def wbs_download(filename):
    """产出物附件下载：仅项目成员或管理员（v12.5.11 加鉴权）"""
    import os
    db = get_db()
    row = db.execute("SELECT DISTINCT project_id FROM wbs_tasks WHERE attachment_path LIKE ? LIMIT 20",
                     (f'%{filename}%',)).fetchone()
    if not row:
        return '文件不存在', 404
    ok, _ = _wbs_project_editable(db, row['project_id'])
    if not ok:
        return '无权限下载该附件', 403
    upload_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'uploads')
    return send_from_directory(upload_dir, filename)


def _build_import_preview(db, pid, parsed, errors):
    """v13.0.0.22：导入前差异预览（P0）。只读，不写库。"""
    sys_rows = db.execute(
        'SELECT id, wbs_code, name, status, progress, attachment_path, notes FROM wbs_tasks WHERE project_id=?',
        (pid,)).fetchall()
    by_code = {r['wbs_code']: r for r in sys_rows if r['wbs_code']}
    parsed_codes = set(p[1] for p in parsed)
    add, upd, missing = [], [], []
    for (row_idx, code, name, work_content, owner, collab,
         p_start, p_end, deliverable, status, progress, remark) in parsed:
        old = by_code.get(code)
        if old is None:
            add.append({'code': code, 'name': name})
            continue
        warn = []
        if (old['name'] or '').strip() != name:
            warn.append('任务名会被改写（原：%s）' % (old['name'] or '（空）'))
        if old['attachment_path']:
            warn.append('这条任务已有产出物，文件仍挂在这条任务上')
        if (old['notes'] or '').strip():
            warn.append('这条任务已有进展流水，流水保留但任务含义可能变化')
        if (old['status'] or '未开始') != status:
            warn.append('状态 %s → %s' % (old['status'] or '未开始', status))
        if (old['progress'] or 0) != progress:
            warn.append('进度 %s%% → %s%%' % (old['progress'] or 0, progress))
        upd.append({'code': code, 'name': name, 'old_name': old['name'] or '', 'warn': warn})
    for code, r in by_code.items():
        if code not in parsed_codes:
            missing.append({'code': code, 'name': r['name'], 'has_att': bool(r['attachment_path'])})
    # 疑似改编号：文件里新增了某个编号，同时某个老编号消失了，且两边名称完全一样
    suspects = []
    for a in add:
        for m in missing:
            if (a['name'] or '').strip() and a['name'].strip() == (m['name'] or '').strip():
                suspects.append({'from': m['code'], 'to': a['code'], 'name': a['name']})
    risky = [u for u in upd if u['warn']]
    return jsonify({
        'ok': not errors,
        'errors': errors[:10],
        'moreErrors': max(0, len(errors) - 10),
        'add': add, 'update': upd, 'missing': missing, 'suspects': suspects,
        'risky': len(risky),
        'counts': {'add': len(add), 'update': len(upd), 'missing': len(missing), 'risky': len(risky)},
    })


def _parse_wbs_rows(ws):
    """v13.0.0.22：Excel 解析与校验从 wbs_import 抽出，「正式导入」与「导入前预览」共用同一套规则，
    保证预览看到的结果和真正写库的结果一致。"""
    parsed = []    # [(row_idx, code, name, work_content, owner, collab, p_start, p_end,
    #               deliverable, status, progress, remark)]
    errors = []    # 格式错误清单：'第 N 行：计划开始「xxx」无法识别为日期'
    unmatched_owners = []  # v13.0.0.13：负责人文本未匹配到平台用户的行，导入后提示
    row_idx = 1    # Excel 行号（表头占第 1 行，数据从第 2 行起）

    def _cell(r, i):
        # v13.0.0.26：安全取列。用户上传的 Excel 列数不足（例如只有「编号/名称」两列）时，
        # 原实现直接 row[i] 会抛 IndexError，整个导入 500。缺列一律按空值处理。
        return r[i] if i < len(r) else None

    for row in ws.iter_rows(min_row=2, values_only=True):  # 第1行表头
        row_idx += 1
        if not row or not row[0]:
            continue
        code = str(row[0]).strip()
        name = str(_cell(row, 1) or '').strip()
        if not name:
            continue
        if len(row) < 3:
            # 连「工作内容」列都没有，说明不是平台模板：拒绝而不是静默建出一批空任务
            errors.append('第 %d 行：只有 %d 列，列数不足，请先下载最新模板（11 列）再填写'
                          % (row_idx, len(row)))
            continue
        work_content = str(_cell(row, 2) or '').strip()
        owner_raw = str(_cell(row, 3) or '').strip()
        # v13.0.0.13：用户仍填人名，落库前翻译为平台账号；未匹配/多人填写保留原文
        owner, owner_stat = _to_username_info(owner_raw)
        if owner_stat in ('unmatched', 'multi'):
            unmatched_owners.append((row_idx, owner_raw))
        collab = str(_cell(row, 4) or '').strip()
        p_start = _fmt_date(_cell(row, 5))
        p_end = _fmt_date(_cell(row, 6))
        if _cell(row, 5) is not None and str(_cell(row, 5)).strip() != '' and p_start is None:
            errors.append('第 %d 行：计划开始「%s」无法识别为日期，支持 2026-11-05 / 2026/11/05 / 2026.11.05 / 2026年11月5日 / 20261105'
                          % (row_idx, str(_cell(row, 5)).strip()))
        if _cell(row, 6) is not None and str(_cell(row, 6)).strip() != '' and p_end is None:
            errors.append('第 %d 行：计划结束「%s」无法识别为日期（格式同上）'
                          % (row_idx, str(_cell(row, 6)).strip()))
        if p_start and p_end and p_start > p_end:
            errors.append('第 %d 行：计划开始 %s 晚于计划结束 %s' % (row_idx, p_start, p_end))
        deliverable = str(_cell(row, 7) or '').strip()
        # 新旧格式识别：第9列（索引8）是状态关键字 → 新 12 列格式（状态|进度|备注）
        # 旧 9 列格式：第9列（索引8）是备注（任意文本）
        _c8 = _cell(row, 8)
        is_new = str(_c8).strip() in ('未开始', '进行中', '已完成', '阻塞')
        if is_new:
            status = str(_c8).strip()
            remark = str(_cell(row, 10) or '').strip()
            raw_p = (str(_cell(row, 9)).strip() if _cell(row, 9) is not None else '')
            # v12.7.10.10：进度列留空是常态（Excel 里刚排的任务还没填），默认按 0 处理，不再报错
            if not raw_p:
                progress = 0
            else:
                try:
                    progress = int(float(raw_p.rstrip('%').strip()))
                except Exception:
                    # 兼容 "50%" / "50 ％" / "５０" / "完成一半" 之类
                    import re as _re
                    m = _re.search(r'\d+', raw_p)
                    if m:
                        progress = int(m.group(0))
                    else:
                        errors.append('第 %d 行：进度「%s」无法识别为 0-100 的数字（留空则按 0 处理）'
                                      % (row_idx, raw_p))
                        progress = 0
        else:
            status = ''
            remark = str(_c8 or '').strip()
            progress = 0
        if status not in ('未开始', '进行中', '已完成', '阻塞'):
            status = '未开始'
        progress = max(0, min(100, progress))
        parsed.append((row_idx, code, name, work_content, owner, collab,
                       p_start, p_end, deliverable, status, progress, remark))
    # v13.0.0.22：同一文件里编号重复直接拒绝（否则同一行会被覆盖两次，产出物归属更乱）
    seen_codes = {}
    for item in parsed:
        code = item[1]
        if code in seen_codes:
            errors.append('第 %d 行：WBS 编号「%s」在本文件里重复（第 %d 行已经用过），同一编号只能出现一次'
                          % (item[0], code, seen_codes[code]))
        else:
            seen_codes[code] = item[0]
    return parsed, errors, unmatched_owners


@blueprint.route('/wbs/<int:pid>/import', methods=['POST'])
def wbs_import(pid):
    """导入 WBS。列：WBS编号|阶段/任务|工作内容|负责人|协作/支撑|计划开始|计划结束|交付物/判定标准|状态|进度|备注
    兼容旧 9 列格式（无状态/进度，备注在第 9 列）。
    管理员导入：已存在 WBS 编号 → 更新该任务（批量更新模式）；新编号 → 新增。
    普通用户导入：只能新增（待审批），已存在编号跳过。"""
    db = get_db()
    proj = db.execute('SELECT approval_status FROM wbs_projects WHERE id=?', (pid,)).fetchone()
    if not proj:
        flash('项目不存在', 'error')
        return redirect(url_for('wbs.wbs_projects'))
    if session.get('user_role') not in ('admin', 'platform_admin'):
        # v13.0.0.22：导入 Excel 收口为「管理员 + 项目负责人」，参与人员与其他成员不再可导入
        if not _wbs_is_project_owner(db, pid):
            flash('导入 Excel 仅对管理员和项目负责人开放；参与人员与其他成员不能导入，需要调整任务请找项目负责人或管理员', 'error')
            return redirect(url_for('wbs.wbs_project_detail', pid=pid))
        if proj['approval_status'] != '已通过':
            flash('项目待审批通过后才能导入任务', 'error')
            return redirect(url_for('wbs.wbs_project_detail', pid=pid))
    f = request.files.get('file')
    if not f:
        flash('请选择文件', 'error')
        return redirect(url_for('wbs.wbs_project_detail', pid=pid))
    import openpyxl
    # v12.7.10.18 修复：上传 CSV / 改了后缀的文本 / 损坏文件时，openpyxl 会抛 BadZipFile 直接 500
    try:
        wb = openpyxl.load_workbook(f, data_only=True)
    except Exception:
        flash('文件打不开：请上传 Excel（.xlsx）格式的 WBS 导入模板（CSV 请先用 Excel 另存为 .xlsx）', 'error')
        return redirect(url_for('wbs.wbs_project_detail', pid=pid))
    ws = wb.active
    db = get_db()
    is_admin = session.get('user_role') in ('admin', 'platform_admin')
    # v13.0.0.22：项目负责人也能导入更新自己项目的任务（新增仍按待审批口径）
    can_update = is_admin or _wbs_is_project_owner(db, pid)
    approval = '已通过' if is_admin else '待审批'
    # 已存在的 WBS 编号 → 任务 id（管理员更新用）
    existing = {}
    for r in db.execute('SELECT id, wbs_code FROM wbs_tasks WHERE project_id=?', (pid,)).fetchall():
        if r['wbs_code']:
            existing[r['wbs_code']] = r['id']
    inserted = []  # (row_id, wbs_code)
    updated = []   # (row_id, wbs_code)
    skip = 0
    risky_att = 0   # v13.0.0.22：名称被改写、但该任务还挂着产出物/进展流水的条数
    renamed = 0
    # ===== v12.7.6 预检：先全量解析校验，任何一行有问题都不写库（冗余设计）=====
    parsed, errors, unmatched_owners = _parse_wbs_rows(ws)
    # ===== v13.0.0.22（P0）：导入前预览，只算差异、不写库 =====
    if request.form.get('preview') or request.args.get('preview'):
        return _build_import_preview(db, pid, parsed, errors)
    if errors:
        head = '导入未执行：发现 %d 处格式问题，请修正后重新导入。' % len(errors)
        for e in errors[:10]:
            head += ' ｜ ' + e
        if len(errors) > 10:
            head += ' ｜ …共 %d 处' % len(errors)
        flash(head, 'error')
        return redirect(url_for('wbs.wbs_project_detail', pid=pid))
    # ===== 预检通过，正式写入 =====
    # v13.0.0.21：写库前先把「导入前的计划」落一份快照，供用户对照/回退
    snapshot_name = _save_import_snapshot(db, pid)
    for (row_idx, code, name, work_content, owner, collab,
         p_start, p_end, deliverable, status, progress, remark) in parsed:
        if code in existing:
            if can_update:
                # 批量更新：替换内容、状态、进度、备注（保留审批状态、进展、产出物、附件）
                _old = db.execute('SELECT name, attachment_path, notes FROM wbs_tasks WHERE id=?',
                                  (existing[code],)).fetchone()
                if _old and (_old['name'] or '').strip() != name:
                    renamed += 1
                    if _old['attachment_path'] or (_old['notes'] or '').strip():
                        risky_att += 1
                db.execute('''UPDATE wbs_tasks SET name=?, work_content=?, owner=?, collaborators=?,
                    plan_start=?, plan_end=?, deliverable=?, remark=?, status=?, progress=? WHERE id=?''',
                    (name, work_content, owner, collab, p_start, p_end, deliverable, remark,
                     status, progress, existing[code]))
                updated.append((existing[code], code))
            else:
                skip += 1
            continue
        cur = db.execute('''INSERT INTO wbs_tasks
            (project_id, wbs_code, parent_id, name, work_content, owner, collaborators,
             plan_start, plan_end, deliverable, status, progress, approval_status, remark)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
            (pid, code, 0, name, work_content, owner, collab, p_start, p_end, deliverable,
             status, progress, approval, remark))
        inserted.append((cur.lastrowid, code))
        existing[code] = cur.lastrowid
    # 第二遍：按 WBS 编号补挂父子关系（新增与更新的行都重挂，父行必然已存在）
    touched = inserted + updated
    for row_id, code in touched:
        if '.' in code:
            parent_code = code.rsplit('.', 1)[0]
            parent = db.execute('SELECT id FROM wbs_tasks WHERE project_id=? AND wbs_code=?',
                                (pid, parent_code)).fetchone()
            if parent:
                db.execute('UPDATE wbs_tasks SET parent_id=? WHERE id=?', (parent['id'], row_id))
    db.commit()
    # 导入后同步父任务时间：让阶段时间自动覆盖所有子任务
    for row_id, code in touched:
        _sync_parent_dates(db, row_id)
    # v13.0.0.22：导入改了子任务状态后，父任务状态按事实重新上卷
    for row_id, code in touched:
        _rollup_parent_status(db, row_id)
    if snapshot_name:
        flash(f'导入前的计划已自动备份：{snapshot_name}（页面顶部可下载，也可在服务器 uploads/snapshots 里找回）', 'success')
    if can_update:
        parts = [f'新增 {len(inserted)} 条' + ('' if (is_admin or not inserted) else '（待管理员审批）')]
        if updated:
            parts.append(f'更新 {len(updated)} 条')
        if skip:
            parts.append(f'跳过 {skip} 条')
        msg = '导入完成：' + '，'.join(parts) + '。'
        if risky_att:
            msg += f' 注意：有 {risky_att} 条任务的任务名被改写，但它们原有的产出物/进展流水仍挂在这条任务上，请到列表逐条核对是否对应。'
        elif renamed:
            msg += f' 本次有 {renamed} 条任务名被改写（这些任务没有产出物，风险较小）。'
        if unmatched_owners:
            picks = '、'.join(f'第{r}行「{n}」' for r, n in unmatched_owners[:8])
            more = f' 等 {len(unmatched_owners)} 处' if len(unmatched_owners) > 8 else ''
            msg += f'<br>以下负责人未匹配到平台用户，已按原文保存：{picks}{more}。可在任务行内编辑中改为平台人员。'
        flash(msg, 'success')
        log_operation('WBS', '导入任务', f'P{pid} 新增{len(inserted)} 更新{len(updated)} 跳过{skip}', session.get('username','admin'))
    else:
        msg = (f'成功导入 {len(inserted)} 条任务' + (f'，跳过 {skip} 条已存在的任务' if skip else '')
               + '，等待管理员审批通过后才能编辑状态、进度、进展和产出物。')
        if unmatched_owners:
            picks = '、'.join(f'第{r}行「{n}」' for r, n in unmatched_owners[:8])
            more = f' 等 {len(unmatched_owners)} 处' if len(unmatched_owners) > 8 else ''
            msg += f'<br>以下负责人未匹配到平台用户，已按原文保存：{picks}{more}。'
        flash(msg, 'success')
    return redirect(url_for('wbs.wbs_project_detail', pid=pid, snapshot=snapshot_name))


# ============ v9.4 行内编辑 AJAX 接口 ============

@blueprint.route('/wbs/task/<int:tid>/inline', methods=['POST'])
def wbs_task_inline(tid):
    """单元格行内保存（v12.5.10 权限收口）。
    - admin / platform_admin：全部字段
    - 项目负责人/参与人员：仅已通过任务的状态/进度/备注（执行字段）
    - 非项目成员：只读"""
    import json
    db = get_db()
    t = db.execute('SELECT * FROM wbs_tasks WHERE id=?', (tid,)).fetchone()
    if not t:
        return jsonify({'ok': False, 'msg': '任务不存在'}), 404
    data = request.get_json(silent=True) or {}
    field = data.get('field')
    value = data.get('value', '')
    admin = session.get('user_role') in ('admin', 'platform_admin')
    member, _ = _wbs_project_editable(db, t['project_id'])
    editable_admin = {'wbs_code','name','work_content','owner','collaborators',
                      'plan_start','plan_end','deliverable','status','progress','remark'}
    if admin:
        allowed = editable_admin
    elif member:
        # 项目成员（普通用户）：仅已通过任务的执行字段
        okp, msgp = _wbs_project_approved(db, t['project_id'])
        if not okp:
            return jsonify({'ok': False, 'msg': msgp}), 403
        if t['approval_status'] != '已通过':
            return jsonify({'ok': False, 'msg': '任务待审批通过后才能编辑'}), 403
        allowed = {'status','progress','remark'}
    else:
        # 非项目成员：只读
        return jsonify({'ok': False, 'msg': '仅项目负责人、参与人员或管理员可修改'}), 403
    if field not in allowed:
        return jsonify({'ok': False, 'msg': '无权限修改此字段'}), 403
    if field == 'progress':
        try:
            value = max(0, min(100, int(value)))
        except Exception:
            value = 0
    if field in ('plan_start', 'plan_end'):
        value = _fmt_date(value)
        if value is None:
            return jsonify({'ok': False, 'msg': '日期格式无法识别，支持 2026-11-05 / 2026/11/05 / 2026.11.05 / 2026年11月5日'}), 400
    # v13.0.0.13：行内改负责人也做账号规范化（手输人名自动转 username，未匹配保留原文）
    if field == 'owner':
        value = _to_username(value)
    # v12.7.10.22 修复：行内保存 WBS 编号必须做同项目唯一校验，否则会出现两行同编号、层级挂接歧义
    if field == 'wbs_code':
        value = (value or '').strip()
        if value:
            dup = db.execute('SELECT id, name FROM wbs_tasks WHERE project_id=? AND wbs_code=? AND id!=?',
                             (t['project_id'], value, tid)).fetchone()
            if dup:
                return jsonify({'ok': False,
                                'msg': f'WBS编号「{value}」已被任务「{dup["name"]}」占用，请换一个编号'}), 400
    old_value = t[field]
    # v13.0.0.29：状态与进度双向联动，避免出现「未开始 30%」「已完成 60%」这类自相矛盾的行
    extra = {}
    if field == 'progress':
        v = int(value)
        if v >= 100:
            extra['status'] = '已完成'
        elif v > 0 and (t['status'] or '未开始') == '未开始':
            extra['status'] = '进行中'
    elif field == 'status':
        if value == '已完成':
            extra['progress'] = 100
        elif value == '未开始':
            extra['progress'] = 0
    sets = ', '.join(['%s=?' % k for k in extra.keys()] + ['%s=?' % field])
    db.execute('UPDATE wbs_tasks SET %s WHERE id=?' % sets,
               tuple(list(extra.values()) + [value, tid]))
    db.commit()
    if field in ('plan_start', 'plan_end'):
        _sync_parent_dates(db, tid)
    if field in ('status', 'progress'):
        # v13.0.0.29：状态或进度任何一边变了，父任务都按事实重新汇总（可升可降）
        _rollup_parent_status(db, tid)
    disp = {'progress': lambda v: f'{v}%'}.get(field, lambda v: v)
    log_operation('WBS', '行内编辑', f'任务#{tid}「{t["name"]}」{field}: {disp(old_value)} → {disp(value)}',
                  session.get('username', ''))
    # v13.0.0.13：负责人回传显示名，页面即时切换为"余丽莎"而非账号
    resp = {'ok': True, 'value': value,
            'disp': (_resolve_display(value) if field in ('owner', 'collaborators') else value)}
    # v13.0.0.29：状态/进度联动结果一并回给前端，行内控件即时对齐
    if extra:
        resp['linked'] = dict(extra)
    # v13.3：状态/进度变化上报流程引擎（完成单独成节点，避免一次变更触发两条流程）
    if field in ('status', 'progress') and str(old_value) != str(value):
        _proj = db.execute('SELECT name FROM wbs_projects WHERE id=?', (t['project_id'],)).fetchone()
        _pname = _proj['name'] if _proj else ''
        _ctx = {'task_name': t['name'], 'wbs_code': t['wbs_code'] or '',
                'owner': t['owner'] or '', 'project_name': _pname,
                'project_id': t['project_id'],
                'status': extra.get('status', value if field == 'status' else t['status']),
                'progress': extra.get('progress', value if field == 'progress' else t['progress']),
                'deliverable': t['deliverable'] or '', 'old_value': old_value, 'field': field}
        _completed = (_ctx.get('status') == '已完成') or (int(_ctx.get('progress') or 0) >= 100)
        _emit_wf('wbs.task.completed' if _completed else 'wbs.task.progress',
                 business_key='WBS-T%d' % tid,
                 title=('任务「%s」完成' if _completed else '任务「%s」进度更新') % t['name'],
                 context=_ctx, record_type='task', record_id=tid)
    _rp = db.execute('SELECT parent_id FROM wbs_tasks WHERE id=?', (tid,)).fetchone()
    if _rp and _rp['parent_id']:
        _p = db.execute('SELECT id, status, progress FROM wbs_tasks WHERE id=?',
                        (_rp['parent_id'],)).fetchone()
        if _p:
            resp['parent'] = {'id': _p['id'], 'status': _p['status'], 'progress': _p['progress']}
    return jsonify(resp)


def _rollup_target(kids):
    """v13.0.0.29：由直接子任务推算父任务的（状态, 进度）。
    规则（全量推导，可升可降，不再"只升不降"）：
      · 全部子任务已完成        → 父 已完成，进度 100
      · 有子任务阻塞            → 父 阻塞，进度取均值
      · 全部子任务未开始        → 父 未开始，进度取均值
      · 其余（有做有没做）      → 父 进行中，进度取均值
    已取消的子任务不参与推导；子任务全是已取消时父也不动。"""
    live = [k for k in kids if (k['status'] or '未开始') != '已取消']
    if not live:
        return None, None
    sts = [(k['status'] or '未开始') for k in live]
    prs = []
    for k in live:
        try:
            prs.append(max(0, min(100, int(k['progress'] or 0))))
        except Exception:
            prs.append(0)
    if all(x == '已完成' for x in sts):
        st = '已完成'
    elif any(x == '阻塞' for x in sts):
        st = '阻塞'
    elif all(x == '未开始' for x in sts):
        st = '未开始'
    else:
        st = '进行中'
    prog = 100 if st == '已完成' else int(sum(prs) / len(prs) + 0.5)  # 四舍五入取整（round() 会把 82.5 抹成 82）
    return st, prog


def _rollup_by_parent(db, parent_id, quiet=False):
    """按直接子任务重算父任务的状态与进度，并逐级向上。
    v13.0.0.22 初版只做了状态、且"只升不降"；v13.0.0.29 补齐：
      1) 状态可降（子任务从全部完成退回未开始/进行中，父任务跟着退，不再卡在已完成）；
      2) 父任务进度 = 直接子任务进度均值，父状态与进度不再各说各话；
      3) 可升可降意味着页面加载时做一次对账即可自愈历史脏数据。"""
    try:
        kids = db.execute('SELECT status, progress FROM wbs_tasks WHERE parent_id=?', (parent_id,)).fetchall()
        if not kids:
            return
        target, prog = _rollup_target(kids)
        if target is None:
            return
        p = db.execute('SELECT status, progress, name FROM wbs_tasks WHERE id=?', (parent_id,)).fetchone()
        if not p:
            return
        cur_st = p['status'] or '未开始'
        cur_pg = int(p['progress'] or 0)
        if cur_st != target or cur_pg != prog:
            db.execute('UPDATE wbs_tasks SET status=?, progress=? WHERE id=?', (target, prog, parent_id))
            db.commit()
            if not quiet:
                log_operation('WBS', '父任务自动汇总',
                              f'父任务#{parent_id}「{p["name"]}」{cur_st}/{cur_pg}% → {target}/{prog}%（按子任务汇总）',
                              session.get('username', ''))
        gp = db.execute('SELECT parent_id FROM wbs_tasks WHERE id=?', (parent_id,)).fetchone()
        if gp and gp['parent_id']:
            _rollup_by_parent(db, gp['parent_id'], quiet=quiet)
    except Exception as e:
        print('=== _rollup_by_parent ===', e)


def _reconcile_project_rollups(db, project_id):
    """进详情页时按子任务对账全部父任务（含阶段行、多层父任务）。
    一次性读全项目任务在内存里自底向上推导，只把与推导不一致的行批量写回；
    数据本来就一致时零写入，不产生日志。用于自愈历史遗留的"父任务卡在已完成"等不一致。"""
    try:
        rows = db.execute('SELECT id, parent_id, status, progress FROM wbs_tasks WHERE project_id=?',
                          (project_id,)).fetchall()
        if not rows:
            return
        cur = {}
        orig = {}
        kids_of = {}
        for r in rows:
            cur[r['id']] = [(r['status'] or '未开始'), int(r['progress'] or 0)]
            orig[r['id']] = (cur[r['id']][0], cur[r['id']][1])
            if r['parent_id']:
                kids_of.setdefault(r['parent_id'], []).append(r['id'])
        # 先纠正自相矛盾的任务行（历史数据里常见「未开始 30%」「已完成 60%」）：
        # 有进度却未开始 → 进行中；进度到 100 却没收尾 → 已完成；已完成但进度不满 → 补到 100
        for tid, (st, prog) in list(cur.items()):
            if st == '已取消':
                continue
            if st == '未开始' and prog > 0:
                cur[tid] = ['进行中', prog]
            elif prog >= 100 and st != '已完成':
                cur[tid] = ['已完成', 100]
            elif st == '已完成' and prog < 100:
                cur[tid] = ['已完成', 100]
        if not kids_of:
            return
        changed = []
        # 最多向上推导 6 层（阶段→任务→子任务的实际深度远小于此），稳定即停
        for _ in range(6):
            dirty = False
            for pid, kid_ids in kids_of.items():
                kids = [{'status': cur[k][0], 'progress': cur[k][1]} for k in kid_ids if k in cur]
                st, prog = _rollup_target(kids)
                if st is None:
                    continue
                old = cur.get(pid)
                if old is None:
                    continue
                if old[0] != st or old[1] != prog:
                    cur[pid] = [st, prog]
                    dirty = True
            if not dirty:
                break
        for pid, (st, prog) in cur.items():
            if orig.get(pid) != (st, prog):
                changed.append((st, prog, pid))
        if changed:
            db.executemany('UPDATE wbs_tasks SET status=?, progress=? WHERE id=?', changed)
            db.commit()
            log_operation('WBS', '父任务自动汇总',
                          f'打开项目详情时按子任务对账，纠正 {len(changed)} 条父任务的状态/进度',
                          session.get('username', ''))
    except Exception as e:
        print('=== _reconcile_project_rollups ===', e)


def _rollup_parent_status(db, tid):
    """v13.0.0.22：某条任务状态变化后，重算它的所有上级。"""
    try:
        t = db.execute('SELECT parent_id FROM wbs_tasks WHERE id=?', (tid,)).fetchone()
        if t and t['parent_id']:
            _rollup_by_parent(db, t['parent_id'])
    except Exception:
        pass


def _sync_parent_dates(db, child_id):
    """子任务时间变更后，自动把父任务时间扩展到覆盖『父任务自身 + 所有直接子任务』的时间并集（只会扩张，不会收缩）"""
    child = db.execute('SELECT * FROM wbs_tasks WHERE id=?', (child_id,)).fetchone()
    if not child or not child['parent_id']:
        return
    pid = child['project_id']
    parent_id = child['parent_id']
    # 取父任务自身 + 所有直接子任务中非空的最早开始和最晚结束
    rows = db.execute('''SELECT MIN(plan_start) as ms, MAX(plan_end) as me FROM (
                             SELECT plan_start, plan_end FROM wbs_tasks
                             WHERE parent_id=? AND plan_start!='' AND plan_end!=''
                             UNION ALL
                             SELECT plan_start, plan_end FROM wbs_tasks
                             WHERE id=? AND plan_start!='' AND plan_end!=''
                         )''', (parent_id, parent_id)).fetchone()
    if rows and rows['ms']:
        db.execute('UPDATE wbs_tasks SET plan_start=?, plan_end=? WHERE id=?',
                   (rows['ms'], rows['me'], parent_id))
        db.commit()
        # 递归往上同步祖父任务
        _sync_parent_dates(db, parent_id)


@blueprint.route('/wbs/<int:pid>/task/quickadd', methods=['POST'])
def wbs_task_quickadd(pid):
    """Excel风格快速新建：按WBS编号自动判断父子层级。
    v13.0.0.21：权限放开给项目负责人（owner）；参与人员仍只能通过导入 Excel 添加。
    管理员建的任务直接「已通过」；负责人建的任务进「待审批」，与导入 Excel 的规则一致。"""
    if not _wbs_is_project_owner(get_db(), pid):
        return jsonify({'ok': False, 'msg': '仅项目负责人、管理员可快速新建任务，其他成员可通过导入Excel添加'}), 403
    db = get_db()
    code = (request.form.get('wbs_code') or '').strip()
    name = (request.form.get('name') or '').strip()
    if not code or not name:
        return jsonify({'ok': False, 'msg': 'WBS号和任务名必填'}), 400
    dup = db.execute('SELECT id, name FROM wbs_tasks WHERE project_id=? AND wbs_code=?',
                     (pid, code)).fetchone()
    if dup:
        return jsonify({'ok': False, 'msg': f'WBS编号「{code}」已存在（任务：{dup["name"]}），请修改编号'}), 400
    parent_id = 0
    if '.' in code:
        parent_code = code.rsplit('.', 1)[0]
        parent = db.execute('SELECT id FROM wbs_tasks WHERE project_id=? AND wbs_code=?',
                            (pid, parent_code)).fetchone()
        if parent:
            parent_id = parent['id']
    if _wbs_is_admin():
        approval = '已通过'
    else:
        proj = db.execute('SELECT approval_status FROM wbs_projects WHERE id=?', (pid,)).fetchone()
        if not proj or proj['approval_status'] != '已通过':
            return jsonify({'ok': False, 'msg': '项目待审批通过后才能新增任务'}), 403
        approval = '待审批'
    task_managed = (approval == '待审批') and _wf_managed('wbs.task.created')
    db.execute('''INSERT INTO wbs_tasks
        (project_id, wbs_code, parent_id, name, work_content, owner, collaborators,
         plan_start, plan_end, deliverable, status, progress, approval_status, remark)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
        (pid, code, parent_id, name,
         request.form.get('work_content',''), _to_username(request.form.get('owner','')),
         request.form.get('collaborators',''), request.form.get('plan_start',''),
         request.form.get('plan_end',''), request.form.get('deliverable',''),
         '未开始', 0, approval, request.form.get('remark','')))
    new_id = db.execute('SELECT last_insert_rowid()').fetchone()[0]
    db.commit()
    _sync_parent_dates(db, new_id)
    db.commit()
    log_operation('WBS', '快速新建', f'P{pid} 快速新建任务 WBS#{code}「{name}」(tid={new_id})',
                  session.get('username', ''))
    _proj = db.execute('SELECT name FROM wbs_projects WHERE id=?', (pid,)).fetchone()
    if approval == '待审批':   # v13.5：管理员建任务直接通过，不发起审批
        _emit_wf('wbs.task.created', business_key='WBS-T%d' % new_id,
                 title='任务「%s」创建' % name,
                 context={'task_name': name, 'wbs_code': code,
                          'owner': _to_username(request.form.get('owner', '')),
                          'project_name': _proj['name'] if _proj else '', 'project_id': pid,
                          'status': '未开始', 'progress': 0},
                 record_type='task', record_id=new_id, start_now=task_managed)
    # v13.0.0.19：返回新任务 id，前端新建成功后直接跳转并高亮该行
    return jsonify({'ok': True, 'tid': new_id, 'pending': (approval == '待审批')})


@blueprint.route('/wbs/project/<int:pid>/tasks/approve', methods=['POST'])
def wbs_project_tasks_approve(pid):
    """管理员批量审批整个项目下所有待审批任务"""
    if session.get('user_role') not in ('admin', 'platform_admin'):
        return jsonify({'ok': False, 'msg': '无权限'}), 403
    # v13.5：任务审批已托管给流程引擎时，不允许业务页批量放行
    if _wf_managed('wbs.task.created'):
        return jsonify({'ok': False,
                        'msg': '任务审批已由流程引擎接管，请在流程中心逐条审批'}), 403
    db = get_db()
    action = request.form.get('action','')
    status = '已通过' if action == 'approve' else '已驳回'
    cur = db.execute('UPDATE wbs_tasks SET approval_status=? WHERE project_id=? AND approval_status=?',
                     (status, pid, '待审批'))
    db.commit()
    return jsonify({'ok': True, 'count': cur.rowcount})


@blueprint.route('/wbs/project/<int:pid>/tasks/resubmit', methods=['POST'])
def wbs_project_tasks_resubmit(pid):
    """普通用户：任务被驳回后，一键重新提交本项目下所有已驳回任务，进入待审批"""
    if session.get('user_role') in ('admin', 'platform_admin'):
        return jsonify({'ok': False, 'msg': '管理员无需重新提交'}), 400
    db = get_db()
    rows = db.execute("SELECT id, name, owner FROM wbs_tasks WHERE project_id=? AND approval_status='已驳回'",
                      (pid,)).fetchall()
    cur = db.execute("UPDATE wbs_tasks SET approval_status='待审批' WHERE project_id=? AND approval_status='已驳回'",
                     (pid,))
    db.commit()
    # v13.5：托管审批时，逐条重新发起流程，否则任务会永远停在待审批
    if rows and _wf_managed('wbs.task.created'):
        proj = db.execute('SELECT name FROM wbs_projects WHERE id=?', (pid,)).fetchone()
        for r in rows:
            _emit_wf('wbs.task.created', business_key='WBS-T%d' % r['id'],
                     title='任务「%s」重新提交审批' % (r['name'] or r['id']),
                     context={'task_name': r['name'] or '', 'owner': r['owner'] or '',
                              'project_name': proj['name'] if proj else '', 'project_id': pid},
                     record_type='task', record_id=r['id'], start_now=True)
    return jsonify({'ok': True, 'count': cur.rowcount})


@blueprint.route('/wbs/task/<int:tid>/notes_data')
def wbs_task_notes_data(tid):
    import json
    db = get_db()
    t = db.execute('SELECT notes, approval_status, project_id FROM wbs_tasks WHERE id=?', (tid,)).fetchone()
    if not t:
        return jsonify({'notes': [], 'approval_ok': False})
    try:
        notes = json.loads(t['notes']) if t['notes'] else []
    except Exception:
        notes = []
    approved = t['approval_status'] == '已通过'
    if not _wbs_is_admin():
        okp, _ = _wbs_project_approved(db, t['project_id'])
        approved = approved and okp
    return jsonify({'notes': notes, 'approval_ok': approved})


@blueprint.route('/wbs/task/<int:tid>/note', methods=['POST'])
def wbs_task_add_note(tid):
    """追加进展流水，不覆盖历史。管理员或项目负责人/参与人员可添加（任务需审批通过）。"""
    import json
    db = get_db()
    t = db.execute('SELECT * FROM wbs_tasks WHERE id=?', (tid,)).fetchone()
    if not t:
        return jsonify({'ok': False, 'msg': '任务不存在'}), 404
    if t['approval_status'] != '已通过':
        return jsonify({'ok': False, 'msg': '任务审批通过后才能添加进展'}), 403
    ok, msg = _wbs_project_editable(db, t['project_id'])
    if not ok:
        return jsonify({'ok': False, 'msg': msg}), 403
    if session.get('user_role') not in ('admin', 'platform_admin'):
        okp, msgp = _wbs_project_approved(db, t['project_id'])
        if not okp:
            return jsonify({'ok': False, 'msg': msgp}), 403
    data = request.get_json(silent=True) or {}
    content = (data.get('content') or '').strip()
    if not content:
        return jsonify({'ok': False, 'msg': '内容为空'}), 400
    notes = []
    try:
        notes = json.loads(t['notes']) if t['notes'] else []
    except Exception:
        notes = []
    notes.append({
        'date': datetime.now().strftime('%Y-%m-%d %H:%M'),
        'author': session.get('username', '未登录'),
        'content': content,
    })
    db.execute('UPDATE wbs_tasks SET notes=? WHERE id=?', (json.dumps(notes, ensure_ascii=False), tid))
    db.commit()
    return jsonify({'ok': True, 'count': len(notes), 'last': notes[-1]})


@blueprint.route('/wbs/task/<int:tid>/note/update', methods=['POST'])
def wbs_task_update_note(tid):
    """编辑已登记的进展流水（按索引更新内容，保留登记时间和作者）。任务审批通过后（管理员或参与人）才能编辑。"""
    import json
    db = get_db()
    t = db.execute('SELECT * FROM wbs_tasks WHERE id=?', (tid,)).fetchone()
    if not t:
        return jsonify({'ok': False, 'msg': '任务不存在'}), 404
    if t['approval_status'] != '已通过':
        return jsonify({'ok': False, 'msg': '任务审批通过后才能编辑进展'}), 403
    ok, msg = _wbs_project_editable(db, t['project_id'])
    if not ok:
        return jsonify({'ok': False, 'msg': msg}), 403
    data = request.get_json(silent=True) or {}
    try:
        idx = int(data.get('index', -1))
    except (TypeError, ValueError):
        return jsonify({'ok': False, 'msg': '无效的记录序号'}), 400
    content = (data.get('content') or '').strip()
    if not content:
        return jsonify({'ok': False, 'msg': '内容为空'}), 400
    notes = []
    try:
        notes = json.loads(t['notes']) if t['notes'] else []
    except Exception:
        notes = []
    if idx < 0 or idx >= len(notes):
        return jsonify({'ok': False, 'msg': '记录不存在'}), 404
    old = notes[idx]
    notes[idx] = {'date': old.get('date', ''), 'author': old.get('author', ''), 'content': content}
    db.execute('UPDATE wbs_tasks SET notes=? WHERE id=?', (json.dumps(notes, ensure_ascii=False), tid))
    db.commit()
    log_operation('WBS', '编辑进展', f'任务#{tid} 第{idx + 1}条进展已更新', session.get('username', ''))
    return jsonify({'ok': True, 'count': len(notes), 'updated': notes[idx]})


@blueprint.route('/wbs/<int:pid>/snapshot/<path:fname>')
def wbs_snapshot_download(pid, fname):
    """v13.0.0.21：下载导入前自动生成的计划快照"""
    db = get_db()
    ok, msg = _wbs_project_editable(db, pid)
    if not ok:
        flash(msg, 'error')
        return redirect(url_for('wbs.wbs_project_detail', pid=pid))
    safe = os.path.basename(fname)
    if not safe.lower().endswith('.xlsx') or not os.path.isfile(os.path.join(_snapshot_dir(), safe)):
        flash('快照文件不存在或已被清理（同一项目只保留最近 20 份）', 'error')
        return redirect(url_for('wbs.wbs_project_detail', pid=pid))
    return send_from_directory(_snapshot_dir(), safe, as_attachment=True)


@blueprint.route('/wbs/task/<int:tid>/upload', methods=['POST'])
def wbs_task_upload(tid):
    """上传产出物文件，追加到 attachment_path（多个用分号分隔）。管理员或项目负责人/参与人员可上传（任务需审批通过）。"""
    db = get_db()
    t = db.execute('SELECT * FROM wbs_tasks WHERE id=?', (tid,)).fetchone()
    if not t:
        return jsonify({'ok': False, 'msg': '任务不存在'}), 404
    if t['approval_status'] != '已通过':
        return jsonify({'ok': False, 'msg': '任务审批通过后才能上传产出物'}), 403
    ok, msg = _wbs_project_editable(db, t['project_id'])
    if not ok:
        return jsonify({'ok': False, 'msg': msg}), 403
    f = request.files.get('file')
    if not f or not f.filename:
        return jsonify({'ok': False, 'msg': '未收到文件'}), 400
    # 单文件大小校验（multipart 下 file.content_length 不可靠，用请求体总大小近似）
    req_len = request.content_length or 0
    if req_len > MAX_UPLOAD_BYTES + 2048:
        return jsonify({'ok': False, 'msg': f'文件超过 50MB 上限（约 {req_len // 1024 // 1024}MB），请压缩后再上传'}), 413
    # 单任务附件数校验
    existing = [x for x in (t['attachment_path'] or '').split(';') if x]
    if len(existing) >= MAX_TASK_ATTACHMENTS:
        return jsonify({'ok': False, 'msg': f'单任务最多上传 {MAX_TASK_ATTACHMENTS} 个附件，请先删除不需要的'}), 413
    attach = _save_upload(f, 'wbs')
    if not attach:
        return jsonify({'ok': False, 'msg': '未收到文件'}), 400
    old = t['attachment_path'] or ''
    new_path = (old + ';' + attach) if old else attach
    db.execute('UPDATE wbs_tasks SET attachment_path=? WHERE id=?', (new_path, tid))
    db.commit()
    fname = attach.split('/')[-1]
    log_operation('WBS', '上传附件', f'任务#{tid}「{t["name"]}」上传附件 {fname}',
                  session.get('username', ''))
    return jsonify({'ok': True, 'path': attach, 'name': fname})


@blueprint.route('/wbs/task/<int:tid>/del_attachment', methods=['POST'])
def wbs_task_del_attachment(tid):
    """删除指定产出物文件（应对传错文件后替换场景）。权限与上传一致。"""
    db = get_db()
    t = db.execute('SELECT * FROM wbs_tasks WHERE id=?', (tid,)).fetchone()
    if not t:
        return jsonify({'ok': False, 'msg': '任务不存在'}), 404
    if t['approval_status'] != '已通过':
        return jsonify({'ok': False, 'msg': '任务审批通过后才能删除产出物'}), 403
    ok, msg = _wbs_project_editable(db, t['project_id'])
    if not ok:
        return jsonify({'ok': False, 'msg': msg}), 403
    target = (request.form.get('filename') or '').strip()
    files = [f for f in (t['attachment_path'] or '').split(';') if f]
    if target not in files:
        return jsonify({'ok': False, 'msg': '产出物不存在或已被删除'}), 404
    # 物理删除文件（相对 uploads 目录），文件已不存在也允许清理记录
    try:
        os.remove(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'uploads', target))
    except OSError:
        pass
    files.remove(target)
    db.execute('UPDATE wbs_tasks SET attachment_path=? WHERE id=?', (';'.join(files), tid))
    db.commit()
    log_operation('WBS', '删除附件', f'任务#{tid}「{t["name"]}」删除附件 {target.split("/")[-1]}',
                  session.get('username', ''))
    return jsonify({'ok': True})


@blueprint.route('/wbs/template')
def wbs_template():
    """下载 Excel 导入模板（与截图同格式）"""
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from io import BytesIO
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = 'WBS计划'
    headers = ['WBS编号','阶段/任务','工作内容','负责人','协作/支撑','计划开始(YYYY-MM-DD)','计划结束(YYYY-MM-DD)','交付物/判定标准','状态','进度','备注']
    header_fill = PatternFill('solid', fgColor='2B579A')
    header_font = Font(color='FFFFFF', bold=True, size=11)
    thin = Side(style='thin', color='D0D7E5')
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    for i, h in enumerate(headers, 1):
        c = ws.cell(row=1, column=i, value=h)
        c.fill = header_fill; c.font = header_font
        c.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
        c.border = border
    widths = [10, 22, 30, 10, 16, 12, 12, 22, 10, 8, 20]
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[openpyxl.utils.get_column_letter(i)].width = w
    # v13.0.0.22（P0）：模板里的编号列也加批注，避免用户把改编号当成"改任务"
    try:
        from openpyxl.comments import Comment
        ws['A1'].comment = Comment(
            '编号列请勿修改：导入按编号匹配已有任务。要改编号，请在系统任务表格里对着编号单元格直接改，'
            '产出物和进展都会保留。', '业务系统', width=330, height=120)
    except Exception:
        pass
    # 示例行（含状态/进度；状态可选：未开始/进行中/已完成/阻塞，进度 0-100）
    samples = [
        [1, '阶段一·资源诉求（输入梳理）', '汇总各渠道输入，为问题定位提供基线', '', '', '2026-10-12', '2026-10-16', '', '进行中', 30, ''],
        ['1.1', '在轨测试情况', '统计切换性能的比对情况（切换失败率、切换时延）', '余丽莎', '谭富元、李雪', '2026-10-12', '2026-10-16', '数据统计表/比对报告', '已完成', 100, ''],
        ['1.2', '质量测试情况', '实验室入网性能/切换性能的测试结果收集', '袁谅', '', '2026-10-12', '2026-10-16', '测试结果记录', '未开始', 0, ''],
        [2, '阶段二·问题现象汇总分析', '整合阶段一输入，复现问题、定位根因', '', '', '2026-10-16', '2026-10-22', '', '未开始', 0, ''],
        ['2.1', '问题现象汇总分析', '汇总失败率偏高与切换时延偏长的现象', '余丽莎', '袁谅、谭富元', '2026-10-16', '2026-10-19', '问题现象汇总表', '未开始', 0, ''],
    ]
    for r, row in enumerate(samples, 2):
        for c, v in enumerate(row, 1):
            cell = ws.cell(row=r, column=c, value=v)
            cell.border = border
            cell.alignment = Alignment(vertical='center', wrap_text=True)
            if '.' not in str(row[0]):
                cell.fill = PatternFill('solid', fgColor='D6E4F5')
    # 日期格式说明批注（v12.7.6：时间格式容错提示）
    try:
        from openpyxl.comments import Comment
        for rr in range(2, 2 + len(samples)):
            c6 = ws.cell(row=rr, column=6)
            c7 = ws.cell(row=rr, column=7)
            note = Comment('日期支持：2026-11-05 / 2026/11/05 / 2026.11.05 / 2026年11月5日 / 20261105；'
                           '也支持 Excel 日期类型直接填写。', '业务高效运转系统', height=90, width=320)
            c6.comment = note
            c7.comment = note
    except Exception:
        pass
    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    return _send_bytes(buf.getvalue(), 'WBS_template.xlsx')


def _build_wbs_workbook(db, pid):
    """v13.0.0.21：导出与导入前快照共用同一套工作簿构建逻辑，保证快照可直接用于导入。"""
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    p = db.execute('SELECT * FROM wbs_projects WHERE id=?', (pid,)).fetchone()
    if not p:
        return None, ''
    tasks = db.execute('SELECT * FROM wbs_tasks WHERE project_id=?', (pid,)).fetchall()
    tasks = sorted(tasks, key=lambda t: (_natural_key(t['wbs_code'] or ''), t['id']))
    wb = openpyxl.Workbook(); ws = wb.active; ws.title = 'WBS计划'
    # v12.7.10.12：补上页面有、导出缺的两列「进展」「产出物」。
    # 注意：导入是按列位置解析的（第9列必须是状态关键字），因此新列只能追加在最后。
    headers = ['WBS编号','阶段/任务','工作内容','负责人','协作/支撑','计划开始','计划结束','交付物/判定标准','状态','进度','备注','进展','产出物']
    header_fill = PatternFill('solid', fgColor='2B579A')
    thin = Side(style='thin', color='D0D7E5')
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    for i, h in enumerate(headers, 1):
        c = ws.cell(row=1, column=i, value=h)
        c.fill = header_fill; c.font = Font(color='FFFFFF', bold=True, size=11)
        c.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True); c.border = border
    widths = [10, 22, 30, 10, 16, 12, 12, 22, 10, 8, 20, 42, 24]
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[openpyxl.utils.get_column_letter(i)].width = w
    # v13.0.0.22（P0）：编号列加批注，提醒导入时按编号匹配、编号不能在这列改
    try:
        from openpyxl.comments import Comment
        ws['A1'].comment = Comment(
            '导入时系统按本列编号匹配任务：编号请勿修改（改编号 = 新增一条 + 老任务连同产出物留在原编号）。'
            '要调整编号，请在系统任务表格里对着编号单元格直接改。', '业务系统', width=340, height=130)
    except Exception:
        pass
    r = 2
    for t in tasks:
        is_phase = '.' not in (t['wbs_code'] or '')
        notes_txt = _notes_export_text(t['notes'])
        vals = [t['wbs_code'], _xlsx_safe(t['name']), _xlsx_safe(t['work_content']), _xlsx_safe(t['owner'] or ''), _xlsx_safe(t['collaborators'] or ''),
                t['plan_start'] or '', t['plan_end'] or '', _xlsx_safe(t['deliverable'] or ''),
                t['status'] or '未开始', t['progress'] or 0, _xlsx_safe(t['remark'] or ''),
                _xlsx_safe(notes_txt), _xlsx_safe(_attach_export_text(t['attachment_path']))]
        for c, v in enumerate(vals, 1):
            cell = ws.cell(row=r, column=c, value=v)
            cell.border = border
            cell.alignment = Alignment(vertical='center', wrap_text=True)
            if is_phase:
                cell.fill = PatternFill('solid', fgColor='D6E4F5')
        n_lines = notes_txt.count('\n') + 1 if notes_txt else 1
        if n_lines > 1:
            ws.row_dimensions[r].height = min(15.0 * n_lines + 3, 160)
        r += 1
    return wb, (p['name'] or ('项目%d' % pid))


def _snapshot_dir():
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), 'uploads', 'snapshots')


def _save_import_snapshot(db, pid):
    """v13.0.0.21：导入前把当前计划落一份 xlsx 快照到服务器，返回文件名（失败返回空串）。
    同一个项目只保留最近 20 份。"""
    try:
        wb, pname = _build_wbs_workbook(db, pid)
        if wb is None:
            return ''
        from io import BytesIO
        buf = BytesIO(); wb.save(buf)
        d = _snapshot_dir(); os.makedirs(d, exist_ok=True)
        safe_name = ''.join(ch for ch in str(pname) if ch.isalnum() or ch in '._-（）')
        fname = 'WBS_%s_导入前快照_%s.xlsx' % (safe_name, datetime.now().strftime('%Y%m%d_%H%M%S'))
        with open(os.path.join(d, fname), 'wb') as fh:
            fh.write(buf.getvalue())
        prefix = 'WBS_%s_导入前快照_' % safe_name
        olds = sorted(f for f in os.listdir(d) if f.startswith(prefix) and f.endswith('.xlsx'))
        for f in olds[:-20]:
            try:
                os.remove(os.path.join(d, f))
            except OSError:
                pass
        return fname
    except Exception:
        return ''


@blueprint.route('/wbs/<int:pid>/export')
def wbs_export(pid):
    """导出当前项目WBS为Excel，含状态/进度列，与导入模板同格式。
    ?tag=preimport 时文件名标注「导入前快照」。"""
    db = get_db()
    wb, pname = _build_wbs_workbook(db, pid)
    if wb is None:
        return redirect(url_for('wbs.wbs_projects'))
    from io import BytesIO
    buf = BytesIO(); wb.save(buf); buf.seek(0)
    tag = (request.args.get('tag') or '').strip()
    if tag == 'preimport':
        fname = 'WBS_%s_导入前快照_%s.xlsx' % (pname, datetime.now().strftime('%Y%m%d_%H%M%S'))
    else:
        fname = 'WBS_%s.xlsx' % pname
    fname = fname.replace('/', '_')
    return _send_bytes(buf.getvalue(), fname)


def _notes_export_text(raw):
    """v12.7.10.12 把 wbs_tasks.notes（JSON 流水）合并成多行文本：时间 登记人：内容"""
    if not raw:
        return ''
    try:
        notes = json.loads(raw) if isinstance(raw, str) else raw
    except Exception:
        return ''
    if not isinstance(notes, list):
        return ''
    lines = []
    for n in notes:
        if not isinstance(n, dict):
            continue
        d = str(n.get('date') or '').strip()
        a = str(n.get('author') or '').strip()
        c = str(n.get('content') or '').replace('\r', '').strip()
        head = ' '.join(x for x in (d, a) if x)
        lines.append(f'{head}：{c}' if head else c)
    return '\n'.join(lines)


def _attach_export_text(raw):
    """v12.7.10.12 attachment_path（分号分隔，可能带目录）→ 只取文件名，用「、」连接"""
    if not raw:
        return ''
    out = []
    for x in str(raw).split(';'):
        x = x.strip()
        if not x:
            continue
        base = os.path.basename(x.replace('\\', '/'))
        out.append(base or x)
    return '、'.join(out)


def _xlsx_safe(v):
    """Excel 公式注入防御（CWE-1236）：以 = + - @ 开头的文本前置单引号强制按文本处理"""
    if isinstance(v, str) and v and v[0] in ('=', '+', '-', '@'):
        return "'" + v
    return v


def _send_bytes(content, filename):
    import tempfile
    from flask import send_file
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix='.xlsx')
    tmp.write(content); tmp.close()
    return send_file(tmp.name, as_attachment=True, download_name=filename,
                     mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
