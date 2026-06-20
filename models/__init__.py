"""Pydantic v2 数据模型.

所有模型使用 strict 模式验证，结构化数据用 JSON 序列化，可读内容用 Markdown。
"""

from models.project import Platform, NovelStatus, ProjectMeta
from models.settings import WorldRule, Location, Faction, PowerLevel, PowerSystem, Settings
from models.character import CharacterArc, RelationshipType, CharacterProfile, Relationship, CharacterSet, CharacterVoiceCard
from models.progress import ChapterNode, VolumeOutline, Progress
from models.chapter import ChapterMetadata, Chapter
from models.facts import FactCategory, FactConfidence, FactEntry, FactLedger
from models.foreshadow import ForeshadowStatus, ForeshadowType, ForeshadowEntry, ForeshadowLedger
from models.rag import DocumentCategory, RAGDocument, RAGQueryResult
from models.power_level import PowerTier, PowerLevelRecord, GoldenFingerCost, PowerLevelLedger

__all__ = [
    # Project
    "Platform",
    "NovelStatus",
    "ProjectMeta",
    # Settings
    "WorldRule",
    "Location",
    "Faction",
    "PowerLevel",
    "PowerSystem",
    "Settings",
    # Character
    "CharacterArc",
    "RelationshipType",
    "CharacterProfile",
    "Relationship",
    "CharacterSet",
    "CharacterVoiceCard",
    # Progress
    "ChapterNode",
    "VolumeOutline",
    "Progress",
    # Chapter
    "ChapterMetadata",
    "Chapter",
    # Facts
    "FactCategory",
    "FactConfidence",
    "FactEntry",
    "FactLedger",
    # Foreshadow
    "ForeshadowStatus",
    "ForeshadowType",
    "ForeshadowEntry",
    "ForeshadowLedger",
    # RAG
    "DocumentCategory",
    "RAGDocument",
    "RAGQueryResult",
    # Power Level
    "PowerTier",
    "PowerLevelRecord",
    "GoldenFingerCost",
    "PowerLevelLedger",
]
