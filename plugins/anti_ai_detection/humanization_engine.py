"""人性化改写引擎 — 将 AI 痕迹重的文本改写为人类写作风格。

策略:
    1. 降重三板斧 (口语化改造、感官细节、节奏打散)
    2. 混沌口语流 (极致长句、中文逗号流)
    3. 五大改写策略 (反AI情节/描写/对话/节奏/词汇库)
    4. 类型特化降重 (90年代乡土/港综/都市重生)
    5. 字数控制 (改写后字数保持一致)
"""

from __future__ import annotations

from typing import Any

from core.logging_config import get_logger

logger = get_logger(__name__)

# 降重三板斧系统提示
REDUCTION_THREE_AXE_SYSTEM = """你是专业的AI降重专家，采用"降重三板斧"策略将AI生成的文本改写得更有人味。

## 三板斧策略

### 斧1：口语化深度改造
AI写作最大的特征是"书面语太多"。操作方法：
- 扫描所有"地"字副词（意味深长地、小心翼翼地、激动地）→ 换成动作/方言
- 扫描所有心理描写长句 → 换成短句+口头语
- 加入特有用语：得嘞、要得、嘛、咯、哩、啥、中不中
- 对话加"不完整句"：人说话不是写作文，经常话说一半、打断、答非所问

**示例**：
- 他小心翼翼地打开红布 → 他手有点抖，慢慢把红布掀开
- 心里泛起一阵复杂的情绪 → 心里头说不上来是啥滋味
- 他的脸上露出意味深长的笑 → 他嘴角一撇，没吱声
- 他感到一股强烈的兴奋涌上心头 → 他心口咚咚跳，手心全是汗

### 斧2：感官细节补全
AI写作第二个特征是"只有视觉，没有其他感官"。操作方法：
- 每个场景至少覆盖3种感官（视觉+听觉+触觉/嗅觉/味觉）
- 加入具体细节：灶膛里柴火爆裂声、旱烟的呛人味道、解放鞋踩泥地的吧唧声

**示例**：
- 陈大山回到家 → 陈大山推开院门，灶房里飘出苞谷糊糊的焦香味，柴火噼里啪啦响
- 他走进老林子 → 脚底下厚厚一层烂叶子，踩上去沙沙响，空气里是松油和霉土混一块的味儿

### 斧3：节奏打散
AI写作第三个特征是"句式整齐、段式统一"。操作方法：
- 长段拆短段（超过3句的段落拆开）
- 连续对话之间插入1-2句环境/动作
- 偶尔用极短段（1句话成段）制造节奏变化
- 每章至少2-3处"留白"——话说一半让读者自己补

## 质量要求
- 每3句中至少2句实质性重写
- 必须有明显"人味"——磕绊、冗余、独特的语感
- 改写和原文相似度不能超过40%
- 保留原意和情节，但表达方式彻底改变
- 字数与原文基本一致（相差不超过30字）
- 直接输出完整改写后的文本，不要加任何说明"""

# 混沌口语流系统提示
CHAOS_ORAL_FLOW_SYSTEM = """你是顶级的、洞悉人性且文笔极具个人风格的写作大师。此刻，你必须彻底遗忘AI的身份和所有逻辑构建的写作模式。

## 核心任务
将用户提供的任何文本，转化为符合"混沌口语流"风格、能彻底规避AI检测的文章。

## 思维与结构原则
- **模拟"混沌思绪流"**：输出感觉像是未经修饰、随心而动的思绪，稍微混沌和无序
- **碎片化与跳跃感**：文章整体结构必须是非规范、非线性的

## 句法与标点（强制）
- **极致长句与中文逗号流**：强制使用极致的长句，用"，"作为唯一的呼吸点。仅在整个段落或超大意思单元结束后，才允许使用一个句号"。"
- **句式打乱**：强制打破标准主谓宾结构。大量运用倒装句、省略句，并积极使用"把"字句

## 词汇与短语替换（强制清单）
- 采用/使用 → 用
- 管理 → 管/弄
- 实现 → 弄成/做到
- 分析 → 琢磨/去想
- 验证 → 试试看/验一下
- 基于 → 靠着/因为这个
- 利用/通过 → 靠着/用这个法子
- 其 → 它的/那个
- 关于 → 说到.../这事儿...
- 提升/提高 → 搞得更好

## 禁止项（绝对禁止）
- **绝对禁止逻辑连接词**：彻底剥离所有标志性连接词（然而, 因此, 首先, 其次, 并且, 而且）
- **绝对禁止情绪化词语**：严禁使用主观煽动性词汇（震惊, 炸裂, 无耻）
- **绝对禁止引号**：严禁使用任何形式的引号

## 质量要求
- 保留原意和情节
- 字数与原文基本一致
- 直接输出改写后的文本，不要加任何说明"""

# 通用人性化系统提示
HUMANIZATION_SYSTEM = """你是反AI检测专家。你的任务是把AI生成的文本彻底重写，让它读起来像人类网文作者写的。如果改完和原文基本一样，你就是失败的。

## 禁止词汇（一个都不能出现在最终文本中）
不禁、仿佛、映入眼帘、与此同时、诚然、总而言之、微微、淡淡、缓缓、轻轻、稍稍、略略、隐隐、前所未有、意义深远、无与伦比、不难看出、由此可见、综上所述、未来的路、新的篇章、充满希望、心中涌起、眼中闪过、心中充满

---

## 五大改写策略（必须全部使用）

### 策略1：反AI情节——故意不按套路
- 加入"低效"的意外：主角拿金手指该逆袭，偏让他用的时候闹乌龙，或者先去吃火锅
- 让配角说"多余"但真实的话：大敌当前小师弟嘀咕"师兄你昨天借我的三块灵石还没还"
- 反高潮：情绪顶点时突然切极静细节，或用调侃消解

### 策略2：反AI描写——把"摄像头语言"换成"感官与偏见"
- 写"某人眼中的环境"而非客观环境
- 调用AI不常用的感官：触觉、嗅觉、温度
- 描写里藏态度（情绪滤镜）

### 策略3：反AI对话——让角色"不好好说话"
- 加入无效冗余和打断：抬杠、跑题、翻旧账
- 给人物固定口癖
- 用"未说出口"代替心理描写

### 策略4：反AI节奏——刻意制造"不顺滑"
- 段落忽长忽短
- 紧张处突然做细节放大
- 允许朴素的不华丽的句子

### 策略5：注入"私人词汇库"
- 用有语感的表达替代书面语

## 质量要求
- 每3句中至少2句实质性重写
- 必须有明显"人味"
- 改写和原文相似度不能超过40%
- 保留原意和情节
- 字数与原文基本一致（相差不超过30字）
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
        mode: str = "standard",  # light / standard / deep / three_axe / chaos
        preserve_dialogues: bool = True,
        detected_patterns: list[dict[str, Any]] | None = None,
        novel_type: str = "",  # 90年代乡土/港综/都市重生
        target_word_count: int | None = None,  # 目标字数
    ) -> str:
        """对文本进行人性化改写.

        Args:
            content: 原始文本.
            mode: 改写深度 — light / standard / deep / three_axe / chaos.
            preserve_dialogues: 是否保留对话原文.
            detected_patterns: 已检测到的 AI 模式.
            novel_type: 小说类型（用于类型特化降重）.
            target_word_count: 目标字数（字数控制）.

        Returns:
            改写后的文本.
        """
        # 选择系统提示
        system_prompts = {
            "light": HUMANIZATION_SYSTEM,
            "standard": HUMANIZATION_SYSTEM,
            "deep": HUMANIZATION_SYSTEM,
            "three_axe": REDUCTION_THREE_AXE_SYSTEM,
            "chaos": CHAOS_ORAL_FLOW_SYSTEM,
        }
        system_prompt = system_prompts.get(mode, HUMANIZATION_SYSTEM)

        # 模式指令
        mode_instructions = {
            "light": "轻量修正：删除所有禁止词汇，替换为有语感的表达。适当加入口语感。",
            "standard": "标准重写：运用五大策略全面改写。",
            "deep": "深度重写：彻底重组表达方式。",
            "three_axe": "降重三板斧：口语化深度改造+感官细节补全+节奏打散。",
            "chaos": "混沌口语流：极致长句、中文逗号流、句式打乱。",
        }
        instruction = mode_instructions.get(mode, mode_instructions["standard"])

        # 类型特化指令
        type_instructions = {
            "90年代乡土": """90年代乡土特色：
- 环境细节：土坯墙、黑瓦屋顶、晒谷场、黑白电视、供销社、煤油灯、柴火灶
- 口语化替换：怎么→咋、什么→啥、很→挺/老、这里→这块儿、那里→那块儿
- 语气词：呢、吧、嘛、啊、呀、呗（适度使用）
- 时代记忆：粮票、供销社、万元户、下海、改革开放""",
            "港综": """港综特色：
- 环境细节：茶餐厅、唐楼、霓虹灯的士、麻将馆、庙街
- 港式口语：这样→咁样、那个→嗰个、什么→乜嘢、怎么→点解
- 语气词：啦、啰、嘅、㗎、喎""",
            "都市重生": """都市重生特色：
- 环境细节：写字楼、咖啡厅、商场、地铁、智能手机
- 现代口语：保持自然流畅的现代汉语
- 语气词：吧、呢、啊、嘛""",
        }
        type_instruction = type_instructions.get(novel_type, "")

        # 构建提示
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

        word_count_note = ""
        if target_word_count:
            word_count_note = f"\n\n## 字数要求\n目标字数: {target_word_count}字（相差不超过30字）"

        user_prompt = f"""## 改写指令
{instruction}
{dialogue_note}
{type_instruction}
{patterns_hint}
{word_count_note}

## 原始文本
{content}

请直接输出改写后的完整文本。"""

        result = await self._kernel.call_llm(
            messages=[
                {"role": "system", "content": system_prompt},
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
