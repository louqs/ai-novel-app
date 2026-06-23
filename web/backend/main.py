"""AI 小说生成智能体 — FastAPI 主入口.

启动:
    python -m web.backend.main
    或
    uvicorn web.backend.main:app --reload --port 8000
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
# 使用手动渲染 (兼容 Python 3.14)

from core.logging_config import setup_logging
from core.middleware.logging_middleware import LoggingMiddleware
from web.backend.dependencies import get_app_state
from web.backend.routes import admin, export_coach, generation, graph, images, models, packs, projects, skills, stream, workbench


# =============================================================================
# 生命周期
# =============================================================================


async def _handle_interrupted_outline_jobs(state):
    """服务器启动时处理中断的大纲生成任务."""
    try:
        if not state.database:
            return
        interrupted = await state.database.get_interrupted_outline_jobs()
        if not interrupted:
            return

        from core.logging_config import get_logger
        logger = get_logger(__name__)

        for job in interrupted:
            pid = job["project_id"]
            versions = job.get("versions", [])
            current = job.get("current", 0)
            total = job.get("total", 3)

            if versions:
                # 有已完成的版本，标记为 done（部分完成）
                await state.database.save_outline_job(
                    pid, "done", total, current, versions,
                    f"服务重启，已恢复 {len(versions)} 个版本（原计划 {total} 个）",
                )
                logger.info(
                    "恢复中断的大纲生成任务",
                    project_id=pid,
                    recovered_versions=len(versions),
                    planned=total,
                )
            else:
                # 没有已完成的版本，标记为 error
                await state.database.save_outline_job(
                    pid, "error", total, current, [],
                    "服务重启，生成中断，无已完成版本",
                )
                logger.info("标记中断的大纲生成任务为失败", project_id=pid)
    except Exception as e:
        from core.logging_config import get_logger
        logger = get_logger(__name__)
        logger.error("处理中断大纲任务失败", error=str(e))


@asynccontextmanager
async def lifespan(app: FastAPI):
    """FastAPI 生命周期管理."""
    # 加载 .env
    from pathlib import Path
    env_file = Path(__file__).parent.parent.parent / ".env"
    if env_file.exists():
        from dotenv import load_dotenv
        load_dotenv(env_file)

    # 启动
    setup_logging(level="DEBUG", json_output=False)
    state = await get_app_state()

    # 处理中断的大纲生成任务
    await _handle_interrupted_outline_jobs(state)

    print(f"\n{'='*60}")
    print(f"  AI 小说生成智能体 v0.3.0")
    print(f"  插件: {len(await state.plugin_manager.list_active())} 个已激活")
    print(f"  Web:  http://127.0.0.1:{app.state.port if hasattr(app.state, 'port') else '7788'}")
    print(f"  数据: D:/ai-output-novel/")
    print(f"{'='*60}\n")
    yield
    # 关闭
    await state.shutdown()


# =============================================================================
# App
# =============================================================================


app = FastAPI(
    title="AI 小说生成智能体",
    description="多 Agent 协作 + RAG 知识库 + MCP 工具链",
    version="0.2.0",
    lifespan=lifespan,
)

# 中间件
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(LoggingMiddleware)

# 路由
app.include_router(projects.router)
app.include_router(generation.router)
app.include_router(models.router)
app.include_router(skills.router)
app.include_router(export_coach.router)
app.include_router(graph.router)
app.include_router(stream.router)
app.include_router(images.router)
app.include_router(packs.router)
app.include_router(workbench.router)
app.include_router(admin.router)

# 静态文件（开发模式禁用缓存）
static_dir = Path(__file__).parent.parent / "frontend" / "static"
if static_dir.exists():
    from starlette.middleware.base import BaseHTTPMiddleware
    from starlette.responses import Response

    class NoCacheMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            response = await call_next(request)
            if request.url.path.startswith("/static/"):
                response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
                response.headers["Pragma"] = "no-cache"
                response.headers["Expires"] = "0"
            return response

    app.add_middleware(NoCacheMiddleware)
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

# Jinja2 模板 (直接使用 Environment, 兼容 Python 3.14)
template_dir = Path(__file__).parent.parent / "frontend" / "templates"
_jinja_env = None
if template_dir.exists():
    from jinja2 import Environment, FileSystemLoader, BaseLoader, TemplateNotFound
    class _NoCacheLoader(FileSystemLoader):
        """绕过 Jinja2 缓存的 Loader."""
        def get_source(self, environment, template):
            return super().get_source(environment, template)
    _jinja_env = Environment(
        loader=FileSystemLoader(str(template_dir)),
        auto_reload=True,
        cache_size=0,
        enable_async=False,
    )
    # 直接去掉内部缓存
    _jinja_env.cache = None


def _render_template(name: str, request) -> str:
    """手动渲染模板，避开 Jinja2 缓存 bug。"""
    if _jinja_env is None:
        return "<h1>模板目录未找到</h1>"
    try:
        tmpl = _jinja_env.get_template(name)
        return tmpl.render(request=request)
    except Exception as e:
        import traceback
        return f"<h1>渲染错误</h1><pre>{traceback.format_exc()}</pre>"

templates = None  # 不再使用 Starlette 的 Jinja2Templates


# =============================================================================
# 前端页面
# =============================================================================


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return HTMLResponse(_render_template("index.html", request))


@app.get("/anti-ai", response_class=HTMLResponse)
async def anti_ai_page(request: Request):
    return HTMLResponse(_render_template("anti_ai.html", request))


@app.get("/workbench", response_class=HTMLResponse)
async def workbench_page(request: Request):
    return HTMLResponse(_render_template("workbench.html", request))


@app.get("/models", response_class=HTMLResponse)
async def models_page(request: Request):
    return HTMLResponse(_render_template("models.html", request))


@app.get("/graph-viz", response_class=HTMLResponse)
async def graph_viz_page(request: Request):
    return HTMLResponse(_render_template("graph_viz.html", request))

@app.get("/coach", response_class=HTMLResponse)
async def coach_page(request: Request):
    return HTMLResponse(_render_template("coach.html", request))

@app.get("/pipeline-editor", response_class=HTMLResponse)
async def pipeline_editor_page(request: Request):
    return HTMLResponse(_render_template("pipeline_editor.html", request))

@app.get("/stats", response_class=HTMLResponse)
async def stats_page(request: Request):
    return HTMLResponse(_render_template("stats.html", request))


@app.get("/reader", response_class=HTMLResponse)
async def reader_page(request: Request):
    return HTMLResponse(_render_template("reader.html", request))

@app.get("/skills", response_class=HTMLResponse)
async def skills_page(request: Request):
    return HTMLResponse(_render_template("skills_page.html", request))

@app.get("/packs", response_class=HTMLResponse)
async def packs_page(request: Request):
    return HTMLResponse(_render_template("packs_page.html", request))

@app.get("/cover", response_class=HTMLResponse)
async def cover_page(request: Request):
    return HTMLResponse(_render_template("cover.html", request))


# =============================================================================
# 直接运行
# =============================================================================

if __name__ == "__main__":
    import uvicorn

    uvicorn.run("web.backend.main:app", host="127.0.0.1", port=8000, reload=True)
