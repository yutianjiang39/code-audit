"""09 依赖库：已知漏洞（pip-audit、OSV.dev）、过期版本、许可证、未锁定版本。"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.request

from ..model import STATIC_SKIP, Context, Finding
from ..util import chunks, kit_tool, read_text, run, which, write_raw
from ..linters import cargo_roots, cargo_env
from .tests_db import project_python

OSV = "https://api.osv.dev/v1"
GH_SEV = {"CRITICAL": "critical", "HIGH": "high", "MODERATE": "medium", "MEDIUM": "medium", "LOW": "low"}
COPYLEFT = re.compile(r"\b(AGPL|GPL|LGPL|SSPL|EUPL|CC-BY-NC)", re.I)


def _http_json(url: str, payload: dict | None = None, timeout: int = 30):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json", "User-Agent": "code-audit-kit"})
    with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 固定的 https 地址
        return json.loads(r.read().decode("utf-8"))


def _osv_detail(ctx: Context, vid: str) -> dict:
    cache = ctx.shared.setdefault("osv_cache", {})
    if vid not in cache:
        try:
            cache[vid] = _http_json(f"{OSV}/vulns/{vid}")
        except (urllib.error.URLError, OSError, ValueError):
            cache[vid] = {}
    return cache[vid]


def _osv_severity(v: dict) -> str:
    ds = v.get("database_specific") or {}
    if str(ds.get("severity", "")).upper() in GH_SEV:
        return GH_SEV[str(ds["severity"]).upper()]
    info = ds.get("informational") or next(
        ((a.get("database_specific") or {}).get("informational") for a in v.get("affected", [])
         if (a.get("database_specific") or {}).get("informational")), None)
    if info == "unmaintained":
        return "low"
    if info in ("unsound", "notice"):
        return "medium"
    for s in v.get("severity", []):
        m = re.search(r"/AV:(\w)/AC:(\w)", s.get("score", ""))
        if m:
            return "high" if m.group(1) == "N" and m.group(2) == "L" else "medium"
    return "high"


def _osv_fixed(v: dict, name: str) -> list[str]:
    out = []
    for a in v.get("affected", []):
        if (a.get("package") or {}).get("name", "").lower() != name.lower():
            continue
        for r in a.get("ranges", []):
            out += [e["fixed"] for e in r.get("events", []) if "fixed" in e]
    return sorted(set(out))


def run_deps(ctx: Context) -> None:
    res = ctx.cat("09")
    reqs = [f for f in ctx.files if re.fullmatch(r"(.*/)?requirements[\w.-]*\.txt", f)]
    locks = [f for f in ctx.files if f.endswith("Cargo.lock")]
    npm = [f for f in ctx.files if f.endswith("package-lock.json")]
    if not (reqs or locks or npm or any(f.endswith(("pyproject.toml", "Cargo.toml", "package.json")) for f in ctx.files)):
        res.applicable = False
        res.notes.append("没有发现依赖清单。")
        return
    n_deps = 0
    if reqs or (ctx.local and project_python(ctx)):
        n_deps += _python(ctx, reqs)
    for lock in locks:
        n_deps += _cargo_lock(ctx, lock)
    if locks and ctx.local:
        _cargo_outdated(ctx)
        _cargo_licenses(ctx)
    elif locks:  # cargo 读取项目里的 .cargo/config.toml，其中可以指定任意可执行文件
        res.skip("cargo update --dry-run / cargo metadata（过期版本、许可证）", STATIC_SKIP)
    for lock in npm:
        _npm(ctx, lock)
    res.metrics["dependency_count"] = n_deps
    res.skip("Snyk / Dependabot / Renovate", "需要账号或托管在代码平台上的服务；本地用 pip-audit + OSV.dev 覆盖同一批漏洞库")
    res.skip("cargo-audit", "未安装；改为直接查询 OSV.dev（包含 RustSec 全量数据）") if not which("cargo-audit") else None


# ---------------------------------------------------------------- Python
def _python(ctx: Context, reqs: list[str]) -> int:
    res = ctx.cat("09")
    n = 0
    for f in reqs:
        for i, line in enumerate(read_text(ctx.base / f).splitlines(), 1):
            s = line.split("#")[0].strip()
            if not s or s.startswith("-"):
                continue
            n += 1
            if "==" not in s and "@" not in s:
                res.findings.append(Finding(
                    category="09", tool="依赖清单检查", rule="unpinned-requirement", severity="low", file=f, line=i,
                    message=f"依赖没有锁定精确版本：{s}",
                    cause="同一份清单在不同时间安装会得到不同版本，线上与本地行为可能不一致，也无法复现历史构建。",
                    fix="用 `==` 锁定版本（或用 pip-compile / uv 生成带哈希的锁文件），升级走单独的提交。"))
    if not ctx.local:
        # pip-audit 要么启动项目解释器，要么为了解析依赖去构建 sdist（会执行 setup.py）；静态模式两样都不做
        _pypi_osv(ctx, reqs)
        res.skip("pip-audit / pip list --outdated", STATIC_SKIP + "（改为对 == 锁定的条目直接查 OSV.dev）")
        return n
    py = project_python(ctx)
    pa = kit_tool(ctx.kit_root, "pip-audit")
    if not pa:
        res.skip("pip-audit", "未安装")
    else:
        if py:
            rc, out, err = run([pa, "--format", "json", "--progress-spinner", "off"], env={"PIPAPI_PYTHON_LOCATION": py},
                               timeout=600, network=True)
            src = "项目解释器里实际安装的包"
        else:
            args = []
            for f in reqs:
                args += ["-r", str(ctx.base / f)]
            rc, out, err = run([pa, "--format", "json", "--progress-spinner", "off", *args], timeout=900, network=True)
            src = "requirements 文件"
        write_raw(ctx, "pip_audit.json", out or err)
        try:
            data = json.loads(out[out.index("{"):])
        except ValueError:
            res.skip("pip-audit", f"运行失败（需要联网）：{(err or out).strip()[-160:]}")
            data = None
        if data is not None:
            deps = data.get("dependencies", [])
            n = max(n, len(deps))
            anchor = reqs[0] if reqs else ""
            for d in deps:
                for v in d.get("vulns", []):
                    det = _osv_detail(ctx, v["id"])
                    fixv = v.get("fix_versions") or []
                    res.findings.append(Finding(
                        category="09", tool="pip-audit", rule=v["id"], severity=_osv_severity(det) if det else "high",
                        file=anchor, line=_req_line(ctx, anchor, d["name"]),
                        message=f"{d['name']} {d['version']} 存在已知漏洞 {v['id']}"
                                + (f"（{'、'.join(v.get('aliases', [])[:2])}）" if v.get("aliases") else ""),
                        cause=(det.get("summary") or v.get("description") or "")[:300],
                        fix=f"升级到 {' / '.join(fixv)}。" if fixv else "暂无修复版本：评估是否受影响，或换用替代库。",
                        fix_code=f"pip install -U \"{d['name']}>={fixv[0]}\"" if fixv else "",
                        url=f"https://osv.dev/vulnerability/{v['id']}"))
            res.metrics["python_packages_audited"] = len(deps)
            res.use(f"pip-audit（{src}）")
    if py:
        rc, out, err = run([py, "-m", "pip", "list", "--outdated", "--format", "json", "--disable-pip-version-check"],
                           timeout=300, network=True)
        try:
            old = json.loads(out)
        except ValueError:
            old = None
        if old is None:
            res.skip("pip list --outdated", "运行失败（需要联网）")
        else:
            direct = {re.split(r"[<>=!~\[; ]", ln.split("#")[0].strip())[0].lower().replace("_", "-")
                      for f in reqs for ln in read_text(ctx.base / f).splitlines() if ln.strip() and not ln.startswith(("#", "-"))}
            rows = [(o["name"], o["version"], o["latest_version"], o["name"].lower().replace("_", "-") in direct) for o in old]
            res.metrics["python_outdated"] = sorted(rows, key=lambda r: not r[3])
            major = [r for r in rows if r[3] and r[1].split(".")[0] != r[2].split(".")[0]]
            if major:
                res.findings.append(Finding(
                    category="09", tool="pip list --outdated", rule="outdated-major", severity="low", file=reqs[0] if reqs else "", line=0,
                    message="直接依赖落后一个以上大版本：" + "、".join(f"{a} {b}→{c}" for a, b, c, _ in major[:10]),
                    cause="大版本落后越多，升级成本越高，安全修复也可能不再回移到旧版本线。",
                    fix="逐个看升级说明，在分支上升级并跑完整测试；不要一次升级多个大版本。"))
            res.use("pip list --outdated")
    return n


PINNED = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)\s*(?:\[[^\]]*\])?\s*==\s*([A-Za-z0-9][A-Za-z0-9.!+_-]*)\s*(?:;.*)?$")


def _pypi_osv(ctx: Context, reqs: list[str]) -> None:
    """静态模式：只读 requirements 文件，对 `==` 锁定的条目查 OSV.dev；没锁定的版本无从判断，照实计数。"""
    res = ctx.cat("09")
    pins: list[tuple[str, str, str, int]] = []
    loose = 0
    for f in reqs:
        for i, line in enumerate(read_text(ctx.base / f).splitlines(), 1):
            s = line.split("#")[0].split("--hash")[0].strip().rstrip("\\").strip()
            if not s or s.startswith("-"):
                continue
            m = PINNED.match(s)
            if m:
                pins.append((m.group(1), m.group(2), f, i))
            else:
                loose += 1
    res.metrics["python_packages_audited"] = len(pins)
    res.metrics["python_packages_unverified"] = loose
    if loose:
        res.notes.append(f"{loose} 条 Python 依赖没有用 == 锁定版本，静态模式下无法判断实际安装的版本，未做漏洞核查"
                         "（--mode local 会用 pip-audit 核查实际安装的包）。")
    if not pins:
        return
    hits: list[tuple[str, str, str, int, str]] = []
    try:
        for part in chunks(pins, 500):
            q = {"queries": [{"package": {"name": n, "ecosystem": "PyPI"}, "version": v} for n, v, _, _ in part]}
            r = _http_json(f"{OSV}/querybatch", q, timeout=60)
            for (n, v, f, ln), item in zip(part, r.get("results", []), strict=False):
                hits += [(n, v, f, ln, x["id"]) for x in item.get("vulns", [])]
    except (urllib.error.URLError, OSError, ValueError) as e:
        res.skip("OSV.dev（requirements）", f"查询失败（需要联网）：{type(e).__name__}")
        return
    for n, v, f, ln, vid in hits:
        det = _osv_detail(ctx, vid)
        fixv = _osv_fixed(det, n)
        aliases = [a for a in det.get("aliases", []) if a.startswith("CVE-")][:2]
        res.findings.append(Finding(
            category="09", tool="OSV.dev", rule=vid, severity=_osv_severity(det) if det else "high", file=f, line=ln,
            message=f"{n} {v} 存在已知漏洞 {vid}" + (f"（{'、'.join(aliases)}）" if aliases else ""),
            cause=(det.get("summary") or " ".join((det.get("details") or "").split()))[:300],
            fix=f"升级到 {' / '.join(fixv[-3:])} 或更高。" if fixv else "暂无修复版本：评估是否受影响，或换用替代库。",
            fix_code=f"pip install -U \"{n}>={fixv[-1]}\"" if fixv else "",
            url=f"https://osv.dev/vulnerability/{vid}"))
    res.use(f"OSV.dev 批量查询（requirements 里 == 锁定的 {len(pins)} 个包）")


def _req_line(ctx: Context, f: str, name: str) -> int:
    if not f:
        return 0
    for i, line in enumerate(read_text(ctx.base / f).splitlines(), 1):
        if re.match(rf"\s*{re.escape(name)}\b", line, re.I):
            return i
    return 0


# ---------------------------------------------------------------- Rust
def _cargo_lock(ctx: Context, lock: str) -> int:
    res = ctx.cat("09")
    text = read_text(ctx.base / lock)
    pkgs = []
    for block in text.split("[[package]]")[1:]:
        name = re.search(r'^name = "(.+?)"', block, re.M)
        ver = re.search(r'^version = "(.+?)"', block, re.M)
        if name and ver and re.search(r'^source = "registry', block, re.M):
            line = text[:text.index(block)].count("\n") + 1
            pkgs.append((name.group(1), ver.group(1), line))
    hits: list[tuple[str, str, int, str]] = []
    try:
        for part in chunks(pkgs, 500):
            q = {"queries": [{"package": {"name": n, "ecosystem": "crates.io"}, "version": v} for n, v, _ in part]}
            r = _http_json(f"{OSV}/querybatch", q, timeout=60)
            for (n, v, ln), item in zip(part, r.get("results", []), strict=False):
                hits += [(n, v, ln, x["id"]) for x in item.get("vulns", [])]
    except (urllib.error.URLError, OSError, ValueError) as e:
        res.skip("OSV.dev（Cargo.lock）", f"查询失败（需要联网）：{type(e).__name__}")
        return len(pkgs)
    for n, v, ln, vid in hits:
        det = _osv_detail(ctx, vid)
        fixv = _osv_fixed(det, n)
        sev = _osv_severity(det) if det else "high"
        res.findings.append(Finding(
            category="09", tool="OSV.dev", rule=vid, severity=sev, file=lock, line=ln,
            message=f"crate {n} {v}：{det.get('summary') or vid}"[:220],
            cause=" ".join((det.get("details") or "").split())[:300] or "该版本在 RustSec / GitHub 安全公告库中有记录。",
            fix=(f"升级到 {' / '.join(fixv[-3:])} 或更高。" if fixv else "没有修复版本（多为不再维护）：换用公告里建议的替代 crate。")
                + " 用 `cargo tree -i " + n + "` 看是谁引入的。",
            fix_code=f"cargo update -p {n}" + (f" --precise {fixv[-1]}" if len(fixv) == 1 else ""),
            url=f"https://osv.dev/vulnerability/{vid}"))
    res.metrics["rust_crates_audited"] = len(pkgs)
    res.use(f"OSV.dev 批量查询（{lock}，{len(pkgs)} 个 crate）")
    return len(pkgs)


def _cargo_outdated(ctx: Context) -> None:
    res = ctx.cat("09")
    if not which("cargo"):
        return
    rows = []
    for root in cargo_roots(ctx):
        rc, out, err = run(["cargo", "update", "--dry-run"], cwd=ctx.base / root, env=cargo_env(ctx), timeout=300, network=True)
        if rc != 0:
            res.skip("cargo update --dry-run", "运行失败（需要联网）")
            return
        rows += re.findall(r"Updating (\S+) v(\S+) -> v(\S+)", out + err)
    res.metrics["rust_outdated"] = rows
    res.use("cargo update --dry-run（只看可升级项，不改锁文件）")


def _cargo_licenses(ctx: Context) -> None:
    res = ctx.cat("09")
    if not which("cargo"):
        return
    counts: dict[str, int] = {}
    for root in cargo_roots(ctx):
        rc, out, err = run(["cargo", "metadata", "--format-version", "1", "--locked"], cwd=ctx.base / root, env=cargo_env(ctx), timeout=300)
        if rc != 0:
            return
        meta = json.loads(out)
        members = set(meta.get("workspace_members", []))
        for p in meta.get("packages", []):
            if p["id"] in members:
                continue
            lic = p.get("license") or ("（见 license 文件）" if p.get("license_file") else "未声明")
            counts[lic] = counts.get(lic, 0) + 1
            # “A OR B” 可以任选其一，只有全部选项都是 copyleft 才算
            options = re.split(r"\s+OR\s+|/", lic)
            if lic == "未声明" or all(COPYLEFT.search(o) for o in options):
                res.findings.append(Finding(
                    category="09", tool="cargo metadata", rule="license-review", severity="medium" if lic != "未声明" else "low",
                    file=f"{root}/Cargo.lock", line=0, message=f"crate {p['name']} {p['version']} 的许可证需要确认：{lic}",
                    cause="copyleft 或未声明许可证的依赖，在分发二进制时可能带来开源义务或授权不明。",
                    fix="确认使用方式是否触发义务；不能接受就换用 MIT / Apache-2.0 的替代库。"))
    res.metrics["rust_licenses"] = sorted(counts.items(), key=lambda kv: -kv[1])[:12]
    res.use("许可证清点（cargo metadata）")


# ---------------------------------------------------------------- npm
def _npm(ctx: Context, lock: str) -> None:
    res = ctx.cat("09")
    npm = which("npm")
    if not npm:
        res.skip("npm audit", "未安装 Node.js")
        return
    if not ctx.local:  # npm 在项目目录里运行会读取项目的 .npmrc（可改 registry、script-shell）
        res.skip("npm audit", STATIC_SKIP)
        return
    cwd = (ctx.base / lock).parent
    rc, out, err = run([npm, "audit", "--json", "--package-lock-only", "--ignore-scripts"], cwd=cwd, timeout=300, network=True)
    try:
        data = json.loads(out)
    except ValueError:
        res.skip("npm audit", "运行失败（需要联网）")
        return
    for name, v in (data.get("vulnerabilities") or {}).items():
        via = [x for x in v.get("via", []) if isinstance(x, dict)]
        fa = v.get("fixAvailable")
        res.findings.append(Finding(
            category="09", tool="npm audit", rule=(via[0].get("url", "").rsplit("/", 1)[-1] if via else "npm-advisory"),
            severity=GH_SEV.get(str(v.get("severity", "")).upper(), "medium"), file=lock, line=0,
            message=f"{name} {v.get('range', '')}：{via[0].get('title') if via else '间接依赖带入的漏洞'}",
            cause="该版本范围在 npm 安全公告库中有记录。",
            fix="运行 npm audit fix；" + ("需要大版本升级。" if isinstance(fa, dict) and fa.get("isSemVerMajor") else "有兼容的修复版本。" if fa else "暂无修复版本。"),
            fix_code="npm audit fix", url=via[0].get("url", "") if via else ""))
    res.use("npm audit")
