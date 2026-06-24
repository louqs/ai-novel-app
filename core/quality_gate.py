"""质量门禁 — 责任链模式。

每道门禁检查章节并返回三种裁决: PASS / FAIL / REVISE。
编排引擎运行门禁链; REVISE 时送回修订; FAIL 超过重试上限时升级给用户。

用法:
    chain = GateChainExecutor(config)
    result = await chain.execute(chapter, context, on_revise=rewrite_fn)
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Coroutine

from core.logging_config import get_logger

logger = get_logger(__name__)


# =============================================================================
# 类型定义
# =============================================================================


class GateVerdict(str, Enum):
    PASS = "pass"
    FAIL = "fail"        # 不可恢复 → 升级
    REVISE = "revise"    # 可恢复 → 送回修订


class Severity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


@dataclass
class GateIssue:
    """门禁发现的问题."""

    severity: Severity
    code: str              # 机器可读, e.g. "consistency.timeline_gap"
    message: str           # 人类可读
    location: str | None = None
    suggestion: str | None = None


@dataclass
class GateResult:
    """单道门禁的检查结果."""

    gate_name: str
    verdict: GateVerdict
    issues: list[GateIssue] = field(default_factory=list)
    score: float = 1.0
    metadata: dict[str, Any] = field(default_factory=dict)


# =============================================================================
# 门禁接口
# =============================================================================


class IQualityGate(ABC):
    """单道门禁接口."""

    name: str
    order: int = 50  # 执行顺序, 越小越先

    @abstractmethod
    async def evaluate(self, chapter: dict[str, Any], context: dict[str, Any]) -> GateResult:
        """检查章节.

        Args:
            chapter: {"chapter_id": str, "content": str, "metadata": {...}}
            context: {"settings": ..., "characters": ..., "facts": ..., "foreshadows": ..., "previous_gate_results": [...]}

        Returns:
            GateResult with verdict and issues.
        """
        ...


class IPipelineContributor(ABC):
    """可向编辑优化流水线注入分析结果的插件接口.

    与 IQualityGate 不同，此接口用于"分析/建议"型插件（如写作技能提取、风格分析等），
    返回的分析结果会被注入到编辑优化的 prompt 中，辅助 LLM 做出更好的优化决策。
    """

    name: str
    order: int = 50  # 执行顺序，越小越先

    @abstractmethod
    async def analyze(self, content: str, context: dict[str, Any]) -> dict[str, Any]:
        """分析文本，返回结构化的分析结果.

        Args:
            content: 待分析的文本内容
            context: {"project_id": str, "chapter_num": int, "platform": str, "kernel": IKernelAPI, ...}

        Returns:
            {
                "summary": str,           # 分析总结（1-2句话）
                "issues": list[str],      # 发现的问题列表
                "suggestions": list[str], # 改进建议列表
                "score": float | None,    # 可选评分 0-1
            }
        """
        ...


# =============================================================================
# 门禁链配置与执行器
# =============================================================================


@dataclass
class GateChainConfig:
    gates: list[IQualityGate] = field(default_factory=list)
    max_revision_rounds: int = 3
    max_consecutive_revise: int = 2
    stop_on_first_fail: bool = True
    min_pass_score: float = 0.6


@dataclass
class GateChainResult:
    passed: bool
    gate_results: list[GateResult]
    total_rounds: int
    final_chapter: dict[str, Any] | None = None


class GateChainExecutor:
    """门禁链执行器 — 按 order 排序依次执行门禁.

    流程:
        1. 按 order 升序排列门禁
        2. 依次执行, 收集结果
        3. 任何门禁返回 REVISE → 调用 on_revise → 重跑全部门禁
        4. 任何门禁返回 FAIL → 如果 stop_on_first_fail=True 则立即停止
        5. 超过 max_revision_rounds → 失败
        6. 超过 max_consecutive_revise → 最后一道修订门禁转为 FAIL
    """

    def __init__(self, config: GateChainConfig) -> None:
        self._config = config
        # 按 order 排序
        self._gates = sorted(config.gates, key=lambda g: g.order)

    async def execute(
        self,
        chapter: dict[str, Any],
        context: dict[str, Any],
        on_revise: Callable[[dict[str, Any], list[GateResult]], Coroutine[Any, Any, dict[str, Any]]],
    ) -> GateChainResult:
        """执行门禁链.

        Args:
            chapter: 待检查章节.
            context: 完整上下文.
            on_revise: 修订回调 — 接收 (chapter, gate_results) → 返回修订后的 chapter.

        Returns:
            GateChainResult.
        """
        round_num = 0
        consecutive_revise = 0
        current_chapter = dict(chapter)
        all_round_results: list[GateResult] = []

        while round_num < self._config.max_revision_rounds:
            round_num += 1
            round_results: list[GateResult] = []

            context["previous_gate_results"] = round_results

            for gate in self._gates:
                logger.debug(
                    "门禁检查中",
                    gate=gate.name,
                    round=round_num,
                    chapter=current_chapter.get("chapter_id", "?"),
                )
                result = await gate.evaluate(current_chapter, context)
                round_results.append(result)

                logger.info(
                    "门禁结果",
                    gate=gate.name,
                    verdict=result.verdict.value,
                    score=result.score,
                    issues=len(result.issues),
                )

                if result.verdict == GateVerdict.FAIL:
                    if self._config.stop_on_first_fail:
                        all_round_results.extend(round_results)
                        return GateChainResult(
                            passed=False,
                            gate_results=all_round_results,
                            total_rounds=round_num,
                            final_chapter=current_chapter,
                        )

                elif result.verdict == GateVerdict.REVISE:
                    consecutive_revise += 1
                    if consecutive_revise > self._config.max_consecutive_revise:
                        # 连续修订超限, 当前 REVISE 升级为 FAIL
                        result.verdict = GateVerdict.FAIL
                        result.issues.append(GateIssue(
                            severity=Severity.ERROR,
                            code="gate.max_consecutive_revise",
                            message=f"连续修订超过上限 ({self._config.max_consecutive_revise}次)",
                            suggestion="请人工审核并手动修订",
                        ))
                        all_round_results.extend(round_results)
                        return GateChainResult(
                            passed=False,
                            gate_results=all_round_results,
                            total_rounds=round_num,
                            final_chapter=current_chapter,
                        )

                    # 收集修订建议
                    revise_issues = [
                        r for r in round_results
                        if r.verdict in (GateVerdict.REVISE, GateVerdict.FAIL)
                    ]
                    # 调用修订回调
                    current_chapter = await on_revise(current_chapter, revise_issues)
                    # 中断当前轮, 重新开始
                    break

            else:
                # 全部 PASS — 成功!
                all_round_results.extend(round_results)
                # 计算综合评分
                avg_score = sum(r.score for r in round_results) / max(len(round_results), 1)
                return GateChainResult(
                    passed=avg_score >= self._config.min_pass_score,
                    gate_results=all_round_results,
                    total_rounds=round_num,
                    final_chapter=current_chapter,
                )

            all_round_results.extend(round_results)

        # 超过最大轮次
        return GateChainResult(
            passed=False,
            gate_results=all_round_results,
            total_rounds=round_num,
            final_chapter=current_chapter,
        )
