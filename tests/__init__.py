"""审查系统自己的测试（标准库 unittest，不需要 .venv）：python -m unittest discover -s tests -t ."""
from __future__ import annotations

import sys
import time
from pathlib import Path

KIT = Path(__file__).resolve().parent.parent
if str(KIT) not in sys.path:
    sys.path.insert(0, str(KIT))

from auditkit.model import CATEGORIES, Context
from auditkit.util import list_project_files, sync_copy


def write_tree(root: Path, files: dict[str, str]) -> None:
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8", newline="\n")


def make_ctx(tmp: Path, files: dict[str, str], mode: str = "static", cfg: dict | None = None) -> Context:
    """在 tmp 里造一个被审查项目，按 audit.py 的方式建好副本和上下文。"""
    target = tmp / "proj"
    write_tree(target, files)
    out = tmp / "reports"
    (out / "raw").mkdir(parents=True, exist_ok=True)
    excluded: dict = {}
    all_files = list_project_files(target, excluded)
    conf = {"name": "fixture", "target": str(target), **(cfg or {})}
    ctx = Context(cfg=conf, kit_root=KIT, target=target, base=tmp / "work" / "baseline", fixed=tmp / "work" / "fixed",
                  out=out, raw=out / "raw", files=list(all_files))
    ctx.shared.update(all_files=all_files, started=time.strftime("%Y-%m-%d %H:%M:%S"), mode=mode, excluded=excluded, only=None)
    ctx.say = ctx.log.append  # 测试里不往终端打印
    sync_copy(target, ctx.base, all_files)
    for cid in CATEGORIES:
        ctx.cat(cid)
    return ctx
