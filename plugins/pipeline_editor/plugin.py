"""证据驱动的编辑优化流水线插件.

设计原则：先收证据 → 一次定点改 → 重测，报告里每个分数、每处修改都可追溯到来源。

三阶段：
1. 收集证据 — 规则预检 + 门禁 + 贡献者 + 统一质量分析器，按段落归并出
   「哪一段有什么问题、出处是谁、命中了哪些具体词」。不额外调 LLM。
2. 定点改写 — 只把「有证据的段落」连同证据清单喂给 LLM，逐段返回修改，
   带长度漂移护栏；无证据段落不送不改。一次 LLM 调用。
3. 重测降重 — 对改写结果重测（有改动才重测），拿到真实的优化后分数；
   AI 率仍超标才触发定点降重。

报告（explanation）全部用实测值构建：分数取分析器实算的 overall_score，
问题取真实检测命中，改进取 before/after 的 issue 差集——不再让 LLM 现编分数。
"""

from __future__ import annotations

import contextlib
import json
import re
from typing import Any

from core.logging_config import get_logger
from core.plugin_manager import PluginManifest

logger = get_logger(__name__)

# ============================================================================
# 系统提示词 — 仅保留「定点改写」一个
# ============================================================================

REWRITE_SYSTEM = """你是一位资深网文编辑。下面给你若干「有问题的段落」及每段的具体证据\
（来自规则检测、内容门禁、AI检测器的真实命中）。你的任务是只修改这些段落，逐段消除证据指出的问题。

## 铁律
- 只改给你的段落，逐段返回；不要新增段落、不要合并段落、不要触碰没给你的内容。
- 每段修改后字数与原段相近（±20% 以内），禁止大幅扩写或缩写。
- 保持原有剧情、人物关系、关键信息不变，不引入新的 AI 味：不堆排比、不加情感标签\
（"心中涌起""不禁感到"），不用"在…中""随着…""当…时"这类模板开头。
- 逐条消除每段的证据，并在 applied_fixes 里回填你实际处理了哪些证据 code。

## 输出格式（严格 JSON，不要用代码块包裹）
{"revisions":[{"paragraph_index":0,"revised_text":"修改后的该段完整文本","applied_fixes":["证据code"]}]}

没问题或无需改动的段落，不要出现在 revisions 里。"""

# AI 降重定点改写提示 — 同样逐段、保结构，只针对命中 AI 痕迹的段落
REDUCE_SYSTEM = """你是中文小说去AI痕迹专家。下面给你若干「AI痕迹偏重的段落」及每段命中的具体AI模式。\
你的任务是只重写这些段落，消除AI腔，让它读起来像人写的。

## 铁律
- 只改给你的段落，逐段返回；不要新增/合并/拆分段落，不要触碰没给你的内容。
- 每段重写后字数与原段相近（±20% 以内）。
- 保持剧情、人物、关键信息不变。删AI腔的同时不要引入新AI味（不堆排比、\
不加"心中涌起/不禁"类情感标签、不用"在…中/随着…/当…时"模板开头）。
- 手法：书面语→口语动作、情感标签→具体生理反应/动作、整齐句式→长短打散、\
模板比喻→具体细节。保留至少一处"毛边"（人才会写的不完美细节）。
- 在 applied_fixes 里回填你实际处理了哪些AI模式。

## 输出格式（严格 JSON，不要用代码块包裹）
{"revisions":[{"paragraph_index":0,"revised_text":"重写后的该段完整文本","applied_fixes":["命中的AI模式"]}]}

没问题的段落不要出现在 revisions 里。"""

# 十维维度中文名（报告与提示词共用）
DIM_NAMES = {
    "hook_strength": "开篇吸引力", "character_depth": "人物塑造",
    "pacing": "节奏控制", "emotional_resonance": "情感共鸣",
    "world_coherence": "世界观", "style_uniqueness": "风格独特性",
    "payoff_density": "爽点密度", "suspense": "悬念感",
    "chapter_hook": "章尾钩子", "theme_depth": "主题深度",
}

# 证据 code 前缀 → 前端修改卡片 type
_CODE_TYPE_MAP = [
    ("anti_ai", "ai_taste"),
    ("consistency", "consistency"),
    ("foreshadow", "consistency"),
    ("poison", "logic"),
    ("logic", "logic"),
    ("ai_taste", "ai_taste"),
    ("style", "style"),
]


def _code_to_type(code: str) -> str:
    """证据 code → 前端类型标签."""
    low = (code or "").lower()
    for prefix, t in _CODE_TYPE_MAP:
        if low.startswith(prefix) or prefix in low:
            return t
    return "writing"


def create_manifest() -> PluginManifest:
    return PluginManifest(
        name="pipeline-editor",
        version="0.2.0",
        description="证据驱动编辑优化流水线 — 收证据 → 定点改写 → 重测",
        dependencies=[],
        hooks=["on_load", "on_unload"],
    )


def create_plugin() -> PipelineEditorPlugin:
    return PipelineEditorPlugin()


class PipelineEditorPlugin:
    """证据驱动的编辑优化流水线插件."""

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
        logger.info("证据驱动编辑优化流水线插件已加载")

    async def on_unload(self) -> None:
        self._kernel = None

    # ==================================================================
    # 主流程
    # ==================================================================

    async def run_pipeline(
        self,
        content: str,
        project_id: str = "",
        chapter_num: int = 0,
        platform: str = "fanqie",
        steps: list[str] | None = None,
        *,
        volume_number: int = 1,
        ai_threshold: float = 0.2,
        humanize_mode: str = "unified",
        gate_issues: list[dict] | None = None,
    ) -> dict:
        """执行证据驱动优化流水线.

        返回契约（保持与旧版一致，调用方零改动）：
            {original, current_content, steps:[...], explanation:{...}}

        steps 语义（向后兼容旧的 annotate/coach/detect 命名）：
            - 含 "annotate" 或 "coach" → 执行定点改写
            - 含 "detect" → 执行重测 + 条件降重
            证据收集始终执行。
        """
        if steps is None:
            steps = ["annotate", "coach", "detect"]
        do_rewrite = ("annotate" in steps) or ("coach" in steps)
        do_detect = "detect" in steps

        result: dict[str, Any] = {
            "original": content,
            "current_content": content,
            "steps": [],
        }

        # ---- 阶段 1：收集证据（含门禁 / 贡献者 / 分析器实测基线）----
        evidence = await self._collect_evidence(
            content, project_id, chapter_num, platform,
            volume_number=volume_number, gate_issues=gate_issues,
        )
        result["steps"].append(evidence["step_record"])

        # ---- 阶段 2：定点改写 ----
        if do_rewrite and evidence["para_evidence"]:
            rewrite_step = await self._rewrite_paragraphs(content, platform, evidence)
            result["steps"].append(rewrite_step)
            result["current_content"] = rewrite_step.get("optimized", content)
            evidence["_rewrite_changes"] = rewrite_step.get("changes", [])
        elif do_rewrite:
            result["steps"].append({
                "step": "coach", "name": "定点改写",
                "original": content, "optimized": content,
                "changes": [], "diff_items": [],
                "summary": "未发现需要修改的段落，正文保持原样",
            })

        # ---- 阶段 3：重测 + 条件降重 ----
        after_report: dict[str, Any] = {}
        if result["current_content"] != content:
            # 内容有改动才值得重测
            after_report = await self._safe_analyze(result["current_content"], platform, evidence["genre_tags"])
        else:
            after_report = evidence["baseline_report"]

        if do_detect:
            detect_step = await self._detect_reduce(
                result["current_content"], platform,
                ai_threshold=ai_threshold, humanize_mode=humanize_mode,
                prev_ai_detection=after_report.get("ai_detection"),
            )
            result["steps"].append(detect_step)
            if detect_step.get("optimized") and detect_step["optimized"] != result["current_content"]:
                result["current_content"] = detect_step["optimized"]
                evidence["_detect_changes"] = detect_step.get("changes", [])
                # 降重改了内容 → 再测一次拿真实终值
                after_report = await self._safe_analyze(result["current_content"], platform, evidence["genre_tags"])

        # ---- 用实测值构建报告（不再让 LLM 编分数）----
        result["explanation"] = self._build_grounded_report(
            evidence, after_report, result["original"], result["current_content"]
        )
        return result

    async def _safe_analyze(self, content: str, platform: str, genre_tags: list[str]) -> dict:
        """调用统一分析器，失败返回空 dict（不阻断流水线）."""
        try:
            report = await self._analyzer.analyze_text(content, platform=platform, genre_tags=genre_tags)
            return report.to_dict()
        except Exception as e:
            logger.warning("统一质量分析失败", error=str(e))
            return {}


    # ==================================================================
    # 阶段 1：收集证据
    # ==================================================================

    async def _collect_evidence(
        self, content: str, project_id: str, chapter_num: int, platform: str,
        *, volume_number: int = 1, gate_issues: list[dict] | None = None,
    ) -> dict:
        """收集所有可追溯证据，按段落归并.

        Returns dict:
            paragraphs: list[str]                 — 原文段落
            para_evidence: dict[int, list[dict]]  — 段落级证据 {idx: [{code,source,message,suggestion}]}
            chapter_evidence: list[dict]          — 章节级证据（门禁/贡献者，无段落坐标）
            baseline_report: dict                 — 分析器实测基线（overall_score/dimension/ai_detection）
            genre_tags: list[str]
            context_info: str                     — 喂给改写模型的项目/体裁上下文
            step_record: dict                     — 写入 result["steps"] 的记录
        """
        paragraphs = content.split("\n\n")
        para_evidence: dict[int, list[dict]] = {}
        chapter_evidence: list[dict] = []

        def add_para(idx: int, ev: dict) -> None:
            para_evidence.setdefault(idx, []).append(ev)

        # ---- 1a. 规则预检（自带 paragraph_index）----
        for issue in self._rule_based_check(content):
            add_para(issue.get("paragraph_index", 0), {
                "code": f"{issue.get('type', 'rule')}.{issue.get('severity', 'low')}",
                "source": "规则检测",
                "message": issue.get("description", ""),
                "suggestion": issue.get("suggestion", ""),
            })

        # ---- 1b. 项目 / 体裁上下文 + 规则基线 ----
        context_info, genre_tags = await self._build_context_info(project_id)

        # ---- 1c. 门禁链（含 skill builder 的 IQualityGate 插件）----
        if self._kernel:
            if not gate_issues and project_id and self._kernel.db:
                with contextlib.suppress(Exception):
                    gate_issues = await self._kernel.db.get_gate_results(project_id, chapter_num, volume_number)
            if not gate_issues:
                gate_issues = await self._run_gate_chain(content, project_id)
        for g in (gate_issues or []):
            gate_name = g.get("gate", "门禁")
            for i in g.get("issues", []):
                ev = {
                    "code": i.get("code", f"gate.{gate_name}"),
                    "source": f"门禁:{gate_name}",
                    "message": i.get("message", ""),
                    "suggestion": i.get("suggestion", ""),
                }
                # 门禁无段落坐标 → 尝试用 message 里的引文定位，否则归章节级
                idx = self._locate_evidence(paragraphs, i.get("message", ""))
                if idx is not None:
                    add_para(idx, ev)
                else:
                    chapter_evidence.append(ev)

        # ---- 1d. 贡献者（含 skill builder 的 IPipelineContributor 插件）----
        contributor_results: list[dict] = []
        if self._kernel:
            if project_id and self._kernel.db:
                with contextlib.suppress(Exception):
                    contributor_results = await self._kernel.db.get_contributor_results(
                        project_id, chapter_num, volume_number)
            if not contributor_results:
                contributor_results = await self._run_contributors(content, project_id, chapter_num, platform)
        for cr in (contributor_results or []):
            name = cr.get("name", "贡献者")
            if cr.get("summary"):
                chapter_evidence.append({
                    "code": f"contributor.{name}", "source": f"分析:{name}",
                    "message": cr["summary"], "suggestion": "",
                })
            for s in (cr.get("suggestions") or []):
                chapter_evidence.append({
                    "code": f"contributor.{name}", "source": f"分析:{name}",
                    "message": str(s), "suggestion": str(s),
                })

        # ---- 1e. 统一质量分析器：实测基线 + AI 命中具体词 ----
        baseline_report = await self._safe_analyze(content, platform, genre_tags)
        for iss in baseline_report.get("issues", []):
            ev = {
                "code": iss.get("code", "quality"),
                "source": {"anti_ai": "AI检测器", "quality_evaluator": "十维评审"}.get(
                    iss.get("source", ""), iss.get("source", "质量分析")),
                "message": iss.get("message", ""),
                "suggestion": iss.get("suggestion", ""),
            }
            # AI 检测命中常带具体词 → 用 suggestion 里的词定位段落
            idx = self._locate_evidence(paragraphs, iss.get("suggestion", "")) \
                or self._locate_evidence(paragraphs, iss.get("message", ""))
            if idx is not None:
                add_para(idx, ev)
            else:
                chapter_evidence.append(ev)

        # ---- 1f. 深度精修兜底 ----
        # 没有任何段落级证据，但十维有低分项或教练有建议 → 不能放过，挑最相关的段挂证据
        deep_polish = False
        if not para_evidence:
            deep_ev = self._deep_polish_evidence(paragraphs, baseline_report)
            for idx, ev in deep_ev:
                add_para(idx, ev)
            deep_polish = bool(deep_ev)

        # ---- 组装 step 记录 ----
        total_para_issues = sum(len(v) for v in para_evidence.values())
        summary = (f"定位到 {len(para_evidence)} 段共 {total_para_issues} 处问题"
                   f"，章节级 {len(chapter_evidence)} 条")
        if deep_polish:
            summary += "（深度精修：依十维低分项挑段精修）"
        step_record = {
            "step": "annotate",
            "name": "证据收集",
            "original": content,
            "optimized": content,
            "para_evidence": {str(k): v for k, v in para_evidence.items()},
            "chapter_evidence": chapter_evidence,
            "baseline_report": baseline_report,
            "summary": summary,
        }

        return {
            "paragraphs": paragraphs,
            "para_evidence": para_evidence,
            "chapter_evidence": chapter_evidence,
            "baseline_report": baseline_report,
            "genre_tags": genre_tags,
            "context_info": context_info,
            "step_record": step_record,
        }

    @staticmethod
    def _locate_evidence(paragraphs: list[str], hint: str) -> int | None:
        """用证据里的引文/命中词在段落中定位 paragraph_index.

        从 hint 中抽取被引号/书名号包裹的词，或冒号后的片段，逐段 substring 查找。
        找不到返回 None（→ 归为章节级证据）。
        """
        if not hint:
            return None
        # 抽取候选定位词：引号内、书名号内、或冒号之后的片段
        cands: list[str] = []
        _open = "「『“‘’"   # 「 『 " ' '
        _close = "」』”‘’"  # 」 』 " ' '
        quote_re = re.compile(rf'[{_open}]([^{_close}]{{2,30}})[{_close}]')
        for m in quote_re.findall(hint):
            cands.append(m)
        for seg in re.split(r'[:：,，、]', hint):
            seg = seg.strip()
            if 2 <= len(seg) <= 30 and not re.search(r'[（）()]', seg):
                cands.append(seg)
        for cand in cands:
            for idx, para in enumerate(paragraphs):
                if cand and cand in para:
                    return idx
        return None

    @staticmethod
    def _deep_polish_evidence(paragraphs: list[str], baseline_report: dict) -> list[tuple[int, dict]]:
        """深度精修兜底：无段落级证据但章节质量偏弱时，按十维低分项挑段挂证据.

        维度 → 候选段落的映射（依网文常识）：
            开篇吸引力 → 首段；章尾钩子 → 末段；
            节奏/爽点/悬念 → 最长的几段（最易拖沓）；
            其余低分维度 → 落到最长段，作为整体精修锚点。
        返回 [(paragraph_index, evidence_dict), ...]，最多 3 段，避免无依据地全篇重写。
        """
        idxs = [i for i, p in enumerate(paragraphs) if p.strip()]
        if not idxs:
            return []
        first_idx = idxs[0]
        last_idx = idxs[-1]
        # 按长度排序取最长段
        longest = sorted(idxs, key=lambda i: len(paragraphs[i]), reverse=True)

        dims = baseline_report.get("dimension_scores", {})
        low_dims = {k: v for k, v in dims.items() if isinstance(v, (int, float)) and v < 6}

        dim_target = {
            "hook_strength": first_idx, "chapter_hook": last_idx,
            "pacing": longest[0], "payoff_density": longest[0], "suspense": longest[0],
        }

        out: list[tuple[int, dict]] = []
        used: set[int] = set()
        for key, val in sorted(low_dims.items(), key=lambda kv: kv[1]):
            name = DIM_NAMES.get(key, key)
            idx = dim_target.get(key, longest[0])
            if idx in used:
                continue
            used.add(idx)
            out.append((idx, {
                "code": f"dimension.{key}",
                "source": f"十维评审:{name}",
                "message": f"{name}得分偏低（{val}/10），此段是该维度的薄弱锚点",
                "suggestion": f"针对「{name}」做精修：强化该段的{name}表现",
            }))
            if len(out) >= 3:
                break

        # 十维全过线，但教练有可执行建议 → 挂到最长段做一次精修
        if not out:
            wq = baseline_report.get("writing_quality", {})
            for sug in (wq.get("suggestions") or [])[:1]:
                if isinstance(sug, dict) and (sug.get("issue") or sug.get("fix")):
                    out.append((longest[0], {
                        "code": "coach.suggestion",
                        "source": "写作教练",
                        "message": sug.get("issue", "") or "可进一步打磨",
                        "suggestion": sug.get("fix", ""),
                    }))
        return out

    async def _build_context_info(self, project_id: str) -> tuple[str, list[str]]:
        """构建项目/体裁上下文 + 润色审阅规则基线（供改写模型参考）."""
        context_info = ""
        genre_tags: list[str] = []
        if project_id and self._kernel:
            try:
                ns = f"project:{project_id}"
                chars = await self._kernel.context().get(ns, "characters", {})
                if chars:
                    char_names = list(chars.get("characters", {}).keys())[:10]
                    if char_names:
                        context_info += f"\n主要角色: {', '.join(char_names)}"
            except Exception:
                pass
            if self._kernel.db:
                try:
                    meta = await self._kernel.db.get_project(project_id)
                    if meta:
                        tags_raw = meta.get("genre_tags", "[]")
                        genre_tags = json.loads(tags_raw) if isinstance(tags_raw, str) else (tags_raw or [])
                except Exception:
                    pass

        try:
            from core import knowledge_resolver as kr

            rule_parts: list[str] = []
            for skill in ("通用-正文润色", "通用-审阅章节正文"):
                layers = kr.skill_layers(skill)
                if layers.get("redlines"):
                    rule_parts.append(f"### {skill} · 红线（违反即错）\n{layers['redlines'][:1000]}")
                if layers.get("targets"):
                    rule_parts.append(f"### {skill} · 靶值取值约定\n{layers['targets'][:700]}")
            targets = kr.genre_targets(genre_tags)
            if targets:
                tv_lines = "\n".join(f"- {k}：{v}" for k, v in targets.items())
                rule_parts.append(f"### 本体裁量化靶值（按此带，非通用默认）\n{tv_lines}")
            for block in kr.genre_boundaries(genre_tags):
                rule_parts.append(block[:900])
            if rule_parts:
                context_info += "\n\n## 改写须遵守的规则基线\n" + "\n\n".join(rule_parts)
        except Exception:
            pass

        return context_info, genre_tags


    # ==================================================================
    # 阶段 2：定点改写
    # ==================================================================

    async def _rewrite_paragraphs(self, content: str, platform: str, evidence: dict) -> dict:
        """只改有证据的段落，逐段返回，带长度漂移护栏."""
        paragraphs: list[str] = evidence["paragraphs"]
        para_evidence: dict[int, list[dict]] = evidence["para_evidence"]
        chapter_evidence: list[dict] = evidence["chapter_evidence"]

        # 组装「问题段落 + 证据」块
        target_blocks: list[str] = []
        for idx in sorted(para_evidence.keys()):
            if idx >= len(paragraphs):
                continue
            ev_lines = "\n".join(
                f"  - [{e['code']}|{e['source']}] {e['message']}"
                + (f" → 建议: {e['suggestion']}" if e.get("suggestion") else "")
                for e in para_evidence[idx]
            )
            target_blocks.append(
                f"【段落 {idx}】\n原文：{paragraphs[idx].strip()}\n证据：\n{ev_lines}"
            )

        if not target_blocks:
            return self._empty_rewrite(content)

        chapter_hint = ""
        if chapter_evidence:
            lines = "\n".join(f"- [{e['source']}] {e['message']}" for e in chapter_evidence[:15])
            chapter_hint = f"\n## 章节级约束（改写时整体注意，不针对单段）\n{lines}\n"

        user_prompt = f"""请按证据修改下列段落。平台: {platform}
{evidence['context_info']}
{chapter_hint}
## 待修改段落（共 {len(target_blocks)} 段）
{chr(10).join(target_blocks)}

严格按 JSON 格式返回 revisions（只含你实际修改的段落）。"""

        try:
            resp = await self._kernel.call_llm(
                [
                    {"role": "system", "content": REWRITE_SYSTEM},
                    {"role": "user", "content": user_prompt},
                ],
                tier="standard",
                max_tokens=8192,
                temperature=0.4,
                response_format={"type": "json_object"},
            )
            parsed = self._parse_json_response(resp.get("content", "{}"))
        except Exception as e:
            logger.error("定点改写 LLM 调用失败", error=str(e))
            return self._empty_rewrite(content, summary=f"改写失败: {str(e)[:80]}")

        revisions = []
        if isinstance(parsed, dict):
            revisions = parsed.get("revisions", [])
        elif isinstance(parsed, list):
            revisions = parsed
        if not isinstance(revisions, list):
            revisions = []

        # 应用改写 + 护栏
        new_paras, changes, rejected = self._apply_revisions(
            paragraphs, revisions, para_evidence, require_evidence=True,
        )

        optimized = "\n\n".join(new_paras)
        diff_items = self._build_diff_items(content, optimized, None)
        summary = f"定点修改 {len(changes)} 段"
        if rejected:
            summary += f"（护栏拒绝 {rejected} 段超幅改写）"

        return {
            "step": "coach",
            "name": "定点改写",
            "original": content,
            "optimized": optimized,
            "changes": changes,
            "diff_items": diff_items,
            "summary": summary,
        }

    @staticmethod
    def _apply_revisions(
        paragraphs: list[str],
        revisions: list,
        allowed: dict[int, list[dict]],
        *,
        require_evidence: bool = True,
    ) -> tuple[list[str], list[dict], int]:
        """把 LLM 返回的 revisions 应用到段落，带证据校验 + 长度漂移护栏.

        Returns: (new_paras, changes, rejected_count)
            changes 每条: {paragraph_index, before, after, applied_fixes, evidence}
        不变量：revised_text 内部换行被压平，保证一段仍是一段（不破坏 \\n\\n 结构）。
        """
        new_paras = list(paragraphs)
        changes: list[dict] = []
        rejected = 0
        for rev in revisions:
            if not isinstance(rev, dict):
                continue
            idx_raw = rev.get("paragraph_index")
            revised = rev.get("revised_text", "")
            if not isinstance(idx_raw, int) or idx_raw < 0 or idx_raw >= len(paragraphs):
                continue
            idx = idx_raw
            if require_evidence and idx not in allowed:
                continue  # 只接受有证据的段落改动
            orig = paragraphs[idx]
            # 压平段内换行，维持「一段=一段」不变量
            revised = re.sub(r"\s*\n\s*", " ", revised).strip() if isinstance(revised, str) else ""
            if not revised or revised == orig.strip():
                continue
            # 护栏：长度漂移 > ±25% 拒绝
            o_len = max(len(orig.strip()), 1)
            drift = abs(len(revised) - o_len) / o_len
            if drift > 0.25:
                rejected += 1
                logger.info("改写护栏拒绝段落 %d：长度漂移 %.0f%%", idx, drift * 100)
                continue
            new_paras[idx] = revised
            ev = allowed.get(idx, [])
            codes = rev.get("applied_fixes") or [e.get("code", "") for e in ev]
            changes.append({
                "paragraph_index": idx,
                "before": orig.strip(),
                "after": revised,
                "applied_fixes": codes,
                "evidence": ev,
            })
        return new_paras, changes, rejected

    @staticmethod
    def _empty_rewrite(content: str, summary: str = "无需改动") -> dict:
        return {
            "step": "coach", "name": "定点改写",
            "original": content, "optimized": content,
            "changes": [], "diff_items": [], "summary": summary,
        }


    # ==================================================================
    # 阶段 3：重测 + 条件降重
    # ==================================================================

    async def _detect_reduce(
        self, content: str, platform: str,
        ai_threshold: float = 0.2, humanize_mode: str = "unified",
        prev_ai_detection: dict | None = None,
    ) -> dict:
        """AI检测降重 — 逐段检测命中→只对命中段落定点降重（保留段落结构）.

        不再做全文盲重写：那会打乱段落结构（导致前端对齐错乱）且把文笔改差。
        改为：① 全文 AI 率达标则跳过；② 否则逐段检测，挑出 AI 痕迹最重的段落，
        连同命中的具体模式一次性喂给 LLM 定点重写；③ 每段带证据生成 change 记录。
        """
        empty = {
            "step": "detect", "name": "AI检测降重",
            "original": content, "optimized": content, "changes": [],
            "ai_score_before": 0, "ai_score_after": 0, "diff_items": [],
            "summary": "无需降重", "reduction_applied": False,
        }
        try:
            # prev_ai_detection["ai_score"] 是人类度（越高越像人类），换算成 AI 率再比阈值
            ai_rate_before = None
            if prev_ai_detection and prev_ai_detection.get("ai_score") is not None:
                ai_rate_before = round(1.0 - float(prev_ai_detection["ai_score"]), 3)
            if ai_rate_before is not None and ai_rate_before <= ai_threshold:
                empty["ai_score_before"] = empty["ai_score_after"] = ai_rate_before
                empty["summary"] = f"AI率 {ai_rate_before:.0%}，未超阈值 {ai_threshold:.0%}，无需降重"
                return empty

            entry = await self._kernel.get_plugin("anti-ai-detection")
            detector = entry.instance if entry else None
            if detector is None:
                empty["summary"] = "anti-ai-detection 插件未加载，跳过降重"
                return empty

            # 全文实测 AI 率（无 prev 时兜底）
            whole = await detector.detect(content)
            ai_rate_before = round(1.0 - whole.get("ai_score", 1.0), 3)
            if ai_rate_before <= ai_threshold:
                empty["ai_score_before"] = empty["ai_score_after"] = ai_rate_before
                empty["summary"] = f"AI率 {ai_rate_before:.0%}，未超阈值 {ai_threshold:.0%}，无需降重"
                return empty

            # 逐段检测，挑出 AI 痕迹段落（人类度越低越靠前），最多 6 段
            paragraphs = content.split("\n\n")
            scored: list[tuple[float, int, list[str]]] = []
            for i, p in enumerate(paragraphs):
                ps = p.strip()
                if len(ps) < 20:  # 太短的段（单句对话/拟声）不单独降重，噪声大
                    continue
                pd = await detector.detect(ps)
                p_ai = 1.0 - pd.get("ai_score", 1.0)
                if p_ai <= ai_threshold:
                    continue
                hits = [m.get("category", "") for m in pd.get("pattern_matches", [])][:6]
                scored.append((p_ai, i, hits))
            scored.sort(reverse=True, key=lambda t: t[0])
            targets = scored[:6]

            if not targets:
                empty["ai_score_before"] = empty["ai_score_after"] = ai_rate_before
                empty["summary"] = f"AI率 {ai_rate_before:.0%}，但无单段超标，跳过定点降重"
                return empty

            # 组装「AI 段落 + 命中模式」块
            ai_evidence: dict[int, list[dict]] = {}
            blocks = []
            for p_ai, idx, hits in targets:
                hit_str = "、".join(h for h in hits if h) or "句式/词汇AI腔"
                ai_evidence[idx] = [{
                    "code": "anti_ai.detect", "source": "AI检测降重",
                    "message": f"本段AI率 {p_ai:.0%}，命中：{hit_str}",
                    "suggestion": "重写消除AI腔",
                }]
                blocks.append(
                    f"【段落 {idx}】（AI率 {p_ai:.0%}）\n原文：{paragraphs[idx].strip()}\n命中模式：{hit_str}"
                )

            user_prompt = (f"请重写下列 AI 痕迹偏重的段落。平台: {platform}\n\n"
                           f"## 待重写段落（共 {len(blocks)} 段）\n" + "\n\n".join(blocks)
                           + "\n\n严格按 JSON 返回 revisions（只含你实际重写的段落）。")
            resp = await self._kernel.call_llm(
                [{"role": "system", "content": REDUCE_SYSTEM},
                 {"role": "user", "content": user_prompt}],
                tier="standard", max_tokens=8192, temperature=0.6,
                response_format={"type": "json_object"},
            )
            parsed = self._parse_json_response(resp.get("content", "{}"))
            revisions = parsed.get("revisions", []) if isinstance(parsed, dict) else (
                parsed if isinstance(parsed, list) else [])

            new_paras, changes, rejected = self._apply_revisions(
                paragraphs, revisions, ai_evidence, require_evidence=True,
            )
            reduced = "\n\n".join(new_paras)

            if reduced == content:
                empty["ai_score_before"] = empty["ai_score_after"] = ai_rate_before
                empty["summary"] = f"AI率 {ai_rate_before:.0%}，降重未产生有效改动"
                return empty

            after = await detector.detect(reduced)
            ai_rate_after = round(1.0 - after.get("ai_score", 1.0 - ai_rate_before), 3)
            summary = f"AI率 {ai_rate_before:.0%} → {ai_rate_after:.0%}，定点降重 {len(changes)} 段"
            if rejected:
                summary += f"（护栏拒绝 {rejected} 段）"

            return {
                "step": "detect", "name": "AI检测降重",
                "original": content, "optimized": reduced, "changes": changes,
                "ai_score_before": ai_rate_before, "ai_score_after": ai_rate_after,
                "diff_items": self._build_diff_items(content, reduced, None),
                "summary": summary, "reduction_applied": True,
            }
        except Exception as e:
            logger.error("AI检测降重失败", error=str(e))
            empty["summary"] = f"降重失败: {str(e)[:100]}"
            empty["error"] = str(e)
            return empty


    # ==================================================================
    # 报告：全部用实测值构建（不让 LLM 编分数）
    # ==================================================================

    def _build_grounded_report(
        self, evidence: dict, after_report: dict, original: str, optimized: str,
    ) -> dict:
        """对比 before/after 实测报告 + 改写记录，构建可追溯的解释报告.

        分数来自分析器实算的 overall_score；问题来自真实检测命中；
        改进来自 before/after 的 issue 差集与 AI 率变化。
        """
        before = evidence["baseline_report"]
        b_score = round(before.get("overall_score", 0) * 100)
        a_score = round(after_report.get("overall_score", b_score / 100) * 100)
        b_grade = before.get("grade", "?")
        a_grade = after_report.get("grade", b_grade)

        # before 的真实问题
        before_issues = [i.get("message", "") for i in before.get("issues", []) if i.get("message")]

        # 改进 = 消失的问题（按 message 差集）+ AI 率变化
        after_msgs = {i.get("message", "") for i in after_report.get("issues", [])}
        resolved = [m for m in before_issues if m and m not in after_msgs]
        improvements: list[str] = []
        b_ai = before.get("ai_detection", {}).get("ai_score")
        a_ai = after_report.get("ai_detection", {}).get("ai_score")
        if b_ai is not None and a_ai is not None and a_ai > b_ai:
            improvements.append(f"AI人类度 {b_ai:.0%} → {a_ai:.0%}")
        if resolved:
            improvements.append(f"消除 {len(resolved)} 个检测问题")
        # 低分维度回升
        b_dims = before.get("dimension_scores", {})
        a_dims = after_report.get("dimension_scores", {})
        for k, bv in b_dims.items():
            av = a_dims.get(k, bv)
            if av > bv:
                improvements.append(f"{DIM_NAMES.get(k, k)} {bv}→{av}")

        # 收集改写 changes（定点改写 + AI降重，均由 run_pipeline 回填到 evidence）
        # 同段被两阶段都改过时，以降重后的 after 为准，证据合并
        changes: list[dict] = []
        merged: dict[int, dict] = {}
        for stage_changes in (evidence.get("_rewrite_changes", []), evidence.get("_detect_changes", [])):
            for ch in stage_changes:
                pi = ch.get("paragraph_index")
                ev_list = ch.get("evidence") or []
                if pi in merged:
                    # 第二阶段又改了这段：更新 after 片段，累加证据
                    merged[pi]["_after"] = ch.get("after", "") or merged[pi]["_after"]
                    merged[pi]["_ev"].extend(ev_list)
                    merged[pi]["_fixes"].extend(ch.get("applied_fixes") or [])
                else:
                    merged[pi] = {
                        "_before": ch.get("before", "") or "",
                        "_after": ch.get("after", "") or "",
                        "_ev": list(ev_list),
                        "_fixes": list(ch.get("applied_fixes") or []),
                    }

        for pi in sorted(k for k in merged if k is not None):
            m = merged[pi]
            ev_list = m["_ev"]
            ev0 = ev_list[0] if ev_list else {}
            code0 = (m["_fixes"] or [ev0.get("code", "")])[0]
            reasons = list(dict.fromkeys(e.get("message", "") for e in ev_list if e.get("message")))
            sources = list(dict.fromkeys(e.get("source", "") for e in ev_list if e.get("source")))
            changes.append({
                "paragraph_index": pi,
                "original_snippet": (m["_before"])[:60],
                "optimized_snippet": (m["_after"])[:60],
                "reason": "；".join(reasons) if reasons else "按证据修改",
                "evidence_source": "、".join(s for s in sources if s),
                "evidence": [
                    {"source": e.get("source", ""), "message": e.get("message", ""),
                     "suggestion": e.get("suggestion", ""), "code": e.get("code", "")}
                    for e in ev_list
                ],
                "type": _code_to_type(code0),
            })

        # 文字结论 — 全部基于真实数字模板化生成（不调 LLM，零幻觉）
        n_changed = len(changes)
        delta = a_score - b_score
        if n_changed == 0:
            summary = f"未发现需修改段落，质量评分 {b_score} 分（{b_grade}），正文保持原样。"
            comparison = "本次未做改动，原文已达当前规则基线。"
            recommendation = "保留原文。"
        else:
            trend = f"提升 {delta} 分" if delta > 0 else (f"下降 {abs(delta)} 分" if delta < 0 else "评分持平")
            summary = (f"基于规则/门禁/AI检测的真实命中，定点修改 {n_changed} 段；"
                       f"综合评分 {b_score}→{a_score}（{trend}）。")
            comparison = (f"优化前 {b_score} 分（{b_grade}），优化后 {a_score} 分（{a_grade}）。"
                          + (f"已解决：{ '；'.join(resolved[:3]) }。" if resolved else ""))
            recommendation = "建议采用优化版。" if delta >= 0 else "评分未提升，建议人工复核后再决定是否采用。"

        return {
            "summary": summary,
            "quality_before": {"score": b_score, "grade": b_grade, "issues": before_issues[:8]},
            "quality_after": {"score": a_score, "grade": a_grade, "improvements": improvements[:8]},
            "changes": changes[:50],
            "comparison": comparison,
            "recommendation": recommendation,
            "grounded": True,  # 标记：分数源自实测，非 LLM 生成
        }


    # ==================================================================
    # 工具方法
    # ==================================================================

    @staticmethod
    def _parse_json_response(text: str) -> Any:
        """从 LLM 响应中提取并解析 JSON，处理代码块包裹、尾逗号、截断等问题."""
        if not text:
            return {}
        text = re.sub(r'```(?:json)?\s*', '', text)
        text = re.sub(r'```\s*', '', text).strip()
        # 直接解析
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass
        # 按括号配对提取第一个完整对象/数组
        for start_char, end_char in (('{', '}'), ('[', ']')):
            start = text.find(start_char)
            if start < 0:
                continue
            depth = 0
            for i in range(start, len(text)):
                if text[i] == start_char:
                    depth += 1
                elif text[i] == end_char:
                    depth -= 1
                    if depth == 0:
                        candidate = re.sub(r',\s*([}\]])', r'\1', text[start:i + 1])
                        try:
                            return json.loads(candidate)
                        except json.JSONDecodeError:
                            break
        # 兜底：清理尾逗号后整体解析
        try:
            return json.loads(re.sub(r',\s*([}\]])', r'\1', text))
        except json.JSONDecodeError:
            pass
        logger.warning("无法解析 LLM 返回的 JSON", preview=text[:200])
        return {}

    async def _run_gate_chain(self, content: str, project_id: str) -> list[dict]:
        """运行所有 IQualityGate 插件（含 skill builder 创建的），返回门禁结果."""
        from core.quality_gate import GateChainConfig, GateChainExecutor, IQualityGate
        try:
            gates = []
            for entry in await self._kernel._plugin_manager.list_active():
                if isinstance(entry.instance, IQualityGate):
                    gates.append(entry.instance)
            if not gates:
                return []
            # 只跑一轮采集证据：不自动修订（修订交给定点改写阶段），避免 LLM 门禁重复执行
            chain = GateChainExecutor(GateChainConfig(gates=gates, max_revision_rounds=1))

            async def _no_revise(chapter: dict, results: list) -> dict:
                return chapter

            gate_result = await chain.execute(
                {"content": content, "project_id": project_id},
                {"project_id": project_id},
                on_revise=_no_revise,
            )
            return [
                {"gate": g.gate_name, "verdict": g.verdict.value, "score": g.score,
                 "issues": [{"severity": i.severity.value, "code": i.code,
                             "message": i.message, "suggestion": i.suggestion}
                            for i in g.issues]}
                for g in gate_result.gate_results
            ]
        except Exception as e:
            logger.warning(f"门禁链执行失败: {e}")
            return []

    async def _run_contributors(self, content: str, project_id: str, chapter_num: int, platform: str) -> list[dict]:
        """并行运行所有 IPipelineContributor 插件（含 skill builder 创建的）."""
        import asyncio

        from core.quality_gate import IPipelineContributor
        try:
            contributors = []
            for entry in await self._kernel._plugin_manager.list_active():
                if isinstance(entry.instance, IPipelineContributor):
                    contributors.append(entry.instance)
            if not contributors:
                return []
            ctx = {"project_id": project_id, "chapter_num": chapter_num,
                   "platform": platform, "kernel": self._kernel}
            tasks = [c.analyze(content, ctx) for c in contributors]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            output: list[dict] = []
            for c, r in zip(contributors, results, strict=False):
                if isinstance(r, BaseException):
                    logger.warning(f"贡献者 {c.name} 执行失败: {r}")
                    continue
                r["name"] = c.name
                output.append(r)
            return output
        except Exception as e:
            logger.warning(f"贡献者执行失败: {e}")
            return []

    def _rule_based_check(self, content: str) -> list[dict]:
        """基于规则的预检（不调用 LLM），命中项带 paragraph_index."""
        issues = []
        paragraphs = content.split("\n\n")

        ai_openings = [
            (r'^在.{2,15}中，', "AI模板开头「在...中」"),
            (r'^随着.{2,20}，', "AI模板开头「随着...」"),
            (r'^当.{2,15}时，', "AI模板开头「当...时」"),
            (r'^在这个.{2,10}', "AI模板开头「在这个...」"),
        ]
        emotion_labels = ["心中充满了", "内心涌起", "不禁感到", "心中一阵"]

        for i, para in enumerate(paragraphs):
            para = para.strip()
            if not para:
                continue
            if len(para) > 500:
                issues.append({
                    "paragraph_index": i, "type": "poison", "severity": "low",
                    "description": f"段落过长（{len(para)}字），可能影响阅读体验",
                    "suggestion": "考虑拆分为多个段落",
                })
            for pattern, desc in ai_openings:
                if re.match(pattern, para):
                    issues.append({
                        "paragraph_index": i, "type": "ai_taste", "severity": "medium",
                        "description": desc, "suggestion": "换一个更自然的开头方式",
                    })
                    break
            for label in emotion_labels:
                if label in para:
                    issues.append({
                        "paragraph_index": i, "type": "ai_taste", "severity": "low",
                        "description": f"情感标签化表达「{label}」",
                        "suggestion": "用具体行为或细节替代情感标签",
                    })

        return issues

    def _build_diff_items(self, original: str, optimized: str, annotations: list | None) -> list:
        """构建段落级 diff items 供前端渲染."""
        orig_paras = original.split("\n\n")
        opt_paras = optimized.split("\n\n")
        items = []
        max_len = max(len(orig_paras), len(opt_paras))
        for i in range(max_len):
            op = orig_paras[i].strip() if i < len(orig_paras) else ""
            fp = opt_paras[i].strip() if i < len(opt_paras) else ""
            if not op and not fp:
                continue
            items.append({
                "index": i, "orig": op, "final": fp,
                "isChanged": op != fp, "reason": "",
            })
        return items





