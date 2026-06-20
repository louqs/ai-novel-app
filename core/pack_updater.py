"""知识包自动更新器 — 使用公开合法数据源.

数据源说明：
1. 平台规则 — 来自官方帮助中心（公开信息）
2. AI 特征 — 通过本地测试自动生成
3. 热门题材 — 来自公开榜单 API
4. 写作技巧 — 来自公共领域知识

所有内容均为原创或来自公开合法渠道，不涉及侵权。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
import yaml

from core.logging_config import get_logger

logger = get_logger(__name__)

# 更新源配置
UPDATE_SOURCES = {
    "platform-rules": {
        "description": "平台规则更新",
        "interval_days": 30,
        "sources": [
            {
                "name": "番茄小说作者帮助",
                "url": "https://fanqiehelp.com",
                "type": "web_scrape",
                "legal_note": "公开帮助中心，允许个人使用",
            },
        ],
    },
    "hot-genres": {
        "description": "热门题材更新",
        "interval_days": 7,
        "sources": [
            {
                "name": "番茄小说榜单",
                "url": "https://fanqie.com/api/rank",
                "type": "api",
                "legal_note": "公开榜单数据",
            },
        ],
    },
    "ai-patterns": {
        "description": "AI 模式特征更新",
        "interval_days": 14,
        "sources": [
            {
                "name": "本地测试",
                "type": "local_test",
                "legal_note": "通过本地测试自动生成，不依赖外部数据",
            },
        ],
    },
}


class PackUpdater:
    """知识包自动更新器."""

    def __init__(self, kernel: Any, packs_dir: str | Path = "knowledge_base/packs") -> None:
        self._kernel = kernel
        self._packs_dir = Path(packs_dir)
        self._update_log_path = self._packs_dir / ".update_log.json"

    async def check_updates(self) -> list[dict[str, Any]]:
        """检查哪些知识包需要更新."""
        updates_needed = []
        update_log = self._load_update_log()

        for pack_name, config in UPDATE_SOURCES.items():
            pack_dir = self._packs_dir / pack_name
            if not pack_dir.exists():
                continue

            last_update = update_log.get(pack_name, {}).get("last_update")
            interval_days = config.get("interval_days", 30)

            if self._needs_update(last_update, interval_days):
                updates_needed.append({
                    "pack_name": pack_name,
                    "description": config.get("description", ""),
                    "last_update": last_update,
                    "sources": config.get("sources", []),
                })

        return updates_needed

    async def update_pack(self, pack_name: str) -> dict[str, Any]:
        """更新指定知识包."""
        if pack_name not in UPDATE_SOURCES:
            return {"name": pack_name, "updated": False, "error": "不支持自动更新此知识包"}

        config = UPDATE_SOURCES[pack_name]
        sources = config.get("sources", [])

        results = []
        for source in sources:
            try:
                result = await self._fetch_from_source(source)
                results.append(result)
            except Exception as exc:
                logger.warning("数据源获取失败", source=source.get("name"), error=str(exc))
                results.append({"source": source.get("name"), "success": False, "error": str(exc)})

        # 合并结果并更新知识包
        success_count = sum(1 for r in results if r.get("success"))
        if success_count > 0:
            await self._apply_updates(pack_name, results)
            self._update_log(pack_name)
            return {
                "name": pack_name,
                "updated": True,
                "sources_updated": success_count,
                "results": results,
            }

        return {
            "name": pack_name,
            "updated": False,
            "error": "所有数据源更新失败",
            "results": results,
        }

    async def update_all(self) -> dict[str, Any]:
        """更新所有需要更新的知识包."""
        updates_needed = await self.check_updates()
        results = []

        for update in updates_needed:
            pack_name = update["pack_name"]
            result = await self.update_pack(pack_name)
            results.append(result)

        return {
            "total_checked": len(updates_needed),
            "updated": sum(1 for r in results if r.get("updated")),
            "results": results,
        }

    # =========================================================================
    # 数据源获取
    # =========================================================================

    async def _fetch_from_source(self, source: dict[str, Any]) -> dict[str, Any]:
        """从数据源获取更新内容."""
        source_type = source.get("type", "")

        if source_type == "api":
            return await self._fetch_from_api(source)
        elif source_type == "web_scrape":
            return await self._fetch_from_web(source)
        elif source_type == "local_test":
            return await self._generate_from_local_test(source)
        else:
            return {"source": source.get("name"), "success": False, "error": "未知数据源类型"}

    async def _fetch_from_api(self, source: dict[str, Any]) -> dict[str, Any]:
        """从公开 API 获取数据."""
        url = source.get("url", "")
        if not url:
            return {"source": source.get("name"), "success": False, "error": "URL 为空"}

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.get(url)
                response.raise_for_status()
                data = response.json()

                return {
                    "source": source.get("name"),
                    "success": True,
                    "data": data,
                    "fetched_at": datetime.now(timezone.utc).isoformat(),
                }
        except Exception as exc:
            return {"source": source.get("name"), "success": False, "error": str(exc)}

    async def _fetch_from_web(self, source: dict[str, Any]) -> dict[str, Any]:
        """从公开网页获取信息（仅提取文本，不复制受版权保护的内容）."""
        url = source.get("url", "")
        if not url:
            return {"source": source.get("name"), "success": False, "error": "URL 为空"}

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.get(url)
                response.raise_for_status()

                # 只提取基本信息，不复制完整内容
                return {
                    "source": source.get("name"),
                    "success": True,
                    "status_code": response.status_code,
                    "content_length": len(response.text),
                    "fetched_at": datetime.now(timezone.utc).isoformat(),
                    "note": "需要人工处理提取的内容",
                }
        except Exception as exc:
            return {"source": source.get("name"), "success": False, "error": str(exc)}

    async def _generate_from_local_test(self, source: dict[str, Any]) -> dict[str, Any]:
        """通过本地测试生成 AI 模式特征."""
        try:
            # 使用当前配置的 LLM 进行测试
            kernel = self._kernel
            if not kernel:
                return {"source": source.get("name"), "success": False, "error": "内核未初始化"}

            # 生成测试文本并分析 AI 特征
            test_prompt = """请分析以下 AI 写作的常见特征，返回 JSON 格式：

```json
{
  "high_freq_words": ["高频词1", "高频词2"],
  "sentence_patterns": ["句式模式1", "句式模式2"],
  "structural_patterns": ["结构模式1", "结构模式2"]
}
```

这些特征用于反 AI 检测，帮助提高小说的原创性。"""

            result = await kernel.call_llm(
                messages=[{"role": "user", "content": test_prompt}],
                tier="budget",
                max_tokens=1024,
                temperature=0.3,
            )

            # 解析结果
            content = result.get("content", "")
            try:
                # 尝试提取 JSON
                import re
                json_match = re.search(r'```(?:json)?\s*([\s\S]*?)```', content)
                if json_match:
                    data = json.loads(json_match.group(1))
                else:
                    data = json.loads(content)

                return {
                    "source": source.get("name"),
                    "success": True,
                    "data": data,
                    "fetched_at": datetime.now(timezone.utc).isoformat(),
                }
            except json.JSONDecodeError:
                return {
                    "source": source.get("name"),
                    "success": False,
                    "error": "无法解析 LLM 返回的 JSON",
                }

        except Exception as exc:
            return {"source": source.get("name"), "success": False, "error": str(exc)}

    # =========================================================================
    # 更新应用
    # =========================================================================

    async def _apply_updates(self, pack_name: str, results: list[dict[str, Any]]) -> None:
        """将获取的更新应用到知识包."""
        pack_dir = self._packs_dir / pack_name
        content_dir = pack_dir / "content"
        content_dir.mkdir(parents=True, exist_ok=True)

        # 更新 pack.yaml 中的版本号和更新时间
        pack_yaml = pack_dir / "pack.yaml"
        if pack_yaml.exists():
            meta = yaml.safe_load(pack_yaml.read_text(encoding="utf-8"))
            version = meta.get("version", "0.1.0")
            # 递增版本号
            parts = version.split(".")
            if len(parts) == 3:
                parts[2] = str(int(parts[2]) + 1)
                meta["version"] = ".".join(parts)
            meta["updated_at"] = datetime.now(timezone.utc).isoformat()
            pack_yaml.write_text(yaml.dump(meta, allow_unicode=True), encoding="utf-8")

        # 根据包类型生成更新内容
        if pack_name == "ai-patterns":
            await self._update_ai_patterns(content_dir, results)
        elif pack_name == "hot-genres":
            await self._update_hot_genres(content_dir, results)
        elif pack_name == "platform-rules":
            await self._update_platform_rules(content_dir, results)

        logger.info("知识包已更新", pack_name=pack_name)

    async def _update_ai_patterns(self, content_dir: Path, results: list[dict[str, Any]]) -> None:
        """更新 AI 模式特征."""
        patterns_file = content_dir / "patterns.yaml"

        # 读取现有模式
        existing = {}
        if patterns_file.exists():
            existing = yaml.safe_load(patterns_file.read_text(encoding="utf-8")) or {}

        # 合并新特征
        for result in results:
            if result.get("success") and result.get("data"):
                data = result["data"]
                # 合并高频词
                existing_words = existing.get("patterns", {}).get("ai_words", {}).get("words", [])
                new_words = data.get("high_freq_words", [])
                combined_words = list(set(existing_words + new_words))

                # 更新配置
                if "patterns" not in existing:
                    existing["patterns"] = {}
                if "ai_words" not in existing["patterns"]:
                    existing["patterns"]["ai_words"] = {}
                existing["patterns"]["ai_words"]["words"] = combined_words

        # 保存
        patterns_file.write_text(yaml.dump(existing, allow_unicode=True), encoding="utf-8")

    async def _update_hot_genres(self, content_dir: Path, results: list[dict[str, Any]]) -> None:
        """更新热门题材."""
        genres_file = content_dir / "hot_genres.yaml"

        # 读取现有数据
        existing = {}
        if genres_file.exists():
            existing = yaml.safe_load(genres_file.read_text(encoding="utf-8")) or {}

        # 合并新数据
        for result in results:
            if result.get("success") and result.get("data"):
                data = result["data"]
                # 根据数据结构更新
                if isinstance(data, dict):
                    existing.update(data)
                elif isinstance(data, list):
                    existing["genres"] = data

        # 添加更新时间
        existing["last_updated"] = datetime.now(timezone.utc).isoformat()

        # 保存
        genres_file.write_text(yaml.dump(existing, allow_unicode=True), encoding="utf-8")

    async def _update_platform_rules(self, content_dir: Path, results: list[dict[str, Any]]) -> None:
        """更新平台规则（仅标记需要人工检查）."""
        update_marker = content_dir / ".update_needed.json"

        marker_data = {
            "needs_review": True,
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "sources": [r.get("source") for r in results if r.get("success")],
            "note": "平台规则已检查，请人工查看并更新相关内容",
        }

        update_marker.write_text(json.dumps(marker_data, ensure_ascii=False, indent=2), encoding="utf-8")

    # =========================================================================
    # 工具方法
    # =========================================================================

    def _load_update_log(self) -> dict[str, Any]:
        """加载更新日志."""
        if self._update_log_path.exists():
            try:
                return json.loads(self._update_log_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, FileNotFoundError):
                pass
        return {}

    def _save_update_log(self, log: dict[str, Any]) -> None:
        """保存更新日志."""
        self._update_log_path.write_text(
            json.dumps(log, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )

    def _update_log(self, pack_name: str) -> None:
        """记录更新时间."""
        log = self._load_update_log()
        log[pack_name] = {
            "last_update": datetime.now(timezone.utc).isoformat(),
        }
        self._save_update_log(log)

    def _needs_update(self, last_update: str | None, interval_days: int) -> bool:
        """检查是否需要更新."""
        if not last_update:
            return True

        try:
            last_dt = datetime.fromisoformat(last_update)
            now = datetime.now(timezone.utc)
            days_since = (now - last_dt).days
            return days_since >= interval_days
        except (ValueError, TypeError):
            return True
