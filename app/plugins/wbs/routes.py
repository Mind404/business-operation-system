from plugins.base import make_blueprint
from flask import g, render_template, request, redirect, url_for, flash, send_from_directory, jsonify, session
import sqlite3
from datetime import datetime, date, timedelta
import os, json, time, random, io
from plugin_system import log_operation

blueprint = make_blueprint('wbs', __name__)

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

# ============================================================
@blueprint.route('/wbs', strict_slashes=False)
def wbs_projects():
    from datetime import date, timedelta
    db = get_db()
    rows = db.execute('''SELECT p.*,
        (SELECT COUNT(*) FROM wbs_tasks t WHERE t.project_id=p.id) AS task_count,
        (SELECT COUNT(*) FROM wbs_tasks t WHERE t.project_id=p.id AND t.parent_id=0) AS phase_count
        FROM wbs_projects p ORDER BY p.id DESC''').fetchall()
    # 预警统计
    today = date.today().isoformat()
    soon3 = (date.today() + timedelta(days=3)).isoformat()
    soon7 = (date.today() + timedelta(days=7)).isoformat()
    overdue = db.execute("SELECT COUNT(*) FROM wbs_tasks WHERE plan_end IS NOT NULL AND plan_end!='' AND plan_end<? AND status!='已完成' AND parent_id>0", (today,)).fetchone()[0]
    due3 = db.execute("SELECT COUNT(*) FROM wbs_tasks WHERE plan_end>=? AND plan_end<=? AND status!='已完成' AND parent_id>0", (today, soon3)).fetchone()[0]
    due7 = db.execute("SELECT COUNT(*) FROM wbs_tasks WHERE plan_end>? AND plan_end<=? AND status!='已完成' AND parent_id>0", (soon3, soon7)).fetchone()[0]
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
        WHERE t.plan_end!='' AND t.plan_end<? AND t.status!='已完成' AND t.parent_id>0
          AND t.approval_status='已通过'
        ORDER BY t.plan_end ASC''', (today_s,)).fetchall()
    # 3天内
    due3 = db.execute('''SELECT t.*, p.name as project_name, p.id as project_id FROM wbs_tasks t
        JOIN wbs_projects p ON t.project_id=p.id
        WHERE t.plan_end>=? AND t.plan_end<=? AND t.status!='已完成' AND t.parent_id>0
          AND t.approval_status='已通过'
        ORDER BY t.plan_end ASC''', (today_s, soon3)).fetchall()
    # 7天内
    due7 = db.execute('''SELECT t.*, p.name as project_name, p.id as project_id FROM wbs_tasks t
        JOIN wbs_projects p ON t.project_id=p.id
        WHERE t.plan_end>? AND t.plan_end<=? AND t.status!='已完成' AND t.parent_id>0
          AND t.approval_status='已通过'
        ORDER BY t.plan_end ASC''', (soon3, soon7)).fetchall()
    return render_template('wbs_alerts.html', overdue=overdue, due3=due3, due7=due7, today=today_s)


@blueprint.route('/wbs/add', methods=['GET', 'POST'])
def wbs_project_add():
    db = get_db()
    admins = db.execute("SELECT username, display_name FROM users WHERE role IN ('admin','platform_admin') AND is_active=1 ORDER BY username").fetchall()
    if request.method == 'POST':
        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        is_admin = session.get('user_role') in ('admin', 'platform_admin')
        approval_status = '已通过' if is_admin else '待审批'
        approver = '' if is_admin else request.form.get('approver','')
        db.execute('''INSERT INTO wbs_projects (name, description, owner, participants, start_date, end_date, status, approver, approval_status, created_at, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)''',
            (request.form.get('name',''), request.form.get('description',''), request.form.get('owner',''),
             request.form.get('participants',''),
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
    return render_template('wbs_project_form.html', p=None, admins=admins)


@blueprint.route('/wbs/<int:pid>/edit', methods=['GET', 'POST'])
def wbs_project_edit(pid):
    if session.get('user_role') not in ('admin', 'platform_admin'):
        flash('只有管理员可以编辑项目信息', 'error')
        return redirect(url_for('wbs.wbs_projects'))
    db = get_db()
    p = db.execute('SELECT * FROM wbs_projects WHERE id=?', (pid,)).fetchone()
    if not p:
        return redirect(url_for('wbs.wbs_projects'))
    if request.method == 'POST':
        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        db.execute('''UPDATE wbs_projects SET name=?, description=?, owner=?, participants=?,
            start_date=?, end_date=?, updated_at=? WHERE id=?''',
            (request.form.get('name',p['name']), request.form.get('description',p['description']),
             request.form.get('owner',p['owner']), request.form.get('participants',p['participants'] or ''),
             request.form.get('start_date',p['start_date']), request.form.get('end_date',p['end_date']),
             now, pid))
        db.commit()
        return redirect(url_for('wbs.wbs_project_detail', pid=pid))
    return render_template('wbs_project_form.html', p=p)


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
    if p['approval_status'] != '已驳回':
        return jsonify({'ok': False, 'msg': '只有被驳回的项目可以重新提交'}), 400
    db.execute('''UPDATE wbs_projects SET approval_status='待审批', updated_at=datetime("now","localtime") WHERE id=?''', (pid,))
    db.commit()
    log_operation('WBS', '重新提交', f'项目#{pid} 重新提交审批', session.get('username','admin'))
    return jsonify({'ok': True})


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
    db.execute('DELETE FROM wbs_tasks WHERE project_id=?', (pid,))
    db.execute('DELETE FROM wbs_projects WHERE id=?', (pid,))
    db.commit()
    log_operation('WBS', '删除项目', f'项目#{pid}「{p["name"]}」(级联删除任务)', session.get('username','admin'))
    flash(f'项目「{p["name"]}」及其下所有任务已删除。', 'success')
    return redirect(url_for('wbs.wbs_projects'))


@blueprint.route('/wbs/<int:pid>')
def wbs_project_detail(pid):
    db = get_db()
    p = db.execute('SELECT * FROM wbs_projects WHERE id=?', (pid,)).fetchone()
    if not p:
        return redirect(url_for('wbs.wbs_projects'))
    tasks = db.execute('''SELECT * FROM wbs_tasks WHERE project_id=? ORDER BY wbs_code, id''', (pid,)).fetchall()
    # 分组：父任务（阶段）和子任务
    phases = [t for t in tasks if t['parent_id'] == 0]
    pending_count = db.execute("SELECT COUNT(*) FROM wbs_tasks WHERE project_id=? AND approval_status='待审批'", (pid,)).fetchone()[0]
    rejected_count = db.execute("SELECT COUNT(*) FROM wbs_tasks WHERE project_id=? AND approval_status='已驳回'", (pid,)).fetchone()[0]
    return render_template('wbs_project_detail.html', p=p, tasks=tasks, phases=phases,
                           pending_count=pending_count, rejected_count=rejected_count)


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
    db = get_db()
    parent_id = request.form.get('parent_id', 0) or 0
    attach = _save_upload(request.files.get('attachment'), 'wbs')
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
    if session.get('user_role') not in ('admin', 'platform_admin'):
        flash('只有管理员可以删除任务', 'error')
        return redirect(request.referrer or url_for('wbs.wbs_projects'))
    db = get_db()
    t = db.execute('SELECT * FROM wbs_tasks WHERE id=?', (tid,)).fetchone()
    if t:
        # 递归收集所有子孙任务id一并删除，避免留下孤儿任务
        def collect(pid):
            ids = [pid]
            for row in db.execute('SELECT id FROM wbs_tasks WHERE parent_id=?', (pid,)).fetchall():
                ids.extend(collect(row['id']))
            return ids
        ids = collect(tid)
        db.execute('DELETE FROM wbs_tasks WHERE id IN (%s)' % ','.join('?' * len(ids)), tuple(ids))
        db.commit()
    return redirect(url_for('wbs.wbs_project_detail', pid=t['project_id'] if t else 0))


@blueprint.route('/wbs/download/<path:filename>')
def wbs_download(filename):
    import os
    upload_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'uploads')
    return send_from_directory(upload_dir, filename)


@blueprint.route('/wbs/<int:pid>/import', methods=['POST'])
def wbs_import(pid):
    """按图2格式导入：WBS编号|阶段/任务|工作内容|负责人|协作支撑|计划开始|计划结束|交付物"""
    db = get_db()
    proj = db.execute('SELECT approval_status FROM wbs_projects WHERE id=?', (pid,)).fetchone()
    if not proj:
        flash('项目不存在', 'error')
        return redirect(url_for('wbs.wbs_projects'))
    if session.get('user_role') not in ('admin', 'platform_admin') and proj['approval_status'] != '已通过':
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
    # 已存在的 WBS 编号（查重）
    existing = set(r[0] for r in db.execute(
        'SELECT wbs_code FROM wbs_tasks WHERE project_id=?', (pid,)).fetchall())
    # 第一遍：全部插入（先不挂父子，避免 Excel 乱序导致父行未插入）
    inserted = []  # (row_id, wbs_code)
    count = 0
    skip = 0
    for row in ws.iter_rows(min_row=2, values_only=True):  # 第1行表头
        if not row or not row[0]:
            continue
        code = str(row[0]).strip()
        name = str(row[1] or '').strip()
        if not name:
            continue
        if code in existing:
            skip += 1
            continue
        existing.add(code)
        cur = db.execute('''INSERT INTO wbs_tasks
            (project_id, wbs_code, parent_id, name, work_content, owner, collaborators,
             plan_start, plan_end, deliverable, status, progress, approval_status, remark)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
            (pid, code, 0, name,
             str(row[2] or '').strip(), str(row[3] or '').strip(), str(row[4] or '').strip(),
             str(row[5] or '').strip(), str(row[6] or '').strip(), str(row[7] or '').strip(),
             '未开始', 0, approval,
             str(row[8] or '').strip() if len(row) > 8 else ''))
        inserted.append((cur.lastrowid, code))
        count += 1
    # 第二遍：按 WBS 编号补挂父子关系（此时父行必然已存在）
    for row_id, code in inserted:
        if '.' in code:
            parent_code = code.rsplit('.', 1)[0]
            parent = db.execute('SELECT id FROM wbs_tasks WHERE project_id=? AND wbs_code=?',
                                (pid, parent_code)).fetchone()
            if parent:
                db.execute('UPDATE wbs_tasks SET parent_id=? WHERE id=?', (parent['id'], row_id))
    db.commit()
    # 导入后同步父任务时间：让阶段时间自动覆盖所有子任务
    for row_id, code in inserted:
        _sync_parent_dates(db, row_id)
    if is_admin:
        flash(f'成功导入 {count} 条WBS任务' + (f'，跳过 {skip} 条已存在的任务' if skip else ''), 'success')
    else:
        flash(f'成功导入 {count} 条任务' + (f'，跳过 {skip} 条已存在的任务' if skip else '')
              + '，等待管理员审批通过后才能编辑状态、进度、进展和产出物。', 'success')
    return redirect(url_for('wbs.wbs_project_detail', pid=pid))


# ============ v9.4 行内编辑 AJAX 接口 ============

@blueprint.route('/wbs/task/<int:tid>/inline', methods=['POST'])
def wbs_task_inline(tid):
    """单元格行内保存。admin 可改全部字段；普通用户只能改已通过任务的 status/progress"""
    import json
    db = get_db()
    t = db.execute('SELECT * FROM wbs_tasks WHERE id=?', (tid,)).fetchone()
    if not t:
        return jsonify({'ok': False, 'msg': '任务不存在'}), 404
    data = request.get_json(force=True)
    field = data.get('field')
    value = data.get('value', '')
    admin = session.get('user_role') in ('admin', 'platform_admin')
    editable_admin = {'wbs_code','name','work_content','owner','collaborators',
                      'plan_start','plan_end','deliverable','status','progress','remark'}
    if admin:
        allowed = editable_admin
    else:
        # 普通用户：只有审批通过的任务才能改状态/进度/备注
        if t['approval_status'] != '已通过':
            return jsonify({'ok': False, 'msg': '任务待审批通过后才能编辑'}), 403
        allowed = {'status','progress','remark'}
    if field not in allowed:
        return jsonify({'ok': False, 'msg': '无权限修改此字段'}), 403
    if field == 'progress':
        try:
            value = max(0, min(100, int(value)))
        except Exception:
            value = 0
    db.execute(f'UPDATE wbs_tasks SET {field}=? WHERE id=?', (value, tid))
    db.commit()
    if field in ('plan_start', 'plan_end'):
        _sync_parent_dates(db, tid)
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
    """Excel风格快速新建：按WBS编号自动判断父子层级。仅管理员可用（普通用户只能通过导入Excel添加任务）。"""
    if session.get('user_role') not in ('admin', 'platform_admin'):
        return jsonify({'ok': False, 'msg': '普通用户只能通过导入Excel添加任务'}), 403
    db = get_db()
    code = (request.form.get('wbs_code') or '').strip()
    name = (request.form.get('name') or '').strip()
    if not code or not name:
        return jsonify({'ok': False, 'msg': 'WBS号和任务名必填'}), 400
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
    """追加进展流水，不覆盖历史。任务审批通过后（管理员或参与人）才能添加。"""
    import json
    db = get_db()
    t = db.execute('SELECT * FROM wbs_tasks WHERE id=?', (tid,)).fetchone()
    if not t:
        return jsonify({'ok': False, 'msg': '任务不存在'}), 404
    if t['approval_status'] != '已通过':
        return jsonify({'ok': False, 'msg': '任务审批通过后才能添加进展'}), 403
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
    """上传产出物文件，追加到 attachment_path（多个用分号分隔）。任务审批通过后才能上传。"""
    db = get_db()
    t = db.execute('SELECT * FROM wbs_tasks WHERE id=?', (tid,)).fetchone()
    if not t:
        return jsonify({'ok': False, 'msg': '任务不存在'}), 404
    if t['approval_status'] != '已通过':
        return jsonify({'ok': False, 'msg': '任务审批通过后才能上传产出物'}), 403
    attach = _save_upload(request.files.get('file'), 'wbs')
    if not attach:
        return jsonify({'ok': False, 'msg': '未收到文件'}), 400
    old = t['attachment_path'] or ''
    new_path = (old + ';' + attach) if old else attach
    db.execute('UPDATE wbs_tasks SET attachment_path=? WHERE id=?', (new_path, tid))
    db.commit()
    fname = attach.split('/')[-1]
    return jsonify({'ok': True, 'path': attach, 'name': fname})


@blueprint.route('/wbs/task/<int:tid>/del_attachment', methods=['POST'])
def wbs_task_del_attachment(tid):
    """删除指定产出物文件（应对传错文件后替换场景）。权限与上传一致：管理员或任务审批通过后的普通用户。"""
    db = get_db()
    t = db.execute('SELECT * FROM wbs_tasks WHERE id=?', (tid,)).fetchone()
    if not t:
        return jsonify({'ok': False, 'msg': '任务不存在'}), 404
    role = session.get('user_role')
    if role not in ('admin', 'platform_admin') and t['approval_status'] != '已通过':
        return jsonify({'ok': False, 'msg': '任务审批通过后才能删除产出物'}), 403
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
    headers = ['WBS编号','阶段/任务','工作内容','负责人','协作/支撑','计划开始','计划结束','交付物/判定标准','备注']
    header_fill = PatternFill('solid', fgColor='2B579A')
    header_font = Font(color='FFFFFF', bold=True, size=11)
    thin = Side(style='thin', color='D0D7E5')
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    for i, h in enumerate(headers, 1):
        c = ws.cell(row=1, column=i, value=h)
        c.fill = header_fill; c.font = header_font
        c.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
        c.border = border
    widths = [10, 22, 30, 10, 16, 12, 12, 22, 20]
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[openpyxl.utils.get_column_letter(i)].width = w
    # 示例行
    samples = [
        [1, '阶段一·资源诉求（输入梳理）', '汇总各渠道输入，为问题定位提供基线', '', '', '2026-10-12', '2026-10-16', '', ''],
        ['1.1', '在轨测试情况', '统计切换性能的比对情况（切换失败率、切换时延）', '余丽莎', '谭富元、李雪', '2026-10-12', '2026-10-16', '数据统计表/比对报告', ''],
        ['1.2', '质量测试情况', '实验室入网性能/切换性能的测试结果收集', '袁谅', '', '2026-10-12', '2026-10-16', '测试结果记录', ''],
        [2, '阶段二·问题现象汇总分析', '整合阶段一输入，复现问题、定位根因', '', '', '2026-10-16', '2026-10-22', '', ''],
        ['2.1', '问题现象汇总分析', '汇总失败率偏高与切换时延偏长的现象', '余丽莎', '袁谅、谭富元', '2026-10-16', '2026-10-19', '问题现象汇总表', ''],
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


def _send_bytes(content, filename):
    import tempfile
    from flask import send_file
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix='.xlsx')
    tmp.write(content); tmp.close()
    return send_file(tmp.name, as_attachment=True, download_name=filename,
                     mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')


@blueprint.route('/wbs/<int:pid>/export')
def wbs_export(pid):
    """导出当前项目WBS为Excel，与截图同格式"""
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    db = get_db()
    p = db.execute('SELECT * FROM wbs_projects WHERE id=?', (pid,)).fetchone()
    if not p:
        return redirect(url_for('wbs.wbs_projects'))
    tasks = db.execute('SELECT * FROM wbs_tasks WHERE project_id=? ORDER BY wbs_code, id', (pid,)).fetchall()
    wb = openpyxl.Workbook(); ws = wb.active; ws.title = 'WBS计划'
    headers = ['WBS编号','阶段/任务','工作内容','负责人','协作/支撑','计划开始','计划结束','交付物/判定标准','备注']
    header_fill = PatternFill('solid', fgColor='2B579A')
    thin = Side(style='thin', color='D0D7E5')
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    for i, h in enumerate(headers, 1):
        c = ws.cell(row=1, column=i, value=h)
        c.fill = header_fill; c.font = Font(color='FFFFFF', bold=True, size=11)
        c.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True); c.border = border
    widths = [10, 22, 30, 10, 16, 12, 12, 22, 20]
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[openpyxl.utils.get_column_letter(i)].width = w
    r = 2
    for t in tasks:
        is_phase = '.' not in (t['wbs_code'] or '')
        vals = [t['wbs_code'], t['name'], t['work_content'], t['owner'] or '', t['collaborators'] or '',
                t['plan_start'] or '', t['plan_end'] or '', t['deliverable'] or '', t['remark'] or '']
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
