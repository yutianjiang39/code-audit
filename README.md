# code-audit · 代码综合审查系统

[![tests](https://github.com/yutianjiang39/code-audit/actions/workflows/tests.yml/badge.svg)](https://github.com/yutianjiang39/code-audit/actions/workflows/tests.yml)

一条命令跑完 12 类代码检查，输出一份带评分的报告和一个可直接应用的修复补丁。

- **不改你的项目** —— 在副本上分析，修复以补丁交付
- **默认不执行项目代码** —— 只做静态分析，跑测试需要显式开启
- **不读密钥** —— 凭据文件和符号链接不复制、不扫描，报告自动脱敏
- **本地运行** —— 不需要账号，不上传代码

## 快速开始

需要 Python 3.10+。

```bash
python audit.py setup                           # 只需一次：安装检查工具
python audit.py init /path/to/project --name myproj   # 生成 profiles/myproj.json
python audit.py run profiles/myproj.json        # 开始审查
```

报告在 `reports/myproj/<时间>/report.html`。

## 两种模式

| | `static`（默认） | `local` |
|---|---|---|
| 执行项目代码 | 否 | 是 |
| 测试与覆盖率 | — | ✓ |
| Rust Clippy | — | ✓ |
| 依赖漏洞 | 锁定版本查 OSV | 另加 pip-audit、过期版本、许可证 |
| 修复补丁 | 只做语法校验 | 重跑测试验证 |

```bash
python audit.py run profiles/myproj.json --mode local
```

> [!WARNING]
> `local` 模式会以你的用户权限运行项目的测试和构建脚本，**不是沙箱**。只用于你信任的代码。
> 详见 [安全模型](docs/security.md)。

## 检查什么

| # | 类别 | 工具 |
|---|---|---|
| 01 | Bug | Ruff、Pylint、Clippy |
| 02 | 安全 | Semgrep、Ruff S 规则 |
| 03 | 性能 | Ruff PERF、Clippy perf、基准实测 |
| 04 | 瘦身 | Vulture、jscpd |
| 05 | 架构 | import 图、cargo metadata |
| 06 | 质量 | Ruff、Pylint、Clippy |
| 07 | 测试 | Coverage.py、cargo test |
| 08 | 数据库 | SQL 静态检查、SQLite 执行计划 |
| 09 | 依赖 | OSV.dev、pip-audit、npm audit |
| 10 | 兼容性 | 行尾、平台专有模块、文件名 |
| 11 | UI/UX | HTML 无障碍检查、Lighthouse |
| 12 | 运行环境 | systemd 单元、shell 脚本、日志 |

每类查哪些问题、有哪些限制：[检查项说明](docs/checks.md)。

## 输出

| 文件 | 内容 |
|---|---|
| `report.html` | 完整报告，可筛选和搜索 |
| `report.md` | 可通读的摘要版 |
| `findings.json` | 全部问题和指标，供脚本或 CI 使用 |
| `fixes.patch` | 自动修复补丁，在项目根目录 `git apply` |
| `raw/` | 各工具的原始输出 |

每条问题都带：位置、严重程度、原因、修复建议、修复代码。

## 常用命令

```bash
python audit.py run profiles/myproj.json --only 01,02    # 只跑某几类
python audit.py run profiles/myproj.json --no-fix        # 不生成补丁
python audit.py run profiles/myproj.json --mode local --skip-rust-tests
```

## 文档

- [配置（profile）](docs/profile.md) —— 全部字段
- [检查项说明](docs/checks.md) —— 12 类各查什么、评分怎么算、自动修复的边界
- [安全模型](docs/security.md) —— 两种模式的信任边界、文件过滤、脱敏
- [开发](docs/development.md) —— 目录结构、测试
- [更新记录](CHANGELOG.md)
