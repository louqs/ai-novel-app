"""模型管理 API — 列出/切换/添加 Provider 和 Model."""

from __future__ import annotations

import os
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from starlette.status import HTTP_404_NOT_FOUND, HTTP_400_BAD_REQUEST, HTTP_500_INTERNAL_SERVER_ERROR

from core.llm.openai_compatible_adapter import OpenAICompatibleAdapter
from web.backend.dependencies import get_kernel

router = APIRouter(prefix="/api/v1/models", tags=["models"])


# =============================================================================
# Schemas
# =============================================================================


class SwitchModelRequest(BaseModel):
    tier: str = Field(..., description="premium / standard / budget")
    provider: str = Field(..., description="Provider 名称")
    model: str = Field(..., description="模型名称")


class AddProviderRequest(BaseModel):
    name: str = Field(..., min_length=1, description="Provider 名称（如 deepseek, qwen）")
    type: str = Field(default="openai_compatible", description="Provider 类型")
    base_url: str = Field(default="", description="API 地址")
    api_key: str = Field(default="", description="API Key（优先用环境变量）")
    api_key_env: str = Field(default="", description="API Key 环境变量名")
    default_model: str = Field(..., min_length=1)
    models: list[str] = Field(default_factory=list)


class TestConnectionRequest(BaseModel):
    provider: str
    model: str = ""


# =============================================================================
# 查询
# =============================================================================


@router.get("", response_model=dict)
async def list_all_models():
    """列出所有 Provider 及其可用模型，标注当前各 tier 使用的模型."""
    kernel = await get_kernel()
    registry = kernel.model_registry
    if not registry:
        return {"providers": [], "tiers": {}, "message": "模型注册中心未初始化"}

    # 从配置中读取 models 列表（作为 fallback）
    config_models: dict[str, list[str]] = {}
    for pc in (kernel._config_manager.get_all().get("providers", []) if kernel._config_manager else []):
        pc_name = pc.get("name", "")
        if pc_name:
            config_models[pc_name] = pc.get("models", [])

    # 从 registry + 数据库读取 Provider
    seen = set()
    providers = []
    for rp in registry.list_providers():
        name = rp["name"]
        seen.add(name)
        adapter = registry.get_adapter(name)
        healthy = None
        error_msg = None
        if adapter:
            healthy, error_msg = await adapter.health_check()
        # 从数据库补充额外信息
        db_info = {}
        if kernel.db:
            for dp in await kernel.db.list_providers_db():
                if dp["name"] == name:
                    db_info = dp; break
        # 合并 models：数据库 + 配置文件（去重）
        db_models = db_info.get("models", [])
        cfg_models = config_models.get(name, [])
        merged_models = list(dict.fromkeys(db_models + cfg_models))
        providers.append({
            "name": name, "type": db_info.get("type", rp.get("type","")),
            "base_url": db_info.get("base_url", ""),
            "default_model": db_info.get("default_model", rp.get("default_model","")),
            "models": merged_models,
            "healthy": healthy, "error": error_msg, "registered": True, "from_db": bool(db_info),
        })

    # 补充数据库中的 Provider（未注册的也显示）
    if kernel.db:
        for p in await kernel.db.list_providers_db():
            if p["name"] not in seen:
                providers.append({**p, "healthy": False, "registered": False, "from_db": True, "note": "未连接（检查API Key或网络）"})

    tiers = registry.list_tier_models()
    return {"providers": providers, "tiers": tiers, "active_provider": tiers.get("premium", {}).get("provider", "")}


@router.get("/providers/{provider_name}", response_model=dict)
@router.get("/providers/{provider_name}/", response_model=dict)
async def get_provider(provider_name: str):
    """获取单个 Provider 详情."""
    kernel = await get_kernel()
    registry = kernel.model_registry
    if not registry:
        raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="注册中心未初始化")

    adapter = registry.get_adapter(provider_name)
    if not adapter:
        raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail=f"Provider '{provider_name}' 未注册")

    models = []
    try:
        models = await adapter.list_models()
    except Exception:
        pass

    healthy, error = await adapter.health_check() if hasattr(adapter, "health_check") else (None, None)
    return {
        "name": provider_name,
        "type": type(adapter).__name__,
        "healthy": healthy,
        "error": error,
        "models": models,
    }


# =============================================================================
# 切换
# =============================================================================


@router.post("/switch", response_model=dict)
async def switch_model(data: SwitchModelRequest):
    """热切换某个 tier 使用的模型（立即生效，无需重启）."""
    kernel = await get_kernel()
    registry = kernel.model_registry
    if not registry:
        raise HTTPException(status_code=HTTP_400_BAD_REQUEST, detail="模型注册中心未初始化")

    result = await registry.switch_tier_model(data.tier, data.provider, data.model)
    return {
        "status": "ok",
        "message": f"{data.tier} 已切换为 {data.provider}/{data.model}",
        **result,
    }


@router.post("/switch/all", response_model=dict)
async def switch_all_to_provider(data: SwitchModelRequest):
    """将所有 tier 批量切换到同一 Provider."""
    kernel = await get_kernel()
    registry = kernel.model_registry
    if not registry:
        raise HTTPException(status_code=HTTP_400_BAD_REQUEST, detail="模型注册中心未初始化")

    results = {}
    for tier in ["premium", "standard", "budget"]:
        result = await registry.switch_tier_model(tier, data.provider, data.model)
        results[tier] = result

    return {
        "status": "ok",
        "message": f"所有 tier 已切换为 {data.provider}/{data.model}",
        "results": results,
    }


# =============================================================================
# Provider 管理
# =============================================================================


@router.post("/providers", response_model=dict)
async def add_provider(data: AddProviderRequest):
    """动态添加新 Provider——立即生效，无需重启。

    通过 API 动态注册 OpenAI 兼容 Provider。
    持久化需要手动添加到 config/default.yaml 的 providers 列表。
    """
    kernel = await get_kernel()
    registry = kernel.model_registry
    if not registry:
        # 需要从配置创建
        raise HTTPException(status_code=HTTP_400_BAD_REQUEST, detail="请先通过配置初始化模型注册中心")

    api_key = data.api_key
    if not api_key and data.api_key_env:
        api_key = os.getenv(data.api_key_env, "")

    if not api_key:
        raise HTTPException(
            status_code=HTTP_400_BAD_REQUEST,
            detail="需要提供 api_key 或设置环境变量",
        )

    adapter = OpenAICompatibleAdapter(
        name=data.name, base_url=data.base_url,
        api_key=api_key, default_model=data.default_model,
    )
    registry.register_adapter(adapter)
    # 持久化到数据库
    if kernel.db:
        await kernel.db.save_provider(data.name, "openai_compatible", data.base_url, api_key, data.default_model, data.models)

    return {
        "status": "ok",
        "message": f"Provider '{data.name}' 已添加（已持久化，重启不丢）",
        "provider": data.name,
    }


@router.delete("/providers/{provider_name}", response_model=dict)
@router.delete("/providers/{provider_name}/", response_model=dict)
async def remove_provider(provider_name: str):
    """动态移除 Provider（持久化到数据库）。"""
    kernel = await get_kernel()
    registry = kernel.model_registry
    if not registry:
        raise HTTPException(status_code=HTTP_404_NOT_FOUND)
    registry.unregister(provider_name)
    if kernel.db:
        # 从数据库的自定义 provider 列表中删除
        await kernel.db._exec("DELETE FROM providers WHERE name=?", (provider_name,))
        # 标记配置文件中的 provider 为已删除（重启后不再加载）
        await kernel.db.mark_config_provider_deleted(provider_name)
    return {"status": "ok", "message": f"Provider '{provider_name}' 已移除"}


@router.post("/providers/{provider_name}/restore", response_model=dict)
@router.post("/providers/{provider_name}/restore/", response_model=dict)
async def restore_config_provider(provider_name: str):
    """恢复被删除的配置文件 Provider（重启后生效）。"""
    kernel = await get_kernel()
    if kernel.db:
        await kernel.db.restore_config_provider(provider_name)
    return {"status": "ok", "message": f"Provider '{provider_name}' 将在下次启动时恢复"}


@router.put("/providers/{provider_name}", response_model=dict)
@router.put("/providers/{provider_name}/", response_model=dict)
async def update_provider(provider_name: str, request: Request):
    """更新已有的 Provider 配置."""
    kernel = await get_kernel()
    registry = kernel.model_registry
    if not registry:
        raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="注册中心未初始化")

    # 检查 Provider 是否存在
    adapter = registry.get_adapter(provider_name)
    if not adapter:
        raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail=f"Provider '{provider_name}' 不存在")

    body = await request.json()
    base_url = body.get("base_url")
    api_key = body.get("api_key")
    default_model = body.get("default_model")

    # 获取现有配置
    existing_config = {}
    if kernel.db:
        for dp in await kernel.db.list_providers_db():
            if dp["name"] == provider_name:
                existing_config = dp
                break

    # 合并配置（新值优先，保留旧值）
    new_base_url = base_url if base_url is not None else existing_config.get("base_url", "")
    new_api_key = api_key if api_key else existing_config.get("api_key", "")
    new_default_model = default_model if default_model else existing_config.get("default_model", "")

    if not new_default_model:
        raise HTTPException(status_code=HTTP_400_BAD_REQUEST, detail="默认模型为必填")

    # 更新 models 列表：确保新 default_model 在列表中
    existing_models = existing_config.get("models", [])
    if isinstance(existing_models, str):
        import json as _json
        try:
            existing_models = _json.loads(existing_models)
        except Exception:
            existing_models = []
    new_models = list(existing_models)
    if new_default_model and new_default_model not in new_models:
        new_models.append(new_default_model)

    # 重新创建适配器
    try:
        from core.llm.openai_compatible_adapter import OpenAICompatibleAdapter
        new_adapter = OpenAICompatibleAdapter(
            name=provider_name,
            base_url=new_base_url,
            api_key=new_api_key,
            default_model=new_default_model,
        )
        # 注销旧的，注册新的
        registry.unregister(provider_name)
        registry.register_adapter(new_adapter)

        # 持久化到数据库
        if kernel.db:
            await kernel.db.save_provider(
                provider_name, "openai_compatible",
                new_base_url, new_api_key, new_default_model,
                new_models,
            )

        return {
            "status": "ok",
            "message": f"Provider '{provider_name}' 已更新",
            "default_model": new_default_model,
            "models": new_models,
        }
    except Exception as exc:
        raise HTTPException(status_code=HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)[:200])


# =============================================================================
# 测试连接
# =============================================================================


@router.post("/test", response_model=dict)
async def test_connection(data: TestConnectionRequest):
    """测试 Provider 连接."""
    kernel = await get_kernel()
    registry = kernel.model_registry
    if not registry:
        return {"provider": data.provider, "healthy": False, "error": "注册中心未初始化"}

    return await registry.test_connection(data.provider)


@router.post("/test/all", response_model=dict)
async def test_all_connections():
    """测试所有 Provider 连接."""
    kernel = await get_kernel()
    registry = kernel.model_registry
    if not registry:
        return {"results": {}, "message": "注册中心未初始化"}

    results = await registry.health_check_all()
    all_healthy = all(v.get("healthy") for v in results.values())
    return {
        "results": results,
        "all_healthy": all_healthy,
        "total": len(results),
    }
