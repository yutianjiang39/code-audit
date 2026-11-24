# 安全模型

把项目复制到 `.work/` 只隔离了**文件**（原项目不会被改），不是操作系统级的沙箱。所以分两种模式。

## static（默认）

不执行被审查项目的任何代码：

- 不启动项目的解释器
- 不跑 cargo（构建脚本和过程宏会执行代码）
- 不跑测试、基准、npm 脚本

代价是没有动态结果：07 测试不评分，Rust 的 01 / 03 / 06 没有 Clippy 结果，补丁没有经过测试验证。

## local

会执行：测试与覆盖率、`cargo clippy`、`cargo test`、profile 里的 bench 命令。

边界：

- 项目代码以**当前用户的权限**运行，能读写这个用户能访问的任何文件，也能联网。
- 子进程只拿到白名单环境变量（`PATH`、临时目录、用户目录等），不继承 shell 里的令牌。
  需要额外变量时写进 profile 的 `execution.env_passthrough`。
  这只防止凭据经环境变量泄漏，**不等于隔离**。
- 代理变量只传给需要联网的工具（Semgrep、pip-audit、npx、cargo update）。不传代理**不等于断网**。

**没有容器隔离。** 要审查不信任的代码，用 static 模式，或者把整个工具放进一次性的虚拟机 / 容器里运行。

## 两种模式都生效的保护

### 不跟随链接

符号链接和 Windows 目录联接不进文件清单、不复制。每个文件的真实路径必须在项目根目录之内。

### 敏感文件不读取

按名字排除，大小写不敏感：

| 类型 | 规则 |
|---|---|
| 目录 | `secrets/` `.ssh/` `.aws/` `.azure/` `.gcloud/` `.gnupg/` `.kube/` `.docker/` `.terraform/` |
| 密钥与证书 | `*.key` `*.pem` `*.p12` `*.pfx` `*.ppk` `*.jks` `*.keystore` `*.kdbx` `*.ovpn` `id_rsa*` `id_dsa*` `id_ecdsa*` `id_ed25519*` |
| 凭据文件 | `.env*` `.envrc` `.netrc` `.npmrc` `.pypirc` `.git-credentials` `.htpasswd` `.dockercfg` `credentials` `credentials.json` `service-account*.json` `kubeconfig` |
| Terraform | `*.tfstate` `*.tfstate.*` `*.tfvars` |

另外，文件开头含 `-----BEGIN … PRIVATE KEY-----` 的一律排除。

报告里只显示排除了多少个，不显示文件名。

### 脱敏

写进 `report.*`、`findings.json`、`run.log`、`raw/` 的文本都会过一遍脱敏，替换为 `[已脱敏]`：

- PEM 私钥块
- AWS、GitHub、Slack、Google、`sk-` 类密钥
- JWT、Telegram bot token
- 带口令的 URL（`scheme://user:pass@host`）
- `password = …`、`API_KEY=…` 一类赋值

两点限制：

- 脱敏靠模式匹配，不保证覆盖所有格式。报告外发前请自己看一遍。
- **`fixes.patch` 不脱敏**（改了就无法应用），它包含被修改文件的上下文行。

### 工具隔离

- git 调用禁用仓库自带的 fsmonitor 和 hooks。
- Ruff / Pylint 用隔离参数运行，不读项目里的配置和插件。
- jscpd 不在项目目录里启动（npm 会读当前目录的 `.npmrc`）。

## 不要提交的东西

你自己的 profile 和 `reports/` 会写出被审查项目的路径、文件名和代码片段，已在 `.gitignore` 里排除。不要提交到公开仓库。
