"""AI 图像生成 — 封面 + 插画。

支持 OpenAI DALL-E 兼容 API（通义万相、智谱CogView、Kimi等均兼容）。
"""

from __future__ import annotations

import base64
import time
import uuid
from pathlib import Path
from typing import Any

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from core.logging_config import get_logger

logger = get_logger(__name__)

RETRYABLE = (httpx.ConnectError, httpx.RemoteProtocolError, httpx.ReadTimeout)

# =============================================================================
# 封面 Prompt 模板
# =============================================================================

COVER_STYLES = {
    "fanqie": {
        "玄幻": "仙侠玄幻封面，金色光芒，古风人物剪影，史诗感，大气磅礴",
        "都市": "现代都市背景，霓虹灯光，人物背影，赛博朋克色调，电影质感",
        "甜宠": "浪漫粉色系，唯美古风或现代背景，男女主侧影，温暖光晕",
        "悬疑": "暗黑色调，迷雾笼罩，关键道具特写，紧张氛围",
        "系统": "科幻数据流，虚拟界面元素，能量光效，未来感",
    },
    "qidian": {
        "玄幻": "东方玄幻封面，主角持剑或施法姿态，天地异象，金红配色",
        "都市": "都市异能风，主角剪影+城市天际线，蓝紫冷调",
    },
}

# AI 作图专用 Prompt 强化词
QUALITY_BOOST = "masterpiece, best quality, highly detailed, professional illustration, book cover design, --no text, --no letters, --no watermark"


class ImageGenerator:
    """AI 图像生成器——OpenAI DALL-E 兼容 API。

    支持所有兼容 /v1/images/generations 端点的服务：
    - OpenAI DALL-E 3
    - 通义万相 (via DashScope)
    - 智谱 CogView
    - 硅基流动 Flux
    - 任何 OpenAI 兼容图片 API
    """

    def __init__(
        self,
        base_url: str = "",
        api_key: str = "",
        default_size: str = "1024x1024",
        output_dir: str | Path = "novel_output/.images",
    ) -> None:
        self._base_url = base_url.rstrip("/") if base_url else "https://api.openai.com"
        self._api_key = api_key
        self._default_size = default_size
        self._output_dir = Path(output_dir)
        self._output_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # 封面生成
    # ------------------------------------------------------------------

    async def generate_cover(
        self,
        title: str,
        *,
        genre: str = "玄幻",
        platform: str = "fanqie",
        one_liner: str = "",
        style_hint: str = "",
        size: str = "",
    ) -> dict[str, Any]:
        """根据小说信息生成封面。

        Returns:
            {"path": str, "prompt": str, "url": str (if API returns URL), "size": str}
        """
        prompt = self._build_cover_prompt(title, genre, platform, one_liner, style_hint)
        return await self._generate(prompt, size or self._default_size, f"cover_{uuid.uuid4().hex[:8]}")

    async def generate_illustration(
        self,
        scene_description: str,
        *,
        style: str = "fantasy illustration",
        size: str = "",
    ) -> dict[str, Any]:
        """根据场景描述生成插画。

        Args:
            scene_description: 场景描述（中文）。
            style: 画风描述。
            size: 图片尺寸。

        Returns:
            {"path": str, "prompt": str, "url": str}
        """
        prompt = f"{scene_description}\nStyle: {style}\n{QUALITY_BOOST}"
        return await self._generate(prompt, size or "1024x1024", f"illu_{uuid.uuid4().hex[:8]}")

    # ------------------------------------------------------------------
    # Prompt 构建
    # ------------------------------------------------------------------

    def _build_cover_prompt(self, title: str, genre: str, platform: str, one_liner: str, style_hint: str) -> str:
        """构建封面绘图 Prompt。"""
        parts = [f"Book cover for a Chinese web novel titled '{title}' by '{author}'"]

        # 类型风格
        platform_styles = COVER_STYLES.get(platform, COVER_STYLES["fanqie"])
        genre_style = platform_styles.get(genre, platform_styles.get("玄幻", "ancient Chinese fantasy, epic atmosphere"))

        # 查找最匹配的类型
        best_match = "玄幻"
        for g in platform_styles:
            if g in genre or genre in g:
                best_match = g
                break
        style_desc = platform_styles.get(best_match, genre_style)
        parts.append(f"Style: {style_desc}")

        if one_liner:
            parts.append(f"Concept: {one_liner[:100]}")

        if style_hint:
            parts.append(f"Additional: {style_hint}")

        parts.append(QUALITY_BOOST)
        parts.append("Vertical/portrait orientation, 2:3 aspect ratio")

        return ", ".join(parts)

    # ------------------------------------------------------------------
    # API 调用
    # ------------------------------------------------------------------

    async def _generate(self, prompt: str, size: str, filename: str) -> dict[str, Any]:
        """调用图片生成 API。"""
        if not self._api_key:
            return self._mock_generate(prompt, size, filename)

        start = time.time()
        try:
            response = await self._call_api(prompt, size)
            elapsed = time.time() - start

            result = {
                "prompt": prompt,
                "size": size,
                "path": "",
                "url": "",
                "latency_ms": round(elapsed * 1000),
            }

            # 解析响应——可能是 URL 或 base64
            data = response.json()
            images = data.get("data", [])
            if images:
                img_data = images[0]
                if "url" in img_data:
                    # 下载 URL
                    result["url"] = img_data["url"]
                    result["path"] = str(await self._download(img_data["url"], filename))
                elif "b64_json" in img_data:
                    result["path"] = str(self._save_base64(img_data["b64_json"], filename))

            logger.info("图片生成完成", filename=filename, latency=result["latency_ms"])
            return result

        except Exception as e:
            logger.warning("图片 API 调用失败", error=str(e))
            return self._mock_generate(prompt, size, filename)

    @retry(retry=retry_if_exception_type(RETRYABLE), wait=wait_exponential(min=2, max=30), stop=stop_after_attempt(2), reraise=True)
    async def _call_api(self, prompt: str, size: str) -> httpx.Response:
        async with httpx.AsyncClient(timeout=httpx.Timeout(120)) as client:
            resp = await client.post(
                f"{self._base_url}/v1/images/generations",
                headers={"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"},
                json={"model": "dall-e-3", "prompt": prompt, "n": 1, "size": size, "quality": "standard"},
            )
            resp.raise_for_status()
            return resp

    async def _download(self, url: str, filename: str) -> Path:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            path = self._output_dir / f"{filename}.png"
            path.write_bytes(resp.content)
            return path

    @staticmethod
    def _save_base64(b64_str: str, filename: str) -> Path:
        import base64 as b64
        path = Path("novel_output/.images") / f"{filename}.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b64.b64decode(b64_str))
        return path

    # ------------------------------------------------------------------
    # Mock (无 API Key 时)
    # ------------------------------------------------------------------

    def _mock_generate(self, prompt: str, size: str, filename: str) -> dict[str, Any]:
        """生成包含书名和作者的 SVG 封面。"""
        import re
        title_match = re.search(r"titled '([^']+)'", prompt)
        title = title_match.group(1) if title_match else "未命名"
        author_match = re.search(r"by '([^']+)'", prompt)
        author = author_match.group(1) if author_match else "AI-Assisted"

        # 根据类型选配色方案
        genre_colors = {
            "玄幻": ("#1a0a2e", "#4a1942", "#ffd700"),   # 深紫+金
            "修仙": ("#0a1628", "#1a3a5c", "#00d4ff"),   # 深蓝+青
            "都市": ("#1a1a2e", "#16213e", "#e94560"),   # 暗蓝+红
            "甜宠": ("#2d1b69", "#f72585", "#ffd6e0"),   # 紫+粉
            "悬疑": ("#0d0d0d", "#1a1a1a", "#c0392b"),   # 黑+暗红
            "系统": ("#0f0f23", "#1e3a5f", "#00ff88"),   # 深色+绿
        }

        # 从 prompt 中提取类型
        genre = "玄幻"
        for g in genre_colors:
            if g in prompt:
                genre = g
                break
        bg1, bg2, accent = genre_colors.get(genre, genre_colors["玄幻"])

        # 处理长标题——超过8字换行
        title_lines = [title]
        if len(title) > 8:
            mid = len(title) // 2
            # 找到中间附近的标点或空格
            for j in range(mid - 2, mid + 3):
                if j < len(title) and title[j] in "之·的·在":
                    mid = j + 1
                    break
            title_lines = [title[:mid], title[mid:]]

        title_ts = ""
        y_start = 340
        for i, tl in enumerate(title_lines):
            title_ts += f'<text x="300" y="{y_start + i * 55}" text-anchor="middle" fill="{accent}" font-family="SimSun, serif" font-size="{48 if len(title_lines)==1 else 42}" font-weight="bold" letter-spacing="4">{tl}</text>\n'

        svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="600" height="900" viewBox="0 0 600 900">
  <defs>
    <linearGradient id="bg" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" style="stop-color:{bg1}"/>
      <stop offset="50%" style="stop-color:{bg2}"/>
      <stop offset="100%" style="stop-color:{bg1}"/>
    </linearGradient>
    <filter id="glow">
      <feGaussianBlur stdDeviation="3" result="blur"/>
      <feMerge><feMergeNode in="blur"/><feMergeNode in="SourceGraphic"/></feMerge>
    </filter>
  </defs>
  <!-- 背景 -->
  <rect width="600" height="900" fill="url(#bg)"/>
  <!-- 装饰边框 -->
  <rect x="40" y="40" width="520" height="820" rx="8" fill="none" stroke="{accent}" stroke-width="1" opacity="0.3"/>
  <rect x="50" y="50" width="500" height="800" rx="6" fill="none" stroke="{accent}" stroke-width="1" opacity="0.15"/>
  <!-- 顶部装饰线 -->
  <line x1="100" y1="120" x2="500" y2="120" stroke="{accent}" stroke-width="1" opacity="0.4"/>
  <!-- 类型标签 -->
  <text x="300" y="150" text-anchor="middle" fill="{accent}" font-family="sans-serif" font-size="14" letter-spacing="8" opacity="0.7">{genre} · 小说</text>
  <!-- 书名 -->
  <g filter="url(#glow)">
{title_ts}  </g>
  <!-- 装饰分隔线 -->
  <line x1="180" y1="{y_start + len(title_lines) * 55 + 30}" x2="420" y2="{y_start + len(title_lines) * 55 + 30}" stroke="{accent}" stroke-width="1" opacity="0.5"/>
  <!-- 作者 -->
  <text x="300" y="{y_start + len(title_lines) * 55 + 80}" text-anchor="middle" fill="rgba(255,255,255,0.7)" font-family="sans-serif" font-size="18" letter-spacing="3">{author} · 著</text>
  <!-- 底部装饰 -->
  <text x="300" y="820" text-anchor="middle" fill="rgba(255,255,255,0.2)" font-family="sans-serif" font-size="10" letter-spacing="4">AI NOVEL APP</text>
  <line x1="80" y1="800" x2="520" y2="800" stroke="{accent}" stroke-width="1" opacity="0.2"/>
</svg>"""

        path = self._output_dir / f"{filename}.svg"
        path.write_text(svg, encoding="utf-8")
        logger.info("生成封面", title=title, genre=genre, path=str(path))
        return {"path": str(path), "prompt": prompt, "url": "", "size": size, "mock": False if self._api_key else True, "note": "" if self._api_key else "设置 IMAGE_API_KEY 使用 AI 生成更精美的封面"}
