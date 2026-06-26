"""统一文本变换服务 — 链式执行去AI味、风格适配、降重等变换步骤.

每步复用已加载的插件实例，不重复创建。
支持单独调用或链式调用 transform(content, steps=["deai", "style"]).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from core.logging_config import get_logger

logger = get_logger(__name__)


@dataclass
class StepResult:
    """单步变换结果."""

    step: str
    input_text: str
    output_text: str
    changed: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "step": self.step,
            "changed": self.changed,
            "input_len": len(self.input_text),
            "output_len": len(self.output_text),
            "metadata": self.metadata,
        }


@dataclass
class TransformResult:
    """链式变换最终结果."""

    original: str
    final: str
    steps: list[StepResult] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "original_len": len(self.original),
            "final_len": len(self.final),
            "changed": self.original != self.final,
            "steps": [s.to_dict() for s in self.steps],
        }


# ---- 步骤注册表 ----
# 每个步骤: (plugin_name, method_name, description)
STEP_REGISTRY: dict[str, tuple[str, str, str]] = {
    "deai": ("anti-ai-detection", "humanize", "去AI味重写"),
    "adversarial": ("anti-ai-detection", "adversarial_rewrite", "对抗式多轮重写"),
    "style": ("style-adapter", "adapt_style", "平台风格适配"),
    "mobile": ("style-adapter", "mobile_optimize", "手机端格式优化"),
}


class TextTransformer:
    """统一文本变换服务."""

    def __init__(self, kernel: Any) -> None:
        self._kernel = kernel

    async def transform(
        self,
        content: str,
        steps: list[str],
        **kwargs: Any,
    ) -> TransformResult:
        """链式执行变换步骤.

        Args:
            content: 原始文本
            steps: 步骤列表，如 ["deai", "style"]
            **kwargs: 传递给各步骤的参数
                deai_mode: str = "standard"
                deai_novel_type: str = ""
                deai_target_word_count: int | None
                adversarial_iterations: int = 2
                style_platform: str = "fanqie"
                style_mode: str = "rewrite"
                detect_threshold: float = 0.2
        """
        result = TransformResult(original=content, final=content)
        current = content

        for step_name in steps:
            step_fn = self._get_step(step_name)
            if step_fn is None:
                logger.warning("未知变换步骤: %s, 跳过", step_name)
                continue

            step_result = await step_fn(current, **kwargs)
            result.steps.append(step_result)
            current = step_result.output_text

        result.final = current
        return result

    async def deai(
        self,
        content: str,
        *,
        mode: str = "standard",
        novel_type: str = "",
        target_word_count: int | None = None,
        **_kwargs: Any,
    ) -> StepResult:
        """去AI味 — 调用 AntiAIDetectionPlugin.humanize()."""
        entry = await self._kernel.get_plugin("anti-ai-detection")
        if not entry or not entry.instance:
            return StepResult(step="deai", input_text=content, output_text=content,
                              metadata={"error": "anti-ai-detection 插件未加载"})

        output = await entry.instance.humanize(
            content, mode=mode, novel_type=novel_type, target_word_count=target_word_count,
        )
        return StepResult(
            step="deai", input_text=content, output_text=output,
            changed=content != output,
            metadata={"mode": mode, "novel_type": novel_type},
        )

    async def adversarial(
        self,
        content: str,
        *,
        iterations: int = 2,
        **_kwargs: Any,
    ) -> StepResult:
        """对抗式重写 — 调用 AntiAIDetectionPlugin.adversarial_rewrite()."""
        entry = await self._kernel.get_plugin("anti-ai-detection")
        if not entry or not entry.instance:
            return StepResult(step="adversarial", input_text=content, output_text=content,
                              metadata={"error": "anti-ai-detection 插件未加载"})

        output = await entry.instance.adversarial_rewrite(content, iterations=iterations)
        return StepResult(
            step="adversarial", input_text=content, output_text=output,
            changed=content != output,
            metadata={"iterations": iterations},
        )

    async def style(
        self,
        content: str,
        *,
        platform: str = "fanqie",
        mode: str = "rewrite",
        chapter_title: str = "",
        **_kwargs: Any,
    ) -> StepResult:
        """平台风格适配 — 调用 StyleAdapterPlugin.adapt_style()."""
        entry = await self._kernel.get_plugin("style-adapter")
        if not entry or not entry.instance:
            return StepResult(step="style", input_text=content, output_text=content,
                              metadata={"error": "style-adapter 插件未加载"})

        output = await entry.instance.adapt_style(
            content, platform=platform, mode=mode, chapter_title=chapter_title,
        )
        return StepResult(
            step="style", input_text=content, output_text=output,
            changed=content != output,
            metadata={"platform": platform, "mode": mode},
        )

    async def mobile(
        self,
        content: str,
        **_kwargs: Any,
    ) -> StepResult:
        """手机端优化 — 调用 StyleAdapterPlugin.mobile_optimize()."""
        entry = await self._kernel.get_plugin("style-adapter")
        if not entry or not entry.instance:
            return StepResult(step="mobile", input_text=content, output_text=content,
                              metadata={"error": "style-adapter 插件未加载"})

        output = await entry.instance.mobile_optimize(content)
        return StepResult(
            step="mobile", input_text=content, output_text=output,
            changed=content != output,
        )

    async def detect_reduce(
        self,
        content: str,
        *,
        threshold: float = 0.2,
        mode: str = "standard",
        max_rounds: int = 2,
        **kwargs: Any,
    ) -> StepResult:
        """检测AI率 + 超标则降重（有界重试）.

        1. 调用 anti-ai-detection.detect() 获取 ai_score
        2. 如果 AI率 > threshold，调用 deai() 降重
        3. 降重后再次检测；仍超标则再降重，最多 max_rounds 轮
        """
        entry = await self._kernel.get_plugin("anti-ai-detection")
        if not entry or not entry.instance:
            return StepResult(step="detect_reduce", input_text=content, output_text=content,
                              metadata={"error": "anti-ai-detection 插件未加载"})

        detector = entry.instance
        detect_result = await detector.detect(content)
        human_score = detect_result.get("ai_score", 1.0)  # 人类度：越高越像人类
        ai_rate = round(1.0 - human_score, 3)             # AI率：越高越像AI

        if ai_rate <= threshold:
            return StepResult(
                step="detect_reduce", input_text=content, output_text=content,
                changed=False,
                metadata={"ai_score_before": ai_rate, "threshold": threshold,
                          "reduction_applied": False, "rounds": 0},
            )

        # 需要降重：逐轮重写，压到阈值下即停
        rounds = max(1, max_rounds)
        current = content
        ai_rate_cur = ai_rate
        rate_trace = [ai_rate]
        applied = 0

        for r in range(rounds):
            logger.info("第%d轮降重：AI率 %.1f%% 超过阈值 %.1f%%", r + 1, ai_rate_cur * 100, threshold * 100)
            deai_result = await self.deai(current, mode=mode, **kwargs)
            reduced = deai_result.output_text
            applied += 1

            after_detect = await detector.detect(reduced)
            ai_rate_after = round(1.0 - after_detect.get("ai_score", 1.0 - ai_rate_cur), 3)
            rate_trace.append(ai_rate_after)

            current = reduced
            ai_rate_cur = ai_rate_after
            if ai_rate_after <= threshold:
                break

        return StepResult(
            step="detect_reduce", input_text=content, output_text=current,
            changed=content != current,
            metadata={"ai_score_before": ai_rate, "ai_score_after": ai_rate_cur,
                      "threshold": threshold, "reduction_applied": current != content,
                      "rounds": applied, "rate_trace": rate_trace},
        )

    # ---- 内部 ----

    def _get_step(self, name: str) -> Any:
        """根据名称获取步骤方法."""
        method_map = {
            "deai": self.deai,
            "adversarial": self.adversarial,
            "style": self.style,
            "mobile": self.mobile,
            "detect_reduce": self.detect_reduce,
        }
        return method_map.get(name)
