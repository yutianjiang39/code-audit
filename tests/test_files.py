"""文件清单与副本：不跟随链接、不越出项目根目录、不碰敏感文件。"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from auditkit.util import _skipped, list_project_files, safe_file, safe_glob, sync_copy
from tests import write_tree

PEM = "-----BEGIN OPENSSH PRIVATE KEY-----\nnot-a-real-key\n-----END OPENSSH PRIVATE KEY-----\n"


def _symlink(src: Path, dst: Path, is_dir: bool = False) -> bool:
    try:
        os.symlink(src, dst, target_is_directory=is_dir)
        return True
    except (OSError, NotImplementedError):
        return False


def _junction(src: Path, dst: Path) -> bool:
    try:
        import _winapi
        _winapi.CreateJunction(str(src), str(dst))
        return True
    except (ImportError, OSError, AttributeError):
        return False


class Base(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(os.path.realpath(self._tmp.name))
        self.proj = self.tmp / "proj"
        self.outside = self.tmp / "outside"
        write_tree(self.proj, {"app/main.py": "print('hi')\n", "README.md": "# x\n"})
        write_tree(self.outside, {"private.txt": "OUTSIDE-CONTENT\n", "sub/deep.py": "x = 1\n"})

    def copied(self, dest: Path) -> set[str]:
        return {p.relative_to(dest).as_posix() for p in dest.rglob("*") if p.is_file()}


class SensitiveNames(Base):
    def test_secret_files_are_not_listed(self) -> None:
        names = ["secrets/api.txt", ".env", ".env.production", "deploy/server.pem", "tls/site.key", "id_rsa", "id_ed25519.pub",
                 ".ssh/config", ".aws/credentials", "home/.aws/config", ".kube/config", ".npmrc", ".pypirc", ".netrc",
                 ".git-credentials", "infra/terraform.tfstate", "infra/prod.tfvars", "gcp/service-account-prod.json",
                 "credentials.json", "keys/release.jks", "vpn/office.ovpn", "pass.kdbx", "putty.ppk", ".htpasswd",
                 ".docker/config.json", ".gnupg/secring.gpg"]
        write_tree(self.proj, dict.fromkeys(names, "x\n"))
        stats: dict = {}
        files = list_project_files(self.proj, stats)
        self.assertEqual(files, ["README.md", "app/main.py"])
        self.assertEqual(stats["sensitive"], len(names))
        self.assertEqual(stats["links"], 0)

    def test_names_are_case_insensitive(self) -> None:
        for rel in (".ENV", "Secrets/x.txt", "ID_RSA", "Deploy/Server.PEM", ".SSH/known_hosts", "Credentials.JSON"):
            self.assertTrue(_skipped(rel), rel)
        for rel in ("app/environment.py", "docs/keys.md", "src/token.py", "credentials.py"):
            self.assertFalse(_skipped(rel), rel)

    def test_private_key_content_is_sniffed(self) -> None:
        write_tree(self.proj, {"deploy/backup.txt": PEM, "notes/ok.txt": "BEGIN something else\n"})
        stats: dict = {}
        files = list_project_files(self.proj, stats)
        self.assertNotIn("deploy/backup.txt", files)
        self.assertIn("notes/ok.txt", files)
        self.assertEqual(stats["sensitive"], 1)

    def test_sensitive_files_are_never_copied(self) -> None:
        write_tree(self.proj, {".env": "A=1\n", "secrets/k.txt": "x\n", "deploy/backup.txt": PEM})
        dest = self.tmp / "copy"
        # 即使调用方硬把这些路径塞进清单，清单函数也不会给出它们
        sync_copy(self.proj, dest, list_project_files(self.proj))
        self.assertEqual(self.copied(dest), {"README.md", "app/main.py"})


class Links(Base):
    def test_symlinked_file_is_not_listed_or_copied(self) -> None:
        if not _symlink(self.outside / "private.txt", self.proj / "app" / "leak.txt"):
            self.skipTest("这台机器不能创建符号链接")
        stats: dict = {}
        files = list_project_files(self.proj, stats)
        self.assertNotIn("app/leak.txt", files)
        self.assertEqual(stats["links"], 1)
        dest = self.tmp / "copy"
        sync_copy(self.proj, dest, [*files, "app/leak.txt"])  # 硬塞进清单也不复制
        self.assertEqual(self.copied(dest), {"README.md", "app/main.py"})

    def test_symlink_inside_project_is_not_followed_either(self) -> None:
        if not _symlink(self.proj / "README.md", self.proj / "alias.md"):
            self.skipTest("这台机器不能创建符号链接")
        self.assertNotIn("alias.md", list_project_files(self.proj))

    def _dir_link_case(self, make) -> None:
        if not make(self.outside, self.proj / "linked"):
            self.skipTest("这台机器不能创建这种目录链接")
        stats: dict = {}
        files = list_project_files(self.proj, stats)
        self.assertEqual(files, ["README.md", "app/main.py"])
        self.assertGreaterEqual(stats["links"], 1)
        self.assertFalse(safe_file(self.proj, "linked/private.txt"))
        self.assertEqual(safe_glob(self.proj, ["linked/*", "**/*.txt"]), [])
        dest = self.tmp / "copy"
        sync_copy(self.proj, dest, [*files, "linked/private.txt", "linked/sub/deep.py"])
        self.assertEqual(self.copied(dest), {"README.md", "app/main.py"})
        self.assertFalse((dest / "linked").exists())

    def test_symlinked_directory(self) -> None:
        self._dir_link_case(lambda src, dst: _symlink(src, dst, is_dir=True))

    def test_windows_junction(self) -> None:
        if os.name != "nt":
            self.skipTest("junction 只在 Windows 上有")
        self._dir_link_case(_junction)

    def test_path_escapes_are_rejected(self) -> None:
        self.assertFalse(safe_file(self.proj, "../outside/private.txt"))
        self.assertFalse(safe_file(self.proj, str(self.outside / "private.txt")))
        self.assertFalse(safe_file(self.proj, "app"))  # 目录不是文件
        self.assertTrue(safe_file(self.proj, "app/main.py"))

    def test_link_left_in_copy_is_not_written_through(self) -> None:
        """副本里如果留有指向外部的链接（local 模式下项目代码可能留下），下次同步不能顺着写出去。"""
        dest = self.tmp / "copy"
        dest.mkdir()
        made = _symlink(self.outside, dest / "app", is_dir=True) or (os.name == "nt" and _junction(self.outside, dest / "app"))
        if not made:
            self.skipTest("这台机器不能创建目录链接")
        sync_copy(self.proj, dest, ["README.md", "app/main.py"])
        self.assertFalse((self.outside / "main.py").exists(), "写穿到了副本目录之外")
        self.assertEqual((self.outside / "private.txt").read_text(encoding="utf-8"), "OUTSIDE-CONTENT\n")
        self.assertFalse(os.path.islink(dest / "app"))
        self.assertTrue((dest / "app" / "main.py").is_file())

    def test_file_link_left_in_copy_is_replaced(self) -> None:
        dest = self.tmp / "copy"
        dest.mkdir()
        if not _symlink(self.outside / "private.txt", dest / "README.md"):
            self.skipTest("这台机器不能创建符号链接")
        sync_copy(self.proj, dest, ["README.md"])
        self.assertEqual((self.outside / "private.txt").read_text(encoding="utf-8"), "OUTSIDE-CONTENT\n")
        self.assertFalse(os.path.islink(dest / "README.md"))
        self.assertEqual((dest / "README.md").read_text(encoding="utf-8"), "# x\n")


class Globs(Base):
    def test_profile_globs_skip_sensitive_names(self) -> None:
        write_tree(self.proj, {"logs/app.log": "ok\n", "logs/.env": "A=1\n", "secrets/db.sqlite3": "x"})
        got = [Path(p).relative_to(self.proj).as_posix() for p in safe_glob(self.proj, ["logs/*", "logs/.*", "secrets/*", "../outside/*"])]
        self.assertEqual(got, ["logs/app.log"])


if __name__ == "__main__":
    unittest.main()
