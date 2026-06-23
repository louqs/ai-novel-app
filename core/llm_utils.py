"""LLM 工具函数 — 公共 JSON 解析等."""

from __future__ import annotations

import json
import re
from typing import Any


def parse_llm_json(content: str) -> dict[str, Any] | list[Any]:
    """从 LLM 响应中提取 JSON.

    尝试顺序:
    1. 直接 json.loads
    2. ```json...``` 代码块提取
    3. 第一个 { ... } 或 [ ... ]

    Returns:
        解析后的 dict 或 list，解析失败返回空 dict 或空 list
    """
    if not content or not content.strip():
        return {}

    # 1. 直接解析
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        pass

    # 2. 代码块提取
    m = re.search(r'```(?:json)?\s*([\s\S]*?)```', content)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass

    # 3. 第一个 JSON 对象或数组
    for start_char, end_char in [('{', '}'), ('[', ']')]:
        start = content.find(start_char)
        end = content.rfind(end_char)
        if start >= 0 and end > start:
            try:
                return json.loads(content[start:end + 1])
            except json.JSONDecodeError:
                continue

    return {} if not content.strip().startswith('[') else []
