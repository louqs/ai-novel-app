"""多模式写作技能测试.

测试内容：
1. QIDIAN_EXPERT_SYSTEM Prompt 包含核心规则
2. ZHIDOU_EXPERT_SYSTEM Prompt 包含核心规则
3. InformationIncrement 信息增量追踪
4. TenDimensionScore 十维评分模型
5. ZhidouDesign 智斗设计模型
6. 类型层优先于平台层
7. 起点模式 prompt 注入
8. 智斗模式 prompt 注入
9. 质量评估器
"""

from __future__ import annotations

from plugins.chapter_writer.plugin import (
    QIDIAN_EXPERT_SYSTEM,
    ZHIDOU_EXPERT_SYSTEM,
    ChapterWriterPlugin,
    InformationIncrement,
    TenDimensionScore,
    ZhidouDesign,
)
from plugins.quality_evaluator.plugin import QualityEvaluatorPlugin

# =============================================================================
# 起点 Prompt 测试
# =============================================================================


class TestQidianExpertPrompt:
    """测试起点精品模式 Prompt 内容."""

    def test_prompt_contains_info_increment(self):
        """Prompt 包含信息增量驱动."""
        assert "信息增量" in QIDIAN_EXPERT_SYSTEM
        assert "每章读完后，读者知道了至少一样他之前不知道的东西" in QIDIAN_EXPERT_SYSTEM

    def test_prompt_contains_three_stage_rhythm(self):
        """Prompt 包含三阶节奏."""
        assert "免费期" in QIDIAN_EXPERT_SYSTEM
        assert "上架期" in QIDIAN_EXPERT_SYSTEM
        assert "稳定期" in QIDIAN_EXPERT_SYSTEM

    def test_prompt_contains_group_portrait(self):
        """Prompt 包含群像写作铁律."""
        assert "群像写作铁律" in QIDIAN_EXPERT_SYSTEM
        assert "独立高光" in QIDIAN_EXPERT_SYSTEM
        assert "低谷/弱点" in QIDIAN_EXPERT_SYSTEM
        assert "独立故事碎片" in QIDIAN_EXPERT_SYSTEM

    def test_prompt_contains_ten_rules(self):
        """Prompt 包含十条铁律."""
        assert "禁止AI句式" in QIDIAN_EXPERT_SYSTEM
        assert "感叹号和粗口初稿到位" in QIDIAN_EXPERT_SYSTEM
        assert "正文中绝对禁止元话语" in QIDIAN_EXPERT_SYSTEM
        assert "每10-15章必须有人死" in QIDIAN_EXPERT_SYSTEM
        assert "主角必须亲手战斗" in QIDIAN_EXPERT_SYSTEM

    def test_prompt_contains_paid_chapter_strategy(self):
        """Prompt 包含付费章节策略."""
        assert "上架时机" in QIDIAN_EXPERT_SYSTEM
        assert "断章技巧" in QIDIAN_EXPERT_SYSTEM
        assert "订阅维护" in QIDIAN_EXPERT_SYSTEM

    def test_prompt_contains_quality_checklist(self):
        """Prompt 包含质量自检清单."""
        assert "历史/设定硬伤扫描" in QIDIAN_EXPERT_SYSTEM
        assert "逻辑闭环" in QIDIAN_EXPERT_SYSTEM
        assert "人物驱动" in QIDIAN_EXPERT_SYSTEM
        assert "爽点高级度" in QIDIAN_EXPERT_SYSTEM


# =============================================================================
# 智斗 Prompt 测试
# =============================================================================


class TestZhidouExpertPrompt:
    """测试智斗小说写作模式 Prompt 内容."""

    def test_prompt_contains_real_person_feeling(self):
        """Prompt 包含真人感塑造."""
        assert "真人感塑造" in ZHIDOU_EXPERT_SYSTEM
        assert "生理反应 > 心理描述" in ZHIDOU_EXPERT_SYSTEM
        assert "多感官场景构建" in ZHIDOU_EXPERT_SYSTEM

    def test_prompt_contains_five_layer(self):
        """Prompt 包含智斗五层体系."""
        assert "信息层" in ZHIDOU_EXPERT_SYSTEM
        assert "资源层" in ZHIDOU_EXPERT_SYSTEM
        assert "规则层" in ZHIDOU_EXPERT_SYSTEM
        assert "人心层" in ZHIDOU_EXPERT_SYSTEM
        assert "时间层" in ZHIDOU_EXPERT_SYSTEM

    def test_prompt_contains_zhidou_structure(self):
        """Prompt 包含智斗结构模板."""
        assert "憋屈" in ZHIDOU_EXPERT_SYSTEM
        assert "收集" in ZHIDOU_EXPERT_SYSTEM
        assert "布局" in ZHIDOU_EXPERT_SYSTEM
        assert "掀桌" in ZHIDOU_EXPERT_SYSTEM
        assert "翻盘" in ZHIDOU_EXPERT_SYSTEM

    def test_prompt_contains_info_gap(self):
        """Prompt 包含信息差管理."""
        assert "读者领先型" in ZHIDOU_EXPERT_SYSTEM
        assert "主角领先型" in ZHIDOU_EXPERT_SYSTEM
        assert "双重盲区型" in ZHIDOU_EXPERT_SYSTEM

    def test_prompt_contains_cognitive_payoff(self):
        """Prompt 包含认知爽感."""
        assert "恍然大悟" in ZHIDOU_EXPERT_SYSTEM
        assert "聪明反被聪明误" in ZHIDOU_EXPERT_SYSTEM
        assert "算无遗策" in ZHIDOU_EXPERT_SYSTEM
        assert "绝境翻盘" in ZHIDOU_EXPERT_SYSTEM

    def test_prompt_contains_ai_countermeasures(self):
        """Prompt 包含AI反制铁律."""
        assert "句式工整均匀" in ZHIDOU_EXPERT_SYSTEM
        assert "对话=设定解说器" in ZHIDOU_EXPERT_SYSTEM
        assert "过度解释" in ZHIDOU_EXPERT_SYSTEM
        assert "不是…是…" in ZHIDOU_EXPERT_SYSTEM


# =============================================================================
# 数据模型测试
# =============================================================================


class TestInformationIncrement:
    """测试信息增量追踪模型."""

    def test_increment_creation(self):
        """创建信息增量."""
        increment = InformationIncrement(
            chapter_num=1,
            world_building="新设定：力量体系",
            character_reveal="主角的隐藏身份",
            foreshadow_plant="神秘的符文",
        )
        assert increment.chapter_num == 1
        assert increment.world_building == "新设定：力量体系"
        assert increment.character_reveal == "主角的隐藏身份"

    def test_increment_defaults(self):
        """信息增量默认值正确."""
        increment = InformationIncrement()
        assert increment.chapter_num == 0
        assert increment.world_building == ""
        assert increment.character_reveal == ""


class TestTenDimensionScore:
    """测试十维评分模型."""

    def test_score_creation(self):
        """创建十维评分."""
        score = TenDimensionScore(
            hook_strength=8.0,
            character_depth=7.5,
            pacing=7.0,
            emotional_resonance=8.5,
            world_coherence=9.0,
            style_uniqueness=6.5,
            payoff_density=7.0,
            suspense=8.0,
            chapter_hook=7.5,
            theme_depth=7.0,
        )
        assert score.hook_strength == 8.0
        assert score.character_depth == 7.5

    def test_score_average(self):
        """计算平均分."""
        score = TenDimensionScore(
            hook_strength=8.0,
            character_depth=8.0,
            pacing=8.0,
            emotional_resonance=8.0,
            world_coherence=8.0,
            style_uniqueness=8.0,
            payoff_density=8.0,
            suspense=8.0,
            chapter_hook=8.0,
            theme_depth=8.0,
        )
        assert score.average == 8.0

    def test_score_defaults(self):
        """评分默认值正确."""
        score = TenDimensionScore()
        assert score.average == 0.0


class TestZhidouDesign:
    """测试智斗设计模型."""

    def test_design_creation(self):
        """创建智斗设计."""
        design = ZhidouDesign(
            layers=["信息层", "资源层", "规则层"],
            protagonist_blind_spots=["不知道对手的盟友"],
            antagonist_blind_spots=["不知道主角的真实身份"],
            info_gap_type="protagonist_ahead",
            stakes="失去所有资源",
            phase="布局",
        )
        assert len(design.layers) == 3
        assert design.info_gap_type == "protagonist_ahead"
        assert design.phase == "布局"

    def test_design_defaults(self):
        """智斗设计默认值正确."""
        design = ZhidouDesign()
        assert design.layers == []
        assert design.info_gap_type == ""


# =============================================================================
# 集成测试
# =============================================================================


class TestModePriority:
    """测试模式优先级."""

    def test_zhidou_priority_over_platform(self):
        """智斗类型优先于平台层."""
        plugin = ChapterWriterPlugin()

        class MockKernel:
            def get_config(self, key, default=None):
                if key == "chapter.zhidou_expert.enabled":
                    return True
                if key == "chapter.fanqie_expert.enabled":
                    return True
                return default

        plugin._kernel = MockKernel()
        prompt = plugin._build_system_prompt("fanqie", "zhidou")
        assert prompt == ZHIDOU_EXPERT_SYSTEM

    def test_qidian_mode(self):
        """起点专家模式."""
        plugin = ChapterWriterPlugin()

        class MockKernel:
            def get_config(self, key, default=None):
                if key == "chapter.qidian_expert.enabled":
                    return True
                return default

        plugin._kernel = MockKernel()
        prompt = plugin._build_system_prompt("qidian")
        assert prompt == QIDIAN_EXPERT_SYSTEM

    def test_fanqie_mode(self):
        """番茄专家模式."""
        plugin = ChapterWriterPlugin()

        class MockKernel:
            def get_config(self, key, default=None):
                if key == "chapter.fanqie_expert.enabled":
                    return True
                return default

        plugin._kernel = MockKernel()
        prompt = plugin._build_system_prompt("fanqie")
        assert "番茄爆款写手" in prompt

    def test_default_mode(self):
        """默认模式."""
        plugin = ChapterWriterPlugin()

        class MockKernel:
            def get_config(self, key, default=None):
                return default

        plugin._kernel = MockKernel()
        prompt = plugin._build_system_prompt("fanqie")
        assert "资深网文作家" in prompt


class TestUserPromptInjection:
    """测试用户 Prompt 注入."""

    def test_qidian_info_increment_injection(self):
        """起点模式注入信息增量."""
        plugin = ChapterWriterPlugin()

        class MockKernel:
            def get_config(self, key, default=None):
                if key == "chapter.qidian_expert.enabled":
                    return True
                return default

        plugin._kernel = MockKernel()

        increment = InformationIncrement(
            chapter_num=1,
            world_building="新设定",
            character_reveal="新人物",
        )

        prompt = plugin._build_user_prompt(
            {"chapter_number": 1, "title": "第一章"},
            {},
            "qidian",
            "",
            "",
            information_increment=increment,
        )
        assert "信息增量检查" in prompt
        assert "新设定" in prompt

    def test_zhidou_design_injection(self):
        """智斗模式注入智斗设计."""
        plugin = ChapterWriterPlugin()

        class MockKernel:
            def get_config(self, key, default=None):
                if key == "chapter.zhidou_expert.enabled":
                    return True
                return default

        plugin._kernel = MockKernel()

        design = ZhidouDesign(
            layers=["信息层", "资源层", "规则层"],
            info_gap_type="protagonist_ahead",
            phase="布局",
        )

        prompt = plugin._build_user_prompt(
            {"chapter_number": 1, "title": "第一章"},
            {},
            "fanqie",
            "",
            "",
            genre="zhidou",
            zhidou_design=design,
        )
        assert "智斗设计" in prompt
        assert "信息层" in prompt
        assert "主角领先型" in prompt


class TestQualityEvaluator:
    """测试质量评估器."""

    def test_evaluator_creation(self):
        """创建质量评估器."""
        evaluator = QualityEvaluatorPlugin()
        assert evaluator.name == "quality-evaluator"
        assert evaluator.order == 50

    def test_evaluator_pass(self):
        """优质内容通过评估."""
        import asyncio

        evaluator = QualityEvaluatorPlugin()

        class MockKernel:
            def get_config(self, key, default=None):
                if key == "chapter.ten_dimension_eval.min_score":
                    return 6.0
                return default

        evaluator._kernel = MockKernel()

        content = """
        敌人一拳砸来，他侧身躲过，反手就是一刀。
        血溅了出来。敌人的手臂被划出一道深深的口子。
        "你完了。"他冷笑着说。
        但他没想到，敌人还有后手——一把暗器从袖中飞出。
        """

        chapter = {"chapter_id": "ch_v01_0001", "content": content, "metadata": {"platform": "fanqie"}}
        context = {}

        result = asyncio.run(evaluator.evaluate(chapter, context))
        assert result.score >= 6.0

    def test_evaluator_detect_meta_language(self):
        """检测元话语."""
        import asyncio

        evaluator = QualityEvaluatorPlugin()

        class MockKernel:
            def get_config(self, key, default=None):
                return default

        evaluator._kernel = MockKernel()

        content = "卷一中提到的那个设定，在这一章得到了验证。"

        chapter = {"chapter_id": "ch_v01_0001", "content": content, "metadata": {"platform": "fanqie"}}
        context = {}

        result = asyncio.run(evaluator.evaluate(chapter, context))
        assert any("元话语" in issue.message for issue in result.issues)

    def test_evaluator_detect_ai_words(self):
        """检测AI口水词."""
        import asyncio

        evaluator = QualityEvaluatorPlugin()

        class MockKernel:
            def get_config(self, key, default=None):
                return default

        evaluator._kernel = MockKernel()

        content = "他缓缓地走过来，不由得叹了口气。眼底闪过一丝光芒，心中升起莫名的情绪。"

        chapter = {"chapter_id": "ch_v01_0001", "content": content, "metadata": {"platform": "fanqie"}}
        context = {}

        result = asyncio.run(evaluator.evaluate(chapter, context))
        assert any("AI口水词" in issue.message for issue in result.issues)
