"""大纲规划器 — 生成分卷/分章节大纲.

输入: 设定 + 人物 + 故事方向 + 目标平台
输出: Progress (VolumeOutline + ChapterNode)

用法:
    progress = await plugin.plan_outline(
        settings=settings,
        characters=characters,
        direction=direction_data,
        platform="fanqie",
        total_chapters=100,
    )
"""

from __future__ import annotations

import json
from typing import Any

from core.logging_config import get_logger
from core.plugin_manager import PluginManifest
from models.progress import ChapterNode, Progress, VolumeOutline

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Platform pacing rules
# ---------------------------------------------------------------------------

PLATFORM_PACING = {
    "fanqie": {
        "chapters_per_volume": "20-30章/卷",
        "climax_density": "每3-5章一个小高潮",
        "hook_rule": "章中撒钩子+章尾强留",
        "opening": "前300字直接冲突，不做铺垫",
    },
    "qidian": {
        "chapters_per_volume": "30-50章/卷",
        "climax_density": "每10-15章一个高潮",
        "hook_rule": "章尾强钩子",
        "opening": "快速建立主角目标与金手指",
    },
    "jinjiang": {
        "chapters_per_volume": "15-25章/卷",
        "climax_density": "情感节点每5-8章一个",
        "hook_rule": "感情线钩子+关系转折",
        "opening": "强人设建立，细腻互动",
    },
}

OUTLINE_PLANNER_SYSTEM = """你是一位资深网文大纲策划，精通各平台的节奏控制和商业看点布局。

## 大纲设计原则
1. **结构清晰**: 每卷有明确的起承转合
2. **节奏紧凑**: 根据平台特性控制高潮密度
3. **名场面布局**: 每卷至少1-2个⭐名场面（读者会记住的高光时刻）
4. **伏笔规划**: 关键伏笔提前标注埋设章节和回收章节
5. **人物节奏**: 人物关系转折点均匀分布

## 番茄小说特殊要求
- 每3-5章必须有一个小高潮或钩子
- 开篇（前3章）必须极强冲突
- 每章结尾留悬念
- 30章为一个关键节点（10万字完读率考核点）"""


class OutlinePlannerPlugin:
    """大纲规划器插件."""

    name = "outline-planner"
    version = "0.1.0"

    def __init__(self) -> None:
        self._kernel = None

    async def on_load(self, kernel) -> None:
        self._kernel = kernel
        logger.info("大纲规划器已加载")

    async def on_unload(self) -> None:
        self._kernel = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def plan_outline(
        self,
        settings: dict[str, Any],
        characters: dict[str, Any],
        direction: dict[str, Any],
        *,
        platform: str = "fanqie",
        total_chapters: int = 100,
        volumes: int = 4,
    ) -> Progress:
        """生成分卷大纲.

        Args:
            settings: 世界观设定 (dict 形式).
            characters: 人物设定 (dict 形式).
            direction: 选定的故事方向.
            platform: 目标平台.
            total_chapters: 总章节数.
            volumes: 分卷数量.

        Returns:
            Progress 模型.
        """
        pacing = PLATFORM_PACING.get(platform, PLATFORM_PACING["fanqie"])

        # 生成卷级大纲
        volume_outlines = await self._generate_volumes(
            settings, characters, direction, platform, total_chapters, volumes, pacing
        )

        # 生成每卷的章节节点
        all_volumes = []
        for vol in volume_outlines:
            chapters = await self._generate_chapters(
                settings, characters, direction, platform, vol, pacing
            )
            vol["chapters"] = chapters
            all_volumes.append(VolumeOutline(**vol))

        progress = Progress(
            project_id="",
            volumes=all_volumes,
            quota_min_words_per_chapter=2000,
            quota_max_words_per_chapter=4000,
        )
        return progress

    async def _generate_volumes(
        self,
        settings: dict,
        characters: dict,
        direction: dict,
        platform: str,
        total_chapters: int,
        num_volumes: int,
        pacing: dict,
    ) -> list[dict[str, Any]]:
        """生成卷级大纲."""
        chars_summary = self._summarize_characters(characters)

        title = direction.get('title', '')
        logline = direction.get('logline', '')
        core_conflict = direction.get('core_conflict', '')
        world_name = settings.get('world_name', '')

        # 构建故事描述，确保有足够信息
        story_parts = []
        if title:
            story_parts.append(f"**书名**: {title}")
        if logline:
            story_parts.append(f"**故事简介**: {logline}")
        if core_conflict:
            story_parts.append(f"**核心冲突**: {core_conflict}")
        if world_name:
            story_parts.append(f"**世界观**: {world_name}")
        story_info = "\n".join(story_parts) if story_parts else "**故事**: （未提供，请自行构思一个有吸引力的故事）"

        prompt = f"""请为以下小说规划分卷大纲:

{story_info}
**人物**: {chars_summary if chars_summary else "（未提供）"}
**平台**: {platform}
**总章节数**: {total_chapters}
**分卷数**: {num_volumes}
**平台节奏**: {pacing}

请规划 {num_volumes} 卷。以 JSON 返回:
```json
{{
  "volumes": [
    {{
      "volume_number": 1,
      "title": "卷名",
      "arc_description": "本卷起承转合（3-5句话）",
      "chapter_range": "第X章-第Y章",
      "chapters_count": 25
    }}
  ]
}}
```

要求:
- 每卷有明确的戏剧任务
- 卷与卷之间有递进关系
- 第1卷是生死关（前10万字的完读率考核点），必须紧凑"""

        result = await self._kernel.call_llm(
            messages=[
                {"role": "system", "content": OUTLINE_PLANNER_SYSTEM},
                {"role": "user", "content": prompt},
            ],
            tier="standard",
            max_tokens=3000,
            temperature=0.7,
        )

        data = self._parse_json(result["content"])
        return data.get("volumes", [])

    async def _generate_chapters(
        self,
        settings: dict,
        characters: dict,
        direction: dict,
        platform: str,
        volume: dict,
        pacing: dict,
    ) -> list[dict[str, Any]]:
        """为单卷生成章节节点."""
        chapters_count = volume.get("chapters_count", 25)
        vol_num = volume.get("volume_number", 1)

        # 故事上下文
        logline = direction.get('logline', '')
        title = direction.get('title', '')
        chars_summary = self._summarize_characters(characters)
        story_ctx = ""
        if title:
            story_ctx += f"书名: {title}\n"
        if logline:
            story_ctx += f"故事简介: {logline}\n"
        if chars_summary:
            story_ctx += f"主要人物: {chars_summary}\n"

        prompt = f"""请为第{vol_num}卷规划 {chapters_count} 个章节节点。

{story_ctx}卷名: {volume.get('title', '')}
卷弧光: {volume.get('arc_description', '')}

以 JSON 返回（只返回 JSON）:
```json
{{
  "chapters": [
    {{
      "chapter_number": 1,
      "volume_number": {vol_num},
      "title": "章节名",
      "summary": "一句话梗概",
      "key_events": ["事件1", "事件2"],
      "character_moments": ["人物节点1"],
      "is_climax": false,
      "is_hook_point": false,
      "foreshadow_plants": [],
      "foreshadow_payoffs": [],
      "status": "planned"
    }}
  ]
}}
```

要求:
- is_climax=true 的章节每卷2-3个
- is_hook_point=true (⭐名场面) 每卷1-2个
- 关键伏笔的埋设和回收跨章节标注"""

        result = await self._kernel.call_llm(
            messages=[
                {"role": "system", "content": OUTLINE_PLANNER_SYSTEM},
                {"role": "user", "content": prompt},
            ],
            tier="standard",
            max_tokens=6000,
            temperature=0.7,
        )

        data = self._parse_json(result["content"])
        return data.get("chapters", [])

    @staticmethod
    def _summarize_characters(characters: dict) -> str:
        """将人物数据压缩为文本摘要."""
        chars = characters.get("characters", {})
        lines = []
        for cid, c in chars.items():
            if isinstance(c, dict):
                name = c.get("name", cid)
                tags = c.get("personality_tags", [])
                motivation = c.get("core_motivation", "")
                lines.append(f"- {name}: {'/'.join(tags)} | 动机: {motivation}")
        return "\n".join(lines)

    @staticmethod
    def _parse_json(content: str) -> dict[str, Any]:
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            pass
        import re
        match = re.search(r'```(?:json)?\s*([\s\S]*?)```', content)
        if match:
            try:
                return json.loads(match.group(1))
            except json.JSONDecodeError:
                pass
        return {}


# ---------------------------------------------------------------------------
# 工厂函数
# ---------------------------------------------------------------------------


def create_manifest() -> PluginManifest:
    return PluginManifest(
        name="outline-planner",
        version="0.1.0",
        description="大纲规划器 — 生成分卷/分章节大纲",
        dependencies=["world-builder"],
        hooks=["on_load", "on_unload"],
    )


def create_plugin() -> OutlinePlannerPlugin:
    return OutlinePlannerPlugin()
