"""项目模型 — 顶层项目描述."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field


# 字数范围 (min_words, max_words)
_LENGTH_WORD_BOUNDS: dict[str, tuple[int, int]] = {
    "short": (10_000, 50_000),
    "medium": (50_000, 150_000),
    "long": (150_000, 500_000),
    "extra_long": (500_000, 7_000_000),
}


class NovelLength(str, Enum):
    """篇幅."""

    SHORT = "short"           # 短篇: 1-5万字
    MEDIUM = "medium"         # 中篇: 5-15万字
    LONG = "long"             # 长篇: 15-50万字
    EXTRA_LONG = "extra_long" # 超长篇: 50-700万字

    @property
    def word_bounds(self) -> tuple[int, int]:
        """返回 (min_words, max_words)."""
        return _LENGTH_WORD_BOUNDS.get(self.value, (150_000, 500_000))

    @property
    def word_range(self) -> str:
        bounds = self.word_bounds
        return f"{bounds[0] // 10000}万-{bounds[1] // 10000}万字"

    def chapter_range(self, words_per_chapter: int = 3000,
                      min_words: int | None = None, max_words: int | None = None) -> tuple[int, int]:
        """根据每章字数计算章节数区间 (min, max).

        Args:
            words_per_chapter: 每章目标字数.
            min_words: 自定义最低字数（覆盖篇幅默认值）.
            max_words: 自定义最高字数（覆盖篇幅默认值）.
        """
        lo, hi = self.word_bounds
        if min_words is not None:
            lo = min_words
        if max_words is not None:
            hi = max_words
        return (max(1, lo // words_per_chapter), max(2, hi // words_per_chapter))

    def default_chapters(self, words_per_chapter: int = 3000,
                         min_words: int | None = None, max_words: int | None = None) -> int:
        """取章节数区间中值作为默认值."""
        lo, hi = self.chapter_range(words_per_chapter, min_words, max_words)
        return (lo + hi) // 2

    def default_volumes(self, total_chapters: int) -> int:
        """根据篇幅和章节数计算默认分卷数.

        Returns:
            1 = 不分卷；>=2 = 分卷数
        """
        if self in (NovelLength.SHORT, NovelLength.MEDIUM):
            return 1  # 短篇、中篇默认不分卷
        if self == NovelLength.LONG:
            return 4  # 长篇默认4卷
        # 超长篇默认6卷，最多10卷
        return 6


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
    min_words: int | None = Field(default=None, ge=1000, description="自定义最低字数（覆盖篇幅默认值）")
    max_words: int | None = Field(default=None, ge=1000, le=7_000_000, description="自定义最高字数（覆盖篇幅默认值，上限700万字）")
    current_chapter: int = Field(default=0, ge=0)
    current_volume: int = Field(default=1, ge=1)
