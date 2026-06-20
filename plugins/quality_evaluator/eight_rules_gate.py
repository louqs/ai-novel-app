"""8条铁律质量门禁 — 来自 novel-templates 项目模板.

检查：
1. 对话双引号
2. 禁止AI模板词
3. 口语化词
4. 主角挫折
5. 反派实质行动
6. 金手指代价
7. 时代细节
8. 展示不讲述
"""

from __future__ import annotations

import re
from typing import Any

from core.logging_config import get_logger
from core.quality_gate import GateIssue, GateResult, GateVerdict, IQualityGate, Severity

logger = get_logger(__name__)

# AI 模板词黑名单
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

# 口语化词
COLLOQUIAL_WORDS = [
    '咋', '啥', '呗', '嘛', '呢', '啊', '呀', '咯', '喽', '哟', '哼',
    '琢磨', '寻思', '盘算', '合计',
    '玩意儿', '咱们', '那小子', '这小子', '那会儿', '这会儿',
    '可不是', '不是嘛', '咋的', '咋不', '瞧', '看那个',
    '吆喝', '搭话', '嘀咕',
]

# 主角挫折关键词
SETBACK_KEYWORDS = [
    '失败', '挫折', '赔了', '亏了', '毁了', '砸了',
    '受伤', '发烧', '头晕', '手抖', '体力不支',
    '被嘲笑', '被质疑', '被拒绝', '被威胁', '被打压',
    '倒霉', '不顺', '意外', '损失',
    '吃亏', '闷亏', '栽了', '碰壁', '受阻',
    '白忙活', '白费劲', '竹篮打水', '功亏一篑',
]

# 反派实质行动关键词
VILLAIN_ACTION = [
    '毁了', '砸了', '踹翻', '抢走', '拦路',
    '买通', '举报', '散播谣言', '传出去',
    '偷偷', '设局', '陷害', '动手',
    '堵', '烧', '扣押', '断了', '砍了',
]

# 金手指代价关键词
GOLDEN_FINGER_COST = [
    '疲惫', '累得', '体力消耗', '虚脱', '冒冷汗',
    '脸色发白', '脸色白了', '站不稳', '手发抖', '手抖',
    '发烧', '头晕目眩', '头晕', '眼前发黑', '发黑',
    '累倒', '歇了好一会儿', '歇了一阵', '喘着粗气', '浑身没劲',
    '腿软', '绊了一跤', '栽了一跤', '缓过劲儿', '打哆嗦', '哆嗦',
    '后脊梁全是冷汗', '冷汗',
    '代价', '付出', '掏空', '透支', '累瘫',
    '身子虚', '虚得', '撑不住', '扛不住',
]


class EightRulesGate(IQualityGate):
    """8条铁律质量门禁."""

    name = "eight-rules"
    order = 60  # 在 anti-ai-detection(40) 之后

    def __init__(self) -> None:
        self._kernel = None

    async def on_load(self, kernel) -> None:
        self._kernel = kernel
        logger.info("8条铁律质量门禁已加载")

    async def on_unload(self) -> None:
        self._kernel = None

    async def evaluate(self, chapter: dict[str, Any], context: dict[str, Any]) -> GateResult:
        """执行8条铁律检查."""
        content = chapter.get("content", "")
        metadata = chapter.get("metadata", {})
        chapter_num = metadata.get("chapter_number", 0)

        if not content:
            return GateResult(
                gate_name=self.name,
                verdict=GateVerdict.PASS,
                issues=[],
                score=1.0,
            )

        issues: list[GateIssue] = []
        scores: dict[str, float] = {}

        # 铁律1：对话双引号
        score1, issues1 = self._check_dialogue_quotes(content)
        scores["dialogue_quotes"] = score1
        issues.extend(issues1)

        # 铁律2：禁止AI模板词
        score2, issues2 = self._check_template_words(content)
        scores["template_words"] = score2
        issues.extend(issues2)

        # 铁律3：口语化词
        score3, issues3 = self._check_colloquial(content)
        scores["colloquial"] = score3
        issues.extend(issues3)

        # 铁律4：主角挫折
        score4, issues4 = self._check_protagonist_setback(content, chapter_num)
        scores["protagonist_setback"] = score4
        issues.extend(issues4)

        # 铁律5：反派实质行动
        score5, issues5 = self._check_villain_action(content)
        scores["villain_action"] = score5
        issues.extend(issues5)

        # 铁律6：金手指代价
        score6, issues6 = self._check_golden_finger_cost(content)
        scores["golden_finger_cost"] = score6
        issues.extend(issues6)

        # 铁律7：时代细节（需要项目上下文）
        score7 = 7.0  # 默认通过
        scores["era_details"] = score7

        # 铁律8：展示不讲述
        score8, issues8 = self._check_show_dont_tell(content)
        scores["show_dont_tell"] = score8
        issues.extend(issues8)

        # 计算平均分
        avg_score = sum(scores.values()) / len(scores) if scores else 0.0

        # 判定
        has_critical = any(i.severity == Severity.ERROR for i in issues)
        verdict = GateVerdict.REVISE if has_critical else GateVerdict.PASS

        return GateResult(
            gate_name=self.name,
            verdict=verdict,
            issues=issues,
            score=avg_score,
            metadata={"dimension_scores": scores},
        )

    def _check_dialogue_quotes(self, text: str) -> tuple[float, list[GateIssue]]:
        """检查对话引号."""
        issues = []
        score = 8.0

        curly = text.count('“')
        straight_pairs = text.count('"') // 2
        total_pairs = curly + straight_pairs

        if total_pairs < 6:
            score = 4.0
            issues.append(GateIssue(
                severity=Severity.ERROR,
                code="rules.dialogue_quotes",
                message=f"对话引号不足：{total_pairs} 对（阈值 ≥ 6）",
                suggestion="所有对话必须用 "" 包裹",
            ))

        return max(0.0, min(10.0, score)), issues

    def _check_template_words(self, text: str) -> tuple[float, list[GateIssue]]:
        """检查AI模板词."""
        issues = []
        score = 8.0

        details = {}
        for word in TEMPLATE_WORDS:
            count = text.count(word)
            if count > 0:
                details[word] = count

        total = sum(details.values())
        single_max = max(details.values()) if details else 0

        if total > 8:
            score = 4.0
            issues.append(GateIssue(
                severity=Severity.ERROR,
                code="rules.template_words",
                message=f"AI模板词总次数 {total} 次（阈值 8）",
                suggestion="替换为具体动作描写",
            ))
        elif total > 5:
            score = 6.0
            issues.append(GateIssue(
                severity=Severity.WARNING,
                code="rules.template_words",
                message=f"AI模板词偏多：{total} 次",
                suggestion="建议替换几处为具体动作",
            ))

        if single_max > 3:
            over_words = [w for w, c in details.items() if c > 3]
            issues.append(GateIssue(
                severity=Severity.ERROR,
                code="rules.template_word_single",
                message=f"单词超限：{'、'.join(over_words)} 各出现 > 3 次",
                suggestion="换用不同的表达方式",
            ))

        return max(0.0, min(10.0, score)), issues

    def _check_colloquial(self, text: str) -> tuple[float, list[GateIssue]]:
        """检查口语化词."""
        issues = []
        score = 8.0

        count = sum(text.count(w) for w in COLLOQUIAL_WORDS)
        freq = count / (len(text) / 100) if text else 0

        if freq < 0.8:
            score = 6.0
            issues.append(GateIssue(
                severity=Severity.WARNING,
                code="rules.colloquial",
                message=f"口语化偏低：{freq:.1f}/百字（阈值 ≥ 0.8）",
                suggestion="增加口语化表达，如"俺/咱/啥/咋/呗/嘛"",
            ))

        return max(0.0, min(10.0, score)), issues

    def _check_protagonist_setback(self, text: str, chapter_num: int) -> tuple[float, list[GateIssue]]:
        """检查主角挫折."""
        issues = []
        score = 8.0

        count = sum(text.count(w) for w in SETBACK_KEYWORDS)

        # 建立期(Ch1-3)允许无重大挫折
        if chapter_num <= 3:
            return max(0.0, min(10.0, score)), issues

        if count < 1:
            score = 4.0
            issues.append(GateIssue(
                severity=Severity.ERROR,
                code="rules.protagonist_setback",
                message="主角无挫折：每章必须有挫折场景",
                suggestion="增加失败、受伤、被嘲笑、被拒绝等挫折情节",
            ))

        return max(0.0, min(10.0, score)), issues

    def _check_villain_action(self, text: str) -> tuple[float, list[GateIssue]]:
        """检查反派实质行动."""
        issues = []
        score = 8.0

        action_count = sum(text.count(w) for w in VILLAIN_ACTION)

        if action_count < 1:
            score = 6.0
            issues.append(GateIssue(
                severity=Severity.WARNING,
                code="rules.villain_action",
                message="反派仅口头威胁未动手",
                suggestion="增加反派实质行动：毁/砸/买通/举报/设局等",
            ))

        return max(0.0, min(10.0, score)), issues

    def _check_golden_finger_cost(self, text: str) -> tuple[float, list[GateIssue]]:
        """检查金手指代价."""
        issues = []
        score = 8.0

        # 检测是否使用了金手指（简化检测）
        has_golden_finger = any(
            keyword in text
            for keyword in ['金手指', '系统', '异能', '功法', '灵珠']
        )

        if not has_golden_finger:
            return max(0.0, min(10.0, score)), issues

        cost_count = sum(text.count(w) for w in GOLDEN_FINGER_COST)

        if cost_count < 1:
            score = 4.0
            issues.append(GateIssue(
                severity=Severity.ERROR,
                code="rules.golden_finger_cost",
                message="金手指无代价：使用金手指必须有身体代价",
                suggestion="增加疲惫、虚脱、冒冷汗、手发抖等代价描写",
            ))

        return max(0.0, min(10.0, score)), issues

    def _check_show_dont_tell(self, text: str) -> tuple[float, list[GateIssue]]:
        """检查展示不讲述."""
        issues = []
        score = 8.0

        # 检测直接讲述的情绪词
        telling_words = [
            '感到愤怒', '感到悲伤', '感到高兴', '感到害怕',
            '非常愤怒', '非常悲伤', '非常高兴', '非常害怕',
            '他很生气', '他很伤心', '他很开心', '他很害怕',
        ]

        count = sum(text.count(w) for w in telling_words)

        if count > 3:
            score = 5.0
            issues.append(GateIssue(
                severity=Severity.WARNING,
                code="rules.show_dont_tell",
                message=f"直接讲述情绪：{count} 次",
                suggestion="用动作/细节/对话展示情绪，禁止直接讲述",
            ))

        return max(0.0, min(10.0, score)), issues


# ---------------------------------------------------------------------------
# 工厂函数
# ---------------------------------------------------------------------------


def create_manifest():
    from core.plugin_manager import PluginManifest
    return PluginManifest(
        name="eight-rules",
        version="0.1.0",
        description="8条铁律质量门禁",
        dependencies=[],
        hooks=["on_load", "on_unload"],
    )


def create_plugin() -> EightRulesGate:
    return EightRulesGate()
