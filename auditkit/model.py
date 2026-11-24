"""数据模型：问题（Finding）、单类结果（CategoryResult）、运行上下文（Context）。"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

SEVERITIES = ("critical", "high", "medium", "low")
SEV_CN = {"critical": "严重", "high": "高", "medium": "中", "low": "低"}
SEV_RANK = {s: i for i, s in enumerate(SEVERITIES)}

CATEGORIES = {
    "01": "Bug 审查",
    "02": "安全漏洞审查",
    "03": "性能审查",
    "04": "代码瘦身审查",
    "05": "架构审查",
    "06": "代码质量审查",
    "07": "测试覆盖率审查",
    "08": "数据库审查",
    "09": "依赖库审查",
    "10": "兼容性审查",
    "11": "UI/UX 审查",
    "12": "运行环境审查",
}


@dataclass
class Finding:
    category: str  # "01".."12"
    tool: str
    rule: str
    severity: str  # critical / high / medium / low
    file: str  # 相对被审查项目根目录，正斜杠
    line: int
    message: str  # 发现的问题
    cause: str = ""  # 问题产生的原因
    fix: str = ""  # 具体修复建议
    fix_code: str = ""  # 可以执行的优化代码（片段或 diff）
    autofix: bool = False  # 是否已包含在 fixes.patch 中
    url: str = ""

    def key(self) -> tuple:
        return (self.category, SEV_RANK.get(self.severity, 9), self.file, self.line, self.rule)


@dataclass
class CategoryResult:
    id: str
    applicable: bool = True
    tools_used: list[str] = field(default_factory=list)
    tools_skipped: list[tuple[str, str]] = field(default_factory=list)  # (工具, 原因)
    findings: list[Finding] = field(default_factory=list)
    metrics: dict = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    score: float | None = None  # 由 score.py 填写；检查项也可以直接给出

    @property
    def name(self) -> str:
        return CATEGORIES[self.id]

    def use(self, tool: str) -> None:
        if tool not in self.tools_used:
            self.tools_used.append(tool)

    def skip(self, tool: str, reason: str) -> None:
        self.tools_skipped.append((tool, reason))


@dataclass
class Context:
    cfg: dict
    kit_root: Path  # code-audit 目录
    target: Path  # 被审查项目（只读，不会被修改）
    base: Path  # 沙箱副本：所有工具在这里运行
    fixed: Path  # 应用自动修复后的沙箱副本
    out: Path  # 本次报告目录
    raw: Path  # 工具原始输出
    files: list[str]  # 纳入审查的文件（相对路径）
    results: dict[str, CategoryResult] = field(default_factory=dict)
    shared: dict = field(default_factory=dict)  # 跨检查项共享的工具输出
    log: list[str] = field(default_factory=list)

    def cat(self, cid: str) -> CategoryResult:
        if cid not in self.results:
            self.results[cid] = CategoryResult(cid)
        return self.results[cid]

    def by_ext(self, *exts: str) -> list[str]:
        return [f for f in self.files if f.lower().endswith(exts)]

    def say(self, msg: str) -> None:
        self.log.append(msg)
        print(msg, flush=True)
