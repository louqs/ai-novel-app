# AI 小说生成智能体

AI 原生小说创作智能体系统 — 多 Agent 协作 + RAG 知识库 + MCP 工具链。

## 特性

- **微内核 + 插件架构**: 核心引擎极简稳定，所有业务能力可插拔
- **多 Agent 协作**: 写作 Agent、审查 Agent、反AI Agent 专业化分工
- **五层记忆体系**: 即时上下文 → 热记忆 → RAG 检索 → 知识图谱 → 冷存储
- **反AI检测**: 10+ AI 模式特征库 + 文本指纹分析 + 人性化引擎
- **模型分层路由**: Opus/Fable 写正文 → Sonnet 做审查 → Haiku 做抽取
- **MCP 集成**: 标准化外部工具接入，支持 stdio/SSE 传输

## 快速开始

```bash
# 安装依赖
pip install -e ".[dev]"

# 运行测试
pytest tests/ -v

# 启动 Web 控制台 (Phase 1)
python -m web.backend.main
```

## 项目结构

```
ai-novel-app/
├── core/           # 微内核 (事件总线, 插件管理, 配置, 上下文)
├── core/llm/       # LLM 适配器 (Claude + Ollama)
├── models/         # Pydantic 数据模型
├── plugins/        # 业务插件
├── skills/         # 可复用 Skills
├── mcp_servers/    # MCP Server 实现
├── rag/            # RAG 引擎
├── web/            # Web 控制台
├── knowledge_base/ # 知识库数据
└── tests/          # 测试
```

## 技术栈

- Python 3.12+ / FastAPI / Pydantic v2
- Anthropic Claude API (分层路由)
- Neo4j (知识图谱) / ChromaDB (向量存储)
- structlog (结构化日志)
- MCP (Model Context Protocol)
