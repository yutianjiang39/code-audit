# 开发

## 目录

```
audit.py                命令行入口：setup / init / run
auditkit/
  linters.py            Ruff / Pylint / Clippy / Semgrep，按规则号分发到各类
  checks/
    slim_arch.py        04 瘦身、05 架构
    tests_db.py         07 测试、08 数据库（代码里的 SQL、SQLite 执行计划）
    sqlfile.py          08：.sql 文件的切分、方言判定、表结构模型、静态规则
    deps.py             09 依赖
    env.py              10 兼容性、11 UI/UX、12 运行环境
    htmlcheck.py        11：HTML 结构与无障碍检查
  fixes.py              自动修复、补丁、修复前后对比
  redact.py             脱敏
  util.py               文件清单与副本、子进程环境白名单
  score.py              评分
  report.py             报告
  knowledge.py          规则 → 原因与修复建议
  model.py              数据结构
tests/                  测试
profiles/               每个项目一份配置
```

运行时生成、可以随时删除的目录：`reports/`（输出）、`.work/`（项目副本）、`.tools/`（构建缓存）、`.venv/`（检查工具）。

## 测试

```bash
python -m unittest discover -s tests -t .
```

只用标准库，不需要先 `setup`。不会真的启动外部工具，也不联网 —— 子进程和网络请求都被打桩。

| 文件 | 验证什么 |
|---|---|
| `test_mode.py` | static 模式下没有命令启动项目解释器、cargo、测试或基准；子进程拿不到白名单之外的环境变量 |
| `test_files.py` | 符号链接、目录联接、指向项目之外的路径不进清单也不被复制；敏感文件名单；私钥内容识别 |
| `test_redact.py` | 每种密钥格式的脱敏；假密钥不会出现在报告目录的任何文件里 |
| `test_sqlfile.py` | 语句切分、方言判定、每条规则的正反例 |
| `test_htmlcheck.py` | 只有 `id` 的输入框必须报；合法的可访问名称不报 |
| `test_semgrep.py` | Semgrep 的调用方式不会漏扫 |

符号链接的用例在没有权限创建链接的机器上会跳过（Windows 默认如此）。CI 在 ubuntu 和 windows 上各跑一遍。

## 搬到另一台机器

拷走整个文件夹（`.venv/`、`.work/`、`.tools/`、`reports/` 不用拷），在新机器上运行 `python audit.py setup`。
