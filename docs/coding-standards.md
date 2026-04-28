# AI 知识库 · 编码规范 v0.1

> 本文档与 `AGENTS.md` 配合使用，定义代码风格和质量标准。

---

## 快速开始

```bash
# 1. 安装开发依赖
pip install -r requirements-dev.txt
npm install

# 2. 配置编辑器（推荐）
# VS Code: 自动读取 .vscode/settings.json

# 3. 验证环境
make lint    # 检查代码风格
make test    # 运行测试
```

---

## 1. Python

### 1.1 格式化
- **工具**: black + isort
- **配置** (`pyproject.toml`):
  ```toml
  [tool.black]
  line-length = 88
  
  [tool.isort]
  profile = "black"
  ```

### 1.2 类型检查
- **工具**: mypy
- **配置**: 严格模式启用

### 1.3 Lint
- **工具**: ruff（替代 flake8，配置兼容）

---

## 2. TypeScript

### 2.1 编译配置
- **strict**: `true`（启用全部严格检查）
- **额外启用**:
  - `noImplicitReturns`
  - `noFallthroughCasesInSwitch`

### 2.2 代码风格
- **工具**: eslint + prettier

---

## 3. 文档规范

### 3.1 范围
所有 `export` 的函数、类、接口，以及模块级常量。

### 3.2 格式
| 语言 | 格式 |
|------|------|
| Python | Google style docstring |
| TypeScript | JSDoc |

### 3.3 必填字段
- 描述（一句话说明用途）
- 参数（Args/@param）
- 返回值（Returns/@returns）

异常说明和示例代码可选。

### 3.4 验证
- CI 自动检查（pydocstyle / eslint-plugin-jsdoc）
- 漏写文档 = CI 失败，阻断合并

---

## 4. 禁止魔法字符串

### 4.1 定义
**魔法字符串** = 业务逻辑中出现的、有语义含义的非自然语言字符串。

**禁止示例**:
```python
# ❌ 禁止
if status == "pending":
    process()

# ✅ 正确
from constants import STATUS_PENDING
if status == STATUS_PENDING:
    process()
```

### 4.2 例外情况（允许保留字面量）
- 本地化文本
- 日志消息
- 单元测试 mock 数据
- 文件路径、URL 片段

### 4.3 常量存放
- 模块级常量放文件顶部
- 多模块共享的放 `constants.py` / `constants.ts`

---

## 5. 禁止 TODO 进主干

### 5.1 禁止范围
- `TODO:`
- `FIXME:`
- `HACK:`
- `XXX:`

允许 `NOTE:` 和 `REVIEW:`。

### 5.2 例外格式
带 issue 引用的 TODO 允许保留：
```python
# ✅ 允许
TODO(#123): 优化查询性能，等数据库迁移完成
```

### 5.3 分支策略
- feature 分支：可以有 TODO
- main 分支：必须清理或转 issue

### 5.4 验证
CI 自动检查：
```bash
git grep -E "(TODO|FIXME|HACK|XXX):" -- "*.py" "*.ts" "*.tsx"
```
匹配即失败，除非符合 `TODO(#\d+):` 格式。

---

## 6. 单测覆盖率

| 指标 | 要求 |
|------|------|
| 全仓库行覆盖 | ≥ 80% |
| 核心模块（agents/、skills/）| ≥ 85% |
| 分支覆盖 | ≥ 70% |

### 6.1 豁免
允许 `# pragma: no cover`，但必须写理由：
```python
# pragma: no cover - 纯配置代码，无业务逻辑
```

### 6.2 验证
- CI 生成覆盖率报告
- 不达标 = 阻断合并

---

## 7. CI 配置

### 7.1 平台
GitHub Actions

### 7.2 触发条件
- 所有 Pull Request
- push 到 main 分支

### 7.3 执行顺序
```
lint (快) → test (慢)
```
lint 失败立即停止，节省 CI 时间。

### 7.4 阻断策略
| 检查项 | 失败处理 |
|--------|----------|
| 代码格式 (black, prettier) | 阻断 |
| Lint (ruff, eslint) | 阻断 |
| 类型检查 (mypy) | 阻断 |
| 文档完整性 | 阻断 |
| TODO 检查 | 阻断 |
| 测试失败 | 阻断 |
| 覆盖率不达标 | 阻断 |

---

## 8. 现有代码迁移

### 8.1 策略
渐进迁移，按 PR 粒度：
- 修改文件时顺手格式化
- 不单独开格式化 PR

### 8.2 git blame 保护
设 `.git-blame-ignore-revs` 文件，大型格式化提交加入忽略列表。

### 8.3 截止
**1 个月内全部合规**，届时移除临时豁免。

### 8.4 跟踪
- GitHub issue 列出待迁移模块
- 站会每周同步进度

---

## 9. Makefile 快捷命令

```makefile
.PHONY: format lint test

format:
	black .
	isort .
	npx prettier --write .

lint:
	black --check .
	isort --check-only .
	ruff check .
	mypy .
	npx eslint .
	./scripts/check_todos.sh
	./scripts/check_docs.sh

test:
	pytest --cov=src --cov-report=term-missing
```

---

## 10. 版本与维护

### 10.1 版本号
简单版本：v0.1、v0.2...
- 新增规则或重大变更时 bump

### 10.2 修改流程
1. 任何人可提 PR 修改
2. 需 1 人 review 通过
3. 合并后更新版本号

---

*文档版本: v0.1*
*生效日期: 待填入*
