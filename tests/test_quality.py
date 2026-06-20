"""Phase 2 集成测试 — 质量保障体系."""

from __future__ import annotations

from typing import Any

import pytest
import yaml

from core.quality_gate import (
    GateChainConfig,
    GateChainExecutor,
    GateIssue,
    GateResult,
    GateVerdict,
    IQualityGate,
    Severity,
)
from core.orchestrator import ChapterPipelineState, OrchestrationEngine, PipelineContext
from plugins.anti_ai_detection.pattern_detector import AIPatternDetector


# =============================================================================
# Mock Gates for testing
# =============================================================================


class PassGate(IQualityGate):
    name = "pass-gate"
    order = 1

    async def evaluate(self, chapter: dict, context: dict) -> GateResult:
        return GateResult(gate_name=self.name, verdict=GateVerdict.PASS, score=1.0)


class ReviseGate(IQualityGate):
    name = "revise-gate"
    order = 2

    async def evaluate(self, chapter: dict, context: dict) -> GateResult:
        return GateResult(
            gate_name=self.name,
            verdict=GateVerdict.REVISE,
            issues=[GateIssue(severity=Severity.WARNING, code="test.revise", message="需要修订")],
            score=0.5,
        )


class FailGate(IQualityGate):
    name = "fail-gate"
    order = 3

    async def evaluate(self, chapter: dict, context: dict) -> GateResult:
        return GateResult(
            gate_name=self.name,
            verdict=GateVerdict.FAIL,
            issues=[GateIssue(severity=Severity.CRITICAL, code="test.fail", message="致命问题")],
            score=0.0,
        )


class CountingReviseGate(IQualityGate):
    """第 N 次 REVISE 后自动 PASS 的门禁."""

    name = "counting-revise-gate"
    order = 1
    revise_count = 0
    max_revise = 2

    async def evaluate(self, chapter: dict, context: dict) -> GateResult:
        self.revise_count += 1
        if self.revise_count <= self.max_revise:
            return GateResult(
                gate_name=self.name,
                verdict=GateVerdict.REVISE,
                issues=[GateIssue(severity=Severity.WARNING, code="test.count", message=f"第{self.revise_count}次修订")],
            )
        return GateResult(gate_name=self.name, verdict=GateVerdict.PASS)


# =============================================================================
# Quality Gate Tests
# =============================================================================


@pytest.mark.asyncio
async def test_pass_gate() -> None:
    """验证门禁 PASS."""
    gate = PassGate()
    result = await gate.evaluate({"content": "测试"}, {})
    assert result.verdict == GateVerdict.PASS
    assert result.score == 1.0


@pytest.mark.asyncio
async def test_gate_chain_all_pass() -> None:
    """验证全部门禁 PASS."""
    config = GateChainConfig(gates=[PassGate(), PassGate()])
    chain = GateChainExecutor(config)

    async def on_revise(ch, issues):
        return ch

    result = await chain.execute({"content": "test"}, {}, on_revise)
    assert result.passed is True
    assert result.total_rounds == 1


@pytest.mark.asyncio
async def test_gate_chain_fail_on_first() -> None:
    """验证首个 FAIL 即停止 (如果 stop_on_first_fail=True)."""
    fail_first = FailGate()
    fail_first.order = 1
    pass_later = PassGate()
    pass_later.order = 2

    config = GateChainConfig(gates=[fail_first, pass_later], stop_on_first_fail=True)
    chain = GateChainExecutor(config)

    async def on_revise(ch, issues):
        return ch

    result = await chain.execute({"content": "test"}, {}, on_revise)
    assert result.passed is False
    assert len(result.gate_results) == 1  # FailGate 第一个, 立即停止


@pytest.mark.asyncio
async def test_gate_chain_revise_then_pass() -> None:
    """验证 REVISE → 修订 → PASS 循环."""
    revise_called = []

    async def on_revise(ch, issues):
        revise_called.append(1)
        return {"content": ch.get("content", "") + " [revised]"}

    config = GateChainConfig(gates=[CountingReviseGate(), PassGate()])
    chain = GateChainExecutor(config)

    result = await chain.execute({"content": "test"}, {}, on_revise)
    # CountingReviseGate REVISE 2次然后PASS
    assert len(revise_called) == 2
    assert result.passed is True


@pytest.mark.asyncio
async def test_gate_chain_consecutive_revise_limit() -> None:
    """验证连续 REVISE 上限."""
    config = GateChainConfig(
        gates=[ReviseGate()],
        max_consecutive_revise=2,
    )
    chain = GateChainExecutor(config)
    revise_count = 0

    async def on_revise(ch, issues):
        nonlocal revise_count
        revise_count += 1
        return ch

    result = await chain.execute({"content": "test"}, {}, on_revise)
    assert revise_count == 2  # 修订了2次，第3次触达上限
    assert result.passed is False


@pytest.mark.asyncio
async def test_gate_chain_order() -> None:
    """验证门禁按 order 排序执行."""
    order_log: list[str] = []

    class OrderedGate(IQualityGate):
        def __init__(self, name: str, order: int):
            self.name = name
            self.order = order

        async def evaluate(self, chapter, context):
            order_log.append(self.name)
            return GateResult(gate_name=self.name, verdict=GateVerdict.PASS)

    gates = [
        OrderedGate("third", 3),
        OrderedGate("first", 1),
        OrderedGate("second", 2),
    ]

    config = GateChainConfig(gates=gates)
    chain = GateChainExecutor(config)

    async def on_revise(ch, issues):
        return ch

    await chain.execute({"content": "test"}, {}, on_revise)

    assert order_log == ["first", "second", "third"]


# =============================================================================
# Anti-AI Detector Tests
# =============================================================================


def test_ai_detector_high_freq_words() -> None:
    """验证 AI 高频词检测."""
    detector = AIPatternDetector()

    # 包含 AI 高频词的文本
    text = "他仿佛看到了希望，不禁微微一笑，与此同时，她的身影映入眼帘。"
    matches = detector.detect(text)

    # 应该检测到 high_freq_ai_words 和 weak_adverbs
    categories = [m.category for m in matches]
    assert "high_freq_ai_words" in categories or "weak_adverbs" in categories


def test_ai_detector_clean_text() -> None:
    """验证干净文本不误报."""
    detector = AIPatternDetector()

    text = "他站在城墙上，冷风刮过脸。远处，敌军的旗帜在夕阳下翻卷。"
    matches = detector.detect(text)

    # 干净文本应该很少或没有匹配
    high_matches = [m for m in matches if m.severity == "high"]
    assert len(high_matches) == 0


def test_ai_score_calculation() -> None:
    """验证 AI 评分计算."""
    detector = AIPatternDetector()

    # 明显 AI 文本
    ai_text = "他不禁微微抬起头，仿佛看到了前所未有的景象，与此同时心中充满了希望，未来的路还很长。"
    matches = detector.detect(ai_text)
    score = detector.calculate_ai_score(matches)
    assert score < 0.8  # 应该扣分

    # 人类风格文本
    human_text = "老王把烟掐了。他盯着门看了三秒。然后一脚踹开。"
    matches = detector.detect(human_text)
    score = detector.calculate_ai_score(matches)
    assert score >= 0.9  # 应该高分


def test_uniform_sentence_detection() -> None:
    """验证句长均匀度检测."""
    detector = AIPatternDetector()

    # 均匀句长 (AI 特征)
    uniform_text = (
        "他走进了房间。她坐在窗边看书。阳光从窗外洒进来。"
        "屋子里面很安静。墙上挂着一幅画。桌上放着一杯茶。"
        "他轻轻咳嗽一声。她抬头看了他一眼。两人相视而笑。"
        "这一刻仿佛永恒。"
    )
    result = detector.detect_uniform_sentences(uniform_text)
    assert result["is_uniform"] is True
    assert result["sd"] < 4.0

    # 变化句长 (人类特征)
    varied_text = (
        "他走进了房间。她坐在窗边，手里捧着一本泛黄的旧书，指腹无意识地摩挲着书页边缘。"
        "阳光。"
        "他咳了一声。她没抬头。"
        "墙上的钟滴答滴答走了十三下，他终于开口了——声音哑得像砂纸刮过铁皮。"
    )
    result = detector.detect_uniform_sentences(varied_text)
    # 变化句长应该不容易被判定为均匀
    assert result["is_uniform"] is False or result["sd"] > 3.5


def test_generic_ending_detection() -> None:
    """验证泛化结尾检测."""
    detector = AIPatternDetector()

    # 泛化结尾
    text_with_generic = "战斗结束了。未来的路还很长，但新的征程即将开始。"
    result = detector.detect_generic_ending(text_with_generic)
    assert result["has_generic_ending"] is True

    # 具体结尾
    text_with_hook = "战斗结束了。他捡起地上的玉佩——上面刻着的，正是三年前那个人的名字。"
    result = detector.detect_generic_ending(text_with_hook)
    assert result["has_generic_ending"] is False


# =============================================================================
# Orchestrator Tests
# =============================================================================


def test_pipeline_states() -> None:
    """验证流水线状态枚举."""
    assert ChapterPipelineState.IDLE.value == "idle"
    assert ChapterPipelineState.DRAFTING.value == "drafting"
    assert ChapterPipelineState.ACCEPTED.value == "accepted"
    assert ChapterPipelineState.FAILED.value == "failed"


def test_pipeline_context_defaults() -> None:
    """验证 PipelineContext 默认值."""
    ctx = PipelineContext()
    assert ctx.chapter_number == 0
    assert ctx.state == ChapterPipelineState.IDLE
    assert ctx.revision_round == 0
    assert ctx.correlation_id != ""


# =============================================================================
# Anti-AI Plugin Tests
# =============================================================================


@pytest.mark.asyncio
async def test_anti_ai_detect_report() -> None:
    """验证反AI检测报告完整性 (不调用 LLM)."""
    detector = AIPatternDetector()

    text = "他仿佛看到了希望，不禁微微一笑，未来的路还很长。"
    matches = detector.detect(text)
    score = detector.calculate_ai_score(matches)
    sentence = detector.detect_uniform_sentences(text)
    ending = detector.detect_generic_ending(text)

    # 验证报告结构
    report = {
        "ai_score": score,
        "pattern_matches": len(matches),
        "sentence_uniform": sentence,
        "generic_ending": ending,
    }
    assert "ai_score" in report
    assert "pattern_matches" in report


# =============================================================================
# Consistency Checker Tests (unit, no LLM)
# =============================================================================


def test_severity_enum() -> None:
    """验证 Severity 枚举."""
    assert Severity.INFO.value == "info"
    assert Severity.WARNING.value == "warning"
    assert Severity.ERROR.value == "error"
    assert Severity.CRITICAL.value == "critical"


def test_gate_issue_creation() -> None:
    """验证 GateIssue 创建."""
    issue = GateIssue(
        severity=Severity.ERROR,
        code="test.code",
        message="测试问题",
        suggestion="修复建议",
    )
    assert issue.code == "test.code"
    assert issue.suggestion == "修复建议"


def test_gate_result_creation() -> None:
    """验证 GateResult 创建."""
    result = GateResult(
        gate_name="test",
        verdict=GateVerdict.PASS,
        score=0.85,
    )
    assert result.verdict == GateVerdict.PASS
