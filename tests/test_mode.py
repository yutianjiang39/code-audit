"""执行模式：static 不启动项目的解释器、cargo、测试、基准；两种模式下子进程都拿不到白名单之外的环境变量。

所有子进程调用都被打桩记录下来，不会真的执行；联网查询同样被打桩。
"""
from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import audit
from auditkit import fixes, util
from auditkit.checks import deps
from tests import make_ctx

FILES = {
    "app/__init__.py": "",
    "app/main.py": "import os\n\n\ndef add(a, b):\n    return a + b\n",
    "tests/__init__.py": "",
    "tests/test_main.py": "import unittest\n\nfrom app.main import add\n\n\nclass T(unittest.TestCase):\n"
                          "    def test_add(self):\n        self.assertEqual(add(1, 2), 3)\n",
    "requirements.txt": "requests==2.19.0\nflask>=1.0\n",
    "rust/Cargo.toml": '[package]\nname = "fixture"\nversion = "0.1.0"\nedition = "2021"\nbuild = "build.rs"\n\n[dependencies]\nserde = "1"\n',
    "rust/Cargo.lock": 'version = 3\n\n[[package]]\nname = "fixture"\nversion = "0.1.0"\n\n[[package]]\nname = "serde"\n'
                       'version = "1.0.100"\nsource = "registry+https://github.com/rust-lang/crates.io-index"\n',
    "rust/build.rs": 'fn main() { println!("cargo:rerun-if-changed=build.rs"); }\n',
    "rust/src/lib.rs": "pub fn add(a: i32, b: i32) -> i32 { a + b }\n\n#[cfg(test)]\nmod tests {\n    #[test]\n    fn adds() { assert_eq!(super::add(1, 2), 3); }\n}\n",
    "package.json": '{"name": "fixture", "version": "1.0.0", "scripts": {"test": "node evil.js"}, "dependencies": {"left-pad": "1.0.0"}}\n',
    "package-lock.json": '{"name": "fixture", "lockfileVersion": 3, "packages": {"": {"name": "fixture"}}}\n',
    "web/index.html": "<!doctype html><html lang='en'><head><title>t</title></head><body><input id='u'></body></html>\n",
    "db/schema.sql": "CREATE TABLE t (id INTEGER PRIMARY KEY, a TEXT);\nSELECT * FROM t WHERE a = ?;\n",
}
BENCH_MARK = "BENCH-MARKER-7f3a"


class Recorder:
    """替身 subprocess.run：只记录，不执行。"""

    def __init__(self, rc: int):
        self.rc = rc
        self.calls: list[tuple[list[str], dict, str]] = []

    def __call__(self, cmd, cwd=None, env=None, **_kw):
        self.calls.append(([str(c) for c in cmd], dict(env or {}), str(cwd or "")))
        return subprocess.CompletedProcess(cmd, self.rc, "", "")

    def lines(self) -> str:
        return "\n".join(" ".join(c) for c, _, _ in self.calls)


class Base(unittest.TestCase):
    def setUp(self) -> None:
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.tmp = Path(os.path.realpath(d.name))
        self.py = self.tmp / "projvenv" / "python.exe"  # 只需要“存在”，永远不会被真的启动
        self.py.parent.mkdir()
        self.py.write_bytes(b"")
        self.cfg = {
            "test_globs": ["tests/*"],
            "python": {"interpreter": str(self.py), "min_version": "3.10", "test_cmd": ["-m", "unittest", "discover", "-s", "tests", "-t", "."]},
            "rust": {"manifest_dirs": ["rust"], "run_tests": True},
            "bench": [{"name": "导入", "cmd": ["{python}", "-c", f"import app  # {BENCH_MARK}"], "repeat": 1},
                      {"name": "脚本", "cmd": ["some-bench-tool", BENCH_MARK], "repeat": 1}],
        }
        env = mock.patch.dict(os.environ, {"FAKE_SECRET_TOKEN": "do-not-leak", "AWS_SECRET_ACCESS_KEY": "do-not-leak",
                                           "HTTPS_PROXY": "http://127.0.0.1:9", "FAKE_ALLOWED": "ok"})
        env.start()
        self.addCleanup(env.stop)
        net = mock.patch.object(deps, "_http_json", side_effect=OSError("测试里不联网"))
        self.http = net.start()
        self.addCleanup(net.stop)
        self.addCleanup(util.ENV_EXTRA.clear)

    def audit(self, mode: str, rc: int) -> tuple[Recorder, object]:
        rec = Recorder(rc)
        root = self.tmp / f"{mode}{rc}"
        with mock.patch.object(util.subprocess, "run", rec):
            ctx = make_ctx(root, FILES, mode=mode, cfg=self.cfg)
            for cats, label, fn in audit.stages():
                try:
                    fn(ctx)
                except Exception as e:  # noqa: BLE001
                    for c in cats.split(","):
                        ctx.cat(c).skip(label, f"{type(e).__name__}: {e}")
            audit._finalize_findings(ctx)
            ctx.shared["fix"] = fixes.run_fix_stage(ctx)
        return rec, ctx


def _exe(argv: list[str]) -> str:
    name = Path(argv[0]).name.lower()
    return name[:-4] if name.endswith((".exe", ".cmd", ".bat")) else name


class StaticMode(Base):
    def test_nothing_from_the_project_is_executed(self) -> None:
        for rc in (0, 1):  # 工具“成功”和“失败”两条路径都走一遍
            with self.subTest(rc=rc):
                rec, ctx = self.audit("static", rc)
                self.assertTrue(rec.calls, "静态模式仍然应该运行分析工具")
                for argv, _env, cwd in rec.calls:
                    line = " ".join(argv)
                    self.assertNotEqual(Path(argv[0]), self.py, f"启动了项目解释器：{line}")
                    self.assertNotIn(str(self.py), argv, f"把项目解释器交给了别的工具：{line}")
                    self.assertNotEqual(_exe(argv), "cargo", f"静态模式调用了 cargo：{line}")
                    self.assertNotIn(_exe(argv), ("npm", "npx", "node") if self._in_copy(ctx, cwd) else (), f"在项目副本里运行了 {line}")
                    self.assertNotIn(BENCH_MARK, line, f"运行了基准：{line}")
                    self.assertFalse("unittest" in argv or "pytest" in argv or "coverage" in line.lower(), f"运行了测试：{line}")
                for cid, tool in (("07", "Coverage.py"), ("01", "Clippy")):
                    reasons = [r for t, r in ctx.cat(cid).tools_skipped if tool.lower() in t.lower()]
                    self.assertTrue(reasons and all("静态模式" in r for r in reasons), f"{cid} 类应写明 {tool} 因静态模式跳过：{reasons}")
                self.assertEqual(ctx.shared["fix"].get("bench"), [])

    @staticmethod
    def _in_copy(ctx, cwd: str) -> bool:
        return bool(cwd) and any(util._inside(cwd, os.path.realpath(r)) for r in (ctx.base, ctx.fixed) if Path(r).exists())

    def test_python_dependencies_are_checked_without_the_interpreter(self) -> None:
        _rec, ctx = self.audit("static", 0)
        urls = [c.args[0] for c in self.http.call_args_list]
        self.assertTrue(any("osv.dev" in u for u in urls), "锁定版本的依赖应直接查 OSV")
        self.assertEqual(ctx.cat("09").metrics.get("python_packages_unverified"), 1)  # flask>=1.0 没锁版本


class Environment(Base):
    def test_children_do_not_inherit_the_environment(self) -> None:
        for mode in ("static", "local"):
            with self.subTest(mode=mode):
                rec, _ctx = self.audit(mode, 0)
                for argv, env, _cwd in rec.calls:
                    upper = {k.upper() for k in env}
                    self.assertFalse(upper & {"FAKE_SECRET_TOKEN", "AWS_SECRET_ACCESS_KEY"}, "环境变量泄漏给了 " + " ".join(argv[:3]))
                    self.assertIn("PATH", upper)

    def test_proxy_only_for_network_calls(self) -> None:
        self.assertNotIn("HTTPS_PROXY", util.base_env())
        self.assertIn("HTTPS_PROXY", util.base_env(network=True))
        rec, _ctx = self.audit("local", 0)
        ran_project = [(argv, env) for argv, env, _ in rec.calls if Path(argv[0]) == self.py and "unittest" in argv]
        self.assertTrue(ran_project)
        for argv, env in ran_project:
            self.assertNotIn("HTTPS_PROXY", {k.upper() for k in env}, "执行项目代码的调用不该带代理：" + " ".join(argv))

    def test_env_passthrough_is_explicit(self) -> None:
        self.assertNotIn("FAKE_ALLOWED", util.base_env())
        util.ENV_EXTRA.add("fake_allowed")
        self.assertEqual(util.base_env().get("FAKE_ALLOWED"), "ok")
        self.assertNotIn("FAKE_SECRET_TOKEN", util.base_env())

    def test_git_never_runs_repository_hooks(self) -> None:
        for mode in ("static", "local"):
            rec, _ctx = self.audit(mode, 0)
            gits = [argv for argv, _, _ in rec.calls if _exe(argv) == "git"]
            self.assertTrue(gits)
            for argv in gits:
                self.assertIn("core.fsmonitor=false", argv, " ".join(argv))
                self.assertIn("core.hooksPath=", argv, " ".join(argv))


class LocalMode(Base):
    def test_local_mode_does_run_the_project(self) -> None:
        """对照组：同一个夹具在 local 模式下确实会启动解释器和 cargo，说明上面的断言不是空转。"""
        rec, ctx = self.audit("local", 0)
        self.assertTrue(any(Path(argv[0]) == self.py for argv, _, _ in rec.calls), rec.lines())
        self.assertTrue(any(_exe(argv) == "cargo" for argv, _, _ in rec.calls), rec.lines())
        self.assertIn(BENCH_MARK, rec.lines())
        self.assertFalse(any("静态模式" in r for cid in ("01", "07") for _, r in ctx.cat(cid).tools_skipped))


if __name__ == "__main__":
    unittest.main()
