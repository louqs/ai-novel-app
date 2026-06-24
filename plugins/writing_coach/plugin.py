"""AI 写作教练 — 分析章节质量，给出平台特定改进建议。

用法:
    coach = WritingCoachPlugin()
    report = await coach.analyze_chapter(content, platform="fanqie")
"""

from __future__ import annotations

import json
import re
from typing import Any

from core.logging_config import get_logger
from core.plugin_manager import PluginManifest

logger = get_logger(__name__)

COACH_SYSTEM = """你是一位资深网文编辑和写作教练，专注于帮作者提升小说质量。

## 你的分析框架
1. **开篇评估**: 前300字是否有冲突/悬念？节奏是否够快？
2. **对话质量**: 对话是否自然？是否符合人物身份？
3. **节奏控制**: 信息密度是否合理？有没有拖沓段落？
4. **章尾钩子**: 结尾是否让人想继续读？
5. **平台适配**: 是否符合目标平台风格要求？
6. **AI痕迹**: 是否有明显的AI写作模式？

## 输出要求
- 给3-5条具体可操作的改进建议
- 每条建议包括：问题定位 + 具体修改方案 + 平台对照
- 语气要像一位有经验的编辑在指导新人
- 每条建议不超过80字
- 以JSON格式返回"""


class WritingCoachPlugin:
    """AI 写作教练插件."""

    name = "writing-coach"
    version = "0.1.0"

    def __init__(self) -> None:
        self._kernel = None

    async def on_load(self, kernel) -> None:
        self._kernel = kernel
        logger.info("写作教练已加载")

    async def on_unload(self) -> None:
        self._kernel = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def analyze_chapter(
        self,
        content: str,
        *,
        platform: str = "fanqie",
        chapter_num: int = 0,
    ) -> dict[str, Any]:
        """分析单章质量。

        Returns:
            {"score": float, "suggestions": [...], "strengths": [...], "metrics": {...}}
        """
        words = len(content)

        # 本地指标
        metrics = self._local_metrics(content)

        # AI 教练
        try:
            ai_analysis = await self._ai_analysis(content, platform, chapter_num)
        except Exception:
            ai_analysis = {"suggestions": [], "strengths": []}

        return {
            "chapter": chapter_num,
            "words": words,
            "metrics": metrics,
            "suggestions": ai_analysis.get("suggestions", []),
            "strengths": ai_analysis.get("strengths", []),
            "score": ai_analysis.get("score", self._calculate_base_score(metrics)),
        }

    async def analyze_project(
        self,
        project_id: str,
        *,
        platform: str = "fanqie",
    ) -> dict[str, Any]:
        """分析整本小说质量。"""
        kernel = self._kernel
        chapters = []

        # 从 progress 获取所有卷和章节信息（多级降级）
        progress = await kernel.context().get(f"project:{project_id}", "progress", {})
        if not progress or not progress.get("volumes"):
            # 降级1：从数据库 settings 读取
            if kernel.db:
                try:
                    settings = await kernel.db.get_settings(project_id)
                    progress = settings.get("progress", {})
                except Exception:
                    pass
        if not progress or not progress.get("volumes"):
            # 降级2：从文件读取
            try:
                import json
                raw = await kernel.read_project_file(project_id, "progress.json")
                progress = json.loads(raw)
            except Exception:
                pass

        for vol in progress.get("volumes", []):
            vol_num = vol.get("volume_number", 1)
            for ch in vol.get("chapters", []):
                ch_num = ch.get("chapter_number", 0)
                if not ch_num:
                    continue
                chapter_id = f"ch_v{vol_num:02d}_{ch_num:04d}"
                try:
                    content = await kernel.read_project_file(project_id, f"chapters/{chapter_id}.md")
                    if content.strip():
                        chapters.append({"num": ch_num, "volume": vol_num, "content": content})
                except Exception:
                    pass

        # 降级3：如果从 progress 没找到章节，直接从数据库列表获取
        if not chapters and kernel.db:
            try:
                ch_list = await kernel.db.list_chapters(project_id)
                for ch_meta in ch_list:
                    ch_num = ch_meta.get("chapter_number", 0)
                    vol_num = ch_meta.get("volume_number", 1)
                    if not ch_num:
                        continue
                    ch_data = await kernel.db.get_chapter(project_id, ch_num, vol_num)
                    if ch_data and ch_data.get("content", "").strip():
                        chapters.append({"num": ch_num, "volume": vol_num, "content": ch_data["content"]})
            except Exception:
                pass

        if not chapters:
            return {"error": "没有章节可分析"}

        # 排序（按卷号+章节号）
        chapters.sort(key=lambda c: (c["volume"], c["num"]))

        # 均匀采样：首2 + 中间均匀 + 尾2，最多12章
        if len(chapters) <= 12:
            sample = chapters
        else:
            head = chapters[:2]
            tail = chapters[-2:]
            middle = chapters[2:-2]
            # 从中间均匀取 8 个
            step = len(middle) / 8
            mid_sample = [middle[int(i * step)] for i in range(8)]
            sample = head + mid_sample + tail

        results = []
        for ch in sample:
            results.append(await self.analyze_chapter(ch["content"], platform=platform, chapter_num=ch["num"]))

        avg_score = sum(r["score"] for r in results) / max(len(results), 1)

        # 全局指标
        total_words = sum(len(ch["content"]) for ch in chapters)
        all_text = " ".join(ch["content"] for ch in chapters)
        dialogue_ratio = len(re.findall(r'["""''「」『』]', all_text)) / max(len(all_text), 1) * 100

        # 整本小说分析
        whole_novel = await self._analyze_whole_novel(chapters, platform)

        return {
            "total_chapters": len(chapters),
            "total_words": total_words,
            "avg_score": round(avg_score, 2),
            "dialogue_ratio": round(dialogue_ratio, 1),
            "chapter_analyses": results,
            "summary": self._generate_summary(results, platform),
            "whole_novel": whole_novel,
        }

    # ------------------------------------------------------------------
    # 整本小说分析
    # ------------------------------------------------------------------

    async def _analyze_whole_novel(self, chapters: list, platform: str) -> dict:
        """整本小说维度分析."""
        if not chapters:
            return {}

        # 1. 章节字数分布
        word_counts = [len(ch["content"]) for ch in chapters]
        avg_words = sum(word_counts) / len(word_counts)
        min_words = min(word_counts)
        max_words = max(word_counts)
        # 字数波动系数（标准差/均值）
        import math
        variance = sum((w - avg_words) ** 2 for w in word_counts) / len(word_counts)
        cv = math.sqrt(variance) / max(avg_words, 1)  # 变异系数

        word_distribution = []
        for i, ch in enumerate(chapters):
            word_distribution.append({
                "ch": ch["num"], "vol": ch["volume"], "words": word_counts[i],
                "deviation": round((word_counts[i] - avg_words) / max(avg_words, 1) * 100, 1),
            })

        # 2. 节奏曲线（每章对话占比 + 段落数）
        rhythm_curve = []
        for ch in chapters:
            content = ch["content"]
            words = len(content)
            dial = len(re.findall(r'["""''「」『』]', content))
            paras = len([p for p in content.split("\n\n") if p.strip()])
            # 感叹号/问号密度 = 情绪强度指标
            excl = len(re.findall(r'[！!？?]', content))
            rhythm_curve.append({
                "ch": ch["num"], "vol": ch["volume"],
                "words": words,
                "dialogue_ratio": round(dial / max(words, 1) * 100, 1),
                "paragraphs": paras,
                "emotion_density": round(excl / max(words, 1) * 1000, 1),  # 每千字感叹/问号
            })

        # 3. 人物出场追踪（从一致性账本或文本提取）
        character_tracker = {}
        try:
            import json as _json
            raw = await self._kernel.read_project_file(chapters[0].get("project_id", ""), "consistency_ledger.json")
            ledger = _json.loads(raw)
            char_states = ledger.get("character_states", {})
            for name, st in char_states.items():
                character_tracker[name] = {
                    "status": st.get("status", ""),
                    "location": st.get("location", ""),
                    "last_seen": st.get("last_seen_ch", 0),
                }
        except Exception:
            pass

        # 4. 伏笔状态
        foreshadow_stats = {"planted": 0, "building": 0, "paid": 0, "total": 0}
        try:
            import json as _json
            raw = await self._kernel.read_project_file(chapters[0].get("project_id", ""), "foreshadows.json")
            fs_data = _json.loads(raw)
            for fs in fs_data.get("entries", {}).values():
                if isinstance(fs, dict):
                    foreshadow_stats["total"] += 1
                    status = fs.get("status", "")
                    if status == "planted":
                        foreshadow_stats["planted"] += 1
                    elif status == "building":
                        foreshadow_stats["building"] += 1
                    elif status in ("paid", "resolved"):
                        foreshadow_stats["paid"] += 1
        except Exception:
            pass

        # 5. 风格一致性（对比首尾章节的对话占比、段落长度差异）
        style_consistency = {}
        if len(chapters) >= 2:
            first_metrics = self._local_metrics(chapters[0]["content"])
            last_metrics = self._local_metrics(chapters[-1]["content"])
            dial_drift = abs(first_metrics["dialogue_ratio"] - last_metrics["dialogue_ratio"])
            para_drift = abs(first_metrics["avg_paragraph_length"] - last_metrics["avg_paragraph_length"])
            style_consistency = {
                "dialogue_drift": round(dial_drift, 1),
                "paragraph_drift": para_drift,
                "is_stable": dial_drift < 10 and para_drift < 30,
            }

        # 6. LLM 整体分析（取首尾各1章 + 中间1章做综合分析）
        ai_overview = {}
        try:
            sample_indices = [0, len(chapters) // 2, -1]
            sample_texts = []
            for idx in sample_indices:
                ch = chapters[idx]
                sample_texts.append(f"第{ch['num']}章:\n{ch['content'][:800]}")

            prompt = f"""请对以下{platform}小说做整体分析（共{len(chapters)}章，{sum(word_counts)}字）:

{chr(10).join(sample_texts)}

以JSON返回:
```json
{{
  "structure_score": 0.0-1.0,
  "pacing_comment": "节奏评价",
  "character_comment": "人物塑造评价",
  "world_building_comment": "世界观评价",
  "platform_fit_score": 0.0-1.0,
  "top_issues": ["最需要改进的问题1", "问题2", "问题3"],
  "strengths": ["最大亮点1", "亮点2"]
}}
```"""

            result = await self._kernel.call_llm(
                messages=[{"role": "system", "content": "你是资深网文编辑，擅长整体架构分析。"}, {"role": "user", "content": prompt}],
                tier="standard",
                max_tokens=2048,
                temperature=0.4,
            )
            ai_overview = self._parse_json(result["content"])
        except Exception:
            pass

        return {
            "word_stats": {
                "avg": round(avg_words), "min": min_words, "max": max_words,
                "cv": round(cv, 2),  # <0.3=稳定, >0.5=波动大
                "distribution": word_distribution,
            },
            "rhythm_curve": rhythm_curve,
            "character_tracker": character_tracker,
            "foreshadow_stats": foreshadow_stats,
            "style_consistency": style_consistency,
            "ai_overview": ai_overview,
        }

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _local_metrics(self, content: str) -> dict:
        """本地计算文本指标."""
        words = len(content)

        # 对话占比
        dialogue_chars = len(re.findall(r'["""''「」『』]', content))
        dialogue_ratio = round(dialogue_chars / max(words, 1) * 100, 1)

        # 段落数
        paragraphs = [p for p in content.split("\n\n") if p.strip()]
        avg_para_len = round(sum(len(p) for p in paragraphs) / max(len(paragraphs), 1))

        # 开头检查
        first_300 = content[:300]
        has_dialogue_open = bool(re.match(r'^["""''「]', first_300.strip()))
        has_action_open = len(re.findall(r'[。！？]', first_300)) < 3  # 句号少 = 节奏快

        # 结尾检查
        last_200 = content[-200:] if words > 200 else content
        has_hook = bool(re.search(r'[？?！!…]{1,}$', last_200.strip())) or "突然" in last_200 or "就在" in last_200

        return {
            "dialogue_ratio": dialogue_ratio,
            "paragraphs": len(paragraphs),
            "avg_paragraph_length": avg_para_len,
            "opens_with_dialogue": has_dialogue_open,
            "fast_opening": has_action_open,
            "has_ending_hook": has_hook,
            "mobile_friendly": avg_para_len < 150,
        }

    async def _ai_analysis(self, content: str, platform: str, ch_num: int) -> dict:
        """LLM 分析."""
        platform_names = {"fanqie": "番茄小说", "qidian": "起点", "jinjiang": "晋江"}
        pname = platform_names.get(platform, platform)

        prompt = f"""请分析以下{pname}小说章节(第{ch_num}章, {len(content)}字):

{content[:5000]}

以JSON返回:
```json
{{
  "score": 0.0-1.0,
  "suggestions": [
    {{"area": "开篇/对话/节奏/结尾/平台适配", "issue": "问题描述", "fix": "具体修改建议"}}
  ],
  "strengths": ["优点1", "优点2"]
}}
```"""

        result = await self._kernel.call_llm(
            messages=[{"role": "system", "content": COACH_SYSTEM}, {"role": "user", "content": prompt}],
            tier="standard",
            max_tokens=2048,
            temperature=0.5,
        )
        return self._parse_json(result["content"])

    def _calculate_base_score(self, metrics: dict) -> float:
        score = 0.5
        if metrics.get("opens_with_dialogue"):
            score += 0.1
        if metrics.get("has_ending_hook"):
            score += 0.15
        if metrics.get("mobile_friendly"):
            score += 0.1
        if metrics.get("dialogue_ratio", 0) > 25:
            score += 0.1
        if metrics.get("fast_opening"):
            score += 0.05
        return min(score, 1.0)

    def _generate_summary(self, results: list, platform: str) -> str:
        if not results:
            return "暂无分析数据"
        avg_score = sum(r["score"] for r in results) / len(results)
        if avg_score > 0.8:
            return "整体质量优秀，接近发布标准。建议重点检查反AI检测和平台规范。"
        elif avg_score > 0.6:
            return "质量良好，部分章节需要优化。建议关注章节钩子和对话密度。"
        else:
            return "需要较多改进。建议重点优化开篇冲突和章尾钩子，增加对话比例。"

    @staticmethod
    def _parse_json(content: str) -> dict:
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


def create_manifest() -> PluginManifest:
    return PluginManifest(
        name="writing-coach",
        version="0.1.0",
        description="AI 写作教练 — 分析章节质量，给出平台特定改进建议",
        dependencies=[],
        hooks=["on_load", "on_unload"],
    )


def create_plugin() -> WritingCoachPlugin:
    return WritingCoachPlugin()
