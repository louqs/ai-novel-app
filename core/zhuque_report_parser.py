"""朱雀 AIGC 检测报告解析器 — 纯正则，零 LLM 依赖.

朱雀「打印为PDF」报告结构高度规整，正则即可确定性解析，
不调用任何大模型 —— 项目主 API 无论换成 DeepSeek/Claude/其他都不受影响。

报告结构：
- 汇总表：`序号 片段 占全文比例 占字符数 AIGC值`（每行一个片段）
- 片段正文：以 `NO.X 片段X AIGC值 Y.YYYY` 为分隔，后跟该片段原文
- 页眉页脚：`日期 朱雀 AI生成检测报告单 _xxx` / `https://matrix.tencent.com/... N/13`

AIGC 值语义（朱雀官方）：0-0.5 人工特征，0.5-0.99 疑似AI，0.99-1 AI特征。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# 页眉页脚噪声（每页重复）
_NOISE_PATTERNS = [
    re.compile(r"\d{4}/\d+/\d+\s+\d+:\d+\s+朱雀.*?报告单[^\n]*"),
    re.compile(r"https://matrix\.tencent\.com/\S+\s+\d+/\d+"),
]
# 汇总表行：序号 片段N 占比% 字符数 AIGC值
_TABLE_ROW = re.compile(r"^(\d+)\s+片段\d+\s+([\d.]+)%\s+(\d+)\s+([\d.]+)\s*$", re.M)
# 片段正文分隔标记：NO.X 片段X AIGC值 Y.YYYY
_SEG_MARKER = re.compile(r"NO\.\s*(\d+)\s+片段(\d+)\s+AIGC值\s+([\d.]+)")


@dataclass
class ZhuqueSegment:
    """一个检测片段."""

    index: int
    aigc: float           # AIGC 值 0-1，越高越像 AI
    ratio: float = 0.0    # 占全文比例 %
    chars: int = 0        # 字符数
    text: str = ""        # 片段原文

    @property
    def is_ai(self) -> bool:
        """AIGC ≥ 0.5 视为疑似/确定 AI."""
        return self.aigc >= 0.5

    def to_dict(self) -> dict:
        return {
            "index": self.index, "aigc": round(self.aigc, 4),
            "ratio": self.ratio, "chars": self.chars,
            "is_ai": self.is_ai, "text": self.text,
        }


@dataclass
class ZhuqueReport:
    """朱雀检测报告解析结果."""

    overall_ai_rate: float = 0.0          # 加权整体 AI 率 0-1
    segments: list[ZhuqueSegment] = field(default_factory=list)
    detect_time: str = ""
    parse_ok: bool = False                # 是否成功解析出有效数据

    def to_dict(self) -> dict:
        return {
            "overall_ai_rate": round(self.overall_ai_rate, 4),
            "detect_time": self.detect_time,
            "parse_ok": self.parse_ok,
            "segment_count": len(self.segments),
            "ai_segment_count": sum(1 for s in self.segments if s.is_ai),
            "segments": [s.to_dict() for s in self.segments],
        }


def strip_noise(text: str) -> str:
    """去掉页眉页脚噪声."""
    for pat in _NOISE_PATTERNS:
        text = pat.sub("", text)
    return text


def parse_report_text(raw: str) -> ZhuqueReport:
    """从朱雀报告纯文本解析出结构化结果（纯正则，不调 LLM）.

    Args:
        raw: pypdf 等抽取出的报告全文.

    Returns:
        ZhuqueReport；解析不到任何片段时 parse_ok=False（调用方可回退）。
    """
    report = ZhuqueReport()
    if not raw or not raw.strip():
        return report

    # 检测时间
    m = re.search(r"检测时间[：:]\s*([\d/]+\s+[\d:]+)", raw)
    if m:
        report.detect_time = m.group(1).strip()

    text = strip_noise(raw)

    # 1. 汇总表 → {片段序号: (ratio, chars, aigc)}
    table: dict[int, tuple[float, int, float]] = {}
    for no, ratio, chars, aigc in _TABLE_ROW.findall(text):
        table[int(no)] = (float(ratio), int(chars), float(aigc))

    # 2. 按片段标记切分正文
    markers = list(_SEG_MARKER.finditer(text))
    segments: list[ZhuqueSegment] = []
    for i, mk in enumerate(markers):
        idx = int(mk.group(2))
        aigc = float(mk.group(3))
        start = mk.end()
        end = markers[i + 1].start() if i + 1 < len(markers) else len(text)
        body = text[start:end].strip()
        ratio, chars, t_aigc = table.get(idx, (0.0, 0, aigc))
        segments.append(ZhuqueSegment(
            index=idx, aigc=aigc or t_aigc, ratio=ratio, chars=chars, text=body,
        ))

    # 3. 没有片段标记时，退而求其次仅用汇总表（拿不到正文，但仍有整体率）
    if not segments and table:
        for idx, (ratio, chars, aigc) in sorted(table.items()):
            segments.append(ZhuqueSegment(index=idx, aigc=aigc, ratio=ratio, chars=chars))

    report.segments = segments

    # 4. 整体 AI 率：优先按字符占比加权，否则取片段均值
    if segments and any(s.ratio for s in segments):
        report.overall_ai_rate = sum(s.aigc * s.ratio / 100 for s in segments)
    elif segments:
        report.overall_ai_rate = sum(s.aigc for s in segments) / len(segments)

    report.parse_ok = bool(segments)
    return report


def extract_pdf_text(pdf_bytes: bytes) -> str:
    """从 PDF 字节抽取全文（pypdf，纯本地）.

    Raises:
        RuntimeError: pypdf 未安装或解析失败.
    """
    try:
        from pypdf import PdfReader
    except ImportError as e:
        raise RuntimeError("需要 pypdf 库：pip install pypdf") from e

    import io
    try:
        reader = PdfReader(io.BytesIO(pdf_bytes))
        return "\n".join(page.extract_text() or "" for page in reader.pages)
    except Exception as e:
        raise RuntimeError(f"PDF 解析失败: {e}") from e


def parse_report_pdf(pdf_bytes: bytes) -> ZhuqueReport:
    """PDF 字节 → 抽文本 → 解析（一步到位）."""
    return parse_report_text(extract_pdf_text(pdf_bytes))
