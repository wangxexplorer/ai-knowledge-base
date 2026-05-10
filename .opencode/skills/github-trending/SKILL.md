---
name: github-trending
description: Use when you need to collect popular GitHub open-source projects related to AI/LLM/Agent fields
description-zh: 当需要采集github热门开源项目时候使用此技能
allowed-tools:
  - read
  - grep
  - glob
  - webfetch
---

# GitHub Trending 采集技能

## 使用场景

- 每日定时采集 GitHub Trending 热门仓库
- 筛选 AI / LLM / Agent 相关项目
- 生成结构化知识条目供后续分析使用

## 执行步骤

### 步骤 1：搜索热门仓库（GitHub API）

使用 GitHub API 获取 Trending 数据：
- 端点：`https://api.github.com/search/repositories`
- 参数：`q=stars:>100 pushed:>2024-01-01 sort:stars`
- 采集近 7 天内活跃且 Stars 增长较快的仓库
- 可选：直接抓取 `https://github.com/trending` 页面补充数据

### 步骤 2：提取信息

从每个仓库提取以下字段：
- `name`: 仓库全名（owner/repo）
- `url`: 仓库链接
- `stars`: Star 数量
- `language`: 主要编程语言
- `topics`: 仓库标签数组
- `description`: 原始英文描述
- `pushed_at`: 最后更新时间

### 步骤 3：过滤

**纳入条件（满足任一）：**
- topics 包含 `ai`, `llm`, `agent`, `artificial-intelligence`, `machine-learning`, `gpt`, `chatgpt`, `openai`, `langchain`, `rag`
- description 包含 AI/LLM/Agent 相关关键词
- language 为 Python/TypeScript/Rust 且 stars > 500

**排除条件（满足任一）：**
- 仓库名包含 `awesome-` 或 `Awesome-`
- topics 包含 `awesome-list`
- 纯资源列表无实质代码
- 已归档仓库（archived）

### 步骤 4：去重

- 按 `name` 字段去重，保留信息最全的记录
- 检查 URL 是否已在 `knowledge/raw/` 历史文件中存在
- 已存在条目更新 `stars` 和 `updated_at`，不重复添加

### 步骤 5：撰写中文摘要

使用公式：**项目名 + 做什么 + 为什么值得关注**

示例：
```
「OpenManus」是一个开源的通用 AI Agent 框架，支持工具调用和任务自主规划，
值得关注因为它在 3 天内获得 10k+ stars，社区活跃度极高。
```

摘要要求：
- 字数：50-150 字
- 语言：中文
- 重点：突出技术亮点和应用价值

### 步骤 6：排序 TOP15

按以下优先级排序：
1. `stars` 数量（降序）
2. 近 7 天 star 增长数（降序）
3. 与 AI/Agent 的相关性评分

取前 15 条作为最终输出。

### 步骤 7：输出 JSON 文件

写入路径：`knowledge/raw/github-trending-YYYY-MM-DD.json`

## 输出格式

```json
{
  "source": "github-trending",
  "skill": "github-trending",
  "collected_at": "2026-05-10T09:00:00+08:00",
  "items": [
    {
      "name": "owner/repo-name",
      "url": "https://github.com/owner/repo-name",
      "summary": "中文摘要内容...",
      "stars": 12345,
      "language": "Python",
      "topics": ["ai", "agent", "llm"]
    }
  ]
}
```

### 字段说明

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `source` | str | ✓ | 数据来源标识 |
| `skill` | str | ✓ | 使用的技能名称 |
| `collected_at` | str | ✓ | 采集时间（ISO 8601） |
| `items` | list | ✓ | 仓库条目数组 |
| `items[].name` | str | ✓ | 仓库全名 |
| `items[].url` | str | ✓ | 仓库链接 |
| `items[].summary` | str | ✓ | 中文摘要 |
| `items[].stars` | int | ✓ | Star 数量 |
| `items[].language` | str | ✗ | 主要语言 |
| `items[].topics` | list | ✗ | 标签数组 |

## 注意事项

1. **API 限制**：GitHub API 有速率限制（未认证 60/hr，认证 5000/hr），建议配置 `GITHUB_TOKEN` 环境变量
2. **数据日期**：文件名和 `collected_at` 使用北京时间（+08:00）
3. **错误处理**：API 失败时重试 3 次，间隔 5 秒；仍失败则记录 error 日志并跳过
4. **禁止修改**：不得直接修改已存在的 `knowledge/raw/*.json` 文件，新数据写入新文件
5. **隐私合规**：不采集私有仓库，仅使用公开 API 数据
