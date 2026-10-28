"""语音合成：edge-tts（微软音色，免费）

Phase 1 前端仍用浏览器 speechSynthesis（零依赖、已验证）。
后端 edge-tts 作为可选升级，前端需要时调用 /tts 即可。
"""
import asyncio

import edge_tts


async def _speak(text: str, voice: str, out_path: str):
    c = edge_tts.Communicate(text, voice)
    await c.save(out_path)


def speak_to_file(text: str, out_path: str, voice: str = "zh-CN-XiaoxiaoNeural") -> str:
    """合成语音到文件，返回文件路径。"""
    asyncio.run(_speak(text, voice, out_path))
    return out_path
