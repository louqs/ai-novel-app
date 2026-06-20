"""章节模型."""

from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, Field


class ChapterMetadata(BaseModel):
    """章节元数据."""

    chapter_id: str = Field(..., pattern=r"^ch_\d{4,}$", description="如 ch_0001")
    chapter_number: int = Field(..., ge=1)
    volume_number: int = Field(default=1, ge=1)
    title: str = Field(default="")
    word_count: int = Field(default=0, ge=0)
    platform: str = Field(default="")
    status: str = Field(default="draft", description="draft, revised, gate_passed, published")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    revision_count: int = Field(default=0, ge=0, le=10)
    model_used: str = Field(default="")
    tokens_consumed: int = Field(default=0, ge=0)
    quality_gate_score: float | None = Field(default=None, ge=0, le=1)


class Chapter(BaseModel):
    """章节 — 包含元数据和正文."""

    metadata: ChapterMetadata
    content: str = Field(default="", description="Markdown 格式正文")

    # 生成后提取的结构化信息
    extracted_facts: list = Field(default_factory=list)
    entities_mentioned: list[str] = Field(default_factory=list)
    foreshadow_updates: list = Field(default_factory=list)
