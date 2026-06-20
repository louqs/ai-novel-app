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

# 静态文件
static_dir = Path(__file__).parent.parent / "frontend" / "static"
if static_dir.exists():
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

@app.get("/stats", response_class=HTMLResponse)
async def stats_page(request: Request):
    return HTMLResponse(_render_template("stats.html", request))

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
