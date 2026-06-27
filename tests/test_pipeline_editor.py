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


@pytest.mark.asyncio
async def test_detect_reduce_is_paragraph_targeted_and_preserves_structure():
    """AI降重改为逐段定点：只重写命中段、保留段落结构、报告统计降重改动."""
    # 三段，段落数必须在降重后保持不变（不再全文盲重写打乱 \n\n 结构）
    para0 = "他心中涌起一股莫名的情绪，不禁感到一阵复杂的悸动，仿佛整个世界都安静了下来。"
    para1 = "桌上摆着一杯凉透的茶。"
    para2 = "随着夜幕降临，整座城市渐渐安静，街道空无一人，只剩路灯在风里摇晃。"
    content = para0 + "\n\n" + para1 + "\n\n" + para2

    # 假检测器：para0/para2 高 AI 率（命中），para1 干净；全文也超阈值
    class FakeDetector:
        async def detect(self, text):
            ai_heavy = ("心中涌起" in text) or ("夜幕降临" in text)
            human = 0.4 if ai_heavy else 0.9  # 人类度
            return {
                "ai_score": human,
                "pattern_matches": ([{"category": "情感标签化"}] if "心中涌起" in text
                                    else [{"category": "模板开头"}] if "夜幕降临" in text else []),
            }

    class _Entry:
        instance = FakeDetector()

    # LLM 只重写被点名的段（保持段内单段、长度相近）
    revised0 = "他说不上来是什么滋味，胸口闷了一下，屋里静得能听见自己呼吸。"
    revised2 = "天黑下来，城市一点点安静，街上没人，路灯在风里晃。"
    llm = ('{"revisions":[{"paragraph_index":0,"revised_text":"' + revised0 + '","applied_fixes":["情感标签化"]},'
           '{"paragraph_index":2,"revised_text":"' + revised2 + '","applied_fixes":["模板开头"]}]}')

    pe = _make_plugin(llm, [BASELINE, AFTER])

    async def _get_plugin(name):
        return _Entry()
    pe._kernel.get_plugin = _get_plugin

    step = await pe._detect_reduce(content, "fanqie", ai_threshold=0.2)

    # 结构保留：段落数不变
    assert step["optimized"].count("\n\n") == content.count("\n\n"), "降重不得改变段落数"
    assert step["reduction_applied"] is True
    # 干净的 para1 原样保留
    assert para1 in step["optimized"]
    # 命中段被改写
    assert revised0 in step["optimized"] and revised2 in step["optimized"]
    # 每段改动带证据
    assert len(step["changes"]) == 2
    for ch in step["changes"]:
        assert ch["evidence"], "降重改动必须带证据"
        assert ch["evidence"][0]["source"] == "AI检测降重"


@pytest.mark.asyncio
async def test_report_counts_reduce_changes_no_false_unchanged():
    """降重改了内容时，报告 n_changed 必须>0，不再出现'内容大变却说未改动'."""
    evidence = {
        "baseline_report": BASELINE,
        "_rewrite_changes": [],  # 定点改写阶段没改
        "_detect_changes": [{    # 但降重阶段改了一段
            "paragraph_index": 0, "before": "旧段", "after": "新段",
            "applied_fixes": ["情感标签化"],
            "evidence": [{"code": "anti_ai.detect", "source": "AI检测降重",
                          "message": "本段AI率 70%", "suggestion": "重写消除AI腔"}],
        }],
    }
    report = PipelineEditorPlugin()._build_grounded_report(
        evidence, AFTER, "原文", "优化后",
    )
    assert "未发现需修改段落" not in report["summary"], "降重改了内容不应说未改动"
    assert report["changes"], "应统计到降重改动"
    assert report["changes"][0]["evidence_source"] == "AI检测降重"


@pytest.mark.asyncio
async def test_detect_reduce_force_mode_for_zhuque():
    """force=True：外部判据(朱雀)判超标，本地全文率即便达标也逐段降重，且保结构."""
    para0 = "他心中涌起一股莫名的情绪，不禁感到一阵复杂的悸动，整个世界安静了下来。"
    para1 = "桌上一杯凉茶。"
    content = para0 + "\n\n" + para1

    class FakeDetector:
        async def detect(self, text):
            # 全文人类度高(0.85→AI率0.15达标)，但 para0 单段偏 AI
            if text == content:
                return {"ai_score": 0.85, "pattern_matches": []}
            heavy = "心中涌起" in text
            return {"ai_score": 0.5 if heavy else 0.9,
                    "pattern_matches": [{"category": "情感标签化"}] if heavy else []}

    class _Entry:
        instance = FakeDetector()

    revised0 = "他说不清心里是什么滋味，胸口闷了闷，屋里一下子静得能听见自己呼吸。"
    llm = '{"revisions":[{"paragraph_index":0,"revised_text":"' + revised0 + '","applied_fixes":["情感标签化"]}]}'
    pe = _make_plugin(llm, [BASELINE, AFTER])

    async def _get_plugin(name):
        return _Entry()
    pe._kernel.get_plugin = _get_plugin

    # 不 force：全文达标 → 跳过
    step_no = await pe._detect_reduce(content, "fanqie", ai_threshold=0.2, force=False)
    assert step_no["reduction_applied"] is False

    # force：即便全文达标也降重命中段，保留段落数
    step = await pe._detect_reduce(content, "fanqie", ai_threshold=0.2, force=True)
    assert step["reduction_applied"] is True
    assert step["optimized"].count("\n\n") == content.count("\n\n")
    assert revised0 in step["optimized"]
    assert para1 in step["optimized"]  # 干净段保留


# --------------------------------------------------------------------------
# A/B/C：让"优化说明里发现的问题"真正被改 / 诚实标注需人工
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_anchorable_chapter_evidence_becomes_paragraph_change():
    """A：可定点改写类型的章节级证据（AI腔/风格/伏笔）应被锚到段落、转为可改证据."""
    pe = PipelineEditorPlugin()
    paragraphs = [
        "开篇第一段，平平无奇地交代了背景。",
        "中间这一段比较长，承载了大量铺陈描写，是最长的一段内容所在之处啊啊啊。",
        "结尾留了个钩子。",
    ]
    # 无引文锚点、但属于可改类型（style）→ 应兜底锚到最长段
    ev_style = {"code": "style.flat", "source": "门禁:风格", "message": "整体风格偏平淡", "suggestion": ""}
    idx = pe._anchor_chapter_evidence(paragraphs, ev_style)
    assert idx == 1, f"风格类问题应锚到最长段(idx=1)，实际 {idx}"

    # 伏笔类 → 锚到末段
    ev_fs = {"code": "foreshadow.stale", "source": "伏笔管理", "message": "有伏笔超期未推进", "suggestion": ""}
    idx_fs = pe._anchor_chapter_evidence(paragraphs, ev_fs)
    assert idx_fs == 2, f"伏笔类问题应锚到末段(idx=2)，实际 {idx_fs}"


@pytest.mark.asyncio
async def test_structural_chapter_evidence_stays_manual():
    """A：纯结构性问题（一致性冲突，不在白名单）不硬锚，返回 None → 留给人工."""
    pe = PipelineEditorPlugin()
    paragraphs = ["段落一。", "段落二。"]
    ev_consistency = {
        "code": "consistency.location_conflict", "source": "一致性校验",
        "message": "人物位置矛盾：主角同时在两地", "suggestion": "核对时间线",
    }
    idx = pe._anchor_chapter_evidence(paragraphs, ev_consistency)
    assert idx is None, "一致性冲突不该被硬锚到某段，应留给人工"


@pytest.mark.asyncio
async def test_deep_polish_runs_even_with_existing_para_evidence():
    """B：已有段落级证据时，十维低分仍应合并进证据（不再被任意段落证据挡住）."""
    # 含模板开头 → 规则预检命中 para0，已产生 para_evidence
    content = "随着夜色四合，街道渐渐冷清了下来。\n\n他在长椅上坐了很久很久很久很久很久很久。"
    baseline = {
        "overall_score": 0.5, "grade": "C",
        "issues": [],  # 无 analyzer issues，低分维度才是重点
        "ai_detection": {"ai_score": 0.5},
        "dimension_scores": {"pacing": 3, "hook_strength": 8},  # pacing 低分
    }
    pe = _make_plugin('{"revisions":[]}', [baseline, baseline])
    ev = await pe._collect_evidence(content, "", 0, "fanqie")

    all_codes = [e["code"] for lst in ev["para_evidence"].values() for e in lst]
    # 既有规则证据（ai_taste），也应有十维低分证据（dimension.pacing）
    assert any(c.startswith("ai_taste") for c in all_codes), f"规则证据应在: {all_codes}"
    assert any(c.startswith("dimension.") for c in all_codes), \
        f"十维低分应仍被合并（B修复），实际: {all_codes}"


@pytest.mark.asyncio
async def test_report_separates_pending_manual_from_handled():
    """C：报告应把锚不上的章节级问题单列 pending_manual，且不混进 quality_before.issues."""
    evidence = {
        "baseline_report": {
            "overall_score": 0.55, "grade": "C",
            "issues": [
                {"message": "句长过于均匀"},      # 这条被改写处理
                {"message": "整章节奏偏慢"},      # 这条是结构性 → 需人工
            ],
            "ai_detection": {"ai_score": 0.45},
            "dimension_scores": {},
        },
        "chapter_evidence": [
            {"code": "consistency.pace", "source": "一致性校验",
             "message": "整章节奏偏慢", "suggestion": "压缩铺陈"},
        ],
        "_rewrite_changes": [{
            "paragraph_index": 0, "before": "旧", "after": "新",
            "applied_fixes": ["anti_ai.x"],
            "evidence": [{"code": "anti_ai.x", "source": "AI检测器",
                          "message": "句长过于均匀", "suggestion": ""}],
        }],
        "_detect_changes": [],
    }
    after = {"overall_score": 0.7, "grade": "B", "issues": [],
             "ai_detection": {"ai_score": 0.7}, "dimension_scores": {}}
    report = PipelineEditorPlugin()._build_grounded_report(evidence, after, "原文", "优化后")

    # 需人工清单含结构性问题
    pending_msgs = [p["message"] for p in report["pending_manual"]]
    assert "整章节奏偏慢" in pending_msgs, f"结构性问题应进需人工: {pending_msgs}"
    # quality_before.issues 不应再含已处理的、也不含需人工的（避免重复/假装没干活）
    before_issues = report["quality_before"]["issues"]
    assert "句长过于均匀" not in before_issues, "已被改写处理的问题不该再列在'其他检测问题'"
    assert "整章节奏偏慢" not in before_issues, "需人工的问题应在 pending_manual 而非'其他检测问题'"


@pytest.mark.asyncio
async def test_pending_manual_in_return_contract():
    """C：返回契约应新增 pending_manual 字段（前端依赖）."""
    pe = _make_plugin('{"revisions":[]}', [BASELINE, BASELINE])
    result = await pe.run_pipeline(CONTENT, steps=["coach", "detect"])
    assert "pending_manual" in result["explanation"]
    assert isinstance(result["explanation"]["pending_manual"], list)


# --------------------------------------------------------------------------
# 降重护栏放宽（40% vs 定点改写的 25%）
# --------------------------------------------------------------------------

def test_reduce_drift_limit_wider_than_rewrite():
    """降重护栏(40%)应比定点改写护栏(25%)更宽松，允许更大的字数变化."""
    # 构造一个 100 字的段落，改写后 140 字（漂移 40%）
    orig = "他走进房间。桌上放着一杯茶。窗外下着雨。" * 3  # ~45 字
    revised_ok = orig + "他没有说话，只是静静地看着窗外的雨滴打在玻璃上，发出细碎的声响。"  # ~85 字，漂移 ~89%

    paragraphs = [orig]
    allowed = {0: [{"code": "anti_ai.test", "source": "测试", "message": "测试", "suggestion": ""}]}
    revisions = [{"paragraph_index": 0, "revised_text": revised_ok, "applied_fixes": ["anti_ai.test"]}]

    # 默认 25% 护栏 → 拒绝
    _, changes_25, rejected_25 = PipelineEditorPlugin._apply_revisions(
        paragraphs, revisions, allowed, drift_limit=0.25,
    )
    assert rejected_25 == 1, "40% 漂移应被 25% 护栏拒绝"
    assert len(changes_25) == 0

    # 用一个刚好在 30% 漂移的版本：定点改写(25%)拒，降重(40%)接受
    revised_30 = orig + "他没说话。"  # ~55 字，漂移 ~22%
    # 换一个更精确的：让漂移刚好 ~30%
    orig_100 = "A" * 100  # 100 字
    revised_130 = "B" * 130  # 130 字，漂移 30%
    paragraphs_100 = [orig_100]
    allowed_100 = {0: [{"code": "x", "source": "", "message": "", "suggestion": ""}]}
    revisions_130 = [{"paragraph_index": 0, "revised_text": revised_130, "applied_fixes": ["x"]}]

    # 25% 护栏拒
    _, ch_25, rej_25 = PipelineEditorPlugin._apply_revisions(
        paragraphs_100, revisions_130, allowed_100, drift_limit=0.25,
    )
    assert rej_25 == 1 and len(ch_25) == 0, "30% 漂移应被 25% 护栏拒绝"

    # 40% 护栏接受
    _, ch_40, rej_40 = PipelineEditorPlugin._apply_revisions(
        paragraphs_100, revisions_130, allowed_100, drift_limit=0.40,
    )
    assert rej_40 == 0 and len(ch_40) == 1, "30% 漂移应被 40% 护栏接受"
    assert ch_40[0]["after"] == revised_130

