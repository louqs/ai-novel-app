"""项目模型 — 顶层项目描述."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field


class NovelLength(str, Enum):
    """篇幅."""

    SHORT = "short"       # 短篇: 1-5万字
    MEDIUM = "medium"     # 中篇: 5-15万字
    LONG = "long"         # 长篇: 15万字以上

    @property
    def word_range(self) -> str:
        return {"short": "1万-5万字 (30-150章)", "medium": "5万-15万字 (150-500章)", "long": "15万字以上 (500+章)"}.get(self.value, "")

    @property
    def default_chapters(self) -> int:
        return {"short": 30, "medium": 150, "long": 500}.get(self.value, 100)


class Platform(str, Enum):
    """投稿平台."""

    FANQIE = "fanqie"       # 番茄小说
    QIDIAN = "qidian"       # 起点中文网
    JINJIANG = "jinjiang"   # 晋江文学城
    QIMAO = "qimao"         # 七猫小说
    DOUBAN = "douban"       # 豆瓣阅读


class NovelStatus(str, Enum):
    """小说创作状态."""

    PLANNING = "planning"       # 规划中
    WRITING = "writing"         # 创作中
    PAUSED = "paused"           # 暂停
    COMPLETED = "completed"     # 已完成


class ProjectMeta(BaseModel):
    """项目元数据 — 序列化为 project.json."""

    model_config = {"extra": "forbid", "str_strip_whitespace": True}

    project_id: str = Field(..., pattern=r"^proj_[a-zA-Z0-9]+$", description="唯一项目标识")
    title: str = Field(..., min_length=1, max_length=200, description="小说标题")
    author: str = Field(default="AI-Assisted", max_length=100)
    platform: Platform = Field(..., description="目标投稿平台")
    length: str = Field(default="long", description="篇幅: short/medium/long")
    genre_tags: list[str] = Field(default_factory=list, max_length=10, description="类型标签")
    one_liner: str = Field(default="", max_length=500, description="一句话梗概 (logline)")
    status: NovelStatus = NovelStatus.PLANNING
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    target_words_per_chapter: int = Field(default=3000, ge=1000, le=10000)
    total_chapters_planned: int | None = Field(default=None, ge=1)
    current_chapter: int = Field(default=0, ge=0)
    current_volume: int = Field(default=1, ge=1)
