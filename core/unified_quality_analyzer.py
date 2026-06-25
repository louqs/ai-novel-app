"""统一质量分析器 — 聚合写作教练、十维评审、AI检测为一份报告.

不实现 IQualityGate（不是质量门，是聚合服务）。
内部通过 kernel.get_plugin() 获取已加载的插件实例，不重复创建。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from core.logging_config import get_logger

logger = get_logger(__name__)

# ---- 评分权重 ----
WEIGHTS = {"writing": 0.35, "ai": 0.35, "dimensions": 0.30}

# ---- 等级映射 ----
GRADE_THRESHOLDS = [
    (0.90, "S"),
    (0.80, "A"),
    (0.65, "B"),
    (0.50, "C"),
    (0.00, "D"),
]


def _score_to_grade(score: float) -> str:
    for threshold, grade in GRADE_THRESHOLDS:
        if score >= threshold:
            return grade
    return "D"


def _gate_issue_to_dict(issue: Any) -> dict:
    """将 GateIssue dataclass 转为可序列化 dict."""
    if hasattr(issue, "__dict__"):
        return {
            "severity": getattr(issue, "severity", "info"),
            "code": getattr(issue, "code", ""),
            "message": getattr(issue, "message", ""),
            "location": getattr(issue, "location", None),
            "suggestion": getattr(issue, "suggestion", None),
        }
    if isinstance(issue, dict):
        return issue
    return {}


@dataclass
class UnifiedReport:
    """统一质量报告."""

    overall_score: float = 0.0
    grade: str = "D"
    writing_quality: dict = field(default_factory=dict)
    dimension_scores: dict = field(default_factory=dict)
    ai_detection: dict = field(default_factory=dict)
    issues: list[dict] = field(default_factory=list)
    strengths: list[str] = field(default_factory=list)
    suggestions: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "overall_score": round(self.overall_score, 3),
            "grade": self.grade,
            "weights": WEIGHTS,
            "writing_quality": self.writing_quality,
            "dimension_scores": self.dimension_scores,
            "ai_detection": self.ai_detection,
            "issues": self.issues,
            "strengths": self.strengths,
            "suggestions": self.suggestions,
        }


class UnifiedQualityAnalyzer:
    """统一质量分析器 — 并行调用三个插件，聚合为一份报告."""

    def __init__(self, kernel: Any) -> None:
        self._kernel = kernel

    async def analyze_chapter(
        self,
        content: str,
        *,
        platform: str = "fanqie",
        chapter_num: int = 0,
        project_id: str = "",
        genre_tags: list[str] | None = None,
    ) -> UnifiedReport:
        """分析章节 — 并行调用写作教练、十维评审、AI检测."""
        if not content.strip():
            return UnifiedReport(overall_score=1.0, grade="S")

        # 并行调用三个插件
        coach_result, evaluator_result, anti_ai_result = await asyncio.gather(
            self._call_coach(content, platform, chapter_num, genre_tags),
            self._call_evaluator(content),
            self._call_anti_ai(content),
            return_exceptions=True,
        )

        # ---- 处理写作教练结果 ----
        if isinstance(coach_result, Exception):
            logger.warning("写作教练调用失败", error=str(coach_result))
            writing_quality = {"score": 0.5, "metrics": {}, "suggestions": [], "strengths": []}
        else:
            writing_quality = coach_result

        # ---- 处理十维评审结果 ----
        if isinstance(evaluator_result, Exception):
            logger.warning("十维评审调用失败", error=str(evaluator_result))
            dimension_scores = {}
            dimensions_normalized = 0.5
            evaluator_issues = []
            evaluator_strengths = []
        else:
            dimension_scores = evaluator_result.get("dimension_scores", {})
            raw_score = evaluator_result.get("score", 5.0)
            dimensions_normalized = raw_score / 10.0 if raw_score > 1.0 else raw_score
            evaluator_issues = evaluator_result.get("issues", [])
            evaluator_strengths = evaluator_result.get("strengths", [])

        # ---- 处理 AI 检测结果 ----
        if isinstance(anti_ai_result, Exception):
            logger.warning("AI检测调用失败", error=str(anti_ai_result))
            ai_detection = {"ai_score": 0.5, "is_likely_ai": False, "total_issues": 0}
            ai_score = 0.5
            anti_ai_issues = []
        else:
            ai_detection = anti_ai_result
            ai_score = ai_detection.get("ai_score", 0.5)
            anti_ai_issues = ai_detection.get("issues", [])

        # ---- 加权综合分 ----
        writing_score = writing_quality.get("score", 0.5)
        overall = (
            writing_score * WEIGHTS["writing"]
            + ai_score * WEIGHTS["ai"]
            + dimensions_normalized * WEIGHTS["dimensions"]
        )
        overall = max(0.0, min(1.0, overall))

        # ---- 聚合 issues / strengths / suggestions ----
        all_issues = []
        for iss in evaluator_issues:
            d = _gate_issue_to_dict(iss)
            d.setdefault("source", "quality_evaluator")
            all_issues.append(d)
        for iss in anti_ai_issues:
            d = _gate_issue_to_dict(iss)
            d.setdefault("source", "anti_ai")
            all_issues.append(d)

        all_strengths = list(evaluator_strengths)
        if writing_quality.get("strengths"):
            all_strengths.extend(writing_quality["strengths"])

        all_suggestions = []
        for sug in writing_quality.get("suggestions", []):
            if isinstance(sug, dict):
                sug.setdefault("source", "writing_coach")
                all_suggestions.append(sug)
            else:
                all_suggestions.append({"source": "writing_coach", "message": str(sug)})

        return UnifiedReport(
            overall_score=round(overall, 3),
            grade=_score_to_grade(overall),
            writing_quality=writing_quality,
            dimension_scores=dimension_scores,
            ai_detection=ai_detection,
            issues=all_issues,
            strengths=list(set(all_strengths)),
            suggestions=all_suggestions,
        )

    async def analyze_text(
        self,
        content: str,
        *,
        platform: str = "fanqie",
        genre_tags: list[str] | None = None,
    ) -> UnifiedReport:
        """分析纯文本（不需要 project_id / chapter_num）."""
        return await self.analyze_chapter(content, platform=platform, genre_tags=genre_tags)

    # ------------------------------------------------------------------
    # 内部: 通过 kernel.get_plugin() 调用已加载的插件
    # ------------------------------------------------------------------

    async def _call_coach(self, content: str, platform: str, chapter_num: int,
                          genre_tags: list[str] | None = None) -> dict:
        entry = await self._kernel.get_plugin("writing-coach")
        if not entry or not entry.instance:
            raise RuntimeError("writing-coach 插件未加载")
        return await entry.instance.analyze_chapter(
            content, platform=platform, chapter_num=chapter_num, genre_tags=genre_tags,
        )

    async def _call_evaluator(self, content: str) -> dict:
        entry = await self._kernel.get_plugin("quality-evaluator")
        if not entry or not entry.instance:
            raise RuntimeError("quality-evaluator 插件未加载")
        result = await entry.instance.evaluate(
            {"content": content},
            {"settings": {}, "characters": {}, "facts": {}, "foreshadows": {}},
        )
        return {
            "score": result.score,
            "dimension_scores": result.metadata.get("dimension_scores", {}),
            "issues": [_gate_issue_to_dict(i) for i in result.issues],
            "strengths": result.metadata.get("strengths", []),
        }

    async def _call_anti_ai(self, content: str) -> dict:
        entry = await self._kernel.get_plugin("anti-ai-detection")
        if not entry or not entry.instance:
            raise RuntimeError("anti-ai-detection 插件未加载")
        # 只调用 detect() 一次，从输出重建 issues —— 避免 evaluate()+detect() 双重扫描
        detect_detail = await entry.instance.detect(content)
        detect_detail["issues"] = self._build_anti_ai_issues(detect_detail)
        return detect_detail

    @staticmethod
    def _build_anti_ai_issues(detect_detail: dict) -> list[dict]:
        """从 detect() 输出重建 GateIssue 格式的 issues 列表."""
        issues: list[dict] = []

        # 1. 高/中严重度模式匹配
        for pm in detect_detail.get("pattern_matches", []):
            sev = pm.get("severity", "low")
            if sev == "high":
                issues.append({
                    "severity": "error",
                    "code": f"anti_ai.{pm['category']}",
                    "message": f"检测到 AI 高频模式: {pm['category']} ({pm['count']}处)",
                    "suggestion": f"替换或删除以下词汇: {', '.join(pm.get('items', [])[:5])}",
                })
            elif sev == "medium" and len([i for i in issues if i.get("severity") == "warning"]) < 3:
                issues.append({
                    "severity": "warning",
                    "code": f"anti_ai.{pm['category']}",
                    "message": f"AI 模式: {pm['category']} ({pm['count']}处)",
                    "suggestion": f"建议处理: {', '.join(pm.get('items', [])[:3])}",
                })

        # 2. 句长均匀度
        su = detect_detail.get("sentence_uniformity", {})
        if su.get("is_uniform"):
            issues.append({
                "severity": "error",
                "code": "anti_ai.uniform_sentences",
                "message": f"句长过于均匀 (SD={su.get('sd', '?')})，是强 AI 信号",
                "suggestion": "刻意变化句长——插入短句打断均匀节奏",
            })

        # 3. 泛化结尾
        ge = detect_detail.get("generic_ending", {})
        if ge.get("has_generic_ending"):
            issues.append({
                "severity": "error",
                "code": "anti_ai.generic_ending",
                "message": f"章尾检测到泛化结尾: {ge.get('found', '')}",
                "suggestion": "替换为具体悬念或冲突",
            })

        # 4. De-AI 句式模板
        st = detect_detail.get("deai_sentence_templates", {})
        if st.get("is_excessive"):
            issues.append({
                "severity": "error",
                "code": "anti_ai.deai_sentence_templates",
                "message": f"De-AI句式模板: {st.get('suggestion', '')}",
                "suggestion": "删除模板句式，用具体事实和判断替代",
            })

        # 5. De-AI 语气态度
        ta = detect_detail.get("deai_tone_attitude", {})
        if ta.get("is_excessive"):
            issues.append({
                "severity": "warning",
                "code": "anti_ai.deai_tone",
                "message": f"De-AI语气问题: {ta.get('suggestion', '')}",
                "suggestion": "删除协作沟通痕迹和谄媚语气",
            })

        # 6. De-AI 硬阈值
        hc = detect_detail.get("deai_hard_constraints", {})
        if not hc.get("pass"):
            issues.append({
                "severity": "error",
                "code": "anti_ai.deai_hard_constraints",
                "message": f"De-AI硬阈值违反: {hc.get('suggestion', '')}",
                "suggestion": "按12项硬阈值修复",
            })

        # 7. 中文特化
        cn = detect_detail.get("chinese_specific", {})
        if cn.get("is_excessive"):
            issues.append({
                "severity": "error",
                "code": "anti_ai.chinese_specific",
                "message": f"中文特化问题: {cn.get('suggestion', '')}",
                "suggestion": "删除儿化音、翻译腔和虚假亲昵词",
            })

        # 8. 小说特化
        sp = detect_detail.get("story_patterns", {})
        if sp.get("is_excessive"):
            issues.append({
                "severity": "warning",
                "code": "anti_ai.story_patterns",
                "message": f"小说AI模式: {sp.get('suggestion', '')}",
                "suggestion": "用具体细节替代模板化描写，开头直接切入，结尾克制",
            })

        # 9. qu-ai-wei
        qa = detect_detail.get("quai_patterns", {})
        if qa.get("is_excessive"):
            issues.append({
                "severity": "warning",
                "code": "anti_ai.quai_patterns",
                "message": f"qu-ai-wei模式: {qa.get('suggestion', '')}",
                "suggestion": "删除AI高频词堆砌、翻译腔残留、的的不休等问题",
            })

        # 10. ximen 频率控制
        xm = detect_detail.get("ximen_patterns", {})
        if xm.get("is_excessive"):
            issues.append({
                "severity": "warning",
                "code": "anti_ai.ximen_patterns",
                "message": f"ximen频率控制: {xm.get('suggestion', '')}",
                "suggestion": "弱化副词≤2/千字、情感标签≤1/千字、比喻词≤1/千字",
            })

        return issues
