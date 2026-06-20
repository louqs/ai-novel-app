"""知识图谱查询 API — 高层次查询接口。

提供:
    - 人物关系网 (一度/二度邻居)
    - 实体邻域查询
    - 伏笔依赖链
    - 图谱可视化数据
    - 一致性检查
"""

from __future__ import annotations

from typing import Any

from core.graph_store import IGraphStore


class GraphQuery:
    """图查询引擎。"""

    def __init__(self, store: IGraphStore) -> None:
        self._store = store

    # =========================================================================
    # 人物查询
    # =========================================================================

    async def character_network(self, character_id: str, depth: int = 1) -> dict:
        """获取人物关系网络。

        Returns:
            {"nodes": [...], "edges": [...]} 适合前端可视化。
        """
        nodes_set: dict[str, dict] = {}
        edges_set: dict[str, dict] = {}

        # 初始节点
        start = await self._get_node(character_id)
        if start:
            nodes_set[character_id] = start

        # 获取邻居
        neighbors = await self._get_neighbors(character_id)
        for n in neighbors:
            target_id = n.get("target_id", n.get("target", ""))
            source_id = n.get("source_id", n.get("source", ""))
            neighbor_id = target_id if target_id != character_id else source_id

            if neighbor_id not in nodes_set:
                nn = await self._get_node(neighbor_id)
                if nn:
                    nodes_set[neighbor_id] = nn

            edge_key = n.get("id", f"{source_id}-{target_id}")
            if edge_key not in edges_set:
                edges_set[edge_key] = {
                    "id": edge_key,
                    "source": source_id,
                    "target": target_id,
                    "type": n.get("type", n.get("rel_type", "RELATED")),
                }

        return {
            "nodes": list(nodes_set.values()),
            "edges": list(edges_set.values()),
        }

    async def all_characters(self) -> list[dict]:
        """列出所有人物及其关系数。"""
        chars = await self._store.execute("SELECT * FROM nodes WHERE labels LIKE '%Character%'")
        results = []
        for c in chars:
            cid = c.get("id", "")
            import json
            props = c.get("properties", "{}")
            if isinstance(props, str):
                try:
                    props = json.loads(props)
                except json.JSONDecodeError:
                    props = {}
            neighbors = await self._get_neighbors(cid)
            results.append({
                "id": cid,
                "name": props.get("name", cid),
                "status": props.get("current_status", "active"),
                "relation_count": len(neighbors),
                "labels": c.get("labels", "Character"),
            })
        return results

    # =========================================================================
    # 实体查询
    # =========================================================================

    async def entity_neighborhood(self, entity_id: str) -> dict:
        """获取任意实体的邻域（人物/地点/势力/物品）。"""
        node = await self._get_node(entity_id)
        neighbors = await self._get_neighbors(entity_id)
        return {"node": node, "neighbors": neighbors}

    async def search_entities(self, keyword: str) -> list[dict]:
        """按名称搜索实体。"""
        results = await self._store.execute(
            "SELECT * FROM nodes WHERE properties LIKE ?",
            {"kw": f"%{keyword}%"},
        )
        return results

    # =========================================================================
    # 伏笔查询
    # =========================================================================

    async def foreshadow_chain(self, foreshadow_id: str) -> dict:
        """追踪伏笔链：触发和被触发关系。"""
        edges = await self._store.execute(
            "SELECT * FROM edges WHERE (source_id = ? OR target_id = ?) AND type = 'TRIGGERS'",
            {"id": foreshadow_id},
        )
        return {"foreshadow_id": foreshadow_id, "edges": edges}

    # =========================================================================
    # 一致性检查
    # =========================================================================

    async def check_contradictions(self, character_id: str) -> list[dict]:
        """检查人物状态矛盾（如同一角色同时在不同地点）。"""
        contradictions = []
        # 简单检测: SQLite
        neighbors = await self._get_neighbors(character_id)
        locations = [n for n in neighbors if n.get("type") == "LOCATED_AT"]
        if len(locations) > 1:
            contradictions.append({
                "entity": character_id,
                "type": "multiple_locations",
                "detail": f"人物同时出现在 {len(locations)} 个地点",
                "locations": locations,
            })
        return contradictions

    # =========================================================================
    # 全图导出 (可视化用)
    # =========================================================================

    async def export_full_graph(self) -> dict:
        """导出全图数据（节点 + 边），供前端可视化。"""
        nodes = await self._store.execute("SELECT * FROM nodes")
        edges = await self._store.execute("SELECT * FROM edges")

        import json
        vis_nodes = []
        for n in nodes:
            props = n.get("properties", "{}")
            if isinstance(props, str):
                try:
                    props = json.loads(props)
                except json.JSONDecodeError:
                    props = {}
            label = props.get("name", n.get("id", "?"))
            vis_nodes.append({
                "id": n["id"],
                "label": str(label)[:20],
                "group": n.get("labels", "Entity"),
                "properties": props,
            })

        vis_edges = []
        for e in edges:
            vis_edges.append({
                "id": e.get("id", ""),
                "source": e.get("source_id", e.get("source", "")),
                "target": e.get("target_id", e.get("target", "")),
                "type": e.get("type", e.get("rel_type", "RELATED")),
            })

        return {"nodes": vis_nodes, "edges": vis_edges}

    # =========================================================================
    # Internal
    # =========================================================================

    async def _get_node(self, node_id: str) -> dict | None:
        rows = await self._store.execute("SELECT * FROM nodes WHERE id = ?", {"id": node_id})
        return rows[0] if rows else None

    async def _get_neighbors(self, node_id: str) -> list[dict]:
        return await self._store.execute(
            "SELECT * FROM edges WHERE source_id = ? OR target_id = ?",
            {"id": node_id},
        )
