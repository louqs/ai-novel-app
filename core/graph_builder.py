"""知识图谱构建器 — 从章节提取实体/关系并写图。

用法:
    builder = GraphBuilder(store, kernel)
    await builder.build_from_chapter(project_id, chapter_num, content)
"""

from __future__ import annotations

import json
import re
import uuid
from typing import Any

from core.graph_store import IGraphStore, SQLiteGraphStore
from core.logging_config import get_logger

logger = get_logger(__name__)


class GraphBuilder:
    """从文本中提取实体和关系，构建知识图谱。"""

    def __init__(self, store: IGraphStore, kernel: Any = None) -> None:
        self._store = store
        self._kernel = kernel

    # =========================================================================
    # 从章节构建
    # =========================================================================

    async def build_from_chapter(
        self,
        project_id: str,
        chapter_num: int,
        content: str,
        *,
        character_names: list[str] | None = None,
    ) -> dict[str, int]:
        """从章节内容提取实体和关系，写入图谱。

        Returns:
            {"nodes_added": int, "edges_added": int}
        """
        nodes_added = 0
        edges_added = 0

        # 1. 用 LLM 提取实体和关系
        extracted = await self._extract_entities(content, chapter_num, character_names)

        # 2. 写入图谱
        for node_data in extracted.get("nodes", []):
            await self._upsert_node(node_data)
            nodes_added += 1

        for edge_data in extracted.get("edges", []):
            await self._upsert_edge(edge_data)
            edges_added += 1

        # 3. 保存章节节点
        chapter_id = f"ch_{project_id}_{chapter_num:04d}"
        await self._upsert_node({
            "id": chapter_id,
            "labels": "Chapter",
            "properties": {"chapter_number": chapter_num, "project_id": project_id},
        })

        logger.info("图谱已更新", project=project_id, chapter=chapter_num, nodes=nodes_added, edges=edges_added)
        return {"nodes_added": nodes_added, "edges_added": edges_added}

    async def build_from_settings(
        self,
        project_id: str,
        settings: dict,
        characters: dict,
    ) -> dict[str, int]:
        """从世界观设定和人物直接构建图谱（无需 LLM）。"""
        nodes_added = 0
        edges_added = 0

        # 人物节点
        chars = characters.get("characters", {})
        for cid, cdata in chars.items():
            if isinstance(cdata, dict):
                await self._upsert_node({
                    "id": cid,
                    "labels": "Character",
                    "properties": {k: v for k, v in cdata.items()
                                  if k not in ("appearance", "background", "arc_description")
                                  and not isinstance(v, (list, dict))},
                })
                nodes_added += 1

                # 势力归属
                faction_id = cdata.get("faction_id")
                if faction_id:
                    edge_id = f"e_belongs_{cid}_{faction_id}"
                    await self._upsert_edge({"id": edge_id, "source": cid, "target": faction_id, "type": "BELONGS_TO"})
                    edges_added += 1

        # 关系
        relationships = characters.get("relationships", [])
        for rel in relationships:
            if isinstance(rel, dict):
                edge_id = rel.get("rel_id", f"e_{uuid.uuid4().hex[:8]}")
                rel_type = rel.get("rel_type", "ALLY")
                if isinstance(rel_type, str):
                    rel_type = rel_type.upper()
                await self._upsert_edge({
                    "id": edge_id,
                    "source": rel.get("source_id", ""),
                    "target": rel.get("target_id", ""),
                    "type": rel_type,
                    "properties": {"description": rel.get("description", "")},
                })
                edges_added += 1

        # 势力节点
        factions = settings.get("factions", {})
        for fid, fdata in factions.items():
            if isinstance(fdata, dict):
                await self._upsert_node({
                    "id": fid,
                    "labels": "Faction",
                    "properties": fdata,
                })
                nodes_added += 1

        # 地点节点
        locations = settings.get("locations", {})
        for lid, ldata in locations.items():
            if isinstance(lidata, dict):
                await self._upsert_node({
                    "id": lid,
                    "labels": "Location",
                    "properties": lidata,
                })
                nodes_added += 1

        logger.info("设定已导入图谱", project=project_id, nodes=nodes_added, edges=edges_added)
        return {"nodes_added": nodes_added, "edges_added": edges_added}

    # =========================================================================
    # 查询
    # =========================================================================

    async def get_character_graph(self, character_id: str) -> dict:
        """获取人物关系子图（一度邻居）。"""
        if isinstance(self._store, SQLiteGraphStore):
            neighbors = await self._store.get_neighbors(character_id)
            node = await self._store.get_node(character_id)
            return {"node": node, "neighbors": neighbors}
        else:
            query = """
                MATCH (c:Character {character_id: $id})-[r]-(n)
                RETURN c, r, n, type(r) as rel_type
            """
            return await self._store.execute(query, {"id": character_id})

    async def get_all_characters(self) -> list[dict]:
        """获取所有人物节点。"""
        if isinstance(self._store, SQLiteGraphStore):
            return await self._store.execute("SELECT * FROM nodes WHERE labels LIKE '%Character%'")
        return await self._store.execute("MATCH (c:Character) RETURN c")

    async def get_foreshadow_chain(self, foreshadow_id: str) -> list[dict]:
        """获取伏笔的依赖链。"""
        if isinstance(self._store, SQLiteGraphStore):
            return await self._store.execute(
                "SELECT * FROM edges WHERE source_id = ? OR target_id = ? AND type = 'TRIGGERS'",
                {"src": foreshadow_id, "tgt": foreshadow_id},
            )
        return await self._store.execute(
            "MATCH (f:Foreshadow {foreshadow_id: $id})-[r:TRIGGERS*]-(related) RETURN f, r, related",
            {"id": foreshadow_id},
        )

    # =========================================================================
    # Internal
    # =========================================================================

    async def _extract_entities(self, content: str, chapter_num: int, known_names: list[str] | None) -> dict:
        """用 LLM 从文本提取实体和关系。"""
        if not self._kernel:
            return {"nodes": [], "edges": []}

        names_hint = ""
        if known_names:
            names_hint = f"\n已知人物: {', '.join(known_names[:10])}"

        prompt = f"""从以下小说章节中提取实体和关系，以 JSON 返回:

{content[:3000]}
{names_hint}

返回格式:
```json
{{
  "nodes": [
    {{"id": "char_001", "labels": "Character", "properties": {{"name": "...", "current_status": "..."}}}},
    {{"id": "loc_001", "labels": "Location", "properties": {{"name": "...", "type": "..."}}}}
  ],
  "edges": [
    {{"id": "e_001", "source": "char_001", "target": "char_002", "type": "ALLY/ENEMY/MASTER_OF/ROMANTIC_WITH/LOCATED_AT/POSSESSES/TRIGGERS", "properties": {{"chapter": {chapter_num}}}}}
  ]
}}
```

规则:
- 只提取本章新出现的实体和信息
- 关系类型用大写
- ID 用已知的 character_id（如 char_001），不知道就用新 ID"""

        try:
            result = await self._kernel.call_llm(
                messages=[{"role": "user", "content": prompt}],
                tier="budget",
                max_tokens=2048,
                temperature=0.2,
            )
            content_str = result.get("content", "{}")
            return self._parse_json(content_str)
        except Exception:
            return {"nodes": [], "edges": []}

    async def _upsert_node(self, data: dict) -> None:
        if isinstance(self._store, SQLiteGraphStore):
            node_id = data["id"]
            labels = data.get("labels", "Entity")
            props = data.get("properties", {})
            await self._store.upsert_node(node_id, labels, props)
        else:
            labels = data.get("labels", "Entity")
            props = data.get("properties", {})
            props_str = ", ".join(f'{k}: "${k}"' for k in props)
            await self._store.execute_write(
                f"MERGE (n:{labels} {{{props_str}}})",
                props,
            )

    async def _upsert_edge(self, data: dict) -> None:
        if isinstance(self._store, SQLiteGraphStore):
            await self._store.upsert_edge(
                data["id"], data["source"], data["target"],
                data.get("type", "RELATED"),
                data.get("properties"),
            )
        else:
            # Neo4j Cypher
            await self._store.execute_write(
                f"""
                MATCH (a {{id: $source}}), (b {{id: $target}})
                MERGE (a)-[r:{data.get('type', 'RELATED')}]->(b)
                SET r += $props
                """,
                {"source": data["source"], "target": data["target"], "props": data.get("properties", {})},
            )

    @staticmethod
    def _parse_json(content: str) -> dict:
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            m = re.search(r'```(?:json)?\s*([\s\S]*?)```', content)
            if m:
                try:
                    return json.loads(m.group(1))
                except json.JSONDecodeError:
                    pass
        return {}
