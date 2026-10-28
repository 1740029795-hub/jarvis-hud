"""J.A.R.V.I.S. 后端入口（FastAPI）

POST /stt   音频文件 -> 文本
POST /ask   文本 -> 回复（含工具调用）

启动：
    .venv/Scripts/python.exe server.py
"""
import subprocess
import tempfile
import threading
from pathlib import Path

from fastapi import FastAPI, File, UploadFile, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from core import brain, config
from stt import asr
from tts import tts
import skills.system  # noqa: F401  导入即注册受控命令工具
import skills.desktop  # noqa: F401  导入即注册桌面操控工具（Phase 2 L1 UIA）
from core import memory  # noqa: F401  导入即注册长期记忆工具（Phase 3），并供自动记忆调用
import skills.browser  # noqa: F401  浏览器工具（Phase 2 借力 huashu-chrome）
import skills.wps  # noqa: F401  WPS Office 工具（Phase 2 L0 COM 结构接口）
import skills.reminder  # noqa: F401  定时任务/提醒（Phase 4）
import skills.vision  # noqa: F401  L3 视觉兜底（Phase 4）
import skills.agent  # noqa: F401  子智能体编排（Phase 4）

# 注册浏览器工具：连接 huashu-chrome（npx + MCP 握手）。失败只降级跳过，不影响语音/桌面功能。
skills.browser.register_all()
# 启动提醒调度线程（幂等，后台每 15 秒查一次到期提醒）。
skills.reminder.start()


# 后台预加载本地模型（whisper + 记忆 embedding），避免首次语音/记忆操作卡 5~20 秒
def _preload_models():
    try:
        asr.get_model()
        print("[server] whisper 模型已预加载", flush=True)
    except Exception as e:  # noqa: BLE001
        print(f"[server] whisper 预加载失败：{e}", flush=True)
    try:
        memory.preload()
        print("[server] 记忆 embedding 模型已预加载", flush=True)
    except Exception as e:  # noqa: BLE001
        print(f"[server] embedding 预加载失败：{e}", flush=True)


threading.Thread(target=_preload_models, daemon=True).start()

app = FastAPI(title="J.A.R.V.I.S. Backend")

# 允许前端（http://localhost:8000）跨域访问
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:8000", "http://127.0.0.1:8000"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class AskRequest(BaseModel):
    message: str


class TTSRequest(BaseModel):
    text: str


@app.post("/stt")
async def stt(file: UploadFile = File(...)):
    """浏览器录音 -> 识别文本。webm -> ffmpeg 转 16k mono wav -> whisper。"""
    raw = await file.read()
    with tempfile.NamedTemporaryFile(suffix=".webm", delete=False) as f:
        f.write(raw)
        webm = f.name
    wav = webm + ".wav"
    try:
        subprocess.run(
            [config.FFMPEG_BIN, "-y", "-i", webm, "-ar", "16000", "-ac", "1", wav],
            check=True, capture_output=True,
        )
        text = asr.transcribe(wav)
        return {"text": text}
    finally:
        for p in (Path(webm), Path(wav)):
            p.unlink(missing_ok=True)


# 会话级短期记忆：单用户本地助手，全局列表足够。存 user/assistant 纯文本对，不存工具调用的中间过程。
_conversation_history: list[dict] = []
_MAX_HISTORY_TURNS = 10  # 保留最近 10 轮（20 条消息），防上下文过长


@app.post("/ask")
async def ask(req: AskRequest):
    reply = brain.ask(req.message, _conversation_history)
    _conversation_history.append({"role": "user", "content": req.message})
    _conversation_history.append({"role": "assistant", "content": reply})
    if len(_conversation_history) > _MAX_HISTORY_TURNS * 2:
        del _conversation_history[: len(_conversation_history) - _MAX_HISTORY_TURNS * 2]

    # 自动记忆（Phase 3 增强）：后台线程抽取本轮对话里的用户信息并去重存档，不阻塞回复
    try:
        threading.Thread(
            target=memory.auto_remember,
            args=(f"用户：{req.message}\n贾维斯：{reply}",),
            daemon=True,
        ).start()
    except Exception:
        pass

    return {"reply": reply}


@app.post("/tts")
def tts_endpoint(req: TTSRequest):
    """文本 -> 语音 mp3（edge-tts，晓晓女声）。同步端点：跑在线程池，避开事件循环里 asyncio.run 的冲突。"""
    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
        out = f.name
    try:
        tts.speak_to_file(req.text, out)
        data = Path(out).read_bytes()
        return Response(content=data, media_type="audio/mpeg")
    finally:
        Path(out).unlink(missing_ok=True)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=config.HOST, port=config.PORT)
