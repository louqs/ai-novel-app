"""导出 + 写作教练 + 数据看板 API."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from core.export import NovelExporter
from core.stats import NovelStats
from web.backend.dependencies import get_kernel

router = APIRouter(tags=["export_coach"])


# =============================================================================
# 导出
# =============================================================================


@router.post("/api/v1/projects/{project_id}/export", response_model=dict)
async def export_novel(project_id: str, fmt: str = "txt"):
    """导出小说为 TXT/EPUB/Markdown."""
    kernel = await get_kernel()
    exporter = NovelExporter(kernel)

    if fmt not in ("txt", "epub", "md"):
        raise HTTPException(status_code=400, detail="格式仅支持 txt / epub / md")

    if fmt == "md":
        path = await exporter.export_markdown(project_id)
    else:
        path = await exporter.export(project_id, fmt=fmt)

    return {
        "status": "ok",
        "format": fmt,
        "path": str(path),
        "size": path.stat().st_size,
        "download_url": f"/api/v1/projects/{project_id}/download?fmt={fmt}",
    }


@router.get("/api/v1/projects/{project_id}/download")
async def download_novel(project_id: str, fmt: str = "txt"):
    """下载导出文件."""
    kernel = await get_kernel()
    exporter = NovelExporter(kernel)

    if fmt == "md":
        path = await exporter.export_markdown(project_id)
    else:
        path = await exporter.export(project_id, fmt=fmt)

    return FileResponse(path, filename=path.name)


# =============================================================================
# 数据看板
# =============================================================================


@router.get("/api/v1/projects/{project_id}/stats", response_model=dict)
async def get_stats(project_id: str):
    """获取项目数据看板."""
    kernel = await get_kernel()
    stats = NovelStats(kernel)
    return await stats.analyze(project_id)


# =============================================================================
# AI 写作教练
# =============================================================================


@router.post("/api/v1/projects/{project_id}/coach", response_model=dict)
async def coach_project(project_id: str):
    """分析整本小说质量."""
    kernel = await get_kernel()
    platform = await kernel.context().get(f"project:{project_id}", "platform", "fanqie")

    try:
        coach = await kernel.get_plugin("writing-coach")
        return await coach.instance.analyze_project(project_id, platform=platform)
    except Exception:
        # fallback: load on demand
        from plugins.writing_coach.plugin import WritingCoachPlugin
        coach_inst = WritingCoachPlugin()
        await coach_inst.on_load(kernel)
        return await coach_inst.analyze_project(project_id, platform=platform)


@router.post("/api/v1/coach/chapter", response_model=dict)
async def coach_chapter(data: dict):
    """分析单章（不绑定项目）."""
    kernel = await get_kernel()
    content = data.get("content", "")
    platform = data.get("platform", "fanqie")
    ch_num = data.get("chapter_num", 0)

    if not content:
        raise HTTPException(status_code=400, detail="需要 content 字段")

    from plugins.writing_coach.plugin import WritingCoachPlugin
    coach = WritingCoachPlugin()
    await coach.on_load(kernel)
    return await coach.analyze_chapter(content, platform=platform, chapter_num=ch_num)
