"""统一编辑优化流水线插件 — AI编辑批注 + 质量分析优化 + AI检测降重.

三步流水线：
1. AI编辑批注 — 分析段落问题，给出修改建议，生成优化版本
2. 质量分析优化 — 统一分析器(写作教练+十维评审+AI检测) + LLM优化
3. AI检测降重 — 复用步骤2的AI检测结果，如超标则降重处理

每一步都产出 A/B 对比数据，前端可逐段采纳/拒绝。
"""

from __future__ import annotations

import json
import re
from typing import Any

from core.logging_config import get_logger
from core.plugin_manager import PluginManifest

logger = get_logger(__name__)

# ============================================================================
# 系统提示词
# ============================================================================

ANNOTATE_SYSTEM = """你是一位资深网文编辑，拥有15年编辑经验。你的任务是逐段审阅小说文本，找出问题并给出修改建议。

## 审维度（按优先级排序）

### 1. 一致性问题（最高优先级）
- **角色状态**: 人物外貌、能力、伤势、情绪是否与前文一致
- **关系准确性**: 人物之间的关系、称谓、互动模式是否正确
- **物品/道具**: 物品的出现、消失、位置是否合理
- **时间线**: 时间流逝、事件顺序是否矛盾
- **地点状态**: 场景描述、位置关系是否一致
- **规则应用**: 世界观规则（异能、系统等）是否一致应用
- **剧情状态**: 已发生事件的引用是否准确

### 2. 毒点检测
- **逻辑硬伤**: 不合常理的情节设计
- **圣母/降智**: 角色做出不符合人设的愚蠢决定
- **无脑反派**: 反派智商下线、行为缺乏动机
- **注水段落**: 无意义的凑字数内容
- **节奏断裂**: 突兀的场景切换或情节跳跃

### 3. 文笔质量
- **AI味痕迹**: 模板化表达、排比堆砌、情感标签化
- **对话质量**: 对话是否自然、是否有信息量
- **描写质量**: 是否空洞、是否有感官细节

## 输出格式

返回JSON数组，每个元素代表一个段落的批注：
```json
[
  {
    "paragraph_index": 0,
    "has_issues": true,
    "issues": [
      {
        "type": "consistency|poison|ai_taste|writing",
        "severity": "high|medium|low",
        "description": "问题描述",
        "suggestion": "具体修改建议"
      }
    ],
    "revised_text": "修改后的段落文本（如有问题）"
  }
]
```

如果某段落没有问题，设置 `has_issues: false`，不需要 `revised_text`。

注意：
- 保持原文风格和语气，不要过度文学化
- 修改要自然，不要引入新的AI味
- 只修改有问题的部分，不要大幅改动无问题的段落
- `revised_text` 必须与原文长度相近（±20%），不要大幅扩写或缩写
"""

COACH_OPTIMIZE_SYSTEM = """你是一位资深网文写作教练+编辑。你需要综合以下信息来优化文本：

1. **统一质量分析报告**: 包含写作教练评分、十维评审(开篇/人物/节奏/情感/世界观/风格/爽点/悬念/章尾/主题)、AI检测结果
2. **编辑批注**: 前一步编辑发现的具体问题和修改建议

你的优化目标：
- 解决编辑批注中指出的所有问题
- 根据写作教练建议提升文本质量
- 针对十维评审中得分较低的维度重点优化
- 降低AI检测痕迹
- 保持原有剧情走向和人物设定不变

## 输出格式

```json
{
  "optimized_text": "优化后的完整文本",
  "changes": [
    {
      "paragraph_index": 0,
      "reason": "修改原因",
      "before": "原文片段",
      "after": "修改后片段"
    }
  ],
  "summary": "本次优化总结（解决了哪些问题、提升了哪些方面）"
}
```

注意：
- 逐段优化，每段修改都要说明原因
- 解决编辑批注中的问题是第一优先级
- 不要改变情节走向和人物性格
- 优化后字数应与原文相近（±15%）
"""

DETECT_REDUCE_SYSTEM = """你是一位反AI检测专家。你需要对文本进行降重处理，使其通过AI检测。

## 降重策略

### 第一轴：口语化改造
- 把书面语替换为口语表达
- 添加语气词（"嘛"、"吧"、"呢"）
- 使用不完整的句子结构

### 第二轴：感官细节增强
- 添加视觉、听觉、嗅觉、触觉、味觉描写
- 用具体细节替代抽象描述
- 加入环境细微感知

### 第三轴：节奏破坏
- 长短句交替，打破均匀节奏
- 插入碎碎念、心理旁白
- 使用省略号、破折号改变节奏

### 第四轴：私人词库注入
- 替换AI高频词为个性化表达
- 使用角色特有的口头禅
- 加入方言或行业术语

## 输出格式

```json
{
  "reduced_text": "降重后的完整文本",
  "changes": [
    {
      "paragraph_index": 0,
      "strategy": "使用了哪种降重策略",
      "before": "原文片段",
      "after": "修改后片段"
    }
  ],
  "ai_score_before": 0.65,
  "ai_score_after": 0.25,
  "summary": "降重总结"
}
```

注意：
- 降重不是重写，要保持原文情节和信息
- 每段最多修改30%的内容
- 优先处理AI味最重的段落
- 降重后字数应与原文相近（±10%）
"""

EXPLAIN_SYSTEM = """你是一位资深网文编辑，正在向作者解释你的修改理由。

## 任务
对比原文和优化版，生成一份清晰的修改解释报告。

## 输出格式（严格JSON）
```json
{
  "summary": "用2-3句话概括本次优化做了什么、效果如何",
  "quality_before": {"score": 65, "grade": "C", "issues": ["问题1", "问题2"]},
  "quality_after": {"score": 85, "grade": "A", "improvements": ["改进1", "改进2"]},
  "changes": [
    {
      "original_snippet": "原文片段（20-50字）",
      "optimized_snippet": "优化后片段（20-50字）",
      "reason": "为什么这样改",
      "type": "ai_taste|writing|consistency|style|logic"
    }
  ],
  "comparison": "两个版本的整体对比分析（100-200字）",
  "recommendation": "建议采用哪个版本，为什么"
}
```

## 要求
- score 为 0-100 的整数，grade 为 S/A/B/C/D 之一
- changes 列出所有有意义的修改（最多10条），每条要具体说明改了什么、为什么改
- comparison 要客观分析两个版本的优劣
- recommendation 要给出明确建议
- 用中文回答，语言要专业但易懂
"""


def create_manifest() -> PluginManifest:
    return PluginManifest(
        name="pipeline-editor",
        version="0.1.0",
        description="统一编辑优化流水线 — AI编辑批注 + 写作教练 + AI检测降重",
        dependencies=[],
        hooks=["on_load", "on_unload"],
    )


def create_plugin() -> PipelineEditorPlugin:
    return PipelineEditorPlugin()


class PipelineEditorPlugin:
    """统一编辑优化流水线插件."""

    def __init__(self) -> None:
        self._kernel: Any = None
        self._analyzer: Any = None
        self._transformer: Any = None

    async def on_load(self, kernel: Any) -> None:
        self._kernel = kernel
        from core.unified_quality_analyzer import UnifiedQualityAnalyzer
        self._analyzer = UnifiedQualityAnalyzer(kernel)
        from core.text_transformer import TextTransformer
        self._transformer = TextTransformer(kernel)
        logger.info("统一编辑优化流水线插件已加载")

    async def on_unload(self) -> None:
        self._kernel = None

    async def run_pipeline(
        self,
        content: str,
        project_id: str = "",
        chapter_num: int = 0,
        platform: str = "fanqie",
        steps: list[str] | None = None,
        *,
        volume_number: int = 1,
        ai_threshold: float = 0.3,
        humanize_mode: str = "unified",
        gate_issues: list[dict] | None = None,
    ) -> dict:
        """执行优化流水线.

        Args:
            content: 原始文本
            project_id: 项目ID（可选，用于获取上下文）
            chapter_num: 章节号
            platform: 平台
            steps: 要执行的步骤，默认全部执行
            volume_number: 卷号
            ai_threshold: AI率阈值，超过则降重
            humanize_mode: 降重模式（默认unified统一模式）

        Returns:
            流水线执行结果，包含每步的A/B对比数据
        """
        if steps is None:
            steps = ["annotate", "coach", "detect"]

        result = {
            "original": content,
            "current_content": content,
            "steps": [],
        }

        # 获取项目上下文（如有）
        context_info = ""
        if project_id and self._kernel:
            try:
                ns = f"project:{project_id}"
                chars = await self._kernel.context().get(ns, "characters", {})
                if chars:
                    char_names = list(chars.get("characters", {}).keys())[:10]
                    context_info += f"\n主要角色: {', '.join(char_names)}"
            except Exception:
                pass

        # 运行门禁链和流水线贡献者插件
        contributor_results = []
        if self._kernel:
            # 如果没有外部传入的门禁结果，先从 DB 读，没有则运行门禁链
            if not gate_issues and project_id and self._kernel.db:
                try:
                    gate_issues = await self._kernel.db.get_gate_results(project_id, chapter_num, volume_number)
                except Exception:
                    pass
            if not gate_issues:
                gate_issues = await self._run_gate_chain(content, project_id)
            # 贡献者结果：先从 DB 读，没有则运行插件
            if project_id and self._kernel.db:
                try:
                    contributor_results = await self._kernel.db.get_contributor_results(project_id, chapter_num, volume_number)
                except Exception:
                    pass
            if not contributor_results:
                contributor_results = await self._run_contributors(content, project_id, chapter_num, platform)
            if contributor_results:
                for cr in contributor_results:
                    if cr.get("summary"):
                        context_info += f"\n[{cr['name']}]: {cr['summary']}"
                    for s in (cr.get("suggestions") or []):
                        context_info += f"\n  - {s}"

        # Step 1: AI编辑批注
        if "annotate" in steps:
            logger.info("流水线步骤1: AI编辑批注")
            annotate_result = await self._annotate(content, platform, context_info, gate_issues=gate_issues)
            result["steps"].append(annotate_result)
            result["current_content"] = annotate_result.get("optimized", content)

        # Step 2: 质量分析优化（统一分析器 + LLM优化）
        if "coach" in steps:
            logger.info("流水线步骤2: 质量分析优化")
            coach_result = await self._coach_optimize(
                result["current_content"], platform, chapter_num, context_info,
                prev_annotations=result["steps"][-1] if result["steps"] else None,
            )
            result["steps"].append(coach_result)
            result["current_content"] = coach_result.get("optimized", result["current_content"])

        # Step 3: AI检测降重（复用步骤2的检测结果）
        if "detect" in steps:
            logger.info("流水线步骤3: AI检测降重")
            # 从步骤2获取已有的AI检测结果，避免重复调用
            prev_ai = None
            if result["steps"]:
                last = result["steps"][-1]
                if last.get("step") == "coach" and last.get("unified_report"):
                    prev_ai = last["unified_report"].get("ai_detection")
            detect_result = await self._detect_reduce(
                result["current_content"], platform, context_info,
                ai_threshold=ai_threshold, humanize_mode=humanize_mode,
                prev_ai_detection=prev_ai,
            )
            result["steps"].append(detect_result)
            result["current_content"] = detect_result.get("optimized", result["current_content"])

        # 生成综合解释报告
        if result["steps"] and self._kernel:
            try:
                explanation = await self._generate_explanation(
                    result["original"], result["current_content"], result["steps"]
                )
                result["explanation"] = explanation
            except Exception as e:
                logger.warning(f"生成解释报告失败: {e}")
                result["explanation"] = None

        return result

    async def _generate_explanation(self, original: str, optimized: str, steps: list[dict]) -> dict | None:
        """生成综合解释报告 — 对比原文和优化版，解释修改原因."""
        try:
            # 收集各步骤的关键信息
            step_summaries = []
            for s in steps:
                name = s.get("name", s.get("step", ""))
                summary = s.get("summary", "")
                step_summaries.append(f"- {name}: {summary}")

            # 截取文本（避免过长）
            orig_short = original[:3000] if len(original) > 3000 else original
            opt_short = optimized[:3000] if len(optimized) > 3000 else optimized

            prompt = f"""请对比以下原文和优化版，生成修改解释报告。

## 执行的优化步骤
{chr(10).join(step_summaries)}

## 原文（前3000字）
{orig_short}

## 优化版（前3000字）
{opt_short}

请按照系统提示的JSON格式返回报告。注意：只返回纯JSON，不要用代码块包裹。"""

            resp = await self._kernel.call_llm(
                [{"role": "system", "content": EXPLAIN_SYSTEM}, {"role": "user", "content": prompt}],
                tier="standard", max_tokens=2000,
            )
            content = resp.get("content", "")
            return self._parse_json_response(content)
        except Exception as e:
            logger.warning(f"解释报告生成异常: {e}")
        return None

    @staticmethod
    def _parse_json_response(text: str) -> dict | None:
        """从 LLM 响应中提取并解析 JSON，处理各种格式问题."""
        if not text:
            return None
        # 1. 去掉代码块包裹
        text = re.sub(r'```(?:json)?\s*', '', text)
        text = re.sub(r'```\s*', '', text)
        text = text.strip()
        # 2. 尝试直接解析
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass
        # 3. 用正则提取最外层 JSON 对象（非贪婪匹配第一个完整的 {}）
        try:
            depth = 0
            start = -1
            for i, ch in enumerate(text):
                if ch == '{':
                    if depth == 0: start = i
                    depth += 1
                elif ch == '}':
                    depth -= 1
                    if depth == 0 and start >= 0:
                        candidate = text[start:i + 1]
                        # 清理常见问题：尾部逗号
                        candidate = re.sub(r',\s*([}\]])', r'\1', candidate)
                        try:
                            return json.loads(candidate)
                        except json.JSONDecodeError:
                            start = -1  # 继续找下一个
        except Exception:
            pass
        # 4. 最后尝试：清理后整体解析
        try:
            cleaned = re.sub(r',\s*([}\]])', r'\1', text)
            return json.loads(cleaned)
        except json.JSONDecodeError:
            pass
        logger.warning("无法解析 LLM 返回的 JSON")
        return None

    async def _run_gate_chain(self, content: str, project_id: str) -> list[dict]:
        """运行所有 IQualityGate 插件，返回门禁结果."""
        from core.quality_gate import GateChainExecutor, GateChainConfig, IQualityGate
        try:
            gates = []
            for entry in await self._kernel._plugin_manager.list_active():
                if isinstance(entry.instance, IQualityGate):
                    gates.append(entry.instance)
            if not gates:
                return []
            chain = GateChainExecutor(GateChainConfig(gates=gates))
            gate_result = await chain.execute(
                {"content": content, "project_id": project_id},
                {"project_id": project_id},
            )
            return [
                {"gate": g.gate_name, "verdict": g.verdict.value, "score": g.score,
                 "issues": [{"severity": i.severity.value, "code": i.code, "message": i.message, "suggestion": i.suggestion}
                            for i in g.issues]}
                for g in gate_result.gates
            ]
        except Exception as e:
            logger.warning(f"门禁链执行失败: {e}")
            return []

    async def _run_contributors(self, content: str, project_id: str, chapter_num: int, platform: str) -> list[dict]:
        """并行运行所有 IPipelineContributor 插件，返回分析结果."""
        import asyncio
        from core.quality_gate import IPipelineContributor
        try:
            contributors = []
            for entry in await self._kernel._plugin_manager.list_active():
                if isinstance(entry.instance, IPipelineContributor):
                    contributors.append(entry.instance)
            if not contributors:
                return []
            ctx = {"project_id": project_id, "chapter_num": chapter_num, "platform": platform, "kernel": self._kernel}
            tasks = [c.analyze(content, ctx) for c in contributors]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            output = []
            for c, r in zip(contributors, results):
                if isinstance(r, Exception):
                    logger.warning(f"贡献者 {c.name} 执行失败: {r}")
                    continue
                r["name"] = c.name
                output.append(r)
            return output
        except Exception as e:
            logger.warning(f"贡献者执行失败: {e}")
            return []

    async def _annotate(self, content: str, platform: str, context_info: str, gate_issues: list[dict] | None = None) -> dict:
        """AI编辑批注."""
        try:
            # 规则预检
            rule_issues = self._rule_based_check(content)

            # 门禁问题提示
            gate_hint = ""
            if gate_issues:
                issues_text = []
                for g in gate_issues:
                    for i in g.get("issues", []):
                        issues_text.append(f"- [{g.get('gate', '')}] {i.get('code', '')}: {i.get('message', '')}")
                if issues_text:
                    gate_hint = "\n## 门禁检测发现的问题（必须重点修复）\n" + "\n".join(issues_text) + "\n"

            # LLM批注
            user_prompt = f"""请审阅以下小说文本，找出问题并给出修改建议。

## 平台: {platform}
{context_info}
{gate_hint}
## 文本
{content}

请严格按照JSON格式返回批注结果。"""

            resp = await self._kernel.call_llm(
                [
                    {"role": "system", "content": ANNOTATE_SYSTEM},
                    {"role": "user", "content": user_prompt},
                ],
                tier="standard",
                max_tokens=8192,
                temperature=0.3,
                response_format={"type": "json_object"},
            )

            raw = resp.get("content", "{}")
            parsed = self._parse_json_response(raw)

            # 处理LLM返回的格式：可能是列表，也可能是包含annotations键的对象
            if isinstance(parsed, dict):
                annotations = parsed.get("annotations", parsed.get("results", []))
                if not isinstance(annotations, list):
                    annotations = []
            elif isinstance(parsed, list):
                annotations = parsed
            else:
                annotations = []

            logger.debug("AI编辑批注解析结果", parsed_type=type(parsed).__name__, annotations_count=len(annotations))

            # 合并规则检测结果
            if rule_issues:
                annotations = self._merge_rule_issues(annotations, rule_issues)

            # 生成优化文本
            optimized = self._apply_annotations(content, annotations)

            # 构建diff items
            diff_items = self._build_diff_items(content, optimized, annotations)

            # 统计
            total_issues = sum(
                len(a.get("issues", []))
                for a in annotations
                if a.get("has_issues")
            )

            return {
                "step": "annotate",
                "name": "AI编辑批注",
                "original": content,
                "optimized": optimized,
                "annotations": annotations,
                "diff_items": diff_items,
                "summary": f"发现 {total_issues} 个问题",
                "rule_issues": rule_issues,
            }
        except Exception as e:
            logger.error("AI编辑批注失败", error=str(e))
            return {
                "step": "annotate",
                "name": "AI编辑批注",
                "original": content,
                "optimized": content,
                "annotations": [],
                "diff_items": [],
                "summary": f"批注失败: {str(e)[:100]}",
                "error": str(e),
            }

    async def _coach_optimize(
        self, content: str, platform: str, chapter_num: int,
        context_info: str, prev_annotations: dict | None = None,
    ) -> dict:
        """质量分析优化 — 统一分析器(写作教练+十维评审+AI检测) + LLM优化."""
        try:
            # 统一质量分析（并行调用三个插件）
            unified_report = {}
            report_summary = ""
            try:
                report = await self._analyzer.analyze_text(content, platform=platform)
                unified_report = report.to_dict()
                report_summary = self._format_unified_report(unified_report)
            except Exception as e:
                logger.warning("统一质量分析失败，降级为纯教练", error=str(e))
                # 降级：只调写作教练
                try:
                    coach_entry = await self._kernel.get_plugin("writing-coach")
                    if coach_entry and coach_entry.instance:
                        analysis = await coach_entry.instance.analyze_chapter(
                            content, platform=platform, chapter_num=chapter_num,
                        )
                        report_summary = json.dumps(analysis, ensure_ascii=False, indent=2)
                except Exception:
                    report_summary = "（质量分析不可用）"

            # 编辑批注
            annotations_text = ""
            if prev_annotations and prev_annotations.get("annotations"):
                issues = []
                for a in prev_annotations["annotations"]:
                    if a.get("has_issues"):
                        for issue in a.get("issues", []):
                            issues.append(f"- [{issue.get('type','')}] {issue.get('description','')}: {issue.get('suggestion','')}")
                if issues:
                    annotations_text = "\n## 编辑批注发现的问题\n" + "\n".join(issues)

            user_prompt = f"""请优化以下小说文本。

## 平台: {platform}
{context_info}

## 统一质量分析报告
{report_summary}
{annotations_text}

## 原文
{content}

请严格按照JSON格式返回优化结果。"""

            resp = await self._kernel.call_llm(
                [
                    {"role": "system", "content": COACH_OPTIMIZE_SYSTEM},
                    {"role": "user", "content": user_prompt},
                ],
                tier="standard",
                max_tokens=8192,
                temperature=0.5,
                response_format={"type": "json_object"},
            )

            raw = resp.get("content", "{}")
            result = self._parse_json_response(raw)
            optimized = result.get("optimized_text", content)
            changes = result.get("changes", [])
            summary = result.get("summary", "")

            diff_items = self._build_diff_items(content, optimized, None)

            return {
                "step": "coach",
                "name": "质量分析优化",
                "original": content,
                "optimized": optimized,
                "changes": changes,
                "diff_items": diff_items,
                "summary": summary or f"优化了 {len(changes)} 处",
                "unified_report": unified_report,
                "report_summary": report_summary,
            }
        except Exception as e:
            logger.error("质量分析优化失败", error=str(e))
            return {
                "step": "coach",
                "name": "质量分析优化",
                "original": content,
                "optimized": content,
                "changes": [],
                "diff_items": [],
                "summary": f"优化失败: {str(e)[:100]}",
                "error": str(e),
            }

    async def _detect_reduce(
        self, content: str, platform: str, context_info: str,
        ai_threshold: float = 0.3, humanize_mode: str = "unified",
        prev_ai_detection: dict | None = None,
    ) -> dict:
        """AI检测降重 — 使用 TextTransformer 统一入口."""
        try:
            # 如果步骤2已有AI检测结果且内容未变，直接复用
            ai_score_before = None
            if prev_ai_detection and prev_ai_detection.get("ai_score") is not None:
                ai_score_before = prev_ai_detection["ai_score"]
                logger.info("复用步骤2的AI检测结果: ai_score=%.3f", ai_score_before)

            # 如果已有检测结果且未超标，直接返回
            if ai_score_before is not None and ai_score_before <= ai_threshold:
                return {
                    "step": "detect",
                    "name": "AI检测降重",
                    "original": content,
                    "optimized": content,
                    "ai_score_before": ai_score_before,
                    "ai_score_after": ai_score_before,
                    "diff_items": [],
                    "summary": f"AI率 {ai_score_before:.0%}，未超过阈值 {ai_threshold:.0%}，无需降重",
                    "reduction_applied": False,
                }

            # 通过 TextTransformer 执行检测+降重
            step_result = await self._transformer.detect_reduce(
                content, threshold=ai_threshold, mode=humanize_mode,
            )
            meta = step_result.metadata
            reduced = step_result.output_text

            diff_items = self._build_diff_items(content, reduced, None) if step_result.changed else []

            return {
                "step": "detect",
                "name": "AI检测降重",
                "original": content,
                "optimized": reduced,
                "ai_score_before": meta.get("ai_score_before", 0),
                "ai_score_after": meta.get("ai_score_after", meta.get("ai_score_before", 0)),
                "diff_items": diff_items,
                "summary": (f"AI率 {meta.get('ai_score_before', 0):.0%} → {meta.get('ai_score_after', 0):.0%}"
                            if step_result.changed else
                            f"AI率 {meta.get('ai_score_before', 0):.0%}，无需降重"),
                "reduction_applied": meta.get("reduction_applied", False),
            }
        except Exception as e:
            logger.error("AI检测降重失败", error=str(e))
            return {
                "step": "detect",
                "name": "AI检测降重",
                "original": content,
                "optimized": content,
                "ai_score_before": 0,
                "ai_score_after": 0,
                "diff_items": [],
                "summary": f"检测失败: {str(e)[:100]}",
                "error": str(e),
            }

    # ==================================================================
    # 内部工具
    # ==================================================================

    def _rule_based_check(self, content: str) -> list[dict]:
        """基于规则的预检（不调用LLM）."""
        issues = []
        paragraphs = content.split("\n\n")

        for i, para in enumerate(paragraphs):
            para = para.strip()
            if not para:
                continue

            # 检查段落过长（可能是注水）
            if len(para) > 500:
                issues.append({
                    "paragraph_index": i,
                    "type": "poison",
                    "severity": "low",
                    "description": f"段落过长（{len(para)}字），可能影响阅读体验",
                    "suggestion": "考虑拆分为多个段落",
                })

            # 检查AI味开头
            ai_openings = [
                (r'^在.{2,15}中，', "AI模板开头「在...中」"),
                (r'^随着.{2,20}，', "AI模板开头「随着...」"),
                (r'^当.{2,15}时，', "AI模板开头「当...时」"),
                (r'^在这个.{2,10}', "AI模板开头「在这个...」"),
            ]
            for pattern, desc in ai_openings:
                if re.match(pattern, para):
                    issues.append({
                        "paragraph_index": i,
                        "type": "ai_taste",
                        "severity": "medium",
                        "description": desc,
                        "suggestion": "换一个更自然的开头方式",
                    })
                    break

            # 检查情感标签
            emotion_labels = ["心中充满了", "内心涌起", "不禁感到", "心中一阵"]
            for label in emotion_labels:
                if label in para:
                    issues.append({
                        "paragraph_index": i,
                        "type": "ai_taste",
                        "severity": "low",
                        "description": f"情感标签化表达「{label}」",
                        "suggestion": "用具体行为或细节替代情感标签",
                    })

        return issues

    def _parse_json_response(self, raw: str) -> Any:
        """解析LLM返回的JSON."""
        # 尝试直接解析
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            pass

        # 尝试提取 ```json ... ``` 代码块（贪婪匹配）
        json_match = re.search(r'```(?:json)?\s*([\s\S]+?)\s*```', raw)
        if json_match:
            try:
                return json.loads(json_match.group(1))
            except json.JSONDecodeError:
                pass

        # 尝试找第一个JSON对象或数组（按括号配对）
        for start_char, end_char in [('{', '}'), ('[', ']')]:
            start = raw.find(start_char)
            if start < 0:
                continue
            # 找到对应的闭合括号
            depth = 0
            for i in range(start, len(raw)):
                if raw[i] == start_char:
                    depth += 1
                elif raw[i] == end_char:
                    depth -= 1
                    if depth == 0:
                        try:
                            return json.loads(raw[start:i + 1])
                        except json.JSONDecodeError:
                            break
                        break

        logger.warning("无法解析JSON响应", raw_preview=raw[:200])
        return [] if raw.strip().startswith('[') else {}

    def _merge_rule_issues(self, llm_annotations: list, rule_issues: list) -> list:
        """合并规则检测结果到LLM批注中."""
        if not isinstance(llm_annotations, list):
            return llm_annotations

        # 按段落索引组织规则问题
        rule_by_para = {}
        for issue in rule_issues:
            idx = issue.get("paragraph_index", 0)
            if idx not in rule_by_para:
                rule_by_para[idx] = []
            rule_by_para[idx].append(issue)

        # 合并
        for ann in llm_annotations:
            idx = ann.get("paragraph_index", 0)
            if idx in rule_by_para:
                existing = ann.get("issues", [])
                existing.extend(rule_by_para.pop(idx))
                ann["issues"] = existing
                ann["has_issues"] = True

        # 添加仅规则检测到的问题
        for idx, issues in rule_by_para.items():
            llm_annotations.append({
                "paragraph_index": idx,
                "has_issues": True,
                "issues": issues,
            })

        return llm_annotations

    def _apply_annotations(self, content: str, annotations: list) -> str:
        """应用批注生成优化文本."""
        if not annotations or not isinstance(annotations, list):
            return content

        paragraphs = content.split("\n\n")
        result = []

        for i, para in enumerate(paragraphs):
            ann = next((a for a in annotations if a.get("paragraph_index") == i), None)
            if ann and ann.get("has_issues") and ann.get("revised_text"):
                result.append(ann["revised_text"])
            else:
                result.append(para)

        return "\n\n".join(result)

    def _build_diff_items(self, original: str, optimized: str, annotations: list | None) -> list:
        """构建段落级diff items供前端渲染."""
        orig_paras = original.split("\n\n")
        opt_paras = optimized.split("\n\n")
        items = []

        max_len = max(len(orig_paras), len(opt_paras))
        for i in range(max_len):
            op = orig_paras[i].strip() if i < len(orig_paras) else ""
            fp = opt_paras[i].strip() if i < len(opt_paras) else ""
            if not op and not fp:
                continue

            is_changed = op != fp
            reason = ""
            if annotations and isinstance(annotations, list):
                ann = next((a for a in annotations if a.get("paragraph_index") == i), None)
                if ann and ann.get("has_issues"):
                    issues = ann.get("issues", [])
                    reason = "; ".join(iss.get("description", "") for iss in issues[:3])

            items.append({
                "index": i,
                "orig": op,
                "final": fp,
                "isChanged": is_changed,
                "reason": reason,
            })

        return items

    def _format_unified_report(self, report: dict) -> str:
        """将统一质量报告格式化为 LLM 可读的文本."""
        lines = []

        overall = report.get("overall_score", 0)
        grade = report.get("grade", "D")
        lines.append(f"综合评分: {overall:.1%} (等级 {grade})")
        lines.append("")

        # 写作教练
        wq = report.get("writing_quality", {})
        if wq:
            lines.append(f"### 写作教练 (得分: {wq.get('score', 0):.1%})")
            if wq.get("metrics"):
                m = wq["metrics"]
                lines.append(f"- 对话占比: {m.get('dialogue_ratio', 0)}%")
                lines.append(f"- 段落均长: {m.get('avg_paragraph_length', 0)}字")
                lines.append(f"- 开头对话: {'是' if m.get('opens_with_dialogue') else '否'}")
                lines.append(f"- 结尾钩子: {'有' if m.get('has_ending_hook') else '无'}")
                lines.append(f"- 手机适配: {'是' if m.get('mobile_friendly') else '否'}")
            for s in wq.get("suggestions", [])[:5]:
                if isinstance(s, dict):
                    lines.append(f"- [{s.get('area','')}] {s.get('issue','')}: {s.get('fix','')}")
            lines.append("")

        # 十维评审
        dims = report.get("dimension_scores", {})
        if dims:
            dim_names = {
                "hook_strength": "开篇吸引力", "character_depth": "人物塑造",
                "pacing": "节奏控制", "emotional_resonance": "情感共鸣",
                "world_coherence": "世界观", "style_uniqueness": "风格独特性",
                "payoff_density": "爽点密度", "suspense": "悬念感",
                "chapter_hook": "章尾钩子", "theme_depth": "主题深度",
            }
            lines.append("### 十维评审")
            for key, name in dim_names.items():
                val = dims.get(key, 0)
                bar = "█" * int(val) + "░" * (10 - int(val))
                lines.append(f"- {name}: {val}/10 {bar}")
            low_dims = [(dim_names.get(k, k), v) for k, v in dims.items() if v < 6]
            if low_dims:
                lines.append(f"⚠️ 低分维度 (< 6): {', '.join(f'{n}({v})' for n, v in low_dims)}")
            lines.append("")

        # AI检测
        ai = report.get("ai_detection", {})
        if ai:
            ai_score = ai.get("ai_score", 0)
            lines.append(f"### AI检测 (人类度: {ai_score:.0%})")
            lines.append(f"- 判定: {'⚠️ 疑似AI' if ai.get('is_likely_ai') else '✅ 通过'}")
            total = ai.get("total_issues", 0)
            if total:
                lines.append(f"- 检测问题数: {total}")
            lines.append("")

        # 问题汇总
        issues = report.get("issues", [])
        if issues:
            lines.append(f"### 问题汇总 ({len(issues)}个)")
            for iss in issues[:10]:
                sev = iss.get("severity", "info")
                lines.append(f"- [{sev}] {iss.get('message', '')}")
            if len(issues) > 10:
                lines.append(f"... 还有 {len(issues) - 10} 个问题")
            lines.append("")

        # 亮点
        strengths = report.get("strengths", [])
        if strengths:
            lines.append("### 亮点")
            for s in strengths[:5]:
                lines.append(f"- ✅ {s}")

        return "\n".join(lines)
