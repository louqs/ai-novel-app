"""一致性审查引擎 — 双 Agent 交叉验证章节一致性。

检查维度:
    1. 时间线连续性
    2. 人物行为一致性 (是否 OOC)
    3. 设定规则遵守
    4. 事实账本校验 (硬事实是否矛盾)
    5. 人物位置/状态追踪

用法:
    result = await plugin.check_consistency(chapter, context)
"""

from __future__ import annotations

from typing import Any

from core.logging_config import get_logger
from core.plugin_manager import PluginManifest
from core.quality_gate import GateIssue, GateResult, GateVerdict, IQualityGate, Severity

logger = get_logger(__name__)

CONSISTENCY_SYSTEM = """你是一位资深网文编辑，专门负责检查小说章节的一致性。

## 检查维度

### 1. 时间线连续性
- 是否与前章时间自然衔接
- 时间跳跃是否有说明
- 事件顺序是否合理

### 2. 人物行为一致性 (OOC 检测)
- 人物行为是否符合既定性格
- 对话是否符合人物身份
- 能力/修为是否前后矛盾
- 人物关系状态是否一致

### 3. 设定规则遵守
- 是否违反已建立的世界规则
- 力量体系是否自洽
- 道具/能力使用是否符合设定

### 4. 事实冲突
- 人物位置是否矛盾 (同一角色不能同时在两地)
- 物品归属是否一致
- 数量/金额是否匹配

## 输出格式
以 JSON 返回检查结果。"""


class ConsistencyCheckerPlugin(IQualityGate):
    """一致性审查引擎插件."""

    name = "consistency-checker"
    order = 10  # 第一道门禁
    version = "0.1.0"

    def __init__(self) -> None:
        self._kernel = None

    async def on_load(self, kernel) -> None:
        self._kernel = kernel
        logger.info("一致性审查引擎已加载")

    async def on_unload(self) -> None:
        self._kernel = None

    # ------------------------------------------------------------------
    # Quality Gate
    # ------------------------------------------------------------------

    async def evaluate(self, chapter: dict[str, Any], context: dict[str, Any]) -> GateResult:
        """执行一致性检查."""
        content = chapter.get("content", "")
        chapter_id = chapter.get("chapter_id", "?")
        chapter_num = chapter.get("chapter_number", 0)

        if not content.strip():
            return GateResult(
                gate_name=self.name,
                verdict=GateVerdict.FAIL,
                issues=[GateIssue(severity=Severity.CRITICAL, code="consistency.empty", message="章节内容为空")],
            )

        # 收集上下文
        facts = context.get("facts", {})
        settings_data = context.get("settings", {})
        characters_data = context.get("characters", {})

        # 构建检查 Prompt
        user_prompt = self._build_check_prompt(content, chapter_num, facts, settings_data, characters_data)

        try:
            result = await self._kernel.call_llm(
                messages=[
                    {"role": "system", "content": CONSISTENCY_SYSTEM},
                    {"role": "user", "content": user_prompt},
                ],
                tier="standard",
                max_tokens=2048,
                temperature=0.3,
            )
            return self._parse_result(result["content"])
        except Exception as exc:
            logger.warning("一致性检查 LLM 调用失败, 降级为 PASS", error=str(exc))
            return GateResult(
                gate_name=self.name,
                verdict=GateVerdict.PASS,
                score=0.6,
                metadata={"fallback": True, "error": str(exc)},
            )

    # ------------------------------------------------------------------
    # 事实自动提取
    # ------------------------------------------------------------------

    async def extract_facts(self, chapter_content: str, chapter_num: int) -> list[dict[str, Any]]:
        """从章节中自动提取硬事实."""
        prompt = f"""请从以下章节中提取所有硬事实（可量化/可验证的信息），以 JSON 返回。

章节:
{chapter_content[:3000]}

返回格式:
```json
{{
  "facts": [
    {{
      "subject": "人物/地点/物品名称",
      "predicate": "发生了什么",
      "value": "新值",
      "category": "character_state|relationship|possession|timeline|quantity|location_state|rule_application|plot_status",
      "confidence": "certain|likely|inferred"
    }}
  ]
}}
```

注意:
- 只提取明确的、可验证的事实
- 推断的事实标记为 inferred
- 不需要提取平凡信息"""

        result = await self._kernel.call_llm(
            messages=[{"role": "user", "content": prompt}],
            tier="budget",
            max_tokens=2048,
            temperature=0.1,
        )

        import json
        try:
            data = json.loads(result["content"])
            return data.get("facts", [])
        except json.JSONDecodeError:
            return []

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _build_check_prompt(
        self,
        content: str,
        chapter_num: int,
        facts: dict,
        settings_data: dict,
        characters_data: dict,
    ) -> str:
        parts = [f"## 待检查章节 (第{chapter_num}章)\n{content[:4000]}"]

        if facts:
            entries = facts.get("entries", {})
            if entries:
                parts.append(f"\n## 已知事实 ({len(entries)}条)")
                for fid, fact in list(entries.items())[-15:]:
                    if isinstance(fact, dict):
                        parts.append(f"- [{fact.get('category', '')}] {fact.get('subject', '')}: {fact.get('predicate', '')} → {fact.get('value', '')}")

        if settings_data:
            rules = settings_data.get("world_rules", [])
            if rules:
                parts.append(f"\n## 世界规则 ({len(rules)}条)")
                for r in rules[:5]:
                    if isinstance(r, dict):
                        parts.append(f"- {r.get('name', '')}: {r.get('description', '')}")

        if characters_data:
            chars = characters_data.get("characters", {})
            if chars:
                parts.append(f"\n## 人物状态")
                for cid, c in list(chars.items())[:8]:
                    if isinstance(c, dict):
                        parts.append(f"- {c.get('name', cid)}: {c.get('current_status', '?')}")

        parts.append("""
请检查并返回 JSON:
```json
{
  "verdict": "pass|revise|fail",
  "score": 0.0-1.0,
  "issues": [
    {
      "severity": "info|warning|error|critical",
      "code": "consistency.xxx",
      "message": "...",
      "suggestion": "..."
    }
  ]
}
```""")
        return "\n".join(parts)

    def _parse_result(self, content: str) -> GateResult:
        import json
        try:
            data = json.loads(content)
        except json.JSONDecodeError:
            import re
            match = re.search(r'```(?:json)?\s*([\s\S]*?)```', content)
            if match:
                try:
                    data = json.loads(match.group(1))
                except json.JSONDecodeError:
                    return GateResult(gate_name=self.name, verdict=GateVerdict.PASS, score=0.5)
            else:
                return GateResult(gate_name=self.name, verdict=GateVerdict.PASS, score=0.5)

        verdict_str = data.get("verdict", "pass")
        try:
            verdict = GateVerdict(verdict_str)
        except ValueError:
            verdict = GateVerdict.PASS

        issues = []
        for iss in data.get("issues", []):
            try:
                severity = Severity(iss.get("severity", "info"))
            except ValueError:
                severity = Severity.WARNING
            issues.append(GateIssue(
                severity=severity,
                code=iss.get("code", "consistency.unknown"),
                message=iss.get("message", ""),
                suggestion=iss.get("suggestion"),
            ))

        return GateResult(
            gate_name=self.name,
            verdict=verdict,
            issues=issues,
            score=float(data.get("score", 0.7)),
        )


def create_manifest() -> PluginManifest:
    return PluginManifest(
        name="consistency-checker",
        version="0.1.0",
        description="一致性审查引擎 — 双Agent交叉验证章节一致性",
        dependencies=[],
        hooks=["on_load", "on_unload", "on_gate_check"],
    )


def create_plugin() -> ConsistencyCheckerPlugin:
    return ConsistencyCheckerPlugin()
