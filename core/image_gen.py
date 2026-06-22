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
    # Mock (无 API Key 时) — 精美 SVG 封面
    # ------------------------------------------------------------------

    def _mock_generate(self, prompt: str, size: str, filename: str) -> dict[str, Any]:
        """生成高质量 SVG 封面 — 渐变+粒子+光效+装饰元素。"""
        import re, random, math
        title_match = re.search(r"titled '([^']+)'", prompt)
        title = title_match.group(1) if title_match else "未命名"
        author_match = re.search(r"by '([^']+)'", prompt)
        author = author_match.group(1) if author_match else "AI-Assisted"

        # 从 prompt 提取一句话梗概
        one_liner = ""
        concept_match = re.search(r"Concept: ([^,]+)", prompt)
        if concept_match:
            one_liner = concept_match.group(1).strip()[:40]

        # 类型配色方案（bg1, bg2, accent, particle, glow）
        THEMES = {
            "玄幻": {"bg1":"#0a0118","bg2":"#1a0a3e","accent":"#ffd700","particle":"#ff9f43","glow":"#ffd700","deco":"sword"},
            "修仙": {"bg1":"#020c1b","bg2":"#0a1929","accent":"#00d4ff","particle":"#64ffda","glow":"#00d4ff","deco":"cloud"},
            "都市": {"bg1":"#0a0a1a","bg2":"#1a1a3e","accent":"#e94560","particle":"#ff6b6b","glow":"#e94560","deco":"building"},
            "甜宠": {"bg1":"#1a0a2e","bg2":"#2d1b69","accent":"#f72585","particle":"#ffd6e0","glow":"#f72585","deco":"heart"},
            "悬疑": {"bg1":"#050505","bg2":"#0d0d0d","accent":"#c0392b","particle":"#e74c3c","glow":"#c0392b","deco":"eye"},
            "系统": {"bg1":"#020208","bg2":"#0a0a2e","accent":"#00ff88","particle":"#00ff88","glow":"#00ff88","deco":"circuit"},
        }
        genre = "玄幻"
        for g in THEMES:
            if g in prompt:
                genre = g
                break
        t = THEMES.get(genre, THEMES["玄幻"])

        # 标题排版
        title_lines = [title]
        if len(title) > 6:
            mid = len(title) // 2
            for j in range(max(0, mid-2), min(len(title), mid+3)):
                if title[j] in "之·的·在与":
                    mid = j + 1; break
            title_lines = [title[:mid], title[mid:]]
        fs = 52 if len(title_lines) == 1 else 44
        y_title = 360

        # 生成随机粒子
        random.seed(hash(title))
        particles = ""
        for _ in range(30):
            px, py = random.randint(30, 570), random.randint(30, 870)
            pr = random.uniform(0.5, 2.5)
            po = random.uniform(0.1, 0.5)
            particles += f'<circle cx="{px}" cy="{py}" r="{pr}" fill="{t["particle"]}" opacity="{po}"/>\n'

        # 生成装饰光斑
        glows = ""
        for _ in range(5):
            gx, gy = random.randint(100, 500), random.randint(150, 700)
            gr = random.randint(40, 100)
            go = random.uniform(0.03, 0.08)
            glows += f'<circle cx="{gx}" cy="{gy}" r="{gr}" fill="{t["glow"]}" opacity="{go}"/>\n'

        # 装饰几何线条
        geo_lines = ""
        for i in range(6):
            x1, y1 = random.randint(0, 600), random.randint(0, 900)
            x2, y2 = x1 + random.randint(-150, 150), y1 + random.randint(-150, 150)
            geo_lines += f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{t["accent"]}" stroke-width="0.3" opacity="0.1"/>\n'

        # 标题文字
        title_svg = ""
        for i, tl in enumerate(title_lines):
            y = y_title + i * (fs + 12)
            title_svg += f'''<text x="300" y="{y}" text-anchor="middle" fill="{t["accent"]}" font-family="SimSun,'Noto Serif SC',serif" font-size="{fs}" font-weight="bold" letter-spacing="6" filter="url(#titleGlow)">{tl}</text>\n'''

        # 副标题/梗概
        subtitle_svg = ""
        if one_liner:
            subtitle_svg = f'<text x="300" y="{y_title + len(title_lines)*(fs+12) + 20}" text-anchor="middle" fill="rgba(255,255,255,0.5)" font-family="sans-serif" font-size="13" letter-spacing="2">{one_liner}</text>'

        # 装饰符号（根据类型）
        deco_svg = {
            "sword": f'<line x1="260" y1="{y_title-30}" x2="340" y2="{y_title-30}" stroke="{t["accent"]}" stroke-width="2" opacity="0.6"/><circle cx="300" cy="{y_title-30}" r="3" fill="{t["accent"]}" opacity="0.8"/>',
            "cloud": f'<path d="M250,{y_title-25} Q300,{y_title-45} 350,{y_title-25}" stroke="{t["accent"]}" stroke-width="1.5" fill="none" opacity="0.5"/>',
            "building": f'<rect x="280" y="{y_title-40}" width="8" height="20" fill="{t["accent"]}" opacity="0.3"/><rect x="295" y="{y_title-50}" width="10" height="30" fill="{t["accent"]}" opacity="0.3"/><rect x="312" y="{y_title-35}" width="8" height="15" fill="{t["accent"]}" opacity="0.3"/>',
            "heart": f'<path d="M300,{y_title-15} C290,{y_title-35} 270,{y_title-35} 270,{y_title-20} C270,{y_title-5} 300,{y_title+5} 300,{y_title+5} C300,{y_title+5} 330,{y_title-5} 330,{y_title-20} C330,{y_title-35} 310,{y_title-35} 300,{y_title-15}Z" fill="none" stroke="{t["accent"]}" stroke-width="1.5" opacity="0.4"/>',
            "eye": f'<ellipse cx="300" cy="{y_title-25}" rx="25" ry="12" fill="none" stroke="{t["accent"]}" stroke-width="1.5" opacity="0.4"/><circle cx="300" cy="{y_title-25}" r="5" fill="{t["accent"]}" opacity="0.3"/>',
            "circuit": f'<circle cx="300" cy="{y_title-25}" r="8" fill="none" stroke="{t["accent"]}" stroke-width="1" opacity="0.4"/><line x1="292" y1="{y_title-25}" x2="270" y2="{y_title-25}" stroke="{t["accent"]}" stroke-width="0.8" opacity="0.3"/><line x1="308" y1="{y_title-25}" x2="330" y2="{y_title-25}" stroke="{t["accent"]}" stroke-width="0.8" opacity="0.3"/>',
        }.get(genre, "")

        svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="600" height="900" viewBox="0 0 600 900">
  <defs>
    <linearGradient id="bg" x1="0%" y1="0%" x2="30%" y2="100%">
      <stop offset="0%" style="stop-color:{t['bg1']}"/>
      <stop offset="50%" style="stop-color:{t['bg2']}"/>
      <stop offset="100%" style="stop-color:{t['bg1']}"/>
    </linearGradient>
    <radialGradient id="spotlight" cx="50%" cy="40%" r="50%">
      <stop offset="0%" style="stop-color:{t['accent']};stop-opacity:0.08"/>
      <stop offset="100%" style="stop-color:{t['accent']};stop-opacity:0"/>
    </radialGradient>
    <filter id="titleGlow">
      <feGaussianBlur stdDeviation="4" result="blur"/>
      <feMerge><feMergeNode in="blur"/><feMergeNode in="SourceGraphic"/></feMerge>
    </filter>
    <filter id="softGlow">
      <feGaussianBlur stdDeviation="8"/>
    </filter>
  </defs>

  <!-- 背景 -->
  <rect width="600" height="900" fill="url(#bg)"/>
  <rect width="600" height="900" fill="url(#spotlight)"/>

  <!-- 装饰光斑 -->
  {glows}

  <!-- 几何线条 -->
  {geo_lines}

  <!-- 粒子 -->
  {particles}

  <!-- 边框 -->
  <rect x="30" y="30" width="540" height="840" rx="4" fill="none" stroke="{t['accent']}" stroke-width="0.5" opacity="0.2"/>
  <rect x="35" y="35" width="530" height="830" rx="3" fill="none" stroke="{t['accent']}" stroke-width="0.3" opacity="0.1"/>

  <!-- 顶部装饰线 -->
  <line x1="120" y1="100" x2="480" y2="100" stroke="{t['accent']}" stroke-width="0.5" opacity="0.3"/>
  <line x1="200" y1="105" x2="400" y2="105" stroke="{t['accent']}" stroke-width="0.3" opacity="0.2"/>

  <!-- 类型标签 -->
  <text x="300" y="135" text-anchor="middle" fill="{t['accent']}" font-family="sans-serif" font-size="11" letter-spacing="10" opacity="0.6">{genre}</text>

  <!-- 装饰符号 -->
  {deco_svg}

  <!-- 书名 -->
  {title_svg}

  <!-- 副标题 -->
  {subtitle_svg}

  <!-- 分隔线 -->
  <line x1="200" y1="{y_title + len(title_lines)*(fs+12) + 50}" x2="400" y2="{y_title + len(title_lines)*(fs+12) + 50}" stroke="{t['accent']}" stroke-width="0.5" opacity="0.3"/>

  <!-- 作者 -->
  <text x="300" y="{y_title + len(title_lines)*(fs+12) + 85}" text-anchor="middle" fill="rgba(255,255,255,0.6)" font-family="sans-serif" font-size="16" letter-spacing="4">{author}</text>

  <!-- 底部装饰 -->
  <line x1="100" y1="810" x2="500" y2="810" stroke="{t['accent']}" stroke-width="0.3" opacity="0.15"/>
  <text x="300" y="835" text-anchor="middle" fill="rgba(255,255,255,0.12)" font-family="sans-serif" font-size="9" letter-spacing="6">AI NOVEL STUDIO</text>
</svg>"""

        path = self._output_dir / f"{filename}.svg"
        path.write_text(svg, encoding="utf-8")
        logger.info("生成封面", title=title, genre=genre, path=str(path))
        return {"path": str(path), "prompt": prompt, "url": "", "size": size, "mock": False if self._api_key else True, "note": "" if self._api_key else "设置 IMAGE_API_KEY 使用 AI 生成更精美的封面"}
