"""工具注册表：把 Python 函数注册成 LLM 可调用的工具（function calling）

用法：
    from core.skills import tool

    @tool("get_time", "获取当前时间", {...schema...})
    def get_time():
        ...
"""
from typing import Any, Callable

TOOLS: list[dict] = []          # 发给 LLM 的工具定义（OpenAI/DeepSeek 兼容格式）
_HANDLERS: dict[str, Callable] = {}   # 工具名 -> 处理函数


def register_tool(name: str, description: str, parameters: dict, func: Callable) -> Callable:
    """运行时注册一个工具（供动态工具使用，如浏览器 MCP 代理）。"""
    TOOLS.append({
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": parameters,
        },
    })
    _HANDLERS[name] = func
    return func


def tool(name: str, description: str, parameters: dict):
    """装饰器：注册一个工具，返回的函数名即工具名。"""
    def decorator(func: Callable):
        return register_tool(name, description, parameters, func)
    return decorator


def dispatch(name: str, args: dict) -> dict:
    """执行工具，统一返回 {ok, result|error}。"""
    if name not in _HANDLERS:
        return {"ok": False, "error": f"未知工具 {name}"}
    try:
        result = _HANDLERS[name](**args)
        return {"ok": True, "result": result}
    except Exception as e:  # noqa: BLE001 —— 工具异常统一兜住，不让 agent 崩
        return {"ok": False, "error": str(e)}
