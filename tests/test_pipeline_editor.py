"""证据驱动编辑优化流水线 smoke 测试.

验证：
1. 报告分数源自分析器实测值（非 LLM 编造）
2. LLM 返回截断/解析失败时保留原文不报错
3. 改写护栏拒绝超长（>±25%）段落
4. 返回契约字段齐全
5. skill builder 类插件（IQualityGate / IPipelineContributor）的反馈进入证据
"""

from __future__ import annotations

import pytest

from plugins.pipeline_editor.plugin import PipelineEditorPlugin

pytestmark = pytest.mark.smoke


# --------------------------------------------------------------------------
# Mock 基础设施
# --------------------------------------------------------------------------

class _FakeReport:
    def __init__(self, d: dict) -> None:
        self._d = d

    def to_dict(self) -> dict:
        return self._d


class _FakeAnalyzer:
    """按调用顺序返回预设报告（首次=基线，之后=优化后）."""

    def __init__(self, reports: list[dict]) -> None:
        self._reports = reports
        self.calls = 0

    async def analyze_text(self, content: str, *, platform: str = "", genre_tags=None):
        idx = min(self.calls, len(self._reports) - 1)
        self.calls += 1
        return _FakeReport(self._reports[idx])


class _FakeKernel:
    """最小 kernel：无 db、无插件链，call_llm 返回预设响应."""

    db = None

    def __init__(self, llm_content: str) -> None:
        self._llm_content = llm_content
        self._plugin_manager = self  # list_active 走自身

    async def list_active(self):
        return []  # 无门禁/贡献者插件

    def context(self):
        return self

    async def get(self, ns, key, default=None):
        return default

    async def call_llm(self, messages, **kwargs):
        return {"content": self._llm_content}


def _make_plugin(llm_content: str, reports: list[dict]) -> PipelineEditorPlugin:
    pe = PipelineEditorPlugin()
    pe._kernel = _FakeKernel(llm_content)
    pe._analyzer = _FakeAnalyzer(reports)
    return pe


BASELINE = {
    "overall_score": 0.55, "grade": "C",
    "issues": [{"message": "句长过于均匀", "source": "anti_ai",
                "suggestion": "刻意变化句长"}],
    "ai_detection": {"ai_score": 0.45},
    "dimension_scores": {"pacing": 5},
}
AFTER = {
    "overall_score": 0.75, "grade": "B",
    "issues": [], "ai_detection": {"ai_score": 0.8},
    "dimension_scores": {"pacing": 8},
}

# 含模板开头的正文 → 规则预检必命中，保证有 para_evidence
CONTENT = "随着夜幕降临，整座城市渐渐安静了下来，街道空无一人。\n\n他站在窗前，望着远方。"


# --------------------------------------------------------------------------
# 测试
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_report_scores_from_measurement_not_llm():
    """报告分数必须等于分析器实测 overall_score，而非 LLM 编的数字."""
    # LLM 给出一段合法改写
    revised = "夜幕压下来，城市一下子静了，街上一个人都没有。"
    llm = '{"revisions":[{"paragraph_index":0,"revised_text":"' + revised + '","applied_fixes":["ai_taste.medium"]}]}'
    pe = _make_plugin(llm, [BASELINE, AFTER])
    result = await pe.run_pipeline(CONTENT, steps=["coach"])

    exp = result["explanation"]
    assert exp["grounded"] is True
    assert exp["quality_before"]["score"] == 55  # == BASELINE overall*100
    assert exp["quality_after"]["score"] == 75    # == AFTER overall*100
    assert exp["quality_before"]["grade"] == "C"
    assert exp["quality_after"]["grade"] == "B"
    # 报告 changes 必须带 paragraph_index（前端列对齐 + 内联批注依赖）
    assert exp["changes"], "应有修改记录"
    ch0 = exp["changes"][0]
    assert ch0["paragraph_index"] == 0
    assert ch0["reason"]
    assert "evidence" in ch0 and isinstance(ch0["evidence"], list)


@pytest.mark.asyncio
async def test_deep_polish_when_no_para_evidence_but_low_dimensions():
    """无段落级证据、但十维有低分项 → 深度精修兜底应挑段挂证据并触发改写."""
    # 一段不会触发规则预检的"干净"文本（无模板开头、无情感标签、不超长）
    clean = "他走到门口，停下脚步。\n\n外面的雨还在下，他没有打伞的打算。"
    # 基线：无 issues（不产生段落证据），但 pacing 低分
    baseline_clean = {
        "overall_score": 0.6, "grade": "B", "issues": [],
        "ai_detection": {"ai_score": 0.7},
        "dimension_scores": {"pacing": 4, "hook_strength": 8},
    }
    after_clean = {
        "overall_score": 0.68, "grade": "B", "issues": [],
        "ai_detection": {"ai_score": 0.7},
        "dimension_scores": {"pacing": 6, "hook_strength": 8},
    }
    revised = "他走到门口，硬生生顿住——雨声在耳边砸开，他偏不打伞。"
    llm = '{"revisions":[{"paragraph_index":0,"revised_text":"' + revised + '","applied_fixes":["dimension.pacing"]}]}'
    pe = _make_plugin(llm, [baseline_clean, after_clean])

    ev = await pe._collect_evidence(clean, "", 0, "fanqie")
    # 兜底应给至少一段挂上 dimension 证据
    assert ev["para_evidence"], "深度精修兜底未挂任何段落证据"
    all_codes = [e["code"] for lst in ev["para_evidence"].values() for e in lst]
    assert any(c.startswith("dimension.") for c in all_codes), \
        f"兜底证据应来自十维低分项: {all_codes}"


@pytest.mark.asyncio
async def test_truncated_json_keeps_original():
    """LLM 返回截断/无法解析 → 保留原文，不抛异常."""
    pe = _make_plugin("{ this is broken json ...", [BASELINE, BASELINE])
    result = await pe.run_pipeline(CONTENT, steps=["coach"])
    assert result["current_content"] == CONTENT  # 原文不变
    assert "explanation" in result


@pytest.mark.asyncio
async def test_guardrail_rejects_oversized_rewrite():
    """改写后长度漂移 >25% 的段落应被护栏拒绝，保留原文该段."""
    para0 = CONTENT.split("\n\n")[0]
    huge = para0 + "这是一段被大幅扩写的内容" * 20  # 远超 +25%
    llm = '{"revisions":[{"paragraph_index":0,"revised_text":"' + huge + '","applied_fixes":["x"]}]}'
    pe = _make_plugin(llm, [BASELINE, BASELINE])
    result = await pe.run_pipeline(CONTENT, steps=["coach"])
    # 段落 0 被拒 → 内容仍为原文
    assert result["current_content"] == CONTENT


@pytest.mark.asyncio
async def test_return_contract_complete():
    """返回契约字段齐全：original / current_content / steps / explanation."""
    llm = '{"revisions":[]}'
    pe = _make_plugin(llm, [BASELINE, BASELINE])
    result = await pe.run_pipeline(CONTENT, steps=["coach", "detect"])
    for key in ("original", "current_content", "steps", "explanation"):
        assert key in result
    assert isinstance(result["steps"], list) and result["steps"]
    exp = result["explanation"]
    for key in ("summary", "quality_before", "quality_after", "changes",
                "comparison", "recommendation"):
        assert key in exp


@pytest.mark.asyncio
async def test_skill_builder_gate_feedback_enters_evidence():
    """IQualityGate 插件（含 skill builder 创建的）的 issue 应进入证据."""
    from core.quality_gate import (
        GateIssue,
        GateResult,
        GateVerdict,
        IQualityGate,
        Severity,
    )

    class _Entry:
        def __init__(self, inst):
            self.instance = inst

    class FakeGate(IQualityGate):
        name = "battle-vividness-checker"
        order = 50

        async def evaluate(self, chapter, context):
            return GateResult(
                gate_name=self.name, verdict=GateVerdict.REVISE,
                issues=[GateIssue(severity=Severity.WARNING,
                                  code="battle_vividness.flat",
                                  message="战斗场面描写较为平淡")],
                score=0.5,
            )

    pe = _make_plugin('{"revisions":[]}', [BASELINE, BASELINE])
    gate = FakeGate()

    async def _list_active():
        return [_Entry(gate)]
    pe._kernel.list_active = _list_active

    ev = await pe._collect_evidence(CONTENT, "", 0, "fanqie")
    all_codes = [e["code"] for e in ev["chapter_evidence"]]
    for lst in ev["para_evidence"].values():
        all_codes += [e["code"] for e in lst]
    assert any("battle_vividness" in c for c in all_codes), \
        f"门禁反馈未进入证据: {all_codes}"
