"""伏笔中文匹配器冒烟测试（无需 API Key）.

验证 foreshadow_text_match 对中文描述的对账能力——这是阶段1修复的核心
（旧版 split()[:3] 对中文无效、旧版 [一-鿿]{2,} 取整串难重叠）。
"""

from __future__ import annotations

import pytest

from models.foreshadow import foreshadow_text_match, _char_bigrams

pytestmark = pytest.mark.smoke


def test_bigrams_chinese():
    # 中文按字符切 2-gram
    bg = _char_bigrams("金手镯")
    assert "金手" in bg and "手镯" in bg


def test_match_same_foreshadow_different_wording():
    # 埋设与回收措辞不同但指向同一伏笔 → 应匹配
    plant = "林晚的玉佩会发出微光"
    payoff = "林晚的玉佩在祠堂里突然发出微光"
    assert foreshadow_text_match(plant, payoff)


def test_match_explicit_number():
    # 显式编号优先
    assert foreshadow_text_match("伏笔#3 神秘信件", "伏笔#3 在结局揭晓")
    assert not foreshadow_text_match("伏笔#1 信件", "伏笔#2 钥匙")


def test_no_match_unrelated():
    assert not foreshadow_text_match("林晚的玉佩有异样", "陈默的剑法师承之谜")


def test_empty_inputs():
    assert not foreshadow_text_match("", "林晚的玉佩")
    assert not foreshadow_text_match("林晚的玉佩", "")
    assert not foreshadow_text_match(None, None)  # type: ignore[arg-type]


def test_old_split_bug_regression():
    # 回归：旧逻辑 plant_desc.split()[:3] 对无空格中文返回整串，
    # 大纲埋设描述与正文提取描述共享核心名词短语时，旧逻辑漏配、新逻辑应命中。
    plant = "办公室的监控被人动过手脚"
    payoff = "办公室的监控被动过手脚"  # 核心短语保留，仅去掉"人"字
    assert foreshadow_text_match(plant, payoff)


def test_matcher_avoids_false_positive():
    # 阈值稳健性：仅共享个别常见字不应误配（宁漏勿误，漏配侧由传入计划回收描述兜底）
    assert not foreshadow_text_match("林晚的玉佩有异样反应", "陈默动过一次手脚")


def test_generation_service_delegates():
    # generation_service._foreshadow_matches_payoff 应复用统一匹配器
    from core.generation_service import GenerationService
    import inspect
    src = inspect.getsource(GenerationService._foreshadow_matches_payoff)
    assert "foreshadow_text_match" in src


# ---- 阶段3：活跃伏笔筛选与超期冒泡 ----

def _mk(fid, status="building", planted=1, building=None, priority=1):
    return {
        "foreshadow_id": fid, "description": f"伏笔{fid}", "status": status,
        "planted_chapter": planted, "building_chapters": building or [planted],
        "priority": priority,
    }


def test_rank_filters_paid_out():
    from models.foreshadow import rank_active_foreshadows
    entries = {"a": _mk("a", status="paid"), "b": _mk("b", status="planted")}
    out = rank_active_foreshadows(entries, current_chapter=5)
    ids = [f["foreshadow_id"] for f in out]
    assert "b" in ids and "a" not in ids  # paid 不注入


def test_rank_marks_overdue():
    from models.foreshadow import rank_active_foreshadows
    # 第30章，伏笔最后在第5章推进 → 间隔25 >= 20 → 超期
    entries = {"a": _mk("a", planted=5, building=[5])}
    out = rank_active_foreshadows(entries, current_chapter=30)
    assert out[0]["_overdue"] is True


def test_rank_top_n_but_keeps_overdue():
    from models.foreshadow import rank_active_foreshadows
    entries = {}
    # 10 个近期普通伏笔（第29章刚推进，不超期）
    for i in range(10):
        entries[f"n{i}"] = _mk(f"n{i}", planted=29, building=[29], priority=1)
    # 1 个超期高危伏笔（第2章后再没动）
    entries["old"] = _mk("old", planted=2, building=[2], priority=5)
    out = rank_active_foreshadows(entries, current_chapter=30, top_n=8)
    ids = [f["foreshadow_id"] for f in out]
    # 超期项必须在，且排最前
    assert "old" in ids
    assert out[0]["foreshadow_id"] == "old"
    assert out[0]["_overdue"] is True


def test_rank_priority_order():
    from models.foreshadow import rank_active_foreshadows
    entries = {
        "lo": _mk("lo", planted=28, building=[28], priority=1),
        "hi": _mk("hi", planted=28, building=[28], priority=5),
    }
    out = rank_active_foreshadows(entries, current_chapter=29)
    # 同样不超期时，高优先级在前
    assert out[0]["foreshadow_id"] == "hi"


# ---- 短篇适配：超期阈值随篇幅缩放 ----

def test_overdue_gap_by_length():
    from models.foreshadow import overdue_gap_for_length
    assert overdue_gap_for_length("short") == 4
    assert overdue_gap_for_length("medium") == 8
    assert overdue_gap_for_length("long") == 20
    assert overdue_gap_for_length(None) == 20  # 未知回退长篇


def test_resolver_overdue_gap_length_baseline():
    from core import knowledge_resolver as kr
    assert kr.overdue_gap("short", []) == 4
    assert kr.overdue_gap("long", []) == 20


def test_resolver_overdue_gap_genre_only_tightens():
    from core import knowledge_resolver as kr
    # 体裁靶值（若有）只能收紧，不能放松短篇的 4
    assert kr.overdue_gap("short", ["悬疑推理"]) <= 4


def test_short_story_overdue_actually_fires():
    # 核心回归：短篇里伏笔拖 5 章未推进，用篇幅阈值(4)应判超期；
    # 旧的写死 20 则永远不触发。
    from models.foreshadow import rank_active_foreshadows, overdue_gap_for_length
    entries = {"a": _mk("a", planted=2, building=[2])}
    gap = overdue_gap_for_length("short")  # 4
    out = rank_active_foreshadows(entries, current_chapter=7, overdue_gap=gap)
    assert out[0]["_overdue"] is True
    # 同样的伏笔，用旧的 20 阈值则不会超期
    out_old = rank_active_foreshadows(entries, current_chapter=7, overdue_gap=20)
    assert out_old[0]["_overdue"] is False


# ---- 记忆窗口随篇幅自适应（A档2处的底层函数）----

def test_memory_windows_by_length():
    from models.project import memory_windows
    assert memory_windows("short") == (3, 8)
    assert memory_windows("long") == (6, 20)      # 长篇维持原口径，存量项目行为不变
    assert memory_windows(None) == (6, 20)        # 未知回退长篇
    # 短篇摘要窗口应严格小于长篇（降噪生效）
    assert memory_windows("short")[0] < memory_windows("long")[0]
    assert memory_windows("short")[1] < memory_windows("long")[1]
