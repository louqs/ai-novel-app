"""封面/插画 API."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from web.backend.dependencies import get_kernel

router = APIRouter(prefix="/api/v1/images", tags=["images"])


class CoverRequest(BaseModel):
    project_id: str
    style_hint: str = ""


class IllustrateRequest(BaseModel):
    project_id: str
    scene: str = Field(..., min_length=3)
    chapter_num: int = 0
    style: str = ""


@router.post("/cover")
async def generate_cover(data: CoverRequest):
    """生成小说封面."""
    kernel = await get_kernel()
    try:
        artist = await kernel.get_plugin("cover-artist")
        return await artist.instance.generate_cover(data.project_id, style_hint=data.style_hint)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/cover/variants")
async def generate_cover_variants(data: CoverRequest):
    """生成多版封面供选择."""
    kernel = await get_kernel()
    try:
        artist = await kernel.get_plugin("cover-artist")
        return await artist.instance.generate_cover_variants(data.project_id, count=3)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/illustrate")
async def generate_illustration(data: IllustrateRequest):
    """生成场景插画."""
    kernel = await get_kernel()
    try:
        artist = await kernel.get_plugin("cover-artist")
        return await artist.instance.generate_illustration(
            data.project_id, data.scene, chapter_num=data.chapter_num, style=data.style,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{project_id}/cover")
async def get_cover_image(project_id: str):
    """获取项目封面图片."""
    kernel = await get_kernel()
    path = await kernel.context().get(f"project:{project_id}", "cover_image", "")
    if path:
        from pathlib import Path
        p = Path(path)
        if p.exists():
            return FileResponse(p)
    raise HTTPException(status_code=404, detail="封面不存在")
