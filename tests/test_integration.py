"""Phase 1 集成测试 — 验证完整的创作流水线 (不含真实 LLM 调用)."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
import yaml

from core.config_manager import ConfigManager
from core.context_manager import ContextManager
from core.event_bus import EventBus
from core.kernel_impl import Kernel
from core.llm.router import ModelRouter
from core.plugin_manager import PluginManager
from plugins.chapter_writer.plugin import ChapterWriterPlugin, create_manifest as cw_manifest
from plugins.idea_incubator.plugin import IdeaIncubatorPlugin, create_manifest as ii_manifest
from plugins.outline_planner.plugin import OutlinePlannerPlugin, create_manifest as op_manifest
from plugins.style_adapter.plugin import StyleAdapterPlugin, create_manifest as sa_manifest
from plugins.world_builder.plugin import WorldBuilderPlugin, create_manifest as wb_manifest
from rag.embeddings import DummyEmbeddingProvider
from rag.retrieval import RetrievalEngine
from rag.store import VectorStore


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
async def full_kernel(temp_config_dir: Path, default_config_yaml: dict) -> Kernel:
    """构建完整的内核实例 (含 PluginManager + ModelRouter + VectorStore)."""
    # Config
    with open(temp_config_dir / "default.yaml", "w", encoding="utf-8") as f:
        yaml.dump(default_config_yaml, f)
    config = ConfigManager(str(temp_config_dir))
    await config.load(env="test")

    # Core services
    bus = EventBus()
    await bus.start()
    ctx = ContextManager()
    await ctx.start()

    pm = PluginManager(event_bus=bus)

    # Vector store (in-memory temp dir)
    import tempfile
    tmp_chroma = tempfile.mkdtemp(prefix="chroma_test_")
    store = VectorStore(persist_directory=tmp_chroma, embedding_provider=DummyEmbeddingProvider())
    await store.start()

    # Retrieval engine
    retrieval = RetrievalEngine(store, bm25_candidates=8, semantic_top_k=4)

    # Model router (empty — no LLM calls in integration tests)
    router = ModelRouter(config.get_all())

    # Kernel
    kernel = Kernel(
        event_bus=bus,
        plugin_manager=pm,
        config_manager=config,
        context_manager=ctx,
        model_registry=None,
        data_dir=tempfile.mkdtemp(prefix="novel_output_test_"),
    )
    kernel.set_retrieval_engine(retrieval)

    yield kernel

    # Cleanup
    await bus.stop()
    await ctx.stop()
    await store.stop()


@pytest.fixture
async def loaded_plugins(full_kernel: Kernel) -> dict[str, Any]:
    """加载全部 5 个创作插件."""
    plugins: dict[str, Any] = {}

    # Idea Incubator
    ii_inst = IdeaIncubatorPlugin()
    await full_kernel._plugin_manager.register(ii_manifest(), ii_inst)
    await full_kernel._plugin_manager.load("idea-incubator")
    await ii_inst.on_load(full_kernel)
    plugins["idea_incubator"] = ii_inst

    # World Builder
    wb_inst = WorldBuilderPlugin()
    await full_kernel._plugin_manager.register(wb_manifest(), wb_inst)
    await full_kernel._plugin_manager.load("world-builder")
    await wb_inst.on_load(full_kernel)
    plugins["world_builder"] = wb_inst

    # Outline Planner
    op_inst = OutlinePlannerPlugin()
    await full_kernel._plugin_manager.register(op_manifest(), op_inst)
    await full_kernel._plugin_manager.load("outline-planner")
    await op_inst.on_load(full_kernel)
    plugins["outline_planner"] = op_inst

    # Chapter Writer
    cw_inst = ChapterWriterPlugin()
    await full_kernel._plugin_manager.register(cw_manifest(), cw_inst)
    await full_kernel._plugin_manager.load("chapter-writer")
    await cw_inst.on_load(full_kernel)
    plugins["chapter_writer"] = cw_inst

    # Style Adapter
    sa_inst = StyleAdapterPlugin()
    await full_kernel._plugin_manager.register(sa_manifest(), sa_inst)
    await full_kernel._plugin_manager.load("style-adapter")
    await sa_inst.on_load(full_kernel)
    plugins["style_adapter"] = sa_inst

    return plugins


# =============================================================================
# Tests
# =============================================================================


@pytest.mark.asyncio
async def test_all_plugins_load(loaded_plugins: dict) -> None:
    """验证全部 5 个插件加载成功."""
    assert len(loaded_plugins) == 5
    for name, plugin in loaded_plugins.items():
        assert plugin._kernel is not None, f"{name} 未获取到 kernel 引用"


@pytest.mark.asyncio
async def test_plugin_dependencies(loaded_plugins: dict, full_kernel: Kernel) -> None:
    """验证插件依赖链正确."""
    deps = await full_kernel._plugin_manager.resolve_dependencies("chapter-writer")
    # chapter-writer → outline-planner → world-builder → idea-incubator
    assert "idea-incubator" in deps
    assert "world-builder" in deps
    assert "outline-planner" in deps
    assert deps[-1] == "chapter-writer"  # 最后加载


@pytest.mark.asyncio
async def test_idea_incubator_parse_json() -> None:
    """验证 IdeaIncubator 的 JSON 解析."""
    from plugins.idea_incubator.plugin import IdeaIncubatorPlugin

    # 直接 JSON
    result = IdeaIncubatorPlugin._parse_json('{"directions": [{"logline": "test"}]}')
    assert result["directions"][0]["logline"] == "test"

    # JSON in code fence
    result = IdeaIncubatorPlugin._parse_json('```json\n{"directions": []}\n```')
    assert result["directions"] == []

    # 无效
    result = IdeaIncubatorPlugin._parse_json("not json at all")
    assert result.get("parse_error") is True


@pytest.mark.asyncio
async def test_chapter_writer_prompt_build(loaded_plugins: dict) -> None:
    """验证 ChapterWriter 的 Prompt 构建."""
    cw = loaded_plugins["chapter_writer"]

    node = {
        "chapter_number": 1,
        "volume_number": 1,
        "title": "血月重归",
        "summary": "女主重生",
        "key_events": ["重生觉醒", "遇到男主"],
        "character_moments": ["女主揭露真相"],
        "is_climax": False,
        "is_hook_point": True,
    }

    context = {
        "previous_chapters_summary": "前世被背叛",
        "rag_results": [],
        "characters": {
            "char_001": {"name": "凌初", "current_status": "active"},
        },
        "active_foreshadows": [
            {"foreshadow_id": "fs_001", "description": "绝情咒的秘密"},
        ],
    }

    prompt = cw._build_user_prompt(node, context, "fanqie", "", "")

    assert "第1章: 血月重归" in prompt
    assert "血月重归" in prompt
    assert "女主重生" in prompt
    assert "前世被背叛" in prompt
    assert "绝情咒" in prompt
    assert "⭐" in prompt


@pytest.mark.asyncio
async def test_style_adapter_modes() -> None:
    """验证 StyleAdapter 的模式配置."""
    from plugins.style_adapter.plugin import ADAPT_PROMPTS, PLATFORM_NAMES

    assert "rewrite" in ADAPT_PROMPTS
    assert "polish" in ADAPT_PROMPTS
    assert "minimal" in ADAPT_PROMPTS
    assert PLATFORM_NAMES["fanqie"] == "番茄小说"
    assert PLATFORM_NAMES["qidian"] == "起点中文网"


@pytest.mark.asyncio
async def test_retrieval_trigger_detection() -> None:
    """验证条件检索触发判断."""
    # 应该触发
    assert RetrievalEngine.should_retrieve({"summary": "主角突破金丹期", "key_events": []}) is True
    assert RetrievalEngine.should_retrieve({"summary": "两人在山谷中重逢", "key_events": []}) is True
    assert RetrievalEngine.should_retrieve({"summary": "大反派揭露了真相", "key_events": []}) is True

    # 不应该触发
    assert RetrievalEngine.should_retrieve({"summary": "日常修炼", "key_events": ["吃饭"]}) is False

    # 强制触发
    assert RetrievalEngine.should_retrieve({"summary": "日常"}, force=True) is True


@pytest.mark.asyncio
async def test_data_models_validation() -> None:
    """验证 Pydantic 模型验证."""
    from models.chapter import Chapter, ChapterMetadata
    from models.project import Platform, ProjectMeta

    # 合法数据
    meta = ProjectMeta(project_id="proj_test001", title="测试小说", platform=Platform.FANQIE)
    assert meta.project_id == "proj_test001"

    # 非法 project_id
    with pytest.raises(Exception):
        ProjectMeta(project_id="bad-format", title="测试", platform=Platform.FANQIE)

    # 章节
    chapter = Chapter(
        metadata=ChapterMetadata(chapter_id="ch_0001", chapter_number=1),
        content="测试正文",
    )
    assert chapter.metadata.chapter_id == "ch_0001"


@pytest.mark.asyncio
async def test_knowledge_loader_parse(full_kernel: Kernel) -> None:
    """验证知识库文件存在且可解析."""
    from rag.knowledge_loader import KnowledgeLoader

    loader = KnowledgeLoader(
        vector_store=None,  # type: ignore — 只测试文件解析
        knowledge_dir="knowledge_base",
    )

    # 验证文件存在
    tips_dir = loader._base_dir / "writing_tips"
    assert tips_dir.exists()
    assert (tips_dir / "fanqie_tips.md").exists()

    # 验证 Markdown 拆分
    content = (tips_dir / "fanqie_tips.md").read_text(encoding="utf-8")
    sections = KnowledgeLoader._split_markdown(content)
    assert len(sections) > 0

    # 验证 YAML 可解析
    yaml_path = loader._base_dir / "anti_ai_patterns" / "patterns.yaml"
    with open(yaml_path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    assert "patterns" in data
    assert len(data["patterns"]) >= 7


@pytest.mark.asyncio
async def test_kernel_file_io(full_kernel: Kernel) -> None:
    """验证内核项目文件读写."""
    # 写文件
    await full_kernel.write_project_file("proj_test", "test.txt", "hello world")

    # 读文件
    content = await full_kernel.read_project_file("proj_test", "test.txt")
    assert content == "hello world"

    # 文件不存在
    with pytest.raises(FileNotFoundError):
        await full_kernel.read_project_file("proj_test", "nonexistent.txt")


@pytest.mark.asyncio
async def test_context_pipeline_state(loaded_plugins: dict) -> None:
    """验证上下文管理器可追踪流水线状态."""
    kernel = loaded_plugins["chapter_writer"]._kernel

    await kernel.context().set("pipeline:proj_test", "state", "drafting")
    await kernel.context().set("pipeline:proj_test", "chapter", 5)

    state = await kernel.context().get("pipeline:proj_test", "state")
    chapter = await kernel.context().get("pipeline:proj_test", "chapter")

    assert state == "drafting"
    assert chapter == 5
