"""知识图谱 API — 查询直接调用 core 层，构建走插件."""

from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException

from web.backend.dependencies import get_kernel

router = APIRouter(prefix="/api/v1/graph", tags=["graph"])


async def _get_graph_components():
    """获取图谱核心组件（GraphQuery + GraphStore）.

    从 graph-manager 插件获取已初始化的实例，避免重复创建连接。
    """
    kernel = await get_kernel()
    gm = await kernel.get_plugin("graph-manager")
    if not gm or not gm.instance:
        raise HTTPException(status_code=503, detail="graph-manager 插件未加载")
    plugin = gm.instance
    if not plugin._query or not plugin._store:
        raise HTTPException(status_code=503, detail="图谱存储未初始化")
    return kernel, plugin._query, plugin._store


@router.get("/export")
async def export_graph(project_id: str = ""):
    """导出全图数据（供前端可视化）。"""
    kernel = await get_kernel()

    # 优先从项目目录读取 graph.json（章节生成时自动更新的）
    if project_id:
        try:
            raw = await kernel.read_project_file(project_id, "graph.json")
            data = json.loads(raw)
            if data.get("nodes"):
                return data
        except (FileNotFoundError, json.JSONDecodeError):
            pass

    # 指定了项目但该项目没有 graph.json，返回空图谱
    if project_id:
        return {"nodes": [], "edges": [], "project_id": project_id}

    # 未指定项目时从 core 层导出
    try:
        _, query, _ = await _get_graph_components()
        return await query.export_full_graph()
    except HTTPException:
        return {"nodes": [], "edges": [], "note": "图谱插件未加载，请先在项目设置中构建人物"}


@router.get("/character/{char_id}/network")
async def character_network(char_id: str):
    """获取人物关系网络。"""
    _, query, _ = await _get_graph_components()
    try:
        return await query.character_network(char_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/characters")
async def list_characters_graph():
    """列出图谱中所有人物（含关系统计）。"""
    _, query, _ = await _get_graph_components()
    try:
        return await query.all_characters()
    except Exception:
        return []


@router.get("/entity/{entity_id}")
async def entity_detail(entity_id: str):
    """查询实体邻域。"""
    _, query, _ = await _get_graph_components()
    try:
        return await query.entity_neighborhood(entity_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/build")
async def build_graph(project_id: str):
    """从项目设定构建图谱 — 走插件（含 LLM 自动提取逻辑）。"""
    kernel = await get_kernel()
    try:
        gm = await kernel.get_plugin("graph-manager")
        result = await gm.instance.build_from_settings(project_id)
        return {"status": "ok", **result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/clear")
async def clear_graph():
    """清空图谱 — 直接调用 store 层。"""
    _, _, store = await _get_graph_components()
    try:
        await store.clear()
        return {"status": "cleared"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
