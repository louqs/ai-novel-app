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
            ]:
                await db.execute(sql)

            # 迁移：确保 volume_number 列存在
            cursor = await db.execute("PRAGMA table_info(chapters)")
            columns = [row[1] for row in await cursor.fetchall()]
            if "volume_number" not in columns:
                await db.execute("ALTER TABLE chapters ADD COLUMN volume_number INTEGER DEFAULT 1")
                logger.info("数据库迁移: chapters 表添加 volume_number 列")

            await db.execute("CREATE INDEX IF NOT EXISTS idx_ch ON chapters(project_id, chapter_number)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_char ON characters(project_id)")
            await db.commit()
        logger.info("SQLite 已连接", path=str(self._path))

    async def close(self) -> None:
        pass

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

    async def save_chapter(self, cid: str, pid: str, num: int, title: str, content: str, score: float = 0, volume: int = 1) -> None:
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

    async def get_settings(self, pid: str) -> dict:
        rows = await self._fetch("SELECT * FROM settings WHERE project_id=?", (pid,))
        if rows:
            d = rows[0].get("data_json", "{}")
            return json.loads(d) if isinstance(d, str) else (d or {})
        return {}
