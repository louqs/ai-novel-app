"""SSE 流式生成 — 章节 + 大纲.

章节生成采用后台任务模式：
- LLM 生成在 asyncio.Task 中运行，不随 SSE 断开而取消
- SSE 端点只负责推送进度，断开后生成继续
- 前端可通过状态端点查询/恢复进行中的生成
"""

import asyncio
import json
import time
from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from web.backend.dependencies import get_kernel

router = APIRouter(tags=["stream"])

# ---------------------------------------------------------------------------
# 章节生成任务管理
# ---------------------------------------------------------------------------

# { "pid_vol_ch": {"status": "generating"|"saved"|"error", "content": "...", "tokens": [...], "error": "...", "word_count": N, "ts": timestamp} }
_chapter_jobs: dict[str, dict] = {}


def _job_key(pid: str, vol: int, ch: int) -> str:
    return f"{pid}_{vol}_{ch}"


# ---------------------------------------------------------------------------
# 大纲生成任务管理（内存 + 数据库双重持久化）
# ---------------------------------------------------------------------------

# 内存缓存，加速访问；同时持久化到数据库，支持服务重启后恢复
_outline_jobs: dict[str, dict] = {}


async def _run_outline_generation(pid: str, num_versions: int = 3, num_volumes: int | None = None):
    """后台执行大纲生成 — 通过 GenerationService 统一入口."""
    from core.generation_service import GenerationService, STYLE_HINTS

    try:
        kernel = await get_kernel()
        gs = GenerationService(kernel)
        versions = []
        gen_tasks: list = []  # 存储 asyncio.Task 引用，用于外部取消
        _outline_jobs[pid] = {
            "status": "generating", "versions": versions, "current": 0, "total": num_versions,
            "message": "正在分析项目...", "ts": time.time(), "tasks": gen_tasks,
        }

        if kernel.db:
            await kernel.db.save_outline_job(pid, "generating", num_versions, 0, [], "正在分析项目...")

        # 通过 GenerationService 生成多版本
        _completed_count = 0  # 用独立计数器跟踪已完成版本数（含失败的）

        async def on_progress(vi: int, result):
            nonlocal _completed_count
            # 检查取消请求
            if _outline_jobs.get(pid, {}).get("cancel_requested"):
                raise asyncio.CancelledError("用户取消大纲生成")

            _outline_jobs[pid]["ts"] = time.time()
            _completed_count += 1

            if result.success:
                total_chs = sum(len(v.get("chapters", [])) for v in result.progress.get("volumes", []))
                style_tag = result.style_hint.split("，")[0].replace("风格偏向", "") if result.style_hint else ""
                version_data = {
                    "data": result.progress,
                    "volumes": len(result.progress.get("volumes", [])),
                    "chapters": total_chs,
                    "style_tag": style_tag,
                }
                versions.append(version_data)
            else:
                # 失败版本也记录，前端可以显示"生成失败"提示
                versions.append({
                    "data": {"project_id": "", "volumes": [], "quota_min_words_per_chapter": 2100, "quota_max_words_per_chapter": 3900,
                             "total_chapters_completed": 0, "total_words_written": 0},
                    "volumes": 0, "chapters": 0,
                    "style_tag": "生成失败",
                    "error": result.error or "未知错误",
                })

            # 用已完成数量作为进度（并行生成时完成顺序不确定）
            _outline_jobs[pid]["current"] = _completed_count
            _outline_jobs[pid]["message"] = f"已生成 {_completed_count}/{num_versions} 个版本..."
            if kernel.db:
                await kernel.db.save_outline_job(pid, "generating", num_versions, _completed_count, versions,
                    f"已生成 {_completed_count}/{num_versions} 个版本...")

        await gs.generate_outline_versions(pid, num_versions=num_versions, volumes=num_volumes,
                                            on_progress=on_progress, tasks_ref=gen_tasks)

        _outline_jobs[pid]["status"] = "done"
        _outline_jobs[pid]["message"] = f"生成完成，共 {len(versions)} 个方案"
        _outline_jobs[pid]["ts"] = time.time()
        _outline_jobs[pid].pop("tasks", None)  # 清理任务引用
        if kernel.db:
            await kernel.db.save_outline_job(pid, "done", num_versions, num_versions, versions,
                f"生成完成，共 {len(versions)} 个方案")

    except asyncio.CancelledError:
        # 用户取消
        if pid in _outline_jobs:
            _outline_jobs[pid]["status"] = "cancelled"
            _outline_jobs[pid]["message"] = f"已取消，已生成 {len(versions)} 个方案"
            _outline_jobs[pid]["ts"] = time.time()
            _outline_jobs[pid].pop("tasks", None)  # 清理任务引用
        if kernel.db:
            try:
                await kernel.db.save_outline_job(pid, "cancelled", num_versions,
                    _outline_jobs.get(pid, {}).get("current", 0),
                    versions,
                    f"已取消，已生成 {len(versions)} 个方案")
            except Exception:
                pass
    except Exception as e:
        if pid in _outline_jobs:
            _outline_jobs[pid]["status"] = "error"
            _outline_jobs[pid]["message"] = f"生成失败: {str(e)[:200]}"
            _outline_jobs[pid]["ts"] = time.time()
            _outline_jobs[pid].pop("tasks", None)  # 清理任务引用
        if kernel.db:
            try:
                await kernel.db.save_outline_job(pid, "error", num_versions,
                    _outline_jobs.get(pid, {}).get("current", 0),
                    _outline_jobs.get(pid, {}).get("versions", []),
                    f"生成失败: {str(e)[:200]}")
            except Exception:
                pass


async def _auto_revise_from_gate(kernel, pid: str, ch_num: int, vol_num: int, content: str, gate_issues: list[dict], job_key: str):
    """门禁发现问题后自动调用 pipeline_editor 修订章节."""
    import logging
    logger = logging.getLogger(__name__)

    # 收集需要修复的问题
    problems = []
    for g in gate_issues:
        for issue in g.get("issues", []):
            if issue.get("severity") in ("error", "critical"):
                problems.append(f"[{g['gate']}] {issue['message']}")
    if not problems:
        return

    logger.info("门禁自动修订: 第%d章 发现 %d 个高严重度问题，开始修订", ch_num, len(problems))

    try:
        plugin = await kernel.get_plugin("pipeline-editor")
        result = await plugin.instance.run_pipeline(
            content=content,
            project_id=pid,
            chapter_num=ch_num,
            volume_number=vol_num,
            steps=["annotate", "coach"],  # 只跑编辑批注+写作优化，不做降重
            gate_issues=gate_issues,
        )
        revised = result.get("current_content", "")
        if not revised or revised == content:
            logger.info("门禁自动修订: 无实质改动，跳过保存")
            return

        # 保存修订后的版本（自动快照旧版本）
        cid = f"ch_v{vol_num:02d}_{ch_num:04d}"
        if kernel.db:
            await kernel.db.save_chapter(
                cid, pid, ch_num, f"第{ch_num}章", revised,
                volume=vol_num, snapshot_source="gate_auto_revise",
                snapshot_summary=f"门禁自动修订（{len(problems)}个问题）",
            )
        await kernel.write_project_file(pid, f"chapters/{cid}.md", revised)

        # 更新任务状态
        _chapter_jobs[job_key]["content"] = revised
        _chapter_jobs[job_key]["word_count"] = len(revised)
        logger.info("门禁自动修订完成: 第%d章，修订后 %d 字", ch_num, len(revised))
    except Exception as e:
        logger.warning("门禁自动修订异常: %s", e)


async def _run_chapter_generation(pid: str, ch_num: int, vol_num: int):
    """后台执行章节生成，完成后自动保存。不依赖 SSE 连接。"""
    key = _job_key(pid, vol_num, ch_num)
    try:
        kernel = await get_kernel()
        cw = await kernel.get_plugin("chapter-writer")
        ns = f"project:{pid}"

        chars = await kernel.db.get_characters(pid) if kernel.db else {}
        settings = await kernel.db.get_settings(pid) if kernel.db else {}
        platform = settings.get("platform", "fanqie") if settings else "fanqie"
        progress = settings.get("progress", {}) if settings else {}

        node = {"chapter_number": ch_num, "volume_number": vol_num, "title": f"第{ch_num}章", "summary": ""}
        for vol in progress.get("volumes", []):
            if vol.get("volume_number") == vol_num:
                for ch in vol.get("chapters", []):
                    if ch.get("chapter_number") == ch_num:
                        node = ch
                        break
                break

        # 上下文摘要（扩展窗口：前章800字 + 最近6章各200字摘要）
        prev = ""
        if ch_num > 1 and kernel.db:
            prev_ch = await kernel.db.get_chapter(pid, ch_num - 1, vol_num)
            if prev_ch:
                prev_content = prev_ch.get("content", "")
                last_part = prev_content[-800:] if len(prev_content) > 800 else prev_content
                prev = f"【第{vol_num}卷第{ch_num-1}章结尾（供参考，不必强制衔接）】\n{last_part}\n\n"
            summaries = []
            from models.project import memory_windows
            _sum_window, _ = memory_windows(settings.get("length", "long") if settings else "long")
            for n in range(max(1, ch_num - _sum_window), ch_num):
                ch = await kernel.db.get_chapter(pid, n, vol_num)
                if ch:
                    content = ch.get("content", "")
                    if len(content) > 200:
                        summaries.append(f"第{n}章概要: {content[:200]}...")
                    else:
                        summaries.append(f"第{n}章概要: {content}")
            if summaries:
                prev += "【近期章节概要】\n" + "\n".join(summaries)
                prev += "\n\n注意：可以写不同场景，但切回之前出现过的地点/角色时，状态要匹配。"
            else:
                prev += "【注意】本章为独立生成（前序章节尚未创作），请根据大纲自行建立场景和人物关系，确保内容自洽。\n\n"

        genre_tags = settings.get("genre_tags", []) if settings else []
        # 加载活跃伏笔（阶段3：按优先级+距今章数排序，超期标记，只注入最相关若干条）
        # 超期阈值随篇幅+体裁自适应（短篇收得紧）
        active_foreshadows = []
        try:
            import json as _json
            from models.foreshadow import rank_active_foreshadows
            from core.knowledge_resolver import overdue_gap as _overdue_gap
            _length = settings.get("length", "long") if settings else "long"
            _gap = _overdue_gap(_length, genre_tags)
            raw = await kernel.read_project_file(pid, "foreshadows.json")
            fs_data = _json.loads(raw)
            active_foreshadows = rank_active_foreshadows(
                fs_data.get("entries", {}), ch_num, overdue_gap=_gap
            )
        except (FileNotFoundError, json.JSONDecodeError):
            pass
        # 构建完整 settings 供 chapter_writer 读取 target_words_per_chapter 等
        writer_settings = {"meta": settings, "platform": platform}
        if genre_tags:
            writer_settings["genre_tags"] = genre_tags

        # 写作技巧检索（知识包）
        writing_tips = []
        try:
            tip_query = node.get("summary", "") or f"第{ch_num}章写作技巧"
            writing_tips = await kernel.rag_retrieve(
                query=tip_query, project_id=pid, top_k=3, categories=["writing_tip"],
            )
        except Exception:
            pass

        # 按篇幅注入方法论包 + 通用写作技巧包 + 题材技能包
        _METHOD_PACKS = {"short": "short-story-writing", "medium": "novel-writing", "long": "novel-templates", "extra_long": "novel-templates"}
        _UNIVERSAL_PACKS = ["writing-master", "writing-tutorial", "writing-workflow", "novel-writing-skills"]
        try:
            from pathlib import Path

            from core import knowledge_resolver as kr

            def _read_pack(name):
                pack_dir = Path("knowledge_base/packs") / name / "content"
                if not pack_dir.exists():
                    return []
                out = []
                for f in sorted(pack_dir.glob("*.md")):
                    text = f.read_text(encoding="utf-8").strip()
                    if text:
                        out.append({"content": text, "category": "writing_tip", "metadata": {"source": f"pack:{name}", "file": f.name}})
                return out

            length = settings.get("length", "long") if settings else "long"
            method_tips = _read_pack(_METHOD_PACKS.get(length, "novel-templates"))
            for name in _UNIVERSAL_PACKS:
                method_tips.extend(_read_pack(name))
            # 题材技能包（约定式自动发现：阶段提示 + 靶值 + 体裁红线）
            for text in kr.genre_stage_prompt(genre_tags, "创建小说正文"):
                method_tips.append({"content": text[:2000], "category": "writing_tip", "metadata": {"source": "genre:stage_prompt", "file": "创建小说正文.prompt.md"}})
            targets = kr.genre_targets(genre_tags)
            if targets:
                tv_lines = "\n".join(f"- {k}：{v}" for k, v in targets.items())
                method_tips.append({"content": f"## 本体裁量化靶值（按此取值，非通用默认）\n{tv_lines}", "category": "writing_tip", "metadata": {"source": "genre:targets", "file": "靶值.md"}})
            for block in kr.genre_boundaries(genre_tags):
                method_tips.append({"content": block[:1500], "category": "writing_tip", "metadata": {"source": "genre:boundary", "file": "题材边界/靶值§二"}})
            writing_tips = method_tips + writing_tips
        except Exception:
            pass

        prompt = cw.instance._build_user_prompt(node, {
            "characters": chars, "previous_chapters_summary": prev,
            "genre_tags": genre_tags, "settings": writer_settings,
            "active_foreshadows": active_foreshadows,
            "writing_tips": writing_tips,
        }, platform, "", "")

        system_prompt = cw.instance._build_system_prompt(platform)
        full = ""
        async for token in kernel.call_llm_stream(
            [{"role": "system", "content": system_prompt}, {"role": "user", "content": prompt}],
            tier="premium", max_tokens=16384,
        ):
            full += token
            _chapter_jobs[key]["content"] = full
            _chapter_jobs[key]["tokens"].append(token)

        # LLM 生成完成，立即保存并通知前端
        cid = f"ch_v{vol_num:02d}_{ch_num:04d}"
        if kernel.db:
            await kernel.db.save_chapter(
                cid, pid, ch_num, node.get("title", f"第{ch_num}章"), full,
                volume=vol_num, snapshot_source="generate", snapshot_summary="流式生成新版本前自动快照",
            )
            from datetime import datetime
            await kernel.db.update_project(pid, {"current_chapter": max(ch_num, 0), "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")})
        await kernel.write_project_file(pid, f"chapters/{cid}.md", full)

        # 立即设置 saved 状态，让前端更新大纲树
        _chapter_jobs[key]["status"] = "saved"
        _chapter_jobs[key]["word_count"] = len(full)
        _chapter_jobs[key]["ts"] = time.time()

        # 后续处理（门禁检查、账本更新、伏笔提取）异步执行，不阻塞前端
        async def _post_process():
            try:
                # 门禁链检查
                gate_issues = []
                from core.quality_gate import GateChainExecutor, GateChainConfig, IQualityGate, GateVerdict
                gates = []
                for entry in await kernel._plugin_manager.list_active():
                    if isinstance(entry.instance, IQualityGate):
                        gates.append(entry.instance)
                if gates:
                    chain = GateChainExecutor(GateChainConfig(gates=gates))
                    gate_result = await chain.execute(
                        {"content": full},
                        {"settings": settings, "characters": chars, "facts": {}, "foreshadows": {}},
                    )
                    gate_issues = [
                        {"gate": g.gate_name, "verdict": g.verdict.value, "score": g.score, "issues": [
                            {"severity": i.severity.value, "code": i.code, "message": i.message}
                            for i in g.issues
                        ]}
                        for g in gate_result.gates
                    ]
                    _chapter_jobs[key]["gate_issues"] = gate_issues
                    # 持久化到数据库
                    if gate_issues and kernel.db:
                        try:
                            await kernel.db.save_gate_results(pid, ch_num, vol_num, gate_issues)
                        except Exception:
                            pass

                    # 门禁发现问题时自动修订（仅当有 REVISE 裁决且含高严重度问题时）
                    has_revise = any(g.verdict == GateVerdict.REVISE for g in gate_result.gates)
                    has_high = any(
                        i.severity.value in ("error", "critical")
                        for g in gate_result.gates for i in g.issues
                    )
                    if has_revise and has_high:
                        try:
                            await _auto_revise_from_gate(kernel, pid, ch_num, vol_num, full, gate_issues, key)
                        except Exception as exc:
                            import logging
                            logging.getLogger(__name__).warning("门禁自动修订失败: %s", exc)
            except Exception as exc:
                import logging
                logging.getLogger(__name__).warning("门禁链检查失败: %s", exc)

            try:
                # 更新一致性账本
                from core.orchestrator import update_consistency_ledger_standalone
                await update_consistency_ledger_standalone(kernel, pid, full, ch_num, vol_num)
            except Exception as exc:
                import logging
                logging.getLogger(__name__).warning("账本更新失败: %s", exc)

            try:
                # 自动提取伏笔
                await cw.instance._extract_and_save_foreshadows(pid, full, ch_num, vol_num)
            except Exception as exc:
                import logging
                logging.getLogger(__name__).warning("伏笔提取失败: %s", exc)

            # 运行流水线贡献者插件（IPipelineContributor），结果存 DB 供编辑优化时使用
            try:
                import asyncio as _aio
                from core.quality_gate import IPipelineContributor
                contributors = []
                for entry in await kernel._plugin_manager.list_active():
                    if isinstance(entry.instance, IPipelineContributor):
                        contributors.append(entry.instance)
                if contributors:
                    platform = await kernel.context().get(f"project:{pid}", "platform", "fanqie")
                    ctx = {"project_id": pid, "chapter_num": ch_num, "volume_number": vol_num, "platform": platform, "kernel": kernel}
                    tasks = [c.analyze(full, ctx) for c in contributors]
                    results = await _aio.gather(*tasks, return_exceptions=True)
                    cr_list = []
                    for c, r in zip(contributors, results):
                        if isinstance(r, Exception):
                            logging.getLogger(__name__).warning("贡献者 %s 失败: %s", c.name, r)
                            continue
                        r["name"] = c.name
                        cr_list.append(r)
                    if cr_list and kernel.db:
                        await kernel.db.save_contributor_results(pid, ch_num, vol_num, cr_list)
            except Exception as exc:
                import logging
                logging.getLogger(__name__).warning("贡献者执行失败: %s", exc)

        # 异步执行后续处理，不阻塞
        asyncio.create_task(_post_process())

    except Exception as e:
        _chapter_jobs[key]["status"] = "error"
        _chapter_jobs[key]["error"] = str(e)[:200]
        _chapter_jobs[key]["ts"] = time.time()


class StreamChapterReq(BaseModel):
    project_id: str = ""
    chapter_number: int = Field(..., ge=1)
    volume_number: int = Field(default=1, ge=1)
    force: bool = Field(default=False, description="强制重新生成，即使章节已存在")


@router.post("/api/v1/stream/chapter")
async def stream_chapter(data: StreamChapterReq):
    """流式生成章节。

    后台任务模式：LLM 生成在独立 Task 中运行，SSE 只推送进度。
    客户端断开不影响生成完成。
    """
    pid = data.project_id
    ch_num = data.chapter_number
    vol_num = data.volume_number
    force = data.force
    key = _job_key(pid, vol_num, ch_num)

    # 如果强制重新生成，清除旧任务状态
    if force and key in _chapter_jobs:
        del _chapter_jobs[key]

    # 如果已有任务在运行或已完成，直接复用
    existing = _chapter_jobs.get(key)
    if existing and existing["status"] == "generating":
        pass  # 已在运行，SSE 会接上
    elif existing and existing["status"] == "saved" and not force:
        # 已完成且非强制模式，直接返回结果
        async def gen_done():
            yield f"data: {json.dumps({'status':'start'})}\n\n"
            yield f"data: {json.dumps({'token': existing['content']})}\n\n"
            yield f"data: {json.dumps({'status':'saved','word_count':existing['word_count']})}\n\n"
            yield f"data: {json.dumps({'status':'done'})}\n\n"
        return StreamingResponse(gen_done(), media_type="text/event-stream",
                                 headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"})
    else:
        # 启动新的后台生成任务
        _chapter_jobs[key] = {
            "status": "generating", "content": "", "tokens": [],
            "error": "", "word_count": 0, "ts": time.time(),
        }
        asyncio.create_task(_run_chapter_generation(pid, ch_num, vol_num))

    # SSE 推送进度
    async def gen():
        yield f"data: {json.dumps({'status':'start'})}\n\n"
        sent_idx = 0
        try:
            while True:
                job = _chapter_jobs.get(key)
                if not job:
                    yield f"data: {json.dumps({'error':'任务丢失'})}\n\n"
                    break

                # 推送新 token
                tokens = job["tokens"]
                while sent_idx < len(tokens):
                    yield f"data: {json.dumps({'token': tokens[sent_idx]})}\n\n"
                    sent_idx += 1

                if job["status"] == "saved":
                    yield f"data: {json.dumps({'status':'saved','word_count':job['word_count']})}\n\n"
                    break
                elif job["status"] == "error":
                    yield f"data: {json.dumps({'error': job['error']})}\n\n"
                    break

                await asyncio.sleep(0.2)
        except asyncio.CancelledError:
            # 客户端断开——不中断后台任务，只停止 SSE
            pass
        yield f"data: {json.dumps({'status':'done'})}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"})


@router.get("/api/v1/stream/chapter/status/{project_id}/{vol_num}/{ch_num}")
async def chapter_generation_status(project_id: str, vol_num: int, ch_num: int):
    """查询章节生成状态（用于前端恢复）。"""
    key = _job_key(project_id, vol_num, ch_num)
    job = _chapter_jobs.get(key)
    if not job:
        return {"status": "idle", "message": "无进行中的生成任务"}
    return {
        "status": job["status"],
        "content_length": len(job.get("content", "")),
        "word_count": job.get("word_count", 0),
        "error": job.get("error", ""),
    }


# ---------------------------------------------------------------------------
# 大纲生成（保持原样——大纲生成时间短，断开重试即可）
# ---------------------------------------------------------------------------


@router.post("/api/v1/stream/outline")
async def stream_outline(data: dict):
    """流式生成大纲（单版本） — 通过 GenerationService 统一入口."""
    kernel = await get_kernel()
    pid = data.get("project_id", "")

    async def gen():
        yield f"data: {json.dumps({'status':'progress','message':'正在分析项目...'})}\n\n"
        try:
            from core.generation_service import GenerationService
            gs = GenerationService(kernel)

            yield f"data: {json.dumps({'status':'progress','message':'正在规划卷结构...'})}\n\n"

            result = await gs.generate_outline(pid)
            if not result.success:
                yield f"data: {json.dumps({'error': result.error[:200]})}\n\n"
            else:
                # 保存到 3 个存储
                await gs.save_outline(pid, result.progress)
                total_chs = sum(len(v.get("chapters", [])) for v in result.progress.get("volumes", []))
                yield f"data: {json.dumps({'status':'saved','volumes':len(result.progress.get('volumes',[])),'chapters':total_chs})}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'error': str(e)[:200]})}\n\n"
        yield f"data: {json.dumps({'status':'done'})}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"})


@router.post("/api/v1/stream/outline-multi")
async def stream_outline_multi(data: dict):
    """流式生成多版本大纲 — 通过 GenerationService 统一入口."""
    kernel = await get_kernel()
    pid = data.get("project_id", "")
    num_versions = data.get("versions", 3)

    async def gen():
        yield f"data: {json.dumps({'status':'progress','message':'正在分析项目...'})}\n\n"
        try:
            from core.generation_service import GenerationService, STYLE_HINTS
            gs = GenerationService(kernel)

            for vi in range(num_versions):
                yield f"data: {json.dumps({'status':'progress','message':f'正在生成版本 {vi+1}/{num_versions}...'})}\n\n"

                hint = STYLE_HINTS[vi % len(STYLE_HINTS)]
                result = await gs.generate_outline(pid, style_hint=hint)

                if result.success:
                    total_chs = sum(len(v.get("chapters", [])) for v in result.progress.get("volumes", []))
                    style_tag = hint.split("，")[0].replace("风格偏向", "") if hint else ""
                    yield f"data: {json.dumps({'status':'version_ready','version':vi+1,'data':result.progress,'volumes':len(result.progress.get('volumes',[])),'chapters':total_chs,'style_tag':style_tag})}\n\n"
                else:
                    yield f"data: {json.dumps({'status':'version_error','version':vi+1,'message':f'版本 {vi+1} 生成失败: {result.error[:100]}'})}\n\n"

        except Exception as e:
            yield f"data: {json.dumps({'error': str(e)[:200]})}\n\n"
        yield f"data: {json.dumps({'status':'done'})}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"})


# ---------------------------------------------------------------------------
# 大纲生成 — 后端异步任务（支持页面切换后恢复状态）
# ---------------------------------------------------------------------------


@router.post("/api/v1/outline/generate-async")
async def outline_generate_async(data: dict):
    """启动大纲生成后台任务（不依赖 SSE 连接）.

    body: {project_id: str, versions: int = 3}
    返回任务状态，前端可轮询 /api/v1/outline/status/{pid} 查询进度。
    """
    pid = data.get("project_id", "")
    num_versions = data.get("versions", 3)
    num_volumes = data.get("volumes")  # 1=不分卷, None=自动分卷
    if not pid:
        return {"error": "需要 project_id"}

    # 如果已有任务在运行（内存中），直接返回状态
    if pid in _outline_jobs and _outline_jobs[pid].get("status") == "generating":
        return {"status": "already_running", **_outline_jobs[pid]}

    # 检查数据库中是否有进行中的任务
    kernel = await get_kernel()
    if kernel.db:
        db_job = await kernel.db.get_outline_job(pid)
        if db_job and db_job.get("status") == "generating":
            # 恢复到内存并继续
            _outline_jobs[pid] = {
                "status": db_job["status"],
                "versions": db_job.get("versions", []),
                "current": db_job.get("current", 0),
                "total": db_job.get("total", 3),
                "message": db_job.get("message", ""),
                "ts": time.time(),
            }
            # 重新启动生成任务，从上次完成的版本继续
            asyncio.create_task(_run_outline_generation(pid, num_versions, num_volumes))
            return {"status": "resumed", "message": "恢复中断的大纲生成"}

    # 启动后台任务
    asyncio.create_task(_run_outline_generation(pid, num_versions, num_volumes))
    return {"status": "started", "message": "大纲生成已启动"}


@router.get("/api/v1/outline/status/{pid}")
async def outline_status(pid: str):
    """查询大纲生成任务状态.

    返回:
    - status: "generating" | "done" | "error" | "not_found"
    - versions: 已生成的版本列表（done 时可用）
    - current: 当前正在生成第几个版本
    - total: 总共要生成几个版本
    - message: 状态描述
    """
    # 先检查内存缓存
    if pid in _outline_jobs:
        job = _outline_jobs[pid]
        return {
            "status": job["status"],
            "versions": job.get("versions", []),
            "current": job.get("current", 0),
            "total": job.get("total", 0),
            "message": job.get("message", ""),
            "ts": job.get("ts", 0),
        }

    # 内存中没有，检查数据库（支持服务重启后恢复）
    kernel = await get_kernel()
    if kernel.db:
        db_job = await kernel.db.get_outline_job(pid)
        if db_job:
            return {
                "status": db_job["status"],
                "versions": db_job.get("versions", []),
                "current": db_job.get("current", 0),
                "total": db_job.get("total", 0),
                "message": db_job.get("message", ""),
                "from_db": True,
            }

    return {"status": "not_found", "message": "没有进行中的大纲生成任务"}


@router.post("/api/v1/outline/cancel/{pid}")
async def outline_cancel(pid: str):
    """取消正在进行的大纲生成任务 — 立即取消所有子任务."""
    if pid in _outline_jobs and _outline_jobs[pid].get("status") == "generating":
        _outline_jobs[pid]["cancel_requested"] = True
        _outline_jobs[pid]["message"] = "正在取消..."
        # 立即取消所有正在运行的 asyncio 任务
        tasks = _outline_jobs[pid].get("tasks", [])
        for t in tasks:
            if not t.done():
                t.cancel()
        return {"status": "cancelling", "message": "已发送取消请求"}
    return {"status": "not_found", "message": "没有进行中的大纲生成任务"}


@router.delete("/api/v1/outline/status/{pid}")
async def outline_status_clear(pid: str):
    """清除大纲生成任务状态（前端在用户确认后调用）."""
    if pid in _outline_jobs:
        del _outline_jobs[pid]
    # 同时清除数据库中的状态
    kernel = await get_kernel()
    if kernel.db:
        await kernel.db.delete_outline_job(pid)
    return {"status": "cleared"}
