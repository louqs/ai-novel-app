"""Skill Builder API — AI 自我扩展."""

from __future__ import annotations

from pathlib import Path
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from core.skill_builder import SkillBuilderAgent
from web.backend.dependencies import get_kernel

router = APIRouter(prefix="/api/v1/skills", tags=["skills"])


class SkillBuildRequest(BaseModel):
    requirement: str = Field(..., min_length=5, description="自然语言需求描述")


class SkillExecuteRequest(BaseModel):
    args: dict = Field(default_factory=dict)


@router.post("/build", response_model=dict)
async def build_skill(data: SkillBuildRequest):
    """用自然语言描述需求，AI 自动生成并加载插件。

    示例:
        "帮我写一个检测战斗场面描写是否过于平淡的插件"
        "创建一个自动提取每章金句的插件"
        "写一个检测角色对话是否符合人设的审查插件"
    """
    kernel = await get_kernel()
    builder = SkillBuilderAgent(kernel)
    result = await builder.build(data.requirement)
    return result


@router.post("/delete", response_model=dict)
async def delete_skill(data: dict):
    """删除 AI 生成的插件。"""
    kernel = await get_kernel()
    name = data.get("name", "")
    if not name:
        raise HTTPException(status_code=400, detail="缺少插件名")
    import shutil
    plugin_dir = Path("plugins/_built") / name
    if plugin_dir.exists():
        shutil.rmtree(plugin_dir)
        # 从 PluginManager 卸载
        try:
            await kernel._plugin_manager.unload(name)
        except Exception:
            pass
        return {"status": "deleted", "name": name}
    raise HTTPException(status_code=404, detail=f"插件 '{name}' 不存在")


@router.get("/built", response_model=dict)
async def list_built_skills():
    """列出所有 AI 生成的插件."""
    kernel = await get_kernel()
    builder = SkillBuilderAgent(kernel)
    plugins = await builder.list_built_plugins()
    return {"plugins": plugins, "count": len(plugins)}


@router.post("/{skill_name}/execute", response_model=dict)
async def execute_skill(skill_name: str, data: SkillExecuteRequest):
    """执行一个 Skill."""
    kernel = await get_kernel()
    args = data.args

    if skill_name == "incubate":
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
            return {"error": str(e)}

    return {"error": f"未知 Skill: {skill_name}"}
