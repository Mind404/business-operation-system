#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""重新生成虚拟数据：2款产品 + 2个用户需求 + 版本规划 + bug等"""

import sqlite3
from datetime import datetime, timedelta

DB_PATH = 'business.db'

def reset_data():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # 清空所有业务表（保留用户表等系统表）
    tables = [
        'bug_records', 'dev_tasks', 'version_requirements',
        'software_versions', 'production_processes', 'material_plans',
        'production_schedules', 'quality_inspections', 'deliveries',
        'revenue_confirmations', 'contracts', 'inventory', 'bom_items',
        'requirements', 'products', 'operation_logs'
    ]
    for table in tables:
        try:
            cursor.execute(f'DELETE FROM {table}')
            cursor.execute(f"DELETE FROM sqlite_sequence WHERE name='{table}'")
        except Exception as e:
            print(f'清空 {table}: {e}')

    conn.commit()
    print('已清空所有业务数据')
    return conn, cursor

def create_products(conn, cursor):
    """创建2款产品"""
    products = [
        {
            'name': 'SAM302窄带高速率模组',
            'model': 'SAM302',
            'version': 'V1.0',
            'description': '窄带物联网高速率通信模组，支持NB-IoT/Cat.M双制式，适用于大规模数据采集场景，下行速率可达1Mbps，支持低功耗模式。',
            'lifecycle_stage': '量产中',
            'stocking_strategy': '不囤货，依据用户需求再进行生产，关键物料由我司提供，其他通用物料由生产供应商自行采购备货'
        },
        {
            'name': 'SAM203窄带手持模组',
            'model': 'SAM203',
            'version': 'V1.0',
            'description': '窄带物联网手持终端模组，集成GNSS定位、蓝牙、NFC功能，适用于移动巡检、资产盘点、人员定位等手持设备场景。',
            'lifecycle_stage': '量产中',
            'stocking_strategy': '不囤货，依据用户需求再进行生产，关键物料由我司提供，其他通用物料由生产供应商自行采购备货'
        }
    ]

    product_ids = {}
    for p in products:
        cursor.execute('''INSERT INTO products (name, model, version, description, lifecycle_stage, stocking_strategy, created_at)
                          VALUES (?,?,?,?,?,?,?)''',
                       (p['name'], p['model'], p['version'], p['description'], p['lifecycle_stage'], p['stocking_strategy'], datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
        product_ids[p['model']] = cursor.lastrowid
        print(f'创建产品: {p["name"]} (ID={cursor.lastrowid})')

    conn.commit()
    return product_ids

def create_requirements(conn, cursor):
    """创建需求：2个用户需求 + 2个集团系统侧需求"""
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    requirements = [
        # 用户需求1：园林局
        {
            'title': '园林局窄带物联网数据采集项目-SAM302模组批量需求',
            'req_type': '用户需求',
            'source_dept': '华东市场团队',
            'customer': '园林局',
            'description': '园林局窄带物联网数据采集项目，需要SAM302窄带高速率模组1500套，用于园区内环境监测、植被数据采集、智能灌溉等场景。项目要求10月30日前全部交付完毕，期间分批交付。',
            'quantity': 1500,
            'priority': '高',
            'status': '进行中',
            'delivery_date': '2026-10-30',
            'product_model': 'SAM302',
            'batch_delivery': '是',
            'batch_detail': '9月10日交付200套（已完成）；9月25日交付300套（正在生产）；10月30日交付剩余1000套',
            'application_scenario': '园林局园区环境监测、植被数据采集、智能灌溉控制系统、病虫害监测预警',
            'created_at': now,
            'updated_at': now
        },
        # 用户需求2：两江卫通
        {
            'title': '两江卫通卫星通信手持终端项目-SAM203模组需求',
            'req_type': '用户需求',
            'source_dept': '华南市场团队',
            'customer': '两江卫通',
            'description': '两江卫通卫星通信手持终端项目，需要SAM203窄带手持模组210套，用于卫星通信手持终端的定位、近场通信、蓝牙数据传输等功能。要求10月12日前一次性交付完毕。',
            'quantity': 210,
            'priority': '高',
            'status': '进行中',
            'delivery_date': '2026-10-12',
            'product_model': 'SAM203',
            'batch_delivery': '否',
            'batch_detail': '',
            'application_scenario': '卫星通信手持终端、应急通信设备、野外作业人员定位与通信',
            'created_at': now,
            'updated_at': now
        },
        # 集团系统侧需求1：V1.3基础版本约束
        {
            'title': '集团窄带物联网系统V1.3版本-基础功能适配约束',
            'req_type': '集团系统侧需求',
            'source_dept': '集团系统部',
            'customer': '集团窄带物联网平台',
            'description': '集团窄带物联网系统升级至V1.3版本，要求所有在网模组适配新的基础通信协议、数据上报格式、设备管理接口。V1.3为当前稳定运行版本，是所有模组的基础适配版本。',
            'quantity': 0,
            'priority': '中',
            'status': '已完成',
            'delivery_date': '2026-06-30',
            'product_model': '',
            'system_verify_point': '2026年Q2系统V1.3版本联调验证',
            'support_product': 'SAM302/SAM203全系列模组',
            'verify_function': '基础通信协议适配、数据上报格式兼容、设备管理接口对接、OTA升级通道验证',
            'performance_req': '数据上报成功率≥99.5%，设备在线率≥98%，OTA升级成功率≥99%',
            'created_at': now,
            'updated_at': now
        },
        # 集团系统侧需求2：V1.5升级约束（不前向兼容）
        {
            'title': '集团窄带物联网系统V1.5版本-协议升级适配约束（不前向兼容）',
            'req_type': '集团系统侧需求',
            'source_dept': '集团系统部',
            'customer': '集团窄带物联网平台',
            'description': '集团窄带物联网系统计划升级至V1.5版本，本次升级涉及核心通信协议重构，不前向兼容V1.3版本。所有需要接入V1.5系统的模组必须进行协议适配开发，包括新的鉴权机制、加密算法、数据帧格式、低功耗唤醒机制等。新用户项目（园林局、两江卫通）要求直接使用V1.5适配版本模组。',
            'quantity': 0,
            'priority': '高',
            'status': '进行中',
            'delivery_date': '2026-09-30',
            'product_model': '',
            'system_verify_point': '2026年Q3系统V1.5版本联调验证',
            'support_product': 'SAM302/SAM203全系列模组（需适配V1.5）',
            'verify_function': 'V1.5新协议适配、新鉴权机制对接、国密加密算法集成、新数据帧格式兼容、低功耗唤醒机制验证、V1.3/V1.5双模式切换',
            'performance_req': 'V1.5协议下数据上报时延≤200ms，鉴权成功率≥99.9%，低功耗模式下待机电流≤5uA',
            'created_at': now,
            'updated_at': now
        }
    ]

    req_ids = {}
    for r in requirements:
        # 构建INSERT语句（动态字段）
        fields = []
        values = []
        for k, v in r.items():
            fields.append(k)
            values.append(v)
        placeholders = ','.join(['?'] * len(fields))
        cursor.execute(f'INSERT INTO requirements ({",".join(fields)}) VALUES ({placeholders})', values)
        req_ids[r['title'][:20]] = cursor.lastrowid
        print(f'创建需求: {r["title"][:30]}... (ID={cursor.lastrowid}, 类型={r["req_type"]})')

    conn.commit()
    return req_ids

def create_versions(conn, cursor, product_ids, req_ids):
    """为每款产品创建2个版本：V1.3基础版本 + V1.5适配版本"""
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    versions = [
        # SAM302 V1.3基础版本
        {
            'product_id': product_ids['SAM302'],
            'version_no': 'V1.3.0',
            'version_name': 'V1.3基础适配版',
            'description': '适配集团窄带物联网系统V1.3版本的基础固件，支持基础通信协议、数据上报、设备管理、OTA升级等功能。为当前量产稳定版本。',
            'capabilities': 'NB-IoT/Cat.M双制式通信、V1.3基础协议适配、数据上报、设备管理接口、OTA升级、低功耗模式',
            'planned_release_date': '2026-06-15',
            'actual_release_date': '2026-06-20',
            'status': '已发布',
            'req_titles': ['集团窄带物联网系统V1.3版本-基础功能适配约束']
        },
        # SAM302 V1.5适配版本
        {
            'product_id': product_ids['SAM302'],
            'version_no': 'V1.5.0',
            'version_name': 'V1.5协议适配版（不前向兼容）',
            'description': '适配集团窄带物联网系统V1.5版本的固件，因V1.5协议重构不前向兼容，需进行全新协议适配。集成国密加密、新鉴权机制、低功耗唤醒等新特性。园林局项目要求使用此版本。',
            'capabilities': 'V1.5新协议适配、国密SM2/SM4加密、新鉴权机制、新数据帧格式、低功耗唤醒、V1.3/V1.5双模式切换、高速率数据传输',
            'planned_release_date': '2026-09-25',
            'actual_release_date': '',
            'status': '测试中',
            'req_titles': ['集团窄带物联网系统V1.5版本-协议升级适配约束（不前向兼容）', '园林局窄带物联网数据采集项目-SAM302模组批量需求']
        },
        # SAM203 V1.3基础版本
        {
            'product_id': product_ids['SAM203'],
            'version_no': 'V1.3.0',
            'version_name': 'V1.3基础适配版',
            'description': '适配集团窄带物联网系统V1.3版本的手持终端模组固件，支持GNSS定位、蓝牙、NFC、基础通信等功能。为当前量产稳定版本。',
            'capabilities': 'NB-IoT通信、GNSS定位、蓝牙5.0、NFC、V1.3基础协议适配、数据上报、设备管理、OTA升级',
            'planned_release_date': '2026-07-10',
            'actual_release_date': '2026-07-15',
            'status': '已发布',
            'req_titles': ['集团窄带物联网系统V1.3版本-基础功能适配约束']
        },
        # SAM203 V1.5适配版本
        {
            'product_id': product_ids['SAM203'],
            'version_no': 'V1.5.0',
            'version_name': 'V1.5协议适配版（不前向兼容）',
            'description': '适配集团窄带物联网系统V1.5版本的手持终端固件，因V1.5协议重构不前向兼容，需进行全新协议适配。集成国密加密、新鉴权机制、低功耗唤醒等新特性。两江卫通项目要求使用此版本。',
            'capabilities': 'V1.5新协议适配、国密加密、新鉴权机制、GNSS定位增强、蓝牙数据传输优化、低功耗唤醒、V1.3/V1.5双模式切换',
            'planned_release_date': '2026-09-30',
            'actual_release_date': '',
            'status': '开发中',
            'req_titles': ['集团窄带物联网系统V1.5版本-协议升级适配约束（不前向兼容）', '两江卫通卫星通信手持终端项目-SAM203模组需求']
        }
    ]

    version_ids = {}
    for v in versions:
        req_titles = v.pop('req_titles')
        fields = list(v.keys())
        values = [v[f] for f in fields]
        placeholders = ','.join(['?'] * len(fields))
        cursor.execute(f'INSERT INTO software_versions ({",".join(fields)}, created_at) VALUES ({placeholders},?)', values + [now])
        vid = cursor.lastrowid
        version_ids[f"{v['version_no']}_{v['product_id']}"] = vid
        print(f'创建版本: {v["version_no"]} {v["version_name"]} (ID={vid}, 状态={v["status"]})')

        # 关联需求
        for req_title in req_titles:
            for key, rid in req_ids.items():
                if req_title.startswith(key[:15]):
                    try:
                        cursor.execute('INSERT INTO version_requirements (version_id, requirement_id) VALUES (?,?)', (vid, rid))
                        print(f'  关联需求: {req_title[:25]}...')
                    except Exception as e:
                        pass
                    break

    conn.commit()
    return version_ids

def create_bugs(conn, cursor, product_ids, version_ids):
    """创建bug虚拟数据"""
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    bugs = [
        {
            'bug_no': 'BUG-2026-001',
            'product_id': product_ids['SAM302'],
            'version_id': version_ids.get('V1.5.0_' + str(product_ids['SAM302'])),
            'title': 'V1.5协议下偶发鉴权失败',
            'description': '在V1.5协议适配版本测试中，发现模组在网络信号较弱环境下（RSRP<-110dBm）偶发鉴权失败，失败率约3%，重试后可恢复。初步分析为新鉴权机制中超时参数设置不合理。',
            'bug_type': '功能缺陷',
            'severity': '中',
            'priority': '高',
            'status': '处理中',
            'reporter': '测试组-张工',
            'assignee': '研发组-李工',
            'discovered_at': '2026-09-05',
            'root_cause': '新鉴权机制中等待服务器响应的超时时间设置为2秒，弱网环境下服务器响应可能超过2秒导致超时',
            'fix_solution': '将鉴权超时时间调整为5秒，并增加自动重试机制（最多重试3次）',
        },
        {
            'bug_no': 'BUG-2026-002',
            'product_id': product_ids['SAM302'],
            'version_id': version_ids.get('V1.5.0_' + str(product_ids['SAM302'])),
            'title': '低功耗唤醒后数据上报丢失',
            'description': 'V1.5版本低功耗模式下，模组从深度睡眠唤醒后，首次数据上报存在约5%的丢包率。唤醒后立即上报的数据帧可能因射频模块未完全稳定而发送失败。',
            'bug_type': '功能缺陷',
            'severity': '高',
            'priority': '高',
            'status': '待处理',
            'reporter': '测试组-王工',
            'assignee': '研发组-赵工',
            'discovered_at': '2026-09-08',
        },
        {
            'bug_no': 'BUG-2026-003',
            'product_id': product_ids['SAM203'],
            'version_id': version_ids.get('V1.5.0_' + str(product_ids['SAM203'])),
            'title': 'V1.5协议下GNSS定位漂移',
            'description': 'SAM203模组V1.5适配版本中，GNSS定位在V1.5协议激活状态下出现定位漂移现象，定位精度从5米下降到15米左右。分析为V1.5协议射频参数与GNSS模块存在干扰。',
            'bug_type': '性能问题',
            'severity': '中',
            'priority': '中',
            'status': '处理中',
            'reporter': '测试组-刘工',
            'assignee': '研发组-陈工',
            'discovered_at': '2026-09-06',
            'root_cause': 'V1.5协议射频发射功率参数与GNSS模块LNA频段存在邻道干扰',
            'fix_solution': '调整射频发射功率回退3dB，优化GNSS工作时段的射频调度策略',
        },
        {
            'bug_no': 'BUG-2026-004',
            'product_id': product_ids['SAM302'],
            'version_id': version_ids.get('V1.3.0_' + str(product_ids['SAM302'])),
            'title': 'V1.3版本OTA升级后配置丢失',
            'description': 'V1.3已发布版本中，部分模组通过OTA升级后，用户自定义配置参数（如APN、上报周期）被重置为默认值。影响已部署设备的正常运行，需现场重新配置。',
            'bug_type': '功能缺陷',
            'severity': '高',
            'priority': '高',
            'status': '已修复',
            'reporter': '交付服务组-周工',
            'assignee': '研发组-李工',
            'discovered_at': '2026-08-15',
            'closed_at': '2026-08-25',
            'close_reason': '已修复并验证通过',
            'fix_solution': 'OTA升级流程中增加配置参数备份与恢复机制，升级前自动备份用户配置，升级完成后自动恢复',
            'test_result': '验证通过，100台设备OTA升级后配置均正常保留',
            'root_cause': 'OTA升级时Flash擦除范围包含了用户配置存储区域',
            'corrective_action': '修改Flash分区布局，用户配置区域独立于固件区域',
            'prevention_measure': 'OTA升级测试用例中增加配置保留验证项',
        },
        {
            'bug_no': 'BUG-2026-005',
            'product_id': product_ids['SAM203'],
            'version_id': version_ids.get('V1.3.0_' + str(product_ids['SAM203'])),
            'title': '蓝牙连接断开后无法自动重连',
            'description': 'V1.3版本SAM203模组蓝牙功能中，当蓝牙连接因距离过远或干扰断开后，模组无法自动重新连接已配对设备，需要手动重启蓝牙功能。影响手持终端的连续使用体验。',
            'bug_type': '功能缺陷',
            'severity': '低',
            'priority': '中',
            'status': '已修复',
            'reporter': '测试组-孙工',
            'assignee': '研发组-陈工',
            'discovered_at': '2026-08-20',
            'closed_at': '2026-09-01',
            'close_reason': '已修复并验证通过',
            'fix_solution': '增加蓝牙自动重连机制，断开后每5秒尝试重连已配对设备，最多重试10次',
            'test_result': '验证通过，10次断开测试均能在30秒内自动重连',
        },
    ]

    for b in bugs:
        fields = list(b.keys())
        values = [b[f] for f in fields]
        placeholders = ','.join(['?'] * len(fields))
        cursor.execute(f'INSERT INTO bug_records ({",".join(fields)}, created_at) VALUES ({placeholders},?)', values + [now])
        print(f'创建BUG: {b["bug_no"]} {b["title"][:25]}... (状态={b["status"]})')

    conn.commit()

def create_production_schedules(conn, cursor, product_ids, req_ids):
    """创建生产排程：对应分批交付"""
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    # 找到需求ID
    yuanlin_req_id = None
    liangjiang_req_id = None
    for key, rid in req_ids.items():
        if '园林局' in key:
            yuanlin_req_id = rid
        elif '两江卫通' in key:
            liangjiang_req_id = rid

    schedules = [
        # 园林局第一批：200套（已完成）
        {
            'schedule_no': 'PS20260825001',
            'product_id': product_ids['SAM302'],
            'requirement_id': yuanlin_req_id,
            'quantity': 200,
            'plan_start': '2026-08-25',
            'plan_finish': '2026-09-05',
            'status': '已完成',
            'remark': '园林局项目第一批交付200套，已完成生产并交付'
        },
        # 园林局第二批：300套（生产中）
        {
            'schedule_no': 'PS20260910001',
            'product_id': product_ids['SAM302'],
            'requirement_id': yuanlin_req_id,
            'quantity': 300,
            'plan_start': '2026-09-10',
            'plan_finish': '2026-09-22',
            'status': '生产中',
            'remark': '园林局项目第二批交付300套，正在生产中'
        },
        # 园林局第三批：1000套（规划中）
        {
            'schedule_no': 'PS20261008001',
            'product_id': product_ids['SAM302'],
            'requirement_id': yuanlin_req_id,
            'quantity': 1000,
            'plan_start': '2026-10-08',
            'plan_finish': '2026-10-28',
            'status': '规划中',
            'remark': '园林局项目第三批交付1000套，待生产排期'
        },
        # 两江卫通：210套（生产中）
        {
            'schedule_no': 'PS20260915001',
            'product_id': product_ids['SAM203'],
            'requirement_id': liangjiang_req_id,
            'quantity': 210,
            'plan_start': '2026-09-15',
            'plan_finish': '2026-10-08',
            'status': '生产中',
            'remark': '两江卫通项目一次性交付210套，正在生产中'
        }
    ]

    for s in schedules:
        cursor.execute('''INSERT INTO production_schedules (schedule_no, product_id, requirement_id, quantity, plan_start, plan_finish, status, remark, created_at)
                          VALUES (?,?,?,?,?,?,?,?,?)''',
                       (s['schedule_no'], s['product_id'], s['requirement_id'], s['quantity'], s['plan_start'], s['plan_finish'], s['status'], s['remark'], now))
        sid = cursor.lastrowid
        print(f'创建生产排程: {s["schedule_no"]} {s["quantity"]}套 (状态={s["status"]})')

        # 自动生成工序
        processes = [('SMT贴片', 1), ('回流焊接', 2), ('AOI检测', 3), ('功能测试', 4), ('外观检验', 5), ('包装入库', 6)]
        for name, seq in processes:
            status = '已完成' if s['status'] == '已完成' else ('进行中' if s['status'] == '生产中' and seq <= 3 else '待开始')
            cursor.execute('INSERT INTO production_processes (schedule_id, process_name, sequence, status) VALUES (?,?,?,?)', (sid, name, seq, status))

    conn.commit()

def main():
    print('=' * 60)
    print('开始重新生成虚拟数据...')
    print('=' * 60)

    conn, cursor = reset_data()
    product_ids = create_products(conn, cursor)
    req_ids = create_requirements(conn, cursor)
    version_ids = create_versions(conn, cursor, product_ids, req_ids)
    create_bugs(conn, cursor, product_ids, version_ids)
    create_production_schedules(conn, cursor, product_ids, req_ids)

    # 统计
    print()
    print('=' * 60)
    print('数据生成完成！统计如下：')
    cursor.execute('SELECT COUNT(*) FROM products')
    print(f'  产品: {cursor.fetchone()[0]}款')
    cursor.execute('SELECT COUNT(*) FROM requirements')
    print(f'  需求: {cursor.fetchone()[0]}条')
    cursor.execute('SELECT req_type, COUNT(*) FROM requirements GROUP BY req_type')
    for row in cursor.fetchall():
        print(f'    - {row[0]}: {row[1]}条')
    cursor.execute('SELECT COUNT(*) FROM software_versions')
    print(f'  软件版本: {cursor.fetchone()[0]}个')
    cursor.execute('SELECT COUNT(*) FROM bug_records')
    print(f'  BUG记录: {cursor.fetchone()[0]}个')
    cursor.execute('SELECT COUNT(*) FROM production_schedules')
    print(f'  生产排程: {cursor.fetchone()[0]}个')
    print('=' * 60)

    conn.close()

if __name__ == '__main__':
    main()
