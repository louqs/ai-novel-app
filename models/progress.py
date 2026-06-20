"""大纲与进度模型."""

from __future__ import annotations

from pydantic import BaseModel, Field


class ChapterNode(BaseModel):
    """大纲中的单个章节节点."""

    chapter_number: int = Field(..., ge=1)
    volume_number: int = Field(default=1, ge=1)
    title: str = Field(default="")
    summary: str = Field(default="", description="一句话梗概")
    key_events: list[str] = Field(default_factory=list, description="关键事件")
    character_moments: list[str] = Field(default_factory=list, description="人物节点")
    is_climax: bool = Field(default=False, description="是否为高潮章")
    is_hook_point: bool = Field(default=False, description="⭐ 名场面标记")
    foreshadow_plants: list[str] = Field(default_factory=list, description="本章要埋的伏笔ID")
    foreshadow_payoffs: list[str] = Field(default_factory=list, description="本章要回收的伏笔ID")
    status: str = Field(default="planned", description="planned, drafting, completed")


class VolumeOutline(BaseModel):
    """卷大纲."""

    volume_number: int = Field(..., ge=1)
    title: str = Field(default="")
    arc_description: str = Field(default="", description="本卷起承转合")
    chapters: list[ChapterNode] = Field(default_factory=list)


class Progress(BaseModel):
    """创作进度 — 序列化为 PROGRESS.md + progress.json."""

    project_id: str
    volumes: list[VolumeOutline] = Field(default_factory=list)
    quota_min_words_per_chapter: int = Field(default=2000, ge=500)
    quota_max_words_per_chapter: int = Field(default=4000, ge=1000)
    total_chapters_completed: int = Field(default=0, ge=0)
    total_words_written: int = Field(default=0, ge=0)
