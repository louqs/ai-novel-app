"""章节生成 + 反AI检测 + 门禁 API."""

from __future__ import annotations

import json
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException
from starlette.status import HTTP_404_NOT_FOUND

from core.orchestrator import OrchestrationEngine
from core.quality_gate import GateChainConfig, GateChainExecutor, GateResult
from web.backend.dependencies import get_kernel
from web.backend.schemas import (
    AntiAICheckRequest,
    AntiAIHumanizeRequest,
    ChapterGenerateBatch,
    ChapterGenerateRequest,
    ChapterResponse,
    GateOverrideRequest,
    StatusResponse,
)

router = APIRouter(tags=["generation"])


# =============================================================================
# 章节生成
# =============================================================================


@router.post("/api/v1/projects/{project_id}/chapters/generate", response_model=dict)
async def generate_chapter(project_id: str, data: ChapterGenerateRequest):
    """生成单章."""
    kernel = await get_kernel()

    # 获取大纲节点
    progress_raw = await kernel.context().get(f"project:{project_id}", "progress", {})
    outline_node = None
    for vol in progress_raw.get("volumes", []):
        for ch in vol.get("chapters", []):
            if ch.get("chapter_number") == data.chapter_number:
                outline_node = ch
                break

    if outline_node is None:
        outline_node = {
            "chapter_number": data.chapter_number,
            "volume_number": 1,
            "title": f"第{data.chapter_number}章",
            "summary": "",
        }

    # 构建门禁链
    gates = []
    for entry in await kernel._plugin_manager.list_active():
        from core.quality_gate import IQualityGate
        if isinstance(entry.instance, IQualityGate):
            gates.append(entry.instance)

    gate_chain = GateChainExecutor(GateChainConfig(gates=gates)) if gates else None
    engine = OrchestrationEngine(kernel=kernel, gate_chain=gate_chain)

    result = await engine.generate_chapter(
        project_id=project_id,
        chapter_number=data.chapter_number,
        auto_retry=data.auto_retry,
    )

    chapter = result["chapter"]
    pipeline = result["pipeline"]

    return {
        "chapter_id": chapter.get("chapter_id"),
        "chapter_number": chapter.get("chapter_number"),
        "content": chapter.get("content", ""),
        "word_count": len(chapter.get("content", "")),
        "pipeline_state": pipeline.state.value,
        "revision_rounds": pipeline.revision_round,
    }


@router.post("/api/v1/projects/{project_id}/chapters/generate/batch", response_model=dict)
async def generate_chapters_batch(project_id: str, data: ChapterGenerateBatch):
    """批量生成章节."""
    return {"status": "accepted", "message": f"将生成 {data.count} 章，从第{data.start_chapter}章开始", "job_id": f"job_{uuid.uuid4().hex[:8]}"}


@router.get("/api/v1/projects/{project_id}/chapters/list", response_model=list)
async def list_chapters(project_id: str):
    """列出项目所有章节（仅元数据，不含正文）。"""
    kernel = await get_kernel()
    if kernel.db:
        return await kernel.db.list_chapters(project_id)
    return []


@router.get("/api/v1/projects/{project_id}/chapters/{ch_num}", response_model=dict)
async def get_chapter(project_id: str, ch_num: int):
    """获取章节."""
    kernel = await get_kernel()
    chapter_id = f"ch_{ch_num:04d}"

    # 优先数据库
    if kernel.db:
        ch = await kernel.db.get_chapter(project_id, ch_num)
        if ch:
            return {"chapter_id": ch["id"], "chapter_number": ch_num, "title": ch.get("title",""), "content": ch["content"], "word_count": ch["word_count"]}

    # 降级文件
    try:
        content = await kernel.read_project_file(project_id, f"chapters/{chapter_id}.md")
        return {"chapter_id": chapter_id, "chapter_number": ch_num, "content": content, "word_count": len(content)}
    except FileNotFoundError:
        raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="章节不存在")


# =============================================================================
# 反AI检测
# =============================================================================


@router.post("/api/v1/anti-ai/check", response_model=dict)
async def check_anti_ai(data: AntiAICheckRequest):
    """检测文本中的 AI 痕迹."""
    kernel = await get_kernel()
    try:
        plugin = await kernel.get_plugin("anti-ai-detection")
        if plugin.instance and hasattr(plugin.instance, "detect"):
            result = await plugin.instance.detect(data.text)
            # 确保包含新增的检测维度
            if "vocabulary_diversity" not in result:
                result["vocabulary_diversity"] = plugin.instance._detector._check_vocabulary_diversity(data.text)
            if "sentence_patterns" not in result:
                result["sentence_patterns"] = plugin.instance._detector._check_sentence_patterns(data.text)
            return result
    except Exception:
        pass

    from plugins.anti_ai_detection.pattern_detector import AIPatternDetector
    detector = AIPatternDetector()
    matches = detector.detect(data.text)
    score = detector.calculate_ai_score(matches, text=data.text)
    sentence = detector.detect_uniform_sentences(data.text)
    ending = detector.detect_generic_ending(data.text)
    not_xy = detector.detect_not_x_but_y(data.text)

    return {
        "ai_score": round(score, 3),
        "is_likely_ai": score < 0.6,
        "pattern_matches": [
            {"category": m.category, "severity": m.severity, "count": m.count, "items": m.matched_items}
            for m in matches
        ],
        "sentence_uniformity": sentence,
        "generic_ending": ending,
        "not_x_but_y": not_xy,
        "vocabulary_diversity": detector._check_vocabulary_diversity(data.text),
        "sentence_patterns": detector._check_sentence_patterns(data.text),
    }


@router.post("/api/v1/anti-ai/humanize", response_model=dict)
async def humanize_text(data: AntiAIHumanizeRequest):
    """对文本进行人性化改写."""
    kernel = await get_kernel()
    if not data.text.strip():
        return {"content": data.text, "mode": data.mode, "note": "empty text"}

    # 直接用 HumanizationEngine 确保 LLM 调用
    from plugins.anti_ai_detection.humanization_engine import HumanizationEngine
    from plugins.anti_ai_detection.pattern_detector import AIPatternDetector

    engine = HumanizationEngine()
    await engine.on_load(kernel)
    detector = AIPatternDetector()
    matches = detector.detect(data.text)
    match_dicts = [{"category": m.category, "matched_items": m.matched_items} for m in matches]

    try:
        result = await engine.humanize(
            data.text,
            mode=data.mode,
            detected_patterns=match_dicts,
            target_word_count=data.target_word_count,
        )
        if result and result != data.text:
            return {"content": result, "mode": data.mode, "changed": True}
        else:
            return {"content": data.text, "mode": data.mode, "changed": False, "note": "LLM returned same or empty; text unchanged"}
    except Exception as e:
        return {"content": data.text, "mode": data.mode, "changed": False, "error": str(e)[:200]}


@router.get("/api/v1/anti-ai/patterns", response_model=dict)
async def list_ai_patterns():
    """列出已知 AI 模式."""
    import yaml
    try:
        with open("knowledge_base/anti_ai_patterns/patterns.yaml", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return {"patterns": list(data.get("patterns", {}).keys())}
    except Exception:
        return {"patterns": []}


# =============================================================================
# 在线AI检测
# =============================================================================


@router.post("/api/v1/anti-ai/online-detect", response_model=dict)
async def get_online_detect_instructions(data: dict):
    """获取在线检测指令（供用户手动粘贴到检测平台）."""
    from plugins.anti_ai_detection.online_detector import OnlineDetector

    text = data.get("text", "")
    platform = data.get("platform", "tianyan")

    if not text.strip():
        return {"error": "文本为空"}

    detector = OnlineDetector()
    return detector.get_detect_instructions(text, platform)


@router.post("/api/v1/anti-ai/record-result", response_model=dict)
async def record_detect_result(data: dict):
    """记录在线检测结果."""
    from plugins.anti_ai_detection.online_detector import OnlineDetector

    project_id = data.get("project_id", "")
    chapter_num = data.get("chapter_num", 0)
    ai_rate = data.get("ai_rate", 0)
    platform = data.get("platform", "天眼AI")
    notes = data.get("notes", "")

    kernel = await get_kernel()

    # 如果没有 project_id，使用全局目录
    if project_id:
        project_dir = kernel.get_project_dir(project_id)
    else:
        project_dir = kernel._data_dir / "global_detect"

    detector = OnlineDetector(project_dir=project_dir)
    result = detector.record_result(project_id or "global", chapter_num, ai_rate, platform, notes)

    return {
        "recorded": True,
        "ai_rate": result.ai_rate,
        "level": result.level_label,
        "platform": result.platform,
    }


@router.get("/api/v1/anti-ai/results/{project_id}", response_model=dict)
async def get_detect_results(project_id: str):
    """获取检测结果汇总."""
    from plugins.anti_ai_detection.online_detector import OnlineDetector

    kernel = await get_kernel()
    project_dir = kernel.get_project_dir(project_id)

    detector = OnlineDetector(project_dir=project_dir)
    return detector.get_results_summary(project_id)


# =============================================================================
# 门禁
# =============================================================================


@router.get("/api/v1/projects/{project_id}/chapters/{ch_num}/gate-results", response_model=dict)
async def get_gate_results(project_id: str, ch_num: int):
    """获取门禁报告."""
    kernel = await get_kernel()
    ns = f"project:{project_id}"
    results = await kernel.context().get(ns, f"gate_results_ch_{ch_num}", [])
    return {"chapter": ch_num, "results": results}


@router.post("/api/v1/projects/{project_id}/chapters/{ch_num}/override-gate", response_model=StatusResponse)
async def override_gate(project_id: str, ch_num: int, data: GateOverrideRequest):
    """强制通过门禁."""
    kernel = await get_kernel()
    ns = f"project:{project_id}"
    await kernel.context().set(ns, f"gate_overridden_ch_{ch_num}", True)
    return StatusResponse(message=f"第{ch_num}章门禁已手动通过，原因: {data.reason}")
