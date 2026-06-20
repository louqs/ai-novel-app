"""设定构建器 — 生成世界观、人物、关系网络.

输入: 选定的故事方向 (logline + 类型)
输出: 完整的 Settings 和 CharacterSet

用法:
    settings, characters = await plugin.build_world(
        direction=direction_data,
        platform="fanqie",
    )
"""

from __future__ import annotations

import json
from typing import Any

from core.logging_config import get_logger
from core.plugin_manager import PluginManifest
from models.character import (
    CharacterArc,
    CharacterProfile,
    CharacterSet,
    Relationship,
    RelationshipType,
)
from models.settings import Faction, Location, PowerLevel, PowerSystem, Settings, WorldRule

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# System Prompt
# ---------------------------------------------------------------------------

WORLD_BUILDER_SYSTEM = """你是一位资深世界观架构师，专精于网文世界观的构建。

## 构建原则
1. **设定服务于故事**: 每个设定都要能驱动冲突或塑造人物
2. **简洁有力**: 网文读者不喜欢大段设定堆砌。核心规则 3-5 条即可
3. **留有空间**: 为后续剧情发展预留设定扩展的余地
4. **一致性**: 所有设定必须自洽，不能互相矛盾

## 输出要求
- 世界观用结构化 JSON 输出
- 人物要有鲜明的性格标签和明确的动机
- 关系网络要有足够的戏剧张力"""

CHARACTER_BUILDER_SYSTEM = """你是一位人物塑造专家。

## 人物设计原则
1. **动机明确**: 每个主要人物都要有清晰的核心动机
2. **性格标签化**: 2-3个词就能概括性格，便于读者记忆
3. **弧光可追踪**: 人物在故事中应该有清晰的变化轨迹
4. **关系有张力**: 人物关系要能产生戏剧冲突

## CP 线设计 (如有)
- 情感发展有内在逻辑，不是一见钟情
- 互动细节丰富，有"磕点"
- 节奏张弛有度"""


class WorldBuilderPlugin:
    """设定构建器插件."""

    name = "world-builder"
    version = "0.1.0"

    def __init__(self) -> None:
        self._kernel = None

    async def on_load(self, kernel) -> None:
        self._kernel = kernel
        logger.info("设定构建器已加载")

    async def on_unload(self) -> None:
        self._kernel = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def build_world(
        self,
        direction: dict[str, Any],
        *,
        platform: str = "fanqie",
    ) -> dict[str, Any]:
        """生成完整的世界观设定.

        Args:
            direction: 从 IdeaIncubator 选择的 story direction.
            platform: 目标平台.

        Returns:
            {"settings": Settings, "characters": CharacterSet}
        """
        logline = direction.get("logline", "")
        core_conflict = direction.get("core_conflict", "")
        golden_finger = direction.get("golden_finger", "")
        genre_tags = direction.get("genre_tags", [])

        # Step 1: 生成世界观
        settings = await self._generate_settings(
            logline, core_conflict, golden_finger, genre_tags
        )

        # Step 2: 生成人物
        characters = await self._generate_characters(
            logline, core_conflict, golden_finger, genre_tags, settings
        )

        return {
            "settings": settings,
            "characters": characters,
        }

    async def _generate_settings(
        self,
        logline: str,
        core_conflict: str,
        golden_finger: str,
        genre_tags: list[str],
    ) -> Settings:
        """生成世界观设定."""
        prompt = f"""请为以下小说构建世界观设定:

故事梗概: {logline}
核心冲突: {core_conflict}
金手指/核心设定: {golden_finger}
类型: {', '.join(genre_tags)}

请以 JSON 格式返回（只返回 JSON）:
```json
{{
  "world_name": "世界名称",
  "timeline": ["关键历史事件1", "事件2", "事件3"],
  "geography": "地理概述（一段话）",
  "world_rules": [
    {{"rule_id": "rule_001", "name": "...", "category": "...", "description": "...", "exceptions": []}}
  ],
  "locations": {{
    "loc_001": {{"location_id": "loc_001", "name": "...", "type": "...", "description": "..."}}
  }},
  "factions": {{
    "fac_001": {{"faction_id": "fac_001", "name": "...", "type": "...", "description": "..."}}
  }},
  "power_systems": [
    {{"system_id": "psys_001", "name": "...", "levels": [{{"rank": 1, "name": "...", "description": "..."}}]}}
  ]
}}
```

要求:
- world_rules 3-5条核心规则, 不足则以生成
- locations 2-4个关键场景
- factions 2-3个势力
- power_systems 为修炼/能力体系(如有), 5-9个等级"""

        result = await self._kernel.call_llm(
            messages=[
                {"role": "system", "content": WORLD_BUILDER_SYSTEM},
                {"role": "user", "content": prompt},
            ],
            tier="standard",
            max_tokens=4096,
            temperature=0.7,
        )

        data = self._parse_json(result["content"])

        # 补充 ID 前缀
        settings = Settings(
            project_id="",  # 由调用方填入
            world_name=data.get("world_name", ""),
            timeline=data.get("timeline", []),
            geography=data.get("geography", ""),
            world_rules=[WorldRule(**r) for r in data.get("world_rules", [])],
            locations={k: Location(**v) for k, v in data.get("locations", {}).items()},
            factions={k: Faction(**v) for k, v in data.get("factions", {}).items()},
            power_systems=[PowerSystem(**ps) for ps in data.get("power_systems", [])],
        )
        return settings

    async def _generate_characters(
        self,
        logline: str,
        core_conflict: str,
        golden_finger: str,
        genre_tags: list[str],
        settings: Settings,
    ) -> CharacterSet:
        """生成人物设定."""
        prompt = f"""请为以下小说创建人物设定:

故事梗概: {logline}
核心冲突: {core_conflict}
金手指: {golden_finger}
类型: {', '.join(genre_tags)}
已有势力: {', '.join(settings.factions.keys())}

请创建 4-6 个核心人物。以 JSON 返回:
```json
{{
  "characters": {{
    "char_001": {{
      "character_id": "char_001",
      "name": "...",
      "aliases": [],
      "age": 20,
      "gender": "男/女",
      "appearance": "外貌描述",
      "personality_tags": ["标签1", "标签2", "标签3"],
      "background": "背景故事",
      "core_motivation": "核心动机",
      "arc_type": "positive/flat/negative",
      "arc_description": "人物弧光描述",
      "current_status": "active",
      "power_level": "当前等级",
      "faction_id": "所属势力ID或null",
      "first_appearance_chapter": 1
    }}
  }},
  "relationships": [
    {{
      "rel_id": "rel_001",
      "source_id": "char_001",
      "target_id": "char_002",
      "rel_type": "ally/enemy/master_disciple/subordinate/emotional/family/rival/acquaintance",
      "description": "关系描述"
    }}
  ]
}}
```

要求:
- 至少包含: 主角、CP对象(如有)、反派/对手、导师/伙伴
- 主角要有明确的成长空间和性格缺陷
- 反派要有合理的动机，不能脸谱化
- 人物之间的关系要有戏剧张力"""

        result = await self._kernel.call_llm(
            messages=[
                {"role": "system", "content": CHARACTER_BUILDER_SYSTEM},
                {"role": "user", "content": prompt},
            ],
            tier="premium",
            max_tokens=4096,
            temperature=0.8,
        )

        data = self._parse_json(result["content"])

        character_set = CharacterSet(
            project_id="",
            characters={k: CharacterProfile(**v) for k, v in data.get("characters", {}).items()},
            relationships=[Relationship(**r) for r in data.get("relationships", [])],
        )
        return character_set

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
        name="world-builder",
        version="0.1.0",
        description="设定构建器 — 生成世界观、人物、关系网络",
        dependencies=["idea-incubator"],
        hooks=["on_load", "on_unload"],
    )


def create_plugin() -> WorldBuilderPlugin:
    return WorldBuilderPlugin()
