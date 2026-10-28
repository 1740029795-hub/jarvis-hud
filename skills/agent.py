"""子智能体编排（Phase 4）

主脑可以把杂活/长任务派给子智能体：子智能体独立上下文跑完，只回结论，不污染主对话历史。
- delegate_task       派一个子智能体干一件具体的事
- delegate_parallel   并行派多个子智能体干多件互不依赖的事

子智能体跑同一个「思考 + 工具调用」循环，但拿不到 delegate_* 工具，防止无限递归。
"""
from core import brain
from core.skills import tool


@tool(
    "delegate_task",
    "把一个子任务交给一个独立的子智能体去做（独立上下文，不污染主对话），返回它的结论。适合长任务或想隔离的杂活",
    {
        "type": "object",
        "properties": {
            "task": {"type": "string", "description": "要交给子智能体的具体任务，说清楚要做什么、要什么结果"},
        },
        "required": ["task"],
    },
)
def delegate_task(task: str) -> str:
    return brain.delegate(task)


@tool(
    "delegate_parallel",
    "并行派多个子智能体同时做多个独立子任务，返回各任务结果（顺序与输入一致）。适合多个互不依赖的查询",
    {
        "type": "object",
        "properties": {
            "tasks": {
                "type": "array",
                "items": {"type": "string"},
                "description": "多个独立子任务，每个一句话说清楚",
            },
        },
        "required": ["tasks"],
    },
)
def delegate_parallel(tasks: list) -> list:
    return brain.delegate_parallel(tasks)
