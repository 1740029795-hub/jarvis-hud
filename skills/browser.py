"""浏览器操控（Phase 2 · 借力 huashu-chrome）

huashu-chrome 是一个 MCP server + Chrome/Edge 扩展，让 agent 操控用户自己已登录的浏览器。
本模块实现一个最小 MCP stdio 客户端（JSON-RPC 2.0，纯标准库 subprocess），
连上 `npx -y huashu-chrome mcp`，把它暴露的浏览器工具动态注册成贾维斯的工具。

设计：
- 懒连接：register_all() 才拉起 npx 进程并做 MCP 握手，失败只降级（浏览器工具不可用），不影响主流程。
- 动态注册：工具定义直接来自 huashu-chrome 的 tools/list，不硬编码，避免版本漂移。
- 工具名加 web_ 前缀，避免和已有桌面工具（如 screenshot）撞名。
"""
import json
import subprocess
import threading
import time

from core.skills import register_tool

_MCP_CMD = "npx -y huashu-chrome mcp --client jarvis"
_PROTOCOL_VERSION = "2024-11-05"

_client = None
_lock = threading.Lock()


class _McpClient:
    """最小 MCP stdio 客户端：initialize → tools/list → tools/call。"""

    def __init__(self):
        self.proc = subprocess.Popen(
            _MCP_CMD,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            shell=True,
            text=True,
            encoding="utf-8",
            bufsize=1,
        )
        self._rid = 0
        self._wlock = threading.Lock()  # stdio 是单请求/响应串行，得锁住
        result = self._request("initialize", {
            "protocolVersion": _PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "jarvis", "version": "0.1.0"},
        })
        self.instructions = result.get("instructions", "")
        self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        self.tools = self._request("tools/list", {}).get("tools", [])

    def _send(self, payload: dict):
        self.proc.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
        self.proc.stdin.flush()

    def _request(self, method: str, params: dict | None = None) -> dict:
        with self._wlock:
            rid = self._rid
            self._rid += 1
            self._send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}})
            while True:
                line = self.proc.stdout.readline()
                if not line:
                    raise RuntimeError("huashu-chrome MCP 进程已退出")
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if "id" not in msg:
                    continue  # 通知，跳过
                if msg.get("id") == rid:
                    if "error" in msg:
                        err = msg["error"]
                        raise RuntimeError(err.get("message", str(err)))
                    return msg.get("result", {})

    def call(self, name: str, args: dict) -> dict:
        result = self._request("tools/call", {"name": name, "arguments": args})
        texts = []
        for c in result.get("content", []):
            if c.get("type") == "text":
                texts.append(c["text"])
            elif c.get("type") == "image":
                texts.append(f"[图片 {c.get('mimeType', 'image/png')} base64 {len(c.get('data', ''))} 字符]")
        text = "\n".join(texts).strip()
        if result.get("isError"):
            return {"ok": False, "text": text or "（浏览器工具返回错误）"}
        return {"ok": True, "text": text or "（无返回内容）"}


def _get_client() -> _McpClient:
    global _client
    with _lock:
        if _client is None:
            _client = _McpClient()
        return _client


def _wake_edge():
    """激活/启动 Edge，让断连的 huashu-chrome 扩展重新连上桥。"""
    try:
        subprocess.Popen(["cmd", "/c", "start", "", "msedge"], shell=True)
    except Exception:
        pass


def _call_browser(mcp_name: str, args: dict) -> str:
    """转发一次浏览器工具调用。失败时抛出异常，由 core.skills.dispatch 统一包成 error。

    扩展断连（NO_EXTENSION）时自动唤醒 Edge 让扩展重连并重试一次，
    避免用户每次都要手动打开/重连浏览器。
    """
    global _client
    try:
        result = _get_client().call(mcp_name, args)
    except Exception:
        _client = None  # 进程可能挂了，下次重建
        raise
    if not result["ok"] and "NO_EXTENSION" in result["text"]:
        _wake_edge()
        time.sleep(3)
        try:
            result = _get_client().call(mcp_name, args)
        except Exception:
            _client = None
            raise
    if not result["ok"]:
        raise RuntimeError(result["text"])
    return result["text"]


def _make_handler(mcp_name: str):
    def handler(**kwargs):
        return _call_browser(mcp_name, kwargs)
    handler.__name__ = f"web_{mcp_name}"
    return handler


def _first_type(anyof) -> str:
    if isinstance(anyof, list):
        for alt in anyof:
            if isinstance(alt, dict) and "type" in alt:
                t = alt["type"]
                return t[0] if isinstance(t, list) else t
    return "string"


def _sanitize_schema(schema):
    """把 MCP inputSchema 转成 DeepSeek/OpenAI function calling 兼容的 parameters。

    处理 function calling 不认的写法：anyOf、type 数组。
    """
    if isinstance(schema, dict):
        out = {}
        for k, v in schema.items():
            if k == "anyOf":
                out["type"] = _first_type(v)
                if isinstance(v, list) and v and isinstance(v[0], dict) and "properties" in v[0]:
                    out["properties"] = _sanitize_schema(v[0]["properties"])
                continue
            if k == "type" and isinstance(v, list):
                out["type"] = v[0] if v else "string"
                continue
            out[k] = _sanitize_schema(v)
        return out
    if isinstance(schema, list):
        return [_sanitize_schema(x) for x in schema]
    return schema


def register_all() -> int:
    """连接 huashu-chrome 并把它的浏览器工具注册成贾维斯工具。返回注册数量（失败返回 0）。"""
    global _client
    try:
        client = _get_client()
    except Exception as e:
        print(f"[browser] 连接 huashu-chrome 失败，浏览器工具不可用：{e}", flush=True)
        return 0

    n = 0
    for t in client.tools:
        mcp_name = t.get("name")
        if not mcp_name:
            continue
        jarvis_name = f"web_{mcp_name}"
        desc = "浏览器工具：" + (t.get("description") or "")
        params = _sanitize_schema(t.get("inputSchema") or {"type": "object", "properties": {}})
        register_tool(jarvis_name, desc, params, _make_handler(mcp_name))
        n += 1

    print(f"[browser] 已注册 {n} 个浏览器工具（huashu-chrome）", flush=True)
    return n
