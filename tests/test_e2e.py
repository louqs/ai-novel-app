"""端到端测试 — 完整创作流水线验证。

覆盖全链路:
    1. 项目创建 (API)          5. 正文撰写 (流式)
    2. 世界观构建 (LLM)        6. 反AI检测 (规则)
    3. 大纲规划 (LLM)          7. 导出 TXT/EPUB
    4. 知识图谱构建            8. 数据看板 + 写作教练

运行:
    pytest tests/test_e2e.py -v -s           # 完整E2E (需API Key)
    pytest tests/test_e2e.py -v -k "smoke"   # 仅冒烟测试 (不需API)
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
import time
from pathlib import Path

import pytest
import yaml

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture(scope="module")
def api_key():
    """获取可用的 API Key。"""
    for env_name in ["DEEPSEEK_API_KEY", "QWEN_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY"]:
        key = os.getenv(env_name, "")
        if key and len(key) > 10:
            return {"key": key, "env": env_name}
    return None


@pytest.fixture(scope="module")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


# =============================================================================
# Helpers
# =============================================================================


async def _setup_kernel(api_key_info: dict | None = None):
    """初始化完整内核 + 全部插件。"""
    from core.config_manager import ConfigManager
    from core.context_manager import ContextManager
    from core.event_bus import EventBus
    from core.kernel_impl import Kernel
    from core.llm.openai_compatible_adapter import OpenAICompatibleAdapter
    from core.llm.registry import ModelRegistry
    from core.plugin_manager import PluginManager

    config = ConfigManager("config")
    config._data = config._load_yaml("default.yaml")

    if api_key_info:
        provider = "bailian" if "QWEN" in api_key_info["env"] else "deepseek"
        base_url = "https://dashscope.aliyuncs.com/compatible-mode" if "QWEN" in api_key_info["env"] else "https://api.deepseek.com/v1"
        model = "qwen-max" if "QWEN" in api_key_info["env"] else "deepseek-chat"

        for tier in ["premium", "standard", "budget"]:
            config._data["llm"]["tiers"][tier]["provider"] = provider
            config._data["llm"]["tiers"][tier]["model"] = model

    bus = EventBus(); await bus.start()
    ctx = ContextManager(); await ctx.start()
    pm = PluginManager(event_bus=bus)

    registry = ModelRegistry(config._data)
    if api_key_info:
        provider = "bailian" if "QWEN" in api_key_info["env"] else "deepseek"
        base_url = "https://dashscope.aliyuncs.com/compatible-mode" if "QWEN" in api_key_info["env"] else "https://api.deepseek.com/v1"
        model = "qwen-max" if "QWEN" in api_key_info["env"] else "deepseek-chat"
        adapter = OpenAICompatibleAdapter(name=provider, base_url=base_url, api_key=api_key_info["key"], default_model=model)
        registry.register_adapter(adapter)
        for t in ["premium", "standard", "budget"]:
            registry.set_tier_model(t, provider, model)

    tmp_out = tempfile.mkdtemp(prefix="e2e_novel_")
    kernel = Kernel(event_bus=bus, plugin_manager=pm, config_manager=config, context_manager=ctx, model_registry=registry, data_dir=tmp_out)

    # 加载全部插件
    import importlib
    plugin_mods = [
        "plugins.idea_incubator.plugin", "plugins.world_builder.plugin",
        "plugins.outline_planner.plugin", "plugins.chapter_writer.plugin",
        "plugins.style_adapter.plugin", "plugins.consistency_checker.plugin",
        "plugins.foreshadow_manager.plugin", "plugins.anti_ai_detection.plugin",
        "plugins.graph_manager.plugin", "plugins.writing_coach.plugin",
        "plugins.cover_artist.plugin", "plugins.pack_market.plugin",
    ]
    for mod_name in plugin_mods:
        try:
            mod = importlib.import_module(mod_name)
            await pm.register(mod.create_manifest(), mod.create_plugin())
        except Exception:
            pass
    await pm.load_all()
    for entry in await pm.list_active():
        if hasattr(entry.instance, "on_load"):
            await entry.instance.on_load(kernel)

    return kernel


async def _create_project(kernel, title="E2E测试小说", platform="fanqie", tags=None, one_liner=""):
    """创建测试项目。"""
    import uuid
    pid = f"proj_{uuid.uuid4().hex[:12]}"
    from models.project import ProjectMeta
    meta = ProjectMeta(project_id=pid, title=title, platform=platform, genre_tags=tags or ["玄幻"], one_liner=one_liner or "一个被宗门抛弃的废物，体内封印着上古魔神")
    await kernel.write_project_file(pid, "project.json", meta.model_dump_json(indent=2))
    await kernel.context().set(f"project:{pid}", "meta", meta.model_dump())
    await kernel.context().set(f"project:{pid}", "platform", platform)
    return pid


# =============================================================================
# Smoke Tests (no API needed)
# =============================================================================


@pytest.mark.smoke
@pytest.mark.asyncio
async def test_smoke_kernel_init():
    """验证内核初始化（无 LLM）。"""
    kernel = await _setup_kernel()
    active = await kernel._plugin_manager.list_active()
    assert len(active) >= 8, f"Expected >=8 plugins, got {len(active)}"
    # 清理
    await kernel.event_bus.stop()
    await kernel._context_manager.stop()


@pytest.mark.smoke
@pytest.mark.asyncio
async def test_smoke_project_create():
    """验证项目创建 + 文件持久化。"""
    kernel = await _setup_kernel()
    pid = await _create_project(kernel)

    # 读回
    data = json.loads(await kernel.read_project_file(pid, "project.json"))
    assert data["title"] == "E2E测试小说"
    assert data["platform"] == "fanqie"

    # 验证文件存在
    project_file = kernel.get_project_dir(pid) / "project.json"
    assert project_file.exists()

    await kernel.event_bus.stop()
    await kernel._context_manager.stop()


@pytest.mark.smoke
@pytest.mark.asyncio
async def test_smoke_anti_ai_detection():
    """验证反AI检测正确区分 AI vs 人类文本。"""
    from plugins.anti_ai_detection.pattern_detector import AIPatternDetector
    detector = AIPatternDetector()

    ai_text = "他不禁微微抬起头，仿佛看到了一幅前所未有的景象，心中充满了希望。"
    human_text = "老王把烟掐了。盯着门看了三秒。一脚踹开。"

    ai_score = detector.calculate_ai_score(detector.detect(ai_text))
    human_score = detector.calculate_ai_score(detector.detect(human_text))

    assert ai_score < 0.5, f"AI text should score low, got {ai_score}"
    assert human_score > 0.8, f"Human text should score high, got {human_score}"


@pytest.mark.smoke
@pytest.mark.asyncio
async def test_smoke_export():
    """验证导出功能（无章节时也应有输出）。"""
    kernel = await _setup_kernel()
    pid = await _create_project(kernel)
    from core.export import NovelExporter
    exporter = NovelExporter(kernel, output_dir=tempfile.mkdtemp(prefix="e2e_export_"))
    path = await exporter.export(pid, fmt="txt")
    assert path.exists()
    content = path.read_text(encoding="utf-8")
    assert "E2E测试小说" in content
    await kernel.event_bus.stop()
    await kernel._context_manager.stop()


@pytest.mark.smoke
@pytest.mark.asyncio
async def test_smoke_graph_store():
    """验证图谱存储 CRUD。"""
    import tempfile as tf
    from core.graph_store import SQLiteGraphStore
    from core.graph_query import GraphQuery

    db_path = Path(tf.mkdtemp()) / "test.db"
    store = SQLiteGraphStore(str(db_path))
    await store.connect()

    await store.upsert_node("c1", "Character", {"name": "主角"})
    await store.upsert_node("c2", "Character", {"name": "反派"})
    await store.upsert_edge("e1", "c1", "c2", "ENEMY")

    query = GraphQuery(store)
    network = await query.character_network("c1")
    assert len(network["nodes"]) >= 2
    assert len(network["edges"]) >= 1

    full = await query.export_full_graph()
    assert len(full["nodes"]) == 2
    assert len(full["edges"]) == 1

    await store.clear()
    await store.close()
    os.unlink(db_path)


# =============================================================================
# Full E2E (requires API key)
# =============================================================================


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_e2e_full_pipeline(api_key):
    """完整端到端流水线——需 API Key。"""
    if not api_key:
        pytest.skip("需要 API Key (设置 DEEPSEEK_API_KEY 或 QWEN_API_KEY)")

    kernel = await _setup_kernel(api_key)
    print(f"\n{'='*60}")
    print(f"  E2E 完整流水线测试")
    print(f"  API: {api_key['env']}")
    print(f"{'='*60}\n")

    total_start = time.time()
    results = {}

    # =====================================================================
    # 1. 项目创建
    # =====================================================================
    print("1/8 创建项目...")
    pid = await _create_project(kernel)
    results["project_id"] = pid
    assert pid.startswith("proj_")
    print(f"   ✓ {pid}")

    # =====================================================================
    # 2. 世界观构建
    # =====================================================================
    print("2/8 构建世界观...")
    t0 = time.time()
    wb = await kernel.get_plugin("world-builder")
    world = await wb.instance.build_world(
        {"logline": "被宗门抛弃的废物弟子，体内封印着上古魔神",
         "genre_tags": ["玄幻", "修仙"]}, platform="fanqie")
    settings = world.get("settings")
    characters = world.get("characters")

    await kernel.context().set(f"project:{pid}", "settings", settings.model_dump() if hasattr(settings, 'model_dump') else settings)
    await kernel.context().set(f"project:{pid}", "characters", characters.model_dump() if hasattr(characters, 'model_dump') else characters)

    chars_dict = getattr(characters, 'characters', {}) if hasattr(characters, 'characters') else characters.get('characters', {})
    results["world_time"] = round(time.time() - t0, 1)
    results["characters"] = len(chars_dict)
    assert len(chars_dict) >= 2, f"Expected >=2 characters, got {len(chars_dict)}"
    print(f"   ✓ {results['world_time']}s, {len(chars_dict)} 个人物")

    # =====================================================================
    # 3. 大纲规划
    # =====================================================================
    print("3/8 生成大纲...")
    t0 = time.time()
    op = await kernel.get_plugin("outline-planner")
    progress = await op.instance.plan_outline(
        settings=settings.model_dump() if hasattr(settings, 'model_dump') else settings,
        characters=characters.model_dump() if hasattr(characters, 'model_dump') else characters,
        direction={"logline": "被宗门抛弃的废物弟子，体内封印着上古魔神", "genre_tags": ["玄幻"]},
        platform="fanqie", total_chapters=5, volumes=1,
    )
    await kernel.context().set(f"project:{pid}", "progress", progress.model_dump())
    results["outline_time"] = round(time.time() - t0, 1)
    total_chs = sum(len(v.chapters) for v in progress.volumes) if progress.volumes else 0
    assert total_chs >= 3, f"Expected >=3 chapters, got {total_chs}"
    print(f"   ✓ {results['outline_time']}s, {total_chs} 章")

    # =====================================================================
    # 4. 知识图谱
    # =====================================================================
    print("4/8 构建知识图谱...")
    t0 = time.time()
    gm = await kernel.get_plugin("graph-manager")
    graph_result = await gm.instance.build_from_settings(pid)
    results["graph_time"] = round(time.time() - t0, 1)
    assert graph_result.get("nodes_added", 0) >= 2
    print(f"   ✓ {results['graph_time']}s, {graph_result['nodes_added']} 节点, {graph_result['edges_added']} 边")

    # =====================================================================
    # 5. 正文撰写
    # =====================================================================
    print("5/8 撰写第1章...")
    t0 = time.time()
    cw = await kernel.get_plugin("chapter-writer")
    node = progress.volumes[0].chapters[0].model_dump() if progress.volumes else {"chapter_number": 1, "title": "第一章"}

    # 流式 + 自动质量修订
    result = await cw.instance.write_chapter_auto_revise(
        node, context={
            "settings": settings.model_dump() if hasattr(settings, 'model_dump') else settings,
            "characters": characters.model_dump() if hasattr(characters, 'model_dump') else characters,
        }, platform="fanqie", min_ai_score=0.60, max_rounds=2,
    )
    chapter = result["chapter"]
    quality = result["quality"]
    content = chapter.content if hasattr(chapter, 'content') else str(chapter)
    results["chapter_time"] = round(time.time() - t0, 1)
    results["word_count"] = len(content)
    results["initial_ai_score"] = quality["initial_score"]
    results["final_ai_score"] = quality["final_score"]

    # 保存
    await kernel.write_project_file(pid, "chapters/ch_v01_0001.md", content)
    assert len(content) >= 500, f"Chapter too short: {len(content)} chars"
    assert quality["final_score"] >= 0.4, f"AI score too low: {quality['final_score']}"
    print(f"   ✓ {results['chapter_time']}s, {len(content)} 字")
    print(f"   AI评分: {quality['initial_score']:.2f} → {quality['final_score']:.2f} ({quality['rounds']}轮修订)")

    # =====================================================================
    # 6. 反AI检测
    # =====================================================================
    print("6/8 反AI检测...")
    from plugins.anti_ai_detection.pattern_detector import AIPatternDetector
    detector = AIPatternDetector()
    matches = detector.detect(content)
    ai_score = detector.calculate_ai_score(matches)
    results["detection_matches"] = len(matches)
    results["detection_score"] = round(ai_score, 3)
    assert ai_score >= 0.3, f"AI detection score too low: {ai_score}"
    print(f"   ✓ AI评分: {ai_score:.3f}, {len(matches)} 类模式")

    # =====================================================================
    # 7. 导出
    # =====================================================================
    print("7/8 导出...")
    t0 = time.time()
    from core.export import NovelExporter
    exporter = NovelExporter(kernel, output_dir=tempfile.mkdtemp(prefix="e2e_export_"))
    txt_path = await exporter.export(pid, fmt="txt")
    epub_path = await exporter.export(pid, fmt="epub")
    md_path = await exporter.export_markdown(pid)

    assert txt_path.exists() and txt_path.stat().st_size > 100
    assert epub_path.exists() and epub_path.stat().st_size > 500
    assert md_path.exists()
    results["export_time"] = round(time.time() - t0, 1)
    print(f"   ✓ TXT: {txt_path.stat().st_size} bytes, EPUB: {epub_path.stat().st_size} bytes")

    # =====================================================================
    # 8. 数据看板
    # =====================================================================
    print("8/8 数据看板...")
    from core.stats import NovelStats
    stats = NovelStats(kernel)
    data = await stats.analyze(pid)
    overview = data.get("overview", {})
    assert overview.get("total_chapters", 0) >= 1
    results["stats_chapters"] = overview.get("total_chapters")
    print(f"   ✓ {overview['total_chapters']} 章, {overview['total_words']} 字")

    # =====================================================================
    # 汇总
    # =====================================================================
    total_time = time.time() - total_start
    results["total_time"] = round(total_time, 1)

    print(f"\n{'='*60}")
    print(f"  E2E 完成 — {total_time:.1f}s")
    print(f"{'='*60}")
    print(f"  项目: {pid}")
    print(f"  人物: {results['characters']}")
    print(f"  章节: {results['word_count']} 字")
    print(f"  AI评分: {results['initial_ai_score']:.2f} → {results['final_ai_score']:.2f}")
    print(f"  图谱: {graph_result['nodes_added']} 节点 {graph_result['edges_added']} 边")
    print(f"  导出: TXT + EPUB + MD")

    # =====================================================================
    # Assertions
    # =====================================================================
    assert total_time < 600, f"E2E took too long: {total_time}s"
    assert results["characters"] >= 2
    assert results["word_count"] >= 500
    assert results["final_ai_score"] >= 0.4

    # 清理
    await kernel.event_bus.stop()
    await kernel._context_manager.stop()
