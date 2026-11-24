#!/usr/bin/env python3
"""代码综合审查系统（质量 / 性能 / 安全 / 瘦身）——命令行入口。

    python audit.py setup                      安装审查工具到本目录的 .venv（只需一次）
    python audit.py init <项目路径> [--name x]  为一个新项目生成 profiles/x.json
    python audit.py run profiles/x.json        审查，报告写到 reports/x/<时间>/

本目录自成一体：不往被审查项目里写任何文件，整个文件夹可以原样拷到别处使用。
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import traceback
from pathlib import Path

KIT = Path(__file__).resolve().parent
sys.path.insert(0, str(KIT))

from auditkit import __version__  # noqa: E402
from auditkit import util  # noqa: E402
from auditkit.model import CATEGORIES, MODES, Context, Finding  # noqa: E402
from auditkit.redact import redact  # noqa: E402
from auditkit.util import BIN_DIR, EXE, list_project_files, match_any, sync_copy, write_raw  # noqa: E402


def cmd_setup(_args) -> int:
    venv = KIT / ".venv"
    py = venv / BIN_DIR / ("python" + EXE)
    if not py.exists():
        print("创建虚拟环境 .venv …")
        subprocess.check_call([sys.executable, "-m", "venv", str(venv)])
    print("安装审查工具（ruff、pylint、vulture、semgrep、pip-audit、coverage）…")
    rc = subprocess.call([str(py), "-m", "pip", "install", "-q", "--disable-pip-version-check", "-r", str(KIT / "requirements.txt")])
    from auditkit.util import which
    print("\n可选的外部工具：")
    for tool, why in (("cargo", "Rust 项目：Clippy、cargo test、依赖图"), ("npx", "重复代码检测 jscpd、Lighthouse"),
                      ("git", "文件清单遵循 .gitignore，校验补丁"), ("shellcheck", "shell 脚本检查"), ("cargo-llvm-cov", "Rust 行覆盖率")):
        print(f"  {'✔' if which(tool) else '✘'} {tool:<15} {why}")
    print("\n完成。" if rc == 0 else "\n有工具安装失败，对应的检查会被标记为“未使用”。")
    return rc


def cmd_init(args) -> int:
    target = Path(args.path).resolve()
    if not target.is_dir():
        print(f"找不到目录：{target}")
        return 2
    name = args.name or target.name.lower().replace(" ", "-")
    files = list_project_files(target)
    has = lambda *ext: any(f.endswith(ext) for f in files)  # noqa: E731
    cfg: dict = {"name": name, "target": str(target), "execution": {"mode": "static", "env_passthrough": []}, "exclude": [],
                 "test_globs": ["tests/*", "test/*", "*/tests/*", "*_test.py", "test_*.py"], "accepted": [], "bench": []}
    if has(".py"):
        cand = [target / ".venv" / BIN_DIR / ("python" + EXE), target / "venv" / BIN_DIR / ("python" + EXE)]
        interp = next((str(c).replace(str(target), "{target}") for c in cand if c.exists()), sys.executable)
        runner = ["-m", "pytest", "-q"] if any(f.endswith(("pytest.ini", "conftest.py")) for f in files) else ["-m", "unittest", "discover", "-s", "tests", "-t", "."]
        cfg["python"] = {"interpreter": interp, "min_version": "3.10", "test_cmd": runner}
    if has("Cargo.toml"):
        cfg["rust"] = {"run_tests": True}
    if has(".service", ".sh"):
        cfg["runtime"] = {"log_globs": []}
    out = KIT / "profiles" / f"{name}.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"已生成 {out}\n检查一下 test_cmd / exclude，然后运行：python audit.py run profiles/{name}.json\n"
          "默认是静态模式（不执行项目代码）；要跑测试和覆盖率，加 --mode local，并且只对你信任的代码这样做。")
    return 0


def load_profile(path: str) -> tuple[dict, Path]:
    p = Path(path)
    if not p.exists() and (KIT / "profiles" / path).exists():
        p = KIT / "profiles" / path
    cfg = json.loads(p.read_text(encoding="utf-8"))
    target = Path(cfg["target"])
    if not target.is_absolute():
        target = (KIT / target).resolve()
    if not target.is_dir():
        raise SystemExit(f"profile 里的 target 不存在：{target}")
    cfg.setdefault("name", target.name)
    return cfg, target


def stages() -> list[tuple[str, str, object]]:
    from auditkit import linters
    from auditkit.checks import deps, env, slim_arch, tests_db
    return [
        ("01,03,04,06", "Ruff", linters.run_ruff), ("01,06,10", "Pylint", linters.run_pylint),
        ("01,03,06", "Clippy", linters.run_clippy), ("02", "Semgrep", linters.run_semgrep),
        ("04", "体积 / 死代码 / 重复代码", slim_arch.run_slim), ("05", "架构与依赖图", slim_arch.run_arch),
        ("03", "性能工具适用性", env.run_perf), ("07", "测试与覆盖率", tests_db.run_coverage),
        ("08", "数据库", tests_db.run_database), ("09", "依赖库", deps.run_deps),
        ("10", "兼容性", env.run_compat), ("11", "UI/UX", env.run_uiux), ("12", "运行环境", env.run_runtime),
    ]


def cmd_run(args) -> int:
    from auditkit import fixes, report, score
    cfg, target = load_profile(args.profile)
    if args.skip_rust_tests:
        cfg.setdefault("rust", {})["run_tests"] = False
    execution = cfg.get("execution", {})
    mode = args.mode or execution.get("mode", "static")
    if mode not in MODES:
        raise SystemExit(f"execution.mode 只能是 {' / '.join(MODES)}，现在是 {mode!r}")
    util.ENV_EXTRA.clear()
    util.ENV_EXTRA.update(str(e) for e in execution.get("env_passthrough", []))
    started = time.strftime("%Y-%m-%d %H:%M:%S")
    stamp = time.strftime("%Y%m%d-%H%M%S")
    work = KIT / ".work" / cfg["name"]
    out = KIT / "reports" / cfg["name"] / stamp
    (out / "raw").mkdir(parents=True, exist_ok=True)
    excluded: dict = {}
    all_files = list_project_files(target, excluded)
    exclude = cfg.get("exclude", [])
    files = [f for f in all_files if not match_any(f, exclude)]
    ctx = Context(cfg=cfg, kit_root=KIT, target=target, base=work / "baseline", fixed=work / "fixed", out=out, raw=out / "raw", files=files)
    ctx.shared.update(all_files=all_files, started=started, mode=mode, excluded=excluded)
    print(f"代码综合审查系统 v{__version__}\n对象：{target}\n文件：{len(files)} 个纳入审查（另有 {len(all_files) - len(files)} 个按 profile 排除）\n报告：{out}")
    print(f"模式：{'local —— 会在本机执行项目的测试、构建和基准，没有系统级隔离，只用于你信任的代码' if ctx.local else 'static —— 只分析，不执行项目代码'}")
    if excluded.get("sensitive") or excluded.get("links"):
        print(f"未读取：{excluded.get('sensitive', 0)} 个敏感文件（密钥 / 凭据），{excluded.get('links', 0)} 个链接或指向项目之外的路径")
    print()
    ctx.say(f"[准备] 模式 {mode}；同步沙箱副本（原项目只读）")
    sync_copy(target, ctx.base, all_files)
    for cid in CATEGORIES:
        ctx.cat(cid)
    only = set(args.only.split(",")) if args.only else None
    ctx.shared["only"] = only
    t0 = time.perf_counter()
    for cats, label, fn in stages():
        if only and not (only & set(cats.split(","))):
            continue
        t = time.perf_counter()
        ctx.say(f"[{cats}] {label} …")
        try:
            fn(ctx)
        except Exception as e:  # noqa: BLE001 一个检查项出错不应该中断整次审查
            write_raw(ctx, "errors.txt", f"== {label}\n{traceback.format_exc()}\n", mode="a")
            for c in cats.split(","):
                ctx.cat(c).skip(label, f"检查项内部出错：{type(e).__name__}: {str(e)[:120]}（详见 raw/errors.txt）")
        ctx.say(f"      {time.perf_counter() - t:.0f}s")
    _finalize_findings(ctx)
    if not args.no_fix and not only:
        ctx.say("[修复] 自动修复、补丁、体积与性能对比、回归测试")
        try:
            ctx.shared["fix"] = fixes.run_fix_stage(ctx)
        except Exception:  # noqa: BLE001
            write_raw(ctx, "errors.txt", f"== 修复阶段\n{traceback.format_exc()}\n", mode="a")
            ctx.say("      修复阶段出错，详见 raw/errors.txt")
    sc = score.score_all(ctx)
    report.write_all(ctx, sc)
    (out / "run.log").write_text(redact("\n".join(ctx.log)), encoding="utf-8")
    print(f"\n完成，用时 {time.perf_counter() - t0:.0f}s")
    print(f"综合评分 {sc['overall']} / 100（{sc['grade']}）  严重 {sc['counts']['critical']}  高 {sc['counts']['high']}  中 {sc['counts']['medium']}  低 {sc['counts']['low']}")
    for r in sc["categories"]:
        s = "  —" if r["score"] is None else f"{r['score']:5.1f}"
        print(f"  {r['id']} {r['name']:<10} {s}   {r['reason']}")
    print(f"\n报告：{out / 'report.html'}\n      {out / 'report.md'}\n补丁：{out / 'fixes.patch'}")
    return 0


def _finalize_findings(ctx: Context) -> None:
    """去掉排除范围内的命中、去重、脱敏、把 profile 里登记为可接受的单独放。"""
    exclude = ctx.cfg.get("exclude", [])
    accepted_rules = ctx.cfg.get("accepted", [])
    scope = set(ctx.files)
    accepted: list[tuple[Finding, str]] = []
    for res in ctx.results.values():
        res.notes = [redact(n) for n in res.notes]
        res.tools_skipped = [(t, redact(why)) for t, why in res.tools_skipped]
        seen, keep = set(), []
        for f in res.findings:
            f.file = f.file.replace("\\", "/")
            f.message, f.cause, f.fix, f.fix_code = redact(f.message), redact(f.cause), redact(f.fix), redact(f.fix_code)
            in_repo = f.file in ctx.shared["all_files"]
            if (in_repo and f.file not in scope) or match_any(f.file, exclude):
                continue
            k = (f.file, f.line, f.rule, f.message)
            if k in seen:
                continue
            seen.add(k)
            why = next((a.get("reason", "") for a in accepted_rules
                        if a.get("rule") in (f.rule, "*") and match_any(f.file, [a.get("file", "*")])), None)
            if why is not None:
                accepted.append((f, why))
            else:
                keep.append(f)
        res.findings = keep
    ctx.shared["accepted"] = accepted
    ctx.shared["findings"] = sorted((f for r in ctx.results.values() for f in r.findings),
                                    key=lambda f: (Finding.key(f)[1], f.category, f.file, f.line))


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="代码综合审查系统：12 类审查，统一报告与评分")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("setup", help="安装审查工具").set_defaults(fn=cmd_setup)
    p = sub.add_parser("init", help="为新项目生成 profile")
    p.add_argument("path")
    p.add_argument("--name")
    p.set_defaults(fn=cmd_init)
    p = sub.add_parser("run", help="按 profile 审查一个项目")
    p.add_argument("profile")
    p.add_argument("--mode", choices=MODES, help="static（默认）：只分析，不执行项目代码；local：在本机运行项目的测试 / 构建 / 基准，"
                                                 "没有系统级隔离，只用于信任的代码")
    p.add_argument("--only", help="只跑部分类别，如 01,02（不做修复阶段）")
    p.add_argument("--no-fix", action="store_true", help="跳过修复阶段")
    p.add_argument("--skip-rust-tests", action="store_true", help="不运行 cargo test")
    p.set_defaults(fn=cmd_run)
    args = ap.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
