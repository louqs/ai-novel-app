"""RAG 文档模型."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class DocumentCategory(str, Enum):
    """文档类别."""

    WRITING_TIP = "writing_tip"             # 写作技巧
    PLATFORM_RULE = "platform_rule"         # 平台规则
    ANTI_AI_PATTERN = "anti_ai_pattern"     # 反AI模式
    GENRE_ANALYSIS = "genre_analysis"       # 题材分析
    CHAPTER_SNIPPET = "chapter_snippet"     # 已写章节片段
    CHARACTER_CARD = "character_card"       # 人物卡
    SETTING_FRAGMENT = "setting_fragment"   # 设定片段
    FORESHADOW_ENTRY = "foreshadow_entry"   # 伏笔条目
    FACT_ENTRY = "fact_entry"              # 事实条目


class RAGDocument(BaseModel):
    """RAG 文档 — 存储在 ChromaDB 中."""

    doc_id: str = Field(..., description="唯一文档标识")
    project_id: str | None = Field(default=None, description="None = 全局知识库文档")
    category: DocumentCategory
    content: str = Field(..., description="待嵌入的文本内容")
    metadata: dict = Field(default_factory=dict)
    # metadata 常用字段:
    #   source: str            — 来源 URL/文件路径
    #   chapter_number: int    — 章节号
    #   character_ids: list    — 相关人物
    #   platform: str          — 目标平台
    #   genre_tags: list       — 类型标签
    #   created_at: str        — ISO 时间
    #   last_accessed: str     — 最后访问时间
    #   access_count: int      — 访问次数
    embedding: list[float] | None = Field(default=None, description="嵌入向量 (由流水线填充)")


class RAGQueryResult(BaseModel):
    """RAG 查询结果."""

    doc: RAGDocument
    bm25_score: float | None = Field(default=None)
    semantic_score: float | None = Field(default=None)
    combined_score: float = Field(default=0.0)
    rank: int = Field(default=0)
