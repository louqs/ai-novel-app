"""知识图谱管理插件 — 自动维护小说知识图谱。

功能:
    1. 章节完成后自动提取实体/关系写入图 (on_chapter_after)
    2. 从设定/章节构建图谱 (build_from_settings，含 LLM 自动提取)

查询和清空操作由 routes 直接调用 core/graph_query.py + core/graph_store.py。
"""

from __future__ import annotations

import json
from typing import Any

from core.graph_builder import GraphBuilder
from core.graph_query import GraphQuery
from core.graph_store import IGraphStore, create_graph_store
from core.logging_config import get_logger
from core.plugin_manager import PluginManifest

logger = get_logger(__name__)


class GraphManagerPlugin:
    """知识图谱管理插件."""

    name = "graph-manager"
    version = "0.1.0"

    def __init__(self) -> None:
        self._kernel = None
        self._store: IGraphStore | None = None
        self._builder: GraphBuilder | None = None
        self._query: GraphQuery | None = None

    async def on_load(self, kernel) -> None:
        self._kernel = kernel
        config = kernel.get_config("", {})

        try:
            self._store = await create_graph_store(config)
            self._builder = GraphBuilder(self._store, kernel)
            self._query = GraphQuery(self._store)
            logger.info("知识图谱管理器已加载")
        except Exception as e:
            logger.warning("图谱存储初始化失败", error=str(e))

    async def on_unload(self) -> None:
        if self._store:
            await self._store.close()

    # ------------------------------------------------------------------
    # 章节钩子
    # ------------------------------------------------------------------

    async def on_chapter_after(self, chapter: dict) -> dict:
        """章节完成后自动更新图谱。"""
        if not self._builder or not self._store:
            return chapter

        project_id = chapter.get("project_id", "")
        ch_num = chapter.get("chapter_number", 0)
        content = chapter.get("content", "")

        # 获取已知人物名
        try:
            chars = await self._kernel.context().get(f"project:{project_id}", "characters", {})
            char_names = [
                c.get("name", "")
                for c in chars.get("characters", {}).values()
                if isinstance(c, dict)
            ]
        except Exception:
            char_names = []

        try:
            await self._builder.build_from_chapter(project_id, ch_num, content, character_names=char_names)
        except Exception as e:
            logger.warning("章节图谱更新失败", error=str(e))

        return chapter

    async def on_memory_update(self, chapter: dict) -> None:
        """记忆更新时同步图。"""
        await self.on_chapter_after(chapter)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def build_from_settings(self, project_id: str) -> dict:
        """从项目设定构建图谱。如果没有人物数据，从章节中自动提取。"""
        if not self._builder:
            return {"error": "图谱未初始化"}

        characters = {}
        settings = {}
        if self._kernel.db:
            characters = await self._kernel.db.get_characters(project_id)
            settings = await self._kernel.db.get_settings(project_id)

        if not characters.get("characters"):
            characters = await self._kernel.context().get(f"project:{project_id}", "characters", {})

        # 如果还是没有人物数据，从已有章节中自动提取
        if not characters.get("characters"):
            chars, rels = await self._extract_characters_from_chapters(project_id)
            if chars:
                characters = {"characters": chars, "relationships": rels}
                # 保存到数据库
                if self._kernel.db:
                    await self._kernel.db.save_characters(project_id, characters)
                    await self._kernel.context().set(f"project:{project_id}", "characters", characters)

        if not characters.get("characters"):
            return {"nodes_added": 0, "edges_added": 0, "message": "无人物数据，请先生成章节或世界观"}

        result = await self._builder.build_from_settings(project_id, settings, characters)

        # 持久化图谱到项目目录，确保切换项目后能正确加载
        try:
            if self._query:
                graph_data = await self._query.export_full_graph()
                graph_data["project_id"] = project_id
                await self._kernel.write_project_file(
                    project_id, "graph.json",
                    json.dumps(graph_data, ensure_ascii=False, indent=2),
                )
        except Exception as exc:
            logger.warning("图谱持久化失败", error=str(exc))

        return result

    async def _extract_characters_from_chapters(self, project_id: str) -> tuple[dict, list]:
        """从已有章节中通过 LLM 自动提取人物和关系。"""
        all_content = ""
        progress = await self._kernel.context().get(f"project:{project_id}", "progress", {})
        chapter_count = 0
        for vol in progress.get("volumes", []):
            vol_num = vol.get("volume_number", 1)
            for ch in vol.get("chapters", []):
                ch_num = ch.get("chapter_number", 0)
                if not ch_num:
                    continue
                try:
                    if self._kernel.db:
                        ch_data = await self._kernel.db.get_chapter(project_id, ch_num, vol_num)
                        if ch_data:
                            all_content += ch_data.get("content", "")[:6000] + "\n\n"
                    else:
                        chapter_id = f"ch_v{vol_num:02d}_{ch_num:04d}"
                        content = await self._kernel.read_project_file(project_id, f"chapters/{chapter_id}.md")
                        all_content += content[:6000] + "\n\n"
                    chapter_count += 1
                    if chapter_count > 10:
                        break
                except Exception:
                    pass
            if chapter_count > 10:
                break

        if not all_content.strip():
            return {}, []

        prompt = f"""从以下小说章节中提取所有人物和关系，以JSON返回。

章节内容：
{all_content[:6000]}

返回格式：
```json
{{
  "characters": {{
    "char_001": {{"name": "主角名", "personality_tags": ["标签"], "core_motivation": "动机", "current_status": "active"}},
    "char_002": {{"name": "配角名", "personality_tags": ["标签"], "core_motivation": "动机", "current_status": "active"}}
  }},
  "relationships": [
    {{"rel_id": "rel_001", "source_id": "char_001", "target_id": "char_002", "rel_type": "ally", "description": "关系描述"}}
  ]
}}
```

只提取明确出现的人物，不要编造。人物ID用char_001/002格式。关系类型用: ally/enemy/master_disciple/subordinate/emotional/family/rival"""

        try:
            result = await self._kernel.call_llm(
                messages=[{"role": "user", "content": prompt}],
                tier="budget",
                max_tokens=2048,
                temperature=0.2,
            )
            import re
            content = result.get("content", "{}")
            m = re.search(r'```(?:json)?\s*([\s\S]*?)```', content)
            if m:
                content = m.group(1)
            data = json.loads(content)
            return data.get("characters", {}), data.get("relationships", [])
        except Exception:
            return {}, []


def create_manifest() -> PluginManifest:
    return PluginManifest(
        name="graph-manager",
        version="0.1.0",
        description="知识图谱管理器 — 自动维护人物/地点/势力/伏笔关系图",
        dependencies=[],
        hooks=["on_load", "on_unload", "on_chapter_after", "on_memory_update"],
    )


def create_plugin() -> GraphManagerPlugin:
    return GraphManagerPlugin()
