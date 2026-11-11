# -*- coding: utf-8 -*-
import os, sqlite3, csv, io
from flask import Blueprint, render_template, request, jsonify, Response, flash, redirect, url_for

blueprint = Blueprint('contract_ledger', __name__, template_folder='templates')
APP_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DB = os.path.join(APP_DIR, 'data', 'contract_ledger.db')
COLS = ['contract_no', 'customer', 'region', 'product_line', 'amount',
        'sign_date', 'delivery_date', 'status', 'owner', 'note']
FIELDS = ['id'] + COLS + ['created_at']
STATUS = ['待签约', '执行中', '已交付', '已回款', '已终止']


def _db():
    c = sqlite3.connect(DB, timeout=10)
    c.row_factory = sqlite3.Row
    return c


def _init():
    os.makedirs(os.path.dirname(DB), exist_ok=True)
    c = sqlite3.connect(DB)
    c.execute('''CREATE TABLE IF NOT EXISTS contract_items(
        id INTEGER PRIMARY KEY AUTOINCREMENT, contract_no TEXT, customer TEXT, region TEXT,
        product_line TEXT, amount REAL, sign_date TEXT, delivery_date TEXT, status TEXT,
        owner TEXT, note TEXT, created_at TEXT)''')
    c.execute('CREATE INDEX IF NOT EXISTS idx_contract_status ON contract_items(status)')
    c.commit(); c.close()


_init()


@blueprint.route('/contract_ledger/')
def index():
    q = (request.args.get('q') or '').strip()
    st = (request.args.get('status') or '').strip()
    try:
        page = max(1, int(request.args.get('page', 1)))
    except Exception:
        page = 1
    size = 50
    c = _db()
    where, args = [], []
    if q:
        where.append('(contract_no LIKE ? OR customer LIKE ? OR product_line LIKE ? OR owner LIKE ?)')
        args += ['%' + q + '%'] * 4
    if st:
        where.append('status = ?'); args.append(st)
    w = (' WHERE ' + ' AND '.join(where)) if where else ''
    total = c.execute('SELECT COUNT(*) FROM contract_items' + w, args).fetchone()[0]
    rows = c.execute('SELECT * FROM contract_items' + w + ' ORDER BY id DESC LIMIT ? OFFSET ?',
                     args + [size, (page - 1) * size]).fetchall()
    stat = c.execute('SELECT status, COUNT(*) n, COALESCE(SUM(amount),0) amt FROM contract_items GROUP BY status').fetchall()
    regions = c.execute('SELECT region, COUNT(*) n FROM contract_items GROUP BY region ORDER BY n DESC LIMIT 8').fetchall()
    allcnt = c.execute('SELECT COUNT(*) FROM contract_items').fetchone()[0]
    c.close()
    pages = max(1, (total + size - 1) // size)
    return render_template('contract_list.html', rows=rows, total=total, page=page, pages=pages,
                           q=q, status=st, stat=stat, regions=regions, allcnt=allcnt, STATUS=STATUS)


@blueprint.route('/contract_ledger/import', methods=['POST'])
def import_csv():
    """导入合同 CSV。
    v12.7.10.18 修复两个缺陷：
    1) 表头不匹配时原来会把整行当成空值插入一条脏记录（contract_no 为空），且返回 ok；
       现在先校验必需表头，不匹配直接拒绝、不写库。
    2) 原来导入成功后返回一串 JSON，用户点完按钮看到的是原始 JSON；
       现在统一 flash 提示并跳回列表页。
    """
    f = request.files.get('file')
    if not f:
        flash('请选择要导入的 CSV 文件', 'error')
        return redirect(url_for('contract_ledger.index'))
    raw = f.read().decode('utf-8-sig', errors='replace')
    rd = csv.DictReader(io.StringIO(raw))
    header = [h.strip() for h in (rd.fieldnames or []) if h]
    missing = [k for k in ('contract_no', 'customer', 'amount', 'status') if k not in header]
    if missing:
        flash('导入失败：表头不完整，缺少「%s」。请先点「导出 CSV」拿到标准表头，填好后再导入。' % '、'.join(missing), 'error')
        return redirect(url_for('contract_ledger.index'))
    n = 0
    skipped = 0
    c = _db()
    try:
        for r in rd:
            if not (r.get('contract_no') or '').strip():
                skipped += 1
                continue
            _amt = (r.get('amount') or '').replace(',', '').strip()
            try:
                _amt = float(_amt) if _amt else 0.0
            except ValueError:
                skipped += 1
                continue
            c.execute('INSERT INTO contract_items (%s) VALUES (%s)' % (
                ','.join(COLS), ','.join('?' * len(COLS))),
                [(_amt if k == 'amount' else (r.get(k) or '').strip()) for k in COLS] + [])
            n += 1
        c.commit()
    finally:
        c.close()
    msg = '已导入 %d 条合同，当前共 %d 条' % (n, _count())
    if skipped:
        msg += '；跳过 %d 条（合同号为空）' % skipped
    flash(msg, 'success')
    return redirect(url_for('contract_ledger.index'))


@blueprint.route('/contract_ledger/export')
def export_csv():
    c = _db()
    rows = c.execute('SELECT * FROM contract_items ORDER BY id').fetchall()
    c.close()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(FIELDS)
    for r in rows:
        w.writerow([r[k] for k in FIELDS])
    data = buf.getvalue().encode('utf-8-sig')
    return Response(data, mimetype='text/csv',
                    headers={'Content-Disposition': 'attachment; filename=contracts.csv'})


def _count():
    c = sqlite3.connect(DB); n = c.execute('SELECT COUNT(*) FROM contract_items').fetchone()[0]; c.close()
    return n


# ===== 数据开放接口 OpenAPI v1（《插件开发规范》§13）=====
@blueprint.route('/contract_ledger/__openapi/datasets')
def __openapi_datasets():
    c = _db()
    try:
        cnt = c.execute('SELECT COUNT(*) FROM contract_items').fetchone()[0]
        cols = [r[1] for r in c.execute('PRAGMA table_info(contract_items)').fetchall()]
    finally:
        c.close()
    return jsonify({'openapi': 1, 'plugin': 'contract_ledger', 'tables': [{
        'name': 'contract_items', 'label': '合同台账', 'primary_key': 'id',
        'time_field': 'sign_date', 'rows': cnt, 'sensitive': False,
        'fields': [{'name': x, 'type': 'real' if x == 'amount' else 'text', 'label': x} for x in cols],
        'description': '一行=一份销售合同；status：待签约/执行中/已交付/已回款/已终止；amount 单位元'}]})


@blueprint.route('/contract_ledger/__openapi/rows')
def __openapi_rows():
    table = request.args.get('table', '')
    if table != 'contract_items':
        return jsonify({'openapi': 1, 'error': 'unknown table'}), 400
    try:
        limit = max(1, min(int(request.args.get('limit', 100)), 1000))
        offset = max(0, int(request.args.get('offset', 0)))
    except Exception:
        limit, offset = 100, 0
    c = _db()
    try:
        total = c.execute('SELECT COUNT(*) FROM contract_items').fetchone()[0]
        rows = [dict(r) for r in c.execute(
            'SELECT * FROM contract_items ORDER BY id LIMIT ? OFFSET ?', (limit, offset)).fetchall()]
    finally:
        c.close()
    return jsonify({'openapi': 1, 'table': table, 'offset': offset, 'limit': limit, 'total': total,
                    'fields': FIELDS, 'rows': rows})
