"""WPS Office 操控（Phase 2 · L0 结构接口 COM 层）

走 WPS 自带的 COM 自动化接口（win32com），不模拟鼠标键盘、不靠坐标，零焦点优先。
比 L1 UIA / L2 坐标更稳更快，是设计里的最高控制层。

组件 ProgID：
- KET.Application   WPS 表格（电子表格，Excel 同类）
- KWPS.Application  WPS 文字（文档，Word 同类）
- KWPP.Application  WPS 演示（PPT，本模块暂不接）

工具工作流（表格）：wps_spreadsheet_new → wps_write_cell / wps_write_table → wps_read_cell 验证 → wps_spreadsheet_save
工具工作流（文字）：wps_document_new → wps_document_write → wps_document_read 验证 → wps_document_save

每个写操作都读回验证，返回 effect = confirmed | suspected_noop（防静默失败判据）。
"""
import os
import re

import win32com.client

from core.skills import tool

# 模块级缓存：跨多次工具调用复用同一个 app/工作簿/文档，否则每步都开个新的，多步流程会断。
_ket_app = None      # WPS 表格 app
_ket_wb = None       # 当前工作簿
_kwps_app = None     # WPS 文字 app
_kwps_doc = None     # 当前文档


# ---------------- 内部辅助 ----------------

def _col_letter(n: int) -> str:
    """列序号 -> 列字母。1->A，26->Z，27->AA。"""
    s = ""
    while n > 0:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def _split_cell(cell: str) -> tuple[int, int]:
    """解析单元格地址 'A1' -> (列序号, 行号)，均从 1 开始。"""
    m = re.match(r"^\s*([A-Za-z]+)(\d+)\s*$", cell)
    if not m:
        raise ValueError(f"非法单元格地址 {cell!r}")
    cnum = 0
    for ch in m.group(1).upper():
        cnum = cnum * 26 + (ord(ch) - 64)
    return cnum, int(m.group(2))


def _end_cell(start: str, nrows: int, ncols: int) -> str:
    c0, r0 = _split_cell(start)
    return f"{_col_letter(c0 + ncols - 1)}{r0 + nrows - 1}"


def _ket():
    """WPS 表格 app（懒加载 + 探活重建）。"""
    global _ket_app
    if _ket_app is not None:
        try:
            _ket_app.Version
        except Exception:
            _ket_app = None
    if _ket_app is None:
        _ket_app = win32com.client.Dispatch("Ket.Application")
        _ket_app.Visible = True
    return _ket_app


def _ket_sheet():
    """当前 WPS 表格的工作簿 + 活动工作表。"""
    global _ket_wb
    app = _ket()
    wb = _ket_wb
    if wb is not None:
        try:
            wb.Name
        except Exception:
            _ket_wb = None
            wb = None
    if wb is None:
        wb = app.ActiveWorkbook
    if wb is None:
        raise RuntimeError("没有可用的 WPS 表格，先调用 wps_spreadsheet_new")
    return wb, wb.ActiveSheet


def _kwps():
    """WPS 文字 app（懒加载 + 探活重建）。"""
    global _kwps_app
    if _kwps_app is not None:
        try:
            _kwps_app.Version
        except Exception:
            _kwps_app = None
    if _kwps_app is None:
        _kwps_app = win32com.client.Dispatch("Kwps.Application")
        _kwps_app.Visible = True
    return _kwps_app


def _kwps_document():
    """当前 WPS 文字文档。"""
    global _kwps_doc
    app = _kwps()
    doc = _kwps_doc
    if doc is not None:
        try:
            doc.Name
        except Exception:
            _kwps_doc = None
            doc = None
    if doc is None:
        doc = app.ActiveDocument
    if doc is None:
        raise RuntimeError("没有可用的 WPS 文字文档，先调用 wps_document_new")
    return doc


# ---------------- WPS 表格（KET） ----------------

@tool(
    "wps_spreadsheet_new",
    "新建一个空白 WPS 表格工作簿（之后用 wps_write_cell / wps_write_table 填数据）",
    {"type": "object", "properties": {}},
)
def wps_spreadsheet_new() -> dict:
    global _ket_wb
    app = _ket()
    wb = app.Workbooks.Add()
    if wb is None:
        wb = app.ActiveWorkbook
    _ket_wb = wb
    return {"ok": True, "effect": "confirmed", "workbook": wb.Name, "sheet": wb.ActiveSheet.Name}


@tool(
    "wps_write_cell",
    "往 WPS 表格的某个单元格写文本（如 A1），写后读回验证。写纯数字列建议用 wps_write_table",
    {
        "type": "object",
        "properties": {
            "cell": {"type": "string", "description": "单元格地址，如 A1"},
            "value": {"type": "string", "description": "要写入的文本内容"},
        },
        "required": ["cell", "value"],
    },
)
def wps_write_cell(cell: str, value: str) -> dict:
    _, ws = _ket_sheet()
    ws.Range(cell).Value = value
    read = ws.Range(cell).Value
    effect = "confirmed" if str(read) == str(value) else "suspected_noop"
    return {"ok": True, "effect": effect, "cell": cell, "read_back": read}


@tool(
    "wps_write_table",
    "从起始单元格批量写入二维数据（一次写多行多列）。rows 是数组的数组，如 [['姓名','年龄'],['张三',20],['李四',25]]",
    {
        "type": "object",
        "properties": {
            "start_cell": {"type": "string", "description": "起始单元格，如 A1"},
            "rows": {
                "type": "array",
                "items": {"type": "array"},
                "description": "二维数据，每个内层数组是一行（值可以是文本或数字）",
            },
        },
        "required": ["start_cell", "rows"],
    },
)
def wps_write_table(start_cell: str, rows: list) -> dict:
    _, ws = _ket_sheet()
    if not rows:
        return {"ok": False, "error": "rows 不能为空"}
    ncols = max((len(r) for r in rows), default=0)
    if ncols == 0:
        return {"ok": False, "error": "rows 每行都不能为空"}
    # 补齐不等长的行，避免 WPS 写入错位
    padded = [list(r) + [""] * (ncols - len(r)) for r in rows]
    end = _end_cell(start_cell, len(padded), ncols)
    rng = f"{start_cell}:{end}"
    ws.Range(rng).Value = padded
    read = ws.Range(rng).Value
    effect = "confirmed" if read is not None else "suspected_noop"
    return {"ok": True, "effect": effect, "range": rng, "read_back": read}


@tool(
    "wps_read_cell",
    "读取 WPS 表格某个单元格的值（用于验证写入结果）",
    {
        "type": "object",
        "properties": {
            "cell": {"type": "string", "description": "单元格地址，如 A1"},
        },
        "required": ["cell"],
    },
)
def wps_read_cell(cell: str) -> dict:
    _, ws = _ket_sheet()
    val = ws.Range(cell).Value
    return {"ok": True, "effect": "confirmed", "cell": cell, "value": val}


@tool(
    "wps_spreadsheet_save",
    "把当前 WPS 表格保存到指定路径（扩展名 .xlsx 或 .et），保存后校验文件已生成",
    {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "完整保存路径，如 D:\\数据\\test.xlsx"},
        },
        "required": ["path"],
    },
)
def wps_spreadsheet_save(path: str) -> dict:
    wb, _ = _ket_sheet()
    wb.SaveAs(path)
    effect = "confirmed" if os.path.exists(path) else "suspected_noop"
    return {"ok": True, "effect": effect, "path": path}


# ---------------- WPS 文字（KWPS） ----------------

@tool(
    "wps_document_new",
    "新建一个空白 WPS 文字文档（之后用 wps_document_write 写内容）",
    {"type": "object", "properties": {}},
)
def wps_document_new() -> dict:
    global _kwps_doc
    app = _kwps()
    doc = app.Documents.Add()
    if doc is None:
        doc = app.ActiveDocument
    _kwps_doc = doc
    return {"ok": True, "effect": "confirmed", "document": doc.Name}


@tool(
    "wps_document_write",
    "在 WPS 文字文档末尾追加一段文字（支持中文），写后读回验证",
    {
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "要写入的文字"},
        },
        "required": ["text"],
    },
)
def wps_document_write(text: str) -> dict:
    doc = _kwps_document()
    doc.Content.InsertAfter(text)
    read = doc.Content.Text
    effect = "confirmed" if text in read else "suspected_noop"
    return {"ok": True, "effect": effect, "read_back": read[-200:]}


@tool(
    "wps_document_read",
    "读取 WPS 文字文档的全部文字内容",
    {"type": "object", "properties": {}},
)
def wps_document_read() -> dict:
    doc = _kwps_document()
    return {"ok": True, "effect": "confirmed", "text": doc.Content.Text}


@tool(
    "wps_document_save",
    "把当前 WPS 文字文档保存到指定路径（扩展名 .docx），保存后校验文件已生成",
    {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "完整保存路径，如 D:\\文档\\test.docx"},
        },
        "required": ["path"],
    },
)
def wps_document_save(path: str) -> dict:
    doc = _kwps_document()
    doc.SaveAs(path)
    effect = "confirmed" if os.path.exists(path) else "suspected_noop"
    return {"ok": True, "effect": effect, "path": path}
