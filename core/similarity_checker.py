"""跨章相似度检测器 — 基于字符/词 shingle 的 Jaccard/Containment 计算."""

from __future__ import annotations

import re
from typing import Any

# 中日韩统一表意文字范围
CJK_RE = re.compile(r'[一-鿿]')
# 提取英文单词/数字
TOKEN_RE = re.compile(r'[A-Za-z0-9]+')


def _is_shingle_char(ch: str) -> bool:
    """判断字符是否参与 shingle（CJK + ASCII 字母数字）."""
    if not ch:
        return False
    code = ord(ch)
    return (0x4e00 <= code <= 0x9fff) or (48 <= code <= 57) or (65 <= code <= 90) or (97 <= code <= 122)


def _get_char_shingles(text: str, n: int = 5) -> set[str]:
    """按字符构建 n-gram shingle 集合（流式，避免大文本爆内存）."""
    if n <= 0 or not text:
        return set()
    shingles = set()
    window: list[str] = []
    for ch in text:
        if not _is_shingle_char(ch):
            continue
        window.append(ch)
        if len(window) > n:
            window.pop(0)
        if len(window) == n:
            shingles.add("".join(window))
    return shingles


def _get_token_shingles(text: str, n: int = 5) -> set[str]:
    """按词构建 n-gram shingle 集合（用于非中文文本）."""
    if n <= 0 or not text:
        return set()
    tokens = [m.group().lower() for m in TOKEN_RE.finditer(text)]
    shingles = set()
    for i in range(len(tokens) - n + 1):
        shingles.add(" ".join(tokens[i : i + n]))
    return shingles


def _jaccard(a: set, b: set) -> float:
    """Jaccard 相似度: |A ∩ B| / |A ∪ B|."""
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union > 0 else 0.0


def _containment(a: set, b: set) -> float:
    """Containment: A 中有多少比例被 B 包含 |A ∩ B| / |A|."""
    if not a:
        return 1.0
    return len(a & b) / len(a)


def compute_similarity(text_a: str, text_b: str, n: int = 5) -> dict[str, float]:
    """计算两个文本的相似度指标（自动判断用字符还是词 shingle）.

    Returns:
        {"jaccard": float, "containment_a_in_b": float, "containment_b_in_a": float, "containment_max": float}
    """
    # 判断是否为中文文本
    cjk_count_a = len(CJK_RE.findall(text_a))
    cjk_count_b = len(CJK_RE.findall(text_b))
    use_char = (cjk_count_a > 0) or (cjk_count_b > 0)

    if use_char:
        sa = _get_char_shingles(text_a, n)
        sb = _get_char_shingles(text_b, n)
    else:
        sa = _get_token_shingles(text_a, n)
        sb = _get_token_shingles(text_b, n)

    jac = _jaccard(sa, sb)
    cab = _containment(sa, sb)
    cba = _containment(sb, sa)
    cmax = max(cab, cba)

    return {
        "jaccard": round(jac, 4),
        "containment_a_in_b": round(cab, 4),
        "containment_b_in_a": round(cba, 4),
        "containment_max": round(cmax, 4),
    }


def check_cross_chapter_similarity(
    current_content: str,
    project_id: str,
    current_chapter_num: int,
    db: Any,
    *,
    lookback_n: int = 5,
    threshold: float = 0.25,
    shingle_n: int = 5,
) -> list[dict]:
    """检查当前章节与前 N 章的相似度，返回超阈值的段落级证据.

    Args:
        current_content: 当前章节正文
        project_id: 项目 ID
        current_chapter_num: 当前章节号
        db: 数据库管理器（用于读取前 N 章内容）
        lookback_n: 回溯章节数（默认检查前 5 章）
        threshold: containment_max 阈值（超过此值判定为高相似）
        shingle_n: shingle 窗口大小

    Returns:
        超阈值的段落证据列表 [{paragraph_index, message, similar_chapter, score}]
    """
    if not db or not project_id or current_chapter_num <= 1:
        return []

    # 取前 N 章内容（同卷）
    import asyncio
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    prev_chapters: list[tuple[int, str]] = []
    for i in range(max(1, current_chapter_num - lookback_n), current_chapter_num):
        try:
            if asyncio.iscoroutinefunction(db.get_chapter):
                ch = loop.run_until_complete(db.get_chapter(project_id, i, volume=1))
            else:
                ch = db.get_chapter(project_id, i, volume=1)
            if ch and ch.get("content"):
                prev_chapters.append((i, ch["content"]))
        except Exception:
            continue

    if not prev_chapters:
        return []

    # 当前章节按段落分
    paragraphs = current_content.split("\n\n")
    issues: list[dict] = []

    for idx, para in enumerate(paragraphs):
        para_clean = para.strip()
        if len(para_clean) < 50:  # 太短的段落（单句对话）不检测，噪声大
            continue

        # 与每个前章计算相似度（段落 vs 整章，用 containment 看段落被章节包含的比例）
        for prev_num, prev_content in prev_chapters:
            sim = compute_similarity(para_clean, prev_content, shingle_n)
            # containment_a_in_b: 当前段落有多少被前章包含（高则说明段落是前章的复读）
            if sim["containment_a_in_b"] > threshold:
                issues.append({
                    "paragraph_index": idx,
                    "message": f"与第 {prev_num} 章高度相似（{sim['containment_a_in_b']:.0%}）",
                    "similar_chapter": prev_num,
                    "score": sim["containment_a_in_b"],
                })
                break  # 找到一个就够了，不必遍历所有前章

    return issues
