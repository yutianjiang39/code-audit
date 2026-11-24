# 配置（profile）

每个被审查的项目一份 `profiles/<名字>.json`。用 `init` 生成，再按需修改：

```bash
python audit.py init /path/to/project --name myproj
```

仓库里的 [`profiles/example.json`](../profiles/example.json) 是一份可以照抄的样例。

## 最小配置

```json
{
  "name": "myproj",
  "target": "../MyProj"
}
```

## 基本

| 字段 | 说明 |
|---|---|
| `name` | 项目名，决定 `reports/<name>/` 和 `.work/<name>/` |
| `target` | 项目路径，相对本目录或绝对路径 |
| `exclude` | 不纳入审查的路径（glob），如 `["vendor/*"]` |
| `test_globs` | 测试代码的路径。放宽部分规则，不计入覆盖率，Semgrep / jscpd 的命中不计 |

## 执行

```json
"execution": { "mode": "static", "env_passthrough": [] }
```

| 字段 | 说明 |
|---|---|
| `mode` | `static`（默认）或 `local`。命令行的 `--mode` 优先 |
| `env_passthrough` | local 模式下额外传给子进程的环境变量名 |

## 语言

```json
"python": {
  "interpreter": "{target}/.venv/Scripts/python.exe",
  "min_version": "3.11",
  "test_cmd": ["-m", "unittest", "discover", "-s", "tests", "-t", "."],
  "pylint": true
},
"rust": { "manifest_dirs": ["rust"], "run_tests": true }
```

| 字段 | 说明 |
|---|---|
| `python.interpreter` | 项目自己的解释器，只在 local 模式用来跑测试 |
| `python.min_version` | 项目支持的最低 Python 版本 |
| `python.test_cmd` | 跑测试的参数（接在解释器后面） |
| `rust.manifest_dirs` | `Cargo.toml` 所在目录 |

## 各类检查的选项

| 字段 | 说明 |
|---|---|
| `architecture.allowed_imports` | 每层允许依赖谁，如 `{"app": ["app"], "ops": ["ops", "app"]}` |
| `architecture.crate_rank` | crate 层级，只能依赖层级更低的 |
| `architecture.max_file_lines` / `max_fan_out` | 超大文件、扇出的提醒阈值 |
| `slim.import_names` | 包名与 import 名不同的依赖，如 `{"some-dist-name": "import_name"}` |
| `slim.dup_min_lines` / `dup_min_tokens` | 重复代码的最小长度 |
| `database.dialect` | `auto`（默认，按语句特征判断）、`sqlite`、`mysql`、`postgresql` |
| `database.max_indexes` / `max_joins` | 单表索引数、单条语句 JOIN 数的提醒阈值 |
| `database.sqlite_globs` | 对现有的 SQLite 库做只读体检 |
| `security.semgrep_configs` | 自定义 Semgrep 规则包 |
| `runtime.log_globs` | 统计日志里的错误条数 |
| `ui.urls` | 用 Lighthouse 检查的地址 |

## 基准

```json
"bench": [
  { "name": "启动耗时", "cmd": ["{python}", "-c", "import app"], "repeat": 7 }
]
```

只在 local 模式运行，用来对比修复前后的耗时。`{python}` 会替换成项目的解释器。

## 调整结果

```json
"accepted": [
  { "rule": "no-restart", "file": "ops/*.service", "reason": "为什么可以接受" }
],
"severity_overrides": { "S603": "low" },
"score_weights": { "02": 30 }
```

| 字段 | 说明 |
|---|---|
| `accepted` | 登记为可接受的命中。不计分，但会在报告末尾单独列出并带上理由 |
| `severity_overrides` | 改某条规则的严重程度 |
| `score_weights` | 改某类在综合分里的权重 |
