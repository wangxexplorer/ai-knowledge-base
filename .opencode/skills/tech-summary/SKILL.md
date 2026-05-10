---
name: tech-summary
description: Use when you need to perform deep analysis and summarization on collected technical content from GitHub or Hacker News
description-zh: 当需要对采集的技术内容进行深度分析总结时使用此技能
allowed-tools:
  - read
  - grep
  - glob
  - webfetch
---

# 技术内容深度分析总结技能

## 使用场景

- 对 `knowledge/raw/` 中采集的 GitHub/Hacker News 数据进行批量深度分析
- 为每个项目生成精炼摘要、技术亮点和评分
- 发现技术趋势和共同主题，为整理 Agent 提供高质量输入

## 执行步骤

### 步骤 1：读取最新采集文件

使用 `glob` 查找 `knowledge/raw/` 目录下最新的采集文件：
- 文件名模式：`github-trending-YYYY-MM-DD.json` 或 `hackernews-YYYY-MM-DD.json`
- 读取文件内容，获取 `items` 数组
- 确认条目数量（通常 10-15 条）

### 步骤 2：逐条深度分析

对 `items` 数组中的每个项目进行分析，提取以下维度：

#### 2.1 精炼摘要（<=50 字）
- 用一句话概括项目核心功能和价值
- 禁止照抄原始 description，必须重新组织语言

#### 2.2 技术亮点（2-3 个）
- 每个亮点必须附带**具体事实或数据**
- 示例格式：
  - "采用 Rust 重写，性能比 Python 版本提升 10 倍（benchmark 数据）"
  - "支持 20+ 种 LLM 后端，包括本地模型和云端 API"
  - "3 天内获得 5k stars，社区贡献者超过 50 人"

#### 2.3 评分（1-10 分，附理由）
- 评分必须附带 1-2 句理由，说明评分依据
- 理由要具体，不能写"看起来不错"等模糊表述

#### 2.4 标签建议
- 从项目 topics/language/description 中提取 3-5 个最相关标签
- 标签应覆盖：**技术领域**（如 LLM, Agent, RAG）、**应用场景**（如 ChatBot, DevTool）、**技术栈**（如 Python, Rust）

### 步骤 3：趋势发现

在完成所有条目分析后，进行横向对比：

#### 3.1 共同主题
- 识别 15 个项目中出现 3 次以上的共同技术方向
- 示例："本月多个项目聚焦 LLM 本地部署优化"

#### 3.2 新概念
- 标记首次出现或近期兴起的新技术概念
- 示例："MCP (Model Context Protocol) 首次在 Trending 中出现"

#### 3.3 输出结构
将趋势发现写入分析结果的 `trends` 字段。

### 步骤 4：输出分析结果 JSON

写入路径：`knowledge/analyzed/tech-summary-YYYY-MM-DD.json`

## 评分标准

| 分数段 | 含义 | 判定标准 |
|--------|------|----------|
| **9-10 分** | 改变格局 | 开创全新范式、解决行业痛点、社区爆发式增长、被大厂采纳 |
| **7-8 分** | 直接有帮助 | 解决具体问题、可直接用于生产、文档完善、生态成熟 |
| **5-6 分** | 值得了解 | 有创新点但尚不成熟、特定场景有用、需要观察后续发展 |
| **1-4 分** | 可略过 | 重复造轮子、文档缺失、社区冷清、无明显技术亮点 |

## 约束条件

1. **高分限制**：15 个项目中，评分 9-10 分的项目**不超过 2 个**
   - 理由：9-10 分代表"改变格局"级别，标准极高，必须严格控制数量
   - 如果候选超过 2 个，仅保留 star 增长最快、社区反响最强烈的 2 个

2. **摘要长度**：每条摘要严格 <= 50 个中文字符（不含标点）

3. **事实依据**：技术亮点必须有具体数据、版本号、benchmark、star 数等事实支撑，禁止主观臆断

4. **标签规范**：
   - 禁止纯语言标签（如 `python`, `rust`）单独出现，必须与领域标签组合
   - 禁止标签超过 5 个

## 输出格式

```json
{
  "source": "tech-summary",
  "skill": "tech-summary",
  "analyzed_at": "2026-05-10T10:30:00+08:00",
  "input_file": "knowledge/raw/github-trending-2026-05-10.json",
  "item_count": 15,
  "items": [
    {
      "name": "owner/repo-name",
      "url": "https://github.com/owner/repo-name",
      "summary": "不超过50字的精炼摘要",
      "highlights": [
        "亮点1：具体事实支撑",
        "亮点2：具体事实支撑"
      ],
      "score": 8,
      "score_reason": "解决具体痛点，可直接用于生产，文档完善",
      "tags": ["LLM", "Agent", "DevTool"],
      "language": "Python",
      "stars": 12345
    }
  ],
  "trends": {
    "common_themes": [
      "共同主题1：描述",
      "共同主题2：描述"
    ],
    "emerging_concepts": [
      "新概念1：描述",
      "新概念2：描述"
    ]
  }
}
```

### 字段说明

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `source` | str | ✓ | 分析来源标识 |
| `skill` | str | ✓ | 使用的技能名称 |
| `analyzed_at` | str | ✓ | 分析时间（ISO 8601，北京时间） |
| `input_file` | str | ✓ | 输入的原始采集文件路径 |
| `item_count` | int | ✓ | 分析条目总数 |
| `items` | list | ✓ | 分析结果数组 |
| `items[].name` | str | ✓ | 仓库/文章名称 |
| `items[].url` | str | ✓ | 原始链接 |
| `items[].summary` | str | ✓ | 精炼摘要（<=50字） |
| `items[].highlights` | list | ✓ | 技术亮点数组（2-3项） |
| `items[].score` | int | ✓ | 评分（1-10） |
| `items[].score_reason` | str | ✓ | 评分理由 |
| `items[].tags` | list | ✓ | 标签数组（3-5个） |
| `items[].language` | str | ✗ | 主要编程语言 |
| `items[].stars` | int | ✗ | Star 数量 |
| `trends` | dict | ✓ | 趋势发现 |
| `trends.common_themes` | list | ✓ | 共同主题数组 |
| `trends.emerging_concepts` | list | ✓ | 新概念数组 |

## 注意事项

1. **严格评分**：9-10 分项目不超过 2 个是硬性约束，不得突破
2. **事实优先**：所有亮点必须有数据支撑，禁止"我觉得"、"看起来"等主观表述
3. **时间一致**：`analyzed_at` 和输出文件名日期保持一致，使用北京时间
4. **禁止修改**：不得修改 `knowledge/raw/` 原始文件，分析结果写入新文件
5. **异常处理**：如果输入文件格式异常或条目为空，记录 error 并输出空 items 数组
