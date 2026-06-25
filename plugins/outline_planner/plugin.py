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
1. **完整故事**: 必须在指定章节数内讲完一个完整故事，有开头、发展、高潮、结局
2. **结构清晰**: 每卷有明确的起承转合
3. **节奏紧凑**: 根据平台特性控制高潮密度
4. **名场面布局**: 每卷至少1-2个⭐名场面（读者会记住的高光时刻）
5. **伏笔规划**: 关键伏笔提前标注埋设章节和回收章节，所有伏笔必须在结局前回收
6. **人物节奏**: 人物关系转折点均匀分布
7. **结局完整**: 最后一卷必须包含故事结局，主要冲突彻底解决，不能留下未解悬念
8. **复杂度匹配**: 故事复杂度必须与章节数匹配——章节数少就写单线故事，章节数多才能写多线交织

## 番茄小说特殊要求
- 每3-5章必须有一个小高潮或钩子
- 开篇（前3章）必须极强冲突
- 每章结尾留悬念
- 30章为一个关键节点（10万字完读率考核点）

## ⚠️ 特别注意
- 不要把大纲写成"故事的开头"——必须是"完整故事"
- **所有内容必须用中文**，禁止使用英文单词（如 closure、flashback 等），必须用中文表达
- 最后一章必须是结局，不能是"新的开始"或"留下悬念"
- 如果章节数有限，简化故事线，不要设置太多角色和支线"""


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

    @staticmethod
    def _build_genre_brief(genre_tags: list[str] | None) -> str:
        """按体裁组装大纲阶段的规则基线（靶值 + 红线）；无则返回空串，回退原行为."""
        try:
            from core import knowledge_resolver as kr

            parts: list[str] = []
            targets = kr.genre_targets(genre_tags)
            if targets:
                # 大纲阶段关注结构性靶值（章首窗口/爽点间隔/字数窗口等）
                tv_lines = "\n".join(f"- {k}：{v}" for k, v in targets.items())
                parts.append(f"**本体裁量化靶值（规划时按此把控节奏与篇幅）**:\n{tv_lines}")
            for block in kr.genre_boundaries(genre_tags):
                parts.append(block[:1200])
            if not parts:
                return ""
            return "\n\n## 体裁规则基线（大纲须遵守）\n" + "\n\n".join(parts)
        except Exception:
            return ""

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
        target_words_per_chapter: int = 3000,
        genre_tags: list[str] | None = None,
    ) -> Progress:
        """生成分卷大纲.

        Args:
            settings: 世界观设定 (dict 形式).
            characters: 人物设定 (dict 形式).
            direction: 选定的故事方向.
            platform: 目标平台.
            total_chapters: 总章节数.
            volumes: 分卷数量.
            target_words_per_chapter: 每章目标字数.
            genre_tags: 项目体裁标签，用于注入体裁靶值与红线（None 则回退通用默认）.

        Returns:
            Progress 模型.
        """
        pacing = PLATFORM_PACING.get(platform, PLATFORM_PACING["fanqie"])
        # 按体裁组装大纲阶段规则基线（约定式自动发现；无体裁/无文件则为空串，回退原行为）
        genre_brief = self._build_genre_brief(genre_tags)

        # 根据目标字数计算每章字数范围（±30%，最低1500）
        quota_min = max(1500, int(target_words_per_chapter * 0.7))
        quota_max = int(target_words_per_chapter * 1.3)

        if volumes <= 1:
            # 不分卷模式：直接生成所有章节，放在一个默认卷下
            chapters = await self._generate_flat_chapters(
                settings, characters, direction, platform, total_chapters, pacing,
                target_words_per_chapter=target_words_per_chapter,
                genre_brief=genre_brief,
            )
            all_volumes = [VolumeOutline(
                volume_number=1,
                title="",
                arc_description="",
                chapters=chapters,
            )]
        else:
            # 分卷模式：先生成卷级大纲，再并行生成每卷章节
            volume_outlines = await self._generate_volumes(
                settings, characters, direction, platform, total_chapters, volumes, pacing,
                target_words_per_chapter=target_words_per_chapter,
                genre_brief=genre_brief,
            )

            import asyncio
            num_volumes = len(volume_outlines)
            async def _gen_chapters_for_vol(vol):
                is_last_vol = vol.get("volume_number", 1) == num_volumes
                chapters = await self._generate_chapters(
                    settings, characters, direction, platform, vol, pacing,
                    target_words_per_chapter=target_words_per_chapter,
                    is_last_volume=is_last_vol,
                    genre_brief=genre_brief,
                )
                vol["chapters"] = chapters
                return vol

            vol_tasks = [asyncio.create_task(_gen_chapters_for_vol(vol)) for vol in volume_outlines]
            all_volumes = [VolumeOutline(**(await t)) for t in vol_tasks]

        progress = Progress(
            project_id="",
            volumes=all_volumes,
            quota_min_words_per_chapter=quota_min,
            quota_max_words_per_chapter=quota_max,
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
        *,
        target_words_per_chapter: int = 3000,
        genre_brief: str = "",
    ) -> list[dict[str, Any]]:
        """生成卷级大纲."""
        chars_summary = self._summarize_characters(characters)

        title = direction.get('title', '')
        logline = direction.get('logline', '')
        core_conflict = direction.get('core_conflict', '')
        world_name = settings.get('world_name', '')

        # 构建故事描述，确保有足够信息（不传书名，避免 LLM 把书名加到卷名/章节名中）
        story_parts = []
        if logline:
            story_parts.append(f"**故事简介**: {logline}")
        if core_conflict:
            story_parts.append(f"**核心冲突**: {core_conflict}")
        if world_name:
            story_parts.append(f"**世界观**: {world_name}")
        story_info = "\n".join(story_parts) if story_parts else "**故事**: （未提供，请自行构思一个有吸引力的故事）"

        # 计算每章字数范围（±30%）
        words_min = max(1500, int(target_words_per_chapter * 0.7))
        words_max = int(target_words_per_chapter * 1.3)

        prompt = f"""请为以下小说规划分卷大纲:

{story_info}
**人物**: {chars_summary if chars_summary else "（未提供）"}
**平台**: {platform}
**总章节数**: {total_chapters}
**分卷数**: {num_volumes}
**每章目标字数**: {target_words_per_chapter}字（范围: {words_min}-{words_max}字/章）
**平台节奏**: {pacing}
{genre_brief}

⚠️ **核心约束**: 这是一部{total_chapters}章的完整小说，必须在{total_chapters}章内讲完一个完整故事。请根据章节数合理控制故事复杂度——章节数少就写单线故事，章节数多才能写多线交织。

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
- 第1卷是生死关（前10万字的完读率考核点），必须紧凑
- **最后一卷必须包含故事结局**: 主要冲突彻底解决，重要伏笔全部回收，给读者完整结局体验
- **故事复杂度必须匹配章节数**: {total_chapters}章只能支撑相应复杂度的故事，不要设置太多悬念线
- 每章内容规划需符合 {target_words_per_chapter} 字的目标字数（{words_min}-{words_max}字范围）"""

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
        *,
        target_words_per_chapter: int = 3000,
        is_last_volume: bool = False,
        genre_brief: str = "",
    ) -> list[dict[str, Any]]:
        """为单卷生成章节节点."""
        chapters_count = volume.get("chapters_count", 25)
        vol_num = volume.get("volume_number", 1)

        # 故事上下文（不传书名，避免 LLM 把书名加到章节标题中）
        logline = direction.get('logline', '')
        chars_summary = self._summarize_characters(characters)
        story_ctx = ""
        if logline:
            story_ctx += f"故事简介: {logline}\n"
        if chars_summary:
            story_ctx += f"主要人物: {chars_summary}\n"

        # 计算每章字数范围（±30%）
        words_min = max(1500, int(target_words_per_chapter * 0.7))
        words_max = int(target_words_per_chapter * 1.3)

        # 如果是最后一卷，明确告知需要结局
        ending_hint = ""
        if is_last_volume:
            ending_hint = "\n**重要**: 这是最后一卷，最后几章必须是故事结局：1)主要冲突彻底解决 2)重要伏笔全部回收 3)给读者完整的结局体验 4)最后一章必须是结局，不能是'新的开始'或'留下悬念'。"

        prompt = f"""请为第{vol_num}卷规划 {chapters_count} 个章节节点。

{story_ctx}卷名: {volume.get('title', '')}
卷弧光: {volume.get('arc_description', '')}
每章目标字数: {target_words_per_chapter}字（范围: {words_min}-{words_max}字/章）
{ending_hint}{genre_brief}

以 JSON 返回（只返回 JSON）:
```json
{{
  "chapters": [
    {{
      "chapter_number": 1,
      "volume_number": {vol_num},
      "title": "章节名",
      "summary": "一句话梗概（简洁描述本章核心事件，不要包含字数信息）",
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
- **章节标题(title)不要包含书名**，只写章节本身的标题，如"血溅退婚夜"、"棋局重开"，不要写"《书名》第一章 xxx"
- is_climax=true 的章节每卷2-3个
- is_hook_point=true (⭐名场面) 每卷1-2个
- 关键伏笔的埋设和回收跨章节标注
{"- **最后一卷必须有完整结局**: 最后2-3章是故事结局，主要冲突解决，重要伏笔回收，给读者完整体验" if is_last_volume else "- 如果是最后一卷，最后几章必须是故事结局，主要冲突解决，重要伏笔回收"}
- **summary 只写本章具体发生了什么**，不要写"主要冲突解决"、"伏笔回收"、"给读者体验"这类元描述，要写具体的情节内容"""

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

    async def _generate_flat_chapters(
        self,
        settings: dict,
        characters: dict,
        direction: dict,
        platform: str,
        total_chapters: int,
        pacing: dict,
        *,
        target_words_per_chapter: int = 3000,
        genre_brief: str = "",
    ) -> list[dict[str, Any]]:
        """不分卷模式：直接生成所有章节节点."""
        # 故事上下文（不传书名，避免 LLM 把书名加到章节标题中）
        logline = direction.get('logline', '')
        chars_summary = self._summarize_characters(characters)
        story_ctx = ""
        if logline:
            story_ctx += f"故事简介: {logline}\n"
        if chars_summary:
            story_ctx += f"主要人物: {chars_summary}\n"

        # 计算每章字数范围（±30%）
        words_min = max(1500, int(target_words_per_chapter * 0.7))
        words_max = int(target_words_per_chapter * 1.3)

        prompt = f"""请为以下小说规划 {total_chapters} 个章节节点（不分卷，直接平铺）。

{story_ctx}
每章目标字数: {target_words_per_chapter}字（范围: {words_min}-{words_max}字/章）
平台节奏: {pacing}
{genre_brief}

⚠️ **核心约束**: 这是一部{total_chapters}章的完整小说，必须在{total_chapters}章内讲完一个完整故事。请根据章节数合理控制故事复杂度——章节数少就写单线故事，章节数多才能写多线交织。不要设置太多悬念线，所有伏笔必须在结局前回收。

以 JSON 返回（只返回 JSON）:
```json
{{
  "chapters": [
    {{
      "chapter_number": 1,
      "volume_number": 1,
      "title": "章节名",
      "summary": "一句话梗概（简洁描述本章核心事件，不要包含字数信息）",
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
- **章节标题(title)不要包含书名**，只写章节本身的标题，如"血溅退婚夜"、"棋局重开"，不要写"《书名》第一章 xxx"
- 所有章节 volume_number 都为 1（不分卷）
- is_climax=true 的章节每5-8章一个
- is_hook_point=true (⭐名场面) 每8-12章一个
- 关键伏笔的埋设和回收跨章节标注
- **最后几章必须是故事结局**: 主要冲突彻底解决，所有重要伏笔回收，不能留下未解悬念
- **故事复杂度必须匹配{total_chapters}章**: 不要设置太多角色和支线，确保能在限定章节数内讲完
- **summary 只写本章具体发生了什么**，不要写"主要冲突解决"、"伏笔回收"、"给读者体验"这类元描述，要写具体的情节内容"""

        result = await self._kernel.call_llm(
            messages=[
                {"role": "system", "content": OUTLINE_PLANNER_SYSTEM},
                {"role": "user", "content": prompt},
            ],
            tier="standard",
            max_tokens=8000,
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
        # 提取 ```json ... ``` 代码块（贪婪匹配）
        match = re.search(r'```(?:json)?\s*([\s\S]+?)\s*```', content)
        if match:
            try:
                return json.loads(match.group(1))
            except json.JSONDecodeError:
                pass
        # 尝试找第一个JSON对象或数组
        for start_char, end_char in [('{', '}'), ('[', ']')]:
            start = content.find(start_char)
            if start < 0:
                continue
            depth = 0
            for i in range(start, len(content)):
                if content[i] == start_char:
                    depth += 1
                elif content[i] == end_char:
                    depth -= 1
                    if depth == 0:
                        try:
                            return json.loads(content[start:i + 1])
                        except json.JSONDecodeError:
                            break
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
