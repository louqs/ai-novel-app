"""KnowledgeResolver 冒烟测试（无需 API Key）.

验证约定式自动发现对真实知识库目录生效：靶值槽解析、目录名直配、别名回退、
红线/靶值节抽取、缺失回退。
"""

from __future__ import annotations

import pytest

from core import knowledge_resolver as kr

pytestmark = pytest.mark.smoke


def test_resolve_genre_dirs_direct_match():
    # 目录名直配：AI科幻 目录真实存在
    assert "AI科幻" in kr.resolve_genre_dirs(["AI科幻"])


def test_resolve_genre_dirs_alias_fallback():
    # 别名回退：'科幻' → AI科幻
    assert kr.resolve_genre_dirs(["科幻"]) == ["AI科幻"]


def test_resolve_genre_dirs_unknown_returns_empty():
    assert kr.resolve_genre_dirs(["不存在的体裁xyz"]) == []
    assert kr.resolve_genre_dirs([]) == []
    assert kr.resolve_genre_dirs(None) == []


def test_genre_targets_parses_dialogue_ratio():
    # AI科幻 靶值.md 中 `对话比例` = 45%–65%
    targets = kr.genre_targets(["AI科幻"])
    assert "对话比例" in targets
    assert "45" in targets["对话比例"]
    assert "字数窗口" in targets


def test_threshold_hits_genre_then_default():
    # 命中体裁靶值
    val = kr.threshold("对话比例", ["AI科幻"], default="25%-55%")
    assert "45" in val
    # 未命中 key → 回退 default
    assert kr.threshold("不存在的key", ["AI科幻"], default="X") == "X"
    # 无体裁 → 回退 default
    assert kr.threshold("对话比例", [], default="25%-55%") == "25%-55%"


def test_genre_boundaries_returns_blocks():
    blocks = kr.genre_boundaries(["AI科幻"])
    assert blocks
    joined = "\n".join(blocks)
    assert "技术" in joined or "红线" in joined


def test_skill_layers_extracts_redlines_and_targets():
    layers = kr.skill_layers("通用-正文润色")
    # 正文润色已重构出 §A 红线 与 §C 靶值
    assert layers["redlines"]
    assert layers["targets"]
    # 不应把整份技法库塞进来——红线节不应含"画面感六步诊断"等技法标题正文
    assert len(layers["redlines"]) < len(
        (kr._SKILL_ROOT / "通用-正文润色" / "SKILL.md").read_text(encoding="utf-8")
    )


def test_skill_layers_missing_skill_returns_empty():
    layers = kr.skill_layers("不存在的技能xyz")
    assert layers == {"redlines": "", "targets": ""}


def test_genre_stage_prompt_creation():
    # AI科幻 有 .github/prompts/创建小说正文.prompt.md
    prompts = kr.genre_stage_prompt(["AI科幻"], "创建小说正文")
    assert prompts
    assert prompts[0]


def test_parse_range_variants():
    # 全角破折号 + 百分号
    assert kr.parse_range("45%–65%") == (45.0, 65.0)
    # 半角连字符
    assert kr.parse_range("30%-50%") == (30.0, 50.0)
    # 带单位
    assert kr.parse_range("2500–4500 CJK") == (2500.0, 4500.0)
    # 低高倒置自动纠正
    assert kr.parse_range("65–45") == (45.0, 65.0)
    # 不足两个数字 → None
    assert kr.parse_range("前300字") is None
    assert kr.parse_range("") is None
    assert kr.parse_range(None) is None


def test_ratio_range_genre_vs_default():
    # AI科幻 对话比例 45%–65%
    assert kr.ratio_range("对话比例", ["AI科幻"], default=(25.0, 100.0)) == (45.0, 65.0)
    # 无体裁 → 回退 default
    assert kr.ratio_range("对话比例", [], default=(25.0, 100.0)) == (25.0, 100.0)
    # 不存在的 key → 回退 default
    assert kr.ratio_range("不存在key", ["AI科幻"], default=(1.0, 2.0)) == (1.0, 2.0)
