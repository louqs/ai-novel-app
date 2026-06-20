"""知识图谱存储 — Neo4j + SQLite 双后端。

Neo4j: 生产环境，原生图查询
SQLite: 开发/降级，无需安装外部服务

统一接口，后端可切换。
"""

from __future__ import annotations

import sqlite3
import threading
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from core.logging_config import get_logger

logger = get_logger(__name__)


# =============================================================================
# 接口
# =============================================================================


class IGraphStore(ABC):
    """图存储抽象接口."""

    @abstractmethod
    async def connect(self) -> None: ...

    @abstractmethod
    async def close(self) -> None: ...

    @abstractmethod
    async def execute(self, query: str, params: dict | None = None) -> list[dict[str, Any]]: ...

    @abstractmethod
    async def execute_write(self, query: str, params: dict | None = None) -> None: ...

    @abstractmethod
    async def clear(self) -> None: ...


# =============================================================================
# SQLite 图存储 (降级方案)
# =============================================================================


class SQLiteGraphStore(IGraphStore):
    """基于 SQLite 的图存储。

    用两张表模拟图:
        nodes(id, labels, properties_json)
        edges(id, source_id, target_id, type, properties_json)
    """

    def __init__(self, db_path: str | Path = "data/graph.db") -> None:
        self._db_path = Path(db_path)
        self._conn: sqlite3.Connection | None = None
        self._lock = threading.Lock()

    async def connect(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS nodes (
                id TEXT PRIMARY KEY,
                labels TEXT NOT NULL,
                properties TEXT DEFAULT '{}'
            )
        """)
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS edges (
                id TEXT PRIMARY KEY,
                source_id TEXT NOT NULL,
                target_id TEXT NOT NULL,
                type TEXT NOT NULL,
                properties TEXT DEFAULT '{}'
            )
        """)
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_edges_source ON edges(source_id)")
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_edges_target ON edges(target_id)")
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_edges_type ON edges(type)")
        self._conn.commit()
        logger.info("SQLite 图存储已连接", path=str(self._db_path))

    async def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None

    async def execute(self, query: str, params: dict | None = None) -> list[dict[str, Any]]:
        """执行自定义类 Cypher 查询（简化的 SQL 翻译）。"""
        query, args = self._convert_params(query, params or {})
        with self._lock:
            cur = self._conn.execute(query, args)
            rows = cur.fetchall()
            cols = [d[0] for d in cur.description] if cur.description else []
            return [dict(zip(cols, row)) for row in rows]

    async def execute_write(self, query: str, params: dict | None = None) -> None:
        query, args = self._convert_params(query, params or {})
        with self._lock:
            self._conn.execute(query, args)
            self._conn.commit()

    @staticmethod
    def _convert_params(query: str, params: dict) -> tuple[str, tuple]:
        """将 :param 命名参数转换为 ? 位置参数。同时兼容已有的 ? 占位。"""
        import re
        if not params:
            return query, ()
        names = re.findall(r':(\w+)', query)
        if not names:
            # Bare ? placeholders — count them and duplicate values as needed
            count = query.count('?')
            vals = list(params.values())
            if len(vals) == 1 and count > 1:
                vals = vals * count
            return query, tuple(vals)
        ordered = [params.get(n) for n in names]
        converted = re.sub(r':(\w+)', '?', query)
        return converted, tuple(ordered)

    async def clear(self) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM edges")
            self._conn.execute("DELETE FROM nodes")
            self._conn.commit()

    # ---- 图操作 ----

    async def upsert_node(self, node_id: str, labels: str, properties: dict) -> None:
        import json
        props_json = json.dumps(properties, ensure_ascii=False)
        await self.execute_write(
            "INSERT OR REPLACE INTO nodes(id, labels, properties) VALUES(:id, :labels, :props)",
            {"id": node_id, "labels": labels, "props": props_json},
        )

    async def upsert_edge(self, edge_id: str, source: str, target: str, etype: str, properties: dict | None = None) -> None:
        import json
        props_json = json.dumps(properties or {}, ensure_ascii=False)
        await self.execute_write(
            "INSERT OR REPLACE INTO edges(id, source_id, target_id, type, properties) VALUES(:id, :src, :tgt, :type, :props)",
            {"id": edge_id, "src": source, "tgt": target, "type": etype, "props": props_json},
        )

    async def get_node(self, node_id: str) -> dict | None:
        rows = await self.execute("SELECT * FROM nodes WHERE id = ?", {"id": node_id})
        return rows[0] if rows else None

    async def get_neighbors(self, node_id: str) -> list[dict]:
        """获取节点的所有邻居（含关系类型）。"""
        rows = await self.execute("""
            SELECT e.type as rel_type, e.source_id, e.target_id,
                   n2.labels as neighbor_labels, n2.properties as neighbor_props
            FROM edges e
            JOIN nodes n2 ON (CASE WHEN e.source_id = ? THEN e.target_id ELSE e.source_id END) = n2.id
            WHERE e.source_id = ? OR e.target_id = ?
        """, {"id": node_id})
        return rows

    async def get_all_nodes(self) -> list[dict]:
        return await self.execute("SELECT * FROM nodes")

    async def get_all_edges(self) -> list[dict]:
        return await self.execute("SELECT * FROM edges")


# =============================================================================
# Neo4j 图存储
# =============================================================================


class Neo4jGraphStore(IGraphStore):
    """基于 Neo4j 的图存储。"""

    def __init__(self, uri: str = "bolt://localhost:7687", user: str = "neo4j", password: str = "password") -> None:
        self._uri = uri
        self._user = user
        self._password = password
        self._driver = None

    async def connect(self) -> None:
        try:
            from neo4j import AsyncGraphDatabase
            self._driver = AsyncGraphDatabase.driver(self._uri, auth=(self._user, self._password))
            await self._driver.verify_connectivity()
            logger.info("Neo4j 已连接", uri=self._uri)
        except ImportError:
            raise ImportError("需要安装 neo4j 包: pip install neo4j")
        except Exception as e:
            logger.warning("Neo4j 连接失败，降级到 SQLite", error=str(e))
            raise

    async def close(self) -> None:
        if self._driver:
            await self._driver.close()
            self._driver = None

    async def execute(self, query: str, params: dict | None = None) -> list[dict[str, Any]]:
        async with self._driver.session() as session:
            result = await session.run(query, params or {})
            records = await result.data()
            return records

    async def execute_write(self, query: str, params: dict | None = None) -> None:
        async with self._driver.session() as session:
            await session.run(query, params or {})

    async def clear(self) -> None:
        await self.execute_write("MATCH (n) DETACH DELETE n")


# =============================================================================
# 工厂
# =============================================================================


async def create_graph_store(config: dict) -> IGraphStore:
    """根据配置创建图存储实例。"""
    kg_config = config.get("knowledge_graph", {})
    enabled = kg_config.get("enabled", False)

    if enabled:
        try:
            store = Neo4jGraphStore(
                uri=kg_config.get("uri", "bolt://localhost:7687"),
                user=kg_config.get("user", "neo4j"),
                password=kg_config.get("password", "password"),
            )
            await store.connect()
            return store
        except Exception:
            pass

    # 降级到 SQLite
    store = SQLiteGraphStore("data/graph.db")
    await store.connect()
    return store
