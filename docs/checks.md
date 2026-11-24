# 检查项说明

工具按“本地能跑、不需要账号、结果可解析”选取。需要线上服务或账号的（Snyk、Sentry、Datadog、BrowserStack 等）不纳入，
报告里会逐项写明没有使用的原因。

## 01 Bug

**工具**：Ruff（F / B / PLE / PLW / ASYNC / RUF）、Pylint（E 类）、Clippy

未定义的名字、可变默认参数、吞掉的异常、`unwrap` / `panic`、有损类型转换。

## 02 安全

**工具**：Semgrep（secrets / python / rust 规则包）、Ruff S 规则（即 Bandit 规则集）

密钥泄漏、注入、不安全的反序列化和子进程调用。命中密钥时报告里不显示内容。

## 03 性能

**工具**：Ruff PERF / C4、Clippy perf 组、基准命令实测

静态找低效写法；local 模式下用 profile 里的 `bench` 命令实测修复前后的耗时。

## 04 瘦身

**工具**：Vulture、jscpd、Ruff F401 / F841 / ERA、requirements ↔ import 对照

死代码、重复代码、未使用的导入和依赖、体积统计。

## 05 架构

**工具**：自带的 import 图分析、cargo metadata（local 模式）

循环依赖、分层规则、扇入扇出、超大文件。输出 Graphviz 的 DOT 图。

## 06 质量

**工具**：Ruff（C901 / N / SIM / UP / PLR / RET）、Pylint、Clippy

复杂度、命名、可简化的写法。

## 07 测试

**工具**：Coverage.py（行 + 分支）、cargo test、cargo-llvm-cov（装了才用）

**只在 local 模式运行**。先跑基线测试，再列出覆盖率低的文件。static 模式只清点测试函数，不评分。

## 08 数据库

**工具**：自带的 SQL 静态检查、SQLite `EXPLAIN QUERY PLAN`

检查代码里的 SQL 字符串和独立的 `.sql` 文件，自动识别 SQLite / MySQL / PostgreSQL 方言。

| 分组 | 规则 |
|---|---|
| 通用 | `SELECT *`、无 WHERE 的 UPDATE / DELETE、前置通配符 LIKE、过滤列上套函数、`NOT IN` 子查询、随机排序、拼接 SQL |
| 索引 | 表没有主键、重复索引、冗余索引（是另一个的前缀）、索引过多、过滤列没有索引 |
| JOIN | 缺连接条件、连接列没有索引、两侧类型不一致、连接过多 |
| MySQL | `utf8`（实为 utf8mb3）、MyISAM 引擎 |
| PostgreSQL | 外键列没有索引、不带时区的时间戳、迁移里建索引没有 `CONCURRENTLY` |

**限制**：执行计划只对 SQLite 做。MySQL / PostgreSQL 需要连接服务器，不做，只有静态规则。

## 09 依赖

**工具**：OSV.dev、pip-audit、pip / cargo 过期检查、许可证清点、npm audit

需要联网。

- static：直接查 OSV.dev —— `Cargo.lock` 和 requirements 里 `==` 锁定的条目。未锁定的计为“未核查”。
- local：另加 pip-audit、过期版本、许可证、npm audit。

## 10 兼容性

**工具**：自带检查、Ruff target-version

行尾、shebang、平台专有模块、文件名可移植性。有前端时的浏览器兼容性需要另用 Playwright 等工具。

## 11 UI/UX

**工具**：自带的 HTML 结构检查、Lighthouse（配置了 `ui.urls` 才运行；它的无障碍部分就是 axe-core）

| 规则 | 说明 |
|---|---|
| `input-label` | 表单控件要有可访问名称：`<label for>`、被 `<label>` 包裹、`aria-label`、有效的 `aria-labelledby`。**只有 `id` 不算** |
| `button-name` / `link-name` | 按钮和链接要有文字或 `aria-label` |
| `aria-labelledby-invalid` / `label-for-missing` | 引用的 id 不存在或没有文字 |
| `duplicate-id` | id 重复 |
| `fieldset-legend` | 成组的单选 / 复选框要有 `<fieldset>` 和 `<legend>` |
| `img-alt` / `iframe-title` | 图片和内嵌框架的替代文字 |
| `html-lang` / `document-title` / `viewport` | 页面语言、标题、视口 |
| `positive-tabindex` | 正数的 `tabindex` 会打乱焦点顺序 |

**限制**：本地 HTML 只做结构检查，不看渲染结果，不解析 JSX / Vue / Svelte。属性值里有模板语法（`{{ }}`、`{% %}`）的不报。
完整的 axe 规则需要配置 `ui.urls`。没有前端的项目自动判为不适用。

## 12 运行环境

**工具**：systemd 单元检查、shell 严格模式、ShellCheck（装了才用）、本地日志统计

日志只计数，内容不写进报告。

---

## 评分

每类得分：

```
100 · exp(−加权问题密度 / 10)
```

- **权重**：严重 25、高 8、中 2、低 0.3
- **密度**：按千行代码归一（数据库按语句数、依赖按依赖数、运行环境按单元数）
- **封顶**：有“严重”问题的类别不超过 59，有“高”的不超过 84
- **07 类**以覆盖率为主，85% 记满分

综合分是各适用类别的加权平均：

| 类别 | 权重 | 类别 | 权重 |
|---|---|---|---|
| Bug | 20 | 架构 | 8 |
| 安全 | 20 | 瘦身 | 5 |
| 性能 | 10 | UI/UX | 5 |
| 质量 | 10 | 数据库 | 4 |
| 测试 | 10 | 运行环境 | 3 |
| 依赖 | 10 | 兼容性 | 2 |

只要还有“严重”问题，综合分不超过 79。

分数用来看趋势，不是合格线。先处理报告里“优先处理的问题”。

## 自动修复的边界

补丁**只包含** Ruff 标记为安全的修复：未使用的导入、多余的 f 前缀、可简化的表达式等。

- local 模式：应用后在副本里重跑测试，报告结果。
- static 模式：只做语法校验，报告里会写明补丁没有经过测试验证。

以下只给建议和示例代码，需要人看过再动：死代码删除、重复代码合并、Rust 侧的修改、依赖升级。
