"""质量评估器 — LLM 驱动 + 本地指标辅助.

双层评估：
  1. LLM 十维评审（主评分，理解语义和叙事质量）
  2. 本地规则检查（辅助，快速发现格式/模式问题）
"""

from __future__ import annotations

import json
import re
from typing import Any

from core.logging_config import get_logger
from core.plugin_manager import PluginManifest
from core.quality_gate import GateIssue, GateResult, GateVerdict, IQualityGate, Severity

logger = get_logger(__name__)

QUALITY_SYSTEM = """你是一位资深网文编辑，精通番茄小说、起点中文网、晋江文学城的创作标准。

## 十维评审体系

请从以下 10 个维度评估章节（每项 0-10 分）：

1. **开篇吸引力** — 前200字是否抓住读者？是否有冲突/悬念/动作？
2. **人物塑造深度** — 角色是否立体？对话是否有区分度？是否有通过行动而非标签展现性格？
3. **情节推进节奏** — 是否每300字有一次小推进？是否有无效描写/心理活动堆砌？
4. **情感共鸣强度** — 是否通过身体反应/细节传递情绪，而非直接说"他很伤心"？
5. **世界观完整性** — 设定是否自洽？是否有元话语（卷X、第X章）出戏？
6. **语言风格独特性** — 是否有AI口水词（缓缓、不由得、眼底闪过）？句式是否单一？
7. **爽点密度与分布** — 是否有可感知的爽点（谁+动词+谁+损失）？分布是否均匀？
8. **悬念与期待感** — 是否有伏笔/悬念让读者想继续读？
9. **章节结尾钩子** — 结尾是否有钩子（危机/反转/悬念）？是否有禁止结尾（岁月静好、未来可期）？
10. **主题深度与格局** — 是否有过度解释？是否让读者自己联想？

## 输出格式
以 JSON 返回，不要输出其他内容。"""


class QualityEvaluatorPlugin(IQualityGate):
    """质量评估器插件 — LLM 驱动十维评审."""

    name = "quality-evaluator"
    order = 50  # 在 anti-ai-detection(40) 之后

    def __init__(self) -> None:
        self._kernel = None

    async def on_load(self, kernel) -> None:
        self._kernel = kernel
        logger.info("质量评估器已加载")

    async def on_unload(self) -> None:
        self._kernel = None

    async def evaluate(self, chapter: dict[str, Any], context: dict[str, Any]) -> GateResult:
        """执行质量评估 — LLM 为主，本地指标为辅."""
        content = chapter.get("content", "")
        if not content:
            return GateResult(gate_name=self.name, verdict=GateVerdict.PASS, issues=[], score=1.0)

        issues: list[GateIssue] = []

        # ---- Layer 1: 本地快速检查（补充 LLM 可能遗漏的模式问题） ----
        local_issues = self._local_checks(content)
        issues.extend(local_issues)

        # ---- Layer 2: LLM 十维评审（主评分） ----
        llm_result = await self._llm_evaluate(content)
        llm_score = llm_result.get("avg_score", 7.0)
        dimension_scores = llm_result.get("dimensions", {})
        llm_issues = llm_result.get("issues", [])

        # 将 LLM 发现的问题转为 GateIssue
        for iss in llm_issues:
            if isinstance(iss, dict):
                severity_str = iss.get("severity", "warning")
                sev = {"critical": Severity.CRITICAL, "error": Severity.ERROR,
                       "warning": Severity.WARNING}.get(severity_str, Severity.INFO)
                issues.append(GateIssue(
                    severity=sev,
                    code=iss.get("code", "quality.llm"),
                    message=iss.get("message", ""),
                    suggestion=iss.get("suggestion"),
                ))

        # 合并本地 issues
        issues.extend(local_issues)

        # 判定（LLM 分数为主）
        min_score = 6.0
        try:
            min_score = self._kernel.get_config("chapter.ten_dimension_eval.min_score", 6.0)
        except Exception:
            pass
        verdict = GateVerdict.PASS if llm_score >= min_score else GateVerdict.REVISE

        return GateResult(
            gate_name=self.name,
            verdict=verdict,
            issues=issues,
            score=llm_score,
            metadata={"dimension_scores": dimension_scores, "local_issues_count": len(local_issues)},
        )

    # ------------------------------------------------------------------
    # LLM 评估
    # ------------------------------------------------------------------

    async def _llm_evaluate(self, content: str) -> dict:
        """LLM 十维评审."""
        prompt = f"""请评估以下网文章节（{len(content)}字）:

{content[:6000]}

以 JSON 返回:
```json
{{
  "avg_score": 0.0-10.0,
  "dimensions": {{
    "hook_strength": 0-10,
    "character_depth": 0-10,
    "pacing": 0-10,
    "emotional_resonance": 0-10,
    "world_coherence": 0-10,
    "style_uniqueness": 0-10,
    "payoff_density": 0-10,
    "suspense": 0-10,
    "chapter_hook": 0-10,
    "theme_depth": 0-10
  }},
  "issues": [
    {{"severity": "warning|error|critical", "code": "quality.xxx", "message": "问题描述", "suggestion": "修改建议"}}
  ],
  "strengths": ["亮点1", "亮点2"]
}}
```"""

        try:
            result = await self._kernel.call_llm(
                messages=[
                    {"role": "system", "content": QUALITY_SYSTEM},
                    {"role": "user", "content": prompt},
                ],
                tier="standard",
                max_tokens=2048,
                temperature=0.3,
            )
            return self._parse_json(result["content"])
        except Exception as exc:
            logger.warning("LLM 质量评估失败，降级为本地评估", error=str(exc))
            return self._fallback_score(content)

    # ------------------------------------------------------------------
    # 本地快速检查（补充性，不作为主评分）
    # ------------------------------------------------------------------

    def _local_checks(self, content: str) -> list[GateIssue]:
        """本地规则检查 — 发现 LLM 可能遗漏的模式问题."""
        issues = []

        # 元话语检查（严重）
        meta_words = ["卷一", "卷二", "第X章", "前文所述", "后文再表"]
        found_meta = [mw for mw in meta_words if mw in content]
        if found_meta:
            issues.append(GateIssue(
                severity=Severity.ERROR,
                code="quality.world.meta_language",
                message=f"正文中出现元话语：{', '.join(found_meta)}",
                suggestion="元话语是作者视角的文字，读者看到会立刻出戏。",
            ))

        # AI 口水词检查
        ai_words = ["缓缓", "不由得", "眼底闪过", "心中升起", "说不出的", "这意味着", "深邃", "不可置信"]
        found_ai = [w for w in ai_words if w in content]
        if found_ai:
            issues.append(GateIssue(
                severity=Severity.WARNING,
                code="quality.style.ai_words",
                message=f"发现AI口水词：{', '.join(found_ai)}",
                suggestion="删除或替换这些AI高频词。用具体动作替代抽象描述。",
            ))

        # 禁止结尾检查
        last_200 = content[-200:] if len(content) > 200 else content
        forbidden_endings = ["明天继续", "就这样", "结束了", "一切归于平静", "岁月静好", "未来可期", "充满希望"]
        found_forbidden = [e for e in forbidden_endings if e in last_200]
        if found_forbidden:
            issues.append(GateIssue(
                severity=Severity.ERROR,
                code="quality.ending.forbidden_ending",
                message=f"结尾使用了禁止的类型：{', '.join(found_forbidden)}",
                suggestion="结尾只写四种：危机突降、悬念反转、挑衅叫板、死亡倒计时。",
            ))

        # 过度解释检查
        explanation_phrases = ["这意味着", "这就是为什么", "原因在于"]
        found_explain = [p for p in explanation_phrases if p in content]
        if found_explain:
            issues.append(GateIssue(
                severity=Severity.INFO,
                code="quality.theme.over_explanation",
                message=f"发现过度解释：{', '.join(found_explain)}",
                suggestion="让读者自己把两件事联系起来，不要替读者写结论。",
            ))

        return issues

    def _fallback_score(self, content: str) -> dict:
        """LLM 失败时的降级评分."""
        score = 7.0
        # 简单启发式
        last_200 = content[-200:] if len(content) > 200 else content
        if any(e in last_200 for e in ["岁月静好", "未来可期"]):
            score -= 2.0
        if len(content) < 1500:
            score -= 1.0
        return {"avg_score": max(0, score), "dimensions": {}, "issues": [], "strengths": []}

    @staticmethod
    def _parse_json(content: str) -> dict:
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            m = re.search(r'```(?:json)?\s*([\s\S]*?)```', content)
            if m:
                try:
                    return json.loads(m.group(1))
                except json.JSONDecodeError:
                    pass
        return {}


# ---------------------------------------------------------------------------
# 工厂函数
# ---------------------------------------------------------------------------


def create_manifest() -> PluginManifest:
    return PluginManifest(
        name="quality-evaluator",
        version="0.2.0",
        description="质量评估器 — LLM 驱动十维评审",
        dependencies=[],
        hooks=["on_load", "on_unload"],
    )


def create_plugin() -> QualityEvaluatorPlugin:
    return QualityEvaluatorPlugin()
