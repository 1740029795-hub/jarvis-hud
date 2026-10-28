"""J.A.R.V.I.S. 配置中心

所有可调项集中在这里。API key 建议用环境变量注入，不要硬编码进代码。
"""
import os
import shutil
from pathlib import Path

# 读取项目根目录的 .env（简单 key=value 解析，不引入额外依赖）
_ENV_FILE = Path(__file__).resolve().parent.parent / ".env"
if _ENV_FILE.exists():
    for _line in _ENV_FILE.read_text(encoding="utf-8").splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip())

# 国内下载 whisper 模型必需：huggingface 走镜像 + 禁用 xet 协议（xet 在镜像上会 401）
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
os.environ.setdefault("HF_HUB_DISABLE_XET", "1")

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
MODEL_DIR = BASE_DIR / "model"
DATA_DIR.mkdir(exist_ok=True)
MODEL_DIR.mkdir(exist_ok=True)

# ---- DeepSeek（LLM 主脑）----
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
DEEPSEEK_MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")
# 视觉模型（L3 视觉兜底）：deepseek-flash 原生多模态，同一个 key 就能传图
DEEPSEEK_VISION_MODEL = os.environ.get("DEEPSEEK_VISION_MODEL", "deepseek-flash")

# ---- Whisper（语音识别）----
WHISPER_MODEL = os.environ.get("WHISPER_MODEL", "small")      # tiny/base/small/medium
WHISPER_DEVICE = os.environ.get("WHISPER_DEVICE", "cpu")      # 默认 CPU（机器无 CUDA 库，auto 会报 cublas 缺失）
WHISPER_COMPUTE_TYPE = os.environ.get("WHISPER_COMPUTE_TYPE", "int8")

# ---- ffmpeg（STT 转码 webm -> wav 依赖）----
# 按优先级找「真实存在」的 ffmpeg：环境变量 > 系统 PATH > 项目内 ffmpeg\bin\ffmpeg.exe
def _find_ffmpeg() -> str:
    env = os.environ.get("FFMPEG_BIN")
    if env:
        return env
    which = shutil.which("ffmpeg")
    if which:
        return which
    local = BASE_DIR / "ffmpeg" / "bin" / "ffmpeg.exe"
    if local.exists():
        return str(local)
    return ""


FFMPEG_BIN = _find_ffmpeg()

# ---- 后端服务 ----
HOST = "127.0.0.1"
PORT = 8001
