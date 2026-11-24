"""报告：report.md（通读）、report.html（可筛选的完整清单）、findings.json（机器可读）。"""
from __future__ import annotations

import html
import json
from collections import defaultdict
from dataclasses import asdict

from .model import CATEGORIES, SEV_CN, SEV_RANK, SEVERITIES, Context, Finding

# 中间结构：("p", 文本) / ("table", 表头, 行) / ("code", 文本) / ("list", 条目) / ("h3", 文本)
Block = tuple


def _kb(n: int) -> str:
    return f"{n / 1024:,.1f} KB"


def _delta(a: float, b: float, unit: str = "") -> str:
    d = b - a
    if not a:
        return "—"
    return f"{d:+,.0f}{unit}（{100 * d / a:+.2f}%）"


def _loc(f: Finding) -> str:
    return f"{f.file}:{f.line}" if f.file and f.line else (f.file or "（项目级）")


# ---------------------------------------------------------------- 各节内容
def overview(ctx: Context, sc: dict) -> list[Block]:
    size = ctx.cat("04").metrics.get("size", {})
    by = size.get("by_ext", {})
    langs = "、".join(f"{e} {v['files']} 个文件 / {v['code']:,} 行代码" for e, v in sorted(by.items(), key=lambda kv: -kv[1]["code"])[:6])
    c = sc["counts"]
    out: list[Block] = [
        ("p", f"**综合评分：{sc['overall']} / 100（{sc['grade']} 级）**　·　"
              f"严重 {c['critical']}　高 {c['high']}　中 {c['medium']}　低 {c['low']}"),
        ("list", [f"审查对象：`{ctx.target}`", f"审查时间：{ctx.shared['started']}",
                  f"范围：{len(ctx.files)} 个文件，{langs}",
                  "排除：" + ("、".join(f"`{g}`" for g in ctx.cfg.get("exclude", [])) or "无")
                  + "；密钥目录（secrets/、*.key、*.pem、.env）始终不读取、不复制",
                  "原项目没有被修改：所有工具都在审查系统自己的沙箱副本里运行，修复以 `fixes.patch` 交付"]),
        ("table", ["类别", "得分", "严重", "高", "中", "低", "权重", "使用的工具"],
         [[f"{r['id']} {r['name']}", "—" if r["score"] is None else f"{r['score']:.0f}",
           r["counts"]["critical"], r["counts"]["high"], r["counts"]["medium"], r["counts"]["low"],
           f"{r['weight']:g}" if r["weight"] else "—",
           "、".join(ctx.results[r["id"]].tools_used) or r["reason"]] for r in sc["categories"]]),
        ("p", "评分方法：" + sc["method"]),
    ]
    return out


def top_issues(ctx: Context, limit: int = 25) -> list[Block]:
    top = [f for f in ctx.shared["findings"] if f.severity in ("critical", "high")][:limit]
    if not top:
        return [("p", "没有“严重”或“高”级别的问题。")]
    n_all = sum(1 for f in ctx.shared["findings"] if f.severity in ("critical", "high"))
    out: list[Block] = [("table", ["级别", "类别", "位置", "问题", "修复建议"],
                         [[SEV_CN[f.severity], f.category, f"`{_loc(f)}`", f"{f.message}（{f.tool} {f.rule}）", f.fix] for f in top])]
    if n_all > len(top):
        out.append(("p", f"共 {n_all} 条，这里列出前 {len(top)} 条，其余见各类别和 report.html。"))
    return out


def fix_sections(ctx: Context) -> list[tuple[str, list[Block]]]:
    fx = ctx.shared.get("fix")
    if not fx:
        return [("修复阶段", [("p", "本次运行跳过了修复阶段（--no-fix）。")])]
    secs = []
    # ---- 自动修复
    b: list[Block] = [("list", [
        f"自动修复 {fx['fixes_applied']} 处，涉及 {fx['files_changed']} 个文件，补丁 {fx['patch_lines']} 行：`fixes.patch`",
        "只包含不改变行为的安全修复（未使用的导入、多余的 f 前缀、可简化的表达式等）；其余问题给出了建议代码，需要人工确认",
        f"补丁校验：{fx['patch_check']}" if fx.get("patch_check") else "没有可自动修复的内容",
        "修复后语法检查：" + ("通过" if fx["syntax_ok"] else "**失败，不要应用补丁**"),
    ] + ([f"同一组规则重新扫描：{fx['issues']['before']:,} → {fx['issues']['after']:,} 条"] if fx.get("issues") else []))]
    if fx["files_changed"]:
        b.append(("code", "cd <项目根目录>\ngit apply --stat  <报告目录>/fixes.patch   # 先看改了什么\ngit apply         <报告目录>/fixes.patch   # 应用\n# 跑一遍测试，没问题再提交；想撤销： git apply -R <报告目录>/fixes.patch"))
    secs.append(("自动修复与补丁", b))
    # ---- ⑦ 体积
    s = fx["size"]
    rows = []
    exts = sorted(set(s["before"]["by_ext"]) | set(s["after"]["by_ext"]), key=lambda e: -s["before"]["by_ext"].get(e, {}).get("bytes", 0))
    for e in exts:
        a, c = s["before"]["by_ext"].get(e), s["after"]["by_ext"].get(e)
        if a and c:
            rows.append([e, a["files"], _kb(a["bytes"]), _kb(c["bytes"]), _delta(a["bytes"], c["bytes"], " B"), f"{a['code']:,}", f"{c['code']:,}", f"{c['code'] - a['code']:+,}"])
    a, c = s["before"]["total"], s["after"]["total"]
    rows.append(["**合计**", a["files"], _kb(a["bytes"]), _kb(c["bytes"]), _delta(a["bytes"], c["bytes"], " B"), f"{a['code']:,}", f"{c['code']:,}", f"{c['code'] - a['code']:+,}"])
    pot = s["potential"]
    b = [("table", ["类型", "文件数", "修复前体积", "修复后体积", "变化", "修复前代码行", "修复后代码行", "行数变化"], rows),
         ("p", f"还可以人工瘦身的部分（不在补丁里）：疑似死代码约 {pot['dead_code_lines']:,} 行，重复代码 {pot['duplicated_lines']:,} 行。{pot['note']}")]
    secs.append(("优化前后的代码体积对比", b))
    # ---- ⑧ 性能
    bench = fx.get("bench") or []
    b = []
    if bench:
        rows = []
        for r in bench:
            if r.get("error"):
                rows.append([r["name"], "—", "—", "—", r["error"]])
                continue
            sig = abs(r["change_pct"]) > max(3.0, 2 * r["noise_pct"])
            verdict = ("变快" if r["change_pct"] < 0 else "变慢") if sig else "在测量噪声内，视为无变化"
            rows.append([r["name"], f"{r['before_median']:.3f}s（最快 {r['before_min']:.3f}s）", f"{r['after_median']:.3f}s（最快 {r['after_min']:.3f}s）",
                         f"{r['change_pct']:+.1f}%（噪声 ±{r['noise_pct']:.1f}%）", verdict])
        b.append(("table", ["基准", "修复前（中位数）", "修复后（中位数）", "变化", "结论"], rows))
        b.append(("p", f"每个基准在两份副本里交替运行 {bench[0].get('repeat', '?')} 次。自动补丁只做不改变行为的清理，"
                       "预期不会带来可测量的性能变化；这张表的作用是确认补丁没有让程序变慢。真正的性能收益来自 03 类里列出的问题，需要人工修改。"))
    else:
        b.append(("p", "profile 里没有配置 bench 命令，未做实测对比。"))
    tb, ta = fx.get("tests_before"), fx.get("tests_after")
    if tb and ta:
        b.append(("p", f"测试套件耗时：修复前 {tb['seconds']}s（带覆盖率插桩）→ 修复后 {ta['seconds']}s（不带插桩，两者不可直接比较）。"))
    secs.append(("性能优化前后对比", b))
    # ---- ⑨ 测试
    b = []
    if ta:
        ok = "**全部通过**" if ta["ok"] else "**有失败，不要应用补丁**"
        b.append(("table", ["", "运行", "失败", "错误", "跳过", "耗时", "结果"],
                  ([["修复前（原代码）", tb["ran"], tb["failed"], tb["errors"], tb["skipped"], f"{tb['seconds']}s", "通过" if tb["ok"] else "失败"]] if tb else [])
                  + [["修复后（应用补丁）", ta["ran"], ta["failed"], ta["errors"], ta["skipped"], f"{ta['seconds']}s", "通过" if ta["ok"] else "失败"]]))
        b.append(("p", f"应用补丁后的 Python 测试：{ok}。完整输出在 `raw/tests_fixed.txt`。"))
    else:
        b.append(("p", "profile 里没有配置 python.test_cmd，无法在修复后自动跑测试。"))
    rt = ctx.cat("07").metrics.get("rust_tests")
    if rt:
        b.append(("p", f"Rust 测试（补丁不涉及 Rust 源码，基线结果即最终结果）：{rt['ran']} 个，失败 {rt['failed']}，耗时 {rt['seconds']:.0f}s，"
                       + ("通过" if rt["ok"] else "**未通过**") + "。"))
    secs.append(("修复后的自动化测试结果", b))
    return secs


def metric_blocks(ctx: Context, cid: str) -> list[Block]:
    m = ctx.cat(cid).metrics
    out: list[Block] = []
    items: list[str] = []
    if cid == "03" and m.get("rust_clone_hotspots"):
        out.append(("table", ["Rust 文件", ".clone() 次数", "每千行"], [[f"`{a}`", b, c] for a, b, c in m["rust_clone_hotspots"]]))
        out.append(("p", "`.clone()` 多不等于有问题，只是提示热路径上值得看一眼的文件。"))
    if cid == "04":
        d = m.get("duplication")
        if d:
            items.append(f"重复代码：{d['lines']:,} 行（{d['percentage']}%），{d.get('clones', 0)} 处克隆")
        if "dead_code_lines_est" in m:
            items.append(f"疑似死代码：约 {m['dead_code_lines_est']:,} 行")
    if cid == "05":
        for k, label in (("python_modules", "Python 模块"), ("python_import_edges", "模块间 import 边"), ("python_import_cycles", "循环依赖"),
                         ("layer_violations", "分层违规"), ("rust_crates", "Rust crate"), ("rust_crate_edges", "crate 依赖边")):
            if k in m:
                items.append(f"{label}：{m[k]}")
        if m.get("top_fan_in"):
            items.append("被依赖最多：" + "、".join(f"`{a}`（{b}）" for a, b in m["top_fan_in"][:5]))
        if m.get("top_fan_out"):
            items.append("依赖别人最多：" + "、".join(f"`{a}`（{b}）" for a, b in m["top_fan_out"][:5]))
        items.append("依赖图：`architecture_python.dot`、`architecture_crates.dot`（可用 Graphviz 或在线查看器打开）")
    if cid == "07":
        pc, pt, rt = m.get("python_coverage"), m.get("python_tests"), m.get("rust_tests")
        if pt:
            items.append(f"Python 测试：{pt['ran']} 个，失败 {pt['failed']}，错误 {pt['errors']}，跳过 {pt['skipped']}，{pt['seconds']}s")
        if pc:
            items.append(f"Python 覆盖率：行 {pc['line_pct']}%" + (f"，分支 {pc['branch_pct']}%" if pc.get("branch_pct") is not None else "")
                         + f"，综合 {pc['combined_pct']}%（{pc['statements']:,} 条语句，{pc['missing']:,} 条未覆盖）")
        if "rust_test_functions" in m:
            items.append(f"Rust 测试函数：{m['rust_test_functions']} 个" + (f"；实际运行 {rt['ran']} 个，失败 {rt['failed']}" if rt else ""))
        if m.get("rust_coverage"):
            items.append(f"Rust 覆盖率：行 {m['rust_coverage']['line_pct']}%，函数 {m['rust_coverage']['function_pct']}%")
        if m.get("python_coverage_files"):
            out.append(("table", ["覆盖率最低的文件", "覆盖率", "语句数", "未覆盖"], [[f"`{a}`", f"{b}%", c, d] for a, b, c, d in m["python_coverage_files"][:10]]))
    if cid == "08":
        if "sql_statements" in m:
            items.append(f"SQL 语句：{m['sql_statements']} 条，其中 {m['sql_checked']} 条做了执行计划分析，{m['sql_dynamic']} 条是动态拼接，"
                         f"{m['sql_unverified']} 条无法静态分析（依赖运行时建的表或参数）")
            items.append(f"还原出的表：{'、'.join(m['schema_tables']) or '无'}；索引 {len(m['schema_indexes'])} 个")
        if m.get("sqlite_files"):
            out.append(("table", ["SQLite 文件", "完整性", "大小 MB", "空闲页 %", "journal"], [[f"`{a}`", b, c, d, e] for a, b, c, d, e in m["sqlite_files"]]))
    if cid == "09":
        for k, label in (("python_packages_audited", "已审计 Python 包"), ("rust_crates_audited", "已审计 Rust crate")):
            if k in m:
                items.append(f"{label}：{m[k]}")
        if m.get("python_outdated"):
            rows = m["python_outdated"]
            items.append(f"Python 可升级：{len(rows)} 个（直接依赖 {sum(1 for r in rows if r[3])} 个）："
                         + "、".join(f"{a} {b}→{c}" for a, b, c, d in rows[:8]) + ("…" if len(rows) > 8 else ""))
        if "rust_outdated" in m:
            rows = m["rust_outdated"]
            items.append(f"Rust 在当前版本约束内可升级：{len(rows)} 个" + ("：" + "、".join(f"{a} {b}→{c}" for a, b, c in rows[:8]) + ("…" if len(rows) > 8 else "") if rows else ""))
        if m.get("rust_licenses"):
            items.append("Rust 依赖许可证分布：" + "、".join(f"{a}（{b}）" for a, b in m["rust_licenses"][:6]))
    if cid == "10" and m.get("declared_versions"):
        items.append("声明的版本：" + "、".join(f"{a} = {b}" for a, b in m["declared_versions"][:8]))
    if cid == "11" and m.get("lighthouse"):
        out.append(("table", ["页面", "Lighthouse 得分"], [[u, "、".join(f"{k} {v}" for k, v in s.items())] for u, s in m["lighthouse"]]))
    if cid == "12":
        if m.get("units") or m.get("scripts"):
            items.append(f"systemd 单元 {m.get('units', 0)} 个，shell 脚本 {m.get('scripts', 0)} 个")
        if m.get("logs"):
            out.append(("table", ["日志文件", "行数", "ERROR", "WARN", "Traceback", "panic"],
                        [[f"`{a}`", n, c["error"], c["warn"], c["traceback"], c["panic"]] for a, n, c in m["logs"][:15]]))
    return ([("list", items)] if items else []) + out


def category_blocks(ctx: Context, cid: str, detail_limit: int) -> list[Block]:
    res = ctx.cat(cid)
    out: list[Block] = []
    if not res.applicable:
        return [("p", "**不适用**：" + " ".join(res.notes))] + ([("list", [f"未使用 {t}：{r}" for t, r in res.tools_skipped])] if res.tools_skipped else [])
    lines = ["使用：" + ("、".join(res.tools_used) or "无")]
    lines += [f"未使用 {t}：{r}" for t, r in res.tools_skipped]
    out.append(("list", lines))
    out += metric_blocks(ctx, cid)
    out += [("p", n) for n in res.notes]
    fs = sorted(res.findings, key=Finding.key)
    if not fs:
        out.append(("p", "没有发现问题。"))
        return out
    groups: dict[tuple, list[Finding]] = defaultdict(list)
    for f in fs:
        groups[(f.tool, f.rule)].append(f)
    rows = []
    for (tool, rule), g in sorted(groups.items(), key=lambda kv: (min(SEV_RANK[f.severity] for f in kv[1]), -len(kv[1]))):
        sev = min((f.severity for f in g), key=SEV_RANK.get)
        auto = sum(1 for f in g if f.autofix)
        rows.append([SEV_CN[sev], f"{tool} `{rule}`", len(g), g[0].cause or g[0].message, g[0].fix + (f"（{auto} 处已在补丁里）" if auto else ""), f"`{_loc(g[0])}`"])
    out.append(("h3", f"按规则汇总（{len(fs)} 条问题，{len(groups)} 种）"))
    out.append(("table", ["级别", "规则", "数量", "原因", "修复建议", "示例位置"], rows[:60]))
    out.append(("h3", f"问题明细（按严重程度，前 {min(detail_limit, len(fs))} 条；完整清单见 report.html / findings.json）"))
    for f in fs[:detail_limit]:
        out.append(("p", f"**[{SEV_CN[f.severity]}] `{_loc(f)}`** — {f.message}　〔{f.tool} `{f.rule}`〕" + ("　✔ 已在 fixes.patch 中" if f.autofix else "")))
        out.append(("list", ["原因：" + (f.cause or "—"), "修复：" + (f.fix or "—")]))
        if f.fix_code:
            out.append(("code", f.fix_code))
    return out


def accepted_blocks(ctx: Context) -> list[Block]:
    acc = ctx.shared.get("accepted", [])
    if not acc:
        return []
    return [("p", "下面这些命中了规则，但 profile 的 `accepted` 里登记了理由，因此不计分。理由不成立就把对应条目从 profile 里删掉。"),
            ("table", ["类别", "位置", "规则", "问题", "登记的理由"],
             [[f.category, f"`{_loc(f)}`", f"{f.tool} `{f.rule}`", f.message, why] for f, why in acc])]


def build(ctx: Context, sc: dict) -> list[tuple[str, list[Block]]]:
    limit = int(ctx.cfg.get("report", {}).get("detail_per_category", 25))
    secs = [("总览与综合评分", overview(ctx, sc)), ("优先处理的问题", top_issues(ctx))]
    secs += fix_sections(ctx)
    for cid in sorted(CATEGORIES):
        if cid in ctx.results:
            res = ctx.results[cid]
            title = f"{cid} {res.name}" + (f"　{res.score:.0f} 分" if res.score is not None else "　不计分")
            secs.append((title, category_blocks(ctx, cid, limit)))
    if ctx.shared.get("accepted"):
        secs.append(("已登记为可接受的问题", accepted_blocks(ctx)))
    return secs


# ---------------------------------------------------------------- Markdown
def _md_cell(v) -> str:
    return str(v).replace("|", "\\|").replace("\n", " ")


def to_markdown(ctx: Context, secs) -> str:
    out = [f"# 代码综合审查报告 — {ctx.cfg.get('name', ctx.target.name)}", ""]
    for title, blocks in secs:
        out += [f"## {title}", ""]
        for b in blocks:
            if b[0] == "p":
                out += [b[1], ""]
            elif b[0] == "h3":
                out += [f"### {b[1]}", ""]
            elif b[0] == "list":
                out += [f"- {i}" for i in b[1]] + [""]
            elif b[0] == "code":
                fence = "````" if "```" in b[1] else "```"
                out += [fence, b[1].rstrip("\n"), fence, ""]
            elif b[0] == "table":
                out.append("| " + " | ".join(b[1]) + " |")
                out.append("|" + "|".join("---" for _ in b[1]) + "|")
                out += ["| " + " | ".join(_md_cell(c) for c in r) + " |" for r in b[2]]
                out.append("")
    return "\n".join(out)


# ---------------------------------------------------------------- HTML
def _inline(s) -> str:
    """把 **粗体** 和 `代码` 转成 HTML，其余转义。"""
    import re
    t = html.escape(str(s))
    t = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", t)
    return re.sub(r"`(.+?)`", r"<code>\1</code>", t)


CSS = """
:root{--bg:#f6f7f9;--card:#fff;--fg:#1c2330;--mute:#667085;--line:#e3e6ec;--acc:#2f5fd0;--crit:#b42318;--high:#c4520a;--med:#946200;--low:#4a6480}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]){--bg:#12151b;--card:#1a1f28;--fg:#e4e8ef;--mute:#98a2b3;--line:#2b3340;--acc:#7da2ff;--crit:#ff8a7d;--high:#ffa35c;--med:#e6bb4c;--low:#9db4cf}}
:root[data-theme=dark]{--bg:#12151b;--card:#1a1f28;--fg:#e4e8ef;--mute:#98a2b3;--line:#2b3340;--acc:#7da2ff;--crit:#ff8a7d;--high:#ffa35c;--med:#e6bb4c;--low:#9db4cf}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.6 -apple-system,"Segoe UI","Microsoft YaHei",sans-serif}
main{max-width:1180px;margin:0 auto;padding:24px 16px 80px}h1{font-size:22px;margin:0 0 4px}h2{font-size:17px;margin:0 0 12px}h3{font-size:14px;margin:18px 0 8px;color:var(--mute)}
section{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:18px 20px;margin:16px 0}
.tw{overflow-x:auto}table{border-collapse:collapse;width:100%;font-size:13px}th,td{border-bottom:1px solid var(--line);padding:6px 8px;text-align:left;vertical-align:top}
th{color:var(--mute);font-weight:600;white-space:nowrap}code{background:rgba(127,127,127,.14);border-radius:4px;padding:1px 4px;font:12px/1.5 Consolas,Menlo,monospace;word-break:break-all}
pre{background:rgba(127,127,127,.12);border-radius:8px;padding:10px 12px;overflow-x:auto;font:12px/1.5 Consolas,Menlo,monospace;margin:6px 0 12px}
ul{margin:6px 0 12px;padding-left:20px}.hero{display:flex;gap:24px;align-items:center;flex-wrap:wrap}.score{font-size:54px;font-weight:700;line-height:1;color:var(--acc)}
.grade{font-size:15px;color:var(--mute)}.pill{display:inline-block;border-radius:99px;padding:0 9px;font-size:12px;font-weight:600;border:1px solid currentColor;white-space:nowrap}
.critical{color:var(--crit)}.high{color:var(--high)}.medium{color:var(--med)}.low{color:var(--low)}
.bars{display:grid;grid-template-columns:repeat(auto-fill,minmax(250px,1fr));gap:8px 20px;margin-top:14px}.bar{font-size:12px}.bar i{display:block;height:6px;border-radius:3px;background:var(--line);margin-top:2px}
.bar i b{display:block;height:100%;border-radius:3px;background:var(--acc)}
.filters{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:10px}.filters select,.filters input,.filters button{font:inherit;color:inherit;background:var(--bg);border:1px solid var(--line);border-radius:6px;padding:5px 8px}
.filters input{flex:1;min-width:160px}tr.f{cursor:pointer}tr.d td{background:rgba(127,127,127,.06)}.mute{color:var(--mute)}nav{font-size:13px;margin:10px 0}nav a{color:var(--acc);margin-right:12px;text-decoration:none;white-space:nowrap}
"""

JS = """
const D=JSON.parse(document.getElementById('data').textContent),SEV={critical:'严重',high:'高',medium:'中',low:'低'};
const $=s=>document.querySelector(s),esc=s=>String(s??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
let page=0;const PER=50;
function rows(){const c=$('#fc').value,s=$('#fs').value,a=$('#fa').value,q=$('#fq').value.toLowerCase();
 return D.filter(f=>(!c||f.category===c)&&(!s||f.severity===s)&&(!a||String(f.autofix)===a)&&(!q||(f.file+' '+f.message+' '+f.rule+' '+f.tool).toLowerCase().includes(q)));}
function draw(){const r=rows(),n=Math.max(1,Math.ceil(r.length/PER));page=Math.min(page,n-1);
 $('#cnt').textContent=`${r.length} 条 · 第 ${page+1}/${n} 页`;
 $('#tb').innerHTML=r.slice(page*PER,page*PER+PER).map((f,i)=>`<tr class="f" data-i="${i}"><td><span class="pill ${f.severity}">${SEV[f.severity]}</span></td><td>${f.category}</td>
 <td><code>${esc(f.file)}${f.line?':'+f.line:''}</code></td><td>${esc(f.message)}${f.autofix?' <span class="mute">✔补丁</span>':''}</td><td class="mute">${esc(f.tool)} ${esc(f.rule)}</td></tr>
 <tr class="d" hidden><td colspan="5"><b>原因</b>　${esc(f.cause)||'—'}<br><b>修复</b>　${esc(f.fix)||'—'}${f.fix_code?`<pre>${esc(f.fix_code)}</pre>`:''}${f.url?`<br><a href="${esc(f.url)}" rel="noopener">${esc(f.url)}</a>`:''}</td></tr>`).join('');}
$('#tb').addEventListener('click',e=>{const tr=e.target.closest('tr.f');if(tr)tr.nextElementSibling.hidden=!tr.nextElementSibling.hidden;});
['#fc','#fs','#fa','#fq'].forEach(s=>$(s).addEventListener('input',()=>{page=0;draw();}));
$('#prev').onclick=()=>{page=Math.max(0,page-1);draw();};$('#next').onclick=()=>{page++;draw();};draw();
"""


def to_html(ctx: Context, sc: dict, secs) -> str:
    name = html.escape(ctx.cfg.get("name", ctx.target.name))
    c = sc["counts"]
    parts = [f"<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
             f"<title>代码审查报告 {name}</title><style>{CSS}</style></head><body><main>",
             f"<h1>代码综合审查报告 — {name}</h1><div class='mute'>{html.escape(ctx.shared['started'])} · {html.escape(str(ctx.target))}</div>",
             "<section><div class='hero'><div><div class='score'>" + (f"{sc['overall']:.0f}" if sc["overall"] is not None else "—")
             + f"</div><div class='grade'>综合评分 · {sc['grade']} 级</div></div><div>"
             + "　".join(f"<span class='pill {s}'>{SEV_CN[s]} {c[s]}</span>" for s in SEVERITIES) + "</div></div><div class='bars'>"]
    for r in sc["categories"]:
        v = r["score"]
        parts.append(f"<div class='bar'>{r['id']} {html.escape(r['name'])} <b style='float:right'>{'—' if v is None else f'{v:.0f}'}</b>"
                     f"<i><b style='width:{0 if v is None else v}%'></b></i></div>")
    parts.append("</div></section><nav>" + "".join(f"<a href='#s{i}'>{html.escape(t.split('　')[0])}</a>" for i, (t, _) in enumerate(secs))
                 + "<a href='#all'>全部问题</a></nav>")
    for i, (title, blocks) in enumerate(secs):
        parts.append(f"<section id='s{i}'><h2>{_inline(title)}</h2>")
        for b in blocks:
            if b[0] == "p":
                parts.append(f"<p>{_inline(b[1])}</p>")
            elif b[0] == "h3":
                parts.append(f"<h3>{_inline(b[1])}</h3>")
            elif b[0] == "list":
                parts.append("<ul>" + "".join(f"<li>{_inline(x)}</li>" for x in b[1]) + "</ul>")
            elif b[0] == "code":
                parts.append(f"<pre>{html.escape(b[1])}</pre>")
            elif b[0] == "table":
                parts.append("<div class='tw'><table><thead><tr>" + "".join(f"<th>{_inline(h)}</th>" for h in b[1]) + "</tr></thead><tbody>"
                             + "".join("<tr>" + "".join(f"<td>{_inline(x)}</td>" for x in r) + "</tr>" for r in b[2]) + "</tbody></table></div>")
        parts.append("</section>")
    data = json.dumps([asdict(f) for f in ctx.shared["findings"]], ensure_ascii=False).replace("</", "<\\/")
    cats = "".join(f"<option value='{k}'>{k} {v}</option>" for k, v in CATEGORIES.items() if k in ctx.results)
    parts.append(
        "<section id='all'><h2>全部问题（可筛选，点一行展开原因与修复代码）</h2><div class='filters'>"
        f"<select id='fc'><option value=''>全部类别</option>{cats}</select>"
        "<select id='fs'><option value=''>全部级别</option><option value='critical'>严重</option><option value='high'>高</option><option value='medium'>中</option><option value='low'>低</option></select>"
        "<select id='fa'><option value=''>是否在补丁里</option><option value='true'>已在补丁里</option><option value='false'>需人工处理</option></select>"
        "<input id='fq' placeholder='搜索文件、规则、描述'><button id='prev'>上一页</button><button id='next'>下一页</button><span id='cnt' class='mute'></span></div>"
        "<div class='tw'><table><thead><tr><th>级别</th><th>类别</th><th>位置</th><th>问题</th><th>工具 / 规则</th></tr></thead><tbody id='tb'></tbody></table></div></section>"
        f"<script type='application/json' id='data'>{data}</script><script>{JS}</script></main></body></html>")
    return "".join(parts)


def write_all(ctx: Context, sc: dict) -> None:
    secs = build(ctx, sc)
    (ctx.out / "report.md").write_text(to_markdown(ctx, secs), encoding="utf-8")
    (ctx.out / "report.html").write_text(to_html(ctx, sc, secs), encoding="utf-8")
    payload = {
        "project": ctx.cfg.get("name", ctx.target.name), "target": str(ctx.target), "started": ctx.shared["started"],
        "score": sc, "fix_stage": ctx.shared.get("fix"),
        "categories": {cid: {"name": r.name, "applicable": r.applicable, "score": r.score, "tools_used": r.tools_used,
                             "tools_skipped": r.tools_skipped, "metrics": r.metrics, "notes": r.notes} for cid, r in sorted(ctx.results.items())},
        "findings": [asdict(f) for f in ctx.shared["findings"]],
        "accepted": [{**asdict(f), "accepted_reason": why} for f, why in ctx.shared.get("accepted", [])],
    }
    (ctx.out / "findings.json").write_text(json.dumps(payload, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
