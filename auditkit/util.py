"""通用工具：子进程、文件清单、沙箱副本、体积统计。"""
from __future__ import annotations

import fnmatch
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

WIN = os.name == "nt"
BIN_DIR = "Scripts" if WIN else "bin"
EXE = ".exe" if WIN else ""

# 永远不复制、不审查的目录和文件（密钥、构建产物、虚拟环境）
ALWAYS_SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "target", "__pycache__", ".ruff_cache",
                    ".pytest_cache", ".mypy_cache", "dist", "build", "secrets", ".idea", ".vscode"}
ALWAYS_SKIP_GLOBS = ("*.key", "*.pem", "*.p12", "*.pfx", ".env", ".env.*", "*.pyc")


def run(cmd, cwd=None, timeout=900, env=None, input_text=None) -> tuple[int, str, str]:
    """运行命令，返回 (退出码, stdout, stderr)。工具缺失返回 127，超时返回 124。"""
    full_env = dict(os.environ)
    full_env.update({"PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8", "NO_COLOR": "1"})
    cargo_bin = Path.home() / ".cargo" / "bin"
    if cargo_bin.is_dir():
        full_env["PATH"] = str(cargo_bin) + os.pathsep + full_env.get("PATH", "")
    if env:
        full_env.update(env)
    try:
        p = subprocess.run([str(c) for c in cmd], cwd=str(cwd) if cwd else None, env=full_env,
                           capture_output=True, timeout=timeout, input=input_text,
                           text=True, encoding="utf-8", errors="replace")
        return p.returncode, p.stdout or "", p.stderr or ""
    except FileNotFoundError:
        return 127, "", f"找不到命令：{cmd[0]}"
    except subprocess.TimeoutExpired as e:
        out = e.stdout.decode("utf-8", "replace") if isinstance(e.stdout, bytes) else (e.stdout or "")
        return 124, out, f"超时（{timeout}s）"


def timed(cmd, cwd=None, timeout=900, env=None) -> tuple[int, str, str, float]:
    t = time.perf_counter()
    rc, out, err = run(cmd, cwd=cwd, timeout=timeout, env=env)
    return rc, out, err, time.perf_counter() - t


def kit_tool(kit_root: Path, name: str) -> str | None:
    """审查系统自己的虚拟环境里的工具；没有就找 PATH。"""
    p = kit_root / ".venv" / BIN_DIR / (name + EXE)
    if p.exists():
        return str(p)
    return shutil.which(name)


def kit_python(kit_root: Path) -> str:
    p = kit_root / ".venv" / BIN_DIR / ("python" + EXE)
    return str(p) if p.exists() else sys.executable


def which(name: str) -> str | None:
    cargo = Path.home() / ".cargo" / "bin" / (name + EXE)
    if cargo.exists():
        return str(cargo)
    return shutil.which(name) or (shutil.which(name + ".cmd") if WIN else None)


def match_any(rel: str, globs) -> bool:
    return any(fnmatch.fnmatch(rel, g) or fnmatch.fnmatch(rel, g.rstrip("/") + "/*") for g in globs)


def _skipped(rel: str) -> bool:
    parts = rel.split("/")
    if any(p in ALWAYS_SKIP_DIRS for p in parts[:-1]):
        return True
    return any(fnmatch.fnmatch(parts[-1], g) for g in ALWAYS_SKIP_GLOBS)


def list_project_files(target: Path) -> list[str]:
    """git 仓库：受版本控制 + 未忽略的新文件；否则遍历目录。再过滤密钥和构建产物。"""
    rels: list[str] = []
    rc, out, _ = run(["git", "-c", "core.quotepath=false", "ls-files", "-co", "--exclude-standard", "-z"],
                     cwd=target, timeout=120)
    if rc == 0 and out:
        rels = [r for r in out.split("\0") if r]
    else:
        for root, dirs, names in os.walk(target):
            dirs[:] = [d for d in dirs if d not in ALWAYS_SKIP_DIRS]
            for n in names:
                rels.append(Path(root, n).relative_to(target).as_posix())
    return sorted(r for r in rels if not _skipped(r) and (target / r).is_file())


def sync_copy(target: Path, dest: Path, rels: list[str]) -> None:
    """把项目文件同步到沙箱副本（保留修改时间，Rust 增量编译缓存才有效）。"""
    dest.mkdir(parents=True, exist_ok=True)
    want = set(rels)
    for root, dirs, names in os.walk(dest):
        dirs[:] = [d for d in dirs if d not in ("target", "__pycache__")]
        for n in names:
            rel = Path(root, n).relative_to(dest).as_posix()
            if rel not in want:
                try:
                    os.remove(Path(root, n))
                except OSError:
                    pass
    for rel in rels:
        src, dst = target / rel, dest / rel
        try:
            s = src.stat()
            if dst.exists():
                d = dst.stat()
                if d.st_size == s.st_size and int(d.st_mtime) == int(s.st_mtime) and \
                        dst.read_bytes() == src.read_bytes():
                    continue
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
        except OSError:
            continue


COMMENT_PREFIX = {".py": "#", ".rs": "//", ".sh": "#", ".toml": "#", ".js": "//", ".ts": "//",
                  ".tsx": "//", ".jsx": "//", ".go": "//", ".java": "//", ".sql": "--"}


def size_stats(root: Path, rels: list[str]) -> dict:
    """按扩展名统计：文件数、字节、总行数、代码行（去掉空行和整行注释）。"""
    by: dict[str, dict] = {}
    for rel in rels:
        ext = os.path.splitext(rel)[1].lower()
        if ext not in COMMENT_PREFIX:
            continue
        try:
            data = (root / rel).read_bytes()
        except OSError:
            continue
        text = data.decode("utf-8", "replace")
        lines = text.splitlines()
        pre = COMMENT_PREFIX[ext]
        code = sum(1 for ln in lines if ln.strip() and not ln.strip().startswith(pre))
        s = by.setdefault(ext, {"files": 0, "bytes": 0, "lines": 0, "code": 0})
        s["files"] += 1
        s["bytes"] += len(data)
        s["lines"] += len(lines)
        s["code"] += code
    total = {k: sum(v[k] for v in by.values()) for k in ("files", "bytes", "lines", "code")}
    return {"by_ext": by, "total": total}


def read_text(p: Path) -> str:
    try:
        return p.read_bytes().decode("utf-8", "replace")
    except OSError:
        return ""


def chunks(seq: list, n: int):
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


def norm_rel(path: str, base: Path) -> str:
    """把工具输出的路径统一成相对沙箱根的正斜杠路径。"""
    p = Path(path)
    try:
        if p.is_absolute():
            p = p.resolve().relative_to(base.resolve())
    except (ValueError, OSError):
        pass
    return p.as_posix()
