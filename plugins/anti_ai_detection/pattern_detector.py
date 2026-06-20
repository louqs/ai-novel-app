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

    def calculate_ai_score(self, matches: list[AIPatternMatch], text: str = "") -> float:
        """根据模式匹配计算 AI 综合评分 (0-1, 越低越像 AI).

        综合考虑：
        1. 关键词模式匹配
        2. 句长均匀度
        3. 词汇多样性
        4. 句式重复度
        5. 情感标签化
        6. 描写模式化
        7. 对话套路化
        8. AI模板词
        9. AI心理套路
        10. 中二独白
        11. Humanizer模式
        12. AI开头模式（新增）
        13. AI过渡词（新增）
        14. 段落结构（新增）
        15. 对话比例（新增）
        """
        # 基础分数（关键词匹配）
        base_score = 1.0
        if matches:
            severity_weights = {"low": 0.05, "medium": 0.1, "high": 0.2}
            for match in matches:
                penalty = severity_weights.get(match.severity, 0.1) * min(match.count, 5)
                base_score -= penalty

        if not text:
            return max(0.0, base_score)

        # 句长均匀度检测
        uniform_result = self.detect_uniform_sentences(text)
        uniform_penalty = 0.0
        if uniform_result["is_uniform"]:
            sd = uniform_result["sd"]
            if sd < 1.5:
                uniform_penalty = 0.20
            elif sd < 2.0:
                uniform_penalty = 0.15
            elif sd < 2.5:
                uniform_penalty = 0.10

        # 词汇多样性检测
        diversity_penalty = self._check_vocabulary_diversity(text)

        # 句式重复度检测
        pattern_penalty = self._check_sentence_patterns(text)

        # 情感标签化检测
        emotion_penalty = self._check_emotion_labels(text)

        # 描写模式化检测
        description_penalty = self._check_description_patterns(text)

        # 对话套路化检测
        dialogue_penalty = self._check_dialogue_patterns(text)

        # AI模板词检测
        template_result = self.check_template_words(text)
        template_penalty = 0.0
        if template_result["is_excessive"]:
            template_penalty = 0.15 if template_result["total"] > 8 else 0.08
        elif template_result["total"] > 3:
            template_penalty = 0.05

        # AI心理套路检测
        psych_result = self.check_psychology_cliche(text)
        psych_penalty = 0.0
        if psych_result["level"] == "critical":
            psych_penalty = 0.15
        elif psych_result["level"] == "warning":
            psych_penalty = 0.08

        # 中二独白检测
        cringe_result = self.check_cringe_monologue(text)
        cringe_penalty = 0.0
        if cringe_result["level"] == "critical":
            cringe_penalty = 0.12
        elif cringe_result["level"] == "warning":
            cringe_penalty = 0.05

        # Humanizer模式检测
        humanizer_result = self.check_humanizer_patterns(text)
        humanizer_penalty = 0.0
        if humanizer_result["a_total"] > 3:
            humanizer_penalty += 0.08
        if humanizer_result["b_total"] > 5:
            humanizer_penalty += 0.08

        # AI开头模式检测（新增）
        opening_result = self.check_ai_openings(text)
        opening_penalty = 0.0
        if opening_result["is_excessive"]:
            opening_penalty = 0.10
        elif opening_result["count"] > 2:
            opening_penalty = 0.05

        # AI过渡词检测（新增）
        transition_result = self.check_transition_words(text)
        transition_penalty = 0.0
        if transition_result["is_excessive"]:
            transition_penalty = 0.10
        elif transition_result["total"] > 3:
            transition_penalty = 0.05

        # 段落结构检测（新增）
        paragraph_result = self.check_paragraph_structure(text)
        paragraph_penalty = 0.0
        if paragraph_result["is_uniform"]:
            paragraph_penalty = 0.08

        # 对话比例检测（新增）
        dialogue_ratio_result = self.check_dialogue_ratio(text)
        dialogue_ratio_penalty = 0.0
        if dialogue_ratio_result["is_abnormal"]:
            dialogue_ratio_penalty = 0.08

        # AI形容词组合检测（新增）
        adj_combo_result = self.check_adjective_combos(text)
        adj_combo_penalty = 0.0
        if adj_combo_result["is_excessive"]:
            adj_combo_penalty = 0.08

        # AI动作描写检测（新增）
        action_result = self.check_action_patterns(text)
        action_penalty = 0.0
        if action_result["is_excessive"]:
            action_penalty = 0.10
        elif action_result["total"] > 3:
            action_penalty = 0.05

        # AI环境描写检测（新增）
        env_result = self.check_environment_patterns(text)
        env_penalty = 0.0
        if env_result["is_excessive"]:
            env_penalty = 0.10
        elif env_result["total"] > 2:
            env_penalty = 0.05

        # AI情感描写检测（新增）
        emotion_pat_result = self.check_emotion_patterns(text)
        emotion_pat_penalty = 0.0
        if emotion_pat_result["is_excessive"]:
            emotion_pat_penalty = 0.10
        elif emotion_pat_result["total"] > 3:
            emotion_pat_penalty = 0.05

        # 综合计算
        final_score = (
            base_score
            - uniform_penalty
            - diversity_penalty
            - pattern_penalty
            - emotion_penalty
            - description_penalty
            - dialogue_penalty
            - template_penalty
            - psych_penalty
            - cringe_penalty
            - humanizer_penalty
            - opening_penalty
            - transition_penalty
            - paragraph_penalty
            - dialogue_ratio_penalty
            - adj_combo_penalty
            - action_penalty
            - env_penalty
            - emotion_pat_penalty
        )

        return max(0.0, min(1.0, final_score))

    def _check_vocabulary_diversity(self, text: str) -> float:
        """检查词汇多样性 — AI 倾向于重复使用常见词汇组合."""
        if len(text) < 200:  # 提高最小文本长度要求
            return 0.0

        # 提取 2-gram 和 3-gram
        words = list(text)  # 中文按字符处理
        bigrams = [words[i:i+2] for i in range(len(words)-1)]
        trigrams = [words[i:i+3] for i in range(len(words)-2)]

        # 计算重复率
        bigram_count = len(bigrams)
        unique_bigrams = len(set(tuple(b) for b in bigrams))
        bigram重复率 = 1 - (unique_bigrams / max(bigram_count, 1))

        trigram_count = len(trigrams)
        unique_trigrams = len(set(tuple(t) for t in trigrams))
        trigram重复率 = 1 - (unique_trigrams / max(trigram_count, 1))

        # 提高阈值，降低误判
        # AI 文本的 2-gram 重复率通常 > 0.85，3-gram 重复率 > 0.92
        penalty = 0.0
        if bigram重复率 > 0.85:
            penalty += 0.08
        if trigram重复率 > 0.92:
            penalty += 0.08

        return penalty

    def _check_sentence_patterns(self, text: str) -> float:
        """检查句式重复度 — AI 倾向于使用相似的句式结构."""
        # 按句号分句
        sentences = re.split(r'[。！？]', text)
        sentences = [s.strip() for s in sentences if len(s.strip()) > 5]

        if len(sentences) < 8:  # 提高最小句子数要求
            return 0.0

        # 检查句首模式
        sentence_starts = []
        for s in sentences:
            # 提取前 2-3 个字符作为句首模式
            start = s[:min(3, len(s))]
            sentence_starts.append(start)

        # 计算句首重复率
        unique_starts = len(set(sentence_starts))
        start重复率 = 1 - (unique_starts / len(sentence_starts))

        # 检查句式结构（主谓宾模式）
        structure_patterns = []
        for s in sentences:
            # 简单的结构模式：按标点分割
            parts = re.split(r'[，、；]', s)
            structure_patterns.append(len(parts))

        # 计算结构多样性
        if structure_patterns:
            avg结构 = sum(structure_patterns) / len(structure_patterns)
            结构方差 = sum((p - avg结构) ** 2 for p in structure_patterns) / len(structure_patterns)
            结构sd = 结构方差 ** 0.5

            # 提高阈值，降低误判
            # AI 文本的结构方差通常更小
            if 结构sd < 0.3 and len(sentences) > 15:
                return 0.12
            elif 结构sd < 0.5:
                return 0.06

        # 提高句首重复率阈值
        if start重复率 > 0.6:
            return 0.10
        elif start重复率 > 0.4:
            return 0.05

        return 0.0

    def _check_emotion_labels(self, text: str) -> float:
        """检测情感标签化 — AI 倾向于使用情感名词而非具体描写."""
        # AI 常用的情感标签
        emotion_labels = [
            "心中充满了", "眼中满是", "眼中闪过", "心中升起",
            "脸上带着", "语气", "眼神中", "心中一凛",
            "心中一紧", "心中一动", "心中暗想", "心中暗道",
            "不禁", "不由得", "忍不住", "情不自禁",
            "感到", "觉得", "感觉到", "意识到",
            "充满了决心", "充满了期待", "充满了希望",
            "松了一口气", "深吸一口气", "稳住心神",
            "石头终于落下", "心中的石头",
        ]

        count = 0
        for label in emotion_labels:
            count += text.count(label)

        # 计算密度（每千字）
        density = count / (len(text) / 1000) if len(text) > 0 else 0

        # AI 文本的情感标签密度通常 > 5/千字
        if density > 8:
            return 0.15
        elif density > 5:
            return 0.10
        elif density > 3:
            return 0.05

        return 0.0

    def _check_description_patterns(self, text: str) -> float:
        """检测描写模式化 — AI 倾向于使用固定的描写模板."""
        # AI 常用的描写模式
        patterns = [
            # 视觉描写模板
            r"阳光透过.{2,10}洒下",
            r"月光洒在.{2,10}形成",
            r"火光映照出",
            r"灯光.{2,8}照亮",
            # 动作描写模板
            r"目光如.{2,6}般",
            r"眼神如.{2,6}般",
            r"声音如.{2,6}般",
            r"心跳加速",
            r"呼吸变得",
            r"汗水.{2,8}滑落",
            r"泪水.{2,8}流下",
            # 环境描写模板
            r"夜幕降临",
            r"天色渐暗",
            r"晨光初现",
            r"夕阳西下",
            r"树叶沙沙作响",
            r"微风拂过",
            r"空气中弥漫着",
        ]

        count = 0
        for pattern in patterns:
            matches = re.findall(pattern, text)
            count += len(matches)

        # 计算密度（每千字）
        density = count / (len(text) / 1000) if len(text) > 0 else 0

        # AI 文本的模式密度通常 > 3/千字
        if density > 5:
            return 0.15
        elif density > 3:
            return 0.10
        elif density > 2:
            return 0.05

        return 0.0

    def _check_dialogue_patterns(self, text: str) -> float:
        """检测对话套路化 — AI 倾向于使用固定的对话模板."""
        # AI 常用的对话模式
        dialogue_patterns = [
            r"你怎么会在这里",
            r"我见你.{2,10}就",
            r"你怎么.{2,8}了",
            r"我.{2,8}你.{2,8}担心",
            r"你看.{2,10}",
            r"你.{2,8}什么",
            r"这是什么",
            r"那.{2,8}是什么",
            r"我们.{2,8}吧",
            r"走吧",
            r"小心",
            r"别动",
            r"站住",
            r"等等",
        ]

        count = 0
        for pattern in dialogue_patterns:
            matches = re.findall(pattern, text)
            count += len(matches)

        # 计算密度（每千字）
        density = count / (len(text) / 1000) if len(text) > 0 else 0

        # AI 文本的对话模式密度通常 > 2/千字
        if density > 4:
            return 0.12
        elif density > 2:
            return 0.08
        elif density > 1:
            return 0.04

        return 0.0

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

        # 降低阈值，减少误判
        # 朱雀等专业工具的阈值通常更低
        is_uniform = sd < 2.5  # 从 4.0 降低到 2.5

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

    # =================================================================
    # 新增：来自 novel-assistant 的高级检测维度
    # =================================================================

    # AI 模板词黑名单（来自 chapter_health_check.py）
    TEMPLATE_WORDS = [
        '笑了笑', '点了点头', '脸红了红',
        '眼睛一亮', '眼睛都直了', '嘴角微微上扬',
        '心里头一暖', '心里头那个高兴', '心里头有些异样',
        '心里头七上八下', '心里头咯噔', '心里咯噔',
        '心里头一紧', '心里头一沉', '心里头一软',
        '激动得心跳加速', '心跳加快', '脑子嗡的一声',
        '攥紧了拳头', '攥紧了车把', '深吸了口气', '深吸一口气',
        '眉头皱了皱', '眉头一挑', '冷哼了一声', '冷哼一声',
        '没搭理', '心里盘算', '心里头有了数',
        '消息传得比风还快',
    ]

    # AI 心理套路词（合并检测）
    AI_PSYCHOLOGY_CLICHE = [
        '心里头咯噔', '心里咯噔', '心里头一紧', '心里一紧',
        '心里头一沉', '心里一沉', '心里头一软', '心里一软',
        '心里头七上八下', '心里七上八下', '心里头咯噔了一下',
        '脑子嗡的一声', '脑袋嗡的一声', '脑子一片空白',
        '眼前发黑', '眼前一黑',
        '攥紧了拳头', '攥紧拳头', '握紧了拳头',
        '指甲掐进掌心', '指甲掐进',
        '深吸了口气', '深吸一口气', '深深吸了口气',
        '眉头皱了皱', '眉头微皱', '眉头一挑', '眉头紧锁',
        '冷哼了一声', '冷哼一声',
        '心跳加快', '心跳加速',
        '鼻子有点酸', '鼻子一酸',
    ]

    # 中二独白黑名单
    CRINGE_MONOLOGUE = [
        '等着吧', '十倍奉还', '付出代价', '不会认输',
        '迟早让你', '迟早要', '俺要让你', '他要让你',
        '走着瞧', '给我等着', '敢惹俺', '别怪俺',
        '总有一天', '总会让',
    ]

    # Humanizer 结构性模式
    HUMANIZER_A_PATTERNS = {
        '否定式排比': ['不是', '也不是', '更不是', '既不', '也不', '还不能', '也不能'],
        '三段式堆砌': ['首先', '其次', '最后', '第一', '第二', '第三'],
        'ing结尾虚假分析': ['突出', '彰显', '确保', '反映', '体现', '为', '做出贡献', '培养', '促进'],
        '谄媚语气': ['当然', '毫无疑问', '无可否认', '您的观点', '承蒙', '拜谢'],
        '强行展望结尾': ['挑战与机遇并存', '光明的前途', '等待着', '未来一定', '必将'],
    }

    HUMANIZER_B_PATTERNS = {
        '填充短语': ['总而言之', '值得注意的是', '实际上', '事实上', '从某种意义上说', '可以说', '严格来说'],
        'AI高频词': ['此外', '深入探讨', '至关重要', '不可或缺', '不容忽视'],
        '过度限定': ['可能', '也许', '大概', '应该', '似乎', '看上去', '一般来说', '基本上'],
    }

    def check_template_words(self, text: str) -> dict[str, Any]:
        """检测 AI 模板词（来自 chapter_health_check.py）.

        Returns:
            {
                "total": int,
                "details": dict[str, int],
                "is_excessive": bool,
                "suggestion": str,
            }
        """
        details = {}
        for word in self.TEMPLATE_WORDS:
            count = text.count(word)
            if count > 0:
                details[word] = count

        total = sum(details.values())
        # 单个模板词 > 3 次 = 过度
        single_max = max(details.values()) if details else 0

        is_excessive = total > 8 or single_max > 3

        suggestion = ""
        if is_excessive:
            if total > 8:
                suggestion = f"AI模板词总次数 {total} 次（阈值 8）。建议：替换为具体动作描写。"
            else:
                over_words = [w for w, c in details.items() if c > 3]
                suggestion = f"单词超限：{'、'.join(over_words)} 各出现 > 3 次。建议：换用不同的表达方式。"

        return {
            "total": total,
            "details": details,
            "is_excessive": is_excessive,
            "suggestion": suggestion,
        }

    def check_psychology_cliche(self, text: str) -> dict[str, Any]:
        """检测 AI 心理套路合并密度（来自 chapter_health_check.py）.

        单独一个"心里头咯噔"没事，但一章里堆砌 9 次 = AI 堆砌。

        Returns:
            {
                "total": int,
                "details": dict[str, int],
                "level": str,  # "pass" / "warning" / "critical"
                "suggestion": str,
            }
        """
        details = {}
        for phrase in self.AI_PSYCHOLOGY_CLICHE:
            count = text.count(phrase)
            if count > 0:
                details[phrase] = count

        total = sum(details.values())

        if total > 8:
            level = "critical"
            suggestion = f"AI心理套路泛滥：合并 {total} 次（阈值 8）。建议：换具体动作——'捏着筷子手顿住'代替'心里头咯噔'。"
        elif total > 5:
            level = "warning"
            suggestion = f"AI心理套路偏多：{total} 次（建议 ≤ 5）。建议替换几处为具体动作。"
        else:
            level = "pass"
            suggestion = ""

        return {
            "total": total,
            "details": details,
            "level": level,
            "suggestion": suggestion,
        }

    def check_cringe_monologue(self, text: str) -> dict[str, Any]:
        """检测中二独白（来自 chapter_health_check.py）.

        "等着吧 / 十倍奉还 / 付出代价" 每章 > 2 次 = 严重问题。

        Returns:
            {
                "total": int,
                "details": dict[str, int],
                "level": str,  # "pass" / "warning" / "critical"
                "suggestion": str,
            }
        """
        details = {}
        for phrase in self.CRINGE_MONOLOGUE:
            count = text.count(phrase)
            if count > 0:
                details[phrase] = count

        total = sum(details.values())

        if total > 2:
            level = "critical"
            suggestion = f"中二独白泛滥：{total} 次（阈值 2）。建议：改用动作/细节——'把名片揣进兜里，指头使劲捏了捏'比'俺十倍奉还'更有劲。"
        elif total > 1:
            level = "warning"
            suggestion = f"中二独白偏多：{total} 次（建议 ≤ 1）。"
        else:
            level = "pass"
            suggestion = ""

        return {
            "total": total,
            "details": details,
            "level": level,
            "suggestion": suggestion,
        }

    def check_humanizer_patterns(self, text: str) -> dict[str, Any]:
        """检测 Humanizer 结构性模式（来自 chapter_health_check.py）.

        A类: 否定排比/三段式/谄媚/强行展望
        B类: 填充短语/AI高频词/过度限定

        Returns:
            {
                "A": dict[str, int],
                "B": dict[str, int],
                "a_total": int,
                "b_total": int,
                "level": str,
                "suggestion": str,
            }
        """
        result_a = {}
        for pattern_name, keywords in self.HUMANIZER_A_PATTERNS.items():
            count = sum(text.count(k) for k in keywords)
            if count > 0:
                result_a[pattern_name] = count

        result_b = {}
        for pattern_name, keywords in self.HUMANIZER_B_PATTERNS.items():
            count = sum(text.count(k) for k in keywords)
            if count > 0:
                result_b[pattern_name] = count

        a_total = sum(result_a.values())
        b_total = sum(result_b.values())

        suggestions = []
        if a_total > 3:
            suggestions.append(f"A类结构性模式: {a_total}次 > 3 (阈值)")
        if b_total > 5:
            suggestions.append(f"B类AI套话: {b_total}次 > 5 (阈值)")

        level = "critical" if suggestions else "pass"

        return {
            "A": result_a,
            "B": result_b,
            "a_total": a_total,
            "b_total": b_total,
            "level": level,
            "suggestion": "；".join(suggestions) if suggestions else "",
        }

    def check_dialogue_quotes(self, text: str) -> dict[str, Any]:
        """检测对话引号使用（来自 chapter_health_check.py）.

        Returns:
            {
                "quote_pairs": int,
                "is_adequate": bool,
                "suggestion": str,
            }
        """
        # 统计中文弯引号对数
        curly = text.count('“')
        # 统计英文直引号对数
        straight_pairs = text.count('"') // 2
        total_pairs = curly + straight_pairs

        is_adequate = total_pairs >= 6

        suggestion = ""
        if not is_adequate:
            suggestion = f"对话引号不足：{total_pairs} 对（阈值 ≥ 6）。所有对话必须用 "" 包裹。"

        return {
            "quote_pairs": total_pairs,
            "is_adequate": is_adequate,
            "suggestion": suggestion,
        }

    # =================================================================
    # 新增：更严格的AI特征检测
    # =================================================================

    # AI常用开头模式
    AI_OPENING_PATTERNS = [
        r"^在.{2,10}中",           # 在...中
        r"^随着.{2,10}",           # 随着...
        r"^当.{2,10}时",           # 当...时
        r"^突然.{0,5}",            # 突然...
        r"^就在这时",              # 就在这时
        r"^正当.{2,10}",           # 正当...
        r"^一阵.{2,6}传来",        # 一阵...传来
    ]

    # AI常用过渡词
    AI_TRANSITION_WORDS = [
        "就在这时", "突然", "忽然", "猛然", "猛地",
        "霎时间", "刹那间", "一瞬间", "转眼间",
        "不知不觉", "不知不觉间", "不知不觉中",
        "心中一动", "心中一凛", "心中一紧",
        "脑海中闪过", "眼前一亮", "眼前一黑",
    ]

    # AI常用描写模板
    AI_DESCRIPTION_TEMPLATES = [
        r"阳光透过.{2,10}洒下",      # 阳光透过...洒下
        r"月光洒在.{2,10}形成",      # 月光洒在...形成
        r"目光.{2,6}般",             # 目光...般
        r"眼神.{2,6}般",             # 眼神...般
        r"声音.{2,6}般",             # 声音...般
        r"心跳加速",                 # 心跳加速
        r"呼吸变得.{2,6}",           # 呼吸变得...
        r"汗水.{2,8}滑落",           # 汗水...滑落
        r"泪水.{2,8}流下",           # 泪水...流下
        r"夜幕降临",                 # 夜幕降临
        r"天色渐暗",                 # 天色渐暗
        r"晨光初现",                 # 晨光初现
        r"夕阳西下",                 # 夕阳西下
        r"树叶沙沙作响",             # 树叶沙沙作响
        r"微风拂过",                 # 微风拂过
        r"空气中弥漫着",             # 空气中弥漫着
    ]

    # AI常用对话模式
    AI_DIALOGUE_PATTERNS = [
        r"你怎么会在这里",
        r"我见你.{2,10}就",
        r"你怎么.{2,8}了",
        r"我.{2,8}你.{2,8}担心",
        r"你看.{2,10}",
        r"你.{2,8}什么",
        r"这是什么",
        r"那.{2,8}是什么",
        r"我们.{2,8}吧",
        r"小心",
        r"别动",
        r"站住",
        r"等等",
        r"交出.{2,10}",
        r"否则.{2,10}",
    ]

    def check_ai_openings(self, text: str) -> dict[str, Any]:
        """检测AI常用开头模式."""
        lines = text.split('\n')
        matches = []
        for i, line in enumerate(lines[:20]):  # 只检查前20行
            line = line.strip()
            if not line:
                continue
            for pattern in self.AI_OPENING_PATTERNS:
                if re.match(pattern, line):
                    matches.append({"line": i + 1, "text": line[:50], "pattern": pattern})
                    break

        count = len(matches)
        return {
            "count": count,
            "matches": matches,
            "is_excessive": count > 3,
            "suggestion": f"AI常用开头模式 {count} 处" if count > 3 else "",
        }

    def check_transition_words(self, text: str) -> dict[str, Any]:
        """检测AI常用过渡词."""
        found = {}
        for word in self.AI_TRANSITION_WORDS:
            count = text.count(word)
            if count > 0:
                found[word] = count

        total = sum(found.values())
        return {
            "total": total,
            "details": found,
            "is_excessive": total > 5,
            "suggestion": f"AI过渡词 {total} 处" if total > 5 else "",
        }

    def check_description_templates(self, text: str) -> dict[str, Any]:
        """检测AI描写模板."""
        found = {}
        for pattern in self.AI_DESCRIPTION_TEMPLATES:
            matches = re.findall(pattern, text)
            if matches:
                found[pattern] = len(matches)

        total = sum(found.values())
        return {
            "total": total,
            "details": found,
            "is_excessive": total > 5,
            "suggestion": f"AI描写模板 {total} 处" if total > 5 else "",
        }

    def check_dialogue_patterns(self, text: str) -> dict[str, Any]:
        """检测AI对话模式."""
        found = {}
        for pattern in self.AI_DIALOGUE_PATTERNS:
            matches = re.findall(pattern, text)
            if matches:
                found[pattern] = len(matches)

        total = sum(found.values())
        return {
            "total": total,
            "details": found,
            "is_excessive": total > 3,
            "suggestion": f"AI对话套路 {total} 处" if total > 3 else "",
        }

    def check_paragraph_structure(self, text: str) -> dict[str, Any]:
        """检测段落结构（AI倾向于段落长度均匀）."""
        paragraphs = [p.strip() for p in text.split('\n\n') if p.strip()]
        if len(paragraphs) < 3:
            return {"uniform": False, "suggestion": ""}

        lengths = [len(p) for p in paragraphs]
        mean_len = sum(lengths) / len(lengths)
        variance = sum((l - mean_len) ** 2 for l in lengths) / len(lengths)
        sd = variance ** 0.5

        # 段落长度标准差 < 50 字符 = 过于均匀
        is_uniform = sd < 50 and mean_len > 100

        return {
            "paragraph_count": len(paragraphs),
            "mean_length": round(mean_len, 1),
            "sd": round(sd, 2),
            "is_uniform": is_uniform,
            "suggestion": "段落长度过于均匀" if is_uniform else "",
        }

    def check_dialogue_ratio(self, text: str) -> dict[str, Any]:
        """检测对话比例（AI倾向于对话过多或过少）."""
        # 匹配引号内的对话
        dialogue_pattern = re.compile(r'"([^"]*)"')
        dialogues = dialogue_pattern.findall(text)

        dialogue_chars = sum(len(d) for d in dialogues)
        total_chars = len(text)
        ratio = dialogue_chars / total_chars if total_chars > 0 else 0

        # 对话比例 < 10% 或 > 60% 都是异常
        is_abnormal = ratio < 0.1 or ratio > 0.6

        return {
            "dialogue_count": len(dialogues),
            "dialogue_chars": dialogue_chars,
            "total_chars": total_chars,
            "ratio": round(ratio * 100, 1),
            "is_abnormal": is_abnormal,
            "suggestion": f"对话比例异常: {round(ratio * 100, 1)}%" if is_abnormal else "",
        }

    # AI常用形容词/副词组合
    AI_ADJECTIVE_COMBOS = [
        "锐利的眼神", "深邃的目光", "温柔的笑容", "坚定的眼神",
        "淡淡的微笑", "冷冷的说道", "轻轻的叹了口气", "微微的点了点头",
        "缓缓的说道", "静静的看着", "默默的承受", "悄悄的离开",
        "紧紧的握住", "狠狠的瞪了一眼", "重重的叹了口气", "慢慢的转过身",
    ]

    # AI常用动作描写
    AI_ACTION_PATTERNS = [
        r"紧握着.{2,6}",
        r"目光.{2,6}般",
        r"眼神.{2,6}般",
        r"心跳加速",
        r"呼吸变得.{2,6}",
        r"汗水.{2,8}滑落",
        r"不由得.{2,10}",
        r"忍不住.{2,10}",
        r"下意识.{2,10}",
        r"本能地.{2,10}",
        r"心中.{2,10}",
        r"脑海中.{2,10}",
        r"眼中.{2,10}",
    ]

    # AI常用环境描写
    AI_ENVIRONMENT_PATTERNS = [
        r"阳光透过.{2,10}洒下",
        r"月光洒在.{2,10}",
        r"星光.{2,10}闪烁",
        r"微风.{2,10}吹过",
        r"空气中弥漫着.{2,10}",
        r"夜幕.{2,10}降临",
        r"天色.{2,10}渐暗",
        r"晨光.{2,10}初现",
        r"夕阳.{2,10}西下",
        r"树叶.{2,10}沙沙作响",
        r"鸟儿.{2,10}鸣叫",
        r"虫鸣.{2,10}声",
    ]

    # AI常用情感描写
    AI_EMOTION_PATTERNS = [
        r"心中充满了.{2,10}",
        r"心中涌起.{2,10}",
        r"心中升起.{2,10}",
        r"心中一动",
        r"心中一凛",
        r"心中一紧",
        r"心中一沉",
        r"心中一软",
        r"心中暗想",
        r"心中暗道",
        r"心中盘算",
        r"心中有数",
        r"心中不安",
        r"心中忐忑",
    ]

    def check_adjective_combos(self, text: str) -> dict[str, Any]:
        """检测AI常用形容词组合."""
        found = {}
        for combo in self.AI_ADJECTIVE_COMBOS:
            count = text.count(combo)
            if count > 0:
                found[combo] = count

        total = sum(found.values())
        return {
            "total": total,
            "details": found,
            "is_excessive": total > 3,
            "suggestion": f"AI形容词组合 {total} 处" if total > 3 else "",
        }

    def check_action_patterns(self, text: str) -> dict[str, Any]:
        """检测AI常用动作描写."""
        found = {}
        for pattern in self.AI_ACTION_PATTERNS:
            matches = re.findall(pattern, text)
            if matches:
                found[pattern] = len(matches)

        total = sum(found.values())
        return {
            "total": total,
            "details": found,
            "is_excessive": total > 5,
            "suggestion": f"AI动作描写 {total} 处" if total > 5 else "",
        }

    def check_environment_patterns(self, text: str) -> dict[str, Any]:
        """检测AI常用环境描写."""
        found = {}
        for pattern in self.AI_ENVIRONMENT_PATTERNS:
            matches = re.findall(pattern, text)
            if matches:
                found[pattern] = len(matches)

        total = sum(found.values())
        return {
            "total": total,
            "details": found,
            "is_excessive": total > 3,
            "suggestion": f"AI环境描写 {total} 处" if total > 3 else "",
        }

    def check_emotion_patterns(self, text: str) -> dict[str, Any]:
        """检测AI常用情感描写."""
        found = {}
        for pattern in self.AI_EMOTION_PATTERNS:
            matches = re.findall(pattern, text)
            if matches:
                found[pattern] = len(matches)

        total = sum(found.values())
        return {
            "total": total,
            "details": found,
            "is_excessive": total > 5,
            "suggestion": f"AI情感描写 {total} 处" if total > 5 else "",
        }
