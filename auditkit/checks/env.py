"""03 性能（工具适用性说明）、10 兼容性、11 UI/UX、12 运行环境。"""
from __future__ import annotations

import ast
import json
import re
from pathlib import Path

from ..model import Context, Finding
from ..util import match_any, read_text, redact_json_file, run, safe_glob, which
from . import htmlcheck

FRONTEND_EXT = (".html", ".htm", ".jsx", ".tsx", ".vue", ".svelte", ".css", ".scss")
POSIX_ONLY = {"fcntl", "pwd", "grp", "resource", "termios", "pty", "tty", "syslog", "crypt"}
WIN_ONLY = {"msvcrt", "winreg", "_winapi", "winsound"}


def has_frontend(ctx: Context) -> bool:
    return bool(ctx.by_ext(*FRONTEND_EXT)) or bool(ctx.cfg.get("ui", {}).get("urls"))


# ====================================================================== 03 性能
def run_perf(ctx: Context) -> None:
    """静态性能问题由 Ruff（PERF / C4）和 Clippy（perf 组）给出；实测对比在修复阶段做。"""
    res = ctx.cat("03")
    if not has_frontend(ctx):
        res.skip("Lighthouse / WebPageTest / Chrome DevTools", "面向网页加载性能，本项目没有前端页面")
    if not ctx.cfg.get("perf", {}).get("load_test_urls"):
        res.skip("k6 / JMeter / Locust", "HTTP 压测工具，需要一个可以压的服务地址（profile 里 perf.load_test_urls 未配置）")
    res.skip("New Relic / Dynatrace / Blackfire", "线上 APM 服务，需要账号并接入运行中的进程，离线审查不适用")
    if ctx.by_ext(".rs"):
        hot = []
        for f in ctx.by_ext(".rs"):
            if re.search(r"(^|/)(tests|examples|benches)/", f):
                continue
            text = read_text(ctx.base / f)
            n = len(re.findall(r"\.clone\(\)", text))
            loc = text.count("\n") + 1
            if n >= 25 and loc:
                hot.append((f, n, round(1000 * n / loc, 1)))
        res.metrics["rust_clone_hotspots"] = sorted(hot, key=lambda r: -r[1])[:8]


# ====================================================================== 10 兼容性
def run_compat(ctx: Context) -> None:
    res = ctx.cat("10")
    if not has_frontend(ctx):
        res.skip("BrowserStack / Sauce Labs / LambdaTest / Playwright / Cypress / Selenium",
                 "浏览器与设备兼容性测试，本项目没有网页或移动端界面")
        res.skip("Can I use / Lighthouse", "同上，没有前端代码")
    tests = ctx.cfg.get("test_globs", [])
    # 1) 行尾与 shebang：在 Windows 上编辑过的脚本带 CRLF，放到 Linux 上直接执行会失败
    for f in ctx.files:
        if not f.endswith((".sh", ".service", ".timer", ".socket", ".env.example")) and "/git-hooks/" not in f:
            continue
        data = (ctx.base / f).read_bytes()
        if b"\r\n" in data:
            res.findings.append(Finding(
                category="10", tool="行尾检查", rule="crlf-in-unix-file", severity="high", file=f,
                line=data[:data.index(b"\r\n")].count(b"\n") + 1, message="Unix 脚本 / systemd 单元里有 CRLF 行尾",
                cause="Linux 把 \\r 当成命令或参数的一部分：shell 报 `$'\\r': command not found`，systemd 读到的值带不可见字符。",
                fix="转成 LF，并在 .gitattributes 里固定：`*.sh text eol=lf`、`*.service text eol=lf`。",
                fix_code="*.sh       text eol=lf\n*.service  text eol=lf\n*.timer    text eol=lf"))
        if f.endswith(".sh") and not data.startswith(b"#!"):
            res.findings.append(Finding(
                category="10", tool="行尾检查", rule="missing-shebang", severity="low", file=f, line=1,
                message="shell 脚本没有 shebang", cause="直接执行时由调用方的 shell 解释，dash / bash / zsh 行为不同。",
                fix="首行写明解释器。", fix_code="#!/usr/bin/env bash"))
    res.use("行尾 / shebang 检查")
    # 2) 只在单一平台存在的标准库模块，没有平台保护就顶层导入
    for f in ctx.by_ext(".py"):
        if match_any(f, tests):
            continue
        try:
            tree = ast.parse(read_text(ctx.base / f))
        except SyntaxError:
            continue
        for node in tree.body:  # 只看模块顶层；写在 try / if 里的视为已做保护
            names = []
            if isinstance(node, ast.Import):
                names = [a.name.split(".")[0] for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                names = [node.module.split(".")[0]]
            for n in names:
                if n in POSIX_ONLY or n in WIN_ONLY:
                    plat = "POSIX（Linux / macOS）" if n in POSIX_ONLY else "Windows"
                    res.findings.append(Finding(
                        category="10", tool="平台模块检查", rule="platform-only-import", severity="low", file=f, line=node.lineno,
                        message=f"顶层导入只在 {plat} 存在的模块 {n}",
                        cause="在另一个平台上 import 这个文件就会 ImportError，连带依赖它的模块和测试都无法加载。",
                        fix="只在目标平台运行就在文档里写明；否则用 try / sys.platform 判断包起来，并给出替代实现。",
                        fix_code=f"try:\n    import {n}\nexcept ImportError:  # 非{plat.split('（')[0]}平台\n    {n} = None"))
    res.use("平台专有模块检查（AST）")
    # 3) 文件名：大小写冲突、Windows 非法字符
    seen: dict[str, str] = {}
    for f in ctx.files:
        low = f.lower()
        if low in seen and seen[low] != f:
            res.findings.append(Finding(
                category="10", tool="文件名检查", rule="case-collision", severity="medium", file=f, line=0,
                message=f"与 {seen[low]} 仅大小写不同", cause="在 Windows / macOS 默认文件系统上这两个文件会互相覆盖。",
                fix="改名，使路径在不区分大小写时也唯一。"))
        seen[low] = f
        if re.search(r'[<>:"|?*]', f) or re.search(r"(^|/)(con|prn|aux|nul|com\d|lpt\d)(\.|/|$)", low):
            res.findings.append(Finding(
                category="10", tool="文件名检查", rule="windows-illegal-name", severity="medium", file=f, line=0,
                message="文件名在 Windows 上非法", cause="含保留字符或保留设备名，Windows 上无法检出。", fix="改名。"))
    res.use("文件名可移植性检查")
    # 4) 声明的最低版本
    decl = {}
    if ctx.cfg.get("python", {}).get("min_version"):
        decl["python（profile）"] = ctx.cfg["python"]["min_version"]
    for f in ctx.files:
        if f.endswith("pyproject.toml"):
            m = re.search(r'requires-python\s*=\s*"(.+?)"', read_text(ctx.base / f))
            if m:
                decl[f"python（{f}）"] = m.group(1)
        if f.endswith("Cargo.toml"):
            t = read_text(ctx.base / f)
            for key in ("rust-version", "edition"):
                m = re.search(rf'^{key}\s*=\s*"(.+?)"', t, re.M)
                if m:
                    decl[f"{key}（{f}）"] = m.group(1)
    res.metrics["declared_versions"] = sorted(decl.items())[:20]
    res.notes.append("Python 语法是否兼容声明的最低版本，由 Ruff 按 target-version 检查（结果在 01 / 06 类里）。")


# ====================================================================== 11 UI/UX
def run_uiux(ctx: Context) -> None:
    res = ctx.cat("11")
    pages = ctx.by_ext(".html", ".htm")
    urls = ctx.cfg.get("ui", {}).get("urls", [])
    if not has_frontend(ctx):
        res.applicable = False
        res.notes.append("项目里没有前端页面（html / jsx / tsx / vue / css），也没有配置 ui.urls，本类不适用、不计入总分。")
        return
    for f in pages:
        for hit in htmlcheck.check_html(read_text(ctx.base / f)):
            res.findings.append(Finding(category="11", tool="HTML 静态检查", file=f, **hit))
    if pages:
        res.use("HTML 静态检查（解析器：表单标签、按钮 / 链接名称、ARIA 引用、重复 id、lang / viewport / title / alt）")
        res.notes.append("本地 HTML 是结构检查：只看写在文件里的标签和属性，不执行脚本、不算样式。"
                         "对比度、焦点顺序、脚本生成的节点等完整的 axe-core 规则，需要在 profile 的 ui.urls 里配置页面地址，由 Lighthouse 在真实页面上跑。")
    other = ctx.by_ext(".jsx", ".tsx", ".vue", ".svelte")
    if other:
        res.notes.append(f"{len(other)} 个 JSX / TSX / Vue / Svelte 组件文件没有做无障碍检查（不是 HTML，本系统不解析）；"
                         "这部分同样靠 ui.urls + Lighthouse 覆盖。")
    npx = which("npx")
    if urls and npx:
        for i, u in enumerate(urls[:5]):
            out_json = ctx.raw / f"lighthouse_{i}.json"
            # 在审查系统自己的目录里启动；不加 --no-sandbox，保留 Chrome 自身的沙箱
            run([npx, "--yes", "lighthouse", u, "--quiet", "--output=json", f"--output-path={out_json}",
                 "--chrome-flags=--headless=new"], cwd=ctx.raw, timeout=300, network=True)
            if not out_json.exists():
                res.skip("Lighthouse", f"{u} 运行失败（需要本机有 Chrome）")
                continue
            redact_json_file(out_json)  # 报告里带页面内容
            lh = json.loads(read_text(out_json))
            res.metrics.setdefault("lighthouse", []).append(
                (u, {k: round((v.get("score") or 0) * 100) for k, v in lh.get("categories", {}).items()}))
            # Lighthouse 的 accessibility 类别就是 axe-core 的规则
            axe = {r.get("id") for r in (lh.get("categories", {}).get("accessibility") or {}).get("auditRefs", [])}
            for a in lh.get("audits", {}).values():
                if a.get("score") is not None and a["score"] < 0.5 and a.get("scoreDisplayMode") in ("binary", "numeric", "metricSavings"):
                    res.findings.append(Finding(
                        category="11", tool="Lighthouse（axe-core）" if a["id"] in axe else "Lighthouse", rule=a["id"], severity="medium", file=u, line=0, message=a.get("title", a["id"]),
                        cause=re.sub(r"\[(.*?)\]\(.*?\)", r"\1", a.get("description", ""))[:260], fix=a.get("displayValue") or "按 Lighthouse 报告里的条目处理。"))
            res.use("Lighthouse（无障碍部分为 axe-core）")
    elif urls:
        res.skip("Lighthouse / axe / Pa11y", "未安装 Node.js")
    else:
        res.skip("Lighthouse / axe / Pa11y / WAVE", "需要一个可访问的页面地址（profile 里 ui.urls 未配置）")
    res.skip("Storybook / Chromatic / Percy / Figma", "组件库与视觉回归平台，需要接入项目构建与账号，不属于静态审查")


# ====================================================================== 12 运行环境
HARDEN = ("NoNewPrivileges", "ProtectSystem", "PrivateTmp", "ProtectHome")


def _unit(text: str) -> dict[str, dict[str, list[tuple[str, int]]]]:
    sec, out = "", {}
    for i, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line[0] in "#;":
            continue
        m = re.match(r"\[(.+)\]$", line)
        if m:
            sec = m.group(1)
            continue
        if "=" in line:
            k, v = line.split("=", 1)
            out.setdefault(sec, {}).setdefault(k.strip(), []).append((v.strip(), i))
    return out


def run_runtime(ctx: Context) -> None:
    res = ctx.cat("12")
    units = [f for f in ctx.files if f.endswith((".service", ".timer"))]
    scripts = ctx.by_ext(".sh")
    docker = [f for f in ctx.files if Path(f).name.lower().startswith(("dockerfile", "docker-compose", "compose."))]
    log_globs = ctx.cfg.get("runtime", {}).get("log_globs", [])
    if not (units or scripts or docker or log_globs):
        res.applicable = False
        res.notes.append("没有发现部署单元、启动脚本或日志，本类不适用。")
        return
    res.metrics["units"], res.metrics["scripts"] = len(units), len(scripts)

    def add(rule, sev, f, line, msg, cause, fix, code=""):
        res.findings.append(Finding(category="12", tool="部署配置检查", rule=rule, severity=sev, file=f, line=line,
                                    message=msg, cause=cause, fix=fix, fix_code=code))

    timers = {Path(f).stem for f in units if f.endswith(".timer")}
    for f in units:
        u = _unit(read_text(ctx.base / f))
        if f.endswith(".timer"):
            t = u.get("Timer", {})
            if "OnCalendar" in t and "Persistent" not in t:
                add("timer-not-persistent", "low", f, t["OnCalendar"][0][1], "定时器没有 Persistent=true",
                    "机器在计划时间关机或重启时，这一次任务会被直接跳过。", "需要补跑就加上 Persistent=true。", "Persistent=true")
            continue
        s = u.get("Service", {})
        if not s:
            continue
        first = min((ln for vals in s.values() for _, ln in vals), default=1)
        typ = (s.get("Type") or [("simple", first)])[0][0]
        oneshot = typ == "oneshot" or Path(f).stem.rstrip("@") in timers
        restart = (s.get("Restart") or [("no", first)])[0]
        if not oneshot and restart[0] == "no":
            add("no-restart", "medium", f, restart[1], "常驻服务没有自动重启（Restart=no 或未设置）",
                "进程崩溃后保持停止状态，直到有人发现；如果这是有意为之（例如崩溃后必须人工核对状态），需要有外部告警兜底。",
                "一般服务用 Restart=on-failure 配合 RestartSec / StartLimit；有意不重启的在 profile 的 accepted 里登记原因。",
                "Restart=on-failure\nRestartSec=5\nStartLimitIntervalSec=300\nStartLimitBurst=5")
        if restart[0] in ("always", "on-failure") and "RestartSec" not in s:
            add("restart-no-backoff", "low", f, restart[1], "自动重启没有设置 RestartSec",
                "默认间隔 100ms，启动即崩溃时会高速重启刷日志，直到触发 StartLimit。", "加上重启间隔。", "RestartSec=5")
        user = (s.get("User") or [("", first)])[0]
        if user[0] in ("", "root") and "DynamicUser" not in s:
            add("runs-as-root", "medium", f, user[1], "服务以 root 身份运行",
                "进程被攻破或出 bug 时拥有整机权限。", "建专用用户并用 User= 指定；必须 root 的在 profile 的 accepted 里登记原因。",
                "User=svc-app\nGroup=svc-app")
        missing = [h for h in HARDEN if h not in s]
        if len(missing) >= 3:
            add("no-sandboxing", "low", f, first, "没有启用 systemd 沙箱选项：" + "、".join(missing),
                "服务能读写整个文件系统，出问题时影响面没有限制。", "按需开启，再用 `systemd-analyze security <unit>` 复查。",
                "NoNewPrivileges=true\nProtectSystem=strict\nProtectHome=true\nPrivateTmp=true\nReadWritePaths=/var/lib/yourapp")
        if not oneshot and "TimeoutStopSec" not in s and "KillMode" not in s:
            add("no-stop-timeout", "low", f, first, "没有设置 TimeoutStopSec",
                "停止时默认等 90 秒后 SIGKILL；对需要优雅收尾（落盘、撤单）的进程，应当明确写出这个时间。", "按收尾所需时间设置。", "TimeoutStopSec=30")
        for key in ("Environment",):
            for v, ln in s.get(key, []):
                if re.search(r"(?i)(secret|token|password|passwd|private_?key|api_?key)\w*=\S{6,}", v):
                    add("secret-in-unit", "critical", f, ln, "单元文件的 Environment= 里疑似写了密钥（内容不在报告里显示）",
                        "单元文件通常全员可读，还会进版本库和 `systemctl show` 的输出。",
                        "改用 EnvironmentFile= 指向权限 600 的文件，或用 LoadCredential=。", "EnvironmentFile=/etc/yourapp/env")
    if units:
        res.use("systemd 单元检查（重启策略、运行身份、沙箱、定时器）")

    for f in scripts:
        text = read_text(ctx.base / f)
        if len(text.splitlines()) < 4:
            continue
        if not re.search(r"^\s*set\s+-[a-z]*e|^\s*set\s+-o\s+errexit", text, re.M):
            add("sh-no-errexit", "low", f, 1, "shell 脚本没有 set -e", "中间某条命令失败后脚本继续往下执行，可能在半完成状态上做后续操作。",
                "脚本开头加上严格模式。", "set -euo pipefail")
    sc = which("shellcheck")
    if scripts and sc:
        rc, out, err = run([sc, "-f", "json", "-S", "warning", *scripts], cwd=ctx.base, timeout=300)
        try:
            for it in json.loads(out or "[]"):
                res.findings.append(Finding(
                    category="12", tool="ShellCheck", rule=f"SC{it['code']}", severity="medium" if it["level"] == "error" else "low",
                    file=it["file"].replace("\\", "/"), line=it["line"], message=it["message"],
                    cause="ShellCheck 规则命中，常见于引号、通配和未定义变量。", fix=f"见 https://www.shellcheck.net/wiki/SC{it['code']}",
                    url=f"https://www.shellcheck.net/wiki/SC{it['code']}"))
            res.use("ShellCheck")
        except ValueError:
            pass
    elif scripts:
        res.use("shell 脚本检查（严格模式）")
        res.skip("ShellCheck", "未安装（装好后自动启用，可以查出引号和通配问题）")

    _logs(ctx, log_globs)
    res.skip("Sentry / Datadog / New Relic / Prometheus / Grafana / ELK / CloudWatch",
             "线上监控与日志平台，需要接入运行中的服务和账号；本系统只做部署配置的静态检查和本地日志统计")


LOG_PAT = [("panic", re.compile(r"panicked at|thread '.*' panicked")), ("traceback", re.compile(r"Traceback \(most recent call last\)")),
           ("error", re.compile(r"\b(ERROR|CRITICAL|FATAL)\b")), ("oom", re.compile(r"MemoryError|Out of memory|oom-kill", re.I)),
           ("warn", re.compile(r"\bWARN(ING)?\b"))]


def _logs(ctx: Context, globs: list[str]) -> None:
    """本地日志只统计条数，不把日志内容写进报告（日志里可能有地址、订单等业务数据）。"""
    res = ctx.cat("12")
    if not globs:
        res.skip("日志统计", "profile 里 runtime.log_globs 未配置")
        return
    rows = []
    for p in safe_glob(ctx.target, globs)[:60]:  # 不跟随链接，不碰敏感文件名
        if Path(p).stat().st_size > 200 * 1024 * 1024:
            continue
        c = dict.fromkeys((k for k, _ in LOG_PAT), 0)
        n = 0
        with open(p, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                n += 1
                for k, pat in LOG_PAT:
                    if pat.search(line):
                        c[k] += 1
                        break
        rel = Path(p).relative_to(ctx.target).as_posix()
        rows.append((rel, n, c))
        if c["panic"] or c["traceback"] or c["oom"]:
            res.findings.append(Finding(
                category="12", tool="日志统计", rule="crash-in-logs", severity="high" if c["panic"] or c["oom"] else "medium", file=rel, line=0,
                message=f"日志里有崩溃记录：panic {c['panic']} 次、Traceback {c['traceback']} 次、内存不足 {c['oom']} 次（共 {n} 行）",
                cause="进程出现过未处理的异常或崩溃。", fix="按时间定位对应记录，确认是否已修复；没修的补上处理和回归测试。"))
    if rows:
        res.metrics["logs"] = rows
        res.use(f"本地日志统计（{len(rows)} 个文件，只计数）")
    else:
        res.skip("日志统计", "log_globs 没有匹配到文件")
