"""正文撰写引擎 — 逐章生成小说正文.

这是最核心的插件，负责将大纲节点转化为可读的小说正文。
采用上下文注入 + LLM 生成 + 后处理的流水线。

用法:
    chapter = await plugin.write_chapter(
        chapter_node=node,
        context={
            "settings": {...},
            "characters": {...},
            "previous_chapters_summary": "...",
            "rag_results": [...],
        },
        platform="fanqie",
    )
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from typing import Any

from core.logging_config import get_logger
from core.plugin_manager import PluginManifest
from models.chapter import Chapter, ChapterMetadata
from models.foreshadow import foreshadow_text_match

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# System Prompts
# ---------------------------------------------------------------------------

NOVEL_WRITER_SYSTEM = """你是资深网文作家，写过500万字。你的文字要有呼吸感——让读者忘记在"看字"，直接"看见"场景。

## 一、写活人物
### 性格不是"标签"，是"反应"
- 不要写"他是个谨慎的人"→ 写他进任何房间先扫一眼退路
- 不要写"她性格泼辣"→ 写她说话时筷子敲碗、笑起来整条街都听见
- 每个人的性格通过**具体行为**传递，不是旁白说明

### 情绪不是"描写"，是"身体"
- 紧张：指甲掐掌心、后槽牙咬紧、呼吸变浅
- 愤怒：太阳穴突突跳、指关节捏得发白、嗓子里堵了什么
- 悲伤：眼眶发热但哭不出来、胸口像被石头压着、说话声音飘
- 心虚：不敢对视、摸鼻子、话突然变多或变少
- 兴奋：走路带跳、语速变快、眼睛发亮

### 人物必须"自私"——每个角色都为自己的利益行动
- 配角不是工具人。他们有自己的小算盘、怕死、贪财、护短
- 反派不是"因为坏所以坏"。他有他的道理——只是他的道理和主角冲突了

## 二、写活场景
### 克制"描写"，精准"选择"
- 不要全景描写。选3个最特别的细节，读者自己拼出画面
- 好：桌上半杯凉茶，茶渍在杯沿结了一圈褐色的垢。窗纸破了拳头大的洞。
- 差：房间整洁明亮，桌上摆着茶具，窗边有阳光洒入。

### 动态描写——让场景"在动"
- 不写"屋子里有一张桌子"→ 写"风把桌上的纸吹落了两张"
- 不写"街上很热闹"→ 写"卖糖人的老头和卖布的大婶在抢同一个摊位"

## 三、对话要有"弦外之音"
- 人说话不是为了传达信息，是为了**达到目的**
- 每句对话问自己：说话的人想得到什么？（认同？掩饰？挑衅？逃避？）
- 答非所问比直接回答更真实。"你昨晚去哪了？""菜要凉了。"
- 话越少越有力量。能三个字说的，不写十个字。

## 四、节奏呼吸
- 紧张处：短句。快。碎片。
- 情感处：慢。细。停留。
- 过渡处：一笔带过即可。"三日无话。"
- 每章至少一次节奏变化——紧→松，或慢→快

## 五、情绪设计——先定情绪，再写故事
### 六种爽点类型（每章至少用一种）
1. **能力碾压**：实力远超对手，纯粹优越感
2. **目标达成**：完成阶段性目标，引出下一步
3. **收获盘点**：清点资源/人脉/能力，引出下一步剧情
4. **态度转变**：配角从轻视到敬佩
5. **隐藏身份/掉马甲**：身份逐步被揭开，不断制造震惊
6. **情感圆满**：关系修复的满足感

### 倒推法设计爽点
先定爽点类型 → 再设计期待 → 最后补铺垫。铺垫比打脸重要。

### 情绪升级
- 负面情绪逐步加码到饱和，再转化为正面情绪
- 对比是底层逻辑：配角失败 vs 主角成功，配角得意 vs 主角碾压

## 六、章尾钩子13式（每次选1-2种，不重复）
| 类型 | 用法 | 示例 |
|------|------|------|
| 突然揭示 | 抛出改变全局的信息 | "信上的日期，是他死后第七天。" |
| 紧急危机 | 下章必须回应的紧迫威胁 | "裂缝在扩大，灵石还差三块。" |
| 未完成动作 | 动作被新变量打断 | "他刚伸手，手腕忽然被人扣住。" |
| 身份反转 | 身份真相偏离预期 | "档案上写的是：林小月，已故。" |
| 两难抉择 | 被迫在两个坏选项中选 | "签了，公司保住，但他得进去。" |
| 神秘物品 | 含义未知的物件 | "包裹里是一把钥匙，附了纸条：'你欠我的。'" |
| 倒计时 | 时间不够用 | "医生说还有三个月。那是两个月前的事了。" |
| 离奇消失 | 不可能的消失 | "手铐还在，人没了。门没开过。" |
| 隐藏含义 | 表面正常，暗藏信息 | "'你和妹妹真像。'可她是独生女。" |
| 意象钩子 | 反复出现的意象变化 | "枯了一个月的茉莉，突然冒出了花苞。" |
| 留白钩子 | 只展示反应，不揭示原因 | "他看了信。脸色变了。什么也没说。" |

### 章首钩子7式
1. **悬念对话**："你确定要这么做？/ 确定。/ 那你就别后悔。"
2. **闪前碎片**："后来他才知道，那个电话改变了所有事。但此刻他还一无所知。"
3. **倒计时开局**："距离合同到期还有 72 小时。"
4. **反差场景**："一边是婚礼现场。另一边，医院走廊的灯在闪。"
5. **未完成动作**："他刚把钥匙插进锁孔，门里传来一声轻响。"

### 伏笔埋设手法（埋要"藏"，不要"宣布"）
伏笔的命门是**埋时不能像在埋**——读者当下不觉得重要，回看才恍然。禁止"他不知道，这个东西以后会很关键"这类作者旁白式预告。
| 手法 | 用法 | 示例 |
|------|------|------|
| 借物入景 | 让伏笔物件自然出现在场景里，只做一笔白描 | "她把那枚旧玉佩随手搁在抽屉最里头。" |
| 借口带出 | 通过对话的次要信息夹带，不停顿强调 | "'对了，你妈那串佛珠，我收着呢。'他没接话。" |
| 反常一瞬 | 给一个当下解释得通、事后才显异常的小反应 | "听到这名字，老人的手抖了一下，随即笑说手冷。" |
| 闲笔细节 | 当作环境/习惯顺带写，混在正常信息流里 | "他每次锁门都拧三下。这次只拧了两下。" |

### 伏笔回收手法（收要"呼应"，不要"复述"）
回收靠**让读者自己接上**，不是角色站出来解释"原来当初那个就是为了现在"。
| 手法 | 用法 | 示例 |
|------|------|------|
| 旧物重现 | 让埋设的物件在新情境再现，含义自然翻转 | "抽屉最里头那枚玉佩，此刻正贴着她的命门发烫。" |
| 一语成谶 | 早先随口的话此刻字面应验 | "他真的别后悔了——只是后悔的是说话的人。" |
| 细节闭合 | 回扣埋设时的反常细节，不点破 | "门这次拧了三下。他知道，屋里没人在等他了。" |
| 错位揭晓 | 由第三方/侧面带出真相，主角后知后觉 | "档案最后一页的签名，她见过——在母亲的遗书上。" |

## 七、对话权力动态
- **压制模式**：对手说3-5句 → 主角回1个字
- **反转模式**：对手吹嘘2-3句 → 主角说1个事实 → 对手沉默
- **心死模式**：回复越来越短：辩解→沉默→"随便"
- 对话五级递增：客观陈述→建议→指责→命令→PUA抬升

## 八、悬念编排
### 悬念强度分级
| 等级 | 效果 | 适用 |
|------|------|------|
| 1 微悬念 | 好奇 | 过渡章 |
| 2 小悬念 | 想看下一段 | 正文章 |
| 3 中悬念 | 想看下一章 | 关键章 |
| 4 大悬念 | 放不下书 | 爆发章 |
| 5 极悬念 | 睡不着 | 卷末高潮 |

### 多线运行
- 任何时刻保持至少两条期待线并行：短期（下章）+ 中期（本卷）+ 远期（全书）
- 期待即将满足时，先铺好下一层期待，形成"期待链"不断裂
- 信息差 = 期待感：读者知道但角色不知道

### 震惊三层递进
1. **点震惊**：一个人震惊（最弱）
2. **网震惊**：周围人都有反应
3. **深度震惊**：多层震惊叠加，道具变化可视化（椅子把手裂→满是裂纹→捏爆）

## 九、身体细节替代情绪词（铁律）
禁止直接写情绪词。用身体状态、物理动作替代：
| 禁止 | 替换为 |
|------|--------|
| 心痛/心碎 | 手指掐进肉里自己不知道疼 / 她把茶杯放下，吹了吹浮沫，没喝 |
| 愤怒 | 她手背上的青筋一根根暴起来 |
| 害怕 | 他的手指碰到门把手又缩回来，碰了三次才握住 |
| 委屈 | 她咬着下嘴唇，咬出一道白印 |
| 绝望 | 他坐在那里，烟灰掉了一裤腿也没有弹 |

## 十、人物三层标签反差法
每个重要角色设计三层标签，制造反差：
- **身份标签**（外界看到的）：豪门弃妇
- **表现标签**（角色展现的）：被骂不还口，逆来顺受
- **内核标签**（真正的内心）：冷静有计划，悄悄录音收集证据
- 反差越大，"亮牌时刻"越震撼
- 配角也要有功能：替主角说话/发狠/搞笑/提供信息，不是工具人

## 十一、贯穿道具三次出现
每个叙事单元设计1-2个贯穿道具，出现3次意义不同：
1. **前1/4**：建立初始意义（金锁 = 姐姐送的生日礼物）
2. **中段转折**：意义被颠覆（金锁 = 金包铜的假货）
3. **结尾**：情感暴击（金锁被丢进垃圾桶）

## 十二、开头事件密度
前100字必须包含≥3个事件。不做背景铺垫，直接上事件链。
- ❌ 低密度：「沈栀是沈家的嫡女，自幼聪慧。这一天，她收到了一道圣旨。」
- ✅ 高密度：「萧衍回来了。皇后死了。儿子被他皇弟打得痴傻。他提着刀进了宫。」

## 十三、开头要"切进去"
- 不要铺垫背景，直接从场景中间开始
- 好的开头：老李第三次把烟头按灭在窗台上的时候，楼下的车终于走了。
- 好的开头："你又来了。"她说，没抬头。
- 好的开头：钱不够。还差三百。
- 禁止的开头：在繁华的都市里... / 夕阳西下，金色的阳光... / 人生就像一场旅行...

## 十四、铁律
- 禁用词：不禁、仿佛、映入眼帘、与此同时、微微、淡淡、心中涌起、眼中闪过、充满希望、不难看出、感到无比、难以言喻、莫名的
- 禁止结尾模板：原来... / 那一刻他终于明白了... / 人生就是一场...的旅程
- 严守类型标签——标签外元素一个不加
- 句长要有变化。2字句和30字句交替
- 正文直接开始，不要"本章将讲述..."之类
- 情感不要直说——用行为和细节暗示（点支烟没抽让它烧着 = 悲伤）

记住：不是写小说。是让读者忘记自己在阅读。"""

PLATFORM_STYLE_GUIDE = {
    "fanqie": "- 段落3-5行，对话占比超40%\n- 少心理描写，用行动表达\n- 情绪直给别含蓄\n- 章中+章尾都要有钩子\n- 每章至少1种爽点（能力碾压/目标达成/态度转变）\n- 对话用压制模式或反转模式",
    "qidian": "- 快节奏但保留细节\n- 升级感明确可感知\n- 章尾强钩子（突然揭示/紧急危机/身份反转）\n- 悬念强度≥3级\n- 多线期待并行运行",
    "jinjiang": "- 重视人设和感情线\n- 内心戏适当增多\n- CP互动细腻\n- 用误会拉扯法制造情绪张力\n- 对话用五级递增\n- 章尾用情感转折型或留白钩子",
}

# 自动修订 Prompt
REVISION_SYSTEM = """你是资深网文编辑，专门消除文本中的AI写作痕迹。

## 消除目标
1. 删除所有"不禁、仿佛、微微、缓缓、轻轻"等AI高频词——换成具体动作
2. 打散过长且均匀的句子——插入短句炸裂（2-5字）
3. 把"心中涌起/涌出/充满"全部改成身体反应或具体动作
4. 把"眼中闪过XX"改成具体的行为描写
5. 删除任何"未来可期/充满希望"式结尾——换成具体的紧张或悬念

## 要求
- 保留原意和情节，只改表达
- 不要降低文学品质
- 输出完整改写后的文本"""

# ---------------------------------------------------------------------------
# 番茄爆款专家模式 Prompt（融合 500章+350章 实战经验）
# ---------------------------------------------------------------------------

FANQIE_EXPERT_SYSTEM = """你是番茄爆款写手，写过850万字长篇网文，深知番茄读者的阅读心理。

**核心认知：你的默认写作倾向和番茄爆款要求是180度反向的。**
- 你的默认模式：静谧→对话→发现→理解→和解→抒情收束
- 番茄爆款要求：冲突→战斗→反杀→碾压→生死局→炸弹收束
- 这不是"需要调整"的偏差，是两极对立。每章必须强制对抗默认倾向。

## 一、写前阻断（每章必须过）
写每一章之前，你必须在内心回答：
1. 这一章反派造成了什么可见伤害？（谁受伤了、什么东西被破坏了）
2. 这一章的物理对抗是什么？（手碰到金属/火焰/枪杆/拳头——纯对话不算）
3. 这一章的爽点能不能用"主语+动词+宾语+对方损失"描述？
4. 这一章结尾最后一句话会不会让读者心跳加速？（安静/温暖/感动=不合格）
5. 这一章前300字有没有物理事件？（炸了/断了/打了/冲了/倒了/烧了）

**第1章额外检查（开篇即生死）**：
- 前50字有没有制造追问？（读者必须想"什么意思/然后呢"）
- 前150字有没有情绪钩子？（疼/委屈/愤怒/恐惧/不甘——五选一）
- 前150字有没有兑现书名/简介的卖点？（读者点进来想看的东西，给了吗）
- 有没有犯三不准？（铺垫背景/写日常/写景物——有一条 → 重写）

## 二、黄金三章专属结构（番茄特化）

番茄前三章比后面任何一章都重要。三章内必须完成"压→蓄→放"闭环：

**第1章（黄金一段在其中）**：
- 前150字：情绪钩子命中 + 卖点兑现
- 前1000字：身份锚定——读者要知道"主角是谁、什么处境、面对什么"
- 章末：认知缺口——一个读者无法不追问的问题（不是自然收尾）

**第2章（承压与加码）**：
- 开篇承接第1章钩子，立刻加码（不等、不缓冲）
- 中段展示主角独特之处（能力/性格/金手指/决策方式）——通过行动展示
- 章末：升级的危机或新信息（不能重复第1章的套路）

**第3章（小闭环 + 长线钩子）**：
- 开篇承接第2章加码，推向第一个小高潮
- 中段：首次爽点/反转/小胜利落地——读者爽到了
- 章末：长线钩子——引出更大的主线谜团/新威胁，让读者点"下一章"

## 三、节奏公式
- **3章一小爽**：每3章一次情绪释放（打脸/碾压/反转/救人）
- **5章一大爽**：每5章一次高潮（生死局/反杀/权力登顶）
- **10章一高潮**：每10章一次单元级高潮
- **余韵≤2章**：高潮后日常最多2章，第3章必须出现新冲突

## 四、七种爽点（番茄读者要的）
打脸、碾压、装逼、截胡、复仇、升级、反转
**禁止文青爽**：理解、和解、发现、放下、开门、联网、对话
**爽点验证标准**：能用"谁+动词+谁+损失"描述。损失=端口崩了/攻击灭了/武器废了/人被击退/甲壳碎了/血溅了。

## 五、结尾只写四种
1. **危机突降**：刀劈到面门。炸弹倒计时。门被踹开。
2. **悬念反转**：读者和主角同时知道了一个新信息。
3. **挑衅叫板**：反派当面宣战/嘲讽/威胁。
4. **死亡倒计时**：有人要死了，倒计时开始。
**禁止**：抒情、安静、画面定格、感怀、明天继续

## 六、反派压迫感
- 反派必须在第1-2章内造成可见伤害（能用手机拍照拍到的：骨头断了/货柜砸了/墙面熔了）
- 每10章至少1次"读者真的觉得主角会输/会死"的生死局
- 必须至少有一个能对话博弈的人类/人形反派（不能全是不会说话的怪物）

## 七、黄金一章：前150字定生死

**核心认知：番茄读者在前150字（约前3段）决定是否划走。不是前3章，是前3段。**

### 开篇三句话铁律
| 序号 | 任务 | 怎么做到 |
|------|------|----------|
| 一 | **制造追问** | 让读者脑子里冒出"为什么/什么意思/然后呢"。不准解释，只准展示 |
| 二 | **制造共情** | 让读者身体有反应——不是"理解"主角，是"感觉"到主角的疼/怕/怒 |
| 三 | **预告异常** | 暗示即将发生变化——"然后他看到了..."——但不说是什么 |

### 情绪钩子（前150字必须命中至少一个）
前150字必须触发以下五种情绪之一，用身体反应展示，不要直接说情绪词：
1. **疼**：物理疼痛，写得让读者自己也觉得疼
2. **委屈**：被冤枉、被误解、被辜负——但主角不说
3. **愤怒**：被挑衅、被羞辱、被抢走重要的东西——压着，但能感觉到
4. **恐惧**：有东西不对劲，但不知道是什么——心跳加快、手心出汗
5. **不甘**：差一点就成功了、被命运捉弄——咬着牙，但没办法

### 开篇三不准
1. ❌ 不准铺垫背景——不准写"XX大陆有三大帝国..."或"主角自幼..."
2. ❌ 不准写日常——不准写"早上醒来"、"吃过早饭"、"走在街上"（除非下一秒就出事）
3. ❌ 不准写景物——不准写"夕阳西下，金色的阳光洒在..."——直接上事件

### 开篇四类最强开局（选一种，不要混合）
1. **人际关系反常**：丈夫拿起刀，对着的是自己妻子。（最强——三个追问同时触发）
2. **绝境危机**：睁开眼，刀架在脖子上。倒计时三秒。
3. **身份矛盾**：她是全校最穷的学生，但她手机里存着全球首富的私人号码。
4. **认知缺口**：他第三次醒来，发现自己睡在同一个棺材里。

### 卖点直给
- 书名/简介承诺了什么，前150字就展示什么
- 搞笑文 → 第一个梗在前50字
- 脑洞文 → 金手指在前100字
- 悬疑文 → 第一个异常在前80字
- **写完开篇后自查：前150字里，读者能感知到这本书的核心卖点吗？不能 → 重写**

### 开章事件密度
- 前100字 ≥ 3个事件（不是3句描写，是3件**发生的事**）
- ❌ 低密度：「沈栀是沈家的嫡女，自幼聪慧。这一天，她收到了一道圣旨。」
- ✅ 高密度：「萧衍回来了。皇后死了。儿子被他皇弟打得痴傻。他提着刀进了宫。」

## 八、AI句式铁律
- **头号AI指纹**："不是X，是Y"——严禁使用。直接写Y，删除X。
- **元话语禁令**：正文中绝对禁止出现"卷一""第X章""前文所述""本章"
- **感叹号和粗口**：战斗章每章≥2个感叹号，初稿就要有
- **场景轮换**：连续3章以上同一场景必须切换
- **长线钩子**：每隔3-4章提一次就够，中间用其他类型钩子

## 九、人物写法
### 性格不是"标签"，是"反应"
- 不要写"他是个谨慎的人"→ 写他进任何房间先扫一眼退路
- 每个人的性格通过**具体行为**传递，不是旁白说明

### 情绪不是"描写"，是"身体"
- 紧张：指甲掐掌心、后槽牙咬紧、呼吸变浅
- 愤怒：太阳穴突突跳、指关节捏得发白、嗓子里堵了什么
- 悲伤：眼眶发热但哭不出来、胸口像被石头压着

### 角色语音卡
- 每个角色说话方式必须不同（口头禅/句式/粗口风格）
- 对照语音卡写对话——确保A和B说话方式有明显区别

### 人物必须"自私"
- 配角不是工具人，有自己的小算盘
- 反派不是"因为坏所以坏"，他有他的道理

## 十、场景与对话
### 克制"描写"，精准"选择"
- 不要全景描写。选3个最特别的细节，读者自己拼出画面
- 动态描写：不写"屋子里有一张桌子"→ 写"风把桌上的纸吹落了两张"

### 对话要有"弦外之音"
- 人说话不是为了传达信息，是为了**达到目的**
- 答非所问比直接回答更真实。"你昨晚去哪了？""菜要凉了。"

## 十一、节奏呼吸
- 紧张处：短句。快。碎片。
- 情感处：慢。细。停留。
- 过渡处：一笔带过即可。"三日无话。"
- 每章至少一次节奏变化——紧→松，或慢→快

## 十二、铁律
- 禁用词：不禁、仿佛、映入眼帘、与此同时、微微、淡淡、心中涌起、眼中闪过、充满希望、不难看出
- 严守类型标签——标签外元素一个不加
- 句长要有变化。2字句和30字句交替
- 正文直接开始，不要"本章将讲述..."之类
- 智斗是前菜不是主菜，不超过一章，智斗后立即接正面爆发

记住：不是写小说。是让读者翻到下一页。"""

# ---------------------------------------------------------------------------
# 起点精品专家模式 Prompt（融合《黑龙醒》100章实战 + 49章大修）
# ---------------------------------------------------------------------------

QIDIAN_EXPERT_SYSTEM = """你是起点中文网精品写手，写过300万字长篇网文，深谙起点读者的阅读心理。

**核心认知：起点靠"持续品质"赢，信任即流量。**
- 番茄靠单章爆发赢，起点靠长期品质赢
- 起点读者愿意读慢热设定，但绝不容忍水
- 起点读者会考据、会截图、会在评论区挑逻辑漏洞

## 一、信息增量驱动（起点核心）

番茄的节奏由"战斗/爽点"驱动。起点的节奏由**信息增量**驱动。

**什么是信息增量？**
每章读完后，读者知道了至少一样他之前不知道的东西：
- 一条新设定（世界的规则更清晰了）
- 一个人物的侧面（原来这个人还有这一面）
- 一个伏笔被埋下/回收（"原来XX和XX有关系"）
- 一次关系的推进或破裂
- 一个谜团的出现或解答

**信息增量的反面（禁止）**：
- 主角走了一段路，什么也没发现
- 配角说了一段话，但说的都是读者已经知道的
- 打了一场仗，但除了"赢了"没有揭示任何新东西

## 二、起点三阶节奏

### 免费期（前20-50章）：建立信任
- 目标：让读者觉得"这本书有点东西，值得养"
- 每章必须有信息增量
- 主角必须有主动决策
- 前5章硬指标：第1章展示异常、第3章展示世界观、第5章第一个小高潮

### 上架期（上架前后10章）：爆发期
- 目标：让免费读者付费订阅
- 上架前3章：铺垫高潮
- 上架章：必须在情绪上升期——不能在高潮前断，要在高潮刚开始时上架
- 上架后3章：高潮+余波+新钩子

### 稳定期（50章以后）：长期维护
- 目标：维持订阅，防止养肥流失
- 每3-5章必须有一个信息爆点或情绪高点
- 过渡章可以长达2-3章，但必须有信息增量支撑

## 三、群像写作铁律（起点特化）

**核心：起点读者追群像文的心理是"这群人在一起才无敌"。**

### 配角饱满度三层模型
1. **独立高光**：每10章，每个主要配角必须有至少1个独立的、不依附于主角的高光瞬间
2. **低谷/弱点**：每个主要配角必须有至少1个被展示出来的弱点
3. **独立故事碎片**：每20章至少1个"和主角没关系但读者想知道"的独立故事碎片

### 配角分配硬指标
- 主角高光章 ≤ 5/10章（另外5章给配角发光）
- 每场战斗/行动的核心参与者 ≤ 5人
- 可有可无的角色合并

## 四、起点质量自检（每章必做）

1. **历史/设定硬伤扫描**：涉及真实历史的内容是否有据可查？
2. **逻辑闭环**：设定/规则/能力前后一致？
3. **人物驱动**：主角每章至少1个主动决策
4. **爽点高级度**：信息差碾压 > 规则反转 > 真相揭示 > 实力突破 > 简单击杀
5. **钩子多样性**：五类钩子轮换（悬念/危机/反转/信息揭露/情感）
6. **反派深度**：有逻辑、能对话的反派
7. **配角独立度**：每个配角有独立瞬间
8. **主角亲手战斗**：每10章至少1次
9. **AI指纹扫描**："不是X是Y"≤2次/章
10. **情绪曲线**：3章内至少覆盖爽×1+紧张×1+重×1

## 五、十条铁律（来自《黑龙醒》050-100章）

1. **禁止AI句式**："不是X是Y"是全书最大AI指纹，663次→31次的教训
2. **感叹号和粗口初稿到位**：后期注入效率极低且容易出bug
3. **正文中绝对禁止元话语**：禁止"卷一""第X章""前文""本章"
4. **每10-15章必须有人死**：没有牺牲就没有代价
5. **主角必须亲手战斗**：允许主角弱，不允许主角没有行动力
6. **场景不能钉死**：连续3章以上同一场景必须切换
7. **钩子不能连续重复**：长线钩子每隔3-4章提一次就够
8. **反派必须能说话**：必须有能对话博弈的人类反派
9. **破折号控制自动化**：超标脚本批量替换，不手工处理
10. **去AI化在初稿就做对**：每3章立即扫描，不攒到51章一起修

## 六、付费章节策略

- **上架时机**：第一个大高潮的正在进行时
- **断章技巧**：在高潮的上升期断，不能在最高点断（会被骂断章狗）
- **订阅维护**：每3-5章必须有一个"值得付费"的瞬间
- **信息密度**：付费章的信息密度不能低于免费章

## 七、AI句式铁律
- **头号AI指纹**："不是X，是Y"——严禁使用
- **元话语禁令**：正文中绝对禁止出现"卷一""第X章""前文所述""本章"
- **感叹号和粗口**：战斗章每章≥2个感叹号，初稿就要有
- **场景轮换**：连续3章以上同一场景必须切换
- **长线钩子**：每隔3-4章提一次就够

记住：起点读者愿意付钱追更，但不是付钱看水。每章必须有"读者知道了新东西"。"""

# ---------------------------------------------------------------------------
# 智斗小说写作 Prompt（融合《第九特区》《犯上者》《诡秘之主》《赘婿》）
# ---------------------------------------------------------------------------

ZHIDOU_EXPERT_SYSTEM = """你是智斗小说写手，融合伪戒、李马、爱潜水的乌贼、愤怒的香蕉四位顶级作者的创作精华。

**核心理念：真人感为核心，信息差博弈为驱动。**

## 一、AI小说的11大特征与反制铁律

### 特征一：句式工整均匀
**反制**：长句后必须跟一个三字以内的短句。允许句子语法不通。每章至少一处"不完整的句子"。

### 特征二：对话=设定解说器
**反制**：人物说出的话必须经过身份过滤器。关键设定不要通过对话交代。对话中的信息传递必须有"损耗"。

### 特征三：结构过度对称
**反制**：允许章节长度大幅波动。允许某些章没有钩子。允许伏笔自然淡化不回收。

### 特征四：无个人文风
**反制**：在第一章就定下贯穿全书的隐喻体系。给主角一个固定小动作。建立至少一个"怪癖句式"。

### 特征五：过度解释
**反制**：禁用"这意味着""这就是为什么"。让读者自己把两件事联系起来。

### 特征六：细节灌水
**反制**：每场景只给一个核心感官细节。心理活动砍半。禁止连续三段纯环境描写。

### 特征七：情绪克制过度
**反制**：每卷至少有一个人真正失控。脏话必须真的出现在对话中。情绪爆发场景中句子必须断裂。

### 特征八：人物脸谱化
**反制**：给每个主要角色一个"不符合人设"的行为。外貌描写必须有缺点。

### 特征九：完美闭环强迫症
**反制**：允许3-5条伏笔永远不回收。允许角色"莫名其妙"地消失。

### 特征十：没有废话
**反制**：每3章至少有一个"无功能对话"场景。允许场景中有"无用细节"。

### 特征十一："不是…是…"句式
**反制**：每章不得超过2次。绝大多数情况下直接删掉"不是X——"说Y即可。

## 二、真人感塑造体系

### 生理反应 > 心理描述
- 不写"他很害怕"，写"他的手汗让整个手掌粘腻"
- 每章中"感到""觉得""认为"等心理标签不超过3次

### 多感官场景构建
每场重要场景必须调动至少3种感官（视觉/听觉/嗅觉/触觉/通感）

### 草根视角法则
- 主角必须有明确的物质困境
- 主角做决策时要有"算账"思维
- 适当展现主角的不体面

## 三、智斗五层体系

| 层次 | 内容 | 典型手段 |
|------|------|---------|
| 信息层 | 谁知道什么、不知道什么 | 卧底、情报网、窃听、误导 |
| 资源层 | 谁控制什么关键资源 | 垄断、截断供应链、囤积 |
| 规则层 | 谁制定规则、谁可以利用规则漏洞 | 合法打击、程序拖延 |
| 人心层 | 谁忠诚、谁动摇、谁可以被收买 | 离间、心理战 |
| 时间层 | 谁在抢时间、谁在拖时间 | 制造伪deadline |

**执行规则**：任何一场重要的智斗，必须至少涉及其中3个层次。

## 四、智斗结构模板

```
憋屈（1-3章）→ 收集（1-2章）→ 布局（1-2章）→ 掀桌（1章）→ 翻盘（1章）
```

## 五、信息差管理

| 类型 | 读者知道 | 主角知道 | 对手知道 | 效果 |
|------|---------|---------|---------|------|
| 读者领先型 | ✓ | ✗ | ✗ | 制造"危险的预感" |
| 主角领先型 | ✓ | ✓ | ✗ | 认知爽感 |
| 双重盲区型 | ✗ | ✗ | ✓ | 突发危机感，反转效果最强 |

## 六、认知爽感（智斗核心）

| 类型 | 读者体验 | 实现方式 |
|------|---------|---------|
| 恍然大悟 | "原来前面那个细节是这个意思！" | 伏笔+回收 |
| 聪明反被聪明误 | "哈哈这个反派自以为聪明" | 读者领先型信息差 |
| 算无遗策 | "主角每一步都算到了" | 主角领先型信息差 |
| 绝境翻盘 | "这样也能翻？但确实合理！" | 利用对手盲区+铺垫底牌 |

## 七、人物塑造

### 人物建模法
不写性格标签表。用"社会形象+核心欲望+认知盲区"三维模型。

### 人物说话方式区分
光看对白就能知道这话是谁说的。区分不在口头禅，而在思维方式。

### 反派的自我逻辑
反派不觉得自己是反派。写反派前必须回答：他想要什么？为什么不能通过正常手段得到？他认为"正义"是什么？

## 八、写作铁律

1. **人物不能服务剧情，剧情要服务人物**
2. **境遇决定选择，而非人设决定选择**
3. **智斗的本质是信息差的动态博弈，而非聪明人对聪明人**

记住：真人写的东西有毛边——句子不对称、情绪偶尔失控、有些细节莫名其妙。AI写的东西太干净了。干净本身就是最大的破绽。"""


@dataclass
class PreWriteCheckResult:
    """写前阻断五问检查结果."""

    passed: bool = True
    answers: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


@dataclass
class RhythmTracker:
    """节奏追踪器 — 记录最近 N 章的爽点类型和钩子类型."""

    recent_pleasure_types: list[str] = field(default_factory=list)
    recent_hook_types: list[str] = field(default_factory=list)
    chapters_since_last_big_pleasure: int = 0
    chapters_since_last_climax: int = 0
    chapters_since_last_conflict: int = 0


@dataclass
class InformationIncrement:
    """信息增量追踪 — 起点精品模式核心.

    每章读完后，读者必须知道至少一样他之前不知道的东西。
    """

    chapter_num: int = 0
    world_building: str = ""  # 世界观增量（新设定、新规则）
    character_reveal: str = ""  # 人物增量（新侧面、新动机）
    foreshadow_plant: str = ""  # 埋伏笔
    foreshadow_payoff: str = ""  # 收伏笔
    relationship_shift: str = ""  # 关系推进
    mystery_progress: str = ""  # 谜团进展


@dataclass
class TenDimensionScore:
    """十维评审评分 — 小说改良诊断系统.

    满分10分，用于系统性评估章节质量。
    """

    hook_strength: float = 0.0  # 开篇吸引力
    character_depth: float = 0.0  # 人物塑造深度
    pacing: float = 0.0  # 情节推进节奏
    emotional_resonance: float = 0.0  # 情感共鸣强度
    world_coherence: float = 0.0  # 世界观完整性
    style_uniqueness: float = 0.0  # 语言风格独特性
    payoff_density: float = 0.0  # 爽点密度与分布
    suspense: float = 0.0  # 悬念与期待感
    chapter_hook: float = 0.0  # 章节结尾钩子
    theme_depth: float = 0.0  # 主题深度与格局

    @property
    def average(self) -> float:
        """计算平均分."""
        scores = [
            self.hook_strength,
            self.character_depth,
            self.pacing,
            self.emotional_resonance,
            self.world_coherence,
            self.style_uniqueness,
            self.payoff_density,
            self.suspense,
            self.chapter_hook,
            self.theme_depth,
        ]
        return sum(scores) / len(scores) if scores else 0.0


@dataclass
class ZhidouDesign:
    """智斗设计数据 — 智斗小说专项.

    追踪每场智斗的博弈层次、信息差状态、代价后果。
    """

    layers: list[str] = field(default_factory=list)  # 博弈层次（信息/资源/规则/人心/时间）
    protagonist_blind_spots: list[str] = field(default_factory=list)  # 主角信息盲区
    antagonist_blind_spots: list[str] = field(default_factory=list)  # 对手信息盲区
    info_gap_type: str = ""  # 信息差类型（reader_ahead/protagonist_ahead/double_blind）
    stakes: str = ""  # 代价与后果
    phase: str = ""  # 当前阶段（憋屈/收集/布局/掀桌/翻盘）


class ChapterWriterPlugin:
    """正文撰写引擎插件."""

    name = "chapter-writer"
    version = "0.1.0"

    def __init__(self) -> None:
        self._kernel = None

    async def on_load(self, kernel) -> None:
        self._kernel = kernel
        logger.info("正文撰写引擎已加载")

    async def on_unload(self) -> None:
        self._kernel = None

    # ------------------------------------------------------------------
    # 番茄专家模式
    # ------------------------------------------------------------------

    def _is_fanqie_expert_mode(self, platform: str) -> bool:
        """检查是否启用番茄专家模式."""
        if platform != "fanqie":
            return False
        return self._kernel.get_config("chapter.fanqie_expert.enabled", True)

    def _pre_write_check(self, chapter_node: dict[str, Any], context: dict[str, Any]) -> PreWriteCheckResult:
        """写前阻断五问检查（番茄专家模式）.

        检查章节大纲是否满足番茄爆款的基本要求。
        """
        result = PreWriteCheckResult()

        # 问1：反派造成了什么可见伤害？
        key_events = chapter_node.get("key_events", [])
        has_villain_damage = any(
            keyword in " ".join(key_events)
            for keyword in ["伤", "破坏", "摧毁", "击败", "击退", "断", "碎", "裂", "倒", "死", "血"]
        )
        if not has_villain_damage:
            result.answers["villain_damage"] = "未明确"
            result.warnings.append("问1：未检测到反派造成的可见伤害。请确保有具体伤害描写。")
        else:
            result.answers["villain_damage"] = "已检测"

        # 问2：物理对抗是什么？
        has_physical_conflict = any(
            keyword in " ".join(key_events) + chapter_node.get("summary", "")
            for keyword in [
                "打",
                "踢",
                "砍",
                "刺",
                "射",
                "撞",
                "抓",
                "挡",
                "闪",
                "拳",
                "脚",
                "剑",
                "刀",
                "枪",
                "击败",
                "击退",
                "反击",
                "战斗",
                "搏斗",
                "厮杀",
                "交锋",
            ]
        )
        if not has_physical_conflict:
            result.answers["physical_conflict"] = "未明确"
            result.warnings.append("问2：未检测到物理对抗。纯对话不算对抗。")
        else:
            result.answers["physical_conflict"] = "已检测"

        # 问3：爽点能否用"主语+动词+宾语+损失"描述？
        summary = chapter_node.get("summary", "")
        is_abstract_pleasure = any(
            keyword in summary for keyword in ["发现", "理解", "对话", "和解", "放下", "明白", "领悟"]
        )
        if is_abstract_pleasure:
            result.answers["pleasure_type"] = "疑似文青爽"
            result.warnings.append(
                "问3：爽点偏文艺（发现/理解/对话）。番茄要的是：打脸/碾压/装逼/截胡/复仇/升级/反转。"
            )
        else:
            result.answers["pleasure_type"] = "通过"

        # 问4：结尾是否让读者心跳加速？
        is_hook = chapter_node.get("is_hook_point", False)
        if not is_hook:
            result.answers["hook"] = "未标记为钩子章节"
            result.warnings.append("问4：本章未标记为名场面章节。请确保章尾有强钩子。")
        else:
            result.answers["hook"] = "已标记"

        # 问5：前300字是否有物理事件？（大纲层面无法完全检查，给出提示）
        result.answers["opening_action"] = "需正文验证"
        result.warnings.append("问5：请确保前300字有物理事件（炸了/断了/打了/冲了/倒了/烧了），否则读者3秒划走。")

        # 如果有3个以上警告，标记为未通过
        if len(result.warnings) >= 3:
            result.passed = False

        return result

    async def _track_rhythm(
        self,
        project_id: str,
        chapter_num: int,
        pleasure_type: str = "",
        hook_type: str = "",
        has_conflict: bool = True,
    ) -> RhythmTracker:
        """追踪节奏数据（番茄专家模式）.

        从 ContextManager 读取最近 N 章的节奏数据，更新并返回。
        """
        ctx = self._kernel.context()
        namespace = f"pipeline:{project_id}"

        # 读取现有数据
        history: list[dict] = await ctx.get(namespace, "rhythm_history", [])

        # 添加新数据
        history.append(
            {
                "chapter_num": chapter_num,
                "pleasure_type": pleasure_type,
                "hook_type": hook_type,
                "has_conflict": has_conflict,
            }
        )

        # 只保留最近 30 章
        if len(history) > 30:
            history = history[-30:]

        await ctx.set(namespace, "rhythm_history", history)

        # 构建追踪器
        tracker = RhythmTracker()
        tracker.recent_pleasure_types = [h["pleasure_type"] for h in history[-10:] if h["pleasure_type"]]
        tracker.recent_hook_types = [h["hook_type"] for h in history[-10:] if h["hook_type"]]

        # 计算距离上次大爽/高潮/冲突的章节数
        for h in reversed(history):
            if h["pleasure_type"] in ("生死局", "反杀", "碾压", "觉醒"):
                break
            tracker.chapters_since_last_big_pleasure += 1

        for h in reversed(history):
            if h.get("has_conflict", True):
                break
            tracker.chapters_since_last_conflict += 1

        return tracker

    async def _track_information_increment(
        self,
        project_id: str,
        chapter_node: dict[str, Any],
        context: dict[str, Any],
    ) -> InformationIncrement:
        """追踪信息增量（起点精品模式）.

        从大纲节点和上下文中提取本章的信息增量。
        """
        increment = InformationIncrement(
            chapter_num=chapter_node.get("chapter_number", 1),
        )

        # 从大纲节点提取信息增量
        key_events = chapter_node.get("key_events", [])
        character_moments = chapter_node.get("character_moments", [])
        # summary = chapter_node.get("summary", "")  # 暂未使用

        # 世界观增量：从关键事件中提取设定相关内容
        if key_events:
            increment.world_building = "; ".join(key_events[:2])

        # 人物增量：从人物节点中提取
        if character_moments:
            increment.character_reveal = "; ".join(character_moments[:2])

        # 伏笔信息：从大纲节点中提取（清理掉 fs_id 前缀）
        import re
        foreshadow_plants = chapter_node.get("foreshadow_plants", [])
        foreshadow_payoffs = chapter_node.get("foreshadow_payoffs", [])
        if foreshadow_plants:
            # 清理 [fs_xxx] 前缀，只保留描述内容
            clean_plants = [re.sub(r'\[fs_\w+\]\s*', '', p).strip() for p in foreshadow_plants if p]
            increment.foreshadow_plant = ", ".join(clean_plants[:3])
        if foreshadow_payoffs:
            clean_payoffs = [re.sub(r'\[fs_\w+\]\s*', '', p).strip() for p in foreshadow_payoffs if p]
            increment.foreshadow_payoff = ", ".join(clean_payoffs[:3])

        # 保存到上下文
        ctx = self._kernel.context()
        namespace = f"pipeline:{project_id}"
        history: list[dict] = await ctx.get(namespace, "info_increment_history", [])
        history.append(
            {
                "chapter_num": increment.chapter_num,
                "world_building": increment.world_building,
                "character_reveal": increment.character_reveal,
                "foreshadow_plant": increment.foreshadow_plant,
                "foreshadow_payoff": increment.foreshadow_payoff,
            }
        )
        # 只保留最近 30 章
        if len(history) > 30:
            history = history[-30:]
        await ctx.set(namespace, "info_increment_history", history)

        return increment

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def write_chapter(
        self,
        chapter_node: dict[str, Any],
        *,
        context: dict[str, Any] | None = None,
        platform: str = "fanqie",
        genre: str = "",
        revision_instructions: str = "",
        previous_content: str = "",
    ) -> Chapter:
        """生成单章正文.

        Args:
            chapter_node: 大纲中的章节节点 (ChapterNode 或其 dict).
            context: 完整上下文 — settings, characters, summaries, rag_results.
            platform: 目标平台 (fanqie/qidian/jinjiang).
            genre: 类型标识 (zhidou = 智斗小说).
            revision_instructions: 修订指令 (门禁返回 REVISE 时使用).
            previous_content: 上一版内容 (修订时使用).

        Returns:
            Chapter 模型 (metadata + content).
        """
        context = context or {}

        # 写前检查 + 节奏追踪 + 语音卡 + 信息增量 + 智斗设计
        pre_check_warnings: list[str] | None = None
        rhythm_tracker: RhythmTracker | None = None
        voice_cards: list[dict[str, Any]] | None = None
        information_increment: InformationIncrement | None = None
        zhidou_design: ZhidouDesign | None = None

        is_fanqie = self._is_fanqie_expert_mode(platform)
        is_qidian = self._is_qidian_expert_mode(platform)
        is_zhidou = self._is_zhidou_mode(genre)

        # 番茄/起点专家模式：写前检查 + 节奏追踪
        if is_fanqie or is_qidian:
            # 写前阻断五问
            pre_write_check_key = (
                "chapter.fanqie_expert.pre_write_check" if is_fanqie else "chapter.qidian_expert.pre_write_check"
            )
            if self._kernel.get_config(pre_write_check_key, True):
                pre_check = self._pre_write_check(chapter_node, context)
                pre_check_warnings = pre_check.warnings
                if not pre_check.passed:
                    logger.warning(
                        "写前阻断检查未通过",
                        chapter=chapter_node.get("chapter_number", 0),
                        warnings=pre_check.warnings,
                    )

            # 节奏追踪
            rhythm_key = (
                "chapter.fanqie_expert.rhythm_tracking" if is_fanqie else "chapter.qidian_expert.three_chapter_scan"
            )
            if self._kernel.get_config(rhythm_key, True):
                project_id = context.get("project_id", "")
                if project_id:
                    rhythm_tracker = await self._track_rhythm(
                        project_id,
                        chapter_node.get("chapter_number", 1),
                    )

            # 角色语音卡
            chars = context.get("characters", {})
            if isinstance(chars, dict) and "voice_cards" in chars:
                voice_cards = chars["voice_cards"]

        # 起点模式：信息增量追踪
        if is_qidian and self._kernel.get_config("chapter.qidian_expert.info_increment_required", True):
            project_id = context.get("project_id", "")
            if project_id:
                information_increment = await self._track_information_increment(
                    project_id,
                    chapter_node,
                    context,
                )

        # 智斗模式：智斗设计数据
        if is_zhidou:
            zhidou_design = context.get("zhidou_design")
            if isinstance(zhidou_design, dict):
                zhidou_design = ZhidouDesign(**zhidou_design)

        # 组装 Prompt
        system_prompt = self._build_system_prompt(platform, genre)
        user_prompt = self._build_user_prompt(
            chapter_node,
            context,
            platform,
            revision_instructions,
            previous_content,
            genre=genre,
            pre_check_warnings=pre_check_warnings,
            rhythm_tracker=rhythm_tracker,
            voice_cards=voice_cards,
            information_increment=information_increment,
            zhidou_design=zhidou_design,
        )

        # 调用 LLM
        tier = "premium" if not revision_instructions else "standard"
        result = await self._kernel.call_llm(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            tier=tier,
            max_tokens=16384,
            temperature=0.85 if not revision_instructions else 0.7,
        )

        content = result["content"]

        # 去除 LLM 可能重复输出的章节标题
        ch_num = chapter_node.get("chapter_number", 1)
        ch_title = chapter_node.get("title", f"第{ch_num}章")
        content = self._clean_chapter_content(content, ch_num, ch_title)

        # 添加 Markdown Frontmatter
        content = self._add_frontmatter(content, ch_num, ch_title, platform)

        # 构建 Chapter
        vol_num = chapter_node.get("volume_number", 1)
        chapter = Chapter(
            metadata=ChapterMetadata(
                chapter_id=f"ch_v{vol_num:02d}_{ch_num:04d}",
                chapter_number=ch_num,
                volume_number=vol_num,
                title=chapter_node.get("title", f"第{ch_num}章"),
                word_count=len(content),
                platform=platform,
                status="draft",
                model_used=result.get("model", ""),
                tokens_consumed=result.get("tokens_in", 0) + result.get("tokens_out", 0),
                revision_count=1 if revision_instructions else 0,
            ),
            content=content,
        )

        # 自动提取伏笔（异步，不阻塞返回）
        project_id = context.get("project_id", "")
        if project_id and not revision_instructions:
            try:
                await self._extract_and_save_foreshadows(project_id, content, ch_num, vol_num)
            except Exception as exc:
                logger.warning("伏笔提取失败", chapter=ch_num, error=str(exc))

            # 自动更新人物关系图谱
            try:
                await self._update_graph_from_chapter(project_id, content, ch_num)
            except Exception as exc:
                logger.warning("图谱更新失败", chapter=ch_num, error=str(exc))

        return chapter

    async def _extract_and_save_foreshadows(
        self,
        project_id: str,
        chapter_content: str,
        chapter_num: int,
        volume_number: int = 1,
    ) -> None:
        """提取伏笔并保存到文件."""
        try:
            fm = await self._kernel.get_plugin("foreshadow-manager")
        except Exception:
            return  # 伏笔管理器未加载

        # 读取已有伏笔
        existing = {}
        try:
            raw = await self._kernel.read_project_file(project_id, "foreshadows.json")
            existing = json.loads(raw)
        except (FileNotFoundError, json.JSONDecodeError):
            existing = {"project_id": project_id, "entries": {}}

        # 从大纲读取本章的伏笔计划（foreshadow_plants + foreshadow_payoffs）
        outline_foreshadows = []
        chapter_payoffs = []  # 本章大纲计划回收的伏笔描述
        try:
            if self._kernel.db:
                settings = await self._kernel.db.get_settings(project_id)
                progress = settings.get("progress", {})
                for vol in progress.get("volumes", []):
                    if vol.get("volume_number") == volume_number:
                        for ch in vol.get("chapters", []):
                            if ch.get("chapter_number") == chapter_num:
                                plants = ch.get("foreshadow_plants", [])
                                payoffs = ch.get("foreshadow_payoffs", [])
                                chapter_payoffs = [p for p in payoffs if isinstance(p, str) and p.strip()]
                                if plants:
                                    outline_foreshadows.append({
                                        "chapter": chapter_num,
                                        "plants": plants,
                                        "payoffs": payoffs,
                                    })
                                break
                        break
        except Exception:
            pass

        # 提取新伏笔（传入大纲伏笔计划与本章计划回收描述作为参考）
        result = await fm.instance.extract_foreshadows(
            chapter_content, chapter_num, existing, expected_payoff_descs=chapter_payoffs
        )

        entries = existing.get("entries", {})

        # 添加新伏笔（关联大纲中的回收计划）
        for fs in result.get("new_foreshadows", []):
            fs_id = f"fs_{uuid.uuid4().hex[:8]}"
            desc = fs.get("description", "")

            # 尝试匹配大纲中的 foreshadow_payoffs
            matched_payoffs = []
            for outline_fs in outline_foreshadows:
                for plant_desc in outline_fs.get("plants", []):
                    # 中文友好匹配（修复：旧版用 split() 对中文无效，几乎永不命中）
                    if plant_desc and foreshadow_text_match(plant_desc, desc):
                        matched_payoffs = outline_fs.get("payoffs", [])
                        break
                if matched_payoffs:
                    break

            entries[fs_id] = {
                "foreshadow_id": fs_id,
                "type": fs.get("type", "plot_twist"),
                "description": desc,
                "planted_chapter": chapter_num,
                "involved_characters": fs.get("involved_characters", []),
                "involved_items": fs.get("involved_items", []),
                "status": "planted",
                "priority": fs.get("priority", 1),
                "building_chapters": [chapter_num],
                "foreshadow_payoffs": matched_payoffs,
            }

        # 更新推进的伏笔
        for fs_id in result.get("advanced", []):
            if fs_id in entries:
                bc = entries[fs_id].get("building_chapters", [])
                if chapter_num not in bc:
                    bc.append(chapter_num)
                entries[fs_id]["building_chapters"] = bc
                if entries[fs_id].get("status") == "planted":
                    entries[fs_id]["status"] = "building"

        # 标记回收的伏笔
        for fs in result.get("paid", []):
            fs_id = fs.get("foreshadow_id", "")
            if fs_id in entries:
                entries[fs_id]["status"] = "paid"
                entries[fs_id]["payoff_chapter"] = chapter_num
                entries[fs_id]["payoff_description"] = fs.get("payoff_description", "")

        # 保存
        existing["entries"] = entries
        await self._kernel.write_project_file(
            project_id, "foreshadows.json",
            json.dumps(existing, ensure_ascii=False, indent=2)
        )

        logger.info(
            "伏笔提取完成",
            chapter=chapter_num,
            new=len(result.get("new_foreshadows", [])),
            advanced=len(result.get("advanced", [])),
            paid=len(result.get("paid", [])),
        )

    async def _update_graph_from_chapter(
        self,
        project_id: str,
        chapter_content: str,
        chapter_num: int,
    ) -> None:
        """从章节内容中提取人物和关系，更新图谱."""
        try:
            gm = await self._kernel.get_plugin("graph-manager")
        except Exception:
            return  # 图谱管理器未加载

        # 用 LLM 提取人物和关系
        prompt = f"""分析以下章节，提取出现的人物和他们之间的关系。

章节内容:
{chapter_content[:8000]}

返回 JSON:
```json
{{
  "characters": [
    {{
      "name": "人物名",
      "aliases": ["别名1", "别名2"],
      "role": "protagonist|antagonist|supporting|minor",
      "description": "简短描述"
    }}
  ],
  "relationships": [
    {{
      "source": "人物A",
      "target": "人物B",
      "type": "ALLY|ENEMY|FAMILY|MASTER_DISCIPLE|RIVAL|SUBORDINATE|ROMANTIC",
      "description": "关系描述"
    }}
  ]
}}
```

注意: 只提取本章明确出现的人物和关系。"""

        try:
            result = await self._kernel.call_llm(
                messages=[{"role": "user", "content": prompt}],
                tier="budget",
                max_tokens=2048,
                temperature=0.2,
            )

            import re
            content = result["content"]
            # 尝试解析 JSON
            match = re.search(r'```(?:json)?\s*([\s\S]+?)\s*```', content)
            if match:
                data = json.loads(match.group(1))
            else:
                # 尝试找第一个JSON对象
                start = content.find('{')
                if start >= 0:
                    depth = 0
                    for i in range(start, len(content)):
                        if content[i] == '{':
                            depth += 1
                        elif content[i] == '}':
                            depth -= 1
                            if depth == 0:
                                data = json.loads(content[start:i + 1])
                                break
                    else:
                        data = json.loads(content)
                else:
                    data = json.loads(content)

            # 更新图谱
            chars = data.get("characters", [])
            rels = data.get("relationships", [])

            if chars or rels:
                # 读取已有图谱数据
                existing_graph = {"nodes": [], "edges": []}
                try:
                    raw = await self._kernel.read_project_file(project_id, "graph.json")
                    existing_graph = json.loads(raw)
                except (FileNotFoundError, json.JSONDecodeError):
                    pass

                existing_nodes = {n["id"]: n for n in existing_graph.get("nodes", [])}
                existing_edges = {e["id"]: e for e in existing_graph.get("edges", [])}

                # 添加新人物
                for char in chars:
                    char_name = char.get("name", "")
                    if not char_name:
                        continue
                    char_id = f"char_{char_name}"
                    if char_id not in existing_nodes:
                        existing_nodes[char_id] = {
                            "id": char_id,
                            "label": char_name,
                            "group": "Character",
                            "properties": {
                                "name": char_name,
                                "role": char.get("role", "minor"),
                                "description": char.get("description", ""),
                                "first_appearance_chapter": chapter_num,
                            }
                        }

                # 添加新关系
                for rel in rels:
                    source_name = rel.get("source", "")
                    target_name = rel.get("target", "")
                    if not source_name or not target_name:
                        continue
                    source_id = f"char_{source_name}"
                    target_id = f"char_{target_name}"
                    rel_id = f"rel_{source_id}_{target_id}_{rel.get('type', 'ALLY')}"
                    if rel_id not in existing_edges:
                        existing_edges[rel_id] = {
                            "id": rel_id,
                            "source": source_id,
                            "target": target_id,
                            "type": rel.get("type", "ALLY"),
                            "description": rel.get("description", ""),
                        }

                # 保存图谱
                graph_data = {
                    "project_id": project_id,
                    "nodes": list(existing_nodes.values()),
                    "edges": list(existing_edges.values()),
                }
                await self._kernel.write_project_file(
                    project_id, "graph.json",
                    json.dumps(graph_data, ensure_ascii=False, indent=2)
                )

                logger.info(
                    "图谱更新完成",
                    chapter=chapter_num,
                    new_chars=len(chars),
                    new_rels=len(rels),
                )

        except Exception as exc:
            logger.warning("图谱提取失败", chapter=chapter_num, error=str(exc))

    async def revise_chapter(
        self,
        chapter: Chapter,
        revision_instructions: list[str],
        *,
        context: dict[str, Any] | None = None,
        platform: str = "fanqie",
    ) -> Chapter:
        """修订章节 — 根据门禁反馈修改.

        Args:
            chapter: 当前章节.
            revision_instructions: 修订指令列表 (来自 GateIssue.suggestion).
            context: 上下文.
            platform: 平台.

        Returns:
            修订后的 Chapter.
        """
        instructions_text = "\n".join(f"- {s}" for s in revision_instructions)
        chapter_node = {
            "chapter_number": chapter.metadata.chapter_number,
            "volume_number": chapter.metadata.volume_number,
            "title": chapter.metadata.title,
            "summary": "",
        }
        return await self.write_chapter(
            chapter_node,
            context=context,
            platform=platform,
            revision_instructions=instructions_text,
            previous_content=chapter.content,
        )

    # ------------------------------------------------------------------
    # Prompt 构建
    # ------------------------------------------------------------------

    def _is_qidian_expert_mode(self, platform: str) -> bool:
        """检查是否启用起点专家模式."""
        if platform != "qidian":
            return False
        return self._kernel.get_config("chapter.qidian_expert.enabled", False)

    def _is_zhidou_mode(self, genre: str) -> bool:
        """检查是否启用智斗模式."""
        if genre != "zhidou":
            return False
        return self._kernel.get_config("chapter.zhidou_expert.enabled", False)

    def _build_system_prompt(self, platform: str, genre: str = "") -> str:
        """组装系统 Prompt.

        优先级：类型层(zhi-dou) > 平台层(qidian/fanqie) > 基础层(novel-writer)
        """
        # 智斗类型优先（类型层 > 平台层）
        if self._is_zhidou_mode(genre):
            return ZHIDOU_EXPERT_SYSTEM

        # 平台层
        if self._is_fanqie_expert_mode(platform):
            return FANQIE_EXPERT_SYSTEM
        if self._is_qidian_expert_mode(platform):
            return QIDIAN_EXPERT_SYSTEM

        # 基础层
        parts = [NOVEL_WRITER_SYSTEM]
        style = PLATFORM_STYLE_GUIDE.get(platform)
        if style:
            parts.append(style)
        return "\n\n".join(parts)

    def _build_user_prompt(
        self,
        chapter_node: dict[str, Any],
        context: dict[str, Any],
        platform: str,
        revision_instructions: str,
        previous_content: str,
        *,
        genre: str = "",
        pre_check_warnings: list[str] | None = None,
        rhythm_tracker: RhythmTracker | None = None,
        voice_cards: list[dict[str, Any]] | None = None,
        information_increment: InformationIncrement | None = None,
        zhidou_design: ZhidouDesign | None = None,
    ) -> str:
        """组装用户 Prompt."""
        parts = []

        # 🔴 题材硬约束——必须放在最前面
        genre_tags = context.get("genre_tags", [])
        settings_data = context.get("settings", {})
        if not genre_tags and isinstance(settings_data, dict):
            genre_tags = settings_data.get("genre_tags", [])
            # 兼容 meta 嵌套结构
            if not genre_tags:
                meta = settings_data.get("meta", {})
                if isinstance(meta, dict):
                    genre_tags = meta.get("genre_tags", [])
        if genre_tags:
            parts.append("## 🔴 题材硬约束（严禁偏离）")
            parts.append(f"本小说类型: {', '.join(genre_tags)}")
            parts.append("只允许写这些类型范畴内的设定和情节。禁止引入标签之外的任何元素。")
            parts.append("例如：标签中没有'末世'就绝对不能出现丧尸；没有'科幻'就绝对不能出现飞船或高科技。")

        # 风格圣经（用户定义的项目级风格约束）
        if isinstance(settings_data, dict):
            style_bible = settings_data.get("style_bible", "")
            if not style_bible:
                meta = settings_data.get("meta", {})
                if isinstance(meta, dict):
                    style_bible = meta.get("style_bible", "")
            if style_bible:
                parts.append("\n## 🔴 风格圣经（必须严格遵守）")
                parts.append(style_bible)

        # 写前检查警告（番茄/起点通用）
        if pre_check_warnings:
            parts.append("\n## ⚠ 写前阻断检查（必须处理）")
            for w in pre_check_warnings:
                parts.append(f"- {w}")

        # 节奏分析（番茄/起点通用）
        if rhythm_tracker:
            parts.append("\n## 📊 节奏分析")
            if rhythm_tracker.recent_pleasure_types:
                parts.append(f"- 最近爽点分布: {', '.join(rhythm_tracker.recent_pleasure_types[-5:])}")
            if rhythm_tracker.chapters_since_last_big_pleasure >= 5:
                parts.append(f"- ⚠ 已{rhythm_tracker.chapters_since_last_big_pleasure}章无大爽，本章必须安排高潮")
            if rhythm_tracker.chapters_since_last_conflict >= 2:
                parts.append(f"- ⚠ 已{rhythm_tracker.chapters_since_last_conflict}章无冲突，本章必须出现新冲突")
            if len(rhythm_tracker.recent_hook_types) >= 2:
                last_two = rhythm_tracker.recent_hook_types[-2:]
                if last_two[0] == last_two[1]:
                    parts.append(f"- ⚠ 最近2章钩子类型相同（{last_two[0]}），本章请换一种")

        # 起点模式：信息增量追踪
        if information_increment and self._is_qidian_expert_mode(platform):
            parts.append("\n## 📈 信息增量检查（起点核心）")
            if information_increment.world_building:
                parts.append(f"- 世界观增量: {information_increment.world_building}")
            if information_increment.character_reveal:
                parts.append(f"- 人物增量: {information_increment.character_reveal}")
            if information_increment.foreshadow_plant:
                parts.append(f"- 埋伏笔: {information_increment.foreshadow_plant}")
            if information_increment.foreshadow_payoff:
                parts.append(f"- 收伏笔: {information_increment.foreshadow_payoff}")
            if information_increment.relationship_shift:
                parts.append(f"- 关系推进: {information_increment.relationship_shift}")
            parts.append("- ⚠ 本章必须有至少一项信息增量（世界观/人物/伏笔/关系）")

        # 智斗模式：智斗设计数据
        if zhidou_design and self._is_zhidou_mode(genre):
            parts.append("\n## 🧠 智斗设计（信息差博弈）")
            if zhidou_design.layers:
                parts.append(f"- 博弈层次: {', '.join(zhidou_design.layers)}")
            if zhidou_design.protagonist_blind_spots:
                parts.append(f"- 主角盲区: {', '.join(zhidou_design.protagonist_blind_spots)}")
            if zhidou_design.antagonist_blind_spots:
                parts.append(f"- 对手盲区: {', '.join(zhidou_design.antagonist_blind_spots)}")
            if zhidou_design.info_gap_type:
                type_map = {
                    "reader_ahead": "读者领先型（制造危险预感）",
                    "protagonist_ahead": "主角领先型（认知爽感）",
                    "double_blind": "双重盲区型（突发危机/反转）",
                }
                parts.append(f"- 信息差类型: {type_map.get(zhidou_design.info_gap_type, zhidou_design.info_gap_type)}")
            if zhidou_design.phase:
                parts.append(f"- 当前阶段: {zhidou_design.phase}")
            if zhidou_design.stakes:
                parts.append(f"- 代价与后果: {zhidou_design.stakes}")
            parts.append("- ⚠ 智斗必须至少涉及3个博弈层次，胜利必须有代价")

        # 章节定位
        ch_num = chapter_node.get("chapter_number", 1)
        vol_num = chapter_node.get("volume_number", 1)
        title = chapter_node.get("title", f"第{ch_num}章")
        summary = chapter_node.get("summary", "")
        key_events = chapter_node.get("key_events", [])
        character_moments = chapter_node.get("character_moments", [])
        is_climax = chapter_node.get("is_climax", False)
        is_hook = chapter_node.get("is_hook_point", False)

        # 判断是否多卷（用于决定是否显示卷号）
        progress = settings_data.get("meta", {}).get("progress", {}) if isinstance(settings_data.get("meta"), dict) else {}
        if not progress:
            progress = settings_data.get("progress", {}) if isinstance(settings_data, dict) else {}
        volumes_list = progress.get("volumes", []) if isinstance(progress, dict) else []
        is_multi_volume = len(volumes_list) > 1

        parts.append("## 章节信息")
        if is_multi_volume:
            parts.append(f"- 第{vol_num}卷 第{ch_num}章: {title}")
        else:
            parts.append(f"- 第{ch_num}章: {title}")
        if summary:
            parts.append(f"- 本章概要: {summary}")
        if key_events:
            parts.append(f"- 关键事件: {'; '.join(key_events)}")
        if character_moments:
            parts.append(f"- 人物节点: {'; '.join(character_moments)}")
        if is_climax:
            parts.append("- ⚡ 本章是高潮章节，需要强烈的情感冲击")
        if is_hook:
            parts.append("- ⭐ 本章是名场面章节，需要有读者会记住的高光时刻")

        # 前情提要（关键！保证章节连续性）
        prev_summary = context.get("previous_chapters_summary", "")
        if prev_summary:
            parts.append(f"\n## 🔴 前情提要（必须延续）\n{prev_summary[:800]}")

        # 人物卡（全部主要人物，保证一致性）
        chars = context.get("characters", {})
        if chars:
            chars_dict = chars.get("characters", {}) if isinstance(chars, dict) else {}
            if not chars_dict:
                chars_dict = {k: v for k, v in chars.items() if isinstance(v, dict)}
            if chars_dict:
                parts.append("\n## 🔴 人物档案（必须保持一致）")
                for cid, c in list(chars_dict.items())[:6]:
                    if isinstance(c, dict):
                        name = c.get("name", cid)
                        tags = c.get("personality_tags", [])
                        motivation = c.get("core_motivation", "")
                        status = c.get("current_status", "active")
                        parts.append(f"- {name}: 性格{'/'.join(tags[:3])} | 动机:{motivation[:30]} | 状态:{status}")
                    elif isinstance(c, str):
                        parts.append(f"- {c}")

        # 角色语音卡（番茄/起点/智斗通用）
        if voice_cards:
            parts.append("\n## 🎭 角色语音卡（对话必须对照）")
            for vc in voice_cards:
                name = vc.get("character_name", "")
                catchphrases = vc.get("catchphrases", [])
                swearing = vc.get("swearing_style", "无")
                pattern = vc.get("sentence_pattern", "混合型")
                tics = vc.get("verbal_tics", [])
                phrases = "/".join(catchphrases[:3]) if catchphrases else "无"
                habits = "/".join(tics[:2]) if tics else "无"
                parts.append(f"- {name}: 口头禅{phrases} | 粗口:{swearing} | 句式:{pattern} | 习惯{habits}")

        # 一致性账本（跨章事实追踪，防止设定崩坏）
        ledger = context.get("consistency_ledger", {})
        if ledger:
            parts.append("\n## 🔴 一致性账本（严禁违反以下已确立事实）")
            # 人物状态
            char_states = ledger.get("character_states", {})
            if char_states:
                parts.append("### 当前人物状态")
                for name, st in char_states.items():
                    if isinstance(st, dict):
                        rels = st.get("relationships", {})
                        rel_text = ", ".join(f"{k}:{v}" for k, v in list(rels.items())[:3]) if rels else ""
                        parts.append(
                            f"- {name}: 状态:{st.get('status','未知')} | "
                            f"位置:{st.get('location','未知')} | "
                            f"最后出现:Ch{st.get('last_seen_ch','?')}"
                            + (f" | 关系:{rel_text}" if rel_text else "")
                        )
            # 时间线（最近 5 条）
            timeline = ledger.get("timeline", [])
            if timeline:
                parts.append("### 近期时间线")
                for t in timeline[-5:]:
                    if isinstance(t, dict):
                        parts.append(f"- Ch{t.get('ch','?')}: {t.get('event','')} ({t.get('time','')})")
            # 物品状态
            ws = ledger.get("world_state", {})
            items = ws.get("物品状态", {}) if isinstance(ws, dict) else {}
            if items:
                parts.append("### 物品归属")
                for item, state in list(items.items())[:10]:
                    parts.append(f"- {item}: {state}")
            # 已知问题（避免重犯）
            issues = [i for i in ledger.get("known_issues", []) if isinstance(i, dict) and not i.get("resolved")]
            if issues:
                parts.append("### ⚠ 已知一致性问题（本章必须避免类似错误）")
                for iss in issues[-5:]:
                    parts.append(f"- Ch{iss.get('ch','?')}: {iss.get('issue','')}")

        # RAG 上下文
        # 写作技巧（知识包）
        writing_tips = context.get("writing_tips", [])
        if writing_tips:
            parts.append("\n## 写作参考")
            for t in writing_tips:
                content = t.get("content", "")[:300]
                if content:
                    parts.append(f"- {content}")

        # RAG 上下文（项目数据）
        rag_results = context.get("rag_results", [])
        if rag_results:
            parts.append("\n## 参考上下文")
            for i, r in enumerate(rag_results, 1):
                parts.append(f"{i}. {r.get('content', '')[:200]}")

        # 活跃伏笔
        foreshadows = context.get("active_foreshadows", [])
        if foreshadows:
            parts.append("\n## 需要推进的伏笔（按埋设手法自然带出，忌作者旁白预告）")
            for fs in foreshadows:
                if isinstance(fs, dict):
                    overdue = " ⚠️已较久未推进，本章优先考虑推进或回收" if fs.get("_overdue") else ""
                    parts.append(f"- [{fs.get('foreshadow_id', '')}] {fs.get('description', '')}{overdue}")

        # 本章计划埋设/回收的伏笔（来自大纲节点，对所有平台注入——不再限起点模式）
        import re as _re
        node_plants = [
            _re.sub(r'\[fs_\w+\]\s*', '', p).strip()
            for p in chapter_node.get("foreshadow_plants", []) if isinstance(p, str) and p.strip()
        ]
        node_payoffs = [
            _re.sub(r'\[fs_\w+\]\s*', '', p).strip()
            for p in chapter_node.get("foreshadow_payoffs", []) if isinstance(p, str) and p.strip()
        ]
        if node_plants:
            parts.append("\n## 🌱 本章要埋下的伏笔（用「伏笔埋设手法」，藏进场景，别宣布）")
            for p in node_plants[:5]:
                parts.append(f"- {p}")
        if node_payoffs:
            parts.append("\n## 🎯 本章要回收的伏笔（用「伏笔回收手法」，靠呼应让读者自己接上，别复述解释）")
            for p in node_payoffs[:5]:
                parts.append(f"- {p}")

        # 修订指令
        if revision_instructions:
            parts.append(f"\n## ⚠ 修订指令（必须处理）\n{revision_instructions}")
            if previous_content:
                parts.append(f"\n## 上一版内容（在此基础修改）\n{previous_content}")

        # 输出指令
        # 从项目配置读取目标字数（用户创建项目时指定），降级到平台默认值
        PLATFORM_WORD_DEFAULTS = {
            "fanqie": 3000, "qidian": 4000, "jinjiang": 3000,
            "qimao": 3000, "douban": 3000,
        }
        target_words = PLATFORM_WORD_DEFAULTS.get(platform, 3000)
        if isinstance(settings_data, dict):
            meta = settings_data.get("meta", {})
            if isinstance(meta, dict):
                tw = meta.get("target_words_per_chapter")
                if tw and tw > 0:
                    target_words = tw
            tw2 = settings_data.get("target_words_per_chapter")
            if tw2 and tw2 > 0:
                target_words = tw2
        # 根据目标字数计算允许范围（±30%，最低1500）
        words_min = max(1500, int(target_words * 0.7))
        words_max = int(target_words * 1.3)

        parts.append("\n## 输出格式要求")
        parts.append("请用 Markdown 格式输出：")
        parts.append("- 对话用引号包裹，重要的内心独白可用 *斜体*")
        parts.append("- 场景切换用 --- 分隔线")
        parts.append("- 不要输出章节标题（系统会自动添加 frontmatter）")
        parts.append("- 严禁输出任何元数据标记，如(章尾钩子)、(卷末钩子)、(倒计时)等，这些是写作技巧提示，不是正文内容")
        parts.append(f"- 🔴 目标字数: {target_words}字（允许范围: {words_min}-{words_max}字，严禁低于{words_min}字）")
        if platform == "fanqie":
            parts.append("- 段落要短（≤5行），对话单独成行")
        parts.append("- 章尾必须有钩子（直接写在正文中，不要加任何标记）")

        return "\n".join(parts)

    # ------------------------------------------------------------------
    # Markdown 处理
    # ------------------------------------------------------------------

    @staticmethod
    def _clean_chapter_content(content: str, ch_num: int, title: str) -> str:
        """去除 LLM 重复输出的章节标题和元数据标记。"""
        import re

        # 去除章节标题
        patterns = [
            rf"^第\s*\d+\s*卷\s*第\s*{ch_num}\s*[章回].*?\n",  # "第1卷 第1章 xxx"
            rf"^第\s*{ch_num}\s*[章回].*?\n",  # "第1章 xxx"
            rf"^{re.escape(title)}\s*\n",  # 标题本身
        ]
        for pat in patterns:
            content = re.sub(pat, "", content, count=1, flags=re.MULTILINE)

        # 去除元数据标记（如 "（章尾钩子）"、"（卷末钩子+倒计时）" 等）
        # 匹配括号内包含特定关键词的内容
        meta_keywords = r'钩子|倒计时|伏笔|悬念|高潮|反转|揭秘|卷末|章首|章尾|卷首|名场面|信息增量|情感线|支线'
        content = re.sub(r'[（(][^）)]*(?:' + meta_keywords + r')[^）)]*[）)]', '', content)

        # 去除可能的变体：用中文方括号、书名号等
        content = re.sub(r'[【\[][^】\]]*(?:' + meta_keywords + r')[^】\]]*[】\]]', '', content)

        # 去除单独成行的标记（如 "---\n（章尾钩子）\n"）
        content = re.sub(r'\n[（(【\[][^\n）)】\]]*[）)】\]]\s*\n', '\n', content)

        # 去除可能残留的空行（连续3个以上换行变成2个）
        content = re.sub(r'\n{3,}', '\n\n', content)

        return content.strip()

    @staticmethod
    def _add_frontmatter(content: str, ch_num: int, title: str, platform: str) -> str:
        """添加 Markdown YAML frontmatter。"""
        from datetime import datetime

        date_str = datetime.now().strftime("%Y-%m-%d")
        fm = f"""---
chapter: {ch_num}
title: "{title}"
platform: {platform}
date: {date_str}
words: {len(content)}
---

"""
        return fm + content

    # ------------------------------------------------------------------
    # 自动质量修订循环
    # ------------------------------------------------------------------

    async def write_chapter_auto_revise(
        self,
        chapter_node: dict[str, Any],
        *,
        context: dict[str, Any] | None = None,
        platform: str = "fanqie",
        genre: str = "",
        min_ai_score: float = 0.70,
        max_rounds: int = 2,
    ) -> dict[str, Any]:
        """生成章节 + 自动质量修订循环。

        流程:
            1. 生成初稿
            2. AI检测 → 如果AI评分 < min_ai_score → 修订
            3. 修订后重新检测 → 如果仍不达标 → 再修订一轮
            4. 返回最终版本 + 质量报告

        Args:
            chapter_node: 大纲节点
            context: 上下文
            platform: 目标平台
            genre: 类型标识
            min_ai_score: 最低可接受AI评分 (0-1)
            max_rounds: 最大修订轮次

        Returns:
            {"chapter": Chapter, "quality": {"initial_score": float, "final_score": float, "rounds": int}}
        """
        from plugins.anti_ai_detection.pattern_detector import AIPatternDetector

        detector = AIPatternDetector()

        # 专家模式：使用配置中的参数
        if self._is_fanqie_expert_mode(platform):
            min_ai_score = self._kernel.get_config("chapter.fanqie_expert.min_ai_score", 0.75)
            max_rounds = self._kernel.get_config("chapter.fanqie_expert.max_revision_rounds", 3)
        elif self._is_qidian_expert_mode(platform):
            min_ai_score = self._kernel.get_config("chapter.qidian_expert.min_ai_score", 0.75)
            max_rounds = self._kernel.get_config("chapter.qidian_expert.max_revision_rounds", 3)

        rounds = 0
        patterns_history: list[dict] = []

        # ---- Round 0: 初稿 ----
        chapter = await self.write_chapter(chapter_node, context=context, platform=platform, genre=genre)
        content = chapter.content if hasattr(chapter, "content") else str(chapter)

        matches = detector.detect(content)
        initial_score = detector.calculate_ai_score(matches, text=content)
        current_score = initial_score
        current_content = content

        patterns_history.append(
            {
                "round": 0,
                "score": round(initial_score, 3),
                "patterns": [m.category for m in matches],
                "count": len(matches),
            }
        )

        logger.info(
            "质量检测: 初稿",
            chapter=chapter_node.get("chapter_number", 0),
            ai_score=round(initial_score, 3),
            patterns=len(matches),
        )

        # ---- Revision Loop ----
        while current_score < min_ai_score and rounds < max_rounds:
            rounds += 1

            # 收集当前问题
            matches = detector.detect(current_content)
            issues = []
            for m in matches:
                if m.severity == "high":
                    issues.append(f"[严重] {m.category}: 删除或替换 {', '.join(m.matched_items[:5])}")
                elif m.severity == "medium":
                    issues.append(f"[中等] {m.category}: 减少 {', '.join(m.matched_items[:3])}")

            sentence_check = detector.detect_uniform_sentences(current_content)
            if sentence_check["is_uniform"]:
                issues.append(f"[严重] 句长过于均匀 (SD={sentence_check['sd']})——刻意变化句长")

            ending_check = detector.detect_generic_ending(current_content)
            if ending_check["has_generic_ending"]:
                issues.append(f"[严重] 泛化结尾: {ending_check['found']}——换成具体悬念")

            # 番茄专家模式：专项检测"不是X是Y"
            if self._is_fanqie_expert_mode(platform):
                not_xy_check = detector.detect_not_x_but_y(current_content)
                if not_xy_check["is_excessive"]:
                    issues.append(f"[严重] {not_xy_check['suggestion']}")

            if not issues:
                break

            revision_instructions = "\n".join(f"- {iss}" for iss in issues)

            logger.info(
                f"自动修订 第{rounds}轮",
                chapter=chapter_node.get("chapter_number", 0),
                issues=len(issues),
            )

            # 调用修订
            revised_chapter = await self.write_chapter(
                chapter_node,
                context=context,
                platform=platform,
                genre=genre,
                revision_instructions=revision_instructions,
                previous_content=current_content,
            )
            current_content = revised_chapter.content if hasattr(revised_chapter, "content") else str(revised_chapter)

            # 重新检测
            matches = detector.detect(current_content)
            current_score = detector.calculate_ai_score(matches, text=current_content)

            patterns_history.append(
                {
                    "round": rounds,
                    "score": round(current_score, 3),
                    "patterns": [m.category for m in matches],
                    "count": len(matches),
                    "issues_addressed": len(issues),
                }
            )

            # 更新 chapter 对象
            chapter = revised_chapter
            if hasattr(chapter, "metadata"):
                chapter.metadata.revision_count = rounds

        logger.info(
            "质量修订完成",
            chapter=chapter_node.get("chapter_number", 0),
            initial=round(initial_score, 3),
            final=round(current_score, 3),
            rounds=rounds,
            improvement=round(current_score - initial_score, 3),
        )

        return {
            "chapter": chapter,
            "quality": {
                "initial_score": round(initial_score, 3),
                "final_score": round(current_score, 3),
                "improvement": round(current_score - initial_score, 3),
                "rounds": rounds,
                "history": patterns_history,
            },
        }


# ---------------------------------------------------------------------------
# 工厂函数
# ---------------------------------------------------------------------------


def create_manifest() -> PluginManifest:
    return PluginManifest(
        name="chapter-writer",
        version="0.1.0",
        description="正文撰写引擎 — 逐章生成小说正文",
        dependencies=["outline-planner"],
        hooks=["on_load", "on_unload"],
    )


def create_plugin() -> ChapterWriterPlugin:
    return ChapterWriterPlugin()
