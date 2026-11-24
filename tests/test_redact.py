"""脱敏：每种模式一个用例，再加一条端到端——假密钥不能出现在 reports/ 下的任何文件里。

这里的“密钥”都是运行时拼出来的假值，源码里没有任何一个完整的、形似真实凭据的字符串。
"""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

import audit
from auditkit import report, score
from auditkit.model import Finding
from auditkit.redact import MASK, redact
from auditkit.util import redact_json_file, write_raw
from tests import make_ctx

FAKE = {
    "aws": "AKIA" + "Q7XK2MPL9RTW4ZBN",
    "github": "ghp_" + "a1B2c3D4e5" * 4,
    "github_pat": "github_pat_" + "11ABCDEFG0" * 4,
    "slack": "xoxb-" + "123456789012-abcdefABCDEF",
    "sk": "sk-" + "proj9" + "Zx8Yw7Vu6Ts5Rq4Po3Nm2",
    "google": "AIza" + "Sy" + "D4e5F6g7H8" * 3 + "abc",
    "jwt": "eyJ" + "hbGciOiJIUzI1NiJ9" + ".eyJ" + "zdWIiOiIxMjM0NTY3ODkwIn0" + "." + "dBjftJeZ4CVPmB92K27uhbUJU1p1r",
    "telegram": "123456789:" + "AAH" + "x9Kq2LmN8pRs4TuVw6YzA1bC3dE5fG7h",
    "url_pass": "hunter2-" + "Zq9xLm",
    "assigned": "s3cr3t-" + "Vq8mK2pL",
    "bare": "Zx81kq" + "PLm20vbQ7rT5",
}
PEM_BODY = "MIIEvQIBADANBgkqhkiG9w0BAQEFAASC" + "fakefakefake"
PEM = f"-----BEGIN RSA PRIVATE KEY-----\n{PEM_BODY}\n-----END RSA PRIVATE KEY-----"


class Patterns(unittest.TestCase):
    def gone(self, text: str, secret: str) -> str:
        out = redact(text)
        self.assertNotIn(secret, out)
        self.assertIn(MASK, out)
        return out

    def test_tokens(self) -> None:
        for name in ("aws", "github", "github_pat", "slack", "sk", "google", "jwt", "telegram"):
            with self.subTest(name):
                out = self.gone(f"found {FAKE[name]} at config.py:3", FAKE[name])
                self.assertIn("config.py:3", out)  # 位置信息保留

    def test_tokens_inside_json_escaped_text(self) -> None:
        """工具的 JSON 输出里换行是字面的 \\n，密钥前面紧贴着一个字母 n。"""
        for name in ("aws", "github", "github_pat", "slack", "sk", "google", "jwt", "telegram"):
            with self.subTest(name):
                self.gone(json.dumps({"lines": f"x = 1\n{FAKE[name]}\ny = 2"}), FAKE[name])
        self.gone(json.dumps({"lines": f"a\n{PEM}\nb"}), PEM_BODY)

    def test_pem_block(self) -> None:
        out = self.gone(f"before\n{PEM}\nafter", PEM_BODY)
        self.assertIn("before", out)
        self.assertIn("after", out)

    def test_truncated_pem_block(self) -> None:
        self.gone("-----BEGIN PRIVATE KEY-----\n" + PEM_BODY, PEM_BODY)

    def test_url_credentials(self) -> None:
        out = self.gone(f"postgresql://admin:{FAKE['url_pass']}@db.internal:5432/app", FAKE["url_pass"])
        self.assertIn("db.internal:5432/app", out)

    def test_quoted_assignment(self) -> None:
        for text in (f'password = "{FAKE["assigned"]}"', f"API_KEY: '{FAKE['assigned']}'", f'"client_secret": "{FAKE["assigned"]}"',
                     f'db_passwd="{FAKE["assigned"]}"', f'{{\\"auth_token\\": \\"{FAKE["assigned"]}\\"}}'):
            with self.subTest(text):
                self.gone(text, FAKE["assigned"])

    def test_bare_assignment(self) -> None:
        self.gone(f"SECRET_KEY={FAKE['bare']}", FAKE["bare"])
        self.gone(f"export TG_TOKEN={FAKE['bare']}", FAKE["bare"])

    def test_ordinary_text_is_untouched(self) -> None:
        for text in ("token = get_token(request)", "password = None", "def check_password(self, password: str) -> bool:",
                     "secret_key = os.environ['SECRET_KEY']", "auth = True", "第 12 行：未使用的导入 os",
                     "SELECT id FROM users WHERE token = ?", "https://example.com/a/b?x=1", ""):
            with self.subTest(text):
                self.assertEqual(redact(text), text)


class ToolReports(unittest.TestCase):
    def test_json_written_by_tools_is_redacted_and_stays_valid(self) -> None:
        """jscpd 的报告带重复代码的原文片段，Lighthouse 的带页面内容：它们不经过 write_raw。"""
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "jscpd-report.json"
            doc = {"duplicates": [{"fragment": f'API_TOKEN = "{FAKE["github"]}"\ndef f(x):\n    return x\n', "lines": 3,
                                   "firstFile": {"name": "app/a.py", "start": 1}}], "statistics": {"total": {"clones": 1}}}
            p.write_text(json.dumps(doc), encoding="utf-8")
            redact_json_file(p)
            text = p.read_text(encoding="utf-8")
            self.assertNotIn(FAKE["github"], text)
            back = json.loads(text)
            self.assertEqual(back["duplicates"][0]["firstFile"], {"name": "app/a.py", "start": 1})
            self.assertEqual(back["statistics"], doc["statistics"])
            self.assertIn("def f(x):", back["duplicates"][0]["fragment"])


class EndToEnd(unittest.TestCase):
    def test_no_secret_reaches_the_report_directory(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(os.path.realpath(d))
            ctx = make_ctx(tmp, {"app/config.py": "DEBUG = True\n", "app/main.py": "print('hi')\n"})
            # 没有上下文的随机串认不出来，所以这三个按它们实际出现的样子给：URL 口令、带引号的赋值、裸赋值
            in_context = {"url_pass": f"redis://default:{FAKE['url_pass']}@cache:6379/0",
                          "assigned": f"db_password = '{FAKE['assigned']}'", "bare": f"SECRET_KEY={FAKE['bare']}"}
            every = "\n".join([*({**FAKE, **in_context}).values(), PEM])
            sec = ctx.cat("02")
            sec.use("Semgrep")
            sec.findings.append(Finding(
                category="02", tool="Semgrep", rule="hardcoded-secret", severity="high", file="app/config.py", line=1,
                message=f"硬编码的密钥：api_key = \"{FAKE['assigned']}\" 和 {FAKE['github']}",
                cause=f"连接串 mysql://root:{FAKE['url_pass']}@10.0.0.5/app 写在代码里；{FAKE['aws']}",
                fix=f"把 {FAKE['sk']} 换成环境变量；{FAKE['telegram']}",
                fix_code=f"TOKEN = \"{FAKE['jwt']}\"\nSLACK={FAKE['slack']}\n{PEM}\nAPI_KEY={FAKE['bare']}\n{FAKE['google']} {FAKE['github_pat']}"))
            sec.notes.append(f"工具输出里出现了 {FAKE['github']}")
            sec.skip("某工具", f"失败：password='{FAKE['assigned']}'")
            write_raw(ctx, "semgrep.json", json.dumps({"lines": every}))
            write_raw(ctx, "tests_baseline.txt", every)
            write_raw(ctx, "errors.txt", "Traceback …\n" + every, mode="a")
            ctx.say("[02] " + every)

            audit._finalize_findings(ctx)
            report.write_all(ctx, score.score_all(ctx))
            (ctx.out / "run.log").write_text(redact("\n".join(ctx.log)), encoding="utf-8")

            written = [p for p in ctx.out.rglob("*") if p.is_file()]
            self.assertGreaterEqual({p.name for p in written},
                                    {"report.html", "report.md", "findings.json", "run.log", "semgrep.json", "errors.txt"})
            for p in written:
                text = p.read_bytes().decode("utf-8", "replace")
                for name, secret in {**FAKE, "pem": PEM_BODY}.items():
                    self.assertNotIn(secret, text, f"{name} 出现在 {p.relative_to(ctx.out)}")
            # 脱敏的是值，不是整条命中
            data = json.loads((ctx.out / "findings.json").read_text(encoding="utf-8"))
            hit = next(f for f in data["findings"] if f["rule"] == "hardcoded-secret")
            self.assertEqual((hit["file"], hit["line"]), ("app/config.py", 1))
            self.assertIn(MASK, hit["message"])
            self.assertIn("硬编码的密钥", hit["message"])


if __name__ == "__main__":
    unittest.main()
