# 代码综合审查系统（code-audit）

一套自成一体的审查工具：把 12 类检查（Bug、安全、性能、瘦身、架构、质量、测试覆盖率、数据库、依赖、兼容性、UI/UX、运行环境）
跑一遍，汇总成一份带评分的报告。

- **不碰被审查的项目**：先把项目复制到本目录的 `.work/` 沙箱，所有工具都在副本上运行；修复以补丁文件交付，由你决定是否应用。
- **不读密钥**：`secrets/`、`*.key`、`*.pem`、`.env` 永远不复制、不扫描；密钥类命中只报位置，不把内容写进报告。
- **可搬走**：整个文件夹拷到别处即可，每个项目一份 `profiles/<名字>.json`。

## 使用

```bash
python audit.py setup                        # 只需一次：建 .venv 并安装工具
python audit.py run profiles/myproj.json     # 审查，约 10 分钟（含 Rust 测试）
python audit.py run profiles/myproj.json --only 01,02     # 只跑某几类（不做修复阶段）
python audit.py run profiles/myproj.json --skip-rust-tests
```

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
| 07 测试 | Coverage.py（行 + 分支）、cargo test、cargo-llvm-cov（装了才用） | 先跑基线测试，再给出低覆盖文件 |
| 08 数据库 | 自带的 SQL 提取 + SQLite EXPLAIN QUERY PLAN | 全表扫描、SELECT *、拼接 SQL；可选对现有 .sqlite 文件做只读体检 |
| 09 依赖 | pip-audit、OSV.dev（Cargo.lock）、pip / cargo 过期检查、许可证清点、npm audit | 需要联网 |
| 10 兼容性 | 行尾 / shebang、平台专有模块、文件名可移植性、Ruff target-version | 有前端时浏览器兼容性另需 Playwright 等 |
| 11 UI/UX | HTML 静态检查、Lighthouse（配置了 `ui.urls` 才跑） | 没有前端的项目自动判为不适用 |
| 12 运行环境 | systemd 单元检查、shell 严格模式、ShellCheck（装了才用）、本地日志统计 | 只计数，不把日志内容写进报告 |

## profile 字段

```jsonc
{
  "name": "myproj",
  "target": "../MyProj",                  // 相对本目录或绝对路径
  "exclude": ["vendor/*"],                // 不纳入审查的路径（glob）
  "test_globs": ["tests/*"],              // 测试代码：放宽部分规则，不计入覆盖率
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
  "database": { "sqlite_globs": ["data/*.sqlite3"] },      // 可选：对现有库做只读体检
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

补丁只包含 Ruff 标记为安全的修复（未使用的导入、多余的 f 前缀、可简化的表达式等），应用后会在副本里重跑测试并报告结果。
死代码删除、重复代码合并、Rust 侧的修改、依赖升级都只给建议和示例代码，需要人看过再动。

## 目录

```
audit.py            命令行入口
auditkit/           实现：linters（Ruff / Pylint / Clippy / Semgrep）、checks/（其余各类）、fixes、score、report、knowledge（规则 → 原因与修复建议）
profiles/           每个项目一份配置
reports/            输出（可删）
.work/ .tools/ .venv/   沙箱副本、构建缓存、工具（可删，会重建）
```
