# -*- coding: utf-8 -*-
"""
v11.0 平台内置 AI 辅助服务（本地小模型，只读，不写业务库）

设计边界（与用户确认）：
- AI 是"秘书/翻译官/被咨询者"，只做数据分析、聚合摘要、节点预警，绝不修改任何业务数据。
- 模型本地部署（Ollama 127.0.0.1:11434），数据不出内网。
- 问答结果必须标注信息来源与数据时间。

对外接口（注册为蓝图，前缀 /api/ai）：
- GET  /api/ai/health          模型与平台数据源健康状态
- POST /api/ai/ask             智能问答（只读聚合平台数据 + 本地模型回答）
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
OLLAMA_MODEL = os.environ.get('OLLAMA_MODEL', 'qwen2.5:0.5b')

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'business.db')


def _db():
    """只读连接：平台聚合数据源（绝不写库）"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _ollama_available():
    """探测本地 Ollama 是否可用"""
    try:
        r = requests.get(f'{OLLAMA_BASE}/api/version', timeout=2)
        return r.status_code == 200
    except Exception:
        return False


def _ollama_models():
    """已安装模型列表"""
    try:
        r = requests.get(f'{OLLAMA_BASE}/api/tags', timeout=3)
        if r.status_code == 200:
            return [m['name'] for m in r.json().get('models', [])]
    except Exception:
        pass
    return []


def _ask_ollama(prompt, model=None):
    """调用本地模型（只读推理，无任何副作用）"""
    try:
        r = requests.post(f'{OLLAMA_BASE}/api/generate', json={
            'model': model or OLLAMA_MODEL,
            'prompt': prompt,
            'stream': False,
        }, timeout=120)
        if r.status_code == 200:
            data = r.json()
            return data.get('response', '')
    except Exception as e:
        return f'[模型调用失败: {e}]'
    return '[模型无响应]'


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
    """健康状态：模型可用性 + 已装模型 + 平台数据源"""
    ok = _ollama_available()
    return jsonify({
        'ollama_available': ok,
        'ollama_base': OLLAMA_BASE,
        'models': _ollama_models() if ok else [],
        'default_model': OLLAMA_MODEL,
        'data_source': 'business.db (只读聚合)',
        'time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
    })


@ai_bp.route('/ask', methods=['POST'])
def ask():
    """智能问答：平台聚合数据 + 本地小模型，只读"""
    data = request.get_json(silent=True) or {}
    question = (data.get('question') or '').strip()
    if not question:
        return jsonify({'ok': False, 'msg': '问题不能为空'}), 400
    if not _ollama_available():
        return jsonify({'ok': False, 'msg': '本地模型服务未启动（Ollama），请先在部署机启动 ollama serve'}), 503

    context = _platform_context()
    prompt = (
        '你是研发管理平台的AI助手（本地部署，只读辅助，不做任何数据修改）。\n'
        f'以下是平台当前聚合数据：\n{context}\n\n'
        f'用户问题：{question}\n'
        '请基于上述数据用中文简洁回答；若数据不足以回答，请明确说明。'
    )
    answer = _ask_ollama(prompt)
    return jsonify({
        'ok': True,
        'question': question,
        'answer': answer,
        'source': '平台内网本地模型（Ollama %s）' % OLLAMA_MODEL,
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
