# -*- coding: utf-8 -*-
"""
AI驱动的业务高效运转系统 - Flask Web应用
适用于内网Windows部署，SQLite轻量数据库
"""
import os
import sqlite3
import random
from datetime import datetime, date
from flask import Flask, render_template, request, redirect, url_for, jsonify, g, session, flash, send_file
from werkzeug.utils import secure_filename
import excel_utils

app = Flask(__name__)
app.config['SECRET_KEY'] = 'business-system-2024'
app.config['DATABASE'] = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'business.db')

# 自定义tojson过滤器（兼容旧版Werkzeug的ensure_ascii参数）
import json
@app.template_filter('tojson')
def tojson_filter(value, indent=None, ensure_ascii=False):
    """自定义JSON序列化过滤器，支持ensure_ascii参数"""
    return json.dumps(value, indent=indent, ensure_ascii=ensure_ascii, default=str)

# 注册扩展模块路由蓝图
from ext_routes import ext_bp
app.register_blueprint(ext_bp)

# ============================================================
# 用户认证
# ============================================================
@app.before_request
def require_login():
    """所有页面需要登录，除了登录页和静态资源"""
    if request.endpoint in ('login', 'static'):
        return None
    if 'user_id' not in session:
        return redirect(url_for('login', next=request.path))
    # 权限校验：检查当前角色是否有权限访问该模块
    endpoint = request.endpoint or ''
    # 工作台和登录相关页面所有角色都可以访问
    if endpoint in ('index', 'logout', 'ext.my_tasks', 'ext.task_add', 'ext.task_detail', 'ext.task_update'):
        return None
    # 【权限放开】v8.5.0 起取消全部模块权限与只读限制，所有登录用户均可访问与编辑
    # 保留登录校验；gantt/模块权限/只读拦截全部放行
    return None

@app.context_processor
def inject_user():
    """所有模板可访问当前用户"""
    return {'current_user': session.get('user_name'), 'current_role': session.get('user_role')}

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        next_url = request.form.get('next', '/')
        db = get_db()
        user = db.execute('SELECT * FROM users WHERE username=? AND password=? AND is_active=1',
                         (username, password)).fetchone()
        if user:
            session['user_id'] = user['id']
            session['user_name'] = user['display_name'] or user['username']
            session['user_role'] = user['role']
            session['username'] = user['username']
            log_operation('系统', '登录', f'{user["username"]} 登录系统', user['username'])
            return redirect(next_url if next_url else '/')
        else:
            error = '用户名或密码错误'
            return render_template('login.html', error=error, next=next_url)
    return render_template('login.html', next=request.args.get('next', '/'))

@app.route('/logout')
def logout():
    username = session.get('username', 'unknown')
    log_operation('系统', '登出', f'{username} 退出系统', username)
    session.clear()
    return redirect(url_for('login'))

# ============================================================
# 角色权限管理
# ============================================================
# 权限定义：每个角色可以访问的模块（endpoint前缀）和操作权限
ROLE_PERMISSIONS = {
    'admin': {
        'name': '管理组',
        'modules': ['all'],  # 所有模块
        'readonly_modules': [],  # 没有只读模块
    },
    'product': {
        'name': '产品组',
        'modules': ['all'],  # 所有模块可见
        'readonly_modules': ['quality', 'delivery', 'ext.delivery_plan',
                            'ext.revenue', 'executive', 'ext.test_case',
                            'ext.test_execution', 'ext.ticket', 'ext.feedback',
                            'ext.device', 'ext.ota'],
    },
    'dev_test': {
        'name': '研发测试组',
        'modules': ['all'],  # 所有模块可见
        'readonly_modules': ['requirement', 'version', 'schedule', 'executive',
                            'quality', 'delivery', 'ext.delivery_plan', 'ext.revenue',
                            'ext.contract', 'ext.tech_spec', 'ext.ticket',
                            'ext.feedback', 'ext.device', 'ext.ota', 'planning'],
    },
    'quality': {
        'name': '质量组',
        'modules': ['all'],  # 所有模块可见
        'readonly_modules': ['requirement', 'version', 'production', 'delivery',
                            'executive', 'schedule', 'ext.contract', 'ext.tech_spec',
                            'ext.ticket', 'ext.feedback', 'ext.device', 'ext.ota',
                            'ext.delivery_plan', 'ext.revenue', 'planning',
                            'ext.resource'],
    },
    'delivery': {
        'name': '交付服务组',
        'modules': ['all'],  # 所有模块可见
        'readonly_modules': ['requirement', 'version', 'quality', 'executive',
                            'production', 'schedule', 'bug', 'ext.test_case',
                            'ext.test_execution', 'ext.resource', 'ext.tech_spec',
                            'planning'],
    },
}

def get_current_role():
    """获取当前用户角色"""
    return session.get('user_role', 'admin')

def _normalize_module(name):
    """规范化模块名，去掉ext.前缀"""
    if name.startswith('ext.'):
        return name[4:]
    return name

def has_module_access(module_prefix):
    """【权限放开】v8.5.0 起所有登录用户均可访问所有模块"""
    return True
    # 保留原逻辑供审计参考：
    role = get_current_role()
    perms = ROLE_PERMISSIONS.get(role, ROLE_PERMISSIONS['admin'])
    if 'all' in perms['modules']:
        return True
    # 规范化模块名，去掉ext.前缀
    module_prefix = _normalize_module(module_prefix)
    # 检查模块前缀是否匹配
    for allowed in perms['modules']:
        allowed = _normalize_module(allowed)
        if module_prefix.startswith(allowed) or allowed.startswith(module_prefix):
            return True
    return False

def is_readonly_module(module_prefix):
    """【权限放开】v8.5.0 起取消只读限制"""
    return False

def can_write(module_prefix):
    """检查当前角色是否对指定模块有写权限"""
    return has_module_access(module_prefix) and not is_readonly_module(module_prefix)

@app.context_processor
def inject_permissions():
    """向模板注入权限相关变量"""
    return {
        'current_role': get_current_role(),
        'role_name': ROLE_PERMISSIONS.get(get_current_role(), {}).get('name', '未知'),
        'has_module_access': has_module_access,
        'is_readonly_module': is_readonly_module,
        'can_write': can_write,
    }

# ============================================================
# 数据库工具
# ============================================================
def get_db():
    if 'db' not in g:
        g.db = sqlite3.connect(app.config['DATABASE'])
        g.db.row_factory = sqlite3.Row
        g.db.execute('PRAGMA foreign_keys = ON')
    return g.db

@app.teardown_appcontext
def close_db(exception):
    db = g.pop('db', None)
    if db is not None:
        db.close()

def init_db():
    """初始化数据库表结构（从schema.sql读取）"""
    schema_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'schema.sql')
    db = sqlite3.connect(app.config['DATABASE'])
    with open(schema_path, 'r', encoding='utf-8') as f:
        db.executescript(f.read())
    db.commit()
    db.close()

# ============================================================
# 输入校验工具
# ============================================================
def validate_positive_int(value, field_name, allow_zero=False):
    """校验正整数，返回(int值, 错误信息)"""
    try:
        val = int(value)
        if allow_zero:
            if val < 0:
                return None, f"{field_name}不能为负数"
        else:
            if val <= 0:
                return None, f"{field_name}必须大于0"
        return val, None
    except (ValueError, TypeError):
        return None, f"{field_name}必须是整数"

def validate_range(value, field_name, min_val, max_val):
    """校验数值范围，返回(float值, 错误信息)"""
    try:
        val = float(value)
        if val < min_val or val > max_val:
            return None, f"{field_name}必须在{min_val}~{max_val}之间"
        return val, None
    except (ValueError, TypeError):
        return None, f"{field_name}必须是数字"

def validation_error(message, back_url=None):
    """返回校验错误页面"""
    return render_template('error.html', message=message, back_url=back_url or request.referrer or '/')

def log_operation(module, action, detail, operator='系统'):
    db = get_db()
    db.execute('INSERT INTO operation_logs (module, action, detail, operator) VALUES (?,?,?,?)',
               (module, action, detail, operator))
    db.commit()

# ============================================================
# 首页 / 仪表盘
# ============================================================
@app.route('/')
def index():
    db = get_db()
    stats = {
        'requirements_total': db.execute('SELECT COUNT(*) FROM requirements WHERE is_deleted=0').fetchone()[0],
        'requirements_pending': db.execute("SELECT COUNT(*) FROM requirements WHERE status='待评审' AND is_deleted=0").fetchone()[0],
        'products_total': db.execute('SELECT COUNT(*) FROM products').fetchone()[0],
        'inventory_items': db.execute('SELECT COUNT(*) FROM inventory').fetchone()[0],
        'production_total': db.execute('SELECT COUNT(*) FROM production_orders').fetchone()[0],
        'production_active': db.execute("SELECT COUNT(*) FROM production_orders WHERE status IN ('生产中','待排产')").fetchone()[0],
        'quality_today': db.execute("SELECT COUNT(*) FROM quality_inspections WHERE inspection_date=date('now','localtime')").fetchone()[0],
        'warehouse_pending': db.execute("SELECT COUNT(*) FROM warehouse_receipts WHERE status='待审批'").fetchone()[0],
        'deliveries_month': db.execute("SELECT COUNT(*) FROM deliveries WHERE strftime('%Y-%m', delivery_date)=strftime('%Y-%m','now','localtime')").fetchone()[0],
        'versions_total': db.execute('SELECT COUNT(*) FROM software_versions').fetchone()[0],
        'versions_active': db.execute("SELECT COUNT(*) FROM software_versions WHERE status IN ('规划中','开发中','测试中')").fetchone()[0],
        'req_pending': db.execute("SELECT COUNT(*) FROM requirements WHERE status IN ('待评审','已评审','进行中') AND is_deleted=0").fetchone()[0],
        # 新模块统计
        'test_cases_total': db.execute('SELECT COUNT(*) FROM test_cases').fetchone()[0],
        'test_executions_active': db.execute("SELECT COUNT(*) FROM test_executions WHERE status='进行中'").fetchone()[0],
        'tickets_pending': db.execute("SELECT COUNT(*) FROM tech_support_tickets WHERE status IN ('待处理','处理中')").fetchone()[0],
        'feedback_pending': db.execute("SELECT COUNT(*) FROM user_feedbacks WHERE status='待评估'").fetchone()[0],
        'delivery_plans_active': db.execute("SELECT COUNT(*) FROM delivery_plans WHERE status IN ('计划中','进行中')").fetchone()[0],
        'revenue_pending': db.execute("SELECT COUNT(*) FROM revenue_confirmations WHERE status='待确认'").fetchone()[0],
        'contracts_active': db.execute("SELECT COUNT(*) FROM contracts WHERE status='执行中'").fetchone()[0],
        'resources_active': db.execute("SELECT COUNT(*) FROM resource_schedules WHERE status='已分配'").fetchone()[0],
        'devices_online': db.execute("SELECT COUNT(*) FROM devices WHERE status='在线'").fetchone()[0],
        'device_alerts': db.execute("SELECT COUNT(*) FROM device_alerts WHERE status='未处理'").fetchone()[0],
        'quality_issues_pending': db.execute("SELECT COUNT(*) FROM bug_records WHERE is_quality_issue=1 AND status NOT IN ('已关闭','已取消')").fetchone()[0],
        'dev_tasks_pending': db.execute("SELECT COUNT(*) FROM dev_tasks WHERE status NOT IN ('已完成','已取消')").fetchone()[0],
    }
    low_stock = db.execute('SELECT * FROM inventory WHERE quantity < 50 ORDER BY quantity ASC LIMIT 5').fetchall()
    bugs_open = db.execute("SELECT COUNT(*) FROM bug_records WHERE status NOT IN ('已关闭','已取消')").fetchone()[0]
    recent_logs = db.execute('SELECT * FROM operation_logs ORDER BY created_at DESC LIMIT 10').fetchall()
    # 我的待办任务
    current_user = session.get('username', 'admin')
    my_tasks = db.execute("SELECT * FROM tasks WHERE assignee=? AND status IN ('待处理','进行中') ORDER BY priority DESC, due_date ASC LIMIT 8", (current_user,)).fetchall()
    my_tasks_count = db.execute("SELECT COUNT(*) FROM tasks WHERE assignee=? AND status IN ('待处理','进行中')", (current_user,)).fetchone()[0]
    now = datetime.now()
    week_map = {0:'周一',1:'周二',2:'周三',3:'周四',4:'周五',5:'周六',6:'周日'}
    return render_template('index.html', stats=stats, low_stock=low_stock,
                           bugs_open=bugs_open, recent_logs=recent_logs,
                           my_tasks=my_tasks, my_tasks_count=my_tasks_count,
                           now_day=now.day, now_month=now.month, now_week=week_map[now.weekday()],
                           now_date=now.strftime('%Y-%m-%d'))

# ============================================================
# 总览（聚焦：需求什么时候满足？做了哪些措施？还有哪些没做？什么时候完成？）
# ============================================================
# ============================================================
# 总览（分两大视角：用户需求交付 + 版本演进约束）
# ============================================================

def calc_req_measures(r, db, now_str):
    """计算单条需求的8个关键措施完成情况"""
    rid = r['id']
    measures_done = []
    measures_pending = []
    product_name = None
    model = None

    # 措施1：需求评审
    review_count = db.execute('SELECT COUNT(*) FROM requirement_reviews WHERE requirement_id=?', (rid,)).fetchone()[0]
    if review_count > 0:
        measures_done.append(('需求评审', '已评审'))
    else:
        measures_pending.append(('需求评审', '待评审', None))

    # 措施2：版本规划
    version_info = db.execute('''
        SELECT sv.id, sv.version_no, sv.version_name, sv.planned_release_date, sv.status,
               sv.product_id, p.name as product_name, p.model
        FROM version_requirements vr
        JOIN software_versions sv ON vr.version_id = sv.id
        LEFT JOIN products p ON sv.product_id = p.id
        WHERE vr.requirement_id=?
        ORDER BY sv.planned_release_date ASC LIMIT 1
    ''', (rid,)).fetchone()
    if version_info:
        measures_done.append(('版本规划', f'纳入{version_info["version_no"]}'))
        version_id = version_info['id']
        expected_date = version_info['planned_release_date'] or r['delivery_date']
        product_name = version_info['product_name']
        model = version_info['model']
    else:
        measures_pending.append(('版本规划', '待纳入版本规划', None))
        version_id = None
        expected_date = r['delivery_date']

    # 措施3：开发完成
    if version_id:
        task_total = db.execute('SELECT COUNT(*) FROM dev_tasks WHERE version_id=?', (version_id,)).fetchone()[0]
        task_done = db.execute("SELECT COUNT(*) FROM dev_tasks WHERE version_id=? AND status='已完成'", (version_id,)).fetchone()[0]
        if task_total > 0 and task_done == task_total:
            measures_done.append(('开发完成', f'{task_done}/{task_total}任务完成'))
        elif task_total > 0:
            dev_due = db.execute("SELECT MAX(planned_finish) FROM dev_tasks WHERE version_id=? AND status!='已完成'", (version_id,)).fetchone()[0]
            measures_pending.append(('开发完成', f'{task_done}/{task_total}完成', dev_due))
        else:
            measures_pending.append(('开发完成', '待创建开发任务', None))
    else:
        measures_pending.append(('开发完成', '等待版本规划', None))

    # 措施4：测试通过
    if version_id:
        test_exec = db.execute("SELECT COUNT(*) FROM test_executions WHERE version_id=? AND status='已完成'", (version_id,)).fetchone()[0]
        open_bugs = db.execute("SELECT COUNT(*) FROM bug_records WHERE version_id=? AND severity IN ('致命','严重') AND status NOT IN ('已关闭','已取消')", (version_id,)).fetchone()[0]
        if test_exec > 0 and open_bugs == 0:
            measures_done.append(('测试通过', '测试通过，无严重Bug'))
        elif test_exec > 0:
            measures_pending.append(('测试通过', f'{open_bugs}个严重Bug待修复', None))
        else:
            measures_pending.append(('测试通过', '待执行测试', None))
    else:
        measures_pending.append(('测试通过', '等待版本规划', None))

    # 措施5：生产排程
    schedule = db.execute('SELECT * FROM production_schedules WHERE requirement_id=? ORDER BY plan_start ASC LIMIT 1', (rid,)).fetchone()
    if schedule:
        if schedule['status'] == '已完成':
            measures_done.append(('生产排程', '生产已完成'))
        else:
            measures_pending.append(('生产排程', f'{schedule["status"]}，预计{schedule["plan_finish"]}完成', schedule['plan_finish']))
    else:
        measures_pending.append(('生产排程', '待排产', None))

    # 措施6：质检通过
    if schedule and schedule['status'] == '已完成':
        measures_done.append(('质检通过', '质检合格'))
    elif schedule:
        measures_pending.append(('质检通过', '待生产完成后质检', schedule['plan_finish']))
    else:
        measures_pending.append(('质检通过', '等待生产排程', None))

    # 措施7：入库完成
    if schedule and schedule['status'] == '已完成':
        measures_done.append(('入库完成', '已入库'))
    elif schedule:
        measures_pending.append(('入库完成', '待生产完成后入库', schedule['plan_finish']))
    else:
        measures_pending.append(('入库完成', '等待生产排程', None))

    # 措施8：交付完成
    if r['status'] == '已交付':
        measures_done.append(('交付完成', '已交付客户'))
    else:
        measures_pending.append(('交付完成', '待交付客户', None))

    progress = int(len(measures_done) / 8 * 100)
    is_overdue = expected_date and expected_date < now_str and r['status'] != '已交付'
    is_high_risk = r['priority'] == '高' and progress < 50

    # 额外信息：版本进度、生产进度
    version_progress = 0
    if version_id:
        vt = db.execute('SELECT COUNT(*) FROM dev_tasks WHERE version_id=?', (version_id,)).fetchone()[0]
        vd = db.execute("SELECT COUNT(*) FROM dev_tasks WHERE version_id=? AND status='已完成'", (version_id,)).fetchone()[0]
        version_progress = int(vd / vt * 100) if vt > 0 else 0

    production_status = '未排产'
    production_finish = None
    if schedule:
        production_status = schedule['status']
        production_finish = schedule['plan_finish']

    return {
        'id': r['id'],
        'title': r['title'],
        'description': r['description'],
        'customer': r['customer'],
        'source_dept': r['source_dept'],
        'req_type': r['req_type'],
        'quantity': r['quantity'],
        'priority': r['priority'],
        'delivery_date': r['delivery_date'],
        'status': r['status'],
        'product_name': product_name,
        'model': model,
        'version_no': version_info['version_no'] if version_info else None,
        'version_id': version_id,
        'expected_date': expected_date,
        'measures_done': measures_done,
        'measures_pending': measures_pending,
        'progress': progress,
        'is_overdue': is_overdue,
        'is_high_risk': is_high_risk,
        'version_progress': version_progress,
        'production_status': production_status,
        'production_finish': production_finish,
    }


def build_journey(db):
    """需求历程全景：从来源→评审→产品→版本→任务→生产→开发→测试→验收→交付→运维
    每个阶段聚合各业务表真实数据，输出 state: done/in_progress/blocked/pending + 摘要详情"""
    journey = []
    reqs = db.execute("""
        SELECT r.*, p.id AS pid, p.name AS product_name
        FROM requirements r
        LEFT JOIN products p ON p.model = r.product_model
        WHERE r.is_deleted=0
        ORDER BY r.delivery_date, r.id
    """).fetchall()
    for r in reqs:
        # 该需求关联的版本集合
        vids = [v['version_id'] for v in db.execute(
            "SELECT version_id FROM version_requirements WHERE requirement_id=?", (r['id'],))]
        vids_sql = ','.join('?' * len(vids)) if vids else ''

        def _stages():
            st = []

            # 1 来源
            st.append({
                'key': 'source', 'name': '来源', 'state': 'done', 'icon': '✓',
                'summary': r['req_type'], 'detail': '来源部门：%s\n应用场景：%s' % (r['source_dept'] or '—', r['application_scenario'] or '—'),
                'time': (r['created_at'] or '')[:10], 'owner': '交付服务组A'})

            # 2 评审
            revs = db.execute("SELECT * FROM requirement_reviews WHERE requirement_id=? ORDER BY review_date DESC", (r['id'],)).fetchall()
            if not revs:
                st.append({'key': 'review', 'name': '评审', 'state': 'blocked', 'icon': '!',
                           'summary': '待评审', 'detail': '尚无评审记录，需尽快组织评审', 'time': '—', 'owner': '产品总体组B'})
            else:
                last = revs[0]
                ok = last['conclusion'] in ('通过', '有条件通过')
                st.append({'key': 'review', 'name': '评审', 'state': 'done' if ok else 'in_progress', 'icon': '✓' if ok else '›',
                           'summary': last['conclusion'] or '—',
                           'detail': '最新结论：%s（建议版本 %s）\n评审人：%s' % (last['conclusion'], last['version_suggested'] or '—', last['reviewer'] or '—'),
                           'time': (last['review_date'] or '')[:10], 'owner': '产品总体组B'})

            # 3 产品定义（集团约束需求通过版本反查约束产品）
            prod_name = r['product_name'] or r['product_model']
            if not prod_name and vids:
                pv = db.execute("SELECT p.name, p.model FROM software_versions sv JOIN products p ON p.id=sv.product_id WHERE sv.id IN (%s) LIMIT 1" % vids_sql, vids).fetchone()
                if pv:
                    prod_name = pv['name']
            if prod_name:
                st.append({'key': 'product', 'name': '产品', 'state': 'done', 'icon': '✓',
                           'summary': prod_name,
                           'detail': '产品型号：%s' % (r['product_model'] or prod_name), 'time': '—', 'owner': '产品总体组B'})
            else:
                st.append({'key': 'product', 'name': '产品', 'state': 'pending', 'icon': '·',
                           'summary': '未定义产品', 'detail': '缺少产品型号定义', 'time': '—', 'owner': '产品总体组B'})

            # 4 版本规划
            if vids:
                vers = db.execute("SELECT * FROM software_versions WHERE id IN (%s)" % vids_sql, vids).fetchall()
                any_pub = any(v['status'] == '已发布' for v in vers)
                any_dev = any(v['status'] in ('开发中', '测试中') for v in vers)
                st.append({'key': 'version', 'name': '版本', 'state': 'done' if any_pub else ('in_progress' if any_dev else 'pending'), 'icon': '✓' if any_pub else ('›' if any_dev else '·'),
                           'summary': '、'.join(v['version_no'] for v in vers) or '—',
                           'detail': '版本状态：\n' + '\n'.join('%s %s' % (v['version_no'], v['status']) for v in vers),
                           'time': '—', 'owner': '产品总体组B'})
            else:
                st.append({'key': 'version', 'name': '版本', 'state': 'pending', 'icon': '·',
                           'summary': '未规划版本', 'detail': '尚无版本规划', 'time': '—', 'owner': '产品总体组B'})

            # 5 计划与任务（研发任务按版本归属）
            tasks = []
            if vids:
                tasks = db.execute("SELECT * FROM dev_tasks WHERE version_id IN (%s) ORDER BY planned_finish" % vids_sql, vids).fetchall()
            done_t = sum(1 for t in tasks if t['status'] == '已完成')
            if not tasks:
                st.append({'key': 'tasks', 'name': '任务', 'state': 'pending', 'icon': '·',
                           'summary': '无任务', 'detail': '尚未设置工作计划与任务', 'time': '—', 'owner': '研发组'})
            elif done_t == len(tasks):
                st.append({'key': 'tasks', 'name': '任务', 'state': 'done', 'icon': '✓',
                           'summary': '%d/%d 完成' % (done_t, len(tasks)),
                           'detail': '任务清单：\n' + '\n'.join('%s %s' % (t['task_name'], t['status']) for t in tasks),
                           'time': '—', 'owner': '研发组'})
            elif any(t['status'] == '进行中' for t in tasks):
                st.append({'key': 'tasks', 'name': '任务', 'state': 'in_progress', 'icon': '›',
                           'summary': '%d/%d 完成' % (done_t, len(tasks)),
                           'detail': '任务清单：\n' + '\n'.join('%s %s' % (t['task_name'], t['status']) for t in tasks),
                           'time': '—', 'owner': '研发组'})
            else:
                st.append({'key': 'tasks', 'name': '任务', 'state': 'pending', 'icon': '·',
                           'summary': '%d 个未开始' % len(tasks),
                           'detail': '任务均未开始', 'time': '—', 'owner': '研发组'})

            # 6 硬件生产
            scheds = db.execute("SELECT * FROM production_schedules WHERE requirement_id=? ORDER BY plan_start", (r['id'],)).fetchall()
            done_s = sum(1 for s in scheds if s['status'] == '已完成')
            if not scheds:
                st.append({'key': 'production', 'name': '生产', 'state': 'pending', 'icon': '·',
                           'summary': '未排产', 'detail': '尚无生产排程', 'time': '—', 'owner': '产品总体组A'})
            elif done_s == len(scheds):
                st.append({'key': 'production', 'name': '生产', 'state': 'done', 'icon': '✓',
                           'summary': '%d批 全完成' % len(scheds),
                           'detail': '排程：\n' + '\n'.join('%s套 %s' % (s['quantity'], s['status']) for s in scheds),
                           'time': '—', 'owner': '产品总体组A'})
            else:
                st.append({'key': 'production', 'name': '生产', 'state': 'in_progress', 'icon': '›',
                           'summary': '%d批 · %d完成' % (len(scheds), done_s),
                           'detail': '排程：\n' + '\n'.join('%s套 %s' % (s['quantity'], s['status']) for s in scheds),
                           'time': '—', 'owner': '产品总体组A'})

            # 7 软件开发（版本发布态）
            if vids:
                vers = db.execute("SELECT * FROM software_versions WHERE id IN (%s)" % vids_sql, vids).fetchall()
                any_pub = any(v['status'] == '已发布' for v in vers)
                any_dev = any(v['status'] in ('开发中', '测试中') for v in vers)
                devs = [v for v in vers if v['status'] in ('开发中', '测试中')]
                st.append({'key': 'dev', 'name': '开发', 'state': 'done' if any_pub else ('in_progress' if any_dev else 'pending'), 'icon': '✓' if any_pub else ('›' if any_dev else '·'),
                           'summary': '版本发布' if any_pub else ('、'.join(v['version_no'] for v in devs) + ' 开发中' if devs else '待开发'),
                           'detail': '版本状态：\n' + '\n'.join('%s %s' % (v['version_no'], v['status']) for v in vers),
                           'time': '—', 'owner': '研发组'})
            else:
                st.append({'key': 'dev', 'name': '开发', 'state': 'pending', 'icon': '·',
                           'summary': '待开发', 'detail': '无版本信息', 'time': '—', 'owner': '研发组'})

            # 8 测试
            tps = []
            if vids:
                tps = db.execute("SELECT * FROM test_plans WHERE version_id IN (%s) ORDER BY planned_finish" % vids_sql, vids).fetchall()
            done_tp = sum(1 for t in tps if t['status'] == '已完成')
            if not tps:
                st.append({'key': 'test', 'name': '测试', 'state': 'pending', 'icon': '·',
                           'summary': '未测试', 'detail': '尚无测试计划', 'time': '—', 'owner': '测试组'})
            elif done_tp == len(tps):
                st.append({'key': 'test', 'name': '测试', 'state': 'done', 'icon': '✓',
                           'summary': '%d份计划完成' % len(tps),
                           'detail': '测试计划：\n' + '\n'.join('%s %s' % (t['title'], t['status']) for t in tps),
                           'time': '—', 'owner': '测试组'})
            else:
                st.append({'key': 'test', 'name': '测试', 'state': 'in_progress', 'icon': '›',
                           'summary': '%d/%d 完成' % (done_tp, len(tps)),
                           'detail': '测试计划：\n' + '\n'.join('%s %s' % (t['title'], t['status']) for t in tps),
                           'time': '—', 'owner': '测试组'})

            # 9 验收（质量：bug 关闭率）
            bugs = []
            if vids:
                bugs = db.execute("SELECT * FROM bug_records WHERE version_id IN (%s)" % vids_sql, vids).fetchall()
            closed_b = sum(1 for b in bugs if b['status'] in ('已关闭', '已修复', '已验证'))
            if not bugs:
                st.append({'key': 'accept', 'name': '验收', 'state': 'pending', 'icon': '·',
                           'summary': '无Bug', 'detail': '无质量验收数据', 'time': '—', 'owner': '质量组'})
            elif closed_b == len(bugs):
                st.append({'key': 'accept', 'name': '验收', 'state': 'done', 'icon': '✓',
                           'summary': 'Bug全关闭 %d' % closed_b,
                           'detail': 'Bug：关闭%d/%d' % (closed_b, len(bugs)), 'time': '—', 'owner': '质量组'})
            else:
                st.append({'key': 'accept', 'name': '验收', 'state': 'in_progress', 'icon': '›',
                           'summary': '关闭%d/%d' % (closed_b, len(bugs)),
                           'detail': 'Bug：\n' + '\n'.join('%s %s' % (b['bug_no'], b['status']) for b in bugs),
                           'time': '—', 'owner': '质量组'})

            # 10 交付
            dps = db.execute("SELECT * FROM delivery_plans WHERE requirement_id=? ORDER BY planned_date", (r['id'],)).fetchall()
            done_dp = sum(1 for d in dps if d['actual_date'] or d['status'] == '已完成')
            if not dps:
                st.append({'key': 'delivery', 'name': '交付', 'state': 'pending', 'icon': '·',
                           'summary': '未排交付', 'detail': '尚无交付计划', 'time': '—', 'owner': '交付服务组A'})
            elif done_dp == len(dps):
                st.append({'key': 'delivery', 'name': '交付', 'state': 'done', 'icon': '✓',
                           'summary': '%d批 全交付' % len(dps),
                           'detail': '交付计划：\n' + '\n'.join('%s套 %s(%s)' % (d['quantity'], d['planned_date'], d['status']) for d in dps),
                           'time': '—', 'owner': '交付服务组A'})
            else:
                st.append({'key': 'delivery', 'name': '交付', 'state': 'in_progress', 'icon': '›',
                           'summary': '%d批 · %d完成' % (len(dps), done_dp),
                           'detail': '交付计划：\n' + '\n'.join('%s套 %s(%s)' % (d['quantity'], d['planned_date'], d['status']) for d in dps),
                           'time': '—', 'owner': '交付服务组A'})

            # 11 运维
            devs = []
            if r['pid']:
                devs = db.execute("SELECT * FROM devices WHERE product_id=? ORDER BY id", (r['pid'],)).fetchall()
            on_n = sum(1 for d in devs if d['status'] == '在线')
            if not devs:
                st.append({'key': 'ops', 'name': '运维', 'state': 'pending', 'icon': '·',
                           'summary': '无设备', 'detail': '尚无联网设备', 'time': '—', 'owner': '交付服务组C'})
            elif on_n > 0:
                st.append({'key': 'ops', 'name': '运维', 'state': 'done', 'icon': '✓',
                           'summary': '在线%d/%d台' % (on_n, len(devs)),
                           'detail': '设备在线 %d/%d' % (on_n, len(devs)), 'time': '—', 'owner': '交付服务组C'})
            else:
                st.append({'key': 'ops', 'name': '运维', 'state': 'in_progress', 'icon': '›',
                           'summary': '设备离线 %d台' % len(devs),
                           'detail': '设备均离线，需排查', 'time': '—', 'owner': '交付服务组C'})
            return st

        journey.append({
            'req': r,
            'stages': _stages(),
            'req_type': r['req_type'],
            'done_n': sum(1 for s in _stages() if s['state'] == 'done'),
        })
    return journey


@app.route('/executive')
def executive_dashboard():
    db = get_db()
    now_str = datetime.now().strftime('%Y-%m-%d')

    # 查询所有进行中的用户需求
    customer_reqs = db.execute('''
        SELECT r.* FROM requirements r
        WHERE r.req_type='用户需求' AND r.status IN ('已评审','进行中','待交付','已交付') AND r.is_deleted=0
        ORDER BY r.priority='高' DESC, r.delivery_date ASC
    ''').fetchall()

    # 查询所有集团约束需求
    evolution_reqs = db.execute('''
        SELECT r.* FROM requirements r
        WHERE r.req_type IN ('集团系统侧需求','集团应用侧需求','其他')
          AND r.status IN ('已评审','进行中','待交付','已交付') AND r.is_deleted=0
        ORDER BY r.req_type, r.delivery_date ASC
    ''').fetchall()

    # 收集所有版本ID
    all_version_ids = set()
    req_version_map = {}
    for r in customer_reqs:
        v = db.execute('''
            SELECT sv.id FROM version_requirements vr
            JOIN software_versions sv ON vr.version_id = sv.id
            WHERE vr.requirement_id=? ORDER BY sv.planned_release_date ASC LIMIT 1
        ''', (r['id'],)).fetchone()
        if v:
            all_version_ids.add(v['id'])
            req_version_map[r['id']] = v['id']

    # 查询所有研发任务
    all_dev_tasks = []
    if all_version_ids:
        placeholders = ','.join(['?'] * len(all_version_ids))
        all_dev_tasks = db.execute(f'''
            SELECT dt.*, sv.product_id FROM dev_tasks dt
            JOIN software_versions sv ON dt.version_id = sv.id
            WHERE dt.version_id IN ({placeholders})
              AND dt.planned_start IS NOT NULL AND dt.planned_finish IS NOT NULL
            ORDER BY dt.planned_start
        ''', list(all_version_ids)).fetchall()

    # 查询所有测试任务
    all_test_tasks = []
    if all_version_ids:
        placeholders = ','.join(['?'] * len(all_version_ids))
        all_test_tasks = db.execute(f'''
            SELECT te.* FROM test_executions te
            WHERE te.version_id IN ({placeholders})
              AND te.start_date IS NOT NULL AND te.end_date IS NOT NULL
            ORDER BY te.start_date
        ''', list(all_version_ids)).fetchall()

    # 查询所有生产任务
    all_prod_tasks = db.execute('''
        SELECT ps.* FROM production_schedules ps
        WHERE ps.requirement_id IN (SELECT id FROM requirements WHERE req_type='用户需求' AND is_deleted=0)
          AND ps.plan_start IS NOT NULL AND ps.plan_finish IS NOT NULL
        ORDER BY ps.plan_start
    ''').fetchall()

    # 计算时间轴范围
    all_dates = []
    for t in all_dev_tasks:
        if t['planned_start']: all_dates.append(t['planned_start'])
        if t['planned_finish']: all_dates.append(t['planned_finish'])
    for t in all_test_tasks:
        if t['start_date']: all_dates.append(t['start_date'])
        if t['end_date']: all_dates.append(t['end_date'])
    for t in all_prod_tasks:
        if t['plan_start']: all_dates.append(t['plan_start'])
        if t['plan_finish']: all_dates.append(t['plan_finish'])
    for r in customer_reqs:
        if r['delivery_date']: all_dates.append(r['delivery_date'])

    if all_dates:
        min_date = min(all_dates)
        max_date = max(all_dates)
        from datetime import timedelta
        min_dt = datetime.strptime(min_date, '%Y-%m-%d') - timedelta(days=3)
        max_dt = datetime.strptime(max_date, '%Y-%m-%d') + timedelta(days=3)
        min_date = min_dt.strftime('%Y-%m-%d')
        max_date = max_dt.strftime('%Y-%m-%d')
        total_days = (max_dt - min_dt).days
    else:
        min_date = now_str
        max_date = now_str
        total_days = 1

    def calc_position(start_str, end_str):
        if not start_str or not end_str or total_days == 0:
            return (0, 20)
        try:
            s = datetime.strptime(start_str, '%Y-%m-%d')
            e = datetime.strptime(end_str, '%Y-%m-%d')
            m = datetime.strptime(min_date, '%Y-%m-%d')
            left = max(0, (s - m).days / total_days * 100)
            width = max(2, (e - s).days / total_days * 100)
            return (left, width)
        except:
            return (0, 20)

    def task_progress(task, task_type):
        if task_type == 'dev':
            if task['status'] == '已完成':
                return 100
            try:
                s = datetime.strptime(task['planned_start'], '%Y-%m-%d')
                e = datetime.strptime(task['planned_finish'], '%Y-%m-%d')
                now = datetime.strptime(now_str, '%Y-%m-%d')
                total = (e - s).days
                elapsed = (now - s).days
                if total <= 0: return 0
                return max(0, min(100, int(elapsed / total * 100)))
            except:
                return 0
        elif task_type == 'test':
            if task['status'] == '已完成':
                return 100
            if task['total_cases'] and task['total_cases'] > 0:
                return int(task['passed_cases'] / task['total_cases'] * 100)
            return 0
        elif task_type == 'prod':
            if task['status'] == '已完成':
                return 100
            try:
                s = datetime.strptime(task['plan_start'], '%Y-%m-%d')
                e = datetime.strptime(task['plan_finish'], '%Y-%m-%d')
                now = datetime.strptime(now_str, '%Y-%m-%d')
                total = (e - s).days
                elapsed = (now - s).days
                if total <= 0: return 0
                return max(0, min(100, int(elapsed / total * 100)))
            except:
                return 0
        return 0

    # 组装研发任务数据
    dev_task_list = []
    for t in all_dev_tasks:
        left, width = calc_position(t['planned_start'], t['planned_finish'])
        # 找到对应的需求
        req_id = None
        for rid, vid in req_version_map.items():
            if vid == t['version_id']:
                req_id = rid
                break
        dev_task_list.append({
            'id': t['id'],
            'name': t['task_name'],
            'desc': t['description'] or '',
            'start': t['planned_start'],
            'end': t['planned_finish'],
            'status': t['status'],
            'progress': task_progress(t, 'dev'),
            'left': left,
            'width': width,
            'req_id': req_id,
        })

    # 组装测试任务数据
    test_task_list = []
    for t in all_test_tasks:
        left, width = calc_position(t['start_date'], t['end_date'])
        req_id = None
        for rid, vid in req_version_map.items():
            if vid == t['version_id']:
                req_id = rid
                break
        test_task_list.append({
            'id': t['id'],
            'name': t['execution_no'] or f'第{t["round_no"]}轮测试',
            'desc': t['summary'] or f'{t["passed_cases"]}/{t["total_cases"]}用例通过',
            'start': t['start_date'],
            'end': t['end_date'],
            'status': t['status'],
            'progress': task_progress(t, 'test'),
            'left': left,
            'width': width,
            'req_id': req_id,
        })

    # 组装生产任务数据
    prod_task_list = []
    for t in all_prod_tasks:
        left, width = calc_position(t['plan_start'], t['plan_finish'])
        prod_task_list.append({
            'id': t['id'],
            'name': t['schedule_no'] or '生产排程',
            'desc': t['remark'] or f'{t["quantity"]}套生产',
            'start': t['plan_start'],
            'end': t['plan_finish'],
            'status': t['status'],
            'progress': task_progress(t, 'prod'),
            'left': left,
            'width': width,
            'req_id': t['requirement_id'],
        })

    # 任务错位布局：检测时间重叠，分配不同子行
    def assign_subrows(tasks):
        if not tasks:
            return tasks, 1
        sorted_tasks = sorted(tasks, key=lambda x: x['start'])
        rows = []
        for task in sorted_tasks:
            placed = False
            for i, row_end in enumerate(rows):
                try:
                    t_start = datetime.strptime(task['start'], '%Y-%m-%d')
                    r_end = datetime.strptime(row_end, '%Y-%m-%d')
                    if (t_start - r_end).days >= 1:
                        rows[i] = task['end']
                        task['subrow'] = i
                        placed = True
                        break
                except:
                    pass
            if not placed:
                rows.append(task['end'])
                task['subrow'] = len(rows) - 1
        return sorted_tasks, len(rows)

    dev_task_list, dev_rows = assign_subrows(dev_task_list)
    test_task_list, test_rows = assign_subrows(test_task_list)
    prod_task_list, prod_rows = assign_subrows(prod_task_list)

    # 生成时间轴刻度 - 专业甘特图样式（月份+日期）
    timeline_marks = []
    if total_days > 0:
        start_dt = datetime.strptime(min_date, '%Y-%m-%d')
        # 生成月份刻度
        current_month = None
        month_start_idx = 0
        for i in range(total_days + 1):
            d = start_dt + timedelta(days=i)
            month_key = d.strftime('%Y-%m')
            if current_month is None:
                current_month = month_key
                month_start_idx = i
            elif month_key != current_month:
                # 上一个月结束，添加月份刻度
                month_width = (i - month_start_idx) / total_days * 100
                month_label = start_dt.strftime('%Y年%m月') if month_start_idx == 0 else (start_dt + timedelta(days=month_start_idx)).strftime('%m月')
                timeline_marks.append({
                    'type': 'month',
                    'label': month_label,
                    'left': month_start_idx / total_days * 100,
                    'width': month_width,
                })
                current_month = month_key
                month_start_idx = i
        # 添加最后一个月
        month_width = (total_days - month_start_idx) / total_days * 100
        timeline_marks.append({
            'type': 'month',
            'label': (start_dt + timedelta(days=month_start_idx)).strftime('%m月'),
            'left': month_start_idx / total_days * 100,
            'width': month_width,
        })
        
        # 生成日期刻度（每周）
        for i in range(0, total_days + 1, 7):
            d = start_dt + timedelta(days=i)
            timeline_marks.append({
                'type': 'day',
                'label': d.strftime('%m-%d'),
                'left': i / total_days * 100,
            })

    # 生成每天的网格线数据
    daily_grid = []
    today_left = None
    if total_days > 0:
        start_dt = datetime.strptime(min_date, '%Y-%m-%d')
        now_dt = datetime.strptime(now_str, '%Y-%m-%d')
        for i in range(total_days + 1):
            d = start_dt + timedelta(days=i)
            left = i / total_days * 100
            is_weekend = d.weekday() >= 5  # 周六周日
            daily_grid.append({
                'date': d.strftime('%Y-%m-%d'),
                'left': left,
                'is_weekend': is_weekend,
                'is_month_start': d.day == 1,
            })
            # 计算当天的位置
            if d.strftime('%Y-%m-%d') == now_str:
                today_left = left

    # 统计指标
    cust_stats = {
        'total': len(customer_reqs),
        'in_progress': len([r for r in customer_reqs if r['status'] != '已交付']),
        'total_qty': sum(r['quantity'] or 0 for r in customer_reqs),
        'dev_count': len(dev_task_list),
        'test_count': len(test_task_list),
        'prod_count': len(prod_task_list),
    }

    # 合并所有需求（用户需求 + 集团约束）
    all_reqs = []
    for r in customer_reqs:
        all_reqs.append(r)
    for r in evolution_reqs:
        all_reqs.append(r)

    # ===== 阶段成果达成检查点 =====
    checkpoints = db.execute('''
        SELECT mc.*, r.title AS req_title, r.customer AS req_customer,
               r.quantity AS req_quantity, r.delivery_date AS req_delivery,
               r.req_type AS req_type, sv.version_no AS version_no
        FROM milestone_checkpoints mc
        LEFT JOIN requirements r ON mc.requirement_id = r.id
        LEFT JOIN software_versions sv ON mc.version_id = sv.id
        ORDER BY r.req_type DESC, mc.requirement_id, mc.sort_order, mc.id
    ''').fetchall()

    # 按需求分组
    cp_groups = {}
    for cp in checkpoints:
        cp_groups.setdefault(cp['requirement_id'], []).append(cp)

    # 需求概要（供检查点卡表头使用）
    cp_req_meta = {}
    for rid, cps in cp_groups.items():
        r0 = cps[0]
        cp_req_meta[rid] = {
            'title': r0['req_title'],
            'customer': r0['req_customer'],
            'quantity': r0['req_quantity'],
            'delivery': r0['req_delivery'],
            'req_type': r0['req_type'],
        }

    # 全局统计
    cp_stats = {
        'total': len(checkpoints),
        'done': len([c for c in checkpoints if c['status'] == '已达成']),
        'doing': len([c for c in checkpoints if c['status'] == '进行中']),
        'blocked': len([c for c in checkpoints if c['status'] == '受阻']),
        'todo': len([c for c in checkpoints if c['status'] == '未开始']),
    }
    # 按方向统计（督促各方向）
    cp_by_dir = {}
    for d in ['研发', '测试', '生产', '交付']:
        items = [c for c in checkpoints if c['direction'] == d]
        cp_by_dir[d] = {
            'total': len(items),
            'done': len([c for c in items if c['status'] == '已达成']),
            'pending': len([c for c in items if c['status'] != '已达成']),
        }

    journey = build_journey(db)
    db.close()
    return render_template('executive.html',
                           customer_reqs=customer_reqs, evolution_reqs=evolution_reqs, all_reqs=all_reqs,
                           dev_tasks=dev_task_list, test_tasks=test_task_list, prod_tasks=prod_task_list,
                           dev_rows=dev_rows, test_rows=test_rows, prod_rows=prod_rows,
                           timeline_marks=timeline_marks, min_date=min_date, max_date=max_date,
                           daily_grid=daily_grid, today_left=today_left,
                           cust_stats=cust_stats, now_str=now_str,
                           cp_groups=cp_groups, cp_req_meta=cp_req_meta, cp_stats=cp_stats, cp_by_dir=cp_by_dir,
                           journey=journey)


@app.route('/executive/checkpoints/update', methods=['POST'])
def executive_checkpoint_update():
    """更新成果检查点状态（需管理员密码，支持批量）"""
    from flask import jsonify
    data = request.get_json() or {}
    admin_pwd = data.get('admin_password', '')
    db = get_db()
    admin = db.execute("SELECT password FROM users WHERE username='admin'").fetchone()
    if not admin or admin['password'] != admin_pwd:
        db.close()
        return jsonify({'success': False, 'error': '管理员密码错误'}), 403

    updates = data.get('updates', [])
    if not isinstance(updates, list) or not updates:
        db.close()
        return jsonify({'success': False, 'error': '无更新数据'}), 400

    import datetime as _dt
    now_ts = _dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    for u in updates:
        cpid = u.get('id')
        if not cpid:
            continue
        fields = {}
        for f in ('status', 'planned_date', 'actual_date', 'blocker', 'name', 'standard', 'direction', 'owner'):
            if f in u and u[f] is not None:
                fields[f] = str(u[f])
        if not fields:
            continue
        fields['updated_at'] = now_ts
        sets = ', '.join(f"{k}=?" for k in fields)
        db.execute(f"UPDATE milestone_checkpoints SET {sets} WHERE id=?", (*fields.values(), cpid))
    db.commit()
    db.close()
    return jsonify({'success': True, 'msg': f'已更新 {len(updates)} 条检查点'})


@app.route('/api/update_gantt_task', methods=['POST'])
def api_update_gantt_task():
    """更新甘特图任务（需管理员密码验证）"""
    data = request.get_json()
    admin_pwd = data.get('admin_password', '')

    # 验证管理员密码
    db = get_db()
    admin = db.execute("SELECT password FROM users WHERE username='admin'").fetchone()
    if not admin or admin['password'] != admin_pwd:
        db.close()
        return jsonify({'success': False, 'error': '管理员密码错误'}), 403

    task_type = data.get('task_type', '')  # dev / test / prod
    task_id = data.get('task_id')
    field = data.get('field', '')  # name / start / end / status / progress
    value = data.get('value', '')

    try:
        if task_type == 'dev':
            if field == 'name':
                db.execute("UPDATE dev_tasks SET task_name=? WHERE id=?", (value, task_id))
            elif field == 'start':
                db.execute("UPDATE dev_tasks SET planned_start=? WHERE id=?", (value, task_id))
            elif field == 'end':
                db.execute("UPDATE dev_tasks SET planned_finish=? WHERE id=?", (value, task_id))
            elif field == 'status':
                db.execute("UPDATE dev_tasks SET status=? WHERE id=?", (value, task_id))
            elif field == 'desc':
                db.execute("UPDATE dev_tasks SET description=? WHERE id=?", (value, task_id))
        elif task_type == 'test':
            if field == 'name':
                db.execute("UPDATE test_executions SET execution_no=? WHERE id=?", (value, task_id))
            elif field == 'start':
                db.execute("UPDATE test_executions SET start_date=? WHERE id=?", (value, task_id))
            elif field == 'end':
                db.execute("UPDATE test_executions SET end_date=? WHERE id=?", (value, task_id))
            elif field == 'status':
                db.execute("UPDATE test_executions SET status=? WHERE id=?", (value, task_id))
            elif field == 'desc':
                db.execute("UPDATE test_executions SET summary=? WHERE id=?", (value, task_id))
        elif task_type == 'prod':
            if field == 'name':
                db.execute("UPDATE production_schedules SET schedule_no=? WHERE id=?", (value, task_id))
            elif field == 'start':
                db.execute("UPDATE production_schedules SET plan_start=? WHERE id=?", (value, task_id))
            elif field == 'end':
                db.execute("UPDATE production_schedules SET plan_finish=? WHERE id=?", (value, task_id))
            elif field == 'status':
                db.execute("UPDATE production_schedules SET status=? WHERE id=?", (value, task_id))
            elif field == 'desc':
                db.execute("UPDATE production_schedules SET remark=? WHERE id=?", (value, task_id))
        db.commit()
        db.close()
        return jsonify({'success': True})
    except Exception as e:
        db.close()
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/add_gantt_task', methods=['POST'])
def api_add_gantt_task():
    """新增甘特图任务（需管理员密码验证）"""
    data = request.get_json()
    admin_pwd = data.get('admin_password', '')

    # 验证管理员密码
    db = get_db()
    admin = db.execute("SELECT password FROM users WHERE username='admin'").fetchone()
    if not admin or admin['password'] != admin_pwd:
        db.close()
        return jsonify({'success': False, 'error': '管理员密码错误'}), 403

    task_type = data.get('task_type', '')  # dev / test / prod
    name = data.get('name', '')
    start = data.get('start', '')
    end = data.get('end', '')
    status = data.get('status', '待开始')
    desc = data.get('desc', '')

    if not name or not start or not end:
        db.close()
        return jsonify({'success': False, 'error': '任务名称、开始时间、结束时间不能为空'}), 400

    from datetime import datetime
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    try:
        if task_type == 'dev':
            db.execute("""
                INSERT INTO dev_tasks (task_name, description, status, planned_start, planned_finish, created_at, priority)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (name, desc, status, start, end, now, '中'))
        elif task_type == 'test':
            execution_no = f'TE-{datetime.now().strftime("%Y%m%d%H%M%S")}'
            db.execute("""
                INSERT INTO test_executions (execution_no, start_date, end_date, status, summary, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (execution_no, start, end, status, desc, now))
        elif task_type == 'prod':
            schedule_no = f'PS-{datetime.now().strftime("%Y%m%d%H%M%S")}{random.randint(100,999)}'
            db.execute("""
                INSERT INTO production_schedules (schedule_no, plan_start, plan_finish, status, remark, created_at, quantity)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (schedule_no, start, end, status, desc, now, 0))
        else:
            db.close()
            return jsonify({'success': False, 'error': '未知任务类型'}), 400

        db.commit()
        db.close()
        return jsonify({'success': True})
    except Exception as e:
        db.close()
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/update_requirement', methods=['POST'])
def api_update_requirement():
    """更新用户需求（需管理员密码验证）"""
    data = request.get_json()
    admin_pwd = data.get('admin_password', '')

    # 验证管理员密码
    db = get_db()
    admin = db.execute("SELECT password FROM users WHERE username='admin'").fetchone()
    if not admin or admin['password'] != admin_pwd:
        db.close()
        return jsonify({'success': False, 'error': '管理员密码错误'}), 403

    req_id = data.get('req_id')
    field = data.get('field', '')  # req_type / title / source_dept / customer / description / quantity / priority / delivery_date / status
    value = data.get('value', '')

    try:
        if field == 'req_type':
            db.execute("UPDATE requirements SET req_type=? WHERE id=?", (value, req_id))
        elif field == 'title':
            db.execute("UPDATE requirements SET title=? WHERE id=?", (value, req_id))
        elif field == 'source_dept':
            db.execute("UPDATE requirements SET source_dept=? WHERE id=?", (value, req_id))
        elif field == 'priority':
            db.execute("UPDATE requirements SET priority=? WHERE id=?", (value, req_id))
        elif field == 'customer':
            db.execute("UPDATE requirements SET customer=? WHERE id=?", (value, req_id))
        elif field == 'product_model':
            db.execute("UPDATE requirements SET product_model=? WHERE id=?", (value, req_id))
        elif field == 'description':
            db.execute("UPDATE requirements SET description=? WHERE id=?", (value, req_id))
        elif field == 'quantity':
            db.execute("UPDATE requirements SET quantity=? WHERE id=?", (value, req_id))
        elif field == 'delivery_date':
            db.execute("UPDATE requirements SET delivery_date=? WHERE id=?", (value, req_id))
        elif field == 'status':
            db.execute("UPDATE requirements SET status=? WHERE id=?", (value, req_id))
        db.commit()
        db.close()
        return jsonify({'success': True})
    except Exception as e:
        db.close()
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/executive/customer')
def executive_customer():
    """用户需求交付视角：什么时候满足？生产进度？版本进度？剩余工作？"""
    db = get_db()
    now_str = datetime.now().strftime('%Y-%m-%d')

    all_reqs = db.execute('''
        SELECT r.* FROM requirements r
        WHERE r.req_type='用户需求' AND r.status IN ('已评审','进行中','待交付','已交付') AND r.is_deleted=0
        ORDER BY r.priority='高' DESC, r.delivery_date ASC
    ''').fetchall()

    req_list = [calc_req_measures(r, db, now_str) for r in all_reqs]

    stats = {
        'total': len(req_list),
        'done': len([r for r in req_list if r['status']=='已交付']),
        'in_progress': len([r for r in req_list if r['status']!='已交付']),
        'overdue': len([r for r in req_list if r['is_overdue']]),
        'total_qty': sum(r['quantity'] or 0 for r in req_list),
        'delivered_qty': sum(r['quantity'] or 0 for r in req_list if r['status']=='已交付'),
    }

    db.close()
    return render_template('executive_customer.html', req_list=req_list, stats=stats, now_str=now_str)


@app.route('/executive/customer/<int:req_id>')
def executive_customer_detail(req_id):
    """单个用户需求的满足详情页：需求信息+约束条件+时间轴甘特图"""
    db = get_db()
    now_str = datetime.now().strftime('%Y-%m-%d')

    # 查询需求基本信息
    req = db.execute('SELECT * FROM requirements WHERE id=? AND is_deleted=0', (req_id,)).fetchone()
    if not req:
        db.close()
        return '需求不存在', 404

    req_info = calc_req_measures(req, db, now_str)

    # 查询关联的版本
    version = None
    if req_info['version_id']:
        version = db.execute('''
            SELECT sv.*, p.name as product_name, p.model
            FROM software_versions sv
            LEFT JOIN products p ON sv.product_id = p.id
            WHERE sv.id=?
        ''', (req_info['version_id'],)).fetchone()

    # 查询关联的约束需求（集团系统侧/应用侧/其他，通过同一个版本关联）
    constraints = []
    if req_info['version_id']:
        constraints = db.execute('''
            SELECT r.* FROM requirements r
            JOIN version_requirements vr ON r.id = vr.requirement_id
            WHERE vr.version_id=? AND r.req_type != '用户需求' AND r.is_deleted=0
            ORDER BY r.req_type
        ''', (req_info['version_id'],)).fetchall()

    # 查询研发任务
    dev_tasks = []
    if req_info['version_id']:
        dev_tasks = db.execute('''
            SELECT * FROM dev_tasks
            WHERE version_id=? AND planned_start IS NOT NULL AND planned_finish IS NOT NULL
            ORDER BY planned_start
        ''', (req_info['version_id'],)).fetchall()

    # 查询测试任务
    test_tasks = []
    if req_info['version_id']:
        test_tasks = db.execute('''
            SELECT * FROM test_executions
            WHERE version_id=? AND start_date IS NOT NULL AND end_date IS NOT NULL
            ORDER BY start_date
        ''', (req_info['version_id'],)).fetchall()

    # 查询生产任务
    prod_tasks = db.execute('''
        SELECT * FROM production_schedules
        WHERE requirement_id=? AND plan_start IS NOT NULL AND plan_finish IS NOT NULL
        ORDER BY plan_start
    ''', (req_id,)).fetchall()

    # 计算时间轴范围
    all_dates = []
    for t in dev_tasks:
        if t['planned_start']: all_dates.append(t['planned_start'])
        if t['planned_finish']: all_dates.append(t['planned_finish'])
    for t in test_tasks:
        if t['start_date']: all_dates.append(t['start_date'])
        if t['end_date']: all_dates.append(t['end_date'])
    for t in prod_tasks:
        if t['plan_start']: all_dates.append(t['plan_start'])
        if t['plan_finish']: all_dates.append(t['plan_finish'])
    if req['delivery_date']:
        all_dates.append(req['delivery_date'])

    if all_dates:
        min_date = min(all_dates)
        max_date = max(all_dates)
        # 扩展一点范围
        from datetime import timedelta
        min_dt = datetime.strptime(min_date, '%Y-%m-%d') - timedelta(days=3)
        max_dt = datetime.strptime(max_date, '%Y-%m-%d') + timedelta(days=3)
        min_date = min_dt.strftime('%Y-%m-%d')
        max_date = max_dt.strftime('%Y-%m-%d')
        total_days = (max_dt - min_dt).days
    else:
        min_date = now_str
        max_date = now_str
        total_days = 1

    def calc_position(start_str, end_str):
        """计算任务在时间轴上的left%和width%"""
        if not start_str or not end_str or total_days == 0:
            return (0, 20)
        try:
            s = datetime.strptime(start_str, '%Y-%m-%d')
            e = datetime.strptime(end_str, '%Y-%m-%d')
            m = datetime.strptime(min_date, '%Y-%m-%d')
            left = max(0, (s - m).days / total_days * 100)
            width = max(2, (e - s).days / total_days * 100)
            return (left, width)
        except:
            return (0, 20)

    # 计算每个任务的进度
    def task_progress(task, task_type):
        if task_type == 'dev':
            if task['status'] == '已完成':
                return 100
            # 按时间估算
            try:
                s = datetime.strptime(task['planned_start'], '%Y-%m-%d')
                e = datetime.strptime(task['planned_finish'], '%Y-%m-%d')
                now = datetime.strptime(now_str, '%Y-%m-%d')
                total = (e - s).days
                elapsed = (now - s).days
                if total <= 0:
                    return 0
                return max(0, min(100, int(elapsed / total * 100)))
            except:
                return 0
        elif task_type == 'test':
            if task['status'] == '已完成':
                return 100
            if task['total_cases'] and task['total_cases'] > 0:
                return int(task['passed_cases'] / task['total_cases'] * 100)
            return 0
        elif task_type == 'prod':
            if task['status'] == '已完成':
                return 100
            try:
                s = datetime.strptime(task['plan_start'], '%Y-%m-%d')
                e = datetime.strptime(task['plan_finish'], '%Y-%m-%d')
                now = datetime.strptime(now_str, '%Y-%m-%d')
                total = (e - s).days
                elapsed = (now - s).days
                if total <= 0:
                    return 0
                return max(0, min(100, int(elapsed / total * 100)))
            except:
                return 0
        return 0

    # 组装任务数据
    dev_task_list = []
    for t in dev_tasks:
        left, width = calc_position(t['planned_start'], t['planned_finish'])
        dev_task_list.append({
            'id': t['id'],
            'name': t['task_name'],
            'desc': t['description'] or '',
            'start': t['planned_start'],
            'end': t['planned_finish'],
            'status': t['status'],
            'progress': task_progress(t, 'dev'),
            'left': left,
            'width': width,
            'assignee': t['assignee'] or '',
        })

    test_task_list = []
    for t in test_tasks:
        left, width = calc_position(t['start_date'], t['end_date'])
        test_task_list.append({
            'id': t['id'],
            'name': t['execution_no'] or f'第{t["round_no"]}轮测试',
            'desc': t['summary'] or f'{t["passed_cases"]}/{t["total_cases"]}用例通过',
            'start': t['start_date'],
            'end': t['end_date'],
            'status': t['status'],
            'progress': task_progress(t, 'test'),
            'left': left,
            'width': width,
            'assignee': t['executor'] or '',
        })

    prod_task_list = []
    for t in prod_tasks:
        left, width = calc_position(t['plan_start'], t['plan_finish'])
        prod_task_list.append({
            'id': t['id'],
            'name': t['schedule_no'] or '生产排程',
            'desc': t['remark'] or f'{t["quantity"]}套生产',
            'start': t['plan_start'],
            'end': t['plan_finish'],
            'status': t['status'],
            'progress': task_progress(t, 'prod'),
            'left': left,
            'width': width,
            'assignee': '',
        })

    # 生成时间轴刻度（每7天一个刻度）
    timeline_marks = []
    if total_days > 0:
        start_dt = datetime.strptime(min_date, '%Y-%m-%d')
        for i in range(0, total_days + 1, 7):
            d = start_dt + timedelta(days=i)
            timeline_marks.append({
                'date': d.strftime('%m-%d'),
                'left': i / total_days * 100,
            })

    db.close()
    return render_template('executive_customer_detail.html',
                           req=req_info, version=version, constraints=constraints,
                           dev_tasks=dev_task_list, test_tasks=test_task_list, prod_tasks=prod_task_list,
                           min_date=min_date, max_date=max_date, timeline_marks=timeline_marks,
                           now_str=now_str)


@app.route('/executive/evolution')
def executive_evolution():
    """版本演进约束视角：什么时候版本具备？什么时候测试具备？剩余工作？"""
    db = get_db()
    now_str = datetime.now().strftime('%Y-%m-%d')

    all_reqs = db.execute('''
        SELECT r.* FROM requirements r
        WHERE r.req_type IN ('集团系统侧需求','集团应用侧需求','其他')
          AND r.status IN ('已评审','进行中','待交付','已交付') AND r.is_deleted=0
        ORDER BY r.req_type, r.priority='高' DESC, r.delivery_date ASC
    ''').fetchall()

    req_list = [calc_req_measures(r, db, now_str) for r in all_reqs]

    stats = {
        'total': len(req_list),
        'version_planned': len([r for r in req_list if r['version_no']]),
        'dev_done': len([r for r in req_list if r['version_progress'] == 100]),
        'test_passed': len([r for r in req_list if any(m[0]=='测试通过' for m in r['measures_done'])]),
        'overdue': len([r for r in req_list if r['is_overdue']]),
        'by_type': {},
    }
    for r in req_list:
        t = r['req_type']
        stats['by_type'][t] = stats['by_type'].get(t, 0) + 1

    db.close()
    return render_template('executive_evolution.html', req_list=req_list, stats=stats, now_str=now_str)

# ============================================================
# 模块1：需求数据库
# ============================================================
@app.route('/requirements')
def requirements_list():
    db = get_db()
    status = request.args.get('status', '')
    req_type = request.args.get('req_type', '')
    search = request.args.get('search', '').strip()
    page = max(1, int(request.args.get('page', 1)))
    per_page = 15
    offset = (page - 1) * per_page

    where = ['is_deleted=0']
    params = []
    if status:
        where.append('status=?')
        params.append(status)
    if req_type:
        where.append('req_type=?')
        params.append(req_type)
    if search:
        where.append('(title LIKE ? OR customer LIKE ? OR source_dept LIKE ? OR description LIKE ? OR product_model LIKE ?)')
        params.extend([f'%{search}%']*5)

    where_sql = (' WHERE ' + ' AND '.join(where)) if where else ''
    total = db.execute(f'SELECT COUNT(*) FROM requirements{where_sql}', params).fetchone()[0]
    items = db.execute(f'SELECT * FROM requirements{where_sql} ORDER BY created_at DESC LIMIT ? OFFSET ?',
                       params + [per_page, offset]).fetchall()
    total_pages = (total + per_page - 1) // per_page
    # 统计各类型数量
    type_stats = db.execute('''SELECT req_type, COUNT(*) as cnt FROM requirements WHERE is_deleted=0 GROUP BY req_type''').fetchall()
    type_count = {t['req_type']: t['cnt'] for t in type_stats}
    db.close()
    return render_template('requirements.html', items=items, current_status=status,
                           current_type=req_type, type_count=type_count,
                           search=search, page=page, total_pages=total_pages, total=total)

@app.route('/requirements/add', methods=['GET', 'POST'])
def requirement_add():
    if request.method == 'POST':
        # 输入校验
        qty, err = validate_positive_int(request.form.get('quantity', 0), '需求数量')
        if err:
            return validation_error(err)
        if not request.form.get('title', '').strip():
            return validation_error('需求标题不能为空')

        db = get_db()
        db.execute('''INSERT INTO requirements (title, req_type, source_dept, customer, description, quantity, priority, delivery_date,
                      product_model, batch_delivery, application_scenario,
                      system_verify_point, verify_function, performance_req, support_product,
                      certification_type, spec_requirement, industry_standard, baseline_product_type,
                      task_progress, time_plan, deliverable)
                      VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                   (request.form['title'].strip(), request.form.get('req_type','用户需求'),
                    request.form.get('source_dept',''), request.form.get('customer',''),
                    request.form.get('description',''), qty,
                    request.form.get('priority','中'), request.form.get('delivery_date',''),
                    request.form.get('product_model',''), request.form.get('batch_delivery','否'),
                    request.form.get('application_scenario',''),
                    request.form.get('system_verify_point',''), request.form.get('verify_function',''),
                    request.form.get('performance_req',''), request.form.get('support_product',''),
                    request.form.get('certification_type',''), request.form.get('spec_requirement',''),
                    request.form.get('industry_standard',''), request.form.get('baseline_product_type',''),
                    request.form.get('task_progress',''), request.form.get('time_plan',''),
                    request.form.get('deliverable','')))
        db.commit()
        log_operation('需求管理', '新增', f"新增需求[{request.form.get('req_type','用户需求')}]: {request.form['title']}")
        return redirect(url_for('requirements_list'))
    return render_template('requirement_form.html', item=None)

@app.route('/requirements/<int:req_id>/edit', methods=['GET', 'POST'])
def requirement_edit(req_id):
    db = get_db()
    item = db.execute('SELECT * FROM requirements WHERE id=? AND is_deleted=0', (req_id,)).fetchone()
    if request.method == 'POST':
        # 输入校验
        qty, err = validate_positive_int(request.form.get('quantity', 0), '需求数量')
        if err:
            return validation_error(err)
        if not request.form.get('title', '').strip():
            return validation_error('需求标题不能为空')

        db.execute('''UPDATE requirements SET title=?, req_type=?, source_dept=?, customer=?, description=?,
                      quantity=?, priority=?, status=?, delivery_date=?,
                      product_model=?, batch_delivery=?, application_scenario=?,
                      system_verify_point=?, verify_function=?, performance_req=?, support_product=?,
                      certification_type=?, spec_requirement=?, industry_standard=?, baseline_product_type=?,
                      task_progress=?, time_plan=?, deliverable=?, updated_at=datetime('now','localtime')
                      WHERE id=?''',
                   (request.form['title'].strip(), request.form.get('req_type','用户需求'),
                    request.form.get('source_dept',''), request.form.get('customer',''),
                    request.form.get('description',''), qty,
                    request.form.get('priority','中'), request.form.get('status','待评审'),
                    request.form.get('delivery_date',''),
                    request.form.get('product_model',''), request.form.get('batch_delivery','否'),
                    request.form.get('application_scenario',''),
                    request.form.get('system_verify_point',''), request.form.get('verify_function',''),
                    request.form.get('performance_req',''), request.form.get('support_product',''),
                    request.form.get('certification_type',''), request.form.get('spec_requirement',''),
                    request.form.get('industry_standard',''), request.form.get('baseline_product_type',''),
                    request.form.get('task_progress',''), request.form.get('time_plan',''),
                    request.form.get('deliverable',''), req_id))
        db.commit()
        log_operation('需求管理', '编辑', f"编辑需求[{request.form.get('req_type','用户需求')}]: {request.form['title']}")
        return redirect(url_for('requirements_list'))
    return render_template('requirement_form.html', item=item)

@app.route('/requirements/<int:req_id>/delete', methods=['POST'])
def requirement_delete(req_id):
    """软删除需求（v8.6.0）：标记 is_deleted=1，保留历史追溯，防止外键崩溃"""
    db = get_db()
    item = db.execute('SELECT id FROM requirements WHERE id=? AND is_deleted=0', (req_id,)).fetchone()
    if not item:
        db.close()
        return validation_error('需求不存在或已删除')
    db.execute('UPDATE requirements SET is_deleted=1, updated_at=datetime("now","localtime") WHERE id=?', (req_id,))
    db.commit()
    log_operation('需求管理', '删除', f'软删除需求ID: {req_id}')
    db.close()
    return redirect(url_for('requirements_list'))

@app.route('/requirements/<int:req_id>/status', methods=['POST'])
def requirement_update_status(req_id):
    """需求状态快速更新（AJAX）"""
    new_status = request.form.get('status', '')
    valid_statuses = ['待评审', '已评审', '进行中', '已完成', '已取消', '已交付']
    if new_status not in valid_statuses:
        return jsonify({'success': False, 'error': '无效的状态值'}), 400
    db = get_db()
    db.execute('''UPDATE requirements SET status=?, updated_at=datetime('now','localtime') WHERE id=?''',
               (new_status, req_id))
    db.commit()
    log_operation('需求管理', '状态更新', f'需求ID {req_id} 状态更新为 {new_status}')
    # 状态联动：当需求状态变更时，自动同步关联版本的状态
    sync_requirement_status(db, req_id, new_status)
    db.close()
    return jsonify({'success': True, 'status': new_status})

def sync_requirement_status(db, req_id, new_status):
    """状态联动：需求状态变更时同步关联版本状态"""
    try:
        # 查询关联的版本
        versions = db.execute('''
            SELECT sv.id, sv.status FROM software_versions sv
            JOIN version_requirements vr ON sv.id = vr.version_id
            WHERE vr.requirement_id = ?
        ''', (req_id,)).fetchall()
        for v in versions:
            if new_status == '已交付' and v['status'] != '已发布':
                db.execute('UPDATE software_versions SET status=? WHERE id=?', ('已发布', v['id']))
                log_operation('版本规划', '状态联动', f'版本ID {v["id"]} 因需求交付自动更新为已发布')
            elif new_status == '进行中' and v['status'] == '规划中':
                db.execute('UPDATE software_versions SET status=? WHERE id=?', ('开发中', v['id']))
                log_operation('版本规划', '状态联动', f'版本ID {v["id"]} 因需求进行中自动更新为开发中')
        db.commit()
    except Exception as e:
        db.rollback()
        app.logger.error(f'状态联动失败: {e}')

@app.route('/requirements/<int:req_id>')
def requirement_detail(req_id):
    """需求详情（端到端追溯：需求→版本→任务→Bug→测试→排程→订单→交付→收入）"""
    db = get_db()
    requirement = db.execute('SELECT * FROM requirements WHERE id=? AND is_deleted=0', (req_id,)).fetchone()
    if not requirement:
        db.close()
        return validation_error('需求不存在')
    # 1. 关联的软件版本（关联产品表获取产品名称，字段名映射为模板期望的名称）
    versions = db.execute('''
        SELECT sv.*, p.name as product_name, 
               sv.planned_release_date as planned_release,
               sv.actual_release_date as actual_release,
               sv.capabilities as core_features,
               '固件版本' as version_type,
               vr.priority as link_priority
        FROM version_requirements vr
        JOIN software_versions sv ON vr.version_id=sv.id
        LEFT JOIN products p ON sv.product_id=p.id
        WHERE vr.requirement_id=?
        ORDER BY sv.created_at DESC
    ''', (req_id,)).fetchall()
    version_ids = [v['id'] for v in versions]
    # 2. 关联的开发任务（直接关联requirement_id + 通过版本间接关联）
    tasks = db.execute('SELECT * FROM dev_tasks WHERE requirement_id=? ORDER BY priority, planned_start', (req_id,)).fetchall()
    if version_ids:
        placeholders = ','.join('?' * len(version_ids))
        version_tasks = db.execute(f'SELECT * FROM dev_tasks WHERE version_id IN ({placeholders}) AND requirement_id IS NULL ORDER BY priority, planned_start', version_ids).fetchall()
        tasks = list(tasks) + list(version_tasks)
    # 3. 关联的Bug（通过版本间接关联）
    bugs = []
    if version_ids:
        placeholders = ','.join('?' * len(version_ids))
        bugs = db.execute(f'SELECT * FROM bug_records WHERE version_id IN ({placeholders}) ORDER BY created_at DESC', version_ids).fetchall()
    # 4. 关联的测试用例（通过版本间接关联）
    test_cases = []
    if version_ids:
        placeholders = ','.join('?' * len(version_ids))
        test_cases = db.execute(f'SELECT * FROM test_cases WHERE version_id IN ({placeholders}) ORDER BY created_at DESC', version_ids).fetchall()
    # 5. 关联的生产排程
    schedules = db.execute('SELECT * FROM production_schedules WHERE requirement_id=? ORDER BY created_at DESC', (req_id,)).fetchall()
    # 6. 关联的生产订单
    orders = db.execute('SELECT * FROM production_orders WHERE requirement_id=? ORDER BY created_at DESC', (req_id,)).fetchall()
    # 7. 关联的交付记录（通过需求ID或产品间接关联）
    deliveries = []
    try:
        deliveries = db.execute('SELECT * FROM deliveries WHERE requirement_id=? ORDER BY created_at DESC', (req_id,)).fetchall()
    except Exception:
        pass
    # 8. 关联的收入确认（通过需求ID关联）
    revenues = []
    try:
        revenues = db.execute('SELECT * FROM revenue_confirmations WHERE requirement_id=? ORDER BY created_at DESC', (req_id,)).fetchall()
    except Exception:
        pass
    # 9. 关联的需求评审记录
    reviews = db.execute('SELECT * FROM requirement_reviews WHERE requirement_id=? ORDER BY created_at DESC', (req_id,)).fetchall()
    # 10. 分批交付明细
    delivery_batches = []
    try:
        delivery_batches = db.execute('SELECT * FROM delivery_batches WHERE requirement_id=? ORDER BY batch_no', (req_id,)).fetchall()
    except Exception:
        pass
    db.close()
    return render_template('requirement_detail.html', requirement=requirement,
                           versions=versions, tasks=tasks, bugs=bugs, test_cases=test_cases,
                           schedules=schedules, orders=orders, deliveries=deliveries,
                           revenues=revenues, reviews=reviews, delivery_batches=delivery_batches)

# ============================================================
# 模块2：产品规划与版本规划（合并页面，全面但简洁）
# ============================================================
@app.route('/planning')
def planning_list():
    """产品规划与版本规划合并页面"""
    db = get_db()
    # 产品列表（带统计）
    products = db.execute('''
        SELECT p.*,
               (SELECT COUNT(*) FROM software_versions sv WHERE sv.product_id = p.id) as version_count,
               (SELECT COUNT(*) FROM version_requirements vr
                JOIN software_versions sv ON vr.version_id = sv.id
                WHERE sv.product_id = p.id) as req_count,
               (SELECT COUNT(*) FROM requirements r
                WHERE r.product_model = p.model AND r.req_type = '用户需求' AND r.is_deleted=0) as user_req_count
        FROM products p ORDER BY p.created_at DESC
    ''').fetchall()
    # 版本列表（带统计，按产品分组）
    versions = db.execute('''
        SELECT sv.*, p.name as product_name, p.model,
               (SELECT COUNT(*) FROM dev_tasks dt WHERE dt.version_id = sv.id) as task_count,
               (SELECT COUNT(*) FROM dev_tasks dt WHERE dt.version_id = sv.id AND dt.status = '已完成') as task_done,
               (SELECT COUNT(*) FROM bug_records br WHERE br.version_id = sv.id) as bug_count,
               (SELECT COUNT(*) FROM version_requirements vr WHERE vr.version_id = sv.id) as req_count
        FROM software_versions sv LEFT JOIN products p ON sv.product_id=p.id
        ORDER BY sv.planned_release_date ASC
    ''').fetchall()
    # 按产品分组版本
    versions_by_product = {}
    for v in versions:
        pid = v['product_id']
        if pid not in versions_by_product:
            versions_by_product[pid] = []
        versions_by_product[pid].append(v)
    db.close()
    return render_template('planning.html', products=products, versions=versions,
                           versions_by_product=versions_by_product)

@app.route('/planning/add', methods=['GET', 'POST'])
def product_add():
    if request.method == 'POST':
        db = get_db()
        cursor = db.execute('''INSERT INTO products (name, model, version, description, lifecycle_stage, stocking_strategy)
                      VALUES (?,?,?,?,?,?)''',
                   (request.form['name'], request.form.get('model',''), request.form.get('version',''),
                    request.form.get('description',''), request.form.get('lifecycle_stage','规划中'),
                    request.form.get('stocking_strategy','不囤货，按需生产')))
        product_id = cursor.lastrowid
        # 保存BOM
        bom_names = request.form.getlist('bom_material[]')
        bom_codes = request.form.getlist('bom_code[]')
        bom_key = request.form.getlist('bom_is_key[]')
        bom_qty = request.form.getlist('bom_qty[]')
        bom_supplier = request.form.getlist('bom_supplier[]')
        for i, name in enumerate(bom_names):
            if name.strip():
                is_key = 1 if str(i) in bom_key else 0
                db.execute('''INSERT INTO bom_items (product_id, material_name, material_code, is_key_material, quantity_per_unit, supplier)
                              VALUES (?,?,?,?,?,?)''',
                           (product_id, name.strip(), bom_codes[i] if i < len(bom_codes) else '',
                            is_key, float(bom_qty[i]) if i < len(bom_qty) and bom_qty[i] else 1,
                            bom_supplier[i] if i < len(bom_supplier) else ''))
        db.commit()
        log_operation('产品规划', '新增', f"新增产品: {request.form['name']}")
        return redirect(url_for('planning_list'))
    return render_template('product_form.html', product=None, bom_items=[])

@app.route('/planning/<int:pid>/edit', methods=['GET', 'POST'])
def product_edit(pid):
    db = get_db()
    product = db.execute('SELECT * FROM products WHERE id=?', (pid,)).fetchone()
    bom_items = db.execute('SELECT * FROM bom_items WHERE product_id=?', (pid,)).fetchall()
    if request.method == 'POST':
        db.execute('''UPDATE products SET name=?, model=?, version=?, description=?,
                      lifecycle_stage=?, stocking_strategy=? WHERE id=?''',
                   (request.form['name'], request.form.get('model',''), request.form.get('version',''),
                    request.form.get('description',''), request.form.get('lifecycle_stage','规划中'),
                    request.form.get('stocking_strategy','不囤货，按需生产'), pid))
        # 重建BOM
        db.execute('DELETE FROM bom_items WHERE product_id=?', (pid,))
        bom_names = request.form.getlist('bom_material[]')
        bom_codes = request.form.getlist('bom_code[]')
        bom_key = request.form.getlist('bom_is_key[]')
        bom_qty = request.form.getlist('bom_qty[]')
        bom_supplier = request.form.getlist('bom_supplier[]')
        for i, name in enumerate(bom_names):
            if name.strip():
                is_key = 1 if str(i) in bom_key else 0
                db.execute('''INSERT INTO bom_items (product_id, material_name, material_code, is_key_material, quantity_per_unit, supplier)
                              VALUES (?,?,?,?,?,?)''',
                           (pid, name.strip(), bom_codes[i] if i < len(bom_codes) else '',
                            is_key, float(bom_qty[i]) if i < len(bom_qty) and bom_qty[i] else 1,
                            bom_supplier[i] if i < len(bom_supplier) else ''))
        db.commit()
        log_operation('产品规划', '编辑', f"编辑产品: {request.form['name']}")
        return redirect(url_for('planning_list'))
    return render_template('product_form.html', product=product, bom_items=bom_items)

@app.route('/planning/<int:pid>/delete', methods=['POST'])
def product_delete(pid):
    db = get_db()
    db.execute('DELETE FROM products WHERE id=?', (pid,))
    db.commit()
    log_operation('产品规划', '删除', f'删除产品ID: {pid}')
    return redirect(url_for('planning_list'))

# ============================================================
# 模块3：研发生产（库存+领用+生产订单）
# ============================================================
@app.route('/production')
def production_dashboard():
    """生产管理看板（研发生产 + 生产排程合并，偏向成果展示）"""
    db = get_db()
    # 生产排程（批次）列表，含工序/物料统计
    schedules = db.execute('''SELECT ps.*, p.name as product_name, p.model, r.title as req_title,
                              (SELECT COUNT(*) FROM production_processes pp WHERE pp.schedule_id = ps.id) as process_count,
                              (SELECT COUNT(*) FROM production_processes pp WHERE pp.schedule_id = ps.id AND pp.status = '已完成') as process_done,
                              (SELECT COUNT(*) FROM material_plans mp WHERE mp.schedule_id = ps.id) as material_count,
                              (SELECT COUNT(*) FROM material_plans mp WHERE mp.schedule_id = ps.id AND mp.status = '待采购') as shortage_count
                              FROM production_schedules ps
                              LEFT JOIN products p ON ps.product_id=p.id
                              LEFT JOIN requirements r ON ps.requirement_id=r.id
                              ORDER BY ps.plan_start''').fetchall()
    # 每个排程的工序明细
    schedule_processes = {}
    for s in schedules:
        procs = db.execute('SELECT * FROM production_processes WHERE schedule_id=? ORDER BY sequence', (s['id'],)).fetchall()
        schedule_processes[s['id']] = procs
    # 生产订单
    orders = db.execute('''SELECT po.*, p.name as product_name, p.model, r.title as req_title, ps.schedule_no
                           FROM production_orders po
                           LEFT JOIN products p ON po.product_id=p.id
                           LEFT JOIN requirements r ON po.requirement_id=r.id
                           LEFT JOIN production_schedules ps ON po.schedule_id=ps.id
                           ORDER BY po.plan_start DESC''').fetchall()
    # 库存
    inventory = db.execute('SELECT * FROM inventory ORDER BY is_key_material DESC, material_name').fetchall()
    # 物料领用
    requisitions = db.execute('''SELECT mr.*, p.name as product_name, r.title as req_title
                                 FROM material_requisitions mr
                                 LEFT JOIN products p ON mr.product_id=p.id
                                 LEFT JOIN requirements r ON mr.requirement_id=r.id
                                 ORDER BY mr.created_at DESC''').fetchall()
    # 回货检测批次
    test_batches = db.execute('''SELECT tb.*, ps.schedule_no
                                  FROM test_batches tb
                                  LEFT JOIN production_schedules ps ON tb.schedule_id=ps.id
                                  ORDER BY tb.created_at DESC''').fetchall()
    # 统计聚合
    order_active = [o for o in orders if o['status'] in ('待排产', '生产中')]
    pending_delivery = sum(s['quantity'] for s in schedules if s['status'] != '已完成')
    process_total = sum(s['process_count'] for s in schedules)
    process_done = sum(s['process_done'] for s in schedules)
    stats = {
        'schedule_total': len(schedules),
        'schedule_active': sum(1 for s in schedules if s['status'] == '进行中'),
        'schedule_done': sum(1 for s in schedules if s['status'] == '已完成'),
        'order_total': len(orders),
        'order_active': len(order_active),
        'pending_delivery': pending_delivery,
        'process_total': process_total,
        'process_done': process_done,
        'shortage_total': sum(s['shortage_count'] for s in schedules),
        'pending_requisitions': sum(1 for r in requisitions if r['status'] == '待审批'),
        'test_batch_total': len(test_batches),
        'test_total': sum(b['total_count'] for b in test_batches),
        'test_pass': sum(b['pass_count'] for b in test_batches),
    }
    return render_template('production.html', schedules=schedules,
                           schedule_processes=schedule_processes,
                           orders=orders, inventory=inventory,
                           requisitions=requisitions, test_batches=test_batches,
                           stats=stats)

# --- 回货检测（生产第6步：回货测试→测试报告→良品情况） ---
@app.route('/production/tests/<int:tid>')
def test_batch_detail(tid):
    """检测批次详情看板（成果展示：统计卡+规格判定+分布图+工站分析+明细）"""
    db = get_db()
    batch = db.execute('''SELECT tb.*, ps.schedule_no, ps.plan_finish
                          FROM test_batches tb
                          LEFT JOIN production_schedules ps ON tb.schedule_id=ps.id
                          WHERE tb.id=?''', (tid,)).fetchone()
    if not batch:
        return '检测批次不存在', 404
    records = db.execute('SELECT * FROM test_records WHERE batch_id=? ORDER BY sn', (tid,)).fetchall()
    spec = json.loads(batch['spec']) if batch['spec'] else {}
    # 统计计算
    rows = [dict(r) for r in records]
    def agg(key, fn):
        vals = [r[key] for r in rows if r[key] is not None]
        return fn(vals) if vals else None
    stats = {
        'total': batch['total_count'],
        'pass': batch['pass_count'],
        'fail': batch['fail_count'],
        'pass_rate': round(batch['pass_count'] * 100.0 / batch['total_count'], 1) if batch['total_count'] else 0,
        'call_power_avg': round(agg('call_power', lambda v: sum(v)/len(v)), 1),
        'standby_power_avg': round(agg('standby_power', lambda v: sum(v)/len(v)), 1),
        'afc_avg': round(agg('afc', lambda v: sum(v)/len(v)), 1),
        'evm_max': agg('evm_9', lambda v: round(max(v), 4)),
        'freq_min': agg('freq_47', lambda v: min(v)),
        'freq_max': agg('freq_47', lambda v: max(v)),
        'pwr_max': agg('pwr_47', lambda v: round(max(v), 2)),
        'station_avg': round(agg('station_total_sec', lambda v: sum(v)/len(v)), 1),
    }
    # 各通道均值（用于趋势图）
    channel_means = {}
    for ch in ('9', '29', '47'):
        evm_key, freq_key, pwr_key = f'evm_{ch}', f'freq_{ch}', f'pwr_{ch}'
        evm_vals = [r[evm_key] for r in rows if r[evm_key] is not None]
        freq_vals = [r[freq_key] for r in rows if r[freq_key] is not None]
        pwr_vals = [r[pwr_key] for r in rows if r[pwr_key] is not None]
        channel_means[ch] = {
            'evm': round(sum(evm_vals)/len(evm_vals), 4) if evm_vals else None,
            'freq': round(sum(freq_vals)/len(freq_vals), 1) if freq_vals else None,
            'pwr': round(sum(pwr_vals)/len(pwr_vals), 2) if pwr_vals else None,
        }
    # 工站用时分析（8工站均值 + 瓶颈）
    station_names = ['工站1', '工站2', '工站3', '工站4', '工站5', '工站6', '工站7', '工站8']
    station_means = [0.0] * 8
    for r in rows:
        try:
            sd = json.loads(r.get('station_data') or '[]')
        except Exception:
            sd = []
        for i, v in enumerate(sd[:8]):
            station_means[i] += v if v else 0.0
    n_st = max(1, len(rows))
    station_means = [round(v / n_st, 1) for v in station_means]
    bottleneck = max(range(8), key=lambda i: station_means[i]) if any(station_means) else -1
    stats['station_means'] = station_means
    stats['station_names'] = station_names
    stats['bottleneck'] = bottleneck
    stats['bottleneck_name'] = station_names[bottleneck] if bottleneck >= 0 else '-'
    stats['bottleneck_sec'] = station_means[bottleneck] if bottleneck >= 0 else 0
    stats['bottleneck_pct'] = round(station_means[bottleneck] * 100.0 / (stats['station_avg'] or 1), 1) if bottleneck >= 0 else 0
    return render_template('test_batch_detail.html', batch=batch, records=rows,
                           spec=spec, stats=stats, channel_means=channel_means)

@app.route('/production/tests/<int:tid>/export')
def test_batch_export(tid):
    """导出检测批次明细Excel"""
    db = get_db()
    batch = db.execute('SELECT * FROM test_batches WHERE id=?', (tid,)).fetchone()
    if not batch:
        return '检测批次不存在', 404
    records = db.execute('SELECT * FROM test_records WHERE batch_id=? ORDER BY sn', (tid,)).fetchall()
    import io
    try:
        from openpyxl import Workbook
        wb = Workbook()
        ws = wb.active
        ws.title = '回货检测明细'
        ws.append(['SN编号', '判定', 'AFC校准值', '通话功耗(mW)', '待机功耗(mW)',
                   'EVM_9(%)', 'EVM_29(%)', 'EVM_47(%)',
                   '频偏_9(Hz)', '频偏_29(Hz)', '频偏_47(Hz)',
                   '功率_9(dBm)', '功率_29(dBm)', '功率_47(dBm)',
                   'ACLR1最差(dBc)', 'ACLR2最差(dBc)', 'ACLR3最差(dBc)', 'ACLR4最差(dBc)',
                   '工站总用时(s)'])
        for r in records:
            ws.append([r['sn'], r['status'], r['afc'], r['call_power'], r['standby_power'],
                       r['evm_9'], r['evm_29'], r['evm_47'],
                       r['freq_9'], r['freq_29'], r['freq_47'],
                       r['pwr_9'], r['pwr_29'], r['pwr_47'],
                       r['aclr1_worst'], r['aclr2_worst'], r['aclr3_worst'], r['aclr4_worst'],
                       r['station_total_sec']])
        buf = io.BytesIO()
        wb.save(buf)
        buf.seek(0)
        resp = Response(buf.getvalue(), mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        resp.headers['Content-Disposition'] = f"attachment; filename=test_batch_{tid}.xlsx"
        return resp
    except Exception:
        return '导出失败：服务端缺少openpyxl（便携版内置）', 500

# --- 库存管理 ---
@app.route('/production/inventory/add', methods=['GET', 'POST'])
def inventory_add():
    if request.method == 'POST':
        # 输入校验
        qty, err = validate_positive_int(request.form.get('quantity', 0), '库存数量', allow_zero=True)
        if err:
            return validation_error(err)
        if not request.form.get('material_name', '').strip():
            return validation_error('物料名称不能为空')

        db = get_db()
        db.execute('''INSERT INTO inventory (material_name, material_code, is_key_material, location, quantity, unit, status, remark)
                      VALUES (?,?,?,?,?,?,?,?)''',
                   (request.form['material_name'].strip(), request.form.get('material_code',''),
                    1 if request.form.get('is_key_material') else 0,
                    request.form.get('location','公司库房'), qty,
                    request.form.get('unit','个'), request.form.get('status','正常'),
                    request.form.get('remark','')))
        db.commit()
        log_operation('研发生产', '入库', f"新增库存: {request.form['material_name']} x{qty}")
        return redirect(url_for('production_dashboard', tab='inventory'))
    return render_template('inventory_form.html', item=None)

@app.route('/production/inventory/<int:iid>/edit', methods=['GET', 'POST'])
def inventory_edit(iid):
    db = get_db()
    item = db.execute('SELECT * FROM inventory WHERE id=?', (iid,)).fetchone()
    if request.method == 'POST':
        # 输入校验
        qty, err = validate_positive_int(request.form.get('quantity', 0), '库存数量', allow_zero=True)
        if err:
            return validation_error(err)
        if not request.form.get('material_name', '').strip():
            return validation_error('物料名称不能为空')

        db.execute('''UPDATE inventory SET material_name=?, material_code=?, is_key_material=?,
                      location=?, quantity=?, unit=?, status=?, remark=?, updated_at=datetime('now','localtime')
                      WHERE id=?''',
                   (request.form['material_name'].strip(), request.form.get('material_code',''),
                    1 if request.form.get('is_key_material') else 0,
                    request.form.get('location','公司库房'), qty,
                    request.form.get('unit','个'), request.form.get('status','正常'),
                    request.form.get('remark',''), iid))
        db.commit()
        log_operation('研发生产', '更新', f"更新库存: {request.form['material_name']}")
        return redirect(url_for('production_dashboard', tab='inventory'))
    return render_template('inventory_form.html', item=item)

# --- 物料领用 ---
@app.route('/production/requisition/add', methods=['GET', 'POST'])
def requisition_add():
    db = get_db()
    products = db.execute('SELECT id, name, model FROM products').fetchall()
    requirements = db.execute('SELECT id, title FROM requirements WHERE status IN ("已评审","进行中") AND is_deleted=0').fetchall()
    inventory = db.execute('''
        SELECT material_name, 
               SUM(quantity) as total_quantity, 
               MAX(is_key_material) as is_key_material,
               MAX(unit) as unit,
               GROUP_CONCAT(DISTINCT location) as locations
        FROM inventory 
        WHERE quantity > 0 
        GROUP BY material_name 
        ORDER BY is_key_material DESC, material_name
    ''').fetchall()
    # 查询待确认/待采购的物料需求计划，用于关联
    material_plans = db.execute('''
        SELECT mp.*, ps.schedule_no, p.name as product_name
        FROM material_plans mp
        LEFT JOIN production_schedules ps ON mp.schedule_id=ps.id
        LEFT JOIN products p ON ps.product_id=p.id
        WHERE mp.status IN ("待确认","待采购")
        ORDER BY mp.is_key_material DESC, mp.material_name
    ''').fetchall()
    if request.method == 'POST':
        # 输入校验
        total_qty, err = validate_positive_int(request.form.get('total_quantity', 0), '领用总数量', allow_zero=True)
        if err:
            return validation_error(err)

        req_no = f"RL{datetime.now().strftime('%Y%m%d%H%M%S')}"
        cursor = db.execute('''INSERT INTO material_requisitions (requisition_no, product_id, requirement_id, applicant, status, total_quantity)
                      VALUES (?,?,?,?,?,?)''',
                   (req_no, request.form.get('product_id') or None,
                    request.form.get('requirement_id') or None,
                    request.form.get('applicant',''), '待审批', total_qty))
        req_id = cursor.lastrowid
        mat_names = request.form.getlist('item_material[]')
        mat_qtys = request.form.getlist('item_quantity[]')
        mat_plan_ids = request.form.getlist('item_material_plan[]')
        for i, name in enumerate(mat_names):
            if name.strip() and i < len(mat_qtys) and mat_qtys[i]:
                item_qty, item_err = validate_positive_int(mat_qtys[i], f'物料[{name}]领用数量')
                if item_err:
                    db.rollback()
                    return validation_error(item_err)
                mp_id = mat_plan_ids[i] if i < len(mat_plan_ids) and mat_plan_ids[i] else None
                mp_id = int(mp_id) if mp_id else None
                db.execute('''INSERT INTO requisition_items (requisition_id, material_plan_id, material_name, quantity)
                              VALUES (?,?,?,?)''', (req_id, mp_id, name.strip(), item_qty))
        db.commit()
        log_operation('研发生产', '领用申请', f"创建领用单: {req_no}")
        return redirect(url_for('production_dashboard', tab='requisitions'))
    return render_template('requisition_form.html', products=products, requirements=requirements, inventory=inventory, material_plans=material_plans)

@app.route('/production/requisition/<int:rid>/approve', methods=['POST'])
def requisition_approve(rid):
    db = get_db()
    req = db.execute('SELECT * FROM material_requisitions WHERE id=?', (rid,)).fetchone()
    if req:
        # 库存校验：检查每个物料的库存是否足够
        items = db.execute('SELECT * FROM requisition_items WHERE requisition_id=?', (rid,)).fetchall()
        insufficient = []
        for item in items:
            inv = db.execute('SELECT quantity FROM inventory WHERE material_name=?', (item['material_name'],)).fetchone()
            current_qty = inv['quantity'] if inv else 0
            if current_qty < item['quantity']:
                insufficient.append(f"{item['material_name']}(库存{current_qty},需{item['quantity']})")
        if insufficient:
            flash('库存不足，无法审批：' + '; '.join(insufficient), 'error')
            return redirect(url_for('production_dashboard', tab='requisitions'))
        # 使用事务扣减库存并更新状态
        try:
            for item in items:
                db.execute('UPDATE inventory SET quantity = quantity - ? WHERE material_name=? AND quantity >= ?',
                           (item['quantity'], item['material_name'], item['quantity']))
                # 打通物料与领用：如果关联了物料需求计划，更新已领用数量和状态
                if item['material_plan_id']:
                    mp = db.execute('SELECT quantity_needed, quantity_available, status FROM material_plans WHERE id=?', (item['material_plan_id'],)).fetchone()
                    if mp:
                        # 计算已领用数量（需要累加所有已审批的领用单中该物料计划的数量）
                        used_qty = db.execute('''
                            SELECT SUM(ri.quantity) as total
                            FROM requisition_items ri
                            JOIN material_requisitions mr ON ri.requisition_id=mr.id
                            WHERE ri.material_plan_id=? AND mr.status='已审批'
                        ''', (item['material_plan_id'],)).fetchone()
                        total_used = used_qty['total'] if used_qty and used_qty['total'] else 0
                        # 更新物料需求计划的状态
                        new_status = mp['status']
                        if total_used >= mp['quantity_needed']:
                            new_status = '已满足'
                        elif total_used > 0:
                            new_status = '部分满足'
                        db.execute('UPDATE material_plans SET status=? WHERE id=?', (new_status, item['material_plan_id']))
            db.execute('''UPDATE material_requisitions SET status=?, approved_at=datetime('now','localtime') WHERE id=?''',
                       ('已审批', rid))
            db.commit()
            log_operation('研发生产', '审批通过', f"领用单 {req['requisition_no']} 已审批，物料已扣减，物料需求计划状态已更新")
            flash('领用单审批通过，物料已扣减，关联的物料需求计划状态已自动更新', 'success')
        except Exception as e:
            db.rollback()
            flash('审批失败：' + str(e), 'error')
    return redirect(url_for('production_dashboard', tab='requisitions'))

# --- 生产订单 ---
@app.route('/production/order/add', methods=['GET', 'POST'])
def production_order_add():
    db = get_db()
    products = db.execute('SELECT id, name, model FROM products').fetchall()
    requirements = db.execute('SELECT id, title FROM requirements WHERE is_deleted=0').fetchall()
    schedules = db.execute('SELECT id, schedule_no, product_id FROM production_schedules WHERE status IN ("规划中","已确认")').fetchall()
    if request.method == 'POST':
        # 输入校验
        qty, err = validate_positive_int(request.form.get('quantity', 0), '生产数量')
        if err:
            return validation_error(err)

        schedule_id = request.form.get('schedule_id')
        schedule_id = int(schedule_id) if schedule_id else None
        # 如果选择了排程，自动填充产品和需求
        if schedule_id:
            sched = db.execute('SELECT product_id, requirement_id, quantity, plan_start, plan_finish FROM production_schedules WHERE id=?', (schedule_id,)).fetchone()
            if sched:
                if not request.form.get('product_id'):
                    product_id = sched['product_id']
                else:
                    product_id = request.form.get('product_id') or None
                if not request.form.get('requirement_id'):
                    requirement_id = sched['requirement_id']
                else:
                    requirement_id = request.form.get('requirement_id') or None
                if not qty:
                    qty = sched['quantity']
                if not request.form.get('plan_start'):
                    plan_start = sched['plan_start']
                else:
                    plan_start = request.form.get('plan_start','')
                if not request.form.get('plan_finish'):
                    plan_finish = sched['plan_finish']
                else:
                    plan_finish = request.form.get('plan_finish','')
            else:
                product_id = request.form.get('product_id') or None
                requirement_id = request.form.get('requirement_id') or None
                plan_start = request.form.get('plan_start','')
                plan_finish = request.form.get('plan_finish','')
        else:
            product_id = request.form.get('product_id') or None
            requirement_id = request.form.get('requirement_id') or None
            plan_start = request.form.get('plan_start','')
            plan_finish = request.form.get('plan_finish','')

        order_no = f"PO{datetime.now().strftime('%Y%m%d%H%M%S')}"
        db.execute('''INSERT INTO production_orders (order_no, product_id, requirement_id, schedule_id, supplier, quantity,
                      plan_start, plan_finish, status, remark)
                      VALUES (?,?,?,?,?,?,?,?,?,?)''',
                   (order_no, product_id, requirement_id, schedule_id,
                    request.form.get('supplier',''), qty,
                    plan_start, plan_finish,
                    request.form.get('status','待排产'), request.form.get('remark','')))
        db.commit()
        log_operation('研发生产', '新建订单', f"创建生产订单: {order_no}" + (f"（来自排程#{schedule_id}）" if schedule_id else ""))
        return redirect(url_for('production_dashboard', tab='orders'))
    return render_template('production_order_form.html', products=products, requirements=requirements, schedules=schedules)

@app.route('/production/order/<int:oid>/update', methods=['POST'])
def production_order_update(oid):
    db = get_db()
    db.execute('''UPDATE production_orders SET status=?, wip_progress=?, actual_finish=? WHERE id=?''',
               (request.form.get('status','待排产'), int(request.form.get('wip_progress',0)),
                request.form.get('actual_finish') or None, oid))
    db.commit()
    log_operation('研发生产', '更新订单', f"生产订单ID {oid} 状态更新为 {request.form.get('status')}")
    return redirect(url_for('production_dashboard', tab='orders'))

# ============================================================
# 模块4：质量检测
# ============================================================
@app.route('/quality')
def quality_dashboard():
    db = get_db()
    tab = request.args.get('tab', 'inspections')
    if tab == 'defects':
        defects = db.execute('''SELECT dh.*, qi.inspection_no, qi.pass_rate, p.name as product_name
                                 FROM defect_handling dh
                                 LEFT JOIN quality_inspections qi ON dh.inspection_id=qi.id
                                 LEFT JOIN products p ON qi.product_id=p.id
                                 ORDER BY dh.created_at DESC''').fetchall()
        return render_template('quality.html', tab=tab, defects=defects)
    else:
        inspections = db.execute('''SELECT qi.*, p.name as product_name, po.order_no
                                     FROM quality_inspections qi
                                     LEFT JOIN products p ON qi.product_id=p.id
                                     LEFT JOIN production_orders po ON qi.production_order_id=po.id
                                     ORDER BY qi.created_at DESC''').fetchall()
        # 统计
        stats = {
            'total': len(inspections),
            'pass': db.execute("SELECT COUNT(*) FROM quality_inspections WHERE result='合格'").fetchone()[0],
            'fail': db.execute("SELECT COUNT(*) FROM quality_inspections WHERE result='不合格'").fetchone()[0],
            'avg_rate': db.execute('SELECT AVG(pass_rate) FROM quality_inspections WHERE pass_rate IS NOT NULL').fetchone()[0] or 0,
        }
        return render_template('quality.html', tab=tab, inspections=inspections, stats=stats)

@app.route('/quality/add', methods=['GET', 'POST'])
def quality_add():
    db = get_db()
    products = db.execute('SELECT id, name, model FROM products').fetchall()
    orders = db.execute("SELECT id, order_no FROM production_orders WHERE status IN ('生产中','已完成')").fetchall()
    if request.method == 'POST':
        # 输入校验
        sample, err = validate_positive_int(request.form.get('sample_quantity', 0), '抽样数量')
        if err:
            return validation_error(err)
        passed, err = validate_positive_int(request.form.get('pass_quantity', 0), '合格数量', allow_zero=True)
        if err:
            return validation_error(err)
        if passed > sample:
            return validation_error(f'合格数量({passed})不能大于抽样数量({sample})')

        insp_no = f"QI{datetime.now().strftime('%Y%m%d%H%M%S')}"
        failed = sample - passed
        rate = round(passed / sample * 100, 2) if sample > 0 else 0
        result = '合格' if rate >= 95 else '不合格'
        db.execute('''INSERT INTO quality_inspections (inspection_no, inspection_type, production_order_id, product_id,
                      inspector, sample_quantity, pass_quantity, fail_quantity, pass_rate, result,
                      defect_description, inspection_date)
                      VALUES (?,?,?,?,?,?,?,?,?,?,?,?)''',
                   (insp_no, request.form.get('inspection_type','OQC'),
                    request.form.get('production_order_id') or None,
                    request.form.get('product_id') or None,
                    request.form.get('inspector',''), sample, passed, failed, rate, result,
                    request.form.get('defect_description',''),
                    request.form.get('inspection_date', date.today().isoformat())))
        db.commit()
        log_operation('质量检测', '新增检验', f"检验单 {insp_no} 结果: {result}, 良品率: {rate}%")
        return redirect(url_for('quality_dashboard'))
    return render_template('quality_form.html', products=products, orders=orders)

@app.route('/quality/defect/<int:did>/close', methods=['POST'])
def defect_close(did):
    db = get_db()
    db.execute('''UPDATE defect_handling SET status=?, handling_method=?, root_cause=?,
                  closed_at=datetime('now','localtime') WHERE id=?''',
               ('已关闭', request.form.get('handling_method',''),
                request.form.get('root_cause',''), did))
    db.commit()
    log_operation('质量检测', '关闭缺陷', f"缺陷处理ID {did} 已关闭")
    return redirect(url_for('quality_dashboard', tab='defects'))

# ============================================================
# 模块5：验证收益（入库+交付）
# ============================================================
@app.route('/delivery')
def delivery_dashboard():
    db = get_db()
    tab = request.args.get('tab', 'receipts')
    if tab == 'deliveries':
        deliveries = db.execute('''SELECT d.*, p.name as product_name, p.model
                                    FROM deliveries d
                                    LEFT JOIN products p ON d.product_id=p.id
                                    ORDER BY d.created_at DESC''').fetchall()
        return render_template('delivery.html', tab=tab, deliveries=deliveries)
    else:
        receipts = db.execute('''SELECT wr.*, p.name as product_name, po.order_no
                                  FROM warehouse_receipts wr
                                  LEFT JOIN products p ON wr.product_id=p.id
                                  LEFT JOIN production_orders po ON wr.production_order_id=po.id
                                  ORDER BY wr.created_at DESC''').fetchall()
        return render_template('delivery.html', tab=tab, receipts=receipts)

@app.route('/delivery/receipt/add', methods=['GET', 'POST'])
def receipt_add():
    db = get_db()
    products = db.execute('SELECT id, name, model FROM products').fetchall()
    orders = db.execute("SELECT id, order_no FROM production_orders WHERE status='已完成'").fetchall()
    if request.method == 'POST':
        # 输入校验
        qty, err = validate_positive_int(request.form.get('quantity', 0), '入库数量')
        if err:
            return validation_error(err)

        receipt_no = f"WR{datetime.now().strftime('%Y%m%d%H%M%S')}"
        db.execute('''INSERT INTO warehouse_receipts (receipt_no, production_order_id, product_id, quantity,
                      quality_status, approver, status)
                      VALUES (?,?,?,?,?,?,?)''',
                   (receipt_no, request.form.get('production_order_id') or None,
                    request.form.get('product_id') or None,
                    qty, request.form.get('quality_status','合格'),
                    request.form.get('approver',''), '待审批'))
        db.commit()
        log_operation('验证收益', '入库申请', f"入库单 {receipt_no} 已创建")
        return redirect(url_for('delivery_dashboard', tab='receipts'))
    return render_template('receipt_form.html', products=products, orders=orders)

@app.route('/delivery/receipt/<int:rid>/approve', methods=['POST'])
def receipt_approve(rid):
    db = get_db()
    receipt = db.execute('SELECT * FROM warehouse_receipts WHERE id=?', (rid,)).fetchone()
    if receipt:
        db.execute('''UPDATE warehouse_receipts SET status=?, approved_at=datetime('now','localtime') WHERE id=?''',
                   ('已入库', rid))
        # 更新生产订单状态
        if receipt['production_order_id']:
            db.execute("UPDATE production_orders SET status='已入库' WHERE id=?", (receipt['production_order_id'],))
        # 打通入库与库存：入库审批通过后自动增加成品库存
        if receipt['product_id'] and receipt['quantity'] > 0:
            product = db.execute('SELECT name, model FROM products WHERE id=?', (receipt['product_id'],)).fetchone()
            if product:
                product_name = f"{product['name']}（{product['model']}）" if product['model'] else product['name']
                # 检查是否已有该成品的库存记录
                inv = db.execute('SELECT id, quantity FROM inventory WHERE product_id=? AND is_product=1', (receipt['product_id'],)).fetchone()
                if inv:
                    db.execute('UPDATE inventory SET quantity=quantity+?, updated_at=datetime("now","localtime") WHERE id=?',
                               (receipt['quantity'], inv['id']))
                else:
                    db.execute('''INSERT INTO inventory (material_name, product_id, is_product, is_key_material, location, quantity, unit, status, remark)
                                  VALUES (?,?,?,0,'成品库',?, '套','正常',?)''',
                               (product_name, receipt['product_id'], 1, receipt['quantity'], f"入库单{receipt['receipt_no']}入库"))
        db.commit()
        log_operation('验证收益', '入库审批', f"入库单 {receipt['receipt_no']} 已审批入库，成品库存已自动增加")
    return redirect(url_for('delivery_dashboard', tab='receipts'))

@app.route('/delivery/deliver/add', methods=['GET', 'POST'])
def deliver_add():
    db = get_db()
    products = db.execute('SELECT id, name, model FROM products').fetchall()
    if request.method == 'POST':
        # 输入校验
        qty, err = validate_positive_int(request.form.get('quantity', 0), '交付数量')
        if err:
            return validation_error(err)
        if not request.form.get('market_team', '').strip():
            return validation_error('市场团队不能为空')

        # 打通交付与库存：交付前校验成品库存是否足够
        product_id = request.form.get('product_id')
        if product_id:
            product_id = int(product_id)
            inv = db.execute('SELECT quantity FROM inventory WHERE product_id=? AND is_product=1', (product_id,)).fetchone()
            current_qty = inv['quantity'] if inv else 0
            if current_qty < qty:
                product = db.execute('SELECT name FROM products WHERE id=?', (product_id,)).fetchone()
                product_name = product['name'] if product else '该产品'
                return validation_error(f'成品库存不足：{product_name}当前库存{current_qty}套，需交付{qty}套，请先入库')

        delivery_no = f"DL{datetime.now().strftime('%Y%m%d%H%M%S')}"
        db.execute('''INSERT INTO deliveries (delivery_no, product_id, market_team, quantity, customer,
                      purpose, delivery_date, status, feedback)
                      VALUES (?,?,?,?,?,?,?,?,?)''',
                   (delivery_no, product_id,
                    request.form.get('market_team','').strip(), qty,
                    request.form.get('customer',''), request.form.get('purpose',''),
                    request.form.get('delivery_date', date.today().isoformat()),
                    '已交付', request.form.get('feedback','')))
        # 打通交付与库存：交付后自动扣减成品库存
        if product_id and qty > 0:
            db.execute('UPDATE inventory SET quantity=quantity-?, updated_at=datetime("now","localtime") WHERE product_id=? AND is_product=1 AND quantity>=?',
                       (qty, product_id, qty))
        db.commit()
        log_operation('验证收益', '交付', f"交付单 {delivery_no} 客户: {request.form.get('customer','')}，成品库存已自动扣减")
        return redirect(url_for('delivery_dashboard', tab='deliveries'))
    return render_template('deliver_form.html', products=products)

# ============================================================
# 系统日志
# ============================================================
@app.route('/logs')
def logs():
    db = get_db()
    logs = db.execute('SELECT * FROM operation_logs ORDER BY created_at DESC LIMIT 100').fetchall()
    return render_template('logs.html', logs=logs)

# ============================================================
# 数据管理（导出/导入/数据字典）
# ============================================================
from data_utils import (BUSINESS_TABLES, TABLE_NAMES_CN, get_table_columns, get_table_count,
                         export_table_csv, export_table_json, export_all_json, export_all_csv_zip,
                         export_sql, import_table_csv, import_table_json, get_data_statistics,
                         get_field_desc)
from flask import Response, send_file

# 注册模板全局函数
app.jinja_env.globals['get_field_desc'] = get_field_desc

@app.route('/data')
def data_management():
    """数据管理页面"""
    db = get_db()
    stats = get_data_statistics(db)
    tables_info = []
    for table in BUSINESS_TABLES:
        tables_info.append({
            'name': table,
            'name_cn': TABLE_NAMES_CN.get(table, table),
            'count': get_table_count(db, table),
            'columns': get_table_columns(db, table)
        })
    db.close()
    return render_template('data_management.html', stats=stats, tables=tables_info)

@app.route('/timeline')
def timeline_view():
    """跨团队协作时间线视图"""
    db = get_db()
    team_filter = request.args.get('team', '')
    product_filter = request.args.get('product', '', type=int)

    query = '''SELECT e.*, p.name as product_name, v.version_no
               FROM timeline_events e
               LEFT JOIN products p ON e.product_id = p.id
               LEFT JOIN software_versions v ON e.version_id = v.id
               WHERE 1=1'''
    params = []
    if team_filter:
        query += ' AND e.team = ?'
        params.append(team_filter)
    if product_filter:
        query += ' AND e.product_id = ?'
        params.append(product_filter)
    query += ' ORDER BY e.event_date ASC, e.id ASC'

    events = db.execute(query, params).fetchall()
    products = db.execute('SELECT id, name FROM products ORDER BY id').fetchall()
    db.close()
    return render_template('timeline.html', events=events, products=products,
                           team_filter=team_filter, product_filter=product_filter)

@app.route('/data/export/json')
def data_export_json():
    """导出全库为结构化JSON（适合AI训练）"""
    db = get_db()
    json_content = export_all_json(db)
    log_operation('数据管理', '导出', f'全库JSON导出，{len(json_content)}字节')
    db.close()
    return Response(
        json_content,
        mimetype='application/json',
        headers={'Content-Disposition': f'attachment; filename=business_data_{datetime.now().strftime("%Y%m%d_%H%M%S")}.json'}
    )

@app.route('/data/export/csv')
def data_export_csv():
    """导出全库为CSV压缩包（每表一个CSV）"""
    db = get_db()
    zip_content = export_all_csv_zip(db)
    log_operation('数据管理', '导出', '全库CSV压缩包导出')
    db.close()
    return Response(
        zip_content,
        mimetype='application/zip',
        headers={'Content-Disposition': f'attachment; filename=business_data_{datetime.now().strftime("%Y%m%d_%H%M%S")}.zip'}
    )

@app.route('/data/export/sql')
def data_export_sql():
    """导出全库为SQL备份"""
    db = get_db()
    sql_content = export_sql(db)
    log_operation('数据管理', '导出', '全库SQL备份导出')
    db.close()
    return Response(
        sql_content,
        mimetype='text/sql',
        headers={'Content-Disposition': f'attachment; filename=business_backup_{datetime.now().strftime("%Y%m%d_%H%M%S")}.sql'}
    )

@app.route('/data/export/<table_name>/<fmt>')
def data_export_table(table_name, fmt):
    """导出单表为CSV或JSON"""
    if table_name not in BUSINESS_TABLES:
        return validation_error(f'表名 {table_name} 不存在')
    if fmt not in ('csv', 'json'):
        return validation_error(f'不支持的格式: {fmt}')

    db = get_db()
    if fmt == 'csv':
        content = export_table_csv(db, table_name)
        mime = 'text/csv'
        ext = 'csv'
    else:
        content = export_table_json(db, table_name)
        mime = 'application/json'
        ext = 'json'
    log_operation('数据管理', '导出', f'单表导出: {table_name}.{ext}')
    db.close()
    return Response(
        content,
        mimetype=mime,
        headers={'Content-Disposition': f'attachment; filename={table_name}.{ext}'}
    )

@app.route('/data/import', methods=['GET', 'POST'])
def data_import():
    """导入数据（CSV/JSON）"""
    if request.method == 'POST':
        table_name = request.form.get('table_name')
        if table_name not in BUSINESS_TABLES:
            return validation_error('请选择有效的表名')

        file = request.files.get('file')
        if not file:
            return validation_error('请选择要导入的文件')

        filename = file.filename.lower()
        content = file.read().decode('utf-8')

        db = get_db()
        try:
            if filename.endswith('.csv'):
                imported, errors = import_table_csv(db, table_name, content)
            elif filename.endswith('.json'):
                imported, errors = import_table_json(db, table_name, content)
            else:
                db.close()
                return validation_error('仅支持 .csv 或 .json 格式文件')

            log_operation('数据管理', '导入', f'表 {table_name} 导入 {imported} 条记录')
            db.close()

            result_msg = f'成功导入 {imported} 条记录到 {TABLE_NAMES_CN.get(table_name, table_name)}'
            if errors:
                result_msg += f'，{len(errors)} 条失败: {"; ".join(errors[:3])}'
            return render_template('data_import_result.html', message=result_msg, success=True)
        except Exception as e:
            db.close()
            return render_template('data_import_result.html', message=f'导入失败: {str(e)}', success=False)

    db = get_db()
    tables = [{'name': t, 'name_cn': TABLE_NAMES_CN.get(t, t)} for t in BUSINESS_TABLES]
    db.close()
    return render_template('data_import.html', tables=tables)

@app.route('/data/dictionary')
def data_dictionary():
    """数据字典页面（供AI理解数据结构）"""
    db = get_db()
    tables = []
    for table in BUSINESS_TABLES:
        columns = get_table_columns(db, table)
        count = get_table_count(db, table)
        # 取前3条示例数据
        samples = db.execute(f'SELECT * FROM {table} LIMIT 3').fetchall()
        tables.append({
            'name': table,
            'name_cn': TABLE_NAMES_CN.get(table, table),
            'count': count,
            'columns': columns,
            'samples': [dict(s) for s in samples]
        })
    db.close()
    return render_template('data_dictionary.html', tables=tables)

# ============================================================
# API接口（供后续扩展）
# ============================================================
@app.route('/api/stats')
def api_stats():
    db = get_db()
    return jsonify({
        'requirements': db.execute('SELECT COUNT(*) FROM requirements WHERE is_deleted=0').fetchone()[0],
        'products': db.execute('SELECT COUNT(*) FROM products').fetchone()[0],
        'production_orders': db.execute('SELECT COUNT(*) FROM production_orders').fetchone()[0],
        'quality_inspections': db.execute('SELECT COUNT(*) FROM quality_inspections').fetchone()[0],
        'deliveries': db.execute('SELECT COUNT(*) FROM deliveries').fetchone()[0],
    })

@app.route('/api/global-search')
def api_global_search():
    """全局搜索API：跨模块搜索需求、Bug、任务、合同、设备、产品等"""
    q = request.args.get('q', '').strip()
    if len(q) < 2:
        return jsonify({'success': False, 'results': []})
    db = get_db()
    results = []
    like = f'%{q}%'
    # 搜索需求
    try:
        reqs = db.execute('''SELECT id, title, customer, source_dept FROM requirements
                             WHERE is_deleted=0 AND (title LIKE ? OR customer LIKE ? OR description LIKE ?) LIMIT 10''',
                          (like, like, like)).fetchall()
        for r in reqs:
            results.append({
                'type': 'requirement',
                'title': r['title'],
                'subtitle': r['customer'] or r['source_dept'] or '',
                'url': f'/requirements/{r["id"]}'
            })
    except Exception:
        pass
    # 搜索Bug
    try:
        bugs = db.execute('''SELECT id, bug_no, title, status FROM bug_records
                             WHERE title LIKE ? OR bug_no LIKE ? OR description LIKE ? LIMIT 10''',
                         (like, like, like)).fetchall()
        for b in bugs:
            results.append({
                'type': 'bug',
                'title': f'{b["bug_no"]} - {b["title"]}',
                'subtitle': b['status'] or '',
                'url': f'/bugs/{b["id"]}'
            })
    except Exception:
        pass
    # 搜索开发任务
    try:
        tasks = db.execute('''SELECT id, task_name, assignee, status FROM dev_tasks
                              WHERE task_name LIKE ? OR description LIKE ? LIMIT 10''',
                           (like, like)).fetchall()
        for t in tasks:
            results.append({
                'type': 'task',
                'title': t['task_name'],
                'subtitle': t['assignee'] or t['status'] or '',
                'url': '#task-' + str(t['id'])
            })
    except Exception:
        pass
    # 搜索合同
    try:
        contracts = db.execute('''SELECT id, contract_no, supplier, contract_type FROM contracts
                                  WHERE contract_no LIKE ? OR supplier LIKE ? LIMIT 10''',
                               (like, like)).fetchall()
        for c in contracts:
            results.append({
                'type': 'contract',
                'title': f'{c["contract_no"]} - {c["supplier"]}',
                'subtitle': c['contract_type'] or '',
                'url': '/ext/contracts'
            })
    except Exception:
        pass
    # 搜索设备
    try:
        devices = db.execute('''SELECT id, device_sn, customer, status FROM devices
                                WHERE device_sn LIKE ? OR customer LIKE ? LIMIT 10''',
                             (like, like)).fetchall()
        for d in devices:
            results.append({
                'type': 'device',
                'title': f'{d["device_sn"]} - {d["customer"]}',
                'subtitle': d['status'] or '',
                'url': '/ext/devices'
            })
    except Exception:
        pass
    # 搜索产品
    try:
        products = db.execute('''SELECT id, name, model, lifecycle_stage FROM products
                                 WHERE name LIKE ? OR model LIKE ? LIMIT 10''',
                              (like, like)).fetchall()
        for p in products:
            results.append({
                'type': 'product',
                'title': f'{p["name"]} ({p["model"]})',
                'subtitle': p['lifecycle_stage'] or '',
                'url': '/planning'
            })
    except Exception:
        pass
    # 搜索软件版本
    try:
        versions = db.execute('''SELECT id, version_no, version_name, status FROM software_versions
                                 WHERE version_no LIKE ? OR version_name LIKE ? LIMIT 10''',
                              (like, like)).fetchall()
        for v in versions:
            results.append({
                'type': 'version',
                'title': f'{v["version_no"]} - {v["version_name"] or ""}',
                'subtitle': v['status'] or '',
                'url': f'/planning/versions/{v["id"]}'
            })
    except Exception:
        pass
    # 搜索生产订单
    try:
        orders = db.execute('''SELECT id, order_no, supplier, status FROM production_orders
                               WHERE order_no LIKE ? OR supplier LIKE ? LIMIT 10''',
                            (like, like)).fetchall()
        for o in orders:
            results.append({
                'type': 'order',
                'title': f'{o["order_no"]} - {o["supplier"]}',
                'subtitle': o['status'] or '',
                'url': '/production'
            })
    except Exception:
        pass
    db.close()
    return jsonify({'success': True, 'results': results[:30]})

# ============================================================
# 反向规划模块1：软件版本规划（根据用户需求反向规划版本升级）
# ============================================================
@app.route('/planning/versions')
def version_list():
    """软件版本列表"""
    db = get_db()
    products = db.execute('SELECT id, name, model FROM products').fetchall()
    search = request.args.get('search', '').strip()
    status = request.args.get('status', '')
    page = max(1, int(request.args.get('page', 1)))
    per_page = 15
    offset = (page - 1) * per_page
    where = []
    params = []
    if status:
        where.append('sv.status=?')
        params.append(status)
    if search:
        where.append('(sv.version_no LIKE ? OR sv.version_name LIKE ? OR p.name LIKE ?)')
        params.extend([f'%{search}%']*3)
    where_sql = (' WHERE ' + ' AND '.join(where)) if where else ''
    total = db.execute(f'SELECT COUNT(*) FROM software_versions sv LEFT JOIN products p ON sv.product_id=p.id{where_sql}', params).fetchone()[0]
    versions = db.execute(f'''SELECT sv.*, p.name as product_name, p.model,
                             (SELECT COUNT(*) FROM dev_tasks dt WHERE dt.version_id = sv.id) as task_count,
                             (SELECT COUNT(*) FROM bug_records br WHERE br.version_id = sv.id) as bug_count,
                             (SELECT COUNT(*) FROM version_requirements vr WHERE vr.version_id = sv.id) as req_count
                             FROM software_versions sv LEFT JOIN products p ON sv.product_id=p.id
                             {where_sql} ORDER BY sv.planned_release_date ASC LIMIT ? OFFSET ?''',
                             params + [per_page, offset]).fetchall()
    total_pages = (total + per_page - 1) // per_page
    return render_template('version_list.html', versions=versions, products=products,
                           search=search, current_status=status, page=page, total_pages=total_pages, total=total)

@app.route('/planning/versions/add', methods=['GET', 'POST'])
def version_add():
    """新增软件版本"""
    db = get_db()
    products = db.execute('SELECT id, name, model FROM products').fetchall()
    requirements = db.execute('''SELECT id, title, source_dept, customer, description, quantity, priority, status, delivery_date, created_at
                                 FROM requirements WHERE status IN ("已评审","进行中") AND is_deleted=0
                                 ORDER BY CASE priority WHEN '高' THEN 1 WHEN '中' THEN 2 ELSE 3 END, created_at DESC''').fetchall()
    if request.method == 'POST':
        cursor = db.execute('''INSERT INTO software_versions (product_id, version_no, version_name, description, capabilities, planned_release_date, status)
                      VALUES (?,?,?,?,?,?,?)''',
                   (request.form.get('product_id'), request.form.get('version_no',''),
                    request.form.get('version_name',''), request.form.get('description',''),
                    request.form.get('capabilities',''), request.form.get('planned_release_date',''),
                    request.form.get('status','规划中')))
        vid = cursor.lastrowid
        # 关联需求
        req_ids = request.form.getlist('requirement_ids')
        for rid in req_ids:
            db.execute('INSERT INTO version_requirements (version_id, requirement_id, priority) VALUES (?,?,?)',
                       (vid, rid, '中'))
        db.commit()
        log_operation('版本规划', '新增', f'新增版本 {request.form.get("version_no")}')
        db.close()
        return redirect(url_for('version_detail', vid=vid))
    return render_template('version_form.html', products=products, requirements=requirements, item=None)

@app.route('/planning/versions/<int:vid>')
def version_detail(vid):
    """版本详情（含功能、开发任务、关联需求、Bug、测试用例）"""
    db = get_db()
    version = db.execute('''SELECT sv.*, p.name as product_name, p.model
                            FROM software_versions sv LEFT JOIN products p ON sv.product_id=p.id
                            WHERE sv.id=?''', (vid,)).fetchone()
    if not version:
        db.close()
        return validation_error('版本不存在')
    tasks = db.execute('SELECT * FROM dev_tasks WHERE version_id=? ORDER BY priority, planned_start', (vid,)).fetchall()
    linked_reqs = db.execute('''SELECT r.*, vr.priority as link_priority
                                 FROM version_requirements vr
                                 JOIN requirements r ON vr.requirement_id=r.id
                                 WHERE vr.version_id=?''', (vid,)).fetchall()
    bugs = db.execute('SELECT * FROM bug_records WHERE version_id=? ORDER BY created_at DESC', (vid,)).fetchall()
    test_cases = db.execute('SELECT * FROM test_cases WHERE version_id=? ORDER BY created_at DESC', (vid,)).fetchall()
    features = db.execute('SELECT * FROM version_features WHERE version_id=? ORDER BY priority, created_at', (vid,)).fetchall()
    db.close()
    return render_template('version_detail.html', version=version, tasks=tasks,
                           linked_reqs=linked_reqs, bugs=bugs, test_cases=test_cases, features=features)

@app.route('/planning/versions/<int:vid>/status', methods=['POST'])
def version_status_update(vid):
    """版本状态人工流转（规划中→开发中→测试中→已发布，任意→已取消）"""
    new_status = request.form.get('status', '')
    valid = ['规划中', '开发中', '测试中', '已发布', '已取消']
    if new_status not in valid:
        return jsonify({'success': False, 'error': '无效的状态值'}), 400
    db = get_db()
    cur = db.execute('SELECT status FROM software_versions WHERE id=?', (vid,)).fetchone()
    if not cur:
        db.close()
        return jsonify({'success': False, 'error': '版本不存在'}), 404
    old_status = cur['status']
    if new_status == '已发布' and old_status != '已发布':
        db.execute('''UPDATE software_versions SET status=?, actual_release_date=datetime('now','localtime'),
                      updated_at=datetime('now','localtime') WHERE id=?''', (new_status, vid))
    else:
        db.execute('''UPDATE software_versions SET status=?, updated_at=datetime('now','localtime') WHERE id=?''',
                   (new_status, vid))
    db.commit()
    log_operation('版本规划', '状态流转', f'版本ID {vid} 状态 {old_status} → {new_status}')
    db.close()
    return jsonify({'success': True, 'status': new_status})

@app.route('/planning/versions/<int:vid>/tasks/add', methods=['POST'])
def version_task_add(vid):
    """添加开发任务"""
    db = get_db()
    requirement_id = request.form.get('requirement_id')
    requirement_id = int(requirement_id) if requirement_id else None
    db.execute('''INSERT INTO dev_tasks (version_id, requirement_id, task_name, description, priority, status, assignee, planned_start, planned_finish)
                  VALUES (?,?,?,?,?,?,?,?,?)''',
               (vid, requirement_id, request.form.get('task_name',''), request.form.get('description',''),
                request.form.get('priority','中'), '待开发', request.form.get('assignee',''),
                request.form.get('planned_start',''), request.form.get('planned_finish','')))
    db.commit()
    log_operation('版本规划', '新增任务', f'版本ID {vid} 新增任务: {request.form.get("task_name")}')
    db.close()
    return redirect(url_for('version_detail', vid=vid))

@app.route('/planning/versions/<int:vid>/tasks/<int:tid>/status', methods=['POST'])
def version_task_status(vid, tid):
    """更新开发任务状态"""
    new_status = request.form.get('status', '')
    valid = ['待开发', '开发中', '已完成', '已取消']
    if new_status not in valid:
        return jsonify({'success': False}), 400
    db = get_db()
    db.execute('UPDATE dev_tasks SET status=?, actual_finish=datetime("now","localtime") WHERE id=?',
               (new_status, tid))
    db.commit()
    db.close()
    return jsonify({'success': True, 'status': new_status})

# ============================================================
# 反向规划模块2：Bug登记与流转（含根因分析闭环）
# ============================================================
@app.route('/bugs')
def bug_list():
    """Bug列表"""
    db = get_db()
    tab = request.args.get('tab', 'all')
    query = '''SELECT b.*, p.name as product_name, sv.version_no
               FROM bug_records b
               LEFT JOIN products p ON b.product_id=p.id
               LEFT JOIN software_versions sv ON b.version_id=sv.id'''
    if tab == 'open':
        query += " WHERE b.status NOT IN ('已关闭','已取消')"
    elif tab == 'quality':
        query += " WHERE b.is_quality_issue=1"
    query += " ORDER BY b.created_at DESC"
    bugs = db.execute(query).fetchall()
    products = db.execute('SELECT id, name FROM products').fetchall()
    db.close()
    return render_template('bug_list.html', bugs=bugs, tab=tab, products=products)

@app.route('/bugs/add', methods=['GET', 'POST'])
def bug_add():
    """新增Bug"""
    db = get_db()
    products = db.execute('SELECT id, name FROM products').fetchall()
    versions = db.execute('SELECT id, version_no, product_id FROM software_versions').fetchall()
    if request.method == 'POST':
        bug_no = f"BUG{datetime.now().strftime('%Y%m%d%H%M%S')}"
        cursor = db.execute('''INSERT INTO bug_records (bug_no, product_id, version_id, title, description,
                              bug_type, severity, priority, status, reporter, assignee, discovered_at)
                              VALUES (?,?,?,?,?,?,?,?,?,?,?,?)''',
                   (bug_no, request.form.get('product_id') or None, request.form.get('version_id') or None,
                    request.form.get('title',''), request.form.get('description',''),
                    request.form.get('bug_type','功能Bug'), request.form.get('severity','一般'),
                    request.form.get('priority','中'), '新建',
                    request.form.get('reporter',''), request.form.get('assignee',''),
                    request.form.get('discovered_at','') or datetime.now().strftime('%Y-%m-%d')))
        bid = cursor.lastrowid
        db.execute('''INSERT INTO bug_workflow (bug_id, action, from_status, to_status, operator, comment)
                      VALUES (?,?,?,?,?,?)''', (bid, '创建', None, '新建', request.form.get('reporter',''), 'Bug登记'))
        db.commit()
        log_operation('Bug管理', '新增', f'新增Bug {bug_no}: {request.form.get("title")}')
        db.close()
        return redirect(url_for('bug_detail', bid=bid))
    return render_template('bug_form.html', products=products, versions=versions)

@app.route('/bugs/<int:bid>')
def bug_detail(bid):
    """Bug详情（含流转记录）"""
    db = get_db()
    bug = db.execute('''SELECT b.*, p.name as product_name, sv.version_no
                        FROM bug_records b
                        LEFT JOIN products p ON b.product_id=p.id
                        LEFT JOIN software_versions sv ON b.version_id=sv.id
                        WHERE b.id=?''', (bid,)).fetchone()
    if not bug:
        db.close()
        return validation_error('Bug不存在')
    workflow = db.execute('SELECT * FROM bug_workflow WHERE bug_id=? ORDER BY created_at', (bid,)).fetchall()
    db.close()
    return render_template('bug_detail.html', bug=bug, workflow=workflow)

@app.route('/bugs/<int:bid>/update', methods=['POST'])
def bug_update(bid):
    """Bug状态流转（完整生命周期：新建→已分配→处理中→已修复→验证中→已关闭/重新打开，可取消）"""
    new_status = request.form.get('status', '')
    valid = ['新建', '已分配', '处理中', '已修复', '验证中', '已关闭', '重新打开', '已取消']
    if new_status not in valid:
        return validation_error('无效的状态')
    db = get_db()
    bug = db.execute('SELECT status FROM bug_records WHERE id=?', (bid,)).fetchone()
    old_status = bug['status'] if bug else None
    update_fields = ['status=?']
    params = [new_status]
    # 分配负责人时更新assignee
    if new_status == '已分配' and request.form.get('assignee'):
        update_fields.append('assignee=?')
        params.append(request.form.get('assignee'))
    # 提交修复时记录修复方案
    if new_status == '已修复' and request.form.get('fix_solution'):
        update_fields.append('fix_solution=?')
        params.append(request.form.get('fix_solution'))
    # 开始验证时记录验证结果（可选）
    if new_status == '验证中' and request.form.get('test_result'):
        update_fields.append('test_result=?')
        params.append(request.form.get('test_result'))
    # 关闭时记录关闭原因、根因分析、纠正措施、预防措施
    if new_status == '已关闭':
        update_fields.append('closed_at=datetime("now","localtime")')
        if request.form.get('close_reason'):
            update_fields.append('close_reason=?')
            params.append(request.form.get('close_reason'))
        if request.form.get('root_cause'):
            update_fields.append('root_cause=?')
            params.append(request.form.get('root_cause'))
        if request.form.get('corrective_action'):
            update_fields.append('corrective_action=?')
            params.append(request.form.get('corrective_action'))
        if request.form.get('prevention_measure'):
            update_fields.append('prevention_measure=?')
            params.append(request.form.get('prevention_measure'))
    # 重新打开时清除关闭时间
    if new_status == '重新打开':
        update_fields.append('closed_at=NULL')
        update_fields.append('close_reason=NULL')
    # 取消时记录关闭时间和关闭原因
    if new_status == '已取消':
        update_fields.append('closed_at=datetime("now","localtime")')
        if request.form.get('close_reason'):
            update_fields.append('close_reason=?')
            params.append(request.form.get('close_reason'))
    params.append(bid)
    db.execute(f'UPDATE bug_records SET {",".join(update_fields)} WHERE id=?', params)
    # 记录流转历史（含修复方案、验证结果、关闭原因）
    db.execute('''INSERT INTO bug_workflow (bug_id, action, from_status, to_status, operator, comment, fix_solution, test_result, close_reason)
                  VALUES (?,?,?,?,?,?,?,?,?)''',
               (bid, '状态变更', old_status, new_status,
                request.form.get('operator','系统'), request.form.get('comment',''),
                request.form.get('fix_solution'), request.form.get('test_result'),
                request.form.get('close_reason')))
    db.commit()
    log_operation('Bug管理', '流转', f'BugID {bid} 状态从 {old_status} 变更为 {new_status}')
    db.close()
    return redirect(url_for('bug_detail', bid=bid))

@app.route('/bugs/<int:bid>/escalate', methods=['POST'])
def bug_escalate(bid):
    """Bug升级为质量问题"""
    db = get_db()
    db.execute('UPDATE bug_records SET is_quality_issue=1 WHERE id=?', (bid,))
    db.execute('''INSERT INTO bug_workflow (bug_id, action, operator, comment)
                  VALUES (?,?,?,?)''', (bid, '升级质量问题', request.form.get('operator','系统'),
                request.form.get('reason','严重Bug，升级为质量问题进行根因分析')))
    db.commit()
    log_operation('Bug管理', '升级', f'BugID {bid} 升级为质量问题')
    db.close()
    return redirect(url_for('bug_detail', bid=bid))

# ============================================================
# 反向规划模块3：生产排程与工序管理（含物料需求计划）
# ============================================================
@app.route('/production/schedules')
def schedule_list():
    """生产排程列表（已并入生产管理看板，重定向）"""
    return redirect(url_for('production_dashboard'))

@app.route('/production/schedules/add', methods=['GET', 'POST'])
def schedule_add():
    """新增生产排程"""
    db = get_db()
    products = db.execute('SELECT id, name, model FROM products').fetchall()
    # 生产排程只关联用户需求（合同刚性需求），其他三类需求只影响软件版本，不进入生产流程
    requirements = db.execute('SELECT id, title, quantity, delivery_date FROM requirements WHERE req_type = "用户需求" AND status IN ("已评审","进行中") AND is_deleted=0').fetchall()
    versions = db.execute('SELECT sv.*, p.name as product_name FROM software_versions sv LEFT JOIN products p ON sv.product_id=p.id ORDER BY sv.product_id, sv.created_at').fetchall()
    if request.method == 'POST':
        schedule_no = f"PS{datetime.now().strftime('%Y%m%d%H%M%S')}{random.randint(100,999)}"
        pid = request.form.get('product_id')
        qty = int(request.form.get('quantity', 1))
        version_id = request.form.get('version_id')
        version_id = int(version_id) if version_id else None
        flash_version_no = None
        if version_id:
            ver = db.execute('SELECT version_no FROM software_versions WHERE id=?', (version_id,)).fetchone()
            if ver:
                flash_version_no = ver['version_no']
        cursor = db.execute('''INSERT INTO production_schedules (schedule_no, product_id, requirement_id, quantity,
                              plan_start, plan_finish, status, remark, version_id, flash_version_no)
                              VALUES (?,?,?,?,?,?,?,?,?,?)''',
                   (schedule_no, pid, request.form.get('requirement_id') or None, qty,
                    request.form.get('plan_start',''), request.form.get('plan_finish',''),
                    '规划中', request.form.get('remark',''), version_id, flash_version_no))
        sid = cursor.lastrowid
        # 自动生成物料需求计划（根据BOM计算关键物料需求）
        boms = db.execute('SELECT * FROM bom_items WHERE product_id=?', (pid,)).fetchall()
        if not boms:
            flash('警告：该产品未配置BOM（物料清单），物料需求计划未生成。请先在BOM管理中配置物料清单。', 'warning')
        for bom in boms:
            needed = int(bom['quantity_per_unit'] * qty)
            available = db.execute('SELECT COALESCE(SUM(quantity),0) as total FROM inventory WHERE material_code=?',
                                   (bom['material_code'],)).fetchone()['total']
            db.execute('''INSERT INTO material_plans (schedule_id, bom_item_id, material_name, material_code,
                          is_key_material, quantity_needed, quantity_available, unit, status)
                          VALUES (?,?,?,?,?,?,?,?,?)''',
                       (sid, bom['id'], bom['material_name'], bom['material_code'],
                        bom['is_key_material'], needed, available, bom['unit'],
                        '已确认' if available >= needed else '待采购'))
        # 自动生成标准工序（增加版本刷新车序）
        processes = [('SMT贴片', 1), ('回流焊接', 2), ('AOI检测', 3), ('功能测试', 4), ('外观检验', 5), ('版本刷新', 6), ('包装入库', 7)]
        for name, seq in processes:
            db.execute('''INSERT INTO production_processes (schedule_id, process_name, sequence, status)
                          VALUES (?,?,?,?)''', (sid, name, seq, '待开始'))
        db.commit()
        log_operation('生产排程', '新增', f'新增排程 {schedule_no}, 自动生成{len(boms)}项物料需求和{len(processes)}道工序')
        db.close()
        return redirect(url_for('schedule_detail', sid=sid))
    return render_template('schedule_form.html', products=products, requirements=requirements, versions=versions)

@app.route('/production/schedules/<int:sid>')
def schedule_detail(sid):
    """排程详情（含工序、物料需求、关联生产订单、关联领用记录）"""
    db = get_db()
    schedule = db.execute('''SELECT ps.*, p.name as product_name, p.model, r.title as req_title
                             FROM production_schedules ps
                             LEFT JOIN products p ON ps.product_id=p.id
                             LEFT JOIN requirements r ON ps.requirement_id=r.id
                             WHERE ps.id=?''', (sid,)).fetchone()
    if not schedule:
        db.close()
        return validation_error('排程不存在')
    processes = db.execute('SELECT * FROM production_processes WHERE schedule_id=? ORDER BY sequence', (sid,)).fetchall()
    materials = db.execute('SELECT * FROM material_plans WHERE schedule_id=? ORDER BY is_key_material DESC, material_name', (sid,)).fetchall()
    linked_orders = db.execute('SELECT * FROM production_orders WHERE schedule_id=? ORDER BY created_at DESC', (sid,)).fetchall()
    # 关联领用记录：通过物料需求计划关联
    material_ids = [m['id'] for m in materials]
    requisitions = []
    if material_ids:
        placeholders = ','.join('?' * len(material_ids))
        requisitions = db.execute(f'''
            SELECT DISTINCT mr.*, ri.material_name as item_material, ri.quantity as item_quantity, ri.material_plan_id
            FROM material_requisitions mr
            JOIN requisition_items ri ON mr.id=ri.requisition_id
            WHERE ri.material_plan_id IN ({placeholders})
            ORDER BY mr.created_at DESC
        ''', material_ids).fetchall()
    db.close()
    return render_template('schedule_detail.html', schedule=schedule, processes=processes,
                           materials=materials, linked_orders=linked_orders, requisitions=requisitions)

@app.route('/production/schedules/<int:sid>/processes/<int:pid>/status', methods=['POST'])
def schedule_process_status(sid, pid):
    """更新工序状态（全部工序完成后自动联动排程为已完成）"""
    new_status = request.form.get('status', '')
    valid = ['待开始', '进行中', '已完成', '已跳过']
    if new_status not in valid:
        return jsonify({'success': False}), 400
    db = get_db()
    db.execute('UPDATE production_processes SET status=?, actual_finish=datetime("now","localtime") WHERE id=?',
               (new_status, pid))
    # 联动：该排程全部工序完成/跳过 → 排程自动置已完成
    done = db.execute('''SELECT COUNT(*) c FROM production_processes
        WHERE schedule_id=? AND status IN ('已完成','已跳过')''', (sid,)).fetchone()['c']
    total = db.execute('SELECT COUNT(*) c FROM production_processes WHERE schedule_id=?', (sid,)).fetchone()['c']
    if total > 0 and done >= total:
        db.execute("UPDATE production_schedules SET status='已完成' WHERE id=? AND status != '已取消'", (sid,))
    db.commit()
    db.close()
    return jsonify({'success': True, 'status': new_status})

# ============================================================
# 启动
# ============================================================
def get_config():
    """读取配置：优先级 命令行参数 > 环境变量 > config.py > 默认值"""
    import argparse
    default_port = 5000
    default_debug = True

    # 尝试从 config.py 读取
    try:
        from config import PORT as cfg_port, DEBUG as cfg_debug
        default_port = cfg_port
        default_debug = cfg_debug
    except ImportError:
        pass

    # 环境变量
    env_port = os.environ.get('BUSINESS_PORT')
    if env_port and env_port.isdigit():
        default_port = int(env_port)
    env_debug = os.environ.get('BUSINESS_DEBUG')
    if env_debug is not None:
        default_debug = env_debug.lower() in ('1', 'true', 'yes')

    # 命令行参数（最高优先级）
    parser = argparse.ArgumentParser(description='AI驱动的业务高效运转系统')
    parser.add_argument('--port', type=int, default=None, help='服务端口号（默认6000）')
    parser.add_argument('--host', type=str, default='0.0.0.0', help='监听地址（默认0.0.0.0）')
    parser.add_argument('--debug', action='store_true', default=None, help='开启调试模式')
    parser.add_argument('--no-debug', action='store_true', help='关闭调试模式（生产环境）')
    args = parser.parse_args()

    port = args.port if args.port is not None else default_port
    debug = default_debug
    if args.debug:
        debug = True
    if args.no_debug:
        debug = False

    return args.host, port, debug


# ============================================================
# Excel批量导入导出
# ============================================================
@app.route('/excel/template/<table_name>')
def download_template(table_name):
    """下载Excel导入模板"""
    if table_name not in excel_utils.TABLE_CONFIG:
        flash('不支持的表名', 'error')
        return redirect(request.referrer or url_for('index'))
    # 检查模块权限
    if not has_module_access(table_name):
        flash('您没有权限访问该模块', 'error')
        return redirect(url_for('index'))
    # 生成模板文件
    temp_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'temp')
    os.makedirs(temp_dir, exist_ok=True)
    config = excel_utils.TABLE_CONFIG[table_name]
    filename = f'{config["name"]}_导入模板.xlsx'
    filepath = os.path.join(temp_dir, filename)
    excel_utils.generate_template(table_name, filepath)
    log_operation('数据管理', '下载模板', f'下载{config["name"]}导入模板')
    return send_file(filepath, as_attachment=True, download_name=filename)

@app.route('/excel/import/<table_name>', methods=['POST'])
def excel_import(table_name):
    """Excel批量导入"""
    if table_name not in excel_utils.TABLE_CONFIG:
        flash('不支持的表名', 'error')
        return redirect(request.referrer or url_for('index'))
    # 检查模块权限和写权限
    if not has_module_access(table_name):
        flash('您没有权限访问该模块', 'error')
        return redirect(url_for('index'))
    if is_readonly_module(table_name):
        flash('该模块为只读模式，无法导入数据', 'error')
        return redirect(request.referrer or url_for('index'))
    # 检查文件
    if 'excel_file' not in request.files:
        flash('请选择要上传的Excel文件', 'error')
        return redirect(request.referrer or url_for('index'))
    file = request.files['excel_file']
    if file.filename == '':
        flash('请选择要上传的Excel文件', 'error')
        return redirect(request.referrer or url_for('index'))
    # 保存文件
    temp_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'temp')
    os.makedirs(temp_dir, exist_ok=True)
    filename = secure_filename(file.filename)
    filepath = os.path.join(temp_dir, f'import_{datetime.now().strftime("%Y%m%d%H%M%S")}_{filename}')
    file.save(filepath)
    # 导入数据
    try:
        db = get_db()
        success, fail, errors = excel_utils.import_from_excel(table_name, filepath, db)
        config = excel_utils.TABLE_CONFIG[table_name]
        msg = f'导入完成：成功{success}条，失败{fail}条'
        if errors:
            msg += f'。错误详情：{"; ".join(errors[:5])}'
            if len(errors) > 5:
                msg += f'等{len(errors)}条错误'
        if fail > 0:
            flash(msg, 'warning')
        else:
            flash(msg, 'success')
        log_operation('数据管理', '批量导入', f'{config["name"]}批量导入：成功{success}条，失败{fail}条')
    except Exception as e:
        flash(f'导入失败：{str(e)[:200]}', 'error')
    finally:
        # 清理临时文件
        if os.path.exists(filepath):
            os.remove(filepath)
    return redirect(request.referrer or url_for('index'))

# ============================================================
# 甘特图工作计划工具 v2.2 - 完全集成
# ============================================================
from flask import send_from_directory
import gantt_utils

# 甘特图源表映射：分类 -> (表名, 名称列, 开始列, 结束列, 状态列, 完成状态值)
GANTT_SOURCE_MAP = [
    ('产品研发情况', 'dev_tasks', 'task_name', 'planned_start', 'planned_finish', 'status', '已完成'),
    ('产品测试情况', 'test_executions', 'execution_no', 'start_date', 'end_date', 'status', '已完成'),
    ('产品生产情况', 'production_schedules', 'schedule_no', 'plan_start', 'plan_finish', 'status', '已完成'),
]


def gantt_load_projects(db):
    """从 gantt_projects 表读取项目；每次加载时，有源标记的项目从源表重新同步最新数据（名称/日期/状态），自定义项目保持不变。"""
    # 首次使用（表空且未同步过）：从源表同步初始化
    rows = db.execute('SELECT * FROM gantt_projects ORDER BY id').fetchall()
    if not rows:
        setting = db.execute('SELECT synced FROM gantt_settings WHERE id=1').fetchone()
        if not setting or not setting['synced']:
            _gantt_init_from_source(db)
            rows = db.execute('SELECT * FROM gantt_projects ORDER BY id').fetchall()

    # 每次加载：对有源标记的项目，从源表读取最新数据并更新镜像
    for r in rows:
        if not r['source_table'] or r['source_id'] is None:
            continue
        latest = _gantt_get_source_latest(db, r['source_table'], r['source_id'])
        if latest:
            db.execute(
                'UPDATE gantt_projects SET name=?, start_date=?, end_date=?, category=?, done=?, updated_at=datetime(\'now\',\'localtime\') '
                'WHERE id=?',
                (latest['name'], latest['start'], latest['end'], latest['category'],
                 1 if latest['done'] else 0, r['id'])
            )
    db.commit()

    # 读取最终数据
    rows = db.execute('SELECT * FROM gantt_projects ORDER BY id').fetchall()
    projects = []
    for r in rows:
        p = {
            'id': r['id'],
            'name': r['name'],
            'start': r['start_date'] or '',
            'end': r['end_date'] or '',
            'category': r['category'],
            'done': bool(r['done']),
        }
        if r['source_table'] and r['source_id'] is not None:
            p['source_table'] = r['source_table']
            p['source_id'] = r['source_id']
        projects.append(p)
    return projects


def _gantt_init_from_source(db):
    """首次使用：从三张源表同步初始化 gantt_projects，并标记 synced=1。"""
    projects = []
    for cat, table, name_col, start_col, end_col, status_col, done_val in GANTT_SOURCE_MAP:
        try:
            src = db.execute(
                f'SELECT id, {name_col} AS nm, {start_col} AS st, {end_col} AS en, {status_col} AS sts '
                f'FROM {table} WHERE {start_col} IS NOT NULL AND {end_col} IS NOT NULL AND {start_col} != "" AND {end_col} != "" '
                f'ORDER BY {start_col}'
            ).fetchall()
        except Exception:
            continue
        for t in src:
            projects.append({
                'name': t['nm'] or '',
                'start': t['st'],
                'end': t['en'],
                'category': cat,
                'done': (t['sts'] == done_val),
                'source_table': table,
                'source_id': t['id'],
            })
    for p in projects:
        db.execute(
            'INSERT INTO gantt_projects (name, start_date, end_date, category, done, source_table, source_id) '
            'VALUES (?,?,?,?,?,?,?)',
            (p['name'], p['start'], p['end'], p['category'], 1 if p['done'] else 0, p.get('source_table'), p.get('source_id'))
        )
    db.execute(
        'INSERT INTO gantt_settings (id, synced) VALUES (1,1) '
        'ON CONFLICT(id) DO UPDATE SET synced=1'
    )
    db.commit()


def _gantt_get_source_latest(db, source_table, source_id):
    """从源表读取单条记录的最新数据，返回 {name, start, end, category, done}；不存在返回None。"""
    for cat, table, name_col, start_col, end_col, status_col, done_val in GANTT_SOURCE_MAP:
        if table != source_table:
            continue
        try:
            row = db.execute(
                f'SELECT {name_col} AS nm, {start_col} AS st, {end_col} AS en, {status_col} AS sts '
                f'FROM {table} WHERE id=?',
                (source_id,)
            ).fetchone()
        except Exception:
            return None
        if not row:
            return None
        return {
            'name': row['nm'] or '',
            'start': row['st'] or '',
            'end': row['en'] or '',
            'category': cat,
            'done': (row['sts'] == done_val),
        }
    return None


def gantt_load_settings(db):
    """读取甘特图设置。"""
    row = db.execute('SELECT * FROM gantt_settings WHERE id=1').fetchone()
    if row:
        return {'start_date': row['start_date'] or '', 'end_date': row['end_date'] or '', 'auto_range': bool(row['auto_range'])}
    return {'start_date': '', 'end_date': '', 'auto_range': True}


def gantt_save_settings(db, settings):
    """保存甘特图设置。"""
    s = gantt_utils.normalize_settings(settings)
    db.execute(
        'INSERT INTO gantt_settings (id, start_date, end_date, auto_range, updated_at) VALUES (1,?,?,?,datetime(\'now\',\'localtime\')) '
        'ON CONFLICT(id) DO UPDATE SET start_date=excluded.start_date, end_date=excluded.end_date, '
        'auto_range=excluded.auto_range, updated_at=datetime(\'now\',\'localtime\')',
        (s['start_date'], s['end_date'], 1 if s['auto_range'] else 0)
    )


def gantt_sync_to_source(db, p):
    """把甘特图项目同步回源表（研发/测试/生产）。返回是否成功。
    注意：done=True 时源表状态设为"已完成"；done=False 时不修改源表状态（保持"待开始"/"进行中"等原状态）。
    """
    st = p.get('source_table')
    sid = p.get('source_id')
    if not st or sid is None:
        return False
    try:
        if st == 'dev_tasks':
            if p.get('done'):
                db.execute('UPDATE dev_tasks SET task_name=?, planned_start=?, planned_finish=?, status=? WHERE id=?',
                           (p['name'], p['start'], p['end'], '已完成', sid))
            else:
                db.execute('UPDATE dev_tasks SET task_name=?, planned_start=?, planned_finish=? WHERE id=?',
                           (p['name'], p['start'], p['end'], sid))
        elif st == 'test_executions':
            if p.get('done'):
                db.execute('UPDATE test_executions SET execution_no=?, start_date=?, end_date=?, status=? WHERE id=?',
                           (p['name'], p['start'], p['end'], '已完成', sid))
            else:
                db.execute('UPDATE test_executions SET execution_no=?, start_date=?, end_date=? WHERE id=?',
                           (p['name'], p['start'], p['end'], sid))
        elif st == 'production_schedules':
            if p.get('done'):
                db.execute('UPDATE production_schedules SET schedule_no=?, plan_start=?, plan_finish=?, status=? WHERE id=?',
                           (p['name'], p['start'], p['end'], '已完成', sid))
            else:
                db.execute('UPDATE production_schedules SET schedule_no=?, plan_start=?, plan_finish=? WHERE id=?',
                           (p['name'], p['start'], p['end'], sid))
        else:
            return False
        return True
    except Exception:
        return False


@app.route('/gantt-tool')
def gantt_tool():
    """甘特图工作计划工具页面"""
    return render_template('gantt_tool/index.html')


@app.route('/gantt_tool/<path:filename>')
def gantt_tool_static(filename):
    """甘特图工具静态文件（含教程页与图片）"""
    return send_from_directory(os.path.join(app.root_path, 'templates', 'gantt_tool'), filename)


@app.route('/api/gantt/data')
def gantt_data():
    """从数据库读取甘特图数据（settings + projects），并返回当前用户是否可编辑"""
    try:
        db = get_db()
        projects = gantt_load_projects(db)
        settings = gantt_load_settings(db)
        role = session.get('user_role', '')
        editable = role in ('admin', 'product')
        return jsonify({'ok': True, 'data': {'version': 2, 'settings': settings, 'projects': projects, 'editable': editable}})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 400


@app.route('/api/gantt/save', methods=['POST'])
def gantt_save():
    """保存甘特图数据到数据库（源表项目同步回源表，新增项目存甘特图表）"""
    try:
        raw = request.get_json()
        if not isinstance(raw, dict):
            raise ValueError('数据格式错误')
        settings = raw.get('settings', {})
        projects_raw = raw.get('projects', [])

        # 规范化
        norm = gantt_utils.normalize_full({'settings': settings, 'projects': projects_raw})
        projects = norm['projects']

        db = get_db()
        gantt_save_settings(db, settings)

        # 记录本次提交中保留的源项目（source_table, source_id）
        kept_sources = set()
        for p in projects:
            if p.get('source_table') and p.get('source_id') is not None:
                kept_sources.add((p['source_table'], p['source_id']))

        # 删除所有自定义项目（无来源标记），以本次提交为准
        db.execute('DELETE FROM gantt_projects WHERE source_table IS NULL OR source_table = ""')

        # 注意：源项目（有source_table/source_id）不在甘特图中删除，
        # 源项目的增删应在对应业务页面（研发/测试/生产）操作。
        # 如果源表中某条记录被删除，下次加载时 _gantt_get_source_latest 返回 None，
        # 该镜像会保留但显示为空名称——这里清理掉源表已不存在的镜像。
        existing_sources = db.execute(
            'SELECT id, source_table, source_id FROM gantt_projects WHERE source_table IS NOT NULL AND source_table != ""'
        ).fetchall()
        for row in existing_sources:
            latest = _gantt_get_source_latest(db, row['source_table'], row['source_id'])
            if latest is None:
                # 源表记录已被删除，清理镜像
                db.execute('DELETE FROM gantt_projects WHERE id=?', (row['id'],))

        for p in projects:
            if p.get('source_table') and p.get('source_id') is not None:
                # 更新来源项目：同步回源表 + 更新甘特图表
                gantt_sync_to_source(db, p)
                db.execute(
                    'UPDATE gantt_projects SET name=?, start_date=?, end_date=?, category=?, done=?, updated_at=datetime(\'now\',\'localtime\') '
                    'WHERE source_table=? AND source_id=?',
                    (p['name'], p['start'], p['end'], p['category'], 1 if p['done'] else 0, p['source_table'], p['source_id'])
                )
            else:
                # 新增自定义项目
                db.execute(
                    'INSERT INTO gantt_projects (name, start_date, end_date, category, done) VALUES (?,?,?,?,?)',
                    (p['name'], p['start'], p['end'], p['category'], 1 if p['done'] else 0)
                )

        db.commit()
        log_operation('甘特图工具', '保存', f'保存了{len(projects)}个任务')
        return jsonify({'ok': True, 'data': {'version': 2, 'settings': gantt_load_settings(db), 'projects': gantt_load_projects(db)}})
    except Exception as e:
        if 'db' in dir() and db:
            db.rollback()
        return jsonify({'ok': False, 'error': str(e)}), 400


@app.route('/api/gantt/export')
def gantt_export():
    """导出甘特图数据（JSON / Excel）"""
    try:
        db = get_db()
        projects = gantt_load_projects(db)
        settings = gantt_load_settings(db)
        data = {'version': 2, 'settings': settings, 'projects': projects}
        fmt = request.args.get('fmt', 'json')
        stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        if fmt == 'excel':
            rows = gantt_utils.data_to_xlsx_rows(data)
            body = gantt_utils.build_xlsx(gantt_utils.XLSX_HEADERS, rows)
            fname = '甘特图数据_%s.xlsx' % stamp
            ascii_name = 'gantt_data_%s.xlsx' % stamp
            from urllib.parse import quote as url_quote
            cd = "attachment; filename=\"%s\"; filename*=UTF-8''%s" % (ascii_name, url_quote(fname))
            resp = app.response_class(body, mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
            resp.headers['Content-Disposition'] = cd
            return resp
        else:
            body = json.dumps(data, ensure_ascii=False, indent=2).encode('utf-8')
            fname = '甘特图数据_%s.json' % stamp
            ascii_name = 'gantt_data_%s.json' % stamp
            from urllib.parse import quote as url_quote
            cd = "attachment; filename=\"%s\"; filename*=UTF-8''%s" % (ascii_name, url_quote(fname))
            resp = app.response_class(body, mimetype='application/json; charset=utf-8')
            resp.headers['Content-Disposition'] = cd
            return resp
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 400


@app.route('/api/gantt/import', methods=['POST'])
def gantt_import():
    """导入 JSON 数据（覆盖甘特图自定义项目；源项目镜像保留并从源表重新同步）。
    注意：导入的数据不保留 source_table/source_id 标记，全部作为自定义项目，避免错误更新源表。
    """
    try:
        raw = request.get_json()
        norm = gantt_utils.normalize_full(raw)
        db = get_db()
        # 清空自定义项目（无来源标记），源项目镜像保留
        db.execute('DELETE FROM gantt_projects WHERE source_table IS NULL OR source_table = ""')
        # 写入导入的数据（全部作为自定义项目，不保留source标记）
        for p in norm['projects']:
            db.execute(
                'INSERT INTO gantt_projects (name, start_date, end_date, category, done) VALUES (?,?,?,?,?)',
                (p['name'], p['start'], p['end'], p['category'], 1 if p['done'] else 0)
            )
        gantt_save_settings(db, norm['settings'])
        db.commit()
        log_operation('甘特图工具', '导入JSON', f'导入{len(norm["projects"])}个任务')
        return jsonify({'ok': True, 'data': {'version': 2, 'settings': gantt_load_settings(db), 'projects': gantt_load_projects(db)}})
    except ValueError as e:
        return jsonify({'ok': False, 'error': str(e)}), 400
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 400


@app.route('/api/gantt/import_excel', methods=['POST'])
def gantt_import_excel():
    """导入 Excel 数据（覆盖甘特图数据）"""
    try:
        obj = request.get_json()
        import base64 as b64_mod
        raw = b64_mod.b64decode(obj.get('file', ''))
        rows = gantt_utils.read_xlsx_rows(raw)
        projects = gantt_utils.xlsx_rows_to_projects(rows)
        norm = gantt_utils.normalize_full({'settings': {}, 'projects': projects})
        db = get_db()
        db.execute('DELETE FROM gantt_projects')
        for p in norm['projects']:
            db.execute(
                'INSERT INTO gantt_projects (name, start_date, end_date, category, done) VALUES (?,?,?,?,?)',
                (p['name'], p['start'], p['end'], p['category'], 1 if p['done'] else 0)
            )
        db.commit()
        log_operation('甘特图工具', '导入Excel', f'导入{len(norm["projects"])}个任务')
        return jsonify({'ok': True, 'data': {'version': 2, 'settings': gantt_load_settings(db), 'projects': gantt_load_projects(db)}})
    except ValueError as e:
        return jsonify({'ok': False, 'error': str(e)}), 400
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 400


@app.route('/api/gantt/reset')
def gantt_reset():
    """示例数据 / 清空自定义项目（源项目镜像保留，从源表重新同步）"""
    try:
        mode = request.args.get('mode', 'demo')
        db = get_db()
        if mode == 'empty':
            # 只清空自定义项目（无来源标记），源项目镜像保留
            db.execute('DELETE FROM gantt_projects WHERE source_table IS NULL OR source_table = ""')
            # 重置设置为默认（自动范围）
            db.execute(
                "INSERT INTO gantt_settings (id, start_date, end_date, auto_range, synced) "
                "VALUES (1,'','',1,1) "
                "ON CONFLICT(id) DO UPDATE SET start_date='', end_date='', auto_range=1, synced=1"
            )
            db.commit()
            # 重新加载（会触发源表最新数据同步）
            data = {'version': 2, 'settings': gantt_load_settings(db), 'projects': gantt_load_projects(db)}
        else:
            # 示例数据：清空自定义项目后写入示例数据（源项目镜像保留）
            db.execute('DELETE FROM gantt_projects WHERE source_table IS NULL OR source_table = ""')
            demo = gantt_utils.DEMO_DATA
            for p in demo['projects']:
                db.execute(
                    'INSERT INTO gantt_projects (name, start_date, end_date, category, done) VALUES (?,?,?,?,?)',
                    (p['name'], p['start'], p['end'], p['category'], 1 if p['done'] else 0)
                )
            gantt_save_settings(db, demo['settings'])
            db.commit()
            data = {'version': 2, 'settings': gantt_load_settings(db), 'projects': gantt_load_projects(db)}
        log_operation('甘特图工具', '重置', '示例数据' if mode != 'empty' else '清空自定义项目')
        return jsonify({'ok': True, 'data': data})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 400

if __name__ == '__main__':
    # 初始化数据库（CREATE TABLE IF NOT EXISTS，兼容旧数据库）
    init_db()
    # 数据库迁移：为旧数据库添加缺失字段
    def migrate_db():
        db = sqlite3.connect(app.config['DATABASE'])
        db.row_factory = sqlite3.Row
        # 检查bug_records表的字段
        cols = [r[1] for r in db.execute('PRAGMA table_info(bug_records)').fetchall()]
        bug_migrations = [
            ('bug_type', "TEXT DEFAULT '功能Bug'"),
            ('priority', "TEXT DEFAULT '中'"),
            ('close_reason', 'TEXT'),
            ('fix_solution', 'TEXT'),
            ('test_result', 'TEXT'),
        ]
        for col, definition in bug_migrations:
            if col not in cols:
                try:
                    db.execute(f'ALTER TABLE bug_records ADD COLUMN {col} {definition}')
                    print(f'  数据库迁移：bug_records表添加字段 {col}')
                except Exception as e:
                    print(f'  数据库迁移警告：添加{col}失败 - {e}')
        # 检查bug_workflow表的字段
        wf_cols = [r[1] for r in db.execute('PRAGMA table_info(bug_workflow)').fetchall()]
        wf_migrations = [
            ('fix_solution', 'TEXT'),
            ('test_result', 'TEXT'),
            ('close_reason', 'TEXT'),
        ]
        for col, definition in wf_migrations:
            if col not in wf_cols:
                try:
                    db.execute(f'ALTER TABLE bug_workflow ADD COLUMN {col} {definition}')
                    print(f'  数据库迁移：bug_workflow表添加字段 {col}')
                except Exception as e:
                    print(f'  数据库迁移警告：添加{col}失败 - {e}')
        # 将旧状态"待处理"迁移为"新建"
        try:
            db.execute("UPDATE bug_records SET status='新建' WHERE status='待处理'")
            db.execute("UPDATE bug_records SET status='已修复' WHERE status='待验证'")
        except Exception as e:
            print(f'  数据库迁移警告：状态迁移失败 - {e}')
        # 检查contracts表的字段
        try:
            contract_cols = [r[1] for r in db.execute('PRAGMA table_info(contracts)').fetchall()]
            if 'paid_amount' not in contract_cols:
                db.execute('ALTER TABLE contracts ADD COLUMN paid_amount REAL DEFAULT 0')
                print('  数据库迁移：contracts表添加字段 paid_amount')
            # 将旧合同类型"采购合同"迁移为"物料合同"
            db.execute("UPDATE contracts SET contract_type='物料合同' WHERE contract_type='采购合同'")
        except Exception as e:
            print(f'  数据库迁移警告：合同表迁移失败 - {e}')
        # 创建contract_payments表（如果不存在）
        try:
            db.execute('''CREATE TABLE IF NOT EXISTS contract_payments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                contract_id INTEGER NOT NULL,
                node_name TEXT NOT NULL,
                plan_date TEXT,
                amount REAL CHECK (amount >= 0),
                status TEXT DEFAULT '待付款',
                actual_date TEXT,
                remark TEXT,
                created_at TEXT DEFAULT (datetime('now','localtime')),
                FOREIGN KEY (contract_id) REFERENCES contracts(id) ON DELETE CASCADE
            )''')
        except Exception as e:
            print(f'  数据库迁移警告：创建contract_payments表失败 - {e}')
        # 业务流程打通：dev_tasks表增加requirement_id字段
        try:
            dt_cols = [r[1] for r in db.execute('PRAGMA table_info(dev_tasks)').fetchall()]
            if 'requirement_id' not in dt_cols:
                db.execute('ALTER TABLE dev_tasks ADD COLUMN requirement_id INTEGER')
                print('  数据库迁移：dev_tasks表添加字段 requirement_id（打通需求与任务关联）')
        except Exception as e:
            print(f'  数据库迁移警告：dev_tasks表迁移失败 - {e}')
        # 业务流程打通：production_orders表增加schedule_id字段
        try:
            po_cols = [r[1] for r in db.execute('PRAGMA table_info(production_orders)').fetchall()]
            if 'schedule_id' not in po_cols:
                db.execute('ALTER TABLE production_orders ADD COLUMN schedule_id INTEGER')
                print('  数据库迁移：production_orders表添加字段 schedule_id（打通规划与执行关联）')
        except Exception as e:
            print(f'  数据库迁移警告：production_orders表迁移失败 - {e}')
        # 业务流程打通：requisition_items表增加material_plan_id字段
        try:
            ri_cols = [r[1] for r in db.execute('PRAGMA table_info(requisition_items)').fetchall()]
            if 'material_plan_id' not in ri_cols:
                db.execute('ALTER TABLE requisition_items ADD COLUMN material_plan_id INTEGER')
                print('  数据库迁移：requisition_items表添加字段 material_plan_id（打通物料与领用关联）')
        except Exception as e:
            print(f'  数据库迁移警告：requisition_items表迁移失败 - {e}')
        # 业务流程打通：inventory表增加product_id和is_product字段（统一管理物料和成品库存）
        try:
            inv_cols = [r[1] for r in db.execute('PRAGMA table_info(inventory)').fetchall()]
            if 'product_id' not in inv_cols:
                db.execute('ALTER TABLE inventory ADD COLUMN product_id INTEGER')
                print('  数据库迁移：inventory表添加字段 product_id（支持成品库存管理）')
            if 'is_product' not in inv_cols:
                db.execute('ALTER TABLE inventory ADD COLUMN is_product INTEGER DEFAULT 0')
                print('  数据库迁移：inventory表添加字段 is_product（区分物料与成品）')
        except Exception as e:
            print(f'  数据库迁移警告：inventory表迁移失败 - {e}')
        # 需求表迁移：增加4类需求的特有字段
        try:
            req_cols = [r[1] for r in db.execute('PRAGMA table_info(requirements)').fetchall()]
            req_new_cols = {
                'req_type': "TEXT DEFAULT '用户需求'",
                'product_model': 'TEXT',
                'batch_delivery': "TEXT DEFAULT '否'",
                'application_scenario': 'TEXT',
                'system_verify_point': 'TEXT',
                'verify_function': 'TEXT',
                'performance_req': 'TEXT',
                'support_product': 'TEXT',
                'certification_type': 'TEXT',
                'spec_requirement': 'TEXT',
                'industry_standard': 'TEXT',
                'baseline_product_type': 'TEXT',
                'task_progress': 'TEXT',
                'time_plan': 'TEXT',
                'deliverable': 'TEXT',
            }
            for col, col_type in req_new_cols.items():
                if col not in req_cols:
                    db.execute(f'ALTER TABLE requirements ADD COLUMN {col} {col_type}')
                    print(f'  数据库迁移：requirements表添加字段 {col}（4类需求重构）')
        except Exception as e:
            print(f'  数据库迁移警告：requirements表迁移失败 - {e}')

        # gantt_settings 迁移：添加 synced 字段
        try:
            gs_cols = [r[1] for r in db.execute('PRAGMA table_info(gantt_settings)').fetchall()]
            if gs_cols and 'synced' not in gs_cols:
                db.execute('ALTER TABLE gantt_settings ADD COLUMN synced INTEGER DEFAULT 0')
                print('  数据库迁移：gantt_settings表添加字段 synced')
        except Exception:
            pass

        db.commit()
        db.close()
    migrate_db()
    host, port, debug = get_config()
    print('='*60)
    print('  AI驱动的业务高效运转系统')
    print(f'  本机访问: http://127.0.0.1:{port}')
    print(f'  内网访问: http://<本机IP>:{port}')
    print(f'  调试模式: {"开启(Flask开发服务器)" if debug else "关闭(waitress生产服务器)"}')
    print('  端口配置: 命令行--port > 环境变量BUSINESS_PORT > config.py > 默认5000')
    print('='*60)
    if debug:
        app.run(host=host, port=port, debug=True)
    else:
        from waitress import serve
        print(f'  waitress生产服务器启动中... 线程数: 8')
        print('  按 Ctrl+C 停止服务')
        print('='*60)
        serve(app, host=host, port=port, threads=8, connection_limit=32)
