"""版本历史管理器 — 章节/大纲的版本追踪、对比与回滚.

每次保存章节或大纲时，在覆盖前自动调用本模块创建版本快照。
支持版本列表、内容对比和一键回滚。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from difflib import unified_diff
from typing import Any

from core.database import DatabaseManager
from core.logging_config import get_logger

logger = get_logger(__name__)

# 默认保留版本数
DEFAULT_KEEP_VERSIONS = 20


class VersionManager:
    """版本历史管理器."""

    def __init__(self, db: DatabaseManager) -> None:
        self._db = db

    # ==================================================================
    # 章节版本
    # ==================================================================

    async def snapshot_chapter(
        self,
        project_id: str,
        chapter_id: str,
        chapter_number: int,
        title: str,
        content: str,
        *,
        volume_number: int = 1,
        word_count: int = 0,
        ai_score: float = 0,
        source: str = "manual",
        change_summary: str = "",
    ) -> int | None:
        """保存章节版本快照（在覆盖前调用）.

        如果当前内容为空则跳过（首次生成无需快照）。
        Returns: 版本ID，跳过时返回 None。
        """
        if not content or not content.strip():
            return None

        try:
            vid = await self._db.save_chapter_version(
                project_id=project_id,
                chapter_id=chapter_id,
                chapter_number=chapter_number,
                title=title,
                content=content,
                word_count=word_count or len(content),
                ai_score=ai_score,
                volume_number=volume_number,
                source=source,
                change_summary=change_summary,
            )
            # 清理旧版本
            await self._db.delete_old_chapter_versions(
                project_id, chapter_number, volume_number, keep=DEFAULT_KEEP_VERSIONS
            )
            logger.info(
                "章节版本快照已保存",
                version_id=vid,
                project_id=project_id,
                chapter=chapter_number,
                volume=volume_number,
                source=source,
            )
            return vid
        except Exception as e:
            logger.error("章节版本快照失败", error=str(e), project_id=project_id, chapter=chapter_number)
            return None

    async def list_chapter_versions(
        self, project_id: str, chapter_number: int, volume_number: int = 1
    ) -> list[dict]:
        """列出某章节的所有历史版本."""
        return await self._db.list_chapter_versions(project_id, chapter_number, volume_number)

    async def get_chapter_version(self, version_id: int) -> dict | None:
        """获取特定版本的完整内容."""
        return await self._db.get_chapter_version(version_id)

    async def diff_chapter_versions(self, version_id_a: int, version_id_b: int) -> dict:
        """对比两个版本的差异.

        Returns:
            {
                "version_a": {...metadata...},
                "version_b": {...metadata...},
                "diff_text": "unified diff string",
                "diff_items": [{"type": "equal"|"add"|"del", "text": str}],
                "stats": {"added": int, "removed": int, "unchanged": int}
            }
        """
        va = await self._db.get_chapter_version(version_id_a)
        vb = await self._db.get_chapter_version(version_id_b)
        if not va or not vb:
            raise ValueError("版本不存在")

        content_a = va.get("content", "")
        content_b = vb.get("content", "")
        lines_a = content_a.splitlines(keepends=True)
        lines_b = content_b.splitlines(keepends=True)

        diff_text = "".join(
            unified_diff(lines_a, lines_b, fromfile=f"v{version_id_a}", tofile=f"v{version_id_b}")
        )

        # 构建段落级diff
        paras_a = content_a.split("\n\n")
        paras_b = content_b.split("\n\n")
        diff_items = self._build_paragraph_diff(paras_a, paras_b)

        added = sum(1 for d in diff_items if d["type"] == "add")
        removed = sum(1 for d in diff_items if d["type"] == "del")
        unchanged = sum(1 for d in diff_items if d["type"] == "equal")

        return {
            "version_a": {k: va[k] for k in va if k != "content"},
            "version_b": {k: vb[k] for k in vb if k != "content"},
            "diff_text": diff_text,
            "diff_items": diff_items,
            "stats": {"added": added, "removed": removed, "unchanged": unchanged},
        }

    async def rollback_chapter(
        self,
        project_id: str,
        chapter_number: int,
        volume_number: int,
        target_version_id: int,
        *,
        kernel: Any = None,
    ) -> dict:
        """回滚章节到指定版本（不创建新历史版本，只切换当前版本指针）."""
        target = await self._db.get_chapter_version(target_version_id)
        if not target:
            raise ValueError(f"版本 {target_version_id} 不存在")

        chapter_id = target["chapter_id"]
        content = target["content"]
        title = target.get("title", "")

        # 覆盖数据库（跳过自动快照，回滚不创建新版本）
        await self._db.save_chapter(chapter_id, project_id, chapter_number, title, content, volume=volume_number, auto_snapshot=False)

        # 覆盖文件系统
        if kernel:
            await kernel.write_project_file(project_id, f"chapters/{chapter_id}.md", content)

        # 存储当前版本指针
        key = f"ch_ver_{project_id}_{volume_number}_{chapter_number}"
        if kernel and kernel.db:
            settings = await kernel.db.get_settings(project_id)
            settings[key] = target_version_id
            await kernel.db.save_settings(project_id, settings)

        logger.info(
            "章节已回滚",
            project_id=project_id,
            chapter=chapter_number,
            target_version=target_version_id,
        )
        return {"status": "rolled_back", "version_id": target_version_id, "word_count": len(content)}

    # ==================================================================
    # 大纲版本
    # ==================================================================

    async def snapshot_outline(
        self,
        project_id: str,
        outline_data: dict,
        *,
        source: str = "manual",
        change_summary: str = "",
    ) -> int | None:
        """保存大纲版本快照（在覆盖前调用）.

        如果大纲数据为空则跳过。
        Returns: 版本ID，跳过时返回 None。
        """
        if not outline_data:
            logger.warning("snapshot_outline: outline_data 为空", project_id=project_id, source=source)
            return None
        if not outline_data.get("volumes"):
            logger.warning("snapshot_outline: outline_data 没有 volumes", project_id=project_id, source=source, keys=list(outline_data.keys()))
            return None

        try:
            volumes_count = len(outline_data.get("volumes", []))
            chapters_count = sum(
                len(v.get("chapters", [])) for v in outline_data.get("volumes", [])
            )
            logger.info("snapshot_outline: 开始保存", project_id=project_id, source=source, volumes=volumes_count, chapters=chapters_count)
            vid = await self._db.save_outline_version(
                project_id=project_id,
                outline_data=json.dumps(outline_data, ensure_ascii=False),
                volumes_count=volumes_count,
                chapters_count=chapters_count,
                source=source,
                change_summary=change_summary,
            )
            await self._db.delete_old_outline_versions(project_id, keep=DEFAULT_KEEP_VERSIONS)
            logger.info(
                "大纲版本快照已保存",
                version_id=vid,
                project_id=project_id,
                source=source,
            )
            return vid
        except Exception as e:
            logger.error("大纲版本快照失败", error=str(e), project_id=project_id)
            return None

    async def list_outline_versions(self, project_id: str) -> list[dict]:
        """列出项目大纲的所有历史版本."""
        return await self._db.list_outline_versions(project_id)

    async def get_outline_version(self, version_id: int) -> dict | None:
        """获取特定版本的大纲数据."""
        row = await self._db.get_outline_version(version_id)
        if row and isinstance(row.get("outline_data"), str):
            row["outline_data"] = json.loads(row["outline_data"])
        return row

    async def diff_outline_versions(self, version_id_a: int, version_id_b: int) -> dict:
        """对比两个大纲版本的差异.

        Returns:
            {
                "version_a": {...metadata...},
                "version_b": {...metadata...},
                "changes": [{"field": str, "old": Any, "new": Any}],
                "summary": str
            }
        """
        va = await self.get_outline_version(version_id_a)
        vb = await self.get_outline_version(version_id_b)
        if not va or not vb:
            raise ValueError("版本不存在")

        data_a = va.get("outline_data", {})
        data_b = vb.get("outline_data", {})
        changes = self._diff_outline_data(data_a, data_b)

        return {
            "version_a": {k: va[k] for k in va if k != "outline_data"},
            "version_b": {k: vb[k] for k in vb if k != "outline_data"},
            "changes": changes,
            "summary": f"共 {len(changes)} 处变更",
        }

    async def rollback_outline(
        self, project_id: str, target_version_id: int, *, kernel: Any = None
    ) -> dict:
        """回滚大纲到指定版本（不创建新历史版本，只切换当前版本指针）."""
        target = await self.get_outline_version(target_version_id)
        if not target:
            raise ValueError(f"版本 {target_version_id} 不存在")

        outline_data = target["outline_data"]

        # 覆盖文件系统
        if kernel:
            await kernel.write_project_file(
                project_id, "progress.json",
                json.dumps(outline_data, indent=2, ensure_ascii=False),
            )
            # 更新上下文
            ns = f"project:{project_id}"
            await kernel.context().set(ns, "progress", outline_data)
            # 更新数据库
            if kernel.db:
                settings = await kernel.db.get_settings(project_id)
                settings["progress"] = outline_data
                settings["outline_current_version_id"] = target_version_id
                await kernel.db.save_settings(project_id, settings)
        elif self._db:
            settings = await self._db.get_settings(project_id)
            settings["outline_current_version_id"] = target_version_id
            await self._db.save_settings(project_id, settings)

        logger.info("大纲已回滚", project_id=project_id, target_version=target_version_id)
        return {
            "status": "rolled_back",
            "version_id": target_version_id,
            "volumes": len(outline_data.get("volumes", [])),
            "chapters": sum(len(v.get("chapters", [])) for v in outline_data.get("volumes", [])),
        }

    # ==================================================================
    # 内部工具
    # ==================================================================

    @staticmethod
    def _build_paragraph_diff(paras_a: list[str], paras_b: list[str]) -> list[dict]:
        """构建段落级diff列表."""
        items = []
        max_len = max(len(paras_a), len(paras_b))
        for i in range(max_len):
            a = paras_a[i].strip() if i < len(paras_a) else ""
            b = paras_b[i].strip() if i < len(paras_b) else ""
            if a == b:
                items.append({"type": "equal", "text": a, "index": i})
            elif not a:
                items.append({"type": "add", "text": b, "index": i})
            elif not b:
                items.append({"type": "del", "text": a, "index": i})
            else:
                items.append({"type": "del", "text": a, "index": i})
                items.append({"type": "add", "text": b, "index": i})
        return items

    @staticmethod
    def _diff_outline_data(a: dict, b: dict) -> list[dict]:
        """对比两个大纲数据结构的差异."""
        changes = []

        # 对比卷级别
        vols_a = a.get("volumes", [])
        vols_b = b.get("volumes", [])
        max_vols = max(len(vols_a), len(vols_b))

        for i in range(max_vols):
            va = vols_a[i] if i < len(vols_a) else {}
            vb = vols_b[i] if i < len(vols_b) else {}

            if va.get("title") != vb.get("title"):
                changes.append({
                    "field": f"volumes[{i}].title",
                    "old": va.get("title", ""),
                    "new": vb.get("title", ""),
                })
            if va.get("arc_description") != vb.get("arc_description"):
                changes.append({
                    "field": f"volumes[{i}].arc_description",
                    "old": va.get("arc_description", ""),
                    "new": vb.get("arc_description", ""),
                })

            # 对比章节级别
            chs_a = va.get("chapters", [])
            chs_b = vb.get("chapters", [])
            max_chs = max(len(chs_a), len(chs_b))

            for j in range(max_chs):
                ca = chs_a[j] if j < len(chs_a) else {}
                cb = chs_b[j] if j < len(chs_b) else {}
                for key in ("title", "summary", "status"):
                    if ca.get(key) != cb.get(key):
                        changes.append({
                            "field": f"volumes[{i}].chapters[{j}].{key}",
                            "old": ca.get(key, ""),
                            "new": cb.get(key, ""),
                        })

        # 对比全局字段
        for key in ("quota_min_words_per_chapter", "quota_max_words_per_chapter"):
            if a.get(key) != b.get(key):
                changes.append({"field": key, "old": a.get(key), "new": b.get(key)})

        return changes
