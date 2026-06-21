"""API 请求/响应 Schema."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


# =============================================================================
# 通用
# =============================================================================


class StatusResponse(BaseModel):
    status: str = "ok"
    message: str = ""


class ErrorResponse(BaseModel):
    status: str = "error"
    message: str
    detail: str | None = None


# =============================================================================
# 项目
# =============================================================================


class ProjectCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    platform: str = Field(default="fanqie")
    genre_tags: list[str] = Field(default_factory=list)
    one_liner: str = Field(default="")
    length: str = Field(default="medium")
    target_words_per_chapter: int = Field(default=3000, ge=1000, le=10000)


class ProjectUpdate(BaseModel):
    title: str | None = None
    platform: str | None = None
    genre_tags: list[str] | None = None
    one_liner: str | None = None
    status: str | None = None
    length: str | None = None


class ProjectResponse(BaseModel):
    project_id: str
    title: str
    platform: str
    genre_tags: list[str]
    one_liner: str
    status: str
    current_chapter: int
    current_volume: int
    created_at: str
    updated_at: str


# =============================================================================
# 人物
# =============================================================================


class CharacterCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    aliases: list[str] = Field(default_factory=list)
    age: int | None = None
    gender: str = ""
    appearance: str = ""
    personality_tags: list[str] = Field(default_factory=list)
    background: str = ""
    core_motivation: str = ""
    arc_type: str = "flat"
    arc_description: str = ""
    current_status: str = "active"
    power_level: str = ""
    faction_id: str | None = None


class CharacterUpdate(BaseModel):
    name: str | None = None
    aliases: list[str] | None = None
    appearance: str | None = None
    personality_tags: list[str] | None = None
    core_motivation: str | None = None
    current_status: str | None = None
    power_level: str | None = None
    notes: str | None = None


class RelationshipCreate(BaseModel):
    source_id: str
    target_id: str
    rel_type: str
    description: str = ""


# =============================================================================
# 章节
# =============================================================================


class ChapterGenerateRequest(BaseModel):
    chapter_number: int = Field(..., ge=1)
    auto_retry: bool = True


class ChapterGenerateBatch(BaseModel):
    start_chapter: int = Field(..., ge=1)
    count: int = Field(..., ge=1, le=50)
    pause_between: float = Field(default=0.0, ge=0)


class ChapterResponse(BaseModel):
    chapter_id: str
    chapter_number: int
    title: str
    word_count: int
    status: str
    content: str = ""
    model_used: str = ""
    tokens_consumed: int = 0
    quality_gate_score: float | None = None


# =============================================================================
# 反AI检测
# =============================================================================


class AntiAICheckRequest(BaseModel):
    text: str = Field(..., min_length=1)


class AntiAIHumanizeRequest(BaseModel):
    text: str = Field(..., min_length=1)
    mode: str = Field(default="standard")  # light / standard / deep / three_axe / chaos
    target_word_count: int | None = Field(default=None)


# =============================================================================
# Skill
# =============================================================================


class SkillExecuteRequest(BaseModel):
    args: dict[str, Any] = Field(default_factory=dict)


# =============================================================================
# 门禁
# =============================================================================


class GateOverrideRequest(BaseModel):
    reason: str = Field(default="人工审核通过")
