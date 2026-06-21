"""小说导出 — TXT / EPUB 格式。

用法:
    exporter = NovelExporter(kernel)
    path = await exporter.export(project_id, format="epub")
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from core.logging_config import get_logger

logger = get_logger(__name__)


class NovelExporter:
    """小说导出器。"""

    def __init__(self, kernel: Any, output_dir: str | Path = "exports") -> None:
        self._kernel = kernel
        self._output_dir = Path(output_dir)
        self._output_dir.mkdir(parents=True, exist_ok=True)

    async def export(self, project_id: str, fmt: str = "txt") -> Path:
        """导出小说。

        Args:
            project_id: 项目 ID。
            fmt: 格式 — "txt" 或 "epub"。

        Returns:
            导出文件路径。
        """
        # 收集章节
        chapters = await self._collect_chapters(project_id)
        meta = await self._get_meta(project_id)

        title = meta.get("title", project_id)
        author = meta.get("author", "AI-Assisted")

        if fmt == "epub":
            return await self._export_epub(project_id, title, author, chapters)
        else:
            return await self._export_txt(project_id, title, author, chapters)

    async def export_markdown(self, project_id: str) -> Path:
        """导出为单个 Markdown 文件（方便导入其他工具）。"""
        chapters = await self._collect_chapters(project_id)
        meta = await self._get_meta(project_id)
        title = meta.get("title", project_id)

        lines = [f"# {title}", "", f"作者: {meta.get('author', 'AI-Assisted')}", ""]
        for ch in chapters:
            lines.append(f"## 第{ch['num']}章 {ch.get('title', '')}")
            lines.append("")
            lines.append(ch["content"])
            lines.append("")

        path = self._output_dir / f"{project_id}.md"
        path.write_text("\n".join(lines), encoding="utf-8")
        return path

    # ------------------------------------------------------------------
    # TXT
    # ------------------------------------------------------------------

    async def _export_txt(self, pid: str, title: str, author: str, chapters: list[dict]) -> Path:
        """导出 TXT（网文平台通用格式）。"""
        lines = [f"《{title}》", f"作者: {author}", "", f"共 {len(chapters)} 章", "=" * 50, ""]

        for ch in chapters:
            ch_title = f"第{ch['num']}章 {ch.get('title', '')}"
            lines.append(ch_title)
            lines.append("-" * 30)
            lines.append(ch["content"].strip())
            lines.extend(["", "", ""])

        path = self._output_dir / f"{pid}.txt"
        path.write_text("\n".join(lines), encoding="utf-8")
        word_count = sum(len(ch["content"]) for ch in chapters)
        logger.info("TXT 导出完成", project=pid, chapters=len(chapters), words=word_count, path=str(path))
        return path

    # ------------------------------------------------------------------
    # EPUB
    # ------------------------------------------------------------------

    async def _export_epub(self, pid: str, title: str, author: str, chapters: list[dict]) -> Path:
        """导出 EPUB 电子书。

        使用简单的 XHTML + ZIP 方式生成，不依赖外部库。
        """
        import zipfile

        uid = uuid.uuid4().urn
        now = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
        path = self._output_dir / f"{pid}.epub"

        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
            # mimetype (必须第一个，不压缩)
            zf.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)

            # container.xml
            zf.writestr("META-INF/container.xml", """<?xml version="1.0"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>""")

            # content.opf
            manifest_items = []
            spine_items = []
            for i, ch in enumerate(chapters):
                fid = f"ch{i+1:04d}"
                manifest_items.append(f'    <item id="{fid}" href="{fid}.xhtml" media-type="application/xhtml+xml"/>')
                spine_items.append(f'    <itemref idref="{fid}"/>')

            opf = f"""<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" unique-identifier="book-id" version="3.0">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="book-id">{uid}</dc:identifier>
    <dc:title>{self._xml_escape(title)}</dc:title>
    <dc:creator>{self._xml_escape(author)}</dc:creator>
    <dc:language>zh-CN</dc:language>
    <meta property="dcterms:modified">{now}</meta>
  </metadata>
  <manifest>
    <item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>
{chr(10).join(manifest_items)}
  </manifest>
  <spine toc="ncx">
{chr(10).join(spine_items)}
  </spine>
</package>"""
            zf.writestr("OEBPS/content.opf", opf)

            # toc.ncx
            nav_points = []
            for i, ch in enumerate(chapters):
                fid = f"ch{i+1:04d}"
                ch_title = f"第{ch['num']}章 {ch.get('title', '')}"
                nav_points.append(
                    f'    <navPoint id="nav-{fid}" playOrder="{i+1}">'
                    f'<navLabel><text>{self._xml_escape(ch_title)}</text></navLabel>'
                    f'<content src="{fid}.xhtml"/></navPoint>'
                )

            ncx = f"""<?xml version="1.0" encoding="UTF-8"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">
  <head><meta name="dtb:uid" content="{uid}"/></head>
  <docTitle><text>{self._xml_escape(title)}</text></docTitle>
  <navMap>
{chr(10).join(nav_points)}
  </navMap>
</ncx>"""
            zf.writestr("OEBPS/toc.ncx", ncx)

            # 每章的 XHTML
            for i, ch in enumerate(chapters):
                fid = f"ch{i+1:04d}"
                ch_title = f"第{ch['num']}章 {ch.get('title', '')}"
                body = ch["content"].replace("\n\n", "</p>\n<p>").replace("\n", "<br/>\n")
                xhtml = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml">
<head><title>{self._xml_escape(ch_title)}</title></head>
<body>
<h2>{self._xml_escape(ch_title)}</h2>
<p>{body}</p>
</body>
</html>"""
                zf.writestr(f"OEBPS/{fid}.xhtml", xhtml)

        word_count = sum(len(ch["content"]) for ch in chapters)
        logger.info("EPUB 导出完成", project=pid, chapters=len(chapters), words=word_count, path=str(path))
        return path

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _collect_chapters(self, project_id: str) -> list[dict]:
        """收集项目所有章节。"""
        kernel = self._kernel
        chapters = []

        # 从 progress 获取所有卷和章节信息
        progress = await kernel.context().get(f"project:{project_id}", "progress", {})
        for vol in progress.get("volumes", []):
            vol_num = vol.get("volume_number", 1)
            for ch in vol.get("chapters", []):
                ch_num = ch.get("chapter_number", 0)
                if not ch_num:
                    continue
                chapter_id = f"ch_v{vol_num:02d}_{ch_num:04d}"
                try:
                    content = await kernel.read_project_file(project_id, f"chapters/{chapter_id}.md")
                    title = ch.get("title", "")
                    chapters.append({"num": ch_num, "volume": vol_num, "title": title, "content": content, "id": chapter_id})
                except FileNotFoundError:
                    pass

        return chapters

    async def _get_meta(self, project_id: str) -> dict[str, Any]:
        """获取项目元数据。"""
        try:
            raw = await self._kernel.read_project_file(project_id, "project.json")
            return json.loads(raw)
        except Exception:
            return {}

    @staticmethod
    def _xml_escape(text: str) -> str:
        return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
