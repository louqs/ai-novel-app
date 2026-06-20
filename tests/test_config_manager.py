"""配置管理器单元测试."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
import yaml

from core.config_manager import ConfigManager


@pytest.mark.asyncio
async def test_load_default_config(temp_config_dir: Path, default_config_yaml: dict) -> None:
    """验证加载默认配置."""
    with open(temp_config_dir / "default.yaml", "w", encoding="utf-8") as f:
        yaml.dump(default_config_yaml, f)

    cm = ConfigManager(str(temp_config_dir))
    await cm.load(env="test")

    assert cm.get("app.name") == "ai-novel-app-test"
    assert cm.get("llm.default_tier") == "standard"


@pytest.mark.asyncio
async def test_dot_notation_access(config_manager: ConfigManager) -> None:
    """验证点号分隔键访问."""
    assert config_manager.get("llm.default_tier") == "standard"
    assert config_manager.get("rag.retrieval.bm25_candidates") == 8


@pytest.mark.asyncio
async def test_default_value(config_manager: ConfigManager) -> None:
    """验证默认值."""
    assert config_manager.get("nonexistent.key", "default") == "default"
    assert config_manager.get("nonexistent.key") is None


@pytest.mark.asyncio
async def test_env_override(temp_config_dir: Path, default_config_yaml: dict) -> None:
    """验证环境变量覆盖."""
    with open(temp_config_dir / "default.yaml", "w", encoding="utf-8") as f:
        yaml.dump(default_config_yaml, f)

    os.environ["NOVEL_APP__NAME"] = "env-override-app"
    os.environ["NOVEL_LLM__DEFAULT_TIER"] = "premium"

    try:
        cm = ConfigManager(str(temp_config_dir))
        await cm.load(env="test")

        assert cm.get("app.name") == "env-override-app"
        assert cm.get("llm.default_tier") == "premium"
    finally:
        del os.environ["NOVEL_APP__NAME"]
        del os.environ["NOVEL_LLM__DEFAULT_TIER"]


@pytest.mark.asyncio
async def test_env_specific_yaml(temp_config_dir: Path, default_config_yaml: dict) -> None:
    """验证环境特定 YAML 覆盖."""
    with open(temp_config_dir / "default.yaml", "w", encoding="utf-8") as f:
        yaml.dump(default_config_yaml, f)
    with open(temp_config_dir / "test.yaml", "w", encoding="utf-8") as f:
        yaml.dump({"app": {"name": "test-env-app"}}, f)

    cm = ConfigManager(str(temp_config_dir))
    await cm.load(env="test")

    # test.yaml 覆盖了 app.name
    assert cm.get("app.name") == "test-env-app"
    # 其他字段保持 default
    assert cm.get("app.version") == "0.1.0"


@pytest.mark.asyncio
async def test_runtime_set(config_manager: ConfigManager) -> None:
    """验证运行时设置配置."""
    config_manager.set("app.debug", False)
    assert config_manager.get("app.debug") is False

    config_manager.set("new.key.nested", "value")
    assert config_manager.get("new.key.nested") == "value"


@pytest.mark.asyncio
async def test_coerce_value() -> None:
    """验证环境变量值类型转换."""
    assert ConfigManager._coerce_value("true") is True
    assert ConfigManager._coerce_value("FALSE") is False
    assert ConfigManager._coerce_value("42") == 42
    assert ConfigManager._coerce_value("3.14") == 3.14
    assert ConfigManager._coerce_value("hello") == "hello"


@pytest.mark.asyncio
async def test_deep_merge() -> None:
    """验证深度合并."""
    base = {"a": {"b": 1, "c": 2}, "d": 3}
    override = {"a": {"b": 10}, "e": 4}

    result = ConfigManager._deep_merge(base, override)

    assert result["a"]["b"] == 10  # 覆盖
    assert result["a"]["c"] == 2   # 保留
    assert result["d"] == 3
    assert result["e"] == 4        # 新增


@pytest.mark.asyncio
async def test_config_change_callback(config_manager: ConfigManager) -> None:
    """验证配置变更回调."""
    changed: list[tuple[str, Any]] = []

    async def callback(key: str, value: Any) -> None:
        changed.append((key, value))

    await config_manager.on_change("app.debug", callback)
    config_manager.set("app.debug", False)

    # 等待异步回调
    import asyncio
    await asyncio.sleep(0.1)

    assert len(changed) == 1
    assert changed[0] == ("app.debug", False)
