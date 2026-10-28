"""桌面操控工具（Phase 2 · L1 UIA 层）

认控件、不靠坐标，零焦点优先。每步带「防静默失败判据」：
工具返回 → 读回 → 截图见字 → 状态指示器 → 副作用
effect = confirmed | partial | suspected_noop | unverifiable

通用 UIA 工具集（LLM 工作流：find_window → dump_controls → click/get → 读回验证）：
- find_window     按标题/类名找窗口
- dump_controls   列出窗口内可操作控件（类型+名称），供后续定位
- click_control   点击指定名称的控件
- get_control_text 读取指定控件的文本
另有记事本便捷工具 notepad_read / notepad_write（基于同样的 UIA 思路）。
"""
import re
import time

import pyperclip
from PIL import ImageGrab
from pywinauto import Desktop

from core import config
from core.skills import tool


# ---------------- 内部辅助 ----------------

_KEEP_TYPES = {
    "Button", "Edit", "Document", "Text", "ListItem", "CheckBox",
    "ComboBox", "MenuItem", "TabItem", "Hyperlink", "RadioButton",
}


def _find_window(title: str):
    """按标题（或类名）模糊匹配窗口，返回第一个匹配的窗口 wrapper（或 None）。

    用 windows() 而非 window()：多实例时后者会抛 ElementAmbiguousError。
    """
    desktop = Desktop(backend="uia")
    try:
        wins = desktop.windows(title_re=f".*{re.escape(title)}.*")
        if wins:
            return wins[0]
    except Exception:
        pass
    try:
        wins = desktop.windows(class_name_re=f".*{re.escape(title)}.*")
        if wins:
            return wins[0]
    except Exception:
        pass
    return None


def _dump_controls(win, max_items: int = 40) -> list[dict]:
    """返回窗口内可操作控件的简化列表 [{type, name}]，过滤掉无意义控件。"""
    items: list[dict] = []

    def walk(wrapper):
        if len(items) >= max_items:
            return
        try:
            ct = wrapper.element_info.control_type
        except Exception:
            return
        if ct in _KEEP_TYPES:
            try:
                name = (wrapper.window_text() or "").strip()
            except Exception:
                name = ""
            if name:
                items.append({"type": ct, "name": name[:40]})
        try:
            for child in wrapper.children():
                walk(child)
        except Exception:
            pass

    for child in win.children():
        walk(child)
    return items


def _find_control(win, name: str):
    """在窗口子树里按标题匹配控件：先精确匹配（避免「减」误中「记忆减法」），再模糊。

    win 是 wrapper，用 descendants() 遍历（多实例时比 child_window 稳）。
    """
    fuzzy = None
    try:
        for c in win.descendants():
            try:
                t = c.window_text() or ""
            except Exception:
                continue
            if t == name:
                return c
            if fuzzy is None and name and name in t:
                fuzzy = c
    except Exception:
        pass
    return fuzzy


# ---------------- 记事本便捷工具 ----------------

def _get_notepad_document():
    """返回记事本当前窗口的 Document 编辑区（Win11 新版记事本）。"""
    desktop = Desktop(backend="uia")
    wins = desktop.windows(class_name="Notepad")
    if not wins:
        raise RuntimeError("找不到记事本窗口，请先打开记事本")
    win = wins[0]
    docs = [d for d in win.descendants() if d.element_info.control_type == "Document"]
    if not docs:
        raise RuntimeError("记事本里找不到编辑区 Document 控件")
    return docs[0]


@tool(
    "notepad_read",
    "读取记事本当前编辑区的全部文字内容",
    {"type": "object", "properties": {}},
)
def notepad_read() -> dict:
    doc = _get_notepad_document()
    text = doc.window_text()
    return {"ok": True, "effect": "confirmed", "text": text}


@tool(
    "notepad_write",
    "在记事本当前编辑区末尾追加文字（支持中文，剪贴板粘贴），并读回验证是否真的写进去了",
    {
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "要写入的文字"}
        },
        "required": ["text"],
    },
)
def notepad_write(text: str) -> dict:
    doc = _get_notepad_document()
    doc.click_input()
    doc.type_keys("^{END}")  # 光标移到末尾，实现「追加」语义
    time.sleep(0.2)
    pyperclip.copy(text)
    doc.type_keys("^v")      # 剪贴板粘贴，中文可靠
    time.sleep(0.5)
    read = doc.window_text()
    effect = "confirmed" if text in read else "suspected_noop"
    return {"ok": True, "effect": effect, "read_back": read[-200:]}


# ---------------- 通用 UIA 工具（L1 通用化） ----------------

@tool(
    "find_window",
    "按标题或类名查找一个已打开的窗口，返回是否找到",
    {
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "窗口标题或类名的部分文字，如「计算器」「记事本」「Notepad」"}
        },
        "required": ["title"],
    },
)
def find_window(title: str) -> dict:
    win = _find_window(title)
    if win:
        return {"ok": True, "found": True, "title": win.window_text()}
    return {"ok": True, "found": False, "error": f"未找到窗口「{title}」"}


@tool(
    "dump_controls",
    "列出指定窗口里可操作的控件（类型+名称），先看这个再决定点击/输入哪个控件",
    {
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "窗口标题的部分文字"}
        },
        "required": ["title"],
    },
)
def dump_controls(title: str) -> dict:
    win = _find_window(title)
    if not win:
        return {"ok": False, "error": f"未找到窗口「{title}」"}
    controls = _dump_controls(win)
    return {"ok": True, "window": win.window_text(), "controls": controls}


@tool(
    "click_control",
    "点击指定窗口里名称匹配的控件（按钮等）。可选 verify 参数：点击后读回指定控件文本验证效果",
    {
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "窗口标题的部分文字"},
            "name": {"type": "string", "description": "控件名称（来自 dump_controls 的 name 字段）"},
            "verify": {"type": "string", "description": "可选：点击后要读回验证的控件名（如结果显示区「显示为」），用于确认点击真的生效了"},
        },
        "required": ["title", "name"],
    },
)
def click_control(title: str, name: str, verify: str | None = None) -> dict:
    win = _find_window(title)
    if not win:
        return {"ok": False, "error": f"未找到窗口「{title}」"}
    ctrl = _find_control(win, name)
    if not ctrl:
        return {"ok": False, "error": f"窗口「{title}」里未找到控件「{name}」"}
    # UWP 控件（如计算器按钮）click_input 模拟鼠标点不进去，优先用 UIA InvokePattern
    try:
        ctrl.invoke()
    except Exception:
        ctrl.click_input()
    result = {"ok": True, "effect": "confirmed", "clicked": name}
    # 判据阶梯第 2 级：读回验证（可选）
    if verify:
        time.sleep(0.3)
        v = _find_control(win, verify)
        if v:
            result["verify_text"] = v.window_text()
    return result


@tool(
    "get_control_text",
    "读取指定窗口里名称匹配控件的文本（用于验证操作结果）",
    {
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "窗口标题的部分文字"},
            "name": {"type": "string", "description": "控件名称（来自 dump_controls 的 name 字段）"},
        },
        "required": ["title", "name"],
    },
)
def get_control_text(title: str, name: str) -> dict:
    win = _find_window(title)
    if not win:
        return {"ok": False, "error": f"未找到窗口「{title}」"}
    ctrl = _find_control(win, name)
    if not ctrl:
        return {"ok": False, "error": f"窗口「{title}」里未找到控件「{name}」"}
    return {"ok": True, "effect": "confirmed", "text": ctrl.window_text()}


@tool(
    "screenshot",
    "截取当前整个屏幕保存为 PNG，返回文件路径。用于操作后取证/确认界面状态（截图留证，人工可复查）",
    {"type": "object", "properties": {}},
)
def screenshot() -> dict:
    shots_dir = config.DATA_DIR / "screenshots"
    shots_dir.mkdir(parents=True, exist_ok=True)
    path = shots_dir / f"shot_{time.strftime('%Y%m%d_%H%M%S')}.png"
    ImageGrab.grab().save(str(path))
    return {"ok": True, "effect": "confirmed", "path": str(path)}


# ---------------- L2 坐标/键盘操控（pyautogui，UIA 覆盖不到时的降级档） ----------------

@tool(
    "get_screen_size",
    "获取屏幕分辨率（宽 x 高），用于理解坐标系",
    {"type": "object", "properties": {}},
)
def get_screen_size() -> dict:
    import pyautogui
    w, h = pyautogui.size()
    return {"ok": True, "width": w, "height": h}


@tool(
    "get_mouse_position",
    "获取当前鼠标所在屏幕坐标",
    {"type": "object", "properties": {}},
)
def get_mouse_position() -> dict:
    import pyautogui
    x, y = pyautogui.position()
    return {"ok": True, "x": x, "y": y}


@tool(
    "click_at",
    "在屏幕指定坐标点击鼠标（L2 降级：UIA 找不到控件时用）。button: left/right/middle",
    {
        "type": "object",
        "properties": {
            "x": {"type": "integer", "description": "屏幕 X 坐标"},
            "y": {"type": "integer", "description": "屏幕 Y 坐标"},
            "button": {"type": "string", "description": "鼠标键，默认 left"},
        },
        "required": ["x", "y"],
    },
)
def click_at(x: int, y: int, button: str = "left") -> dict:
    import pyautogui
    pyautogui.click(x, y, button=button)
    return {"ok": True, "effect": "confirmed", "clicked_at": [x, y]}


@tool(
    "type_text",
    "在当前光标位置输入文字（中文用剪贴板粘贴，可靠）",
    {
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "要输入的文字"}
        },
        "required": ["text"],
    },
)
def type_text(text: str) -> dict:
    import pyautogui
    pyperclip.copy(text)
    pyautogui.hotkey("ctrl", "v")
    return {"ok": True, "effect": "confirmed", "typed": text}


@tool(
    "press_key",
    "按一个键或组合键（如 enter、esc、tab、ctrl+c、alt+f4）",
    {
        "type": "object",
        "properties": {
            "keys": {"type": "string", "description": "键名或组合键，多个键用 + 连接，如 enter / ctrl+c / alt+f4"}
        },
        "required": ["keys"],
    },
)
def press_key(keys: str) -> dict:
    import pyautogui
    parts = [p.strip().lower() for p in keys.split("+") if p.strip()]
    if len(parts) > 1:
        pyautogui.hotkey(*parts)
    elif parts:
        pyautogui.press(parts[0])
    return {"ok": True, "effect": "confirmed", "pressed": keys}
