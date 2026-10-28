"""LLM 主脑：DeepSeek function calling

自然语言 -> 理解意图 -> 决定是否调用工具 -> 执行 -> 汇总回复。
零第三方 HTTP 依赖（用标准库 urllib）。
"""
import base64
import json
import threading
import urllib.request
from pathlib import Path

from core import config
from core.skills import TOOLS, dispatch

SYSTEM_PROMPT = (
    "你是贾维斯（J.A.R.V.I.S.），一个中文语音助手。回答简洁、口语化，像人对话，不要罗列。"
    "用户要你做任何实际动作（查时间、打开软件、操作软件、查网页、读写记忆、建表格、定时提醒等）时，"
    "必须调用对应的工具真正执行，不要自己心算、猜测或空口回答。"
    "具体怎么用看每个工具的 description；需要多步操作时按工具说明的顺序来，每步读回验证。"
    "网页/浏览器操作直接用 web_* 工具（它们在你已打开的浏览器里操作），不要先调用 open_app 打开浏览器。"
    "网页搜索默认用必应 Bing，URL 形如 https://www.bing.com/search?q=关键词，不要用百度。"
    "做完用一句话把结果告诉用户。"
    "重要：如果工具返回错误（ok 为 false、或含 error 字段），必须如实告诉用户「失败」和原因，"
    "不要谎称成功、不要替用户脑补已经完成。"
)


def _run(messages: list, tools: list, max_rounds: int = 15) -> str:
    """跑一轮「LLM 思考 + 工具调用」循环，直到 LLM 给出最终回复或轮次超限。"""
    for _ in range(max_rounds):  # 工具调用最多 15 轮，防死循环（桌面操控需多步顺序操作）
        resp = _call(messages, tools)
        msg = resp["choices"][0]["message"]
        if msg.get("tool_calls"):
            messages.append({"role": "assistant", "content": msg.get("content") or "", "tool_calls": msg["tool_calls"]})
            for tc in msg["tool_calls"]:
                fn = tc["function"]
                name = fn["name"]
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                except json.JSONDecodeError:
                    args = {}
                print(f"[tool] {name} {json.dumps(args, ensure_ascii=False)}", flush=True)
                result = dispatch(name, args)
                print(f"[tool] {name} -> {json.dumps(result, ensure_ascii=False)[:200]}", flush=True)
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": json.dumps(result, ensure_ascii=False),
                })
        else:
            return msg.get("content") or "（无回复）"
    return "（工具调用轮次超限）"


def ask(user_text: str, history: list | None = None) -> str:
    """返回最终回复文本。history 是完整的多轮消息列表（可选）。"""
    messages = list(history) if history else [{"role": "system", "content": SYSTEM_PROMPT}]
    if messages and messages[0].get("role") != "system":
        messages.insert(0, {"role": "system", "content": SYSTEM_PROMPT})
    messages.append({"role": "user", "content": user_text})
    return _run(messages, TOOLS)


# ---------------- 子智能体（Phase 4） ----------------

_SUBAGENT_SYSTEM = (
    "你是贾维斯的一个子智能体，负责独立完成主脑交给你的一件具体任务。"
    "你可以调用工具，但只做这一件事，做完用简洁的话回报结果，不要啰嗦，不要再次委托给别人。"
)

# 子智能体不提供「再次委托」工具，防止无限递归
_DELEGATE_TOOL_NAMES = {"delegate_task", "delegate_parallel"}


def _subagent_tools() -> list:
    return [t for t in TOOLS if t["function"]["name"] not in _DELEGATE_TOOL_NAMES]


def delegate(task: str, tools: list | None = None) -> str:
    """派一个子智能体：独立上下文跑完任务，只回结论（不污染主对话历史）。"""
    messages = [
        {"role": "system", "content": _SUBAGENT_SYSTEM},
        {"role": "user", "content": task},
    ]
    return _run(messages, tools if tools is not None else _subagent_tools())


def delegate_parallel(tasks: list, tools: list | None = None) -> list:
    """并行派多个子智能体，各自独立上下文，按输入顺序返回各任务结果。"""
    results: list = [None] * len(tasks)

    def _worker(i: int, t: str):
        results[i] = delegate(t, tools)

    threads = [threading.Thread(target=_worker, args=(i, t)) for i, t in enumerate(tasks)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    return results


def _post(payload: dict) -> dict:
    req = urllib.request.Request(
        f"{config.DEEPSEEK_BASE_URL}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {config.DEEPSEEK_API_KEY}",
        },
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode("utf-8"))


def _call(messages: list, tools: list | None = None) -> dict:
    if not config.DEEPSEEK_API_KEY:
        return _mock_reply(messages)
    return _post({
        "model": config.DEEPSEEK_MODEL,
        "messages": messages,
        "tools": tools if tools is not None else TOOLS,
        "temperature": 0.3,
    })


def llm_chat(messages: list, temperature: float = 0.3) -> str:
    """无工具纯文本补全，供辅助任务（自动记忆抽取、摘要等）复用。无 key 时返回空串。"""
    if not config.DEEPSEEK_API_KEY:
        return ""
    data = _post({
        "model": config.DEEPSEEK_MODEL,
        "messages": messages,
        "temperature": temperature,
    })
    return (data["choices"][0]["message"].get("content") or "").strip()


def _load_image_for_vision(path: Path) -> tuple[str, bytes]:
    """读取图片，过大则缩到最长边 1600 并转 JPEG，减小请求体积（视觉模型内部还会再缩）。"""
    from io import BytesIO
    from PIL import Image
    img = Image.open(path).convert("RGB")
    if max(img.size) > 1600:
        img.thumbnail((1600, 1600))
    buf = BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return "image/jpeg", buf.getvalue()


def llm_vision(prompt: str, image_path: str) -> str:
    """用 DeepSeek 视觉模型（deepseek-flash）看一张图并回答 prompt，返回文本。"""
    if not config.DEEPSEEK_API_KEY:
        return "（未配置 DEEPSEEK_API_KEY，无法使用视觉）"
    path = Path(image_path)
    if not path.exists():
        raise FileNotFoundError(f"图片不存在：{image_path}")
    mime, img_bytes = _load_image_for_vision(path)
    b64 = base64.b64encode(img_bytes).decode("ascii")
    data = _post({
        "model": config.DEEPSEEK_VISION_MODEL,
        "messages": [
            {"role": "system", "content": "你是视觉助手，能看懂截图里的界面。回答简洁准确，用中文。"},
            {"role": "user", "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
            ]},
        ],
        "temperature": 0.3,
    })
    return (data["choices"][0]["message"].get("content") or "").strip()


def _mock_reply(messages: list) -> dict:
    """未配置 API key 时的离线兜底，保证语音回路可联调。"""
    return {
        "choices": [{
            "message": {
                "role": "assistant",
                "content": "（未配置 DEEPSEEK_API_KEY，这是离线 mock 回复。请设置环境变量后重试。）",
            }
        }]
    }
