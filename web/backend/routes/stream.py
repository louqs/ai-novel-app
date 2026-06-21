"""SSE 流式生成 — 章节 + 大纲."""

import json
from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from web.backend.dependencies import get_kernel

router = APIRouter(tags=["stream"])

class StreamChapterReq(BaseModel):
    project_id: str = ""
    chapter_number: int = Field(..., ge=1)
    volume_number: int = Field(default=1, ge=1)


@router.post("/api/v1/stream/chapter")
async def stream_chapter(data: StreamChapterReq):
    """流式生成章节。"""
    kernel = await get_kernel()
    pid = data.project_id
    ch_num = data.chapter_number
    vol_num = data.volume_number

    async def gen():
        yield f"data: {json.dumps({'status':'start'})}\n\n"
        try:
            cw = await kernel.get_plugin("chapter-writer")
            ns = f"project:{pid}"
            # 从数据库加载上下文
            chars = await kernel.db.get_characters(pid) if kernel.db else {}
            settings = await kernel.db.get_settings(pid) if kernel.db else {}
            platform = settings.get("platform", "fanqie") if settings else "fanqie"
            progress = settings.get("progress", {}) if settings else {}

            node = {"chapter_number": ch_num, "volume_number": vol_num, "title": f"第{ch_num}章", "summary": ""}
            for vol in progress.get("volumes", []):
                if vol.get("volume_number") == vol_num:
                    for ch in vol.get("chapters", []):
                        if ch.get("chapter_number") == ch_num:
                            node = ch; break
                    break

            # 上下文摘要——提供前情但不强制衔接（按卷号隔离）
            prev = ""
            if ch_num > 1 and kernel.db:
                # 上一章结尾（同卷内，场景状态参考——不需要强制衔接）
                prev_ch = await kernel.db.get_chapter(pid, ch_num - 1, vol_num)
                if prev_ch:
                    prev_content = prev_ch.get("content", "")
                    last_part = prev_content[-400:] if len(prev_content) > 400 else prev_content
                    prev = f"【第{vol_num}卷第{ch_num-1}章结尾（供参考，不必强制衔接）】\n{last_part}\n\n"
                # 前几章摘要（同卷内）
                summaries = []
                for n in range(max(1, ch_num - 4), ch_num):
                    ch = await kernel.db.get_chapter(pid, n, vol_num)
                    if ch:
                        content = ch.get("content", "")
                        if len(content) > 150:
                            summaries.append(f"第{n}章概要: {content[:150]}...")
                        else:
                            summaries.append(f"第{n}章概要: {content}")
                if summaries:
                    prev += "【近期章节概要】\n" + "\n".join(summaries)
                    prev += "\n\n注意：可以写不同场景，但切回之前出现过的地点/角色时，状态要匹配。"

            genre_tags = settings.get("genre_tags", []) if settings else []
            prompt = cw.instance._build_user_prompt(node, {
                "characters": chars, "previous_chapters_summary": prev,
                "genre_tags": genre_tags,
            }, platform, "", "")

            system_prompt = cw.instance._build_system_prompt(platform)
            full = ""
            async for token in kernel.call_llm_stream(
                [{"role": "system", "content": system_prompt}, {"role": "user", "content": prompt}],
                tier="premium", max_tokens=8192,
            ):
                full += token
                yield f"data: {json.dumps({'token': token})}\n\n"

            # 保存到数据库 + 文件
            cid = f"ch_v{vol_num:02d}_{ch_num:04d}"
            if kernel.db:
                await kernel.db.save_chapter(cid, pid, ch_num, node.get("title", f"第{ch_num}章"), full, volume=vol_num)
                # 更新项目当前进度
                await kernel.db.update_project(pid, {"current_chapter": max(ch_num, 0), "updated_at": ""})
            await kernel.write_project_file(pid, f"chapters/{cid}.md", full)
            yield f"data: {json.dumps({'status':'saved','word_count':len(full)})}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'error': str(e)[:200]})}\n\n"
        yield f"data: {json.dumps({'status':'done'})}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"})


@router.post("/api/v1/stream/outline")
async def stream_outline(data: dict):
    """流式生成大纲。"""
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
