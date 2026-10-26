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

from flask import Blueprint, current_app, g, jsonify, render_template, request, session

ai_bp = Blueprint('ai_admin', __name__, url_prefix='/admin/ai')


def _db():
    if 'ai_db' not in g:
        g.ai_db = sqlite3.connect(current_app.config['DATABASE'])
        g.ai_db.row_factory = sqlite3.Row
    return g.ai_db


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
# 数据库上下文采集（含插件表）：只读、白名单表名、限量
# ============================================================
_SAFE_TABLE = re.compile(r'^[A-Za-z0-9_]+$')
_EXCLUDE_TABLES = ('schema_version', 'ai_chat_logs', 'ai_providers')


def _collect_db_context(max_chars=14000):
    db = _db()
    rows = db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name").fetchall()
    parts = []
    used = 0
    for r in rows:
        t = r['name']
        if t in _EXCLUDE_TABLES or not _SAFE_TABLE.match(t):
            continue
        try:
            cols = [c[1] for c in db.execute('PRAGMA table_info(%s)' % t).fetchall()]
            cnt = db.execute('SELECT COUNT(*) AS c FROM %s' % t).fetchone()['c']
            sample_rows = db.execute('SELECT * FROM %s LIMIT 3' % t).fetchall()
            sample = [dict(x) for x in sample_rows]
            part = '【表 %s】（%d 行）列: %s\n样例数据: %s' % (
                t, cnt, ', '.join(cols), json.dumps(sample, ensure_ascii=False, default=str)[:1400])
        except Exception as e:
            part = '【表 %s】读取失败: %s' % (t, e)
        if used + len(part) > max_chars:
            break
        parts.append(part)
        used += len(part)
    return '\n\n'.join(parts) if parts else '（未读取到业务数据表）'


_SYSTEM_PROMPT = (
    '你是一名企业业务系统“业务高效运转系统”的数据分析助手。系统包含单点登录、统一权限、'
    'WBS计划管理、需求收集管理（在线云表格）等插件，数据保存在 SQLite 数据库中。\n'
    '用户会提供数据库各表的表结构、行数与样例数据。请基于这些真实数据回答用户的问题；'
    '可以进行汇总统计、趋势分析、问题归因等分析。数据不足或无法确定时，请明确说明，不要编造数字。\n'
    '回答用中文，简洁、条理清晰，需要时可使用小标题或列表。'
)


# ============================================================
# 问答 / 数据分析
# ============================================================
@ai_bp.route('/api/chat', methods=['POST'])
def chat():
    if not _require_platform():
        return _deny()
    d = request.get_json(silent=True) or {}
    pid = d.get('provider_id')
    model = (d.get('model') or '').strip()
    question = (d.get('question') or '').strip()
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

    context = _collect_db_context()
    messages = [
        {'role': 'system', 'content': _SYSTEM_PROMPT},
        {'role': 'user', 'content': '以下是数据库当前结构与样例数据：\n%s\n\n请回答/分析：%s' % (context, question)},
    ]
    answer = _chat_request(prov['api_url'], prov['api_key'], model, messages)
    try:
        db.execute('INSERT INTO ai_chat_logs (provider_id, model, question, answer) VALUES (?,?,?,?)',
                   (pid, model, question, answer))
        db.commit()
    except Exception:
        pass
    return jsonify({'ok': True, 'answer': answer})


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
