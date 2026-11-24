"""07 测试覆盖率（Coverage.py、cargo test / llvm-cov）与 08 数据库（SQL 提取 + EXPLAIN QUERY PLAN）。"""
from __future__ import annotations

import ast
import glob
import json
import os
import re
import sqlite3
from pathlib import Path

from ..model import Context, Finding
from ..util import match_any, read_text, run, timed, which
from ..linters import cargo_roots, cargo_env


# ====================================================================== 07 测试
def project_python(ctx: Context) -> str | None:
    p = ctx.cfg.get("python", {}).get("interpreter")
    if not p:
        return None
    p = p.replace("{target}", str(ctx.target))
    return p if Path(p).exists() else None


def test_cmd(ctx: Context) -> list[str] | None:
    cmd = ctx.cfg.get("python", {}).get("test_cmd")
    py = project_python(ctx)
    return [py, *cmd] if cmd and py else None


def parse_test_output(text: str) -> dict:
    """识别 unittest / pytest / cargo test 的汇总行。"""
    r = {"ran": None, "failed": 0, "errors": 0, "skipped": 0}
    m = re.search(r"Ran (\d+) tests? in ([\d.]+)s", text)
    if m:
        r["ran"] = int(m.group(1))
        for k, pat in (("failed", r"failures=(\d+)"), ("errors", r"errors=(\d+)"), ("skipped", r"skipped=(\d+)")):
            mm = re.search(pat, text)
            r[k] = int(mm.group(1)) if mm else 0
        return r
    m = re.search(r"=+ (.*?) in [\d.]+s", text)
    if m and "passed" in m.group(1) or m and "failed" in m.group(1):
        g = dict((k, int(v)) for v, k in re.findall(r"(\d+) (passed|failed|error|errors|skipped)", m.group(1)))
        r.update(ran=g.get("passed", 0) + g.get("failed", 0), failed=g.get("failed", 0),
                 errors=g.get("error", 0) + g.get("errors", 0), skipped=g.get("skipped", 0))
        return r
    rows = re.findall(r"test result: \w+\. (\d+) passed; (\d+) failed; (\d+) ignored", text)
    if rows:
        r.update(ran=sum(int(a) + int(b) for a, b, _ in rows), failed=sum(int(b) for _, b, _ in rows),
                 skipped=sum(int(c) for *_, c in rows))
    return r


def run_tests_in(ctx: Context, root: Path, label: str) -> dict | None:
    cmd = test_cmd(ctx)
    if not cmd:
        return None
    rc, out, err, secs = timed(cmd, cwd=root, timeout=int(ctx.cfg.get("python", {}).get("test_timeout", 1800)))
    text = out + "\n" + err
    (ctx.raw / f"tests_{label}.txt").write_text(text, encoding="utf-8")
    r = parse_test_output(text)
    r.update(rc=rc, seconds=round(secs, 2), ok=(rc == 0))
    return r


def run_coverage(ctx: Context) -> None:
    res = ctx.cat("07")
    tests = ctx.cfg.get("test_globs", [])
    py_files = ctx.by_ext(".py")
    n_test_files = sum(1 for f in ctx.files if match_any(f, tests))
    res.metrics["test_files"] = n_test_files
    if py_files:
        _py_coverage(ctx)
    if ctx.by_ext(".rs"):
        _rust_tests(ctx)
    if not res.tools_used and n_test_files == 0:
        res.findings.append(Finding(
            category="07", tool="测试清点", rule="no-tests", severity="high", file="", line=0,
            message="项目里没有找到测试文件", cause="没有自动化测试，任何改动都只能靠人工验证，回归无法发现。",
            fix="先为核心路径补冒烟测试，再逐步提高覆盖率。"))


def _py_coverage(ctx: Context) -> None:
    res = ctx.cat("07")
    cmd = test_cmd(ctx)
    if not cmd:
        res.skip("Coverage.py", "profile 里没有配置 python.interpreter / python.test_cmd")
        return
    pylib = ctx.kit_root / ".tools" / "pylib"
    py = cmd[0]
    rc, _, _ = run([py, "-c", "import coverage"], env={"PYTHONPATH": str(pylib)}, timeout=60)
    if rc != 0:
        pylib.mkdir(parents=True, exist_ok=True)
        run([py, "-m", "pip", "install", "-q", "--disable-pip-version-check", "--target", str(pylib), "coverage"], timeout=600)
        rc, _, _ = run([py, "-c", "import coverage"], env={"PYTHONPATH": str(pylib)}, timeout=60)
        if rc != 0:
            res.skip("Coverage.py", "无法为项目解释器安装 coverage（装在审查系统目录内，不动项目环境）")
            return
    cov_file = ctx.raw / ".coverage"
    env = {"PYTHONPATH": str(pylib), "COVERAGE_FILE": str(cov_file)}
    omit = ",".join(["*/.venv/*", "*/site-packages/*"] + [g if g.startswith("*") else g for g in tests_as_omit(ctx)])
    rc, out, err, secs = timed([py, "-m", "coverage", "run", "--branch", "--source=.", f"--omit={omit}", *cmd[1:]],
                               cwd=ctx.base, env=env, timeout=int(ctx.cfg.get("python", {}).get("test_timeout", 1800)))
    text = out + "\n" + err
    (ctx.raw / "tests_baseline_coverage.txt").write_text(text, encoding="utf-8")
    t = parse_test_output(text)
    t.update(rc=rc, seconds=round(secs, 2), ok=(rc == 0))
    res.metrics["python_tests"] = t
    if rc != 0:
        res.findings.append(Finding(
            category="07", tool="测试运行", rule="tests-failing", severity="critical", file="", line=0,
            message=f"基线测试未通过：失败 {t['failed']}，错误 {t['errors']}（退出码 {rc}）",
            cause="当前代码在测试套件下就有失败，说明存在回归或环境不一致。", fix="先看 raw/tests_baseline_coverage.txt 里的失败用例并修复。"))
    cj = ctx.raw / "coverage.json"
    run([py, "-m", "coverage", "json", "-o", str(cj), "-q"], cwd=ctx.base, env=env, timeout=300)
    if not cj.exists():
        res.skip("Coverage.py", "没有生成覆盖率数据")
        return
    data = json.loads(read_text(cj))
    tot = data.get("totals", {})
    res.metrics["python_coverage"] = {
        "line_pct": round(tot.get("percent_statements_covered", tot.get("percent_covered", 0)), 1),
        "branch_pct": round(100 * tot.get("covered_branches", 0) / tot["num_branches"], 1) if tot.get("num_branches") else None,
        "combined_pct": round(tot.get("percent_covered", 0), 1),
        "statements": tot.get("num_statements", 0), "missing": tot.get("missing_lines", 0)}
    cov = ctx.cfg.get("coverage", {})
    low, min_stmts = cov.get("low_file_pct", 50), cov.get("min_statements", 30)
    per_file = []
    for name, f in data.get("files", {}).items():
        rel = name.replace("\\", "/")
        s = f.get("summary", {})
        if rel not in ctx.files or match_any(rel, ctx.cfg.get("test_globs", [])):
            continue
        pct, n = s.get("percent_covered", 0), s.get("num_statements", 0)
        per_file.append((rel, round(pct, 1), n, s.get("missing_lines", 0)))
        if n >= min_stmts and pct < low:
            miss = f.get("missing_lines") or [0]
            res.findings.append(Finding(
                category="07", tool="Coverage.py", rule="low-coverage", severity="medium" if pct < 20 else "low",
                file=rel, line=miss[0],
                message=f"覆盖率 {pct:.0f}%（{n} 条语句，{s.get('missing_lines', 0)} 条未执行）",
                cause="这个文件的大部分代码没有被任何测试执行到，改动它时回归不会被发现。"
                      + ("（一次性 / 研究脚本可以接受，可在 profile 的 exclude 里排除。）" if pct < 5 else ""),
                fix=f"优先为未覆盖的分支补测试，首个未覆盖行是第 {miss[0]} 行；完整清单见 raw/coverage.json。"))
    res.metrics["python_coverage_files"] = sorted(per_file, key=lambda r: r[1])[:15]
    res.use("Coverage.py（行 + 分支）")


def tests_as_omit(ctx: Context) -> list[str]:
    return [g for g in ctx.cfg.get("test_globs", [])] + [g for g in ctx.cfg.get("exclude", [])]


def _rust_tests(ctx: Context) -> None:
    res = ctx.cat("07")
    n_tests = 0
    for f in ctx.by_ext(".rs"):
        n_tests += len(re.findall(r"#\[(?:tokio::)?test(?:\(.*?\))?\]", read_text(ctx.base / f)))
    res.metrics["rust_test_functions"] = n_tests
    rust = ctx.cfg.get("rust", {})
    if not which("cargo"):
        res.skip("cargo test", "未安装 Rust 工具链")
        return
    if not rust.get("run_tests", False):
        res.skip("cargo test", "profile 里 rust.run_tests=false（静态清点了测试函数数量）")
        return
    has_llvm_cov = run(["cargo", "llvm-cov", "--version"], timeout=30)[0] == 0
    agg = {"ran": 0, "failed": 0, "skipped": 0, "seconds": 0.0, "ok": True}
    for root in cargo_roots(ctx):
        if has_llvm_cov:
            cj = ctx.raw / "rust_coverage.json"
            rc, out, err, secs = timed(["cargo", "llvm-cov", "--workspace", "--json", "--output-path", str(cj)],
                                       cwd=ctx.base / root, env=cargo_env(ctx), timeout=int(rust.get("test_timeout", 3600)))
            if cj.exists():
                t = json.loads(read_text(cj))["data"][0]["totals"]
                res.metrics["rust_coverage"] = {"line_pct": round(t["lines"]["percent"], 1),
                                                "function_pct": round(t["functions"]["percent"], 1)}
                res.use("cargo-llvm-cov")
        else:
            rc, out, err, secs = timed(["cargo", "test", "--workspace", "--locked"], cwd=ctx.base / root,
                                       env=cargo_env(ctx), timeout=int(rust.get("test_timeout", 3600)))
        text = out + "\n" + err
        (ctx.raw / "tests_rust_baseline.txt").write_text(text, encoding="utf-8")
        t = parse_test_output(text)
        agg["ran"] += t["ran"] or 0
        agg["failed"] += t["failed"]
        agg["skipped"] += t["skipped"]
        agg["seconds"] += round(secs, 1)
        agg["ok"] = agg["ok"] and rc == 0
        if rc != 0:
            failed = re.findall(r"^test (\S+) \.\.\. FAILED", text, re.M)[:8]
            res.findings.append(Finding(
                category="07", tool="cargo test", rule="tests-failing", severity="critical", file=f"{root}/Cargo.toml", line=1,
                message=f"Rust 基线测试未通过（退出码 {rc}）" + ("：" + "、".join(failed) if failed else ""),
                cause="当前代码在测试套件下就有失败（也可能是本机环境与 CI 不一致）。", fix="看 raw/tests_rust_baseline.txt 里的失败用例。"))
    res.metrics["rust_tests"] = agg
    res.use("cargo test")
    if not has_llvm_cov:
        res.skip("cargo-llvm-cov", "未安装，Rust 只有通过 / 失败数，没有行覆盖率（cargo install cargo-llvm-cov 后自动启用）")


# ====================================================================== 08 数据库
SQL_HEAD = re.compile(r"^\s*(SELECT\b[\s\S]*\bFROM\b|INSERT\s+(OR\s+\w+\s+)?INTO\b|UPDATE\s+\w+\s+SET\b|DELETE\s+FROM\b|"
                      r"CREATE\s+(UNIQUE\s+)?(TABLE|INDEX|VIEW|TRIGGER)\b|ALTER\s+TABLE\b|WITH\s+\w+\s+AS\b|REPLACE\s+INTO\b)")
RUST_STR = re.compile(r'r(#*)"([\s\S]*?)"\1|"((?:[^"\\]|\\[\s\S])*)"')


def _unescape_rust(s: str) -> str:
    s = re.sub(r"\\\r?\n\s*", "", s)  # 行尾反斜杠续行
    return s.replace('\\"', '"').replace("\\n", "\n").replace("\\t", " ").replace("\\\\", "\\")


def extract_sql(ctx: Context) -> list[dict]:
    found: list[dict] = []
    for f in ctx.by_ext(".py"):
        try:
            tree = ast.parse(read_text(ctx.base / f))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and SQL_HEAD.match(node.value):
                found.append({"file": f, "line": node.lineno, "sql": node.value, "dynamic": False})
            elif isinstance(node, ast.JoinedStr):
                parts = [v.value if isinstance(v, ast.Constant) and isinstance(v.value, str) else "{}" for v in node.values]
                s = "".join(parts)
                if SQL_HEAD.match(s):
                    found.append({"file": f, "line": node.lineno, "sql": s, "dynamic": True})
    for f in ctx.by_ext(".rs"):
        text = read_text(ctx.base / f)
        for m in RUST_STR.finditer(text):
            raw = m.group(2) if m.group(2) is not None else _unescape_rust(m.group(3) or "")
            if SQL_HEAD.match(raw):
                pre = text[max(0, m.start() - 12):m.start()]
                dyn = bool(re.search(r"\{[^}]*\}", raw)) and "format!" in pre.replace(" ", "").replace("\n", "")[-9:] + "format!" * 0 \
                    or bool(re.search(r"format!\(\s*$", text[max(0, m.start() - 20):m.start()])) and "{" in raw
                found.append({"file": f, "line": text.count("\n", 0, m.start()) + 1, "sql": raw, "dynamic": dyn})
    return found


def run_database(ctx: Context) -> None:
    res = ctx.cat("08")
    stmts = extract_sql(ctx)
    db_globs = ctx.cfg.get("database", {}).get("sqlite_globs", [])
    if not stmts and not db_globs and not ctx.by_ext(".sql"):
        res.applicable = False
        res.notes.append("没有发现 SQL 语句或数据库文件。")
        return
    tests = ctx.cfg.get("test_globs", [])
    is_test = lambda f: match_any(f, tests) or "/tests/" in f or "/examples/" in f  # noqa: E731
    mem = sqlite3.connect(":memory:")
    ddl = 0
    for s in sorted(stmts, key=lambda s: is_test(s["file"])):
        for part in s["sql"].split(";"):
            if re.match(r"\s*CREATE\s", part) and not (is_test(s["file"]) and ddl):
                try:
                    mem.execute(part)
                    ddl += 1
                except sqlite3.Error:
                    pass
    tables = [r[0] for r in mem.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    indexes = [r[0] for r in mem.execute("SELECT name FROM sqlite_master WHERE type='index' AND sql IS NOT NULL")]
    checked = unverified = dynamic = 0
    for s in stmts:
        if is_test(s["file"]):
            continue
        if s["dynamic"]:
            dynamic += 1
            if s["file"].endswith(".rs"):  # Python 侧由 Ruff S608 报告
                res.findings.append(Finding(
                    category="08", tool="SQL 提取", rule="dynamic-sql", severity="low", file=s["file"], line=s["line"],
                    message="用 format! 拼接的 SQL：" + " ".join(s["sql"].split())[:120],
                    cause="拼接进 SQL 的片段不经过参数绑定；片段来自外部输入时是注入点，另外每次文本不同也用不上预编译缓存。",
                    fix="值一律用 `?` 绑定；只有占位符个数、表名这类结构性片段才拼接，并确认来源是代码内常量。"))
            continue
        for part in s["sql"].split(";"):
            part = part.strip()
            if not part or re.match(r"(CREATE|ALTER|DROP|PRAGMA)\b", part):
                continue
            if re.search(r"SELECT\s+\*", part):
                res.findings.append(Finding(
                    category="08", tool="SQL 提取", rule="select-star", severity="low", file=s["file"], line=s["line"],
                    message="SELECT *：" + " ".join(part.split())[:120],
                    cause="取回所有列：表结构一加列，读取量和按位置取值的代码都会受影响。", fix="列出实际用到的列。"))
            n = len(re.findall(r"\?(?!\d)", part))
            numbered = [int(x) for x in re.findall(r"\?(\d+)", part)]
            named = sorted(set(re.findall(r"(?<![:\w])[:@$]([A-Za-z_]\w*)", part)))
            try:
                params = {k: None for k in named} if named and not n else [None] * max(n, max(numbered, default=0))
                plan = mem.execute("EXPLAIN QUERY PLAN " + part, params).fetchall()
            except sqlite3.Error:
                unverified += 1
                continue
            checked += 1
            has_filter = bool(re.search(r"\b(WHERE|JOIN)\b", part, re.I))
            for row in plan:
                detail = row[-1]
                m = re.match(r"SCAN (\w+)", detail)
                if m and has_filter and "USING" not in detail and m.group(1) in tables:
                    res.findings.append(Finding(
                        category="08", tool="EXPLAIN QUERY PLAN", rule="full-table-scan", severity="medium", file=s["file"], line=s["line"],
                        message=f"带条件的语句对表 {m.group(1)} 做全表扫描：" + " ".join(part.split())[:110],
                        cause="WHERE / JOIN 用到的列没有可用索引，表越大越慢（执行计划：" + detail + "）。",
                        fix=f"给 {m.group(1)} 上被过滤的列建索引，或改写条件让已有索引可用；表确定很小（几十行）可以忽略。",
                        fix_code=_index_hint(part, m.group(1))))
                elif "USE TEMP B-TREE FOR ORDER BY" in detail:
                    res.findings.append(Finding(
                        category="08", tool="EXPLAIN QUERY PLAN", rule="sort-without-index", severity="low", file=s["file"], line=s["line"],
                        message="ORDER BY 需要临时排序：" + " ".join(part.split())[:110],
                        cause="排序列没有索引，每次查询都要把结果集整体排序。", fix="结果集大时给排序列建索引；小表可以忽略。"))
    res.metrics.update(sql_statements=len([s for s in stmts if not is_test(s["file"])]), sql_checked=checked,
                       sql_unverified=unverified, sql_dynamic=dynamic, schema_tables=tables, schema_indexes=indexes)
    res.use("SQL 提取 + EXPLAIN QUERY PLAN（SQLite 内存库，按代码里的建表语句建库）")
    mem.close()
    _sample_dbs(ctx, db_globs)
    res.skip("pt-query-digest / pgBadger / MySQL Workbench", "面向 MySQL / PostgreSQL 的慢日志工具，本项目只有 SQLite")


def _index_hint(sql: str, table: str) -> str:
    m = re.search(r"\bWHERE\s+(\w+)\s*(=|<|>|IN|LIKE)", sql, re.I)
    return f"CREATE INDEX IF NOT EXISTS idx_{table}_{m.group(1)} ON {table}({m.group(1)});" if m else ""


def _sample_dbs(ctx: Context, globs: list[str]) -> None:
    """对项目里已有的 SQLite 文件做只读体检（immutable 打开，不产生 -wal / -shm 文件）。"""
    res = ctx.cat("08")
    paths: list[str] = []
    for g in globs:
        paths += glob.glob(str(ctx.target / g), recursive=True)
    limit = int(ctx.cfg.get("database", {}).get("max_db_mb", 300)) * 1024 * 1024
    rows = []
    for p in sorted(set(paths))[:40]:
        rel = Path(p).relative_to(ctx.target).as_posix()
        try:
            if os.path.getsize(p) > limit:
                continue
            uri = "file:" + Path(p).as_posix().replace("?", "%3f").replace("#", "%23") + "?mode=ro&immutable=1"
            db = sqlite3.connect(uri, uri=True, timeout=5)
            ok = db.execute("PRAGMA quick_check").fetchone()[0]
            pages = db.execute("PRAGMA page_count").fetchone()[0]
            free = db.execute("PRAGMA freelist_count").fetchone()[0]
            psize = db.execute("PRAGMA page_size").fetchone()[0]
            mode = db.execute("PRAGMA journal_mode").fetchone()[0]
            db.close()
        except (sqlite3.Error, OSError) as e:
            rows.append((rel, f"无法读取：{type(e).__name__}", 0, 0, ""))
            continue
        frag = round(100 * free / pages, 1) if pages else 0
        rows.append((rel, ok, round(pages * psize / 1048576, 2), frag, mode))
        if ok != "ok":
            res.findings.append(Finding(
                category="08", tool="SQLite quick_check", rule="db-corruption", severity="high", file=rel, line=0,
                message=f"数据库完整性检查未通过：{str(ok)[:120]}", cause="文件损坏或拷贝时数据库正在写入。",
                fix="从正在运行的库拷贝时用 `.backup` / VACUUM INTO，不要直接复制文件。"))
        elif frag >= 25 and pages * psize > 20 * 1048576:
            res.findings.append(Finding(
                category="08", tool="SQLite 体检", rule="db-fragmentation", severity="low", file=rel, line=0,
                message=f"空闲页占 {frag}%（文件 {pages * psize / 1048576:.0f} MB）", cause="大量删除后空间没有归还，文件偏大、扫描变慢。",
                fix="停机窗口执行 VACUUM，或开启 auto_vacuum=INCREMENTAL。"))
    if rows:
        res.metrics["sqlite_files"] = rows
        res.use("SQLite 文件只读体检（quick_check、空闲页、journal_mode）")
