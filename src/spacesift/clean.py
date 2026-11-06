"""安全清理。

这是整个工具唯一会删文件的模块，所以规则写死在代码里而不是配置里：

1. **只清规则表里声明的路径** —— 目标路径必须落在 ``CleanRule.paths`` 展开后的结果中，
   调用方传进来的任意路径一律拒绝。
2. **黑名单兜底** —— 盘符根、系统目录、用户目录本身、Program Files 等永远不会被清，
   即使有人写规则写错了。
3. **不跟随链接** —— junction / 符号链接只跳过，绝不递归进去，避免删到 ``C:\\Windows``
   的别名上。
4. **默认 dry-run** —— ``dry_run=True`` 时只统计不删除，CLI 必须显式 ``--yes`` 才真删。
"""

from __future__ import annotations

import os
import shutil
import stat
import sys
from dataclasses import dataclass, field
from typing import Iterable

from .rules import CleanRule, Finding, expand, rule_targets

_IS_WIN = sys.platform.startswith("win")
_FILE_ATTRIBUTE_REPARSE_POINT = 0x400

#: 路径规范化后**等于**这些值的一律拒绝清理（它们的子目录不在此列 ——
#: `C:/Windows/Temp` 是合法清理目标，而 `C:/Windows` 本身永远不行）。
_FORBIDDEN_EXACT = (
    "C:",
    "C:/",
    "C:/Windows",
    "C:/Users",
    "C:/Program Files",
    "C:/Program Files (x86)",
    "C:/ProgramData",
    "C:/ProgramData/Microsoft",
)

#: 连同整棵子树一起拒绝的目录。这些是真删了就起不来的地方。
_FORBIDDEN_TREES = (
    "C:/Windows/System32",
    "C:/Windows/SysWOW64",
    "C:/Windows/Boot",
    "C:/Windows/assembly",
    "C:/Windows/servicing",
    "C:/Windows/System32/config",
)


@dataclass
class CleanReport:
    """一次清理的结果。"""

    rule_id: str
    rule_name: str
    risk: str
    dry_run: bool
    freed_bytes: int = 0
    deleted_files: int = 0
    deleted_dirs: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)
    targets: list[str] = field(default_factory=list)


def _norm(path: str) -> str:
    """规范化路径用于比较：绝对路径 + 正斜杠 + 去尾斜杠。"""
    resolved = os.path.abspath(path).replace("\\", "/").rstrip("/")
    return resolved or "/"


def is_forbidden(path: str) -> bool:
    """这个路径本身是否被禁止清理。

    两条独立的防线：

    * 深度不足 2 的路径（盘符根、``/``）永远不行。
    * 与 :data:`_FORBIDDEN_EXACT` 相等，或落在 :data:`_FORBIDDEN_TREES` 之内。
    """
    normalized = _norm(path)
    lowered = normalized.lower()

    parts = [part for part in normalized.split("/") if part]
    if len(parts) < 2:
        return True

    for banned in _FORBIDDEN_EXACT:
        if lowered == _norm(banned).lower():
            return True

    for tree in _FORBIDDEN_TREES:
        tree_norm = _norm(tree).lower()
        if lowered == tree_norm or lowered.startswith(tree_norm + "/"):
            return True

    return False


def is_within(path: str, roots: Iterable[str]) -> bool:
    """``path`` 是否位于 ``roots`` 其中之一的内部（不含根本身）。"""
    normalized = _norm(path)
    for root in roots:
        root_norm = _norm(root)
        if normalized == root_norm:
            return True
        if normalized.lower().startswith(root_norm.lower() + "/"):
            return True
    return False


def _is_reparse(path: str) -> bool:
    try:
        st = os.lstat(path)
    except OSError:
        return True
    return bool(getattr(st, "st_file_attributes", 0) & _FILE_ATTRIBUTE_REPARSE_POINT)


def _force_remove_file(path: str) -> None:
    """删一个文件，遇到只读属性时先去掉再删。"""
    try:
        os.unlink(path)
    except PermissionError:
        os.chmod(path, stat.S_IWRITE)
        os.unlink(path)


def _delete_tree(path: str, report: CleanReport, dry_run: bool) -> None:
    """递归删除 ``path`` 目录（连同目录本身），不跟随 reparse point。

    先走一遍收集所有文件和目录，再按「文件 → 目录的逆序」删除。反过来做
    （边遍历边 rmdir）会因为子目录还在而删不掉父目录 —— 这是真实踩过的坑。
    """
    files: list[tuple[str, int]] = []
    dirs: list[str] = []
    stack = [path]

    while stack:
        current = stack.pop()
        dirs.append(current)
        try:
            iterator = os.scandir(current)
        except OSError as exc:
            report.errors.append(f"{current}: {exc.strerror or exc}")
            continue
        with iterator:
            while True:
                try:
                    entry = next(iterator)
                except StopIteration:
                    break
                except OSError as exc:
                    report.errors.append(f"{current}: {exc.strerror or exc}")
                    continue
                try:
                    if entry.is_dir(follow_symlinks=False):
                        if bool(getattr(entry.stat(follow_symlinks=False), "st_file_attributes", 0) & _FILE_ATTRIBUTE_REPARSE_POINT):
                            report.skipped += 1
                            continue
                        stack.append(entry.path)
                    elif entry.is_file(follow_symlinks=False):
                        files.append((entry.path, entry.stat(follow_symlinks=False).st_size))
                    else:
                        # 管道、设备节点等：不碰
                        report.skipped += 1
                except OSError as exc:
                    report.errors.append(f"{entry.path}: {exc.strerror or exc}")

    for file_path, size in files:
        if dry_run:
            report.freed_bytes += size
            report.deleted_files += 1
            continue
        try:
            _force_remove_file(file_path)
            report.freed_bytes += size
            report.deleted_files += 1
        except OSError as exc:
            report.errors.append(f"{file_path}: {exc.strerror or exc}")

    if dry_run:
        return

    # dirs 是前序（父在子前），逆序即后序：子目录先删，父目录才空得下来。
    for directory in reversed(dirs):
        try:
            os.rmdir(directory)
            report.deleted_dirs += 1
        except OSError:
            report.skipped += 1


def _clear_dir_contents(target: str, report: CleanReport, dry_run: bool) -> None:
    """清空目录内容，保留目录本身。"""
    try:
        entries = list(os.scandir(target))
    except OSError as exc:
        report.errors.append(f"{target}: {exc.strerror or exc}")
        return

    for entry in entries:
        try:
            if entry.is_dir(follow_symlinks=False):
                if bool(getattr(entry.stat(follow_symlinks=False), "st_file_attributes", 0) & _FILE_ATTRIBUTE_REPARSE_POINT):
                    report.skipped += 1
                    continue
                if dry_run:
                    _delete_tree(entry.path, report, dry_run=True)
                else:
                    _delete_tree(entry.path, report, dry_run=False)
            elif entry.is_file(follow_symlinks=False):
                size = entry.stat(follow_symlinks=False).st_size
                if not dry_run:
                    _force_remove_file(entry.path)
                report.freed_bytes += size
                report.deleted_files += 1
            else:
                report.skipped += 1
        except OSError as exc:
            report.errors.append(f"{entry.path}: {exc.strerror or exc}")


def clean_finding(finding: Finding, *, dry_run: bool = True) -> CleanReport:
    """按一条检测结果执行清理。

    只处理 ``Finding.paths`` 里、且确实由该规则展开出来的目标。
    """
    rule: CleanRule = finding.rule
    report = CleanReport(
        rule_id=rule.id,
        rule_name=rule.name,
        risk=rule.risk,
        dry_run=dry_run,
    )

    allowed = {_norm(p) for p in rule_targets(rule)}
    for raw_path in finding.paths:
        path = _norm(raw_path)
        if is_forbidden(path):
            report.errors.append(f"拒绝清理受保护路径: {raw_path}")
            continue
        if path not in allowed:
            report.errors.append(f"拒绝清理规则外路径: {raw_path}")
            continue
        report.targets.append(raw_path)

        if rule.kind == "dir" and os.path.isdir(raw_path):
            _clear_dir_contents(raw_path, report, dry_run)
        elif os.path.exists(raw_path):
            try:
                size = os.path.getsize(raw_path)
                if not dry_run:
                    _force_remove_file(raw_path)
                report.freed_bytes += size
                report.deleted_files += 1
            except OSError as exc:
                report.errors.append(f"{raw_path}: {exc.strerror or exc}")
        else:
            report.skipped += 1

    return report


def clean_findings(
    findings: Iterable[Finding],
    *,
    dry_run: bool = True,
    include_caution: bool = False,
    only: Iterable[str] | None = None,
) -> list[CleanReport]:
    """批量清理。默认只动 ``safe`` 规则。"""
    wanted = {rule_id.lower() for rule_id in only} if only else None
    reports: list[CleanReport] = []
    for finding in findings:
        rule = finding.rule
        if wanted is not None and rule.id.lower() not in wanted:
            continue
        if rule.risk == "manual":
            continue
        if rule.risk == "caution" and not include_caution:
            continue
        reports.append(clean_finding(finding, dry_run=dry_run))
    return reports
