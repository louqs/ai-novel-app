"""RAG 检索引擎 — BM25 粗筛 + 语义精排.

两阶段检索:
    1. BM25 粗筛 (候选池, 默认 8 条)
    2. 语义重排 (精排返回 Top-K, 默认 4 条)

条件触发:
    - 日常过渡章 → 跳过检索
    - 关键情节/多人物交互 → 触发检索
"""

from __future__ import annotations

from typing import Any

from rank_bm25 import BM25Okapi

from core.logging_config import get_logger
from models.rag import DocumentCategory, RAGDocument, RAGQueryResult
from rag.store import VectorStore

logger = get_logger(__name__)


class RetrievalEngine:
    """RAG 检索引擎 — BM25 + 语义混合."""

    # 触发检索的关键词
    TRIGGER_KEYWORDS = [
        "战斗", "突破", "揭露", "真相", "冲突", "反转", "重逢",
        "阴谋", "危机", "阴谋", "决斗", "升级", "觉醒", "背叛",
    ]

    def __init__(
        self,
        vector_store: VectorStore,
        *,
        bm25_candidates: int = 8,
        semantic_top_k: int = 4,
        bm25_weight: float = 0.3,
        semantic_weight: float = 0.7,
        min_score_threshold: float = 0.5,
    ) -> None:
        self._store = vector_store
        self._bm25_candidates = bm25_candidates
        self._semantic_top_k = semantic_top_k
        self._bm25_weight = bm25_weight
        self._semantic_weight = semantic_weight
        self._min_score_threshold = min_score_threshold

    @classmethod
    def should_retrieve(
        cls,
        chapter_context: dict[str, Any],
        *,
        force: bool = False,
    ) -> bool:
        """条件触发判断 — 是否需要检索.

        Args:
            chapter_context: 当前章节上下文 (大纲节点摘要 + 关键事件).
            force: 强制检索.

        Returns:
            True = 需要检索, False = 跳过 (日常过渡章).
        """
        if force:
            return True

        context_text = " ".join([
            chapter_context.get("summary", ""),
            *chapter_context.get("key_events", []),
        ]).lower()

        return any(kw in context_text for kw in cls.TRIGGER_KEYWORDS)

    async def retrieve(
        self,
        query: str,
        project_id: str,
        *,
        top_k: int | None = None,
        categories: list[DocumentCategory] | None = None,
    ) -> list[RAGQueryResult]:
        """执行两阶段检索.

        Args:
            query: 检索查询.
            project_id: 项目 ID.
            top_k: 最终返回数量 (默认 semantic_top_k).
            categories: 文档类别过滤.

        Returns:
            排序后的检索结果.
        """
        if top_k is None:
            top_k = self._semantic_top_k

        # 第一阶段: 语义检索 (ChromaDB 原生)
        # BM25 需要在已索引的文档语料上执行 — 这里先用语义检索
        results = await self._store.search(
            query=query,
            project_id=project_id,
            top_k=self._bm25_candidates,
            categories=categories,
        )

        # 第二阶段: 重新计算合并分数 + 截断到 top_k
        for r in results:
            r.combined_score = (
                self._bm25_weight * (r.bm25_score or 0)
                + self._semantic_weight * (r.semantic_score or 0)
            )

        results.sort(key=lambda r: r.combined_score, reverse=True)

        # 过滤低分
        results = [r for r in results if r.combined_score >= self._min_score_threshold]

        # 截断
        results = results[:top_k]

        # 重新分配 rank
        for i, r in enumerate(results):
            r.rank = i + 1

        return results

    async def retrieve_chapter_context(
        self,
        project_id: str,
        chapter_number: int,
        *,
        top_k: int = 4,
    ) -> list[RAGQueryResult]:
        """检索当前章节的相关上下文.

        返回:
            - 前几章片段 (人物状态、最近事件)
            - 相关人物卡
            - 活跃伏笔
            - 最近事实
        """
        categories = [
            DocumentCategory.CHAPTER_SNIPPET,
            DocumentCategory.CHARACTER_CARD,
            DocumentCategory.FORESHADOW_ENTRY,
            DocumentCategory.FACT_ENTRY,
        ]

        query = f"chapter {chapter_number} context"
        return await self.retrieve(
            query=query,
            project_id=project_id,
            top_k=top_k,
            categories=categories,
        )

    async def retrieve_writing_tips(
        self,
        query: str,
        *,
        platform: str | None = None,
        top_k: int = 3,
    ) -> list[RAGQueryResult]:
        """检索写作技巧."""
        categories = [DocumentCategory.WRITING_TIP, DocumentCategory.PLATFORM_RULE]
        return await self.retrieve(
            query=query,
            project_id="",  # 全局
            top_k=top_k,
            categories=categories,
        )
