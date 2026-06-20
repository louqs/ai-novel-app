"""Skill Builder Agent 测试."""

from __future__ import annotations

import textwrap

import pytest

from core.skill_builder import SkillBuilderAgent


# =============================================================================
# 代码验证测试
# =============================================================================


def make_builder():
    """创建无 kernel 的 builder (仅测试代码生成和验证)."""
    return SkillBuilderAgent(kernel=None)  # type: ignore


def test_validate_valid_code():
    """验证合法代码通过验证."""
    builder = make_builder()
    code = textwrap.dedent("""\
    from core.plugin_manager import PluginManifest

    class MyPlugin:
        name = "test-plugin"
        version = "0.1.0"

        async def on_load(self, kernel):
            self._kernel = kernel

        async def on_unload(self):
            pass

    def create_manifest():
        return PluginManifest(name="test-plugin", version="0.1.0", description="",
                              dependencies=[], hooks=["on_load", "on_unload"])

    def create_plugin():
        return MyPlugin()
    """)
    valid, errors = builder._validate_code(code)
    assert valid, f"Unexpected errors: {errors}"


def test_validate_missing_manifest():
    """验证缺少 create_manifest 的代码被检测."""
    builder = make_builder()
    code = textwrap.dedent("""\
    class MyPlugin:
        name = "test"
        version = "0.1.0"
    def create_plugin():
        return MyPlugin()
    """)
    valid, errors = builder._validate_code(code)
    assert not valid
    assert any("create_manifest" in e for e in errors)


def test_validate_missing_name():
    """验证缺少 name 属性的类被检测."""
    builder = make_builder()
    code = textwrap.dedent("""\
    from core.plugin_manager import PluginManifest

    class MyPlugin:
        version = "0.1.0"

    def create_manifest():
        return PluginManifest(name="x", version="0.1.0", description="", dependencies=[], hooks=[])

    def create_plugin():
        return MyPlugin()
    """)
    valid, errors = builder._validate_code(code)
    assert not valid
    assert any("name" in e for e in errors)


def test_validate_syntax_error():
    """验证语法错误被检测."""
    builder = make_builder()
    code = "class Foo: invalid python syntax here @@@"
    valid, errors = builder._validate_code(code)
    assert not valid
    assert any("语法错误" in e for e in errors)


def test_extract_code_from_markdown():
    """验证从 Markdown 代码块中提取代码."""
    builder = make_builder()
    llm_output = "以下是你需要的代码:\n```python\nprint('hello')\n```\n希望能帮到你"
    result = builder._extract_code(llm_output)
    assert result == "print('hello')"


def test_extract_code_plain():
    """验证纯代码直接返回."""
    builder = make_builder()
    result = builder._extract_code("print('hello world')")
    assert result == "print('hello world')"


def test_parse_json_from_llm():
    """验证从 LLM 输出中解析 JSON."""
    builder = make_builder()
    content = '分析结果:\n```json\n{"name": "test", "type": "utility"}\n```'
    result = builder._parse_json(content)
    assert result.get("name") == "test"
    assert result.get("type") == "utility"


def test_parse_json_direct():
    """验证直接 JSON 解析."""
    builder = make_builder()
    result = builder._parse_json('{"key": "value"}')
    assert result.get("key") == "value"


# =============================================================================
# 完整流程测试 (模拟)
# =============================================================================


def test_validate_valid_quality_gate():
    """验证质量门禁类型插件代码."""
    builder = make_builder()
    code = textwrap.dedent("""\
    from core.plugin_manager import PluginManifest
    from core.quality_gate import GateIssue, GateResult, GateVerdict, IQualityGate, Severity

    class BattleChecker(IQualityGate):
        name = "battle-checker"
        order = 45
        version = "0.1.0"

        async def on_load(self, kernel):
            self._kernel = kernel

        async def on_unload(self):
            pass

        async def evaluate(self, chapter, context):
            content = chapter.get("content", "")
            score = 0.8
            issues = []
            if "他轻松击败" in content:
                issues.append(GateIssue(severity=Severity.WARNING, code="battle.too_easy",
                                        message="战斗过于轻松", suggestion="增加战斗的波折感"))
            return GateResult(gate_name=self.name, verdict=GateVerdict.PASS if not issues else GateVerdict.REVISE,
                              issues=issues, score=score)

    def create_manifest():
        return PluginManifest(name="battle-checker", version="0.1.0",
                              description="战斗场面审查", dependencies=[],
                              hooks=["on_load", "on_unload", "on_gate_check"])

    def create_plugin():
        return BattleChecker()
    """)
    valid, errors = builder._validate_code(code)
    if errors:
        print("Validation errors:", errors)
    assert valid, f"Unexpected errors: {errors}"
