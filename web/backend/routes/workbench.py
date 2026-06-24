"""创作工作台 API — 大纲生成、章节保存、人物列表."""

from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from starlette.status import HTTP_404_NOT_FOUND

from core.logging_config import get_logger
from core.version_manager import VersionManager
from web.backend.dependencies import get_kernel

logger = get_logger(__name__)

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
    """手动保存/更新大纲 — 保存草稿，不创建版本历史."""
    kernel = await get_kernel()
    from core.generation_service import GenerationService
    gs = GenerationService(kernel)
    # 只保存到 context 和文件，不创建版本历史
    ns = f"project:{project_id}"
    await kernel.context().set(ns, "progress", data)
    import json
    await kernel.write_project_file(
        project_id, "progress.json", json.dumps(data, ensure_ascii=False, indent=2),
    )
    if kernel.db:
        settings = await kernel.db.get_settings(project_id) or {}
        settings["progress"] = data
        await kernel.db.save_settings(project_id, settings)
    return {"status": "saved"}


@router.post("/api/v1/projects/{project_id}/generate/outline", response_model=dict)
async def generate_outline(project_id: str):
    """生成项目大纲 — 通过 GenerationService 统一入口."""
    kernel = await get_kernel()
    from core.generation_service import GenerationService
    gs = GenerationService(kernel)

    try:
        # 调用 outline-planner 插件
        result = await gs.generate_outline(project_id)
        if not result.success:
            raise HTTPException(status_code=500, detail=f"大纲生成失败: {result.error[:200]}")

        # 保存大纲（save_outline 内部会自动快照旧版本并保存新版本）
        await gs.save_outline(project_id, result.progress, snapshot_source="generate", snapshot_summary="生成新大纲")

        return result.progress
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"大纲生成失败: {str(e)[:200]}")


@router.post("/api/v1/projects/{project_id}/outline/apply", response_model=dict)
async def apply_outline(project_id: str, data: dict):
    """应用用户选择的大纲版本 — 通过 GenerationService 统一入口."""
    try:
        kernel = await get_kernel()
        from core.generation_service import GenerationService
        gs = GenerationService(kernel)

        progress = data.get("data")
        if not progress or not isinstance(progress, dict):
            raise HTTPException(status_code=400, detail="缺少大纲数据")
        if "volumes" not in progress:
            raise HTTPException(status_code=400, detail="大纲数据格式错误，缺少 volumes")

        logger.info("应用大纲", project_id=project_id, volumes=len(progress.get("volumes", [])))

        # 保存大纲（save_outline 内部会自动快照旧版本并保存新版本）
        vid = await gs.save_outline(project_id, progress, snapshot_source="apply", snapshot_summary="用户应用大纲方案")

        # 同步大纲伏笔到 foreshadows.json
        await gs.sync_outline_foreshadows(project_id, progress)

        # 记录当前使用的版本
        if vid and kernel.db:
            try:
                settings = await kernel.db.get_settings(project_id) or {}
                settings["outline_current_version_id"] = vid
                await kernel.db.save_settings(project_id, settings)
            except Exception:
                pass

        total_chs = sum(len(v.get("chapters", [])) for v in progress.get("volumes", []))
        return {"status": "applied", "volumes": len(progress.get("volumes", [])), "chapters": total_chs}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("应用大纲失败", error=str(e))
        raise HTTPException(status_code=500, detail=f"应用大纲失败: {str(e)[:200]}")


# =============================================================================
# 章节
# =============================================================================


@router.put("/api/v1/projects/{project_id}/chapters/{ch_num}", response_model=dict)
async def save_chapter(project_id: str, ch_num: int, data: ChapterSaveRequest):
    """手动保存/更新章节内容 — save_chapter 自动快照旧版本."""
    kernel = await get_kernel()
    vol = data.volume_number
    chapter_id = f"ch_v{vol:02d}_{ch_num:04d}"
    # 数据库（自动快照旧版本）
    if kernel.db:
        await kernel.db.save_chapter(
            chapter_id, project_id, ch_num, f"第{vol}卷第{ch_num}章", data.content,
            volume=vol, snapshot_source="manual", snapshot_summary="手动保存章节",
        )
        await kernel.db.update_project(project_id, {"current_chapter": max(ch_num,
            (await kernel.db.get_project(project_id) or {}).get("current_chapter", 0))})
    # 文件
    await kernel.write_project_file(project_id, f"chapters/{chapter_id}.md", data.content)
    ns = f"project:{project_id}"
    await kernel.context().set(ns, "current_chapter", max(ch_num, await kernel.context().get(ns, "current_chapter", 0)))
    return {"status": "saved", "chapter_id": chapter_id, "volume_number": vol, "word_count": len(data.content)}


# =============================================================================
# 版本历史 — 章节
# =============================================================================


@router.get("/api/v1/projects/{project_id}/chapters/{ch_num}/versions", response_model=dict)
async def list_chapter_versions(project_id: str, ch_num: int, volume: int = 1):
    """列出某章节的所有历史版本."""
    kernel = await get_kernel()
    if not kernel.db:
        raise HTTPException(status_code=500, detail="数据库未初始化")
    await kernel.db._ensure_version_tables()
    vm = VersionManager(kernel.db)
    versions = await vm.list_chapter_versions(project_id, ch_num, volume)

    # 从 settings 读取当前版本指针
    current_version_id = None
    settings = await kernel.db.get_settings(project_id)
    key = f"ch_ver_{project_id}_{volume}_{ch_num}"
    current_version_id = settings.get(key)

    # fallback: 最近的非 pre_ 来源版本
    if not current_version_id:
        for v in versions:
            if v.get("source") not in ("pre_rollback",):
                current_version_id = v["id"]
                break

    return {"versions": versions, "total": len(versions), "current_version_id": current_version_id}


@router.delete("/api/v1/projects/{project_id}/chapters/{ch_num}/versions/{version_id}", response_model=dict)
async def delete_chapter_version(project_id: str, ch_num: int, version_id: int):
    """删除指定的章节历史版本."""
    kernel = await get_kernel()
    if not kernel.db:
        raise HTTPException(status_code=500, detail="数据库未初始化")
    await kernel.db._ensure_version_tables()

    # 检查版本是否存在
    ver = await kernel.db.get_chapter_version(version_id)
    if not ver:
        raise HTTPException(status_code=404, detail="版本不存在")

    success = await kernel.db.delete_chapter_version(version_id)
    if success:
        logger.info("删除章节版本", project_id=project_id, chapter=ch_num, version_id=version_id)
        return {"status": "deleted", "version_id": version_id}
    else:
        raise HTTPException(status_code=500, detail="删除失败")


@router.get("/api/v1/versions/chapter/{version_id}", response_model=dict)
async def get_chapter_version(version_id: int):
    """获取特定版本的完整内容."""
    kernel = await get_kernel()
    if not kernel.db:
        raise HTTPException(status_code=500, detail="数据库未初始化")
    await kernel.db._ensure_version_tables()
    vm = VersionManager(kernel.db)
    ver = await vm.get_chapter_version(version_id)
    if not ver:
        raise HTTPException(status_code=404, detail="版本不存在")
    return ver


@router.post("/api/v1/projects/{project_id}/chapters/{ch_num}/versions/{version_id}/rollback", response_model=dict)
async def rollback_chapter(project_id: str, ch_num: int, version_id: int, volume: int = 1):
    """回滚章节到指定版本."""
    kernel = await get_kernel()
    if not kernel.db:
        raise HTTPException(status_code=500, detail="数据库未初始化")
    await kernel.db._ensure_version_tables()
    vm = VersionManager(kernel.db)
    try:
        result = await vm.rollback_chapter(project_id, ch_num, volume, version_id, kernel=kernel)
        return result
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/api/v1/versions/chapter/{v1}/diff/{v2}", response_model=dict)
async def diff_chapter_versions(v1: int, v2: int):
    """对比两个章节版本的差异."""
    kernel = await get_kernel()
    if not kernel.db:
        raise HTTPException(status_code=500, detail="数据库未初始化")
    await kernel.db._ensure_version_tables()
    vm = VersionManager(kernel.db)
    try:
        return await vm.diff_chapter_versions(v1, v2)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


# =============================================================================
# 版本历史 — 大纲
# =============================================================================


@router.get("/api/v1/projects/{project_id}/outline/versions", response_model=dict)
async def list_outline_versions(project_id: str):
    """列出项目大纲的所有历史版本."""
    kernel = await get_kernel()
    if not kernel.db:
        raise HTTPException(status_code=500, detail="数据库未初始化")
    try:
        # 确保表存在（兼容旧数据库）
        await kernel.db._ensure_outline_tables()
        vm = VersionManager(kernel.db)
        versions = await vm.list_outline_versions(project_id)

        # 从 settings 读取当前版本指针
        current_version_id = None
        if kernel.db:
            settings = await kernel.db.get_settings(project_id)
            current_version_id = settings.get("outline_current_version_id")

        # fallback: 如果没有指针，找最近的 apply 来源
        if not current_version_id:
            for v in versions:
                if v.get("source") == "apply":
                    current_version_id = v["id"]
                    break

        logger.info("获取大纲版本列表", project_id=project_id, count=len(versions), current=current_version_id)
        return {"versions": versions, "total": len(versions), "current_version_id": current_version_id}
    except Exception as e:
        import traceback
        error_detail = traceback.format_exc()
        logger.error("获取大纲版本列表失败", error=str(e), detail=error_detail)
        raise HTTPException(status_code=500, detail=f"获取版本列表失败: {str(e)[:200]}")


@router.delete("/api/v1/projects/{project_id}/outline/versions/{version_id}", response_model=dict)
async def delete_outline_version(project_id: str, version_id: int):
    """删除指定的大纲历史版本."""
    kernel = await get_kernel()
    if not kernel.db:
        raise HTTPException(status_code=500, detail="数据库未初始化")
    await kernel.db._ensure_outline_tables()

    # 检查版本是否存在
    ver = await kernel.db.get_outline_version(version_id)
    if not ver:
        raise HTTPException(status_code=404, detail="版本不存在")

    success = await kernel.db.delete_outline_version(version_id)
    if success:
        logger.info("删除大纲版本", project_id=project_id, version_id=version_id)
        return {"status": "deleted", "version_id": version_id}
    else:
        raise HTTPException(status_code=500, detail="删除失败")


@router.get("/api/v1/versions/outline/{version_id}", response_model=dict)
async def get_outline_version(version_id: int):
    """获取特定版本的大纲数据."""
    kernel = await get_kernel()
    if not kernel.db:
        raise HTTPException(status_code=500, detail="数据库未初始化")
    await kernel.db._ensure_outline_tables()
    vm = VersionManager(kernel.db)
    ver = await vm.get_outline_version(version_id)
    if not ver:
        raise HTTPException(status_code=404, detail="版本不存在")
    return ver


@router.post("/api/v1/projects/{project_id}/outline/versions/{version_id}/rollback", response_model=dict)
async def rollback_outline(project_id: str, version_id: int):
    """回滚大纲到指定版本."""
    kernel = await get_kernel()
    if not kernel.db:
        raise HTTPException(status_code=500, detail="数据库未初始化")
    await kernel.db._ensure_outline_tables()
    vm = VersionManager(kernel.db)
    try:
        result = await vm.rollback_outline(project_id, version_id, kernel=kernel)
        return result
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/api/v1/versions/outline/{v1}/diff/{v2}", response_model=dict)
async def diff_outline_versions(v1: int, v2: int):
    """对比两个大纲版本的差异."""
    kernel = await get_kernel()
    if not kernel.db:
        raise HTTPException(status_code=500, detail="数据库未初始化")
    await kernel.db._ensure_outline_tables()
    vm = VersionManager(kernel.db)
    try:
        return await vm.diff_outline_versions(v1, v2)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


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


# =============================================================================
# 统一编辑优化流水线
# =============================================================================


@router.post("/api/v1/projects/{project_id}/pipeline/optimize", response_model=dict)
async def run_optimize_pipeline(project_id: str, data: dict):
    """执行统一优化流水线.

    body: {
        chapter_num: int,
        volume_number: int = 1,
        steps: ["annotate", "coach", "detect"],  // 可选，默认全部
        ai_threshold: float = 0.3,
        humanize_mode: str = "standard"
    }
    """
    kernel = await get_kernel()
    ch_num = data.get("chapter_num", 0)
    vol_num = data.get("volume_number", 1)
    steps = data.get("steps", ["annotate", "coach", "detect"])
    ai_threshold = data.get("ai_threshold", 0.15)
    humanize_mode = data.get("humanize_mode", "unified")

    if not ch_num:
        raise HTTPException(status_code=400, detail="需要 chapter_num")

    # 获取章节内容
    chapter_id = f"ch_v{vol_num:02d}_{ch_num:04d}"
    content = ""
    if kernel.db:
        ch = await kernel.db.get_chapter(project_id, ch_num, vol_num)
        if ch:
            content = ch.get("content", "")
    if not content:
        try:
            content = await kernel.read_project_file(project_id, f"chapters/{chapter_id}.md")
        except FileNotFoundError:
            pass
    if not content:
        raise HTTPException(status_code=404, detail=f"章节 {chapter_id} 不存在或内容为空")

    platform = await kernel.context().get(f"project:{project_id}", "platform", "fanqie")

    # 获取门禁检查结果：先从内存取，没有则从数据库取
    gate_issues = []
    try:
        from web.backend.routes.stream import _chapter_jobs
        job_key = f"{project_id}:v{vol_num}:ch{ch_num}"
        job = _chapter_jobs.get(job_key)
        if job:
            gate_issues = job.get("gate_issues", [])
    except Exception:
        pass
    if not gate_issues and kernel.db:
        try:
            gate_issues = await kernel.db.get_gate_results(project_id, ch_num, vol_num)
        except Exception:
            pass

    try:
        plugin = await kernel.get_plugin("pipeline-editor")
        result = await plugin.instance.run_pipeline(
            content=content,
            project_id=project_id,
            chapter_num=ch_num,
            platform=platform,
            steps=steps,
            volume_number=vol_num,
            ai_threshold=ai_threshold,
            humanize_mode=humanize_mode,
            gate_issues=gate_issues,
        )
        # 优化结果持久化到数据库，避免切换页面丢失
        if kernel.db and result.get("current_content"):
            try:
                await kernel.db.save_optimization_result(
                    project_id, ch_num, vol_num,
                    result.get("original", content),
                    result.get("current_content", ""),
                    result.get("explanation"),
                )
            except Exception:
                pass
        return result
    except Exception as e:
        import traceback
        raise HTTPException(status_code=500, detail=f"流水线执行失败: {str(e)[:200]}")


@router.post("/api/v1/pipeline/optimize-text", response_model=dict)
async def optimize_text(data: dict):
    """对纯文本执行优化流水线（不绑定项目）.

    body: {
        content: str,
        platform: str = "fanqie",
        steps: [...],
        ai_threshold: float = 0.3,
        humanize_mode: str = "standard"
    }
    """
    kernel = await get_kernel()
    content = data.get("content", "")
    if not content:
        raise HTTPException(status_code=400, detail="需要 content")

    platform = data.get("platform", "fanqie")
    steps = data.get("steps", ["annotate", "coach", "detect"])
    ai_threshold = data.get("ai_threshold", 0.15)
    humanize_mode = data.get("humanize_mode", "unified")

    try:
        plugin = await kernel.get_plugin("pipeline-editor")
        result = await plugin.instance.run_pipeline(
            content=content,
            platform=platform,
            steps=steps,
            ai_threshold=ai_threshold,
            humanize_mode=humanize_mode,
        )
        return result
    except Exception as e:
        import traceback
        raise HTTPException(status_code=500, detail=f"流水线执行失败: {str(e)[:200]}")


@router.post("/api/v1/projects/{project_id}/pipeline/save", response_model=dict)
async def save_pipeline_result(project_id: str, data: dict):
    """保存流水线最终结果到章节.

    body: {
        chapter_num: int,
        volume_number: int = 1,
        content: str
    }
    """
    kernel = await get_kernel()
    ch_num = data.get("chapter_num", 0)
    vol_num = data.get("volume_number", 1)
    content = data.get("content", "")

    if not ch_num or not content:
        raise HTTPException(status_code=400, detail="需要 chapter_num 和 content")

    chapter_id = f"ch_v{vol_num:02d}_{ch_num:04d}"

    # 保存（自动快照旧版本）
    if kernel.db:
        await kernel.db.save_chapter(
            chapter_id, project_id, ch_num, f"第{vol_num}卷第{ch_num}章", content,
            volume=vol_num, snapshot_source="optimize", snapshot_summary="优化流水线保存前自动快照",
        )

    await kernel.write_project_file(project_id, f"chapters/{chapter_id}.md", content)
    return {"status": "saved", "chapter_id": chapter_id, "word_count": len(content)}


@router.get("/api/v1/projects/{project_id}/pipeline/result/{ch_num}")
async def get_optimization_result(project_id: str, ch_num: int, volume: int = 1):
    """获取章节的最近一次优化结果."""
    kernel = await get_kernel()
    if not kernel.db:
        return {"found": False}
    result = await kernel.db.get_optimization_result(project_id, ch_num, volume)
    if result:
        result["found"] = True
        return result
    return {"found": False}


# =============================================================================
# 统一质量分析
# =============================================================================


@router.post("/api/v1/quality/analyze", response_model=dict)
async def analyze_quality(data: dict):
    """统一质量分析 — 聚合写作教练 + 十维评审 + AI检测.

    body: {
        content: str (必填),
        platform: str = "fanqie",
        chapter_num: int = 0,
        project_id: str = "" (可选)
    }
    """
    kernel = await get_kernel()
    content = data.get("content", "")
    if not content.strip():
        raise HTTPException(status_code=400, detail="content 不能为空")

    platform = data.get("platform", "fanqie")
    chapter_num = data.get("chapter_num", 0)
    project_id = data.get("project_id", "")

    from core.unified_quality_analyzer import UnifiedQualityAnalyzer
    analyzer = UnifiedQualityAnalyzer(kernel)
    report = await analyzer.analyze_chapter(
        content,
        platform=platform,
        chapter_num=chapter_num,
        project_id=project_id,
    )
    return report.to_dict()


@router.post("/api/v1/transform", response_model=dict)
async def transform_text(data: dict):
    """统一文本变换 — 链式执行多个变换步骤.

    body: {
        content: str (必填),
        steps: list[str] (必填, 如 ["deai", "style"]),
        platform: str = "fanqie",
        mode: str = "standard",        # deai 模式
        style_mode: str = "rewrite",   # style 模式
        threshold: float = 0.3,        # detect_reduce 阈值
    }
    """
    kernel = await get_kernel()
    content = data.get("content", "")
    steps = data.get("steps", [])
    if not content.strip():
        raise HTTPException(status_code=400, detail="content 不能为空")
    if not steps:
        raise HTTPException(status_code=400, detail="steps 不能为空")

    from core.text_transformer import TextTransformer
    transformer = TextTransformer(kernel)
    result = await transformer.transform(content, steps, **data)
    return result.to_dict()
