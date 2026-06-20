"""知识包市场 — 打包/安装/卸载/共享写作知识。

包格式: 目录或 .zip，包含:
    pack.yaml     — 元数据 (name, version, description, author, tags, dependencies)
    content/       — 知识内容文件 (.md / .yaml / .json)

安装后自动索引到 RAG。

用法:
    market = KnowledgePackMarket(kernel)
    packs = await market.list_local()        # 列出已安装
    await market.install("path/to/pack.zip") # 安装包
    await market.uninstall("pack-name")      # 卸载
    path = await market.create("my-pack")    # 创建新包
"""

from __future__ import annotations

import json
import shutil
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from core.logging_config import get_logger

logger = get_logger(__name__)

PACK_SCHEMA = {
    "name": "", "version": "0.1.0", "type": "knowledge_pack",
    "title": "", "description": "", "author": "",
    "tags": [], "platforms": [], "genres": [],
    "created_at": "", "updated_at": "",
    "dependencies": [],
}


class KnowledgePackMarket:
    """知识包市场——管理本地包目录 + RAG 索引。"""

    def __init__(self, kernel: Any, packs_dir: str | Path = "knowledge_base/packs") -> None:
        self._kernel = kernel
        self._packs_dir = Path(packs_dir)
        self._packs_dir.mkdir(parents=True, exist_ok=True)

    # =========================================================================
    # 列表
    # =========================================================================

    async def list_local(self) -> list[dict]:
        """列出本地已安装的知识包。"""
        packs = []
        for d in self._packs_dir.iterdir():
            if d.is_dir() and (d / "pack.yaml").exists():
                try:
                    meta = yaml.safe_load((d / "pack.yaml").read_text(encoding="utf-8"))
                    meta["installed"] = True
                    meta["path"] = str(d)
                    # 统计内容文件
                    content_dir = d / "content"
                    meta["file_count"] = len(list(content_dir.rglob("*"))) if content_dir.exists() else 0
                    packs.append(meta)
                except Exception:
                    pass
        return packs

    async def list_catalog(self) -> list[dict]:
        """列出内置示例知识包目录。"""
        builtin = self._packs_dir / "_catalog.yaml"
        if builtin.exists():
            data = yaml.safe_load(builtin.read_text(encoding="utf-8"))
            return data.get("packs", [])
        return self._builtin_catalog()

    # =========================================================================
    # 安装 / 卸载
    # =========================================================================

    async def install(self, source: str) -> dict[str, Any]:
        """安装知识包。

        Args:
            source: 包路径 (.zip 或目录) 或内置包名。

        Returns:
            {"name": str, "installed": bool, "files_indexed": int}
        """
        # 检查是否是内置包名
        catalog = await self.list_catalog()
        for entry in catalog:
            if entry.get("name") == source:
                return await self._install_builtin(entry)

        # 文件安装
        src_path = Path(source)
        if not src_path.exists():
            return {"name": source, "installed": False, "error": "包不存在"}

        return await self._install_from_path(src_path)

    async def uninstall(self, pack_name: str) -> dict[str, Any]:
        """卸载知识包。"""
        pack_dir = self._packs_dir / pack_name
        if not pack_dir.exists():
            return {"name": pack_name, "uninstalled": False, "error": "包未安装"}

        shutil.rmtree(pack_dir)
        logger.info("知识包已卸载", name=pack_name)
        return {"name": pack_name, "uninstalled": True}

    # =========================================================================
    # 创建
    # =========================================================================

    async def create(self, name: str, *, title: str = "", description: str = "", tags: list | None = None, files: dict[str, str] | None = None) -> Path:
        """创建新知识包。

        Args:
            name: 包名 (kebab-case)。
            title: 显示标题。
            description: 描述。
            tags: 标签列表。
            files: {filename: content} — 内容文件。

        Returns:
            包目录路径。
        """
        pack_dir = self._packs_dir / name
        pack_dir.mkdir(parents=True, exist_ok=True)

        # 写 pack.yaml
        meta = dict(PACK_SCHEMA)
        meta.update({
            "name": name, "version": "0.1.0",
            "title": title or name, "description": description,
            "tags": tags or [], "author": "AI Novel App",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        })
        (pack_dir / "pack.yaml").write_text(
            yaml.dump(meta, allow_unicode=True, default_flow_style=False), encoding="utf-8",
        )

        # 写内容文件
        content_dir = pack_dir / "content"
        content_dir.mkdir(exist_ok=True)
        if files:
            for fname, fcontent in files.items():
                (content_dir / fname).write_text(fcontent, encoding="utf-8")

        # 索引到 RAG
        count = await self._index_pack(pack_dir, meta)

        logger.info("知识包已创建", name=name, files=len(files or {}), indexed=count)
        return pack_dir

    async def export_zip(self, name: str) -> Path | None:
        """导出知识包为 .zip 文件。"""
        pack_dir = self._packs_dir / name
        if not pack_dir.exists():
            return None

        zip_path = self._packs_dir / f"{name}.zip"
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for f in pack_dir.rglob("*"):
                if f.is_file():
                    zf.write(f, f.relative_to(pack_dir))
        return zip_path

    # =========================================================================
    # Internal
    # =========================================================================

    async def _install_from_path(self, src_path: Path) -> dict:
        """从路径安装包。"""
        pack_name = src_path.stem if src_path.suffix == ".zip" else src_path.name

        # 解压 zip
        if src_path.suffix == ".zip":
            extract_dir = self._packs_dir / pack_name
            extract_dir.mkdir(exist_ok=True)
            with zipfile.ZipFile(src_path, "r") as zf:
                zf.extractall(extract_dir)
            src_path = extract_dir

        # 复制目录
        elif src_path.is_dir():
            dest = self._packs_dir / pack_name
            if src_path != dest:
                if dest.exists():
                    shutil.rmtree(dest)
                shutil.copytree(src_path, dest)

        # 读取元数据
        pack_dir = self._packs_dir / pack_name
        meta = {}
        yaml_path = pack_dir / "pack.yaml"
        if yaml_path.exists():
            meta = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))

        # 索引到 RAG
        count = await self._index_pack(pack_dir, meta)

        return {"name": meta.get("name", pack_name), "installed": True, "files_indexed": count}

    async def _install_builtin(self, entry: dict) -> dict:
        """安装内置示例包——从 knowledge_base 数据生成。"""
        name = entry.get("name", "")
        title = entry.get("title", name)
        description = entry.get("description", "")

        sources = entry.get("sources", {})
        files: dict[str, str] = {}

        # 从 knowledge_base 读取预置内容
        for src_path, dest_name in sources.items():
            src_file = Path(src_path)
            if src_file.exists():
                files[dest_name] = src_file.read_text(encoding="utf-8")

        if not files:
            return {"name": name, "installed": False, "error": "无可用内容"}

        await self.create(name, title=title, description=description, tags=entry.get("tags", []), files=files)
        return {"name": name, "installed": True, "files_indexed": len(files)}

    async def _index_pack(self, pack_dir: Path, meta: dict) -> int:
        """将知识包内容索引到 RAG。"""
        content_dir = pack_dir / "content"
        if not content_dir.exists():
            return 0

        try:
            from models.rag import DocumentCategory, RAGDocument
            from rag.store import VectorStore
        except ImportError:
            return 0

        docs = []
        for f in content_dir.rglob("*"):
            if not f.is_file():
                continue
            try:
                text = f.read_text(encoding="utf-8")
                if not text.strip():
                    continue

                ext = f.suffix.lower()
                if ext == ".yaml":
                    cat = DocumentCategory.ANTI_AI_PATTERN if "pattern" in f.name else DocumentCategory.WRITING_TIP
                elif ext == ".md":
                    cat = DocumentCategory.WRITING_TIP
                else:
                    cat = DocumentCategory.WRITING_TIP

                docs.append(RAGDocument(
                    doc_id=f"pack_{meta.get('name', '')}_{f.name}_{uuid.uuid4().hex[:6]}",
                    project_id=None,
                    category=cat,
                    content=text,
                    metadata={"source": f"pack:{meta.get('name', '')}", "pack_name": meta.get("name", ""), "file": f.name},
                ))
            except Exception:
                continue

        if docs:
            try:
                # 尝试写入向量存储
                kernel = self._kernel
                if kernel and hasattr(kernel, '_retrieval_engine') and kernel._retrieval_engine:
                    store = kernel._retrieval_engine._store
                    if store:
                        await store.index_documents(docs)
                        return len(docs)
            except Exception:
                pass

        return 0

    def _builtin_catalog(self) -> list[dict]:
        """内置示例知识包目录。"""
        return [
            {
                "name": "fanqie-writing-tips",
                "title": "番茄小说写作技巧包",
                "description": "番茄小说平台爆款公式、黄金三章、章中钩子、书名技巧、赛道分析",
                "tags": ["写作技巧", "番茄", "新手"],
                "platforms": ["fanqie"],
                "genres": ["通用"],
                "updated": "2026-06-19",
                "sources": {
                    "knowledge_base/writing_tips/fanqie_tips.md": "fanqie_tips.md",
                    "knowledge_base/writing_tips/common_tips.md": "common_tips.md",

                },
            },
            {
                "name": "anti-ai-patterns",
                "updated": "2026-06-19",
                "title": "反AI模式特征库",
                "description": "10类AI写作模式特征，用于检测和消除文本中的AI痕迹",
                "tags": ["反AI", "质量", "检测"],
                "platforms": ["通用"],
                "genres": ["通用"],
                "sources": {
                    "knowledge_base/anti_ai_patterns/patterns.yaml": "patterns.yaml",
                },
            },
            {
                "name": "hot-genres-2026",
                "updated": "2026-06-19",
                "title": "2026热门赛道分析",
                "description": "番茄/起点/晋江最新热门赛道数据与竞争分析",
                "tags": ["赛道", "市场", "选题"],
                "platforms": ["fanqie", "qidian", "jinjiang"],
                "genres": ["通用"],
                "sources": {
                    "knowledge_base/genre_data/hot_genres.yaml": "hot_genres.yaml",
                },
            },
            {
                "name": "platform-rules",
                "updated": "2026-06-19",
                "title": "各平台规则合集",
                "description": "番茄/起点/晋江/七猫/豆瓣的投稿规范、算法机制、全勤规则",
                "tags": ["平台", "规则", "投稿"],
                "platforms": ["通用"],
                "genres": ["通用"],
                "sources": {
                    "knowledge_base/platform_rules/platforms.md": "platforms.md",
                },
            },        {
                "name": "internet-memes",
                "updated": "2026-06-19",
                "title": "网文热梗素材库",
                "description": "谐音梗、网络热梗、流行语——适时融入增加趣味性和读者代入感",
                "tags": ["热梗", "谐音梗", "流行语", "趣味"],
                "platforms": ["通用"],
                "genres": ["都市", "轻松", "搞笑"],
                "sources": {
                    "knowledge_base/writing_tips/internet_memes.md": "internet_memes.md",
                },
            },
        {
            "name": "southwest-dialect",
            "updated": "2026-06-19",
            "title": "云贵川渝方言素材库",
            "description": "四川/重庆/云南/贵州方言——高频口语、角色塑造、适当融入增强地域感",
            "tags": ["方言", "川渝", "云南", "贵州", "角色塑造"],
            "platforms": ["通用"],
            "genres": ["都市", "轻松", "搞笑"],
            "sources": {"knowledge_base/writing_tips/southwest_dialect.md": "southwest_dialect.md"},
        },
        ]
