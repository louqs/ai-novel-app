"""完整创作流水线实测 — 使用阿里云百炼 Qwen API.

端到端: 灵感孵化 → 世界观 → 大纲 → 正文 → 风格适配 → 反AI检测
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.config_manager import ConfigManager
from core.context_manager import ContextManager
from core.event_bus import EventBus
from core.kernel_impl import Kernel
from core.llm.openai_compatible_adapter import OpenAICompatibleAdapter
from core.llm.registry import ModelRegistry
from core.plugin_manager import PluginManager

# =============================================================================
# Setup
# =============================================================================


async def setup():
    """初始化系统 + 注册百炼 Qwen Provider."""
    import os
    import tempfile

    # API 配置
    api_key = os.getenv("QWEN_API_KEY", "")
    if not api_key:
        raise RuntimeError("请设置 QWEN_API_KEY 环境变量")
    base_url = "https://dashscope.aliyuncs.com/compatible-mode"
    default_model = "qwen-max"

    # 基础设施
    config_dir = Path("config")
    config = ConfigManager(str(config_dir))
    config._data = config._load_yaml("default.yaml")
    config._data["llm"]["tiers"]["premium"]["provider"] = "bailian"
    config._data["llm"]["tiers"]["premium"]["model"] = default_model
    config._data["llm"]["tiers"]["standard"]["provider"] = "bailian"
    config._data["llm"]["tiers"]["standard"]["model"] = default_model
    config._data["llm"]["tiers"]["budget"]["provider"] = "bailian"
    config._data["llm"]["tiers"]["budget"]["model"] = default_model

    bus = EventBus()
    await bus.start()
    ctx = ContextManager()
    await ctx.start()
    pm = PluginManager(event_bus=bus)

    # Model Registry
    registry = ModelRegistry(config._data)
    adapter = OpenAICompatibleAdapter(
        name="bailian",
        base_url=base_url,
        api_key=api_key,
        default_model=default_model,
    )
    registry.register_adapter(adapter)
    registry.set_tier_model("premium", "bailian", default_model)
    registry.set_tier_model("standard", "bailian", default_model)
    registry.set_tier_model("budget", "bailian", default_model)

    tmp_out = tempfile.mkdtemp(prefix="novel_real_")

    kernel = Kernel(
        event_bus=bus,
        plugin_manager=pm,
        config_manager=config,
        context_manager=ctx,
        model_registry=registry,
        data_dir=tmp_out,
    )

    # 加载插件
    plugin_modules = [
        ("plugins.idea_incubator.plugin", "灵感孵化器"),
        ("plugins.world_builder.plugin", "设定构建器"),
        ("plugins.outline_planner.plugin", "大纲规划器"),
        ("plugins.chapter_writer.plugin", "正文撰写引擎"),
        ("plugins.style_adapter.plugin", "风格适配器"),
        ("plugins.anti_ai_detection.plugin", "反AI检测"),
    ]

    import importlib
    plugins_loaded = []
    for mod_name, label in plugin_modules:
        mod = importlib.import_module(mod_name)
        manifest = mod.create_manifest()
        instance = mod.create_plugin()
        await pm.register(manifest, instance)
        await pm.load(manifest.name)
        await instance.on_load(kernel)
        plugins_loaded.append((label, instance))

    print(f"[Setup] {len(plugins_loaded)} 插件已加载")
    print(f"[Setup] Provider: bailian ({default_model})")
    print()
    return kernel, plugins_loaded, adapter


# =============================================================================
# Pipeline
# =============================================================================


async def run_pipeline():
    kernel, plugins, adapter = await setup()
    total_start = time.time()

    # =====================================================================
    # Step 1: 灵感孵化
    # =====================================================================
    print("=" * 60)
    print("STEP 1: 灵感孵化")
    print("=" * 60)

    idea_plugin = None
    for label, inst in plugins:
        if label == "灵感孵化器":
            idea_plugin = inst
            break

    seed = "一个被宗门抛弃的废物弟子，意外发现自己体内封印着一尊上古魔神"
    print(f"  输入: {seed}")

    t0 = time.time()
    result = await idea_plugin.incubate(seed, platform="fanqie", count=2)
    t1 = time.time()

    directions = result.get("directions", [])
    if directions:
        d = directions[0]
        print(f"  耗时: {t1 - t0:.1f}s")
        print(f"  梗概: {d.get('logline', '')}")
        print(f"  冲突: {d.get('core_conflict', '')}")
        print(f"  金手指: {d.get('golden_finger', '')}")
        print(f"  平台建议: {d.get('platform_suggestion', '')}")
        print(f"  类型: {d.get('genre_tags', [])}")
        chosen_direction = d
    else:
        print("  (孵化结果为空, 使用默认方向)")
        chosen_direction = {"logline": seed, "core_conflict": "废材逆袭", "golden_finger": "上古魔神", "genre_tags": ["玄幻", "修仙"]}
    print()

    # =====================================================================
    # Step 2: 世界观构建
    # =====================================================================
    print("=" * 60)
    print("STEP 2: 世界观构建")
    print("=" * 60)

    wb_plugin = None
    for label, inst in plugins:
        if label == "设定构建器":
            wb_plugin = inst
            break

    t0 = time.time()
    world_data = await wb_plugin.build_world(chosen_direction, platform="fanqie")
    t1 = time.time()

    settings = world_data.get("settings", {})
    characters = world_data.get("characters", {})

    print(f"  耗时: {t1 - t0:.1f}s")
    print(f"  世界: {getattr(settings, 'world_name', '?') if hasattr(settings, 'world_name') else '?'}")
    chars_dict = getattr(characters, 'characters', {}) if hasattr(characters, 'characters') else {}
    print(f"  人物: {len(chars_dict)} 个")
    for cid, c in list(chars_dict.items())[:5]:
        name = getattr(c, 'name', cid) if hasattr(c, 'name') else c.get('name', cid)
        print(f"    - {name}")
    print()

    # =====================================================================
    # Step 3: 大纲规划 (只做前5章)
    # =====================================================================
    print("=" * 60)
    print("STEP 3: 大纲规划 (前5章)")
    print("=" * 60)

    op_plugin = None
    for label, inst in plugins:
        if label == "大纲规划器":
            op_plugin = inst
            break

    t0 = time.time()
    progress = await op_plugin.plan_outline(
        settings=settings.model_dump() if hasattr(settings, 'model_dump') else settings,
        characters=characters.model_dump() if hasattr(characters, 'model_dump') else characters,
        direction=chosen_direction,
        platform="fanqie",
        total_chapters=5,
        volumes=1,
    )
    t1 = time.time()

    print(f"  耗时: {t1 - t0:.1f}s")
    if progress.volumes:
        vol = progress.volumes[0]
        print(f"  第{vol.volume_number}卷: {vol.title}")
        print(f"  章节数: {len(vol.chapters)}")
        for ch in vol.chapters[:5]:
            star = "⭐" if ch.is_hook_point else ("⚡" if ch.is_climax else "  ")
            print(f"    {star} 第{ch.chapter_number}章: {ch.title}")
            if ch.summary:
                print(f"       {ch.summary[:80]}")
    print()

    # =====================================================================
    # Step 4: 正文撰写 (第1章)
    # =====================================================================
    print("=" * 60)
    print("STEP 4: 正文撰写 (第1章)")
    print("=" * 60)

    cw_plugin = None
    for label, inst in plugins:
        if label == "正文撰写引擎":
            cw_plugin = inst
            break

    chapter_node = None
    if progress.volumes and progress.volumes[0].chapters:
        chapter_node = progress.volumes[0].chapters[0].model_dump()

    t0 = time.time()
    chapter = await cw_plugin.write_chapter(
        chapter_node=chapter_node or {"chapter_number": 1, "title": "第一章"},
        context={
            "settings": settings.model_dump() if hasattr(settings, 'model_dump') else settings,
            "characters": characters.model_dump() if hasattr(characters, 'model_dump') else characters,
            "previous_chapters_summary": "（无前情）",
        },
        platform="fanqie",
    )
    t1 = time.time()

    content = chapter.content if hasattr(chapter, 'content') else str(chapter)
    word_count = len(content)

    print(f"  耗时: {t1 - t0:.1f}s")
    print(f"  字数: {word_count}")
    print(f"  模型: {chapter.metadata.model_used if hasattr(chapter, 'metadata') else '?'}")
    print()
    print("--- 正文预览 ---")
    print(content[:500])
    if len(content) > 500:
        print(f"  ... (共{word_count}字)")
    print()

    # =====================================================================
    # Step 5: 反AI检测
    # =====================================================================
    print("=" * 60)
    print("STEP 5: 反AI检测")
    print("=" * 60)

    ad_plugin = None
    for label, inst in plugins:
        if label == "反AI检测":
            ad_plugin = inst
            break

    t0 = time.time()
    # 先规则检测
    from plugins.anti_ai_detection.pattern_detector import AIPatternDetector
    detector = AIPatternDetector()
    matches = detector.detect(content)
    ai_score = detector.calculate_ai_score(matches)
    sentence = detector.detect_uniform_sentences(content)
    ending = detector.detect_generic_ending(content)
    t1 = time.time()

    print(f"  检测耗时: {t1 - t0:.3f}s (纯规则, 无LLM调用)")
    print(f"  AI评分: {ai_score:.3f} ({'像人类' if ai_score > 0.7 else '有AI痕迹' if ai_score > 0.4 else '高度疑似AI'})")
    print(f"  检测模式: {len(matches)} 类")
    for m in matches:
        print(f"    [{m.severity}] {m.category}: {m.matched_items[:3]} ({m.count}处)")
    if sentence["is_uniform"]:
        print(f"  ⚠ 句长均匀 (SD={sentence['sd']})")
    else:
        print(f"  句长变化: SD={sentence['sd']} (自然)")
    if ending["has_generic_ending"]:
        print(f"  ⚠ 泛化结尾: {ending['found']}")
    else:
        print(f"  结尾: 具体")
    print()

    # =====================================================================
    # Summary
    # =====================================================================
    total_time = time.time() - total_start
    print("=" * 60)
    print("流水线完成")
    print("=" * 60)
    print(f"  总耗时: {total_time:.1f}s")
    print(f"  API调用: 4次 (灵感+设定+大纲+正文)")
    print(f"  产出: {word_count}字章节 + 世界观 + 大纲 + 检测报告")
    print(f"  模型: 阿里云百炼 qwen-max")

    await bus_shutdown(kernel)


async def bus_shutdown(kernel):
    await kernel.event_bus.stop()
    await kernel._context_manager.stop()


if __name__ == "__main__":
    asyncio.run(run_pipeline())
