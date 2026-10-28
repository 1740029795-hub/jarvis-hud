"""语音识别：faster-whisper（本地离线）

首次调用会加载模型并自动下载到 model/ 目录。
"""
from faster_whisper import WhisperModel

from core import config

_model: WhisperModel | None = None


def get_model() -> WhisperModel:
    global _model
    if _model is None:
        _model = WhisperModel(
            config.WHISPER_MODEL,
            device=config.WHISPER_DEVICE,
            compute_type=config.WHISPER_COMPUTE_TYPE,
            download_root=str(config.MODEL_DIR),
        )
    return _model


def transcribe(audio_path: str, language: str = "zh") -> str:
    """识别音频文件，返回纯文本。"""
    model = get_model()
    segments, _info = model.transcribe(audio_path, language=language, beam_size=5)
    text = "".join(seg.text for seg in segments).strip()
    return text
