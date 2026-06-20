"""小说数据看板 — 字数趋势、节奏分析、人物出场统计。

纯本地计算，无需 LLM 调用。
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Any


class NovelStats:
    """小说数据分析器。"""

    def __init__(self, kernel: Any) -> None:
        self._kernel = kernel

    async def analyze(self, project_id: str) -> dict[str, Any]:
        """完整分析报告。"""
        chapters = await self._collect(project_id)

        return {
            "overview": self._overview(chapters),
            "word_count_trend": self._word_count_trend(chapters),
            "pacing": self._pacing_analysis(chapters),
            "characters": self._character_frequency(chapters),
            "readability": self._readability(chapters),
            "platform_check": self._platform_check(project_id, chapters),
        }

    async def _collect(self, pid: str) -> list[dict]:
        chapters = []
        n = 1
        while True:
            try:
                content = await self._kernel.read_project_file(pid, f"chapters/ch_{n:04d}.md")
                chapters.append({"num": n, "content": content, "words": len(content)})
                n += 1
            except Exception:
                break
        return chapters

    # ---- 总览 ----

    def _overview(self, chapters: list) -> dict:
        if not chapters:
            return {"total_chapters": 0, "total_words": 0}
        words = [c["words"] for c in chapters]
        return {
            "total_chapters": len(chapters),
            "total_words": sum(words),
            "avg_words_per_chapter": round(sum(words) / len(words)),
            "min_words": min(words),
            "max_words": max(words),
            "completed": len(chapters),
        }

    # ---- 字数趋势 ----

    def _word_count_trend(self, chapters: list) -> list[dict]:
        return [
            {"chapter": c["num"], "words": c["words"]}
            for c in chapters
        ]

    # ---- 节奏分析 ----

    def _pacing_analysis(self, chapters: list) -> dict:
        if len(chapters) < 3:
            return {"message": "需要至少3章才能分析节奏"}

        words = [c["words"] for c in chapters]
        avg = sum(words) / len(words)

        # 识别高潮章（字数突增/突减）+ 对话密度
        pacing = []
        for i, c in enumerate(chapters):
            content = c["content"]
            dialogue_chars = len(re.findall(r'["""''「」『』]', content))
            dialogue_ratio = round(dialogue_chars / max(len(content), 1) * 100, 1)
            pacing.append({
                "chapter": c["num"],
                "words": c["words"],
                "vs_avg": round((c["words"] - avg) / avg * 100, 1),
                "dialogue_ratio": dialogue_ratio,
            })

        # 找峰值（字数高于均值30%）
        peaks = [p for p in pacing if p["vs_avg"] > 30]
        valleys = [p for p in pacing if p["vs_avg"] < -30]

        return {
            "average_words": round(avg),
            "peaks": peaks,          # 可能的重点章节
            "valleys": valleys,      # 可能的过渡章节
            "detail": pacing,
            "suggestion": self._pacing_suggestion(pacing),
        }

    def _pacing_suggestion(self, pacing: list) -> str:
        """节奏建议——基于番茄/起点平台标准。"""
        suggestions = []
        for p in pacing:
            if p["words"] < 1500:
                suggestions.append(f"第{p['chapter']}章字数偏少({p['words']}字)，番茄平台建议2000-4000字")
            if p["dialogue_ratio"] < 20:
                suggestions.append(f"第{p['chapter']}章对话占比低({p['dialogue_ratio']}%)，建议增加对话提升可读性")

        if not suggestions:
            return "节奏符合平台标准 ✓"
        return "; ".join(suggestions[:5])

    # ---- 人物频率 ----

    def _character_frequency(self, chapters: list) -> dict:
        """统计人物出场频率（基于名字匹配）。"""
        # 从 kernel 获取人物列表
        all_text = " ".join(c["content"] for c in chapters)
        names_counter: Counter = Counter()

        # 简单的中文名字模式（2-3个汉字）
        # 实际应该从人物设定中读取名字列表
        name_pattern = re.compile(r'[一-鿿]{2,3}(?=\s|[,，。.""\'\'!！?？:：;；\n)】》—])')
        for match in name_pattern.finditer(all_text):
            names_counter[match.group()] += 1

        # 过滤高频词（不是人名的常用词）
        stop_words = {"一个", "这个", "那个", "什么", "自己", "他们", "我们", "你们", "没有", "可以",
                      "已经", "不是", "还是", "但是", "因为", "所以", "如果", "虽然", "不过", "只是"}
        for w in stop_words:
            names_counter.pop(w, None)

        top = names_counter.most_common(15)
        return {
            "top_characters": [{"name": n, "appearances": c} for n, c in top if c > 3],
            "total_names_found": len(names_counter),
        }

    # ---- 可读性 ----

    def _readability(self, chapters: list) -> dict:
        if not chapters:
            return {}
        all_text = " ".join(c["content"] for c in chapters)
        sentences = re.split(r'[。！？.!?\n]', all_text)
        sentences = [s.strip() for s in sentences if len(s.strip()) > 2]
        lengths = [len(s) for s in sentences]

        if not lengths:
            return {}

        avg_len = sum(lengths) / len(lengths)
        sd = (sum((l - avg_len) ** 2 for l in lengths) / len(lengths)) ** 0.5

        # 段落分析
        paragraphs = all_text.split("\n\n")
        para_lengths = [len(p.strip()) for p in paragraphs if p.strip()]
        avg_para = sum(para_lengths) / max(len(para_lengths), 1)

        return {
            "total_sentences": len(sentences),
            "avg_sentence_length": round(avg_len, 1),
            "sentence_variation_sd": round(sd, 2),
            "avg_paragraph_length": round(avg_para, 1),
            "is_mobile_friendly": avg_para < 150,
            "suggestion": "移动端友好 ✓" if avg_para < 150 else "段落偏长，建议拆分以适应移动端阅读",
        }

    # ---- 平台适配检查 ----

    def _platform_check(self, pid: str, chapters: list) -> dict:
        """根据目标平台检查合规性。"""
        # 这里只是静态检查示例
        total_words = sum(c["words"] for c in chapters)
        return {
            "total_words": total_words,
            "fanqie_10w_check": "已达到10万字考核线 ✓" if total_words >= 100000 else f"距10万字还差 {100000 - total_words} 字",
            "chapters_count": len(chapters),
            "publish_ready": total_words >= 20000 and len(chapters) >= 10,
            "checklist": [
                {"item": "首章冲突", "status": "请人工确认"},
                {"item": "章尾钩子", "status": "请人工确认"},
                {"item": "AI痕迹检测", "status": "运行 novel check 检测"},
                {"item": "10万字完读率考核线", "status": "已达标" if total_words >= 100000 else "未达标"},
                {"item": "日更字数", "status": "平均每章" + str(round(total_words / max(len(chapters), 1))) + "字"},
            ],
        }
