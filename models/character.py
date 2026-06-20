"""人物模型."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class CharacterArc(str, Enum):
    """人物弧光类型."""

    POSITIVE = "positive"  # 成长/救赎
    FLAT = "flat"  # 静态/标志性
    NEGATIVE = "negative"  # 堕落/悲剧


class RelationshipType(str, Enum):
    """关系类型."""

    ALLY = "ally"
    ENEMY = "enemy"
    MASTER_DISCIPLE = "master_disciple"
    SUBORDINATE = "subordinate"
    EMOTIONAL = "emotional"  # 浪漫/CP
    FAMILY = "family"
    RIVAL = "rival"
    ACQUAINTANCE = "acquaintance"


class CharacterProfile(BaseModel):
    """人物档案."""

    character_id: str = Field(..., pattern=r"^char_[a-zA-Z0-9]+$")
    name: str = Field(..., min_length=1, max_length=100)
    aliases: list[str] = Field(default_factory=list)
    age: int | None = Field(default=None, ge=0)
    gender: str = Field(default="")
    appearance: str = Field(default="", description="外貌描述")
    personality_tags: list[str] = Field(default_factory=list, description="性格标签")
    background: str = Field(default="", description="背景故事")
    core_motivation: str = Field(default="", description="核心动机")
    arc_type: CharacterArc = Field(default=CharacterArc.FLAT)
    arc_description: str = Field(default="", description="人物弧光规划")
    current_status: str = Field(default="active", description="active, injured, missing, dead...")
    power_level: str = Field(default="", description="如 Golden Core Stage 3")
    faction_id: str | None = Field(default=None)
    first_appearance_chapter: int | None = Field(default=None, ge=0)
    notes: str = Field(default="")


class Relationship(BaseModel):
    """人物关系."""

    rel_id: str = Field(..., pattern=r"^rel_[a-zA-Z0-9]+$")
    source_id: str = Field(..., description="主体 character_id")
    target_id: str = Field(..., description="客体 character_id")
    rel_type: RelationshipType
    description: str = Field(default="", description="关系描述")
    first_established_chapter: int | None = Field(default=None, ge=0)
    last_updated_chapter: int | None = Field(default=None, ge=0)
    status: str = Field(default="active")


class CharacterVoiceCard(BaseModel):
    """角色语音卡 — 定义角色的语言风格，用于番茄爆款写作。

    确保每个角色说话方式不一样，避免所有角色用同一种语气（AI味重灾区）。
    """

    character_id: str = Field(..., description="关联的 character_id")
    character_name: str = Field(..., min_length=1, max_length=100, description="角色名")
    catchphrases: list[str] = Field(default_factory=list, description="口头禅/高频词")
    swearing_style: str = Field(
        default="无",
        description="粗口风格：无/轻度（语气词）/重度（脏话）/特定词（角色专属）",
    )
    sentence_pattern: str = Field(
        default="混合型",
        description="说话节奏：短句型/长句型/混合型/省略型",
    )
    verbal_tics: list[str] = Field(default_factory=list, description="语言习惯，如'说实话''你懂的'")
    notes: str = Field(default="", description="备注，如：说话时总是带着笑")


class CharacterSet(BaseModel):
    """人物集合 — 序列化为 CHARACTERS.md + characters.json."""

    project_id: str
    characters: dict[str, CharacterProfile] = Field(default_factory=dict)
    relationships: list[Relationship] = Field(default_factory=list)
    voice_cards: list[CharacterVoiceCard] = Field(default_factory=list, description="角色语音卡")
