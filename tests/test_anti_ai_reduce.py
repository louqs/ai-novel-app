"""本地降重链路回归测试.

锁定三个曾经的缺陷，防止再退化：
1. plugin.humanize 不再因把正文当 matches 传入而 AttributeError 崩溃
2. build_diagnosis_report 能读到 match_dicts(带 severity/count) 与数值化 detection_details
   —— 不再恒输出"未发现严重问题"而退化成盲重写
3. text_transformer.detect_reduce 有界重试：压到阈值下即停，达标则不调用 LLM
"""

from __future__ import annotations

import contextlib

import pytest

from core.text_transformer import TextTransformer
from plugins.anti_ai_detection.humanization_engine import build_diagnosis_report
from plugins.anti_ai_detection.plugin import AntiAIDetectionPlugin

pytestmark = pytest.mark.smoke


_AI_HEAVY = (
    "他心里一沉，心头一震，心中暗道不好。她笑了笑，点了点头，又笑了笑。"
    "映入眼帘的是一片狼藉，心中涌起一股莫名的情绪。不禁让人感慨，"
    "与此同时，他缓缓地、轻轻地、淡淡地说道。" * 6
)


class _FakeKernel:
    """记录 call_llm 调用次数，返回固定改写文本."""

    def __init__(self) -> None:
        self.llm_calls = 0

    async def call_llm(self, **_kw):
        self.llm_calls += 1
        return {"content": "院门吱呀推开，灶房飘出苞谷糊糊的焦香。他蹲下身，手在裤腿上蹭了蹭。"}


# --------------------------------------------------------------------------
# Fix 1: humanize 不再崩溃
# --------------------------------------------------------------------------

async def test_humanize_does_not_crash_on_ai_heavy_text():
    p = AntiAIDetectionPlugin()
    await p.on_load(_FakeKernel())

    # detect 此前正常；humanize 此前在 calculate_ai_score(content) 处 AttributeError
    detect = await p.detect(_AI_HEAVY)
    assert "ai_score" in detect

    out = await p.humanize(_AI_HEAVY, mode="unified")
    assert isinstance(out, str) and out


# --------------------------------------------------------------------------
# Fix 2: 诊断报告不再恒空
# --------------------------------------------------------------------------

def test_diagnosis_report_populated_from_dict_patterns():
    patterns = [
        {"category": "ai_psychology", "severity": "high", "count": 9,
         "matched_items": ["心里一沉", "心头一震"]},
        {"category": "template_words", "severity": "medium", "count": 4,
         "matched_items": ["笑了笑"]},
    ]
    details = {"sentence_uniformity": 1.8, "paragraph_uniformity": 30, "dialogue_ratio": 0.05}
    report = build_diagnosis_report(0.45, patterns, details)

    assert "ai_psychology" in report
    assert "template_words" in report
    assert "句式均匀度" in report
    assert "段落均匀度" in report
    assert "对话比例" in report
    assert "未发现严重问题" not in report


def test_diagnosis_report_empty_evidence_falls_back():
    report = build_diagnosis_report(0.9, [], {})
    assert "未发现严重问题" in report


# --------------------------------------------------------------------------
# Fix 3: detect_reduce 有界重试
# --------------------------------------------------------------------------

class _SeqDetector:
    """按序列返回人类度分数."""

    def __init__(self, human_scores: list[float]) -> None:
        self._it = iter(human_scores)
        self._last = human_scores[-1]

    async def detect(self, _text):
        with contextlib.suppress(StopIteration):
            self._last = next(self._it)
        return {"ai_score": self._last}

    async def humanize(self, content, **_kw):
        return content + "·改"


class _Entry:
    def __init__(self, inst) -> None:
        self.instance = inst


class _PluginKernel:
    def __init__(self, det) -> None:
        self._det = det

    async def get_plugin(self, _name):
        return _Entry(self._det)


async def test_detect_reduce_retries_until_below_threshold():
    # 人类度 0.3 -> AI率0.7；0.5 -> 0.5；0.85 -> 0.15(达标)
    det = _SeqDetector([0.3, 0.5, 0.85])
    t = TextTransformer(_PluginKernel(det))
    r = await t.detect_reduce("原文", threshold=0.2, mode="unified", max_rounds=3)
    m = r.metadata

    assert m["ai_score_before"] == 0.7
    assert m["ai_score_after"] == 0.15
    assert m["ai_score_after"] <= 0.2
    assert m["rounds"] == 2  # 第2轮达标即停，不跑满3轮
    assert m["reduction_applied"] is True
    assert m["rate_trace"] == [0.7, 0.5, 0.15]


async def test_detect_reduce_skips_when_already_clean():
    det = _SeqDetector([0.9])  # 人类度0.9 -> AI率0.1，已达标
    t = TextTransformer(_PluginKernel(det))
    r = await t.detect_reduce("原文", threshold=0.2)
    m = r.metadata

    assert m["reduction_applied"] is False
    assert m["rounds"] == 0
    assert r.changed is False


async def test_detect_reduce_stops_at_max_rounds_when_stubborn():
    # 始终不达标，应跑满 max_rounds 轮后停
    det = _SeqDetector([0.3, 0.35, 0.4])  # AI率 0.7/0.65/0.6 都超阈值
    t = TextTransformer(_PluginKernel(det))
    r = await t.detect_reduce("原文", threshold=0.2, max_rounds=2)
    m = r.metadata

    assert m["rounds"] == 2
    assert m["ai_score_after"] > 0.2  # 仍超标，但不无限重试
