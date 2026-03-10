"""OpenClaw 适配层 - 将 DocAssistant 的 skill 注册到 OpenClaw"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, List, Optional

from .skills import DocAssistant
from .skills.runtime import SkillRuntime


# skill 描述信息，OpenClaw 用来展示和路由
SKILL_SPECS = [
    {
        "name": "fetch_doc",
        "description": "抓取指定云厂商的产品文档，支持按产品发现或按链接直接抓取，可选 AI 摘要",
        "method": "fetch_doc",
    },
    {
        "name": "check_changes",
        "description": "检测指定云厂商产品文档的变更，与历史版本对比，生成变更摘要",
        "method": "check_changes",
    },
    {
        "name": "compare_docs",
        "description": "对比两个云厂商的产品文档，AI 分析差异点和侧重点",
        "method": "compare_docs",
    },
    {
        "name": "summarize_diff",
        "description": "对新旧两个版本的文档内容进行 diff 和 AI 摘要",
        "method": "summarize_diff",
    },
    {
        "name": "run_monitor",
        "description": "批量巡检多云多产品文档，生成日报摘要，可推送通知",
        "method": "run_monitor",
    },
]


@dataclass(frozen=True)
class OpenClawSkillSpec:
    name: str
    handler: Callable[..., Any]
    description: str
    is_async: bool = False


class OpenClawAdapter:
    """将 DocAssistant 暴露为 OpenClaw 可注册的 skill"""

    def __init__(
        self,
        assistant: Optional[DocAssistant] = None,
        llm_api_key: str = "",
        llm_api_base: str = "",
        llm_model: str = "",
    ) -> None:
        self.assistant = assistant or DocAssistant(
            llm_api_key=llm_api_key,
            llm_api_base=llm_api_base,
            llm_model=llm_model,
        )

    def list_skills(self) -> List[OpenClawSkillSpec]:
        return [
            OpenClawSkillSpec(
                name=spec["name"],
                handler=getattr(self.assistant, spec["method"]),
                description=spec["description"],
            )
            for spec in SKILL_SPECS
        ]

    def registry(self) -> dict[str, Callable[..., Any]]:
        """返回 {skill_name: handler} 字典"""
        return {spec.name: spec.handler for spec in self.list_skills()}

    def register(self, register_fn: Callable[..., Any]) -> List[OpenClawSkillSpec]:
        """通过回调函数注册所有 skill"""
        specs = self.list_skills()
        for spec in specs:
            try:
                register_fn(
                    name=spec.name,
                    handler=spec.handler,
                    description=spec.description,
                    is_async=spec.is_async,
                )
            except TypeError:
                register_fn(spec.name, spec.handler)
        return specs


def build_openclaw_registry(
    llm_api_key: str = "",
    llm_api_base: str = "",
    llm_model: str = "",
) -> dict[str, Callable[..., Any]]:
    """快捷方式：构建 skill registry 字典"""
    return OpenClawAdapter(
        llm_api_key=llm_api_key,
        llm_api_base=llm_api_base,
        llm_model=llm_model,
    ).registry()


def register_openclaw_skills(
    register_fn: Callable[..., Any],
    llm_api_key: str = "",
    llm_api_base: str = "",
    llm_model: str = "",
) -> List[OpenClawSkillSpec]:
    """快捷方式：通过回调注册所有 skill"""
    return OpenClawAdapter(
        llm_api_key=llm_api_key,
        llm_api_base=llm_api_base,
        llm_model=llm_model,
    ).register(register_fn)
