"""管理 API — 健康检查、插件管理、指标."""

from __future__ import annotations

from fastapi import APIRouter

from web.backend.dependencies import get_kernel

router = APIRouter(tags=["admin"])


@router.get("/api/v1/admin/health", response_model=dict)
async def health_check():
    """健康检查."""
    kernel = await get_kernel()
    active_plugins = await kernel._plugin_manager.list_active()
    return {
        "status": "healthy",
        "version": "0.2.0",
        "plugins_loaded": len(active_plugins),
        "plugins": [p.manifest.name for p in active_plugins],
    }


@router.get("/api/v1/admin/plugins", response_model=dict)
async def list_plugins():
    """列出所有插件."""
    kernel = await get_kernel()
    all_plugins = await kernel._plugin_manager.list_all()
    return {
        "plugins": [
            {
                "name": p.manifest.name,
                "version": p.manifest.version,
                "state": p.state.value,
                "hooks": p.manifest.hooks,
            }
            for p in all_plugins
        ]
    }


@router.post("/api/v1/admin/plugins/{name}/reload", response_model=dict)
async def reload_plugin(name: str):
    """重载插件 (暂不支持, 返回提示)."""
    return {"status": "not_implemented", "message": f"插件 '{name}' 热重载暂不支持，请重启服务"}


@router.get("/api/v1/admin/metrics", response_model=dict)
async def get_metrics():
    """获取运行指标."""
    kernel = await get_kernel()
    active = await kernel._plugin_manager.list_active()
    return {
        "plugins_active": len(active),
        "config_keys": len(kernel.get_config("", {})),
        "uptime": "N/A",
    }
