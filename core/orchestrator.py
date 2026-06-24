"""编排引擎 — 管理章节生成的完整流水线。

状态机:
    IDLE → CONTEXT_ASSEMBLY → DRAFTING → STYLE_ADAPT
    → CONSISTENCY_CHECK → ANTI_AI_CHECK → GATE_CHAIN
    → { ACCEPTED | REVISION_NEEDED | FAILED }
    → MEMORY_UPDATE → IDLE (下一章)

用法:
    engine = OrchestrationEngine(kernel, gate_chain, writer_plugin, ...)
    chapter = await engine.generate_chapter(project_id, chapter_number)
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from core.logging_config import get_logger
from core.quality_gate import GateChainExecutor, GateChainResult, GateResult, GateVerdict

logger = get_logger(__name__)


class ChapterPipelineState(str, Enum):
    IDLE = "idle"
    CONTEXT_ASSEMBLY = "context_assembly"
    DRAFTING = "drafting"
    STYLE_ADAPT = "style_adapt"
    CONSISTENCY_CHECK = "consistency_check"
    ANTI_AI_CHECK = "anti_ai_check"
    GATE_CHAIN = "gate_chain"
    REVISION = "revision"
    MEMORY_UPDATE = "memory_update"
    ACCEPTED = "accepted"
    FAILED = "failed"


@dataclass
class PipelineContext:
    project_id: str = ""
    chapter_number: int = 0
    volume_number: int = 1
    state: ChapterPipelineState = ChapterPipelineState.IDLE
    revision_round: int = 0
    max_revisions: int = 3

    # 组装后的上下文
    outline_node: dict[str, Any] | None = None
    rag_results: list[dict[str, Any]] = field(default_factory=list)
    writing_tips: list[dict[str, Any]] = field(default_factory=list)
    active_foreshadows: list[dict[str, Any]] = field(default_factory=list)
    relevant_facts: list[dict[str, Any]] = field(default_factory=list)
    style_profile: dict[str, Any] = field(default_factory=dict)
    consistency_ledger: dict[str, Any] = field(default_factory=dict)

    # 产出
    draft_content: str = ""
    gate_results: list[dict[str, Any]] = field(default_factory=list)

    correlation_id: str = field(default_factory=lambda: f"pipe_{uuid.uuid4().hex[:8]}")


class OrchestrationEngine:
    """章节生成编排引擎 — 协调所有插件完成单章生成流水线."""

    def __init__(
        self,
        kernel: Any,
        gate_chain: GateChainExecutor | None = None,
    ) -> None:
        self._kernel = kernel
        self._gate_chain = gate_chain
        self._pipelines: dict[str, PipelineContext] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def generate_chapter(
        self,
        project_id: str,
        chapter_number: int,
        *,
        volume_number: int = 1,
        auto_retry: bool = True,
    ) -> dict[str, Any]:
        """执行单章生成流水线.

        Returns:
            {"chapter": Chapter, "pipeline": PipelineContext}
        """
        ctx = PipelineContext(
            project_id=project_id,
            chapter_number=chapter_number,
            volume_number=volume_number,
        )
        key = f"{project_id}:v{volume_number}:ch{chapter_number}"
        self._pipelines[key] = ctx

        try:
            # ---- Stage 1: 上下文组装 ----
            ctx.state = ChapterPipelineState.CONTEXT_ASSEMBLY
            await self._assemble_context(ctx)

            # ---- Stage 2: 正文撰写 ----
            ctx.state = ChapterPipelineState.DRAFTING
            chapter = await self._draft_chapter(ctx)
            ctx.draft_content = chapter.get("content", "")

            # ---- Stage 3: 风格适配 ----
            ctx.state = ChapterPipelineState.STYLE_ADAPT
            chapter = await self._adapt_style(chapter, ctx)

            # ---- Stage 4-6: 质量门禁 ----
            if self._gate_chain:
                ctx.state = ChapterPipelineState.GATE_CHAIN
                gate_result = await self._run_gate_chain(chapter, ctx, auto_retry)
                if not gate_result.passed and not auto_retry:
                    ctx.state = ChapterPipelineState.FAILED
                    return {"chapter": chapter, "pipeline": ctx}
                elif not gate_result.passed:
                    ctx.state = ChapterPipelineState.FAILED
                    logger.warning("门禁未通过 (已达重试上限)", chapter=chapter_number)
                else:
                    ctx.state = ChapterPipelineState.ACCEPTED
            else:
                ctx.state = ChapterPipelineState.ACCEPTED

            # ---- Stage 7: 记忆更新 ----
            ctx.state = ChapterPipelineState.MEMORY_UPDATE
            await self._update_memory(chapter, ctx)

            ctx.state = ChapterPipelineState.ACCEPTED
            return {"chapter": chapter, "pipeline": ctx}

        except Exception:
            ctx.state = ChapterPipelineState.FAILED
            logger.exception("章节生成失败", chapter=chapter_number)
            raise

    async def get_pipeline_status(self, project_id: str, chapter_number: int, volume_number: int = 1) -> PipelineContext | None:
        """获取流水线状态."""
        key = f"{project_id}:v{volume_number}:ch{chapter_number}"
        return self._pipelines.get(key)

    # ------------------------------------------------------------------
    # Pipeline Stages
    # ------------------------------------------------------------------

    # 篇幅 → 方法论包映射
    _METHOD_PACKS = {
        "short": "short-story-writing",
        "medium": "novel-writing",
        "long": "novel-templates",
        "extra_long": "novel-templates",
    }
    # 通用写作技巧包（所有篇幅都注入）
    _UNIVERSAL_PACKS = ["writing-master", "writing-tutorial", "writing-workflow", "novel-writing-skills"]

    # 题材标签 → genre_skills 目录映射
    _GENRE_SKILL_MAP = {
        "都市": "都市职场", "职场": "都市职场",
        "悬疑": "悬疑推理", "推理": "悬疑推理",
        "都市悬疑": "都市悬疑",
        "科幻": "AI科幻", "AI": "AI科幻",
        "太空": "太空科幻",
        "赛博朋克": "赛博庞克", "赛博": "赛博庞克",
        "言情": "女频爱情", "女频": "女频爱情", "爱情": "女频爱情",
        "异能": "异能志怪", "志怪": "异能志怪", "灵异": "异能志怪",
    }

    async def _load_method_pack(self, kernel: Any, project_id: str) -> list[dict[str, Any]]:
        """根据项目篇幅加载方法论包 + 通用写作技巧包 + 题材技能包."""
        try:
            from pathlib import Path
            # 获取项目信息
            length = "long"
            genre_tags: list[str] = []
            if kernel.db:
                meta = await kernel.db.get_project(project_id)
                if meta:
                    length = meta.get("length", "long")
                    import json
                    tags_raw = meta.get("genre_tags", "[]")
                    genre_tags = json.loads(tags_raw) if isinstance(tags_raw, str) else tags_raw

            results = []
            # 篇幅方法论包
            pack_name = self._METHOD_PACKS.get(length, "novel-templates")
            results.extend(self._read_pack_content(pack_name))
            # 通用写作技巧包
            for name in self._UNIVERSAL_PACKS:
                results.extend(self._read_pack_content(name))
            # 题材技能包
            results.extend(self._load_genre_skills(genre_tags))
            return results
        except Exception:
            return []

    @staticmethod
    def _load_genre_skills(genre_tags: list[str]) -> list[dict[str, Any]]:
        """根据题材标签加载 genre_skills 内容."""
        from pathlib import Path
        loaded_dirs = set()
        results = []
        for tag in genre_tags:
            dir_name = OrchestrationEngine._GENRE_SKILL_MAP.get(tag)
            if not dir_name or dir_name in loaded_dirs:
                continue
            loaded_dirs.add(dir_name)
            skill_dir = Path("knowledge_base/genre_skills") / dir_name
            if not skill_dir.exists():
                continue
            # 读取创建小说正文 prompt（核心题材规则）
            prompt_file = skill_dir / ".github" / "prompts" / "创建小说正文.prompt.md"
            if prompt_file.exists():
                text = prompt_file.read_text(encoding="utf-8").strip()
                if text:
                    # 截取关键部分（跳过 YAML front matter）
                    if "---" in text[1:]:
                        parts = text.split("---", 2)
                        text = parts[2].strip() if len(parts) > 2 else text
                    results.append({"content": text[:2000], "category": "writing_tip", "metadata": {"source": f"genre:{dir_name}", "file": "创建小说正文.prompt.md"}})
        return results

    @staticmethod
    def _read_pack_content(pack_name: str) -> list[dict[str, Any]]:
        """读取单个知识包的 .md 内容."""
        from pathlib import Path
        pack_dir = Path("knowledge_base/packs") / pack_name / "content"
        if not pack_dir.exists():
            return []
        results = []
        for f in sorted(pack_dir.glob("*.md")):
            text = f.read_text(encoding="utf-8").strip()
            if text:
                results.append({"content": text, "category": "writing_tip", "metadata": {"source": f"pack:{pack_name}", "file": f.name}})
        return results

    async def _assemble_context(self, ctx: PipelineContext) -> None:
        """组装章节上下文."""
        kernel = self._kernel

        # 获取大纲节点 — 同时匹配 volume_number 和 chapter_number
        try:
            progress_data = await kernel.context().get(f"project:{ctx.project_id}", "progress")
            if progress_data:
                for vol in progress_data.get("volumes", []):
                    if vol.get("volume_number") != ctx.volume_number:
                        continue
                    for ch in vol.get("chapters", []):
                        if ch.get("chapter_number") == ctx.chapter_number:
                            ctx.outline_node = ch
                            break
                    if ctx.outline_node:
                        break
        except Exception:
            pass

        # RAG 检索
        try:
            rag = await kernel.rag_retrieve(
                query=f"chapter {ctx.chapter_number} context",
                project_id=ctx.project_id,
                top_k=4,
            )
            ctx.rag_results = rag
        except Exception:
            pass

        # 写作技巧检索（知识包）
        try:
            node_summary = ""
            if ctx.outline_node:
                node_summary = ctx.outline_node.get("summary", "")
            tip_query = node_summary or f"第{ctx.chapter_number}章写作技巧"
            tips = await kernel.rag_retrieve(
                query=tip_query,
                project_id=ctx.project_id,
                top_k=3,
                categories=["writing_tip"],
            )
            # 按篇幅注入方法论包
            method_tips = await self._load_method_pack(kernel, ctx.project_id)
            ctx.writing_tips = method_tips + tips
        except Exception:
            ctx.writing_tips = []

        # 活跃伏笔（从文件读取，ContextManager 中无持久化数据）
        try:
            raw = await kernel.read_project_file(ctx.project_id, "foreshadows.json")
            foreshadows = json.loads(raw)
            if foreshadows:
                ctx.active_foreshadows = [
                    fs for fs in foreshadows.get("entries", {}).values()
                    if isinstance(fs, dict) and fs.get("status") in ("planted", "building")
                ]
        except (FileNotFoundError, json.JSONDecodeError):
            pass

        # 事实账本
        try:
            facts = await kernel.context().get(f"project:{ctx.project_id}", "facts")
            if facts:
                ctx.relevant_facts = list(facts.get("entries", {}).values())[-20:]  # 最近20条
        except Exception:
            pass

        # 一致性账本
        try:
            raw = await kernel.read_project_file(ctx.project_id, "consistency_ledger.json")
            ctx.consistency_ledger = json.loads(raw)
        except (FileNotFoundError, json.JSONDecodeError):
            ctx.consistency_ledger = {}

        logger.info("上下文已组装", chapter=ctx.chapter_number, foreshadows=len(ctx.active_foreshadows))

    async def _draft_chapter(self, ctx: PipelineContext) -> dict[str, Any]:
        """调用 ChapterWriter 生成正文."""
        kernel = self._kernel
        chapter_writer = await kernel.get_plugin("chapter-writer")

        # 构建上下文 — 使用卷+章组合键获取近6章摘要
        prev_summary = ""
        if ctx.chapter_number > 1:
            summaries = []
            for n in range(max(1, ctx.chapter_number - 6), ctx.chapter_number):
                s = await kernel.context().get(
                    f"project:{ctx.project_id}", f"summary_vol{ctx.volume_number}_ch{n}", ""
                )
                if s:
                    summaries.append(f"第{n}章: {s}")
            if summaries:
                prev_summary = "【近期章节摘要】\n" + "\n".join(summaries)
        writer_context = {
            "settings": await kernel.context().get_namespace(f"project:{ctx.project_id}"),
            "characters": await kernel.context().get(f"project:{ctx.project_id}", "characters", {}),
            "previous_chapters_summary": prev_summary,
            "rag_results": ctx.rag_results,
            "writing_tips": ctx.writing_tips,
            "active_foreshadows": ctx.active_foreshadows,
            "consistency_ledger": ctx.consistency_ledger,
        }

        chapter = await chapter_writer.instance.write_chapter(
            chapter_node=ctx.outline_node or {
                "chapter_number": ctx.chapter_number,
                "title": f"第{ctx.chapter_number}章",
            },
            context=writer_context,
            platform=await kernel.context().get(f"project:{ctx.project_id}", "platform", "fanqie"),
        )
        return {
            "chapter_id": chapter.metadata.chapter_id,
            "chapter_number": chapter.metadata.chapter_number,
            "content": chapter.content,
            "metadata": chapter.metadata.model_dump(),
        }

    async def _adapt_style(self, chapter: dict, ctx: PipelineContext) -> dict:
        """调用 StyleAdapter 做风格适配."""
        kernel = self._kernel
        platform = await kernel.context().get(f"project:{ctx.project_id}", "platform", "fanqie")

        try:
            style_adapter = await kernel.get_plugin("style-adapter")
            adapted = await style_adapter.instance.adapt_style(
                content=chapter["content"],
                platform=platform,
                mode="polish",
            )
            chapter["content"] = adapted
        except Exception:
            # 风格适配失败不阻塞
            logger.warning("风格适配跳过 (插件不可用)")

        return chapter

    async def _run_gate_chain(
        self,
        chapter: dict,
        ctx: PipelineContext,
        auto_retry: bool,
    ) -> GateChainResult:
        """运行质量门禁链."""

        async def revise_fn(ch: dict, results: list[GateResult]) -> dict:
            ctx.state = ChapterPipelineState.REVISION
            ctx.revision_round += 1
            suggestions = []
            for r in results:
                for issue in r.issues:
                    if issue.suggestion:
                        suggestions.append(issue.suggestion)

            # 调用 ChapterWriter 修订
            kernel = self._kernel
            cw = await kernel.get_plugin("chapter-writer")
            revised = await cw.instance.revise_chapter(
                chapter=ch,
                revision_instructions=suggestions,
                context={
                    "settings": await kernel.context().get_namespace(f"project:{ctx.project_id}"),
                },
                platform=await kernel.context().get(f"project:{ctx.project_id}", "platform", "fanqie"),
            )
            return {
                "chapter_id": ch.get("chapter_id", ""),
                "chapter_number": ch.get("chapter_number", 0),
                "content": revised.content,
                "metadata": revised.metadata.model_dump(),
            }

        context = {
            "settings": await self._kernel.context().get_namespace(f"project:{ctx.project_id}"),
            "facts": await self._kernel.context().get(f"project:{ctx.project_id}", "facts", {}),
            "foreshadows": await self._kernel.context().get(f"project:{ctx.project_id}", "foreshadows", {}),
            "platform": await self._kernel.context().get(f"project:{ctx.project_id}", "platform", "fanqie"),
        }

        return await self._gate_chain.execute(chapter, context, revise_fn)

    async def _update_memory(self, chapter: dict, ctx: PipelineContext) -> None:
        """更新记忆 — 事实提取、伏笔更新、RAG 索引."""
        kernel = self._kernel
        ns = f"project:{ctx.project_id}"
        vol = ctx.volume_number
        ch = ctx.chapter_number

        # 保存章节摘要 — 使用卷+章组合键
        content = chapter.get("content", "")
        summary = content[:200] + "..." if len(content) > 200 else content
        await kernel.context().set(ns, f"summary_vol{vol}_ch{ch}", summary)

        # 更新进度
        await kernel.context().set(ns, "current_chapter", ch)
        await kernel.context().set(ns, "current_volume", vol)

        # 保存章节到文件（DB 保存由调用者负责，使用 auto_snapshot=True）
        chapter_id = chapter.get("chapter_id", f"ch_v{vol:02d}_{ch:04d}")
        await kernel.write_project_file(
            ctx.project_id,
            f"chapters/{chapter_id}.md",
            chapter.get("content", ""),
        )

        # 更新一致性账本
        await self._update_consistency_ledger(chapter, ctx)

        logger.info("记忆已更新", volume=vol, chapter=ch)

    async def _update_consistency_ledger(self, chapter: dict, ctx: PipelineContext) -> None:
        """更新一致性账本 — 提取事实、更新人物状态/时间线/世界状态."""
        kernel = self._kernel
        content = chapter.get("content", "")
        vol = ctx.volume_number
        ch = ctx.chapter_number

        # 读取现有账本
        ledger = ctx.consistency_ledger or {}
        if not ledger:
            ledger = {
                "chapter_summaries": {},
                "character_states": {},
                "timeline": [],
                "world_state": {"已揭示设定": [], "物品状态": {}, "悬念": []},
                "known_issues": [],
                "last_updated_ch": 0,
                "last_updated_vol": 1,
            }

        # 1. 保存本章摘要（比 _update_memory 的 200 字更完整）
        summary_text = content[:500] + "..." if len(content) > 500 else content
        ch_key = str(ch)
        ledger["chapter_summaries"][ch_key] = {
            "summary": summary_text,
            "volume": vol,
            "word_count": len(content),
        }

        # 2. 调用 consistency_checker 提取硬事实
        try:
            checker = await kernel.get_plugin("consistency-checker")
            facts = await checker.instance.extract_facts(content, ch)
            if facts:
                # 更新人物状态
                for fact in facts:
                    cat = fact.get("category", "")
                    subj = fact.get("subject", "")
                    if not subj:
                        continue
                    if cat == "character_state":
                        if subj not in ledger["character_states"]:
                            ledger["character_states"][subj] = {}
                        ledger["character_states"][subj]["status"] = fact.get("value", "")
                        ledger["character_states"][subj]["last_seen_ch"] = ch
                    elif cat == "location_state":
                        if subj not in ledger["character_states"]:
                            ledger["character_states"][subj] = {}
                        ledger["character_states"][subj]["location"] = fact.get("value", "")
                        ledger["character_states"][subj]["last_seen_ch"] = ch
                    elif cat == "relationship":
                        if subj not in ledger["character_states"]:
                            ledger["character_states"][subj] = {}
                        if "relationships" not in ledger["character_states"][subj]:
                            ledger["character_states"][subj]["relationships"] = {}
                        obj = fact.get("predicate", "").replace("与", "").replace("关系变为", "")
                        ledger["character_states"][subj]["relationships"][obj] = fact.get("value", "")
                    elif cat == "timeline":
                        ledger["timeline"].append({
                            "ch": ch, "event": fact.get("predicate", ""),
                            "time": fact.get("value", f"Ch{ch}"),
                        })
                    elif cat == "possession":
                        ws = ledger["world_state"]
                        if "物品状态" not in ws:
                            ws["物品状态"] = {}
                        ws["物品状态"][subj] = fact.get("value", "")

                logger.info("一致性账本事实已提取", chapter=ch, facts=len(facts))
        except Exception as exc:
            logger.warning("事实提取失败（降级为仅保存摘要）", error=str(exc))

        # 3. 更新进度指针
        ledger["last_updated_ch"] = ch
        ledger["last_updated_vol"] = vol

        # 4. 写回文件
        try:
            await kernel.write_project_file(
                ctx.project_id,
                "consistency_ledger.json",
                json.dumps(ledger, indent=2, ensure_ascii=False),
            )
            ctx.consistency_ledger = ledger
            logger.info("一致性账本已更新", volume=vol, chapter=ch)
        except Exception as exc:
            logger.warning("一致性账本写入失败", error=str(exc))


# ---------------------------------------------------------------------------
# 独立函数 — 供 stream.py 等非 orchestrator 路径调用
# ---------------------------------------------------------------------------


async def update_consistency_ledger_standalone(
    kernel: Any,
    project_id: str,
    chapter_content: str,
    chapter_number: int,
    volume_number: int,
) -> None:
    """独立的一致性账本更新函数（不依赖 PipelineContext）."""
    # 读取现有账本
    ledger = {}
    try:
        raw = await kernel.read_project_file(project_id, "consistency_ledger.json")
        ledger = json.loads(raw)
    except (FileNotFoundError, json.JSONDecodeError):
        pass

    if not ledger:
        ledger = {
            "chapter_summaries": {},
            "character_states": {},
            "timeline": [],
            "world_state": {"已揭示设定": [], "物品状态": {}, "悬念": []},
            "known_issues": [],
            "last_updated_ch": 0,
            "last_updated_vol": 1,
        }

    # 保存摘要
    summary_text = chapter_content[:500] + "..." if len(chapter_content) > 500 else chapter_content
    ch_key = str(chapter_number)
    ledger["chapter_summaries"][ch_key] = {
        "summary": summary_text,
        "volume": volume_number,
        "word_count": len(chapter_content),
    }

    # 提取事实
    try:
        checker = await kernel.get_plugin("consistency-checker")
        facts = await checker.instance.extract_facts(chapter_content, chapter_number)
        for fact in facts:
            cat = fact.get("category", "")
            subj = fact.get("subject", "")
            if not subj:
                continue
            if cat == "character_state":
                if subj not in ledger["character_states"]:
                    ledger["character_states"][subj] = {}
                ledger["character_states"][subj]["status"] = fact.get("value", "")
                ledger["character_states"][subj]["last_seen_ch"] = chapter_number
            elif cat == "location_state":
                if subj not in ledger["character_states"]:
                    ledger["character_states"][subj] = {}
                ledger["character_states"][subj]["location"] = fact.get("value", "")
                ledger["character_states"][subj]["last_seen_ch"] = chapter_number
            elif cat == "relationship":
                if subj not in ledger["character_states"]:
                    ledger["character_states"][subj] = {}
                if "relationships" not in ledger["character_states"][subj]:
                    ledger["character_states"][subj]["relationships"] = {}
                obj = fact.get("predicate", "").replace("与", "").replace("关系变为", "")
                ledger["character_states"][subj]["relationships"][obj] = fact.get("value", "")
            elif cat == "timeline":
                ledger["timeline"].append({
                    "ch": chapter_number, "event": fact.get("predicate", ""),
                    "time": fact.get("value", f"Ch{chapter_number}"),
                })
            elif cat == "possession":
                ws = ledger["world_state"]
                if "物品状态" not in ws:
                    ws["物品状态"] = {}
                ws["物品状态"][subj] = fact.get("value", "")
    except Exception as exc:
        logger.warning("独立账本事实提取失败", error=str(exc))

    ledger["last_updated_ch"] = chapter_number
    ledger["last_updated_vol"] = volume_number

    try:
        await kernel.write_project_file(
            project_id, "consistency_ledger.json",
            json.dumps(ledger, indent=2, ensure_ascii=False),
        )
        logger.info("独立账本已更新", volume=volume_number, chapter=chapter_number)
    except Exception as exc:
        logger.warning("独立账本写入失败", error=str(exc))
