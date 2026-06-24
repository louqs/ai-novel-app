"""FastAPI 日志中间件 — 请求追踪 + 慢请求告警."""

from __future__ import annotations

import time
import uuid
from typing import Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from core.logging_config import get_logger, set_trace_context


logger = get_logger(__name__)


class LoggingMiddleware(BaseHTTPMiddleware):
    """记录每个 HTTP 请求的方法、路径、状态码、耗时.

    自动生成 correlation_id 并注入到 structlog 上下文.
    """

    def __init__(
        self,
        app,
        *,
        slow_request_threshold_ms: int = 5000,
        log_request_body: bool = False,
        log_response_body: bool = False,
    ) -> None:
        super().__init__(app)
        self._slow_threshold = slow_request_threshold_ms
        self._log_request_body = log_request_body
        self._log_response_body = log_response_body

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        # 生成/提取 correlation_id
        correlation_id = request.headers.get("X-Correlation-ID", str(uuid.uuid4())[:12])
        set_trace_context(correlation_id=correlation_id)

        start = time.perf_counter()

        # 请求日志
        req_logger = get_logger("http.request", method=request.method, path=request.url.path)
        req_logger.debug("→ 收到请求")

        try:
            response = await call_next(request)
        except Exception:
            elapsed_ms = (time.perf_counter() - start) * 1000
            err_logger = get_logger("http.error", status=500, elapsed_ms=round(elapsed_ms, 2))
            err_logger.exception("[ERROR] 请求处理异常")
            raise

        elapsed_ms = (time.perf_counter() - start) * 1000
        status_code = response.status_code

        # 响应日志
        resp_logger = get_logger(
            "http.response",
            method=request.method,
            path=request.url.path,
            status=status_code,
            elapsed_ms=round(elapsed_ms, 2),
        )

        if status_code >= 500:
            resp_logger.error("[ERROR] 服务器错误")
        elif status_code >= 400:
            resp_logger.warning("[WARN] 客户端错误")
        elif elapsed_ms > self._slow_threshold:
            resp_logger.warning("[WARN] 慢请求")
        else:
            resp_logger.info("[OK] 请求完成")

        # 注入 correlation_id 到响应头
        response.headers["X-Correlation-ID"] = correlation_id

        return response
