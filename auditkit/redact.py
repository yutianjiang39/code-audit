"""脱敏：报告和原始输出落盘前，把看起来像密钥 / 口令的内容替换掉。

只做替换，不做判断：宁可多遮一点（遮掉的是值，位置和规则号仍然保留）。
"""
from __future__ import annotations

import re

MASK = "[已脱敏]"

_PEM = re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----[\s\S]*?(?:-----END [A-Z0-9 ]*PRIVATE KEY-----|\Z)")
# 词首：普通的词边界，或者紧跟在 JSON / 日志里没还原的转义换行（字面的 \n、\r、\t）后面
_B = r"(?:\b|(?<=\\[nrt]))"
_TOKENS = re.compile(
    _B + r"(?:"
    r"(?:AKIA|ASIA)[0-9A-Z]{16}\b"                                # AWS access key id
    r"|gh[pousr]_[A-Za-z0-9]{30,}\b|github_pat_[A-Za-z0-9_]{30,}\b"  # GitHub
    r"|xox[abposr]-[A-Za-z0-9-]{10,}\b"                           # Slack
    r"|sk-[A-Za-z0-9_-]{20,}\b"                                   # sk- 开头的 API key
    r"|AIza[0-9A-Za-z_-]{35}\b"                                   # Google API key
    r"|eyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"  # JWT
    r"|\d{8,10}:[A-Za-z0-9_-]{35}\b)")                            # Telegram bot token
_URL_CRED = re.compile(r"\b([a-z][a-z0-9+.-]*://[^\s/:@\"']+):([^\s/@\"']+)@", re.I)
_KEY = r"(?:pass(?:word|wd)?|secret|token|api_?key|private_?key|access_?key|auth)"
# 名字里带 password / token … 的赋值：引号里的字面量，或一段看起来像随机串的裸值
_ASSIGN = re.compile(
    r"(?i)(\b\w*" + _KEY + r"\w*\b\\?[\"']?\s*[=:]\s*)"
    r"(?:(\\?[\"'])([^\"'\\\s]{6,})(\\?[\"'])|([A-Za-z0-9_\-+/=.]{8,}))")


def _assign(m: re.Match) -> str:
    if m.group(3) is not None:
        return f"{m.group(1)}{m.group(2)}{MASK}{m.group(4)}"
    v = m.group(5)
    looks_random = (len(v) >= 16 or m.group(1).lstrip()[:1].isupper()) and re.search(r"\d", v) and re.search(r"[A-Za-z]", v)
    return f"{m.group(1)}{MASK}" if looks_random and "(" not in v else m.group(0)


def redact(text: str) -> str:
    if not text:
        return text
    text = _PEM.sub(MASK, text)
    text = _TOKENS.sub(MASK, text)
    text = _URL_CRED.sub(lambda m: f"{m.group(1)}:{MASK}@", text)
    return _ASSIGN.sub(_assign, text)
