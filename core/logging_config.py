"""结构化日志配置 — structlog + contextvar 追踪.

用法:
    from core.logging_config import setup_logging, get_logger

    setup_logging(level="DEBUG", json_output=False)
    logger = get_logger(__name__)
    logger.info("章节生成开始", chapter=5, project="proj_001")
"""

from __future__ import annotations

import logging
import os
import sys
from contextvars import ContextVar
from typing import Any

import structlog

# ---------------------------------------------------------------------------
# ContextVar — 跨协程传递 trace 信息
# ---------------------------------------------------------------------------

correlation_id_var: ContextVar[str] = ContextVar("correlation_id", default="")
project_id_var: ContextVar[str] = ContextVar("project_id", default="")
plugin_name_var: ContextVar[str] = ContextVar("plugin_name", default="")

# ---------------------------------------------------------------------------
# 公共 API
# ---------------------------------------------------------------------------


def setup_logging(
    level: str = "DEBUG",
    json_output: bool = False,
) -> None:
    """配置 structlog.

    Args:
        level: 日志级别 (DEBUG, INFO, WARNING, ERROR).
        json_output: True = JSON 格式 (生产), False = 彩色控制台 (开发).
    """
    # 标准库 logging 桥接
    std_level = getattr(logging, level.upper(), logging.DEBUG)

    # structlog 的共享处理器链
    shared_processors: list[Any] = [
        structlog.contextvars.merge_contextvars,  # 合并 ContextVar
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.UnicodeDecoder(),
    ]

    if json_output:
        # 生产: JSON 输出到 stdout
        structlog.configure(
            processors=shared_processors
            + [
                structlog.processors.CallsiteParameterAdder(
                    {
                        structlog.processors.CallsiteParameter.FILENAME,
                        structlog.processors.CallsiteParameter.FUNC_NAME,
                        structlog.processors.CallsiteParameter.LINENO,
                    }
                ),
                structlog.processors.JSONRenderer(),
            ],
            wrapper_class=structlog.stdlib.BoundLogger,
            context_class=dict,
            logger_factory=structlog.PrintLoggerFactory(sys.stdout),
            cache_logger_on_first_use=True,
        )
    else:
        # 开发: 彩色控制台
        structlog.configure(
            processors=shared_processors
            + [
                structlog.dev.ConsoleRenderer(colors=True),
            ],
            wrapper_class=structlog.stdlib.BoundLogger,
            context_class=dict,
            logger_factory=structlog.PrintLoggerFactory(sys.stdout),
            cache_logger_on_first_use=True,
        )

    # 配置标准 logging root (uvicorn / FastAPI 使用)
    logging.basicConfig(
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        level=std_level,
        stream=sys.stdout,
    )

    # 静默一些噪音库
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("chromadb").setLevel(logging.WARNING)
    logging.getLogger("neo4j").setLevel(logging.WARNING)


def get_logger(name: str | None = None, **bind_kwargs: Any) -> structlog.stdlib.BoundLogger:
    """获取绑定了上下文变量的 structlog logger.

    自动绑定 correlation_id、project_id、plugin_name。
    可通过 bind_kwargs 额外绑定字段。
    """
    logger = structlog.get_logger(name or __name__)

    # 自动绑定 ContextVar
    bound: dict[str, Any] = {}
    cid = correlation_id_var.get()
    if cid:
        bound["correlation_id"] = cid
    pid = project_id_var.get()
    if pid:
        bound["project_id"] = pid
    pn = plugin_name_var.get()
    if pn:
        bound["plugin"] = pn

    bound.update(bind_kwargs)

    if bound:
        return logger.bind(**bound)
    return logger


def set_trace_context(
    correlation_id: str = "",
    project_id: str = "",
    plugin_name: str = "",
) -> None:
    """设置当前协程的 trace 上下文."""
    if correlation_id:
        correlation_id_var.set(correlation_id)
    if project_id:
        project_id_var.set(project_id)
    if plugin_name:
        plugin_name_var.set(plugin_name)


def clear_trace_context() -> None:
    """清除 trace 上下文."""
    correlation_id_var.set("")
    project_id_var.set("")
    plugin_name_var.set("")
