"""修复阶段：在 fixed 副本里应用安全的自动修复，生成补丁，比较体积、性能，并重跑测试。

原项目始终不动：补丁交给使用者自己 `git apply`。
"""
from __future__ import annotations

import difflib
import json
import statistics

from .model import Context
from .util import kit_tool, run, size_stats, sync_copy, timed, which
from .linters import apply_ruff_fixes, ruff_args
from . import knowledge as K
from .checks.tests_db import project_python, run_tests_in


def run_fix_stage(ctx: Context) -> dict:
    all_files = ctx.shared["all_files"]
    ctx.say("  · 建立 fixed 副本并应用自动修复")
    sync_copy(ctx.base, ctx.fixed, all_files)
    n_fixed = apply_ruff_fixes(ctx)
    changed, patch = _make_patch(ctx)
    (ctx.out / "fixes.patch").write_text(patch, encoding="utf-8", newline="")
    r: dict = {"fixes_applied": n_fixed, "files_changed": len(changed), "changed_files": changed,
               "patch_lines": patch.count("\n"), "patch_check": _check_patch(ctx) if changed else None}
    r["syntax_ok"] = _compile_check(ctx, changed)
    r["size"] = _size_compare(ctx)
    r["issues"] = _recount(ctx)
    ctx.say("  · 在 fixed 副本里重跑测试")
    r["tests_after"] = run_tests_in(ctx, ctx.fixed, "fixed")
    r["tests_before"] = ctx.cat("07").metrics.get("python_tests")
    ctx.say("  · 性能基准：修复前 / 修复后")
    r["bench"] = _bench(ctx)
    (ctx.raw / "fix_stage.json").write_text(json.dumps(r, ensure_ascii=False, indent=1), encoding="utf-8")
    return r


def _make_patch(ctx: Context) -> tuple[list[str], str]:
    changed, parts = [], []
    for rel in ctx.files:
        a, b = ctx.base / rel, ctx.fixed / rel
        try:
            da, db = a.read_bytes(), b.read_bytes()
        except OSError:
            continue
        if da == db:
            continue
        changed.append(rel)
        la = da.decode("utf-8", "replace").splitlines(keepends=True)
        lb = db.decode("utf-8", "replace").splitlines(keepends=True)
        parts.append(f"diff --git a/{rel} b/{rel}\n")
        for line in difflib.unified_diff(la, lb, f"a/{rel}", f"b/{rel}", n=3):
            parts.append(line if line.endswith("\n") else line + "\n\\ No newline at end of file\n")
    return changed, "".join(parts)


def _check_patch(ctx: Context) -> str:
    """只做 `git apply --check`（只读），确认补丁能直接套到原项目上。"""
    if not which("git") or not (ctx.target / ".git").exists():
        return "原项目不是 git 仓库，未验证；可以用 `patch -p1 < fixes.patch` 应用"
    rc, out, err = run(["git", "apply", "--check", str(ctx.out / "fixes.patch")], cwd=ctx.target, timeout=120)
    return "可直接应用（git apply --check 通过）" if rc == 0 else "与当前工作区有冲突：" + (err.strip().splitlines() or ["?"])[0][:160]


def _compile_check(ctx: Context, changed: list[str]) -> bool:
    py = project_python(ctx) or "python"
    files = [f for f in changed if f.endswith(".py")]
    if not files:
        return True
    code = "import sys,ast\nbad=0\nfor p in sys.argv[1:]:\n    try: ast.parse(open(p,encoding='utf-8').read(),p)\n    except SyntaxError as e: print(p,e); bad=1\nsys.exit(bad)"
    return all(run([py, "-c", code, *files[i:i + 100]], cwd=ctx.fixed, timeout=120)[0] == 0 for i in range(0, len(files), 100))


def _size_compare(ctx: Context) -> dict:
    before, after = size_stats(ctx.base, ctx.files), size_stats(ctx.fixed, ctx.files)
    slim = ctx.cat("04").metrics
    dead = slim.get("dead_code_lines_est", 0)
    dup = (slim.get("duplication") or {}).get("lines", 0)
    return {"before": before, "after": after, "potential": {"dead_code_lines": dead, "duplicated_lines": dup,
            "note": "死代码与重复代码需要人工确认后删除，不在自动补丁里；重复行按保留一份估算可删一半。"}}


def _recount(ctx: Context) -> dict | None:
    """修复前后各跑一遍同一组 Ruff 规则，对比问题条数。"""
    ruff = kit_tool(ctx.kit_root, "ruff")
    files = ctx.by_ext(".py")
    if not ruff or not files:
        return None
    select = ctx.cfg.get("python", {}).get("ruff_select", K.RUFF_SELECT)
    ignore = K.RUFF_IGNORE + ctx.cfg.get("python", {}).get("ruff_ignore", [])

    def count(root) -> int:
        n = 0
        for i in range(0, len(files), 200):
            rc, out, err = run([ruff, "check", *ruff_args(ctx), "--select", ",".join(select), "--ignore", ",".join(ignore),
                                "--output-format", "json", "--exit-zero", *files[i:i + 200]], cwd=root, timeout=600)
            try:
                n += len(json.loads(out or "[]"))
            except ValueError:
                pass
        return n
    return {"before": ctx.shared.get("ruff_count", count(ctx.base)), "after": count(ctx.fixed)}


def _bench(ctx: Context) -> list[dict]:
    """每个基准在两个副本里交替运行 N 次，取中位数和最小值（交替是为了抵消机器负载漂移）。"""
    py = project_python(ctx) or "python"
    rows = []
    for b in ctx.cfg.get("bench", []):
        cmd = [c.replace("{python}", py).replace("{target}", str(ctx.target)) for c in b["cmd"]]
        n = int(b.get("repeat", 5))
        t: dict[str, list[float]] = {"before": [], "after": []}
        ok = True
        for label, root in (("before", ctx.base), ("after", ctx.fixed)):  # 预热，不计时
            ok = ok and run(cmd, cwd=root / b.get("cwd", "."), timeout=int(b.get("timeout", 600)))[0] == 0
        if not ok:
            rows.append({"name": b["name"], "error": "命令执行失败（退出码非 0）"})
            continue
        for _ in range(n):
            for label, root in (("before", ctx.base), ("after", ctx.fixed)):
                rc, _, _, secs = timed(cmd, cwd=root / b.get("cwd", "."), timeout=int(b.get("timeout", 600)))
                t[label].append(secs)
        mb, ma = statistics.median(t["before"]), statistics.median(t["after"])
        spread = max(statistics.pstdev(t["before"]) / mb, statistics.pstdev(t["after"]) / ma) if mb and ma else 0
        rows.append({"name": b["name"], "repeat": n, "before_median": round(mb, 3), "after_median": round(ma, 3),
                     "before_min": round(min(t["before"]), 3), "after_min": round(min(t["after"]), 3),
                     "change_pct": round(100 * (ma - mb) / mb, 1) if mb else 0, "noise_pct": round(100 * spread, 1)})
    return rows
