"""Semgrep 的调用方式：副本不能被上层仓库的 .gitignore 藏掉，通配符不能出现在命令行参数里。"""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from auditkit import linters
from tests import make_ctx

FILES = {"app/main.py": "x = 1\n", "tests/test_main.py": "y = 2\n", "vendor/gen_a.py": "z = 3\n"}
CFG = {"exclude": ["vendor/gen_*"], "test_globs": ["tests/*"], "security": {"semgrep_configs": ["p/python"]}}


def _hit(path: str) -> dict:
    return {"check_id": "python.lang.security.audit.eval-detected", "path": path, "start": {"line": 1},
            "extra": {"severity": "WARNING", "message": "eval", "metadata": {}, "lines": "x = 1"}}


class Invocation(unittest.TestCase):
    def run_it(self, scanned: list[str], results: list[dict]):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        ctx = make_ctx(Path(os.path.realpath(d.name)), FILES, cfg=CFG)
        ctx.files = [f for f in ctx.files if not f.startswith("vendor/gen_")]  # audit.py 在建 Context 之前就按 exclude 过滤了
        calls: list[tuple[list[str], dict]] = []

        def fake_run(cmd, **kw):
            calls.append(([str(c) for c in cmd], kw))
            return 0, json.dumps({"results": results, "errors": [], "paths": {"scanned": scanned}}), ""

        with mock.patch.object(linters, "kit_tool", return_value="semgrep"), mock.patch.object(linters, "run", fake_run):
            linters.run_semgrep(ctx)
        return ctx, calls

    def test_command_line(self) -> None:
        _, calls = self.run_it(["app/main.py"], [])
        self.assertEqual(len(calls), 1)
        cmd, kw = calls[0]
        self.assertIn("--no-git-ignore", cmd)
        self.assertNotIn("--exclude", cmd)
        self.assertFalse([a for a in cmd if "*" in a or "?" in a], "命令行里不能有通配符")
        self.assertEqual(cmd[-1], ".")
        self.assertEqual(Path(kw["cwd"]).name, "baseline")

    def test_excluded_and_test_files_are_filtered_from_results(self) -> None:
        ctx, _ = self.run_it(["app/main.py", "tests/test_main.py", "vendor/gen_a.py"],
                             [_hit("app/main.py"), _hit("tests/test_main.py"), _hit("vendor/gen_a.py")])
        self.assertEqual([f.file for f in ctx.cat("02").findings], ["app/main.py"])
        self.assertNotIn("x = 1", (ctx.raw / "semgrep.json").read_text(encoding="utf-8").replace('"lines"', ""))

    def test_nothing_scanned_is_reported_not_silently_passed(self) -> None:
        ctx, _ = self.run_it([], [])
        res = ctx.cat("02")
        self.assertFalse([t for t in res.tools_used if "Semgrep" in t])
        self.assertTrue([why for t, why in res.tools_skipped if t == "Semgrep" and "没有扫描到任何文件" in why])


if __name__ == "__main__":
    unittest.main()
