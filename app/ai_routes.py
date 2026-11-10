# -*- coding: utf-8 -*-
"""
平台管理员 AI 智能助手（v12.4.1）
====================================
- 仅平台管理员（platform_admin）可访问：页面、接口全部拦截，其他角色不可感知
- 支持接入本地 / 云端 OpenAI 兼容大模型（Ollama、OpenAI、各类中转服务）
- AI 可读取系统全部数据库（含插件数据表），用于问答与数据分析
- 全部使用标准库实现（urllib），便携版零新增依赖
"""
import json
import re
import sqlite3
import urllib.error
import urllib.request
from datetime import datetime

from flask import Blueprint, Response, current_app, g, jsonify, render_template, request, session

ai_bp = Blueprint('ai_admin', __name__, url_prefix='/admin/ai')


def _db():
    if 'ai_db' not in g:
        g.ai_db = sqlite3.connect(current_app.config['DATABASE'])
        g.ai_db.row_factory = sqlite3.Row
        _init_tables(g.ai_db)
    return g.ai_db


def _init_tables(db):
    """会话与消息表（幂等建表）"""
    db.execute('''CREATE TABLE IF NOT EXISTS ai_conversations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT DEFAULT '新会话',
        created_at TEXT,
        updated_at TEXT,
        msg_count INTEGER DEFAULT 0,
        char_count INTEGER DEFAULT 0,
        summary TEXT DEFAULT '',
        summary_up_to_id INTEGER DEFAULT 0
    )''')
    db.execute('''CREATE TABLE IF NOT EXISTS ai_messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        conv_id INTEGER NOT NULL,
        role TEXT,
        content TEXT,
        created_at TEXT,
        aborted INTEGER DEFAULT 0
    )''')
    # 幂等迁移：中止标记列（旧库补列）
    cols = [r[1] for r in db.execute('PRAGMA table_info(ai_messages)').fetchall()]
    if 'aborted' not in cols:
        db.execute('ALTER TABLE ai_messages ADD COLUMN aborted INTEGER DEFAULT 0')
    # v12.5.20：分层记忆列（长期摘要 + 压缩覆盖点）
    ccols = [r[1] for r in db.execute('PRAGMA table_info(ai_conversations)').fetchall()]
    if 'summary' not in ccols:
        db.execute('ALTER TABLE ai_conversations ADD COLUMN summary TEXT DEFAULT \'\'')
    if 'summary_up_to_id' not in ccols:
        db.execute('ALTER TABLE ai_conversations ADD COLUMN summary_up_to_id INTEGER DEFAULT 0')
    db.commit()


def _require_platform():
    """非平台管理员：页面/接口一律拒绝（返回 False 表示无权限）"""
    return session.get('user_role') == 'platform_admin'


def _deny():
    return jsonify({'ok': False, 'msg': '仅平台管理员可访问'}), 403


# ============================================================
# 页面
# ============================================================
@ai_bp.route('/')
def index():
    if not _require_platform():
        return render_template('error.html', title='无权限访问', message='仅平台管理员可访问此功能'), 403
    return render_template('admin_ai.html')


# ============================================================
# 服务商配置 CRUD
# ============================================================
@ai_bp.route('/api/providers')
def list_providers():
    if not _require_platform():
        return _deny()
    rows = _db().execute('SELECT * FROM ai_providers ORDER BY id').fetchall()
    return jsonify({'ok': True, 'providers': [dict(r) for r in rows]})


@ai_bp.route('/api/providers/save', methods=['POST'])
def save_provider():
    if not _require_platform():
        return _deny()
    d = request.get_json(silent=True) or {}
    pid = d.get('id')
    name = (d.get('name') or '').strip()
    api_url = (d.get('api_url') or '').strip()
    api_key = (d.get('api_key') or '').strip()
    models = (d.get('models') or '').strip()
    default_model = (d.get('default_model') or '').strip()
    enabled = 1 if d.get('enabled') else 0
    if not name:
        return jsonify({'ok': False, 'msg': '请填写服务商名称'})
    if not api_url:
        return jsonify({'ok': False, 'msg': '请填写 API 地址'})
    db = _db()
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    if pid:
        db.execute('UPDATE ai_providers SET name=?, api_url=?, api_key=?, models=?, default_model=?, enabled=?, updated_at=? WHERE id=?',
                   (name, api_url, api_key, models, default_model, enabled, now, pid))
    else:
        cur = db.execute('INSERT INTO ai_providers (name, api_url, api_key, models, default_model, enabled, updated_at) VALUES (?,?,?,?,?,?,?)',
                         (name, api_url, api_key, models, default_model, enabled, now))
        pid = cur.lastrowid
    db.commit()
    return jsonify({'ok': True, 'id': pid})


@ai_bp.route('/api/providers/<int:pid>/delete', methods=['POST'])
def delete_provider(pid):
    if not _require_platform():
        return _deny()
    db = _db()
    db.execute('DELETE FROM ai_providers WHERE id=?', (pid,))
    db.commit()
    return jsonify({'ok': True})


# ============================================================
# 连通性检测：拉取模型列表
# ============================================================
@ai_bp.route('/api/detect', methods=['POST'])
def detect():
    if not _require_platform():
        return _deny()
    d = request.get_json(silent=True) or {}
    api_url = (d.get('api_url') or '').strip().rstrip('/')
    api_key = (d.get('api_key') or '').strip()
    if not api_url:
        return jsonify({'ok': False, 'msg': '请先填写 API 地址'})
    url = api_url + '/models'
    req = urllib.request.Request(url, headers={'Authorization': 'Bearer ' + api_key} if api_key else {})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = resp.read().decode('utf-8', errors='replace')
        try:
            data = json.loads(raw)
        except Exception:
            return jsonify({'ok': True, 'msg': '服务可达，但返回内容不是标准 JSON，请确认地址为 OpenAI 兼容 /v1 端点', 'models': []})
        models = []
        # OpenAI 兼容: data[].id ；Ollama 也兼容此格式
        for m in (data.get('data') or []):
            mid = m.get('id') or m.get('name')
            if mid:
                models.append(mid)
        if not models and isinstance(data, dict) and data.get('models'):
            for m in data['models']:
                mid = m.get('name') or m.get('id')
                if mid:
                    models.append(mid)
        if not models:
            return jsonify({'ok': True, 'msg': '服务可达，但未能解析出模型列表（页面中手工填写模型名即可）', 'models': []})
        return jsonify({'ok': True, 'msg': '连接成功，发现 %d 个模型' % len(models), 'models': models})
    except urllib.error.HTTPError as e:
        return jsonify({'ok': False, 'msg': 'HTTP %d：%s' % (e.code, e.read().decode('utf-8', errors='replace')[:300])})
    except Exception as e:
        return jsonify({'ok': False, 'msg': '连接失败：%s' % e})


# ============================================================
# 会话管理（持久化）：新建 / 列表 / 消息 / 删除
# ============================================================
def _now_str():
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')


@ai_bp.route('/api/conversations')
def list_conversations():
    if not _require_platform():
        return _deny()
    rows = _db().execute(
        'SELECT id, title, created_at, updated_at, msg_count, char_count '
        'FROM ai_conversations ORDER BY updated_at DESC LIMIT 100').fetchall()
    return jsonify({'ok': True, 'conversations': [dict(r) for r in rows]})


@ai_bp.route('/api/conversations', methods=['POST'])
def new_conversation():
    if not _require_platform():
        return _deny()
    db = _db()
    now = _now_str()
    cur = db.execute('INSERT INTO ai_conversations (title, created_at, updated_at) VALUES (?,?,?)',
                     ('新会话', now, now))
    db.commit()
    return jsonify({'ok': True, 'id': cur.lastrowid})


@ai_bp.route('/api/conversations/<int:cid>/messages')
def conv_messages(cid):
    if not _require_platform():
        return _deny()
    db = _db()
    conv = db.execute('SELECT * FROM ai_conversations WHERE id=?', (cid,)).fetchone()
    if not conv:
        return jsonify({'ok': False, 'msg': '会话不存在'}), 404
    rows = db.execute('SELECT id, role, content, created_at, aborted FROM ai_messages WHERE conv_id=? ORDER BY id', (cid,)).fetchall()
    return jsonify({'ok': True, 'conversation': dict(conv), 'messages': [dict(r) for r in rows]})


@ai_bp.route('/api/conversations/<int:cid>/delete', methods=['POST'])
def delete_conversation(cid):
    if not _require_platform():
        return _deny()
    db = _db()
    db.execute('DELETE FROM ai_messages WHERE conv_id=?', (cid,))
    db.execute('DELETE FROM ai_conversations WHERE id=?', (cid,))
    db.commit()
    return jsonify({'ok': True})


# ============================================================
# 数据库上下文采集（含插件表）：只读、白名单表名、按问题相关性、仅注入有数据表
# ============================================================
_SAFE_TABLE = re.compile(r'^[A-Za-z0-9_]+$')
# v12.7.10.10：平台系统表一律不进 AI 上下文（账号密码哈希、登录令牌、审计日志等不得外发）
_EXCLUDE_TABLES = ('schema_version', 'ai_chat_logs', 'ai_providers', 'ai_conversations',
                   'ai_messages', 'users', 'operation_logs', 'plugin_registry', 'user_plugins',
                   'user_sessions', 'ui_prefs', 'tasks', 'approval_requests')


_SEL_STOP = {'合同', '台账', '需求', '任务', '项目', '金额', '客户', '负责人', '状态', '阻塞', '部门',
             '优先级', '汇总', '统计', '明细', '列表', '所有', '每个', '分别', '情况', '数据', '记录',
             '当前', '目前', '平台', '系统', '一共', '总共', '多少', '哪些', '怎么', '如何', '是否',
             '有没有', '分别是', '还有', '哪些人', '谁负责', '看一下', '分析', '查看', '查询', '现在'}


def _selective_words(question):
    """行级检索专用关键词：只保留有区分度的词（编号、专有名词、≥4 字中文片段），
    避免"合同/任务/需求"这类泛词把整表前几行都命中（v12.7.14）。"""
    out = []
    for w in re.findall(r'[A-Za-z][A-Za-z0-9_\-]{2,}', question):
        out.append(w)
    for seg in re.findall(r'[\u4e00-\u9fa5]{2,10}', question):
        for i in range(len(seg)):
            for j in range(i + 4, min(i + 9, len(seg) + 1)):
                frag = seg[i:j]
                if frag not in _SEL_STOP:
                    out.append(frag)
    seen, res = set(), []
    for w in sorted(out, key=lambda x: -len(x)):
        if w not in seen:
            seen.add(w); res.append(w)
    return res[:8]

def _table_words(question):
    """从问题中提取关键词（中英文单词/短语片段），返回按长度优先去重排序的列表，
    用于表相关性匹配与行数据搜索（实体词越长越具区分度，排前面）。"""
    words = set()
    # 英文单词/数字（含型号类：SAM203 / 5G / WiFi6）
    for w in re.findall(r'[a-zA-Z][a-zA-Z0-9_-]{1,}', question.lower()):
        words.add(w)
    # 中文 2-6 字滑动片段（去常见虚词/业务泛词）
    cn = re.findall(r'[\u4e00-\u9fa5]{2,6}', question)
    stop = {'什么', '多少', '如何', '怎么', '为什么', '是否', '统计', '分析', '汇总', '查看', '查询',
            '目前', '当前', '情况', '状态', '数量', '进度', '完成', '计划', '项目', '任务', '数据',
            '有没有', '需要', '一下', '可以', '帮我', '以及', '还有', '全部', '各个',
            '多少', '哪些', '几个', '一', '两', '三', '四', '五', '六', '七', '八', '九', '十'}
    for w in cn:
        for i in range(len(w)):
            for j in range(i + 2, min(i + 5, len(w) + 1)):
                frag = w[i:j]
                if frag not in stop:
                    words.add(frag)
    # 长度优先（实体名长片段优先），等长按字典序，最多取 12 个
    ordered = sorted(words, key=lambda x: (-len(x), x))
    return ordered[:12]


# 注入 AI 上下文时排除的敏感列（如密码哈希）
_SENSITIVE_COL = ('password',)


def _safe_sample_cols(cols):
    """过滤敏感列（如密码哈希），避免进入 AI 上下文"""
    return [c for c in cols if c not in _SENSITIVE_COL]


def _univer_summary(db, max_chars=9000, words=None):
    """需求统计管理提取（v12.7.14 重构）：
    大表（几百~几千行）不再把单元格全文拼接后被截断，而是输出
    「表头 + 确定性列画像（合计/均值/分布）+ 3 行样例」，让 AI 能回答
    汇总统计类问题；每张表仍标注真实行数，避免误判为数据不完整。"""
    import json as _json
    try:
        from plugins.univer_sheet.routes import _cell_text
    except Exception:
        def _cell_text(cell):
            if not cell or not isinstance(cell, dict):
                return None
            v = cell.get('v')
            if v is not None:
                return v
            p = cell.get('p')
            if p is None:
                return None
            if isinstance(p, str):
                return p
            if isinstance(p, dict):
                body = p.get('body')
                if isinstance(body, dict) and body.get('dataStream'):
                    return body['dataStream'].rstrip('\r\n')
                return p.get('t')
            if isinstance(p, list):
                parts = []
                for seg in p:
                    if isinstance(seg, str):
                        parts.append(seg)
                    elif isinstance(seg, dict):
                        b = seg.get('body')
                        if isinstance(b, dict) and b.get('dataStream'):
                            parts.append(b['dataStream'])
                        else:
                            parts.append(seg.get('t') or str(seg.get('v') or ''))
                txt = ''.join(parts)
                return txt.rstrip('\r\n') if txt else None
            return None
    try:
        rooms = db.execute(
            'SELECT id, name, description, state, version, updated_at, updated_by '
            'FROM univer_rooms WHERE deleted = 0 ORDER BY id DESC').fetchall()
    except Exception:
        return ''
    if not rooms:
        return ''
    total = len(rooms)
    words = words or []
    per = max(900, max_chars // total)
    parts = []
    for r in rooms:
        sheet_txts = []
        snap = None
        try:
            snap = _json.loads(r['state']) if r['state'] else None
        except Exception:
            snap = None
        if snap and isinstance(snap, dict):
            sheets = snap.get('sheets')
            if not isinstance(sheets, dict):
                continue
            for _sk, _s in sheets.items():
                if not isinstance(_s, dict):
                    continue
                cell_data = _s.get('cellData')
                if not isinstance(cell_data, dict):
                    sheet_txts.append('〔工作表：%s〕（空白表，无数据）' % ((_s or {}).get('name') or _sk))
                    continue
                grid, maxrow, maxcol = {}, -1, -1
                for rk, row_map in cell_data.items():
                    if not isinstance(row_map, dict):
                        continue
                    try:
                        rr = int(rk)
                    except Exception:
                        continue
                    for ck, cell in row_map.items():
                        try:
                            cc = int(ck)
                        except Exception:
                            continue
                        v = _cell_text(cell)
                        if v is None or (isinstance(v, str) and not v.strip()):
                            continue
                        grid.setdefault(rr, {})[cc] = str(v)
                        if rr > maxrow:
                            maxrow = rr
                        if cc > maxcol:
                            maxcol = cc
                sname = (_s or {}).get('name') or _sk
                if maxrow < 0:
                    sheet_txts.append('〔工作表：%s〕（空白表，无数据）' % sname)
                    continue
                names = []
                for c in range(maxcol + 1):
                    h = grid.get(0, {}).get(c, '')
                    names.append(h[:20] if h else '列%d' % (c + 1))
                rows = []
                for rr in range(1, min(maxrow, 3000) + 1):
                    rd = {}
                    for c in range(maxcol + 1):
                        rd[names[c]] = grid.get(rr, {}).get(c, '')
                    if any(str(v).strip() for v in rd.values()):
                        rows.append(rd)
                prof = _profile_text(names, rows, total=maxrow, budget=1100)
                extra = '\n'.join(x for x in (_kw_rows(names, rows, words),
                                              _extreme_rows(names, rows)) if x)
                sample = _json.dumps(rows[:3], ensure_ascii=False, default=str)[:480]
                stxt = ('〔工作表：%s，共 %d 行 x %d 列〕表头: %s\n%s\n%s\n样例行(前3行): %s' % (
                    sname, maxrow, maxcol + 1, ' | '.join(names), prof, extra, sample))
                sheet_txts.append(stxt)
        head = '表「%s」（房间#%s，v%s，%s更新·%s，工作表%d 个）' % (
            r['name'] or '(未命名)', r['id'], r['version'],
            (r['updated_at'] or '')[:16], r['updated_by'] or '?',
            len(sheet_txts))
        if not sheet_txts:
            txt = head + '\n  （空白表，无数据）'
        else:
            txt = head + '\n' + '\n'.join(sheet_txts)
            if len(txt) > per:
                txt = txt[:per] + '\n…（本表内容超长，已截断，请告知用户数据可能不完整）'
        parts.append(txt)
    return '【需求统计管理】共 %d 张（按新建先后倒序，以下为各表内容）：\n\n%s' % (total, '\n\n'.join(parts))



def _search_wbs_rows(db, words, max_rows_proj=6, max_rows_task=12):
    """方案B：按问题关键词到 WBS 项目/任务【行数据】中搜索相关行注入。
    - 项目：name/description/owner/participants 命中关键词
    - 任务：自身字段命中关键词，或属于命中的项目（project_id IN 命中项目）
    返回 (项目文本, 任务文本)；无命中返回 (None, None)"""
    proj_txt = None
    task_txt = None
    if not words:
        return proj_txt, task_txt
    proj_conds, proj_params = [], []
    for w in words:
        proj_conds.append('(name LIKE ? OR description LIKE ? OR owner LIKE ? OR participants LIKE ?)')
        proj_params += ['%%%s%%' % w] * 4
    try:
        projs = db.execute(
            'SELECT * FROM wbs_projects WHERE ' + ' OR '.join(proj_conds) + ' LIMIT %d' % max_rows_proj,
            proj_params).fetchall()
    except Exception:
        projs = []
    pids = [r['id'] for r in projs]
    if projs:
        proj_txt = '【表 wbs_projects·相关】（%d/%d 行）列: %s\n%s' % (
            len(projs),
            db.execute('SELECT COUNT(*) AS c FROM wbs_projects').fetchone()['c'],
            ', '.join(_safe_sample_cols([c[1] for c in db.execute('PRAGMA table_info(wbs_projects)').fetchall()])),
            '\n'.join(_row_line(r) for r in projs[:max_rows_proj]))
    # 任务：字段命中 或 属于命中项目
    task_conds, task_params = [], []
    for w in words:
        task_conds.append('(name LIKE ? OR work_content LIKE ? OR owner LIKE ? OR collaborators LIKE ? OR deliverable LIKE ? OR status LIKE ? OR wbs_code LIKE ?)')
        task_params += ['%%%s%%' % w] * 7
    sql = 'SELECT * FROM wbs_tasks WHERE ' + ' OR '.join(task_conds)
    if pids:
        sql += ' OR project_id IN (%s)' % ','.join('?' * len(pids))
        task_params += pids
    sql += ' ORDER BY id LIMIT %d' % max_rows_task
    try:
        tasks = db.execute(sql, task_params).fetchall()
    except Exception:
        tasks = []
    if tasks:
        task_txt = '【表 wbs_tasks·相关】（%d/%d 行）列: %s\n%s' % (
            len(tasks),
            db.execute('SELECT COUNT(*) AS c FROM wbs_tasks').fetchone()['c'],
            ', '.join(_safe_sample_cols([c[1] for c in db.execute('PRAGMA table_info(wbs_tasks)').fetchall()])),
            '\n'.join(_row_line(r) for r in tasks[:max_rows_task]))
    return proj_txt, task_txt


def _row_line(row, max_chars=320):
    """一行记录转紧凑 JSON 文本（截断防上下文爆炸）"""
    d = {k: v for k, v in dict(row).items() if k not in _SENSITIVE_COL}
    t = __import__('json').dumps(d, ensure_ascii=False, default=str)
    return t[:max_chars]


def _collect_db_context(question, max_chars=14000):
    """只注入【有数据】的表；按问题关键词做相关性排序（相关表优先完整注入），
    空表与无关表直接跳过 —— 让小模型聚焦真实业务数据，避免被无关空表干扰发散。
    特殊表：univer_rooms（需求统计管理，每行=整本 Workbook 快照 JSON）走专用解析，全量转文本注入。
    WBS 业务表（wbs_projects/wbs_tasks）：按问题关键词做【行数据】搜索注入（方案B），
    命中具体项目/任务时其明细行进入上下文，未命中则回退样例。"""
    db = _db()
    rows = db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name").fetchall()
    tables = []
    for r in rows:
        t = r['name']
        if t in _EXCLUDE_TABLES or not _SAFE_TABLE.match(t):
            continue
        try:
            cols = [c[1] for c in db.execute('PRAGMA table_info(%s)' % t).fetchall()]
            cnt = db.execute('SELECT COUNT(*) AS c FROM %s' % t).fetchone()['c']
            tables.append((t, cnt, cols))
        except Exception:
            pass
    # 只保留有数据的表
    active = [x for x in tables if x[1] > 0]
    if not active:
        return '（当前数据库中暂无任何业务数据）'

    words = _table_words(question)
    scored = []
    for t, cnt, cols in active:
        hay = (t + ' ' + ' '.join(cols)).lower()
        score = sum(1 for w in words if w in hay)
        scored.append((t, cnt, cols, score))
    # 需求收集工作簿：问题常提及"需求/收集/子表"而表名/列名不含这些词，
    # 但它是需求收集插件的数据本体 → 强制提升相关性，保证完整注入
    scored = [(t, cnt, cols, s + 100) if t in ('univer_rooms',) else (t, cnt, cols, s) for t, cnt, cols, s in scored]
    # WBS 业务核心表同样提升优先级（保证有数据即进入上下文，避免被挤掉）
    scored = [(t, cnt, cols, s + 60) if t in ('wbs_projects', 'wbs_tasks') else (t, cnt, cols, s) for t, cnt, cols, s in scored]
    # 相关表在前（score 降序），其余有数据表在后
    scored.sort(key=lambda x: -x[3])

    # 动态预算：问题指向需求收集时给足，否则给需求收集少一些、把空间让给 WBS 行数据
    req_words = ('需求', '收集', '子表', '表', '采集', '清单')
    req_budget = 11000 if any(w in question for w in req_words) else 6500

    # 预先做 WBS 行级搜索（在预算内优先于样例）
    sel = _selective_words(question)
    proj_txt, task_txt = _search_wbs_rows(db, (sel or words), max_rows_task=15)

    parts = []
    used = 0
    total_active = len(active)
    related = sum(1 for x in scored if x[3] > 0)
    for idx, (t, cnt, cols, score) in enumerate(scored):
        if t == 'univer_rooms':
            part = _univer_summary(db, max_chars=req_budget, words=sel)
            tag = '相关'
        elif t == 'wbs_projects':
            if proj_txt:
                part = proj_txt
            else:
                part = _sample_part(db, t, cnt, cols, score, sel)
            tag = '相关' if (proj_txt or score > 0) else ''
        elif t == 'wbs_tasks':
            if task_txt:
                part = task_txt
            else:
                part = _sample_part(db, t, cnt, cols, score, words)
            tag = '相关' if (task_txt or score > 0) else ''
        else:
            part = _sample_part(db, t, cnt, cols, score, words)
            tag = '相关' if score > 0 else ''
        if used + len(part) > max_chars:
            # 至少保证最相关块能装入
            if not parts:
                parts.append(part)
                used += len(part)
            break
        parts.append(part)
        used += len(part)
    head = '数据库概要：共 %d 张业务表，其中 %d 张有数据（%d 张与你的问题相关）。只列出有数据的表，空表未读取。' % (
        len(tables), total_active, related)
    # v12.7.0 数据隔离（方案B）：上传插件的数据表在独立库 app/data/<插件id>.db，跨库只读注入
    import os as _os
    data_dir = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), 'data')
    plug_parts, plug_files = [], []
    # v12.7.10.10 插件数据开放：按 manifest 的 data_open 声明决定是否读、读哪些表
    try:
        import plugin_system as _ps
        _src = {x['id']: x for x in _ps.plugin_data_sources()}
    except Exception:
        _src = {}
    # v12.7.10.10 数据开放优先：插件实现了 OpenAPI v1 接口，就走接口取数（ai_routes 不再直连插件库）
    if _src:
        for _pid, src in _src.items():
            if src.get('access') != 'openapi':
                continue
            name_p = src.get('name') or _pid
            if src.get('ai_readable') is False:
                plug_parts.append('插件「%s」声明数据不对平台 AI 开放（data_open.ai_readable=false），已跳过。' % name_p)
                continue
            allow_tables = set(src.get('tables') or [])
            usable = []
            for d in (src.get('openapi') or {}).get('tables') or []:
                if not isinstance(d, dict):
                    continue
                t = d.get('name')
                if not t or not isinstance(t, str):
                    continue
                if allow_tables and t not in allow_tables:
                    continue
                if t in _EXCLUDE_TABLES or not _SAFE_TABLE.match(t):
                    continue
                try:
                    cnt = int(d.get('rows') or 0)
                except Exception:
                    cnt = 0
                if cnt > 0:
                    usable.append((d, t, cnt))
            if not usable:
                plug_parts.append('插件「%s」开放接口已接通，但当前没有数据表有数据。' % name_p)
                plug_files.append(_pid)
                continue
            lines = ['外部插件「%s」开放数据（OpenAPI v1 接口，%d 张有数据表）：' % (name_p, len(usable))]
            for d, t, cnt in usable:
                _rows_all, _off = [], 0
                while _off < 3000:
                    _g = _ps.plugin_openapi_rows(_pid, t, limit=1000, offset=_off) or {}
                    _rs = _g.get('rows') or []
                    if not _rs:
                        break
                    _rows_all.extend(_rs)
                    _off += 1000
                    if len(_rs) < 1000:
                        break
                got = {'rows': _rows_all}
                rows = _rows_all
                cols = (got or {}).get('fields') or d.get('fields') or []
                if cols and isinstance(cols[0], dict):
                    cols = [c.get('name') for c in cols]
                cols = _safe_sample_cols([c for c in cols if c])
                safe_rows = []
                for r in rows:
                    if isinstance(r, dict):
                        safe_rows.append({k: v for k, v in r.items() if k not in _SENSITIVE_COL})
                    else:
                        safe_rows.append(r)
                prof = _profile_text(cols, safe_rows, total=cnt, budget=1000)
                extra = '\n'.join(x for x in (_kw_rows(cols, safe_rows, sel),
                                              _extreme_rows(cols, safe_rows)) if x)
                lines.append('【表 %s·相关】（%d 行）列: %s\n%s\n%s\n样例数据: %s' % (
                    t, cnt, ', '.join(cols), prof, extra,
                    __import__('json').dumps(safe_rows[:2], ensure_ascii=False, default=str)[:480]))
            plug_parts.append('\n'.join(lines))
            plug_files.append(_pid)
    # 兼容兜底：没有开放接口的老插件，直读 data/<插件id>.db（AI 上下文显式标注"直读兜底"）
    if _os.path.isdir(data_dir):
        for fn in sorted(_os.listdir(data_dir)):
            if not fn.endswith('.db'):
                continue
            pid = fn[:-3]
            src = _src.get(pid) or {}
            if src.get('access') == 'openapi':
                continue
            if src and src.get('ai_readable') is False:
                plug_parts.append('插件「%s」在 manifest 中声明数据不对平台 AI 开放（data_open.ai_readable=false），已跳过。'
                                  % src.get('name', pid))
                continue
            allow_tables = set(src.get('tables') or [])
            try:
                cdb = sqlite3.connect(_os.path.join(data_dir, fn), timeout=3.0)
                cdb.row_factory = sqlite3.Row
                tabs = [x[0] for x in cdb.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' "
                    "ORDER BY name").fetchall()]
                ptabs = []
                for t in tabs:
                    if t in _EXCLUDE_TABLES or not _SAFE_TABLE.match(t):
                        continue
                    if allow_tables and t not in allow_tables:
                        continue
                    try:
                        cols = [c[1] for c in cdb.execute('PRAGMA table_info(%s)' % t).fetchall()]
                        cnt = cdb.execute('SELECT COUNT(*) AS c FROM %s' % t).fetchone()['c']
                        if cnt > 0:
                            ptabs.append((t, cnt, cols))
                    except Exception:
                        pass
                if ptabs:
                    label = src.get('name') or pid
                    head_note = '（该插件未实现数据开放接口，平台直读其独立库，属兼容兜底）' if _src else ''
                    lines = ['外部插件「%s」独立库数据%s（%d 张有数据表）：' % (label, head_note, len(ptabs))]
                    for t, cnt, cols in ptabs:
                        lines.append(_sample_part(cdb, t, cnt, cols, 1))
                    plug_parts.append('\n'.join(lines))
                    plug_files.append(pid)
                cdb.close()
            except Exception as e:
                plug_parts.append('插件「%s」独立库读取失败: %s' % (pid, e))
    extra = '\n\n'.join(plug_parts) if plug_parts else ''
    if plug_files:
        head += '另有 %d 个上传插件的独立库数据已读取（%s）。' % (len(plug_files), '、'.join(plug_files))
    body = '\n\n'.join(parts) if parts else ''
    return head + (('\n\n' + extra) if extra else '') + (('\n\n' + body) if body else '')


def _profile_text(fields, rows, total=None, budget=760):
    """v12.7.14 确定性列画像：把【几百~几千行】压成可分析的统计文本。
    数值列给 合计/均值/最大/最小；低基数列给 top 分布；其余列给取值个数。
    rows 是已取到的样本行（建议 200~300 行），total 是表内真实行数。"""
    if not rows:
        return ''
    n = len(rows)
    out = []
    for f in fields:
        vals = [r.get(f) for r in rows if isinstance(r, dict)]
        nonnull = [v for v in vals if v is not None and str(v).strip() != '']
        if not nonnull:
            continue
        nums = []
        for v in nonnull:
            try:
                nums.append(float(str(v).replace(',', '')))
            except Exception:
                pass
        if len(nums) >= max(6, int(len(nonnull) * 0.8)):
            sm = sum(nums)
            if str(f).lower() == 'id' or str(f).lower().endswith('_id'):
                out.append('%s: 最大%.0f / 最小%.0f（主键/关联ID，不求合计）' % (f, max(nums), min(nums)))
            else:
                out.append('%s: 合计%.2f / 均值%.2f / 最大%.2f / 最小%.2f' % (f, sm, sm / len(nums), max(nums), min(nums)))
            continue
        uniq = {}
        for v in nonnull:
            k = str(v).strip()[:26]
            uniq[k] = uniq.get(k, 0) + 1
        if len(uniq) <= 30 and len(nonnull) >= 8:
            top = sorted(uniq.items(), key=lambda x: -x[1])[:6]
            out.append('%s: %s' % (f, '，'.join('%s=%d' % (k, c) for k, c in top)))
        else:
            out.append('%s: %d 种取值（非空 %d）' % (f, len(uniq), len(nonnull)))
    head = '【确定性统计·基于 %d 行%s】' % (n, ('/全表 %d 行' % total) if total else '')
    return head + '；'.join(out)[:budget]

def _extreme_rows(fields, rows, budget=700):
    """v12.7.14 极值行：给第一个可用数值列的 最大3行 + 最小1行（带编号/名称等关键列），
    让 AI 能回答“金额最大的 5 份合同”“工时最高的需求”这类明细排名问题。"""
    numf = None
    for f in fields:
        vals = [r.get(f) for r in rows if isinstance(r, dict)]
        ok = 0
        for v in vals:
            try:
                float(str(v).replace(',', '')); ok += 1
            except Exception:
                pass
        if vals and ok >= max(4, int(len(vals) * 0.8)) and not str(f).lower().endswith('_id') and str(f).lower() != 'id':
            numf = f
            break
    if not numf:
        return ''
    def key(r):
        try:
            return float(str(r.get(numf)).replace(',', ''))
        except Exception:
            return -1e18
    keep = [f for f in fields if f not in _SENSITIVE_COL][:7]
    if numf not in keep:
        keep.append(numf)
    lines = []
    for label, rs in (('最大', sorted(rows, key=key, reverse=True)[:5]), ('最小', sorted(rows, key=key)[:1])):
        for r in rs:
            lines.append('%s：%s' % (label, __import__('json').dumps(
                {k: r.get(k) for k in keep}, ensure_ascii=False, default=str)))
    return ('【%s 极值行】' % numf) + '\n'.join(lines)[:budget]


def _kw_rows(fields, rows, words, limit=8, budget=900):
    """v12.7.14 明细检索：按问题关键词在行数据里找命中行（编号/客户/名称/负责人等）。"""
    if not words:
        return ''
    hit = []
    for r in rows:
        blob = ' '.join(str(r.get(f, '')) for f in fields if f not in _SENSITIVE_COL)
        if any(w and w in blob for w in words):
            hit.append(r)
        if len(hit) >= limit:
            break
    if not hit:
        return ''
    keep = [f for f in fields if f not in _SENSITIVE_COL][:8]
    return ('【关键词命中 %d 行】' % len(hit)) + '\n'.join(
        __import__('json').dumps({k: r.get(k) for k in keep}, ensure_ascii=False, default=str)
        for r in hit)[:budget]

def _sql_profile(db, t, cols, cnt, budget=900):
    """v12.7.14 全表精确画像：用 SQL GROUP BY / SUM 直接算【整张表】，
    不抽样，几百~几千行都能给出与真实数据一致的合计与分布。"""
    out = []
    types = {}
    try:
        types = {r[1]: (r[2] or '').upper() for r in db.execute('PRAGMA table_info(%s)' % t).fetchall()}
    except Exception:
        pass
    for c in cols:
        if c in _SENSITIVE_COL:
            continue
        try:
            ty = types.get(c, '')
            if any(k in ty for k in ('INT', 'REAL', 'NUM', 'DEC')):
                r = db.execute('SELECT SUM(%s), AVG(%s), MAX(%s), MIN(%s), COUNT(%s) FROM %s'
                               % (c, c, c, c, c, t)).fetchone()
                if r and r[4]:
                    if str(c).lower() == 'id' or str(c).lower().endswith('_id'):
                        out.append('%s: 最大%s / 最小%s（主键/关联ID，不求合计）' % (c, r[2], r[3]))
                    else:
                        out.append('%s: 合计%.2f / 均值%.2f / 最大%.2f / 最小%.2f' % (
                            c, r[0] or 0, r[1] or 0, r[2] or 0, r[3] or 0))
                continue
            nd = db.execute('SELECT COUNT(DISTINCT %s) FROM %s' % (c, t)).fetchone()[0]
            if nd and nd <= 40:
                rs = db.execute('SELECT %s, COUNT(*) n FROM %s GROUP BY %s ORDER BY n DESC LIMIT 8'
                                % (c, t, c)).fetchall()
                txt = '，'.join('%s=%d' % ((x[0] if x[0] not in (None, '') else '（空）'), x[1]) for x in rs)
                out.append('%s: %s' % (c, txt))
            else:
                out.append('%s: %d 种取值' % (c, nd or 0))
        except Exception as e:
            out.append('%s: 统计失败(%s)' % (c, str(e)[:40]))
    return ('【全表精确统计·%d 行】' % cnt) + '；'.join(out)[:budget]

def _sample_part(db, t, cnt, cols, score, words=None):
    """普通表：确定性列画像 + 2 行样例（v12.7.14：取样 200 行做统计，不再只有 2 行）"""
    try:
        lim = 200 if cnt <= 200 else 200
        sample_rows = db.execute('SELECT * FROM %s LIMIT %d' % (t, lim)).fetchall()
        sample = [dict(x) for x in sample_rows]
        cols2 = _safe_sample_cols(cols)
        for srow in sample:
            for k in list(srow):
                if k in _SENSITIVE_COL:
                    srow.pop(k, None)
        prof = _sql_profile(db, t, cols, cnt)
        kw = ''
        _words = words or []
        if _words:
            conds, params = [], []
            for w in _words[:6]:
                for c in cols2[:10]:
                    conds.append('%s LIKE ?' % c)
                    params.append('%%%s%%' % w)
            if conds:
                try:
                    hits = db.execute('SELECT * FROM %s WHERE %s LIMIT 8' % (t, ' OR '.join(conds)), params).fetchall()
                    hd = [{k: v for k, v in dict(x).items() if k not in _SENSITIVE_COL} for x in hits]
                    if hd:
                        kw = '【关键词命中 %d 行】' % len(hd) + __import__('json').dumps(hd, ensure_ascii=False, default=str)[:900]
                except Exception:
                    kw = ''
        brief = __import__('json').dumps(sample[:2], ensure_ascii=False, default=str)[:480]
        tag = '相关' if score > 0 else ''
        return '【表 %s·%s】（%d 行）列: %s\n%s\n%s\n样例数据: %s' % (
            t, tag, cnt, ', '.join(cols2), prof, kw, brief)
    except Exception as e:
        return '【表 %s】读取失败: %s' % (t, e)


def _overdue_report():
    """系统侧确定性预判 WBS 任务超期/到期情况（不依赖小模型推理）：
    规则——计划结束时间 plan_end 早于今天且状态不是“已完成/已取消”= 已逾期（“未开始”也算）；
    plan_end=今天且未完成 = 今日到期；plan_end 在 1~7 天内且未完成 = 7 日内到期。
    返回一段权威摘要文本，直接注入 AI 上下文，AI 只需复述。"""
    try:
        db = _db()
        today = datetime.now().strftime('%Y-%m-%d')
        try:
            from datetime import timedelta
            week_end = (datetime.now() + timedelta(days=7)).strftime('%Y-%m-%d')
        except Exception:
            week_end = today
        rows = db.execute('''
            SELECT t.name AS task, t.plan_end, t.status, t.owner,
                   p.name AS project
            FROM wbs_tasks t LEFT JOIN wbs_projects p ON t.project_id = p.id
            WHERE t.plan_end IS NOT NULL AND t.plan_end != '' AND (t.parent_id > 0 OR t.parent_id IS NULL)
              AND t.approval_status = '已通过'
            ORDER BY t.plan_end
        ''').fetchall()
        done_status = ('已完成', '已取消')
        overdue, due_today, due_week = [], [], []
        for r in rows:
            end = (r['plan_end'] or '').strip()
            if not end:
                continue
            if end < today and r['status'] not in done_status:
                overdue.append(r)
            elif end == today and r['status'] not in done_status:
                due_today.append(r)
            elif today < end <= week_end and r['status'] not in done_status:
                due_week.append(r)
        if not (overdue or due_today or due_week):
            return ''
        def _fmt(items):
            out = []
            for r in items[:12]:
                out.append('%s（项目:%s / 负责人:%s / 计划结束:%s / 状态:%s）' % (
                    r['task'] or '-', r['project'] or '-', r['owner'] or '-', r['plan_end'], r['status']))
            if len(items) > 12:
                out.append('……等共 %d 个' % len(items))
            return '\n'.join(out)
        parts = []
        if overdue:
            parts.append('【已逾期 %d 个】（计划结束时间已过且未完成，含“未开始”）：\n%s' % (len(overdue), _fmt(overdue)))
        if due_today:
            parts.append('【今日到期 %d 个】：\n%s' % (len(due_today), _fmt(due_today)))
        if due_week:
            parts.append('【7 日内到期 %d 个】：\n%s' % (len(due_week), _fmt(due_week)))
        head = 'WBS 任务超期预判（系统按规则计算，请直接引用，不要自行推断）——' \
               '已逾期 = 计划结束时间早于 %s 且状态非“已完成/已取消”（未开始也算逾期）：' % today
        return head + '\n' + '\n\n'.join(parts)
    except Exception as e:
        return ''


_SYSTEM_PROMPT = (
    '你是一名企业业务系统“业务高效运转系统”的数据分析助手。系统包含单点登录、统一权限、'
    'WBS计划管理、需求收集管理（在线云表格）等插件，数据保存在 SQLite 数据库中。\n'
    '用户会提供数据库【有数据】的各表结构、行数与样例数据（空表不提供，不代表不存在）。\n'
    '回答要基于这些真实数据，充分发挥分析能力：汇总统计、趋势分析、问题归因、横向对比、'
    '交叉分析、数据洞察等；若相关数据为空或不足，先明确说明“当前没有相关数据/无法确认”，'
    '然后可以基于常识给出方向性的排查或建议（必须明确标注为推断，不得把推断当事实）。'
    '绝不编造数据库中存在具体数字。\n'
    '关于超期/逾期/到期类问题：若用户输入中包含“WBS 任务超期预判”，直接采用其中的数字与明细'
    '（系统已按“计划结束时间已过且状态非已完成/已取消即为逾期，未开始也算逾期”的规则计算），'
    '不要自行另行推断或漏掉未开始的任务。\n'
    '对话注意：同一会话的历史消息会随本次消息一并提供，请保持上下文连贯，'
    '可引用之前讨论过的内容、结论、指标口径或用户偏好，不要重复询问已回答过的问题。\n'
    '回答用中文，先给结论再给依据；格式可灵活使用小标题、列表、表格、代码块等 Markdown；'
    '在保证准确的前提下充分展开分析，给出可操作的建议，不要过度自我设限。'
    '读取【需求统计管理】内容时：表格正文以〔工作表：名称，行数×列数〕逐表列出；若某个工作表标注（空白表，无数据），表示该工作表已创建但单元格内容为空，请如实告知用户“该工作表内容为空”，不要表述为“不存在”“无法读取”或“未提供内容”。'
)

# v12.5.19：会话历史注入预算（字符数，按最近优先截断，防止本地小模型窗口溢出）
_HISTORY_MAX_CHARS = 5000

# ============================================================
# v12.5.20：分层记忆（长期摘要 + 最近原文窗口）
# 参考业界成熟方案（LangChain SummaryBuffer / Recursive Summarization / MemGPT 分层记忆）：
#   系统提示 + 【会话长期摘要】+ 最近 N 轮原文 + 当前问题
# 旧对话不直接丢弃，超过阈值后在后台异步递归压缩成滚动摘要；
# 编辑/删除消息后摘要自动失效（summary_up_to_id 归零，下次全量重算）。
# ============================================================
_RAW_TRIGGER_CHARS = 6000     # 未压缩原文累计超过此值才触发压缩（短会话不压缩）
_RECENT_KEEP_CHARS = 2500     # 压缩时：最近这么多字符的原文保留不压（保证近期连贯）
_RECENT_RAW_CHARS = 3000      # 注入请求时：从压缩点之后取最近这么多字符原文

_COMPACT_PROMPT = (
    '你是会话记忆压缩助手。给定一份【已有会话摘要】（可能为空）和一段【新发生的对话】，'
    '请输出一份更新后的中文会话长期要点摘要（不超过 500 字）。\n'
    '必须保留：关键事实与数字、用户明确提出的需求/约束/偏好、已达成的结论与决定、'
    '悬而未决的问题或待办。合并重复内容，删除寒暄与过程性废话。'
    '不要编造未出现的信息；不要输出任何解释，只输出摘要正文。'
)


def _compact_conversation(db_path, cid, pid, model, api_url, api_key):
    """后台异步调用：把最旧的未压缩对话递归压缩进会话长期摘要。
    失败静默（下次对话触发时再试）。"""
    import threading

    def _run():
        try:
            conn = sqlite3.connect(db_path, timeout=10)
            conn.row_factory = sqlite3.Row
            conv = conn.execute('SELECT summary, summary_up_to_id FROM ai_conversations WHERE id=?',
                                (cid,)).fetchone()
            if not conv:
                return
            old_summary = conv['summary'] or ''
            upto = conv['summary_up_to_id'] or 0
            # 未压缩原文（压缩点之后）
            rows = conn.execute(
                "SELECT id, role, content FROM ai_messages WHERE conv_id=? AND id>? AND role IN ('user','assistant') ORDER BY id",
                (cid, upto)).fetchall()
            rows = [r for r in rows if (r['content'] or '').strip()]
            total = sum(len(r['content'] or '') for r in rows)
            if total <= _RAW_TRIGGER_CHARS:
                return
            # 从最早开始压，保留最近 _RECENT_KEEP_CHARS 原文不动
            kept = 0
            compact_rows = []
            last_id = upto
            for r in reversed(rows):
                kept += len(r['content'] or '')
                if kept > _RECENT_KEEP_CHARS:
                    break
            for r in rows:
                if kept >= len(r['content'] or ''):
                    kept -= len(r['content'] or '')
                    continue  # 属于保留的尾部原文
                compact_rows.append(r)
                last_id = r['id']
            if not compact_rows:
                return
            # 组织对话片段（user: / assistant:）
            seg = '\n'.join('%s: %s' % (r['role'], (r['content'] or '').strip()) for r in compact_rows)
            user_content = ('【已有会话摘要】\n%s\n\n【新对话片段】\n请据此更新摘要：\n%s'
                            % (old_summary or '（无）', seg))
            new_summary = _chat_request(api_url, api_key, model, [
                {'role': 'system', 'content': _COMPACT_PROMPT},
                {'role': 'user', 'content': user_content},
            ], timeout=120)
            new_summary = (new_summary or '').strip()
            if not new_summary:
                return
            conn.execute('UPDATE ai_conversations SET summary=?, summary_up_to_id=? WHERE id=?',
                         (new_summary, last_id, cid))
            conn.commit()
            conn.close()
        except Exception:
            pass  # 静默：压缩失败不影响主对话

    threading.Thread(target=_run, daemon=True).start()


# ============================================================
# 问答 / 数据分析（流式：支持前端中止）


# 预置统计模板（mini Text-to-SQL）：问题关键词 → 白名单 SQL → 结果文本
_SQL_TEMPLATES = [
    ('逾期', '逾期任务', None, 'overdue'),
    ('超期', '超期任务', None, 'overdue'),
    ('到期', '即将到期', None, 'overdue'),
    ('负责人', '负责人任务负载', None, 'owner_load'),
    ('项目任务量', '各项目任务量', None, 'proj_load'),
    ('任务量', '各项目任务量', None, 'proj_load'),
    ('状态分布', '任务状态分布', None, 'status_dist'),
    ('占比', '任务状态分布', None, 'status_dist'),
    ('平均进度', '平均进度', None, 'avg_progress'),
    ('附件', '附件清单', None, 'attachments'),
    ('审批', '审批状态分布', None, 'approval_dist'),
]


def _template_sql(question, db):
    """命中预置统计模板则执行并返回 (标题, 文本)；未命中返回 None"""
    # 关键词需在问题中出现（长词优先）
    hit = None
    for kw, title, _, action in _SQL_TEMPLATES:
        if kw in question:
            hit = (title, action)
            break
    if not hit:
        return None
    title, action = hit
    if action == 'overdue':
        r = _overdue_report()
        if not r:
            return (title, '当前没有逾期/超期任务')
        return (title, r)
    if action == 'owner_load':
        rows = db.execute('SELECT owner, COUNT(*) c FROM wbs_tasks WHERE owner IS NOT NULL AND owner!=\'\' GROUP BY owner ORDER BY c DESC').fetchall()
        return (title, '\n'.join('%s: %d 条' % (r['owner'], r['c']) for r in rows) or '暂无')
    if action == 'proj_load':
        rows = db.execute('SELECT p.name, COUNT(t.id) c FROM wbs_projects p LEFT JOIN wbs_tasks t ON t.project_id=p.id GROUP BY p.id ORDER BY c DESC').fetchall()
        return (title, '\n'.join('%s: %d 条' % (r['name'], r['c']) for r in rows) or '暂无')
    if action == 'status_dist':
        rows = db.execute('SELECT status, COUNT(*) c FROM wbs_tasks GROUP BY status ORDER BY c DESC').fetchall()
        return (title, '\n'.join('%s: %d 条' % (r['status'] or '（空）', r['c']) for r in rows) or '暂无')
    if action == 'avg_progress':
        r = db.execute('SELECT ROUND(AVG(progress),1) a, COUNT(*) c FROM wbs_tasks').fetchone()
        return (title, '全部 %d 条任务平均进度 %s%%' % (r['c'], r['a']))
    if action == 'attachments':
        rows = db.execute("SELECT name, owner, attachment_path FROM wbs_tasks WHERE attachment_path IS NOT NULL AND attachment_path!='' ORDER BY id").fetchall()
        return (title, '\n'.join('%s（负责人%s，%s）' % (r['name'], r['owner'] or '空', r['attachment_path']) for r in rows) or '当前没有附件')
    if action == 'approval_dist':
        rows = db.execute('SELECT approval_status, COUNT(*) c FROM wbs_projects GROUP BY approval_status').fetchall()
        return (title, '\n'.join('%s: %d 个' % (r['approval_status'] or '（空）', r['c']) for r in rows) or '暂无')


@ai_bp.route('/api/chat', methods=['POST'])
def chat():
    if not _require_platform():
        return _deny()
    d = request.get_json(silent=True) or {}
    pid = d.get('provider_id')
    model = (d.get('model') or '').strip()
    question = (d.get('question') or '').strip()
    cid = d.get('conv_id')
    if not pid:
        return jsonify({'ok': False, 'msg': '请先选择 AI 服务商'})
    if not question:
        return jsonify({'ok': False, 'msg': '请输入问题'})
    db = _db()
    prov = db.execute('SELECT * FROM ai_providers WHERE id=?', (pid,)).fetchone()
    if not prov:
        return jsonify({'ok': False, 'msg': '服务商不存在'})
    if not prov['enabled']:
        return jsonify({'ok': False, 'msg': '该服务商未启用，请先在配置中启用'})
    if not model:
        model = prov['default_model'] or ''
    if not model:
        return jsonify({'ok': False, 'msg': '请选择模型（可先点击“检测”拉取模型列表）'})


    # 会话归属：指定会话必须存在；未指定则自动新建（在请求上下文内完成，保证中断也不丢用户消息）
    if cid:
        conv = db.execute('SELECT * FROM ai_conversations WHERE id=?', (cid,)).fetchone()
        if not conv:
            return jsonify({'ok': False, 'msg': '会话不存在或已删除'})
    else:
        now = _now_str()
        cur = db.execute('INSERT INTO ai_conversations (title, created_at, updated_at) VALUES (?,?,?)',
                         ('新会话', now, now))
        cid = cur.lastrowid
        db.commit()

    # 编辑重发：复用已更新的用户消息（不重复插入）；否则插入新用户消息
    reuse_msg_id = d.get('user_msg_id')
    if reuse_msg_id:
        um = db.execute('SELECT * FROM ai_messages WHERE id=? AND conv_id=?', (reuse_msg_id, cid)).fetchone()
        if not um or um['role'] != 'user':
            return jsonify({'ok': False, 'msg': '待重发消息不存在'})
        user_msg_id = um['id']
    else:
        now = _now_str()
        cur = db.execute('INSERT INTO ai_messages (conv_id, role, content, created_at) VALUES (?,?,?,?)',
                         (cid, 'user', question, now))
        user_msg_id = cur.lastrowid
        _refresh_conv_stats(db, cid)
        db.commit()

    # v12.7.10.13：连通性预检（1 秒端口探测）。服务没起时立即返回中文诊断，
    # 不再先把全部库表上下文拼一遍再抛原始异常（用户当时只看到"已中止生成"）
    _api_url = prov['api_url']
    _diag = _llm_preflight(_api_url)
    if _diag:
        _mid = _save_ai_message(current_app.config['DATABASE'], cid, _diag, pid, model, question,
                                aborted=True, err=_diag)
        _body = ('event: meta\ndata: %s\n\n'
                 'data: %s\n\n'
                 'event: end\ndata: %s\n\n') % (
            json.dumps({'conv_id': cid, 'user_msg_id': user_msg_id}, ensure_ascii=False),
            json.dumps({'delta': _diag}, ensure_ascii=False),
            json.dumps({'ok': False, 'answer': _diag, 'msg_id': _mid, 'err': _diag}, ensure_ascii=False))
        return Response(_body, mimetype='text/event-stream',
                        headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})

    # —— 以下闭包变量全部在请求上下文内准备；generator 运行时上下文已弹出，只能读闭包 ——
    db_path = current_app.config['DATABASE']
    context = _collect_db_context(question)
    # 预置统计模板（mini Text-to-SQL）：命中高频问题（逾期/分布/负载等）则注入系统精确计算结果
    _tpl = _template_sql(question, db)
    if _tpl:
        context += '\n\n【预置统计查询结果（系统SQL精确计算）】\n标题：%s\n%s' % (_tpl[0], _tpl[1])
    overdue = _overdue_report()
    extra = '\n\n%s' % overdue if overdue else ''
    # v12.5.19/20：注入会话历史（分层记忆）。
    # 分层 = 系统提示 + 【会话长期摘要】+ 压缩点之后的最近原文 + 当前问题。
    # 超过预算的旧对话已由后台异步压缩为滚动摘要（见 _compact_conversation），
    # 这里只取 summary_up_to_id 之后的原文，再按最近优先截断到 _RECENT_RAW_CHARS。
    conv_row = db.execute('SELECT summary, summary_up_to_id FROM ai_conversations WHERE id=?', (cid,)).fetchone()
    long_summary = (conv_row['summary'] or '') if conv_row else ''
    upto_id = (conv_row['summary_up_to_id'] or 0) if conv_row else 0
    hist_msgs, used = [], 0
    hist_rows = db.execute(
        "SELECT role, content FROM ai_messages WHERE conv_id=? AND role IN ('user','assistant') AND id>? ORDER BY id DESC",
        (cid, upto_id)).fetchall()
    for hr in hist_rows:
        hc = (hr['content'] or '').strip()
        if not hc:
            continue
        if used + len(hc) > _RECENT_RAW_CHARS:
            break
        hist_msgs.append({'role': hr['role'], 'content': hc})
        used += len(hc) + 1
    hist_msgs.reverse()
    req_messages = [{'role': 'system', 'content': _SYSTEM_PROMPT}]
    if long_summary:
        req_messages.append({'role': 'system',
                             'content': '【本会话长期要点摘要（早期对话已压缩为以下摘要，可据此理解上下文）】\n' + long_summary})
    req_messages += hist_msgs + [
        {'role': 'user', 'content': '以下是数据库当前（有数据的）结构、行数与样例数据：\n%s%s\n\n请回答/分析：%s' % (context, extra, question)},
    ]
    api_url, api_key = prov['api_url'], prov['api_key']

    def gen():
        collected = ''
        done = False
        assistant_id = None
        err_msg = ''
        try:
            yield 'event: meta\ndata: %s\n\n' % json.dumps({'conv_id': cid, 'user_msg_id': user_msg_id})
            for chunk in _stream_chat(api_url, api_key, model, req_messages):
                collected += chunk
                yield 'data: %s\n\n' % json.dumps({'delta': chunk})
            done = True
        except GeneratorExit:
            # 客户端断开（点击中止）：保留已生成部分
            pass
        except Exception as e:
            err_msg = _diagnose_llm_error(e, api_url)
            if not collected:
                collected = err_msg
                yield 'data: %s\n\n' % json.dumps({'delta': err_msg}, ensure_ascii=False)
        finally:
            try:
                assistant_id = _save_ai_message(db_path, cid, collected, pid, model, question,
                                                aborted=not done, err=err_msg)
                # 正常完成后：后台异步检查并压缩超长历史（分层记忆）
                if done and assistant_id:
                    _compact_conversation(db_path, cid, pid, model, api_url, api_key)
            except Exception:
                pass
        yield 'event: end\ndata: %s\n\n' % json.dumps({'ok': done, 'answer': collected, 'msg_id': assistant_id, 'err': err_msg}, ensure_ascii=False)

    return Response(gen(), mimetype='text/event-stream',
                    headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})


def _last_user_msg_id(cid):
    """返回会话中最近一条用户消息 id（供前端绑定编辑/删除）"""
    db = _db()
    row = db.execute('SELECT id FROM ai_messages WHERE conv_id=? AND role=? ORDER BY id DESC LIMIT 1',
                     (cid, 'user')).fetchone()
    return row['id'] if row else None


def _refresh_conv_stats(db, cid):
    """重算会话标题 / 消息数 / 上下文字符数（删除、编辑后保持列表准确）"""
    now = _now_str()
    title_row = db.execute('SELECT content FROM ai_messages WHERE conv_id=? AND role=? ORDER BY id LIMIT 1',
                           (cid, 'user')).fetchone()
    new_title = '新会话'
    if title_row:
        t = (title_row['content'] or '').strip().replace('\n', ' ')
        new_title = t[:24] if t else '新会话'
    cc = db.execute('SELECT COALESCE(SUM(LENGTH(content)),0) AS c FROM ai_messages WHERE conv_id=?', (cid,)).fetchone()['c']
    mc = db.execute('SELECT COUNT(*) AS c FROM ai_messages WHERE conv_id=?', (cid,)).fetchone()['c']
    db.execute('UPDATE ai_conversations SET title=?, updated_at=?, msg_count=?, char_count=? WHERE id=?',
               (new_title, now, mc, cc, cid))


def _save_ai_message(db_path, cid, content, pid, model, question, aborted=False, err='', scope=''):
    """保存 AI 回复（正常完成=完整内容；中止=已生成部分；失败=具体错误原因），返回消息 id。
    独立直连数据库（流式 generator 阶段请求上下文已弹出，不能依赖 flask g）"""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        if not content or not content.strip():
            if err:
                content = '【模型调用失败】%s' % err[:300]
            else:
                content = '【已中止】未生成内容' if aborted else '（空回复）'
        cur = conn.execute('INSERT INTO ai_messages (conv_id, role, content, created_at, aborted) VALUES (?,?,?,?,?)',
                           (cid, 'assistant', content, _now_str(), 1 if aborted else 0))
        _refresh_conv_stats(conn, cid)
        try:
            conn.execute('INSERT INTO ai_chat_logs (provider_id, model, question, answer) VALUES (?,?,?,?)',
                         (pid, model, question, content))
        except Exception:
            pass
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


# ============================================================
# 消息管理：删除气泡 / 编辑并重发（防上下文污染）
# ============================================================
@ai_bp.route('/api/messages/<int:mid>/delete', methods=['POST'])
def delete_message(mid):
    if not _require_platform():
        return _deny()
    db = _db()
    row = db.execute('SELECT conv_id FROM ai_messages WHERE id=?', (mid,)).fetchone()
    if not row:
        return jsonify({'ok': False, 'msg': '消息不存在'})
    db.execute('DELETE FROM ai_messages WHERE id=?', (mid,))
    # 分层记忆：历史变动后摘要可能失真，清空摘要下次全量重算
    db.execute('UPDATE ai_conversations SET summary=\'\', summary_up_to_id=0 WHERE id=?', (row['conv_id'],))
    _refresh_conv_stats(db, row['conv_id'])
    db.commit()
    return jsonify({'ok': True, 'conv_id': row['conv_id']})


@ai_bp.route('/api/messages/<int:mid>/edit', methods=['POST'])
def edit_message(mid):
    if not _require_platform():
        return _deny()
    d = request.get_json(silent=True) or {}
    content = (d.get('content') or '').strip()
    if not content:
        return jsonify({'ok': False, 'msg': '内容不能为空'})
    db = _db()
    row = db.execute('SELECT conv_id FROM ai_messages WHERE id=?', (mid,)).fetchone()
    if not row:
        return jsonify({'ok': False, 'msg': '消息不存在'})
    db.execute('UPDATE ai_messages SET content=? WHERE id=?', (content, mid))
    # 分层记忆：历史变动后摘要可能失真，清空摘要下次全量重算
    db.execute('UPDATE ai_conversations SET summary=\'\', summary_up_to_id=0 WHERE id=?', (row['conv_id'],))
    if d.get('reask'):
        # 编辑并重发：删除本条之后的全部旧回复，避免上下文污染
        db.execute('DELETE FROM ai_messages WHERE conv_id=? AND id>?', (row['conv_id'], mid))
    _refresh_conv_stats(db, row['conv_id'])
    db.commit()
    return jsonify({'ok': True, 'conv_id': row['conv_id']})


def _chat_request(api_url, api_key, model, messages, timeout=120):
    url = api_url.rstrip('/') + '/chat/completions'
    body = json.dumps({
        'model': model,
        'messages': messages,
        'temperature': 0.2,
        'stream': False,
    }).encode('utf-8')
    headers = {'Content-Type': 'application/json'}
    if api_key:
        headers['Authorization'] = 'Bearer ' + api_key
    req = urllib.request.Request(url, data=body, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode('utf-8', errors='replace'))
        try:
            return data['choices'][0]['message']['content']
        except Exception:
            return '【返回格式异常】服务端返回：%s' % json.dumps(data, ensure_ascii=False)[:500]
    except urllib.error.HTTPError as e:
        return '【调用失败】HTTP %d：%s' % (e.code, e.read().decode('utf-8', errors='replace')[:400])
    except Exception as e:
        return '【调用失败】%s' % e


def _diagnose_llm_error(err, target=''):
    """把底层网络/HTTP 异常翻译成中文可操作诊断（v12.7.10.13）。

    幂等：已经是本函数生成的中文诊断时原样返回，避免外层再套"调用失败："。
    """
    s = str(err or '')
    if not s:
        return 'AI 服务调用失败（未知原因）。'
    if s.startswith('AI 服务'):
        return s
    low = s.lower()
    if '10061' in s or 'actively refused' in low or 'connection refused' in low:
        return ('AI 服务未启动或地址/端口写错（%s）：本机没有服务在该端口监听。'
                '请先启动 LM Studio / Ollama 的本地服务，并确认 API 地址（通常形如 http://127.0.0.1:1234/v1）。' % target)
    if '10060' in s or 'timed out' in low or 'timeout' in low:
        return ('AI 服务连接超时（%s）：模型可能正在加载或显存不足，请稍后重试。' % target)
    if '10054' in s or 'reset' in low:
        return ('AI 服务连接被重置（%s）：服务进程可能崩溃或显存不足（OOM），请重启模型服务。' % target)
    m = re.search(r'HTTP Error (\d{3})', s)
    if m:
        code = m.group(1)
        if code == '401':
            return 'AI 服务认证失败（401）：请检查 API Key 是否填写正确。'
        if code == '404':
            return 'AI 服务地址或模型名不存在（404）：请检查 API 地址是否以 /v1 结尾、模型名是否与服务端一致。'
        if code == '429':
            return 'AI 服务限流（429）：请求过于频繁，请稍后重试。'
        if code.startswith('5'):
            return 'AI 服务内部错误（%s）：服务端异常，请查看模型服务日志。' % code
    if 'getaddrinfo' in low or 'name or service not known' in low:
        return 'AI 服务域名解析失败（%s）：请检查地址中的主机名，或改用 127.0.0.1。' % target
    if 'ssl' in low or 'certificate' in low:
        return 'AI 服务 SSL/证书错误（%s）：请检查 https 地址与证书是否匹配，或改用 http。' % target
    return 'AI 服务调用失败（%s）：%s' % (target, s[:200])


def _llm_preflight(api_url):
    """1 秒端口探测（v12.7.10.13）：能连上返回 None，连不上返回中文诊断。

    目的：服务没启动时 0 秒级给出原因，而不是先把全部库表上下文拼完才抛原始异常。
    """
    try:
        from urllib.parse import urlparse
        import socket
        raw = api_url if '://' in (api_url or '') else 'http://' + (api_url or '127.0.0.1')
        u = urlparse(raw)
        host = u.hostname or '127.0.0.1'
        port = u.port or (443 if u.scheme == 'https' else 80)
        target = '%s:%s' % (host, port)
    except Exception:
        return None
    try:
        socket.create_connection((host, port), timeout=1.0).close()
        return None
    except Exception as e:
        return _diagnose_llm_error(e, target)


def _stream_chat(api_url, api_key, model, messages, timeout=180):
    """流式调用 OpenAI 兼容接口（stream=True），逐块 yield 增量内容。
    - 正常：每块内容逐段返回
    - HTTP 错误：yield 一条【调用失败】文本
    - 连接中断（前端中止 / 网络断开）：向上抛异常，由调用方保存已生成部分
    """
    url = api_url.rstrip('/') + '/chat/completions'
    body = json.dumps({
        'model': model,
        'messages': messages,
        'temperature': 0.2,
        'stream': True,
    }).encode('utf-8')
    headers = {'Content-Type': 'application/json'}
    if api_key:
        headers['Authorization'] = 'Bearer ' + api_key
    req = urllib.request.Request(url, data=body, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            for raw in resp:
                line = raw.decode('utf-8', errors='replace').strip()
                if not line or not line.startswith('data:'):
                    continue
                payload = line[len('data:'):].strip()
                if not payload or payload == '[DONE]':
                    continue
                try:
                    obj = json.loads(payload)
                    choices = obj.get('choices') or []
                    if not choices:
                        continue
                    delta = choices[0].get('delta') or {}
                    content = delta.get('content')
                    if content:
                        yield content
                except Exception:
                    continue
    except urllib.error.HTTPError as e:
        yield '【调用失败】HTTP %d：%s' % (e.code, e.read().decode('utf-8', errors='replace')[:400])
    except Exception:
        raise
