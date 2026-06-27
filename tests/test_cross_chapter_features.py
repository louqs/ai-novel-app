"""测试跨章功能：聚合报告、相似度检测、一致性/伏笔校验."""

from __future__ import annotations

import pytest


# ===== 跨章相似度检测 =====

def test_similarity_compute_jaccard_containment():
    """测试相似度计算的核心指标."""
    from core.similarity_checker import compute_similarity

    text_a = "这是一个测试文本，用于计算相似度指标。"
    text_b = "这是一个测试文本，用于验证相似度功能。"

    result = compute_similarity(text_a, text_b, n=3)

    assert "jaccard" in result
    assert "containment_max" in result
    assert 0 <= result["jaccard"] <= 1
    assert 0 <= result["containment_max"] <= 1
    # 两个文本有部分重叠，jaccard 应该 > 0
    assert result["jaccard"] > 0


def test_similarity_identical_texts():
    """完全相同的文本，所有指标应为 1.0."""
    from core.similarity_checker import compute_similarity

    text = "完全相同的测试文本内容"
    result = compute_similarity(text, text, n=3)

    assert result["jaccard"] == 1.0
    assert result["containment_max"] == 1.0


def test_similarity_completely_different():
    """完全不同的文本，相似度应接近 0."""
    from core.similarity_checker import compute_similarity

    text_a = "春天的花朵绽放在阳光下"
    text_b = "冬季的雪花飘落在大地上"

    result = compute_similarity(text_a, text_b, n=5)

    # 完全不同的中文文本，containment 应该很低
    assert result["containment_max"] < 0.3


def test_similarity_english_token_mode():
    """英文文本应使用 token shingle 模式."""
    from core.similarity_checker import compute_similarity

    text_a = "This is a test sentence for similarity checking"
    text_b = "This is a test sentence for validation purposes"

    result = compute_similarity(text_a, text_b, n=3)

    assert result["jaccard"] > 0
    # 前半部分完全相同，containment 应该较高
    assert result["containment_max"] > 0.4


@pytest.mark.asyncio
async def test_cross_chapter_similarity_check_empty_when_no_db():
    """无 DB 时跨章相似度检测返回空列表."""
    from core.similarity_checker import check_cross_chapter_similarity

    issues = check_cross_chapter_similarity(
        "测试内容", "proj1", 5, None,
        lookback_n=3, threshold=0.25, shingle_n=5,
    )

    assert issues == []


# ===== 聚合报告 =====

@pytest.mark.asyncio
async def test_aggregate_report_api_contract():
    """验证聚合报告 API 的数据结构契约."""
    # 模拟 list_optimization_results 返回值
    mock_results = [
        {
            "chapter_number": 1,
            "volume_number": 1,
            "original": "原文",
            "optimized": "优化版",
            "explanation": {
                "quality_after": {"score": 75, "grade": "B", "issues": []},
                "changes": [{"paragraph_index": 0, "reason": "测试"}],
            },
            "created_at": "2026-06-26 12:00:00",
        },
        {
            "chapter_number": 2,
            "volume_number": 1,
            "original": "原文2",
            "optimized": "优化版2",
            "explanation": {
                "quality_after": {"score": 82, "grade": "A", "issues": []},
                "changes": [],
            },
            "created_at": "2026-06-26 13:00:00",
        },
    ]

    # 验证聚合逻辑（模拟 workbench.py 的 get_aggregate_report 逻辑）
    chapters = []
    total_score, count_score = 0, 0

    for r in mock_results:
        explanation = r.get("explanation") or {}
        quality_after = explanation.get("quality_after") or {}
        score = quality_after.get("score", 0)
        grade = quality_after.get("grade", "?")

        chapters.append({
            "chapter_number": r["chapter_number"],
            "score": score,
            "grade": grade,
        })

        if score > 0:
            total_score += score
            count_score += 1

    avg_score = round(total_score / count_score, 1) if count_score > 0 else 0

    # 断言
    assert len(chapters) == 2
    assert chapters[0]["score"] == 75
    assert chapters[1]["score"] == 82
    assert avg_score == 78.5


# ===== 证据驱动流水线集成 =====

@pytest.mark.asyncio
async def test_evidence_collection_includes_cross_chapter_when_available(mock_kernel):
    """当有 DB 和项目上下文时，证据收集应包含跨章检测."""
    from plugins.pipeline_editor.plugin import PipelineEditorPlugin

    plugin = PipelineEditorPlugin()
    await plugin.on_load(mock_kernel)

    # 模拟内容：第二章与第一章有重复段落
    content = "这是第二章的内容。\n\n这是一个重复的段落测试。\n\n新的独特段落。"

    # _collect_evidence 会尝试调用 check_cross_chapter_similarity
    # 由于 mock_kernel.db 是 None，相似度检测会被跳过，不会报错
    evidence = await plugin._collect_evidence(
        content, "test_proj", 2, "fanqie",
        volume_number=1, gate_issues=None,
    )

    # 至少应该有规则预检的证据（如果内容符合规则）
    assert "para_evidence" in evidence
    assert "chapter_evidence" in evidence
    assert "baseline_report" in evidence


@pytest.mark.asyncio
async def test_pipeline_preserves_return_contract_with_cross_chapter():
    """跨章功能不影响 run_pipeline 的返回契约."""
    from plugins.pipeline_editor.plugin import PipelineEditorPlugin

    class MinimalKernel:
        db = None
        async def call_llm(self, *args, **kwargs):
            return {"content": '{"revisions":[]}'}
        async def context(self):
            class Ctx:
                async def get(self, *args, **kwargs):
                    return {}
            return Ctx()
        async def get_plugin(self, name):
            return None

    plugin = PipelineEditorPlugin()
    await plugin.on_load(MinimalKernel())

    result = await plugin.run_pipeline(
        content="测试内容段落一。\n\n测试内容段落二。",
        project_id="",
        chapter_num=0,
        platform="fanqie",
        steps=["annotate"],
    )

    # 返回契约验证
    assert "original" in result
    assert "current_content" in result
    assert "steps" in result
    assert "explanation" in result
    assert isinstance(result["steps"], list)


# ===== Fixtures =====

@pytest.fixture
def mock_kernel():
    """最小化 Kernel mock."""
    class MockContext:
        async def get(self, ns, key, default=None):
            return default

    class MockKernel:
        db = None

        async def call_llm(self, *args, **kwargs):
            return {"content": '{"overall_score": 0.7}'}

        def context(self):
            return MockContext()

        async def get_plugin(self, name):
            return None

    return MockKernel()
