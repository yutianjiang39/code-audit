"""04 代码瘦身（Vulture、jscpd、体积统计）与 05 架构（import 图、crate 图、Graphviz DOT）。"""
from __future__ import annotations

import ast
import json
import re
from collections import defaultdict
from pathlib import Path

from ..model import Context, Finding
from ..util import kit_python, match_any, read_text, run, size_stats, which
from ..linters import cargo_roots, cargo_env


# ====================================================================== 04 瘦身
def run_slim(ctx: Context) -> None:
    res = ctx.cat("04")
    res.metrics["size"] = size_stats(ctx.base, ctx.files)
    _vulture(ctx)
    _jscpd(ctx)
    _unused_deps(ctx)


def _vulture(ctx: Context) -> None:
    res = ctx.cat("04")
    files = ctx.by_ext(".py")
    if not files:
        return
    py = kit_python(ctx.kit_root)
    conf = int(ctx.cfg.get("slim", {}).get("vulture_min_confidence", 60))
    rc, out, err = run([py, "-m", "vulture", *files, "--min-confidence", str(conf), "--sort-by-size"],
                       cwd=ctx.base, timeout=600)
    if rc not in (0, 3):
        res.skip("Vulture", (err.strip().splitlines() or ["运行失败"])[-1][:160])
        return
    (ctx.raw / "vulture.txt").write_text(out, encoding="utf-8")
    tests = ctx.cfg.get("test_globs", [])
    dead_lines = 0
    for line in out.splitlines():
        m = re.match(r"(.+?):(\d+): (.+?) \((\d+)% confidence(?:, (\d+) lines?)?\)", line)
        if not m:
            continue
        rel = m.group(1).replace("\\", "/")
        if match_any(rel, tests):
            continue
        pct, n = int(m.group(4)), int(m.group(5) or 1)
        kind = m.group(3)
        if "import" in kind or "unused variable" in kind and pct < 100:
            continue  # Ruff F401 / F841 已覆盖
        dead_lines += n
        res.findings.append(Finding(
            category="04", tool="Vulture", rule="dead-code", severity="medium" if pct >= 90 else "low", file=rel,
            line=int(m.group(2)), message=f"{kind}（约 {n} 行，置信度 {pct}%）",
            cause="全项目没有找到对它的引用，很可能是重构后遗留的死代码。"
                  + ("置信度 60% 表示可能有动态调用（getattr、反射、框架回调），删除前先全局搜索确认。" if pct < 90 else ""),
            fix="全局搜索名字确认无引用后删除；确实通过动态方式使用的，加入 Vulture 白名单。"))
    res.metrics["dead_code_lines_est"] = dead_lines
    res.use("Vulture")


def _jscpd(ctx: Context) -> None:
    res = ctx.cat("04")
    npx = which("npx")
    if not npx:
        res.skip("jscpd", "未安装 Node.js（npx）")
        return
    fmts = [n for n, exts in (("python", (".py",)), ("rust", (".rs",)), ("javascript", (".js", ".jsx")),
                              ("typescript", (".ts", ".tsx")), ("java", (".java",)), ("go", (".go",)))
            if ctx.by_ext(*exts)]
    if not fmts:
        return
    out_dir = ctx.raw / "jscpd"
    slim = ctx.cfg.get("slim", {})
    ignore = ["**/.venv/**", "**/target/**", "**/node_modules/**"] + \
             [f"**/{g}" if not g.startswith("**") else g for g in ctx.cfg.get("exclude", []) + ctx.cfg.get("test_globs", [])]
    rc, out, err = run([npx, "--yes", "jscpd@4", ".", "--reporters", "json", "--output", str(out_dir), "--silent",
                        "--min-lines", str(slim.get("dup_min_lines", 10)), "--min-tokens", str(slim.get("dup_min_tokens", 100)),
                        "--format", ",".join(fmts), "--ignore", ",".join(ignore), "--gitignore"],
                       cwd=ctx.base, timeout=900)
    report = out_dir / "jscpd-report.json"
    if not report.exists():
        res.skip("jscpd", f"运行失败：{(err or out).strip()[-160:]}")
        return
    data = json.loads(read_text(report))
    total = data.get("statistics", {}).get("total", {})
    res.metrics["duplication"] = {"percentage": total.get("percentage", 0), "lines": total.get("duplicatedLines", 0),
                                  "clones": total.get("clones", 0)}
    for d in data.get("duplicates", []):
        a, b = d.get("firstFile", {}), d.get("secondFile", {})
        fa, fb = a.get("name", "").replace("\\", "/"), b.get("name", "").replace("\\", "/")
        if fa not in ctx.files or fb not in ctx.files:
            continue
        n = d.get("lines", 0)
        res.findings.append(Finding(
            category="04", tool="jscpd", rule="duplicate-code", severity="medium" if n >= 30 else "low",
            file=fa, line=a.get("start", 0),
            message=f"{n} 行重复代码：{fa}:{a.get('start')}-{a.get('end')} 与 {fb}:{b.get('start')}-{b.get('end')}",
            cause="同一段逻辑复制了两份，改一处时另一处容易漏改，行为会悄悄分叉。",
            fix="抽成一个共用函数 / 模块，两处都调用它；若两份必须独立演化，在两处互相注明。"))
    res.use("jscpd")


def _unused_deps(ctx: Context) -> None:
    """声明了但没有 import 的 Python 依赖（requirements*.txt 对照 import 语句）。"""
    res = ctx.cat("04")
    reqs = [f for f in ctx.files if re.fullmatch(r"requirements[^/]*\.txt", Path(f).name) and "/" not in f]
    if not reqs or not ctx.by_ext(".py"):
        return
    imported: set[str] = set()
    for f in ctx.by_ext(".py"):
        try:
            tree = ast.parse(read_text(ctx.base / f))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported |= {a.name.split(".")[0].lower() for a in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                imported.add(node.module.split(".")[0].lower())
    alias = {"pyyaml": "yaml", "pillow": "pil", "beautifulsoup4": "bs4", "scikit-learn": "sklearn", "python-dateutil": "dateutil",
             "opencv-python": "cv2", "pyjwt": "jwt", "python-dotenv": "dotenv",
             **{k.lower(): v.lower() for k, v in ctx.cfg.get("slim", {}).get("import_names", {}).items()}}
    dev_tools = {"ruff", "pytest", "black", "mypy", "pylint", "coverage", "pytest-cov", "pre-commit", "maturin", "pip", "wheel", "setuptools"}
    for req in reqs:
        for i, line in enumerate(read_text(ctx.base / req).splitlines(), 1):
            m = re.match(r"\s*([A-Za-z0-9][A-Za-z0-9._-]*)", line)
            if not m or line.strip().startswith(("#", "-")):
                continue
            name = m.group(1).lower()
            mod = alias.get(name, name.replace("-", "_"))
            if name in dev_tools or mod in imported:
                continue
            res.findings.append(Finding(
                category="04", tool="依赖对照", rule="unused-dependency", severity="low", file=req, line=i,
                message=f"依赖 {m.group(1)} 在代码里没有被 import",
                cause="可能是功能删除后忘了移除依赖，白占安装时间和体积，也多一个漏洞面。也可能是命令行工具或被其他包间接需要（需人工确认）。",
                fix="确认没有运行时用途后从 requirements 里删除。"))
    res.use("依赖对照（requirements ↔ import）")


# ====================================================================== 05 架构
def run_arch(ctx: Context) -> None:
    _py_graph(ctx)
    _crate_graph(ctx)
    _big_files(ctx)


def _py_graph(ctx: Context) -> None:
    res = ctx.cat("05")
    files = ctx.by_ext(".py")
    if not files:
        return
    mod_of = {}
    for f in files:
        parts = f[:-3].split("/")
        if parts[-1] == "__init__":
            parts = parts[:-1]
        if parts:
            mod_of[".".join(parts)] = f
    file_mod = {v: k for k, v in mod_of.items()}

    def resolve(name: str, src: str) -> str | None:
        sib = (str(Path(src).parent.as_posix()).replace("/", ".") + "." + name).lstrip(".")
        for cand in (name, sib):
            parts = cand.split(".")
            while parts:
                m = ".".join(parts)
                if m in mod_of:
                    return m
                parts.pop()
        return None

    edges: dict[str, set[str]] = defaultdict(set)  # 模块级 import（决定加载顺序）
    lazy: dict[str, set[str]] = defaultdict(set)  # 函数内延迟 import
    where: dict[tuple[str, str], int] = {}
    for f in files:
        try:
            tree = ast.parse(read_text(ctx.base / f))
        except SyntaxError:
            continue
        me = file_mod[f]
        top_nodes = set()
        stack = list(tree.body)
        while stack:  # 模块顶层（含 if / try 块内）
            n = stack.pop()
            if isinstance(n, (ast.Import, ast.ImportFrom)):
                top_nodes.add(id(n))
            elif isinstance(n, (ast.If, ast.Try, ast.With)):
                for attr in ("body", "orelse", "finalbody", "handlers"):
                    for c in getattr(n, attr, []) or []:
                        stack.extend(c.body if isinstance(c, ast.ExceptHandler) else [c])
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                base = node.module or ""
                if node.level:
                    pkg = me.split(".")
                    if not f.endswith("__init__.py"):
                        pkg = pkg[:-1]
                    pkg = pkg[:len(pkg) - (node.level - 1)] if node.level > 1 else pkg
                    base = ".".join(pkg + ([base] if base else []))
                names = [f"{base}.{a.name}" for a in node.names] if base else [a.name for a in node.names]
            for nm in names:
                tgt = resolve(nm, f)
                if tgt and tgt != me:
                    (edges if id(node) in top_nodes else lazy)[me].add(tgt)
                    where.setdefault((me, tgt), node.lineno)
    res.metrics["python_modules"] = len(mod_of)
    res.metrics["python_import_edges"] = sum(len(v) for v in edges.values())

    # 循环依赖（Tarjan 强连通分量，只看模块级 import）
    cycles = [c for c in _scc(set(mod_of), edges) if len(c) > 1]
    tests = ctx.cfg.get("test_globs", [])
    for comp in cycles:
        comp = sorted(comp)
        a = comp[0]
        b = next((t for t in sorted(edges[a]) if t in comp), comp[1])
        res.findings.append(Finding(
            category="05", tool="import 图分析", rule="import-cycle", severity="medium" if len(comp) <= 3 else "high",
            file=mod_of[a], line=where.get((a, b), 1),
            message=f"{len(comp)} 个模块循环 import：" + " ↔ ".join(comp[:6]) + (" …" if len(comp) > 6 else ""),
            cause="这些模块在加载时互相依赖：import 顺序一变就可能拿到未初始化完的模块，也无法单独测试或复用其中一个。",
            fix="把双方共用的类型 / 常量下移到一个更底层的模块；或把其中一个方向改成函数内延迟 import、通过参数注入。"))
    res.metrics["python_import_cycles"] = len(cycles)

    # 分层规则
    allowed = ctx.cfg.get("architecture", {}).get("allowed_imports", {})
    if allowed:
        def layer(mod: str) -> str | None:
            best = None
            for k in allowed:
                if (mod == k or mod.startswith(k + ".")) and (best is None or len(k) > len(best)):
                    best = k
            return best
        viol = 0
        for src in sorted(mod_of):
            ls = layer(src)
            if ls is None:
                continue
            for tgt in sorted(edges[src] | lazy[src]):
                if not any(tgt == a or tgt.startswith(a + ".") for a in allowed[ls]):
                    viol += 1
                    res.findings.append(Finding(
                        category="05", tool="import 图分析", rule="layer-violation", severity="high", file=mod_of[src],
                        line=where.get((src, tgt), 1), message=f"{src}（{ls} 层）import 了 {tgt}，不在允许的依赖方向内",
                        cause=f"分层规则规定 {ls} 只能依赖 {', '.join(allowed[ls])}。反向依赖会让底层模块无法独立部署和测试。",
                        fix="把被依赖的部分下移到允许的层，或反转依赖（由上层把对象传进来）。"))
        res.metrics["layer_violations"] = viol

    # 扇入 / 扇出
    fan_out = {m: len(edges[m] | lazy[m]) for m in mod_of}
    fan_in: dict[str, int] = defaultdict(int)
    for m in mod_of:
        for t in edges[m] | lazy[m]:
            fan_in[t] += 1
    limit = int(ctx.cfg.get("architecture", {}).get("max_fan_out", 15))
    for m, n in sorted(fan_out.items(), key=lambda kv: -kv[1]):
        if n > limit and not match_any(mod_of[m], tests):
            res.findings.append(Finding(
                category="05", tool="import 图分析", rule="high-fan-out", severity="low", file=mod_of[m], line=1,
                message=f"{m} 直接依赖 {n} 个项目内模块（上限 {limit}）",
                cause="一个模块知道的东西太多：任何一个依赖变化都可能波及它，通常说明它承担了多项职责。",
                fix="按职责拆分，或引入一个门面模块收敛依赖。"))
    res.metrics["top_fan_in"] = sorted(fan_in.items(), key=lambda kv: -kv[1])[:8]
    res.metrics["top_fan_out"] = sorted(fan_out.items(), key=lambda kv: -kv[1])[:8]

    # 包级依赖图：Graphviz DOT（dot -Tsvg architecture_python.dot 可出图）
    def pkg(m: str) -> str:
        p = m.split(".")
        return ".".join(p[:2]) if len(p) > 2 else p[0]
    pe: dict[tuple[str, str], int] = defaultdict(int)
    for s in mod_of:
        for t in edges[s] | lazy[s]:
            if pkg(s) != pkg(t) and not match_any(mod_of[s], tests):
                pe[(pkg(s), pkg(t))] += 1
    dot = ["digraph python_packages {", "  rankdir=LR; node [shape=box, fontname=\"sans-serif\"];"]
    dot += [f'  "{a}" -> "{b}" [label="{n}"];' for (a, b), n in sorted(pe.items())]
    dot.append("}")
    (ctx.out / "architecture_python.dot").write_text("\n".join(dot), encoding="utf-8")
    ctx.shared["py_pkg_edges"] = sorted(pe.items())
    res.use("import 图分析（AST，输出 Graphviz DOT）")


def _scc(nodes: set[str], edges: dict[str, set[str]]) -> list[list[str]]:
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    on: set[str] = set()
    stack: list[str] = []
    out: list[list[str]] = []
    counter = 0
    for root in sorted(nodes):
        if root in index:
            continue
        work = [(root, iter(sorted(edges.get(root, ()))))]
        index[root] = low[root] = counter
        counter += 1
        stack.append(root)
        on.add(root)
        while work:
            v, it = work[-1]
            advanced = False
            for w in it:
                if w not in nodes:
                    continue
                if w not in index:
                    index[w] = low[w] = counter
                    counter += 1
                    stack.append(w)
                    on.add(w)
                    work.append((w, iter(sorted(edges.get(w, ())))))
                    advanced = True
                    break
                if w in on:
                    low[v] = min(low[v], index[w])
            if advanced:
                continue
            work.pop()
            if work:
                low[work[-1][0]] = min(low[work[-1][0]], low[v])
            if low[v] == index[v]:
                comp = []
                while True:
                    w = stack.pop()
                    on.discard(w)
                    comp.append(w)
                    if w == v:
                        break
                out.append(comp)
    return out


def _crate_graph(ctx: Context) -> None:
    res = ctx.cat("05")
    if not ctx.by_ext(".rs") or not which("cargo"):
        return
    rank = ctx.cfg.get("architecture", {}).get("crate_rank", {})
    for root in cargo_roots(ctx):
        rc, out, err = run(["cargo", "metadata", "--no-deps", "--format-version", "1", "--offline"],
                           cwd=ctx.base / root, env=cargo_env(ctx), timeout=300)
        if rc != 0:
            res.skip("cargo metadata", (err.strip().splitlines() or ["失败"])[-1][:160])
            continue
        meta = json.loads(out)
        names = {p["name"] for p in meta["packages"]}
        dot = ["digraph crates {", "  rankdir=LR; node [shape=box, fontname=\"sans-serif\"];"]
        n_edges = 0
        for p in sorted(meta["packages"], key=lambda p: p["name"]):
            for d in p["dependencies"]:
                if d["name"] not in names:
                    continue
                dev = d.get("kind") == "dev"
                n_edges += 1
                dot.append(f'  "{p["name"]}" -> "{d["name"]}"' + (' [style=dashed, label="dev"];' if dev else ";"))
                if rank and not dev and not d.get("optional") and p["name"] in rank and d["name"] in rank \
                        and rank[d["name"]] >= rank[p["name"]]:
                    man = Path(p["manifest_path"]).as_posix()
                    rel = man.split(ctx.base.as_posix() + "/")[-1]
                    res.findings.append(Finding(
                        category="05", tool="cargo metadata", rule="crate-layer-violation", severity="high", file=rel, line=1,
                        message=f"crate {p['name']}（层级 {rank[p['name']]}）依赖了不低于它的 {d['name']}（层级 {rank[d['name']]}）",
                        cause="crate 只能依赖更低层的 crate；平级或向上的依赖会让层次塌掉，改动波及面变大。",
                        fix="把共用部分下移到更低层的 crate，或改成 dev-dependency / optional 特性。"))
        dot.append("}")
        (ctx.out / "architecture_crates.dot").write_text("\n".join(dot), encoding="utf-8")
        res.metrics["rust_crates"] = len(names)
        res.metrics["rust_crate_edges"] = n_edges
        res.use("cargo metadata（crate 依赖图，输出 Graphviz DOT）")


def _big_files(ctx: Context) -> None:
    res = ctx.cat("05")
    limit = int(ctx.cfg.get("architecture", {}).get("max_file_lines", 1200))
    tests = ctx.cfg.get("test_globs", [])
    for f in ctx.by_ext(".rs", ".go", ".java", ".ts", ".tsx", ".js"):
        if match_any(f, tests) or "/tests/" in f:
            continue
        n = read_text(ctx.base / f).count("\n")
        if n > limit:
            res.findings.append(Finding(
                category="05", tool="体积统计", rule="oversized-file", severity="low", file=f, line=1,
                message=f"单文件 {n} 行（上限 {limit}）",
                cause="文件过大通常意味着承担了多项职责，评审和定位都变慢，合并冲突也更多。",
                fix="按职责拆成子模块（例如把内嵌的测试、类型定义、各阶段逻辑分开）。"))
