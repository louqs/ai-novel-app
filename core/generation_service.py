"""统一生成服务 — 大纲/章节生成的单一入口.

所有大纲生成路径（workbench、stream、async）统一调用此服务。
stream.py 变成纯 SSE 包装层，不再硬编码 prompt。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Callable, Awaitable

from core.logging_config import get_logger
from core.version_manager import VersionManager
from models.project import NovelLength

logger = get_logger(__name__)


@dataclass
class ProjectData:
    """项目元数据 + 角色 + 设定."""

    project_id: str = ""
    platform: str = "fanqie"
    one_liner: str = ""
    title: str = ""
    length: str = "long"
    target_words_per_chapter: int = 3000
    min_words: int | None = None
    max_words: int | None = None
    genre_tags: list[str] = field(default_factory=list)
    settings: dict[str, Any] = field(default_factory=dict)
    characters: dict[str, Any] = field(default_factory=dict)
    direction: dict[str, Any] = field(default_factory=dict)
    progress: dict[str, Any] = field(default_factory=dict)


@dataclass
class OutlineResult:
    """大纲生成结果."""

    success: bool = True
    progress: dict[str, Any] = field(default_factory=dict)
    style_hint: str = ""
    temperature: float = 0.7
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "progress": self.progress,
            "style_hint": self.style_hint,
            "temperature": self.temperature,
            "error": self.error,
        }


# ---- 风格变体配置 ----
STYLE_HINTS = [
    "节奏紧凑，每章必有冲突或反转",
    "人物关系复杂，多线交织",
    "悬念层层递进，读者欲罢不能",
    "情感细腻，细节丰富",
    "爽感强烈，打脸升级不断",
]

STYLE_TEMPERATURES = [0.7, 0.85, 0.9, 0.95, 1.0]


class GenerationService:
    """统一生成服务."""

    def __init__(self, kernel: Any) -> None:
        self._kernel = kernel

    # ==================================================================
    # 大纲生成
    # ==================================================================

    async def generate_outline(
        self,
        project_id: str,
        *,
        style_hint: str = "",
        temperature: float = 0.7,
        total_chapters: int | None = None,
        volumes: int | None = None,
    ) -> OutlineResult:
        """调用 outline-planner 插件生成大纲.

        Args:
            project_id: 项目 ID
            style_hint: 风格提示（可选）
            temperature: LLM 温度
            total_chapters: 总章节数（None 则根据项目篇幅自动决定）
            volumes: 分卷数（None 则根据章节数自动决定）
        """
        try:
            data = await self.load_project_data(project_id)

            # 根据项目篇幅自动决定章节数和卷数
            if total_chapters is None:
                length_enum = NovelLength(data.length) if data.length in NovelLength.__members__.values() else NovelLength.LONG
                total_chapters = length_enum.default_chapters(
                    data.target_words_per_chapter, data.min_words, data.max_words,
                )
            if volumes is None:
                volumes = max(2, total_chapters // 25)

            logger.info("大纲参数", project_id=project_id, length=data.length,
                        total_chapters=total_chapters, volumes=volumes)

            # 将 style_hint 注入 direction
            direction = dict(data.direction)
            if style_hint:
                direction["style_hint"] = style_hint

            entry = await self._kernel.get_plugin("outline-planner")
            if not entry or not entry.instance:
                raise RuntimeError("outline-planner 插件未加载")

            progress_model = await entry.instance.plan_outline(
                settings=data.settings,
                characters=data.characters,
                direction=direction,
                platform=data.platform,
                total_chapters=total_chapters,
                volumes=volumes,
            )

            progress_dict = progress_model.model_dump() if hasattr(progress_model, 'model_dump') else progress_model

            return OutlineResult(
                success=True,
                progress=progress_dict,
                style_hint=style_hint,
                temperature=temperature,
            )
        except Exception as e:
            logger.error("大纲生成失败", error=str(e))
            return OutlineResult(success=False, error=str(e))

    async def generate_outline_versions(
        self,
        project_id: str,
        *,
        num_versions: int = 3,
        total_chapters: int | None = None,
        volumes: int | None = None,
        on_progress: Callable[[int, OutlineResult], Awaitable[None]] | None = None,
    ) -> list[OutlineResult]:
        """生成多个风格变体.

        Args:
            project_id: 项目 ID
            num_versions: 生成数量
            on_progress: 进度回调 (version_index, result)
        """
        results = []
        for i in range(num_versions):
            hint = STYLE_HINTS[i % len(STYLE_HINTS)]
            temp = STYLE_TEMPERATURES[i % len(STYLE_TEMPERATURES)]

            result = await self.generate_outline(
                project_id,
                style_hint=hint,
                temperature=temp,
                total_chapters=total_chapters,
                volumes=volumes,
            )
            results.append(result)

            if on_progress:
                await on_progress(i, result)

        return results

    # ==================================================================
    # 保存与快照
    # ==================================================================

    async def save_outline(
        self, project_id: str, progress: dict[str, Any], *,
        snapshot_source: str = "auto", snapshot_summary: str = "",
    ) -> None:
        """保存大纲到 3 个存储（context + file + DB，DB 自动快照旧版本）."""
        ns = f"project:{project_id}"

        # Context
        await self._kernel.context().set(ns, "progress", progress)

        # 文件
        import json
        await self._kernel.write_project_file(
            project_id, "progress.json", json.dumps(progress, ensure_ascii=False, indent=2),
        )

        # DB（save_outline 自动快照旧版本）
        if self._kernel.db:
            await self._kernel.db.save_outline(
                project_id, progress,
                snapshot_source=snapshot_source, snapshot_summary=snapshot_summary,
            )

        # 同步大纲伏笔到 foreshadows.json
        await self.sync_outline_foreshadows(project_id, progress)

    async def snapshot_outline(
        self,
        project_id: str,
        progress: dict[str, Any],
        source: str = "generate",
        change_summary: str = "",
    ) -> None:
        """版本快照（统一 try/except + 日志）."""
        if not self._kernel.db or not progress or not progress.get("volumes"):
            return
        try:
            vm = VersionManager(self._kernel.db)
            await vm.snapshot_outline(project_id, progress, source=source, change_summary=change_summary)
        except Exception as e:
            logger.warning("版本快照失败", error=str(e))

    async def sync_outline_foreshadows(self, project_id: str, progress: dict[str, Any]) -> None:
        """从大纲章节节点提取伏笔计划，同步到 foreshadows.json.

        将 foreshadow_plants 写入为 status="planted", source="outline" 的条目，
        这样正文生成时这些伏笔会出现在 prompt 的"需要推进的伏笔"段落中。
        已存在的大纲伏笔（按 description 去重）不会重复创建。
        """
        import json
        import uuid

        # 读取已有伏笔
        try:
            raw = await self._kernel.read_project_file(project_id, "foreshadows.json")
            foreshadows = json.loads(raw)
        except (FileNotFoundError, json.JSONDecodeError):
            foreshadows = {"project_id": project_id, "entries": {}}

        entries = foreshadows.get("entries", {})

        # 已有的大纲来源伏笔 description 集合（用于去重）
        existing_descs = {
            e["description"] for e in entries.values()
            if isinstance(e, dict) and e.get("source") == "outline"
        }

        new_count = 0
        for vol in progress.get("volumes", []):
            for ch in vol.get("chapters", []):
                ch_num = ch.get("chapter_number", 0)
                for plant in ch.get("foreshadow_plants", []):
                    plant_desc = plant.strip() if isinstance(plant, str) else str(plant).strip()
                    if not plant_desc or plant_desc in existing_descs:
                        continue
                    fs_id = f"fs_{uuid.uuid4().hex[:8]}"
                    entries[fs_id] = {
                        "foreshadow_id": fs_id,
                        "type": "plot_twist",
                        "description": plant_desc,
                        "planted_chapter": ch_num,
                        "involved_characters": [],
                        "involved_items": [],
                        "status": "planted",
                        "priority": 3,
                        "building_chapters": [ch_num],
                        "source": "outline",
                    }
                    existing_descs.add(plant_desc)
                    new_count += 1

        if new_count > 0:
            foreshadows["entries"] = entries
            await self._kernel.write_project_file(
                project_id, "foreshadows.json",
                json.dumps(foreshadows, ensure_ascii=False, indent=2),
            )
            logger.info("大纲伏笔已同步", project_id=project_id, new_count=new_count)

    # ==================================================================
    # 加载项目数据
    # ==================================================================

    async def load_project_data(self, project_id: str) -> ProjectData:
        """加载项目元数据 + 角色 + 设定."""
        data = ProjectData(project_id=project_id)
        ns = f"project:{project_id}"

        # 项目元数据
        if self._kernel.db:
            try:
                meta = await self._kernel.db.get_project(project_id)
                if meta:
                    data.platform = meta.get("platform", "fanqie")
                    data.one_liner = meta.get("one_liner", "")
                    data.title = meta.get("title", "")
                    data.length = meta.get("length", "long")
                    data.target_words_per_chapter = meta.get("target_words_per_chapter", 3000)
                    data.min_words = meta.get("min_words")
                    data.max_words = meta.get("max_words")
                    import json
                    genre_str = meta.get("genre_tags", "[]")
                    data.genre_tags = json.loads(genre_str) if isinstance(genre_str, str) else genre_str
            except Exception:
                pass

        # 设定
        data.settings = await self._kernel.context().get(ns, "settings", {})
        if not data.settings and self._kernel.db:
            try:
                data.settings = await self._kernel.db.get_settings(project_id)
            except Exception:
                pass

        # 角色
        data.characters = await self._kernel.context().get(ns, "characters", {})
        if not data.characters and self._kernel.db:
            try:
                data.characters = await self._kernel.db.get_characters(project_id)
            except Exception:
                pass

        # 方向
        data.direction = await self._kernel.context().get(ns, "direction", {})

        # 如果 direction 为空但有 one_liner，用项目信息自动填充
        if not data.direction.get("logline") and data.one_liner:
            data.direction["logline"] = data.one_liner
        if not data.direction.get("title") and data.title:
            data.direction["title"] = data.title

        # 大纲进度
        data.progress = await self._kernel.context().get(ns, "progress", {})
        if not data.progress and data.settings:
            data.progress = data.settings.get("progress", {})

        return data

    async def load_characters_text(self, project_id: str) -> str:
        """加载角色并格式化为文本摘要."""
        data = await self.load_project_data(project_id)
        chars = data.characters.get("characters", {})
        if not chars:
            return "暂无人物"
        lines = []
        for name, info in chars.items():
            if isinstance(info, dict):
                tags = info.get("tags", [])
                tag_str = "/".join(tags[:3]) if tags else ""
                lines.append(f"{name}: {tag_str}" if tag_str else name)
            else:
                lines.append(str(name))
        return "\n".join(lines) if lines else "暂无人物"
