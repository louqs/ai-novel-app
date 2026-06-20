"""Skill Builder Agent — AI 自我扩展能力。

用户用自然语言描述需求，AI 自动生成完整的插件代码、注册、热加载。

用法:
    builder = SkillBuilderAgent(kernel)
    result = await builder.build("帮我写一个检测战斗描写合理性的插件")
    # → 插件已生成并加载到系统中
"""

from __future__ import annotations

import ast
import importlib
import re
import sys
import uuid
from pathlib import Path
from typing import Any

from core.logging_config import get_logger

logger = get_logger(__name__)

BUILDER_SYSTEM = """你是一位资深 Python 工程师，专精于为 AI 小说生成智能体系统编写插件。

## 插件架构

所有插件必须实现以下接口:

```python
from core.plugin_manager import PluginManifest
from core.quality_gate import GateIssue, GateResult, GateVerdict, IQualityGate, Severity

class MyPlugin(IQualityGate):  # 如果做门禁检查，继承 IQualityGate
    name = "plugin-name"
    version = "0.1.0"
    order = 50  # 门禁执行顺序，越小越先

    async def on_load(self, kernel):
        self._kernel = kernel

    async def on_unload(self):
        pass

    # ⚠️ 门禁插件必须实现这个方法（注意参数名是 evaluate，不是 on_gate_check）:
    async def evaluate(self, chapter: dict, context: dict) -> GateResult:
        content = chapter.get("content", "")
        # 你的检查逻辑...
        issues = []
        # 示例:
        # if some_problem:
        #     issues.append(GateIssue(severity=Severity.WARNING, code="my_check.code", message="问题描述"))
        return GateResult(
            gate_name=self.name,
            verdict=GateVerdict.PASS if not issues else GateVerdict.REVISE,
            issues=issues,
            score=0.8 if not issues else 0.5,
        )

def create_manifest() -> PluginManifest:
    return PluginManifest(
        name="plugin-name",
        version="0.1.0",
        description="插件描述",
        dependencies=[],
        hooks=["on_load", "on_unload", "on_gate_check"],  # 门禁插件必须包含 on_gate_check
    )

def create_plugin():
    return MyPlugin()
```

## ⚠️ 关键规则
1. 门禁插件必须继承 IQualityGate
2. 必须实现 evaluate(self, chapter, context) 方法（注意是 evaluate，不是 on_gate_check）
3. evaluate 的 context 参数不能省略
4. 必须返回 GateResult 对象

## Kernel API (通过 self._kernel 访问)
- await self._kernel.call_llm(messages, tier="standard", max_tokens=4096) → {"content":..., "model":..., "provider":...}
- self._kernel.get_config(key, default) → 读取配置
- self._kernel.get_logger(name) → 结构化日志

## 代码要求
1. 必须包含 create_manifest() 和 create_plugin() 两个工厂函数
2. 插件类必须设置 name 和 version 属性
3. 不要导入不存在的模块
4. 代码要有完整的错误处理
5. 用中文写注释
6. 输出完整的 Python 代码，不要截断"""


class SkillBuilderAgent:
    """AI 自我扩展代理 — 根据自然语言需求生成插件代码。"""

    def __init__(self, kernel: Any, plugins_dir: str | Path = "plugins") -> None:
        self._kernel = kernel
        self._plugins_dir = Path(plugins_dir)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def build(self, requirement: str) -> dict[str, Any]:
        """根据需求描述生成并加载插件。

        Args:
            requirement: 自然语言需求描述。

        Returns:
            {"plugin_name": str, "code": str, "loaded": bool, "message": str}
        """
        logger.info("Skill Builder: 开始分析需求", requirement=requirement[:100])

        # Step 1: 分析需求 → 提取插件名和功能规格
        spec = await self._analyze_requirement(requirement)

        # Step 2: 生成插件代码
        code = await self._generate_code(requirement, spec)

        # Step 3: 验证代码
        valid, errors = self._validate_code(code)
        if not valid:
            logger.warning("Skill Builder: 代码验证失败", errors=errors)
            # 尝试修复
            code = await self._fix_code(code, errors, requirement)
            valid, errors = self._validate_code(code)
            if not valid:
                return {
                    "plugin_name": spec.get("name", "unknown"),
                    "code": code,
                    "loaded": False,
                    "message": f"代码验证失败: {'; '.join(errors[:3])}",
                }

        # Step 4: 保存到磁盘
        plugin_name = spec.get("name", f"plugin_{uuid.uuid4().hex[:8]}")
        saved_path = self._save_plugin(plugin_name, code)

        # Step 5: 动态加载
        load_result = await self._load_plugin(plugin_name, saved_path)

        return {
            "plugin_name": plugin_name,
            "code": code,
            "path": str(saved_path),
            "loaded": load_result["success"],
            "message": load_result["message"],
        }

    async def list_built_plugins(self) -> list[dict[str, Any]]:
        """列出所有 Skill Builder 生成的插件."""
        skill_dir = self._plugins_dir / "_built"
        if not skill_dir.exists():
            return []

        plugins = []
        for plugin_dir in skill_dir.iterdir():
            if plugin_dir.is_dir() and (plugin_dir / "plugin.py").exists():
                try:
                    code = (plugin_dir / "plugin.py").read_text(encoding="utf-8")
                    # Extract description from class docstring or manifest
                    desc = ""
                    # Try class docstring
                    match = re.search(r'class\s+\w+.*?:\s*\n\s*"""(.+?)"""', code, re.DOTALL)
                    if match:
                        desc = match.group(1).strip()[:150]
                    # Fallback: extract from manifest description
                    if not desc:
                        match2 = re.search(r'description="([^"]+)"', code)
                        if match2:
                            desc = match2.group(1)[:150]
                    # Fallback: check if it's a quality gate
                    if not desc and 'IQualityGate' in code:
                        desc = "门禁检查插件（自动在章节生成时执行）"
                    plugins.append({
                        "name": plugin_dir.name,
                        "description": desc or "AI 生成插件",
                        "size": len(code),
                    })
                except Exception:
                    pass
        return plugins

    # ------------------------------------------------------------------
    # Internal Steps
    # ------------------------------------------------------------------

    async def _analyze_requirement(self, requirement: str) -> dict[str, Any]:
        """分析需求 → 提取插件名、类型、功能列表."""
        prompt = f"""分析以下需求，提取插件信息。以 JSON 返回:

需求:
{requirement}

返回格式:
```json
{{
  "name": "插件名 (英文, kebab-case, 如 battle-checker)",
  "type": "plugin_type (generation|quality_gate|style|utility)",
  "description": "一句话描述",
  "functions": ["功能1", "功能2"],
  "hooks": ["on_load", "on_unload", "on_gate_check"],
  "dependencies": [],
  "needs_llm": true,
  "llm_tier": "standard"
}}
```

规则:
- name 用英文 kebab-case
- 如果是检查/审查类插件, type=quality_gate, 实现 on_gate_check
- 如果是生成类, type=generation
- hooks 列出需要实现的生命周期钩子"""

        result = await self._kernel.call_llm(
            messages=[{"role": "user", "content": prompt}],
            tier="budget",
            max_tokens=1024,
            temperature=0.3,
        )

        content = result.get("content", "{}")
        return self._parse_json(content)

    async def _generate_code(self, requirement: str, spec: dict[str, Any]) -> str:
        """生成完整插件代码."""
        prompt = f"""请根据以下需求生成完整的 Python 插件代码。

## 需求
{requirement}

## 规格
{spec}

## 代码要求
- 必须包含 create_manifest() 和 create_plugin() 工厂函数
- 插件类设置 name="{spec.get('name', 'my-plugin')}" 和 version="0.1.0"
- 通过 self._kernel.call_llm() 调用 LLM
- 完整的错误处理 (try/except)
- 中文注释
- 只输出 Python 代码，不输出其他文字

## 插件目录
插件将保存在 plugins/_built/{spec.get('name', 'plugin')}/

现在请输出完整代码:"""

        result = await self._kernel.call_llm(
            messages=[
                {"role": "system", "content": BUILDER_SYSTEM},
                {"role": "user", "content": prompt},
            ],
            tier="standard",
            max_tokens=4096,
            temperature=0.5,
        )
        return self._extract_code(result.get("content", ""))

    async def _fix_code(self, code: str, errors: list[str], requirement: str) -> str:
        """修复代码中的错误."""
        prompt = f"""以下插件代码有错误，请修复:

## 错误
{chr(10).join(errors)}

## 原始需求
{requirement}

## 有问题的代码
```python
{code}
```

只输出修复后的完整 Python 代码，不要其他内容。"""

        result = await self._kernel.call_llm(
            messages=[{"role": "user", "content": prompt}],
            tier="standard",
            max_tokens=4096,
            temperature=0.3,
        )
        return self._extract_code(result.get("content", code))

    def _validate_code(self, code: str) -> tuple[bool, list[str]]:
        """验证 Python 代码合法性."""
        errors: list[str] = []

        # 1. 语法检查
        try:
            tree = ast.parse(code)
        except SyntaxError as e:
            return False, [f"语法错误: {e}"]

        # 2. 必须包含的工厂函数
        if "create_manifest" not in code:
            errors.append("缺少 create_manifest() 函数")
        if "create_plugin" not in code:
            errors.append("缺少 create_plugin() 函数")

        # 3. 必须有类定义且设置了 name
        classes = [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]
        if not classes:
            errors.append("缺少插件类定义")
        else:
            cls = classes[0]
            has_name = any(
                isinstance(item, ast.Assign)
                and any(t.id == "name" for t in item.targets if isinstance(t, ast.Name))
                for item in cls.body
            )
            if not has_name:
                errors.append("插件类缺少 name 属性")
            # 如果继承了 IQualityGate，必须实现 evaluate 方法
            bases = [b.id for b in cls.bases if isinstance(b, ast.Name)]
            if "IQualityGate" in bases:
                has_evaluate = any(
                    isinstance(item, ast.AsyncFunctionDef) and item.name == "evaluate"
                    for item in cls.body
                )
                if not has_evaluate:
                    errors.append("门禁插件必须实现 async def evaluate(self, chapter, context) 方法")

        # 4. 检查导入
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name not in (
                        "json", "re", "uuid", "asyncio", "yaml",
                        "datetime", "typing", "pathlib", "textwrap",
                    ):
                        errors.append(f"可能不存在的导入: {alias.name}")
            elif isinstance(node, ast.ImportFrom):
                if node.module and node.module.startswith("core."):
                    continue  # 内核模块 OK
                if node.module and node.module.startswith("plugins."):
                    continue
                if node.module and node.module in ("models",):
                    continue

        return len(errors) == 0, errors

    def _save_plugin(self, name: str, code: str) -> Path:
        """将插件代码保存到磁盘."""
        plugin_dir = self._plugins_dir / "_built" / name
        plugin_dir.mkdir(parents=True, exist_ok=True)

        # 写 __init__.py
        (plugin_dir / "__init__.py").write_text(f'"""{name} — AI 生成插件."""\n', encoding="utf-8")

        # 写 plugin.py
        (plugin_dir / "plugin.py").write_text(code, encoding="utf-8")

        logger.info("Skill Builder: 插件已保存", name=name, path=str(plugin_dir))
        return plugin_dir

    async def _load_plugin(self, name: str, path: Path) -> dict[str, Any]:
        """动态加载插件到运行中的系统."""
        try:
            # 动态导入——强制重载（避免缓存旧版本）
            mod_name = f"plugins._built.{name}.plugin"
            # 清除旧缓存
            for key in list(sys.modules.keys()):
                if name in key and '_built' in key:
                    del sys.modules[key]
            mod = importlib.import_module(mod_name)
            importlib.reload(mod)

            # 获取 manifest 和 instance
            manifest = mod.create_manifest()
            instance = mod.create_plugin()

            # 通过 PluginManager 注册和加载
            pm = self._kernel._plugin_manager
            await pm.register(manifest, instance)
            await pm.load(name)

            # 触发 on_load
            if hasattr(instance, "on_load"):
                await instance.on_load(self._kernel)

            logger.info("Skill Builder: 插件已加载", name=name)
            return {"success": True, "message": f"插件 '{name}' 已生成并加载"}

        except Exception as e:
            logger.exception("Skill Builder: 加载失败", name=name)
            return {"success": False, "message": f"加载失败: {e}"}

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_json(content: str) -> dict[str, Any]:
        import json
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            match = re.search(r'```(?:json)?\s*([\s\S]*?)```', content)
            if match:
                try:
                    return json.loads(match.group(1))
                except json.JSONDecodeError:
                    pass
        return {}

    @staticmethod
    def _extract_code(content: str) -> str:
        """从 LLM 输出中提取 Python 代码."""
        match = re.search(r'```(?:python)?\s*([\s\S]*?)```', content)
        if match:
            return match.group(1).strip()
        return content.strip()
