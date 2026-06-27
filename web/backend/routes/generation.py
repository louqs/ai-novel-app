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

    # 获取大纲节点 — 同时匹配 volume_number 和 chapter_number
    progress_raw = await kernel.context().get(f"project:{project_id}", "progress", {})
    outline_node = None
    for vol in progress_raw.get("volumes", []):
        if vol.get("volume_number") != data.volume_number:
            continue
        for ch in vol.get("chapters", []):
            if ch.get("chapter_number") == data.chapter_number:
                outline_node = ch
                break
        if outline_node:
            break

    if outline_node is None:
        outline_node = {
            "chapter_number": data.chapter_number,
            "volume_number": data.volume_number,
            "title": f"第{data.volume_number}卷第{data.chapter_number}章",
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
        volume_number=data.volume_number,
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


@router.post("/api/v1/projects/{project_id}/ab-compare", response_model=dict)
async def ab_compare_chapter(project_id: str, data: dict):
    """A/B 对比 — 同一章节生成两个版本并评估差异."""
    kernel = await get_kernel()
    ch_num = data.get("chapter_number", 1)
    vol_num = data.get("volume_number", 1)

    # 读取已有内容作为版本A
    version_a = ""
    if kernel.db:
        ch = await kernel.db.get_chapter(project_id, ch_num, vol_num)
        if ch:
            version_a = ch.get("content", "")
    if not version_a:
        try:
            cid = f"ch_v{vol_num:02d}_{ch_num:04d}"
            version_a = await kernel.read_project_file(project_id, f"chapters/{cid}.md")
        except FileNotFoundError:
            return {"error": "章节尚未生成，请先生成章节"}

    # 生成版本B（用不同 temperature）
    try:
        cw = await kernel.get_plugin("chapter-writer")
        chars = await kernel.db.get_characters(project_id) if kernel.db else {}
        settings = await kernel.db.get_settings(project_id) if kernel.db else {}
        platform = settings.get("platform", "fanqie") if settings else "fanqie"
        progress = settings.get("progress", {}) if settings else {}

        node = {"chapter_number": ch_num, "volume_number": vol_num, "title": f"第{ch_num}章", "summary": ""}
        for vol in progress.get("volumes", []):
            if vol.get("volume_number") == vol_num:
                for ch in vol.get("chapters", []):
                    if ch.get("chapter_number") == ch_num:
                        node = ch; break
                break

        system_prompt = cw.instance._build_system_prompt(platform)
        genre_tags = settings.get("genre_tags", []) if settings else []
        writer_settings = {"meta": settings, "platform": platform}
        if genre_tags:
            writer_settings["genre_tags"] = genre_tags
        prompt = cw.instance._build_user_prompt(node, {
            "characters": chars, "previous_chapters_summary": "",
            "genre_tags": genre_tags, "settings": writer_settings,
        }, platform, "", "")

        result = await kernel.call_llm(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt},
            ],
            tier="premium",
            max_tokens=16384,
            temperature=0.95,  # 高温度 = 更多变化
        )
        version_b = result["content"]
    except Exception as e:
        return {"error": f"版本B生成失败: {str(e)[:100]}"}

    # 评估两个版本
    from plugins.writing_coach.plugin import WritingCoachPlugin
    coach = WritingCoachPlugin()
    await coach.on_load(kernel)
    eval_a = await coach.analyze_chapter(version_a, platform=platform, chapter_num=ch_num)
    eval_b = await coach.analyze_chapter(version_b, platform=platform, chapter_num=ch_num)

    return {
        "version_a": {"content": version_a, "score": eval_a.get("score", 0), "words": len(version_a), "suggestions": eval_a.get("suggestions", [])},
        "version_b": {"content": version_b, "score": eval_b.get("score", 0), "words": len(version_b), "suggestions": eval_b.get("suggestions", [])},
    }


@router.post("/api/v1/projects/{project_id}/rewrite", response_model=dict)
async def rewrite_selection(project_id: str, data: dict):
    """段落重写 — 对选中文本进行局部改写（基于整章上下文）."""
    kernel = await get_kernel()
    selected_text = data.get("text", "").strip()
    full_content = data.get("full_content", "")
    context_before = data.get("context_before", "")
    context_after = data.get("context_after", "")
    instruction = data.get("instruction", "")
    platform = data.get("platform", "fanqie")

    if not selected_text:
        return {"error": "未选中文本"}

    platform_names = {"fanqie": "番茄小说", "qidian": "起点中文网", "jinjiang": "晋江文学城"}
    pname = platform_names.get(platform, platform)

    # 如果有完整内容，用标记分隔的方式让 AI 看到全章
    if full_content and len(full_content) > len(selected_text) + 100:
        # 在完整内容中标记需要改写的部分
        sel_start = data.get("selection_start", 0)
        sel_end = data.get("selection_end", 0)

        # 验证选区位置是否匹配
        if sel_start >= 0 and sel_end <= len(full_content) and full_content[sel_start:sel_end] == selected_text:
            marked_content = (
                full_content[:sel_start]
                + "\n<<<REWRITE_START>>>\n"
                + selected_text
                + "\n<<<REWRITE_END>>>\n"
                + full_content[sel_end:]
            )
            prompt = f"""请改写以下小说章节中用 <<<REWRITE_START>>> 和 <<<REWRITE_END>>> 标记的段落。

【完整章节内容（标记处为需要改写的部分）】
{marked_content}

{f"【用户指令】{instruction}" if instruction else ""}

要求：
1. **只改写标记之间的段落**，其他内容一字不动
2. 改写后的段落必须与前后文自然衔接，不产生任何矛盾
3. 保持原文的核心情节、人物关系、情感状态不变
4. 保持后文中引用到的细节在改写中有对应铺垫
5. 优化表达方式，使其更自然、更有画面感
6. 对话要符合人物性格
7. 适合{pname}平台风格
8. 改写后的字数与原文相近（±20%）
9. **严格保持叙事视角**：改写段落的叙事视角、信息量、人物认知必须与原文一致。角色在此刻不该知道的信息不能出现，不能因为看到了后文就提前暗示或剧透
10. **禁止上帝视角泄露**：不要让角色表现出对后续情节的"预知"，不要添加原文没有的伏笔暗示

只返回改写后的段落文本（不含标记），不要返回整章，不要加任何解释。"""
        else:
            # 选区不匹配，回退到分段模式
            full_content = ""

    # 回退：没有完整内容或选区不匹配时，用前后文片段
    if not full_content or len(full_content) <= len(selected_text) + 100:
        before = context_before[-800:] if context_before else ""
        after = context_after[:2000] if context_after else ""
        prompt = f"""请对以下选中的段落进行改写。

【上下文-前文】
{before}

【需要改写的段落】
{selected_text}

【上下文-后文（改写时必须保持与此处的衔接）】
{after[:600]}

{f"【用户指令】{instruction}" if instruction else ""}

要求：
1. **严格保持与后文的衔接**：改写后的文本必须能自然过渡到后文，不产生矛盾
2. 保持原文的核心情节、人物关系、情感状态不变
3. 保持后文中引用到的细节在改写中有对应铺垫
4. 优化表达方式，使其更自然、更有画面感
5. 对话要符合人物性格
6. 适合{pname}平台风格
7. 改写后的字数与原文相近（±20%）

只返回改写后的文本，不要加任何解释。"""

    try:
        result = await kernel.call_llm(
            messages=[
                {"role": "system", "content": "你是专业网文编辑，擅长局部改写。只返回改写后的文本。"},
                {"role": "user", "content": prompt},
            ],
            tier="standard",
            max_tokens=4096,
            temperature=0.7,
        )
        rewritten = result["content"].strip()
        return {
            "original": selected_text,
            "rewritten": rewritten,
            "original_len": len(selected_text),
            "rewritten_len": len(rewritten),
        }
    except Exception as e:
        return {"error": f"改写失败: {str(e)[:100]}"}


@router.get("/api/v1/projects/{project_id}/chapters/list", response_model=list)
async def list_chapters(project_id: str):
    """列出项目所有章节（仅元数据，不含正文）。

    优先从数据库读取，同时扫描文件系统补充数据库中缺失的章节。
    这样即使章节只保存到了文件系统（如数据库写入失败），重启后仍能正确显示。
    """
    kernel = await get_kernel()
    import re
    from pathlib import Path

    # 1. 从数据库获取章节列表
    db_chapters = []
    if kernel.db:
        try:
            db_chapters = await kernel.db.list_chapters(project_id)
        except Exception:
            pass

    # 2. 构建数据库中已有的章节键集合 (volume_number_chapter_number)
    db_keys = set()
    for ch in db_chapters:
        vol = ch.get("volume_number", 1)
        num = ch.get("chapter_number", 0)
        db_keys.add(f"{vol}_{num}")

    # 3. 扫描文件系统，补充数据库中缺失的章节
    chapters_dir = kernel._data_dir / project_id / "chapters"
    file_only_chapters = []
    if chapters_dir.exists():
        pattern = re.compile(r"^ch_v(\d{2})_(\d{4})\.md$")
        for md_file in chapters_dir.iterdir():
            m = pattern.match(md_file.name)
            if not m:
                continue
            vol_num = int(m.group(1))
            ch_num = int(m.group(2))
            key = f"{vol_num}_{ch_num}"
            if key in db_keys:
                continue  # 数据库已有，跳过
            # 文件系统有但数据库没有，补充元数据
            try:
                content = md_file.read_text(encoding="utf-8")
                file_only_chapters.append({
                    "id": f"ch_v{vol_num:02d}_{ch_num:04d}",
                    "project_id": project_id,
                    "chapter_number": ch_num,
                    "volume_number": vol_num,
                    "title": f"第{ch_num}章",
                    "content": None,  # 列表接口不含正文
                    "word_count": len(content),
                    "ai_score": 0,
                    "created_at": "",
                })
            except Exception:
                pass

    # 4. 合并并排序
    all_chapters = db_chapters + file_only_chapters
    all_chapters.sort(key=lambda c: (c.get("volume_number", 1), c.get("chapter_number", 0)))
    return all_chapters


@router.delete("/api/v1/projects/{project_id}/chapters/{ch_num}")
async def delete_chapter(project_id: str, ch_num: int, volume: int = 1):
    """删除指定章节。

    章节可能只存在于文件系统（如数据库写入失败、重新生成大纲后残留旧文件）。
    list_chapters 会合并 DB + 文件系统两个来源显示为「已生成」，因此删除也必须
    同时清理两处，否则会出现「列表显示已生成、删除却提示不存在」的状态不一致。
    """
    kernel = await get_kernel()
    chapter_id = f"ch_v{volume:02d}_{ch_num:04d}"
    md_path = kernel._data_dir / project_id / "chapters" / f"{chapter_id}.md"

    in_db = False
    if kernel.db:
        existing = await kernel.db.get_chapter(project_id, ch_num, volume)
        in_db = existing is not None

    file_exists = md_path.exists()

    # DB 与文件系统两处都没有，才算真不存在
    if not in_db and not file_exists:
        return {"status": "error", "message": "章节不存在"}

    if in_db:
        await kernel.db.delete_chapter(project_id, ch_num, volume)
    if file_exists:
        try:
            md_path.unlink()
        except OSError as e:
            return {"status": "error", "message": f"章节文件删除失败: {e}"}

    # 同步更新 current_chapter（以剩余章节最大号为准）
    if kernel.db:
        chapters = await kernel.db.list_chapters(project_id)
        max_ch = max((c.get("chapter_number", 0) for c in chapters), default=0)
        await kernel.db.update_project(project_id, {"current_chapter": max_ch})
    return {"status": "ok", "message": f"第{volume}卷第{ch_num}章已删除"}


@router.get("/api/v1/projects/{project_id}/annotations/{ch_num}", response_model=dict)
async def get_annotations(project_id: str, ch_num: int, volume: int = 1):
    """获取章节批注."""
    kernel = await get_kernel()
    key = f"v{volume}_ch{ch_num}"
    try:
        raw = await kernel.read_project_file(project_id, "annotations.json")
        data = json.loads(raw)
        return {"annotations": data.get(key, [])}
    except (FileNotFoundError, json.JSONDecodeError):
        return {"annotations": []}


@router.post("/api/v1/projects/{project_id}/annotations/auto", response_model=dict)
async def auto_annotate_chapter(project_id: str, data: dict):
    """AI 编辑自动批注 — 模拟资深编辑审稿."""
    kernel = await get_kernel()
    ch_num = data.get("chapter_number", 1)
    vol_num = data.get("volume_number", 1)
    content = data.get("content", "")

    if not content:
        # 从数据库/文件读取
        if kernel.db:
            ch = await kernel.db.get_chapter(project_id, ch_num, vol_num)
            if ch:
                content = ch.get("content", "")
        if not content:
            try:
                cid = f"ch_v{vol_num:02d}_{ch_num:04d}"
                content = await kernel.read_project_file(project_id, f"chapters/{cid}.md")
            except FileNotFoundError:
                return {"error": "章节不存在"}

    platform = data.get("platform", "fanqie")

    prompt = f"""你是一位资深网文编辑，正在审阅第{ch_num}章（{len(content)}字）。

请逐段审阅，在有问题的地方留下批注。每个批注必须引用原文中的一段话（10-50字），以便精确定位。

## 批注类型
- issue: 严重问题（设定崩坏、OOC、逻辑漏洞）
- suggestion: 改进建议（可以更好但不影响阅读）
- praise: 亮点（写得好的地方，鼓励保持）

## 审阅重点
1. 人物言行是否符合人设
2. 情节逻辑是否自洽
3. 对话是否有区分度（不同角色说话方式不同）
4. 是否有AI口水词或套路表达
5. 节奏是否合适（有没有拖沓或太赶）
6. 章尾钩子是否有效
7. 情感表达是否通过行动/细节而非标签

## 章节内容
{content[:8000]}

以 JSON 返回批注列表:
```json
{{
  "annotations": [
    {{
      "quote": "原文中的一段话（用于定位）",
      "type": "issue|suggestion|praise",
      "comment": "编辑批注内容",
      "fix": "具体的修改建议（issue/suggestion 类型必填）"
    }}
  ],
  "overall_comment": "总体评价（1-2句话）"
}}
```

要求:
- 批注数量 3-8 个，不要太多
- quote 必须是原文中实际存在的文字
- 优先标注严重问题，其次改进建议，偶尔亮点鼓励
- 评价要具体，不要泛泛而谈"""

    try:
        result = await kernel.call_llm(
            messages=[
                {"role": "system", "content": "你是资深网文编辑，擅长精确诊断问题。只返回JSON。"},
                {"role": "user", "content": prompt},
            ],
            tier="standard",
            max_tokens=4096,
            temperature=0.3,
        )
        parsed = _parse_json(result["content"])
        annotations = parsed.get("annotations", [])
        overall = parsed.get("overall_comment", "")

        # 在原文中定位 quote 的位置
        for ann in annotations:
            quote = ann.get("quote", "")
            if quote:
                idx = content.find(quote)
                if idx >= 0:
                    ann["start"] = idx
                    ann["end"] = idx + len(quote)
                else:
                    ann["start"] = -1
                    ann["end"] = -1

        # 保存批注
        key = f"v{vol_num}_ch{ch_num}"
        all_annotations = {}
        try:
            raw = await kernel.read_project_file(project_id, "annotations.json")
            all_annotations = json.loads(raw)
        except (FileNotFoundError, json.JSONDecodeError):
            pass
        all_annotations[key] = annotations
        await kernel.write_project_file(
            project_id, "annotations.json",
            json.dumps(all_annotations, indent=2, ensure_ascii=False),
        )

        return {
            "annotations": annotations,
            "overall_comment": overall,
            "total": len(annotations),
            "issues": len([a for a in annotations if a.get("type") == "issue"]),
            "suggestions": len([a for a in annotations if a.get("type") == "suggestion"]),
            "praises": len([a for a in annotations if a.get("type") == "praise"]),
        }
    except Exception as e:
        return {"error": f"批注生成失败: {str(e)[:100]}"}


def _parse_json(content: str) -> dict:
    import re
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        # 提取 ```json ... ``` 代码块（贪婪匹配）
        m = re.search(r'```(?:json)?\s*([\s\S]+?)\s*```', content)
        if m:
            try:
                return json.loads(m.group(1))
            except json.JSONDecodeError:
                pass
        # 尝试找第一个JSON对象
        start = content.find('{')
        if start >= 0:
            depth = 0
            for i in range(start, len(content)):
                if content[i] == '{':
                    depth += 1
                elif content[i] == '}':
                    depth -= 1
                    if depth == 0:
                        try:
                            return json.loads(content[start:i + 1])
                        except json.JSONDecodeError:
                            break
    return {}


@router.get("/api/v1/projects/{project_id}/chapters/{ch_num}", response_model=dict)
async def get_chapter(project_id: str, ch_num: int, volume: int = 1):
    """获取章节."""
    kernel = await get_kernel()
    chapter_id = f"ch_v{volume:02d}_{ch_num:04d}"

    # 优先数据库
    if kernel.db:
        ch = await kernel.db.get_chapter(project_id, ch_num, volume)
        if ch:
            return {"chapter_id": ch["id"], "chapter_number": ch_num, "volume_number": volume, "title": ch.get("title",""), "content": ch["content"], "word_count": ch["word_count"]}

    # 降级文件
    try:
        content = await kernel.read_project_file(project_id, f"chapters/{chapter_id}.md")
        return {"chapter_id": chapter_id, "chapter_number": ch_num, "volume_number": volume, "content": content, "word_count": len(content)}
    except FileNotFoundError:
        # 章节未生成，返回空内容（不报 404）
        return {"chapter_id": chapter_id, "chapter_number": ch_num, "volume_number": volume, "content": "", "word_count": 0, "status": "not_generated"}


# =============================================================================
# 反AI检测
# =============================================================================


@router.get("/api/v1/anti-ai/check")
async def check_anti_ai_get():
    """GET 不支持，仅 POST。"""
    return {"error": "请使用 POST 方法", "status": 405}

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


@router.get("/api/v1/anti-ai/humanize")
async def humanize_text_get():
    """GET 不支持，仅 POST。"""
    return {"error": "请使用 POST 方法", "status": 405}

@router.post("/api/v1/anti-ai/humanize", response_model=dict)
async def humanize_text(data: AntiAIHumanizeRequest):
    """对文本进行人性化改写 — 通过 TextTransformer 统一入口."""
    kernel = await get_kernel()
    if not data.text.strip():
        return {"content": data.text, "mode": data.mode, "note": "empty text"}

    from core.text_transformer import TextTransformer
    transformer = TextTransformer(kernel)

    try:
        step_result = await transformer.deai(
            data.text, mode=data.mode, target_word_count=data.target_word_count,
        )
        output = step_result.output_text
        if output and output != data.text:
            return {"content": output, "mode": data.mode, "changed": True}
        else:
            return {"content": data.text, "mode": data.mode, "changed": False,
                    "note": "LLM returned same or empty; text unchanged"}
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


@router.get("/api/v1/anti-ai/online-detect")
async def online_detect_get():
    """GET 不支持，仅 POST。"""
    return {"error": "请使用 POST 方法", "status": 405}

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


@router.get("/api/v1/anti-ai/record-result")
async def record_detect_result_get():
    """GET 不支持，仅 POST。"""
    return {"error": "请使用 POST 方法", "status": 405}

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
async def get_gate_results(project_id: str, ch_num: int, volume: int = 1):
    """获取门禁报告."""
    kernel = await get_kernel()
    ns = f"project:{project_id}"
    results = await kernel.context().get(ns, f"gate_results_vol{volume}_ch{ch_num}", [])
    return {"chapter": ch_num, "volume": volume, "results": results}


@router.post("/api/v1/projects/{project_id}/chapters/{ch_num}/override-gate", response_model=StatusResponse)
async def override_gate(project_id: str, ch_num: int, data: GateOverrideRequest, volume: int = 1):
    """强制通过门禁."""
    kernel = await get_kernel()
    ns = f"project:{project_id}"
    await kernel.context().set(ns, f"gate_overridden_vol{volume}_ch{ch_num}", True)
    return StatusResponse(message=f"第{volume}卷第{ch_num}章门禁已手动通过，原因: {data.reason}")
