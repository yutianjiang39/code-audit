# 代码综合审查系统（code-audit）

[![tests](https://github.com/yutianjiang39/code-audit/actions/workflows/tests.yml/badge.svg)](https://github.com/yutianjiang39/code-audit/actions/workflows/tests.yml)

一套自成一体的审查工具：把 12 类检查（Bug、安全、性能、瘦身、架构、质量、测试覆盖率、数据库、依赖、兼容性、UI/UX、运行环境）
跑一遍，汇总成一份带评分的报告。

- **不碰被审查的项目**：先把项目复制到本目录的 `.work/` 副本，所有工具都在副本上运行；修复以补丁文件交付，由你决定是否应用。
- **默认不执行项目代码**：默认是静态模式，只分析不运行；要跑测试、Clippy、基准需要显式 `--mode local`（见下面“执行模式与信任边界”）。
- **不读密钥**：密钥与凭据文件、符号链接永远不复制、不扫描；报告和原始输出里形似密钥的内容会被替换为 `[已脱敏]`。
- **可搬走**：整个文件夹拷到别处即可，每个项目一份 `profiles/<名字>.json`。

## 使用

```bash
python audit.py setup                        # 只需一次：建 .venv 并安装工具
python audit.py run profiles/myproj.json     # 静态审查（默认），不执行项目代码
python audit.py run profiles/myproj.json --mode local     # 另外运行测试 / 覆盖率 / Clippy / 基准，只用于你信任的代码
python audit.py run profiles/myproj.json --only 01,02     # 只跑某几类（不做修复阶段）
python audit.py run profiles/myproj.json --mode local --skip-rust-tests
```

## 执行模式与信任边界

把项目复制到 `.work/` 只隔离了**文件**（原项目不会被改），不是操作系统级的安全沙箱。所以分两种模式：

| | `static`（默认） | `local` |
|---|---|---|
| 执行项目代码 | 不执行：不启动项目的解释器，不跑 cargo、测试、基准、npm 脚本 | 执行：测试与覆盖率、`cargo clippy` / `cargo test`（构建脚本和过程宏会运行）、bench 命令 |
| 07 测试 | 不评分（只清点测试函数） | 覆盖率、测试结果 |
| Rust 的 01 / 03 / 06 | 没有 Clippy 结果 | 有 |
| 09 依赖 | 直接查 OSV.dev：`Cargo.lock`、requirements 里 `==` 锁定的条目；未锁定的计为“未核查” | 另外有 pip-audit、过期版本、许可证、npm audit |
| 修复补丁 | 只做语法校验和 `git apply --check`，**没有经过测试验证** | 在副本里重跑测试 |

`local` 模式的边界要说清楚：

- 项目代码以**当前用户的权限**运行，能读写这个用户能访问的任何文件，也能联网。只对你信任的代码使用。
- 子进程拿到的是白名单环境变量（`PATH`、临时目录、用户目录等），不继承你 shell 里的令牌和密钥；需要额外变量时写进 profile 的
  `execution.env_passthrough`。这只是防止凭据经环境变量泄漏，**不等于隔离**。
- 代理变量只传给需要联网的工具（Semgrep、pip-audit、npx、cargo update）。不传代理变量**不等于断网**。
- 容器隔离（Docker / Podman）目前没有提供。要审查不信任的代码，请用静态模式，或者自己把整个工具放进一次性的虚拟机 / 容器里运行。

两种模式都生效的保护：

- 不跟随链接：符号链接和 Windows 目录联接不进文件清单、不复制；每个文件的真实路径必须在项目根目录之内。
- 敏感文件按名字排除（大小写不敏感）：`secrets/`、`.ssh/`、`.aws/`、`.azure/`、`.gcloud/`、`.gnupg/`、`.kube/`、`.docker/`、`.terraform/`，
  `.env*`、`*.key`、`*.pem`、`*.p12`、`*.pfx`、`*.ppk`、`*.jks`、`*.keystore`、`*.kdbx`、`*.ovpn`、`id_rsa*` 等 SSH 私钥、
  `.netrc`、`.npmrc`、`.pypirc`、`.git-credentials`、`.htpasswd`、`credentials`、`credentials.json`、`service-account*.json`、
  `*.tfstate`、`*.tfvars`、`kubeconfig`；另外，文件开头含 `-----BEGIN … PRIVATE KEY-----` 的一律排除。报告概览里只显示排除了多少个。
- 脱敏：写进 `report.*`、`findings.json`、`run.log`、`raw/` 的文本都会过一遍脱敏（PEM 私钥块、AWS / GitHub / Slack / `sk-` 类密钥、JWT、
  Telegram bot token、带口令的 URL、`password = …` 一类赋值）。脱敏靠模式匹配，不保证覆盖所有格式，报告外发前仍请自己看一遍。
  **`fixes.patch` 不脱敏**（改了就无法应用），它包含被修改文件的上下文行。
- git 调用禁用了仓库自带的 fsmonitor 和 hooks；工具用隔离参数运行，不读项目里的 ruff / pylint 配置和插件。

结果在 `reports/<项目>/<时间>/`：

| 文件 | 内容 |
|---|---|
| `report.html` | 完整报告，问题清单可按类别 / 级别筛选和搜索，点开看原因和修复代码 |
| `report.md` | 同一份报告的可通读版本（每类列出最严重的若干条） |
| `findings.json` | 全部问题和指标，供脚本或 CI 使用 |
| `fixes.patch` | 自动修复的补丁，在项目根目录 `git apply` |
| `architecture_*.dot` | 模块 / crate 依赖图（Graphviz） |
| `raw/` | 各工具的原始输出、测试日志 |

每次扫描都包含这十项：① 审查项目与文件位置 ② 问题与行号 ③ 严重程度（严重 / 高 / 中 / 低）④ 原因 ⑤ 修复建议
⑥ 可执行的修复代码 ⑦ 修复前后体积对比 ⑧ 修复前后性能对比 ⑨ 修复后的自动化测试结果 ⑩ 综合评分。

## 用到别的项目上

```bash
python audit.py init D:\path\to\project --name myproj   # 自动探测语言，生成 profiles/myproj.json
# 打开 profile，核对 test_cmd、exclude，然后
python audit.py run profiles/myproj.json
```

要把整套系统挪到另一台机器：拷走这个文件夹（`.venv/`、`.work/`、`.tools/`、`reports/` 不用拷），在新机器上 `python audit.py setup`。

## 12 类各用什么工具

从常见工具清单里，按“本地能跑、不需要账号、结果可解析”选取；需要线上服务或账号的（Snyk、Sentry、Datadog、BrowserStack 等）
不纳入，报告里会逐项写明“未使用”的原因。

| 类别 | 工具 | 说明 |
|---|---|---|
| 01 Bug | Ruff（F / B / PLE / PLW / ASYNC / RUF）、Pylint（E 类）、Clippy | 未定义名字、可变默认参数、吞异常、unwrap / panic、有损转换 |
| 02 安全 | Semgrep（secrets / python / rust 规则包）、Ruff S 规则（Bandit 规则集） | 密钥泄漏、注入、不安全的反序列化与子进程调用 |
| 03 性能 | Ruff PERF / C4、Clippy perf 组、基准命令实测 | 静态找低效写法；修复前后用 profile 里的 bench 命令实测 |
| 04 瘦身 | Vulture、jscpd、Ruff F401 / F841 / ERA、requirements ↔ import 对照 | 死代码、重复代码、未使用的导入和依赖、体积统计 |
| 05 架构 | 自带的 import 图分析、cargo metadata | 循环依赖、分层规则、扇入扇出、超大文件，输出 DOT 图 |
| 06 质量 | Ruff（C901 / N / SIM / UP / PLR / RET）、Pylint、Clippy | 复杂度、命名、可简化写法 |
| 07 测试 | Coverage.py（行 + 分支）、cargo test、cargo-llvm-cov（装了才用） | 只在 local 模式运行：先跑基线测试，再给出低覆盖文件 |
| 08 数据库 | 自带的 SQL 静态检查（代码里的 SQL 字符串 + 独立 `.sql` 文件）、SQLite EXPLAIN QUERY PLAN | 识别 SQLite / MySQL / PostgreSQL 方言；索引合理性（无主键、重复 / 冗余索引、过滤列和外键列没有索引）、JOIN（缺条件、连接列无索引、类型不一致、连接过多）、无 WHERE 的 UPDATE / DELETE、SELECT *、拼接 SQL。执行计划只对 SQLite 做；MySQL / PostgreSQL 需要连服务器，不做，只有静态规则 |
| 09 依赖 | OSV.dev（Cargo.lock、锁定版本的 requirements）、pip-audit、pip / cargo 过期检查、许可证清点、npm audit | 需要联网；后四项只在 local 模式 |
| 10 兼容性 | 行尾 / shebang、平台专有模块、文件名可移植性、Ruff target-version | 有前端时浏览器兼容性另需 Playwright 等 |
| 11 UI/UX | 自带的 HTML 结构检查（标准库 HTML 解析器）、Lighthouse（无障碍部分是 axe-core；配置了 `ui.urls` 才跑） | 表单控件的可访问名称（`<label for>`、包裹的 label、`aria-label`、有效的 `aria-labelledby`；只有 `id` 不算）、按钮 / 链接名称、重复 id、失效的引用、图片 alt、页面语言和标题。本地 HTML 只是结构检查，不解析 JSX / Vue / Svelte，也不看渲染结果；完整的 axe 规则需要配置 `ui.urls`。没有前端的项目自动判为不适用 |
| 12 运行环境 | systemd 单元检查、shell 严格模式、ShellCheck（装了才用）、本地日志统计 | 只计数，不把日志内容写进报告 |

## profile 字段

```jsonc
{
  "name": "myproj",
  "target": "../MyProj",                  // 相对本目录或绝对路径
  "execution": {
    "mode": "static",                     // static（默认）或 local；命令行 --mode 优先
    "env_passthrough": []                 // local 模式下额外传给子进程的环境变量名
  },
  "exclude": ["vendor/*"],                // 不纳入审查的路径（glob）
  "test_globs": ["tests/*"],              // 测试代码：放宽部分规则，不计入覆盖率，Semgrep / jscpd 的命中不计
  "python": {
    "interpreter": "{target}/.venv/Scripts/python.exe",   // 项目自己的解释器，用来跑测试
    "min_version": "3.11",
    "test_cmd": ["-m", "unittest", "discover", "-s", "tests", "-t", "."],
    "pylint": true
  },
  "rust": { "manifest_dirs": ["rust"], "run_tests": true },
  "architecture": {
    "allowed_imports": { "app": ["app"], "ops": ["ops", "app"] },   // 每层允许依赖谁
    "crate_rank": { "core-types": 0, "core": 1 },                    // crate 只能依赖层级更低的
    "max_file_lines": 1200, "max_fan_out": 15
  },
  "slim":     { "import_names": { "some-dist-name": "import_name" } },   // 可选：包名与 import 名不同的依赖
  "database": {
    "dialect": "auto",                    // auto（按语句特征判断）| sqlite | mysql | postgresql
    "max_indexes": 6, "max_joins": 6,     // 单表索引数、单条语句 JOIN 数的提醒阈值
    "sqlite_globs": ["data/*.sqlite3"]    // 可选：对现有库做只读体检
  },
  "runtime":  { "log_globs": ["logs/*.log"] },             // 可选：统计日志里的错误条数
  "ui":       { "urls": ["http://localhost:3000"] },       // 可选：Lighthouse
  "bench": [ { "name": "启动耗时", "cmd": ["{python}", "-c", "import app"], "repeat": 7 } ],
  "accepted": [ { "rule": "no-restart", "file": "ops/*.service", "reason": "为什么可以接受" } ],
  "severity_overrides": { "S603": "low" },
  "score_weights": { "02": 30 }
}
```

`accepted` 里登记的命中不计分，但会在报告末尾单独列出并带上理由，方便复核。

仓库里只带一份 `profiles/example.json`；你自己的 profile 和 `reports/` 会写出被审查项目的路径、文件名和代码片段，
已在 `.gitignore` 里排除，不要提交到公开仓库。

## 评分

每类：`100 · exp(−加权问题密度 / 10)`，权重 严重 25 / 高 8 / 中 2 / 低 0.3，密度按千行代码（数据库按语句数、依赖按依赖数、运行环境按单元数）归一。
有“严重”问题的类别不超过 59，有“高”的不超过 84。07 类以覆盖率为主（85% 记满分）。
综合分是各适用类别的加权平均（Bug 20、安全 20、性能 10、质量 10、测试 10、依赖 10、架构 8、瘦身 5、UI 5、数据库 4、运行环境 3、兼容性 2）；
只要还有“严重”问题，综合分不超过 79。分数用来看趋势，不是合格线——先处理报告里“优先处理的问题”。

## 自动修复的边界

补丁只包含 Ruff 标记为安全的修复（未使用的导入、多余的 f 前缀、可简化的表达式等）。local 模式下应用后会在副本里重跑测试并报告结果；
静态模式下只做语法校验，报告里会写明补丁没有经过测试验证。
死代码删除、重复代码合并、Rust 侧的修改、依赖升级都只给建议和示例代码，需要人看过再动。

## 目录

```
audit.py                命令行入口：setup / init / run
auditkit/
  linters.py            Ruff / Pylint / Clippy / Semgrep，按规则号分发到各类
  checks/
    slim_arch.py        04 瘦身、05 架构
    tests_db.py         07 测试、08 数据库（代码里的 SQL、SQLite 执行计划）
    sqlfile.py          08：.sql 文件的切分、方言判定、表结构模型与静态规则
    deps.py             09 依赖
    env.py              10 兼容性、11 UI/UX、12 运行环境
    htmlcheck.py        11：HTML 结构与无障碍检查
  fixes.py              自动修复、补丁、修复前后对比
  redact.py             脱敏
  util.py               文件清单与副本（链接 / 敏感文件过滤）、子进程环境白名单
  score.py report.py knowledge.py model.py    评分、报告、规则说明、数据结构
tests/                  本系统自己的测试
profiles/               每个项目一份配置（仓库里只有 example.json）
reports/                输出（可删）
.work/ .tools/ .venv/   项目副本、构建缓存、工具（可删，会重建）
```

## 开发与测试

```bash
python -m unittest discover -s tests -t .
```

测试只用标准库，不需要先 `setup`，也不会真的启动任何外部工具或联网（子进程和网络请求都被打桩）。覆盖的是安全相关的承诺：

| 文件 | 验证什么 |
|---|---|
| `test_mode.py` | static 模式下没有任何命令启动项目解释器、cargo、测试或基准；子进程拿不到白名单之外的环境变量 |
| `test_files.py` | 符号链接、目录联接、指向项目之外的路径不进清单也不被复制；敏感文件名单和私钥内容嗅探 |
| `test_redact.py` | 每种密钥格式的脱敏；假密钥不会出现在报告目录的任何文件里 |
| `test_sqlfile.py` | 语句切分（引号、注释、`$$`、`DELIMITER`）、方言判定、每条规则的正反例 |
| `test_htmlcheck.py` | 只有 `id` 的输入框必须报；各种合法的可访问名称不报 |
| `test_semgrep.py` | Semgrep 的调用方式（见下面 1.1.0 的说明） |

符号链接的用例在没有权限创建链接的机器上会跳过（Windows 默认如此）；CI 在 ubuntu 和 windows 上各跑一遍。

## 更新记录

### 1.1.0

- **执行模式**：新增 `--mode static|local`，默认 static，不执行被审查项目的任何代码。子进程改用白名单环境变量；git 调用禁用 fsmonitor 和 hooks；
  jscpd 不再在项目目录里启动；Lighthouse 去掉 `--no-sandbox`。
- **文件安全**：不跟随符号链接和目录联接，校验真实路径在项目根目录内；敏感文件名单扩充到 SSH 密钥、云凭据、Terraform 状态等，
  并按文件内容识别私钥；报告、`findings.json`、`run.log`、`raw/` 全部脱敏。
- **08 数据库**：独立 `.sql` 文件现在会被解析（之前只算“适用”，实际没有检查）；识别 SQLite / MySQL / PostgreSQL 方言；
  新增索引、JOIN 和方言专项规则。
- **11 UI/UX**：改用 HTML 解析器。修正了把带 `id` 的输入框当成“已有标签”的漏报；新增按钮 / 链接名称、重复 id、失效引用等规则。
- **修正：Semgrep 实际没有扫描项目代码**。两个原因：副本在本仓库的 `.work/` 下，而本仓库的 `.gitignore` 忽略 `.work/`，
  Semgrep 会据此跳过整个副本；Windows 上 `semgrep.exe` 会展开 `--exclude` 参数里的通配符，被排除的文件反而成了扫描目标。
  现在使用 `--no-git-ignore`，排除范围改为按结果过滤。用 1.0.0 且把本目录纳入了 git 的，02 类的 Semgrep 结果需要重跑。
- **修正**：jscpd 在 Windows 上收到绝对路径时一个文件也扫不到且不报错。
- 新增单元测试和 CI。

### 1.0.0

首个版本：12 类检查、评分、报告、自动修复补丁。
