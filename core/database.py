"""数据库管理器 — SQLite (零配置，始终可用)。"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from core.logging_config import get_logger

logger = get_logger(__name__)


class DatabaseManager:
    """SQLite 数据库管理器。"""

    def __init__(self, db_path: str = "data/novel.db") -> None:
        self._path = Path(db_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)

    async def connect(self) -> None:
        import aiosqlite
        async with aiosqlite.connect(str(self._path)) as db:
            db.row_factory = aiosqlite.Row
            for sql in [
                """CREATE TABLE IF NOT EXISTS projects (
                    id TEXT PRIMARY KEY, title TEXT, platform TEXT,
                    genre_tags TEXT, one_liner TEXT, status TEXT DEFAULT 'planning',
                    current_chapter INTEGER DEFAULT 0, meta_json TEXT,
                    created_at TEXT, updated_at TEXT
                )""",
                """CREATE TABLE IF NOT EXISTS chapters (
                    id TEXT PRIMARY KEY, project_id TEXT, chapter_number INTEGER,
                    volume_number INTEGER DEFAULT 1,
                    title TEXT, content TEXT, word_count INTEGER DEFAULT 0,
                    ai_score REAL DEFAULT 0, created_at TEXT
                )""",
                """CREATE TABLE IF NOT EXISTS characters (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, project_id TEXT, char_id TEXT,
                    name TEXT, data_json TEXT
                )""",
                """CREATE TABLE IF NOT EXISTS settings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, project_id TEXT UNIQUE, data_json TEXT
                )""",
                """CREATE TABLE IF NOT EXISTS providers (
                    name TEXT PRIMARY KEY, type TEXT, base_url TEXT,
                    api_key TEXT, default_model TEXT, models TEXT, enabled INTEGER DEFAULT 1
                )""",
                """CREATE TABLE IF NOT EXISTS model_settings (
                    tier TEXT PRIMARY KEY, provider TEXT, model TEXT, updated_at TEXT
                )""",
                """CREATE TABLE IF NOT EXISTS deleted_config_providers (
                    name TEXT PRIMARY KEY, deleted_at TEXT
                )""",
                """CREATE TABLE IF NOT EXISTS chapter_versions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_id TEXT NOT NULL,
                    chapter_id TEXT NOT NULL,
                    chapter_number INTEGER NOT NULL,
                    volume_number INTEGER DEFAULT 1,
                    title TEXT,
                    content TEXT NOT NULL,
                    word_count INTEGER DEFAULT 0,
                    ai_score REAL DEFAULT 0,
                    source TEXT DEFAULT 'manual',
                    change_summary TEXT DEFAULT '',
                    created_at TEXT NOT NULL
                )""",
                """CREATE TABLE IF NOT EXISTS outline_versions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_id TEXT NOT NULL,
                    outline_data TEXT NOT NULL,
                    volumes_count INTEGER DEFAULT 0,
                    chapters_count INTEGER DEFAULT 0,
                    source TEXT DEFAULT 'manual',
                    change_summary TEXT DEFAULT '',
                    created_at TEXT NOT NULL
                )""",
                """CREATE TABLE IF NOT EXISTS outline_jobs (
                    project_id TEXT PRIMARY KEY,
                    status TEXT DEFAULT 'generating',
                    total INTEGER DEFAULT 3,
                    current INTEGER DEFAULT 0,
                    versions_json TEXT DEFAULT '[]',
                    message TEXT DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )""",
            ]:
                await db.execute(sql)

            # 迁移：确保 volume_number 列存在
            cursor = await db.execute("PRAGMA table_info(chapters)")
            columns = [row[1] for row in await cursor.fetchall()]
            if "volume_number" not in columns:
                await db.execute("ALTER TABLE chapters ADD COLUMN volume_number INTEGER DEFAULT 1")
                logger.info("数据库迁移: chapters 表添加 volume_number 列")

            # 迁移：确保 chapter_versions 表的 chapter_id 列存在
            cursor = await db.execute("PRAGMA table_info(chapter_versions)")
            cv_columns = {row[1] for row in await cursor.fetchall()}
            if "chapter_id" not in cv_columns:
                await db.execute("ALTER TABLE chapter_versions ADD COLUMN chapter_id TEXT NOT NULL DEFAULT ''")
                logger.info("数据库迁移: chapter_versions 表添加 chapter_id 列")

            await db.execute("CREATE INDEX IF NOT EXISTS idx_ch ON chapters(project_id, chapter_number)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_char ON characters(project_id)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_cv ON chapter_versions(project_id, chapter_number, volume_number)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_ov ON outline_versions(project_id)")
            await db.commit()
        logger.info("SQLite 已连接", path=str(self._path))

    async def close(self) -> None:
        pass

    async def _ensure_outline_tables(self) -> None:
        """确保大纲相关表存在（兼容旧数据库）."""
        import aiosqlite
        async with aiosqlite.connect(str(self._path)) as db:
            # 创建表（如果不存在）
            await db.execute("""CREATE TABLE IF NOT EXISTS outline_versions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id TEXT NOT NULL,
                outline_data TEXT NOT NULL,
                volumes_count INTEGER DEFAULT 0,
                chapters_count INTEGER DEFAULT 0,
                source TEXT DEFAULT 'manual',
                change_summary TEXT DEFAULT '',
                created_at TEXT NOT NULL
            )""")
            await db.execute("""CREATE TABLE IF NOT EXISTS outline_jobs (
                project_id TEXT PRIMARY KEY,
                status TEXT DEFAULT 'generating',
                total INTEGER DEFAULT 3,
                current INTEGER DEFAULT 0,
                versions_json TEXT DEFAULT '[]',
                message TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )""")

            # 迁移：检查并添加缺失的列
            cursor = await db.execute("PRAGMA table_info(outline_versions)")
            columns = {row[1] for row in await cursor.fetchall()}

            # 处理旧表 data_json → outline_data 列名迁移
            if "data_json" in columns and "outline_data" not in columns:
                try:
                    await db.execute("ALTER TABLE outline_versions RENAME COLUMN data_json TO outline_data")
                    logger.info("数据库迁移: data_json → outline_data")
                except Exception:
                    # SQLite 版本太低不支持 RENAME COLUMN，用复制方式迁移
                    await db.execute("ALTER TABLE outline_versions ADD COLUMN outline_data TEXT NOT NULL DEFAULT '{}'")
                    await db.execute("UPDATE outline_versions SET outline_data = data_json WHERE outline_data = '{}'")
                    logger.info("数据库迁移: data_json 数据已复制到 outline_data")
            elif "data_json" in columns and "outline_data" in columns:
                # 两列都存在，把 data_json 的数据同步到 outline_data，然后删除旧列
                await db.execute("UPDATE outline_versions SET outline_data = data_json WHERE outline_data = '{}' OR outline_data IS NULL")
                try:
                    await db.execute("ALTER TABLE outline_versions DROP COLUMN data_json")
                    logger.info("数据库迁移: 已删除旧列 data_json")
                except Exception:
                    # SQLite < 3.35 不支持 DROP COLUMN，改为设为可空
                    # 重建表的方式太复杂，直接尝试将 NOT NULL 改为 NULL
                    logger.warning("数据库迁移: 无法删除 data_json 列，INSERT 将同时填充两列")

            if "outline_data" not in columns:
                await db.execute("ALTER TABLE outline_versions ADD COLUMN outline_data TEXT NOT NULL DEFAULT '{}'")
            if "volumes_count" not in columns:
                await db.execute("ALTER TABLE outline_versions ADD COLUMN volumes_count INTEGER DEFAULT 0")
            if "chapters_count" not in columns:
                await db.execute("ALTER TABLE outline_versions ADD COLUMN chapters_count INTEGER DEFAULT 0")
            if "source" not in columns:
                await db.execute("ALTER TABLE outline_versions ADD COLUMN source TEXT DEFAULT 'manual'")
            if "change_summary" not in columns:
                await db.execute("ALTER TABLE outline_versions ADD COLUMN change_summary TEXT DEFAULT ''")

            # 迁移旧列数据到新列（如果旧列存在）
            if "volume_count" in columns and "volumes_count" in columns:
                await db.execute("UPDATE outline_versions SET volumes_count = volume_count WHERE volumes_count = 0 AND volume_count > 0")
            if "chapter_count" in columns and "chapters_count" in columns:
                await db.execute("UPDATE outline_versions SET chapters_count = chapter_count WHERE chapters_count = 0 AND chapter_count > 0")

            # 尝试删除旧列（SQLite >= 3.35 支持 DROP COLUMN）
            for old_col in ("volume_count", "chapter_count", "version_tag", "source_description", "data_json"):
                if old_col in columns:
                    try:
                        await db.execute(f"ALTER TABLE outline_versions DROP COLUMN {old_col}")
                        logger.info("数据库迁移: 已删除旧列", column=old_col)
                    except Exception:
                        pass  # SQLite 版本不支持 DROP COLUMN，忽略

            await db.execute("CREATE INDEX IF NOT EXISTS idx_ov ON outline_versions(project_id)")
            await db.commit()

    async def _ensure_version_tables(self) -> None:
        """确保所有版本相关表存在（兼容旧数据库）."""
        await self._ensure_outline_tables()
        import aiosqlite
        async with aiosqlite.connect(str(self._path)) as db:
            await db.execute("""CREATE TABLE IF NOT EXISTS chapter_versions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id TEXT NOT NULL,
                chapter_id TEXT NOT NULL,
                chapter_number INTEGER NOT NULL,
                volume_number INTEGER DEFAULT 1,
                title TEXT,
                content TEXT NOT NULL,
                word_count INTEGER DEFAULT 0,
                ai_score REAL DEFAULT 0,
                source TEXT DEFAULT 'manual',
                change_summary TEXT DEFAULT '',
                created_at TEXT NOT NULL
            )""")

            # 迁移：检查并添加缺失的列
            cursor = await db.execute("PRAGMA table_info(chapter_versions)")
            columns = {row[1] for row in await cursor.fetchall()}
            if "chapter_id" not in columns:
                await db.execute("ALTER TABLE chapter_versions ADD COLUMN chapter_id TEXT NOT NULL DEFAULT ''")
                logger.info("数据库迁移: chapter_versions 表添加 chapter_id 列")
            if "volume_number" not in columns:
                await db.execute("ALTER TABLE chapter_versions ADD COLUMN volume_number INTEGER DEFAULT 1")
            if "source" not in columns:
                await db.execute("ALTER TABLE chapter_versions ADD COLUMN source TEXT DEFAULT 'manual'")
            if "change_summary" not in columns:
                await db.execute("ALTER TABLE chapter_versions ADD COLUMN change_summary TEXT DEFAULT ''")

            await db.execute("CREATE INDEX IF NOT EXISTS idx_cv ON chapter_versions(project_id, chapter_number, volume_number)")
            await db.commit()

    async def _fetch(self, sql: str, params: tuple = ()) -> list[dict]:
        import aiosqlite
        async with aiosqlite.connect(str(self._path)) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(sql, params)
            return [dict(r) for r in await cur.fetchall()]

    async def _exec(self, sql: str, params: tuple = ()) -> None:
        import aiosqlite
        async with aiosqlite.connect(str(self._path)) as db:
            await db.execute(sql, params)
            await db.commit()

    # ---- Project ----

    async def create_project(self, pid: str, data: dict) -> None:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        # 确保 datetime 对象转为字符串
        clean = {}
        for k, v in data.items():
            if isinstance(v, datetime):
                clean[k] = v.isoformat()
            elif isinstance(v, list):
                clean[k] = v
            else:
                clean[k] = v
        await self._exec(
            "INSERT INTO projects (id,title,platform,length,genre_tags,one_liner,status,current_chapter,meta_json,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (pid, clean.get("title",""), clean.get("platform","fanqie"),
             clean.get("length","medium"),
             json.dumps(clean.get("genre_tags",[])), clean.get("one_liner",""),
             "planning", 0, json.dumps(clean, ensure_ascii=False, default=str), now, now))

    async def list_projects(self) -> list[dict]:
        rows = await self._fetch("SELECT id as project_id, title, platform, genre_tags, one_liner, status, current_chapter, length FROM projects ORDER BY updated_at DESC")
        for r in rows:
            if isinstance(r.get("genre_tags"), str):
                try: r["genre_tags"] = json.loads(r["genre_tags"])
                except Exception: pass
        return rows

    async def get_project(self, pid: str) -> dict | None:
        rows = await self._fetch("SELECT * FROM projects WHERE id=?", (pid,))
        return rows[0] if rows else None

    async def update_project(self, pid: str, data: dict) -> None:
        sets = ", ".join(f"{k}=?" for k in data)
        await self._exec(f"UPDATE projects SET {sets} WHERE id=?", tuple(data.values()) + (pid,))

    async def delete_project(self, pid: str) -> None:
        await self._exec("DELETE FROM chapters WHERE project_id=?", (pid,))
        await self._exec("DELETE FROM characters WHERE project_id=?", (pid,))
        await self._exec("DELETE FROM settings WHERE project_id=?", (pid,))
        await self._exec("DELETE FROM projects WHERE id=?", (pid,))

    # ---- Chapter ----

    async def save_chapter(
        self, cid: str, pid: str, num: int, title: str, content: str,
        score: float = 0, volume: int = 1, *,
        auto_snapshot: bool = True,
        snapshot_source: str = "auto",
        snapshot_summary: str = "",
    ) -> None:
        """保存章节 — 自动快照当前版本后再覆盖."""
        if auto_snapshot:
            try:
                current = await self.get_chapter(pid, num, volume)
                if current and current.get("content"):
                    from core.version_manager import VersionManager
                    vm = VersionManager(self)
                    await vm.snapshot_chapter(
                        pid, cid, num,
                        current.get("title", ""), current["content"],
                        volume_number=volume, source=snapshot_source,
                        change_summary=snapshot_summary or f"自动快照({snapshot_source})",
                    )
            except Exception:
                pass  # 快照失败不影响保存
        await self._exec("DELETE FROM chapters WHERE id=?", (cid,))
        await self._exec(
            "INSERT INTO chapters (id,project_id,chapter_number,volume_number,title,content,word_count,ai_score,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (cid, pid, num, volume, title, content, len(content), score, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))

    async def get_chapter(self, pid: str, num: int, volume: int = 1) -> dict | None:
        rows = await self._fetch("SELECT * FROM chapters WHERE project_id=? AND chapter_number=? AND volume_number=?", (pid, num, volume))
        return rows[0] if rows else None

    async def list_chapters(self, pid: str) -> list[dict]:
        return await self._fetch("SELECT * FROM chapters WHERE project_id=? ORDER BY volume_number, chapter_number", (pid,))

    async def delete_chapter(self, pid: str, num: int, volume: int = 1) -> None:
        await self._exec("DELETE FROM chapters WHERE project_id=? AND chapter_number=? AND volume_number=?", (pid, num, volume))

    # ---- Character / Settings ----

    async def save_characters(self, pid: str, data: dict) -> None:
        await self._exec("DELETE FROM characters WHERE project_id=?", (pid,))
        for cid, cdata in data.get("characters", {}).items():
            name = cdata.get("name", cid) if isinstance(cdata, dict) else str(cdata)
            await self._exec("INSERT INTO characters (project_id,char_id,name,data_json) VALUES (?,?,?,?)",
                             (pid, cid, name, json.dumps(cdata, ensure_ascii=False)))

    async def get_characters(self, pid: str) -> dict:
        rows = await self._fetch("SELECT * FROM characters WHERE project_id=?", (pid,))
        chars = {}
        for r in rows:
            d = r.get("data_json", "{}")
            chars[r["char_id"]] = json.loads(d) if isinstance(d, str) else (d or {})
        return {"characters": chars}

    # ---- Providers ----

    async def save_provider(self, name: str, ptype: str, base_url: str, api_key: str, default_model: str, models: list | None = None) -> None:
        import json
        await self._exec("INSERT OR REPLACE INTO providers (name,type,base_url,api_key,default_model,models,enabled) VALUES (?,?,?,?,?,?,1)",
                         (name, ptype, base_url, api_key, default_model, json.dumps(models or [])))

    async def delete_provider(self, name: str) -> None:
        await self._exec("DELETE FROM providers WHERE name=?", (name,))

    async def list_providers_db(self) -> list[dict]:
        rows = await self._fetch("SELECT * FROM providers WHERE enabled=1")
        for r in rows:
            if isinstance(r.get("models"), str):
                try:
                    import json
                    r["models"] = json.loads(r["models"])
                except Exception:
                    r["models"] = []
        return rows

    # ---- 已删除的配置 Provider 管理 ----

    async def mark_config_provider_deleted(self, name: str) -> None:
        """标记配置文件中的 Provider 为已删除。"""
        from datetime import datetime, timezone
        await self._exec(
            "INSERT OR REPLACE INTO deleted_config_providers (name, deleted_at) VALUES (?, ?)",
            (name, datetime.now(timezone.utc).isoformat()),
        )

    async def get_deleted_config_providers(self) -> set[str]:
        """获取所有已删除的配置 Provider 名称。"""
        rows = await self._fetch("SELECT name FROM deleted_config_providers")
        return {r["name"] for r in rows}

    async def restore_config_provider(self, name: str) -> None:
        """恢复被删除的配置 Provider。"""
        await self._exec("DELETE FROM deleted_config_providers WHERE name=?", (name,))

    # ---- Model Settings (Tier 配置持久化) ----

    async def save_tier_setting(self, tier: str, provider: str, model: str) -> None:
        """保存 tier 配置到数据库."""
        await self._exec(
            "INSERT OR REPLACE INTO model_settings (tier, provider, model, updated_at) VALUES (?,?,?,?)",
            (tier, provider, model, datetime.now().isoformat())
        )

    async def load_tier_settings(self) -> dict[str, dict[str, str]]:
        """加载所有 tier 配置."""
        rows = await self._fetch("SELECT tier, provider, model FROM model_settings")
        return {r["tier"]: {"provider": r["provider"], "model": r["model"]} for r in rows}

    async def get_tier_setting(self, tier: str) -> dict[str, str] | None:
        """获取单个 tier 配置."""
        rows = await self._fetch("SELECT provider, model FROM model_settings WHERE tier=?", (tier,))
        if rows:
            return {"provider": rows[0]["provider"], "model": rows[0]["model"]}
        return None

    async def save_settings(self, pid: str, data: dict) -> None:
        await self._exec("DELETE FROM settings WHERE project_id=?", (pid,))
        await self._exec("INSERT INTO settings (project_id,data_json) VALUES (?,?)", (pid, json.dumps(data, ensure_ascii=False)))

    async def save_outline(
        self, pid: str, progress: dict, *,
        auto_snapshot: bool = True,
        snapshot_source: str = "auto",
        snapshot_summary: str = "",
    ) -> None:
        """保存大纲 — 自动快照当前版本后再覆盖."""
        if auto_snapshot:
            try:
                current_settings = await self.get_settings(pid)
                current_progress = current_settings.get("progress", {}) if current_settings else {}
                if current_progress and current_progress.get("volumes"):
                    from core.version_manager import VersionManager
                    vm = VersionManager(self)
                    await vm.snapshot_outline(
                        pid, current_progress,
                        source=snapshot_source,
                        change_summary=snapshot_summary or f"自动快照({snapshot_source})",
                    )
            except Exception:
                pass  # 快照失败不影响保存
        settings = await self.get_settings(pid) or {}
        settings["progress"] = progress
        await self.save_settings(pid, settings)

    async def get_settings(self, pid: str) -> dict:
        rows = await self._fetch("SELECT * FROM settings WHERE project_id=?", (pid,))
        if rows:
            d = rows[0].get("data_json", "{}")
            return json.loads(d) if isinstance(d, str) else (d or {})
        return {}

    # ---- Chapter Versions ----

    async def save_chapter_version(
        self, project_id: str, chapter_id: str, chapter_number: int,
        title: str, content: str, word_count: int = 0, ai_score: float = 0,
        volume_number: int = 1, source: str = "manual", change_summary: str = "",
    ) -> int:
        """保存章节版本快照，返回版本ID."""
        import aiosqlite
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        try:
            async with aiosqlite.connect(str(self._path)) as db:
                cursor = await db.execute(
                    "INSERT INTO chapter_versions (project_id,chapter_id,chapter_number,volume_number,title,content,word_count,ai_score,source,change_summary,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (project_id, chapter_id, chapter_number, volume_number, title, content, word_count, ai_score, source, change_summary, now),
                )
                await db.commit()
                return cursor.lastrowid or 0
        except Exception:
            await self._ensure_version_tables()
            async with aiosqlite.connect(str(self._path)) as db:
                cursor = await db.execute(
                    "INSERT INTO chapter_versions (project_id,chapter_id,chapter_number,volume_number,title,content,word_count,ai_score,source,change_summary,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (project_id, chapter_id, chapter_number, volume_number, title, content, word_count, ai_score, source, change_summary, now),
                )
                await db.commit()
                return cursor.lastrowid or 0

    async def list_chapter_versions(self, project_id: str, chapter_number: int, volume_number: int = 1) -> list[dict]:
        """列出某章节的所有历史版本（不含完整content，节省带宽）."""
        try:
            return await self._fetch(
                "SELECT id,project_id,chapter_id,chapter_number,volume_number,title,word_count,ai_score,source,change_summary,created_at FROM chapter_versions WHERE project_id=? AND chapter_number=? AND volume_number=? ORDER BY id DESC",
                (project_id, chapter_number, volume_number),
            )
        except Exception:
            await self._ensure_version_tables()
            return await self._fetch(
                "SELECT id,project_id,chapter_id,chapter_number,volume_number,title,word_count,ai_score,source,change_summary,created_at FROM chapter_versions WHERE project_id=? AND chapter_number=? AND volume_number=? ORDER BY id DESC",
                (project_id, chapter_number, volume_number),
            )

    async def get_chapter_version(self, version_id: int) -> dict | None:
        """获取特定版本的完整内容."""
        try:
            rows = await self._fetch("SELECT * FROM chapter_versions WHERE id=?", (version_id,))
        except Exception:
            await self._ensure_version_tables()
            rows = await self._fetch("SELECT * FROM chapter_versions WHERE id=?", (version_id,))
        return rows[0] if rows else None

    async def delete_chapter_version(self, version_id: int) -> bool:
        """删除指定的章节版本."""
        try:
            await self._exec("DELETE FROM chapter_versions WHERE id=?", (version_id,))
            return True
        except Exception:
            return False

    async def delete_old_chapter_versions(self, project_id: str, chapter_number: int, volume_number: int = 1, keep: int = 20) -> None:
        """清理旧版本，保留最近 keep 个."""
        rows = await self._fetch(
            "SELECT id FROM chapter_versions WHERE project_id=? AND chapter_number=? AND volume_number=? ORDER BY id DESC",
            (project_id, chapter_number, volume_number),
        )
        if len(rows) > keep:
            old_ids = [r["id"] for r in rows[keep:]]
            placeholders = ",".join("?" * len(old_ids))
            await self._exec(f"DELETE FROM chapter_versions WHERE id IN ({placeholders})", tuple(old_ids))

    # ---- Outline Versions ----

    async def save_outline_version(
        self, project_id: str, outline_data: str, volumes_count: int = 0,
        chapters_count: int = 0, source: str = "manual", change_summary: str = "",
    ) -> int:
        """保存大纲版本快照，返回版本ID."""
        import aiosqlite
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        try:
            async with aiosqlite.connect(str(self._path)) as db:
                cursor = await db.execute(
                    "INSERT INTO outline_versions (project_id,outline_data,volumes_count,chapters_count,source,change_summary,created_at) VALUES (?,?,?,?,?,?,?)",
                    (project_id, outline_data, volumes_count, chapters_count, source, change_summary, now),
                )
                await db.commit()
                vid = cursor.lastrowid or 0
                logger.info("保存大纲版本成功", version_id=vid, project_id=project_id, source=source)
                return vid
        except Exception as e:
            logger.warning("保存大纲版本失败，尝试建表重试", error=str(e))
            # 表可能不存在，创建后重试
            await self._ensure_outline_tables()
            async with aiosqlite.connect(str(self._path)) as db:
                # 检查是否有遗留的 data_json 列（旧表 schema）
                try:
                    pragma = await db.execute("PRAGMA table_info(outline_versions)")
                    cols = {row[1] for row in await pragma.fetchall()}
                except Exception:
                    cols = set()
                if "data_json" in cols:
                    # 旧表：同时填充 data_json 和 outline_data
                    cursor = await db.execute(
                        "INSERT INTO outline_versions (project_id,outline_data,data_json,volumes_count,chapters_count,source,change_summary,created_at) VALUES (?,?,?,?,?,?,?,?)",
                        (project_id, outline_data, outline_data, volumes_count, chapters_count, source, change_summary, now),
                    )
                else:
                    cursor = await db.execute(
                        "INSERT INTO outline_versions (project_id,outline_data,volumes_count,chapters_count,source,change_summary,created_at) VALUES (?,?,?,?,?,?,?)",
                        (project_id, outline_data, volumes_count, chapters_count, source, change_summary, now),
                    )
                await db.commit()
                vid = cursor.lastrowid or 0
                logger.info("保存大纲版本成功（重试）", version_id=vid, project_id=project_id, source=source)
                return vid

    async def list_outline_versions(self, project_id: str) -> list[dict]:
        """列出项目大纲的所有历史版本（不含完整数据）."""
        try:
            return await self._fetch(
                "SELECT id,project_id,volumes_count,chapters_count,source,change_summary,created_at FROM outline_versions WHERE project_id=? ORDER BY id DESC",
                (project_id,),
            )
        except Exception:
            # 表可能不存在，尝试创建后重试
            await self._ensure_outline_tables()
            return await self._fetch(
                "SELECT id,project_id,volumes_count,chapters_count,source,change_summary,created_at FROM outline_versions WHERE project_id=? ORDER BY id DESC",
                (project_id,),
            )

    async def get_outline_version(self, version_id: int) -> dict | None:
        """获取特定版本的大纲数据."""
        cols = "id,project_id,outline_data,volumes_count,chapters_count,source,change_summary,created_at"
        try:
            rows = await self._fetch(f"SELECT {cols} FROM outline_versions WHERE id=?", (version_id,))
        except Exception:
            await self._ensure_outline_tables()
            rows = await self._fetch(f"SELECT {cols} FROM outline_versions WHERE id=?", (version_id,))
        return rows[0] if rows else None

    async def delete_outline_version(self, version_id: int) -> bool:
        """删除指定的大纲版本."""
        try:
            await self._exec("DELETE FROM outline_versions WHERE id=?", (version_id,))
            return True
        except Exception:
            return False

    async def delete_old_outline_versions(self, project_id: str, keep: int = 20) -> None:
        """清理旧大纲版本，保留最近 keep 个."""
        rows = await self._fetch(
            "SELECT id FROM outline_versions WHERE project_id=? ORDER BY id DESC",
            (project_id,),
        )
        if len(rows) > keep:
            old_ids = [r["id"] for r in rows[keep:]]
            placeholders = ",".join("?" * len(old_ids))
            await self._exec(f"DELETE FROM outline_versions WHERE id IN ({placeholders})", tuple(old_ids))

    # ---- Outline Jobs (大纲生成任务持久化) ----

    async def save_outline_job(
        self, project_id: str, status: str = "generating",
        total: int = 3, current: int = 0, versions: list | None = None,
        message: str = "",
    ) -> None:
        """保存或更新大纲生成任务状态."""
        import aiosqlite
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        versions_json = json.dumps(versions or [], ensure_ascii=False)
        async with aiosqlite.connect(str(self._path)) as db:
            await db.execute(
                """INSERT INTO outline_jobs (project_id, status, total, current, versions_json, message, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(project_id) DO UPDATE SET
                   status=excluded.status, current=excluded.current,
                   versions_json=excluded.versions_json, message=excluded.message, updated_at=excluded.updated_at""",
                (project_id, status, total, current, versions_json, message, now, now),
            )
            await db.commit()

    async def get_outline_job(self, project_id: str) -> dict | None:
        """获取大纲生成任务状态."""
        rows = await self._fetch("SELECT * FROM outline_jobs WHERE project_id=?", (project_id,))
        if not rows:
            return None
        row = rows[0]
        # 解析 versions_json
        vj = row.get("versions_json", "[]")
        row["versions"] = json.loads(vj) if isinstance(vj, str) else (vj or [])
        return row

    async def delete_outline_job(self, project_id: str) -> None:
        """删除大纲生成任务."""
        await self._exec("DELETE FROM outline_jobs WHERE project_id=?", (project_id,))

    async def get_interrupted_outline_jobs(self) -> list[dict]:
        """获取所有中断的大纲生成任务（状态为 generating）."""
        rows = await self._fetch("SELECT * FROM outline_jobs WHERE status='generating'")
        result = []
        for row in rows:
            vj = row.get("versions_json", "[]")
            row["versions"] = json.loads(vj) if isinstance(vj, str) else (vj or [])
            result.append(row)
        return result
