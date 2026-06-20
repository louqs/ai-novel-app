"""AI 模式检测器 — 检测文本中的 AI 写作痕迹。

基于 10 类 AI 模式特征库进行规则 + LLM 混合检测。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from core.logging_config import get_logger

logger = get_logger(__name__)


@dataclass
class AIPatternMatch:
    """AI 模式匹配结果."""

    category: str
    severity: str  # low, medium, high
    pattern_name: str
    matched_items: list[str] = field(default_factory=list)
    count: int = 0
    threshold: int | None = None


class AIPatternDetector:
    """AI 写作模式检测器 — 基于规则的高效检测.

    不需要 LLM 调用，纯文本分析。
    """

    def __init__(self, patterns_path: str | Path | None = None) -> None:
        self._patterns: dict[str, Any] = {}
        self._compiled: dict[str, list[re.Pattern]] = {}

        if patterns_path is None:
            patterns_path = Path("knowledge_base/anti_ai_patterns/patterns.yaml")

        if Path(patterns_path).exists():
            self._load_patterns(Path(patterns_path))

    def _load_patterns(self, path: Path) -> None:
        """加载 AI 模式特征库."""
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        self._patterns = data.get("patterns", {})

        # 预编译正则
        for name, pattern in self._patterns.items():
            words = pattern.get("words", [])
            regex_patterns = pattern.get("patterns", [])
            compiled: list[re.Pattern] = []

            for word in words:
                compiled.append(re.compile(re.escape(word)))
            for pat in regex_patterns:
                compiled.append(re.compile(pat))

            if compiled:
                self._compiled[name] = compiled

        logger.info("AI 模式特征库已加载", patterns=len(self._patterns))

    def detect(self, text: str) -> list[AIPatternMatch]:
        """检测文本中的所有 AI 模式.

        Args:
            text: 待检测文本.

        Returns:
            AI 模式匹配列表.
        """
        results: list[AIPatternMatch] = []
        char_count = max(len(text), 1)
        per_1000 = lambda n: n / (char_count / 1000)

        for name, pattern in self._patterns.items():
            severity = pattern.get("severity", "medium")
            threshold_1k = pattern.get("threshold", "")

            # 尝试正则匹配
            matched_items: list[str] = []
            if name in self._compiled:
                for pat in self._compiled[name]:
                    for match in pat.finditer(text):
                        matched_items.append(match.group())

            # 尝试关键词匹配 (words)
            words = pattern.get("words", [])
            for word in words:
                count = text.count(word)
                for _ in range(count):
                    matched_items.append(word)

            count = len(matched_items)
            if count == 0:
                continue

            # 阈值判断
            threshold = None
            if threshold_1k and "_per_1000_chars" in threshold_1k:
                threshold = int(threshold_1k.split("_")[0])
                if per_1000(count) < threshold:
                    continue

            results.append(
                AIPatternMatch(
                    category=name,
                    severity=severity,
                    pattern_name=name,
                    matched_items=list(set(matched_items)),
                    count=count,
                    threshold=threshold,
                )
            )

        return results

    def calculate_ai_score(self, matches: list[AIPatternMatch]) -> float:
        """根据模式匹配计算 AI 综合评分 (0-1, 越低越像 AI)."""
        if not matches:
            return 1.0

        severity_weights = {"low": 0.05, "medium": 0.1, "high": 0.2}
        score = 1.0
        for match in matches:
            penalty = severity_weights.get(match.severity, 0.1) * min(match.count, 5)
            score -= penalty

        return max(0.0, score)

    def detect_uniform_sentences(self, text: str) -> dict[str, Any]:
        """检测句长均匀度 (突发度分析).

        Returns:
            {"sd": float, "is_uniform": bool, "suggestion": str}
        """
        # 按句号、感叹号、问号分句
        sentences = re.split(r"[。！？.!?\n]", text)
        sentences = [s.strip() for s in sentences if s.strip() and len(s.strip()) > 2]

        if len(sentences) < 10:
            return {"sd": 0, "is_uniform": False, "suggestion": ""}

        lengths = [len(s) for s in sentences]
        mean_len = sum(lengths) / len(lengths)
        variance = sum((l - mean_len) ** 2 for l in lengths) / len(lengths)
        sd = variance**0.5

        is_uniform = sd < 4.0  # SD < 4 是强 AI 信号

        suggestion = ""
        if is_uniform:
            suggestion = "句长过于均匀 (疑似AI)。建议刻意变化句长——长句分析后接短句炸裂，制造阅读节奏变化。"

        return {"sd": round(sd, 2), "mean": round(mean_len, 1), "is_uniform": is_uniform, "suggestion": suggestion}

    def detect_generic_ending(self, text: str) -> dict[str, Any]:
        """检测泛化结尾."""
        generic_patterns = [
            "未来的路还很长",
            "新的征程即将开始",
            "充满希望",
            "未来可期",
            "新的篇章",
            "这只是开始",
            "一切才刚刚开始",
            "故事还在继续",
        ]
        last_200 = text[-200:] if len(text) > 200 else text
        found = [p for p in generic_patterns if p in last_200]
        return {
            "has_generic_ending": len(found) > 0,
            "found": found,
            "suggestion": "章尾是读者留下的关键节点，请替换为具体悬念或冲突，而非泛化抒情。" if found else "",
        }

    def detect_not_x_but_y(self, text: str) -> dict[str, Any]:
        """检测"不是X，是Y"句式 — AI 头号指纹。

        来自番茄爆款实战经验：《黑龙醒》从 663 次清洗到 31 次，
        是所有修复中耗时最长的单项。

        叙事中的"不是X，是Y"是强 AI 信号，对话中的口语化"不是"可保留。

        Returns:
            {
                "count": int,           # 匹配次数
                "matches": list[str],   # 匹配的具体文本
                "is_excessive": bool,   # 是否超过阈值（默认每章2次）
                "suggestion": str,      # 修改建议
            }
        """
        # 匹配"不是X，是Y"模式（叙事句式，非对话）
        # 支持：不是X，是Y / 不是X，而是Y / 不是X。是Y
        # 排除引号后的（对话中的）
        # 使用非贪婪匹配，并在Y部分遇到标点时停止
        pattern = re.compile(r"不是.{1,20}?(?:[，,。]?\s*(?:而)?是.{1,20}?)(?=[。！？\n]|$)")

        matches = [m.group() for m in pattern.finditer(text)]
        count = len(matches)

        is_excessive = count > 2  # 默认阈值：每章2次

        suggestion = ""
        if is_excessive:
            suggestion = (
                f"检测到 {count} 处'不是X，是Y'句式（AI头号指纹）。"
                f"建议：直接写'Y'，删除'不是X'的部分。"
                f"如需对比，用'——'或另起一句。"
            )

        return {
            "count": count,
            "matches": list(set(matches)),
            "is_excessive": is_excessive,
            "suggestion": suggestion,
        }
