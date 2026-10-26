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
        char_count INTEGER DEFAULT 0
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
_EXCLUDE_TABLES = ('schema_version', 'ai_chat_logs', 'ai_providers')


def _table_words(question):
    """从问题中提取关键词（中英文单词/短语片段），用于表相关性匹配"""
    words = set()
    # 英文单词/数字
    for w in re.findall(r'[a-zA-Z][a-zA-Z0-9_]{1,}', question.lower()):
        words.add(w)
    # 中文 2-4 字片段（去常见虚词）
    cn = re.findall(r'[\u4e00-\u9fa5]{2,6}', question)
    stop = {'什么', '多少', '如何', '怎么', '为什么', '是否', '统计', '分析', '汇总', '查看', '查询',
            '目前', '当前', '情况', '状态', '数量', '进度', '完成', '计划', '项目', '任务', '数据',
            '有没有', '需要', '一下', '可以', '帮我', '以及', '还有', '全部', '各个'}
    for w in cn:
        for i in range(len(w)):
            for j in range(i + 2, min(i + 5, len(w) + 1)):
                frag = w[i:j]
                if frag not in stop:
                    words.add(frag)
    return words


def _collect_db_context(question, max_chars=8000):
    """只注入【有数据】的表；按问题关键词做相关性排序（相关表优先完整注入），
    空表与无关表直接跳过 —— 让小模型聚焦真实业务数据，避免被无关空表干扰发散。"""
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
    # 相关表在前（score 降序），其余有数据表在后
    scored.sort(key=lambda x: -x[3])

    parts = []
    used = 0
    total_active = len(active)
    related = sum(1 for x in scored if x[3] > 0)
    for idx, (t, cnt, cols, score) in enumerate(scored):
        try:
            sample_rows = db.execute('SELECT * FROM %s LIMIT 2' % t).fetchall()
            sample = [dict(x) for x in sample_rows]
            tag = '相关' if score > 0 else ''
            part = '【表 %s·%s】（%d 行）列: %s\n样例数据: %s' % (
                t, tag, cnt, ', '.join(cols), json.dumps(sample, ensure_ascii=False, default=str)[:900])
        except Exception as e:
            part = '【表 %s】读取失败: %s' % (t, e)
        if used + len(part) > max_chars:
            break
        parts.append(part)
        used += len(part)
    head = '数据库概要：共 %d 张业务表，其中 %d 张有数据（%d 张与你的问题相关）。只列出有数据的表，空表未读取。' % (
        len(tables), total_active, related)
    return head + '\n\n' + '\n\n'.join(parts) if parts else head


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
    '请只基于这些真实数据回答用户的问题，做汇总统计、趋势分析、问题归因等；'
    '若相关数据为空或不足，直接说明“当前没有相关数据/无法判断”，不要编造数字，不要推测空表中可能有什么。\n'
    '关于超期/逾期/到期类问题：若用户输入中包含“WBS 任务超期预判”，直接采用其中的数字与明细（系统已按'
    '“计划结束时间已过且状态非已完成/已取消即为逾期，未开始也算逾期”的规则计算），不要自行另行推断或漏掉未开始的任务。\n'
    '回答用中文，先给结论再给依据，条理清晰，可使用小标题、列表、加粗等 Markdown 格式。'
)


# ============================================================
# 问答 / 数据分析（流式：支持前端中止）
# ============================================================
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

    # —— 以下闭包变量全部在请求上下文内准备；generator 运行时上下文已弹出，只能读闭包 ——
    db_path = current_app.config['DATABASE']
    context = _collect_db_context(question)
    overdue = _overdue_report()
    extra = '\n\n%s' % overdue if overdue else ''
    req_messages = [
        {'role': 'system', 'content': _SYSTEM_PROMPT},
        {'role': 'user', 'content': '以下是数据库当前（有数据的）结构、行数与样例数据：\n%s%s\n\n请回答/分析：%s' % (context, extra, question)},
    ]
    api_url, api_key = prov['api_url'], prov['api_key']

    def gen():
        collected = ''
        done = False
        assistant_id = None
        try:
            yield 'event: meta\ndata: %s\n\n' % json.dumps({'conv_id': cid, 'user_msg_id': user_msg_id})
            for chunk in _stream_chat(api_url, api_key, model, req_messages):
                collected += chunk
                yield 'data: %s\n\n' % json.dumps({'delta': chunk})
            done = True
        except GeneratorExit:
            # 客户端断开（点击中止）：保留已生成部分
            pass
        except Exception:
            pass
        finally:
            try:
                assistant_id = _save_ai_message(db_path, cid, collected, pid, model, question,
                                                aborted=not done)
            except Exception:
                pass
        yield 'event: end\ndata: %s\n\n' % json.dumps({'ok': done, 'answer': collected, 'msg_id': assistant_id})

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


def _save_ai_message(db_path, cid, content, pid, model, question, aborted=False):
    """保存 AI 回复（正常完成=完整内容；中止=已生成部分），返回消息 id。
    独立直连数据库（流式 generator 阶段请求上下文已弹出，不能依赖 flask g）"""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        if not content or not content.strip():
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
