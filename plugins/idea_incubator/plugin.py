"""灵感孵化器 — 将一句话灵感扩展为多个可写的故事方向.

输入: 一句话灵感 / 关键词 / 梗概
输出: 3-5 个扩展方向, 每个含 logline、类型标签、目标平台建议

用法:
    result = await plugin.incubate(
        seed="一个普通人意外继承了异世界的图书馆",
        platform="fanqie",
        count=3,
    )
"""

from __future__ import annotations

import json
from typing import Any

from core.logging_config import get_logger
from core.plugin_manager import PluginManifest

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# System Prompt
# ---------------------------------------------------------------------------

IDEA_INCUBATOR_SYSTEM = """你是一位资深网文编辑和故事策划，精通各大网文平台的爆款逻辑。

## 你的能力
- 从一句话灵感出发，发现其中最有商业潜力的核心冲突
- 熟悉各平台的热门赛道和读者偏好
- 能给出具体可写的方向，而非空泛建议

## 输出要求
对于每个方向，你必须给出：
1. **logline**（一句话梗概，30字以内）
2. **核心冲突**（主角面对的主要矛盾）
3. **金手指/核心设定**（主角的独特优势或世界观亮点）
4. **前3章的剧情走向**（每章一句话）
5. **目标平台建议**及其理由
6. **类型标签**（3-5个关键词）
7. **商业看点**（这个方向为什么能火）

## 赛道参考
- 番茄小说（男频）: 都市脑洞、战神赘婿、神豪、规则怪谈、反套路玄幻
- 番茄小说（女频）: 萌宝甜宠、霸总、年代文+系统、重生复仇
- 起点: 升级流、系统流、诸天无限、科幻末世
- 晋江: 古言权谋、仙侠甜宠、双向暗恋、反派洗白"""


class IdeaIncubatorPlugin:
    """灵感孵化器插件."""

    name = "idea-incubator"
    version = "0.1.0"

    def __init__(self) -> None:
        self._kernel = None

    async def on_load(self, kernel) -> None:
        self._kernel = kernel
        logger.info("灵感孵化器已加载")

    async def on_unload(self) -> None:
        self._kernel = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def incubate(
        self,
        seed: str,
        *,
        platform: str = "fanqie",
        count: int = 3,
        genre_preference: str = "",
    ) -> dict[str, Any]:
        """孵化灵感 — 生成多个故事方向.

        Args:
            seed: 原始灵感文本.
            platform: 目标平台 (fanqie/qidian/jinjiang/qimao/douban).
            count: 生成方向数量 (2-5).
            genre_preference: 题材偏好 (可选).

        Returns:
            {
                "seed": str,
                "directions": [
                    {
                        "logline": str,
                        "core_conflict": str,
                        "golden_finger": str,
                        "first_3_chapters": [str, str, str],
                        "platform_suggestion": str,
                        "platform_reason": str,
                        "genre_tags": [str, ...],
                        "commercial_appeal": str,
                    },
                    ...
                ]
            }
        """
        platform_names = {
            "fanqie": "番茄小说",
            "qidian": "起点中文网",
            "jinjiang": "晋江文学城",
            "qimao": "七猫小说",
            "douban": "豆瓣阅读",
        }
        platform_name = platform_names.get(platform, platform)

        user_prompt = f"""请根据以下灵感，生成 {count} 个不同的故事方向。

原始灵感:
"{seed}"

目标平台: {platform_name}"""
        if genre_preference:
            user_prompt += f"\n题材偏好: {genre_preference}"

        user_prompt += f"""

请以 JSON 格式返回（只返回 JSON，不要其他文字）:
```json
{{
  "directions": [
    {{
      "logline": "...",
      "core_conflict": "...",
      "golden_finger": "...",
      "first_3_chapters": ["第1章: ...", "第2章: ...", "第3章: ..."],
      "platform_suggestion": "fanqie/qidian/jinjiang...",
      "platform_reason": "...",
      "genre_tags": ["tag1", "tag2", "tag3"],
      "commercial_appeal": "..."
    }}
  ]
}}
```"""

        result = await self._kernel.call_llm(
            messages=[
                {"role": "system", "content": IDEA_INCUBATOR_SYSTEM},
                {"role": "user", "content": user_prompt},
            ],
            tier="standard",
            max_tokens=4096,
            temperature=0.8,
        )

        content = result["content"]
        # 提取 JSON
        data = self._parse_json(content)
        data["seed"] = seed
        return data

    @staticmethod
    def _parse_json(content: str) -> dict[str, Any]:
        """从 LLM 输出中提取 JSON."""
        # 尝试直接解析
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            pass

        # 尝试提取 ```json ... ``` 区块
        import re
        match = re.search(r'```(?:json)?\s*([\s\S]*?)```', content)
        if match:
            try:
                return json.loads(match.group(1))
            except json.JSONDecodeError:
                pass

        logger.warning("无法解析 LLM JSON 输出", content=content[:200])
        return {"directions": [], "parse_error": True, "raw": content}


# ---------------------------------------------------------------------------
# 工厂函数
# ---------------------------------------------------------------------------


def create_manifest() -> PluginManifest:
    return PluginManifest(
        name="idea-incubator",
        version="0.1.0",
        description="灵感孵化器 — 将一句话灵感扩展为多个故事方向",
        dependencies=[],
        hooks=["on_load", "on_unload"],
    )


def create_plugin() -> IdeaIncubatorPlugin:
    return IdeaIncubatorPlugin()
