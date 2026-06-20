"""反AI检测插件 — 统一入口.

组合 PatternDetector + HumanizationEngine + AdversarialRewriter，
实现完整的检测 → 分析 → 修复流水线。

实现 IQualityGate 接口，可直接挂载到门禁链。
"""

from __future__ import annotations

from typing import Any

from core.logging_config import get_logger
from core.plugin_manager import PluginManifest
from core.quality_gate import GateIssue, GateResult, GateVerdict, IQualityGate, Severity
from plugins.anti_ai_detection.humanization_engine import AdversarialRewriter, HumanizationEngine
from plugins.anti_ai_detection.pattern_detector import AIPatternDetector

logger = get_logger(__name__)


class AntiAIDetectionPlugin(IQualityGate):
    """反AI检测插件 — 门禁链的第4道."""

    name = "anti-ai-detection"
    order = 40  # 在 consistency(10) / foreshadow(20) / style(30) 之后
    version = "0.1.0"

    def __init__(self) -> None:
        self._kernel = None
        self._detector: AIPatternDetector | None = None
        self._humanizer: HumanizationEngine | None = None
        self._rewriter: AdversarialRewriter | None = None

    async def on_load(self, kernel) -> None:
        self._kernel = kernel
        self._detector = AIPatternDetector()
        self._humanizer = HumanizationEngine()
        self._rewriter = AdversarialRewriter()
        await self._humanizer.on_load(kernel)
        await self._rewriter.on_load(kernel)
        logger.info("反AI检测模块已加载")

    async def on_unload(self) -> None:
        self._kernel = None

    # ------------------------------------------------------------------
    # Quality Gate
    # ------------------------------------------------------------------

    async def evaluate(self, chapter: dict[str, Any], context: dict[str, Any]) -> GateResult:
        """执行反AI检测."""
        content = chapter.get("content", "")
        if not content:
            return GateResult(gate_name=self.name, verdict=GateVerdict.PASS, score=1.0)

        issues: list[GateIssue] = []

        # 1. 规则检测
        if self._detector:
            matches = self._detector.detect(content)
            ai_score = self._detector.calculate_ai_score(matches)

            # 高严重度模式 → 必须修复
            high_matches = [m for m in matches if m.severity == "high"]
            if high_matches:
                for m in high_matches:
                    issues.append(GateIssue(
                        severity=Severity.ERROR,
                        code=f"anti_ai.{m.category}",
                        message=f"检测到 AI 高频模式: {m.category} ({m.count}处)",
                        suggestion=f"替换或删除以下词汇: {', '.join(m.matched_items[:5])}",
                    ))

            # 中严重度 → 建议修复
            medium_matches = [m for m in matches if m.severity == "medium"]
            for m in medium_matches[:3]:
                issues.append(GateIssue(
                    severity=Severity.WARNING,
                    code=f"anti_ai.{m.category}",
                    message=f"AI 模式: {m.category} ({m.count}处)",
                    suggestion=f"建议处理: {', '.join(m.matched_items[:3])}",
                ))

            # 2. 句长均匀度
            sentence_check = self._detector.detect_uniform_sentences(content)
            if sentence_check.get("is_uniform"):
                issues.append(GateIssue(
                    severity=Severity.ERROR,
                    code="anti_ai.uniform_sentences",
                    message=f"句长过于均匀 (SD={sentence_check['sd']})，是强 AI 信号",
                    suggestion="刻意变化句长——插入短句打断均匀节奏",
                ))

            # 3. 泛化结尾
            ending_check = self._detector.detect_generic_ending(content)
            if ending_check.get("has_generic_ending"):
                issues.append(GateIssue(
                    severity=Severity.ERROR,
                    code="anti_ai.generic_ending",
                    message=f"章尾检测到泛化结尾: {ending_check['found']}",
                    suggestion="替换为具体悬念或冲突",
                ))

            # 综合判定
            if any(i.severity == Severity.ERROR for i in issues):
                verdict = GateVerdict.REVISE
            elif issues:
                verdict = GateVerdict.PASS  # WARNING 不阻塞
            else:
                verdict = GateVerdict.PASS

            return GateResult(
                gate_name=self.name,
                verdict=verdict,
                issues=issues,
                score=ai_score,
                metadata={"matches_count": len(matches), "pattern_match_count": len(matches)},
            )

        return GateResult(gate_name=self.name, verdict=GateVerdict.PASS, score=1.0)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def detect(self, text: str) -> dict[str, Any]:
        """完整检测报告."""
        if not self._detector:
            return {"error": "detector not initialized"}

        matches = self._detector.detect(text)
        ai_score = self._detector.calculate_ai_score(matches)
        sentence_check = self._detector.detect_uniform_sentences(text)
        ending_check = self._detector.detect_generic_ending(text)

        return {
            "ai_score": round(ai_score, 3),
            "is_likely_ai": ai_score < 0.6,
            "pattern_matches": [
                {
                    "category": m.category,
                    "severity": m.severity,
                    "count": m.count,
                    "items": m.matched_items,
                }
                for m in matches
            ],
            "sentence_uniformity": sentence_check,
            "generic_ending": ending_check,
            "total_issues": len(matches),
        }

    async def humanize(
        self,
        content: str,
        mode: str = "standard",
    ) -> str:
        """人性化改写."""
        if not self._humanizer:
            return content

        matches = self._detector.detect(content) if self._detector else []
        match_dicts = [
            {"category": m.category, "matched_items": m.matched_items}
            for m in matches
        ]
        return await self._humanizer.humanize(
            content,
            mode=mode,
            detected_patterns=match_dicts,
        )

    async def adversarial_rewrite(self, content: str, iterations: int = 2) -> str:
        """对抗改写."""
        if not self._rewriter:
            return content
        return await self._rewriter.rewrite_adversarial(content, iterations=iterations)


def create_manifest() -> PluginManifest:
    return PluginManifest(
        name="anti-ai-detection",
        version="0.1.0",
        description="反AI检测模块 — 检测+人性化+对抗改写",
        dependencies=[],
        hooks=["on_load", "on_unload", "on_gate_check"],
    )


def create_plugin() -> AntiAIDetectionPlugin:
    return AntiAIDetectionPlugin()
