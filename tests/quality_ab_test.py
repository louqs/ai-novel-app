"""A/B 质量对比测试 — 优化前 vs 优化后。

使用相同的章节大纲和上下文，对比：
    A组: 直接生成 (旧)
    B组: 自动质量修订循环 (新)
"""

from __future__ import annotations

import asyncio
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
from plugins.anti_ai_detection.pattern_detector import AIPatternDetector


async def setup():
    import os
    import tempfile

    api_key = os.getenv("QWEN_API_KEY", "")
    if not api_key:
        raise RuntimeError("请设置 QWEN_API_KEY 环境变量")
    base_url = "https://dashscope.aliyuncs.com/compatible-mode"
    model = "qwen-max"

    config = ConfigManager("config")
    config._data = config._load_yaml("default.yaml")
    for tier in ["premium", "standard", "budget"]:
        config._data["llm"]["tiers"][tier]["provider"] = "bailian"
        config._data["llm"]["tiers"][tier]["model"] = model

    bus = EventBus(); await bus.start()
    ctx = ContextManager(); await ctx.start()
    pm = PluginManager(event_bus=bus)

    registry = ModelRegistry(config._data)
    registry.register_adapter(OpenAICompatibleAdapter(name="bailian", base_url=base_url, api_key=api_key, default_model=model))
    for t in ["premium","standard","budget"]:
        registry.set_tier_model(t, "bailian", model)

    kernel = Kernel(event_bus=bus, plugin_manager=pm, config_manager=config,
                    context_manager=ctx, model_registry=registry,
                    data_dir=tempfile.mkdtemp(prefix="ab_"))

    import importlib
    # 注册所有依赖
    deps = [
        "plugins.idea_incubator.plugin",
        "plugins.world_builder.plugin",
        "plugins.outline_planner.plugin",
        "plugins.chapter_writer.plugin",
    ]
    instances = {}
    for mod_name in deps:
        mod = importlib.import_module(mod_name)
        manifest = mod.create_manifest()
        instance = mod.create_plugin()
        await pm.register(manifest, instance)
        instances[manifest.name] = instance

    await pm.load_all()
    for name, inst in instances.items():
        await inst.on_load(kernel)

    return kernel, instances["chapter-writer"]


async def main():
    kernel, writer = await setup()

    chapter_node = {
        "chapter_number": 1, "volume_number": 1,
        "title": "宗门的背叛",
        "summary": "主角被宗门诬陷偷盗宝物，在绝望中觉醒体内力量",
        "key_events": ["被诬陷偷盗", "被押入密室", "体内力量觉醒"],
        "character_moments": ["主角的愤怒与绝望", "对宗门信任的崩塌"],
        "is_climax": False, "is_hook_point": True,
    }

    context = {
        "settings": {"world_name": "现代都市与古武世界"},
        "characters": {
            "char_001": {"name": "云飞扬", "current_status": "active",
                         "personality_tags": ["隐忍", "不屈"]},
        },
        "previous_chapters_summary": "（开篇）",
    }

    # =====================================================================
    # A组: 直接生成
    # =====================================================================
    print("=" * 60)
    print("A组: 直接生成（旧版 Prompt）")
    print("=" * 60)

    t0 = time.time()
    chapter_a = await writer.write_chapter(chapter_node, context=context, platform="fanqie")
    t_a = time.time() - t0

    content_a = chapter_a.content
    detector = AIPatternDetector()
    matches_a = detector.detect(content_a)
    score_a = detector.calculate_ai_score(matches_a)
    sentence_a = detector.detect_uniform_sentences(content_a)
    ending_a = detector.detect_generic_ending(content_a)

    print(f"  耗时: {t_a:.1f}s")
    print(f"  字数: {len(content_a)}")
    print(f"  AI评分: {score_a:.3f}")
    print(f"  检测模式: {len(matches_a)} 类")
    for m in matches_a:
        print(f"    [{m.severity}] {m.category}: {m.count}处")
    print(f"  句长SD: {sentence_a['sd']} {'(均匀)' if sentence_a['is_uniform'] else '(自然)'}")
    print(f"  泛化结尾: {'有' if ending_a['has_generic_ending'] else '无'}")
    print()

    # =====================================================================
    # B组: 自动质量修订
    # =====================================================================
    print("=" * 60)
    print("B组: 自动质量修订（新版 Prompt + 自修订循环）")
    print("=" * 60)

    t0 = time.time()
    result_b = await writer.write_chapter_auto_revise(
        chapter_node, context=context, platform="fanqie",
        min_ai_score=0.70, max_rounds=2,
    )
    t_b = time.time() - t0

    chapter_b = result_b["chapter"]
    quality = result_b["quality"]
    content_b = chapter_b.content

    matches_b = detector.detect(content_b)
    score_b = detector.calculate_ai_score(matches_b)
    sentence_b = detector.detect_uniform_sentences(content_b)
    ending_b = detector.detect_generic_ending(content_b)

    print(f"  耗时: {t_b:.1f}s")
    print(f"  字数: {len(content_b)}")
    print(f"  修订轮次: {quality['rounds']}")
    print(f"  初稿评分: {quality['initial_score']:.3f} → 终稿评分: {quality['final_score']:.3f}")
    print(f"  AI评分: {score_b:.3f}")
    print(f"  检测模式: {len(matches_b)} 类")
    for m in matches_b:
        print(f"    [{m.severity}] {m.category}: {m.count}处")
    print(f"  句长SD: {sentence_b['sd']} {'(均匀)' if sentence_b['is_uniform'] else '(自然)'}")
    print(f"  泛化结尾: {'有' if ending_b['has_generic_ending'] else '无'}")

    if quality["rounds"] > 0:
        print(f"\n  修订历史:")
        for h in quality["history"]:
            improve = f"+{quality['improvement']:.3f}" if quality['improvement'] > 0 else str(quality['improvement'])
            print(f"    第{h['round']}轮: score={h['score']}, patterns={h['count']}类 → {improve}")
    print()

    # =====================================================================
    # 对比
    # =====================================================================
    print("=" * 60)
    print("对比总结")
    print("=" * 60)

    improvement = score_b - score_a
    time_ratio = t_b / t_a if t_a > 0 else 0

    print(f"  {'指标':<20} {'A组(直接)':<15} {'B组(优化)':<15} {'变化':<10}")
    print(f"  {'-'*20} {'-'*15} {'-'*15} {'-'*10}")
    print(f"  {'AI评分':<20} {score_a:<15.3f} {score_b:<15.3f} {'+' + str(round(improvement,3)):<10}")
    print(f"  {'检测模式数':<20} {len(matches_a):<15} {len(matches_b):<15} {len(matches_b)-len(matches_a):<10}")
    print(f"  {'句长SD':<20} {sentence_a['sd']:<15} {sentence_b['sd']:<15}")
    print(f"  {'字数':<20} {len(content_a):<15} {len(content_b):<15}")
    print(f"  {'耗时':<20} {t_a:<14.1f}s {t_b:<14.1f}s {f'{time_ratio:.1f}x':<10}")
    print(f"  {'修订轮次':<20} {'0':<15} {quality['rounds']:<15}")

    # ---- 输出 B 组正文节选 ----
    print()
    print("=" * 60)
    print("B组（优化后）正文节选")
    print("=" * 60)
    print(content_b[:400])
    if len(content_b) > 400:
        print(f"  ... (共{len(content_b)}字)")

    await kernel.event_bus.stop()
    await kernel._context_manager.stop()


if __name__ == "__main__":
    asyncio.run(main())
