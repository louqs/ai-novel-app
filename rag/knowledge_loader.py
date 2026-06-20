"""知识库加载器 — 将 knowledge_base/ 下的文件索引到 RAG.

用法:
    loader = KnowledgeLoader(vector_store=store)
    count = await loader.load_all()
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import yaml

from core.logging_config import get_logger
from models.rag import DocumentCategory, RAGDocument
from rag.store import VectorStore

logger = get_logger(__name__)


class KnowledgeLoader:
    """知识库加载器 — 读取知识库文件并索引到 ChromaDB."""

    def __init__(
        self,
        vector_store: VectorStore,
        knowledge_dir: str | Path = "knowledge_base",
    ) -> None:
        self._store = vector_store
        self._base_dir = Path(knowledge_dir)

    async def load_all(self) -> int:
        """加载全部知识库内容.

        Returns:
            索引的文档总数.
        """
        total = 0

        # 写作技巧
        total += await self._load_markdown_dir(
            self._base_dir / "writing_tips",
            DocumentCategory.WRITING_TIP,
        )

        # 平台规则
        total += await self._load_markdown_dir(
            self._base_dir / "platform_rules",
            DocumentCategory.PLATFORM_RULE,
        )

        # 反AI 模式
        total += await self._load_anti_ai_patterns()

        # 题材数据
        total += await self._load_genre_data()

        logger.info("知识库加载完成", total_documents=total)
        return total

    async def _load_markdown_dir(
        self,
        directory: Path,
        category: DocumentCategory,
    ) -> int:
        """加载目录下所有 Markdown 文件，按标题拆分为文档."""
        if not directory.exists():
            return 0

        documents: list[RAGDocument] = []
        for md_file in directory.glob("*.md"):
            content = md_file.read_text(encoding="utf-8")
            if not content.strip():
                continue

            # 按 ## 标题拆分
            sections = self._split_markdown(content)
            for section_title, section_content in sections:
                if not section_content.strip():
                    continue
                doc_id = f"kb_{category.value}_{uuid.uuid4().hex[:8]}"
                documents.append(RAGDocument(
                    doc_id=doc_id,
                    project_id=None,  # 全局知识库
                    category=category,
                    content=f"{section_title}\n\n{section_content}",
                    metadata={
                        "source": str(md_file.relative_to(self._base_dir)),
                        "title": section_title,
                    },
                ))

        if documents:
            return await self._store.index_documents(documents)
        return 0

    async def _load_anti_ai_patterns(self) -> int:
        """加载反AI 模式特征库."""
        yaml_path = self._base_dir / "anti_ai_patterns" / "patterns.yaml"
        if not yaml_path.exists():
            return 0

        with open(yaml_path, encoding="utf-8") as f:
            data = yaml.safe_load(f)

        documents: list[RAGDocument] = []
        patterns = data.get("patterns", {})
        for pattern_name, pattern_data in patterns.items():
            content = yaml.dump({pattern_name: pattern_data}, allow_unicode=True, default_flow_style=False)
            doc_id = f"kb_anti_ai_{pattern_name}"
            documents.append(RAGDocument(
                doc_id=doc_id,
                project_id=None,
                category=DocumentCategory.ANTI_AI_PATTERN,
                content=content,
                metadata={
                    "source": "patterns.yaml",
                    "pattern_name": pattern_name,
                    "severity": pattern_data.get("severity", "unknown"),
                },
            ))

        if documents:
            return await self._store.index_documents(documents)
        return 0

    async def _load_genre_data(self) -> int:
        """加载热门赛道数据."""
        yaml_path = self._base_dir / "genre_data" / "hot_genres.yaml"
        if not yaml_path.exists():
            return 0

        with open(yaml_path, encoding="utf-8") as f:
            data = yaml.safe_load(f)

        documents: list[RAGDocument] = []
        genres = data.get("genres", [])
        for genre in genres:
            doc_id = f"kb_genre_{genre.get('name', 'unknown').replace(' ', '_')}"
            documents.append(RAGDocument(
                doc_id=doc_id,
                project_id=None,
                category=DocumentCategory.GENRE_ANALYSIS,
                content=yaml.dump(genre, allow_unicode=True, default_flow_style=False),
                metadata={
                    "source": "hot_genres.yaml",
                    "genre_name": genre.get("name", ""),
                    "platform": genre.get("platform", ""),
                    "heat": str(genre.get("heat", "")),
                },
            ))

        if documents:
            return await self._store.index_documents(documents)
        return 0

    @staticmethod
    def _split_markdown(content: str) -> list[tuple[str, str]]:
        """按 ## 标题拆分 Markdown，返回 (标题, 内容) 列表."""
        sections: list[tuple[str, str]] = []
        lines = content.split("\n")
        current_title = ""
        current_lines: list[str] = []

        for line in lines:
            if line.startswith("## ") and current_lines:
                sections.append((current_title, "\n".join(current_lines).strip()))
                current_title = line.strip("# ").strip()
                current_lines = []
            elif line.startswith("## "):
                current_title = line.strip("# ").strip()
                current_lines = []
            else:
                current_lines.append(line)

        if current_title and current_lines:
            sections.append((current_title, "\n".join(current_lines).strip()))

        # 如果没有 ## 标题, 整篇作为一个 section
        if not sections and content.strip():
            sections.append(("全文", content.strip()))

        return sections
