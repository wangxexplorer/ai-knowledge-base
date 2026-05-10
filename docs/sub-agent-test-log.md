# Sub-Agent 测试日志

**测试日期**: 2026-04-28
**测试场景**: GitHub Trending AI 领域 Top 10 采集-分析-整理全流程
**测试目标**: 验证 collector / analyzer / organizer 三个业务 Agent 在 oh-my-openagent 框架下的实际执行表现

---

## 一、Collector Agent 测试

### 1. 角色定义执行
| 维度 | 预期 | 实际 | 结果 |
|---|---|---|---|
| 职责 | 从 GitHub API 采集原始数据 | 成功采集 10 个仓库 | 通过 |
| 输入 | 定时调度 / 手动触发 | 手动触发执行 | 通过 |
| 输出 | knowledge/raw/*.json | 输出 github-trending-20260428.json | 通过 |
| 字段完整性 | id/name/stars/language/topics 等 | 所有字段齐全 | 通过 |

### 2. 越权行为
- 无越权行为
- 未修改 raw 目录以外的文件
- 未安装额外依赖（仅使用 urllib）
- 未泄露敏感信息

### 3. 产出质量
| 指标 | 评分 | 说明 |
|---|---|---|
| 数据完整性 | 10/10 | 10 条记录，字段无缺失 |
| 数据准确性 | 9/10 | Stars 数与 GitHub 实时数据一致 |
| 去重能力 | N/A | 首次采集，无重复数据可验证 |
| 错误处理 | 8/10 | 实现了 rate limit 重试，但 fallback 查询未实际触发 |

**产出物**: knowledge/raw/github-trending-20260428.json

### 4. 需要调整的地方
1. **排序维度单一**: 当前按总 stars 排序，未能体现"本周 trending"（新增 stars / 活跃度），建议增加 pushed:>2026-04-21 时间窗口筛选
2. **API 限制**: 无 token 时 rate limit 60/hour，生产环境必须配置 GITHUB_TOKEN
3. **数据源单一**: 仅依赖 GitHub Search API，建议增加 GitHub Trending 页面 scraping 作为交叉验证
4. **元数据缺失**: 未采集 readme 前 500 字，导致 analyzer 只能基于 description 分析

---

## 二、Analyzer Agent 测试

### 1. 角色定义执行
| 维度 | 预期 | 实际 | 结果 |
|---|---|---|---|
| 职责 | 读取 raw 数据，生成摘要/亮点/评分 | 未由独立 subagent 执行 | **失败** |
| 输出格式 | 标准知识条目 JSON | 最终格式正确 | 通过（人工兜底） |
| 分析维度 | 创新性/活跃度/相关性/实用性/成熟度 | 五个维度均有覆盖 | 通过（人工兜底） |

**关键问题**: 连续 10 次 task(subagent_type="Sisyphus-Junior") 调用全部参数校验失败，错误信息为 "A parameter specified in the request is not valid"。最终由主 Agent（Sisyphus）直接在对话中完成全部 10 条分析。

**根本原因**: 
- task 工具的 subagent_type 枚举值仅支持: explore, librarian, oracle, metis, momus
- 业务角色名（analyzer, collector, organizer）不是合法的 subagent_type
- 正确的做法应使用 category="deep" + prompt 中定义角色，而非 subagent_type

### 2. 越权行为
- 未发生（未成功启动）

### 3. 产出质量（人工兜底结果）
| 指标 | 评分 | 说明 |
|---|---|---|
| 摘要质量 | 9/10 | 中文摘要 150-200 字，准确概括项目核心价值 |
| 标签提取 | 8/10 | 从 topics + description 提取，相关性高，但部分标签偏技术栈而非业务场景 |
| 评分合理性 | 9/10 | 1-10 分 + 50-100 字理由，逻辑自洽，拉开梯度（7-10 分） |
| 分类准确性 | 8/10 | 五分类（基础设施/应用平台/Agent框架/工具框架/效率工具/开发范式/社区资源）基本合理 |
| 格式合规性 | 10/10 | 严格遵循知识条目 JSON Schema，字段类型正确 |

**产出物**: knowledge/articles/github-trending-20260428-analyzed.json

### 4. 需要调整的地方
1. **致命: subagent 无法启动**: 必须修正调用方式，将 subagent_type="Sisyphus-Junior" 改为 category="deep"
2. **评分标准未量化**: 当前评分依赖主观判断，建议制定量化 rubric（如 stars>100k +1 分，近期更新 +1 分，MCP支持 +1 分等）
3. **缺少交叉验证**: 未通过 webfetch 获取 README 补充信息，分析深度受限于 description 字数
4. **一致性风险**: 人工分析 10 条时，评分尺度可能有漂移，建议增加 calibration 步骤

---

## 三、Organizer Agent 测试

### 1. 角色定义执行
| 维度 | 预期 | 实际 | 结果 |
|---|---|---|---|
| 职责 | 聚合分析结果，拆分为单文件，去重入库 | 部分执行 | 部分通过 |
| 输入 | analyzed 条目数组 | 读取 github-trending-20260428-analyzed.json | 通过 |
| 输出 | knowledge/articles/*.json（单文件） | 生成 10 个独立 JSON 文件 | 通过 |
| 去重逻辑 | 基于 source_url 去重 | 实现了去重检查（首次入库，未命中重复） | 通过 |
| 命名规范 | YYYYMMDD-source-slug.json | 格式正确 | 通过 |
| 状态转换 | analyzed -> published | 已转换 | 通过 |

**注意**: Organizer 未作为独立 subagent 启动，由主 Agent 直接执行 Python 脚本完成。

### 2. 越权行为
- 无越权行为
- 未删除已有文件
- 未修改 raw 目录原始数据

### 3. 产出质量
| 指标 | 评分 | 说明 |
|---|---|---|
| 文件命名 | 10/10 | 统一格式: 20260428-github-{slug}.json |
| UUID 生成 | 10/10 | 每个条目独立 UUID v4 |
| 去重逻辑 | 9/10 | 基于 source_url 检查，逻辑正确，但缺少 hash 校验 |
| 数据完整性 | 10/10 | 单文件包含全部必需字段 |
| 编码格式 | 10/10 | UTF-8 + ensure_ascii=False |

**产出物**: knowledge/articles/20260428-github-*.json (10 files)

### 4. 需要调整的地方
1. **未独立执行**: 与 analyzer 相同，organizer 未作为独立 subagent 运行，而是主 Agent 直接操作
2. **去重策略单一**: 仅依赖 source_url，建议增加内容 hash（如 description + summary 的 md5）作为辅助去重键
3. **缺少归档机制**: 重复条目仅跳过，未记录到 duplicate.log，无法追溯
4. **文件名冲突**: 如果同一天同一 source 有多个更新，slug 相同会覆盖，建议增加时间戳或版本号
5. **状态机不完整**: 当前只有 analyzed -> published，缺少 archived / rejected 等终态流转

---

## 四、框架层问题汇总

### 1. Subagent 调用规范混乱
| 错误写法 | 正确写法 | 说明 |
|---|---|---|
| task(subagent_type="analyzer", ...) | task(category="deep", prompt="你扮演 analyzer...") | analyzer 不是合法 subagent_type |
| task(subagent_type="Sisyphus-Junior", ...) | task(category="deep", ...) | Sisyphus-Junior 不是合法 subagent_type |
| task(subagent_type="collector", ...) | task(category="deep", prompt="你扮演 collector...") | collector 不是合法 subagent_type |

**合法 subagent_type 白名单**: explore, librarian, oracle, metis, momus
**合法 category 列表**: deep, visual-engineering, quick, unspecified-high, unspecified-low, writing, artistry, ultrabrain, default

### 2. 业务角色 vs 系统角色的映射缺失
- AGENTS.md 中定义了 collector / analyzer / organizer 三个业务角色
- 但 oh-my-openagent 框架没有注册这些角色为可调用的 subagent
- 建议: 在 AGENTS.md 中补充"如何调用"章节，明确每个业务角色对应的 category + prompt 模板

### 3. 错误信息不友好
- 参数错误返回: "A parameter specified in the request is not valid"
- 未提示具体哪个参数、允许的值是什么
- 建议: 框架层增加参数校验的详细错误提示

---

## 五、总体评估

| Agent | 角色执行 | 越权行为 | 产出质量 | 可用性 |
|---|---|---|---|---|
| Collector | 通过 | 无 | 8.5/10 | 可用（需优化 trending 逻辑） |
| Analyzer | **失败** | 未启动 | 9/10（人工兜底） | **不可用**（subagent 无法启动） |
| Organizer | 部分通过 | 无 | 9.5/10 | 可用（但未独立执行） |

**关键阻塞项**: Analyzer 无法通过 task 工具启动，必须修正调用参数（category 替代 subagent_type）。

---

## 六、下一步行动

1. **高优先级**: 修正 analyzer 的调用方式，编写标准 prompt 模板并放入 .opencode/agents/analyzer.py
2. **中优先级**: 为 collector 增加 GITHUB_TOKEN 支持和 trending 时间窗口筛选
3. **中优先级**: 为 organizer 增加内容 hash 去重和 duplicate.log
4. **低优先级**: 制定 analyzer 评分量化 rubric，减少主观性

---

*日志创建者: Sisyphus (主 Agent)*
*测试范围: 采集-分析-整理全流程*
*环境: oh-my-openagent + opencode*