#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""用真实业务场景重新生成虚拟数据"""

import sqlite3
from datetime import datetime

DB_PATH = 'business.db'
NOW = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

def reset_data():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    tables = [
        'timeline_events', 'test_plans', 'delivery_batches', 'version_features',
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
        except:
            pass
    conn.commit()
    print('已清空所有业务数据')
    return conn, cursor

def create_products(conn, cursor):
    products = [
        {
            'name': 'SAM302窄带高速率模组',
            'model': 'SAM302',
            'version': 'V1.0',
            'description': '窄带物联网高速率通信模组，支持NB-IoT/Cat.M双制式，适用于大规模数据采集场景，下行速率可达1Mbps，支持低功耗模式和OTA远程升级。',
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
    pids = {}
    for p in products:
        cursor.execute('''INSERT INTO products (name, model, version, description, lifecycle_stage, stocking_strategy, created_at)
                          VALUES (?,?,?,?,?,?,?)''',
                       (p['name'], p['model'], p['version'], p['description'], p['lifecycle_stage'], p['stocking_strategy'], NOW))
        pids[p['model']] = cursor.lastrowid
        print(f'创建产品: {p["name"]} (ID={cursor.lastrowid})')
    conn.commit()
    return pids

def create_requirements(conn, cursor, pids):
    reqs = [
        # 集团系统侧需求：V1.3
        {
            'title': '集团窄带物联网系统V1.3版本-基础功能适配需求',
            'req_type': '集团系统侧需求',
            'source_dept': '集团系统部',
            'customer': '集团窄带物联网平台',
            'description': '集团窄带物联网系统于2026年2月升级至V1.3版本，要求所有在网模组适配新的基础通信协议、数据上报格式、设备管理接口。V1.3为当前稳定运行版本，是所有模组的基础适配版本。',
            'quantity': 0,
            'priority': '中',
            'status': '已完成',
            'delivery_date': '2026-02-28',
            'system_verify_point': '2026年2月系统V1.3版本联调验证',
            'support_product': 'SAM302/SAM203全系列模组',
            'verify_function': '基础通信协议适配、数据上报格式兼容、设备管理接口对接、OTA升级通道验证',
            'performance_req': '数据上报成功率≥99.5%，设备在线率≥98%，OTA升级成功率≥99%',
        },
        # 集团系统侧需求：V1.5
        {
            'title': '集团窄带物联网系统V1.5版本-协议升级适配需求（不前向兼容）',
            'req_type': '集团系统侧需求',
            'source_dept': '集团系统部',
            'customer': '集团窄带物联网平台',
            'description': '集团窄带物联网系统于2026年8月升级至V1.5版本，本次升级涉及核心通信协议重构，不前向兼容V1.3版本。所有需要接入V1.5系统的模组必须进行协议适配开发，包括：1) 开发原本V1.3的全部能力；2) 开发新功能（系统需要逐步开放，并需要模组配合验证）：闭环功率控制、空闲态调度、紧急呼叫、异网漫游。',
            'quantity': 0,
            'priority': '高',
            'status': '进行中',
            'delivery_date': '2026-08-31',
            'system_verify_point': '2026年8月系统V1.5版本联调验证',
            'support_product': 'SAM302/SAM203全系列模组（需适配V1.5）',
            'verify_function': 'V1.5新协议适配、新鉴权机制对接、国密加密算法集成、新数据帧格式兼容、低功耗唤醒机制验证、闭环功率控制、空闲态调度、紧急呼叫、异网漫游、V1.3/V1.5双模式切换',
            'performance_req': 'V1.5协议下数据上报时延≤200ms，鉴权成功率≥99.9%，低功耗模式下待机电流≤5uA，闭环功率控制精度±1dB，异网漫游切换时间≤3s',
        },
        # 用户需求：园林局
        {
            'title': '园林局窄带物联网数据采集项目-SAM302模组批量需求',
            'req_type': '用户需求',
            'source_dept': '华东市场团队',
            'customer': '园林局',
            'description': '园林局窄带物联网数据采集项目，需要SAM302窄带高速率模组1500套，用于园区内环境监测、植被数据采集、智能灌溉、病虫害监测预警等场景。项目要求分批交付，各批次对应不同软件版本。',
            'quantity': 1500,
            'priority': '高',
            'status': '进行中',
            'delivery_date': '2026-10-20',
            'product_model': 'SAM302',
            'batch_delivery': '是',
            'batch_detail': '第1批：9月10日交付V1.3基础版200套；第2批：9月27日交付V1.5版本500套；第3批：10月20日交付V1.5版本800套',
            'application_scenario': '园林局园区环境监测、植被数据采集、智能灌溉控制系统、病虫害监测预警',
        },
        # 用户需求：两江卫通
        {
            'title': '两江卫通卫星通信手持终端项目-SAM203模组需求',
            'req_type': '用户需求',
            'source_dept': '华南市场团队',
            'customer': '两江卫通',
            'description': '两江卫通卫星通信手持终端项目，需要SAM203窄带手持模组210套，用于卫星通信手持终端的定位、近场通信、蓝牙数据传输等功能。要求一次性交付完毕。',
            'quantity': 210,
            'priority': '高',
            'status': '进行中',
            'delivery_date': '2026-10-12',
            'product_model': 'SAM203',
            'batch_delivery': '否',
            'application_scenario': '卫星通信手持终端、应急通信设备、野外作业人员定位与通信',
        }
    ]
    rids = {}
    for r in reqs:
        fields = list(r.keys())
        values = [r[f] for f in fields]
        placeholders = ','.join(['?'] * len(fields))
        cursor.execute(f'INSERT INTO requirements ({",".join(fields)}, created_at, updated_at) VALUES ({placeholders},?,?)', values + [NOW, NOW])
        rids[r['title'][:25]] = cursor.lastrowid
        print(f'创建需求: {r["title"][:35]}... (ID={cursor.lastrowid})')
    conn.commit()
    return rids

def create_versions(conn, cursor, pids, rids):
    versions = [
        # SAM302 V1.3
        {
            'product_id': pids['SAM302'],
            'version_no': 'V1.3.0',
            'version_name': 'V1.3基础适配版',
            'description': '适配集团窄带物联网系统V1.3版本的基础固件，支持基础通信协议、数据上报、设备管理、OTA远程升级等功能。2026年9月10日已完成版本发布。',
            'capabilities': 'NB-IoT/Cat.M双制式通信、V1.3基础协议适配、数据上报、设备管理接口、OTA远程升级、低功耗模式',
            'system_version': 'V1.3',
            'is_forward_compatible': 1,
            'ota_supported': 1,
            'planned_release_date': '2026-09-10',
            'actual_release_date': '2026-09-10',
            'status': '已发布',
            'req_keys': ['集团窄带物联网系统V1.3版本-基础功能适配需求']
        },
        # SAM302 V1.5
        {
            'product_id': pids['SAM302'],
            'version_no': 'V1.5.0',
            'version_name': 'V1.5协议适配版（不前向兼容）',
            'description': '适配集团窄带物联网系统V1.5版本的固件，因V1.5协议重构不前向兼容，需进行全新协议适配。集成V1.3全部能力，并新增闭环功率控制、空闲态调度、紧急呼叫、异网漫游等功能。计划9月18日完成版本发布。',
            'capabilities': 'V1.5新协议适配、国密加密、新鉴权机制、新数据帧格式、低功耗唤醒、V1.3/V1.5双模式切换、闭环功率控制、空闲态调度、紧急呼叫、异网漫游、高速率数据传输',
            'system_version': 'V1.5',
            'is_forward_compatible': 0,
            'ota_supported': 1,
            'planned_release_date': '2026-09-18',
            'actual_release_date': '',
            'status': '测试中',
            'req_keys': ['集团窄带物联网系统V1.5版本-协议升级适配需求（不前向兼容）', '园林局窄带物联网数据采集项目-SAM302模组批量需求']
        },
        # SAM203 V1.3
        {
            'product_id': pids['SAM203'],
            'version_no': 'V1.3.0',
            'version_name': 'V1.3基础适配版',
            'description': '适配集团窄带物联网系统V1.3版本的手持终端模组固件，支持GNSS定位、蓝牙、NFC、基础通信等功能。',
            'capabilities': 'NB-IoT通信、GNSS定位、蓝牙5.0、NFC、V1.3基础协议适配、数据上报、设备管理、OTA升级',
            'system_version': 'V1.3',
            'is_forward_compatible': 1,
            'ota_supported': 1,
            'planned_release_date': '2026-07-10',
            'actual_release_date': '2026-07-15',
            'status': '已发布',
            'req_keys': ['集团窄带物联网系统V1.3版本-基础功能适配需求']
        },
        # SAM203 V1.5
        {
            'product_id': pids['SAM203'],
            'version_no': 'V1.5.0',
            'version_name': 'V1.5协议适配版（不前向兼容）',
            'description': '适配集团窄带物联网系统V1.5版本的手持终端固件，因V1.5协议重构不前向兼容，需进行全新协议适配。集成国密加密、新鉴权机制、低功耗唤醒等新特性。',
            'capabilities': 'V1.5新协议适配、国密加密、新鉴权机制、GNSS定位增强、蓝牙数据传输优化、低功耗唤醒、V1.3/V1.5双模式切换',
            'system_version': 'V1.5',
            'is_forward_compatible': 0,
            'ota_supported': 1,
            'planned_release_date': '2026-09-30',
            'actual_release_date': '',
            'status': '开发中',
            'req_keys': ['集团窄带物联网系统V1.5版本-协议升级适配需求（不前向兼容）', '两江卫通卫星通信手持终端项目-SAM203模组需求']
        }
    ]
    vids = {}
    for v in versions:
        req_keys = v.pop('req_keys')
        fields = list(v.keys())
        values = [v[f] for f in fields]
        placeholders = ','.join(['?'] * len(fields))
        cursor.execute(f'INSERT INTO software_versions ({",".join(fields)}, created_at) VALUES ({placeholders},?)', values + [NOW])
        vid = cursor.lastrowid
        vids[f"{v['version_no']}_{v['product_id']}"] = vid
        print(f'创建版本: {v["version_no"]} {v["version_name"][:20]} (ID={vid})')

        # 关联需求
        for req_key in req_keys:
            for key, rid in rids.items():
                if req_key.startswith(key[:15]):
                    try:
                        cursor.execute('INSERT INTO version_requirements (version_id, requirement_id) VALUES (?,?)', (vid, rid))
                    except:
                        pass
                    break
    conn.commit()
    return vids

def create_version_features(conn, cursor, vids, pids):
    """创建版本功能清单"""
    # SAM302 V1.5的新功能
    sam302_v15_id = vids.get('V1.5.0_' + str(pids['SAM302']))
    features = [
        {'version_id': sam302_v15_id, 'feature_name': '闭环功率控制', 'feature_desc': '根据网络侧指令动态调整发射功率，实现闭环功率控制，精度±1dB', 'status': '开发中', 'priority': '高'},
        {'version_id': sam302_v15_id, 'feature_name': '空闲态调度', 'feature_desc': '优化空闲态资源调度机制，降低待机功耗，提升电池续航', 'status': '开发中', 'priority': '高'},
        {'version_id': sam302_v15_id, 'feature_name': '紧急呼叫', 'feature_desc': '支持紧急呼叫功能，在网络拥塞时优先接入，保障紧急通信', 'status': '未开始', 'priority': '中'},
        {'version_id': sam302_v15_id, 'feature_name': '异网漫游', 'feature_desc': '支持异网漫游切换，切换时间≤3s，保障跨网络连续通信', 'status': '未开始', 'priority': '中'},
        {'version_id': sam302_v15_id, 'feature_name': 'V1.3全部能力继承', 'feature_desc': '继承V1.3版本的全部基础通信能力，包括基础协议适配、数据上报、设备管理等', 'status': '已完成', 'priority': '高'},
        {'version_id': sam302_v15_id, 'feature_name': '国密加密算法集成', 'feature_desc': '集成国密SM2/SM4加密算法，提升通信安全性', 'status': '已完成', 'priority': '高'},
        {'version_id': sam302_v15_id, 'feature_name': '新鉴权机制对接', 'feature_desc': '对接V1.5新鉴权机制，鉴权成功率≥99.9%', 'status': '已完成', 'priority': '高'},
        {'version_id': sam302_v15_id, 'feature_name': 'OTA远程升级', 'feature_desc': '支持OTA远程升级功能，可通过远程方式升级模组固件', 'status': '已完成', 'priority': '高'},
    ]

    # SAM302 V1.3的功能
    sam302_v13_id = vids.get('V1.3.0_' + str(pids['SAM302']))
    features.extend([
        {'version_id': sam302_v13_id, 'feature_name': 'V1.3基础协议适配', 'feature_desc': '适配V1.3基础通信协议', 'status': '已完成', 'priority': '高'},
        {'version_id': sam302_v13_id, 'feature_name': '数据上报', 'feature_desc': '支持数据上报功能', 'status': '已完成', 'priority': '高'},
        {'version_id': sam302_v13_id, 'feature_name': 'OTA远程升级', 'feature_desc': '支持OTA远程升级功能', 'status': '已完成', 'priority': '高'},
    ])

    for f in features:
        cursor.execute('''INSERT INTO version_features (version_id, feature_name, feature_desc, status, priority, created_at, updated_at)
                          VALUES (?,?,?,?,?,?,?)''',
                       (f['version_id'], f['feature_name'], f['feature_desc'], f['status'], f['priority'], NOW, NOW))
    print(f'创建版本功能清单: {len(features)}项')
    conn.commit()

def create_dev_tasks(conn, cursor, vids, pids, rids):
    """创建研发任务"""
    sam302_v15_id = vids.get('V1.5.0_' + str(pids['SAM302']))
    sam302_v13_id = vids.get('V1.3.0_' + str(pids['SAM302']))

    tasks = [
        # V1.3版本任务（已完成）
        {'version_id': sam302_v13_id, 'task_name': 'V1.3基础协议适配开发', 'description': '完成V1.3基础通信协议适配开发', 'priority': '高', 'status': '已完成', 'assignee': '研发组-李工', 'planned_start': '2026-08-20', 'planned_finish': '2026-09-05', 'actual_finish': '2026-09-03', 'task_type': '功能开发'},
        {'version_id': sam302_v13_id, 'task_name': 'OTA远程升级功能开发', 'description': '开发OTA远程升级功能，支持远程固件升级', 'priority': '高', 'status': '已完成', 'assignee': '研发组-王工', 'planned_start': '2026-08-25', 'planned_finish': '2026-09-08', 'actual_finish': '2026-09-07', 'task_type': '功能开发'},
        {'version_id': sam302_v13_id, 'task_name': 'V1.3版本发布', 'description': '完成V1.3版本测试验证并发布', 'priority': '高', 'status': '已完成', 'assignee': '研发组-李工', 'planned_start': '2026-09-08', 'planned_finish': '2026-09-10', 'actual_finish': '2026-09-10', 'task_type': '版本发布'},

        # V1.5版本任务
        {'version_id': sam302_v15_id, 'task_name': 'V1.5新协议适配开发', 'description': '完成V1.5新通信协议适配开发，包括新鉴权机制、国密加密、新数据帧格式', 'priority': '高', 'status': '已完成', 'assignee': '研发组-李工', 'planned_start': '2026-08-15', 'planned_finish': '2026-09-05', 'actual_finish': '2026-09-04', 'task_type': '功能开发'},
        {'version_id': sam302_v15_id, 'task_name': '闭环功率控制功能开发', 'description': '开发闭环功率控制功能，精度±1dB', 'priority': '高', 'status': '开发中', 'assignee': '研发组-赵工', 'planned_start': '2026-09-01', 'planned_finish': '2026-09-12', 'task_type': '功能开发'},
        {'version_id': sam302_v15_id, 'task_name': '空闲态调度功能开发', 'description': '开发空闲态调度功能，优化待机功耗', 'priority': '高', 'status': '开发中', 'assignee': '研发组-陈工', 'planned_start': '2026-09-01', 'planned_finish': '2026-09-12', 'task_type': '功能开发'},
        {'version_id': sam302_v15_id, 'task_name': '紧急呼叫功能开发', 'description': '开发紧急呼叫功能，网络拥塞时优先接入', 'priority': '中', 'status': '未开始', 'assignee': '研发组-李工', 'planned_start': '2026-09-10', 'planned_finish': '2026-09-15', 'task_type': '功能开发'},
        {'version_id': sam302_v15_id, 'task_name': '异网漫游功能开发', 'description': '开发异网漫游功能，切换时间≤3s', 'priority': '中', 'status': '未开始', 'assignee': '研发组-王工', 'planned_start': '2026-09-10', 'planned_finish': '2026-09-15', 'task_type': '功能开发'},
        {'version_id': sam302_v15_id, 'task_name': 'V1.5版本催熟与实网测试', 'description': '版本催熟，实网完成测试与bug修复，确保版本稳定', 'priority': '高', 'status': '进行中', 'assignee': '研发组-李工', 'planned_start': '2026-09-13', 'planned_finish': '2026-09-18', 'task_type': '版本催熟'},
        {'version_id': sam302_v15_id, 'task_name': 'V1.5版本发布', 'description': '完成V1.5版本测试验证并发布，具备可发布状态', 'priority': '高', 'status': '待开始', 'assignee': '研发组-李工', 'planned_start': '2026-09-17', 'planned_finish': '2026-09-18', 'task_type': '版本发布'},
    ]

    for t in tasks:
        fields = list(t.keys())
        values = [t[f] for f in fields]
        placeholders = ','.join(['?'] * len(fields))
        cursor.execute(f'INSERT INTO dev_tasks ({",".join(fields)}, created_at) VALUES ({placeholders},?)', values + [NOW])
    print(f'创建研发任务: {len(tasks)}个')
    conn.commit()

def create_bugs(conn, cursor, vids, pids):
    """创建BUG记录"""
    sam302_v15_id = vids.get('V1.5.0_' + str(pids['SAM302']))
    sam302_v13_id = vids.get('V1.3.0_' + str(pids['SAM302']))

    bugs = [
        {'bug_no': 'BUG-2026-001', 'product_id': pids['SAM302'], 'version_id': sam302_v15_id, 'title': 'V1.5协议下弱网环境偶发鉴权失败', 'description': '在V1.5协议适配版本测试中，发现模组在网络信号较弱环境下（RSRP<-110dBm）偶发鉴权失败，失败率约3%，重试后可恢复。', 'bug_type': '功能缺陷', 'severity': '中', 'priority': '高', 'status': '处理中', 'reporter': '测试组-张工', 'assignee': '研发组-李工', 'discovered_at': '2026-09-14', 'root_cause': '新鉴权机制中超时参数设置不合理，弱网环境下服务器响应超时', 'fix_solution': '将鉴权超时时间调整为5秒，增加自动重试机制'},
        {'bug_no': 'BUG-2026-002', 'product_id': pids['SAM302'], 'version_id': sam302_v15_id, 'title': '低功耗唤醒后首次数据上报丢包', 'description': 'V1.5版本低功耗模式下，模组从深度睡眠唤醒后，首次数据上报存在约5%的丢包率。', 'bug_type': '功能缺陷', 'severity': '高', 'priority': '高', 'status': '待处理', 'reporter': '测试组-王工', 'assignee': '研发组-赵工', 'discovered_at': '2026-09-15'},
        {'bug_no': 'BUG-2026-003', 'product_id': pids['SAM302'], 'version_id': sam302_v15_id, 'title': '闭环功率控制精度不达标', 'description': '闭环功率控制功能测试中，功率控制精度为±2dB，未达到±1dB的设计要求。', 'bug_type': '性能问题', 'severity': '中', 'priority': '中', 'status': '处理中', 'reporter': '测试组-刘工', 'assignee': '研发组-陈工', 'discovered_at': '2026-09-15', 'root_cause': '功率检测电路校准参数偏差', 'fix_solution': '重新校准功率检测电路参数，优化控制算法'},
        {'bug_no': 'BUG-2026-004', 'product_id': pids['SAM302'], 'version_id': sam302_v13_id, 'title': 'V1.3版本OTA升级后配置丢失', 'description': 'V1.3已发布版本中，部分模组通过OTA升级后，用户自定义配置参数被重置为默认值。', 'bug_type': '功能缺陷', 'severity': '高', 'priority': '高', 'status': '已修复', 'reporter': '交付服务组-周工', 'assignee': '研发组-李工', 'discovered_at': '2026-08-20', 'closed_at': '2026-09-01', 'close_reason': '已修复并验证通过', 'fix_solution': 'OTA升级流程中增加配置参数备份与恢复机制', 'test_result': '验证通过，100台设备OTA升级后配置均正常保留', 'root_cause': 'OTA升级时Flash擦除范围包含了用户配置存储区域', 'corrective_action': '修改Flash分区布局，用户配置区域独立', 'prevention_measure': 'OTA升级测试用例中增加配置保留验证项'},
    ]

    for b in bugs:
        fields = list(b.keys())
        values = [b[f] for f in fields]
        placeholders = ','.join(['?'] * len(fields))
        cursor.execute(f'INSERT INTO bug_records ({",".join(fields)}, created_at) VALUES ({placeholders},?)', values + [NOW])
    print(f'创建BUG记录: {len(bugs)}个')
    conn.commit()

def create_production_schedules(conn, cursor, pids, rids, vids):
    """创建生产排程"""
    # 找到需求ID
    yuanlin_req_id = None
    for key, rid in rids.items():
        if '园林局' in key:
            yuanlin_req_id = rid
            break

    sam302_v13_id = vids.get('V1.3.0_' + str(pids['SAM302']))
    sam302_v15_id = vids.get('V1.5.0_' + str(pids['SAM302']))

    schedules = [
        # 第1批：9月10日 V1.3 200套（已完成）
        {'schedule_no': 'PS20260901001', 'product_id': pids['SAM302'], 'requirement_id': yuanlin_req_id, 'quantity': 200, 'plan_start': '2026-09-01', 'plan_finish': '2026-09-08', 'status': '已完成', 'remark': '园林局项目第1批，V1.3基础版200套，9月10日交付', 'version_id': sam302_v13_id, 'flash_version_no': 'V1.3.0'},
        # 第2批：9月27日 V1.5 500套（生产中）
        {'schedule_no': 'PS20260920001', 'product_id': pids['SAM302'], 'requirement_id': yuanlin_req_id, 'quantity': 500, 'plan_start': '2026-09-20', 'plan_finish': '2026-09-25', 'status': '生产中', 'remark': '园林局项目第2批，V1.5版本500套，9月25日完成生产并刷新V1.5版本，9月27日交付', 'version_id': sam302_v15_id, 'flash_version_no': 'V1.5.0'},
        # 第3批：10月20日 V1.5 800套（规划中）
        {'schedule_no': 'PS20261010001', 'product_id': pids['SAM302'], 'requirement_id': yuanlin_req_id, 'quantity': 800, 'plan_start': '2026-10-10', 'plan_finish': '2026-10-18', 'status': '规划中', 'remark': '园林局项目第3批，V1.5版本800套，10月20日交付', 'version_id': sam302_v15_id, 'flash_version_no': 'V1.5.0'},
    ]

    for s in schedules:
        fields = list(s.keys())
        values = [s[f] for f in fields]
        placeholders = ','.join(['?'] * len(fields))
        cursor.execute(f'INSERT INTO production_schedules ({",".join(fields)}, created_at) VALUES ({placeholders},?)', values + [NOW])
        sid = cursor.lastrowid

        # 自动生成工序
        processes = [('SMT贴片', 1), ('回流焊接', 2), ('AOI检测', 3), ('功能测试', 4), ('外观检验', 5), ('版本刷新', 6), ('包装入库', 7)]
        for name, seq in processes:
            status = '已完成' if s['status'] == '已完成' else ('进行中' if s['status'] == '生产中' and seq <= 4 else '待开始')
            cursor.execute('INSERT INTO production_processes (schedule_id, process_name, sequence, status) VALUES (?,?,?,?)', (sid, name, seq, status))

    print(f'创建生产排程: {len(schedules)}个')
    conn.commit()

def create_delivery_batches(conn, cursor, rids, vids, pids):
    """创建分批交付明细"""
    yuanlin_req_id = None
    for key, rid in rids.items():
        if '园林局' in key:
            yuanlin_req_id = rid
            break

    sam302_v13_id = vids.get('V1.3.0_' + str(pids['SAM302']))
    sam302_v15_id = vids.get('V1.5.0_' + str(pids['SAM302']))

    batches = [
        {'requirement_id': yuanlin_req_id, 'batch_no': 1, 'delivery_date': '2026-09-10', 'quantity': 200, 'version_id': sam302_v13_id, 'version_no': 'V1.3.0', 'status': '已交付', 'remark': '第1批：V1.3基础版200套'},
        {'requirement_id': yuanlin_req_id, 'batch_no': 2, 'delivery_date': '2026-09-27', 'quantity': 500, 'version_id': sam302_v15_id, 'version_no': 'V1.5.0', 'status': '待交付', 'remark': '第2批：V1.5版本500套'},
        {'requirement_id': yuanlin_req_id, 'batch_no': 3, 'delivery_date': '2026-10-20', 'quantity': 800, 'version_id': sam302_v15_id, 'version_no': 'V1.5.0', 'status': '待交付', 'remark': '第3批：V1.5版本800套'},
    ]

    for b in batches:
        fields = list(b.keys())
        values = [b[f] for f in fields]
        placeholders = ','.join(['?'] * len(fields))
        cursor.execute(f'INSERT INTO delivery_batches ({",".join(fields)}, created_at, updated_at) VALUES ({placeholders},?,?)', values + [NOW, NOW])
    print(f'创建分批交付明细: {len(batches)}条')
    conn.commit()

def create_test_plans(conn, cursor, vids, pids):
    """创建测试计划"""
    sam302_v15_id = vids.get('V1.5.0_' + str(pids['SAM302']))
    sam302_v13_id = vids.get('V1.3.0_' + str(pids['SAM302']))

    plans = [
        {
            'plan_no': 'TP-2026-001',
            'version_id': sam302_v13_id,
            'product_id': pids['SAM302'],
            'title': 'SAM302 V1.3版本黑盒测试',
            'description': 'V1.3基础适配版本黑盒测试，包括基础通信、数据上报、OTA升级等功能验证',
            'test_type': '黑盒测试',
            'planned_start': '2026-09-05',
            'planned_finish': '2026-09-09',
            'actual_start': '2026-09-05',
            'actual_finish': '2026-09-09',
            'status': '已完成',
            'tester': '测试组-张工',
            'total_cases': 50,
            'passed_cases': 48,
            'failed_cases': 2,
            'blocked_cases': 0,
            'bug_count': 2,
        },
        {
            'plan_no': 'TP-2026-002',
            'version_id': sam302_v15_id,
            'product_id': pids['SAM302'],
            'title': 'SAM302 V1.5版本黑盒测试与版本催熟',
            'description': 'V1.5协议适配版本黑盒测试，包括新协议适配、闭环功率控制、空闲态调度、紧急呼叫、异网漫游等功能验证。9月13日研发提供版本，9月18日完成催熟版本并通过测试确认具备可发布状态。',
            'test_type': '黑盒测试',
            'planned_start': '2026-09-13',
            'planned_finish': '2026-09-18',
            'actual_start': '2026-09-13',
            'status': '进行中',
            'tester': '测试组-张工',
            'total_cases': 80,
            'passed_cases': 45,
            'failed_cases': 5,
            'blocked_cases': 2,
            'bug_count': 3,
        },
    ]

    for p in plans:
        fields = list(p.keys())
        values = [p[f] for f in fields]
        placeholders = ','.join(['?'] * len(fields))
        cursor.execute(f'INSERT INTO test_plans ({",".join(fields)}, created_at, updated_at) VALUES ({placeholders},?,?)', values + [NOW, NOW])
    print(f'创建测试计划: {len(plans)}个')
    conn.commit()

def create_timeline_events(conn, cursor, pids, vids, rids):
    """创建跨团队时间线事件"""
    sam302_id = pids['SAM302']
    sam302_v13_id = vids.get('V1.3.0_' + str(sam302_id))
    sam302_v15_id = vids.get('V1.5.0_' + str(sam302_id))

    yuanlin_req_id = None
    for key, rid in rids.items():
        if '园林局' in key:
            yuanlin_req_id = rid
            break

    events = [
        # 研发团队
        {'event_date': '2026-09-10', 'event_type': '版本发布', 'title': 'V1.3版本发布', 'description': '完成V1.3版本发布，具备V1.3功能和OTA远程升级功能', 'team': '研发组', 'status': '已完成', 'product_id': sam302_id, 'version_id': sam302_v13_id},
        {'event_date': '2026-09-13', 'event_type': '版本提测', 'title': 'V1.5版本提测', 'description': '研发团队提供完成开发的V1.5模组版本，提交测试', 'team': '研发组', 'status': '已完成', 'product_id': sam302_id, 'version_id': sam302_v15_id},
        {'event_date': '2026-09-18', 'event_type': '版本发布', 'title': 'V1.5版本发布', 'description': '完成V1.5版本开发和版本发布，完成版本催熟，实网完成测试与bug修复', 'team': '研发组', 'status': '计划中', 'product_id': sam302_id, 'version_id': sam302_v15_id},

        # 测试团队
        {'event_date': '2026-09-13', 'event_type': '测试开始', 'title': 'V1.5版本黑盒测试开始', 'description': '测试团队开始V1.5版本黑盒测试与版本bug提单', 'team': '测试组', 'status': '进行中', 'product_id': sam302_id, 'version_id': sam302_v15_id},
        {'event_date': '2026-09-18', 'event_type': '测试完成', 'title': 'V1.5版本催熟测试完成', 'description': '完成催熟版本并通过测试确认具备可发布状态', 'team': '测试组', 'status': '计划中', 'product_id': sam302_id, 'version_id': sam302_v15_id},

        # 生产团队
        {'event_date': '2026-09-25', 'event_type': '生产完成', 'title': 'V1.5版本生产完成', 'description': '基于版本发布状态与用户需求，完成生产并将模组刷新为V1.5版本', 'team': '生产组', 'status': '计划中', 'product_id': sam302_id, 'version_id': sam302_v15_id},

        # 交付
        {'event_date': '2026-09-10', 'event_type': '交付', 'title': '第1批交付：V1.3版本200套', 'description': '园林局项目第1批交付，V1.3基础版200套', 'team': '交付服务组', 'status': '已完成', 'product_id': sam302_id, 'version_id': sam302_v13_id, 'requirement_id': yuanlin_req_id},
        {'event_date': '2026-09-27', 'event_type': '交付', 'title': '第2批交付：V1.5版本500套', 'description': '园林局项目第2批交付，V1.5版本500套', 'team': '交付服务组', 'status': '计划中', 'product_id': sam302_id, 'version_id': sam302_v15_id, 'requirement_id': yuanlin_req_id},
        {'event_date': '2026-10-20', 'event_type': '交付', 'title': '第3批交付：V1.5版本800套', 'description': '园林局项目第3批交付，V1.5版本800套', 'team': '交付服务组', 'status': '计划中', 'product_id': sam302_id, 'version_id': sam302_v15_id, 'requirement_id': yuanlin_req_id},
    ]

    for e in events:
        fields = list(e.keys())
        values = [e[f] for f in fields]
        placeholders = ','.join(['?'] * len(fields))
        cursor.execute(f'INSERT INTO timeline_events ({",".join(fields)}, created_at) VALUES ({placeholders},?)', values + [NOW])
    print(f'创建时间线事件: {len(events)}个')
    conn.commit()

def main():
    print('=' * 60)
    print('用真实业务场景重新生成虚拟数据...')
    print('=' * 60)

    conn, cursor = reset_data()
    pids = create_products(conn, cursor)
    rids = create_requirements(conn, cursor, pids)
    vids = create_versions(conn, cursor, pids, rids)
    create_version_features(conn, cursor, vids, pids)
    create_dev_tasks(conn, cursor, vids, pids, rids)
    create_bugs(conn, cursor, vids, pids)
    create_production_schedules(conn, cursor, pids, rids, vids)
    create_delivery_batches(conn, cursor, rids, vids, pids)
    create_test_plans(conn, cursor, vids, pids)
    create_timeline_events(conn, cursor, pids, vids, rids)

    # 统计
    print()
    print('=' * 60)
    print('数据生成完成！统计如下：')
    for table in ['products', 'requirements', 'software_versions', 'version_features', 'dev_tasks', 'bug_records', 'production_schedules', 'delivery_batches', 'test_plans', 'timeline_events']:
        cursor.execute(f'SELECT COUNT(*) FROM {table}')
        print(f'  {table}: {cursor.fetchone()[0]}条')
    print('=' * 60)

    conn.close()

if __name__ == '__main__':
    main()
