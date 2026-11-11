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


_UNIVER_STATS = {}   # v12.7.10.32：记录云表格规模，供概览口径使用（工作簿数/有内容数/合计行数）


def _univer_scale(db):
    """v12.7.10.32：按库内真实快照统计云表格规模（工作簿数 / 有内容数 / 数据行数 / 工作表数）。
    与"本次是否展开"无关，保证概览口径永远真实；独立函数避免多请求共用全局变量串值。"""
    out = {'books': 0, 'with_data': 0, 'rows': 0, 'sheets': 0}
    try:
        rooms = db.execute('SELECT state FROM univer_rooms WHERE deleted = 0').fetchall()
    except Exception:
        return out
    out['books'] = len(rooms)
    for r in rooms:
        try:
            snap = __import__('json').loads(r['state']) if r['state'] else None
        except Exception:
            snap = None
        rows, sh = 0, 0
        sheets = (snap or {}).get('sheets')
        if isinstance(sheets, dict):
            for _sk, sd in sheets.items():
                cd = (sd or {}).get('cellData') if isinstance(sd, dict) else None
                if not isinstance(cd, dict):
                    continue
                mr = -1
                for rk, rm in cd.items():
                    try:
                        rr = int(rk)
                    except Exception:
                        continue
                    if isinstance(rm, dict) and rm:
                        mr = max(mr, rr)
                if mr >= 0:
                    rows += mr
                    sh += 1
        if rows > 0:
            out['with_data'] += 1
            out['rows'] += rows
            out['sheets'] += sh
    return out


def _univer_summary(db, max_chars=9000, words=None, expand_names=None):
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
    _U_WITH, _U_ROWS, _U_SHEETS = 0, 0, 0
    words = words or []
    # v12.7.10.30：不限制模式（max_chars=None）下每张表固定 1400，统计块不被压缩；
    # 总长交给"分块编译"消化，不再靠压缩房间内容来省篇幅。
    if max_chars is None:
        per = 1400
    else:
        per = min(1400, max(700, max_chars // max(1, total)))
    parts = []
    for r in rooms:
        _rname = r['name'] or '(未命名)'
        # v12.7.10.30：expand_names 给定时，未被问题命中的房间只输出一行（行数+未展开说明），
        # 数据仍在（行数准确），真正问到哪张表时才完整展开统计
        if expand_names is not None and _rname not in expand_names:
            # v12.7.10.32：即使不展开，也解析出该表规模，保证概览里的"云表格合计行数"真实
            try:
                _snap0 = _json.loads(r['state']) if r['state'] else None
                _r0, _s0 = 0, 0
                for _sk0, _s0d in ((_snap0 or {}).get('sheets') or {}).items():
                    _cd0 = (_s0d or {}).get('cellData') if isinstance(_s0d, dict) else None
                    if not isinstance(_cd0, dict):
                        continue
                    _mr0 = -1
                    for _rk0, _rm0 in _cd0.items():
                        try:
                            _rr0 = int(_rk0)
                        except Exception:
                            continue
                        if isinstance(_rm0, dict) and _rm0:
                            _mr0 = max(_mr0, _rr0)
                    if _mr0 >= 0:
                        _r0 += _mr0
                        _s0 += 1
                if _r0 > 0:
                    _U_WITH += 1
                    _U_ROWS += _r0
                    _U_SHEETS += _s0
            except Exception:
                pass
            parts.append((_rname, '表「%s」（房间#%s，v%s，%s更新·%s）本次未展开统计（表名未出现在问题中）；'
                                 '如需分析该表请点名提问，平台会完整统计。'
                          % (_rname, r['id'], r['version'], (r['updated_at'] or '')[:16], r['updated_by'] or '?')))
            continue
        sheet_txts = []
        _has_row = False
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
                # v12.7.10.28：原上限 3000 行，超限时只在标题里写"基于 3000 行/全表 3500 行"，
                # 模型极易把 3000 行的合计当成全表合计。现在上限提到 10000（本地 JSON 解析，
                # 无网络开销），超限时和插件表一样输出醒目 ⚠ 覆盖范围。
                trunc_note = ''
                if maxrow > _UNIVER_MAX_ROWS:
                    trunc_note = ('⚠ 该表共 %d 行，平台单表解析上限 %d 行，本次统计仅覆盖前 %d 行；'
                                  '超出部分未纳入，全表合计请勿直接引用。' % (maxrow, _UNIVER_MAX_ROWS, _UNIVER_MAX_ROWS))
                for rr in range(1, min(maxrow, _UNIVER_MAX_ROWS) + 1):
                    rd = {}
                    for c in range(maxcol + 1):
                        rd[names[c]] = grid.get(rr, {}).get(c, '')
                    if any(str(v).strip() for v in rd.values()):
                        rows.append(rd)
                prof = _profile_text(names, rows, total=maxrow, budget=1100)
                extra = '\n'.join(x for x in (_kw_rows(names, rows, words),
                                              _extreme_rows(names, rows)) if x)
                sample = _json.dumps(rows[:3], ensure_ascii=False, default=str)[:480]
                stxt = '〔工作表：%s，共 %d 行 x %d 列〕表头: %s\n' % (
                    sname, maxrow, maxcol + 1, ' | '.join(names))
                if trunc_note:
                    stxt += trunc_note + '\n'
                stxt += prof
                if extra:
                    stxt += '\n' + extra
                stxt += '\n样例行(前3行): ' + sample
                sheet_txts.append(stxt)
                _has_row = True
                _U_ROWS += maxrow
                _U_SHEETS += 1
        head = '表「%s」（房间#%s，v%s，%s更新·%s，工作表%d 个）' % (
            r['name'] or '(未命名)', r['id'], r['version'],
            (r['updated_at'] or '')[:16], r['updated_by'] or '?',
            len(sheet_txts))
        if not sheet_txts:
            txt = head + '\n  （空白表，无数据）'
        else:
            txt = head + '\n' + '\n'.join(sheet_txts)
            if len(txt) > per:
                # v12.7.10.28：只截样例/极值明细，统计块在头部一定保留，避免模型误判"统计也不完整"
                txt = txt[:per] + '\n…（本表样例/极值明细因篇幅截断；上方确定性统计按整表计算，可直接引用）'
        if _has_row:
            _U_WITH += 1
        parts.append((r['name'] or '(未命名)', txt))
    global _UNIVER_STATS
    _UNIVER_STATS = {'books': total, 'with_data': _U_WITH, 'rows': _U_ROWS, 'sheets': _U_SHEETS}
    # v12.7.10.29：总长超预算时按新→旧顺序保留，并明确列出本次未展开的表名，
    # 避免整块需求统计被上层降级成一行概览、所有表的统计全部丢失
    kept, _u, _rest = [], 0, []
    for _nm, _txt in parts:
        if max_chars is not None and _u + len(_txt) > max_chars and kept:
            _rest.append(_nm)
            continue
        kept.append(_txt)
        _u += len(_txt) + 2
    out = '【需求统计管理】共 %d 张（按新建先后倒序，以下为各表内容）：\n\n%s' % (total, '\n\n'.join(kept))
    if _rest:
        out += ('\n\n【其余 %d 张需求表本次未展开】%s —— 这些表都有数据；'
                '请指明表名单独提问，平台会输出该表完整统计。' % (len(_rest), '、'.join(_rest)))
    return out



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
        task_txt = '【表 wbs_tasks·相关】（按问题命中 %d 条，仅列前 %d 条；全表 %d 条）列: %s\n%s' % (
            len(tasks), len(tasks[:max_rows_task]),
            db.execute('SELECT COUNT(*) AS c FROM wbs_tasks').fetchone()['c'],
            ', '.join(_safe_sample_cols([c[1] for c in db.execute('PRAGMA table_info(wbs_tasks)').fetchall()])),
            '\n'.join(_row_line(r) for r in tasks[:max_rows_task]))
        # v12.7.10.27：命中具体项目时，该项目的【整项目】确定性统计（不依赖抽样行）。
        # 之前只注入前 15 条任务行，问「这个项目进展/有哪些阻塞任务」会以 15 条代表 205 条，
        # 分布和阻塞清单都可能失真。这里用 SQL 对命中项目做全量聚合 + 阻塞清单。
        if pids:
            stat_blocks = []
            for pid in pids[:2]:
                prow = db.execute('SELECT name FROM wbs_projects WHERE id=?', (pid,)).fetchone()
                pcnt = db.execute('SELECT COUNT(*) c FROM wbs_tasks WHERE project_id=?', (pid,)).fetchone()['c']
                dist = db.execute('SELECT status, COUNT(*) n FROM wbs_tasks WHERE project_id=? '
                                  'GROUP BY status ORDER BY n DESC', (pid,)).fetchall()
                avgp = db.execute('SELECT AVG(progress) p FROM wbs_tasks WHERE project_id=?', (pid,)).fetchone()['p']
                blk_n = db.execute("SELECT COUNT(*) c FROM wbs_tasks WHERE project_id=? AND status='阻塞'",
                                   (pid,)).fetchone()['c']
                blk = db.execute("SELECT name, plan_end, owner FROM wbs_tasks WHERE project_id=? AND status='阻塞' "
                                 "ORDER BY plan_end LIMIT 10", (pid,)).fetchall()
                line = '项目「%s」(#%s)：任务 %d 条；状态 %s；平均进度 %.1f%%；阻塞 %d 个' % (
                    (prow['name'] if prow else ''), pid, pcnt,
                    '，'.join('%s=%d' % (x['status'] or '（空）', x['n']) for x in dist),
                    (avgp or 0), blk_n)
                if blk:
                    line += '；阻塞任务（按计划结束先后，前 %d 个）：%s' % (
                        len(blk), '；'.join('%s(%s/%s)' % (b['name'], b['owner'], b['plan_end']) for b in blk))
                    if blk_n > len(blk):
                        line += ' ……等共 %d 个' % blk_n
                stat_blocks.append(line)
            if stat_blocks:
                task_txt += '\n【命中项目·整项目确定性统计（SQL 全量计算，可直接引用）】\n' + '\n'.join(stat_blocks)
    return proj_txt, task_txt


def _row_line(row, max_chars=320):
    """一行记录转紧凑 JSON 文本（截断防上下文爆炸）"""
    d = {k: v for k, v in dict(row).items() if k not in _SENSITIVE_COL}
    t = __import__('json').dumps(d, ensure_ascii=False, default=str)
    return t[:max_chars]


def _collect_db_context(question, max_chars=None):   # v12.7.10.30：默认不限制，全量装载后由分块编译消化
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
    skipped = []
    total_active = len(active)
    related = sum(1 for x in scored if x[3] > 0)
    for idx, (t, cnt, cols, score) in enumerate(scored):
        if t == 'univer_rooms':
            # v12.7.10.30：全量模式下按问题命中程度决定展开哪些表（未命中的只给一行，行数仍真实）；
            # 若问题没有点名任何表且属于泛问（"各表多少条""总览"），则全部展开。
            _expand = None
            try:
                _rooms = db.execute('SELECT name FROM univer_rooms WHERE deleted = 0').fetchall()
                _names = [x['name'] or '' for x in _rooms]
                _qn = (question or '').lower()
                _hit_names = set()
                for _n in _names:
                    # v12.7.10.31：改用 4 字窗口，避免"需求/模块"这类 2 字泛词把一堆表误判成"命中"
                    if len(_n) >= 4 and any(_n[_i:_i + 4].lower() in _qn for _i in range(len(_n) - 3)):
                        _hit_names.add(_n)
                _generic = any(w in (question or '') for w in ('总览', '所有', '全部', '各表', '各模块', '整体', '全局'))  # v12.7.10.30：'一共/多少条'只问数量，不再当总览
                _req_like = any(w in (question or '') for w in ('需求', '收集', '子表', '清单', '采集', '表格'))
                _explicit_other = any(w in (question or '') for w in
                                      ('合同', '物料', '库存', '项目', '任务', '逾期', 'WBS', 'wbs', '供应商', '客户'))
                if _hit_names and not _generic:
                    _expand = _hit_names
                elif _explicit_other and not _req_like:
                    _expand = set()           # v12.7.10.31：问的明确是别的模块（合同/物料/项目…）→ 需求表只列行数
                else:
                    _expand = None            # 其余（含"分析一下数据"这类模糊提问）→ 全部展开，绝不为省篇幅漏表
            except Exception:
                _expand = None
            part = _univer_summary(db, max_chars=None, words=sel, expand_names=_expand)
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
            _generic_q = any(w in (question or '') for w in ('全部', '所有', '各表', '总览', '一共', '整体', '汇总', '多少条'))
            _any_hit = any(x[3] > 0 for x in scored)
            # v12.7.10.31：只有"问题确实命中了某些表"时，才把没命中的表压成概览；
            # 一个问题都没命中（如"分析一下数据"）说明是模糊提问，此时全部展开，避免只剩个别模块。
            if max_chars is None and score <= 0 and _any_hit and not _generic_q:
                # v12.7.10.30：与本问题无关的表只给一行概览（行数+核心合计），不丢数据、也不占篇幅
                part = _brief_line(db, t, cnt, cols)
                tag = ''
            else:
                part = _sample_part(db, t, cnt, cols, score, words)
                tag = '相关' if score > 0 else ''
        if max_chars is not None and used + len(part) > max_chars:
            # v12.7.10.29：装不下不再 break 静默丢弃——先降级为极简概览（一行统计）；
            # 概览也放不下就登记进"未展开清单"，让模型知道这些数据存在、各多少行。
            brief = _brief_line(db, t, cnt, cols)
            if used + len(brief) <= max_chars:
                parts.append(brief)
                used += len(brief)
                skipped.append((t, cnt, '仅概览'))
            else:
                skipped.append((t, cnt, '未展开'))
            continue
        parts.append(part)
        used += len(part)
    # v12.7.10.32：概览改为"按业务模块汇总"，不再把系统的房间登记行数当成数据量。
    # 原写法"共 5 张业务表…univer_rooms(6行)…总行数 1024"会让模型误判需求统计没有数据。
    _sys_tables = {'univer_rooms', 'plugin_tables', 'wbs_recycle', 'schema_version', 'ui_prefs'}
    _cnt_map = {x[0]: x[1] for x in active}
    _mods = []
    _u = _univer_scale(db)
    if _u.get('books'):
        _mods.append('需求统计管理（云表格）：%d 个工作簿，其中 %d 个有内容，合计 %d 行数据、%d 个工作表'
                     % (_u.get('books', 0), _u.get('with_data', 0), _u.get('rows', 0), _u.get('sheets', 0)))
    if _cnt_map.get('wbs_tasks') or _cnt_map.get('wbs_projects'):
        _mods.append('计划管理（WBS）：%d 个项目、%d 条任务'
                     % (_cnt_map.get('wbs_projects', 0), _cnt_map.get('wbs_tasks', 0)))
    head = '业务数据总览（本次实际读取到的数据）：' + ('；'.join(_mods) if _mods else '暂无业务数据') + '。'
    _other = [x for x in scored if x[0] not in _sys_tables]
    _other_l = [x for x in _other if x[0] not in ('wbs_projects', 'wbs_tasks')]
    if _other_l:
        head += ' 其他业务表：%s。' % '、'.join('%s(%d行)' % (x[0], x[1]) for x in _other_l[:8])
    head += (' 说明：univer_rooms 等系统表的行数只是"工作簿/插件/回收站的登记记录"，'
             '不代表业务数据量；回答"数据量/总量"时必须按上面的模块口径分别说明（云表格行数 + 任务条数），'
             '不得用系统表的行数代替。系统表（插件登记、回收站、用户、日志等）不参与业务分析，已略去。'
             ' 外部插件数据见各插件区块。')
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
            # v12.7.10.27：插件被停用后，其独立库数据不再注入 AI（页面已不可访问，
            # 继续参与统计会污染结论）；这里显式告知用户，而不是静默丢弃。
            if not src.get('enabled', True):
                plug_parts.append('插件「%s」当前已停用，其数据未纳入本次分析'
                                  '（如需分析请先在插件管理中启用该插件）。' % name_p)
                continue
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
                # v12.7.10.31：插件没在 datasets 里报 rows（或报 0）时，不能就此判定"无数据"而跳过；
                # 先标记为未知(-1)，实际读取后以读到的行数/接口返回的 total 为准。
                usable.append((d, t, cnt if cnt > 0 else -1))
            if not usable:
                plug_parts.append('插件「%s」开放接口已接通，但当前没有数据表有数据。' % name_p)
                plug_files.append(_pid)
                continue
            lines = ['外部插件「%s」开放数据（OpenAPI v1 接口，%d 张有数据表）：' % (name_p, len(usable))]
            for d, t, cnt in usable:
                # v12.7.10.27：插件表与当前问题的相关性判断。与问题无关的表只给
                # 列画像概要（不展开极值行/样例），避免上百行无关明细把上下文预算吃满。
                _hay = ' '.join([t, str(name_p or ''), str(d.get('label') or ''), str(d.get('description') or '')] +
                                [str(x.get('name') or '') for x in (d.get('fields') or []) if isinstance(x, dict)]).lower()
                _hit = any(str(w).lower() in _hay for w in list(sel or []) + list(words or []) if w)
                # v12.7.10.29：问题原文直接出现插件/表的中文标签时也算相关。
                # 修复"问金额最大的合同，合同表却被判无关、只给统计不给 Top5 明细"的漏判。
                if not _hit:
                    _q = (question or '').lower()
                    for _cand in (name_p, d.get('label'), d.get('description'), d.get('name')):
                        _c = str(_cand or '')
                        if _c and any(_c[_i:_i + 2].lower() in _q for _i in range(max(1, len(_c) - 1))):
                            _hit = True
                            break
                _rows_all, _off, _total = [], 0, None
                while _off < 200000:   # v12.7.10.30：取消插件单表 3000 行限制，分页读全表
                    _g = _ps.plugin_openapi_rows(_pid, t, limit=1000, offset=_off) or {}
                    _rs = _g.get('rows') or []
                    if _total is None and _g.get('total') is not None:
                        try:
                            _total = int(_g.get('total'))
                        except Exception:
                            _total = None
                    if not _rs:
                        break
                    _rows_all.extend(_rs)
                    # v12.7.10.31：按"实际返回条数"推进游标。原先固定 +1000，遇到插件
                    # 每页上限小于 1000（如只给 100 行）时会每次跳过 900 行，造成静默丢数据。
                    _off += len(_rs)
                    if _total is not None:
                        if _off >= _total:
                            break
                    elif len(_rs) < 1000:
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
                _total_n = cnt if cnt > 0 else (int(_total) if _total else len(safe_rows))
                prof = _profile_text(cols, safe_rows, total=_total_n,
                                     budget=1000 if _hit else 420)
                if _hit:
                    extra = '\n'.join(x for x in (_kw_rows(cols, safe_rows, sel),
                                                  _extreme_rows(cols, safe_rows)) if x)
                    sample_txt = __import__('json').dumps(
                        safe_rows[:2], ensure_ascii=False, default=str)[:480]
                else:
                    extra = ''
                    sample_txt = ('（该表与当前问题未直接相关，仅保留列画像概要；'
                                  '如需明细请提出具体的客户/编号/状态等条件）')
                # v12.7.10.27：单表最多读 3000 行；超过时必须显式标注覆盖范围，
                # 否则 >3000 行的表会被静默当全量统计，模型据此报出的合计/分布是错的。
                _trunc = ''
                if _total_n > len(safe_rows):
                    _trunc = ('⚠ 该表共 %d 行，本次实际读取 %d 行（插件接口分页未取完）；'
                              '超出部分未纳入，全表合计请勿直接引用\n' % (_total_n, len(safe_rows)))
                if _total_n <= 0 or not safe_rows:
                    lines.append('【表 %s】当前没有数据（0 行）：插件接口连通但该表为空，'
                                 '或是插件独立库未随本次备份/同步带过来；本次不参与分析，'
                                 '需要时请在插件页面确认数据是否存在。' % t)
                    continue
                lines.append('【表 %s·%s】（%d 行）列: %s\n%s%s\n%s\n样例数据: %s' % (
                    t, '相关' if _hit else '概要', _total_n, ', '.join(cols), _trunc, prof,
                    (extra + '\n') if extra else '', sample_txt))
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
            if src and not src.get('enabled', True):
                plug_parts.append('插件「%s」当前已停用，其数据未纳入本次分析。' % (src.get('name') or pid))
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
    # v12.7.10.30：插件数据不再截断（原先 9000 字符上限会丢掉后面插件的明细）
    extra = '\n\n'.join(plug_parts) if plug_parts else ''
    if plug_files:
        head += '另有 %d 个外部插件的数据块（%s），是否有数据以各插件区块的标注为准。' % (len(plug_files), '、'.join(plug_files))
    body = '\n\n'.join(parts) if parts else ''
    if skipped:
        # v12.7.10.29：明确告知覆盖度，不让模型把"没展开"当成"没数据"
        body += ('\n\n【以下表有数据，但本次篇幅不足以展开明细】%s —— 需要时请指明表名（或缩小范围）单独提问，'
                 '平台会完整统计该表。' % '、'.join('%s（%d 行·%s）' % (t, cnt, how) for t, cnt, how in skipped))
    return head + (('\n\n' + body) if body else '') + (('\n\n' + extra) if extra else '')


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
            # v12.7.10.27 修复：SQLite 把 NULL 与空串分成两个分组，旧写法会输出两条同名
            # “（空）=...”，看起来像两种不同的空值；这里先按展示标签合并再输出。
            # 另：整列全空时不再输出“0 种取值”，直接标注全空，减少噪音与上下文占用。
            if nd and nd <= 40:
                rs = db.execute('SELECT %s, COUNT(*) n FROM %s GROUP BY %s ORDER BY n DESC LIMIT 8'
                                % (c, t, c)).fetchall()
                agg, order = {}, []
                for x in rs:
                    k = '（空）' if x[0] in (None, '') else str(x[0])
                    if k not in agg:
                        order.append(k)
                    agg[k] = agg.get(k, 0) + x[1]
                if len(order) == 1 and order[0] == '（空）':
                    out.append('%s: 全部为空(%d)' % (c, agg['（空）']))
                else:
                    txt = '，'.join('%s=%d' % (k, agg[k]) for k in order[:8])
                    out.append('%s: %s' % (c, txt))
            elif not nd:
                out.append('%s: 全部为空' % c)
            else:
                out.append('%s: %d 种取值' % (c, nd))
        except Exception as e:
            out.append('%s: 统计失败(%s)' % (c, str(e)[:40]))
    return ('【全表精确统计·%d 行】' % cnt) + '；'.join(out)[:budget]

def _brief_line(db, t, cnt, cols):
    """v12.7.10.29：上下文预算不够展开某张表时输出的极简概览（≤约 220 字符）。
    目的：多业务模块同时提问时，排后的表不会被静默丢弃，模型至少知道"这张表存在、多少行、
    核心数值合计多少"，必要时可再单独追问该表。"""
    try:
        info = db.execute('PRAGMA table_info(%s)' % t).fetchall()
    except Exception:
        info = []
    types = {r[1]: (r[2] or '').upper() for r in info}
    safe = [c for c in cols if c not in _SENSITIVE_COL]
    num = [c for c in safe if any(k in types.get(c, '') for k in
           ('INT', 'REAL', 'NUM', 'DEC', 'FLOA', 'DOUB')) and c.lower() != 'id' and not c.lower().endswith('_id')]
    cat = [c for c in safe if ('CHAR' in types.get(c, '') or 'TEXT' in types.get(c, '')) and c.lower() != 'id']
    bits = ['%d 行' % cnt]
    for c in num[:1]:
        try:
            r = db.execute('SELECT SUM(%s) AS s, MAX(%s) AS mx FROM %s' % (c, c, t)).fetchone()
            if r and r[0] is not None:
                bits.append('%s 合计 %s / 最大 %s' % (c, r[0], r[1]))
        except Exception:
            pass
    for c in cat[:1]:
        try:
            rs = db.execute('SELECT %s AS v, COUNT(*) AS n FROM %s GROUP BY %s ORDER BY n DESC LIMIT 3'
                            % (c, t, c)).fetchall()
            if rs:
                bits.append('%s: %s' % (c, '，'.join('%s=%d' % (x[0], x[1]) for x in rs)))
        except Exception:
            pass
    return '【表 %s·概览】%s' % (t, '；'.join(bits))[:220]


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


def _overdue_report(brief=False):
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
        if brief:
            # v12.7.10.31：泛问时只给一行汇总，避免每次回答都被这段长明细主导
            return ('WBS 逾期提示（系统计算）：已逾期 %d 个任务、今日到期 %d 个、7 日内到期 %d 个；'
                    '如需具体任务明细，请在提问中指明 WBS/项目/逾期。'
                    % (len(overdue), len(due_today), len(due_week)))
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
_UNIVER_MAX_ROWS = 200000     # v12.7.10.30：取消单表 1 万行限制，按全表统计（仅留安全阀防解析爆内存）
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
    # v12.7.10.31：WBS 逾期报告不再无条件塞进每次提问（原先问合同/物料也会带一大段 WBS 逾期明细，
    # 导致模型把回答重心放到 WBS）。按问题主题分级：点名 WBS → 完整明细；泛问 → 一行汇总；其余 → 不注入。
    _qx = (question or '')
    _wbs_strong = any(w in _qx for w in ('WBS', 'wbs', '任务', '项目', '逾期', '超期', '到期', '进度', '计划', '排期', '里程碑'))
    _q_broad = any(w in _qx for w in ('分析', '总览', '整体', '全部', '所有', '数据', '情况', '问题', '风险'))
    if _wbs_strong:
        overdue = _overdue_report()
    elif _q_broad:
        overdue = _overdue_report(brief=True)
    else:
        overdue = ''
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
    _head_msgs = [{'role': 'system', 'content': _SYSTEM_PROMPT}]
    if long_summary:
        _head_msgs.append({'role': 'system',
                           'content': '【本会话长期要点摘要（早期对话已压缩为以下摘要，可据此理解上下文）】\n' + long_summary})
    _head_msgs += hist_msgs
    api_url, api_key = prov['api_url'], prov['api_key']

    def gen():
        collected = ''
        done = False
        assistant_id = None
        err_msg = ''
        try:
            yield 'event: meta\ndata: %s\n\n' % json.dumps({'conv_id': cid, 'user_msg_id': user_msg_id})
            # v12.7.10.30：数据超过单轮阈值时，分批送入模型、滚动汇总要点，再基于要点作答；
            # 不再靠丢弃表/压缩统计来"挤进一轮"。
            msgs = list(_head_msgs)
            if len(context) > _CTX_SINGLE:
                chunks = _split_chunks(context)
                memos, covered = [], 0
                for i, ch in enumerate(chunks, 1):
                    covered += len(ch)
                    yield 'data: %s\n\n' % json.dumps(
                        {'stage': '正在分批分析数据：第 %d/%d 批（本批 %d 字符，累计已读 %d 字符）…'
                         % (i, len(chunks), len(ch), covered)}, ensure_ascii=False)
                    bmemo, cerr = _compile_batch(api_url, api_key, model, ch, i, len(chunks), question)
                    if bmemo:
                        memos.append('【第 %d/%d 批】%s' % (i, len(chunks), bmemo))
                    if cerr:
                        yield 'data: %s\n\n' % json.dumps(
                            {'stage': '第 %d 批分析失败（%s），该批数据未纳入汇总' % (i, cerr)}, ensure_ascii=False)
                memo = '\n'.join(memos)
                if len(memo) > _CTX_SINGLE:
                    yield 'data: %s\n\n' % json.dumps(
                        {'stage': '各批要点合计 %d 字符，正在统一合并…' % len(memo)}, ensure_ascii=False)
                    memo = _merge_memos(api_url, api_key, model, memo, question)
                if memo:
                    yield 'data: %s\n\n' % json.dumps(
                        {'stage': '全量数据汇总完成（共 %d 批），正在生成结论…' % len(chunks)}, ensure_ascii=False)
                    msgs.append({'role': 'user', 'content':
                        '【全量数据要点（由 %d 批数据逐步汇总而成，已覆盖上面所有表）】\n%s\n%s\n\n请基于以上要点回答/分析：%s'
                        % (len(chunks), memo, extra, question)})
                else:
                    # 全部批次失败：退回原文（放宽到 6 万字符），并明确提示覆盖度
                    msgs.append({'role': 'user', 'content':
                        '以下是数据库当前（有数据的）结构、行数与样例数据（分批汇总失败，本次仅直接投喂前 6 万字符）：\n%s%s\n\n请回答/分析：%s'
                        % (context[:60000], extra, question)})
            else:
                msgs.append({'role': 'user', 'content':
                    '以下是数据库当前（有数据的）结构、行数与样例数据：\n%s%s\n\n请回答/分析：%s'
                    % (context, extra, question)})
            for chunk in _stream_chat(api_url, api_key, model, msgs):
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


_CTX_SINGLE = 12000     # v12.7.10.30：单轮直接送模型的数据阈值，超过则进入分批编译
_CTX_CHUNK = 9000       # 分批编译时每批字符数（按表块边界切，不切断统计块）
_MEMO_MAX = 1800        # 滚动要点上限（每批合并后重写，不是简单追加）


def _split_chunks(text, size=_CTX_CHUNK):
    """v12.7.10.30：按"表块"（空行分隔）切分数据文本，保证不把一张表的统计切断；
    单块本身超长时按行硬切兜底。"""
    blocks = text.split('\n\n')
    chunks, cur = [], ''
    for b in blocks:
        while len(b) > size:
            if cur:
                chunks.append(cur); cur = ''
            cut = b.rfind('\n', 0, size)
            cut = size if cut <= 0 else cut
            chunks.append(b[:cut]); b = b[cut:]
        if cur and len(cur) + len(b) + 2 > size:
            chunks.append(cur); cur = b
        else:
            cur = (cur + '\n\n' + b) if cur else b
    if cur:
        chunks.append(cur)
    return chunks or [text]


_COMPILE_SYS = ('你是数据摘录员。用户逐批给你业务数据，你负责把事实压成要点，供后续汇总。'
                '必须遵守：1) 保留所有表名与总行数；2) 保留数值列的合计/均值/最大/最小；'
                '3) 保留分类列的前 5 项分布计数；4) 保留与问题直接相关的关键明细行（编号/名称/金额等）；'
                '5) 不得省略数字、不得推测、不得给建议、不得编造；6) 只输出要点，不写过程说明。')


def _compile_batch(api_url, api_key, model, chunk, idx, total, question):
    """v12.7.10.30：把单批数据压成要点（只摘本批，不与其他批合并）。
    各批要点会全部保留并汇总，最终必要时再统一合并——避免模型"忘了合并"导致前面批次的信息丢失。
    返回 (本批要点, 错误文本)。"""
    usr = ('【用户问题】%s\n\n【本批数据（第 %d/%d 批）】\n%s\n\n'
           '请输出本批要点（不超过 700 字）：只写事实与数字，按表分组；'
           '与问题最相关的表保留其关键明细行（编号/名称/金额等）。'
           % (question, idx, total, chunk))
    out = ''.join(_stream_chat(api_url, api_key, model,
                               [{'role': 'system', 'content': _COMPILE_SYS},
                                {'role': 'user', 'content': usr}], timeout=300))
    if out.startswith('【调用失败') or out.startswith('【诊断'):
        return '', out[:120]
    return out.strip()[:700], ''


def _merge_memos(api_url, api_key, model, joined, question):
    """v12.7.10.30：各批要点总长超过单轮预算时，统一合并压缩一次（保留全部数字）。"""
    usr = ('【用户问题】%s\n\n【各批要点（必须全部保留，不得丢弃任何表名与数字）】\n%s\n\n'
           '请合并成一份要点（不超过 %d 字），按表分组，只写事实与数字。' % (question, joined, _MEMO_MAX))
    out = ''.join(_stream_chat(api_url, api_key, model,
                               [{'role': 'system', 'content': _COMPILE_SYS},
                                {'role': 'user', 'content': usr}], timeout=300))
    if out.startswith('【调用失败') or out.startswith('【诊断'):
        return joined[:_MEMO_MAX * 3]
    return out.strip()[:_MEMO_MAX]


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
        _seen_content = False
        _buf = []
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            for raw in resp:
                line = raw.decode('utf-8', errors='replace').strip()
                if not line:
                    continue
                _buf.append(line)
                if not line.startswith('data:'):
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
                        _seen_content = True
                        yield content
                except Exception:
                    continue
        # v12.7.10.32：兼容"不支持流式"的 OpenAI 兼容服务（一次性返回整体 JSON）。
        # 原实现只认 SSE 的 data: 行，遇到非流式响应会静默得到空答案，用户只看到"没有返回内容"。
        if not _seen_content:
            txt = '\n'.join(_buf).strip()
            try:
                obj = json.loads(txt)
            except Exception:
                obj = None
            if isinstance(obj, dict):
                ch = (obj.get('choices') or [{}])[0] or {}
                c = ((ch.get('message') or {}).get('content')
                     or (ch.get('delta') or {}).get('content')
                     or (obj.get('response') if isinstance(obj.get('response'), str) else ''))
                if c:
                    yield c
                    return
            yield ('【调用失败】模型返回内容为空或格式无法识别'
                   '（既不是流式 data: 分片，也没有 message.content）；'
                   '请确认模型服务支持 OpenAI 兼容接口，并检查模型是否正常加载。')
    except urllib.error.HTTPError as e:
        yield '【调用失败】HTTP %d：%s' % (e.code, e.read().decode('utf-8', errors='replace')[:400])
    except Exception:
        raise
