# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

AI 小说生成智能体系统 — 微内核 + 插件架构，支持多 Agent 协作、RAG 知识库、反AI检测和模型分层路由。

## 写作技能库（整合自 Lorn.NovelWriteSkills）

项目已整合专业写作技能库，包含50+个通用写作技能、8个题材特定技能和质检工具链。

### 知识库结构

```
knowledge_base/
├── writing_skills/           # 通用写作技能
│   ├── 通用-创建小说正文/     # 章节正文创作完整流程
│   ├── 通用-去AI味重写/       # AI文本检测与重写
│   ├── 通用-章节创作闭环/     # 章节级执行编排
│   ├── 通用-设计人物传记/     # 人物设计与传记
│   ├── 通用-设计故事设定/     # 世界观与规则设定
│   ├── 通用-设计总大纲/       # 大纲设计与规划
│   ├── 通用-正文润色/         # 文本精修与优化
│   ├── 通用-审阅章节正文/     # 章节质量审查
│   ├── 长篇小说最佳实践.md   # 长篇写作指南
│   └── 短篇小说集最佳实践.md # 短篇写作指南
├── writing_research/         # 写作研究资料
│   ├── 写作技法_*.md         # 各类写作技法研究
│   ├── 小说写作中避免AI味的策略与技巧研究.md
│   ├── 起点中文网爆款小说竞品拆解方法论.md
│   └── 平台字数_16平台每章最佳字数研究.md
├── genre_skills/             # 题材特定技能
│   ├── AI科幻/               # AI科幻题材技能
│   ├── 都市悬疑/             # 都市悬疑题材技能
│   ├── 悬疑推理/             # 悬疑推理题材技能
│   ├── 女频爱情/             # 女频爱情题材技能
│   ├── 异能志怪/             # 异能志怪题材技能
│   ├── 都市职场/             # 都市职场题材技能
│   ├── 太空科幻/             # 太空科幻题材技能
│   └── 赛博庞克/             # 赛博庞克题材技能
└── anti_ai_patterns/         # 反AI检测模式库
```

### 质检工具链

```
scripts/writing_tools/
├── count-chapter.ps1              # 章节字数统计
├── count-afterword.ps1            # 作者有话说字数统计
├── chapter_similarity_check.ps1   # 章节相似度检测
├── check_internal_dup.ps1         # 内部重复检测
└── format_novel_markdown.ps1      # Markdown格式化
```

### 核心能力覆盖

- **构思与设计**: 题材定位、故事面、人物传记、故事设定、大纲设计
- **写作执行**: 章节创作、场景单元、对话冲突、章末钩子
- **修订与质控**: 正文润色、去AI味、审阅优化
- **商业化与分发**: 多平台输出、标题设计、内容简介、签约评估
- **研究与调研**: 深度研究、竞对分析、市场调研

### 规则分层体系（R/P/G/S）与体裁靶值取值约定

写作技能库的规范条款按四层组织，判据与复用模板见 `knowledge_base/writing_skills/规则分类抽样框.md`。
**调用润色/审阅类 Skill 时必须先认条款层级**，避免把可量化项（对话占比、字数）当成压过判断项（节奏、声口）的铁律：

| 层 | 含义 | 措辞 | 是否强制 |
|---|---|---|---|
| **R 红线** | 违反即错，全体裁通用 | 必须 / 禁止 | 是 |
| **P 工序门禁** | 流程纪律（先读什么、跑什么脚本、报告格式），与文笔无关 | 必须 | 是 |
| **G 体裁靶值** | 量化阈值，**向体裁包取值**，不在通用规则写死 | 向体裁靶值靠拢 | 取值后按值判 |
| **S 诊断信号** | 影响体验非致命，是体检倾向 | 优先 / 倾向 / 检查 | 否 |

**体裁靶值取值约定（重要）**：通用 Skill 里的量化阈值（对话占比、正文字数、章首窗口、爽点间隔、
单处解释长度、零画面段落上限等）均为**通用默认回退值**。执行润色/审阅时：

1. 先确定当前题材 → 读 `knowledge_base/genre_skills/{题材}/靶值.md`
2. 该文件给定的参数 **优先于** 通用默认值；未给定的项才回退通用默认
3. 例：审悬疑推理章节，对话占比读靶值的 `45%–65%`（盘问对白偏多），而非一律用通用 `25%–55%` 判超标

已落地文件：
- `writing_skills/规则分类抽样框.md` — 四象限判据 + 决策树 + 抽样脚本 + 复用模板
- `writing_skills/通用-正文润色/SKILL.md` — 按 R/P/G/S 四层重构（§A红线/§B工序/§C靶值/§D信号；`.bak` 为原件）
- `writing_skills/通用-审阅章节正文/SKILL.md` — 头部加分层导航表，量化阈值指向靶值（`.bak` 为原件）
- `genre_skills/{8题材}/靶值.md` — 8 体裁全覆盖；`{题材}/.github/instructions/题材边界与创作说明*` 为数值依据
  - 数值可靠度分两档：AI科幻/女频爱情/都市悬疑/都市职场/异能志怪 源自体裁包既有口径；
    太空科幻/悬疑推理/赛博庞克 源自类型常识基线（靶值表中标 `⚙`，建议按竞品数据校准）

新增体裁时：建 `genre_skills/{题材}/靶值.md`（照搬 `AI科幻/靶值.md` 结构，只填与通用默认的差量即可）。

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

## 写作技能库使用指南

### 快速开始

1. **查看通用写作技能**
   ```bash
   # 浏览所有通用技能
   ls knowledge_base/writing_skills/

   # 查看特定技能详情
   cat knowledge_base/writing_skills/通用-创建小说正文/SKILL.md
   ```

2. **查看题材特定技能**
   ```bash
   # 浏览所有题材技能
   ls knowledge_base/genre_skills/

   # 查看特定题材技能
   ls knowledge_base/genre_skills/AI科幻/.github/prompts/
   ```

3. **使用质检工具**
   ```bash
   # 统计章节字数
   pwsh scripts/writing_tools/count-chapter.ps1 -File "novel_output/chapter1.md"

   # 检查章节相似度
   pwsh scripts/writing_tools/chapter_similarity_check.ps1 -Dir "novel_output/"
   ```

### 核心写作流程

#### 1. 项目初始化阶段
- 参考 `knowledge_base/writing_research/小说项目初始化方法论体系*.md`
- 使用 `通用-小说项目初始化` 技能进行战略策划

#### 2. 构思与设计阶段
- **题材定位**: `通用-设计题材定位框架`
- **人物设计**: `通用-设计人物传记` (含人物传记模板)
- **故事设定**: `通用-设计故事设定` (含世界观规则)
- **大纲设计**: `通用-设计总大纲` + `通用-设计分卷大纲`

#### 3. 章节写作阶段
- **正文创作**: `通用-创建小说正文` (含四拍执行与钩子轮换)
- **章节闭环**: `通用-章节创作闭环` (含日志续跑与终审门槛)
- **场景执行**: `通用-执行场景单元` (含场景施工卡)

#### 4. 修订与质控阶段
- **正文润色**: `通用-正文润色`
- **去AI味**: `通用-去AI味重写` (含十大结构指纹诊断)
- **章节审阅**: `通用-审阅章节正文` (含问题分级)

### 写作研究资料

关键研究资料位于 `knowledge_base/writing_research/`:

- `写作技法_黄金三章与黄金一章含金量提升研究.md` - 开篇写作技巧
- `小说写作中避免AI味的策略与技巧研究.md` - 反AI检测策略
- `起点中文网爆款小说竞品拆解方法论*.md` - 竞品分析方法
- `平台字数_16平台每章最佳字数研究.md` - 平台字数规范

### 题材特定技能

每个题材目录包含:
- `.github/prompts/` - 任务入口与SOP
- `.github/skills/` - 题材包装层技能
- `.github/instructions/` - 题材局部规则
- `.github/agents/` - 题材特定Agent配置

### 质检工具使用

所有质检脚本位于 `scripts/writing_tools/`:

- **字数统计**: `count-chapter.ps1` (正文) / `count-afterword.ps1` (作者有话说)
- **相似度检测**: `chapter_similarity_check.ps1` (检测章节间相似度)
- **重复检测**: `check_internal_dup.ps1` (检测内部重复)
- **格式化**: `format_novel_markdown.ps1` (Markdown格式标准化)

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
