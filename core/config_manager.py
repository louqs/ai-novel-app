"""配置管理器 — 三层合并 YAML + 环境变量覆盖 + 热加载.

加载优先级 (后覆盖前):
    1. config/default.yaml       — 默认值
    2. config/{NOVEL_ENV}.yaml   — 环境特定配置 (development / production)
    3. 环境变量 (NOVEL_ 前缀)    — 运行时覆盖, 点号用双下划线替代
       例: NOVEL_LLM__DEFAULT_TIER=premium

用法:
    config = ConfigManager(config_dir="config")
    await config.load()

    tier = config.get("llm.default_tier")  # -> "standard"
    tier = config.get("llm.default_tier", "premium")  # 带默认值

    config.on_change("llm", lambda key, val: print(f"LLM 配置变更: {key}={val}"))
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any, Callable, Coroutine

import structlog
import yaml
from pydantic import BaseModel, Field, ValidationError
from watchfiles import awatch  # type: ignore[import-untyped]

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# 配置 Schema (Pydantic — 用于验证)
# ---------------------------------------------------------------------------

class LLMTierConfig(BaseModel):
    provider: str = "claude"
    model: str = ""
    max_tokens: int = 4096
    temperature: float = 0.7
    description: str = ""


class LLMConfig(BaseModel):
    default_tier: str = "standard"
    tiers: dict[str, LLMTierConfig] = Field(default_factory=dict)
    request_timeout: int = 120
    max_retries: int = 3
    retry_base_delay: float = 2.0
    prompt_cache: dict[str, Any] = Field(default_factory=dict)


class AppConfig(BaseModel):
    name: str = "ai-novel-app"
    version: str = "0.1.0"
    env: str = "development"
    debug: bool = True
    data_dir: str = "./novel_output"
    max_concurrent_chapters: int = 1


class ConfigSchema(BaseModel):
    """顶层配置 Schema 用于验证."""
    app: AppConfig = Field(default_factory=AppConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    ollama: dict[str, Any] = Field(default_factory=dict)
    rag: dict[str, Any] = Field(default_factory=dict)
    knowledge_graph: dict[str, Any] = Field(default_factory=dict)
    vector_store: dict[str, Any] = Field(default_factory=dict)
    mcp: dict[str, Any] = Field(default_factory=dict)
    logging: dict[str, Any] = Field(default_factory=dict)
    anti_ai: dict[str, Any] = Field(default_factory=dict)
    chapter: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# ConfigManager
# ---------------------------------------------------------------------------

ChangeCallback = Callable[[str, Any], Coroutine[Any, Any, None]]


class ConfigError(Exception):
    """配置加载/访问错误."""


class ConfigManager:
    """三层合并配置管理器."""

    def __init__(self, config_dir: str | Path = "config") -> None:
        self._config_dir = Path(config_dir)
        self._data: dict[str, Any] = {}
        self._listeners: dict[str, list[ChangeCallback]] = {}
        self._watcher_task: asyncio.Task[None] | None = None
        self._should_watch = True
        self._lock = asyncio.Lock()

    # ---- 加载 ----

    async def load(self, env: str | None = None) -> dict[str, Any]:
        """加载配置: default.yaml < {env}.yaml < 环境变量."""
        async with self._lock:
            # 1. 默认配置
            merged = self._load_yaml("default.yaml")

            # 2. 环境特定配置
            if env is None:
                env = os.getenv("NOVEL_ENV", "development")
            env_path = self._config_dir / f"{env}.yaml"
            if env_path.exists():
                env_data = self._load_yaml(f"{env}.yaml")
                merged = self._deep_merge(merged, env_data)

            # 3. 环境变量覆盖
            merged = self._apply_env_overrides(merged)

            self._data = merged

            # 验证
            try:
                ConfigSchema.model_validate(merged)
            except ValidationError as exc:
                logger.warning("配置验证警告", errors=str(exc))

            logger.info("配置已加载", env=env, keys=len(merged))
            return merged

    async def reload(self) -> dict[str, Any]:
        """热重载配置 (保留当前 env)."""
        return await self.load(env=self.get("app.env", "development"))

    # ---- 访问 ----

    def get(self, key: str, default: Any = None) -> Any:
        """点号分隔键访问, 如 'llm.default_tier'."""
        keys = key.split(".")
        node: Any = self._data
        for k in keys:
            if isinstance(node, dict) and k in node:
                node = node[k]
            else:
                return default
        return node

    def get_all(self) -> dict[str, Any]:
        """返回全部配置的浅拷贝."""
        return dict(self._data)

    def set(self, key: str, value: Any) -> None:
        """运行时设置配置项 (仅内存, 不写盘)."""
        keys = key.split(".")
        node = self._data
        for k in keys[:-1]:
            if k not in node:
                node[k] = {}
            node = node[k]
        old = node.get(keys[-1])
        node[keys[-1]] = value
        if old != value:
            # 触发回调（异步 fire-and-forget）
            for cb in self._listeners.get(key, []):
                asyncio.create_task(self._safe_fire(cb, key, value))

    # ---- 变更监听 ----

    async def on_change(self, key: str, callback: ChangeCallback) -> None:
        """注册配置变更监听. key 支持前缀匹配."""
        self._listeners.setdefault(key, []).append(callback)

    # ---- 文件监听 ----

    async def start_watching(self) -> None:
        """启动文件监听, 配置目录变更时自动热重载."""
        self._should_watch = True
        self._watcher_task = asyncio.create_task(self._watch_loop())

    async def stop_watching(self) -> None:
        """停止文件监听."""
        self._should_watch = False
        if self._watcher_task:
            self._watcher_task.cancel()
            try:
                await self._watcher_task
            except asyncio.CancelledError:
                pass
            self._watcher_task = None

    # ---- 内部方法 ----

    def _load_yaml(self, filename: str) -> dict[str, Any]:
        path = self._config_dir / filename
        if not path.exists():
            logger.warning("配置文件不存在", path=str(path))
            return {}
        with open(path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}

    def _apply_env_overrides(self, config: dict[str, Any], prefix: str = "NOVEL") -> dict[str, Any]:
        """将 NOVEL_ 前缀的环境变量合并到配置.

        NOVEL_LLM__DEFAULT_TIER=premium  →  config["llm"]["default_tier"] = "premium"
        (双下划线分隔层级，单下划线保留在键名中)
        """
        result = dict(config)
        for env_key, env_val in os.environ.items():
            if not env_key.startswith(f"{prefix}_"):
                continue
            # 去掉前缀, 双下划线 → 点号分隔 (层级)
            key_path = env_key[len(prefix) + 1 :]
            keys = [k.lower() for k in key_path.split("__")]
            self._set_nested(result, keys, self._coerce_value(env_val))
        return result

    @staticmethod
    def _set_nested(data: dict[str, Any], keys: list[str], value: Any) -> None:
        for k in keys[:-1]:
            if k not in data:
                data[k] = {}
            data = data[k]
        data[keys[-1]] = value

    @staticmethod
    def _coerce_value(raw: str) -> Any:
        """尝试将字符串转为 bool/int/float, 否则保留字符串."""
        r = raw.strip()
        if r.lower() in ("true", "false"):
            return r.lower() == "true"
        try:
            return int(r)
        except ValueError:
            pass
        try:
            return float(r)
        except ValueError:
            pass
        return r

    @staticmethod
    def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
        """递归合并, override 的值覆盖 base."""
        result = dict(base)
        for key, val in override.items():
            if key in result and isinstance(result[key], dict) and isinstance(val, dict):
                result[key] = ConfigManager._deep_merge(result[key], val)
            else:
                result[key] = val
        return result

    async def _watch_loop(self) -> None:
        """文件监听循环."""
        try:
            async for _changes in awatch(self._config_dir):
                if not self._should_watch:
                    break
                logger.info("检测到配置变更, 正在热重载...")
                try:
                    await self.reload()
                    # 触发全局配置变更通知
                    for cb in self._listeners.get("*", []):
                        await self._safe_fire(cb, "*", self._data)
                except Exception:
                    logger.exception("配置热重载失败")
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("文件监听异常")

    async def _safe_fire(self, callback: ChangeCallback, key: str, value: Any) -> None:
        try:
            await callback(key, value)
        except Exception:
            logger.exception("配置变更回调异常", key=key)
