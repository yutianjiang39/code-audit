"""通用工具：子进程、文件清单、沙箱副本、体积统计。"""
from __future__ import annotations

import fnmatch
import glob as _glob
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

WIN = os.name == "nt"
BIN_DIR = "Scripts" if WIN else "bin"
EXE = ".exe" if WIN else ""

# 永远不复制、不审查的目录和文件（密钥、构建产物、虚拟环境）
SECRET_DIRS = {"secrets", ".ssh", ".aws", ".azure", ".gcloud", ".gnupg", ".kube", ".docker", ".terraform"}
SECRET_GLOBS = ("*.key", "*.pem", "*.p12", "*.pfx", ".env", ".env.*",
                "id_rsa*", "id_dsa*", "id_ecdsa*", "id_ed25519*", "*.ppk", "*.jks", "*.keystore", "*.kdbx", "*.ovpn",
                ".netrc", "_netrc", ".npmrc", ".pypirc", ".git-credentials", ".htpasswd", ".envrc", ".dockercfg",
                "credentials", "credentials.json", "service-account*.json", "*.tfstate", "*.tfstate.*", "*.tfvars",
                "kubeconfig")
ALWAYS_SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "target", "__pycache__", ".ruff_cache",
                    ".pytest_cache", ".mypy_cache", "dist", "build", ".idea", ".vscode"} | SECRET_DIRS
ALWAYS_SKIP_GLOBS = (*SECRET_GLOBS, "*.pyc")
PRIVATE_KEY_HEAD = re.compile(rb"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----")

# 子进程只拿到这些环境变量；其余（各种 token、云凭据）不外传
ENV_ALLOW = {"PATH", "PATHEXT", "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC", "TEMP", "TMP", "TMPDIR", "HOME",
             "USERPROFILE", "HOMEDRIVE", "HOMEPATH", "APPDATA", "LOCALAPPDATA", "PROGRAMDATA", "PROGRAMFILES",
             "PROGRAMFILES(X86)", "COMMONPROGRAMFILES", "ALLUSERSPROFILE", "NUMBER_OF_PROCESSORS",
             "PROCESSOR_ARCHITECTURE", "OS", "USERNAME", "USER", "LOGNAME", "SHELL", "LANG", "TZ", "TERM",
             "CARGO_HOME", "RUSTUP_HOME", "SSL_CERT_FILE", "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE", "NODE_EXTRA_CA_CERTS"}
ENV_NETWORK = {"HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY"}
ENV_EXTRA: set[str] = set()  # profile 的 execution.env_passthrough，由 audit.py 填写
# 被审查仓库的 .git/config 不能借 git 执行命令
GIT_SAFE = ["-c", "core.fsmonitor=false", "-c", "core.hooksPath="]


def base_env(network: bool = False) -> dict:
    allow = ENV_ALLOW | {e.upper() for e in ENV_EXTRA} | (ENV_NETWORK if network else set())
    return {k: v for k, v in os.environ.items() if k.upper() in allow or k.upper().startswith("LC_")}


def run(cmd, cwd=None, timeout=900, env=None, input_text=None, network=False) -> tuple[int, str, str]:
    """运行命令，返回 (退出码, stdout, stderr)。工具缺失返回 127，超时返回 124。

    环境变量按白名单传递；只有 network=True 的调用（下载规则、查漏洞库）才带上代理设置。
    """
    full_env = base_env(network)
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


def timed(cmd, cwd=None, timeout=900, env=None, network=False) -> tuple[int, str, str, float]:
    t = time.perf_counter()
    rc, out, err = run(cmd, cwd=cwd, timeout=timeout, env=env, network=network)
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


def _secret_name(rel: str) -> bool:
    parts = rel.lower().split("/")
    return any(p in SECRET_DIRS for p in parts[:-1]) or any(fnmatch.fnmatch(parts[-1], g) for g in SECRET_GLOBS)


def _skipped(rel: str) -> bool:
    """按名字就该跳过（不区分大小写）：密钥、凭据、构建产物、虚拟环境。"""
    parts = rel.lower().split("/")
    if any(p in ALWAYS_SKIP_DIRS for p in parts[:-1]):
        return True
    return any(fnmatch.fnmatch(parts[-1], g) for g in ALWAYS_SKIP_GLOBS)


def _is_link(p) -> bool:
    """符号链接或 Windows junction。"""
    return os.path.islink(p) or getattr(os.path, "isjunction", lambda _p: False)(p)


def _unlink_link(p) -> bool:
    try:
        if os.path.islink(p) and not os.path.isdir(p):
            os.unlink(p)
        else:
            try:
                os.rmdir(p)  # 目录链接 / junction：只摘掉链接本身
            except OSError:
                os.unlink(p)
        return True
    except OSError:
        return False


def _inside(path, root_real: str) -> bool:
    real = os.path.normcase(os.path.realpath(path))
    root = os.path.normcase(root_real)
    try:
        return os.path.commonpath([real, root]) == root
    except ValueError:  # 不同盘符
        return False


def safe_file(root: Path, rel: str, _dirs: dict | None = None) -> bool:
    """rel 是 root 下的普通文件，路径上没有任何链接，真实位置也仍在 root 之内。"""
    parts = Path(rel).parts
    if not parts or Path(rel).is_absolute() or ".." in parts:
        return False
    p = Path(root)
    for part in parts[:-1]:
        p = p / part
        key = str(p)
        ok = _dirs.get(key) if _dirs is not None else None
        if ok is None:
            ok = not _is_link(p)
            if _dirs is not None:
                _dirs[key] = ok
        if not ok:
            return False
    p = p / parts[-1]
    return not _is_link(p) and os.path.isfile(p) and _inside(p, os.path.realpath(root))


def _has_private_key(p: Path) -> bool:
    try:
        with open(p, "rb") as fh:
            return bool(PRIVATE_KEY_HEAD.search(fh.read(4096)))
    except OSError:
        return True  # 读不了就不要


def list_project_files(target: Path, stats: dict | None = None) -> list[str]:
    """git 仓库：受版本控制 + 未忽略的新文件；否则遍历目录。再过滤密钥、链接和构建产物。

    stats（可选）会被填上被排除的数量：sensitive（敏感文件规则）、links（链接，或真实位置在项目之外）。
    """
    rels: list[str] = []
    rc, out, _ = run(["git", *GIT_SAFE, "-c", "core.quotepath=false", "ls-files", "-co", "--exclude-standard", "-z"],
                     cwd=target, timeout=120)
    if rc == 0 and out:
        rels = [r for r in out.split("\0") if r]
    else:
        for root, dirs, names in os.walk(target):
            for d in dirs:
                if _is_link(Path(root, d)):  # 目录链接不进去，但记一笔
                    rels.append(Path(root, d).relative_to(target).as_posix())
                elif d.lower() in SECRET_DIRS:  # 敏感目录不进去（不读里面的文件名），整个目录记一项
                    rels.append(Path(root, d).relative_to(target).as_posix() + "/*")
            dirs[:] = [d for d in dirs if d.lower() not in ALWAYS_SKIP_DIRS and not _is_link(Path(root, d))]
            for n in names:
                rels.append(Path(root, n).relative_to(target).as_posix())
    keep: list[str] = []
    dirs_seen: dict = {}
    n_sensitive = n_links = 0
    for r in sorted(set(rels)):
        if _skipped(r):
            n_sensitive += _secret_name(r)
        elif not safe_file(target, r, dirs_seen):
            parents = [target.joinpath(*Path(r).parts[:i + 1]) for i in range(len(Path(r).parts))]
            n_links += any(_is_link(p) for p in parents) or (os.path.isfile(target / r) and
                                                             not _inside(target / r, os.path.realpath(target)))
        elif _has_private_key(target / r):
            n_sensitive += 1
        else:
            keep.append(r)
    if stats is not None:
        stats.update(sensitive=int(n_sensitive), links=int(n_links))
    return keep


def _clear_dest(dest: Path, dest_real: str, rel: str) -> bool:
    """确认写入位置真的在副本目录内：路径上遇到链接就摘掉（可能是上次运行时项目代码留下的）。"""
    p = dest
    parts = Path(rel).parts
    for i, part in enumerate(parts):
        p = p / part
        if _is_link(p) and not _unlink_link(p):
            return False
        if i < len(parts) - 1 and os.path.lexists(p) and not os.path.isdir(p):
            return False
    return _inside(p.parent, dest_real)


def sync_copy(target: Path, dest: Path, rels: list[str]) -> None:
    """把项目文件同步到沙箱副本（保留修改时间，Rust 增量编译缓存才有效）。不复制链接，也不顺着链接写。"""
    dest.mkdir(parents=True, exist_ok=True)
    dest_real = os.path.realpath(dest)
    want = set(rels)
    for root, dirs, names in os.walk(dest):
        for d in [d for d in dirs if _is_link(Path(root, d))]:
            dirs.remove(d)
            _unlink_link(Path(root, d))
        dirs[:] = [d for d in dirs if d not in ("target", "__pycache__")]
        for n in names:
            rel = Path(root, n).relative_to(dest).as_posix()
            if rel not in want or _is_link(Path(root, n)):
                try:
                    os.remove(Path(root, n))
                except OSError:
                    pass
    dirs_seen: dict = {}
    for rel in rels:
        src, dst = target / rel, dest / rel
        if not safe_file(target, rel, dirs_seen) or not _clear_dest(dest, dest_real, rel):
            continue
        try:
            s = src.stat()
            if dst.exists():
                d = dst.stat()
                if d.st_size == s.st_size and int(d.st_mtime) == int(s.st_mtime) and \
                        dst.read_bytes() == src.read_bytes():
                    continue
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst, follow_symlinks=False)
        except OSError:
            continue


def safe_glob(target: Path, globs) -> list[str]:
    """profile 里的 glob（日志、数据库文件）：结果同样不能是链接、敏感文件或项目之外的路径。"""
    out: set[str] = set()
    for g in globs:
        for p in _glob.glob(str(target / g), recursive=True):
            try:
                rel = Path(p).relative_to(target).as_posix()
            except ValueError:
                continue
            if not _skipped(rel) and safe_file(target, rel):
                out.add(p)
    return sorted(out)


def write_raw(ctx, name: str, text: str, mode: str = "w") -> None:
    """工具原始输出一律经过脱敏再落盘。"""
    from .redact import redact
    with open(ctx.raw / name, mode, encoding="utf-8") as fh:
        fh.write(redact(text))


def redact_json_file(path: Path) -> None:
    """工具自己写进报告目录的 JSON（jscpd 带代码片段、Lighthouse 带页面内容）：逐个字符串脱敏后写回。"""
    import json

    from .redact import redact

    def walk(v):
        if isinstance(v, str):
            return redact(v)
        if isinstance(v, list):
            return [walk(x) for x in v]
        if isinstance(v, dict):
            return {k: walk(x) for k, x in v.items()}
        return v
    try:
        data = json.loads(read_text(path))
    except ValueError:
        path.write_text(redact(read_text(path)), encoding="utf-8")
        return
    path.write_text(json.dumps(walk(data), ensure_ascii=False), encoding="utf-8")


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
