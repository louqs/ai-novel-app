"""知识图谱 API."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from web.backend.dependencies import get_kernel

router = APIRouter(prefix="/api/v1/graph", tags=["graph"])


@router.get("/export")
async def export_graph(project_id: str = ""):
    """导出全图数据（供前端可视化）。"""
    kernel = await get_kernel()

    # 优先从项目目录读取 graph.json（章节生成时自动更新的）
    if project_id:
        try:
            import json
            raw = await kernel.read_project_file(project_id, "graph.json")
            data = json.loads(raw)
            if data.get("nodes"):
                return data
        except (FileNotFoundError, json.JSONDecodeError):
            pass

    # 指定了项目但该项目没有 graph.json，返回空图谱（避免显示其他项目的数据）
    if project_id:
        return {"nodes": [], "edges": [], "project_id": project_id}

    # 未指定项目时回退到图谱管理器
    try:
        gm = await kernel.get_plugin("graph-manager")
        data = await gm.instance.export_graph()
        return data
    except Exception:
        return {"nodes": [], "edges": [], "note": "图谱插件未加载，请先在项目设置中构建人物"}


@router.get("/character/{char_id}/network")
async def character_network(char_id: str):
    """获取人物关系网络。"""
    kernel = await get_kernel()
    try:
        gm = await kernel.get_plugin("graph-manager")
        return await gm.instance.query_character_network(char_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/characters")
async def list_characters_graph():
    """列出图谱中所有人物（含关系统计）。"""
    kernel = await get_kernel()
    try:
        gm = await kernel.get_plugin("graph-manager")
        return await gm.instance.query_all_characters()
    except Exception:
        return []


@router.get("/entity/{entity_id}")
async def entity_detail(entity_id: str):
    """查询实体邻域。"""
    kernel = await get_kernel()
    try:
        gm = await kernel.get_plugin("graph-manager")
        return await gm.instance.query_entity(entity_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/build")
async def build_graph(project_id: str):
    """从项目设定构建图谱。"""
    kernel = await get_kernel()
    try:
        gm = await kernel.get_plugin("graph-manager")
        result = await gm.instance.build_from_settings(project_id)
        return {"status": "ok", **result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/clear")
async def clear_graph():
    """清空图谱。"""
    kernel = await get_kernel()
    try:
        gm = await kernel.get_plugin("graph-manager")
        await gm.instance.clear_graph()
        return {"status": "cleared"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
