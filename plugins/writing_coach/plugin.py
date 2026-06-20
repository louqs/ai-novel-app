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
        ch_num = 1
        while True:
            try:
                content = await kernel.read_project_file(project_id, f"chapters/ch_{ch_num:04d}.md")
                chapters.append({"num": ch_num, "content": content})
                ch_num += 1
            except Exception:
                break

        if not chapters:
            return {"error": "没有章节可分析"}

        # 分析前3章和后2章
        sample = chapters[:3] + chapters[-2:] if len(chapters) > 5 else chapters
        results = []
        for ch in sample:
            results.append(await self.analyze_chapter(ch["content"], platform=platform, chapter_num=ch["num"]))

        avg_score = sum(r["score"] for r in results) / max(len(results), 1)

        # 全局指标
        total_words = sum(len(ch["content"]) for ch in chapters)
        all_text = " ".join(ch["content"] for ch in chapters)
        dialogue_ratio = len(re.findall(r'["""''「」『』]', all_text)) / max(len(all_text), 1) * 100

        return {
            "total_chapters": len(chapters),
            "total_words": total_words,
            "avg_score": round(avg_score, 2),
            "dialogue_ratio": round(dialogue_ratio, 1),
            "chapter_analyses": results,
            "summary": self._generate_summary(results, platform),
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

{content[:2500]}

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
            m = re.search(r'```(?:json)?\s*([\s\S]*?)```', content)
            if m:
                try:
                    return json.loads(m.group(1))
                except json.JSONDecodeError:
                    pass
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
