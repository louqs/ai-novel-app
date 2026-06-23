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

        # De-AI 词汇检测
        deai_vocab_result = self.check_deai_vocabulary(text)
        deai_vocab_penalty = 0.0
        if deai_vocab_result["is_excessive"]:
            deai_vocab_penalty = 0.12
        elif deai_vocab_result["total"] > 5:
            deai_vocab_penalty = 0.06

        # De-AI 句式模板检测
        sentence_tmpl_result = self.check_sentence_templates(text)
        sentence_tmpl_penalty = 0.0
        if sentence_tmpl_result["is_excessive"]:
            sentence_tmpl_penalty = 0.15  # 高权重：句式模板是强 AI 信号

        # De-AI 语气态度检测
        tone_result = self.check_tone_attitude(text)
        tone_penalty = 0.0
        if tone_result["is_excessive"]:
            tone_penalty = 0.10

        # De-AI 段落模板检测
        para_tmpl_result = self.check_paragraph_templates(text)
        para_tmpl_penalty = 0.0
        if para_tmpl_result["is_excessive"]:
            para_tmpl_penalty = 0.08

        # De-AI 硬阈值检测
        hard_result = self.check_hard_constraints(text)
        hard_penalty = 0.0
        if not hard_result["pass"]:
            hard_penalty = min(0.20, hard_result["total_violations"] * 0.05)

        # 中文特化检测（儿化音/翻译腔/虚假亲昵）
        cn_result = self.check_chinese_specific(text)
        cn_penalty = 0.0
        if cn_result["is_excessive"]:
            cn_penalty = 0.10

        # 小说特化检测（开头/结尾/情感/场景模板）
        story_result = self.check_story_patterns(text)
        story_penalty = 0.0
        if story_result["is_excessive"]:
            story_penalty = 0.10
        elif story_result["total_violations"] > 0:
            story_penalty = 0.05

        # qu-ai-wei 51 类模式精选检测
        quai_result = self.check_quai_patterns(text)
        quai_penalty = 0.0
        if quai_result["is_excessive"]:
            quai_penalty = 0.12
        elif quai_result["total_violations"] > 0:
            quai_penalty = 0.06

        # ximen-aimazi 频率控制检测
        ximen_result = self.check_ximen_patterns(text)
        ximen_penalty = 0.0
        if ximen_result["is_excessive"]:
            ximen_penalty = 0.12
        elif ximen_result["total_violations"] > 0:
            ximen_penalty = 0.06

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
            - deai_vocab_penalty
            - sentence_tmpl_penalty
            - tone_penalty
            - para_tmpl_penalty
            - hard_penalty
            - cn_penalty
            - story_penalty
            - quai_penalty
            - ximen_penalty
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
            return {"is_uniform": False, "suggestion": ""}

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

    # =================================================================
    # De-AI 24 项检测系统集成
    # 来源: De-AI-Prompt-Enhancer-Writer-Booster-SKILL
    # =================================================================

    # --- Item 1: 膨胀意义 ---
    DEAI_SIGNIFICANCE = [
        "标志着", "象征着", "体现了", "关键时刻", "里程碑",
        "不可磨灭的印记", "不断演变的格局", "深远影响", "深远意义",
        "划时代", "历史性", "里程碑式", "里程碑意义",
    ]

    # --- Item 4: 推广/广告语言 ---
    DEAI_PROMOTIONAL = [
        "无缝", "直观", "强大", "革命性", "颠覆", "赋能", "引领",
        "卓越旅程", "卓越表现", "卓越品质", "极致体验", "极致追求",
        "全新升级", "全面升级", "重磅推出", "震撼来袭",
    ]

    # --- Item 5: 模糊归因 ---
    DEAI_VAGUE_ATTRIBUTION = [
        "专家认为", "研究表明", "有人指出", "普遍认为",
        "业内一致认为", "众所周知", "不难发现", "不难看出",
        "由此可见", "显而易见", "毋庸置疑", "不可否认",
    ]

    # --- Item 6: 挑战展望模板 ---
    DEAI_CHALLENGE_OUTLOOK = [
        "挑战与机遇并存", "未来仍需努力", "展望未来",
        "任重道远", "道阻且长", "长路漫漫", "未来可期",
        "拭目以待", "我们有理由相信", "光明前景", "这只是开始",
        "一切才刚刚开始", "新的征程", "新的篇章",
    ]

    # --- Item 7.6: 伪学术高频词 ---
    DEAI_PSEUDO_ACADEMIC = [
        "舆论场", "话语场", "权力场", "宏大叙事", "官方叙事",
        "底层逻辑", "顶层设计", "认知升级", "降维打击",
        "赛道", "红利", "抓手", "闭环", "存量", "增量",
        "博弈", "范式", "路径依赖", "权柄", "话语权",
        "壁垒", "护城河", "飞轮效应", "马太效应", "长尾效应",
        "破圈", "出圈", "种草", "拔草", "心智",
    ]

    # --- Item 7/7.1: AI 高频分析动词 ---
    DEAI_ANALYSIS_VERBS = [
        "拆解", "梳理", "剖析", "解构", "聚焦", "洞察",
        "深耕", "赋能", "助力", "践行", "驱动", "构建", "打造",
        "拆一拆", "盘一盘", "捋一捋", "聊一聊", "划重点", "敲黑板",
    ]

    # --- Item 7.1: 伪亲密语气词 ---
    DEAI_PSEUDO_INTIMATE = [
        "说白了", "本质上", "归根结底", "一句话概括",
        "简单来说", "换个角度看", "往深了说", "往大了说",
        "摊开来看", "摊开来说",
    ]

    # --- Item 22: 填充短语 (增强版) ---
    DEAI_FILLER_PHRASES = [
        "总的来说", "换句话说", "简而言之", "需要指出的是",
        "在此背景下", "与此同时", "值得注意的是", "更关键的是",
        "更要命的是", "事实上", "总之", "更进一步",
        "从某种意义上说", "可以说", "严格来说",
    ]

    def check_deai_vocabulary(self, text: str) -> dict[str, Any]:
        """De-AI 词汇检测 — 合并多项 De-AI 检测维度.

        覆盖: Item 1(膨胀意义), Item 4(推广语言), Item 5(模糊归因),
              Item 6(挑战展望), Item 7.6(伪学术词), Item 7/7.1(分析动词),
              Item 7.1(伪亲密语气), Item 22(填充短语)
        """
        categories = {
            "膨胀意义": self.DEAI_SIGNIFICANCE,
            "推广语言": self.DEAI_PROMOTIONAL,
            "模糊归因": self.DEAI_VAGUE_ATTRIBUTION,
            "挑战展望模板": self.DEAI_CHALLENGE_OUTLOOK,
            "伪学术高频词": self.DEAI_PSEUDO_ACADEMIC,
            "AI分析动词": self.DEAI_ANALYSIS_VERBS,
            "伪亲密语气": self.DEAI_PSEUDO_INTIMATE,
            "填充短语": self.DEAI_FILLER_PHRASES,
        }

        details = {}
        cat_details = {}
        for cat_name, words in categories.items():
            cat_matches = {}
            for word in words:
                count = text.count(word)
                if count > 0:
                    cat_matches[word] = count
                    details[word] = count
            if cat_matches:
                cat_details[cat_name] = cat_matches

        total = sum(details.values())
        char_count = max(len(text), 1)
        density = total / (char_count / 1000)

        # 阈值: > 3/千字 = 过度
        is_excessive = density > 3

        suggestion = ""
        if is_excessive:
            top_cats = sorted(cat_details.keys(), key=lambda k: sum(cat_details[k].values()), reverse=True)[:3]
            suggestion = f"De-AI词汇密度 {density:.1f}/千字（阈值 3）。主要问题: {'、'.join(top_cats)}"

        return {
            "total": total,
            "density": round(density, 2),
            "categories": cat_details,
            "details": details,
            "is_excessive": is_excessive,
            "suggestion": suggestion,
        }

    # --- Item 7.3: 逻辑连接词 ---
    DEAI_LOGICAL_CONNECTORS = [
        (r"一旦.{1,20}就", "一旦...就"),
        (r"只要.{1,20}就", "只要...就"),
        (r"只有.{1,20}才", "只有...才"),
        (r"无论.{1,20}都", "无论...都"),
        (r"不管.{1,20}都", "不管...都"),
        (r"正是因为.{1,20}所以", "正是因为...所以"),
        (r"随着.{2,15}的发展", "随着...的发展"),
        (r"随着.{2,15}的推进", "随着...的推进"),
        (r"通过.{2,15}来", "通过...来"),
    ]

    # --- Item 7.4: 戏剧化揭示 ---
    DEAI_DRAMATIC_REVELATION = [
        (r"遮羞布", "遮羞布"),
        (r"面具", "面具(戏剧化)"),
        (r"画皮", "画皮"),
        (r"伪装", "伪装(戏剧化)"),
        (r"外衣", "外衣(戏剧化)"),
        (r"扯下|撕下|揭下|剥开|戳穿|揭穿", "扯下/撕下/揭穿"),
        (r"揭开真面目", "揭开真面目"),
        (r"戳穿真相", "戳穿真相"),
    ]

    # --- Item 7.5: 极端判断句 ---
    DEAI_EXTREME_JUDGMENT = [
        r"最.{1,10}的地方在于",
        r"真正.{1,10}的是",
        r"残酷之处在于",
        r"更残酷的是",
        r"更可怕的是",
        r"更讽刺的是",
        r"更令人.{1,6}的是",
    ]

    # --- Item 9.1: 二元对比壳 ---
    DEAI_BINARY_CONTRAST = [
        r"不是.{1,15}而是",
        r"并非.{1,15}而是",
        r"不在于.{1,15}而在于",
    ]

    # --- Item 9.4: "却"整洁对比壳 ---
    DEAI_NEAT_CONTRAST = [
        r"字面上.{1,20}却",
        r"谐音里却藏着",
        r"表面上.{1,20}却",
    ]

    # --- Item 9.5: "很容易"解释捷径 ---
    DEAI_EASY_SHORTCUT = [
        r"很容易.{0,3}把",
        r"很容易.{0,3}被",
        r"很容易.{0,3}让",
        r"很容易.{0,3}想到",
        r"很容易.{0,3}变成",
        r"很容易.{0,3}说成",
        r"很容易.{0,3}理解成",
    ]

    # --- Item 9.8: 指示性定位壳 ---
    DEAI_INDICATIVE_POSITION = [
        r"正卡在这条裂缝里",
        r"就卡在这道缝隙里",
        r"正落在这条夹缝里",
        r"难处.{0,5}正在这里",
        r"麻烦的地方.{0,5}正在这里",
    ]

    def check_sentence_templates(self, text: str) -> dict[str, Any]:
        """De-AI 句式模板检测.

        覆盖: Item 7.3(逻辑连接词≤2), Item 7.4(戏剧化揭示零容忍),
              Item 7.5(极端判断零容忍), Item 9.1(二元对比壳零容忍),
              Item 9.4/9.5/9.8(各类句式壳零容忍)
        """
        categories = {}

        # Item 7.3: 逻辑连接词 (阈值: 全文 ≤ 2)
        conn_matches = []
        for pattern, label in self.DEAI_LOGICAL_CONNECTORS:
            found = re.findall(pattern, text)
            if found:
                conn_matches.extend([(label, m) for m in found])
        if conn_matches:
            categories["逻辑连接词"] = {
                "count": len(conn_matches),
                "threshold": 2,
                "items": [m[0] for m in conn_matches],
            }

        # Item 7.4: 戏剧化揭示 (零容忍)
        dram_matches = []
        for pattern, label in self.DEAI_DRAMATIC_REVELATION:
            found = re.findall(pattern, text)
            if found:
                dram_matches.extend([(label, m) for m in found])
        if dram_matches:
            categories["戏剧化揭示"] = {
                "count": len(dram_matches),
                "threshold": 0,
                "items": [m[0] for m in dram_matches],
            }

        # Item 7.5: 极端判断句 (零容忍)
        extreme_matches = []
        for pattern in self.DEAI_EXTREME_JUDGMENT:
            found = re.findall(pattern, text)
            if found:
                extreme_matches.extend(found)
        if extreme_matches:
            categories["极端判断句"] = {
                "count": len(extreme_matches),
                "threshold": 0,
                "items": extreme_matches,
            }

        # Item 9.1: 二元对比壳 (零容忍)
        binary_matches = []
        for pattern in self.DEAI_BINARY_CONTRAST:
            found = re.findall(pattern, text)
            if found:
                binary_matches.extend(found)
        if binary_matches:
            categories["二元对比壳"] = {
                "count": len(binary_matches),
                "threshold": 0,
                "items": binary_matches,
            }

        # Item 9.4: "却"整洁对比壳 (零容忍)
        neat_matches = []
        for pattern in self.DEAI_NEAT_CONTRAST:
            found = re.findall(pattern, text)
            if found:
                neat_matches.extend(found)
        if neat_matches:
            categories["整洁对比壳"] = {
                "count": len(neat_matches),
                "threshold": 0,
                "items": neat_matches,
            }

        # Item 9.5: "很容易"解释捷径 (零容忍)
        easy_matches = []
        for pattern in self.DEAI_EASY_SHORTCUT:
            found = re.findall(pattern, text)
            if found:
                easy_matches.extend(found)
        if easy_matches:
            categories["很容易捷径"] = {
                "count": len(easy_matches),
                "threshold": 0,
                "items": easy_matches,
            }

        # Item 9.8: 指示性定位壳 (零容忍)
        pos_matches = []
        for pattern in self.DEAI_INDICATIVE_POSITION:
            found = re.findall(pattern, text)
            if found:
                pos_matches.extend(found)
        if pos_matches:
            categories["指示性定位壳"] = {
                "count": len(pos_matches),
                "threshold": 0,
                "items": pos_matches,
            }

        total_violations = sum(
            1 for v in categories.values()
            if v["count"] > v.get("threshold", 0)
        )

        suggestions = []
        for name, v in categories.items():
            threshold = v.get("threshold", 0)
            if v["count"] > threshold:
                suggestions.append(f"{name}: {v['count']}次（阈值 {threshold}）")

        return {
            "categories": categories,
            "total_violations": total_violations,
            "is_excessive": total_violations > 0,
            "suggestion": "；".join(suggestions) if suggestions else "",
        }

    # --- Item 19: 协作沟通痕迹 ---
    DEAI_COLLABORATIVE_TONE = [
        "让我们", "接下来我们将", "本文将", "希望这能帮助你",
        "下面我会", "我们先来看", "下面我们", "接下来我们",
        "作为AI", "截至我的知识", "我无法访问", "作为语言模型",
    ]

    # --- Item 21: 谄媚语气 ---
    DEAI_SYCOPHANTIC = [
        "很高兴", "非常荣幸", "感谢你的提问", "令人振奋",
        "令人鼓舞", "令人欣慰", "令人感动", "令人敬佩",
    ]

    # --- Item 23: 过度对冲 ---
    DEAI_HEDGING = [
        "某种程度上", "可能", "或许", "似乎", "相对而言",
        "在一定程度上", "从某种角度", "或多或少",
    ]

    def check_tone_attitude(self, text: str) -> dict[str, Any]:
        """De-AI 语气态度检测.

        覆盖: Item 19(协作沟通), Item 21(谄媚语气), Item 23(过度对冲)
        """
        categories = {}

        # Item 19: 协作沟通痕迹 (零容忍)
        collab_matches = {}
        for word in self.DEAI_COLLABORATIVE_TONE:
            count = text.count(word)
            if count > 0:
                collab_matches[word] = count
        if collab_matches:
            categories["协作沟通痕迹"] = {
                "count": sum(collab_matches.values()),
                "threshold": 0,
                "items": collab_matches,
            }

        # Item 21: 谄媚语气 (零容忍)
        syc_matches = {}
        for word in self.DEAI_SYCOPHANTIC:
            count = text.count(word)
            if count > 0:
                syc_matches[word] = count
        if syc_matches:
            categories["谄媚语气"] = {
                "count": sum(syc_matches.values()),
                "threshold": 0,
                "items": syc_matches,
            }

        # Item 23: 过度对冲 (≤ 2)
        hedge_matches = {}
        for word in self.DEAI_HEDGING:
            count = text.count(word)
            if count > 0:
                hedge_matches[word] = count
        hedge_total = sum(hedge_matches.values())
        if hedge_total > 2:
            categories["过度对冲"] = {
                "count": hedge_total,
                "threshold": 2,
                "items": hedge_matches,
            }

        total_violations = sum(1 for v in categories.values() if v["count"] > v.get("threshold", 0))

        suggestions = []
        for name, v in categories.items():
            threshold = v.get("threshold", 0)
            if v["count"] > threshold:
                suggestions.append(f"{name}: {v['count']}次（阈值 {threshold}）")

        return {
            "categories": categories,
            "total_violations": total_violations,
            "is_excessive": total_violations > 0,
            "suggestion": "；".join(suggestions) if suggestions else "",
        }

    # --- Item 10: 三项法则过度使用 ---
    DEAI_TRIPLET_PATTERNS = [
        r"既.{1,10}又.{1,10}还",
        r"首先.{1,20}其次.{1,20}最后",
        r"第一.{1,15}第二.{1,15}第三",
    ]

    # --- Item 7.2: 分析师漫游姿态 ---
    DEAI_ANALYST_WALKTHROUGH = [
        r"把.{1,10}(做完|说完|看完|拆完|理完)[，,]再",
        r"先看.{1,10}接着看",
        r"看完了.{1,10}再说",
        r"上面说的是.{1,10}下面说",
    ]

    # --- Item 6: 挑战展望模板段落 ---
    DEAI_OUTLOOK_PARAGRAPHS = [
        r"挑战与机遇并存",
        r"未来仍需努力",
        r"展望未来.{1,20}",
        r"在未来的发展中",
    ]

    def check_paragraph_templates(self, text: str) -> dict[str, Any]:
        """De-AI 段落模板检测.

        覆盖: Item 10(三项法则), Item 7.2(分析师漫游), Item 6(展望模板)
        """
        categories = {}

        # Item 10: 三项法则
        triplet_matches = []
        for pattern in self.DEAI_TRIPLET_PATTERNS:
            found = re.findall(pattern, text)
            if found:
                triplet_matches.extend(found)
        if triplet_matches:
            categories["三项法则"] = {
                "count": len(triplet_matches),
                "threshold": 2,
                "items": triplet_matches,
            }

        # Item 7.2: 分析师漫游
        analyst_matches = []
        for pattern in self.DEAI_ANALYST_WALKTHROUGH:
            found = re.findall(pattern, text)
            if found:
                analyst_matches.extend(found)
        if analyst_matches:
            categories["分析师漫游"] = {
                "count": len(analyst_matches),
                "threshold": 0,
                "items": analyst_matches,
            }

        # Item 6: 展望模板
        outlook_matches = []
        for pattern in self.DEAI_OUTLOOK_PARAGRAPHS:
            found = re.findall(pattern, text)
            if found:
                outlook_matches.extend(found)
        if outlook_matches:
            categories["展望模板"] = {
                "count": len(outlook_matches),
                "threshold": 0,
                "items": outlook_matches,
            }

        total_violations = sum(1 for v in categories.values() if v["count"] > v.get("threshold", 0))

        suggestions = []
        for name, v in categories.items():
            threshold = v.get("threshold", 0)
            if v["count"] > threshold:
                suggestions.append(f"{name}: {v['count']}次（阈值 {threshold}）")

        return {
            "categories": categories,
            "total_violations": total_violations,
            "is_excessive": total_violations > 0,
            "suggestion": "；".join(suggestions) if suggestions else "",
        }

    def check_hard_constraints(self, text: str) -> dict[str, Any]:
        """De-AI 12 项硬阈值检测.

        返回每项约束的违反情况。
        """
        violations = []
        char_count = max(len(text), 1)

        # 1. 二元对比壳: 全文 ≤ 1
        binary_count = sum(len(re.findall(p, text)) for p in self.DEAI_BINARY_CONTRAST)
        if binary_count > 1:
            violations.append({"constraint": "二元对比壳", "count": binary_count, "threshold": 1})

        # 2. 第二人称: 全文 ≤ 1
        second_person = len(re.findall(r"你(?!们)", text))
        if second_person > 1:
            violations.append({"constraint": "第二人称'你'", "count": second_person, "threshold": 1})

        # 3. 标记词: ≤ 2
        marker_words = ["更关键", "更要命", "换句话说", "事实上", "值得注意", "总之", "与此同时"]
        marker_count = sum(text.count(w) for w in marker_words)
        if marker_count > 2:
            violations.append({"constraint": "标记词", "count": marker_count, "threshold": 2})

        # 4. 讲义动作: 零容忍
        lecture_words = ["拆一拆", "盘一盘", "捋一捋", "聊一聊", "划重点", "敲黑板",
                        "说白了", "本质上", "归根结底", "简单来说"]
        lecture_count = sum(text.count(w) for w in lecture_words)
        if lecture_count > 0:
            violations.append({"constraint": "讲义动作", "count": lecture_count, "threshold": 0})

        # 5. 分析动词堆砌: ≤ 2
        analysis_verbs = ["拆解", "梳理", "剖析", "解构", "聚焦", "洞察",
                         "深耕", "赋能", "助力", "践行", "驱动", "构建", "打造"]
        analysis_count = sum(text.count(w) for w in analysis_verbs)
        if analysis_count > 2:
            violations.append({"constraint": "分析动词堆砌", "count": analysis_count, "threshold": 2})

        # 6. 条件句堆叠: ≤ 2
        cond_patterns = [
            r"一旦.{1,20}就", r"只有.{1,20}才", r"无论.{1,20}都",
            r"随着.{2,15}的发展", r"正是因为.{1,20}所以", r"通过.{2,15}来",
        ]
        cond_count = sum(len(re.findall(p, text)) for p in cond_patterns)
        if cond_count > 2:
            violations.append({"constraint": "条件句堆叠", "count": cond_count, "threshold": 2})

        # 7. 戏剧化揭示: 零容忍
        dram_words = ["遮羞布", "面具", "画皮", "外衣", "揭开真面目", "戳穿真相"]
        dram_count = sum(text.count(w) for w in dram_words)
        if dram_count > 0:
            violations.append({"constraint": "戏剧化揭示", "count": dram_count, "threshold": 0})

        # 8. 段落同构: 检查连续段落是否都是"观点+解释+总结"
        paragraphs = [p.strip() for p in text.split('\n\n') if p.strip() and len(p.strip()) > 20]
        if len(paragraphs) >= 3:
            # 检查是否有连续3段以上结构相似（句数相近）
            para_sent_counts = []
            for p in paragraphs:
                sents = re.split(r'[。！？]', p)
                sents = [s for s in sents if s.strip()]
                para_sent_counts.append(len(sents))
            if len(para_sent_counts) >= 3:
                uniform_count = 0
                max_uniform = 0
                for i in range(1, len(para_sent_counts)):
                    if abs(para_sent_counts[i] - para_sent_counts[i-1]) <= 1:
                        uniform_count += 1
                        max_uniform = max(max_uniform, uniform_count)
                    else:
                        uniform_count = 0
                if max_uniform >= 2:
                    violations.append({
                        "constraint": "段落同构",
                        "count": max_uniform + 1,
                        "threshold": 2,
                        "detail": f"连续 {max_uniform + 1} 段句数相近",
                    })

        # 9. 段落结尾: 检查是否每段都以抽象结论结尾
        conclusion_endings = [
            r"这.{0,10}(意味着|说明|表明|反映)",
            r"(因此|所以|总之|综上).{0,15}$",
            r"(未来|今后).{0,10}(将|会|必)",
        ]
        if len(paragraphs) >= 3:
            conclusion_count = 0
            for p in paragraphs[-3:]:
                last_sent = re.split(r'[。！？]', p)[-1].strip()
                for pat in conclusion_endings:
                    if re.search(pat, last_sent):
                        conclusion_count += 1
                        break
            if conclusion_count >= 3:
                violations.append({
                    "constraint": "段尾抽象结论",
                    "count": conclusion_count,
                    "threshold": 2,
                })

        return {
            "violations": violations,
            "total_violations": len(violations),
            "pass": len(violations) == 0,
            "suggestion": "；".join(
                f"{v['constraint']}: {v['count']}次（阈值 {v['threshold']}）"
                for v in violations
            ) if violations else "",
        }

    # =================================================================
    # 中文特化检测（来自 my-writing SKILL.md）
    # =================================================================

    # 儿化音黑名单
    ER_ER_SOUNDS = [
        "那儿", "这儿", "一点儿", "玩儿", "今儿", "明儿",
        "后儿", "前儿", "一会儿", "这点儿", "那点儿",
        "哪儿", "有点儿", "没事儿", "闹着玩儿",
    ]

    # 翻译腔句式
    TRANSLATION_PATTERNS = [
        r"当.{2,15}时",
        r"当.{2,15}的时候",
    ]

    # 虚假亲昵词
    FALSE_INTIMACY = [
        "咱们", "咱",
    ]

    # =================================================================
    # 小说特化检测（来自 human-story-writer SKILL.md）
    # =================================================================

    # AI 开头模板
    STORY_OPENING_TEMPLATES = [
        r"在.{2,15}的世界里",
        r"在.{2,15}中，有",
        r"阳光.{0,5}洒在",
        r"夕阳西下",
        r"金色的阳光",
        r"人生就像一场旅行",
        r"人生就像.{2,10}",
        r"繁华.{0,5}的都市",
        r"喧嚣.{0,5}的城市",
        r"孤独的身影",
        r"在这个.{2,10}的时代",
    ]

    # AI 结尾模板
    STORY_ENDING_TEMPLATES = [
        r"原来[，,].{2,20}$",
        r"那一刻[，,]他.{0,5}明白了",
        r"那一刻[，,]她.{0,5}明白了",
        r"那一刻[，,]他.{0,5}终于",
        r"那一刻[，,]她.{0,5}终于",
        r"他终于明白了",
        r"她终于明白了",
        r"他终于意识到",
        r"她终于意识到",
        r"人生就是.{2,15}旅程",
        r"每个人都是自己故事里的主角",
        r"岁月如.{2,8}般逝去",
        r"带走了.{2,10}却带不走",
    ]

    # AI 情感抽象表达
    STORY_EMOTION_ABSTRACT = [
        "感到无比", "感到一阵莫名的", "内心涌起一股",
        "难以言喻的", "无法形容的", "说不清道不明的",
        "心中充满了", "心中涌起", "心中升起",
        "一股莫名的", "一种说不出的",
        "感到无比痛苦", "感到无比悲伤", "感到无比孤独",
        "感到无比绝望", "感到无比失落",
    ]

    # AI 场景描写模板
    STORY_SCENE_TEMPLATES = [
        r"破旧的.{2,6}弥漫着.{2,10}的气息",
        r"昏暗的灯光下.*斑驳的.{2,6}诉说着",
        r"岁月的沧桑",
        r"时光的痕迹",
        r"历史的沉淀",
        r"空气中弥漫着.{2,10}",
        r"夜幕.{0,5}降临",
        r"晨光.{0,5}初现",
    ]

    # 小说中过度使用的书面语
    STORY_FORMAL_WORDS = [
        "摒弃", "亦", "乃至", "所谓", "诚然",
        "固然", "未尝", "何尝", "盖因", "乃",
        "遂", "竟", "遂即", "旋即", "俄而",
    ]

    def check_story_patterns(self, text: str) -> dict[str, Any]:
        """小说特化 AI 模式检测.

        覆盖: 开头模板、结尾模板、情感抽象表达、场景描写模板、书面语过度使用
        来源: human-story-writer SKILL.md
        """
        categories = {}

        # 开头模板检测（检查前 200 字）
        opening_text = text[:200] if len(text) > 200 else text
        opening_matches = []
        for pattern in self.STORY_OPENING_TEMPLATES:
            found = re.findall(pattern, opening_text)
            if found:
                opening_matches.extend(found)
        if opening_matches:
            categories["AI开头模板"] = {
                "count": len(opening_matches),
                "threshold": 0,
                "items": opening_matches,
            }

        # 结尾模板检测（检查最后 300 字）
        ending_text = text[-300:] if len(text) > 300 else text
        ending_matches = []
        for pattern in self.STORY_ENDING_TEMPLATES:
            found = re.findall(pattern, ending_text)
            if found:
                ending_matches.extend(found)
        if ending_matches:
            categories["AI结尾模板"] = {
                "count": len(ending_matches),
                "threshold": 0,
                "items": ending_matches,
            }

        # 情感抽象表达检测
        emotion_matches = {}
        for phrase in self.STORY_EMOTION_ABSTRACT:
            count = text.count(phrase)
            if count > 0:
                emotion_matches[phrase] = count
        if emotion_matches:
            total = sum(emotion_matches.values())
            categories["情感抽象表达"] = {
                "count": total,
                "threshold": 3,
                "items": emotion_matches,
            }

        # 场景描写模板检测
        scene_matches = []
        for pattern in self.STORY_SCENE_TEMPLATES:
            found = re.findall(pattern, text)
            if found:
                scene_matches.extend(found)
        if scene_matches:
            categories["场景描写模板"] = {
                "count": len(scene_matches),
                "threshold": 2,
                "items": scene_matches,
            }

        # 书面语过度使用检测
        formal_matches = {}
        for word in self.STORY_FORMAL_WORDS:
            count = text.count(word)
            if count > 0:
                formal_matches[word] = count
        if formal_matches:
            total = sum(formal_matches.values())
            categories["书面语过度"] = {
                "count": total,
                "threshold": 3,
                "items": formal_matches,
            }

        total_violations = sum(1 for v in categories.values() if v["count"] > v.get("threshold", 0))

        suggestions = []
        for name, v in categories.items():
            threshold = v.get("threshold", 0)
            if v["count"] > threshold:
                suggestions.append(f"{name}: {v['count']}次（阈值 {threshold}）")

        return {
            "categories": categories,
            "total_violations": total_violations,
            "is_excessive": total_violations > 0,
            "suggestion": "；".join(suggestions) if suggestions else "",
        }

    # =================================================================
    # qu-ai-wei 检测系统集成（51 类 AI 模式精选）
    # 来源: qu-ai-wei-main/references/patterns.md
    # =================================================================

    # --- #7: AI 高频词过载 (3+ 共现触发) ---
    QUAI_HIGH_FREQ_WORDS = [
        "赋能", "助力", "打造", "护航", "抓手", "闭环", "生态",
        "底层逻辑", "核心竞争力", "高质量发展", "全方位", "多维度",
        "多层次", "全链路", "一站式", "根植于", "聚焦点",
        "至关重要", "转折点", "不可磨灭",
    ]

    # --- #8: 华丽意象词堆砌 ---
    QUAI_ORNATE_IMAGERY = [
        "璀璨", "熠熠生辉", "绽放光芒", "画卷", "乐章", "华章",
        "扬帆起航", "乘风破浪", "砥砺奋进", "蓬勃发展",
        "谱写新篇章", "书写新华章", "注入新活力",
    ]

    # --- #11: "的的不休" (3+ 连续的，间隔含标点) ---
    QUAI_DEI_PATTERN = r"的[^。，！？\n]{0,15}的[^。，！？\n]{0,15}的"

    # --- #12: "进行+V" (语体敏感) ---
    QUAI_JINXING_PATTERNS = [
        r"进行.{1,6}(讨论|分析|研究|沟通|优化|思考|探索|调整|改进|评估)",
    ]

    # --- #29: 空洞积极结尾 ---
    QUAI_EMPTY_ENDINGS = [
        "未来可期", "让我们共同期待", "相信在不久的将来",
        "美好的明天", "光明的前景", "大有可为", "拭目以待",
        "让我们一起", "让我们共同", "让我们携手",
    ]

    # --- #33: 翻译腔残留 (英文句法残留) ---
    QUAI_TRANSLATIONESE = [
        r"作为一个.{1,10}[,，]",
        r"与.{5,20}相关的",
        r"在.{5,15}的情况下",
        r"通过.{5,15}的方式",
        r"由于.{5,15}的原因",
        r"关于.{5,15}的问题",
        r"为了.{5,15}的目的",
    ]

    # --- #39: 物理动作动词用于思考过程 ---
    QUAI_PHYSICAL_IN_MENTAL = [
        "接住", "击穿", "拆解", "锋利", "不崩", "撑不住",
        "打穿", "拎清", "咬住", "扛住", "落地", "扣住",
    ]

    # --- #40: "X很Y:" 形容词+冒号起手 ---
    QUAI_ADJ_COLON = [
        r"逻辑很清晰[：:]",
        r"结论很明确[：:]",
        r"道理很简单[：:]",
        r"答案很直接[：:]",
        r"事实很清楚[：:]",
        r"问题很明显[：:]",
    ]

    # --- #49: 有毒正能量拼接 ---
    QUAI_TOXIC_POSITIVITY = [
        r"(打工人|内卷|躺平|摆烂|社畜|996).{0,30}(但|但是|不过|然而).{0,20}(希望|阳光|温暖|美好|未来|坚持|努力|加油)",
    ]

    # --- #48: 否定对举下定义句 ---
    QUAI_NEGATION_DEFINE = [
        r"这不是.{1,15}[，,]而是.{1,15}",
        r"不是.{1,10}[，,]也不是.{1,10}[，,]而是",
        r"既不是.{1,10}[，,]也不是.{1,10}",
    ]

    def check_quai_patterns(self, text: str) -> dict[str, Any]:
        """qu-ai-wei 51 类 AI 模式精选检测.

        覆盖: #7(高频词), #8(华丽意象), #11(的的不休), #12(进行+V),
              #29(空洞结尾), #33(翻译腔), #39(物理动词用于思考),
              #40(形容词+冒号), #49(有毒正能量), #48(否定对举)
        """
        categories = {}

        # #7: AI 高频词过载 (3+ 共现)
        hf_matches = {}
        for word in self.QUAI_HIGH_FREQ_WORDS:
            count = text.count(word)
            if count > 0:
                hf_matches[word] = count
        if len(hf_matches) >= 3:
            categories["AI高频词过载"] = {
                "count": sum(hf_matches.values()),
                "threshold": 2,
                "items": hf_matches,
            }

        # #8: 华丽意象词堆砌
        ornate_matches = {}
        for word in self.QUAI_ORNATE_IMAGERY:
            count = text.count(word)
            if count > 0:
                ornate_matches[word] = count
        if ornate_matches:
            total = sum(ornate_matches.values())
            categories["华丽意象堆砌"] = {
                "count": total,
                "threshold": 2,
                "items": ornate_matches,
            }

        # #11: 的的不休 (3+ 连续的)
        dei_matches = re.findall(self.QUAI_DEI_PATTERN, text)
        if dei_matches:
            categories["的的不休"] = {
                "count": len(dei_matches),
                "threshold": 0,
                "items": dei_matches,
            }

        # #12: 进行+V (语体敏感，2+ 触发)
        jx_matches = []
        for pattern in self.QUAI_JINXING_PATTERNS:
            found = re.findall(pattern, text)
            if found:
                jx_matches.extend(found)
        if len(jx_matches) >= 2:
            categories["进行+V过度"] = {
                "count": len(jx_matches),
                "threshold": 1,
                "items": jx_matches,
            }

        # #29: 空洞积极结尾 (检查最后 200 字)
        ending_text = text[-200:] if len(text) > 200 else text
        ending_matches = [w for w in self.QUAI_EMPTY_ENDINGS if w in ending_text]
        if ending_matches:
            categories["空洞积极结尾"] = {
                "count": len(ending_matches),
                "threshold": 0,
                "items": ending_matches,
            }

        # #33: 翻译腔残留
        trans_matches = []
        for pattern in self.QUAI_TRANSLATIONESE:
            found = re.findall(pattern, text)
            if found:
                trans_matches.extend(found)
        if trans_matches:
            categories["翻译腔残留"] = {
                "count": len(trans_matches),
                "threshold": 1,
                "items": trans_matches,
            }

        # #39: 物理动作动词用于思考过程
        phys_matches = {}
        for word in self.QUAI_PHYSICAL_IN_MENTAL:
            count = text.count(word)
            if count > 0:
                phys_matches[word] = count
        if phys_matches:
            categories["物理动词思维化"] = {
                "count": sum(phys_matches.values()),
                "threshold": 2,
                "items": phys_matches,
            }

        # #40: 形容词+冒号起手
        adj_colon_matches = []
        for pattern in self.QUAI_ADJ_COLON:
            found = re.findall(pattern, text)
            if found:
                adj_colon_matches.extend(found)
        if adj_colon_matches:
            categories["形容词冒号起手"] = {
                "count": len(adj_colon_matches),
                "threshold": 0,
                "items": adj_colon_matches,
            }

        # #49: 有毒正能量拼接
        toxic_matches = []
        for pattern in self.QUAI_TOXIC_POSITIVITY:
            found = re.findall(pattern, text)
            if found:
                toxic_matches.extend(found)
        if toxic_matches:
            categories["有毒正能量拼接"] = {
                "count": len(toxic_matches),
                "threshold": 0,
                "items": toxic_matches,
            }

        # #48: 否定对举下定义句
        neg_matches = []
        for pattern in self.QUAI_NEGATION_DEFINE:
            found = re.findall(pattern, text)
            if found:
                neg_matches.extend(found)
        if neg_matches:
            categories["否定对举定义"] = {
                "count": len(neg_matches),
                "threshold": 1,
                "items": neg_matches,
            }

        total_violations = sum(1 for v in categories.values() if v["count"] > v.get("threshold", 0))

        suggestions = []
        for name, v in categories.items():
            threshold = v.get("threshold", 0)
            if v["count"] > threshold:
                suggestions.append(f"{name}: {v['count']}次（阈值 {threshold}）")

        return {
            "categories": categories,
            "total_violations": total_violations,
            "is_excessive": total_violations > 0,
            "suggestion": "；".join(suggestions) if suggestions else "",
        }

    # =================================================================
    # ximen-aimazi 频率控制检测（按密度而非出现）
    # 来源: ximen-aimazi-main/references/anti-ai-writing.md
    # =================================================================

    # 弱化副词（每1000字不超过2个 = AI签名）
    XIMEN_WEAK_ADVERBS = ["微微", "淡淡", "缓缓", "轻轻", "不禁", "不由自主"]

    # 直白情感标签（每章不超过1个）
    XIMEN_EMOTION_LABELS = ["紧张", "焦急", "愤怒", "恐惧", "兴奋", "失落"]

    # 比喻词（每章不超过1个，必须是功能性比喻）
    XIMEN_METAPHOR_WORDS = ["像", "就像", "好像", "如同", "仿佛", "宛如", "犹如", "好似"]

    # 禁语表一级（出现即替换）
    XIMEN_BANNED_TIER1 = [
        "映入眼帘", "心中暗道", "心中一动", "心头一震", "心下了然",
        "心底泛起", "目光如炬", "闪烁着光芒", "不由自主", "情不自禁",
        "嘴角勾起", "嘴角微扬", "眼中闪过", "空气凝固", "笑容凝固",
        "深吸一口气", "指节泛白", "眉头微皱", "瞳孔微缩",
    ]

    # 禁语表二级（高频出现时替换）
    XIMEN_BANNED_TIER2 = [
        "终于明白了", "这才意识到", "取而代之", "引以为傲",
        "不容置疑", "不易察觉", "显而易见", "毫无疑问", "不可否认",
        "坚定", "狡黠", "深邃", "凛冽", "虔诚", "狰狞",
    ]

    # 模板化句式
    XIMEN_TEMPLATE_SENTENCES = [
        r"眼中闪过一丝.{1,6}",
        r"嘴角勾起一抹.{1,6}",
        r"心中涌起一股.{1,6}",
        r"他.{0,2}说道",
        r"她.{0,2}说道",
        r"带着一丝.{1,6}",
        r"仿佛能.{1,10}一般",
    ]

    # 升华式章尾
    XIMEN_SUBLIME_ENDINGS = [
        "终于明白了", "这才意识到", "这一夜", "注定无人入眠",
        "他不知道的是", "更大的风暴", "人生就是这样",
    ]

    def check_ximen_patterns(self, text: str) -> dict[str, Any]:
        """ximen-aimazi 频率控制检测.

        核心原则：看密度，不看出现。单次出现是正常中文，堆砌才是 AI。
        """
        categories = {}
        char_count = max(len(text), 1)
        per_1000 = lambda n: n / (char_count / 1000)

        # 弱化副词（每1000字不超过2个）
        weak_matches = {}
        for word in self.XIMEN_WEAK_ADVERBS:
            count = text.count(word)
            if count > 0:
                weak_matches[word] = count
        weak_total = sum(weak_matches.values())
        weak_density = per_1000(weak_total)
        if weak_density > 2:
            categories["弱化副词过密"] = {
                "count": weak_total,
                "density": round(weak_density, 1),
                "threshold": "2/千字",
                "_limit": 2,
                "items": weak_matches,
            }

        # 直白情感标签（每1000字不超过1个）
        emotion_matches = {}
        for word in self.XIMEN_EMOTION_LABELS:
            count = text.count(word)
            if count > 0:
                emotion_matches[word] = count
        emotion_total = sum(emotion_matches.values())
        emotion_density = per_1000(emotion_total)
        if emotion_density > 1:
            categories["情感标签过密"] = {
                "count": emotion_total,
                "density": round(emotion_density, 1),
                "threshold": "1/千字",
                "_limit": 1,
                "items": emotion_matches,
            }

        # 比喻词（每1000字不超过1个）
        metaphor_matches = {}
        for word in self.XIMEN_METAPHOR_WORDS:
            count = text.count(word)
            if count > 0:
                metaphor_matches[word] = count
        metaphor_total = sum(metaphor_matches.values())
        metaphor_density = per_1000(metaphor_total)
        if metaphor_density > 1:
            categories["比喻词过密"] = {
                "count": metaphor_total,
                "density": round(metaphor_density, 1),
                "threshold": "1/千字",
                "_limit": 1,
                "items": metaphor_matches,
            }

        # 禁语表一级（出现即标记）
        banned1_matches = {}
        for word in self.XIMEN_BANNED_TIER1:
            count = text.count(word)
            if count > 0:
                banned1_matches[word] = count
        if banned1_matches:
            categories["禁语一级"] = {
                "count": sum(banned1_matches.values()),
                "threshold": 0,
                "items": banned1_matches,
            }

        # 禁语表二级（每1000字不超过1个）
        banned2_matches = {}
        for word in self.XIMEN_BANNED_TIER2:
            count = text.count(word)
            if count > 0:
                banned2_matches[word] = count
        banned2_total = sum(banned2_matches.values())
        banned2_density = per_1000(banned2_total)
        if banned2_density > 1:
            categories["禁语二级过密"] = {
                "count": banned2_total,
                "density": round(banned2_density, 1),
                "threshold": "1/千字",
                "_limit": 1,
                "items": banned2_matches,
            }

        # 模板化句式
        template_matches = []
        for pattern in self.XIMEN_TEMPLATE_SENTENCES:
            found = re.findall(pattern, text)
            if found:
                template_matches.extend(found)
        if len(template_matches) >= 3:
            categories["模板句式"] = {
                "count": len(template_matches),
                "threshold": 2,
                "items": template_matches,
            }

        # 升华式章尾（检查最后 200 字）
        ending_text = text[-200:] if len(text) > 200 else text
        ending_matches = [w for w in self.XIMEN_SUBLIME_ENDINGS if w in ending_text]
        if ending_matches:
            categories["升华式章尾"] = {
                "count": len(ending_matches),
                "threshold": 0,
                "items": ending_matches,
            }

        # 计算违规数（密度类用 _limit 做数值比较，非密度类直接用 threshold）
        total_violations = 0
        suggestions = []
        for name, v in categories.items():
            limit = v.get("_limit", 0)
            density = v.get("density")
            threshold = v.get("threshold", 0)
            if density is not None and limit > 0:
                if density > limit:
                    total_violations += 1
                    suggestions.append(f"{name}: {density}/千字（阈值 {limit}）")
            elif isinstance(threshold, (int, float)) and v["count"] > threshold:
                total_violations += 1
                suggestions.append(f"{name}: {v['count']}次（阈值 {threshold}）")

        return {
            "categories": categories,
            "total_violations": total_violations,
            "is_excessive": total_violations > 0,
            "suggestion": "；".join(suggestions) if suggestions else "",
        }

    def check_chinese_specific(self, text: str) -> dict[str, Any]:
        """中文特化检测 — 儿化音、翻译腔、虚假亲昵.

        来源: my-writing SKILL.md 的严禁行为清单。
        """
        categories = {}

        # 儿化音 (零容忍)
        er_matches = {}
        for word in self.ER_ER_SOUNDS:
            count = text.count(word)
            if count > 0:
                er_matches[word] = count
        if er_matches:
            categories["儿化音"] = {
                "count": sum(er_matches.values()),
                "threshold": 0,
                "items": er_matches,
            }

        # 翻译腔 (零容忍)
        trans_matches = []
        for pattern in self.TRANSLATION_PATTERNS:
            found = re.findall(pattern, text)
            if found:
                trans_matches.extend(found)
        if trans_matches:
            categories["翻译腔"] = {
                "count": len(trans_matches),
                "threshold": 0,
                "items": trans_matches,
            }

        # 虚假亲昵 (零容忍)
        intimacy_matches = {}
        for word in self.FALSE_INTIMACY:
            count = text.count(word)
            if count > 0:
                intimacy_matches[word] = count
        if intimacy_matches:
            categories["虚假亲昵"] = {
                "count": sum(intimacy_matches.values()),
                "threshold": 0,
                "items": intimacy_matches,
            }

        total_violations = sum(1 for v in categories.values() if v["count"] > v.get("threshold", 0))

        suggestions = []
        for name, v in categories.items():
            threshold = v.get("threshold", 0)
            if v["count"] > threshold:
                suggestions.append(f"{name}: {v['count']}次（阈值 {threshold}）")

        return {
            "categories": categories,
            "total_violations": total_violations,
            "is_excessive": total_violations > 0,
            "suggestion": "；".join(suggestions) if suggestions else "",
        }
