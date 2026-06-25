"""伏笔管理器 — 追踪伏笔的完整生命周期。

生命周期:
    planted → building → paid / dropped

功能:
    1. 自动从章节中检测新伏笔和伏笔推进
    2. 每章注入活跃伏笔到撰写 Prompt
    3. 检测伏笔回收
    4. dropped 状态需要用户确认
    5. 伏笔审计 (列出所有未回收伏笔)

用法:
    # 门禁检查
    result = await plugin.evaluate(chapter, context)

    # 伏笔提取
    updates = await plugin.extract_foreshadows(chapter_content, chapter_num)
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from core.logging_config import get_logger
from core.plugin_manager import PluginManifest
from core.quality_gate import GateIssue, GateResult, GateVerdict, IQualityGate, Severity

logger = get_logger(__name__)


class ForeshadowManagerPlugin(IQualityGate):
    """伏笔管理器插件."""

    name = "foreshadow-manager"
    order = 20  # 第二道门禁
    version = "0.1.0"

    def __init__(self) -> None:
        self._kernel = None

    async def on_load(self, kernel) -> None:
        self._kernel = kernel
        logger.info("伏笔管理器已加载")

    async def on_unload(self) -> None:
        self._kernel = None

    # ------------------------------------------------------------------
    # Quality Gate
    # ------------------------------------------------------------------

    async def evaluate(self, chapter: dict[str, Any], context: dict[str, Any]) -> GateResult:
        """检查伏笔状态 — 是否有未授权丢弃、是否有该推进的伏笔被遗忘."""
        foreshadows_data = context.get("foreshadows", {})
        entries = foreshadows_data.get("entries", {})

        issues: list[GateIssue] = []

        # 超期阈值随篇幅+体裁自适应（短篇收得紧，长篇可拖久；不再写死 20）
        settings = context.get("settings", {}) or {}
        meta = settings.get("meta", {}) if isinstance(settings, dict) else {}
        length = settings.get("length") or (meta.get("length") if isinstance(meta, dict) else None)
        genre_tags = settings.get("genre_tags") or (meta.get("genre_tags") if isinstance(meta, dict) else None) or []
        try:
            from core.knowledge_resolver import overdue_gap as _overdue_gap
            gap_limit = _overdue_gap(length, genre_tags)
        except Exception:
            gap_limit = 20

        # 检查: 距上次推进超过阈值的活跃伏笔
        chapter_num = chapter.get("chapter_number", 0)
        stale_count = 0
        for fs_id, fs in entries.items():
            if isinstance(fs, dict) and fs.get("status") in ("planted", "building"):
                building_chs = fs.get("building_chapters", [])
                planted_ch = fs.get("planted_chapter", 0)
                last_mention = max(building_chs) if building_chs else planted_ch
                if chapter_num - last_mention > gap_limit:
                    stale_count += 1

        if stale_count > 0:
            issues.append(GateIssue(
                severity=Severity.WARNING,
                code="foreshadow.stale",
                message=f"有 {stale_count} 个伏笔超过 {gap_limit} 章未推进，可能导致读者遗忘或作者遗忘",
                suggestion="在最近章节中提及或推进这些伏笔，或确认是否需要废弃",
            ))

        # 检查: 是否有未确认废弃的伏笔
        unauthorized_drops = 0
        for fs_id, fs in entries.items():
            if isinstance(fs, dict) and fs.get("status") == "dropped" and not fs.get("user_confirmed_drop"):
                unauthorized_drops += 1

        if unauthorized_drops > 0:
            issues.append(GateIssue(
                severity=Severity.ERROR,
                code="foreshadow.unauthorized_drop",
                message=f"有 {unauthorized_drops} 个伏笔被标记为废弃但未经用户确认",
                suggestion="请确认是否废弃这些伏笔，或将其恢复为 planted 状态",
            ))

        score = 1.0 if not issues else 0.7
        verdict = GateVerdict.PASS if not issues else GateVerdict.REVISE

        return GateResult(
            gate_name=self.name,
            verdict=verdict,
            issues=issues,
            score=score,
        )

    # ------------------------------------------------------------------
    # 伏笔提取
    # ------------------------------------------------------------------

    async def extract_foreshadows(
        self,
        chapter_content: str,
        chapter_num: int,
        existing_foreshadows: dict[str, Any] | None = None,
        expected_payoff_descs: list[str] | None = None,
    ) -> dict[str, Any]:
        """从章节中提取伏笔信息.

        Args:
            expected_payoff_descs: 本章大纲计划回收的伏笔「描述」列表，用于生成回收提示。

        Returns:
            {
                "new_foreshadows": [...],
                "advanced": [foreshadow_id, ...],
                "paid": [{"foreshadow_id": ..., "description": ...}, ...],
            }
        """
        from models.foreshadow import foreshadow_text_match

        existing = {}
        expected_payoffs = []  # 本章计划回收的伏笔
        entries = (existing_foreshadows or {}).get("entries", {})
        for fs_id, fs in entries.items():
            if not isinstance(fs, dict):
                continue
            existing[fs_id] = {
                "description": fs.get("description", ""),
                "status": fs.get("status", ""),
                "planted_chapter": fs.get("planted_chapter", 0),
            }
        # 用大纲本章的「计划回收描述」中文友好匹配到已有伏笔，生成回收提示
        for desc in (expected_payoff_descs or []):
            if not desc:
                continue
            for fs_id, fs in entries.items():
                if not isinstance(fs, dict) or fs.get("status") == "paid":
                    continue
                if foreshadow_text_match(fs.get("description", ""), desc):
                    expected_payoffs.append({"id": fs_id, "description": fs.get("description", "")})
                    break

        payoff_hint = ""
        if expected_payoffs:
            payoff_hint = "\n\n⚠️ 以下伏笔计划在本章回收，请重点检查是否已回收:\n"
            for ep in expected_payoffs:
                payoff_hint += f"- [{ep['id']}] {ep['description']}\n"

        prompt = f"""分析以下章节中的伏笔活动:

章节内容:
{chapter_content[:8000]}

已有伏笔:
{json.dumps(existing, ensure_ascii=False, indent=2) if existing else "无"}
{payoff_hint}

请识别:
1. **新伏笔**: 本章新埋的伏笔
2. **伏笔推进**: 本章推进/提及了哪些已有伏笔
3. **伏笔回收**: 本章回收/揭示/引爆了哪些伏笔（重点关注上面标记的计划回收伏笔）

返回 JSON:
```json
{{
  "new_foreshadows": [
    {{
      "type": "character_secret|item_clue|plot_twist|relationship_hint|world_mystery|chekhov_gun",
      "description": "伏笔内容",
      "involved_characters": [],
      "involved_items": [],
      "priority": 1-5
    }}
  ],
  "advanced": ["fs_001", "fs_002"],
  "paid": [
    {{
      "foreshadow_id": "fs_001",
      "payoff_description": "如何回收的"
    }}
  ]
}}
```

注意: 如果本章没有伏笔活动，返回空数组即可。"""

        result = await self._kernel.call_llm(
            messages=[{"role": "user", "content": prompt}],
            tier="budget",
            max_tokens=2048,
            temperature=0.2,
        )

        try:
            return json.loads(result["content"])
        except json.JSONDecodeError:
            import re
            # 提取 ```json ... ``` 代码块（贪婪匹配）
            match = re.search(r'```(?:json)?\s*([\s\S]+?)\s*```', result["content"])
            if match:
                try:
                    return json.loads(match.group(1))
                except json.JSONDecodeError:
                    pass
            # 尝试找第一个JSON对象
            content = result["content"]
            start = content.find('{')
            if start >= 0:
                depth = 0
                for i in range(start, len(content)):
                    if content[i] == '{':
                        depth += 1
                    elif content[i] == '}':
                        depth -= 1
                        if depth == 0:
                            try:
                                return json.loads(content[start:i + 1])
                            except json.JSONDecodeError:
                                break
            return {"new_foreshadows": [], "advanced": [], "paid": []}

    async def get_active_foreshadows(
        self,
        project_id: str,
    ) -> list[dict[str, Any]]:
        """获取所有活跃伏笔 (planted + building)."""
        ns = f"project:{project_id}"
        data = await self._kernel.context().get(ns, "foreshadows", {})
        entries = data.get("entries", {})
        return [
            fs for fs in entries.values()
            if isinstance(fs, dict) and fs.get("status") in ("planted", "building")
        ]

    async def audit_foreshadows(self, project_id: str) -> dict[str, Any]:
        """伏笔审计报告."""
        ns = f"project:{project_id}"
        data = await self._kernel.context().get(ns, "foreshadows", {})
        entries = data.get("entries", {})

        planted = 0
        building = 0
        paid = 0
        dropped = 0
        unpaid: list[dict] = []

        for fs_id, fs in entries.items():
            if not isinstance(fs, dict):
                continue
            status = fs.get("status", "planted")
            if status == "planted":
                planted += 1
                unpaid.append(fs)
            elif status == "building":
                building += 1
                unpaid.append(fs)
            elif status == "paid":
                paid += 1
            elif status == "dropped":
                dropped += 1

        return {
            "total": len(entries),
            "planted": planted,
            "building": building,
            "paid": paid,
            "dropped": dropped,
            "unpaid_count": len(unpaid),
            "unpaid": unpaid,
            "payoff_rate": round(paid / max(len(entries), 1) * 100, 1),
        }


def create_manifest() -> PluginManifest:
    return PluginManifest(
        name="foreshadow-manager",
        version="0.1.0",
        description="伏笔管理器 — 追踪伏笔生命周期",
        dependencies=[],
        hooks=["on_load", "on_unload", "on_gate_check"],
    )


def create_plugin() -> ForeshadowManagerPlugin:
    return ForeshadowManagerPlugin()
