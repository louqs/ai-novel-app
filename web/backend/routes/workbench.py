"""创作工作台 API — 大纲生成、章节保存、人物列表."""

from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from starlette.status import HTTP_404_NOT_FOUND

from web.backend.dependencies import get_kernel

router = APIRouter(tags=["workbench"])


class ChapterSaveRequest(BaseModel):
    content: str
    volume_number: int = 1


# =============================================================================
# 大纲
# =============================================================================


@router.get("/api/v1/projects/{project_id}/outline", response_model=dict)
async def get_outline(project_id: str):
    """获取项目大纲."""
    kernel = await get_kernel()
    ns = f"project:{project_id}"
    progress = await kernel.context().get(ns, "progress")
    if progress:
        return progress
    # Try from file
    try:
        raw = await kernel.read_project_file(project_id, "progress.json")
        return json.loads(raw)
    except FileNotFoundError:
        return {"volumes": [], "message": "暂无大纲，请先生成"}


@router.put("/api/v1/projects/{project_id}/outline", response_model=dict)
async def save_outline(project_id: str, data: dict):
    """手动保存/更新大纲."""
    kernel = await get_kernel()
    ns = f"project:{project_id}"
    await kernel.context().set(ns, "progress", data)
    await kernel.write_project_file(project_id, "progress.json", json.dumps(data, indent=2, ensure_ascii=False))
    if kernel.db:
        settings = await kernel.db.get_settings(project_id)
        settings["progress"] = data
        await kernel.db.save_settings(project_id, settings)
    return {"status": "saved"}


@router.post("/api/v1/projects/{project_id}/generate/outline", response_model=dict)
async def generate_outline(project_id: str):
    """生成项目大纲."""
    kernel = await get_kernel()
    # 获取项目设定用于大纲生成
    ns = f"project:{project_id}"
    settings = await kernel.context().get_namespace(ns)
    platform = await kernel.context().get(ns, "platform", "fanqie")

    try:
        op = await kernel.get_plugin("outline-planner")
        progress = await op.instance.plan_outline(
            settings=settings,
            characters=await kernel.context().get(ns, "characters", {}),
            direction={"logline": settings.get("one_liner", ""), "genre_tags": settings.get("genre_tags", [])},
            platform=platform,
            total_chapters=30,
            volumes=2,
        )
        # 保存
        data = progress.model_dump()
        await kernel.context().set(ns, "progress", data)
        await kernel.write_project_file(project_id, "progress.json", json.dumps(data, indent=2, ensure_ascii=False))
        return data
    except Exception as e:
        import traceback
        raise HTTPException(status_code=500, detail=f"大纲生成失败: {str(e)[:200]}")


# =============================================================================
# 章节
# =============================================================================


@router.put("/api/v1/projects/{project_id}/chapters/{ch_num}", response_model=dict)
async def save_chapter(project_id: str, ch_num: int, data: ChapterSaveRequest):
    """手动保存/更新章节内容."""
    kernel = await get_kernel()
    vol = data.volume_number
    chapter_id = f"ch_v{vol:02d}_{ch_num:04d}"
    # 数据库
    if kernel.db:
        await kernel.db.save_chapter(chapter_id, project_id, ch_num, f"第{vol}卷第{ch_num}章", data.content, volume=vol)
        await kernel.db.update_project(project_id, {"current_chapter": max(ch_num,
            (await kernel.db.get_project(project_id) or {}).get("current_chapter", 0))})
    # 文件
    await kernel.write_project_file(project_id, f"chapters/{chapter_id}.md", data.content)
    ns = f"project:{project_id}"
    await kernel.context().set(ns, "current_chapter", max(ch_num, await kernel.context().get(ns, "current_chapter", 0)))
    return {"status": "saved", "chapter_id": chapter_id, "volume_number": vol, "word_count": len(data.content)}


# =============================================================================
# 人物
# =============================================================================


@router.get("/api/v1/projects/{project_id}/characters", response_model=dict)
async def list_characters(project_id: str):
    """列出项目人物."""
    kernel = await get_kernel()
    ns = f"project:{project_id}"
    chars = await kernel.context().get(ns, "characters", {})
    return chars if chars else {"characters": {}, "message": "暂无人物"}


# =============================================================================
# Skills (内联实现)
# =============================================================================


@router.post("/api/v1/skills/incubate", response_model=dict)
async def execute_skill_incubate(data: dict):
    """执行灵感孵化 Skill."""
    kernel = await get_kernel()
    args = data.get("args", {})
    seed = args.get("seed", "")
    platform = args.get("platform", "fanqie")
    count = args.get("count", 3)

    if not seed:
        return {"error": "请提供 seed 参数"}

    try:
        plugin = await kernel.get_plugin("idea-incubator")
        result = await plugin.instance.incubate(seed=seed, platform=platform, count=count)
        return result
    except Exception as e:
        return {"error": str(e), "directions": []}
