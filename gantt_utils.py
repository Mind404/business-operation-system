# -*- coding: utf-8 -*-
"""
甘特图工作计划工具 - 数据工具模块
====================================
从独立工具 server.py 移植的纯标准库 Excel 读写与数据规范化逻辑，
用于在 Flask 系统中支持甘特图的导入导出（JSON / Excel .xlsx）。

数据模型：
    settings: { start_date, end_date, auto_range }
    projects: [{ id, name, start, end, category, done, source_table?, source_id? }]
    分类：产品研发情况 / 产品测试情况 / 产品生产情况
"""

import re
import io
import zipfile
import datetime
from urllib.parse import quote
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape as xml_escape

VALID_CATEGORIES = ("产品研发情况", "产品测试情况", "产品生产情况")
DEFAULT_CATEGORY = VALID_CATEGORIES[0]

DEMO_DATA = {
    "version": 2,
    "settings": {
        "start_date": "2026-09-01",
        "end_date": "2026-10-10",
        "auto_range": False,
    },
    "projects": [
        {"id": 1, "name": "V1.3基础协议适配开发", "start": "2026-09-01", "end": "2026-09-12", "category": "产品研发情况", "done": True},
        {"id": 2, "name": "OTA远程升级功能开发", "start": "2026-09-08", "end": "2026-09-22", "category": "产品研发情况", "done": False},
        {"id": 3, "name": "V1.3版本发布", "start": "2026-09-23", "end": "2026-09-30", "category": "产品研发情况", "done": False},
        {"id": 4, "name": "TE-V2.2.2-R2", "start": "2026-09-05", "end": "2026-09-14", "category": "产品测试情况", "done": True},
        {"id": 5, "name": "TE-V1.3-R1-功能测试", "start": "2026-09-15", "end": "2026-09-24", "category": "产品测试情况", "done": False},
        {"id": 6, "name": "TE-V2.2.2-R1", "start": "2026-09-10", "end": "2026-09-19", "category": "产品测试情况", "done": False},
        {"id": 7, "name": "TE-V1.3-R2-发布验证", "start": "2026-09-25", "end": "2026-10-02", "category": "产品测试情况", "done": False},
        {"id": 8, "name": "PS20260901001", "start": "2026-09-01", "end": "2026-09-08", "category": "产品生产情况", "done": True},
        {"id": 9, "name": "PS20260920001", "start": "2026-09-20", "end": "2026-09-27", "category": "产品生产情况", "done": False},
        {"id": 10, "name": "PS20261010001", "start": "2026-10-01", "end": "2026-10-10", "category": "产品生产情况", "done": False},
    ],
}


def default_settings():
    return {"start_date": "", "end_date": "", "auto_range": True}


# ============================================================
# 日期解析
# ============================================================
def parse_date(s):
    """解析 YYYY-MM-DD / YYYY/M/D / YYYY.M.D，返回 datetime.date；失败抛 ValueError。"""
    s = str(s).strip().replace("/", "-").replace(".", "-")
    parts = s.split("-")
    if len(parts) != 3:
        raise ValueError("日期格式应为 YYYY-MM-DD")
    y, m, d = int(parts[0]), int(parts[1]), int(parts[2])
    if m < 1 or m > 12 or d < 1 or d > 31:
        raise ValueError("日期数值越界")
    return datetime.date(y, m, d)


# ============================================================
# Excel(.xlsx) 读写 —— 纯标准库实现（xlsx 本质是 zip+XML）
# ============================================================
XLSX_NS_MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
XLSX_NS_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
XLSX_NS_PKG_REL = "http://schemas.openxmlformats.org/package/2006/relationships"
XLSX_NS_CT = "http://schemas.openxmlformats.org/package/2006/content-types"

XLSX_HEADERS = ["序号", "是否完成", "工作项目名称", "开始日期", "结束日期", "分类"]

_XLSX_NAME_KEYS = ("工作项目名称", "项目名称", "任务名称", "名称", "工作项目", "任务")
_XLSX_START_KEYS = ("开始日期", "开始时间", "开始")
_XLSX_END_KEYS = ("结束日期", "结束时间", "结束")
_XLSX_CAT_KEYS = ("分类", "类别", "所属分类")
_XLSX_DONE_KEYS = ("是否完成", "完成", "已完成", "完成状态", "完成标志")


def _xlsx_col_idx(ref):
    """单元格引用 'AB12' -> 0-based 列号。"""
    letters = ""
    for ch in str(ref):
        if ch.isalpha():
            letters += ch
        else:
            break
    idx = 0
    for ch in letters:
        idx = idx * 26 + (ord(ch.upper()) - 64)
    return idx - 1


def _xlsx_ref(ci, rn):
    """0-based 列号 + 1-based 行号 -> 'AB12'。"""
    n = ci + 1
    letters = ""
    while n:
        n, rem = divmod(n - 1, 26)
        letters = chr(65 + rem) + letters
    return letters + str(rn)


def _xlsx_is_number(s):
    return bool(re.fullmatch(r"-?\d+(\.\d+)?", str(s)))


def _xlsx_parse_excel_date(v):
    """解析 Excel 单元格中的日期值：支持 YYYY-MM-DD / YYYY/M/D / 数字序列号 / 带时间。"""
    s = str(v).strip()
    if not s:
        raise ValueError("日期为空")
    if re.fullmatch(r"\d+(\.\d+)?", s):
        try:
            serial = float(s)
        except ValueError:
            raise ValueError("日期数值非法")
        if serial < 1:
            raise ValueError("日期数值越界")
        if serial == 60:  # Excel 1900 闰年 bug 的虚假日期
            return datetime.date(1900, 2, 28)
        if serial > 2958465:  # 9999-12-31 序列号
            raise ValueError("日期数值越界")
        try:
            return datetime.date(1899, 12, 30) + datetime.timedelta(days=serial)
        except OverflowError:
            raise ValueError("日期数值越界")
    # 去掉时间部分：2026-09-01 00:00:00 / 2026-09-01T00:00:00
    s = s.split(" ")[0].split("T")[0]
    return parse_date(s)


def _xlsx_parse_done(v):
    s = str(v).strip().lower()
    if s in ("是", "1", "true", "yes", "y", "完成", "已完成", "对", "√", "✔", "o"):
        return True
    return False


def read_xlsx_rows(raw):
    """解析 xlsx 二进制，返回二维数组（字符串）。非法文件抛 ValueError。"""
    try:
        z = zipfile.ZipFile(io.BytesIO(raw))
    except Exception:
        raise ValueError("文件不是有效的 Excel(.xlsx) 文件")
    names = set(z.namelist())

    # 共享字符串表
    shared = []
    if "xl/sharedStrings.xml" in names:
        try:
            sroot = ET.fromstring(z.read("xl/sharedStrings.xml"))
        except Exception:
            sroot = None
        if sroot is not None:
            for si in sroot:
                shared.append("".join(n.text or "" for n in si.iter() if n.tag.endswith("}t")))

    # 定位第一个工作表
    sheet_path = None
    try:
        wb = ET.fromstring(z.read("xl/workbook.xml"))
        rid = None
        for sh in wb.iter():
            if sh.tag.endswith("}sheet"):
                rid = sh.get("{%s}id" % XLSX_NS_REL)
                break
        if rid:
            rels = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
            for rel in rels:
                if rel.get("Id") == rid:
                    tgt = rel.get("Target") or ""
                    if tgt.startswith("/"):
                        sheet_path = tgt.lstrip("/")
                    else:
                        sheet_path = "xl/" + tgt.lstrip("/")
                    break
    except Exception:
        sheet_path = None
    if not sheet_path or sheet_path not in names:
        cand = sorted(n for n in names if n.startswith("xl/worksheets/") and n.endswith(".xml"))
        sheet_path = cand[0] if cand else None
    if not sheet_path:
        raise ValueError("Excel 文件中没有找到工作表")

    try:
        sheet = ET.fromstring(z.read(sheet_path))
    except Exception:
        raise ValueError("Excel 工作表解析失败")

    rows = []
    for row in sheet.iter():
        if not row.tag.endswith("}row"):
            continue
        cells = {}
        maxc = -1
        for c in row:
            if not c.tag.endswith("}c"):
                continue
            ref = c.get("r") or ""
            idx = _xlsx_col_idx(ref) if ref else maxc + 1
            t = c.get("t")
            vnode = isnode = None
            for child in c:
                if child.tag.endswith("}v"):
                    vnode = child
                elif child.tag.endswith("}is"):
                    isnode = child
            val = ""
            if t == "s" and vnode is not None:
                try:
                    val = shared[int(vnode.text)]
                except Exception:
                    val = ""
            elif t == "inlineStr" and isnode is not None:
                val = "".join(n.text or "" for n in isnode.iter() if n.tag.endswith("}t"))
            elif vnode is not None:
                val = vnode.text or ""
            cells[idx] = val
            if idx > maxc:
                maxc = idx
        if cells:
            rows.append([cells.get(i, "") for i in range(maxc + 1)])
    return rows


def xlsx_rows_to_projects(rows):
    """把 Excel 表格（含或不含表头）转成项目列表；非法时抛中文 ValueError。"""
    if not rows:
        return []
    header = [str(x).strip().replace(" ", "").replace("　", "") for x in rows[0]]
    col = {}
    for i, h in enumerate(header):
        if not col.get("name") and h in _XLSX_NAME_KEYS:
            col["name"] = i
        elif not col.get("start") and h in _XLSX_START_KEYS:
            col["start"] = i
        elif not col.get("end") and h in _XLSX_END_KEYS:
            col["end"] = i
        elif not col.get("cat") and h in _XLSX_CAT_KEYS:
            col["cat"] = i
        elif not col.get("done") and h in _XLSX_DONE_KEYS:
            col["done"] = i
    has_header = "name" in col and "start" in col and "end" in col

    projects = []
    start_idx = 1 if has_header else 0
    for i in range(start_idx, len(rows)):
        r = rows[i]

        def getv(k, d=""):
            idx = col.get(k)
            if idx is None or idx >= len(r):
                return d
            return str(r[idx]).strip()

        if not has_header:  # 无表头：按列序 名称/开始/结束/分类/完成
            name = str(r[0]).strip() if len(r) > 0 else ""
            s = str(r[1]).strip() if len(r) > 1 else ""
            e = str(r[2]).strip() if len(r) > 2 else ""
            cat = str(r[3]).strip() if len(r) > 3 else ""
            done = str(r[4]).strip() if len(r) > 4 else ""
        else:
            name, s, e, cat, done = getv("name"), getv("start"), getv("end"), getv("cat"), getv("done")
        if not name:
            continue
        try:
            ds = _xlsx_parse_excel_date(s)
            de = _xlsx_parse_excel_date(e)
        except ValueError:
            raise ValueError("第 %d 行「%s」日期格式错误（应为 YYYY-MM-DD 或 YYYY/M/D）" % (i + 1, name))
        if de < ds:
            raise ValueError("第 %d 行「%s」结束日期早于开始日期" % (i + 1, name))
        if cat and cat not in VALID_CATEGORIES:
            cat = DEFAULT_CATEGORY
        projects.append({
            "name": name,
            "start": ds.isoformat(),
            "end": de.isoformat(),
            "category": cat or DEFAULT_CATEGORY,
            "done": _xlsx_parse_done(done),
        })
    return projects


def build_xlsx(headers, rows):
    """用纯标准库生成最小合法 xlsx（inlineStr 字符串），返回字节。"""
    xlsx_ns_worksheet = XLSX_NS_MAIN

    def sheet_xml():
        parts = [
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
            '<worksheet xmlns="%s"><sheetData>' % xlsx_ns_worksheet,
        ]
        rn = 0
        for row in rows:
            rn += 1
            cells = []
            for ci, val in enumerate(row):
                ref = _xlsx_ref(ci, rn)
                s = "" if val is None else str(val)
                if s == "":
                    cells.append('<c r="%s"/>' % ref)
                elif _xlsx_is_number(s):
                    cells.append('<c r="%s"><v>%s</v></c>' % (ref, s))
                else:
                    cells.append('<c r="%s" t="inlineStr"><is><t xml:space="preserve">%s</t></is></c>'
                                 % (ref, xml_escape(s)))
            parts.append('<row r="%d">%s</row>' % (rn, "".join(cells)))
        parts.append('</sheetData></worksheet>')
        return "".join(parts)

    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="%s">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        '</Types>' % XLSX_NS_CT)
    rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="%s">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
        '</Relationships>' % XLSX_NS_PKG_REL)
    workbook = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="%s" xmlns:r="%s">'
        '<sheets><sheet name="甘特图数据" sheetId="1" r:id="rId1"/></sheets>'
        '</workbook>' % (XLSX_NS_MAIN, XLSX_NS_REL))
    workbook_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="%s">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
        '</Relationships>' % XLSX_NS_PKG_REL)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", content_types)
        z.writestr("_rels/.rels", rels)
        z.writestr("xl/workbook.xml", workbook)
        z.writestr("xl/_rels/workbook.xml.rels", workbook_rels)
        z.writestr("xl/worksheets/sheet1.xml", sheet_xml())
    return buf.getvalue()


def data_to_xlsx_rows(data):
    """把工具数据转成 xlsx 导出表格（二维数组）。"""
    rows = [XLSX_HEADERS]
    for i, p in enumerate(data.get("projects", []), 1):
        rows.append([
            i,
            "是" if p.get("done") else "否",
            p.get("name", ""),
            p.get("start", ""),
            p.get("end", ""),
            p.get("category", DEFAULT_CATEGORY),
        ])
    return rows


# ============================================================
# 数据规范化
# ============================================================
def normalize_projects(projects):
    """校验并规范化项目列表，非法时抛中文 ValueError。返回规范化后的列表（无 id）。"""
    if not isinstance(projects, list):
        raise ValueError("数据缺少 projects 数组")
    out = []
    for i, p in enumerate(projects):
        if not isinstance(p, dict):
            continue
        name = str(p.get("name", "")).strip()
        if not name:
            continue
        category = str(p.get("category", "")).strip() or DEFAULT_CATEGORY
        if category not in VALID_CATEGORIES:
            category = DEFAULT_CATEGORY
        try:
            ds = parse_date(p.get("start", ""))
            de = parse_date(p.get("end", ""))
        except ValueError:
            raise ValueError("第 %d 行「%s」日期格式错误（应为 YYYY-MM-DD 或 YYYY/M/D）" % (i + 1, name))
        if de < ds:
            raise ValueError("第 %d 行「%s」结束日期早于开始日期" % (i + 1, name))
        item = {
            "name": name,
            "start": ds.isoformat(),
            "end": de.isoformat(),
            "category": category,
            "done": bool(p.get("done", False)),
        }
        # 保留来源标记（如果存在）
        if p.get("source_table") and p.get("source_id") is not None:
            item["source_table"] = str(p["source_table"])
            item["source_id"] = int(p["source_id"])
        out.append(item)
    return out


def normalize_settings(raw):
    """规范化 settings，非法日期自动忽略。"""
    settings = default_settings()
    if isinstance(raw, dict):
        try:
            settings["start_date"] = parse_date(raw.get("start_date", "")).isoformat()
        except ValueError:
            pass
        try:
            settings["end_date"] = parse_date(raw.get("end_date", "")).isoformat()
        except ValueError:
            pass
        settings["auto_range"] = bool(raw.get("auto_range", True))
    return settings


def normalize_full(raw):
    """规范化完整数据对象 {settings, projects}。"""
    if not isinstance(raw, dict):
        raise ValueError("数据必须是 JSON 对象")
    return {
        "version": 2,
        "settings": normalize_settings(raw.get("settings")),
        "projects": normalize_projects(raw.get("projects")),
    }
