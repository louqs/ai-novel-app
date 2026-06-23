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
            ai_score = self._detector.calculate_ai_score(matches, text=content)

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

            # 4. De-AI 句式模板检测
            sentence_tmpl = self._detector.check_sentence_templates(content)
            if sentence_tmpl.get("is_excessive"):
                issues.append(GateIssue(
                    severity=Severity.ERROR,
                    code="anti_ai.deai_sentence_templates",
                    message=f"De-AI句式模板: {sentence_tmpl['suggestion']}",
                    suggestion="删除模板句式，用具体事实和判断替代",
                ))

            # 5. De-AI 语气态度检测
            tone_check = self._detector.check_tone_attitude(content)
            if tone_check.get("is_excessive"):
                issues.append(GateIssue(
                    severity=Severity.WARNING,
                    code="anti_ai.deai_tone",
                    message=f"De-AI语气问题: {tone_check['suggestion']}",
                    suggestion="删除协作沟通痕迹和谄媚语气",
                ))

            # 6. De-AI 硬阈值检测
            hard_check = self._detector.check_hard_constraints(content)
            if not hard_check.get("pass"):
                issues.append(GateIssue(
                    severity=Severity.ERROR,
                    code="anti_ai.deai_hard_constraints",
                    message=f"De-AI硬阈值违反: {hard_check['suggestion']}",
                    suggestion="按12项硬阈值修复",
                ))

            # 7. 中文特化检测（儿化音/翻译腔/虚假亲昵）
            cn_check = self._detector.check_chinese_specific(content)
            if cn_check.get("is_excessive"):
                issues.append(GateIssue(
                    severity=Severity.ERROR,
                    code="anti_ai.chinese_specific",
                    message=f"中文特化问题: {cn_check['suggestion']}",
                    suggestion="删除儿化音、翻译腔和虚假亲昵词",
                ))

            # 8. 小说特化检测（开头/结尾/情感/场景模板）
            story_check = self._detector.check_story_patterns(content)
            if story_check.get("is_excessive"):
                issues.append(GateIssue(
                    severity=Severity.WARNING,
                    code="anti_ai.story_patterns",
                    message=f"小说AI模式: {story_check['suggestion']}",
                    suggestion="用具体细节替代模板化描写，开头直接切入，结尾克制",
                ))

            # 9. qu-ai-wei 51 类模式精选检测
            quai_check = self._detector.check_quai_patterns(content)
            if quai_check.get("is_excessive"):
                issues.append(GateIssue(
                    severity=Severity.WARNING,
                    code="anti_ai.quai_patterns",
                    message=f"qu-ai-wei模式: {quai_check['suggestion']}",
                    suggestion="删除AI高频词堆砌、翻译腔残留、的的不休等问题",
                ))

            # 10. ximen-aimazi 频率控制检测
            ximen_check = self._detector.check_ximen_patterns(content)
            if ximen_check.get("is_excessive"):
                issues.append(GateIssue(
                    severity=Severity.WARNING,
                    code="anti_ai.ximen_patterns",
                    message=f"ximen频率控制: {ximen_check['suggestion']}",
                    suggestion="弱化副词≤2/千字、情感标签≤1/千字、比喻词≤1/千字",
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
        ai_score = self._detector.calculate_ai_score(matches, text=text)
        sentence_check = self._detector.detect_uniform_sentences(text)
        ending_check = self._detector.detect_generic_ending(text)
        not_xy = self._detector.detect_not_x_but_y(text)

        # 新增检测维度（来自 chapter_health_check.py）
        template_check = self._detector.check_template_words(text)
        psych_check = self._detector.check_psychology_cliche(text)
        cringe_check = self._detector.check_cringe_monologue(text)
        humanizer_check = self._detector.check_humanizer_patterns(text)
        dialogue_check = self._detector.check_dialogue_quotes(text)

        # De-AI 24 项检测系统
        deai_vocab_check = self._detector.check_deai_vocabulary(text)
        sentence_tmpl_check = self._detector.check_sentence_templates(text)
        tone_check = self._detector.check_tone_attitude(text)
        para_tmpl_check = self._detector.check_paragraph_templates(text)
        hard_check = self._detector.check_hard_constraints(text)

        return {
            "ai_score": round(ai_score, 3),
            "is_likely_ai": ai_score < 0.6,
            "not_x_but_y": not_xy,
            "vocabulary_diversity": round(self._detector._check_vocabulary_diversity(text), 3),
            "sentence_patterns": round(self._detector._check_sentence_patterns(text), 3),
            "emotion_labels": round(self._detector._check_emotion_labels(text), 3),
            "description_patterns": round(self._detector._check_description_patterns(text), 3),
            "dialogue_patterns": round(self._detector._check_dialogue_patterns(text), 3),
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
            # 新增维度
            "template_words": template_check,
            "psychology_cliche": psych_check,
            "cringe_monologue": cringe_check,
            "humanizer_patterns": humanizer_check,
            "dialogue_quotes": dialogue_check,
            # De-AI 24 项检测
            "deai_vocabulary": deai_vocab_check,
            "deai_sentence_templates": sentence_tmpl_check,
            "deai_tone_attitude": tone_check,
            "deai_paragraph_templates": para_tmpl_check,
            "deai_hard_constraints": hard_check,
            # 中文特化检测
            "chinese_specific": self._detector.check_chinese_specific(text),
            # 小说特化检测
            "story_patterns": self._detector.check_story_patterns(text),
            # qu-ai-wei 51 类模式精选
            "quai_patterns": self._detector.check_quai_patterns(text),
            # ximen-aimazi 频率控制检测
            "ximen_patterns": self._detector.check_ximen_patterns(text),
        }

    async def humanize(
        self,
        content: str,
        mode: str = "standard",
        novel_type: str = "",
        target_word_count: int | None = None,
    ) -> str:
        """人性化改写.

        Args:
            content: 原始文本.
            mode: 改写深度 — light / standard / deep / three_axe / chaos / deai.
            novel_type: 小说类型（90年代乡土/港综/都市重生）.
            target_word_count: 目标字数.
        """
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
            novel_type=novel_type,
            target_word_count=target_word_count,
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
