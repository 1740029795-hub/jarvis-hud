"""定时任务 / 提醒（Phase 4）

到点后用语音把提醒内容说出来，并落库留痕。纯后端、零新依赖：
- 存储：SQLite（`data/reminders.db`），与长期记忆同思路，数据全透明
- 调度：后台守护线程每 15 秒查一次到期提醒
- 播报：edge-tts 合成 → ffmpeg 转 wav → winsound 播放（异步），失败退回 Windows SAPI

工具：
- schedule_reminder(message, fire_at)  定时提醒（fire_at 支持 YYYY-MM-DD HH:MM 或 HH:MM）
- list_reminders                       列出未触发的提醒
- cancel_reminder(id)                  取消某条提醒

工作流：用户说「5 分钟后提醒我喝水」→ LLM 先 get_time 算好目标时间 → schedule_reminder。
"""
import calendar
import sqlite3
import subprocess
import tempfile
import threading
import time
import winsound  # Windows 播放 wav
from datetime import datetime, timedelta

from core import config
from core.skills import tool
from tts import tts

_DB_PATH = config.DATA_DIR / "reminders.db"

_scheduler_started = False
_start_lock = threading.Lock()


# ---------------- 存储 ----------------

def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _init_db():
    conn = _conn()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS reminders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fire_at TEXT NOT NULL,
            message TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',   -- pending / done / cancelled
            recurrence TEXT,                          -- NULL=一次性 / daily / weekly / monthly
            created_at TEXT NOT NULL
        )
        """
    )
    # 迁移：旧表可能没有 recurrence 列（循环提醒是后加的）
    cols = [r["name"] for r in conn.execute("PRAGMA table_info(reminders)")]
    if "recurrence" not in cols:
        conn.execute("ALTER TABLE reminders ADD COLUMN recurrence TEXT")
    conn.commit()
    conn.close()


# ---------------- 时间解析 ----------------

def _parse_fire_at(s: str) -> datetime:
    """把 LLM 给的时间字符串解析成 datetime。支持 YYYY-MM-DD HH:MM[:SS] 或 HH:MM[:SS]。"""
    s = s.strip().replace("T", " ")
    now = datetime.now()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%H:%M:%S", "%H:%M"):
        try:
            dt = datetime.strptime(s, fmt)
        except ValueError:
            continue
        if fmt.startswith("%H"):  # 只给了时间：补今天日期，已过则顺延到明天
            dt = dt.replace(year=now.year, month=now.month, day=now.day)
            if dt <= now:
                dt += timedelta(days=1)
        elif dt <= now:
            raise ValueError(f"时间 {s} 已经过去，请指定一个未来时间")
        return dt
    raise ValueError(f"无法解析时间 {s!r}，请用 YYYY-MM-DD HH:MM 或 HH:MM 格式")


def _add_months(dt: datetime, n: int) -> datetime:
    """加 n 个月，日号超出目标月天数时夹到月末。"""
    month = dt.month - 1 + n
    year = dt.year + month // 12
    month = month % 12 + 1
    day = min(dt.day, calendar.monthrange(year, month)[1])
    return dt.replace(year=year, month=month, day=day)


def _next_fire_at(fire_at: datetime, recurrence: str) -> datetime:
    if recurrence == "daily":
        return fire_at + timedelta(days=1)
    if recurrence == "weekly":
        return fire_at + timedelta(days=7)
    if recurrence == "monthly":
        return _add_months(fire_at, 1)
    raise ValueError(f"未知的循环类型 {recurrence!r}")


# ---------------- 播报 ----------------

def _speak_aloud(text: str):
    """edge-tts 合成 → ffmpeg 转 wav → winsound 异步播放。任何一步失败退回 Windows SAPI。"""
    mp3 = wav = None
    try:
        f = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
        f.close()
        mp3 = f.name
        wav = mp3 + ".wav"
        tts.speak_to_file(text, mp3)
        subprocess.run(
            [config.FFMPEG_BIN, "-y", "-i", mp3, "-ar", "24000", "-ac", "1", wav],
            check=True, capture_output=True,
        )
        winsound.PlaySound(wav, winsound.SND_FILENAME | winsound.SND_ASYNC)
    except Exception:
        try:
            import win32com.client
            win32com.client.Dispatch("SAPI.SpVoice").Speak(text)
        except Exception as e:
            print(f"[reminder] 播报失败：{e}", flush=True)
    # 临时文件不立即删：异步播放中删了会断声，留在系统临时目录由 OS 清理


# ---------------- 调度 ----------------

def _fire_due():
    """找出到期的 pending 提醒：一次性标记 done；循环提醒推进到下一个周期（保持 pending）。"""
    now_dt = datetime.now()
    now = now_dt.isoformat(timespec="seconds")
    conn = _conn()
    rows = conn.execute(
        "SELECT * FROM reminders WHERE status='pending' AND fire_at <= ? ORDER BY id", (now,)
    ).fetchall()
    for r in rows:
        if r["recurrence"]:
            nxt = datetime.fromisoformat(r["fire_at"])
            while nxt <= now_dt:  # 把错过多轮的都补推到未来
                nxt = _next_fire_at(nxt, r["recurrence"])
            conn.execute("UPDATE reminders SET fire_at=? WHERE id=?", (nxt.isoformat(timespec="seconds"), r["id"]))
            conn.commit()
            print(f"[reminder] 循环触发 #{r['id']}（{r['recurrence']}）：{r['message']}，下次 {nxt}", flush=True)
        else:
            conn.execute("UPDATE reminders SET status='done' WHERE id=?", (r["id"],))
            conn.commit()
            print(f"[reminder] 触发 #{r['id']}: {r['message']}", flush=True)
        _speak_aloud(r["message"])
    conn.close()


def _scheduler_loop():
    while True:
        try:
            _fire_due()
        except Exception as e:
            print(f"[reminder] 调度异常：{e}", flush=True)
        time.sleep(15)


def start():
    """启动后台调度线程（幂等，重复调用只起一次）。"""
    global _scheduler_started
    with _start_lock:
        if _scheduler_started:
            return
        _init_db()
        threading.Thread(target=_scheduler_loop, daemon=True, name="reminder-scheduler").start()
        _scheduler_started = True
        print("[reminder] 调度线程已启动", flush=True)


# ---------------- 工具 ----------------

@tool(
    "schedule_reminder",
    "定一个提醒：到指定时间用语音把提醒内容说出来。fire_at 用 YYYY-MM-DD HH:MM 或 HH:MM；recurrence 填 daily/weekly/monthly 可做循环提醒",
    {
        "type": "object",
        "properties": {
            "message": {"type": "string", "description": "提醒内容，一句话，口语化"},
            "fire_at": {
                "type": "string",
                "description": "触发时间：YYYY-MM-DD HH:MM[:SS]，或只给 HH:MM（默认今天，已过则明天）",
            },
            "recurrence": {
                "type": "string",
                "enum": ["daily", "weekly", "monthly"],
                "description": "可选：循环周期。daily=每天此时，weekly=每周此时，monthly=每月此日此时。不填=一次性",
            },
        },
        "required": ["message", "fire_at"],
    },
)
def schedule_reminder(message: str, fire_at: str, recurrence: str | None = None) -> dict:
    _init_db()
    dt = _parse_fire_at(fire_at)
    start()  # 确保调度线程在跑
    conn = _conn()
    cur = conn.execute(
        "INSERT INTO reminders (fire_at, message, status, recurrence, created_at) VALUES (?, ?, 'pending', ?, ?)",
        (dt.isoformat(timespec="seconds"), message, recurrence, datetime.now().isoformat(timespec="seconds")),
    )
    conn.commit()
    conn.close()
    return {
        "ok": True,
        "effect": "confirmed",
        "reminder_id": cur.lastrowid,
        "fire_at": dt.isoformat(timespec="seconds"),
        "recurrence": recurrence,
        "message": message,
    }


@tool(
    "list_reminders",
    "列出还没有触发的提醒（用户问「有哪些提醒」时调用）",
    {"type": "object", "properties": {}},
)
def list_reminders() -> dict:
    _init_db()
    conn = _conn()
    rows = conn.execute(
        "SELECT id, fire_at, message, status, recurrence FROM reminders WHERE status='pending' ORDER BY fire_at"
    ).fetchall()
    conn.close()
    return {
        "ok": True,
        "effect": "confirmed",
        "count": len(rows),
        "results": [dict(r) for r in rows],
    }


@tool(
    "cancel_reminder",
    "取消一条提醒（用户说「取消提醒」时调用，需要提醒 id）",
    {
        "type": "object",
        "properties": {
            "id": {"type": "integer", "description": "提醒 id（来自 list_reminders）"},
        },
        "required": ["id"],
    },
)
def cancel_reminder(id: int) -> dict:
    _init_db()
    conn = _conn()
    cur = conn.execute("UPDATE reminders SET status='cancelled' WHERE id=? AND status='pending'", (id,))
    conn.commit()
    conn.close()
    effect = "confirmed" if cur.rowcount > 0 else "suspected_noop"
    return {"ok": True, "effect": effect, "cancelled": cur.rowcount > 0}
