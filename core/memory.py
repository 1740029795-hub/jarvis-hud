"""长期记忆（Phase 3）：SQLite 持久化 + 语义检索

- 存储：SQLite（`data/memory.db`），embedding 存 BLOB，数据全透明。
- 检索：sentence-transformers 余弦相似度（语义「按意思」找回）；
  embedding 不可用时自动降级到子串匹配。

模型：paraphrase-multilingual-MiniLM-L12-v2（多语言，约 120MB，走 hf-mirror）。
"""
import os

# 确保走 hf-mirror 镜像（国内直连 huggingface.co 会超时）
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
os.environ.setdefault("HF_HUB_DISABLE_XET", "1")

import json
import sqlite3
from datetime import datetime

import numpy as np

from core import config
from core.skills import tool

_DB_PATH = config.DATA_DIR / "memory.db"
_MODEL_NAME = "paraphrase-multilingual-MiniLM-L12-v2"
_model = None  # 懒加载，首次调用才下载/加载模型


def _get_model():
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer
        _model = SentenceTransformer(_MODEL_NAME, cache_folder=str(config.MODEL_DIR))
    return _model


def preload():
    """预加载 embedding 模型，避免首次 remember/recall 卡顿。失败静默。"""
    try:
        _get_model()
    except Exception:
        pass


def _embed(text: str) -> np.ndarray:
    """文本 -> 归一化向量（L2 norm=1，点积即余弦相似度）。"""
    return _get_model().encode(text, normalize_embeddings=True)


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _init_db():
    conn = _conn()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS memories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            content TEXT NOT NULL,
            embedding BLOB,
            created_at TEXT NOT NULL
        )
        """
    )
    # 迁移：旧表可能没有 embedding 列（Phase 3 第二步之前建的）
    cols = [r["name"] for r in conn.execute("PRAGMA table_info(memories)")]
    if "embedding" not in cols:
        conn.execute("ALTER TABLE memories ADD COLUMN embedding BLOB")
    conn.commit()
    conn.close()


@tool(
    "remember",
    "把一条重要信息记到长期记忆（用户说「记住…」时调用）",
    {
        "type": "object",
        "properties": {
            "content": {"type": "string", "description": "要记住的内容，一句话说清楚"}
        },
        "required": ["content"],
    },
)
def remember(content: str) -> dict:
    _init_db()
    # 存 embedding（失败也不影响记忆本身，只是少了语义检索）
    blob = None
    try:
        blob = _embed(content).astype(np.float32).tobytes()
    except Exception:
        pass
    conn = _conn()
    cur = conn.execute(
        "INSERT INTO memories (content, embedding, created_at) VALUES (?, ?, ?)",
        (content, blob, datetime.now().isoformat(timespec="seconds")),
    )
    conn.commit()
    conn.close()
    return {"ok": True, "effect": "confirmed", "memory_id": cur.lastrowid}


@tool(
    "recall",
    "从长期记忆里检索相关内容（需要回忆之前聊过/记过的事时调用）",
    {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "要回忆的主题/关键词"},
        },
        "required": ["query"],
    },
)
def recall(query: str, top_k: int = 5) -> dict:
    _init_db()
    conn = _conn()
    rows = conn.execute("SELECT * FROM memories ORDER BY id DESC LIMIT 500").fetchall()
    conn.close()

    if not rows:
        return {"ok": True, "effect": "confirmed", "results": [], "note": "还没有任何记忆"}

    # 语义检索（embedding 余弦相似度）；失败则降级子串匹配
    try:
        q_emb = _embed(query)
        scored = []
        for row in rows:
            if row["embedding"]:
                emb = np.frombuffer(row["embedding"], dtype=np.float32)
                sim = float(np.dot(q_emb, emb))  # 归一化后点积即余弦
                scored.append((sim, row))
            elif query in row["content"]:  # 旧数据无 embedding，子串匹配兜底
                scored.append((0.5, row))
        scored.sort(key=lambda x: -x[0])
        results = [
            {"id": r[1]["id"], "content": r[1]["content"], "created_at": r[1]["created_at"], "score": round(r[0], 3)}
            for r in scored[:top_k]
        ]
    except Exception:
        # 降级：子串匹配
        scored = [(row["content"].count(query), row) for row in rows if query in row["content"]]
        scored.sort(key=lambda x: (-x[0], x[1]["id"]))
        results = [
            {"id": r[1]["id"], "content": r[1]["content"], "created_at": r[1]["created_at"]}
            for r in scored[:top_k]
        ]

    if not results:
        return {"ok": True, "effect": "confirmed", "results": [], "note": "没有找到相关记忆"}
    return {"ok": True, "effect": "confirmed", "results": results}


@tool(
    "list_memories",
    "列出已保存的长期记忆（最近优先）。用户问「记得什么/有哪些记忆」时调用",
    {
        "type": "object",
        "properties": {
            "limit": {"type": "integer", "description": "最多返回条数，默认 50"},
        },
    },
)
def list_memories(limit: int = 50) -> dict:
    _init_db()
    conn = _conn()
    rows = conn.execute(
        "SELECT id, content, created_at FROM memories ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    return {
        "ok": True,
        "effect": "confirmed",
        "count": len(rows),
        "results": [
            {"id": r["id"], "content": r["content"], "created_at": r["created_at"]}
            for r in rows
        ],
    }


@tool(
    "forget_memory",
    "删除一条长期记忆（用户明确要求忘记/删除某条记忆时调用）",
    {
        "type": "object",
        "properties": {
            "content": {"type": "string", "description": "要删除的记忆内容关键词（模糊匹配）"},
            "id": {"type": "integer", "description": "要删除的记忆 id（来自 list_memories）"},
        },
    },
)
def forget_memory(content: str | None = None, id: int | None = None) -> dict:
    _init_db()
    conn = _conn()
    if id is not None:
        cur = conn.execute("DELETE FROM memories WHERE id = ?", (id,))
    elif content:
        cur = conn.execute("DELETE FROM memories WHERE content LIKE ?", (f"%{content}%",))
    else:
        conn.close()
        return {"ok": False, "error": "需提供 content 或 id 之一"}
    conn.commit()
    deleted = cur.rowcount
    conn.close()
    return {"ok": True, "effect": "confirmed", "deleted": deleted}


# ---------------- 自动记忆（Phase 3 增强） ----------------

_AUTOMEM_PROMPT = (
    "你是一个记忆抽取器。下面是一轮对话（用户对语音助手贾维斯说的话，以及贾维斯的回复）。\n"
    "请判断其中是否包含「值得长期记住的、关于用户本人的信息」，例如：\n"
    "- 用户的身份、喜好、习惯、计划、目标\n"
    "- 用户与他人的关系、重要事件、正在进行的事项\n"
    "- 用户明确表达的事实或偏好\n\n"
    "不要记录：临时性的问题、闲聊、命令本身、贾维斯做了什么动作。\n\n"
    "如果没有任何值得记住的信息，返回空数组 []。\n"
    "否则返回 JSON 字符串数组，每项是一条简洁完整的事实（中文，一句话），例如 [\"用户最喜欢的电影是星际穿越\"]。\n\n"
    "只返回 JSON 数组，不要任何其他文字。\n\n"
    "对话：\n{turn_text}"
)


def _parse_json_array(raw: str) -> list[str]:
    """从 LLM 输出里稳健地解析出 JSON 字符串数组。"""
    if not raw:
        return []
    raw = raw.strip()
    if raw.startswith("```"):  # 去掉 markdown 代码围栏
        raw = raw.strip("`")
        if raw.lower().startswith("json"):
            raw = raw[4:]
        raw = raw.strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        try:  # 兜底：取第一个 [ 到最后一个 ]
            start = raw.index("[")
            end = raw.rindex("]") + 1
            data = json.loads(raw[start:end])
        except (ValueError, json.JSONDecodeError):
            return []
    if not isinstance(data, list):
        return []
    return [str(x).strip() for x in data if str(x).strip()]


def _extract_facts(turn_text: str) -> list[str]:
    """用 LLM 从一轮对话里抽取值得长期记住的用户信息（纯文本，无工具）。"""
    from core.brain import llm_chat  # 延迟导入，避免模块加载顺序依赖
    raw = llm_chat([
        {"role": "system", "content": "你只输出 JSON 数组。"},
        {"role": "user", "content": _AUTOMEM_PROMPT.format(turn_text=turn_text)},
    ])
    return _parse_json_array(raw)


def _is_new(content: str, threshold: float = 0.82) -> bool:
    """判断一条事实是否已在记忆里（语义去重）。模型不可用时只做字面去重。"""
    _init_db()
    conn = _conn()
    rows = conn.execute("SELECT content, embedding FROM memories").fetchall()
    conn.close()
    if not rows:
        return True
    for row in rows:
        existing = row["content"]
        # 完全一致或互为子串，视为同一事实
        if content == existing or content in existing or existing in content:
            return False
    try:
        q = _embed(content)
        best = 0.0
        for row in rows:
            if row["embedding"]:
                emb = np.frombuffer(row["embedding"], dtype=np.float32)
                best = max(best, float(np.dot(q, emb)))
        return best < threshold
    except Exception:
        return True


def auto_remember(turn_text: str) -> list[str]:
    """对话后自动抽取并存储值得长期记住的用户信息（去重）。返回新增条目内容列表。

    设计为「尽力而为」：任何一步失败都静默跳过，绝不因自动记忆影响主对话。
    """
    try:
        facts = _extract_facts(turn_text)
    except Exception:
        return []
    stored: list[str] = []
    for fact in facts:
        try:
            if _is_new(fact):
                remember(fact)
                stored.append(fact)
        except Exception:
            continue
    return stored
