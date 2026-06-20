# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

AI 小说生成智能体系统 — 微内核 + 插件架构，支持多 Agent 协作、RAG 知识库、反AI检测和模型分层路由。

## 开发命令

```bash
# 安装依赖
pip install -e ".[dev]"
# 或
pip install -r requirements.txt

# 运行测试
pytest tests/ -v                    # 运行所有测试
pytest tests/ -v -m smoke           # 仅冒烟测试 (无需 API Key)
pytest tests/ -v -m e2e             # 端到端测试 (需 API Key)
pytest tests/test_event_bus.py -v   # 运行单个测试文件

# 代码质量检查
ruff check .                        # Lint
ruff format .                       # 格式化
mypy core/ models/ plugins/         # 类型检查

# 启动 Web 服务
python -m web.backend.main          # 默认 8000 端口
start.bat                           # Windows 快速启动 (8080 端口)

# CLI 工具 (需先安装)
novel init                          # 创建新项目
novel outline --project <ID>        # 生成大纲
novel generate --chapter 1          # 生成第1章
novel check "文本内容"               # 反AI检测
novel serve                         # 启动 Web 服务
novel status                        # 查看状态
```

## 架构核心

### 微内核设计

- **EventBus** (`core/event_bus.py`): 异步事件总线，支持发布/订阅和请求/响应模式，通配符订阅 (`prefix.*`)
- **PluginManager** (`core/plugin_manager.py`): Kahn 算法拓扑排序的插件依赖管理
- **ContextManager** (`core/context_manager.py`): 命名空间化的共享状态存储 (project/session/pipeline/agent/global)
- **ConfigManager** (`core/config_manager.py`): YAML + 环境变量三层合并配置
- **IKernelAPI** (`core/kernel_api.py`): 暴露给插件的公共接口 (LLM、RAG、图谱、文件 I/O)

### 插件系统

插件位于 `plugins/` 目录，每个插件需实现：
- `create_manifest()` → `PluginManifest`
- `create_plugin()` → 插件实例 (duck-typed `PluginHooks` 协议)
- 生命周期钩子: `on_load(kernel)`, `on_unload()`, `on_chapter_before/after()`, `on_gate_check()`, `on_memory_update()`

核心插件链:
1. `idea_incubator` → `world_builder` → `outline_planner`
2. `chapter_writer` → `style_adapter` → `consistency_checker` → `anti_ai_detection`
3. `foreshadow_manager` → `graph_manager` → `writing_coach`
4. `cover_artist` → `pack_market`

### LLM 路由

`core/llm/` 提供多 Provider 支持:
- **Tier 分层**: premium (正文) → standard (审查) → budget (抽取)
- **Provider**: DeepSeek, Qwen, Zhipu, Kimi, Baichuan, Claude, Ollama
- 配置在 `config/default.yaml` 的 `providers` 和 `llm.tiers` 节

### 数据模型

`models/` 目录使用 Pydantic v2 (strict 模式):
- `ProjectMeta`: 项目元数据
- `Settings`: 世界观设定 (世界规则、地点、势力、力量体系)
- `CharacterProfile` + `Relationship`: 人物与关系
- `Progress` + `ChapterNode`: 大纲与进度
- `Chapter`: 章节内容与元数据
- `FactEntry` / `ForeshadowEntry`: 事实账本与伏笔追踪

### 五层记忆体系

| 层级 | 存储 | 用途 |
|:---:|------|------|
| L1 | 内存 | 即时上下文 (对话窗口) |
| L2 | 内存 | 热记忆 (最近 N 章摘要 + 活跃伏笔) |
| L3 | ChromaDB | RAG 检索 (BM25 粗筛 → 语义精排) |
| L4 | Neo4j | 知识图谱 (Cypher 查询) |
| L5 | 文件系统 | 冷存储 (完整小说 + 历史版本) |

### 章节生成流水线

```
IDLE → CONTEXT_ASSEMBLY → DRAFTING → STYLE_ADAPT
    → CONSISTENCY_CHECK → ANTI_AI_CHECK → GATE_CHAIN
    → { ACCEPTED | REVISION_NEEDED }
    → MEMORY_UPDATE → IDLE
```

## 配置

- 主配置: `config/default.yaml` (环境变量前缀 `NOVEL_`)
- 环境变量: `.env` (参考 `.env.example`)
- 数据目录: `novel_output/` (可在配置中修改)

## 测试

- 使用 `pytest-asyncio` (auto 模式)
- Fixtures 在 `tests/conftest.py`
- 测试标记: `smoke` (无 API Key), `e2e` (需 API Key)
- 异步测试直接用 `async def test_xxx()`

## 代码规范

- Python 3.12+ 语法 (使用 `X | Y` 联合类型、f-string 等)
- Ruff 规则: E, F, I, N, W, UP, B, C4, SIM
- 行宽 120 字符
- mypy strict 模式
- 结构化日志: `from core.logging_config import get_logger`
