"""L3 视觉兜底（Phase 4）

UIA（L1）找不到控件、坐标（L2）对不上时，截屏让视觉模型（DeepSeek deepseek-flash）看屏幕，
回答「界面上有什么 / 某个控件在什么位置」，供 LLM 据此继续操作。

这是四层控制面的最后一层（L0 结构 → L1 UIA → L2 坐标 → L3 像素/视觉）。
"""
from core import brain
from core.skills import tool
from skills.desktop import screenshot as _screenshot


@tool(
    "vision_analyze",
    "让视觉模型看一张截图并回答你的问题（L3 视觉兜底：UIA 找不到控件时，用它看屏幕识别界面元素和位置）",
    {
        "type": "object",
        "properties": {
            "prompt": {
                "type": "string",
                "description": "要问的问题，如「这个界面上有哪些按钮和输入框」「计算器的等于号按钮在屏幕大约什么位置（给出坐标）」",
            },
            "image_path": {
                "type": "string",
                "description": "可选：要分析的图片路径。不填则先截当前屏幕再分析",
            },
        },
        "required": ["prompt"],
    },
)
def vision_analyze(prompt: str, image_path: str | None = None) -> dict:
    if not image_path:
        shot = _screenshot()
        image_path = shot["path"]
    answer = brain.llm_vision(prompt, image_path)
    return {"ok": True, "effect": "confirmed", "image": image_path, "answer": answer}
