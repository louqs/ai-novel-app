"""事实账本模型 — 追踪小说中的硬事实."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field


class FactCategory(str, Enum):
    """事实类别."""

    CHARACTER_STATE = "character_state"     # 人物状态 (位置、健康、修为)
    RELATIONSHIP = "relationship"           # 人物关系状态
    POSSESSION = "possession"              # 物品归属
    TIMELINE = "timeline"                  # 时间线节点
    QUANTITY = "quantity"                  # 数量 (金钱、距离、数量)
    LOCATION_STATE = "location_state"      # 地点状态
    RULE_APPLICATION = "rule_application"  # 世界规则应用
    PLOT_STATUS = "plot_status"            # 剧情状态


class FactConfidence(str, Enum):
    """事实置信度."""

    CERTAIN = "certain"     # 明确
    LIKELY = "likely"       # 很可能
    INFERRED = "inferred"   # 推断
    DISPUTED = "disputed"   # 有冲突


class FactEntry(BaseModel):
    """单条事实."""

    fact_id: str = Field(..., pattern=r"^fact_[a-zA-Z0-9]+$")
    category: FactCategory
    subject: str = Field(..., description="事实主体 (人物/地点/物品)")
    predicate: str = Field(..., description="发生了什么变化")
    value: str = Field(..., description="新值/状态")
    evidence_chapter: int = Field(..., ge=1, description="确立此事实的章节")
    evidence_span: str = Field(default="", description="原文片段")
    confidence: FactConfidence = Field(default=FactConfidence.CERTAIN)
    contradicts_fact_id: str | None = Field(default=None)
    is_active: bool = Field(default=True)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class FactLedger(BaseModel):
    """事实账本 — 序列化为 facts_ledger.json."""

    project_id: str
    entries: dict[str, FactEntry] = Field(default_factory=dict)
    last_updated_chapter: int = Field(default=0, ge=0)
