"""封面艺术家插件 — 生成小说封面和关键场景插画。

用法:
    artist = CoverArtistPlugin()
    result = await artist.generate_cover(project_id)
    result = await artist.generate_scene_illustration("主角在月下挥剑", style="仙侠")
"""

from __future__ import annotations

import os
from typing import Any

from core.image_gen import ImageGenerator
from core.logging_config import get_logger
from core.plugin_manager import PluginManifest

logger = get_logger(__name__)


class CoverArtistPlugin:
    """封面/插画生成插件."""

    name = "cover-artist"
    version = "0.1.0"

    def __init__(self) -> None:
        self._kernel = None
        self._generator: ImageGenerator | None = None

    async def on_load(self, kernel) -> None:
        self._kernel = kernel
        # 读取图片 API 配置
        api_key = os.getenv("IMAGE_API_KEY", os.getenv("OPENAI_API_KEY", os.getenv("DEEPSEEK_API_KEY", "")))
        base_url = os.getenv("IMAGE_API_BASE", "")

        # 尝试从 providers 配置中找支持图片的 provider
        if not base_url:
            providers = kernel.get_config("providers", [])
            for p in providers:
                if p.get("type") == "openai_compatible" and p.get("name") != "ollama":
                    base_url = p.get("base_url", "")
                    break

        self._generator = ImageGenerator(base_url=base_url, api_key=api_key)
        logger.info("封面艺术家已加载", has_api_key=bool(api_key))

    async def on_unload(self) -> None:
        self._generator = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def generate_cover(self, project_id: str, *, style_hint: str = "") -> dict[str, Any]:
        """根据项目信息生成封面。

        Args:
            project_id: 项目 ID。
            style_hint: 额外的风格提示。

        Returns:
            {"path": str, "prompt": str, "url": str}
        """
        kernel = self._kernel

        # 获取项目元数据
        try:
            import json
            meta_raw = await kernel.read_project_file(project_id, "project.json")
            meta = json.loads(meta_raw)
        except Exception:
            meta = {"title": project_id, "genre_tags": ["玄幻"]}

        title = meta.get("title", project_id)
        author = meta.get("author", "AI-Assisted")
        genre = (meta.get("genre_tags", ["玄幻"]) or ["玄幻"])[0]
        platform = meta.get("platform", "fanqie")
        one_liner = meta.get("one_liner", "")

        # 获取人物名作为 prompt 参考
        chars = await kernel.context().get(f"project:{project_id}", "characters", {})
        char_names = []
        for c in (chars.get("characters", {}) if isinstance(chars, dict) else {}).values():
            if isinstance(c, dict) and c.get("name"):
                char_names.append(c["name"])

        if char_names and not style_hint:
            style_hint = f"Main characters: {', '.join(char_names[:3])}"

        result = await self._generator.generate_cover(
            title=title, genre=genre, platform=platform,
            one_liner=one_liner, style_hint=style_hint,
        )
        # Inject author into result
        if result.get("path") and author:
            result["author"] = author

        # 保存封面路径到项目
        if result.get("path"):
            await kernel.context().set(f"project:{project_id}", "cover_image", result["path"])

        return result

    async def generate_illustration(
        self,
        project_id: str,
        scene: str,
        *,
        chapter_num: int = 0,
        style: str = "",
    ) -> dict[str, Any]:
        """根据场景描述生成插画。

        Args:
            project_id: 项目 ID。
            scene: 场景描述。
            chapter_num: 关联章节号。
            style: 画风 (fantasy/modern/romance/thriller)。

        Returns:
            {"path": str, "prompt": str}
        """
        # 读取章节风格
        platform = await self._kernel.context().get(f"project:{project_id}", "platform", "fanqie")

        style_map = {"fanqie": "chinese web novel illustration, dramatic lighting",
                     "qidian": "epic fantasy illustration, detailed",
                     "jinjiang": "romantic illustration, soft colors, elegant"}
        if not style:
            style = style_map.get(platform, "chinese fantasy illustration")

        if chapter_num > 0:
            # 从章节中提取关键场景
            try:
                content = await self._kernel.read_project_file(project_id, f"chapters/ch_{chapter_num:04d}.md")
                scene = await self._extract_key_scene(content, scene)
            except Exception:
                pass

        return await self._generator.generate_illustration(scene, style=style)

    async def generate_cover_variants(self, project_id: str, count: int = 3) -> list[dict[str, Any]]:
        """生成多版封面，供用户选择。

        Returns:
            [{"path": str, "prompt": str}, ...]
        """
        variants = []
        hints = ["", "minimalist style, clean composition", "dramatic lighting, cinematic angle"]
        for i in range(min(count, 3)):
            result = await self.generate_cover(project_id, style_hint=hints[i])
            variants.append(result)
        return variants

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _extract_key_scene(self, content: str, user_hint: str) -> str:
        """从章节中提取关键场景描述（LLM 辅助）。"""
        if not self._kernel or len(content) < 100:
            return user_hint

        prompt = f"""从以下小说章节中，提取一个最适合做插画的关键场景。用1-2句话描述画面（中文，50字以内）。

章节:
{content[:1500]}

只返回场景描述，不要其他内容。"""

        try:
            result = await self._kernel.call_llm(
                messages=[{"role": "user", "content": prompt}],
                tier="budget",
                max_tokens=200,
                temperature=0.3,
            )
            scene = result.get("content", "").strip()[:100]
            return scene or user_hint
        except Exception:
            return user_hint


def create_manifest() -> PluginManifest:
    return PluginManifest(
        name="cover-artist",
        version="0.1.0",
        description="封面/插画生成 — 根据小说信息自动生成封面和场景插画",
        dependencies=[],
        hooks=["on_load", "on_unload"],
    )


def create_plugin() -> CoverArtistPlugin:
    return CoverArtistPlugin()
