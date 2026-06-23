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


async def _run_outline_generation(pid: str, num_versions: int = 3):
    """后台执行大纲生成 — 通过 GenerationService 统一入口."""
    from core.generation_service import GenerationService, STYLE_HINTS

    try:
        kernel = await get_kernel()
        gs = GenerationService(kernel)
        versions = []
        _outline_jobs[pid] = {
            "status": "generating", "versions": versions, "current": 0, "total": num_versions,
            "message": "正在分析项目...", "ts": time.time(),
        }

        if kernel.db:
            await kernel.db.save_outline_job(pid, "generating", num_versions, 0, [], "正在分析项目...")

        # 通过 GenerationService 生成多版本
        async def on_progress(vi: int, result):
            _outline_jobs[pid]["current"] = vi + 1
            _outline_jobs[pid]["message"] = f"正在生成版本 {vi + 1}/{num_versions}..."
            _outline_jobs[pid]["ts"] = time.time()
            if kernel.db:
                await kernel.db.save_outline_job(pid, "generating", num_versions, vi + 1, versions,
                    f"正在生成版本 {vi + 1}/{num_versions}...")

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
                if kernel.db:
                    await kernel.db.save_outline_job(pid, "generating", num_versions, vi + 1, versions,
                        f"已生成 {len(versions)}/{num_versions} 个版本...")

        await gs.generate_outline_versions(pid, num_versions=num_versions, on_progress=on_progress)

        _outline_jobs[pid]["status"] = "done"
        _outline_jobs[pid]["message"] = f"生成完成，共 {len(versions)} 个方案"
        _outline_jobs[pid]["ts"] = time.time()
        if kernel.db:
            await kernel.db.save_outline_job(pid, "done", num_versions, num_versions, versions,
                f"生成完成，共 {len(versions)} 个方案")

    except Exception as e:
        if pid in _outline_jobs:
            _outline_jobs[pid]["status"] = "error"
            _outline_jobs[pid]["message"] = f"生成失败: {str(e)[:200]}"
            _outline_jobs[pid]["ts"] = time.time()
        if kernel.db:
            try:
                await kernel.db.save_outline_job(pid, "error", num_versions,
                    _outline_jobs.get(pid, {}).get("current", 0),
                    _outline_jobs.get(pid, {}).get("versions", []),
                    f"生成失败: {str(e)[:200]}")
            except Exception:
                pass


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
            for n in range(max(1, ch_num - 6), ch_num):
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
        # 加载活跃伏笔
        active_foreshadows = []
        try:
            import json as _json
            raw = await kernel.read_project_file(pid, "foreshadows.json")
            fs_data = _json.loads(raw)
            active_foreshadows = [
                fs for fs in fs_data.get("entries", {}).values()
                if isinstance(fs, dict) and fs.get("status") in ("planted", "building")
            ]
        except (FileNotFoundError, json.JSONDecodeError):
            pass
        # 构建完整 settings 供 chapter_writer 读取 target_words_per_chapter 等
        writer_settings = {"meta": settings, "platform": platform}
        if genre_tags:
            writer_settings["genre_tags"] = genre_tags
        prompt = cw.instance._build_user_prompt(node, {
            "characters": chars, "previous_chapters_summary": prev,
            "genre_tags": genre_tags, "settings": writer_settings,
            "active_foreshadows": active_foreshadows,
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

        # 保存到数据库 + 文件（save_chapter 自动快照旧版本）
        cid = f"ch_v{vol_num:02d}_{ch_num:04d}"
        if kernel.db:
            await kernel.db.save_chapter(
                cid, pid, ch_num, node.get("title", f"第{ch_num}章"), full,
                volume=vol_num, snapshot_source="generate", snapshot_summary="流式生成新版本前自动快照",
            )
            await kernel.db.update_project(pid, {"current_chapter": max(ch_num, 0), "updated_at": ""})
        await kernel.write_project_file(pid, f"chapters/{cid}.md", full)

        # 更新一致性账本
        try:
            from core.orchestrator import update_consistency_ledger_standalone
            await update_consistency_ledger_standalone(kernel, pid, full, ch_num, vol_num)
        except Exception as exc:
            import logging
            logging.getLogger(__name__).warning("账本更新失败: %s", exc)

        # 自动提取伏笔
        try:
            await cw.instance._extract_and_save_foreshadows(pid, full, ch_num)
        except Exception as exc:
            import logging
            logging.getLogger(__name__).warning("伏笔提取失败: %s", exc)

        _chapter_jobs[key]["status"] = "saved"
        _chapter_jobs[key]["word_count"] = len(full)
        _chapter_jobs[key]["ts"] = time.time()

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
            asyncio.create_task(_run_outline_generation(pid, num_versions))
            return {"status": "resumed", "message": "恢复中断的大纲生成"}

    # 启动后台任务
    asyncio.create_task(_run_outline_generation(pid, num_versions))
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
