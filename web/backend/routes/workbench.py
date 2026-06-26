"""创作工作台 API — 大纲生成、章节保存、人物列表."""

from __future__ import annotations

import json

from fastapi import APIRouter, File, HTTPException, UploadFile
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
        ai_threshold: float = 0.2,
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
        ai_threshold: float = 0.2,
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


@router.post("/api/v1/projects/{project_id}/pipeline/apply-all", response_model=dict)
async def apply_all_optimization(project_id: str, data: dict | None = None):
    """把已保存的优化结果批量应用到对应章节（自动快照旧版本）.

    body（可选）: { chapters: [{chapter_num, volume_number}, ...] }
        给定则只应用这些章节；不给则应用项目下全部已优化章节。
    """
    kernel = await get_kernel()
    if not kernel.db:
        raise HTTPException(status_code=400, detail="数据库不可用，无法批量应用")

    results = await kernel.db.list_optimization_results(project_id)
    if not results:
        return {"status": "noop", "applied": 0, "message": "没有可应用的优化结果"}

    # 可选过滤
    wanted = None
    if data and isinstance(data.get("chapters"), list):
        wanted = {(int(c.get("chapter_num", c.get("chapter_number", 0))),
                   int(c.get("volume_number", 1))) for c in data["chapters"]}

    applied, skipped = [], []
    for r in results:
        ch_num = r["chapter_number"]
        vol_num = r["volume_number"] or 1
        optimized = r.get("optimized") or ""
        if wanted is not None and (ch_num, vol_num) not in wanted:
            continue
        if not optimized:
            skipped.append(ch_num)
            continue
        chapter_id = f"ch_v{vol_num:02d}_{ch_num:04d}"
        await kernel.db.save_chapter(
            chapter_id, project_id, ch_num, f"第{vol_num}卷第{ch_num}章", optimized,
            volume=vol_num, snapshot_source="optimize_apply",
            snapshot_summary="批量应用优化结果前自动快照",
        )
        await kernel.write_project_file(project_id, f"chapters/{chapter_id}.md", optimized)
        applied.append(ch_num)

    return {"status": "applied", "applied": len(applied), "skipped": len(skipped),
            "applied_chapters": applied}


@router.post("/api/v1/projects/{project_id}/pipeline/zhuque-reduce", response_model=dict)
async def zhuque_reduce(project_id: str, data: dict):
    """以朱雀回填分数为补充，对优化后文本执行针对性降重.

    互补逻辑：取 min(本地人类度, 朱雀人类度) 作为严格判据——
    两个检测器都认可才放行，否则以更严格的那个触发降重。

    body: {
        content: str,
        zhuque_ai_rate: float,   # 朱雀检测到的 AI 率，0-100
        chapter_num: int,
        volume_number: int = 1,
        humanize_mode: str = "unified",
        ai_threshold: float = 0.2
    }
    """
    kernel = await get_kernel()
    content = data.get("content", "")
    if not content:
        raise HTTPException(status_code=400, detail="需要 content")

    zhuque_ai_rate = float(data.get("zhuque_ai_rate", 0)) / 100.0  # 转换为 0-1
    zhuque_human = 1.0 - zhuque_ai_rate           # 转为人类度（与本地 ai_score 同语义）
    ai_threshold = float(data.get("ai_threshold", 0.2))
    humanize_mode = data.get("humanize_mode", "unified")
    ch_num = int(data.get("chapter_num", 0))
    vol_num = int(data.get("volume_number", 1))

    # 先取本地检测分
    local_human = 1.0  # 默认满分（无法检测时不干预）
    try:
        entry = await kernel.get_plugin("anti-ai-detection")
        if entry and entry.instance:
            detect = await entry.instance.detect(content)
            local_human = detect.get("ai_score", 1.0)   # anti_ai plugin 的 ai_score 即人类度
    except Exception:
        pass

    # 取更严格的判据
    effective_human = min(local_human, zhuque_human)
    needs_reduce = effective_human <= ai_threshold

    triggered_by = []
    if local_human <= ai_threshold:
        triggered_by.append(f"本地检测人类度 {local_human:.0%}")
    if zhuque_human <= ai_threshold:
        triggered_by.append(f"朱雀AI率 {zhuque_ai_rate:.0%}")

    if not needs_reduce:
        return {
            "changed": False, "reduced_text": content,
            "local_human": round(local_human, 3),
            "zhuque_human": round(zhuque_human, 3),
            "effective_human": round(effective_human, 3),
            "summary": (f"本地人类度 {local_human:.0%}，朱雀人类度 {zhuque_human:.0%}，"
                        f"均高于阈值 {ai_threshold:.0%}，无需降重"),
        }

    # 执行定点降重（逐段，保留段落结构；force=True 因外部判据已判超标）
    reduced = content
    changes: list = []
    try:
        pe_entry = await kernel.get_plugin("pipeline-editor")
        if pe_entry and pe_entry.instance:
            step = await pe_entry.instance._detect_reduce(
                content, "fanqie", ai_threshold=ai_threshold,
                humanize_mode=humanize_mode, force=True,
            )
            reduced = step.get("optimized", content)
            changes = step.get("changes", [])
        else:
            raise RuntimeError("pipeline-editor 插件未加载")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"降重失败: {e}") from e

    # 更新 DB 里该章的优化结果
    if kernel.db and ch_num:
        try:
            existing = await kernel.db.get_optimization_result(project_id, ch_num, vol_num)
            orig = existing["original"] if existing else content
            explanation = existing.get("explanation") or {} if existing else {}
            explanation["zhuque_ai_rate"] = round(zhuque_ai_rate * 100, 1)
            explanation["zhuque_reduce_applied"] = True
            await kernel.db.save_optimization_result(project_id, ch_num, vol_num, orig, reduced, explanation)
        except Exception:
            pass

    return {
        "changed": content != reduced, "reduced_text": reduced,
        "changes": changes,
        "local_human": round(local_human, 3),
        "zhuque_human": round(zhuque_human, 3),
        "effective_human": round(effective_human, 3),
        "triggered_by": triggered_by,
        "summary": f"降重已执行（{' + '.join(triggered_by)} 低于阈值 {ai_threshold:.0%}）",
    }


@router.get("/api/v1/projects/{project_id}/pipeline/export-text", response_model=dict)
async def export_optimized_text(project_id: str, separator: str = "\n\n---\n\n"):
    """合并所有已优化章节为一个文本，供用户导出去朱雀检测.

    返回: { text: str, chapter_count: int }
    各章用 separator 分隔，每章前附「第N章 标题」章头。
    """
    kernel = await get_kernel()
    if not kernel.db:
        raise HTTPException(status_code=400, detail="数据库不可用")

    results = await kernel.db.list_optimization_results(project_id)
    if not results:
        return {"text": "", "chapter_count": 0, "message": "暂无已优化章节"}

    parts = []
    for r in results:
        ch_num = r["chapter_number"]
        vol_num = r["volume_number"] or 1
        body = (r.get("optimized") or "").strip()
        if body:
            parts.append(f"【第{vol_num}卷第{ch_num}章】\n{body}")

    return {
        "text": separator.join(parts),
        "chapter_count": len(parts),
    }


@router.post("/api/v1/projects/{project_id}/pipeline/import-zhuque-report", response_model=dict)
async def import_zhuque_report(
    project_id: str,
    file: UploadFile = File(...),
    chapter_num: int = 0,
    volume_number: int = 1,
    ai_threshold: float = 0.2,
    humanize_mode: str = "unified",
    auto_reduce: bool = True,
):
    """导入朱雀检测报告 PDF，纯正则解析（不调 LLM），用解析出的 AI 率触发降重.

    解析出整体 AI 率与各高 AIGC 片段后：
    - 若 auto_reduce 且整体 AI 率 > 阈值 → 自动对该章降重
    - 否则只返回解析结果供前端展示
    """
    from core.zhuque_report_parser import parse_report_pdf

    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="上传文件为空")

    try:
        report = parse_report_pdf(raw)
    except RuntimeError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e

    if not report.parse_ok:
        raise HTTPException(
            status_code=422,
            detail="未能从报告中解析出检测数据，请确认是朱雀「打印为PDF」的报告",
        )

    result: dict = {"report": report.to_dict(), "reduction": None}
    ai_rate = report.overall_ai_rate

    if not auto_reduce or ai_rate <= ai_threshold:
        result["summary"] = (
            f"朱雀整体AI率 {ai_rate:.0%}，未超阈值 {ai_threshold:.0%}，无需降重"
            if ai_rate <= ai_threshold else
            f"朱雀整体AI率 {ai_rate:.0%}（已解析 {len(report.segments)} 个片段）"
        )
        return result

    # 取该章当前内容降重
    kernel = await get_kernel()
    content = ""
    if kernel.db and chapter_num:
        existing = await kernel.db.get_optimization_result(project_id, chapter_num, volume_number)
        if existing:
            content = existing.get("optimized") or existing.get("original") or ""
        if not content:
            ch = await kernel.db.get_chapter(project_id, chapter_num, volume_number)
            content = ch.get("content", "") if ch else ""
    if not content:
        result["summary"] = f"朱雀整体AI率 {ai_rate:.0%}，但未找到第{chapter_num}章内容，无法降重"
        return result

    # 定点降重（逐段，保留段落结构；force=True 因朱雀已判超标）
    reduced = content
    try:
        pe_entry = await kernel.get_plugin("pipeline-editor")
        if pe_entry and pe_entry.instance:
            step = await pe_entry.instance._detect_reduce(
                content, "fanqie", ai_threshold=ai_threshold,
                humanize_mode=humanize_mode, force=True,
            )
            reduced = step.get("optimized", content)
        else:
            raise RuntimeError("pipeline-editor 插件未加载")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"降重失败: {e}") from e

    # 回写优化结果，并记录朱雀报告数据
    if kernel.db and chapter_num:
        try:
            existing = await kernel.db.get_optimization_result(project_id, chapter_num, volume_number)
            orig = existing["original"] if existing else content
            explanation = (existing.get("explanation") or {}) if existing else {}
            explanation["zhuque_report"] = report.to_dict()
            explanation["zhuque_ai_rate"] = round(ai_rate * 100, 1)
            explanation["zhuque_reduce_applied"] = True
            await kernel.db.save_optimization_result(
                project_id, chapter_num, volume_number, orig, reduced, explanation)
        except Exception:
            pass

    result["reduction"] = {
        "changed": content != reduced,
        "reduced_text": reduced,
        "ai_rate_before": round(ai_rate, 3),
    }
    result["summary"] = (
        f"朱雀整体AI率 {ai_rate:.0%} 超过阈值 {ai_threshold:.0%}，已对第{chapter_num}章降重"
    )
    return result


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
        threshold: float = 0.2,        # detect_reduce 阈值
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
