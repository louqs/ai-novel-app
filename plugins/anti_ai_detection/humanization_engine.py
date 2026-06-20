"""人性化改写引擎 — 将 AI 痕迹重的文本改写为人类写作风格。

策略:
    1. 感知细节注入 (五感描写)
    2. 句式变异 (长短句交替)
    3. 口语化处理
    4. 主观性注入
    5. 不完美引入 (允许适当的重复、修正)
"""

from __future__ import annotations

from typing import Any

from core.logging_config import get_logger

logger = get_logger(__name__)

HUMANIZATION_SYSTEM = """你是反AI检测专家。你的任务是把AI生成的文本彻底重写，让它读起来像人类网文作者写的。如果改完和原文基本一样，你就是失败的。

## 禁止词汇（一个都不能出现在最终文本中）
不禁、仿佛、映入眼帘、与此同时、诚然、总而言之、微微、淡淡、缓缓、轻轻、稍稍、略略、隐隐、前所未有、意义深远、无与伦比、不难看出、由此可见、综上所述、未来的路、新的篇章、充满希望、心中涌起、眼中闪过、心中充满

---

## 五大改写策略（必须全部使用）

### 策略1：反AI情节——故意不按套路
- 加入"低效"的意外：主角拿金手指该逆袭，偏让他用的时候闹乌龙，或者先去吃火锅
- 让配角说"多余"但真实的话：大敌当前小师弟嘀咕"师兄你昨天借我的三块灵石还没还"
- 反高潮：情绪顶点时突然切极静细节，或用调侃消解。突破境界后不是膜拜而是肚子叫了

### 策略2：反AI描写——把"摄像头语言"换成"感官与偏见"
- 写"某人眼中的环境"而非客观环境：龙椅→"扶手上那块油光，多少代皇帝手摸出来的包浆"
- 调用AI不常用的感官：触觉（粗布磨脖子）、嗅觉（雨后泥腥味）、温度（黏糊糊的热风）
- 描写里藏态度（情绪滤镜）：阳光很好→"太阳亮得刺眼，像是在嘲笑他"

### 策略3：反AI对话——让角色"不好好说话"
- 加入无效冗余和打断：抬杠、跑题、翻旧账。"我觉得此事——""你别觉得了，上次你也觉得。"
- 给人物固定口癖：主角从不解释、小孩把"喜欢"说成"想要那个"、某人每句话都在试探
- 用"未说出口"代替心理描写：说半句忽然沉默，让读者自己品那没说出来的话

### 策略4：反AI节奏——刻意制造"不顺滑"
- 段落忽长忽短：上一段三个字（"他愣住。"），下一段两百字回忆闪回
- 紧张处突然做细节放大：对峙出刀时去写屋檐水滴的轨迹，拉长时间感
- 允许朴素的不华丽的句子："他就那么看着她，没说话。"——AI写不出这种白水句

### 策略5：注入"私人词汇库"
- "此人行事随意" → "这人不讲究"
- "怒火中烧" → "火蹭地就上来了"
- "他感到困惑" → "他有点懵"
- 用有语感的表达替代书面语

## 质量要求
- 每3句中至少2句实质性重写（不是换词，是改表达方式）
- 必须有明显"人味"——磕绊、冗余、独特的语感
- 改写和原文相似度不能超过40%
- 保留原意和情节，但表达方式彻底改变
- 直接输出完整改写后的文本，不要加任何说明"""


class HumanizationEngine:
    """人性化改写引擎."""

    def __init__(self) -> None:
        self._kernel = None

    async def on_load(self, kernel) -> None:
        self._kernel = kernel

    async def humanize(
        self,
        content: str,
        *,
        mode: str = "standard",  # light / standard / deep
        preserve_dialogues: bool = True,
        detected_patterns: list[dict[str, Any]] | None = None,
    ) -> str:
        """对文本进行人性化改写.

        Args:
            content: 原始文本.
            mode: 改写深度 — light(仅去AI词) / standard(句式+口语) / deep(段落重写).
            preserve_dialogues: 是否保留对话原文.
            detected_patterns: 已检测到的 AI 模式 (用于精准修复).

        Returns:
            改写后的文本.
        """
        mode_instructions = {
            "light": "轻量修正：删除所有禁止词汇，替换为有语感的表达。适当加入口语感。",
            "standard": "标准重写：运用五大策略全面改写——加入意外和冗余、用感官和偏见替代客观描写、让对话有打断和口癖、制造段落长短节奏变化、注入私人词汇。每3句至少重写2句。",
            "deep": "深度重写：彻底重组表达方式。可以改写段落结构、重新编排对话、大幅调整叙述节奏。唯一要求：保留原意和情节。",
        }

        instruction = mode_instructions.get(mode, mode_instructions["standard"])

        patterns_hint = ""
        if detected_patterns:
            items = []
            for p in detected_patterns:
                items.append(f"- {p.get('category', '')}: {', '.join(p.get('matched_items', [])[:5])}")
            if items:
                patterns_hint = "\n\n## 已检测到的 AI 痕迹 (优先处理)\n" + "\n".join(items)

        dialogue_note = ""
        if preserve_dialogues:
            dialogue_note = "\n注意: 对话部分尽量保留原文，只做最小调整。"

        user_prompt = f"""## 改写指令
{instruction}
{dialogue_note}

## 改写深度
{mode}
{patterns_hint}

## 原始文本
{content}

请直接输出改写后的完整文本。"""

        result = await self._kernel.call_llm(
            messages=[
                {"role": "system", "content": HUMANIZATION_SYSTEM},
                {"role": "user", "content": user_prompt},
            ],
            tier="standard",
            max_tokens=8192,
            temperature=0.7,
        )
        return result["content"]


class AdversarialRewriter:
    """对抗改写器 — 绕过 AI 检测的对抗性改写."""

    def __init__(self) -> None:
        self._kernel = None

    async def on_load(self, kernel) -> None:
        self._kernel = kernel

    async def rewrite_adversarial(
        self,
        content: str,
        *,
        iterations: int = 2,
    ) -> str:
        """多次迭代对抗改写.

        Args:
            content: 原始文本.
            iterations: 改写迭代次数 (1-3).

        Returns:
            对抗改写后的文本.
        """
        current = content
        for i in range(iterations):
            prompt = f"""请对以下文本进行第{i+1}轮对抗性改写，使 AI 检测器无法识别:

## 要求
- 保留原意和情节
- 融入更多不规则的表达
- 加入具体而非常规的描写
- 破坏 AI 文本的统计特征

## 文本
{current}

直接输出改写后的文本。"""

            result = await self._kernel.call_llm(
                messages=[{"role": "user", "content": prompt}],
                tier="premium",
                max_tokens=8192,
                temperature=0.9,
            )
            current = result["content"]

        return current
