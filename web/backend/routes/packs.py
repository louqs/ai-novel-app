"""知识包市场 API."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from web.backend.dependencies import get_kernel

router = APIRouter(prefix="/api/v1/packs", tags=["packs"])


class PackInstallRequest(BaseModel):
    source: str = Field(..., description="包名或路径")


class PackCreateRequest(BaseModel):
    name: str = Field(..., min_length=2)
    title: str = ""
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    files: dict[str, str] = Field(default_factory=dict)


@router.get("/local")
async def list_local():
    """已安装的知识包."""
    kernel = await get_kernel()
    try:
        pm = await kernel.get_plugin("pack-market")
        return await pm.instance.market.list_local()
    except Exception:
        return []


@router.get("/catalog")
async def list_catalog():
    """内置知识包目录."""
    kernel = await get_kernel()
    try:
        pm = await kernel.get_plugin("pack-market")
        catalog = await pm.instance.market.list_catalog()
        local = await pm.instance.market.list_local()
        installed_names = {p["name"] for p in local}
        for item in catalog:
            item["installed"] = item.get("name") in installed_names
        return catalog
    except Exception:
        return []


@router.post("/install")
async def install_pack(data: PackInstallRequest):
    """安装知识包."""
    kernel = await get_kernel()
    try:
        pm = await kernel.get_plugin("pack-market")
        return await pm.instance.market.install(data.source)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/uninstall/{name}")
async def uninstall_pack(name: str):
    """卸载知识包."""
    kernel = await get_kernel()
    try:
        pm = await kernel.get_plugin("pack-market")
        return await pm.instance.market.uninstall(name)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/create")
async def create_pack(data: PackCreateRequest):
    """创建新知识包."""
    kernel = await get_kernel()
    try:
        pm = await kernel.get_plugin("pack-market")
        path = await pm.instance.market.create(
            data.name, title=data.title, description=data.description, tags=data.tags, files=data.files,
        )
        return {"status": "created", "path": str(path)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/update", response_model=dict)
async def update_knowledge():
    """联网更新知识库（通过 AI 生成最新内容）。"""
    kernel = await get_kernel()
    from core.knowledge_updater import KnowledgeUpdater
    updater = KnowledgeUpdater(kernel)
    try:
        result = await updater.update_all()
        return {"status": "ok", "result": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/update-status", response_model=dict)
async def get_update_status():
    """获取知识库更新状态。"""
    kernel = await get_kernel()
    from core.knowledge_updater import KnowledgeUpdater
    updater = KnowledgeUpdater(kernel)
    return await updater.get_update_status()


@router.post("/export/{name}")
async def export_pack(name: str):
    """导出知识包为 ZIP."""
    kernel = await get_kernel()
    try:
        pm = await kernel.get_plugin("pack-market")
        path = await pm.instance.market.export_zip(name)
        if path:
            return {"status": "exported", "path": str(path)}
        raise HTTPException(status_code=404, detail="包不存在")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
