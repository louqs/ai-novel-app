"""伏笔追踪模型."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field


def _char_bigrams(text: str) -> set[str]:
    """提取中文文本的字符 2-gram 集合（中文无空格，按字符切更可靠）."""
    chars = re.findall(r"[一-鿿]", text or "")
    if len(chars) < 2:
        return set(chars)
    return {chars[i] + chars[i + 1] for i in range(len(chars) - 1)}


def foreshadow_text_match(a: str, b: str, threshold: float = 0.3) -> bool:
    """判断两段伏笔描述是否指向同一伏笔（中文友好）.

    优先比对显式编号「伏笔#N」；否则用字符 2-gram 的 Jaccard 相似度。
    threshold 默认 0.3：中文描述措辞常有出入，过高会漏配。
    """
    if not a or not b:
        return False
    # 1) 显式编号优先
    ma = re.search(r"伏笔#?(\d+)", a)
    mb = re.search(r"伏笔#?(\d+)", b)
    if ma and mb:
        return ma.group(1) == mb.group(1)
    # 2) 字符 2-gram Jaccard
    ga, gb = _char_bigrams(a), _char_bigrams(b)
    if not ga or not gb:
        return False
    inter = len(ga & gb)
    union = len(ga | gb)
    if union == 0:
        return False
    # Jaccard 或「较短串被高度覆盖」任一成立即视为匹配
    jaccard = inter / union
    coverage = inter / min(len(ga), len(gb))
    return jaccard >= threshold or coverage >= 0.6


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

    # 新增：评分系统（来自 novel-templates）
    strength: int = Field(default=5, ge=1, le=10, description="强度：影响读者多强烈感知到此伏笔")
    hidden_level: int = Field(default=5, ge=1, le=10, description="隐藏度：越高越隐蔽，读者越难察觉")
    urgency: int = Field(default=0, ge=0, le=3, description="紧急度：0=不紧急, 1=需关注, 2=急需回收, 3=已超期")
    anticipation_score: int = Field(default=5, ge=1, le=10, description="期待感评分：读者对此伏笔的期待程度")
    planned_payoff_chapter: int | None = Field(default=None, description="计划解决章节")
    best_payoff_chapter: int | None = Field(default=None, description="最佳兑现章节")

    def check_overdue(self, current_chapter: int) -> dict:
        """检查伏笔是否超期."""
        if self.status == ForeshadowStatus.PAID:
            return {"is_overdue": False, "reason": "已回收"}

        if self.planned_payoff_chapter and current_chapter > self.planned_payoff_chapter + 5:
            return {
                "is_overdue": True,
                "reason": f"已超期：当前第{current_chapter}章，计划第{self.planned_payoff_chapter}章解决",
                "overdue_chapters": current_chapter - self.planned_payoff_chapter - 5,
            }

        if self.urgency >= 2 and self.planned_payoff_chapter:
            chapters_left = self.planned_payoff_chapter - current_chapter
            if 0 < chapters_left <= 3:
                return {
                    "is_overdue": False,
                    "is_urgent": True,
                    "reason": f"急需回收：距计划章节仅剩{chapters_left}章",
                }

        return {"is_overdue": False}


class ForeshadowLedger(BaseModel):
    """伏笔账本 — 序列化为 foreshadows.json."""

    project_id: str
    entries: dict[str, ForeshadowEntry] = Field(default_factory=dict)

    def get_overdue_foreshadows(self, current_chapter: int) -> list[dict]:
        """获取所有超期伏笔."""
        overdue = []
        for fs_id, entry in self.entries.items():
            result = entry.check_overdue(current_chapter)
            if result.get("is_overdue"):
                overdue.append({
                    "foreshadow_id": fs_id,
                    "description": entry.description,
                    **result,
                })
        return overdue

    def get_urgent_foreshadows(self, current_chapter: int) -> list[dict]:
        """获取所有急需回收的伏笔."""
        urgent = []
        for fs_id, entry in self.entries.items():
            result = entry.check_overdue(current_chapter)
            if result.get("is_urgent"):
                urgent.append({
                    "foreshadow_id": fs_id,
                    "description": entry.description,
                    **result,
                })
        return urgent

    def get_by_anticipation_score(self, top_n: int = 10) -> list[dict]:
        """按期待感评分排序，返回前N个伏笔."""
        sorted_entries = sorted(
            self.entries.items(),
            key=lambda x: x[1].anticipation_score,
            reverse=True,
        )
        return [
            {
                "foreshadow_id": fs_id,
                "description": entry.description,
                "anticipation_score": entry.anticipation_score,
                "status": entry.status.value,
            }
            for fs_id, entry in sorted_entries[:top_n]
        ]
