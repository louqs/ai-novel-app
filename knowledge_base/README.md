# 写作技能库

本目录包含从 Lorn.NovelWriteSkills 整合的专业写作技能库，为AI小说生成系统提供完整的写作能力支持。

## 目录结构

```
knowledge_base/
├── writing_skills/           # 通用写作技能 (50+个)
├── writing_research/         # 写作研究资料
├── genre_skills/             # 题材特定技能 (8个题材)
├── anti_ai_patterns/         # 反AI检测模式库
├── platform_rules/           # 平台规则库
├── genre_data/               # 题材数据
├── packs/                    # 技能包
└── writing_tips/             # 写作技巧
```

## 通用写作技能 (writing_skills/)

### 核心技能

| 技能名称 | 功能描述 | 关键特性 |
|---------|---------|---------|
| 通用-创建小说正文 | 章节正文创作完整流程 | 四拍执行、钩子轮换、黄金三章 |
| 通用-去AI味重写 | AI文本检测与重写 | 十大结构指纹、三档手术强度 |
| 通用-章节创作闭环 | 章节级执行编排 | 日志续跑、终审门槛、读者产物 |
| 通用-设计人物传记 | 人物设计与传记 | 声口即时贴、压力反应微镜头 |
| 通用-设计故事设定 | 世界观与规则设定 | 中国语境、证据链、真实感 |
| 通用-设计总大纲 | 大纲设计与规划 | 模板与验收规则 |
| 通用-正文润色 | 文本精修与优化 | 整体提纯、局部强化 |
| 通用-审阅章节正文 | 章节质量审查 | 问题分级、报告骨架 |

### 写作流程技能

- **构思与设计**: 题材定位、故事面、事件案件引擎、线索伏笔台账
- **写作执行**: 场景单元、对话冲突、微空间受限场景、章节开头/章末钩子
- **修订与质控**: 润色作者有话说、审阅总大纲/分卷大纲/人物传记/故事设定
- **商业化与分发**: 多平台输出、标题设计、内容简介、封面生图提示词

## 写作研究资料 (writing_research/)

### 核心研究

- `写作技法_黄金三章与黄金一章含金量提升研究.md` - 开篇写作技巧
- `小说写作中避免AI味的策略与技巧研究.md` - 反AI检测策略
- `起点中文网爆款小说竞品拆解方法论*.md` - 竞品分析方法
- `平台字数_16平台每章最佳字数研究.md` - 平台字数规范
- `小说项目初始化方法论体系*.md` - 项目初始化框架

### 写作技法研究

- 网文套路蒸馏与提取方法
- 蒸馏小说时识别与理清故事线的方法
- 题材定位框架优化与故事概念PK筛选法
- 网文作者素材库构建完全指南

### 参考书籍

- 《大师写作班：这样写出好故事》
- 哈佛非虚构写作课
- 故事力学/故事工程
- 爆款写作课

## 题材特定技能 (genre_skills/)

### 支持的题材

| 题材 | 目录 | 包含内容 |
|-----|------|---------|
| AI科幻 | `AI科幻/` | 题材技能、写作研究、平台输出 |
| 都市悬疑 | `都市悬疑/` | 题材技能、写作研究、平台输出 |
| 悬疑推理 | `悬疑推理/` | 题材技能、写作研究、平台输出 |
| 女频爱情 | `女频爱情/` | 题材技能、写作研究、平台输出 |
| 异能志怪 | `异能志怪/` | 题材技能、写作研究、平台输出 |
| 都市职场 | `都市职场/` | 题材技能、写作研究、平台输出 |
| 太空科幻 | `太空科幻/` | 题材技能、写作研究、平台输出 |
| 赛博庞克 | `赛博庞克/` | 题材技能、写作研究、平台输出 |

### 题材技能结构

每个题材目录包含:
- `.github/prompts/` - 任务入口与SOP
- `.github/skills/` - 题材包装层技能
- `.github/instructions/` - 题材局部规则
- `.github/agents/` - 题材特定Agent配置

## 质检工具链 (scripts/writing_tools/)

### 字数统计

- `count-chapter.ps1` - 章节正文字数统计
- `count-afterword.ps1` - 作者有话说字数统计

### 质量检测

- `chapter_similarity_check.ps1` - 章节相似度检测
- `check_internal_dup.ps1` - 内部重复检测

### 格式化

- `format_novel_markdown.ps1` - Markdown格式标准化

## 使用指南

### 1. 查看技能详情

```bash
# 查看通用技能
cat knowledge_base/writing_skills/通用-创建小说正文/SKILL.md

# 查看题材技能
cat knowledge_base/genre_skills/AI科幻/.github/prompts/创建小说正文.prompt.md
```

### 2. 使用质检工具

```bash
# 统计章节字数
pwsh scripts/writing_tools/count-chapter.ps1 -File "novel_output/chapter1.md"

# 检查章节相似度
pwsh scripts/writing_tools/chapter_similarity_check.ps1 -Dir "novel_output/"
```

### 3. 参考写作研究

```bash
# 查看开篇写作技巧
cat knowledge_base/writing_research/写作技法_黄金三章与黄金一章含金量提升研究.md

# 查看反AI检测策略
cat knowledge_base/writing_research/小说写作中避免AI味的策略与技巧研究.md
```

## 集成说明

本技能库已与项目的以下组件集成:

1. **RAG知识库**: 技能文档已纳入RAG检索范围
2. **插件系统**: 核心技能可作为插件调用
3. **LLM路由**: 技能文档可作为上下文提供给LLM
4. **质检流水线**: 质检工具已集成到章节生成流水线

## 更新日志

- **2026-06-22**: 从 Lorn.NovelWriteSkills 整合初始版本
  - 50+个通用写作技能
  - 8个题材特定技能
  - 完整的写作研究资料库
  - 质检工具链

## 参考资源

- [Lorn.NovelWriteSkills 原始仓库](https://github.com/LornWriteSkills/Lorn.NovelWriteSkills)
- [项目CLAUDE.md](../CLAUDE.md)
- [插件系统文档](../plugins/README.md)
