"""AI 小说生成智能体 — CLI 命令行工具.

用法:
    novel init                    创建新项目 (交互式)
    novel generate <章节号>        生成章节
    novel outline                  查看/生成大纲
    novel check <文本>             反AI检测
    novel models                   模型管理
    novel serve                    启动 Web 服务
    novel status                   项目状态
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path

# 确保项目路径
sys.path.insert(0, str(Path(__file__).parent.parent))


# =============================================================================
# 颜色输出
# =============================================================================


def c(text: str, color: str = "") -> str:
    """终端颜色."""
    codes = {"red": "31", "green": "32", "yellow": "33", "blue": "34", "magenta": "35", "cyan": "36", "bold": "1", "dim": "2"}
    if not color or not os.environ.get("TERM"):
        return text
    return f"\033[{codes.get(color, '0')}m{text}\033[0m"


def header(title: str) -> None:
    print(f"\n{c('━' * 50, 'dim')}")
    print(f"  {c(title, 'bold')}")
    print(f"{c('━' * 50, 'dim')}\n")


def success(msg: str) -> None:
    print(f"  {c('✓', 'green')} {msg}")


def info(msg: str) -> None:
    print(f"  {c('•', 'blue')} {msg}")


def warn(msg: str) -> None:
    print(f"  {c('!', 'yellow')} {msg}")


def error(msg: str) -> None:
    print(f"  {c('✗', 'red')} {msg}")


# =============================================================================
# 内核懒加载
# =============================================================================

_kernel = None


async def get_kernel():
    global _kernel
    if _kernel is not None:
        return _kernel

    from core.config_manager import ConfigManager
    from core.context_manager import ContextManager
    from core.event_bus import EventBus
    from core.kernel_impl import Kernel
    from core.plugin_manager import PluginManager

    config = ConfigManager("config")
    await config.load()
    bus = EventBus(); await bus.start()
    ctx = ContextManager(); await ctx.start()
    pm = PluginManager(event_bus=bus)
    kernel = Kernel(event_bus=bus, plugin_manager=pm, config_manager=config, context_manager=ctx, model_registry=None)
    await kernel.setup_providers_from_config()

    # 加载插件
    import importlib
    plugin_mods = [
        "plugins.idea_incubator.plugin",
        "plugins.world_builder.plugin",
        "plugins.outline_planner.plugin",
        "plugins.chapter_writer.plugin",
        "plugins.style_adapter.plugin",
        "plugins.consistency_checker.plugin",
        "plugins.foreshadow_manager.plugin",
        "plugins.anti_ai_detection.plugin",
        "plugins.graph_manager.plugin",
        "plugins.writing_coach.plugin",
        "plugins.cover_artist.plugin",
        "plugins.pack_market.plugin",
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

    _kernel = kernel
    return kernel


# =============================================================================
# 子命令
# =============================================================================


async def cmd_init(args):
    """创建新项目 (交互式)."""
    header("创建新项目")

    title = input(f"  {c('小说标题', 'bold')}: ").strip()
    if not title:
        error("标题不能为空")
        return

    print(f"\n  {c('目标平台', 'bold')}:")
    platforms = {"1": "fanqie", "2": "qidian", "3": "jinjiang", "4": "qimao", "5": "douban"}
    for k, v in platforms.items():
        names = {"fanqie": "番茄小说", "qidian": "起点中文网", "jinjiang": "晋江文学城", "qimao": "七猫小说", "douban": "豆瓣阅读"}
        print(f"    {k}. {names[v]}")
    choice = input(f"  {c('选择 (1-5)', 'dim')}: ").strip() or "1"
    platform = platforms.get(choice, "fanqie")

    one_liner = input(f"  {c('一句话梗概 (可选)', 'dim')}: ").strip()
    tags_input = input(f"  {c('类型标签，逗号分隔 (可选)', 'dim')}: ").strip()
    genre_tags = [t.strip() for t in tags_input.split(",") if t.strip()]

    kernel = await get_kernel()
    import uuid
    pid = f"proj_{uuid.uuid4().hex[:12]}"

    from models.project import ProjectMeta
    meta = ProjectMeta(
        project_id=pid, title=title, platform=platform,
        genre_tags=genre_tags, one_liner=one_liner,
    )
    await kernel.write_project_file(pid, "project.json", meta.model_dump_json(indent=2))
    await kernel.context().set(f"project:{pid}", "meta", meta.model_dump())
    await kernel.context().set(f"project:{pid}", "platform", platform)

    success(f"项目创建成功!")
    info(f"ID: {pid}")
    info(f"标题: {title}")
    info(f"平台: {platform}")
    info(f"下一步: novel outline --project {pid}")


async def cmd_outline(args):
    """生成大纲."""
    pid = args.project
    if not pid:
        # Try to find a project
        kernel = await get_kernel()
        data_dir = kernel.get_project_dir("").parent
        if data_dir.exists():
            projs = [d.name for d in data_dir.iterdir() if d.is_dir() and d.name.startswith("proj_")]
            if projs:
                pid = projs[0]
                info(f"自动选择项目: {pid}")
        if not pid:
            error("请指定项目: novel outline --project <ID>")
            return

    header(f"生成大纲 — {pid}")
    kernel = await get_kernel()

    try:
        op = await kernel.get_plugin("outline-planner")
    except Exception:
        error("大纲规划器未加载")
        return

    settings = await kernel.context().get_namespace(f"project:{pid}")
    platform = await kernel.context().get(f"project:{pid}", "platform", "fanqie")

    info("正在生成大纲 (预计 30-60s)...")
    t0 = time.time()
    progress = await op.instance.plan_outline(
        settings=settings,
        characters=await kernel.context().get(f"project:{pid}", "characters", {}),
        direction={"logline": settings.get("one_liner", ""), "genre_tags": settings.get("genre_tags", [])},
        platform=platform,
        total_chapters=args.chapters or 30,
        volumes=args.volumes or 2,
    )
    elapsed = time.time() - t0

    # 打印大纲
    for vol in progress.volumes:
        print(f"\n  {c(f'第{vol.volume_number}卷: {vol.title}', 'bold')}")
        print(f"  {c(vol.arc_description[:80], 'dim')}")
        for ch in vol.chapters[:10]:
            star = "⭐" if ch.is_hook_point else ("⚡" if ch.is_climax else "  ")
            print(f"    {star} 第{ch.chapter_number}章: {ch.title}")
            if ch.summary:
                print(f"       {ch.summary[:70]}")
        if len(vol.chapters) > 10:
            print(f"    {c(f'... 还有 {len(vol.chapters) - 10} 章', 'dim')}")

    # 保存
    data = progress.model_dump()
    await kernel.context().set(f"project:{pid}", "progress", data)
    await kernel.write_project_file(pid, "progress.json", json.dumps(data, indent=2, ensure_ascii=False))
    success(f"大纲已保存 ({elapsed:.0f}s)")


async def cmd_generate(args):
    """生成章节."""
    pid = args.project
    ch_num = args.chapter
    vol_num = getattr(args, 'volume', 1) or 1
    if not ch_num:
        error("请指定章节号: novel generate --chapter 1")
        return
    if not pid:
        kernel = await get_kernel()
        data_dir = kernel.get_project_dir("").parent
        if data_dir.exists():
            projs = [d.name for d in data_dir.iterdir() if d.is_dir() and d.name.startswith("proj_")]
            if projs:
                pid = projs[0]
                info(f"自动选择项目: {pid}")
        if not pid:
            error("请指定项目: novel generate --project <ID> --chapter 1")
            return

    header(f"生成第{vol_num}卷第{ch_num}章 — {pid}")
    kernel = await get_kernel()

    try:
        cw = await kernel.get_plugin("chapter-writer")
    except Exception:
        error("正文撰写引擎未加载")
        return

    # 获取大纲节点 — 同时匹配 volume_number 和 chapter_number
    outline_node = {"chapter_number": ch_num, "volume_number": vol_num, "title": f"第{vol_num}卷第{ch_num}章", "summary": ""}
    progress = await kernel.context().get(f"project:{pid}", "progress", {})
    for vol in progress.get("volumes", []):
        if vol.get("volume_number") != vol_num:
            continue
        for ch in vol.get("chapters", []):
            if ch.get("chapter_number") == ch_num:
                outline_node = ch
                break
        if outline_node.get("volume_number") == vol_num:
            break

    platform = await kernel.context().get(f"project:{pid}", "platform", "fanqie")

    info(f"标题: {outline_node.get('title', '')}")

    if args.stream:
        # 流式输出
        info("流式生成中...\n")
        system_prompt = cw.instance._build_system_prompt(platform)
        user_prompt = cw.instance._build_user_prompt(outline_node, {
            "settings": await kernel.context().get_namespace(f"project:{pid}"),
            "characters": await kernel.context().get(f"project:{pid}", "characters", {}),
            "previous_chapters_summary": "",
        }, platform, "", "")

        full = ""
        t0 = time.time()
        async for token in kernel.call_llm_stream(
            [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}],
            tier="premium", max_tokens=8192,
        ):
            print(token, end="", flush=True)
            full += token
        elapsed = time.time() - t0
        print(f"\n\n  {c(f'完成 ({elapsed:.0f}s, {len(full)}字)', 'dim')}")

        chapter_id = f"ch_v{vol_num:02d}_{ch_num:04d}"
        await kernel.write_project_file(pid, f"chapters/{chapter_id}.md", full)
        return

    # 获取上一章摘要 — 使用卷+章组合键
    prev_summary = ""
    if ch_num > 1:
        prev_summary = await kernel.context().get(f"project:{pid}", f"summary_vol{vol_num}_ch{ch_num - 1}", "")

    info(f"正在撰写 (预计 30-90s)...")
    t0 = time.time()
    result = await cw.instance.write_chapter_auto_revise(
        outline_node,
        context={
            "settings": await kernel.context().get_namespace(f"project:{pid}"),
            "characters": await kernel.context().get(f"project:{pid}", "characters", {}),
            "previous_chapters_summary": prev_summary,
        },
        platform=platform,
        min_ai_score=0.70,
        max_rounds=2,
    )
    elapsed = time.time() - t0

    chapter = result["chapter"]
    quality = result["quality"]
    content = chapter.content if hasattr(chapter, 'content') else str(chapter)

    # 保存 — 使用卷+章组合键
    chapter_id = f"ch_v{vol_num:02d}_{ch_num:04d}"
    await kernel.write_project_file(pid, f"chapters/{chapter_id}.md", content)
    await kernel.context().set(f"project:{pid}", f"summary_vol{vol_num}_ch{ch_num}", content[:200])

    # 输出
    print(f"\n  {c('━' * 40, 'dim')}")
    print(content[:600])
    if len(content) > 600:
        print(f"  {c(f'... (共{len(content)}字)', 'dim')}")
    print(f"  {c('━' * 40, 'dim')}")

    success(f"第{ch_num}章完成 ({elapsed:.0f}s, {len(content)}字)")
    info(f"AI评分: {quality['initial_score']:.2f} → {quality['final_score']:.2f}")
    info(f"修订轮次: {quality['rounds']}")
    info(f"保存位置: novel_output/{pid}/chapters/{chapter_id}.md")


async def cmd_check(args):
    """反AI检测."""
    text = args.text
    if args.file:
        text = Path(args.file).read_text(encoding="utf-8")

    if not text:
        # 尝试从 stdin 读取
        if not sys.stdin.isatty():
            text = sys.stdin.read().strip()
        else:
            error("请提供文本: novel check '文本内容' 或 novel check --file chapter.md")
            return

    header("反AI检测")
    info(f"文本长度: {len(text)} 字")

    from plugins.anti_ai_detection.pattern_detector import AIPatternDetector
    detector = AIPatternDetector()

    t0 = time.time()
    matches = detector.detect(text)
    score = detector.calculate_ai_score(matches)
    sentence = detector.detect_uniform_sentences(text)
    ending = detector.detect_generic_ending(text)
    elapsed = time.time() - t0

    score_color = "green" if score > 0.7 else ("yellow" if score > 0.4 else "red")
    print(f"\n  AI评分: {c(f'{score:.3f}', score_color)}")

    if matches:
        print(f"\n  {c('检测到的模式:', 'bold')}")
        for m in matches:
            sev_color = "red" if m.severity == "high" else ("yellow" if m.severity == "medium" else "dim")
            print(f"    {c(f'[{m.severity}]', sev_color)} {m.category}: {m.count}处")
            if m.matched_items:
                print(f"         {', '.join(m.matched_items[:5])}")
    else:
        success("未检测到AI模式")

    if sentence["is_uniform"]:
        warn(f"句长过于均匀 (SD={sentence['sd']})")
    else:
        info(f"句长变化: SD={sentence['sd']} (自然)")

    if ending["has_generic_ending"]:
        warn(f"泛化结尾: {ending['found']}")
    else:
        info("结尾: 具体 ✓")

    print(f"\n  {c(f'检测耗时: {elapsed:.3f}s (纯规则)', 'dim')}")


async def cmd_models(args):
    """模型管理."""
    kernel = await get_kernel()
    registry = kernel.model_registry

    if not registry:
        error("模型注册中心未初始化")
        return

    if args.action == "list":
        header("模型列表")
        providers = registry.list_providers()
        tiers = registry.list_tier_models()

        print(f"\n  {c('当前 Tier 配置:', 'bold')}")
        for tier, cfg in tiers.items():
            print(f"    {tier:10s} → {cfg.get('provider', '?')}/{cfg.get('model', '?')}")

        print(f"\n  {c('Provider:', 'bold')}")
        for p in providers:
            print(f"    {p['name']:15s} [{p['type']:20s}] default: {p.get('default_model', '?')}")

    elif args.action == "switch":
        if not args.provider or not args.model:
            error("用法: novel models switch --provider deepseek --model deepseek-chat --tier premium")
            return
        result = await registry.switch_tier_model(args.tier or "standard", args.provider, args.model)
        success(f"已切换: {args.tier or 'standard'} → {args.provider}/{args.model}")

    elif args.action == "test":
        if args.provider:
            result = await registry.test_connection(args.provider)
            if result["healthy"]:
                success(f"{args.provider}: 连接正常")
            else:
                error(f"{args.provider}: {result.get('error', '连接失败')}")
        else:
            header("测试所有 Provider")
            results = await registry.health_check_all()
            for name, r in results.items():
                if r["healthy"]:
                    success(f"{name}: 正常")
                else:
                    error(f"{name}: {r.get('error', '失败')}")


async def cmd_serve(args):
    """启动 Web 服务."""
    print(f"\n  {c('启动 Web 服务...', 'bold')}")
    print(f"  API:   {c('http://127.0.0.1:8000', 'cyan')}")
    print(f"  Docs:  {c('http://127.0.0.1:8000/docs', 'cyan')}")
    print(f"  页面:  {c('http://127.0.0.1:8000', 'cyan')}")
    print(f"  按 {c('Ctrl+C', 'yellow')} 停止\n")

    import uvicorn
    uvicorn.run(
        "web.backend.main:app",
        host=args.host or "127.0.0.1",
        port=args.port or 8000,
        reload=args.reload,
        log_level="warning",
    )


async def cmd_status(args):
    """查看项目状态."""
    kernel = await get_kernel()

    header("系统状态")

    # 项目列表
    data_dir = kernel.get_project_dir("").parent
    projects = []
    if data_dir.exists():
        for d in data_dir.iterdir():
            if d.is_dir() and d.name.startswith("proj_"):
                try:
                    meta = json.loads((d / "project.json").read_text(encoding="utf-8"))
                    projects.append(meta)
                except Exception:
                    pass

    print(f"\n  {c('项目:', 'bold')}")
    if projects:
        for p in projects:
            ch = p.get("current_chapter", 0)
            print(f"    {p.get('title', '?')} [{p.get('platform', '?')}] — 第{ch}章 ({p.get('status', '?')})")
    else:
        print(f"    暂无项目。运行 {c('novel init', 'cyan')} 创建")

    # Provider 状态
    if kernel.model_registry:
        providers = kernel.model_registry.list_providers()
        print(f"\n  {c('Provider:', 'bold')}")
        for p in providers:
            print(f"    {p['name']:15s} [{p.get('type', '?')}]")

    # 插件状态
    active = await kernel._plugin_manager.list_active()
    print(f"\n  {c(f'插件: {len(active)} 个已激活', 'bold')}")
    for entry in active[:5]:
        print(f"    {entry.manifest.name}")
    if len(active) > 5:
        print(f"    ... 还有 {len(active) - 5} 个")


async def cmd_export(args):
    """导出小说."""
    pid = args.project
    if not pid:
        kernel = await get_kernel()
        data_dir = kernel.get_project_dir("").parent
        if data_dir.exists():
            projs = [d.name for d in data_dir.iterdir() if d.is_dir() and d.name.startswith("proj_")]
            if projs: pid = projs[0]; info(f"自动选择项目: {pid}")
        if not pid: error("请指定项目"); return

    header(f"导出 — {pid}")
    kernel = await get_kernel()
    from core.export import NovelExporter
    exporter = NovelExporter(kernel)

    fmt = args.format or "txt"
    info(f"格式: {fmt}"); info("导出中...")
    path = await exporter.export(pid, fmt=fmt)

    size_kb = path.stat().st_size / 1024
    success(f"导出完成: {path} ({size_kb:.1f} KB)")
    info(f"格式: {fmt}")


async def cmd_stats(args):
    """数据看板."""
    pid = args.project
    if not pid:
        kernel = await get_kernel()
        data_dir = kernel.get_project_dir("").parent
        if data_dir.exists():
            projs = [d.name for d in data_dir.iterdir() if d.is_dir() and d.name.startswith("proj_")]
            if projs: pid = projs[0]; info(f"自动选择项目: {pid}")
        if not pid: error("请指定项目"); return

    header(f"数据看板 — {pid}")
    kernel = await get_kernel()
    from core.stats import NovelStats
    stats = NovelStats(kernel)
    data = await stats.analyze(pid)

    ov = data.get("overview", {})
    print(f"\n  {c('总览', 'bold')}: {ov.get('total_chapters',0)}章 / {ov.get('total_words',0):,}字 / 均{ov.get('avg_words_per_chapter',0)}字/章")

    wct = data.get("word_count_trend", [])
    if wct:
        # ASCII 趋势图
        max_w = max(c["words"] for c in wct) if wct else 1
        print(f"\n  {c('字数趋势', 'bold')}:")
        for c in wct[-20:]:
            bar = "█" * min(int(c["words"] / max_w * 30), 30)
            print(f"    Ch{c['chapter']:4d} {bar} {c['words']}")

    pc = data.get("pacing", {})
    if pc.get("suggestion"):
        print(f"\n  {c('节奏', 'bold')}: {pc['suggestion']}")

    chf = data.get("characters", {}).get("top_characters", [])
    if chf:
        print(f"\n  {c('人物出场', 'bold')}:")
        for c in chf[:10]:
            print(f"    {c['name']}: {c['appearances']}次")

    pub = data.get("platform_check", {})
    print(f"\n  {c('发布检查', 'bold')}:")
    for item in pub.get("checklist", []):
        status_icon = "✓" if "达标" in item["status"] or "请" in item["status"] else "✗"
        print(f"    {status_icon} {item['item']}: {item['status']}")


async def cmd_coach(args):
    """AI 写作教练."""
    pid = args.project
    if not pid:
        kernel = await get_kernel()
        data_dir = kernel.get_project_dir("").parent
        if data_dir.exists():
            projs = [d.name for d in data_dir.iterdir() if d.is_dir() and d.name.startswith("proj_")]
            if projs: pid = projs[0]; info(f"自动选择项目: {pid}")
        if not pid: error("请指定项目"); return

    header(f"AI 写作教练 — {pid}")
    kernel = await get_kernel()

    from plugins.writing_coach.plugin import WritingCoachPlugin
    coach = WritingCoachPlugin()
    await coach.on_load(kernel)

    platform = await kernel.context().get(f"project:{pid}", "platform", "fanqie")

    if args.chapter:
        info(f"分析第{args.chapter}章...")
        try:
            content = await kernel.read_project_file(pid, f"chapters/ch_{args.chapter:04d}.md")
            result = await coach.analyze_chapter(content, platform=platform, chapter_num=args.chapter)
            score_val = result.get("score", 0)
            print(f"\n  {c('评分: ' + str(round(float(score_val), 2)), 'bold')}")
            print(f"\n  {c('改进建议:', 'bold')}")
            for s in result.get("suggestions", []):
                print(f"    [{s.get('area', '')}] {s.get('issue', '')}")
                print(f"    → {s.get('fix', '')}")
            if result.get("strengths"):
                print(f"\n  {c('优点:', 'green')}")
                for s in result["strengths"]:
                    success(s)
        except Exception as e:
            error(f"分析失败: {e}")
    else:
        info("分析整本小说 (预计 20-40s)...")
        result = await coach.analyze_project(pid, platform=platform)
        avg_s = round(float(result.get("avg_score", 0)), 2)
        print(f"\n  {c('总评分: ' + str(avg_s), 'bold')}")
        print(f"  章节: {result.get('total_chapters', 0)}章 / {result.get('total_words', 0):,}字")
        print(f"  总结: {result.get('summary', '')}")
        for ch in result.get("chapter_analyses", []):
            print(f"\n  Ch{ch['chapter']}: score={ch['score']:.2f}")
            for s in ch.get("suggestions", [])[:2]:
                print(f"    → {s.get('fix', s.get('issue', ''))[:80]}")


async def cmd_graph(args):
    """知识图谱操作."""
    kernel = await get_kernel()

    try:
        gm = await kernel.get_plugin("graph-manager")
    except Exception:
        error("图谱管理器未加载。请确保 graph-manager 插件已激活")
        return

    if args.action == "export":
        header("知识图谱 — 全图导出")
        data = await gm.instance.export_graph()
        print(f"\n  节点: {len(data.get('nodes', []))} 个")
        for n in data.get("nodes", [])[:15]:
            print(f"    [{n.get('group', '?')}] {n.get('label', n.get('id', '?'))}")
        print(f"\n  边: {len(data.get('edges', []))} 条")
        for e in data.get("edges", [])[:10]:
            print(f"    {e.get('source', '')} --[{e.get('type', '?')}]--> {e.get('target', '')}")

    elif args.action == "network" and args.char:
        header(f"人物关系网 — {args.char}")
        data = await gm.instance.query_character_network(args.char)
        print(f"  节点: {len(data.get('nodes', []))}")
        print(f"  边: {len(data.get('edges', []))}")
        for e in data.get("edges", []):
            print(f"    {e.get('source', '')} --[{e.get('type', '?')}]--> {e.get('target', '')}")

    elif args.action == "characters":
        header("图谱人物列表")
        chars = await gm.instance.query_all_characters()
        for c in chars:
            print(f"    {c.get('name', c.get('id', '?'))} — {c.get('relation_count', 0)} 条关系 [{c.get('status', '?')}]")

    elif args.action == "build" and args.project:
        header(f"构建图谱 — {args.project}")
        result = await gm.instance.build_from_settings(args.project)
        success(f"节点: +{result.get('nodes_added', 0)}, 边: +{result.get('edges_added', 0)}")

    elif args.action == "clear":
        await gm.instance.clear_graph()
        success("图谱已清空")

    else:
        error("用法: novel graph [export|network|characters|build|clear]")


async def cmd_cover(args):
    """生成封面."""
    pid = args.project
    if not pid:
        kernel = await get_kernel()
        data_dir = kernel.get_project_dir("").parent
        if data_dir.exists():
            projs = [d.name for d in data_dir.iterdir() if d.is_dir() and d.name.startswith("proj_")]
            if projs: pid = projs[0]; info(f"自动选择项目: {pid}")
        if not pid: error("请指定项目"); return

    header(f"生成封面 — {pid}")
    kernel = await get_kernel()
    try:
        artist = await kernel.get_plugin("cover-artist")
    except Exception:
        error("封面艺术家插件未加载"); return

    if args.variants:
        info("生成3版封面...")
        variants = await artist.instance.generate_cover_variants(pid)
        for i, v in enumerate(variants):
            print(f"\n  版本{i+1}: {v.get('path', '?')}")
            if v.get('mock'): warn(v.get('note', '占位封面'))
            else: success("AI 生成完成")
    else:
        info("生成封面...")
        result = await artist.instance.generate_cover(pid, style_hint=args.style or "")
        print(f"\n  路径: {result.get('path', '?')}")
        print(f"  Prompt: {result.get('prompt', '')[:150]}...")
        if result.get('mock'): warn(result.get('note', '无API Key，生成占位封面'))
        else: success("封面生成完成")


async def cmd_illustrate(args):
    """生成插画."""
    pid = args.project
    if not pid: error("请指定项目"); return

    header(f"生成插画 — {pid}")
    kernel = await get_kernel()
    try:
        artist = await kernel.get_plugin("cover-artist")
    except Exception:
        error("封面艺术家插件未加载"); return

    info(f"场景: {args.scene[:60]}...")
    result = await artist.instance.generate_illustration(pid, args.scene, chapter_num=args.chapter or 0)
    print(f"\n  路径: {result.get('path', '?')}")
    if result.get('mock'): warn(result.get('note', '无API Key，需设置 IMAGE_API_KEY'))
    else: success("插画生成完成")


async def cmd_pack(args):
    """知识包管理."""
    kernel = await get_kernel()
    try:
        gm = await kernel.get_plugin("pack-market")
        market = gm.instance.market
    except Exception:
        error("知识包市场插件未加载"); return

    if args.action == "list":
        header("已安装知识包")
        packs = await market.list_local()
        for p in packs:
            print(f"  {c(p.get('title', p['name']), 'bold')} v{p.get('version','?')}")
            print(f"    {p.get('description','')[:80]}")
            print(f"    标签: {', '.join(p.get('tags', []))}")

    elif args.action == "catalog":
        header("知识包目录")
        catalog = await market.list_catalog()
        local = {p["name"] for p in await market.list_local()}
        for item in catalog:
            installed = item.get("name") in local
            status = c("✓ 已安装", "green") if installed else "未安装"
            print(f"  {c(item['title'], 'bold')}")
            print(f"    {item['description'][:80]}")
            print(f"    {status} | 标签: {', '.join(item.get('tags', []))}")

    elif args.action == "install" and args.name:
        info(f"安装 {args.name}...")
        result = await market.install(args.name)
        if result["installed"]:
            success(f"已安装，索引 {result.get('files_indexed', 0)} 个文件")
        else:
            error(result.get("error", "失败"))

    elif args.action == "uninstall" and args.name:
        result = await market.uninstall(args.name)
        if result["uninstalled"]:
            success(f"{args.name} 已卸载")
        else:
            error(result.get("error", "失败"))

    else:
        error("用法: novel pack [list|catalog|install|uninstall]")


async def cmd_world(args):
    """构建世界观."""
    pid = args.project
    if not pid:
        kernel = await get_kernel()
        data_dir = kernel.get_project_dir("").parent
        if data_dir.exists():
            projs = [d.name for d in data_dir.iterdir() if d.is_dir() and d.name.startswith("proj_")]
            if projs:
                pid = projs[0]
                info(f"自动选择项目: {pid}")
        if not pid:
            error("请指定项目: novel world --project <ID>")
            return

    header(f"构建世界观 — {pid}")
    kernel = await get_kernel()

    try:
        wb = await kernel.get_plugin("world-builder")
    except Exception:
        error("设定构建器未加载")
        return

    # 获取项目信息
    platform = await kernel.context().get(f"project:{pid}", "platform", "fanqie")
    meta = await kernel.context().get(f"project:{pid}", "meta", {})

    info("正在生成世界观和人物 (预计 40-70s)...")
    t0 = time.time()
    world = await wb.instance.build_world(
        direction={"logline": meta.get("one_liner", ""), "genre_tags": meta.get("genre_tags", [])},
        platform=platform,
    )
    elapsed = time.time() - t0

    settings = world.get("settings", {})
    characters = world.get("characters", {})

    # 打印
    print(f"\n  {c('世界观', 'bold')}: {getattr(settings, 'world_name', '?')}")
    chars_dict = getattr(characters, 'characters', {}) if hasattr(characters, 'characters') else {}
    print(f"  {c('人物', 'bold')}: {len(chars_dict)} 个")
    for cid, c in list(chars_dict.items())[:6]:
        name = getattr(c, 'name', cid) if hasattr(c, 'name') else c.get('name', cid)
        tags = getattr(c, 'personality_tags', []) if hasattr(c, 'personality_tags') else c.get('personality_tags', [])
        print(f"    - {name} [{', '.join(tags[:3])}]")

    # 保存
    await kernel.context().set(f"project:{pid}", "settings", settings.model_dump() if hasattr(settings, 'model_dump') else settings)
    await kernel.context().set(f"project:{pid}", "characters", characters.model_dump() if hasattr(characters, 'model_dump') else characters)
    success(f"世界观已构建 ({elapsed:.0f}s)")


# =============================================================================
# 主入口
# =============================================================================


def main():
    parser = argparse.ArgumentParser(
        prog="novel",
        description="AI 小说生成智能体 — CLI 工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  novel init                             创建新项目
  novel outline --project proj_xxx       生成大纲
  novel generate --chapter 1             生成第1章
  novel check "文本内容"                  检测AI痕迹
  novel check --file chapter.md          检测文件
  novel models list                      列出模型
  novel models switch --provider qwen --model qwen-max --tier premium
  novel serve                            启动Web服务
  novel status                           查看状态
        """,
    )

    sub = parser.add_subparsers(dest="command", help="子命令")

    # init
    sub.add_parser("init", help="创建新项目 (交互式)")

    # outline
    p_outline = sub.add_parser("outline", help="生成大纲")
    p_outline.add_argument("--project", "-p", help="项目ID")
    p_outline.add_argument("--chapters", "-c", type=int, default=30, help="总章节数")
    p_outline.add_argument("--volumes", "-v", type=int, default=2, help="分卷数")

    # generate
    p_gen = sub.add_parser("generate", help="生成章节")
    p_gen.add_argument("--project", "-p", help="项目ID")
    p_gen.add_argument("--chapter", "-c", type=int, required=True, help="章节号")
    p_gen.add_argument("--volume", "-V", type=int, default=1, help="卷号 (默认1)")
    p_gen.add_argument("--stream", "-s", action="store_true", help="流式输出")

    # check
    p_check = sub.add_parser("check", help="反AI检测")
    p_check.add_argument("text", nargs="?", help="待检测文本")
    p_check.add_argument("--file", "-f", help="从文件读取")

    # world
    p_world = sub.add_parser("world", help="构建世界观和人物")
    p_world.add_argument("--project", "-p", help="项目ID")

    # models
    p_models = sub.add_parser("models", help="模型管理")
    p_models.add_argument("action", nargs="?", default="list", choices=["list", "switch", "test"], help="操作")
    p_models.add_argument("--provider", help="Provider 名称")
    p_models.add_argument("--model", help="模型名称")
    p_models.add_argument("--tier", default="standard", help="Tier (premium/standard/budget)")

    # serve
    p_serve = sub.add_parser("serve", help="启动 Web 服务")
    p_serve.add_argument("--host", default="127.0.0.1")
    p_serve.add_argument("--port", type=int, default=8000)
    p_serve.add_argument("--reload", action="store_true", help="热重载")

    # export
    p_export = sub.add_parser("export", help="导出小说")
    p_export.add_argument("--project", "-p", help="项目ID")
    p_export.add_argument("--format", "-f", choices=["txt", "epub", "md"], default="txt", help="格式")

    # stats
    p_stats = sub.add_parser("stats", help="数据看板")
    p_stats.add_argument("--project", "-p", help="项目ID")

    # coach
    p_coach = sub.add_parser("coach", help="AI写作教练")
    p_coach.add_argument("--project", "-p", help="项目ID")
    p_coach.add_argument("--chapter", "-c", type=int, help="指定章节号")

    # cover
    p_cover = sub.add_parser("cover", help="生成封面")
    p_cover.add_argument("--project", "-p", help="项目ID")
    p_cover.add_argument("--style", "-s", default="", help="风格提示")
    p_cover.add_argument("--variants", "-v", action="store_true", help="生成多版")

    # packs
    p_pack = sub.add_parser("pack", help="知识包管理")
    p_pack.add_argument("action", nargs="?", default="list", choices=["list", "catalog", "install", "uninstall", "create"], help="操作")
    p_pack.add_argument("--name", "-n", help="包名")
    p_pack.add_argument("--title", help="标题")

    # illustrate
    p_illu = sub.add_parser("illustrate", help="生成插画")
    p_illu.add_argument("--project", "-p", help="项目ID")
    p_illu.add_argument("--scene", "-s", required=True, help="场景描述")
    p_illu.add_argument("--chapter", "-c", type=int, default=0, help="关联章节")

    # graph
    p_graph = sub.add_parser("graph", help="知识图谱")
    p_graph.add_argument("action", nargs="?", default="export", choices=["export", "network", "characters", "build", "clear"], help="操作")
    p_graph.add_argument("--project", "-p", help="项目ID")
    p_graph.add_argument("--char", help="人物ID")

    # status
    sub.add_parser("status", help="查看状态")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    # 路由到子命令
    cmd_map = {
        "init": cmd_init,
        "outline": cmd_outline,
        "generate": cmd_generate,
        "check": cmd_check,
        "world": cmd_world,
        "models": cmd_models,
        "graph": cmd_graph,
        "cover": cmd_cover,
        "illustrate": cmd_illustrate,
        "pack": cmd_pack,
        "export": cmd_export,
        "stats": cmd_stats,
        "coach": cmd_coach,
        "serve": cmd_serve,
        "status": cmd_status,
    }

    handler = cmd_map.get(args.command)
    if handler:
        asyncio.run(handler(args))
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
