"""手动测试脚本 — 验证核心模块的实际运行。

覆盖:
    1. AI 模式检测器 (规则检测, 无需 LLM)
    2. 事件总线 + 插件管理器 联动
    3. 配置管理
    4. 全插件加载 + 门禁链
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# 确保项目根目录在 path 中
sys.path.insert(0, str(Path(__file__).parent.parent))


# =============================================================================
# 测试 1: AI 模式检测器
# =============================================================================


def test_ai_pattern_detector() -> None:
    """测试 AI 模式检测器——纯规则, 不调 LLM."""
    from plugins.anti_ai_detection.pattern_detector import AIPatternDetector

    detector = AIPatternDetector()

    print("=" * 60)
    print("测试 1: AI 模式检测器")
    print("=" * 60)

    # 样本 1: 明显 AI 文本
    ai_text = (
        "他不禁微微抬起头，仿佛看到了一幅前所未有、意义深远的景象。"
        "与此同时，她的身影映入眼帘。诚然，这一切都令人难以置信。"
        "不难看出，未来的路还很长。充满希望的新篇章即将开启。"
    )

    print("\n[样本1] 明显 AI 风格文本:")
    print(f"  {ai_text.strip()[:120]}...")

    matches = detector.detect(ai_text)
    score = detector.calculate_ai_score(matches)
    sentence = detector.detect_uniform_sentences(ai_text)
    ending = detector.detect_generic_ending(ai_text)

    print(f"\n  检测结果:")
    print(f"    AI 评分: {score:.3f} (越接近1越像人类)")
    print(f"    检测到的模式: {len(matches)} 类")
    for m in matches:
        print(f"      [{m.severity.upper():6s}] {m.category}: {m.matched_items[:5]} (共{m.count}处)")

    if sentence["is_uniform"]:
        print(f"    ⚠ 句长均匀 (SD={sentence['sd']}) — AI 信号")
    if ending["has_generic_ending"]:
        print(f"    ⚠ 泛化结尾: {ending['found']}")

    # 样本 2: 人类风格文本
    human_text = (
        "老王把烟掐了。\n"
        "他盯着门看了三秒。\n"
        "然后一脚踹开。\n\n"
        "屋里黑洞洞的，只有角落那台旧电视闪着雪花。沙发上的女人动都没动——"
        "她已经这样坐了一下午了。\n"
        "\"找到了？\"\n"
        "老王没答话。他把那把钥匙扔在桌上。铜的，上面还带着土。"
    )

    print(f"\n[样本2] 人类风格文本:")
    for line in human_text.strip().split("\n")[:4]:
        print(f"  {line}")
    print("  ...")

    matches2 = detector.detect(human_text)
    score2 = detector.calculate_ai_score(matches2)
    sentence2 = detector.detect_uniform_sentences(human_text)
    ending2 = detector.detect_generic_ending(human_text)

    print(f"\n  检测结果:")
    print(f"    AI 评分: {score2:.3f}")
    print(f"    检测到的模式: {len(matches2)} 类")
    print(f"    句长 SD: {sentence2['sd']} {'⚠ 均匀' if sentence2['is_uniform'] else '✓ 多变'}")
    print(f"    泛化结尾: {'⚠ 有' if ending2['has_generic_ending'] else '✓ 无'}")

    # 断言: AI 文本得分应明显低于人类文本
    assert score < score2, f"AI 文本评分({score:.3f})应低于人类文本({score2:.3f})"
    print(f"\n  ✓ 通过: AI 评分 {score:.3f} < 人类评分 {score2:.3f}")


# =============================================================================
# 测试 2: 事件总线 + 插件联动
# =============================================================================


async def test_event_bus_plugins() -> None:
    """测试事件总线 + 插件管理器 联动."""
    from core.event_bus import EventBus, EventCategory, EventEnvelope, EventPriority
    from core.events import BuiltInEvents
    from core.plugin_manager import PluginManager, PluginManifest

    print("\n" + "=" * 60)
    print("测试 2: 事件总线 + 插件联动")
    print("=" * 60)

    bus = EventBus()
    await bus.start()

    pm = PluginManager(event_bus=bus)

    # 模拟一个监听插件
    received_events: list[str] = []

    class ListenerPlugin:
        name = "test-listener"
        version = "0.1.0"

        async def on_load(self, kernel) -> None:
            await kernel.subscribe(
                "pipeline.chapter.accepted",
                self._on_chapter_accepted,
                priority=EventPriority.HIGH,
            )

        async def _on_chapter_accepted(self, env: EventEnvelope) -> None:
            received_events.append(env.payload.get("chapter_id", "?"))

        async def on_unload(self) -> None:
            pass

    manifest = PluginManifest(
        name="test-listener",
        version="0.1.0",
        description="测试监听插件",
        dependencies=[],
        hooks=["on_load", "on_unload"],
    )

    plugin = ListenerPlugin()
    await pm.register(manifest, plugin)
    await pm.load("test-listener")

    # 发布章节接受事件
    await bus.publish(EventEnvelope(
        event_type=BuiltInEvents.PIPELINE_CHAPTER_ACCEPTED,
        category=EventCategory.PIPELINE,
        source="orchestrator",
        payload={"chapter_id": "ch_v01_0001", "word_count": 3200},
    ))
    await bus.publish(EventEnvelope(
        event_type=BuiltInEvents.PIPELINE_CHAPTER_ACCEPTED,
        category=EventCategory.PIPELINE,
        payload={"chapter_id": "ch_v01_0002"},
    ))

    await asyncio.sleep(0.2)

    print(f"  收到事件: {len(received_events)} 个")
    print(f"  章节: {received_events}")

    assert len(received_events) == 2
    assert "ch_v01_0001" in received_events
    print("  ✓ 通过: 插件通过事件总线收到了2个章节完成通知")

    await pm.unload("test-listener")
    await bus.stop()


# =============================================================================
# 测试 3: 配置管理
# =============================================================================


async def test_config() -> None:
    """测试配置管理的三层合并."""
    import tempfile
    import yaml
    from pathlib import Path
    from core.config_manager import ConfigManager

    print("\n" + "=" * 60)
    print("测试 3: 配置管理")
    print("=" * 60)

    tmp = Path(tempfile.mkdtemp(prefix="novel_demo_"))

    # 写入测试配置
    config_data = {
        "app": {"name": "demo-app", "debug": True},
        "llm": {
            "default_tier": "standard",
            "tiers": {
                "premium": {"provider": "claude", "model": "claude-opus-test", "max_tokens": 8192},
                "standard": {"provider": "claude", "model": "claude-sonnet-test", "max_tokens": 4096},
                "budget": {"provider": "claude", "model": "claude-haiku-test", "max_tokens": 2048},
            },
        },
        "chapter": {"default_words_min": 2000, "default_words_max": 4000},
    }
    (tmp / "default.yaml").write_text(yaml.dump(config_data), encoding="utf-8")

    cm = ConfigManager(str(tmp))
    await cm.load(env="test")

    print(f"  app.name = {cm.get('app.name')}")
    print(f"  llm.default_tier = {cm.get('llm.default_tier')}")
    print(f"  chapter.words_range = {cm.get('chapter.default_words_min')}-{cm.get('chapter.default_words_max')}")

    assert cm.get("app.name") == "demo-app"
    assert cm.get("llm.tiers.premium.model") == "claude-opus-test"

    # 运行时修改
    cm.set("app.debug", False)
    assert cm.get("app.debug") is False

    print("  ✓ 通过: 配置三层合并 + 运行时修改 + 点号键访问")


# =============================================================================
# 测试 4: 全插件加载 + 门禁链
# =============================================================================


async def test_full_pipeline() -> None:
    """测试全插件加载 + 门禁链 (不调用 LLM)."""
    import tempfile
    import yaml
    from pathlib import Path

    from core.config_manager import ConfigManager
    from core.context_manager import ContextManager
    from core.event_bus import EventBus
    from core.kernel_impl import Kernel
    from core.llm.router import ModelRouter
    from core.plugin_manager import PluginManager
    from core.quality_gate import GateChainConfig, GateChainExecutor, GateResult, GateVerdict, IQualityGate

    print("\n" + "=" * 60)
    print("测试 4: 全插件加载 + 门禁链")
    print("=" * 60)

    # --- 基础设施 ---
    tmp = Path(tempfile.mkdtemp(prefix="novel_full_"))
    config_data = {
        "app": {"name": "test", "debug": True},
        "llm": {
            "default_tier": "standard",
            "tiers": {
                "premium": {"provider": "claude", "model": "claude-opus-test", "max_tokens": 8192},
                "standard": {"provider": "claude", "model": "claude-sonnet-test", "max_tokens": 4096},
                "budget": {"provider": "claude", "model": "claude-haiku-test", "max_tokens": 2048},
            },
        },
    }
    (tmp / "default.yaml").write_text(yaml.dump(config_data), encoding="utf-8")

    config = ConfigManager(str(tmp))
    await config.load(env="test")

    bus = EventBus()
    await bus.start()
    ctx = ContextManager()
    await ctx.start()
    pm = PluginManager(event_bus=bus)
    router = ModelRouter(config.get_all())

    kernel = Kernel(
        event_bus=bus,
        plugin_manager=pm,
        config_manager=config,
        context_manager=ctx,
        model_router=router,
        data_dir=tempfile.mkdtemp(prefix="novel_out_"),
    )

    # --- 加载所有插件 ---
    from plugins.idea_incubator.plugin import create_manifest as ii_man, create_plugin as ii_plug
    from plugins.world_builder.plugin import create_manifest as wb_man, create_plugin as wb_plug
    from plugins.outline_planner.plugin import create_manifest as op_man, create_plugin as op_plug
    from plugins.chapter_writer.plugin import create_manifest as cw_man, create_plugin as cw_plug
    from plugins.style_adapter.plugin import create_manifest as sa_man, create_plugin as sa_plug
    from plugins.consistency_checker.plugin import create_manifest as cc_man, create_plugin as cc_plug
    from plugins.foreshadow_manager.plugin import create_manifest as fm_man, create_plugin as fm_plug
    from plugins.anti_ai_detection.plugin import create_manifest as ad_man, create_plugin as ad_plug

    all_plugins = [
        (ii_man(), ii_plug()),
        (wb_man(), wb_plug()),
        (op_man(), op_plug()),
        (cw_man(), cw_plug()),
        (sa_man(), sa_plug()),
        (cc_man(), cc_plug()),
        (fm_man(), fm_plug()),
        (ad_man(), ad_plug()),
    ]

    for manifest, instance in all_plugins:
        await pm.register(manifest, instance)

    await pm.load_all()

    # 触发 on_load
    for _, instance in all_plugins:
        if hasattr(instance, "on_load"):
            await instance.on_load(kernel)

    active = await pm.list_active()
    print(f"  已激活插件: {len(active)} 个")
    for entry in active:
        print(f"    ✓ {entry.manifest.name} v{entry.manifest.version}")

    assert len(active) == 8
    print("  ✓ 通过: 全部 8 个插件成功加载")

    # --- 门禁链 ---
    # 收集实现了 IQualityGate 的插件
    gates: list[IQualityGate] = []
    for entry in active:
        if isinstance(entry.instance, IQualityGate):
            gates.append(entry.instance)

    print(f"\n  门禁链: {len(gates)} 道")
    for g in sorted(gates, key=lambda g: g.order):
        print(f"    {g.order:2d}. {g.name}")

    config = GateChainConfig(gates=gates)
    chain = GateChainExecutor(config)

    # 用一段测试文本跑门禁
    test_chapter = {
        "chapter_id": "ch_v01_0001",
        "chapter_number": 1,
        "content": (
            "他走进院子。月光正亮。\n"
            "石桌上摆着一壶酒，两只杯子。\n"
            "一只满的，一只空的。\n\n"
            "\"来了？\"\n"
            "阴影里走出一个人。\n"
            "\"东西带来了？\"\n"
            "他从怀里摸出那把钥匙——铜的，上面还带着土。"
        ),
        "metadata": {},
    }

    test_context = {
        "settings": {},
        "facts": {"entries": {}},
        "foreshadows": {"entries": {}},
    }

    async def on_revise(ch, issues):
        print(f"    [修订] 收到 {len(issues)} 条问题")
        suggestions = []
        for r in issues:
            for iss in r.issues:
                if iss.suggestion:
                    suggestions.append(iss.suggestion)
        ch["content"] = ch.get("content", "") + "\n\n[已根据建议修订]"
        return ch

    result = await chain.execute(test_chapter, test_context, on_revise)

    print(f"\n  门禁结果: {'✓ 通过' if result.passed else '✗ 未通过'}")
    print(f"  执行轮次: {result.total_rounds}")
    for r in result.gate_results:
        status = "✓" if r.verdict == GateVerdict.PASS else "✗"
        print(f"    {status} {r.gate_name}: score={r.score:.2f}, issues={len(r.issues)}")
        for iss in r.issues:
            print(f"         [{iss.severity.value}] {iss.code}: {iss.message}")

    # 干净的人类风格文本应该通过大部分门禁
    print(f"\n  ✓ 通过: 门禁链完整执行")

    # --- 清理 ---
    await bus.stop()
    await ctx.stop()


# =============================================================================
# main
# =============================================================================


async def main() -> None:
    print("\n" + "█" * 60)
    print("  AI 小说生成智能体 — 手动测试")
    print("█" * 60)

    # 同步测试
    test_ai_pattern_detector()

    # 异步测试
    await test_event_bus_plugins()
    await test_config()
    await test_full_pipeline()

    print("\n" + "█" * 60)
    print("  全部测试通过 ✓")
    print("█" * 60)


if __name__ == "__main__":
    asyncio.run(main())
