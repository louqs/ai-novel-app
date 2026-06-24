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

    # API Key 加密相关
    _ENCRYPT_PREFIX = "enc:"

    def __init__(self, db_path: str = "data/novel.db") -> None:
        self._path = Path(db_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._encryption_key = self._derive_key()
        self._init_wal_mode()

    def _init_wal_mode(self) -> None:
        """初始化 SQLite WAL 模式，提高并发性能."""
        import sqlite3
        try:
            conn = sqlite3.connect(str(self._path))
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA busy_timeout=5000")
            conn.close()
            logger.debug("SQLite WAL 模式已启用", path=str(self._path))
        except Exception as e:
            logger.warning("SQLite WAL 模式启用失败", error=str(e))

    @staticmethod
    def _derive_key() -> bytes:
        """从机器特征派生加密密钥."""
        import hashlib
        import platform
        # 使用机器名 + 用户名作为密钥材料
        seed = f"{platform.node()}-{platform.machine()}-novel-app-salt"
        return hashlib.sha256(seed.encode()).digest()

    def _encrypt(self, plaintext: str) -> str:
        """加密 API Key."""
        if not plaintext:
            return plaintext
        # 如果已经加密过，直接返回
        if plaintext.startswith(self._ENCRYPT_PREFIX):
            return plaintext
        import base64
        key = self._encryption_key
        # XOR 加密
        encrypted = bytes(b ^ key[i % len(key)] for i, b in enumerate(plaintext.encode('utf-8')))
        return self._ENCRYPT_PREFIX + base64.urlsafe_b64encode(encrypted).decode('ascii')

    def _decrypt(self, ciphertext: str) -> str:
        """解密 API Key."""
        if not ciphertext or not ciphertext.startswith(self._ENCRYPT_PREFIX):
            return ciphertext
        import base64
        key = self._encryption_key
        try:
            encrypted = base64.urlsafe_b64decode(ciphertext[len(self._ENCRYPT_PREFIX):])
            # XOR 解密
            decrypted = bytes(b ^ key[i % len(key)] for i, b in enumerate(encrypted))
            return decrypted.decode('utf-8')
        except Exception:
            return ciphertext  # 解密失败返回原值

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
                """CREATE TABLE IF NOT EXISTS gate_results (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_id TEXT NOT NULL,
                    chapter_number INTEGER NOT NULL,
                    volume_number INTEGER DEFAULT 1,
                    gate_name TEXT NOT NULL,
                    verdict TEXT DEFAULT 'PASS',
                    score REAL DEFAULT 1.0,
                    issues_json TEXT DEFAULT '[]',
                    created_at TEXT NOT NULL
                )""",
                """CREATE TABLE IF NOT EXISTS contributor_results (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_id TEXT NOT NULL,
                    chapter_number INTEGER NOT NULL,
                    volume_number INTEGER DEFAULT 1,
                    contributor_name TEXT NOT NULL,
                    summary TEXT DEFAULT '',
                    issues_json TEXT DEFAULT '[]',
                    suggestions_json TEXT DEFAULT '[]',
                    score REAL,
                    created_at TEXT NOT NULL
                )""",
                """CREATE TABLE IF NOT EXISTS optimization_results (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_id TEXT NOT NULL,
                    chapter_number INTEGER NOT NULL,
                    volume_number INTEGER DEFAULT 1,
                    original_content TEXT DEFAULT '',
                    optimized_content TEXT DEFAULT '',
                    explanation_json TEXT DEFAULT '{}',
                    created_at TEXT NOT NULL
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
            await db.execute("CREATE INDEX IF NOT EXISTS idx_gr ON gate_results(project_id, chapter_number, volume_number)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_cr ON contributor_results(project_id, chapter_number, volume_number)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_or ON optimization_results(project_id, chapter_number, volume_number)")
            await db.commit()

            # 迁移：加密现有的明文 API Key
            await self._migrate_encrypt_keys(db)

        logger.info("SQLite 已连接", path=str(self._path))

    async def _migrate_encrypt_keys(self, db) -> None:
        """迁移：将现有的明文 API Key 加密."""
        try:
            cursor = await db.execute("SELECT name, api_key FROM providers")
            rows = await cursor.fetchall()
            updated = 0
            for row in rows:
                name, api_key = row[0], row[1]
                if api_key and not api_key.startswith(self._ENCRYPT_PREFIX):
                    encrypted = self._encrypt(api_key)
                    await db.execute("UPDATE providers SET api_key=? WHERE name=?", (encrypted, name))
                    updated += 1
            if updated > 0:
                await db.commit()
                logger.info("API Key 加密迁移完成", count=updated)
        except Exception as e:
            logger.warning("API Key 加密迁移失败", error=str(e))

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

            # 添加项目内版本号列（每个项目独立递增）
            if "project_version_num" not in columns:
                await db.execute("ALTER TABLE outline_versions ADD COLUMN project_version_num INTEGER DEFAULT 0")
                # 为已有记录填充项目内版本号
                try:
                    projects = await db.execute("SELECT DISTINCT project_id FROM outline_versions")
                    pids = [r[0] for r in await projects.fetchall()]
                    for pid in pids:
                        rows = await db.execute("SELECT id FROM outline_versions WHERE project_id=? ORDER BY id ASC", (pid,))
                        for i, row in enumerate(await rows.fetchall(), 1):
                            await db.execute("UPDATE outline_versions SET project_version_num=? WHERE id=?", (i, row[0]))
                except Exception:
                    pass

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

            # 添加项目内版本号列（每个项目独立递增）
            if "project_version_num" not in columns:
                await db.execute("ALTER TABLE chapter_versions ADD COLUMN project_version_num INTEGER DEFAULT 0")
                # 为已有记录填充项目内版本号（按章节分组）
                try:
                    keys = await db.execute("SELECT DISTINCT project_id, chapter_number, volume_number FROM chapter_versions")
                    for pid, chn, voln in await keys.fetchall():
                        rows = await db.execute("SELECT id FROM chapter_versions WHERE project_id=? AND chapter_number=? AND volume_number=? ORDER BY id ASC", (pid, chn, voln))
                        for i, row in enumerate(await rows.fetchall(), 1):
                            await db.execute("UPDATE chapter_versions SET project_version_num=? WHERE id=?", (i, row[0]))
                except Exception:
                    pass

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

    async def _exec_many(self, statements: list[tuple[str, tuple]]) -> None:
        """在同一事务中执行多条 SQL 语句，避免 DELETE+INSERT 之间的瞬态缺失窗口."""
        import aiosqlite
        async with aiosqlite.connect(str(self._path)) as db:
            for sql, params in statements:
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
        await self._exec_many([
            ("DELETE FROM optimization_results WHERE project_id=?", (pid,)),
            ("DELETE FROM contributor_results WHERE project_id=?", (pid,)),
            ("DELETE FROM gate_results WHERE project_id=?", (pid,)),
            ("DELETE FROM chapter_versions WHERE project_id=?", (pid,)),
            ("DELETE FROM chapters WHERE project_id=?", (pid,)),
            ("DELETE FROM outline_versions WHERE project_id=?", (pid,)),
            ("DELETE FROM outline_jobs WHERE project_id=?", (pid,)),
            ("DELETE FROM characters WHERE project_id=?", (pid,)),
            ("DELETE FROM settings WHERE project_id=?", (pid,)),
            ("DELETE FROM projects WHERE id=?", (pid,)),
        ])

    # ---- Gate Results ----

    async def save_gate_results(self, pid: str, ch_num: int, vol_num: int, gate_issues: list[dict]) -> None:
        """保存门禁检查结果（先删旧的再插新的）."""
        import json
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        await self._exec("DELETE FROM gate_results WHERE project_id=? AND chapter_number=? AND volume_number=?",
                         (pid, ch_num, vol_num))
        for g in gate_issues:
            await self._exec(
                "INSERT INTO gate_results (project_id,chapter_number,volume_number,gate_name,verdict,score,issues_json,created_at) VALUES (?,?,?,?,?,?,?,?)",
                (pid, ch_num, vol_num, g.get("gate", ""), g.get("verdict", "PASS"),
                 g.get("score", 1.0), json.dumps(g.get("issues", []), ensure_ascii=False), now))

    async def get_gate_results(self, pid: str, ch_num: int, vol_num: int = 1) -> list[dict]:
        """获取章节的门禁检查结果."""
        import json
        rows = await self._fetch(
            "SELECT gate_name, verdict, score, issues_json FROM gate_results WHERE project_id=? AND chapter_number=? AND volume_number=?",
            (pid, ch_num, vol_num))
        results = []
        for r in rows:
            issues = []
            try:
                issues = json.loads(r["issues_json"])
            except Exception:
                pass
            results.append({"gate": r["gate_name"], "verdict": r["verdict"], "score": r["score"], "issues": issues})
        return results

    async def save_contributor_results(self, pid: str, ch_num: int, vol_num: int, results: list[dict]) -> None:
        """保存流水线贡献者分析结果."""
        import json
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        await self._exec("DELETE FROM contributor_results WHERE project_id=? AND chapter_number=? AND volume_number=?",
                         (pid, ch_num, vol_num))
        for r in results:
            await self._exec(
                "INSERT INTO contributor_results (project_id,chapter_number,volume_number,contributor_name,summary,issues_json,suggestions_json,score,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (pid, ch_num, vol_num, r.get("name", ""), r.get("summary", ""),
                 json.dumps(r.get("issues", []), ensure_ascii=False),
                 json.dumps(r.get("suggestions", []), ensure_ascii=False),
                 r.get("score"), now))

    async def get_contributor_results(self, pid: str, ch_num: int, vol_num: int = 1) -> list[dict]:
        """获取章节的贡献者分析结果."""
        import json
        rows = await self._fetch(
            "SELECT contributor_name, summary, issues_json, suggestions_json, score FROM contributor_results WHERE project_id=? AND chapter_number=? AND volume_number=?",
            (pid, ch_num, vol_num))
        results = []
        for r in rows:
            issues = json.loads(r["issues_json"]) if r["issues_json"] else []
            suggestions = json.loads(r["suggestions_json"]) if r["suggestions_json"] else []
            results.append({"name": r["contributor_name"], "summary": r["summary"],
                            "issues": issues, "suggestions": suggestions, "score": r["score"]})
        return results

    async def save_optimization_result(self, pid: str, ch_num: int, vol_num: int,
                                        original: str, optimized: str, explanation: dict | None = None) -> None:
        """保存编辑优化结果（覆盖同一章节的旧优化结果）."""
        import json
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        await self._exec("DELETE FROM optimization_results WHERE project_id=? AND chapter_number=? AND volume_number=?",
                         (pid, ch_num, vol_num))
        await self._exec(
            "INSERT INTO optimization_results (project_id,chapter_number,volume_number,original_content,optimized_content,explanation_json,created_at) VALUES (?,?,?,?,?,?,?)",
            (pid, ch_num, vol_num, original, optimized, json.dumps(explanation or {}, ensure_ascii=False), now))

    async def get_optimization_result(self, pid: str, ch_num: int, vol_num: int = 1) -> dict | None:
        """获取最近一次编辑优化结果."""
        import json
        rows = await self._fetch(
            "SELECT original_content, optimized_content, explanation_json, created_at FROM optimization_results WHERE project_id=? AND chapter_number=? AND volume_number=? ORDER BY created_at DESC LIMIT 1",
            (pid, ch_num, vol_num))
        if not rows:
            return None
        r = rows[0]
        explanation = {}
        try:
            explanation = json.loads(r["explanation_json"])
        except Exception:
            pass
        return {"original": r["original_content"], "optimized": r["optimized_content"],
                "explanation": explanation, "created_at": r["created_at"]}

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
        await self._exec_many([
            ("DELETE FROM chapters WHERE id=?", (cid,)),
            ("INSERT INTO chapters (id,project_id,chapter_number,volume_number,title,content,word_count,ai_score,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
             (cid, pid, num, volume, title, content, len(content), score, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))),
        ])

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
        # 加密 API Key
        encrypted_key = self._encrypt(api_key)
        await self._exec("INSERT OR REPLACE INTO providers (name,type,base_url,api_key,default_model,models,enabled) VALUES (?,?,?,?,?,?,1)",
                         (name, ptype, base_url, encrypted_key, default_model, json.dumps(models or [])))

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
            # 解密 API Key
            if r.get("api_key"):
                r["api_key"] = self._decrypt(r["api_key"])
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
    ) -> int | None:
        """保存大纲 — 自动快照当前版本后再覆盖，返回新版本ID."""
        version_id = None
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

        # 保存新版本到历史
        try:
            from core.version_manager import VersionManager
            vm = VersionManager(self)
            version_id = await vm.snapshot_outline(
                pid, progress,
                source=snapshot_source,
                change_summary=snapshot_summary or f"保存({snapshot_source})",
            )
        except Exception:
            pass  # 快照失败不影响保存

        settings = await self.get_settings(pid) or {}
        settings["progress"] = progress
        await self.save_settings(pid, settings)
        return version_id

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
                # 计算章节内版本号
                row = await db.execute(
                    "SELECT COALESCE(MAX(project_version_num), 0) FROM chapter_versions WHERE project_id=? AND chapter_number=? AND volume_number=?",
                    (project_id, chapter_number, volume_number),
                )
                pvn = (await row.fetchone())[0] + 1

                cursor = await db.execute(
                    "INSERT INTO chapter_versions (project_id,chapter_id,chapter_number,volume_number,title,content,word_count,ai_score,source,change_summary,created_at,project_version_num) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (project_id, chapter_id, chapter_number, volume_number, title, content, word_count, ai_score, source, change_summary, now, pvn),
                )
                await db.commit()
                return cursor.lastrowid or 0
        except Exception:
            await self._ensure_version_tables()
            async with aiosqlite.connect(str(self._path)) as db:
                row = await db.execute(
                    "SELECT COALESCE(MAX(project_version_num), 0) FROM chapter_versions WHERE project_id=? AND chapter_number=? AND volume_number=?",
                    (project_id, chapter_number, volume_number),
                )
                pvn = (await row.fetchone())[0] + 1

                cursor = await db.execute(
                    "INSERT INTO chapter_versions (project_id,chapter_id,chapter_number,volume_number,title,content,word_count,ai_score,source,change_summary,created_at,project_version_num) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (project_id, chapter_id, chapter_number, volume_number, title, content, word_count, ai_score, source, change_summary, now, pvn),
                )
                await db.commit()
                return cursor.lastrowid or 0

    async def list_chapter_versions(self, project_id: str, chapter_number: int, volume_number: int = 1) -> list[dict]:
        """列出某章节的所有历史版本（不含完整content，节省带宽）."""
        cols = "id,project_id,chapter_id,chapter_number,volume_number,title,word_count,ai_score,source,change_summary,created_at,COALESCE(project_version_num,0) as project_version_num"
        try:
            return await self._fetch(
                f"SELECT {cols} FROM chapter_versions WHERE project_id=? AND chapter_number=? AND volume_number=? ORDER BY id DESC",
                (project_id, chapter_number, volume_number),
            )
        except Exception:
            await self._ensure_version_tables()
            return await self._fetch(
                f"SELECT {cols} FROM chapter_versions WHERE project_id=? AND chapter_number=? AND volume_number=? ORDER BY id DESC",
                (project_id, chapter_number, volume_number),
            )

    async def get_chapter_version(self, version_id: int) -> dict | None:
        """获取特定版本的完整内容."""
        cols = "id,project_id,chapter_id,chapter_number,volume_number,title,content,word_count,ai_score,source,change_summary,created_at,COALESCE(project_version_num,0) as project_version_num"
        try:
            rows = await self._fetch(f"SELECT {cols} FROM chapter_versions WHERE id=?", (version_id,))
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
                # 计算项目内版本号
                row = await db.execute("SELECT COALESCE(MAX(project_version_num), 0) FROM outline_versions WHERE project_id=?", (project_id,))
                max_num = (await row.fetchone())[0]
                pvn = max_num + 1

                cursor = await db.execute(
                    "INSERT INTO outline_versions (project_id,outline_data,volumes_count,chapters_count,source,change_summary,created_at,project_version_num) VALUES (?,?,?,?,?,?,?,?)",
                    (project_id, outline_data, volumes_count, chapters_count, source, change_summary, now, pvn),
                )
                await db.commit()
                vid = cursor.lastrowid or 0
                logger.info("保存大纲版本成功", version_id=vid, project_id=project_id, source=source, project_version_num=pvn)
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
                # 计算项目内版本号
                row = await db.execute("SELECT COALESCE(MAX(project_version_num), 0) FROM outline_versions WHERE project_id=?", (project_id,))
                max_num = (await row.fetchone())[0]
                pvn = max_num + 1

                if "data_json" in cols:
                    # 旧表：同时填充 data_json 和 outline_data
                    cursor = await db.execute(
                        "INSERT INTO outline_versions (project_id,outline_data,data_json,volumes_count,chapters_count,source,change_summary,created_at,project_version_num) VALUES (?,?,?,?,?,?,?,?,?)",
                        (project_id, outline_data, outline_data, volumes_count, chapters_count, source, change_summary, now, pvn),
                    )
                else:
                    cursor = await db.execute(
                        "INSERT INTO outline_versions (project_id,outline_data,volumes_count,chapters_count,source,change_summary,created_at,project_version_num) VALUES (?,?,?,?,?,?,?,?)",
                        (project_id, outline_data, volumes_count, chapters_count, source, change_summary, now, pvn),
                    )
                await db.commit()
                vid = cursor.lastrowid or 0
                logger.info("保存大纲版本成功（重试）", version_id=vid, project_id=project_id, source=source)
                return vid

    async def list_outline_versions(self, project_id: str) -> list[dict]:
        """列出项目大纲的所有历史版本（不含完整数据）."""
        cols = "id,project_id,volumes_count,chapters_count,source,change_summary,created_at,COALESCE(project_version_num,0) as project_version_num"
        try:
            return await self._fetch(
                f"SELECT {cols} FROM outline_versions WHERE project_id=? ORDER BY id DESC",
                (project_id,),
            )
        except Exception:
            # 表可能不存在，尝试创建后重试
            await self._ensure_outline_tables()
            return await self._fetch(
                f"SELECT {cols} FROM outline_versions WHERE project_id=? ORDER BY id DESC",
                (project_id,),
            )

    async def get_outline_version(self, version_id: int) -> dict | None:
        """获取特定版本的大纲数据."""
        cols = "id,project_id,outline_data,volumes_count,chapters_count,source,change_summary,created_at,COALESCE(project_version_num,0)"
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
