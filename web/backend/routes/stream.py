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

        # 保存到数据库 + 文件
        cid = f"ch_v{vol_num:02d}_{ch_num:04d}"
        if kernel.db:
            await kernel.db.save_chapter(cid, pid, ch_num, node.get("title", f"第{ch_num}章"), full, volume=vol_num)
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


@router.post("/api/v1/stream/chapter")
async def stream_chapter(data: StreamChapterReq):
    """流式生成章节。

    后台任务模式：LLM 生成在独立 Task 中运行，SSE 只推送进度。
    客户端断开不影响生成完成。
    """
    pid = data.project_id
    ch_num = data.chapter_number
    vol_num = data.volume_number
    key = _job_key(pid, vol_num, ch_num)

    # 如果已有任务在运行或已完成，直接复用
    existing = _chapter_jobs.get(key)
    if existing and existing["status"] == "generating":
        pass  # 已在运行，SSE 会接上
    elif existing and existing["status"] == "saved":
        # 已完成，直接返回结果
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
    """流式生成大纲（单版本，向后兼容）。"""
    kernel = await get_kernel()
    pid = data.get("project_id", "")

    async def gen():
        yield f"data: {json.dumps({'status':'progress','message':'正在分析项目...'})}\n\n"
        try:
            ns = f"project:{pid}"
            chars = await kernel.db.get_characters(pid) if kernel.db else {}
            meta = await kernel.db.get_project(pid) if kernel.db else {}
            platform = meta.get("platform", "fanqie") if meta else "fanqie"
            one_liner = meta.get("one_liner", "") if meta else ""

            char_list = []
            for cid, c in chars.get("characters", {}).items():
                if isinstance(c, dict):
                    char_list.append(f"{c.get('name',cid)}: {'/'.join(c.get('personality_tags',[])[:3])}")
            char_text = "\n".join(char_list) if char_list else "暂无人物"

            yield f"data: {json.dumps({'status':'progress','message':'正在规划卷结构...'})}\n\n"
            prompt = f"规划2卷大纲，每卷10-15章。梗概:{one_liner}。人物:{char_text}。平台:{platform}。返回JSON:{{\"volumes\":[{{\"volume_number\":1,\"title\":\"\",\"arc_description\":\"\",\"chapters\":[{{\"chapter_number\":1,\"title\":\"\",\"summary\":\"\",\"key_events\":[],\"is_climax\":false,\"is_hook_point\":false}}]}}]}}"

            full = ""
            async for token in kernel.call_llm_stream(
                [{"role": "system", "content": "你是网文大纲策划。只返回JSON。"}, {"role": "user", "content": prompt}],
                tier="standard", max_tokens=8000,
            ):
                full += token
                yield f"data: {json.dumps({'token': token, 'stage': 'outline'})}\n\n"

            # 解析JSON
            import re
            m = re.search(r'\{[\s\S]*\}', full)
            if m:
                try:
                    progress = json.loads(m.group())
                    await kernel.context().set(ns, "progress", progress)
                    await kernel.write_project_file(pid, "progress.json", json.dumps(progress, indent=2, ensure_ascii=False))
                    # Also save to settings for the workbench
                    if kernel.db:
                        settings = await kernel.db.get_settings(pid)
                        settings["progress"] = progress
                        await kernel.db.save_settings(pid, settings)
                    total_chs = sum(len(v.get("chapters",[])) for v in progress.get("volumes",[]))
                    yield f"data: {json.dumps({'status':'saved','volumes':len(progress.get('volumes',[])),'chapters':total_chs})}\n\n"
                except Exception:
                    pass
        except Exception as e:
            yield f"data: {json.dumps({'error': str(e)[:200]})}\n\n"
        yield f"data: {json.dumps({'status':'done'})}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"})


@router.post("/api/v1/stream/outline-multi")
async def stream_outline_multi(data: dict):
    """流式生成多版本大纲。"""
    kernel = await get_kernel()
    pid = data.get("project_id", "")
    num_versions = data.get("versions", 3)

    async def gen():
        yield f"data: {json.dumps({'status':'progress','message':'正在分析项目...'})}\n\n"
        try:
            chars = await kernel.db.get_characters(pid) if kernel.db else {}
            meta = await kernel.db.get_project(pid) if kernel.db else {}
            platform = meta.get("platform", "fanqie") if meta else "fanqie"
            one_liner = meta.get("one_liner", "") if meta else ""

            char_list = []
            for cid, c in chars.get("characters", {}).items():
                if isinstance(c, dict):
                    char_list.append(f"{c.get('name',cid)}: {'/'.join(c.get('personality_tags',[])[:3])}")
            char_text = "\n".join(char_list) if char_list else "暂无人物"

            style_hints = [
                "风格偏向热血爽文，节奏快，冲突密集",
                "风格偏向细腻情感，人物关系复杂，伏笔层层递进",
                "风格偏向悬念推理，每章结尾反转，层层揭秘",
                "风格偏向轻松日常，温馨治愈，穿插幽默",
                "风格偏向史诗宏大，世界观广阔，势力博弈",
            ]

            for vi in range(num_versions):
                yield f"data: {json.dumps({'status':'progress','message':f'正在生成版本 {vi+1}/{num_versions}...'})}\n\n"

                hint = style_hints[vi % len(style_hints)]
                prompt = (
                    f"规划2卷大纲，每卷10-15章。梗概:{one_liner}。人物:{char_text}。平台:{platform}。"
                    f"特殊要求:{hint}。"
                    f"返回JSON:{{\"volumes\":[{{\"volume_number\":1,\"title\":\"\",\"arc_description\":\"\","
                    f"\"chapters\":[{{\"chapter_number\":1,\"title\":\"\",\"summary\":\"\",\"key_events\":[],\"is_climax\":false,\"is_hook_point\":false}}]}}]}}"
                )

                full = ""
                # 用不同 temperature 增加多样性
                temps = [0.7, 0.85, 0.9, 0.95, 1.0]
                temp = temps[vi % len(temps)]

                async for token in kernel.call_llm_stream(
                    [{"role": "system", "content": "你是网文大纲策划。只返回JSON。"}, {"role": "user", "content": prompt}],
                    tier="standard", max_tokens=8000, temperature=temp,
                ):
                    full += token

                # 解析 JSON — 先尝试 code fence，再尝试裸 JSON
                import re
                progress = None
                # 优先匹配 ```json ... ``` 代码块
                m = re.search(r'```(?:json)?\s*(\{[\s\S]*?\})\s*```', full)
                if m:
                    try:
                        progress = json.loads(m.group(1))
                    except Exception:
                        pass
                # 降级：匹配最外层 {}
                if not progress:
                    m2 = re.search(r'\{[\s\S]*\}', full)
                    if m2:
                        try:
                            progress = json.loads(m2.group())
                        except Exception:
                            pass
                if progress and "volumes" in progress:
                    total_chs = sum(len(v.get("chapters", [])) for v in progress.get("volumes", []))
                    style_tag = hint.split("，")[0].replace("风格偏向", "") if hint else ""
                    yield f"data: {json.dumps({'status':'version_ready','version':vi+1,'data':progress,'volumes':len(progress.get('volumes',[])),'chapters':total_chs,'style_tag':style_tag})}\n\n"
                else:
                    yield f"data: {json.dumps({'status':'version_error','version':vi+1,'message':f'版本 {vi+1} 解析失败，已跳过'})}\n\n"

        except Exception as e:
            yield f"data: {json.dumps({'error': str(e)[:200]})}\n\n"
        yield f"data: {json.dumps({'status':'done'})}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"})
