# -*- coding: utf-8 -*-
"""云文档表格插件。

本插件将 Univer 开源项目（https://github.com/dream-num/univer，v1.3.1）
官方 SDK 构建产物真实接入平台：

- 前端：Univer 官方表格引擎（公式/样式/合并/筛选/多工作表/编辑框），
  界面为 Univer 官方 UI（中文语言包），非仿制。
- 协议：房间（共享表格）+ 快照 JSON 存取 + 乐观锁版本递增；协同采用
  5 秒轮询同步，加载服务端快照重建本地实例。
- 存储：插件自管表 univer_rooms，state 为 Univer Workbook 快照 JSON
  全文，可随平台数据库一起备份/恢复。

权限约定：
- 新建、打开、编辑、保存：所有登录用户（云文档协作）
- 删除房间：管理员 / 平台管理员 / 创建者本人
"""
import io
import json
import os
import re
import sqlite3
import urllib.parse

from flask import (Blueprint, render_template, request, redirect, url_for,
                   flash, jsonify, session, abort, Response, send_file)
from flask import g

from plugins.base import make_blueprint
from plugin_system import log_operation, create_plugin_table

bp = make_blueprint('univer_sheet', __name__, static_url_path='/univer/static')


MANAGER_ROLES = ('admin', 'platform_admin')
ROOM_NAME_RE = re.compile(r'^[\w\u4e00-\u9fff\u3000-\u303f\-]{1,50}$')
MAX_STATE_BYTES = 12 * 1024 * 1024   # 单表格快照上限 12MB


def get_db():
    if 'db' not in g:
        from flask import current_app
        g.db = sqlite3.connect(current_app.config['DATABASE'])
        g.db.row_factory = sqlite3.Row
    return g.db


@bp.teardown_request
def close_db(exception):
    db = g.pop('db', None)
    if db is not None:
        db.close()


def _init_tables():
    """插件自管表：走平台统一建表入口（登记 plugin_tables + 幂等建表 + 老库补列），
    消除插件管理页「未走统一建表入口」提示（v12.5.39）"""
    create_plugin_table('univer_sheet', 'univer_rooms', '''
    CREATE TABLE IF NOT EXISTS univer_rooms (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        name        TEXT NOT NULL UNIQUE,
        description TEXT DEFAULT '',
        created_by  TEXT NOT NULL,
        created_at  TEXT DEFAULT (datetime('now','localtime')),
        updated_at  TEXT,
        updated_by  TEXT,
        version     INTEGER DEFAULT 0,
        state       TEXT DEFAULT '',
        deleted     INTEGER DEFAULT 0,
        deleted_at  TEXT,
        deleted_by  TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_univer_rooms_created ON univer_rooms(created_by);
    ''')
    db = get_db()
    cols = [r[1] for r in db.execute('PRAGMA table_info(univer_rooms)').fetchall()]
    for col, ddl in (
        ('description', "ALTER TABLE univer_rooms ADD COLUMN description TEXT DEFAULT ''"),
        ('updated_at', "ALTER TABLE univer_rooms ADD COLUMN updated_at TEXT"),
        ('updated_by', "ALTER TABLE univer_rooms ADD COLUMN updated_by TEXT"),
        ('version', "ALTER TABLE univer_rooms ADD COLUMN version INTEGER DEFAULT 0"),
        ('state', "ALTER TABLE univer_rooms ADD COLUMN state TEXT DEFAULT ''"),
        ('deleted', "ALTER TABLE univer_rooms ADD COLUMN deleted INTEGER DEFAULT 0"),
        ('deleted_at', "ALTER TABLE univer_rooms ADD COLUMN deleted_at TEXT"),
        ('deleted_by', "ALTER TABLE univer_rooms ADD COLUMN deleted_by TEXT"),
    ):
        if col not in cols:
            db.execute(ddl)
    db.commit()


def _username():
    return session.get('username', '')


def _is_manager():
    role = session.get('user_role') or session.get('role')
    return role in MANAGER_ROLES


def _get_room(rid, need_state=False, include_deleted=False):
    db = get_db()
    _init_tables()
    cols = 'id,name,description,created_by,created_at,updated_at,updated_by,version,deleted,deleted_at,deleted_by'
    if need_state:
        cols += ',state'
    cond = '' if include_deleted else ' AND deleted=0'
    return db.execute(
        'SELECT %s FROM univer_rooms WHERE id=?%s' % (cols, cond), (rid,)).fetchone()


# ---------------------------------------------------------------- 页面 ----

@bp.route('/univer/')
def index():
    """房间（共享表格）列表：列出全部 + 新建 + 删除 + 回收站入口"""
    _init_tables()
    rooms = get_db().execute(
        'SELECT id,name,description,created_by,created_at,updated_at,updated_by,version '
        'FROM univer_rooms WHERE deleted=0 '
        'ORDER BY COALESCE(updated_at, created_at) DESC, id DESC'
    ).fetchall()
    recycle_count = get_db().execute(
        'SELECT COUNT(*) FROM univer_rooms WHERE deleted=1').fetchone()[0]
    return render_template('univer_index.html', rooms=rooms,
                           is_manager=_is_manager(),
                           username=_username(),
                           recycle_count=recycle_count)


@bp.route('/univer/new', methods=['POST'])
def new_room():
    name = (request.form.get('name') or '').strip()
    desc = (request.form.get('description') or '').strip()
    db = get_db()
    _init_tables()
    if not name:
        flash('请输入表格名称', 'error')
    elif not ROOM_NAME_RE.match(name):
        flash('名称限 1-50 个汉字/字母/数字/下划线/短横线，且不含空格或特殊符号', 'error')
    elif db.execute('SELECT id FROM univer_rooms WHERE name=?', (name,)).fetchone():
        flash(f'已存在同名共享表格「{name}」，请换一个名称', 'error')
    else:
        try:
            cur = _username()
            db.execute(
                'INSERT INTO univer_rooms (name, description, created_by) VALUES (?,?,?)',
                (name, desc, cur))
            db.commit()
            rid = db.execute('SELECT id FROM univer_rooms WHERE name=?', (name,)).fetchone()['id']
            log_operation('云文档表格', '新建共享表格', f'新建「{name}」(id={rid})', cur)
            flash(f'共享表格「{name}」已创建', 'ok')
            return redirect(url_for('univer_sheet.open_room', rid=rid))
        except Exception as e:
            db.rollback()
            flash(f'创建失败：{e}', 'error')
    return redirect(url_for('univer_sheet.index'))


@bp.route('/univer/<int:rid>/')
def open_room(rid):
    """打开共享表格（Univer 官方 UI 渲染）"""
    room = _get_room(rid)
    if not room:
        abort(404, description='共享表格不存在或已被删除')
    return render_template('univer_room.html', room=room, username=_username(),
                           is_manager=_is_manager())


@bp.route('/univer/<int:rid>/rename', methods=['POST'])
def rename_room(rid):
    """重命名共享表格（平台管理员 / 管理员 / 创建者本人）"""
    room = _get_room(rid)
    if not room:
        flash('共享表格不存在或已被删除', 'error')
        return redirect(url_for('univer_sheet.index'))
    cur = _username()
    if not (_is_manager() or room['created_by'] == cur):
        flash('无权限：仅平台管理员/管理员/创建者可重命名', 'error')
        return redirect(url_for('univer_sheet.index'))
    name = (request.form.get('name') or '').strip()
    db = get_db()
    if not name:
        flash('请输入新名称', 'error')
    elif not ROOM_NAME_RE.match(name):
        flash('名称限 1-50 个汉字/字母/数字/下划线/短横线，且不含空格或特殊符号', 'error')
    elif db.execute('SELECT id FROM univer_rooms WHERE name=? AND id<>?', (name, rid)).fetchone():
        flash('已存在同名共享表格「%s」，请换一个名称' % name, 'error')
    else:
        old = room['name']
        try:
            db.execute('UPDATE univer_rooms SET name=? WHERE id=?', (name, rid))
            db.commit()
            log_operation('云文档表格', '重命名表格', '「%s」→「%s」(id=%s)' % (old, name, rid), cur)
            flash('已重命名为「%s」' % name, 'ok')
        except Exception as e:
            db.rollback()
            flash('重命名失败：%s' % e, 'error')
    return redirect(url_for('univer_sheet.index'))


@bp.route('/univer/<int:rid>/delete', methods=['POST'])
def delete_room(rid):
    """删除共享表格（管理员/平台管理员/创建者）→ 进回收站（软删，可恢复）"""
    room = _get_room(rid, include_deleted=True)
    if not room:
        flash('共享表格不存在', 'error')
        return redirect(url_for('univer_sheet.index'))
    if not (_is_manager() or room['created_by'] == _username()):
        return abort(403, description='仅管理员或创建者可删除')
    db = get_db()
    now = None
    try:
        from datetime import datetime
        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    except Exception:
        now = None
    db.execute('UPDATE univer_rooms SET deleted=1, deleted_at=?, deleted_by=? WHERE id=?',
               (now, _username(), rid))
    db.commit()
    log_operation('云文档表格', '删除共享表格(入回收站)',
                  f'删除「{room["name"]}」(id={rid})，已入回收站', _username())
    flash(f'共享表格「{room["name"]}」已删除，可在回收站恢复', 'ok')
    return redirect(url_for('univer_sheet.index'))


# ------------------------------------------------------------ 回收站 ----

@bp.route('/univer/recycle')
def recycle():
    """回收站：列出已删除表格，支持恢复 / 彻底删除"""
    _init_tables()
    rooms = get_db().execute(
        'SELECT id,name,description,created_by,created_at,updated_at,updated_by,version,'
        'deleted_at,deleted_by FROM univer_rooms WHERE deleted=1 '
        'ORDER BY deleted_at DESC, id DESC'
    ).fetchall()
    return render_template('univer_recycle.html', rooms=rooms,
                           is_manager=_is_manager(),
                           username=_username())


@bp.route('/univer/<int:rid>/restore', methods=['POST'])
def restore_room(rid):
    """从回收站恢复表格（管理员/平台管理员/原创建者）"""
    room = _get_room(rid, include_deleted=True)
    if not room:
        flash('共享表格不存在', 'error')
        return redirect(url_for('univer_sheet.recycle'))
    if room['deleted'] != 1:
        flash('该表格不在回收站中', 'error')
        return redirect(url_for('univer_sheet.recycle'))
    if not (_is_manager() or room['created_by'] == _username()):
        return abort(403, description='仅管理员或创建者可恢复')
    db = get_db()
    # 若存在同名未删除表格（极端情况：恢复前有人新建同名），则改名避免 UNIQUE 冲突
    dup = db.execute(
        'SELECT id FROM univer_rooms WHERE name=? AND deleted=0 AND id<>?',
        (room['name'], rid)).fetchone()
    if dup:
        db.execute('UPDATE univer_rooms SET name=? WHERE id=?',
                   (room['name'] + '（恢复）', rid))
    db.execute('UPDATE univer_rooms SET deleted=0, deleted_at=NULL, deleted_by=NULL WHERE id=?', (rid,))
    db.commit()
    log_operation('云文档表格', '恢复共享表格', f'恢复「{room["name"]}」(id={rid})', _username())
    flash(f'共享表格「{room["name"]}」已恢复', 'ok')
    return redirect(url_for('univer_sheet.recycle'))


@bp.route('/univer/<int:rid>/purge', methods=['POST'])
def purge_room(rid):
    """彻底删除（物理 DELETE，不可恢复）——仅管理员/平台管理员"""
    room = _get_room(rid, include_deleted=True)
    if not room:
        flash('共享表格不存在', 'error')
        return redirect(url_for('univer_sheet.recycle'))
    if not _is_manager():
        return abort(403, description='仅管理员可彻底删除')
    db = get_db()
    db.execute('DELETE FROM univer_rooms WHERE id=?', (rid,))
    db.commit()
    log_operation('云文档表格', '彻底删除共享表格',
                  f'彻底删除「{room["name"]}」(id={rid})', _username())
    flash(f'共享表格「{room["name"]}」已彻底删除（不可恢复）', 'ok')
    return redirect(url_for('univer_sheet.recycle'))


# ------------------------------------------------------------ 数据 API ----

@bp.route('/univer/<int:rid>/state')
def get_state(rid):
    """获取表格最新快照（Univer Workbook JSON）与版本号（前端轮询用）"""
    room = _get_room(rid, need_state=True)
    if not room:
        return jsonify({'ok': 0, 'error': 'not_found'})
    return jsonify({
        'ok': 1,
        'id': room['id'],
        'name': room['name'],
        'version': room['version'],
        'updated_at': room['updated_at'],
        'updated_by': room['updated_by'],
        'state': room['state'] or '',
    })


@bp.route('/univer/<int:rid>/save', methods=['POST'])
def save_state(rid):
    """保存整表快照（前端防抖提交 Univer Workbook JSON 全文）。

    乐观锁：base_version 与服务端 version 不一致时返回 conflict，
    由前端提示用户手动同步，避免无声覆盖他人编辑。
    """
    room = _get_room(rid, need_state=True)
    if not room:
        return jsonify({'ok': 0, 'error': 'not_found'})
    data = request.get_json(force=True, silent=True) or {}
    state = data.get('state') or ''
    if isinstance(state, str) and len(state.encode('utf-8')) > MAX_STATE_BYTES:
        return jsonify({'ok': 0, 'error': 'too_large',
                        'msg': '表格内容超过 12MB 上限，请精简后保存'})
    if not isinstance(state, str):
        return jsonify({'ok': 0, 'error': 'bad_state'})

    base_version = data.get('base_version')
    db = get_db()
    cur = _username()
    if base_version is not None:
        try:
            base_version = int(base_version)
        except (TypeError, ValueError):
            base_version = None
        if base_version is not None and base_version != room['version']:
            return jsonify({'ok': 0, 'conflict': 1,
                            'version': room['version'],
                            'msg': '他人已更新（v%d），请同步后再保存' % room['version']})

    new_version = room['version'] + 1
    now = None
    try:
        from datetime import datetime
        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    except Exception:
        now = None
    if base_version is not None:
        # 乐观锁原子化：条件更新（WHERE version=base_version），并发下仅一个请求命中，
        # 其余 rowcount=0 判定冲突，杜绝「校验-更新」分离导致的无声覆盖
        c = db.execute(
            'UPDATE univer_rooms SET state=?, version=?, updated_at=COALESCE(?, updated_at), '
            'updated_by=? WHERE id=? AND version=?',
            (state, new_version, now, cur, rid, base_version))
        if c.rowcount == 0:
            fresh = _get_room(rid, need_state=True)
            db.rollback()
            fv = fresh['version'] if fresh else new_version
            return jsonify({'ok': 0, 'conflict': 1,
                            'version': fv,
                            'msg': '他人已更新（v%d），请同步后再保存' % fv})
    else:
        db.execute(
            'UPDATE univer_rooms SET state=?, version=?, updated_at=COALESCE(?, updated_at), '
            'updated_by=? WHERE id=?',
            (state, new_version, now, cur, rid))
    db.commit()
    log_operation('云文档表格', '保存表格', f'保存「{room["name"]}」(id={rid}) v{new_version}', cur)
    return jsonify({'ok': 1, 'version': new_version, 'updated_at': now})

# ------------------------------------------------------------ 导出 ----



def _snapshot_to_matrix(state):
    """把 Univer Workbook 快照 JSON 转成 (rows, cols, matrix) 矩阵。

    matrix[r][c] = 单元格显示值（v 字段或公式结果），r/c 均为 0 起整数。
    """
    try:
        snap = json.loads(state) if state else None
    except Exception:
        snap = None
    if not snap or not isinstance(snap, dict) or not snap.get('sheets'):
        return [], []
    first_sheet = None
    for _k, _s in snap['sheets'].items():
        first_sheet = _s
        break
    cell_data = (first_sheet or {}).get('cellData') or {}
    rows, cols = [], []
    max_r, max_c = -1, -1
    for rk, row_map in cell_data.items():
        try:
            r = int(rk)
        except Exception:
            continue
        for ck, cell in row_map.items():
            try:
                c = int(ck)
            except Exception:
                continue
            v = (cell or {}).get('v')
            if v is None and (cell or {}).get('s'):
                v = ''
            if v is None:
                continue
            while len(rows) <= r:
                rows.append({})
            rows[r][c] = v
            if r > max_r:
                max_r = r
            if c > max_c:
                max_c = c
    if max_r < 0:
        return rows, cols
    return rows, max_c


def _cell_to_display(v):
    """单元格值 → 可写入文件的值（数字保持数字、公式结果取 v、True/False 转文本）"""
    if v is None:
        return ''
    if isinstance(v, bool):
        return 'TRUE' if v else 'FALSE'
    return v


def _snapshot_to_sheets(state):
    """把 Univer 快照转成多工作表结构，供 xlsx 导出完整还原（数据+合并+列宽+行高）。

    返回 [{'name','rows','max_c','merges','col_w','row_h'}]，按快照内顺序。
    """
    try:
        snap = json.loads(state) if state else None
    except Exception:
        snap = None
    if not snap or not isinstance(snap, dict) or not snap.get('sheets'):
        return []
    out = []
    for _k, _s in snap['sheets'].items():
        cd = (_s or {}).get('cellData') or {}
        rows = {}
        max_c = -1
        for rk, row_map in cd.items():
            try:
                r = int(rk)
            except Exception:
                continue
            row = {}
            for ck, cell in row_map.items():
                try:
                    c = int(ck)
                except Exception:
                    continue
                v = (cell or {}).get('v')
                if v is None and (cell or {}).get('s'):
                    v = ''
                row[c] = v
                if c > max_c:
                    max_c = c
            rows[r] = row
        # 合并区（0-based → Excel 1-based）
        merges = []
        for m in (_s or {}).get('mergeData') or []:
            try:
                merges.append((m['startRow'] + 1, m['endRow'] + 1,
                               m['startColumn'] + 1, m['endColumn'] + 1))
            except Exception:
                continue
        # 列宽 / 行高（像素 → Excel 单位反算）
        col_w = {}
        for ck, d in ((_s or {}).get('columnData') or {}).items():
            try:
                w = (d or {}).get('w')
                if w:
                    col_w[int(ck)] = w
            except Exception:
                continue
        row_h = {}
        for rk, d in ((_s or {}).get('rowData') or {}).items():
            try:
                h = (d or {}).get('h')
                if h:
                    row_h[int(rk)] = h
            except Exception:
                continue
        out.append({
            'name': (_s or {}).get('name') or 'Sheet1',
            'rows': rows, 'max_c': max_c,
            'merges': merges, 'col_w': col_w, 'row_h': row_h,
        })
    return out


@bp.route('/univer/<int:rid>/export')
def export_room(rid):
    """导出表格：fmt=xlsx（默认，openpyxl）或 fmt=csv（UTF-8 BOM）。

    读取服务端最新快照导出，不依赖浏览器端引擎状态。
    """
    room = _get_room(rid, need_state=True)
    if not room:
        abort(404, description='共享表格不存在或已被删除')
    fmt = (request.args.get('fmt') or 'xlsx').lower()
    if fmt not in ('xlsx', 'csv'):
        fmt = 'xlsx'
    rows, max_c = _snapshot_to_matrix(room['state'] or '')
    base_name = re.sub(r'[\\/:*?"<>|]', '_', room['name'] or '表格')

    def _encode_disp(v):
        if isinstance(v, (int, float)):
            return v
        return str(v)

    if fmt == 'csv':
        buf = io.StringIO()
        buf.write('\ufeff')  # UTF-8 BOM，Excel 打开中文不乱码
        import csv as _csv
        writer = _csv.writer(buf, lineterminator='\r\n')
        max_row_out = max(len(rows), 1)
        for r in range(max_row_out):
            row_map = rows[r] if r < len(rows) else {}
            line = []
            for c in range(max_c + 1):
                line.append(_encode_disp(_cell_to_display(row_map.get(c, ''))))
            writer.writerow(line)
        data = buf.getvalue().encode('utf-8')
        resp = Response(data, mimetype='text/csv; charset=utf-8')
        resp.headers['Content-Disposition'] = (
            "attachment; filename*=UTF-8''" +
            urllib.parse.quote(base_name + '.csv'))
        return resp

    # xlsx（openpyxl）
    try:
        import openpyxl
        from openpyxl.utils import get_column_letter
    except ImportError:
        # openpyxl 不可用时兜底导出 CSV
        buf = io.StringIO()
        buf.write('\ufeff')
        import csv as _csv
        writer = _csv.writer(buf, lineterminator='\r\n')
        max_row_out = max(len(rows), 1)
        for r in range(max_row_out):
            row_map = rows[r] if r < len(rows) else {}
            writer.writerow([_encode_disp(_cell_to_display(row_map.get(c, '')))
                             for c in range(max_c + 1)])
        data = buf.getvalue().encode('utf-8')
        resp = Response(data, mimetype='text/csv; charset=utf-8')
        resp.headers['Content-Disposition'] = (
            "attachment; filename*=UTF-8''" +
            urllib.parse.quote(base_name + '.csv'))
        return resp

    wb = openpyxl.Workbook()
    wb.remove(wb.active)  # 删除默认空表，按快照逐工作表重建
    sheets_out = _snapshot_to_sheets(room['state'] or '')
    if not sheets_out:
        ws = wb.create_sheet(title='Sheet1')
        ws.cell(row=1, column=1, value='')
    for idx, sh in enumerate(sheets_out):
        ws = wb.create_sheet(title=(sh['name'] or 'Sheet%d' % (idx + 1))[:31])
        rows = sh['rows']
        max_c = sh['max_c']
        max_row_out = (max(rows) + 1) if rows else 1
        for r in range(max_row_out):
            row_map = rows.get(r, {})
            for c in range(max_c + 1):
                v = _cell_to_display(row_map.get(c, ''))
                if v == '':
                    continue
                try:
                    ws.cell(row=r + 1, column=c + 1, value=v)
                except Exception:
                    ws.cell(row=r + 1, column=c + 1, value=str(v))
        # 合并单元格还原
        for (r1, r2, c1, c2) in sh['merges']:
            try:
                ws.merge_cells(start_row=r1, end_row=r2, start_column=c1, end_column=c2)
            except Exception:
                pass
        # 列宽还原（px → Excel 字符宽：width = (w-5)/7）
        for c, w in sh['col_w'].items():
            try:
                ws.column_dimensions[get_column_letter(c + 1)].width = max(round((w - 5) / 7, 1), 3)
            except Exception:
                pass
        # 行高还原（px → Excel 单位：height = h*3/4）
        for r, h in sh['row_h'].items():
            try:
                ws.row_dimensions[r + 1].height = max(round(h * 3 / 4, 1), 10)
            except Exception:
                pass
    out = io.BytesIO()
    wb.save(out)
    out.seek(0)
    resp = send_file(out, mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                     as_attachment=False, download_name=base_name + '.xlsx')
    resp.headers['Content-Disposition'] = (
        "attachment; filename*=UTF-8''" +
        urllib.parse.quote(base_name + '.xlsx'))
    return resp


def _summary_for_ai(db, max_chars=6000):
    rooms = db.execute(
        'SELECT id,name,state,version,updated_at,updated_by FROM univer_rooms '
        'ORDER BY COALESCE(updated_at, created_at) DESC, id DESC').fetchall()
    if not rooms:
        return '【云文档表格】共 0 张'
    lines = ['【云文档表格】共 %d 张：' % len(rooms)]
    budget_total = max_chars
    base = max(300, budget_total // max(len(rooms), 1))
    for room in rooms:
        rows_out = []
        max_row = -1
        max_col = -1
        try:
            snap = json.loads(room['state']) if room['state'] else None
        except Exception:
            snap = None
        if snap and isinstance(snap, dict) and snap.get('sheets'):
            sheets = snap['sheets']
            first_sheet = None
            for _k, _s in sheets.items():
                first_sheet = _s
                break
            cell_data = (first_sheet or {}).get('cellData') or {}
            for rk, row_map in cell_data.items():
                try:
                    r = int(rk)
                except Exception:
                    continue
                for ck, cell in row_map.items():
                    try:
                        c = int(ck)
                    except Exception:
                        continue
                    v = (cell or {}).get('v')
                    if v is None and (cell or {}).get('s'):
                        v = ''
                    if v is not None:
                        col_letter = ''
                        n = c + 1
                        while n:
                            n, rem = divmod(n - 1, 26)
                            col_letter = chr(65 + rem) + col_letter
                        rows_out.append('%s%d=%s' % (col_letter, r + 1, v))
                        if r > max_row:
                            max_row = r
                        if c > max_col:
                            max_col = c
        t = (room['updated_at'] or '')
        meta = '表「%s」（房间#%d，v%d' % (room['name'], room['id'], room['version'])
        if t:
            meta += '，%s更新' % t[5:16]
        if room['updated_by']:
            meta += '·%s' % room['updated_by']
        meta += '，%d×%d）' % (max_row + 1 if max_row >= 0 else 0, max_col + 1 if max_col >= 0 else 0)
        if not rows_out:
            lines.append('  · %s（空白表，无数据）' % meta)
            continue
        budget = base
        chunk = '  · ' + meta
        for item in rows_out:
            add = '；' + item
            if len(chunk.encode('utf-8')) + len(add.encode('utf-8')) > budget:
                chunk += '…（余略）'
                break
            chunk += add
        lines.append(chunk)
    return '\n'.join(lines)


# 插件规范：blueprint 别名（插件加载器要求）
blueprint = bp
