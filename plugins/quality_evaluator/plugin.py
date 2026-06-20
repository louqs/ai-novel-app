"""质量评估器 — 十维评审质量门禁.

支持番茄/起点双平台的质量评估，根据平台和类型执行不同的质量检查。

十维评审体系：
1. 开篇吸引力 (Hook Strength)
2. 人物塑造深度 (Character Depth)
3. 情节推进节奏 (Pacing)
4. 情感共鸣强度 (Emotional Resonance)
5. 世界观完整性 (World Coherence)
6. 语言风格独特性 (Style Uniqueness)
7. 爽点密度与分布 (Payoff Density)
8. 悬念与期待感 (Suspense)
9. 章节结尾钩子 (Chapter Hook)
10. 主题深度与格局 (Theme Depth)
"""

from __future__ import annotations

import re
from typing import Any

from core.logging_config import get_logger
from core.plugin_manager import PluginManifest
from core.quality_gate import GateIssue, GateResult, GateVerdict, IQualityGate, Severity

logger = get_logger(__name__)


class QualityEvaluatorPlugin(IQualityGate):
    """质量评估器插件 — 十维评审质量门禁."""

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
        """执行质量评估.

        Args:
            chapter: {"chapter_id": str, "content": str, "metadata": {...}}
            context: {"settings": {...}, "characters": {...}, "facts": {...}, ...}

        Returns:
            GateResult with score and issues
        """
        content = chapter.get("content", "")
        # metadata = chapter.get("metadata", {})  # 暂未使用
        # platform = metadata.get("platform", "fanqie")  # 暂未使用

        if not content:
            return GateResult(
                gate_name=self.name,
                verdict=GateVerdict.PASS,
                issues=[],
                score=1.0,
            )

        issues: list[GateIssue] = []
        scores: dict[str, float] = {}

        # 1. 开篇吸引力检查
        hook_score, hook_issues = self._check_opening_hook(content)
        scores["hook_strength"] = hook_score
        issues.extend(hook_issues)

        # 2. 人物塑造深度检查
        char_score, char_issues = self._check_character_depth(content, context)
        scores["character_depth"] = char_score
        issues.extend(char_issues)

        # 3. 情节推进节奏检查
        pacing_score, pacing_issues = self._check_pacing(content)
        scores["pacing"] = pacing_score
        issues.extend(pacing_issues)

        # 4. 情感共鸣强度检查
        emotion_score, emotion_issues = self._check_emotional_resonance(content)
        scores["emotional_resonance"] = emotion_score
        issues.extend(emotion_issues)

        # 5. 世界观完整性检查
        world_score, world_issues = self._check_world_coherence(content, context)
        scores["world_coherence"] = world_score
        issues.extend(world_issues)

        # 6. 语言风格独特性检查
        style_score, style_issues = self._check_style_uniqueness(content)
        scores["style_uniqueness"] = style_score
        issues.extend(style_issues)

        # 7. 爽点密度检查
        payoff_score, payoff_issues = self._check_payoff_density(content)
        scores["payoff_density"] = payoff_score
        issues.extend(payoff_issues)

        # 8. 悬念与期待感检查
        suspense_score, suspense_issues = self._check_suspense(content)
        scores["suspense"] = suspense_score
        issues.extend(suspense_issues)

        # 9. 章节结尾钩子检查
        ending_score, ending_issues = self._check_ending_hook(content)
        scores["chapter_hook"] = ending_score
        issues.extend(ending_issues)

        # 10. 主题深度检查
        theme_score, theme_issues = self._check_theme_depth(content)
        scores["theme_depth"] = theme_score
        issues.extend(theme_issues)

        # 计算平均分
        avg_score = sum(scores.values()) / len(scores) if scores else 0.0

        # 判定
        min_score = self._kernel.get_config("chapter.ten_dimension_eval.min_score", 6.0)
        verdict = GateVerdict.PASS if avg_score >= min_score else GateVerdict.REVISE

        return GateResult(
            gate_name=self.name,
            verdict=verdict,
            issues=issues,
            score=avg_score,
            metadata={"dimension_scores": scores},
        )

    def _check_opening_hook(self, content: str) -> tuple[float, list[GateIssue]]:
        """检查开篇吸引力."""
        issues = []
        score = 7.0  # 默认分

        # 检查前300字是否有物理事件
        first_300 = content[:300]
        physical_events = ["炸", "断", "打", "冲", "倒", "烧", "砸", "撕", "砍", "刺"]
        has_physical = any(event in first_300 for event in physical_events)

        if not has_physical:
            score -= 2.0
            issues.append(
                GateIssue(
                    severity=Severity.WARNING,
                    code="quality.opening.no_physical_event",
                    message="前300字缺少物理事件",
                    suggestion="番茄读者3秒划走，起点读者也会失去耐心。建议在开头加入动作/冲突/危机。",
                )
            )

        # 检查开头是否有景物描写
        first_100 = content[:100]
        scene_keywords = ["天气", "阳光", "月光", "风景", "景色", "天空"]
        if any(kw in first_100 for kw in scene_keywords):
            score -= 1.0
            issues.append(
                GateIssue(
                    severity=Severity.INFO,
                    code="quality.opening.scene_description",
                    message="开头有景物描写",
                    suggestion="建议用动作开头，景物描写放在人物出场之后。",
                )
            )

        return max(0.0, min(10.0, score)), issues

    def _check_character_depth(self, content: str, context: dict[str, Any]) -> tuple[float, list[GateIssue]]:
        """检查人物塑造深度."""
        issues = []
        score = 7.0

        # 检查心理标签使用
        mental_labels = ["感到", "觉得", "认为", "想到", "意识到"]
        mental_count = sum(content.count(label) for label in mental_labels)
        word_count = len(content)

        if word_count > 0:
            mental_density = mental_count / (word_count / 1000)
            if mental_density > 3.0:
                score -= 1.5
                issues.append(
                    GateIssue(
                        severity=Severity.WARNING,
                        code="quality.character.mental_labels",
                        message=f"心理标签使用过多（{mental_count}次，{mental_density:.1f}次/千字）",
                        suggestion="用生理反应替代心理描述。不写'他很害怕'，写'他的手汗让整个手掌粘腻'。",
                    )
                )

        # 检查对话是否有区分度
        dialogue_pattern = re.compile(r'"([^"]*)"')
        dialogues = dialogue_pattern.findall(content)
        if len(dialogues) >= 4:
            # 简单检查：对话长度是否多样化
            lengths = [len(d) for d in dialogues[:10]]
            avg_len = sum(lengths) / len(lengths) if lengths else 0
            if avg_len > 0:
                variance = sum((length - avg_len) ** 2 for length in lengths) / len(lengths)
                if variance < 10:  # 对话长度过于均匀
                    score -= 1.0
                    issues.append(
                        GateIssue(
                            severity=Severity.INFO,
                            code="quality.character.dialogue_uniform",
                            message="对话长度过于均匀，缺乏区分度",
                            suggestion="不同角色说话方式应该不同。急性子短句多，慢性子话里有话。",
                        )
                    )

        return max(0.0, min(10.0, score)), issues

    def _check_pacing(self, content: str) -> tuple[float, list[GateIssue]]:
        """检查情节推进节奏."""
        issues = []
        score = 7.0

        # 检查段落长度
        paragraphs = content.split("\n\n")
        long_paragraphs = [p for p in paragraphs if len(p) > 300]

        if len(long_paragraphs) > 3:
            score -= 1.0
            issues.append(
                GateIssue(
                    severity=Severity.WARNING,
                    code="quality.pacing.long_paragraphs",
                    message=f"有{len(long_paragraphs)}个段落超过300字",
                    suggestion="手机阅读不友好。建议将长段落拆分为2-3个短段落。",
                )
            )

        # 检查信息密度（纯描写/心理活动占比）
        description_keywords = ["看着", "望着", "想着", "回忆", "感觉", "觉得"]
        description_count = sum(content.count(kw) for kw in description_keywords)
        if len(content) > 0:
            description_ratio = description_count / (len(content) / 1000)
            if description_ratio > 5.0:
                score -= 1.0
                issues.append(
                    GateIssue(
                        severity=Severity.WARNING,
                        code="quality.pacing.high_description_ratio",
                        message=f"描写/心理活动密度过高（{description_ratio:.1f}次/千字）",
                        suggestion="每300字检查一次：如果连续300字全是描写/心理活动→立即插入动作或对话。",
                    )
                )

        return max(0.0, min(10.0, score)), issues

    def _check_emotional_resonance(self, content: str) -> tuple[float, list[GateIssue]]:
        """检查情感共鸣强度."""
        issues = []
        score = 7.0

        # 检查是否有具体的身体反应描写
        body_reactions = ["手", "拳", "眼", "嘴", "脸", "肩", "背", "脚"]
        has_body_detail = any(
            content.find(reaction) != -1
            and any(
                verb in content[max(0, content.find(reaction) - 20) : content.find(reaction) + 20]
                for verb in ["握", "抖", "咬", "抬", "低", "颤", "攥", "掐"]
            )
            for reaction in body_reactions
        )

        if not has_body_detail:
            score -= 1.0
            issues.append(
                GateIssue(
                    severity=Severity.INFO,
                    code="quality.emotion.no_body_detail",
                    message="缺少具体的身体反应描写",
                    suggestion="情绪通过身体反应传递，不通过情绪名称。'他握紧拳'比'他很愤怒'更有力。",
                )
            )

        return max(0.0, min(10.0, score)), issues

    def _check_world_coherence(self, content: str, context: dict[str, Any]) -> tuple[float, list[GateIssue]]:
        """检查世界观完整性."""
        issues = []
        score = 8.0  # 默认高分，因为硬伤需要人工检查

        # 检查元话语
        meta_words = ["卷一", "卷二", "第X章", "前文所述", "后文再表", "本章"]
        found_meta = [mw for mw in meta_words if mw in content]
        if found_meta:
            score -= 3.0
            issues.append(
                GateIssue(
                    severity=Severity.ERROR,
                    code="quality.world.meta_language",
                    message=f"正文中出现元话语：{', '.join(found_meta)}",
                    suggestion="元话语是作者视角的文字，读者看到会立刻出戏。引用过去事件用具体内容描述，不用章节编号。",
                )
            )

        return max(0.0, min(10.0, score)), issues

    def _check_style_uniqueness(self, content: str) -> tuple[float, list[GateIssue]]:
        """检查语言风格独特性."""
        issues = []
        score = 7.0

        # 检查AI口水词
        ai_words = ["缓缓", "不由得", "眼底闪过", "心中升起", "说不出的", "这意味着", "深邃", "不可置信"]
        found_ai_words = [w for w in ai_words if w in content]
        if found_ai_words:
            score -= len(found_ai_words) * 0.5
            issues.append(
                GateIssue(
                    severity=Severity.WARNING,
                    code="quality.style.ai_words",
                    message=f"发现AI口水词：{', '.join(found_ai_words)}",
                    suggestion="删除或替换这些AI高频词。用具体动作替代抽象描述。",
                )
            )

        return max(0.0, min(10.0, score)), issues

    def _check_payoff_density(self, content: str) -> tuple[float, list[GateIssue]]:
        """检查爽点密度."""
        issues = []
        score = 7.0

        # 检查是否有动词驱动的爽点
        payoff_verbs = ["击败", "碾压", "打脸", "反杀", "截胡", "复仇", "突破", "觉醒"]
        has_payoff = any(verb in content for verb in payoff_verbs)

        if not has_payoff:
            score -= 1.0
            issues.append(
                GateIssue(
                    severity=Severity.INFO,
                    code="quality.payoff.no_payoff_verb",
                    message="未检测到爽点动词",
                    suggestion="每章爽点必须能用'谁+动词+谁+损失'描述。发现/理解/对话不是爽点。",
                )
            )

        return max(0.0, min(10.0, score)), issues

    def _check_suspense(self, content: str) -> tuple[float, list[GateIssue]]:
        """检查悬念与期待感."""
        issues = []
        score = 7.0

        # 检查伏笔相关词汇
        foreshadow_words = ["伏笔", "悬念", "谜团", "秘密", "隐藏", "暗示"]
        has_foreshadow = any(word in content for word in foreshadow_words)

        if has_foreshadow:
            score += 0.5  # 有伏笔加分

        return max(0.0, min(10.0, score)), issues

    def _check_ending_hook(self, content: str) -> tuple[float, list[GateIssue]]:
        """检查章节结尾钩子."""
        issues = []
        score = 7.0

        # 获取最后200字
        last_200 = content[-200:] if len(content) > 200 else content

        # 检查禁止的结尾类型
        forbidden_endings = [
            "明天继续",
            "继续",
            "就这样",
            "结束了",
            "一切归于平静",
            "岁月静好",
            "未来可期",
            "充满希望",
            "新的篇章",
        ]
        found_forbidden = [e for e in forbidden_endings if e in last_200]
        if found_forbidden:
            score -= 3.0
            issues.append(
                GateIssue(
                    severity=Severity.ERROR,
                    code="quality.ending.forbidden_ending",
                    message=f"结尾使用了禁止的类型：{', '.join(found_forbidden)}",
                    suggestion="结尾只写四种：危机突降、悬念反转、挑衅叫板、死亡倒计时。",
                )
            )

        # 检查是否有钩子（简化检查）
        hook_indicators = ["？", "——", "…", "!", "突然", "忽然", "没想到"]
        has_hook = any(indicator in last_200 for indicator in hook_indicators)

        if not has_hook and not found_forbidden:
            score -= 1.0
            issues.append(
                GateIssue(
                    severity=Severity.WARNING,
                    code="quality.ending.no_hook",
                    message="结尾缺少钩子",
                    suggestion="每章结尾必须让读者想翻下一页。建议加入危机/悬念/反转。",
                )
            )

        return max(0.0, min(10.0, score)), issues

    def _check_theme_depth(self, content: str) -> tuple[float, list[GateIssue]]:
        """检查主题深度."""
        issues = []
        score = 7.0

        # 主题深度需要人工评估，这里只做基础检查
        # 检查是否有过度解释
        explanation_phrases = ["这意味着", "这就是为什么", "原因在于", "说明"]
        found_explanations = [p for p in explanation_phrases if p in content]
        if found_explanations:
            score -= len(found_explanations) * 0.5
            issues.append(
                GateIssue(
                    severity=Severity.INFO,
                    code="quality.theme.over_explanation",
                    message=f"发现过度解释：{', '.join(found_explanations)}",
                    suggestion="让读者自己把两件事联系起来，不要替读者写结论。",
                )
            )

        return max(0.0, min(10.0, score)), issues


# ---------------------------------------------------------------------------
# 工厂函数
# ---------------------------------------------------------------------------


def create_manifest() -> PluginManifest:
    return PluginManifest(
        name="quality-evaluator",
        version="0.1.0",
        description="质量评估器 — 十维评审质量门禁",
        dependencies=[],
        hooks=["on_load", "on_unload"],
    )


def create_plugin() -> QualityEvaluatorPlugin:
    return QualityEvaluatorPlugin()
