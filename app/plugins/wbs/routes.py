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


def _fmt_date(v):
    """日期归一化：datetime/date/带时间的字符串 → YYYY-MM-DD（v12.5.11）"""
    from datetime import datetime, date
    if isinstance(v, (datetime, date)):
        return v.strftime('%Y-%m-%d')
    s = str(v or '').strip()
    return s[:10] if len(s) >= 10 else s


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
    rows = [dict(r, task_count=count_map.get(r['id'], (0,0))[0], phase_count=count_map.get(r['id'], (0,0))[1]) for r in rows]
    # 预警统计
    today = date.today().isoformat()
    soon3 = (date.today() + timedelta(days=3)).isoformat()
    soon7 = (date.today() + timedelta(days=7)).isoformat()
    overdue = db.execute("SELECT COUNT(*) FROM wbs_tasks WHERE plan_end IS NOT NULL AND plan_end!='' AND plan_end<? AND status NOT IN ('已完成','已取消') AND (parent_id>0 OR parent_id IS NULL)", (today,)).fetchone()[0]
    due3 = db.execute("SELECT COUNT(*) FROM wbs_tasks WHERE plan_end>=? AND plan_end<=? AND status NOT IN ('已完成','已取消') AND (parent_id>0 OR parent_id IS NULL)", (today, soon3)).fetchone()[0]
    due7 = db.execute("SELECT COUNT(*) FROM wbs_tasks WHERE plan_end>? AND plan_end<=? AND status NOT IN ('已完成','已取消') AND (parent_id>0 OR parent_id IS NULL)", (soon3, soon7)).fetchone()[0]
    return render_template('wbs_projects.html', rows=rows, overdue=overdue, due3=due3, due7=due7, today=today)


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
    overdue = db.execute('''SELECT t.*, p.name as project_name, p.id as project_id FROM wbs_tasks t
        JOIN wbs_projects p ON t.project_id=p.id
        WHERE t.plan_end!='' AND t.plan_end<? AND t.status NOT IN ('已完成','已取消') AND (t.parent_id>0 OR t.parent_id IS NULL)
          AND t.approval_status='已通过'
        ORDER BY t.plan_end ASC''', (today_s,)).fetchall()
    # 3天内
    due3 = db.execute('''SELECT t.*, p.name as project_name, p.id as project_id FROM wbs_tasks t
        JOIN wbs_projects p ON t.project_id=p.id
        WHERE t.plan_end>=? AND t.plan_end<=? AND t.status NOT IN ('已完成','已取消') AND (t.parent_id>0 OR t.parent_id IS NULL)
          AND t.approval_status='已通过'
        ORDER BY t.plan_end ASC''', (today_s, soon3)).fetchall()
    # 7天内
    due7 = db.execute('''SELECT t.*, p.name as project_name, p.id as project_id FROM wbs_tasks t
        JOIN wbs_projects p ON t.project_id=p.id
        WHERE t.plan_end>? AND t.plan_end<=? AND t.status NOT IN ('已完成','已取消') AND (t.parent_id>0 OR t.parent_id IS NULL)
          AND t.approval_status='已通过'
        ORDER BY t.plan_end ASC''', (soon3, soon7)).fetchall()
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
        approver = '' if is_admin else (request.form.get('approver') or '').strip()
        # v12.5.26 漏洞修复：普通用户提交审批必须选择审批人（后端强校验，防止绕过表单直建）
        if not is_admin and not approver:
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
        if is_admin:
            return redirect(url_for('wbs.wbs_project_detail', pid=pid))
        else:
            flash('项目已提交，等待管理员审批通过后才能导入任务。', 'success')
            return redirect(url_for('wbs.wbs_projects'))
    return render_template('wbs_project_form.html', p=None, admins=admins, users=users,
                           can_change_members=True,
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
        if session.get('user_role') in ('admin', 'platform_admin'):
            owner = ','.join(request.form.getlist('owner')) or (p['owner'] or '')
            participants = ','.join(request.form.getlist('participants')) or (p['participants'] or '')
        else:
            # v12.5.11：人员名单变更仅管理员，防止成员自行拉人造成权限扩散
            owner = p['owner'] or ''
            participants = p['participants'] or ''
        db.execute('''UPDATE wbs_projects SET name=?, description=?, owner=?, participants=?,
            start_date=?, end_date=?, updated_at=? WHERE id=?''',
            (request.form.get('name',p['name']), request.form.get('description',p['description']),
             owner, participants,
             request.form.get('start_date',p['start_date']), request.form.get('end_date',p['end_date']),
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
    db = get_db()
    action = request.form.get('action','')
    status = '已通过' if action == 'approve' else '已驳回'
    db.execute('UPDATE wbs_projects SET approval_status=?, updated_at=datetime("now","localtime") WHERE id=?', (status, pid))
    db.commit()
    log_operation('WBS', '项目审批', f'项目#{pid} → {status}', session.get('username','admin'))
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
    tasks = db.execute('SELECT * FROM wbs_tasks WHERE project_id=?', (pid,)).fetchall()
    # 自然排序：按 WBS 编号的数字分段排序（1.10 排在 1.2 之后），同编号按 id 稳定
    tasks = sorted(tasks, key=lambda t: (_natural_key(t['wbs_code'] or ''), t['id']))
    # 分组：父任务（阶段）和子任务
    phases = [t for t in tasks if t['parent_id'] == 0]
    pending_count = db.execute("SELECT COUNT(*) FROM wbs_tasks WHERE project_id=? AND approval_status='待审批'", (pid,)).fetchone()[0]
    rejected_count = db.execute("SELECT COUNT(*) FROM wbs_tasks WHERE project_id=? AND approval_status='已驳回'", (pid,)).fetchone()[0]
    can_edit, _ = _wbs_project_editable(db, pid)
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
    return render_template('wbs_project_detail.html', p=p, tasks=tasks, phases=phases,
                           pending_count=pending_count, rejected_count=rejected_count,
                           can_edit=can_edit, attach_mb=attach_mb, attach_count=attach_count)


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
    dup = db.execute('SELECT id, name FROM wbs_tasks WHERE project_id=? AND wbs_code=?',
                     (pid, code)).fetchone() if code else None
    if dup:
        flash(f'WBS编号「{code}」已存在（任务：{dup["name"]}），请使用不同编号', 'error')
        return redirect(request.referrer or url_for('wbs.wbs_projects'))
    parent_id = request.form.get('parent_id', 0) or 0
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
        (pid, request.form.get('wbs_code',''), parent_id, request.form.get('name',''),
         request.form.get('work_content',''), request.form.get('owner',''), request.form.get('collaborators',''),
         request.form.get('plan_start',''), request.form.get('plan_end',''),
         request.form.get('deliverable',''), request.form.get('priority','P2中'),
         attach, request.form.get('status','未开始'), int(request.form.get('progress',0))))
    db.commit()
    log_operation('WBS', '新增任务', f'P{pid} {request.form.get("name","")}', session.get('username','admin'))
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
        ok, msg = _wbs_project_editable(db, pid)
        if not ok:
            flash('只有项目负责人/参与人员或管理员可以导入任务', 'error')
            return redirect(url_for('wbs.wbs_project_detail', pid=pid))
        if proj['approval_status'] != '已通过':
            flash('项目待审批通过后才能导入任务', 'error')
            return redirect(url_for('wbs.wbs_project_detail', pid=pid))
    f = request.files.get('file')
    if not f:
        flash('请选择文件', 'error')
        return redirect(url_for('wbs.wbs_project_detail', pid=pid))
    import openpyxl
    wb = openpyxl.load_workbook(f, data_only=True)
    ws = wb.active
    db = get_db()
    is_admin = session.get('user_role') in ('admin', 'platform_admin')
    approval = '已通过' if is_admin else '待审批'
    # 已存在的 WBS 编号 → 任务 id（管理员更新用）
    existing = {}
    for r in db.execute('SELECT id, wbs_code FROM wbs_tasks WHERE project_id=?', (pid,)).fetchall():
        if r['wbs_code']:
            existing[r['wbs_code']] = r['id']
    inserted = []  # (row_id, wbs_code)
    updated = []   # (row_id, wbs_code)
    skip = 0
    for row in ws.iter_rows(min_row=2, values_only=True):  # 第1行表头
        if not row or not row[0]:
            continue
        code = str(row[0]).strip()
        name = str(row[1] or '').strip()
        if not name:
            continue
        work_content = str(row[2] or '').strip()
        owner = str(row[3] or '').strip()
        collab = str(row[4] or '').strip()
        p_start = _fmt_date(row[5])
        p_end = _fmt_date(row[6])
        deliverable = str(row[7] or '').strip()
        # 新旧格式识别：第9列（索引8）是状态关键字 → 新 12 列格式（状态|进度|备注）
        # 旧 9 列格式：第9列（索引8）是备注（任意文本）
        is_new = len(row) > 8 and str(row[8]).strip() in ('未开始', '进行中', '已完成', '阻塞')
        if is_new:
            status = str(row[8]).strip()
            remark = str(row[10] or '').strip() if len(row) > 10 else ''
            raw_p = (str(row[9]).strip() if row[9] is not None else '')
            try:
                progress = int(float(raw_p.rstrip('%')))
            except Exception:
                progress = 0
        else:
            status = ''
            remark = str(row[8] or '').strip() if len(row) > 8 else ''
            progress = 0
        if status not in ('未开始', '进行中', '已完成', '阻塞'):
            status = '未开始'
        progress = max(0, min(100, progress))
        if code in existing:
            if is_admin:
                # 批量更新：替换内容、状态、进度、备注（保留审批状态、进展、产出物、附件）
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
    if is_admin:
        parts = [f'新增 {len(inserted)} 条']
        if updated:
            parts.append(f'更新 {len(updated)} 条')
        if skip:
            parts.append(f'跳过 {skip} 条')
        flash('导入完成：' + '，'.join(parts) + '。', 'success')
        log_operation('WBS', '导入任务', f'P{pid} 新增{len(inserted)} 更新{len(updated)} 跳过{skip}', session.get('username','admin'))
    else:
        flash(f'成功导入 {len(inserted)} 条任务' + (f'，跳过 {skip} 条已存在的任务' if skip else '')
              + '，等待管理员审批通过后才能编辑状态、进度、进展和产出物。', 'success')
    return redirect(url_for('wbs.wbs_project_detail', pid=pid))


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
    data = request.get_json(force=True)
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
    old_value = t[field]
    db.execute(f'UPDATE wbs_tasks SET {field}=? WHERE id=?', (value, tid))
    db.commit()
    if field in ('plan_start', 'plan_end'):
        _sync_parent_dates(db, tid)
    disp = {'progress': lambda v: f'{v}%'}.get(field, lambda v: v)
    log_operation('WBS', '行内编辑', f'任务#{tid}「{t["name"]}」{field}: {disp(old_value)} → {disp(value)}',
                  session.get('username', ''))
    return jsonify({'ok': True, 'value': value})


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
    """Excel风格快速新建：按WBS编号自动判断父子层级。仅管理员/平台管理员可用
    （普通用户（含项目负责人/参与人员）通过导入Excel添加任务）。"""
    if session.get('user_role') not in ('admin', 'platform_admin'):
        return jsonify({'ok': False, 'msg': '仅管理员和平台管理员可快速新建任务，普通用户可通过导入Excel添加任务'}), 403
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
    approval = '已通过'
    db.execute('''INSERT INTO wbs_tasks
        (project_id, wbs_code, parent_id, name, work_content, owner, collaborators,
         plan_start, plan_end, deliverable, status, progress, approval_status, remark)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
        (pid, code, parent_id, name,
         request.form.get('work_content',''), request.form.get('owner',''),
         request.form.get('collaborators',''), request.form.get('plan_start',''),
         request.form.get('plan_end',''), request.form.get('deliverable',''),
         '未开始', 0, approval, request.form.get('remark','')))
    new_id = db.execute('SELECT last_insert_rowid()').fetchone()[0]
    db.commit()
    _sync_parent_dates(db, new_id)
    db.commit()
    log_operation('WBS', '快速新建', f'P{pid} 快速新建任务 WBS#{code}「{name}」(tid={new_id})',
                  session.get('username', ''))
    return jsonify({'ok': True})


@blueprint.route('/wbs/project/<int:pid>/tasks/approve', methods=['POST'])
def wbs_project_tasks_approve(pid):
    """管理员批量审批整个项目下所有待审批任务"""
    if session.get('user_role') not in ('admin', 'platform_admin'):
        return jsonify({'ok': False, 'msg': '无权限'}), 403
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
    cur = db.execute("UPDATE wbs_tasks SET approval_status='待审批' WHERE project_id=? AND approval_status='已驳回'",
                     (pid,))
    db.commit()
    return jsonify({'ok': True, 'count': cur.rowcount})


@blueprint.route('/wbs/task/<int:tid>/notes_data')
def wbs_task_notes_data(tid):
    import json
    db = get_db()
    t = db.execute('SELECT notes, approval_status FROM wbs_tasks WHERE id=?', (tid,)).fetchone()
    if not t:
        return jsonify({'notes': [], 'approval_ok': False})
    try:
        notes = json.loads(t['notes']) if t['notes'] else []
    except Exception:
        notes = []
    return jsonify({'notes': notes, 'approval_ok': t['approval_status'] == '已通过'})


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
    data = request.get_json(force=True)
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
    data = request.get_json(force=True)
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
    headers = ['WBS编号','阶段/任务','工作内容','负责人','协作/支撑','计划开始','计划结束','交付物/判定标准','状态','进度','备注']
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
    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    return _send_bytes(buf.getvalue(), 'WBS_template.xlsx')


@blueprint.route('/wbs/<int:pid>/export')
def wbs_export(pid):
    """导出当前项目WBS为Excel，含状态/进度列，与导入模板同格式"""
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    db = get_db()
    p = db.execute('SELECT * FROM wbs_projects WHERE id=?', (pid,)).fetchone()
    if not p:
        return redirect(url_for('wbs.wbs_projects'))
    tasks = db.execute('SELECT * FROM wbs_tasks WHERE project_id=?', (pid,)).fetchall()
    tasks = sorted(tasks, key=lambda t: (_natural_key(t['wbs_code'] or ''), t['id']))
    wb = openpyxl.Workbook(); ws = wb.active; ws.title = 'WBS计划'
    headers = ['WBS编号','阶段/任务','工作内容','负责人','协作/支撑','计划开始','计划结束','交付物/判定标准','状态','进度','备注']
    header_fill = PatternFill('solid', fgColor='2B579A')
    thin = Side(style='thin', color='D0D7E5')
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    for i, h in enumerate(headers, 1):
        c = ws.cell(row=1, column=i, value=h)
        c.fill = header_fill; c.font = Font(color='FFFFFF', bold=True, size=11)
        c.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True); c.border = border
    widths = [10, 22, 30, 10, 16, 12, 12, 22, 10, 8, 20]
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[openpyxl.utils.get_column_letter(i)].width = w
    r = 2
    for t in tasks:
        is_phase = '.' not in (t['wbs_code'] or '')
        vals = [t['wbs_code'], _xlsx_safe(t['name']), _xlsx_safe(t['work_content']), _xlsx_safe(t['owner'] or ''), _xlsx_safe(t['collaborators'] or ''),
                t['plan_start'] or '', t['plan_end'] or '', _xlsx_safe(t['deliverable'] or ''),
                t['status'] or '未开始', t['progress'] or 0, _xlsx_safe(t['remark'] or '')]
        for c, v in enumerate(vals, 1):
            cell = ws.cell(row=r, column=c, value=v)
            cell.border = border
            cell.alignment = Alignment(vertical='center', wrap_text=True)
            if is_phase:
                cell.fill = PatternFill('solid', fgColor='D6E4F5')
        r += 1
    from io import BytesIO
    buf = BytesIO(); wb.save(buf); buf.seek(0)
    fname = f'WBS_{p["name"]}.xlsx'.replace('/', '_')
    return _send_bytes(buf.getvalue(), fname)


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
