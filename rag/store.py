"""向量存储 — ChromaDB 封装.

管理 RAG 文档的索引、查询和 CRUD 操作。
"""

from __future__ import annotations

import uuid
from typing import Any

import chromadb
from chromadb.api import ClientAPI
from chromadb.config import Settings as ChromaSettings

from core.logging_config import get_logger
from models.rag import DocumentCategory, RAGDocument, RAGQueryResult
from rag.embeddings import BaseEmbeddingProvider, DummyEmbeddingProvider

logger = get_logger(__name__)


class VectorStore:
    """ChromaDB 向量存储封装.

    维护两个 collection:
        - novel_global: 全局知识库 (写作技巧、平台规则、反AI模式)
        - novel_project_{id}: 项目级知识库 (章节片段、人物卡、设定)
    """

    GLOBAL_COLLECTION = "novel_global"
    PROJECT_COLLECTION_PREFIX = "novel_project_"

    def __init__(
        self,
        persist_directory: str = "./data/chroma",
        embedding_provider: BaseEmbeddingProvider | None = None,
    ) -> None:
        self._persist_dir = persist_directory
        self._embedding_provider = embedding_provider or DummyEmbeddingProvider()
        self._client: ClientAPI | None = None
        self._collections: dict[str, Any] = {}

    # ---- 生命周期 ----

    async def start(self) -> None:
        """初始化 ChromaDB 客户端."""
        self._client = chromadb.PersistentClient(
            path=self._persist_dir,
            settings=ChromaSettings(anonymized_telemetry=False),
        )
        logger.info("ChromaDB 已连接", persist_dir=self._persist_dir)

    async def stop(self) -> None:
        """关闭 (ChromaDB PersistentClient 无需显式关闭)."""
        self._client = None
        self._collections.clear()

    # ---- 索引 ----

    async def index_documents(self, documents: list[RAGDocument]) -> int:
        """批量索引文档.

        根据 project_id 路由到全局或项目级 collection。

        Returns:
            成功索引的文档数量。
        """
        if not documents:
            return 0

        # 按 collection 分组
        batches: dict[str, list[RAGDocument]] = {}
        for doc in documents:
            coll_name = self._collection_name(doc.project_id)
            batches.setdefault(coll_name, []).append(doc)

        total = 0
        for coll_name, docs in batches.items():
            collection = await self._get_or_create_collection(coll_name)

            # 准备数据
            ids = [d.doc_id for d in docs]
            texts = [d.content for d in docs]
            metadatas = [
                {
                    "project_id": d.project_id or "",
                    "category": d.category.value,
                    **{k: str(v) for k, v in d.metadata.items()},
                }
                for d in docs
            ]

            # 生成嵌入
            embeddings = await self._embedding_provider.embed(texts)

            collection.add(
                ids=ids,
                documents=texts,
                embeddings=embeddings,
                metadatas=metadatas,
            )
            total += len(docs)

        logger.info("RAG 文档已索引", count=total)
        return total

    async def remove_documents(self, doc_ids: list[str], project_id: str | None = None) -> int:
        """删除指定文档."""
        coll_name = self._collection_name(project_id)
        collection = await self._get_or_create_collection(coll_name)
        collection.delete(ids=doc_ids)
        return len(doc_ids)

    # ---- 检索 ----

    async def search(
        self,
        query: str,
        project_id: str,
        *,
        top_k: int = 4,
        categories: list[DocumentCategory] | None = None,
    ) -> list[RAGQueryResult]:
        """混合检索 — BM25 粗筛 + 语义精排.

        当前版本: 仅语义检索 (BM25 粗筛在 retrieval.py 中实现后接入).
        """
        results: list[RAGQueryResult] = []

        # 1. 对查询生成嵌入
        query_embedding = await self._embedding_provider.embed_query(query)

        # 2. 搜索全局 collection
        global_results = await self._semantic_search(
            self.GLOBAL_COLLECTION,
            query_embedding,
            top_k=top_k,
            categories=categories,
        )
        results.extend(global_results)

        # 3. 搜索项目 collection (如果存在)
        project_coll = self._collection_name(project_id)
        try:
            project_results = await self._semantic_search(
                project_coll,
                query_embedding,
                top_k=top_k,
                categories=categories,
            )
            results.extend(project_results)
        except Exception:
            # 项目 collection 可能不存在
            pass

        # 4. 按相似度排序
        results.sort(key=lambda r: r.semantic_score or 0, reverse=True)
        results = results[:top_k]

        # 5. 分配 rank
        for i, r in enumerate(results):
            r.rank = i + 1
            r.combined_score = r.semantic_score or 0

        return results

    async def _semantic_search(
        self,
        coll_name: str,
        query_embedding: list[float],
        top_k: int,
        categories: list[DocumentCategory] | None = None,
    ) -> list[RAGQueryResult]:
        """在指定 collection 中执行语义搜索."""
        collection = await self._get_or_create_collection(coll_name)

        where_filter = None
        if categories:
            where_filter = {
                "$or": [{"category": c.value} for c in categories]
            }

        response = collection.query(
            query_embeddings=[query_embedding],
            n_results=top_k,
            where=where_filter,
            include=["documents", "metadatas", "distances"],
        )

        results: list[RAGQueryResult] = []
        if response["ids"] and response["ids"][0]:
            for i, doc_id in enumerate(response["ids"][0]):
                metadata = response["metadatas"][0][i] if response["metadatas"] else {}
                category_str = metadata.get("category", "writing_tip")

                try:
                    category = DocumentCategory(category_str)
                except ValueError:
                    category = DocumentCategory.WRITING_TIP

                doc = RAGDocument(
                    doc_id=doc_id,
                    project_id=metadata.get("project_id"),
                    category=category,
                    content=response["documents"][0][i] if response["documents"] else "",
                    metadata=metadata,
                )
                distance = response["distances"][0][i] if response["distances"] else 0
                # ChromaDB distance → similarity score (cosine distance → similarity)
                score = 1.0 - min(distance, 2.0) / 2.0

                results.append(RAGQueryResult(
                    doc=doc,
                    semantic_score=round(score, 4),
                    combined_score=round(score, 4),
                ))

        return results

    # ---- 内部 ----

    def _collection_name(self, project_id: str | None) -> str:
        if project_id:
            return f"{self.PROJECT_COLLECTION_PREFIX}{project_id}"
        return self.GLOBAL_COLLECTION

    async def _get_or_create_collection(self, name: str) -> Any:
        if name in self._collections:
            return self._collections[name]

        if self._client is None:
            raise RuntimeError("ChromaDB 未初始化, 请先调用 start()")

        # 获取或创建
        try:
            collection = self._client.get_collection(name=name)
        except Exception:
            collection = self._client.create_collection(
                name=name,
                metadata={"hnsw:space": "cosine"},
            )

        self._collections[name] = collection
        return collection
