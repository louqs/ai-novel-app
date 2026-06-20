"""伏笔追踪模型."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class ForeshadowStatus(str, Enum):
    """伏笔生命周期."""

    PLANTED = "planted"     # 已埋
    BUILDING = "building"   # 发展中
    PAID = "paid"           # 已回收
    DROPPED = "dropped"     # 已废弃 (需用户确认)


class ForeshadowType(str, Enum):
    """伏笔类型."""

    CHARACTER_SECRET = "character_secret"   # 人物秘密
    ITEM_CLUE = "item_clue"                 # 物品线索
    PLOT_TWIST = "plot_twist"              # 剧情反转
    RELATIONSHIP = "relationship_hint"     # 关系暗示
    WORLD_MYSTERY = "world_mystery"        # 世界谜团
    CHEKHOV_GUN = "chekhov_gun"            # 契诃夫之枪


class ForeshadowEntry(BaseModel):
    """单条伏笔."""

    foreshadow_id: str = Field(..., pattern=r"^fs_[a-zA-Z0-9]+$")
    type: ForeshadowType
    description: str = Field(..., description="伏笔内容描述")
    planted_chapter: int = Field(..., ge=1)
    planted_span: str = Field(default="", description="原文证据")
    involved_characters: list[str] = Field(default_factory=list)
    involved_items: list[str] = Field(default_factory=list)
    status: ForeshadowStatus = Field(default=ForeshadowStatus.PLANTED)
    payoff_chapter: int | None = Field(default=None, ge=1)
    payoff_description: str = Field(default="")
    building_chapters: list[int] = Field(default_factory=list, description="推进此伏笔的章节")
    priority: int = Field(default=0, description="重要程度")
    user_confirmed_drop: bool = Field(default=False)


class ForeshadowLedger(BaseModel):
    """伏笔账本 — 序列化为 foreshadows.json."""

    project_id: str
    entries: dict[str, ForeshadowEntry] = Field(default_factory=dict)
