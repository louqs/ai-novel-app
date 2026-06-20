"""内置事件类型常量 — 规范事件命名空间.

事件命名约定: <domain>.<component>.<action>
例: pipeline.chapter.draft_complete
"""

from __future__ import annotations


class BuiltInEvents:
    """规范事件类型 — 全部以点号分隔的字符串标识."""

    # =========================================================================
    # System — 系统级事件
    # =========================================================================
    PLUGIN_LOADED = "system.plugin.loaded"
    PLUGIN_UNLOADED = "system.plugin.unloaded"
    CONFIG_CHANGED = "system.config.changed"
    SYSTEM_SHUTDOWN = "system.shutdown"

    # =========================================================================
    # Pipeline — 章节生成流水线阶段转换
    # =========================================================================
    PIPELINE_CHAPTER_START = "pipeline.chapter.start"
    PIPELINE_CONTEXT_ASSEMBLED = "pipeline.chapter.context_assembled"
    PIPELINE_DRAFT_COMPLETE = "pipeline.chapter.draft_complete"
    PIPELINE_STYLE_APPLIED = "pipeline.chapter.style_applied"
    PIPELINE_CONSISTENCY_DONE = "pipeline.chapter.consistency_done"
    PIPELINE_ANTI_AI_DONE = "pipeline.chapter.anti_ai_done"
    PIPELINE_CHAPTER_ACCEPTED = "pipeline.chapter.accepted"
    PIPELINE_CHAPTER_REJECTED = "pipeline.chapter.rejected"

    # =========================================================================
    # Quality Gate — 门禁检查
    # =========================================================================
    GATE_CHECK_START = "gate.check.start"
    GATE_CHECK_PASS = "gate.check.pass"
    GATE_CHECK_FAIL = "gate.check.fail"
    GATE_CHECK_REVISE = "gate.check.revise"

    # =========================================================================
    # Memory — 记忆更新
    # =========================================================================
    MEMORY_FACT_EXTRACTED = "memory.fact.extracted"
    MEMORY_FORESHADOW_UPDATE = "memory.foreshadow.update"
    MEMORY_RAG_INDEXED = "memory.rag.indexed"
    MEMORY_GRAPH_UPDATED = "memory.graph.updated"

    # =========================================================================
    # Agent — Agent 间通信
    # =========================================================================
    AGENT_DISCOVERY_REQUEST = "agent.discovery.request"
    AGENT_DISCOVERY_RESPONSE = "agent.discovery.response"
    AGENT_TASK_REQUEST = "agent.task.request"
    AGENT_TASK_RESPONSE = "agent.task.response"

    # =========================================================================
    # User — 用户操作
    # =========================================================================
    USER_PROJECT_CREATED = "user.project.created"
    USER_CHAPTER_MANUAL_EDIT = "user.chapter.manual_edit"
    USER_FORESHADOW_CONFIRM_DROP = "user.foreshadow.confirm_drop"
    USER_GATE_OVERRIDE = "user.gate.override"
