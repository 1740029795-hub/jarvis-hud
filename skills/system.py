"""系统类受控命令（Phase 1）

只放零额外依赖、纯标准库能实现的命令。截图/桌面操控等进 Phase 2。
"""
import re
import subprocess
import time
from datetime import datetime

from core.skills import tool


# 常见软件名 -> 可执行文件名（用于启动后验证进程是否真的出现）
_APP_EXE = {
    "notepad": "notepad.exe", "记事本": "notepad.exe",
    "calc": "Calculator.exe", "计算器": "Calculator.exe",
    "mspaint": "mspaint.exe", "画图": "mspaint.exe",
    "explorer": "explorer.exe", "资源管理器": "explorer.exe",
    "wps": "wps.exe",
    # 本机装的是 Edge 不是 Chrome：浏览器名统一映射到 msedge
    "msedge": "msedge.exe", "edge": "msedge.exe", "浏览器": "msedge.exe",
    "chrome": "msedge.exe", "谷歌浏览器": "msedge.exe", "google浏览器": "msedge.exe",
}


@tool(
    "open_app",
    "打开一个 Windows 本地软件（记事本、计算器、画图、资源管理器等）。注意：网页/浏览器操作不要用这个，直接用 web_* 工具",
    {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "软件名或可执行文件名，如 notepad / calc / mspaint / explorer"}
        },
        "required": ["name"],
    },
)
def open_app(name: str) -> dict:
    exe = _APP_EXE.get(name.lower())
    target = exe or name  # 有映射用映射的 exe，否则原样 start
    subprocess.Popen(["cmd", "/c", "start", "", target], shell=True)
    if not exe:
        # 无法验证的软件：诚实标记 unverifiable
        return {"ok": True, "effect": "unverifiable", "app": name, "note": "已发送启动命令，但无法验证是否真的打开"}
    time.sleep(1.2)
    out = subprocess.run(
        ["tasklist", "/FI", f"IMAGENAME eq {exe}"],
        capture_output=True, text=True, timeout=10,
    )
    if exe.lower() in out.stdout.lower():
        return {"ok": True, "effect": "confirmed", "app": name}
    return {"ok": False, "error": f"未检测到 {name}（{exe}）启动，可能未安装或启动失败"}


# 停手线：不可逆危险命令模式（设计方案决策⑤——不可逆动作交还用户，不代做）
_DANGEROUS_PATTERNS = [
    r"\bdel\b", r"\berase\b", r"\brm\b", r"\brmdir\b", r"\brd\b",   # 删除
    r"\bformat\b", r"\bdiskpart\b",                                 # 格式化/磁盘分区
    r"\breg\s+delete\b",                                            # 注册表删除
    r"\bdel\s+/[fsq]", r"\brm\s+-[rf]",                             # 强制/递归删除
]


def _is_dangerous(cmd: str) -> bool:
    for pat in _DANGEROUS_PATTERNS:
        if re.search(pat, cmd, re.IGNORECASE):
            return True
    return False


@tool(
    "run_command",
    "执行一条系统命令（谨慎：命令会真实执行；删除/格式化等不可逆操作会被拦截）",
    {
        "type": "object",
        "properties": {
            "cmd": {"type": "string", "description": "要执行的命令"}
        },
        "required": ["cmd"],
    },
)
def run_command(cmd: str) -> str:
    # 停手线：不可逆操作不代做，交还用户手动确认
    if _is_dangerous(cmd):
        return "（已拦截）此命令含不可逆的危险操作（删除/格式化/改注册表等），为安全起见我不代执行，请你自己在终端手动确认后执行。"
    out = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=30)
    if out.stdout:
        return out.stdout.strip()[:2000]
    if out.stderr:
        return f"（stderr）{out.stderr.strip()[:2000]}"
    return f"（退出码 {out.returncode}）"


@tool(
    "get_time",
    "获取当前日期和时间",
    {"type": "object", "properties": {}},
)
def get_time() -> str:
    now = datetime.now()
    weekdays = "一二三四五六日"
    return f"{now:%Y-%m-%d} 星期{weekdays[now.weekday()]} {now:%H:%M:%S}"


@tool(
    "lock_screen",
    "锁定屏幕",
    {"type": "object", "properties": {}},
)
def lock_screen() -> str:
    subprocess.Popen(["rundll32.exe", "user32.dll,LockWorkStation"])
    return "已锁定屏幕"
