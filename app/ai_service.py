# -*- coding: utf-8 -*-
"""
v11.1 平台内置 AI 辅助服务（本地小模型 / 远程 OpenAI 兼容 API，只读，不写业务库）

设计边界（与用户确认）：
- AI 是"秘书/翻译官/被咨询者"，只做数据分析、聚合摘要、节点预警，绝不修改任何业务数据。
- 模型接入双模式（管理员在「AI 设置」页配置，存储于 ai_config 表）：
    1) local  —— 本地 Ollama（默认，数据不出内网）
    2) remote —— 远程 OpenAI 兼容 API（如 X-All In One 等平台，BaseURL + API Key + 模型名）
- 问答结果必须标注信息来源与数据时间。

对外接口（注册为蓝图，前缀 /api/ai）：
- GET  /api/ai/health          模型与平台数据源健康状态（按当前配置）
- POST /api/ai/test            连接测试（配置页"测试连接"用，不落库）
- POST /api/ai/ask             智能问答（只读聚合平台数据 + 模型回答）
- GET  /api/ai/alert-summary   预警摘要（临期/逾期/待审批，纯只读）
"""
import os
import sqlite3
import json
from datetime import datetime

import requests
from flask import Blueprint, jsonify, request, g

ai_bp = Blueprint('ai_assistant', __name__)

OLLAMA_BASE = os.environ.get('OLLAMA_HOST', 'http://127.0.0.1:11434')
DEFAULT_MODEL = os.environ.get('OLLAMA_MODEL', 'qwen2.5:0.5b')

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'business.db')


def _db():
    """只读连接：平台聚合数据源（绝不写库）——优先用 app 配置的库，保持与业务库一致"""
    try:
        from flask import current_app
        path = current_app.config['DATABASE']
    except Exception:
        path = DB_PATH
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


# ============================================================
# 配置读写（ai_config 表，key-value）
# ============================================================
def _get_config():
    """读取 AI 配置（管理员在 /admin/ai-settings 维护）"""
    cfg = {'mode': 'local', 'base_url': '', 'api_key': '', 'model': DEFAULT_MODEL, 'enabled': '1'}
    try:
        db = _db()
        for row in db.execute('SELECT key, value FROM ai_config').fetchall():
            cfg[row['key']] = row['value']
        db.close()
    except Exception:
        pass
    return cfg


def _save_config(cfg):
    """写入 AI 配置（仅平台层调用，管理员操作）"""
    from flask import current_app
    db = sqlite3.connect(current_app.config['DATABASE'])
    for k, v in cfg.items():
        db.execute('INSERT OR REPLACE INTO ai_config (key, value) VALUES (?, ?)', (k, str(v)))
    db.commit()
    db.close()


def _mask_key(api_key):
    """脱敏：sk-****后4位（图2 API 密钥管理样式）"""
    if not api_key:
        return ''
    if len(api_key) <= 8:
        return '****' + api_key[-4:]
    return api_key[:3] + '****' + api_key[-4:]


# ============================================================
# Provider 层：本地 Ollama / 远程 OpenAI 兼容 API
# ============================================================
def _ollama_available(base=None):
    """探测本地 Ollama 是否可用"""
    try:
        r = requests.get(f'{base or OLLAMA_BASE}/api/version', timeout=2)
        return r.status_code == 200
    except Exception:
        return False


def _ollama_models(base=None):
    """已安装模型列表"""
    try:
        r = requests.get(f'{base or OLLAMA_BASE}/api/tags', timeout=3)
        if r.status_code == 200:
            return [m['name'] for m in r.json().get('models', [])]
    except Exception:
        pass
    return []


def _ask_ollama(prompt, model=None, base=None):
    """调用本地模型（只读推理，无任何副作用）"""
    try:
        r = requests.post(f'{base or OLLAMA_BASE}/api/generate', json={
            'model': model or DEFAULT_MODEL,
            'prompt': prompt,
            'stream': False,
        }, timeout=120)
        if r.status_code == 200:
            data = r.json()
            return data.get('response', '')
    except Exception as e:
        return f'[模型调用失败: {e}]'
    return '[模型无响应]'


def _ask_remote(prompt, cfg):
    """调用远程 OpenAI 兼容 API（/chat/completions，Bearer 认证）"""
    base = (cfg.get('base_url') or '').rstrip('/')
    model = cfg.get('model') or DEFAULT_MODEL
    api_key = cfg.get('api_key') or ''
    if not base:
        return '[远程模型未配置 BaseURL]'
    url = base + '/chat/completions'
    headers = {'Authorization': f'Bearer {api_key}', 'Content-Type': 'application/json'}
    payload = {
        'model': model,
        'messages': [
            {'role': 'system', 'content': '你是研发管理平台的AI助手（只读辅助，不做任何数据修改），用中文简洁回答，数据不足时明确说明。'},
            {'role': 'user', 'content': prompt},
        ],
        'stream': False,
    }
    try:
        r = requests.post(url, json=payload, headers=headers, timeout=120)
        if r.status_code == 200:
            data = r.json()
            return data.get('choices', [{}])[0].get('message', {}).get('content', '')
        return f'[远程模型返回错误 {r.status_code}: {r.text[:200]}]'
    except Exception as e:
        return f'[远程模型调用失败: {e}]'


def _provider_check(cfg):
    """按配置检查当前 provider 可用性，返回 (ok, info_dict)"""
    mode = cfg.get('mode', 'local')
    if mode == 'remote':
        base = (cfg.get('base_url') or '').rstrip('/')
        if not base:
            return False, {'msg': '未配置 BaseURL'}
        try:
            r = requests.get(base + '/models', headers={'Authorization': f'Bearer {cfg.get("api_key", "")}'}, timeout=5)
            models = []
            if r.status_code == 200:
                models = [m.get('id') for m in r.json().get('data', [])[:20]]
            return r.status_code == 200, {'msg': f'HTTP {r.status_code}', 'models': models}
        except Exception as e:
            return False, {'msg': f'连接失败: {e}', 'models': []}
    else:
        ok = _ollama_available()
        return ok, {'msg': 'Ollama 运行中' if ok else 'Ollama 未启动', 'models': _ollama_models() if ok else []}


def _ask_provider(prompt, cfg):
    """按配置分发到对应 provider"""
    mode = cfg.get('mode', 'local')
    if mode == 'remote':
        return _ask_remote(prompt, cfg)
    return _ask_ollama(prompt, cfg.get('model'), None)


def _platform_context(max_rows=8):
    """只读聚合平台关键数据，拼成给模型的上下文（标注数据时间）"""
    ctx = []
    now = datetime.now().strftime('%Y-%m-%d %H:%M')
    ctx.append(f'平台数据时间：{now}（内网实时只读快照）')
    try:
        db = _db()
        # WBS 概览
        wbs = db.execute('''
            SELECT COUNT(*) total,
                   SUM(CASE WHEN status IN ('进行中','规划中') THEN 1 ELSE 0 END) active,
                   SUM(CASE WHEN status='已完成' THEN 1 ELSE 0 END) done
            FROM wbs_projects''').fetchone()
        ctx.append(f'WBS项目：共{wbs["total"] or 0}个，进行中{wbs["active"] or 0}个，已完成{wbs["done"] or 0}个')
        # WBS 逾期任务
        overdue = db.execute('''
            SELECT id, name, plan_end FROM wbs_tasks
            WHERE status NOT IN ('已完成','已取消') AND plan_end < date('now','localtime')
            ORDER BY plan_end LIMIT %d''' % max_rows).fetchall()
        if overdue:
            ctx.append('WBS逾期任务：' + '；'.join(f'{t["name"]}(截止{t["plan_end"]})' for t in overdue))
        else:
            ctx.append('WBS逾期任务：无')
        # 需求概览
        req = db.execute('''
            SELECT COUNT(*) total,
                   SUM(CASE WHEN status='待评审' THEN 1 ELSE 0 END) pending,
                   SUM(CASE WHEN status IN ('评审通过','已采纳','开发中') THEN 1 ELSE 0 END) doing,
                   SUM(CASE WHEN status IN ('已完成','已关闭') THEN 1 ELSE 0 END) done
            FROM req_items WHERE is_deleted=0''').fetchone()
        ctx.append(f'需求：共{req["total"] or 0}条，待评审{req["pending"] or 0}，进行中{req["doing"] or 0}，已完成{req["done"] or 0}')
        # 需求临期/逾期
        req_alerts = db.execute('''
            SELECT code, title, delivery_date, status FROM req_items
            WHERE is_deleted=0 AND status NOT IN ('已完成','已关闭') AND delivery_date != ''
            AND delivery_date <= date('now','localtime','+7 day')
            ORDER BY delivery_date LIMIT %d''' % max_rows).fetchall()
        if req_alerts:
            ctx.append('需求临期/逾期提醒：' + '；'.join(
                f'{r["code"]} {r["title"]}(交付{r["delivery_date"]},{r["status"]})' for r in req_alerts))
        # 审批待办
        appr = db.execute("SELECT COUNT(*) c FROM approval_requests WHERE status='待审批'").fetchone()
        ctx.append(f'待审批事项：{appr["c"] or 0}条')
        # 外部系统接入
        ext = db.execute('SELECT COUNT(*) c FROM external_systems').fetchone()
        ctx.append(f'已接入外部系统/插件：{ext["c"] or 0}个')
        db.close()
    except Exception as e:
        ctx.append(f'[部分数据源读取异常: {e}]')
    return '\n'.join(ctx)


@ai_bp.route('/health')
def health():
    """健康状态：当前配置模式 + 模型可用性 + 平台数据源"""
    cfg = _get_config()
    mode = cfg.get('mode', 'local')
    ok, info = _provider_check(cfg)
    return jsonify({
        'ok': ok,
        'mode': mode,
        'base_url': cfg.get('base_url', '') if mode == 'remote' else OLLAMA_BASE,
        'model': cfg.get('model') or DEFAULT_MODEL,
        'api_key_masked': _mask_key(cfg.get('api_key', '')),
        'detail': info.get('msg', ''),
        'models': info.get('models', []),
        'data_source': 'business.db (只读聚合)',
        'time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
    })


@ai_bp.route('/test', methods=['POST'])
def test():
    """连接测试：用请求体里的配置试连（不保存），供设置页测试按钮调用"""
    data = request.get_json(silent=True) or {}
    mode = data.get('mode') or 'local'
    if mode == 'remote':
        base = (data.get('base_url') or '').rstrip('/')
        if not base:
            return jsonify({'ok': False, 'msg': '请先填写 BaseURL'})
        key = data.get('api_key') or ''
        try:
            r = requests.get(base + '/models', headers={'Authorization': f'Bearer {key}'}, timeout=8)
            models = []
            if r.status_code == 200:
                models = [m.get('id') for m in r.json().get('data', [])[:10]]
            return jsonify({
                'ok': r.status_code == 200,
                'msg': f'连接成功，发现 {len(models)} 个模型' if r.status_code == 200 else f'连接失败：HTTP {r.status_code}',
                'models': models,
            })
        except Exception as e:
            return jsonify({'ok': False, 'msg': f'连接失败：{e}', 'models': []})
    else:
        ok = _ollama_available()
        return jsonify({'ok': ok, 'msg': '本地 Ollama 运行中' if ok else '本地 Ollama 未启动（请先运行 ollama serve）', 'models': _ollama_models() if ok else []})


@ai_bp.route('/ask', methods=['POST'])
def ask():
    """智能问答：平台聚合数据 + 模型（按当前配置），只读"""
    data = request.get_json(silent=True) or {}
    question = (data.get('question') or '').strip()
    if not question:
        return jsonify({'ok': False, 'msg': '问题不能为空'}), 400
    cfg = _get_config()
    if cfg.get('enabled', '1') != '1':
        return jsonify({'ok': False, 'msg': 'AI 助手当前已停用，请在「AI 设置」中启用'}), 503
    ok, info = _provider_check(cfg)
    if not ok:
        return jsonify({'ok': False, 'msg': '模型服务不可用（请检查「AI 设置」配置或本地 Ollama 是否启动）', 'detail': info.get('msg', '')}), 503

    context = _platform_context()
    prompt = (
        '你是研发管理平台的AI助手（只读辅助，不做任何数据修改）。\n'
        f'以下是平台当前聚合数据：\n{context}\n\n'
        f'用户问题：{question}\n'
        '请基于上述数据用中文简洁回答；若数据不足以回答，请明确说明。'
    )
    answer = _ask_provider(prompt, cfg)
    mode = cfg.get('mode', 'local')
    source = f'平台内网本地模型（{cfg.get("model") or DEFAULT_MODEL}）' if mode == 'local' else f'远程模型（{cfg.get("base_url")} / {cfg.get("model")}）'
    return jsonify({
        'ok': True,
        'question': question,
        'answer': answer,
        'source': source,
        'data_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'readonly': True,
    })


@ai_bp.route('/alert-summary')
def alert_summary():
    """预警摘要：临期/逾期/待审批（确定性规则 + 模型润色可选，纯只读）"""
    db = _db()
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    try:
        wbs_overdue = db.execute('''
            SELECT COUNT(*) c FROM wbs_tasks
            WHERE status NOT IN ('已完成','已取消') AND plan_end < date('now','localtime')''').fetchone()['c']
        req_due = db.execute('''
            SELECT COUNT(*) c FROM req_items
            WHERE is_deleted=0 AND status NOT IN ('已完成','已关闭') AND delivery_date != ''
            AND delivery_date <= date('now','localtime','+7 day')''').fetchone()['c']
        req_overdue = db.execute('''
            SELECT COUNT(*) c FROM req_items
            WHERE is_deleted=0 AND status NOT IN ('已完成','已关闭') AND delivery_date != ''
            AND delivery_date < date('now','localtime')''').fetchone()['c']
        appr_pending = db.execute("SELECT COUNT(*) c FROM approval_requests WHERE status='待审批'").fetchone()['c']
    finally:
        db.close()
    return jsonify({
        'ok': True,
        'time': now,
        'alerts': {
            'wbs_overdue_tasks': wbs_overdue,
            'req_due_soon': req_due,
            'req_overdue': req_overdue,
            'approval_pending': appr_pending,
        },
        'readonly': True,
    })
