"""世界观设定模型."""

from __future__ import annotations

from pydantic import BaseModel, Field


class WorldRule(BaseModel):
    """世界规则."""

    rule_id: str = Field(..., pattern=r"^rule_[a-zA-Z0-9]+$")
    name: str = Field(..., min_length=1, max_length=200)
    category: str = Field(..., description="如 magic_system, technology, social_structure")
    description: str = Field(default="")
    exceptions: list[str] = Field(default_factory=list)


class Location(BaseModel):
    """地点."""

    location_id: str = Field(..., pattern=r"^loc_[a-zA-Z0-9]+$")
    name: str = Field(..., min_length=1, max_length=200)
    type: str = Field(default="", description="city, realm, building, natural_formation...")
    faction_owner: str | None = Field(default=None)
    first_appearance_chapter: int | None = Field(default=None, ge=0)
    description: str = Field(default="")


class Faction(BaseModel):
    """势力/组织."""

    faction_id: str = Field(..., pattern=r"^fac_[a-zA-Z0-9]+$")
    name: str = Field(..., min_length=1, max_length=200)
    type: str = Field(default="", description="sect, empire, guild, family...")
    leader_id: str | None = Field(default=None)
    member_ids: list[str] = Field(default_factory=list)
    allies: list[str] = Field(default_factory=list)
    enemies: list[str] = Field(default_factory=list)
    description: str = Field(default="")


class PowerLevel(BaseModel):
    """力量体系等级."""

    rank: int = Field(..., ge=0)
    name: str = Field(..., min_length=1, max_length=100)
    description: str = Field(default="")
    breakthrough_conditions: list[str] = Field(default_factory=list)


class PowerSystem(BaseModel):
    """力量体系."""

    system_id: str = Field(..., pattern=r"^psys_[a-zA-Z0-9]+$")
    name: str = Field(..., description="如 Qi Cultivation, Mage Circles, Cyber-Ranks")
    levels: list[PowerLevel] = Field(default_factory=list)


class Settings(BaseModel):
    """世界观设定 — 序列化为 SETTINGS.md + settings.json."""

    model_config = {"extra": "forbid"}

    project_id: str
    world_name: str = Field(default="")
    timeline: list[str] = Field(default_factory=list, description="关键历史事件")
    geography: str = Field(default="", description="地理概述 (自由文本)")
    world_rules: list[WorldRule] = Field(default_factory=list)
    locations: dict[str, Location] = Field(default_factory=dict)
    factions: dict[str, Faction] = Field(default_factory=dict)
    power_systems: list[PowerSystem] = Field(default_factory=list)
