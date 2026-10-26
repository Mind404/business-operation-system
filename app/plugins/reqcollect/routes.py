# -*- coding: utf-8 -*-
"""需求收集插件（Luckysheet 云文档版）。

以「工作簿」为单位：每个子表 = 一个 Luckysheet 工作簿（可含多个 sheet 标签），
工作簿完整 JSON 存于插件自管表 reqcollect_books.json_data。

权限约定：
- 新建/删除工作簿（子表）：管理员 / 平台管理员
- 打开、编辑、保存工作簿：所有登录用户（类云文档协作）
"""
import json

from flask import (Blueprint, render_template, request, redirect, url_for,
                   flash, jsonify, session, abort)
from flask import g
import sqlite3
from datetime import datetime

from plugins.base import make_blueprint
from plugin_system import log_operation

bp = make_blueprint('reqcollect', __name__, static_url_path='/reqcollect/static')

MANAGER_ROLES = ('admin', 'platform_admin')
MAX_JSON_BYTES = 8 * 1024 * 1024   # 单工作簿 JSON 上限 8MB
MAX_SHEETS = 20                    # 单工作簿 sheet 上限


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
    """插件自管表（幂等建表 + 索引）"""
    db = get_db()
    db.executescript('''
    CREATE TABLE IF NOT EXISTS reqcollect_books (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        name        TEXT NOT NULL,
        description TEXT DEFAULT '',
        created_by  TEXT NOT NULL,
        created_at  TEXT DEFAULT (datetime('now','localtime')),
        updated_at  TEXT,
        updated_by  TEXT,
        json_data   TEXT DEFAULT '[]'
    );
    CREATE INDEX IF NOT EXISTS idx_reqcollect_books_created ON reqcollect_books(created_by);
    ''')


def _now():
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def _username():
    return session.get('user_name') or session.get('username') or '未知用户'


def _is_manager():
    return session.get('user_role') in MANAGER_ROLES


def _default_book(name, description):
    """新建工作簿的初始结构：一个空 sheet。"""
    return json.dumps([{
        'name': 'Sheet1',
        'celldata': [],
        'config': {},
        'row': 100,
        'column': 26,
    }], ensure_ascii=False)


# ============================================================
# 列表页
# ============================================================
@bp.route('/reqcollect')
def index():
    _init_tables()
    db = get_db()
    books = db.execute('SELECT * FROM reqcollect_books ORDER BY id DESC').fetchall()
    return render_template('reqcollect_index.html', books=books, is_manager=_is_manager())


# ============================================================
# 子表管理（管理组）
# ============================================================
@bp.route('/reqcollect/add', methods=['POST'])
def book_add():
    """新建子表（工作簿）"""
    if not _is_manager():
        return abort(403, description='无权限：新建子表需要管理员或平台管理员权限')
    name = (request.form.get('name') or '').strip()
    description = (request.form.get('description') or '').strip()
    if not name:
        flash('子表名称不能为空', 'error')
        return redirect(url_for('reqcollect.index'))
    if len(name) > 50:
        flash('子表名称过长（最多50字）', 'error')
        return redirect(url_for('reqcollect.index'))
    db = get_db()
    _init_tables()
    try:
        db.execute(
            'INSERT INTO reqcollect_books (name, description, created_by, json_data) VALUES (?,?,?,?)',
            (name, description, _username(), _default_book(name, description)),
        )
        db.commit()
        log_operation('需求收集管理', '新建子表', f'新建子表「{name}」', _username())
        flash(f'子表「{name}」创建成功', 'ok')
    except Exception as e:
        db.rollback()
        flash(f'创建失败：{e}', 'error')
    return redirect(url_for('reqcollect.index'))


@bp.route('/reqcollect/<int:sid>/delete', methods=['POST'])
def book_delete(sid):
    """删除子表（工作簿，含全部数据）"""
    if not _is_manager():
        return abort(403, description='无权限：删除子表需要管理员或平台管理员权限')
    db = get_db()
    _init_tables()
    try:
        book = db.execute('SELECT * FROM reqcollect_books WHERE id=?', (sid,)).fetchone()
        if not book:
            flash('子表不存在或已删除', 'error')
            return redirect(url_for('reqcollect.index'))
        name = book['name']
        db.execute('DELETE FROM reqcollect_books WHERE id=?', (sid,))
        db.commit()
        log_operation('需求收集管理', '删除子表', f'删除子表「{name}」(id={sid})', _username())
        flash(f'子表「{name}」已删除（含其全部数据）', 'ok')
    except Exception as e:
        db.rollback()
        flash(f'删除失败：{e}', 'error')
    return redirect(url_for('reqcollect.index'))


# ============================================================
# 工作簿：打开 / 读取 JSON / 保存
# ============================================================
@bp.route('/reqcollect/<int:sid>')
def book_view(sid):
    """打开工作簿（Luckysheet 云文档表格页，全员可访问）"""
    _init_tables()
    db = get_db()
    book = db.execute('SELECT * FROM reqcollect_books WHERE id=?', (sid,)).fetchone()
    if not book:
        flash('子表不存在或已删除', 'error')
        return redirect(url_for('reqcollect.index'))
    return render_template('reqcollect_sheet.html', book=dict(book),
                           is_manager=_is_manager(), current_user=_username())


@bp.route('/reqcollect/<int:sid>/data')
def book_data(sid):
    """返回工作簿 JSON（供 Luckysheet 加载，全员可读）"""
    _init_tables()
    db = get_db()
    book = db.execute('SELECT id,name,updated_at,updated_by,json_data FROM reqcollect_books WHERE id=?',
                      (sid,)).fetchone()
    if not book:
        return jsonify({'ok': False, 'msg': '子表不存在或已删除'})
    try:
        data = json.loads(book['json_data'] or '[]')
    except Exception:
        data = []
    return jsonify({
        'ok': True,
        'id': book['id'],
        'name': book['name'],
        'updated_at': book['updated_at'],
        'updated_by': book['updated_by'],
        'data': data,
    })


@bp.route('/reqcollect/<int:sid>/save', methods=['POST'])
def book_save(sid):
    """保存整个工作簿 JSON（全员可写）"""
    _init_tables()
    db = get_db()
    book = db.execute('SELECT * FROM reqcollect_books WHERE id=?', (sid,)).fetchone()
    if not book:
        return jsonify({'ok': False, 'msg': '子表不存在或已删除'})
    try:
        payload = request.get_json(force=True, silent=True) or {}
        data = payload.get('data')
        if data is None and isinstance(payload, list):
            data = payload
    except Exception:
        data = None
    if not isinstance(data, list) or len(data) < 1:
        return jsonify({'ok': False, 'msg': '数据格式不正确：应为工作簿 sheet 数组'})
    if len(data) > MAX_SHEETS:
        return jsonify({'ok': False, 'msg': f'sheet 数量超过上限（{MAX_SHEETS}个）'})
    raw = json.dumps(data, ensure_ascii=False)
    if len(raw.encode('utf-8')) > MAX_JSON_BYTES:
        return jsonify({'ok': False, 'msg': '工作簿数据超过 8MB 上限，请精简内容'})
    try:
        db.execute(
            'UPDATE reqcollect_books SET json_data=?, updated_at=?, updated_by=? WHERE id=?',
            (raw, _now(), _username(), sid),
        )
        db.commit()
        log_operation('需求收集管理', '保存子表', f'保存子表「{book["name"]}」(id={sid})', _username())
        return jsonify({'ok': True, 'time': _now(), 'by': _username()})
    except Exception as e:
        db.rollback()
        return jsonify({'ok': False, 'msg': f'保存失败：{e}'})


# 兼容插件加载器：加载器按约定读取 routes.blueprint
blueprint = bp
