"""评分：每类 0–100，再按权重汇总成综合分。

思路：问题按严重程度加权求和，除以这一类的“规模”得到密度，密度越高分越低；
再加两条硬上限——有“严重”问题的类别不及格，有“高”问题的类别到不了优秀。
"""
from __future__ import annotations

import math

from .model import Context
from .util import size_stats

SEV_WEIGHT = {"critical": 25.0, "high": 8.0, "medium": 2.0, "low": 0.3}
CAT_WEIGHT = {"01": 20, "02": 20, "03": 10, "04": 5, "05": 8, "06": 10, "07": 10, "08": 4, "09": 10, "10": 2, "11": 5, "12": 3}
CAP_CRITICAL, CAP_HIGH = 59.0, 84.0
DENSITY_SCALE = 10.0  # 每千行加权扣分达到 10 时，得分约 37


def _basis(ctx: Context, cid: str, kloc: float) -> float:
    """这一类问题数应该除以什么：代码类按千行，其余按被检查对象的个数。"""
    m = ctx.cat(cid).metrics
    if cid == "08":
        return max(1.0, m.get("sql_statements", 0) / 10)
    if cid == "09":
        return max(1.0, m.get("dependency_count", 0) / 20)
    if cid == "12":
        return max(1.0, (m.get("units", 0) + m.get("scripts", 0)) / 3)
    if cid == "11":
        return max(1.0, len(ctx.by_ext(".html", ".htm", ".jsx", ".tsx", ".vue")) / 3)
    return max(1.0, kloc)


def score_all(ctx: Context) -> dict:
    weights = {**CAT_WEIGHT, **ctx.cfg.get("score_weights", {})}
    size = ctx.cat("04").metrics.get("size") or size_stats(ctx.base, ctx.files)
    kloc = size["total"]["code"] / 1000
    only = ctx.shared.get("only")
    rows, total_w, total = [], 0.0, 0.0
    for cid in sorted(ctx.results):
        res = ctx.results[cid]
        if only and cid not in only:
            res.score = None
            rows.append({"id": cid, "name": res.name, "score": None, "weight": 0, "reason": "本次未审查（--only）",
                         "counts": {s: sum(1 for f in res.findings if f.severity == s) for s in SEV_WEIGHT}})
            continue
        counts = {s: sum(1 for f in res.findings if f.severity == s) for s in SEV_WEIGHT}
        if not res.applicable or not res.tools_used:
            res.score = None
            rows.append({"id": cid, "name": res.name, "score": None, "counts": counts, "weight": 0,
                         "reason": "不适用" if not res.applicable else
                         "静态模式未运行测试，未评分" if cid == "07" and not ctx.local else "没有可用的工具，未评分"})
            continue
        penalty = sum(SEV_WEIGHT[s] * n for s, n in counts.items())
        score = 100 * math.exp(-(penalty / _basis(ctx, cid, kloc)) / DENSITY_SCALE)
        if cid == "07":
            score = _coverage_score(res.metrics, score)
        if counts["critical"]:
            score = min(score, CAP_CRITICAL)
        elif counts["high"]:
            score = min(score, CAP_HIGH)
        res.score = round(score, 1)
        w = float(weights.get(cid, 0))
        rows.append({"id": cid, "name": res.name, "score": res.score, "counts": counts, "weight": w, "reason": ""})
        total_w += w
        total += w * res.score
    overall = round(total / total_w, 1) if total_w else None
    all_counts = {s: sum(r["counts"][s] for r in rows) for s in SEV_WEIGHT}
    if overall is not None and all_counts["critical"]:
        overall = min(overall, 79.0)  # 还有“严重”问题没处理，综合分不给到 A / B+
    return {"overall": overall, "grade": grade(overall), "categories": rows, "counts": all_counts,
            "kloc": round(kloc, 1), "method": "每类：100·exp(−加权问题密度/10)，权重 严重25 / 高8 / 中2 / 低0.3；"
            "有严重问题的类别 ≤59，有高危的 ≤84；07 类以覆盖率为主；综合分为各适用类别的加权平均。"}


def _coverage_score(m: dict, finding_score: float) -> float:
    cov = m.get("python_coverage") or m.get("rust_coverage")
    parts = []
    if m.get("python_coverage"):
        parts.append(m["python_coverage"]["combined_pct"])
    if m.get("rust_coverage"):
        parts.append(m["rust_coverage"]["line_pct"])
    if not cov:
        return finding_score
    pct = sum(parts) / len(parts)
    s = min(100.0, pct / 0.85)  # 85% 覆盖率记满分
    for key in ("python_tests", "rust_tests"):
        if m.get(key) and not m[key].get("ok", True):
            s = min(s, 40.0)
    return s


def grade(score: float | None) -> str:
    if score is None:
        return "-"
    for lim, g in ((90, "A"), (80, "B"), (70, "C"), (60, "D")):
        if score >= lim:
            return g
    return "E"
