# -*- coding: utf-8 -*-
"""设备运维与OTA版本管理插件路由（从 ext_routes.py 迁移，v10.4.0）"""
from flask import Blueprint, render_template, request, redirect, url_for, jsonify, g, Response, flash, session, send_from_directory
from datetime import datetime
import io, os, json, random
from plugin_system import log_operation
from plugins.base import make_blueprint

blueprint = make_blueprint('device', __name__)

def get_db():
    if 'db' not in g:
        import sqlite3, os
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
# 测试组：测试执行

# ============================================================
# 产品总体组A：资源调度
# ============================================================
def _get_product_id_by_model(model):
    """按产品型号/名称匹配 product_id"""
    if not model:
        return None
    db = get_db()
    m = str(model).strip()
    row = db.execute("SELECT id FROM products WHERE model=? OR name=? LIMIT 1", (m, m)).fetchone()
    return row['id'] if row else None

@blueprint.route('/device-center')
def device_center():
    """设备运维与OTA版本管理合并页（tab: device / ota）"""
    db = get_db()
    tab = request.args.get('tab', 'device')
    # ---- 设备运维数据 ----
    d_status = request.args.get('d_status', '')
    d_search = request.args.get('d_search', '').strip()
    d_page = max(1, int(request.args.get('d_page', 1)))
    per_page = 12
    offset = (d_page - 1) * per_page
    where = []
    params = []
    if d_status:
        where.append('d.status=?')
        params.append(d_status)
    if d_search:
        where.append('(d.device_sn LIKE ? OR d.customer LIKE ? OR d.firmware_version LIKE ?)')
        params.extend([f'%{d_search}%'] * 3)
    where_sql = (' WHERE ' + ' AND '.join(where)) if where else ''
    d_total = db.execute(f'SELECT COUNT(*) FROM devices d{where_sql}', params).fetchone()[0]
    devices = db.execute(f'''SELECT d.*, p.name as product_name FROM devices d
        LEFT JOIN products p ON d.product_id=p.id
        {where_sql} ORDER BY d.created_at DESC LIMIT ? OFFSET ?''',
        params + [per_page, offset]).fetchall()
    d_pages = max(1, (d_total + per_page - 1) // per_page)
    online = db.execute("SELECT COUNT(*) FROM devices WHERE status='在线'").fetchone()[0]
    offline = db.execute("SELECT COUNT(*) FROM devices WHERE status='离线'").fetchone()[0]
    inactive = db.execute("SELECT COUNT(*) FROM devices WHERE status='未激活'").fetchone()[0]
    alerts = db.execute("SELECT COUNT(*) FROM device_alerts WHERE status='未处理'").fetchone()[0]
    # 最近版本变更（全局监控视图）
    recent_changes = db.execute('''SELECT vc.*, d.device_sn as sn FROM device_version_changes vc
        LEFT JOIN devices d ON vc.device_id=d.id
        ORDER BY vc.changed_at DESC LIMIT 8''').fetchall()
    # ---- OTA版本数据 ----
    o_status = request.args.get('o_status', '')
    o_search = request.args.get('o_search', '').strip()
    o_where, o_params = [], []
    if o_status:
        o_where.append('ov.status=?')
        o_params.append(o_status)
    if o_search:
        o_where.append('(ov.version_no LIKE ? OR ov.file_name LIKE ?)')
        o_params.extend([f'%{o_search}%'] * 2)
    o_where_sql = (' WHERE ' + ' AND '.join(o_where)) if o_where else ''
    otas = db.execute(f'''SELECT ov.*, p.name as product_name FROM ota_versions ov
        LEFT JOIN products p ON ov.product_id=p.id
        {o_where_sql} ORDER BY ov.created_at DESC''', o_params).fetchall()
    o_total = len(otas)
    o_released = sum(1 for x in otas if x['status'] == '已发布')
    o_testing = sum(1 for x in otas if x['status'] == '测试中')
    o_rolled = sum(1 for x in otas if x['status'] == '已回滚')
    return render_template('device_center.html', tab=tab,
                           devices=devices, d_total=d_total, d_pages=d_pages,
                           d_status=d_status, d_search=d_search,
                           online=online, offline=offline, inactive=inactive, alerts=alerts,
                           recent_changes=recent_changes,
                           otas=otas, o_status=o_status, o_search=o_search,
                           o_total=o_total, o_released=o_released, o_testing=o_testing, o_rolled=o_rolled)

# ---- Excel 模板下载 ----
def _make_template(filename, sheet_title, headers, sample_rows, note_lines):
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Font, PatternFill, Alignment
    except ImportError:
        return '服务端缺少openpyxl', 500
    wb = Workbook()
    ws = wb.active
    ws.title = sheet_title
    head_fill = PatternFill('solid', fgColor='1D4ED8')
    head_font = Font(color='FFFFFF', bold=True)
    ws.append(headers)
    for c in ws[1]:
        c.fill = head_fill
        c.font = head_font
        c.alignment = Alignment(horizontal='center', vertical='center')
    for row in sample_rows:
        ws.append(row)
    ws2 = wb.create_sheet('填写说明')
    for line in note_lines:
        ws2.append([line])
    ws2.column_dimensions['A'].width = 100
    widths = [max(len(str(h)) * 2 + 6, 14) for h in headers]
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[chr(64 + i) if i <= 26 else 'A'].width = min(w, 40)
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    resp = Response(buf.getvalue(), mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    # 中文文件名需RFC5987编码，避免latin-1编码错误
    from urllib.parse import quote
    resp.headers['Content-Disposition'] = "attachment; filename*=UTF-8''" + quote(filename)
    return resp

@blueprint.route('/device-center/template/devices')
def device_template():
    return _make_template(
        '设备登记模板.xlsx', '设备台账',
        ['设备SN(必填)', '产品型号', '客户', '固件版本', '状态', '最后在线', '位置', '备注'],
        [['SN-DEMO-0001', 'SAM302', '示例客户', 'V1.3.0', '在线', '2026-09-20 10:00', '重庆', '示例行，可删除'],
         ['SN-DEMO-0002', 'SAM203', '', 'V1.2.1', '离线', '', '成都', '']],
        ['1. 首行表头请勿修改；蓝色表头为固定字段。',
         '2. 设备SN必填且不重复；重复SN将更新该设备信息（固件变更自动记录）。',
         '3. 产品型号填写产品名称或型号（如 SAM302 / SAM203），留空则该设备不关联产品。',
         '4. 状态取值：在线 / 离线 / 未激活（默认未激活）。',
         '5. 最后在线格式：YYYY-MM-DD HH:MM。'])

@blueprint.route('/device-center/template/ota')
def ota_template():
    return _make_template(
        'OTA版本模板.xlsx', 'OTA版本台账',
        ['版本号(必填)', '产品型号', '文件名', '文件大小(MB)', 'MD5', '发布说明', '发布策略', '发布比例(%)', '状态', '创建人'],
        [['V1.5.0', 'SAM302', 'SAM302_V1.5.0.bin', 2.4, 'a1b2c3d4e5f6', '新增闭环功率控制', '灰度发布', 30, '测试中', '张三'],
         ['V1.3.0', 'SAM302', 'SAM302_V1.3.0.bin', 2.1, 'f6e5d4c3b2a1', '基础版本', '全量发布', 100, '已发布', '张三']],
        ['1. 首行表头请勿修改；版本号必填且不重复（重复版本号将被跳过）。',
         '2. 产品型号填写产品名称或型号（如 SAM302 / SAM203），留空则不关联产品。',
         '3. 发布策略：全量发布 / 灰度发布；发布比例1-100。',
         '4. 状态取值：测试中 / 已发布 / 已回滚（默认测试中）。',
         '5. 文件大小单位为MB（数字）。'])

# ---- Excel 导入 ----
@blueprint.route('/device-center/import/devices', methods=['POST'])
def device_import():
    from app import can_write
    if not can_write('ext.device'):
        flash('无权限：设备运维当前为只读', 'error')
        return redirect(url_for('device.device_center', tab='device'))
    file = request.files.get('file')
    if not file or not file.filename:
        flash('请选择要导入的Excel文件', 'error')
        return redirect(url_for('device.device_center', tab='device'))
    try:
        from openpyxl import load_workbook
    except ImportError:
        flash('服务端缺少openpyxl，无法导入', 'error')
        return redirect(url_for('device.device_center', tab='device'))
    try:
        wb = load_workbook(io.BytesIO(file.read()), data_only=True)
        ws = wb.worksheets[0]
        rows = list(ws.iter_rows(values_only=True))
    except Exception:
        flash('Excel解析失败，请使用系统提供的模板', 'error')
        return redirect(url_for('device.device_center', tab='device'))
    db = get_db()
    added = updated = skipped = 0
    err_lines = []
    for idx, r in enumerate(rows[1:], 2):
        if not r or all(v is None or str(v).strip() == '' for v in r[:3]):
            continue
        sn = str(r[0]).strip() if r[0] is not None else ''
        if not sn:
            err_lines.append(f'第{idx}行：设备SN为空')
            continue
        model = str(r[1]).strip() if r[1] is not None else ''
        customer = str(r[2]).strip() if r[2] is not None else ''
        fw = str(r[3]).strip() if r[3] is not None else ''
        status = str(r[4]).strip() if r[4] is not None else '未激活'
        if status not in ('在线', '离线', '未激活'):
            status = '未激活'
        last_online = str(r[5]).strip() if r[5] is not None else ''
        location = str(r[6]).strip() if r[6] is not None else ''
        remark = str(r[7]).strip() if r[7] is not None else ''
        pid = _get_product_id_by_model(model)
        exist = db.execute('SELECT * FROM devices WHERE device_sn=?', (sn,)).fetchone()
        if exist:
            # 更新，固件变化则记录变更
            old_fw = exist['firmware_version'] or ''
            db.execute('''UPDATE devices SET product_id=?, customer=?, firmware_version=?, status=?,
                          last_online=?, location=?, remark=? WHERE id=?''',
                       (pid or exist['product_id'], customer or exist['customer'],
                        fw or old_fw, status, last_online or exist['last_online'],
                        location or exist['location'], remark, exist['id']))
            if fw and old_fw != fw:
                db.execute('''INSERT INTO device_version_changes (device_id, device_sn, old_version,
                              new_version, change_type, reason, operator)
                              VALUES (?,?,?,?,?,?,?)''',
                           (exist['id'], sn, old_fw or '-', fw, '批量导入更新', 'Excel批量导入', '导入'))
            updated += 1
        else:
            cur = db.execute('''INSERT INTO devices (device_sn, product_id, customer, firmware_version,
                                status, last_online, location, remark)
                                VALUES (?,?,?,?,?,?,?,?)''',
                             (sn, pid, customer, fw, status, last_online, location, remark))
            db.execute('''INSERT INTO device_version_changes (device_id, device_sn, old_version,
                          new_version, change_type, reason, operator)
                          VALUES (?,?,?,?,?,?,?)''',
                       (cur.lastrowid, sn, '-', fw or '-', '出厂登记', '批量导入', '导入'))
            added += 1
    db.commit()
    log_operation('设备', '批量导入', f'新增{added}台，更新{updated}台，跳过{skipped}，异常{len(err_lines)}行', '导入')
    msg = f'设备导入完成：新增 {added} 台，更新 {updated} 台' + (f'，{len(err_lines)} 行异常' if err_lines else '')
    flash(msg, 'success')
    return redirect(url_for('device.device_center', tab='device'))

@blueprint.route('/device-center/import/ota', methods=['POST'])
def ota_import():
    from app import can_write
    if not can_write('ext.ota'):
        flash('无权限：OTA版本管理当前为只读', 'error')
        return redirect(url_for('device.device_center', tab='ota'))
    file = request.files.get('file')
    if not file or not file.filename:
        flash('请选择要导入的Excel文件', 'error')
        return redirect(url_for('device.device_center', tab='ota'))
    try:
        from openpyxl import load_workbook
    except ImportError:
        flash('服务端缺少openpyxl，无法导入', 'error')
        return redirect(url_for('device.device_center', tab='ota'))
    try:
        wb = load_workbook(io.BytesIO(file.read()), data_only=True)
        ws = wb.worksheets[0]
        rows = list(ws.iter_rows(values_only=True))
    except Exception:
        flash('Excel解析失败，请使用系统提供的模板', 'error')
        return redirect(url_for('device.device_center', tab='ota'))
    db = get_db()
    added = skipped = 0
    err_lines = []
    for idx, r in enumerate(rows[1:], 2):
        if not r or all(v is None or str(v).strip() == '' for v in r[:2]):
            continue
        ver = str(r[0]).strip() if r[0] is not None else ''
        if not ver:
            err_lines.append(f'第{idx}行：版本号为空')
            continue
        model = str(r[1]).strip() if r[1] is not None else ''
        file_name = str(r[2]).strip() if r[2] is not None else ''
        file_size = None
        try:
            file_size = int(float(r[3]) * 1024 * 1024) if r[3] not in (None, '') else None
        except Exception:
            pass
        md5 = str(r[4]).strip() if r[4] is not None else ''
        notes = str(r[5]).strip() if r[5] is not None else ''
        strategy = str(r[6]).strip() if r[6] is not None else '全量发布'
        pct = 100
        try:
            pct = min(100, max(1, int(float(r[7])))) if r[7] not in (None, '') else 100
        except Exception:
            pct = 100
        status = str(r[8]).strip() if r[8] is not None else '测试中'
        if status not in ('测试中', '已发布', '已回滚'):
            status = '测试中'
        creator = str(r[9]).strip() if r[9] is not None else '导入'
        pid = _get_product_id_by_model(model)
        exist = db.execute('SELECT id FROM ota_versions WHERE version_no=?', (ver,)).fetchone()
        if exist:
            skipped += 1
            continue
        cur = db.execute('''INSERT INTO ota_versions (version_no, product_id, file_name, file_size, md5,
                            release_notes, status, rollout_strategy, rollout_percentage, creator,
                            published_at, published_by)
                            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)''',
                         (ver, pid, file_name, file_size, md5, notes, status, strategy, pct, creator,
                          datetime.now().strftime('%Y-%m-%d %H:%M') if status == '已发布' else None, creator if status == '已发布' else None))
        added += 1
    db.commit()
    log_operation('OTA', '批量导入', f'新增{added}个版本，跳过重复{skipped}个', '导入')
    flash(f'OTA版本导入完成：新增 {added} 个版本，跳过重复 {skipped} 个', 'success')
    return redirect(url_for('device.device_center', tab='ota'))

# ---- Excel 导出 ----
@blueprint.route('/device-center/export/devices')
def device_export():
    db = get_db()
    rows = db.execute('''SELECT d.device_sn, p.name as product_name, d.customer, d.firmware_version,
                         d.status, d.last_online, d.location, d.remark, d.activate_date
                         FROM devices d LEFT JOIN products p ON d.product_id=p.id
                         ORDER BY d.created_at DESC''').fetchall()
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Font, PatternFill
    except ImportError:
        return '服务端缺少openpyxl', 500
    wb = Workbook()
    ws = wb.active
    ws.title = '设备台账'
    ws.append(['设备SN', '产品型号', '客户', '固件版本', '状态', '最后在线', '位置', '激活日期', '备注'])
    for c in ws[1]:
        c.font = Font(bold=True, color='FFFFFF')
        c.fill = PatternFill('solid', fgColor='1D4ED8')
    for r in rows:
        ws.append([r['device_sn'], r['product_name'], r['customer'], r['firmware_version'],
                   r['status'], r['last_online'], r['location'], r['activate_date'], r['remark']])
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    resp = Response(buf.getvalue(), mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    from urllib.parse import quote
    resp.headers['Content-Disposition'] = "attachment; filename*=UTF-8''" + quote('devices.xlsx')
    return resp

@blueprint.route('/device-center/export/ota')
def ota_export():
    db = get_db()
    rows = db.execute('''SELECT ov.version_no, p.name as product_name, ov.file_name,
                         ROUND(ov.file_size/1024.0/1024.0, 2) as size_mb, ov.md5, ov.release_notes,
                         ov.rollout_strategy, ov.rollout_percentage, ov.status, ov.creator,
                         ov.created_at, ov.published_at
                         FROM ota_versions ov LEFT JOIN products p ON ov.product_id=p.id
                         ORDER BY ov.created_at DESC''').fetchall()
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Font, PatternFill
    except ImportError:
        return '服务端缺少openpyxl', 500
    wb = Workbook()
    ws = wb.active
    ws.title = 'OTA版本台账'
    ws.append(['版本号', '产品型号', '文件名', '大小(MB)', 'MD5', '发布说明', '发布策略',
               '发布比例(%)', '状态', '创建人', '创建时间', '发布时间'])
    for c in ws[1]:
        c.font = Font(bold=True, color='FFFFFF')
        c.fill = PatternFill('solid', fgColor='1D4ED8')
    for r in rows:
        ws.append([r['version_no'], r['product_name'], r['file_name'], r['size_mb'], r['md5'],
                   r['release_notes'], r['rollout_strategy'], r['rollout_percentage'], r['status'],
                   r['creator'], r['created_at'], r['published_at']])
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    resp = Response(buf.getvalue(), mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    from urllib.parse import quote
    resp.headers['Content-Disposition'] = "attachment; filename*=UTF-8''" + quote('ota_versions.xlsx')
    return resp

@blueprint.route('/devices')
def device_list():
    """旧入口：跳转到合并页设备运维tab"""
    return redirect(url_for('device.device_center', tab='device'))

@blueprint.route('/devices/add', methods=['GET', 'POST'])
def device_add():
    db = get_db()
    if request.method == 'POST':
        device_sn = request.form.get('device_sn', '').strip()
        product_id = request.form.get('product_id')
        customer = request.form.get('customer', '')
        firmware_version = request.form.get('firmware_version', '')
        location = request.form.get('location', '')
        remark = request.form.get('remark', '')
        cur = db.execute('''INSERT INTO devices (device_sn, product_id, customer, firmware_version,
            status, location, remark) VALUES (?,?,?,?,?,?,?)''',
            (device_sn, product_id, customer, firmware_version, '未激活', location, remark))
        db.execute('''INSERT INTO device_version_changes (device_id, device_sn, old_version,
                      new_version, change_type, reason, operator)
                      VALUES (?,?,?,?,?,?,?)''',
                   (cur.lastrowid, device_sn, '-', firmware_version or '-', '出厂登记', '设备登记', '登记'))
        db.commit()
        log_operation('设备', '新增', f'{device_sn}: {customer}', 'delivery_c')
        return redirect(url_for('device.device_center', tab='device'))
    products = db.execute('SELECT id, name, model FROM products').fetchall()
    return render_template('device_form.html', products=products)

@blueprint.route('/devices/<int:did>')
def device_detail(did):
    db = get_db()
    device = db.execute('''SELECT d.*, p.name as product_name FROM devices d
        LEFT JOIN products p ON d.product_id=p.id WHERE d.id=?''', (did,)).fetchone()
    if not device:
        return '设备不存在', 404
    alerts = db.execute('SELECT * FROM device_alerts WHERE device_id=? ORDER BY created_at DESC LIMIT 10', (did,)).fetchall()
    upgrades = db.execute('''SELECT ou.*, ov.version_no, ov.status as ota_status FROM ota_upgrades ou
        LEFT JOIN ota_versions ov ON ou.ota_version_id=ov.id
        WHERE ou.device_id=? ORDER BY ou.created_at DESC LIMIT 10''', (did,)).fetchall()
    changes = db.execute('''SELECT * FROM device_version_changes WHERE device_id=?
                            ORDER BY changed_at DESC, id DESC LIMIT 20''', (did,)).fetchall()
    return render_template('device_detail.html', device=device, alerts=alerts,
                           upgrades=upgrades, changes=changes)

# ============================================================
# 交付服务组C：OTA版本管理
# ============================================================
@blueprint.route('/ota')
def ota_list():
    """旧入口：跳转到合并页OTA tab"""
    return redirect(url_for('device.device_center', tab='ota'))

@blueprint.route('/ota/add', methods=['GET', 'POST'])
def ota_add():
    db = get_db()
    if request.method == 'POST':
        version_no = request.form.get('version_no', '').strip()
        product_id = request.form.get('product_id')
        file_name = request.form.get('file_name', '')
        file_size = int(request.form.get('file_size', 0))
        md5 = request.form.get('md5', '')
        release_notes = request.form.get('release_notes', '')
        rollout_strategy = request.form.get('rollout_strategy', '全量发布')
        rollout_percentage = int(request.form.get('rollout_percentage', 100))
        creator = request.form.get('creator', 'delivery_c')
        db.execute('''INSERT INTO ota_versions (version_no, product_id, file_name, file_size, md5,
            release_notes, status, rollout_strategy, rollout_percentage, creator)
            VALUES (?,?,?,?,?,?,?,?,?,?)''',
            (version_no, product_id, file_name, file_size, md5, release_notes, '测试中', rollout_strategy, rollout_percentage, creator))
        db.commit()
        log_operation('OTA', '新增版本', f'{version_no}: {file_name}', creator)
        return redirect(url_for('device.device_center', tab='ota'))
    products = db.execute('SELECT id, name, model FROM products').fetchall()
    return render_template('ota_form.html', products=products)

@blueprint.route('/ota/<int:oid>/publish', methods=['POST'])
def ota_publish(oid):
    db = get_db()
    status = request.form.get('status', '已发布')
    now = datetime.now().strftime('%Y-%m-%d %H:%M')
    if status == '已发布':
        db.execute('UPDATE ota_versions SET status=?, published_at=?, published_by=? WHERE id=?',
                   (status, now, request.form.get('operator') or 'delivery_c', oid))
    else:
        db.execute('UPDATE ota_versions SET status=? WHERE id=?', (status, oid))
    db.commit()
    log_operation('OTA', '发布', f'版本#{oid} 状态改为 {status}', 'delivery_c')
    return redirect(url_for('device.device_center', tab='ota'))

# ---- 设备状态监控与版本变更 ----
@blueprint.route('/devices/<int:did>/status', methods=['POST'])
def device_status_update(did):
    """更新设备在线状态"""
    from app import is_admin, submit_status_change
    db = get_db()
    status = request.form.get('status', '在线')
    # v10.3.2 权限统一：普通用户提交审批
    perm, aid = submit_status_change('device', 'device', did, status)
    if perm == 'pending':
        flash('状态变更申请已提交，等待管理员审批', 'info')
        return redirect(url_for('device.device_detail', did=did))
    db.execute("UPDATE devices SET status=? WHERE id=?", (status, did))
    db.commit()
    log_operation('设备', '状态更新', f'设备#{did} → {status}', 'delivery_c')
    return redirect(url_for('device.device_detail', did=did))

@blueprint.route('/devices/<int:did>/firmware', methods=['POST'])
def device_firmware_update(did):
    """登记设备固件版本变更（自动留痕）"""
    from app import can_write
    if not can_write('ext.device'):
        return jsonify(ok=False, msg='只读权限'), 403
    db = get_db()
    dev = db.execute('SELECT * FROM devices WHERE id=?', (did,)).fetchone()
    if not dev:
        return '设备不存在', 404
    new_fw = request.form.get('new_version', '').strip()
    change_type = request.form.get('change_type', 'OTA升级')
    reason = request.form.get('reason', '').strip()
    operator = request.form.get('operator', 'delivery_c').strip()
    if not new_fw:
        return '新固件版本不能为空', 400
    old_fw = dev['firmware_version'] or '-'
    db.execute('UPDATE devices SET firmware_version=? WHERE id=?', (new_fw, did))
    db.execute('''INSERT INTO device_version_changes (device_id, device_sn, old_version,
                  new_version, change_type, reason, operator)
                  VALUES (?,?,?,?,?,?,?)''',
               (did, dev['device_sn'], old_fw, new_fw, change_type, reason, operator))
    db.commit()
    log_operation('设备', '版本变更', f'{dev["device_sn"]}: {old_fw} → {new_fw} ({change_type})', operator)
    return redirect(url_for('device.device_detail', did=did))

# ============================================================
# 统一任务追踪（个人工作台核心）
# ============================================================
