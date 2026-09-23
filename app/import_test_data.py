#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""导入550套SAM302回货检测数据到业务系统数据库（测试批次TB20260922001）
数据来源：综测数据SUM.xlsx / 功耗测试结果统计.xlsx / AFC校准频偏值统计.xlsx / 工站测试用时矩阵.xlsx
"""
import sqlite3, os, re, sys, json

DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'business.db')
DATA_DIR = '/home/user/.doubao/agent_mode/workspace/.sessions/38440335741282818/attachments'

def load_excel(path, sheet=0):
    import openpyxl
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb.worksheets[sheet]
    rows = []
    for row in ws.iter_rows(values_only=True):
        rows.append(row)
    wb.close()
    return rows

def parse_power(v):
    if v is None: return None
    s = str(v).replace('mW', '').strip()
    try: return round(float(s), 3)
    except: return None

def parse_time(v):
    if v is None: return None
    s = str(v).strip()
    if ':' in s:
        parts = s.split(':')
        try:
            return round(int(parts[0])*3600 + int(parts[1])*60 + float(parts[2]), 1)
        except: return None
    return None

def main():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    # ---- 1. 综测数据SUM（546个模组，3通道×11项）----
    rf_rows = load_excel(os.path.join(DATA_DIR, '综测数据SUM.xlsx'))
    rf = {}
    for r in rf_rows[2:]:
        if not r or not r[0]: continue
        sn = str(r[0]).strip()
        if not sn: continue
        # 每通道11项：EVM 频偏 功率 ACLR1_N ACLR1_P ACLR2_N ACLR2_P ACLR3_N ACLR3_P ACLR4_N ACLR4_P
        # 9_0从列1(r[1])、29_0从列12(r[12])、47_0从列23(r[23])开始
        ch = {}
        for ci, chname in [(0, '9'), (11, '29'), (22, '47')]:
            try:
                evm = float(r[ci+1]); freq = float(r[ci+2]); pwr = float(r[ci+3])
                aclr1 = max(float(r[ci+4]), float(r[ci+5]))
                aclr2 = max(float(r[ci+6]), float(r[ci+7]))
                aclr3 = max(float(r[ci+8]), float(r[ci+9]))
                aclr4 = max(float(r[ci+10]), float(r[ci+11]))
                ch[chname] = dict(evm=evm, freq=freq, pwr=pwr,
                                  aclr1=aclr1, aclr2=aclr2, aclr3=aclr3, aclr4=aclr4)
            except (TypeError, ValueError, IndexError):
                pass
        if ch:
            rf[sn] = ch

    # ---- 2. 功耗测试结果统计（550个模组）----
    pw_rows = load_excel(os.path.join(DATA_DIR, '功耗测试结果统计.xlsx'))
    pw = {}
    for r in pw_rows[1:]:
        if not r or not r[0]: continue
        sn = str(r[0]).strip()
        if not sn: continue
        pw[sn] = (parse_power(r[1]), parse_power(r[2]))

    # ---- 3. AFC校准频偏值（550个模组）----
    afc_rows = load_excel(os.path.join(DATA_DIR, 'AFC校准频偏值统计.xlsx'))
    afc = {}
    for r in afc_rows[1:]:
        if not r or not r[0]: continue
        sn = str(r[0]).strip()
        if not sn: continue
        try: afc[sn] = int(float(r[1]))
        except: afc[sn] = None

    # ---- 4. 工站测试用时矩阵（550个模组 × 8工站）----
    st_rows = load_excel(os.path.join(DATA_DIR, '工站测试用时矩阵.xlsx'))
    st = {}      # 总用时
    st_detail = {}  # 8工站明细
    for r in st_rows[1:]:
        if not r or not r[0]: continue
        sn = str(r[0]).strip()
        if not sn: continue
        total = 0.0
        detail = []
        for v in r[1:9]:
            t = parse_time(v)
            t = t if t is not None else 0.0
            detail.append(t)
            total += t
        st[sn] = round(total, 1)
        st_detail[sn] = detail

    # ---- 汇总所有SN ----
    all_sns = list(dict.fromkeys(list(rf.keys()) + list(pw.keys()) + list(afc.keys()) + list(st.keys())))
    all_sns.sort()
    print(f'综测模组: {len(rf)} 个, 功耗模组: {len(pw)} 个, AFC模组: {len(afc)} 个, 工站模组: {len(st)} 个')
    print(f'合计唯一SN: {len(all_sns)} 个')

    # ---- 规格判定 ----
    SPEC = dict(evm_max=4.0, freq_abs=300.0, pwr_abs=1.0,
                aclr1=-32.0, aclr2=-45.0, aclr3=-49.0, aclr4=-46.0)

    def judge(ch):
        """判定单个模组综测是否合格（三通道全指标）"""
        if not ch: return False
        for c in ch.values():
            if c['evm'] < 0 or c['evm'] > SPEC['evm_max']: return False
            if abs(c['freq']) > SPEC['freq_abs']: return False
            if abs(c['pwr']) > SPEC['pwr_abs']: return False
            if c['aclr1'] > SPEC['aclr1']: return False
            if c['aclr2'] > SPEC['aclr2']: return False
            if c['aclr3'] > SPEC['aclr3']: return False
            if c['aclr4'] > SPEC['aclr4']: return False
        return True

    # ---- 创建检测批次 ----
    batch_no = 'TB20260922001'
    cur.execute('DELETE FROM test_records WHERE batch_id IN (SELECT id FROM test_batches WHERE batch_no=?)', (batch_no,))
    cur.execute('DELETE FROM test_batches WHERE batch_no=?', (batch_no,))
    cur.execute('''INSERT INTO test_batches (batch_no, product_id, product_name, schedule_id, total_count,
                   pass_count, fail_count, test_date, spec, status, remark)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)''',
                (batch_no, 1, 'SAM302窄带高速率模组', 2, len(all_sns), 0, 0, '2026-09-22',
                 json.dumps(SPEC, ensure_ascii=False), '已完成',
                 '园林局第2批500套回货测试（含550套全量检测数据：综测546 + 功耗550 + AFC550 + 工站550）'))
    batch_id = cur.lastrowid

    # ---- 写入每套模组检测明细 ----
    pass_cnt = fail_cnt = 0
    for sn in all_sns:
        ch = rf.get(sn, {})
        is_pass = judge(ch) if ch else True  # 无综测数据按合格处理（用户判定口径）
        # 聚合关键指标
        evm_max = max([c['evm'] for c in ch.values()], default=None)
        freq_min = min([c['freq'] for c in ch.values()], default=None)
        freq_max = max([c['freq'] for c in ch.values()], default=None)
        pwr_max = max([c['pwr'] for c in ch.values()], default=None)
        aclr_w = {}
        for key in ('aclr1','aclr2','aclr3','aclr4'):
            vals = [c[key] for c in ch.values()]
            aclr_w[key] = max(vals) if vals else None
        cp, sp = pw.get(sn, (None, None))
        cur.execute('''INSERT INTO test_records (batch_id, sn, is_pass, afc, call_power, standby_power,
                       evm_9, evm_29, evm_47, freq_9, freq_29, freq_47,
                       pwr_9, pwr_29, pwr_47, aclr1_worst, aclr2_worst, aclr3_worst, aclr4_worst,
                       station_total_sec, station_data, status)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                    (batch_id, sn, 1 if is_pass else 0, afc.get(sn), cp, sp,
                     ch.get('9',{}).get('evm'), ch.get('29',{}).get('evm'), ch.get('47',{}).get('evm'),
                     ch.get('9',{}).get('freq'), ch.get('29',{}).get('freq'), ch.get('47',{}).get('freq'),
                     ch.get('9',{}).get('pwr'), ch.get('29',{}).get('pwr'), ch.get('47',{}).get('pwr'),
                     aclr_w.get('aclr1'), aclr_w.get('aclr2'), aclr_w.get('aclr3'), aclr_w.get('aclr4'),
                     st.get(sn), json.dumps(st_detail.get(sn, []), ensure_ascii=False),
                     'PASS' if is_pass else 'FAIL'))
        if is_pass: pass_cnt += 1
        else: fail_cnt += 1

    cur.execute('UPDATE test_batches SET pass_count=?, fail_count=? WHERE id=?', (pass_cnt, fail_cnt, batch_id))
    conn.commit()
    print(f'批次 {batch_no} 导入完成: 总数 {len(all_sns)}, 良品 {pass_cnt}, 不良 {fail_cnt}')
    print(f'良品率: {pass_cnt*100.0/len(all_sns):.2f}%')
    conn.close()

if __name__ == '__main__':
    main()
