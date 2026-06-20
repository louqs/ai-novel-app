"""在线AI检测器 — 整合天眼AI和朱雀AIGC检测.

支持的在线检测平台：
1. 天眼AI（首选）：https://www.tianyanai.org/ （完全免费，实测有效）
2. 朱雀AIGC检测：https://matrix.tencent.com/ai-detect/ai_gen （中文最准95%+，免费）

使用方式：
- 提取文本供用户手动粘贴到检测平台
- 记录检测结果到项目文件
- 支持批量检测和结果追踪
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.logging_config import get_logger

logger = get_logger(__name__)

# 检测平台配置
DETECT_PLATFORMS = {
    "tianyan": {
        "name": "天眼AI",
        "url": "https://www.tianyanai.org/",
        "description": "完全免费，实测有效",
        "max_text_length": 10000,
    },
    "zhuqi": {
        "name": "朱雀AIGC检测",
        "url": "https://matrix.tencent.com/ai-detect/ai_gen",
        "description": "中文最准95%+，免费",
        "max_text_length": 5000,
    },
}

# AI率评估标准
AI_RATE_THRESHOLDS = {
    "excellent": 20,   # < 20% 优秀
    "acceptable": 40,  # 20-40% 可接受
    "needs_work": 60,  # 40-60% 需精修
    # > 60% 严重问题
}


@dataclass
class DetectResult:
    """检测结果."""

    platform: str
    ai_rate: float
    human_rate: float
    details: dict[str, Any] = field(default_factory=dict)
    timestamp: str = ""
    chapter_num: int | None = None
    notes: str = ""

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).isoformat()

    @property
    def level(self) -> str:
        """评估等级."""
        if self.ai_rate < AI_RATE_THRESHOLDS["excellent"]:
            return "excellent"
        elif self.ai_rate < AI_RATE_THRESHOLDS["acceptable"]:
            return "acceptable"
        elif self.ai_rate < AI_RATE_THRESHOLDS["needs_work"]:
            return "needs_work"
        else:
            return "critical"

    @property
    def level_label(self) -> str:
        """评估等级标签."""
        labels = {
            "excellent": "✅ 优秀",
            "acceptable": "⚠️ 可接受",
            "needs_work": "❌ 需精修",
            "critical": "🚨 严重问题",
        }
        return labels.get(self.level, "未知")


class OnlineDetector:
    """在线AI检测器."""

    def __init__(self, project_dir: str | Path | None = None) -> None:
        self._project_dir = Path(project_dir) if project_dir else None
        self._results_file = "ai_detect_results.json"

    def preprocess_text(self, text: str) -> str:
        """预处理文本：去除Markdown格式标记."""
        text = re.sub(r'^#{1,6}\s+', '', text, flags=re.MULTILINE)
        text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)
        text = re.sub(r'\*(.+?)\*', r'\1', text)
        text = re.sub(r'`(.+?)`', r'\1', text)
        text = re.sub(r'\[([^\]]+)\]\([^\)]+\)', r'\1', text)
        text = re.sub(r'^---+$', '', text, flags=re.MULTILINE)
        text = re.sub(r'\n{3,}', '\n\n', text)
        return text.strip()

    def split_text(self, text: str, max_length: int = 5000) -> list[dict[str, Any]]:
        """将长文本分段，每段不超过max_length."""
        if len(text) <= max_length:
            return [{"index": 0, "text": text, "length": len(text)}]

        paragraphs = text.split('\n\n')
        chunks = []
        current_chunk = ""
        chunk_index = 0

        for para in paragraphs:
            if len(current_chunk) + len(para) + 2 <= max_length:
                current_chunk += para + "\n\n"
            else:
                if current_chunk:
                    chunks.append({
                        "index": chunk_index,
                        "text": current_chunk.strip(),
                        "length": len(current_chunk.strip()),
                    })
                    chunk_index += 1
                if len(para) > max_length:
                    # 按句子分割长段落
                    sentences = re.split(r'(?<=[。！？])', para)
                    sub_chunk = ""
                    for s in sentences:
                        if len(sub_chunk) + len(s) <= max_length:
                            sub_chunk += s
                        else:
                            if sub_chunk:
                                chunks.append({
                                    "index": chunk_index,
                                    "text": sub_chunk.strip(),
                                    "length": len(sub_chunk.strip()),
                                })
                                chunk_index += 1
                            sub_chunk = s
                    if sub_chunk:
                        current_chunk = sub_chunk + "\n\n"
                    else:
                        current_chunk = ""
                else:
                    current_chunk = para + "\n\n"

        if current_chunk:
            chunks.append({
                "index": chunk_index,
                "text": current_chunk.strip(),
                "length": len(current_chunk.strip()),
            })

        return chunks

    def get_detect_instructions(self, text: str, platform: str = "tianyan") -> dict[str, Any]:
        """获取检测指令（供用户手动粘贴到检测平台）."""
        platform_config = DETECT_PLATFORMS.get(platform, DETECT_PLATFORMS["tianyan"])
        max_length = platform_config["max_text_length"]

        processed_text = self.preprocess_text(text)
        chunks = self.split_text(processed_text, max_length)

        return {
            "platform": platform_config,
            "text_length": len(processed_text),
            "chunks_count": len(chunks),
            "chunks": chunks,
            "instructions": f"请将以下文本粘贴到 {platform_config['name']} 进行检测：{platform_config['url']}",
        }

    def record_result(
        self,
        project_id: str,
        chapter_num: int,
        ai_rate: float,
        platform: str = "天眼AI",
        notes: str = "",
    ) -> DetectResult:
        """记录检测结果."""
        result = DetectResult(
            platform=platform,
            ai_rate=ai_rate,
            human_rate=100 - ai_rate,
            chapter_num=chapter_num,
            notes=notes,
        )

        # 保存到文件
        if self._project_dir:
            results_path = self._project_dir / self._results_file
            results = self._load_results(results_path)

            # 更新或添加
            found = False
            for ch in results.get("chapters", []):
                if ch.get("chapter") == chapter_num:
                    ch["ai_rate"] = ai_rate
                    ch["platform"] = platform
                    ch["date"] = result.timestamp
                    ch["notes"] = notes
                    found = True
                    break

            if not found:
                results.setdefault("chapters", []).append({
                    "chapter": chapter_num,
                    "ai_rate": ai_rate,
                    "platform": platform,
                    "date": result.timestamp,
                    "notes": notes,
                })

            results["chapters"].sort(key=lambda x: x.get("chapter", 0))
            results["last_updated"] = result.timestamp

            results_path.parent.mkdir(parents=True, exist_ok=True)
            results_path.write_text(
                json.dumps(results, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

        return result

    def get_results_summary(self, project_id: str) -> dict[str, Any]:
        """获取检测结果汇总."""
        if not self._project_dir:
            return {"chapters": [], "summary": {}}

        results_path = self._project_dir / self._results_file
        results = self._load_results(results_path)

        chapters = results.get("chapters", [])
        if not chapters:
            return {"chapters": [], "summary": {"total": 0}}

        ai_rates = [ch["ai_rate"] for ch in chapters]
        summary = {
            "total": len(chapters),
            "avg_ai_rate": round(sum(ai_rates) / len(ai_rates), 1),
            "min_ai_rate": min(ai_rates),
            "max_ai_rate": max(ai_rates),
            "excellent_count": sum(1 for r in ai_rates if r < 20),
            "acceptable_count": sum(1 for r in ai_rates if 20 <= r < 40),
            "needs_work_count": sum(1 for r in ai_rates if 40 <= r < 60),
            "critical_count": sum(1 for r in ai_rates if r >= 60),
        }

        return {
            "chapters": chapters,
            "summary": summary,
            "last_updated": results.get("last_updated", ""),
        }

    def _load_results(self, path: Path) -> dict[str, Any]:
        """加载检测结果."""
        if path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, FileNotFoundError):
                pass
        return {"chapters": [], "last_updated": ""}
