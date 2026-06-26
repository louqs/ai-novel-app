"""朱雀报告解析器 smoke 测试 — 纯正则，不依赖 LLM / PDF 库."""

from __future__ import annotations

import pytest

from core.zhuque_report_parser import parse_report_text

pytestmark = pytest.mark.smoke


# 仿真朱雀「打印为PDF」抽出的文本（含页眉页脚噪声、汇总表、片段标记）
SAMPLE = """朱雀AI生成检测报告单
检测时间：2026/6/26 11:16:38
检测结果
片段解析
NO. 1  片段1 AIGC值 0.9473
胡雨轩没开美颜。
镜头里，他左耳垂上那枚银杏叶耳钉，泛着旧铜色的光。
序号 片段 占全文比例 占字符数 AIGC值
1 片段1 94.34% 3250 0.9473
2 片段2 5.66% 195 0.9910
2026/6/26 11:17 朱雀 AI 生成检测报告单 _1782443799201
https://matrix.tencent.com/ai-detect/ai_gen 1/13
他点了开播。
NO. 2  片段2 AIGC值 0.9910
糖盒表面，浮起一层极淡的水汽。
水汽里，映出半轮月亮。
2026/6/26 11:17 朱雀 AI 生成检测报告单 _1782443799201
https://matrix.tencent.com/ai-detect/ai_gen 13/13
"""


def test_parse_basic_structure():
    """能解析出片段数、AIGC 值、整体加权 AI 率."""
    rep = parse_report_text(SAMPLE)
    assert rep.parse_ok
    assert rep.detect_time == "2026/6/26 11:16:38"
    assert len(rep.segments) == 2

    s1, s2 = rep.segments
    assert s1.index == 1 and abs(s1.aigc - 0.9473) < 1e-6
    assert s1.ratio == 94.34 and s1.chars == 3250
    assert s2.index == 2 and abs(s2.aigc - 0.9910) < 1e-6

    # 加权整体 = 0.9473*0.9434 + 0.9910*0.0566 ≈ 0.9498
    assert abs(rep.overall_ai_rate - 0.9498) < 0.005


def test_segment_text_extracted():
    """片段正文应抽出且不含页眉页脚噪声."""
    rep = parse_report_text(SAMPLE)
    s1 = rep.segments[0]
    assert "胡雨轩没开美颜" in s1.text
    assert "matrix.tencent.com" not in s1.text  # 噪声已剔除
    assert "报告单" not in s1.text


def test_all_segments_flagged_ai():
    """AIGC ≥ 0.5 的片段 is_ai=True."""
    rep = parse_report_text(SAMPLE)
    assert all(s.is_ai for s in rep.segments)
    assert rep.to_dict()["ai_segment_count"] == 2


def test_empty_or_garbage_returns_not_ok():
    """空文本/非报告文本 → parse_ok=False，调用方可回退."""
    assert parse_report_text("").parse_ok is False
    assert parse_report_text("这是一段普通文本，不是检测报告").parse_ok is False


def test_table_only_fallback():
    """只有汇总表没有片段正文标记时，仍能拿到整体率."""
    table_only = """序号 片段 占全文比例 占字符数 AIGC值
1 片段1 60.0% 1000 0.80
2 片段2 40.0% 600 0.90
"""
    rep = parse_report_text(table_only)
    assert rep.parse_ok
    assert len(rep.segments) == 2
    # 0.80*0.6 + 0.90*0.4 = 0.84
    assert abs(rep.overall_ai_rate - 0.84) < 1e-6
