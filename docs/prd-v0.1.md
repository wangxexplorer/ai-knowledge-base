# AI 知识库 · 三 Agent PRD v0.1

## 总流程

每天 UTC 0:00 触发 · collector → analyzer → organizer · 串行。

## Agent 职责

- **collector**：抓 GitHub Trending Top 50 · 过滤 AI 相关 · 存 `knowledge/raw/`
- **analyzer**：读 `raw/` · 给每条打 3 维度标签 · 存 `knowledge/analyzed/`
- **organizer**：读 `analyzed/` · 整理成标准 JSON 条目 · 存 `knowledge/articles/`

## 数据流

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│  Collector  │ --> │  Analyzer   │ --> │  Organizer  │
└─────────────┘     └─────────────┘     └─────────────┘
       │                     │                   │
       v                     v                   v
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│  raw/*.jsonl│     │ analyzed/*. │     │articles/*.  │
│             │     │ jsonl       │     │json         │
└─────────────┘     └─────────────┘     └─────────────┘
```

## 数据传递方式

文件系统（按日期分文件）。

## 输出格式

Organizer 最终输出为标准 JSON 文件（单条知识条目），命名规范：
`{date}-{source}-{slug}.json`

详见 `AGENTS.md` 知识条目 JSON 格式定义。

## 开放问题（已细化成 issue）

- 上游失败下游怎么办？→ Slice 3
- 数据怎么传？→ 文件系统
- 重跑策略？→ 幂等覆盖
- 进度追踪？→ 状态文件
