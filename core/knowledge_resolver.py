"""知识库约定式自动发现器（KnowledgeResolver）.

按目录与命名约定发现并加载知识库文档，供「正文生成 / 大纲生成 / 编辑优化」
三个接入点共用。核心原则：**约定优于配置** —— 加载器只认约定槽位，不认写死的文件名，
未来新增体裁/技能只要符合约定即被自动吸收，无需改代码。

约定槽位
--------
体裁目录 `knowledge_base/genre_skills/{题材}/`：
- 靶值槽      `靶值.md`                       → G 层量化阈值
- 边界槽      `题材边界与创作说明*.md`          → 体裁红线（也兼容 .github/instructions 下）
- 阶段提示槽  `.github/prompts/{阶段}.prompt.md` → 各阶段题材规则

通用技能目录 `knowledge_base/writing_skills/{技能}/`：
- 分层技能槽  `SKILL.md` 的 `§A 红线` / `§C 靶值` 节 → 只抽红线+靶值，不抽技法库

加载优先级（高覆盖低）
--------------------
1. 项目级 Agents.md 显式指定（若存在）
2. 体裁靶值槽  靶值.md
3. 体裁边界槽  题材边界与创作说明
4. 通用分层技能槽  SKILL.md 的 §A/§C
5. 代码内置默认回退值
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from core.logging_config import get_logger

logger = get_logger(__name__)

# 知识库根（相对工作目录，与 orchestrator 现有写法一致）
_GENRE_ROOT = Path("knowledge_base/genre_skills")
_SKILL_ROOT = Path("knowledge_base/writing_skills")

# 旧标签别名 → 目录名（仅作回退兼容；新增体裁请直接建同名目录，无需在此登记）
_GENRE_ALIASES = {
    "都市": "都市职场", "职场": "都市职场",
    "悬疑": "悬疑推理", "推理": "悬疑推理",
    "科幻": "AI科幻", "AI": "AI科幻",
    "太空": "太空科幻",
    "赛博朋克": "赛博庞克", "赛博": "赛博庞克",
    "言情": "女频爱情", "女频": "女频爱情", "爱情": "女频爱情",
    "异能": "异能志怪", "志怪": "异能志怪", "灵异": "异能志怪",
}

def resolve_genre_dirs(genre_tags: list[str] | None) -> list[str]:
    """把项目体裁标签解析为存在的体裁目录名（目录名直配优先，别名回退）.

    新增体裁只要在 genre_skills/ 下建同名目录即可被直配命中，无需改代码。
    """
    if not genre_tags:
        return []
    dirs: list[str] = []
    seen: set[str] = set()
    for tag in genre_tags:
        if not tag:
            continue
        # 1) 目录名直配
        candidate = tag.strip()
        if not (_GENRE_ROOT / candidate).is_dir():
            # 2) 别名回退
            candidate = _GENRE_ALIASES.get(tag.strip(), "")
        if candidate and candidate not in seen and (_GENRE_ROOT / candidate).is_dir():
            seen.add(candidate)
            dirs.append(candidate)
    return dirs


def _read_text(path: Path) -> str:
    """安全读取文本，失败返回空串（沿用项目静默回退风格）."""
    try:
        if path.is_file():
            return path.read_text(encoding="utf-8").strip()
    except Exception:
        logger.debug("knowledge_resolver 读取失败", path=str(path))
    return ""


def _parse_target_table(text: str) -> dict[str, str]:
    """解析靶值.md 的『§一 靶值表』，返回 {参数key: 体裁靶值}.

    表格形如：| 参数 key | 体裁靶值 | 通用默认 | 依据 |
    取第 1 列为 key、第 2 列为体裁靶值；跳过表头与分隔行。
    key 与 value 去除 markdown 反引号与加粗标记。
    """
    targets: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) < 2:
            continue
        key = cells[0].strip("` *")
        val = cells[1].replace("`", "").replace("**", "").strip()
        # 跳过表头/分隔行
        if not key or key in ("参数 key", "参数key") or set(key) <= {"-", ":", " "}:
            continue
        if not val or set(val) <= {"-", ":", " "}:
            continue
        targets[key] = val
    return targets


def _extract_section(text: str, *keywords: str) -> str:
    """从 markdown 抽取标题含任一关键词的 `## ` 节正文（到下一个 `## ` 为止）."""
    lines = text.splitlines()
    out: list[str] = []
    capturing = False
    for line in lines:
        if line.startswith("## "):
            if capturing:
                break
            capturing = any(k in line for k in keywords)
            if capturing:
                continue
        elif capturing:
            out.append(line)
    return "\n".join(out).strip()


def genre_targets(genre_tags: list[str] | None) -> dict[str, str]:
    """扫各体裁『靶值槽』，合并返回 {参数key: 体裁靶值}（先命中的体裁优先）."""
    merged: dict[str, str] = {}
    for d in resolve_genre_dirs(genre_tags):
        text = _read_text(_GENRE_ROOT / d / "靶值.md")
        if not text:
            continue
        for k, v in _parse_target_table(text).items():
            merged.setdefault(k, v)
    return merged


def genre_boundaries(genre_tags: list[str] | None) -> list[str]:
    """扫各体裁『边界槽』，返回体裁红线文本块列表（靶值.md §二 + 题材边界文件）."""
    blocks: list[str] = []
    for d in resolve_genre_dirs(genre_tags):
        # 来源1：靶值.md 的「§二 体裁专属红线增量」
        tv = _read_text(_GENRE_ROOT / d / "靶值.md")
        sec = _extract_section(tv, "红线增量", "红线")
        if sec:
            blocks.append(f"【{d} 体裁红线】\n{sec}")
        # 来源2：题材边界与创作说明（根目录或 .github/instructions 下，glob 匹配）
        for base in (_GENRE_ROOT / d, _GENRE_ROOT / d / ".github" / "instructions"):
            if not base.is_dir():
                continue
            for f in sorted(base.glob("题材边界与创作说明*.md")):
                bt = _read_text(f)
                if bt:
                    blocks.append(f"【{d} 题材边界】\n{bt}")
    return blocks


def genre_stage_prompt(genre_tags: list[str] | None, stage: str) -> list[str]:
    """扫各体裁『阶段提示槽』 .github/prompts/{stage}.prompt.md，返回正文（去 YAML 头）."""
    out: list[str] = []
    for d in resolve_genre_dirs(genre_tags):
        f = _GENRE_ROOT / d / ".github" / "prompts" / f"{stage}.prompt.md"
        text = _read_text(f)
        if not text:
            continue
        if "---" in text[1:]:
            parts = text.split("---", 2)
            text = parts[2].strip() if len(parts) > 2 else text
        if text:
            out.append(text)
    return out


def skill_layers(skill_name: str) -> dict[str, str]:
    """抽通用技能 SKILL.md 的红线节(§A/硬规则)与靶值节(§C)，不含技法库.

    返回 {"redlines": <红线节文本>, "targets": <靶值节文本>}；缺则为空串。
    """
    text = _read_text(_SKILL_ROOT / skill_name / "SKILL.md")
    if not text:
        return {"redlines": "", "targets": ""}
    return {
        "redlines": _extract_section(text, "§A", "红线", "硬规则"),
        "targets": _extract_section(text, "§C", "体裁靶值"),
    }


def threshold(key: str, genre_tags: list[str] | None, default: Any = None) -> Any:
    """按加载优先级取单个量化阈值.

    优先级：体裁靶值槽 > 代码默认（default）。
    （项目级 Agents.md 覆盖由调用方在更外层处理；本函数聚焦体裁→默认两层。）
    命中体裁靶值时返回其原始字符串（如 "45%–65%"），未命中返回 default。
    """
    targets = genre_targets(genre_tags)
    return targets.get(key, default)


def parse_range(text: str | None) -> tuple[float, float] | None:
    """把靶值区间字符串解析为 (low, high) 数值对.

    支持全角/半角破折号与百分号，如 "45%–65%" / "30%-50%" / "2500–4500 CJK"。
    取文本中前两个数字为 low/high；不足两个数字返回 None。
    百分号不影响数值本身（"45%" → 45.0）。
    """
    if not text:
        return None
    nums = re.findall(r"\d+(?:\.\d+)?", text)
    if len(nums) < 2:
        return None
    try:
        lo, hi = float(nums[0]), float(nums[1])
    except ValueError:
        return None
    if lo > hi:
        lo, hi = hi, lo
    return (lo, hi)


def ratio_range(key: str, genre_tags: list[str] | None,
                default: tuple[float, float]) -> tuple[float, float]:
    """取体裁某区间型靶值的数值对；缺失或不可解析则回退 default."""
    raw = threshold(key, genre_tags, default=None)
    parsed = parse_range(raw) if isinstance(raw, str) else None
    return parsed if parsed else default


def overdue_gap(length: str | None, genre_tags: list[str] | None) -> int:
    """伏笔超期阈值（章）：篇幅定基线，体裁靶值可进一步收紧（取 min，不放松）.

    - 基线随篇幅：短篇 4 / 中篇 8 / 长篇 20 / 超长 30。
    - 体裁靶值 `伏笔超期阈值` 若给定且更小，则采用更小值（短篇悬疑不会被放松）。
    """
    from models.foreshadow import overdue_gap_for_length

    base = overdue_gap_for_length(length)
    raw = threshold("伏笔超期阈值", genre_tags, default=None)
    if isinstance(raw, str):
        import re as _re
        nums = _re.findall(r"\d+", raw)
        if nums:
            return min(base, int(nums[0]))
    return base


__all__ = [
    "resolve_genre_dirs",
    "genre_targets",
    "genre_boundaries",
    "genre_stage_prompt",
    "skill_layers",
    "threshold",
    "parse_range",
    "ratio_range",
    "overdue_gap",
]



