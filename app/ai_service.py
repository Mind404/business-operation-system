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
from flask import Blueprint, jsonify, request, g, session

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
    cfg = {
        'mode': 'local',          # local | remote
        'provider_type': 'ollama',  # 本地模式服务类型：ollama（原生）| openai_compat（LM Studio/vLLM 等）
        'base_url': '',           # 本地默认由 provider_type 推导；远程必填
        'api_key': '',
        'model': DEFAULT_MODEL,
        'enabled': '1',
    }
    try:
        db = _db()
        for row in db.execute('SELECT key, value FROM ai_config').fetchall():
            cfg[row['key']] = row['value']
        db.close()
    except Exception:
        pass
    return cfg


def _effective_base(cfg):
    """按模式/服务类型返回实际服务地址（本地未填时给默认值）"""
    mode = cfg.get('mode', 'local')
    if mode == 'remote':
        return (cfg.get('base_url') or '').rstrip('/')
    # 本地模式：未配置地址时按服务类型给默认
    if cfg.get('provider_type') == 'openai_compat':
        return (cfg.get('base_url') or 'http://127.0.0.1:1234/v1').rstrip('/')
    return (cfg.get('base_url') or OLLAMA_BASE).rstrip('/')


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
            'keep_alive': '30m',  # 模型常驻内存：避免每次对话重新加载（CPU 环境首答慢，热身后秒级）
        }, timeout=120)
        if r.status_code == 200:
            data = r.json()
            return data.get('response', '')
    except Exception as e:
        return f'[模型调用失败: {e}]'
    return '[模型无响应]'


def _ask_remote(prompt, cfg):
    """调用 OpenAI 兼容 API（/chat/completions，Bearer 认证；本地 OpenAI 兼容后端密钥可为空）"""
    base = _effective_base(cfg)
    model = cfg.get('model') or DEFAULT_MODEL
    api_key = cfg.get('api_key') or ''
    if not base:
        return '[模型服务未配置 BaseURL]'
    url = base + '/chat/completions'
    headers = {'Content-Type': 'application/json'}
    if api_key:
        headers['Authorization'] = f'Bearer {api_key}'
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
        return f'[模型返回错误 {r.status_code}: {r.text[:200]}]'
    except Exception as e:
        return f'[模型调用失败: {e}]'


def _provider_check(cfg):
    """按配置检查当前 provider 可用性，返回 (ok, info_dict)"""
    mode = cfg.get('mode', 'local')
    is_openai_compat = (mode == 'remote') or (cfg.get('provider_type') == 'openai_compat')
    if is_openai_compat:
        base = _effective_base(cfg)
        if not base:
            return False, {'msg': '未配置 BaseURL'}
        headers = {'Content-Type': 'application/json'}
        if cfg.get('api_key'):
            headers['Authorization'] = f'Bearer {cfg.get("api_key", "")}'
        try:
            r = requests.get(base + '/models', headers=headers, timeout=5)
            models = []
            if r.status_code == 200:
                models = [m.get('id') for m in r.json().get('data', [])[:20]]
            return r.status_code == 200, {'msg': f'HTTP {r.status_code}', 'models': models}
        except Exception as e:
            return False, {'msg': f'连接失败: {e}', 'models': []}
    else:
        base = _effective_base(cfg)
        ok = _ollama_available(base)
        return ok, {'msg': 'Ollama 运行中' if ok else 'Ollama 未启动', 'models': _ollama_models(base) if ok else []}


def _ask_provider(prompt, cfg):
    """按配置分发到对应 provider（本地 Ollama 原生 / OpenAI 兼容）"""
    mode = cfg.get('mode', 'local')
    is_openai_compat = (mode == 'remote') or (cfg.get('provider_type') == 'openai_compat')
    if is_openai_compat:
        return _ask_remote(prompt, cfg)
    return _ask_ollama(prompt, cfg.get('model'), _effective_base(cfg))


# ============================================================
# 数据源注册表（v11.1 注册式聚合）
# 每个数据源 = {'id','name','fn'}；fn(db, role, username, max_rows) 返回只读文本段。
# 后续新增插件（bug/待办/售后/合同…）：加一条注册 + 写一个查询函数，AI 自动认识该插件数据。
# ============================================================
def _src_wbs(db, role, username, max_rows, staleness_days=14):
    """WBS 计划：项目概览 + 逾期任务（普通用户只聚合自己参与的项目）"""
    lines = []
    if role in ('admin', 'platform_admin'):
        wbs = db.execute('''SELECT COUNT(*) total,
               SUM(CASE WHEN status IN ('进行中','规划中') THEN 1 ELSE 0 END) active,
               SUM(CASE WHEN status='已完成' THEN 1 ELSE 0 END) done
            FROM wbs_projects''').fetchone()
        lines.append(f'WBS项目：共{wbs["total"] or 0}个，进行中{wbs["active"] or 0}个，已完成{wbs["done"] or 0}个')
        rows = db.execute('''SELECT id, name, plan_end, status FROM wbs_tasks
            WHERE status NOT IN ('已完成','已取消') AND plan_end < date('now','localtime')
            ORDER BY plan_end LIMIT %d''' % max_rows).fetchall()
    else:
        wbs = db.execute('''SELECT COUNT(*) total FROM wbs_projects
            WHERE owner=? OR participants LIKE ?''', (username, '%' + username + '%')).fetchone()
        lines.append(f'WBS项目（我参与）：共{wbs["total"] or 0}个')
        rows = db.execute('''SELECT t.id, t.name, t.plan_end, t.status FROM wbs_tasks t
            JOIN wbs_projects p ON p.id=t.project_id
            WHERE t.status NOT IN ('已完成','已取消') AND t.plan_end < date('now','localtime')
            AND (p.owner=? OR p.participants LIKE ? OR t.owner=? OR t.collaborators LIKE ?)
            ORDER BY t.plan_end LIMIT %d''' % max_rows,
            (username, '%' + username + '%', username, '%' + username + '%')).fetchall()
    if rows:
        lines.append('WBS逾期任务：' + '；'.join(f'{t["name"]}(截止{t["plan_end"]})' for t in rows))
    else:
        lines.append('WBS逾期任务：无')
    return '\n'.join(lines)


def _src_requirements(db, role, username, max_rows, staleness_days):
    """需求：概览 + 临期/逾期 + 呆滞需求（普通用户只聚合自己创建/负责的）"""
    lines = []
    if role in ('admin', 'platform_admin'):
        req = db.execute('''SELECT COUNT(*) total,
               SUM(CASE WHEN status='待评审' THEN 1 ELSE 0 END) pending,
               SUM(CASE WHEN status IN ('评审通过','已采纳','开发中') THEN 1 ELSE 0 END) doing,
               SUM(CASE WHEN status IN ('已完成','已关闭') THEN 1 ELSE 0 END) done
            FROM req_items WHERE is_deleted=0''').fetchone()
        lines.append(f'需求：共{req["total"] or 0}条，待评审{req["pending"] or 0}，进行中{req["doing"] or 0}，已完成{req["done"] or 0}')
        alerts = db.execute('''SELECT code, title, delivery_date, status FROM req_items
            WHERE is_deleted=0 AND status NOT IN ('已完成','已关闭') AND delivery_date != ''
            AND delivery_date <= date('now','localtime','+7 day')
            ORDER BY delivery_date LIMIT %d''' % max_rows).fetchall()
        stal = db.execute('''SELECT code, title, status, updated_at FROM req_items
            WHERE is_deleted=0 AND status NOT IN ('已完成','已关闭')
            AND updated_at <= datetime('now','localtime', ?)
            ORDER BY updated_at LIMIT %d''' % max_rows, (f'-{staleness_days} days',)).fetchall()
    else:
        req = db.execute('''SELECT COUNT(*) total FROM req_items
            WHERE is_deleted=0 AND (owner=? OR created_by=?)''',
            (username, username)).fetchone()
        lines.append(f'需求（我创建/负责）：共{req["total"] or 0}条')
        alerts = db.execute('''SELECT code, title, delivery_date, status FROM req_items
            WHERE is_deleted=0 AND status NOT IN ('已完成','已关闭') AND delivery_date != ''
            AND delivery_date <= date('now','localtime','+7 day')
            AND (owner=? OR created_by=?)
            ORDER BY delivery_date LIMIT %d''' % max_rows,
            (username, username)).fetchall()
        stal = db.execute('''SELECT code, title, status, updated_at FROM req_items
            WHERE is_deleted=0 AND status NOT IN ('已完成','已关闭')
            AND updated_at <= datetime('now','localtime', ?)
            AND (owner=? OR created_by=?)
            ORDER BY updated_at LIMIT %d''' % max_rows,
            (f'-{staleness_days} days', username, username)).fetchall()
    if alerts:
        lines.append('需求临期/逾期提醒：' + '；'.join(
            f'{r["code"]} {r["title"]}(交付{r["delivery_date"]},{r["status"]})' for r in alerts))
    if stal:
        lines.append(f'呆滞需求（超{staleness_days}天未推进）：' + '；'.join(
            f'{r["code"]} {r["title"]}({r["status"]},最后更新{r["updated_at"]})' for r in stal))
    return '\n'.join(lines)


def _data_source_registry():
    """数据源注册表（动态扩展点）"""
    return [
        {'id': 'wbs', 'name': 'WBS计划管理', 'fn': _src_wbs},
        {'id': 'requirements', 'name': '需求收集', 'fn': _src_requirements},
    ]


def _platform_context(role='user', username=None, max_rows=8):
    """只读聚合平台关键数据，拼成给模型的上下文（标注数据时间与只读声明）"""
    ctx = []
    now = datetime.now().strftime('%Y-%m-%d %H:%M')
    ctx.append(f'平台数据时间：{now}（内网实时只读快照；AI助手为只读辅助，绝不修改任何业务数据）')
    try:
        db = _db()
        # 数据结构说明（来自插件注册表 tables 登记，动态）
        try:
            regs = db.execute("SELECT id, name, tables FROM plugin_registry WHERE core=0").fetchall()
            for r in regs:
                tbls = json.loads(r['tables'] or '[]')
                if tbls:
                    ctx.append(f'{r["name"]}数据表：' + '；'.join(
                        f'{t["table"]}({t["desc"]})' if isinstance(t, dict) else str(t) for t in tbls))
        except Exception:
            pass
        cfg = _get_config()
        staleness_days = int(cfg.get('staleness_days') or 14)
        for src in _data_source_registry():
            try:
                seg = src['fn'](db, role, username or '', max_rows, staleness_days)
                if seg:
                    ctx.append(seg)
            except Exception as e:
                ctx.append(f'[{src["name"]}数据源读取异常:{e}]')
        # 审批待办 + 外部系统
        appr = db.execute("SELECT COUNT(*) c FROM approval_requests WHERE status='待审批'").fetchone()
        ctx.append(f'待审批事项：{appr["c"] or 0}条')
        ext = db.execute('SELECT COUNT(*) c FROM external_systems').fetchone()
        ctx.append(f'已接入外部系统/插件：{ext["c"] or 0}个')
        db.close()
    except Exception as e:
        ctx.append(f'[部分数据源读取异常: {e}]')
    return '\n'.join(ctx)


def _extract_refs(text):
    """从文本中识别需求编号，返回可点击的原始记录链接（只读指引）"""
    refs = []
    if not text:
        return refs
    import re
    try:
        db = _db()
        for m in re.finditer(r'RQ-\d{4}-\d{3}', text):
            code = m.group(0)
            if any(r['type'] == 'req' and r['label'].startswith(code) for r in refs):
                continue
            row = db.execute('SELECT id, title FROM req_items WHERE code=? AND is_deleted=0',
                             (code,)).fetchone()
            if row:
                refs.append({'type': 'req', 'label': f'{code} {row["title"]}',
                             'url': f'/requirements/{row["id"]}'})
        db.close()
    except Exception:
        pass
    return refs[:5]


@ai_bp.route('/health')
def health():
    """健康状态：当前配置模式 + 模型可用性 + 平台数据源"""
    cfg = _get_config()
    mode = cfg.get('mode', 'local')
    ok, info = _provider_check(cfg)
    return jsonify({
        'ok': ok,
        'mode': mode,
        'provider_type': cfg.get('provider_type', 'ollama'),
        'base_url': _effective_base(cfg),
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
    provider_type = data.get('provider_type') or ('ollama' if mode != 'remote' else 'openai_compat')
    is_openai_compat = (mode == 'remote') or (provider_type == 'openai_compat')
    if is_openai_compat:
        base = (data.get('base_url') or '').rstrip('/')
        if not base:
            return jsonify({'ok': False, 'msg': '请先填写 BaseURL'})
        key = data.get('api_key') or ''
        headers = {'Content-Type': 'application/json'}
        if key:
            headers['Authorization'] = f'Bearer {key}'
        try:
            r = requests.get(base + '/models', headers=headers, timeout=8)
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
        base = (data.get('base_url') or OLLAMA_BASE).rstrip('/')
        ok = _ollama_available(base)
        return jsonify({'ok': ok, 'msg': '本地 Ollama 运行中' if ok else '本地 Ollama 未启动（请先运行 ollama serve）', 'models': _ollama_models(base) if ok else []})


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

    role = session.get('user_role', 'user')
    username = session.get('username')
    context = _platform_context(role=role, username=username)
    prompt = (
        '你是研发管理平台的AI助手（只读辅助，不做任何数据修改，绝不修改数据库）。\n'
        f'当前用户角色：{role}；仅聚合其有权查看的数据。\n'
        f'以下是平台当前聚合数据：\n{context}\n\n'
        f'用户问题：{question}\n'
        '请基于上述数据用中文简洁回答；回答中如提到需求请带上编号（如 RQ-2026-001）；'
        '若数据不足以回答，请明确说明，不要编造。'
    )
    answer = _ask_provider(prompt, cfg)
    mode = cfg.get('mode', 'local')
    if mode == 'remote':
        source = f'远程模型（{_effective_base(cfg)} / {cfg.get("model") or DEFAULT_MODEL}）'
    elif cfg.get('provider_type') == 'openai_compat':
        source = f'本地 OpenAI 兼容后端（{_effective_base(cfg)} / {cfg.get("model") or DEFAULT_MODEL}）'
    else:
        source = f'平台内网本地模型（{cfg.get("model") or DEFAULT_MODEL}）'
    refs = _extract_refs(question + ' ' + answer)
    return jsonify({
        'ok': True,
        'question': question,
        'answer': answer,
        'source': source,
        'refs': refs,
        'data_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'readonly': True,
    })


@ai_bp.route('/alert-summary')
def alert_summary():
    """预警摘要：临期/逾期/呆滞/待审批（确定性规则，纯只读），含原始记录链接"""
    db = _db()
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    role = session.get('user_role', 'user')
    username = session.get('username')
    cfg = _get_config()
    staleness_days = int(cfg.get('staleness_days') or 14)
    try:
        if role in ('admin', 'platform_admin'):
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
            stal = db.execute('''
                SELECT COUNT(*) c FROM req_items
                WHERE is_deleted=0 AND status NOT IN ('已完成','已关闭')
                AND updated_at <= datetime('now','localtime', ?)''',
                (f'-{staleness_days} days',)).fetchone()['c']
            stal_list = db.execute('''
                SELECT id, code, title, status, updated_at FROM req_items
                WHERE is_deleted=0 AND status NOT IN ('已完成','已关闭')
                AND updated_at <= datetime('now','localtime', ?)
                ORDER BY updated_at LIMIT 8''',
                (f'-{staleness_days} days',)).fetchall()
        else:
            wbs_overdue = db.execute('''
                SELECT COUNT(*) c FROM wbs_tasks t JOIN wbs_projects p ON p.id=t.project_id
                WHERE t.status NOT IN ('已完成','已取消') AND t.plan_end < date('now','localtime')
                AND (p.owner=? OR p.participants LIKE ? OR t.owner=? OR t.collaborators LIKE ?)''',
                (username, '%' + username + '%', username, '%' + username + '%')).fetchone()['c']
            req_due = db.execute('''
                SELECT COUNT(*) c FROM req_items
                WHERE is_deleted=0 AND status NOT IN ('已完成','已关闭') AND delivery_date != ''
                AND delivery_date <= date('now','localtime','+7 day')
                AND (owner=? OR created_by=?)''', (username, username)).fetchone()['c']
            req_overdue = db.execute('''
                SELECT COUNT(*) c FROM req_items
                WHERE is_deleted=0 AND status NOT IN ('已完成','已关闭') AND delivery_date != ''
                AND delivery_date < date('now','localtime')
                AND (owner=? OR created_by=?)''', (username, username)).fetchone()['c']
            stal = db.execute('''
                SELECT COUNT(*) c FROM req_items
                WHERE is_deleted=0 AND status NOT IN ('已完成','已关闭')
                AND updated_at <= datetime('now','localtime', ?)
                AND (owner=? OR created_by=?)''',
                (f'-{staleness_days} days', username, username)).fetchone()['c']
            stal_list = db.execute('''
                SELECT id, code, title, status, updated_at FROM req_items
                WHERE is_deleted=0 AND status NOT IN ('已完成','已关闭')
                AND updated_at <= datetime('now','localtime', ?)
                AND (owner=? OR created_by=?)
                ORDER BY updated_at LIMIT 8''',
                (f'-{staleness_days} days', username, username)).fetchall()
        appr_pending = db.execute("SELECT COUNT(*) c FROM approval_requests WHERE status='待审批'").fetchone()['c']
    finally:
        db.close()
    return jsonify({
        'ok': True,
        'time': now,
        'staleness_days': staleness_days,
        'alerts': {
            'wbs_overdue_tasks': wbs_overdue or 0,
            'req_due_soon': req_due or 0,
            'req_overdue': req_overdue or 0,
            'req_stale': stal or 0,
            'approval_pending': appr_pending or 0,
        },
        'staleness_list': [{'id': r['id'], 'code': r['code'], 'title': r['title'],
                            'status': r['status'], 'updated_at': r['updated_at'],
                            'url': f'/requirements/{r["id"]}'} for r in stal_list],
        'readonly': True,
    })
