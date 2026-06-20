"""战力等级模型 — 追踪角色战力变化，避免战力崩坏."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field


class PowerTier(str, Enum):
    """战力等级."""

    LV1 = "lv1"  # 普通人
    LV2 = "lv2"  # 略有特长
    LV3 = "lv3"  # 小有所成
    LV4 = "lv4"  # 独当一面
    LV5 = "lv5"  # 高手级别
    LV6 = "lv6"  # 顶尖存在

    @property
    def label(self) -> str:
        labels = {
            "lv1": "普通人",
            "lv2": "略有特长",
            "lv3": "小有所成",
            "lv4": "独当一面",
            "lv5": "高手级别",
            "lv6": "顶尖存在",
        }
        return labels.get(self.value, "未知")


class PowerLevelRecord(BaseModel):
    """战力等级记录."""

    character_id: str = Field(..., description="角色ID")
    character_name: str = Field(..., description="角色名")
    tier: PowerTier = Field(default=PowerTier.LV1, description="当前等级")
    abilities: list[str] = Field(default_factory=list, description="当前能力列表")
    upgrade_history: list[dict] = Field(default_factory=list, description="升级历史")
    last_updated_chapter: int = Field(default=0, description="最后更新章节")


class GoldenFingerCost(BaseModel):
    """金手指代价记录."""

    character_id: str = Field(..., description="角色ID")
    golden_finger_name: str = Field(..., description="金手指名称")
    cost_type: str = Field(default="", description="代价类型：体力消耗/精神消耗/身体伤害/时间消耗")
    cost_keywords: list[str] = Field(
        default_factory=lambda: [
            "疲惫", "累得", "体力消耗", "虚脱", "冒冷汗",
            "脸色发白", "手发抖", "头晕", "眼前发黑",
        ],
        description="代价关键词",
    )
    usage_count: int = Field(default=0, description="使用次数")
    last_used_chapter: int = Field(default=0, description="最后使用章节")


class PowerLevelLedger(BaseModel):
    """战力等级账本."""

    project_id: str
    records: dict[str, PowerLevelRecord] = Field(default_factory=dict)
    golden_fingers: dict[str, GoldenFingerCost] = Field(default_factory=dict)
    warnings: list[dict] = Field(default_factory=list, description="战力崩坏警告")

    def check_upgrade_validity(
        self,
        character_id: str,
        new_tier: PowerTier,
        chapter_num: int,
    ) -> tuple[bool, str]:
        """检查升级是否有效（防止突然变强）."""
        record = self.records.get(character_id)
        if not record:
            return True, "新角色，无历史记录"

        current_tier_value = list(PowerTier).index(record.tier)
        new_tier_value = list(PowerTier).index(new_tier)

        # 检查是否跳级
        if new_tier_value - current_tier_value > 1:
            return False, f"跳级警告：从 {record.tier.label} 直接到 {new_tier.label}，需要铺垫"

        # 检查章节间隔
        if record.last_updated_chapter > 0:
            chapter_gap = chapter_num - record.last_updated_chapter
            if chapter_gap < 3 and new_tier_value > current_tier_value:
                return False, f"升级过快：距离上次升级仅 {chapter_gap} 章"

        return True, "升级有效"

    def add_warning(self, chapter_num: int, warning_type: str, message: str) -> None:
        """添加战力崩坏警告."""
        self.warnings.append({
            "chapter": chapter_num,
            "type": warning_type,
            "message": message,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
