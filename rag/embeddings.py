"""嵌入提供器 — 支持本地模型和 API 嵌入.

本地: sentence-transformers (BGE-M3 / bge-small-zh)
API: 可扩展为调用外部 Embedding 服务
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from core.logging_config import get_logger

logger = get_logger(__name__)


class BaseEmbeddingProvider(ABC):
    """嵌入提供器基类."""

    @abstractmethod
    async def embed(self, texts: list[str]) -> list[list[float]]:
        """将文本列表嵌入为向量列表."""
        ...

    @abstractmethod
    async def embed_query(self, text: str) -> list[float]:
        """嵌入单条查询文本."""
        ...

    @property
    @abstractmethod
    def dimension(self) -> int:
        """嵌入向量维度."""
        ...


class LocalEmbeddingProvider(BaseEmbeddingProvider):
    """使用本地 sentence-transformers 模型.

    依赖: pip install sentence-transformers
    """

    def __init__(self, model_name: str = "BAAI/bge-small-zh-v1.5") -> None:
        self._model_name = model_name
        self._model: Any = None
        self._dimension: int = 0

    async def _ensure_model(self) -> None:
        if self._model is not None:
            return
        try:
            from sentence_transformers import SentenceTransformer

            self._model = SentenceTransformer(self._model_name)
            self._dimension = self._model.get_sentence_embedding_dimension()
            logger.info("嵌入模型已加载", model=self._model_name, dimension=self._dimension)
        except ImportError:
            raise ImportError(
                "需要安装 sentence-transformers: pip install sentence-transformers"
            )

    async def embed(self, texts: list[str]) -> list[list[float]]:
        await self._ensure_model()
        # sentence-transformers encode 是同步的，在 async 上下文中用 run_in_executor
        import asyncio

        loop = asyncio.get_running_loop()
        embeddings = await loop.run_in_executor(
            None,
            lambda: self._model.encode(texts, normalize_embeddings=True).tolist(),
        )
        return embeddings

    async def embed_query(self, text: str) -> list[float]:
        embeddings = await self.embed([text])
        return embeddings[0]

    @property
    def dimension(self) -> int:
        return self._dimension


class DummyEmbeddingProvider(BaseEmbeddingProvider):
    """占位嵌入提供器 — 用于开发/测试, 返回随机向量."""

    def __init__(self, dimension: int = 512) -> None:
        self._dimension = dimension

    async def embed(self, texts: list[str]) -> list[list[float]]:
        import hashlib
        import random

        results = []
        for text in texts:
            # 用 hash 做确定性"嵌入"
            seed = int(hashlib.md5(text.encode()).hexdigest()[:8], 16)
            rng = random.Random(seed)
            vec = [rng.uniform(-1, 1) for _ in range(self._dimension)]
            # 归一化
            norm = sum(v * v for v in vec) ** 0.5
            vec = [v / norm for v in vec]
            results.append(vec)
        return results

    async def embed_query(self, text: str) -> list[float]:
        embeddings = await self.embed([text])
        return embeddings[0]

    @property
    def dimension(self) -> int:
        return self._dimension
