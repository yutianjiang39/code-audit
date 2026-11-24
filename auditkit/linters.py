"""多类别共用的静态分析器：Ruff、Pylint、Clippy、Semgrep。

每个分析器只跑一次，按规则号把问题分发到 12 个类别（见 knowledge.py）。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from . import knowledge as K
from .model import STATIC_SKIP, Context, Finding
from .util import chunks, kit_python, kit_tool, norm_rel, read_text, run, which, match_any, write_raw


def _sev(ctx: Context, rule: str, default: str) -> str:
    return ctx.cfg.get("severity_overrides", {}).get(rule, default)


def _is_test(ctx: Context, rel: str) -> bool:
    return match_any(rel, ctx.cfg.get("test_globs", []))


# ------------------------------------------------------------------ Ruff
def _edit_preview(text_lines: list[str], edits: list[dict]) -> str:
    """把 Ruff 给出的修复渲染成「- 原代码 / + 新代码」。"""
    if not edits:
        return ""
    try:
        r0 = min(e["location"]["row"] for e in edits)
        r1 = max(e["end_location"]["row"] for e in edits)
        if r1 - r0 > 30 or r1 > len(text_lines) + 1:
            return ""
        old = text_lines[r0 - 1:r1]
        offs, pos = [], 0
        for ln in old:
            offs.append(pos)
            pos += len(ln)
        chunk = "".join(old)
        for e in sorted(edits, key=lambda e: (e["location"]["row"], e["location"]["column"]), reverse=True):
            a = offs[e["location"]["row"] - r0] + e["location"]["column"] - 1
            er = e["end_location"]["row"] - r0
            b = (offs[er] if er < len(offs) else len(chunk)) + e["end_location"]["column"] - 1
            chunk = chunk[:a] + (e.get("content") or "") + chunk[b:]
        new = chunk.splitlines()
        out = [f"- {ln.rstrip()}" for ln in old] + [f"+ {ln.rstrip()}" for ln in new]
        return "\n".join(out)
    except (KeyError, IndexError, TypeError):
        return ""


def ruff_args(ctx: Context) -> list[str]:
    py = ctx.cfg.get("python", {})
    args = ["--isolated", "--no-cache", "--line-length", "120",
            "--target-version", "py" + py.get("min_version", "3.11").replace(".", ""),
            "--config", f"lint.mccabe.max-complexity={py.get('max_complexity', 15)}",
            "--config", "lint.pylint.max-args=8", "--config", "lint.pylint.max-branches=20",
            "--config", "lint.pylint.max-statements=80", "--config", "lint.pylint.max-returns=10"]
    for g in ctx.cfg.get("test_globs", []):
        args += ["--per-file-ignores", ",".join(f"{g}:{c}" for c in ("S", "PLR", "N", "C901", "ERA", "PLW", "B"))]
    return args


def run_ruff(ctx: Context) -> None:
    files = ctx.by_ext(".py")
    ruff = kit_tool(ctx.kit_root, "ruff")
    cats = ("01", "02", "03", "04", "06", "10")
    if not files:
        return
    if not ruff:
        for c in cats:
            ctx.cat(c).skip("Ruff", "未安装（先运行 python audit.py setup）")
        return
    select = ctx.cfg.get("python", {}).get("ruff_select", K.RUFF_SELECT)
    ignore = K.RUFF_IGNORE + ctx.cfg.get("python", {}).get("ruff_ignore", [])
    fixable = set(ctx.cfg.get("python", {}).get("fix_select", K.RUFF_FIX_SELECT))
    items: list[dict] = []
    for part in chunks(files, 200):
        rc, out, err = run([ruff, "check", *ruff_args(ctx), "--output-format", "json", "--no-fix",
                            "--select", ",".join(select), "--ignore", ",".join(ignore), *part], cwd=ctx.base)
        try:
            if rc not in (0, 1):
                raise ValueError(rc)
            items += json.loads(out or "[]")
        except ValueError:
            for c in cats:
                ctx.cat(c).skip("Ruff", f"运行失败：{err.strip()[:200]}")
            return
    write_raw(ctx, "ruff.json", json.dumps(items, ensure_ascii=False, indent=1))
    cache: dict[str, list[str]] = {}
    for it in items:
        rule = it.get("code") or "invalid-syntax"
        info = K.lookup(K.RUFF, rule) or ("06", "low", "", "")
        rel = norm_rel(it["filename"], ctx.base)
        fix = it.get("fix") or {}
        safe = fix.get("applicability") == "safe"
        auto = safe and any(rule.startswith(p) for p in fixable)
        if rel not in cache:
            cache[rel] = read_text(ctx.base / rel).splitlines(keepends=True)
        preview = _edit_preview(cache[rel], fix.get("edits") or []) if fix else ""
        hint = info[3] + (f"（Ruff：{fix['message']}）" if fix.get("message") else "")
        ctx.cat(info[0]).findings.append(Finding(
            category=info[0], tool="Ruff", rule=rule, severity=_sev(ctx, rule, info[1]), file=rel,
            line=(it.get("location") or {}).get("row", 0), message=it.get("message", ""), cause=info[2],
            fix=hint, fix_code=preview, autofix=auto, url=it.get("url") or ""))
    for c in cats:
        ctx.cat(c).use("Ruff")
    ctx.shared["ruff_count"] = len(items)


def apply_ruff_fixes(ctx: Context) -> int:
    """在 fixed 副本里应用安全修复，返回修复条数。"""
    files = ctx.by_ext(".py")
    ruff = kit_tool(ctx.kit_root, "ruff")
    if not files or not ruff:
        return 0
    fixable = ctx.cfg.get("python", {}).get("fix_select", K.RUFF_FIX_SELECT)
    ignore = K.RUFF_IGNORE + ctx.cfg.get("python", {}).get("ruff_ignore", [])
    n = 0
    for part in chunks(files, 200):
        rc, out, err = run([ruff, "check", *ruff_args(ctx), "--fix", "--select", ",".join(fixable),
                            "--ignore", ",".join(ignore), "--output-format", "concise", *part], cwd=ctx.fixed)
        m = re.search(r"(\d+) fixed", out + err)
        if m:
            n += int(m.group(1))
    return n


# ------------------------------------------------------------------ Pylint
def run_pylint(ctx: Context) -> None:
    files = ctx.by_ext(".py")
    if not files or not ctx.cfg.get("python", {}).get("pylint", True):
        return
    py = kit_python(ctx.kit_root)
    rc, out, _ = run([py, "-I", "-c", "import pylint"], timeout=60)
    if rc != 0:
        for c in ("01", "05", "06"):
            ctx.cat(c).skip("Pylint", "未安装")
        return
    items: list[dict] = []
    for part in chunks(files, 300):
        # -I：当前目录（项目副本）不进 sys.path，项目里的同名模块不会被审查系统的解释器导入
        rc, out, err = run([py, "-I", "-m", "pylint", "--rcfile=", "--disable=all", "--enable=" + ",".join(K.PYLINT_ENABLE),
                            "--disable=" + ",".join(K.PYLINT_DISABLE), "--output-format=json", "--jobs=0",
                            "--max-module-lines=800", "--max-locals=25", "--max-attributes=15",
                            "--max-public-methods=30", "--max-nested-blocks=5", "--score=n", "--persistent=n",
                            *part], cwd=ctx.base, timeout=1800)
        try:
            items += json.loads(out or "[]")
        except json.JSONDecodeError:
            ctx.say(f"  ! Pylint 输出无法解析：{(err or out)[:200]}")
    write_raw(ctx, "pylint.json", json.dumps(items, ensure_ascii=False, indent=1))
    for it in items:
        rule = it.get("message-id", "")
        info = K.lookup(K.PYLINT, rule) or ("06", "low", "", "")
        rel = norm_rel(it.get("path", ""), ctx.base)
        if _is_test(ctx, rel) and info[0] == "06":
            continue
        ctx.cat(info[0]).findings.append(Finding(
            category=info[0], tool="Pylint", rule=f"{rule}({it.get('symbol', '')})",
            severity=_sev(ctx, rule, info[1]), file=rel, line=it.get("line") or 0,
            message=it.get("message", "").split("\n")[0], cause=info[2], fix=info[3]))
    for c in ("01", "05", "06"):
        ctx.cat(c).use("Pylint")


# ------------------------------------------------------------------ Clippy
NON_PROD = re.compile(r"(^|/)(tests|examples|benches)/|(^|/)(testing|test_util|tests)\.rs$|(^|/)build\.rs$")
RESTRICTION = {"clippy::unwrap_used", "clippy::panic", "clippy::cast_possible_truncation", "clippy::cast_sign_loss",
               "clippy::cast_possible_wrap", "clippy::too_many_lines", "clippy::cognitive_complexity",
               "clippy::needless_pass_by_value"}


def cargo_roots(ctx: Context) -> list[str]:
    """最外层的 Cargo.toml 所在目录（workspace 根）。"""
    cfg = ctx.cfg.get("rust", {}).get("manifest_dirs")
    if cfg:
        return cfg
    dirs = sorted({str(Path(f).parent.as_posix()) for f in ctx.files if f.endswith("Cargo.toml")}, key=len)
    roots: list[str] = []
    for d in dirs:
        if not any(d == r or d.startswith(r + "/") or r == "." for r in roots):
            roots.append(d)
    return roots


def cargo_env(ctx: Context) -> dict:
    return {"CARGO_TARGET_DIR": str(ctx.kit_root / ".work" / "cargo-target"),
            "CLIPPY_CONF_DIR": str(ctx.kit_root / "auditkit" / "conf"), "CARGO_TERM_COLOR": "never"}


def cargo_fetch(ctx: Context, root: str) -> None:
    """先把依赖下载好（这一步带代理设置、不编译）；之后真正执行项目代码的 cargo 调用就不带代理变量了。"""
    done = ctx.shared.setdefault("cargo_fetched", set())
    if root in done:
        return
    done.add(root)
    cmd = ["cargo", "fetch"] + (["--locked"] if (ctx.base / root / "Cargo.lock").exists() else [])
    run(cmd, cwd=ctx.base / root, env=cargo_env(ctx), timeout=1800, network=True)


def run_clippy(ctx: Context) -> None:
    if not ctx.by_ext(".rs"):
        return
    cats = ("01", "03", "04", "06")
    if not ctx.local:  # Clippy 要编译：依赖和项目自己的 build.rs、过程宏都会被执行
        for c in cats:
            ctx.cat(c).skip("Clippy", STATIC_SKIP)
        return
    if not which("cargo"):
        for c in cats:
            ctx.cat(c).skip("Clippy", "未安装 Rust 工具链")
        return
    extra = ctx.cfg.get("rust", {}).get("extra_lints", K.CLIPPY_EXTRA)
    seen: set[tuple] = set()
    raw_lines: list[str] = []
    for root in cargo_roots(ctx):
        cmd = ["cargo", "clippy", "--workspace", "--all-targets", "--message-format=json"]
        if (ctx.base / root / "Cargo.lock").exists():
            cmd.append("--locked")
        cmd += ["--"] + [x for lint in extra for x in ("-W", lint)]
        cargo_fetch(ctx, root)
        rc, out, err = run(cmd, cwd=ctx.base / root, env=cargo_env(ctx), timeout=3600)
        if rc not in (0, 101) or (rc == 101 and '"reason"' not in out):
            for c in cats:
                ctx.cat(c).skip("Clippy", f"运行失败：{err.strip().splitlines()[-1][:160] if err.strip() else rc}")
            continue
        for line in out.splitlines():
            if '"compiler-message"' not in line:
                continue
            try:
                msg = json.loads(line).get("message") or {}
            except json.JSONDecodeError:
                continue
            if msg.get("level") not in ("warning", "error") or not msg.get("spans"):
                continue
            rule = (msg.get("code") or {}).get("code") or ""
            span = next((s for s in msg["spans"] if s.get("is_primary")), msg["spans"][0])
            fname = span.get("file_name", "").replace("\\", "/")
            if Path(fname).is_absolute():
                continue  # 依赖库里的告警
            rel = fname if root in (".", "") else f"{root}/{fname}"
            if rule in RESTRICTION and NON_PROD.search(rel):
                continue
            if rel not in ctx.files:
                continue
            key = (rule, rel, span.get("line_start"), span.get("column_start"))
            if key in seen:
                continue
            seen.add(key)
            raw_lines.append(line)
            info = K.clippy_lookup(rule)
            sev = "high" if msg.get("level") == "error" else info[1]
            sugg = []
            for ch in msg.get("children") or []:
                for sp in ch.get("spans") or []:
                    if sp.get("suggested_replacement") is not None:
                        old = "".join(t.get("text", "")[t.get("highlight_start", 1) - 1:t.get("highlight_end", 1) - 1]
                                      for t in sp.get("text") or [])
                        sugg.append(f"- {old}\n+ {sp['suggested_replacement']}")
            helps = [ch.get("message", "") for ch in msg.get("children") or []
                     if ch.get("level") == "help" and not ch.get("message", "").startswith("for further")]
            ctx.cat(info[0]).findings.append(Finding(
                category=info[0], tool="Clippy", rule=rule or "rustc", severity=_sev(ctx, rule, sev), file=rel,
                line=span.get("line_start") or 0, message=msg.get("message", ""), cause=info[2],
                fix=info[3] + (f"（Clippy：{helps[0]}）" if helps else ""), fix_code="\n".join(sugg[:3]),
                url=(f"https://rust-lang.github.io/rust-clippy/master/index.html#{rule.split('::')[1]}"
                     if rule.startswith("clippy::") else "")))
        for c in cats:
            ctx.cat(c).use("Clippy")
    write_raw(ctx, "clippy.jsonl", "\n".join(raw_lines))


# ------------------------------------------------------------------ Semgrep
SEMGREP_SEV = {"CRITICAL": "critical", "ERROR": "high", "HIGH": "high", "WARNING": "medium", "MEDIUM": "medium",
               "INFO": "low", "LOW": "low"}


def run_semgrep(ctx: Context) -> None:
    res = ctx.cat("02")
    sg = kit_tool(ctx.kit_root, "semgrep")
    if not sg:
        res.skip("Semgrep", "未安装")
        return
    packs = ctx.cfg.get("security", {}).get("semgrep_configs") or _default_packs(ctx)
    cmd = [sg, "scan", "--json", "--quiet", "--metrics", "off", "--disable-version-check", "--timeout", "30"]
    for p in packs:
        cmd += ["--config", p]
    # --no-git-ignore：副本在 kit 的 .work/ 下，而 kit 自己的 .gitignore 忽略 .work/，Semgrep 会顺着上层仓库把整个副本都当成被忽略。
    # 不传 --exclude：Windows 上 semgrep.exe 会把参数里的通配符展开成文件名，结果是“排除的文件”反而成了显式扫描目标。
    # 排除范围和测试文件改在下面按结果过滤。
    rc, out, err = run(cmd + ["--no-git-ignore", "."], cwd=ctx.base, timeout=1800, network=True)
    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        res.skip("Semgrep", f"运行失败（规则包需要联网下载）：{(err or out).strip()[-160:]}")
        return
    if not (data.get("paths") or {}).get("scanned"):
        msg = "；".join(e.get("message", "") for e in data.get("errors", []))[:240]
        res.skip("Semgrep", f"没有扫描到任何文件：{msg or '未知原因'}")
        return
    # 原始输出里可能带命中行的内容（密钥规则），落盘前去掉
    for r in data.get("results", []):
        r.get("extra", {}).pop("lines", None)
        r.get("extra", {}).pop("fingerprint", None)
    write_raw(ctx, "semgrep.json", json.dumps(data, ensure_ascii=False, indent=1))
    for r in data.get("results", []):
        ex = r.get("extra", {})
        meta = ex.get("metadata", {})
        rel = norm_rel(r.get("path", ""), ctx.base)
        if rel not in ctx.files or _is_test(ctx, rel):
            continue
        rule = r.get("check_id", "").split(".")[-1]
        is_secret = "secret" in r.get("check_id", "").lower()
        sev = SEMGREP_SEV.get(str(ex.get("severity", "")).upper(), "medium")
        if is_secret:
            sev = "critical" if not _is_test(ctx, rel) else "medium"
        cwe = meta.get("cwe")
        cwe = ", ".join(cwe) if isinstance(cwe, list) else (cwe or "")
        refs = meta.get("references") or []
        res.findings.append(Finding(
            category="02", tool="Semgrep", rule=rule, severity=_sev(ctx, rule, sev), file=rel,
            line=(r.get("start") or {}).get("line", 0),
            message=("疑似密钥 / 凭据写在代码里（内容不在报告中显示）" if is_secret
                     else " ".join(ex.get("message", "").split())[:400]),
            cause=(f"{cwe}。" if cwe else "") + ("凭据进了版本库，任何能读到仓库的人都能拿到。" if is_secret
                                               else "命中 Semgrep 安全规则，匹配到已知的危险写法。"),
            fix=("立即轮换该凭据，从代码和 git 历史里清除，改从环境变量或密钥文件读取；确认是假值则加行内豁免。"
                 if is_secret else (ex.get("fix") and f"建议替换为：{ex['fix']}") or "按规则说明改写（见链接）。"),
            fix_code=ex.get("fix") or "", url=meta.get("source") or (refs[0] if refs else "")))
    res.use("Semgrep（" + "、".join(packs) + "）")
    errs = [e for e in data.get("errors", []) if e.get("level") == "error"]
    if errs:
        res.notes.append(f"Semgrep 有 {len(errs)} 个文件解析失败，详见 raw/semgrep.json。")


def _default_packs(ctx: Context) -> list[str]:
    packs = ["p/secrets"]
    if ctx.by_ext(".py"):
        packs.append("p/python")
    if ctx.by_ext(".rs"):
        packs.append("p/rust")
    if ctx.by_ext(".js", ".ts", ".tsx", ".jsx"):
        packs += ["p/javascript", "p/typescript"]
    if any(Path(f).name.lower().startswith("dockerfile") for f in ctx.files):
        packs.append("p/dockerfile")
    return packs
