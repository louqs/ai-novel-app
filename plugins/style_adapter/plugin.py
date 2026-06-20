"""风格适配器 — 将章节文本适配到目标平台风格.

处理:
    - 移动端优化 (短段落, 对话分行)
    - 平台特定风格 (番茄极简/晋江细腻/起点升级感)
    - 章节钩子强化

用法:
    adapted = await plugin.adapt_style(
        content=chapter_content,
        platform="fanqie",
        mode="rewrite",  # rewrite | polish | minimal
    )
"""

from __future__ import annotations

from typing import Any

from core.logging_config import get_logger
from core.plugin_manager import PluginManifest

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# System Prompts
# ---------------------------------------------------------------------------

STYLE_ADAPTER_SYSTEM = """你是一位专业网文编辑，精于将文本适配到不同平台风格。

## 适配原则
1. **不改变情节**: 只调整表达方式，不增删情节
2. **保持人物**: 人物对话风格和性格特征不变
3. **保留金句**: 原文中的高光句子保持不变或微调
4. **目标平台优先**: 一切以目标平台的阅读体验为准

## 各平台核心差异
- 番茄小说: 极简、快节奏、短段落、高密度钩子、大白话
- 起点中文网: 快节奏但保留细节、升级感明确、章尾强钩子
- 晋江文学城: 细腻、重视情感表达、对话有韵味
- 豆瓣阅读: 文学性强、有质感、克制"""

ADAPT_PROMPTS = {
    "rewrite": "请全面改写以下文本，适配{platform_name}风格。保持情节不变，调整表达方式：",
    "polish": "请润色以下文本，优化{platform_name}风格的表达，保持主要结构：",
    "minimal": "请对以下文本做最轻量的{platform_name}风格优化，仅调整明显不符合平台风格的地方：",
}

PLATFORM_NAMES = {
    "fanqie": "番茄小说",
    "qidian": "起点中文网",
    "jinjiang": "晋江文学城",
    "qimao": "七猫小说",
    "douban": "豆瓣阅读",
}

PLATFORM_REQUIREMENTS = {
    "fanqie": """
- 段落缩短为 3-5 行
- 长对话拆分成短句
- 删除冗长心理描写
- 确保章尾有钩子/悬念
- 语言更加直白口语化""",
    "qidian": """
- 保持合适的段落长度
- 强化升级感和成长节点
- 确保章尾钩子有力
- 金手指/系统交互清晰""",
    "jinjiang": """
- 适当增加内心戏
- 人物互动更加细腻
- 情感转折自然
- 语言可以有修饰感但不过度""",
}


class StyleAdapterPlugin:
    """风格适配器插件."""

    name = "style-adapter"
    version = "0.1.0"

    def __init__(self) -> None:
        self._kernel = None

    async def on_load(self, kernel) -> None:
        self._kernel = kernel
        logger.info("风格适配器已加载")

    async def on_unload(self) -> None:
        self._kernel = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def adapt_style(
        self,
        content: str,
        *,
        platform: str = "fanqie",
        mode: str = "rewrite",
        chapter_title: str = "",
    ) -> str:
        """适配章节到目标平台风格.

        Args:
            content: 原始章节文本.
            platform: 目标平台.
            mode: 适配模式 — rewrite(重写), polish(润色), minimal(最小).
            chapter_title: 章节标题 (用于上下文).

        Returns:
            适配后的文本.
        """
        platform_name = PLATFORM_NAMES.get(platform, platform)
        adapt_instruction = ADAPT_PROMPTS.get(mode, ADAPT_PROMPTS["rewrite"]).format(
            platform_name=platform_name
        )
        platform_req = PLATFORM_REQUIREMENTS.get(platform, "")

        user_prompt = f"""{adapt_instruction}

## 目标平台要求
{platform_req}

## 章节
{chapter_title + "\\n\\n" if chapter_title else ""}
{content}

请直接输出适配后的完整文本（不要加任何说明）。"""

        result = await self._kernel.call_llm(
            messages=[
                {"role": "system", "content": STYLE_ADAPTER_SYSTEM},
                {"role": "user", "content": user_prompt},
            ],
            tier="standard",
            max_tokens=8192,
            temperature=0.6,
        )

        return result["content"]

    async def mobile_optimize(self, content: str) -> str:
        """纯移动端优化 — 不改变风格，仅调整格式.

        - 段落拆分（>150字/5行 → 拆分）
        - 对话确保单独成行
        - 长句提示
        """
        user_prompt = f"""请对以下文本做纯移动端格式优化，不改变风格和内容：

1. 超过150字或5行的段落 → 合理拆分
2. 对话确保单独成行
3. 过长的句子适当断句

原文：
{content}

直接输出优化后的文本。"""

        result = await self._kernel.call_llm(
            messages=[
                {"role": "system", "content": "你是移动端文本格式优化专家。只做格式调整，不改变内容。"},
                {"role": "user", "content": user_prompt},
            ],
            tier="budget",
            max_tokens=8192,
            temperature=0.2,
        )
        return result["content"]


# ---------------------------------------------------------------------------
# 工厂函数
# ---------------------------------------------------------------------------


def create_manifest() -> PluginManifest:
    return PluginManifest(
        name="style-adapter",
        version="0.1.0",
        description="风格适配器 — 将文本适配到目标平台风格",
        dependencies=[],
        hooks=["on_load", "on_unload"],
    )


def create_plugin() -> StyleAdapterPlugin:
    return StyleAdapterPlugin()
