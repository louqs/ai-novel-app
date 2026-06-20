"""番茄专家模式测试.

测试内容：
1. FANQIE_EXPERT_SYSTEM Prompt 包含核心规则
2. PreWriteCheckResult 写前检查
3. RhythmTracker 节奏追踪
4. CharacterVoiceCard 角色语音卡
5. "不是X是Y"检测
6. 番茄专家模式集成
"""

from __future__ import annotations

from models.character import CharacterSet, CharacterVoiceCard
from plugins.anti_ai_detection.pattern_detector import AIPatternDetector
from plugins.chapter_writer.plugin import (
    FANQIE_EXPERT_SYSTEM,
    RhythmTracker,
    ChapterWriterPlugin,
)


# =============================================================================
# Prompt 测试
# =============================================================================


class TestFanqieExpertPrompt:
    """测试番茄专家模式 Prompt 内容."""

    def test_prompt_contains_rhythm_formula(self):
        """Prompt 包含节奏公式."""
        assert "3章一小爽" in FANQIE_EXPERT_SYSTEM
        assert "5章一大爽" in FANQIE_EXPERT_SYSTEM
        assert "10章一高潮" in FANQIE_EXPERT_SYSTEM
        assert "余韵≤2章" in FANQIE_EXPERT_SYSTEM

    def test_prompt_contains_seven_pleasures(self):
        """Prompt 包含七种爽点."""
        pleasures = ["打脸", "碾压", "装逼", "截胡", "复仇", "升级", "反转"]
        for p in pleasures:
            assert p in FANQIE_EXPERT_SYSTEM, f"缺少爽点: {p}"

    def test_prompt_contains_forbidden_pleasures(self):
        """Prompt 包含禁止的文青爽."""
        forbidden = ["理解", "和解", "发现", "放下"]
        for f in forbidden:
            assert f in FANQIE_EXPERT_SYSTEM, f"缺少禁止项: {f}"

    def test_prompt_contains_hook_types(self):
        """Prompt 包含四种结尾钩子."""
        hooks = ["危机突降", "悬念反转", "挑衅叫板", "死亡倒计时"]
        for h in hooks:
            assert h in FANQIE_EXPERT_SYSTEM, f"缺少钩子类型: {h}"

    def test_prompt_contains_not_x_but_y_ban(self):
        """Prompt 包含"不是X是Y"禁令."""
        assert "不是X，是Y" in FANQIE_EXPERT_SYSTEM
        assert "严禁使用" in FANQIE_EXPERT_SYSTEM

    def test_prompt_contains_villain_requirements(self):
        """Prompt 包含反派压迫感要求."""
        assert "可见伤害" in FANQIE_EXPERT_SYSTEM
        assert "生死局" in FANQIE_EXPERT_SYSTEM

    def test_prompt_contains_voice_card_rule(self):
        """Prompt 包含角色语音卡规则."""
        assert "角色语音卡" in FANQIE_EXPERT_SYSTEM
        assert "每个角色说话方式必须不同" in FANQIE_EXPERT_SYSTEM

    def test_prompt_contains_opening_formula(self):
        """Prompt 包含开章炸裂公式."""
        assert "开章公式" in FANQIE_EXPERT_SYSTEM
        assert "敌人用X攻击了Y" in FANQIE_EXPERT_SYSTEM

    def test_prompt_contains_meta_ban(self):
        """Prompt 包含元话语禁令."""
        assert "元话语禁令" in FANQIE_EXPERT_SYSTEM
        assert "卷一" in FANQIE_EXPERT_SYSTEM


# =============================================================================
# 写前检查测试
# =============================================================================


class TestPreWriteCheck:
    """测试写前阻断五问检查."""

    def test_check_pass_with_good_chapter(self):
        """大纲包含所有必要信息时通过."""
        plugin = ChapterWriterPlugin()
        chapter_node = {
            "chapter_number": 1,
            "title": "第一章",
            "summary": "主角击败敌人，获得胜利",
            "key_events": ["敌人攻击主角", "主角反击击败敌人"],
            "is_hook_point": True,
        }
        context = {}
        result = plugin._pre_write_check(chapter_node, context)
        assert result.passed is True
        assert result.answers["villain_damage"] == "已检测"
        assert result.answers["physical_conflict"] == "已检测"

    def test_check_fail_without_conflict(self):
        """大纲缺少冲突时未通过."""
        plugin = ChapterWriterPlugin()
        chapter_node = {
            "chapter_number": 1,
            "title": "第一章",
            "summary": "主角发现了一个秘密",
            "key_events": ["主角发现了真相"],
            "is_hook_point": False,
        }
        context = {}
        result = plugin._pre_write_check(chapter_node, context)
        assert result.passed is False
        assert len(result.warnings) >= 3

    def test_check_warns_abstract_pleasure(self):
        """大纲爽点偏文艺时警告."""
        plugin = ChapterWriterPlugin()
        chapter_node = {
            "chapter_number": 1,
            "title": "第一章",
            "summary": "主角发现了真相，理解了世界的本质",
            "key_events": ["战斗场景"],
            "is_hook_point": True,
        }
        context = {}
        result = plugin._pre_write_check(chapter_node, context)
        assert any("文青爽" in w or "发现" in w for w in result.warnings)

    def test_check_warns_no_hook(self):
        """大纲未标记钩子时警告."""
        plugin = ChapterWriterPlugin()
        chapter_node = {
            "chapter_number": 1,
            "title": "第一章",
            "summary": "战斗",
            "key_events": ["击败敌人"],
            "is_hook_point": False,
        }
        context = {}
        result = plugin._pre_write_check(chapter_node, context)
        assert any("钩子" in w for w in result.warnings)


# =============================================================================
# 节奏追踪测试
# =============================================================================


class TestRhythmTracker:
    """测试节奏追踪器."""

    def test_tracker_initial_state(self):
        """追踪器初始状态正确."""
        tracker = RhythmTracker()
        assert tracker.recent_pleasure_types == []
        assert tracker.recent_hook_types == []
        assert tracker.chapters_since_last_big_pleasure == 0
        assert tracker.chapters_since_last_climax == 0
        assert tracker.chapters_since_last_conflict == 0

    def test_tracker_stores_pleasure_types(self):
        """追踪器正确存储爽点类型."""
        tracker = RhythmTracker()
        tracker.recent_pleasure_types = ["打脸", "碾压", "反转"]
        assert len(tracker.recent_pleasure_types) == 3
        assert "打脸" in tracker.recent_pleasure_types


# =============================================================================
# 角色语音卡测试
# =============================================================================


class TestCharacterVoiceCard:
    """测试角色语音卡模型."""

    def test_voice_card_creation(self):
        """创建角色语音卡."""
        card = CharacterVoiceCard(
            character_id="char_001",
            character_name="张三",
            catchphrases=["妈的", "操"],
            swearing_style="重度",
            sentence_pattern="短句型",
            verbal_tics=["说实话", "你懂的"],
            notes="说话时总是带着笑",
        )
        assert card.character_id == "char_001"
        assert card.character_name == "张三"
        assert len(card.catchphrases) == 2
        assert card.swearing_style == "重度"

    def test_voice_card_defaults(self):
        """语音卡默认值正确."""
        card = CharacterVoiceCard(
            character_id="char_002",
            character_name="李四",
        )
        assert card.catchphrases == []
        assert card.swearing_style == "无"
        assert card.sentence_pattern == "混合型"
        assert card.verbal_tics == []

    def test_voice_card_in_character_set(self):
        """CharacterSet 包含语音卡字段."""
        char_set = CharacterSet(project_id="proj_test")
        assert hasattr(char_set, "voice_cards")
        assert char_set.voice_cards == []

    def test_voice_card_serialization(self):
        """语音卡序列化正确."""
        card = CharacterVoiceCard(
            character_id="char_001",
            character_name="张三",
            catchphrases=["妈的"],
            swearing_style="重度",
        )
        data = card.model_dump()
        assert data["character_id"] == "char_001"
        assert data["catchphrases"] == ["妈的"]
        assert data["swearing_style"] == "重度"


# =============================================================================
# "不是X是Y"检测测试
# =============================================================================


class TestNotXButYDetection:
    """测试"不是X是Y"句式检测."""

    def test_detect_basic_pattern(self):
        """检测基本的"不是X，是Y"模式."""
        detector = AIPatternDetector()
        text = "这不是恐惧，是愤怒。他握紧了拳头。"
        result = detector.detect_not_x_but_y(text)
        assert result["count"] >= 1
        assert result["is_excessive"] is False

    def test_detect_multiple_patterns(self):
        """检测多处"不是X是Y"模式."""
        detector = AIPatternDetector()
        # 使用更明确的格式，确保正则能匹配
        text = "这不是退缩，是策略。那不是敌人，是盟友。这不是结束，是开始。他又想了想，觉得不对。"
        result = detector.detect_not_x_but_y(text)
        assert result["count"] >= 2
        assert result["is_excessive"] is True  # 超过2次

    def test_detect_with_er_shi(self):
        """检测"不是X，而是Y"模式."""
        detector = AIPatternDetector()
        text = "这不是软弱，而是智慧。他转身离开。"
        result = detector.detect_not_x_but_y(text)
        assert result["count"] >= 1

    def test_no_false_positive_on_dialogue(self):
        """对话中的简单"不是"不应匹配"不是X，是Y"模式."""
        detector = AIPatternDetector()
        # 简单的"不是"没有后面的"是Y"模式
        text = '"不是这样的，"他摇摇头，"你听我说。"'
        result = detector.detect_not_x_but_y(text)
        # 没有"不是X，是Y"完整模式，不应匹配
        assert result["count"] == 0

    def test_empty_text(self):
        """空文本返回零计数."""
        detector = AIPatternDetector()
        result = detector.detect_not_x_but_y("")
        assert result["count"] == 0
        assert result["is_excessive"] is False

    def test_suggestion_provided_when_excessive(self):
        """超过阈值时提供建议."""
        detector = AIPatternDetector()
        text = "这不是A，是B。这不是C，是D。这不是E，是F。"
        result = detector.detect_not_x_but_y(text)
        if result["is_excessive"]:
            assert len(result["suggestion"]) > 0
            assert "不是X，是Y" in result["suggestion"]


# =============================================================================
# 集成测试
# =============================================================================


class TestFanqieExpertIntegration:
    """测试番茄专家模式集成."""

    def test_is_fanqie_expert_mode_returns_false_for_other_platforms(self):
        """非番茄平台返回 False."""
        plugin = ChapterWriterPlugin()
        # 不加载 kernel，直接测试方法逻辑
        assert plugin._is_fanqie_expert_mode("qidian") is False
        assert plugin._is_fanqie_expert_mode("jinjiang") is False

    def test_fanqie_expert_prompt_used_when_enabled(self):
        """启用番茄专家模式时使用专用 Prompt."""
        plugin = ChapterWriterPlugin()

        # 模拟 kernel 返回配置
        class MockKernel:
            def get_config(self, key, default=None):
                if key == "chapter.fanqie_expert.enabled":
                    return True
                return default

        plugin._kernel = MockKernel()
        prompt = plugin._build_system_prompt("fanqie")
        assert prompt == FANQIE_EXPERT_SYSTEM

    def test_standard_prompt_used_when_disabled(self):
        """未启用番茄专家模式时使用标准 Prompt."""
        plugin = ChapterWriterPlugin()

        # 模拟 kernel 返回配置
        class MockKernel:
            def get_config(self, key, default=None):
                if key == "chapter.fanqie_expert.enabled":
                    return False
                return default

        plugin._kernel = MockKernel()
        prompt = plugin._build_system_prompt("fanqie")
        assert prompt != FANQIE_EXPERT_SYSTEM
        assert "资深网文作家" in prompt
