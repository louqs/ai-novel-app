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
    state: ChapterPipelineState = ChapterPipelineState.IDLE
    revision_round: int = 0
    max_revisions: int = 3

    # 组装后的上下文
    outline_node: dict[str, Any] | None = None
    rag_results: list[dict[str, Any]] = field(default_factory=list)
    active_foreshadows: list[dict[str, Any]] = field(default_factory=list)
    relevant_facts: list[dict[str, Any]] = field(default_factory=list)
    style_profile: dict[str, Any] = field(default_factory=dict)

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
        auto_retry: bool = True,
    ) -> dict[str, Any]:
        """执行单章生成流水线.

        Returns:
            {"chapter": Chapter, "pipeline": PipelineContext}
        """
        ctx = PipelineContext(
            project_id=project_id,
            chapter_number=chapter_number,
        )
        key = f"{project_id}:{chapter_number}"
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

    async def get_pipeline_status(self, project_id: str, chapter_number: int) -> PipelineContext | None:
        """获取流水线状态."""
        key = f"{project_id}:{chapter_number}"
        return self._pipelines.get(key)

    # ------------------------------------------------------------------
    # Pipeline Stages
    # ------------------------------------------------------------------

    async def _assemble_context(self, ctx: PipelineContext) -> None:
        """组装章节上下文."""
        kernel = self._kernel

        # 获取大纲节点
        try:
            progress_data = await kernel.context().get(f"project:{ctx.project_id}", "progress")
            if progress_data:
                for vol in progress_data.get("volumes", []):
                    for ch in vol.get("chapters", []):
                        if ch.get("chapter_number") == ctx.chapter_number:
                            ctx.outline_node = ch
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

        # 活跃伏笔
        try:
            foreshadows = await kernel.context().get(f"project:{ctx.project_id}", "foreshadows")
            if foreshadows:
                ctx.active_foreshadows = [
                    fs for fs in foreshadows.get("entries", {}).values()
                    if isinstance(fs, dict) and fs.get("status") in ("planted", "building")
                ]
        except Exception:
            pass

        # 事实账本
        try:
            facts = await kernel.context().get(f"project:{ctx.project_id}", "facts")
            if facts:
                ctx.relevant_facts = list(facts.get("entries", {}).values())[-20:]  # 最近20条
        except Exception:
            pass

        logger.info("上下文已组装", chapter=ctx.chapter_number, foreshadows=len(ctx.active_foreshadows))

    async def _draft_chapter(self, ctx: PipelineContext) -> dict[str, Any]:
        """调用 ChapterWriter 生成正文."""
        kernel = self._kernel
        chapter_writer = await kernel.get_plugin("chapter-writer")

        # 构建上下文
        writer_context = {
            "settings": await kernel.context().get_namespace(f"project:{ctx.project_id}"),
            "characters": await kernel.context().get(f"project:{ctx.project_id}", "characters", {}),
            "previous_chapters_summary": await kernel.context().get(
                f"project:{ctx.project_id}", f"summary_ch_{ctx.chapter_number - 1}", ""
            ),
            "rag_results": ctx.rag_results,
            "active_foreshadows": ctx.active_foreshadows,
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

        # 保存章节摘要
        content = chapter.get("content", "")
        summary = content[:200] + "..." if len(content) > 200 else content
        await kernel.context().set(ns, f"summary_ch_{ctx.chapter_number}", summary)

        # 更新进度
        await kernel.context().set(ns, "current_chapter", ctx.chapter_number)

        # 保存章节
        chapter_id = chapter.get("chapter_id", f"ch_{ctx.chapter_number:04d}")
        await kernel.write_project_file(
            ctx.project_id,
            f"chapters/{chapter_id}.md",
            chapter.get("content", ""),
        )

        logger.info("记忆已更新", chapter=ctx.chapter_number)
