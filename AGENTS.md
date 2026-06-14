# AGENTS.md - AI Knowledge Base Assistant

## 项目概述

AI Knowledge Base Assistant 是一个自动化采集、分析和分发 AI/LLM/Agent 领域技术动态的智能系统。系统每天从 GitHub Trending 和 Hacker News 自动抓取相关内容，通过大语言模型进行智能分析和结构化整理，最终生成知识条目并分发到 Telegram 和飞书等渠道，帮助技术团队持续跟踪 AI 领域前沿动态。

## 技术栈

| 组件 | 技术选型 | 说明 |
|------|----------|------|
| 运行环境 | Python 3.12 | 要求类型注解、模式匹配等现代特性 |
| AI 框架 | LangGraph | Agent 工作流编排与状态管理 |
| 开发环境 | OpenCode + 国产大模型 | 代码辅助与智能审查 |
| 数据采集 | OpenClaw | GitHub/HN API 封装与调度 |
| 数据存储 | JSON Lines | 知识条目持久化 |
| 消息分发 | Telegram Bot API / 飞书 Webhook | 多渠道推送 |

## 编码规范

> **完整规范参见 [`docs/coding-standards.md`](docs/coding-standards.md)**
> 
> 以下摘要仅作快速参考，执行标准以完整文档为准。

### 核心要求
- **Python**: black (line-length=88) + isort + ruff + mypy
- **TypeScript**: strict mode + eslint + prettier
- **文档**: 所有 `export` 函数必须有 Google style docstring / JSDoc
- **覆盖率**: 行覆盖 ≥ 80%，核心模块 ≥ 85%，分支覆盖 ≥ 70%

### 红线摘要
- 禁止魔法字符串（业务逻辑中的语义字面量）
- 禁止 TODO/FIXME/HACK/XXX 进 main 分支（带 issue 引用除外）
- 禁止裸 `print()`，必须使用 `logging`
- 禁止捕获裸异常，必须指定异常类型
- 禁止硬编码敏感信息

## 项目结构

```
/root/wangxiao_ai/ai-knowledge-base/
├── .opencode/
│   ├── agents/           # Agent 角色定义与实现
│   │   ├── collector.py    # 采集 Agent
│   │   ├── analyzer.py     # 分析 Agent
│   │   └── organizer.py    # 整理 Agent
│   └── skills/           # 可复用的 Skill 模块
│       ├── fetcher/        # 数据抓取
│       ├── llm_client/     # 大模型客户端
│       └── distributor/    # 分发器
├── knowledge/
│   ├── raw/              # 原始采集数据 (JSON Lines)
│   └── articles/         # 处理后知识条目 (JSON)
├── AGENTS.md             # 本文件
└── requirements.txt      # Python 依赖
```

## 知识条目 JSON 格式

```json
{
  "id": "github-20260524-001",
  "title": "文章或仓库标题",
  "source_url": "https://github.com/owner/repo",
  "source_type": "github|hackernews",
  "summary": "AI 生成的摘要内容",
  "tags": ["LLM", "Agent", "Framework"],
  "status": "archived|draft|published|review",
  "created_at": "2026-04-20T10:30:00+08:00",
  "updated_at": "2026-04-20T10:35:00+08:00",
  "metadata": {
    "stars": 1200,
    "language": "Python",
    "author": "username",
    "hn_score": 45
  },
  "ai_analysis": {
    "relevance_score": 0.92,
    "key_insights": ["要点1", "要点2"],
    "category": "工具框架"
  }
}
```

### 字段说明

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `id` | str | ✓ | 格式 `{source}-YYYYMMDD-NNN`，如 `github-20260524-001` |
| `title` | str | ✓ | 原始标题，保留原语言 |
| `source_url` | str | ✓ | 原始链接 |
| `source_type` | str | ✓ | 来源类型：github/hackernews |
| `summary` | str | ✗ | AI 生成中文摘要，200字内 |
| `tags` | List[str] | ✓ | 自动提取的 AI 相关标签 |
| `status` | str | ✓ | 生命周期状态：archived/draft/published/review |
| `created_at` | str | ✓ | ISO 8601 格式创建时间 |
| `updated_at` | str | ✓ | 最后更新时间 |
| `metadata` | dict | ✗ | 来源特定元数据 |
| `ai_analysis` | dict | ✗ | LLM 分析结果 |

## Agent 角色概览

| 角色 | 职责 | 输入 | 输出 | 触发条件 |
|------|------|------|------|----------|
| **采集 Agent** | 从 GitHub Trending、Hacker News 抓取原始数据 | 定时调度 (Cron) | `knowledge/raw/*.jsonl` | 每日 09:00 / 18:00 |
| **分析 Agent** | 使用 LLM 分析内容，生成摘要和标签 | `knowledge/raw/` 新条目 | 结构化分析结果 | 有新条目时自动触发 |
| **整理 Agent** | 聚合分析结果，生成待发布知识包 | 已分析条目 | Telegram/飞书消息 | 积累 5+ 条或定时触发 |

### 工作流

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│  采集 Agent  │ --> │  分析 Agent  │ --> │  整理 Agent  │
│  (Collector) │     │  (Analyzer) │     │ (Organizer) │
└─────────────┘     └─────────────┘     └─────────────┘
       │                     │                   │
       v                     v                   v
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│  raw/*.jsonl│     │  analyzed/  │     │  Telegram/  │
│             │     │             │     │    飞书     │
└─────────────┘     └─────────────┘     └─────────────┘
```

## 红线 (绝对禁止)

### 代码规范
1. **禁止裸 `print()` 语句** - 必须使用 `logging` 模块
2. **禁止捕获裸异常** - `except:` 必须指定异常类型
3. **禁止 `*args` / `**kwargs` 滥用** - 必须明确参数签名
4. **禁止硬编码敏感信息** - API Key、Token 必须使用环境变量
5. **禁止同步 HTTP 请求** - 必须使用 `aiohttp` 或 `httpx` 异步客户端

### 数据处理
6. **禁止直接修改 raw 目录文件** - 原始数据不可变，分析结果写入新文件
7. **禁止丢失 source_url** - 必须完整保留原始链接用于溯源
8. **禁止跳过 AI 分析直接发布** - 必须经过 LLM 处理环节

### Agent 行为
9. **禁止无限制 API 调用** - 必须实现速率限制 (Rate Limiting)
10. **禁止发布未审核内容** - AI 生成内容必须经过质量检查

### Git 操作
11. **禁止提交大文件** - 单文件 >10MB 必须使用 Git LFS
12. **禁止提交包含密钥的代码** - 使用 `.env.example` 模板而非真实值

---

*文档版本: v1.0*  
*最后更新: 2026-04-20*
