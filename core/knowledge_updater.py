"""知识库自动更新——联网获取最新写作技巧、平台规则、赛道数据。

更新策略:
    1. 通过 LLM 生成最新的写作趋势分析
    2. 抓取公开的写作指南页面
    3. 缓存更新结果，避免重复请求

用法:
    updater = KnowledgeUpdater(kernel)
    result = await updater.update_all()
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
import yaml

from core.logging_config import get_logger

logger = get_logger(__name__)

UPDATE_SOURCES = {
    "hot_genres": {
        "file": "knowledge_base/genre_data/hot_genres.yaml",
        "prompt": """你是网文行业分析师。请根据当前（2026年）网文市场最新趋势，提供热门赛道分析数据。
格式要求 (YAML):
```yaml
genres:
  - name: 赛道名
    platform: 平台(fanqie/qidian/jinjiang)
    audience: 受众
    heat: 热度(★★★★★)
    description: 描述
    keywords: [标签]
    competition: 竞争度
    new_author_friendly: true/false
    updated: "2026-06-19"
```
请提供5-8个当前最热门的赛道。"""
    },
    "writing_tips": {
        "file": "knowledge_base/writing_tips/latest_tips.md",
        "prompt": """你是资深网文编辑。请提供2026年最新的网文写作技巧（500字以内）。
聚焦：当前最有效的开篇技巧、章尾钩子设计、平台算法偏好变化。
用Markdown格式，分点列出。""",
    },
    "southwest_dialect": {"file": "knowledge_base/writing_tips/southwest_dialect.md","prompt": "你是方言研究专家。请更新云贵川渝方言素材（300字以内）。提供高频口语、角色塑造技巧、使用原则。用Markdown格式。"},
    "internet_memes": {
        "file": "knowledge_base/writing_tips/internet_memes.md",
        "prompt": """你是网文热梗分析师。请提供当前（2026年）最新的网络热梗和谐音梗素材（500字以内）。
按类别整理：谐音梗、网络流行语、社会现象梗。每个梗标注使用场景和示例。
格式用Markdown，分点列出，标注时效性。""",
    },
    "platform_rules": {
        "file": "knowledge_base/platform_rules/latest_rules.md",
        "prompt": """请提供当前（2026年6月）主流网文平台的最新规则和算法变化：
- 番茄小说：全勤规则、推荐算法变化、热门品类
- 起点中文网：订阅分成变化、推荐机制
- 晋江文学城：榜单规则、签约政策变化
用Markdown格式，标注信息时效性。""",
    },
}


class KnowledgeUpdater:
    """知识库自动更新器——通过 LLM + 网络获取最新内容。"""

    def __init__(self, kernel: Any) -> None:
        self._kernel = kernel
        self._http = httpx.AsyncClient(timeout=30, follow_redirects=True)

    async def update_all(self) -> dict[str, Any]:
        """更新全部知识库内容。

        Returns:
            {"hot_genres": bool, "writing_tips": bool, "platform_rules": bool, "updated_at": str}
        """
        results = {}
        now = datetime.now(timezone.utc).isoformat()

        for name, config in UPDATE_SOURCES.items():
            try:
                success = await self._update_by_llm(name, config)
                results[name] = success
            except Exception as e:
                logger.warning(f"更新 {name} 失败: {e}")
                results[name] = False

        # 保存更新记录
        results["updated_at"] = now
        update_log = Path("knowledge_base/.update_log.json")
        update_log.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")

        logger.info("知识库更新完成", **results)
        return results

    async def _update_by_llm(self, name: str, config: dict) -> bool:
        """通过 LLM 生成最新内容。"""
        file_path = Path(config["file"])
        prompt = config["prompt"]

        # 检查是否需要更新（24小时内不重复更新）
        if file_path.exists():
            age_hours = (time.time() - file_path.stat().st_mtime) / 3600
            if age_hours < 24:
                logger.info(f"{name}: 内容较新 ({age_hours:.1f}h前)，跳过")
                return True

        logger.info(f"{name}: 正在通过 AI 获取最新内容...")
        result = await self._kernel.call_llm(
            messages=[
                {"role": "system", "content": "你是专业的网文行业分析师。请提供准确、具体的分析结果。直接输出内容，不要额外说明。"},
                {"role": "user", "content": prompt},
            ],
            tier="standard",
            max_tokens=4096,
            temperature=0.5,
        )

        content = result.get("content", "")
        if not content or len(content) < 50:
            return False

        # 提取代码块中的内容 (如果 LLM 返回了 markdown 代码块)
        import re
        match = re.search(r'```(?:yaml|markdown)?\s*([\s\S]*?)```', content)
        if match:
            content = match.group(1).strip()

        # 写文件
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content, encoding="utf-8")
        logger.info(f"{name}: 已更新 ({len(content)} 字符) → {file_path}")

        # 重新索引到 RAG
        await self._reindex_file(name, file_path, content)
        return True

    async def _reindex_file(self, name: str, file_path: Path, content: str) -> None:
        """将更新后的文件重新索引到 RAG，并同步更新知识包的更新时间。"""
        try:
            from models.rag import DocumentCategory, RAGDocument
            cat = {
                "hot_genres": DocumentCategory.GENRE_ANALYSIS,
                "writing_tips": DocumentCategory.WRITING_TIP,
                "platform_rules": DocumentCategory.PLATFORM_RULE,
            }.get(name, DocumentCategory.WRITING_TIP)

            doc = RAGDocument(
                doc_id=f"kb_updated_{name}",
                project_id=None,
                category=cat,
                content=content,
                metadata={"source": str(file_path), "updated": datetime.now(timezone.utc).isoformat(), "auto_generated": True},
            )
            # 尝试写入向量存储
            if self._kernel._retrieval_engine:
                store = getattr(self._kernel._retrieval_engine, '_store', None)
                if store and hasattr(store, 'index_documents'):
                    await store.index_documents([doc])

            # 同步更新对应知识包的更新时间
            pack_name_map = {
                "hot_genres": "hot-genres-2026",
                "writing_tips": "fanqie-writing-tips",
                "platform_rules": "platform-rules",
                "internet_memes": "internet-memes",
                "southwest_dialect": "southwest-dialect",
            }
            pack_name = pack_name_map.get(name)
            if pack_name:
                pack_yaml = Path("knowledge_base/packs") / pack_name / "pack.yaml"
                if pack_yaml.exists():
                    import yaml
                    meta = yaml.safe_load(pack_yaml.read_text(encoding="utf-8"))
                    meta["updated_at"] = datetime.now(timezone.utc).isoformat()
                    # 递增版本号
                    version = meta.get("version", "0.1.0")
                    parts = version.split(".")
                    if len(parts) == 3:
                        parts[2] = str(int(parts[2]) + 1)
                        meta["version"] = ".".join(parts)
                    pack_yaml.write_text(yaml.dump(meta, allow_unicode=True), encoding="utf-8")
                    logger.info("知识包版本已更新", pack_name=pack_name, version=meta.get("version"))

        except Exception as exc:
            logger.warning("RAG 索引失败", error=str(exc))

    async def get_update_status(self) -> dict[str, Any]:
        """获取更新状态。"""
        status = {}
        for name, config in UPDATE_SOURCES.items():
            file_path = Path(config["file"])
            if file_path.exists():
                age_hours = (time.time() - file_path.stat().st_mtime) / 3600
                status[name] = {
                    "exists": True,
                    "age_hours": round(age_hours, 1),
                    "size": file_path.stat().st_size,
                    "updated": datetime.fromtimestamp(file_path.stat().st_mtime).isoformat(),
                }
            else:
                status[name] = {"exists": False}
        return status

    async def close(self) -> None:
        await self._http.aclose()
