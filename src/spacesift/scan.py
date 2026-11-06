"""目录占用扫描。

只用标准库：os.scandir 递归 + 后序聚合。目标是「快、稳、不炸内存」。

设计要点
--------
* 默认跳过 reparse point（junction / 符号链接），否则会重复计数甚至死循环。
* 权限错误、文件消失等一律计入 ``errors`` 继续扫，绝不因为一个目录中断整盘。
* 只在内存里保留深度 <= ``max_depth`` 的目录聚合结果，深层目录只在聚合时用后序累加上去。
* 大文件用固定大小的最小堆，内存是 O(top_files) 而不是 O(文件数)。

典型用法::

    result = scan("C:/", max_depth=3, top_files=50)
    for path, size in result.child_sizes("C:/"):
        print(path, human_size(size))
"""

from __future__ import annotations

import heapq
import os
import stat
import time
from dataclasses import dataclass, field
from typing import Callable, Iterable, Iterator

#: 默认跳过的目录名：系统还原区/回收站，扫它们既慢又没意义。
DEFAULT_SKIP_NAMES = frozenset({"$Recycle.Bin", "System Volume Information", "$RECYCLE.BIN"})

_FILE_ATTRIBUTE_REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


@dataclass(frozen=True)
class FileHit:
    """一个大文件记录。"""

    path: str
    size: int


@dataclass
class ScanResult:
    """一次扫描的完整结果。"""

    root: str
    total_bytes: int = 0
    total_files: int = 0
    total_dirs: int = 0
    #: 目录绝对路径 -> 该目录下所有内容的字节数（含子目录）
    dirs: dict[str, int] = field(default_factory=dict)
    #: 按大小降序的最大的若干个文件
    top_files: list[FileHit] = field(default_factory=list)
    #: 读不了的文件/目录个数
    errors: int = 0
    elapsed: float = 0.0

    def child_sizes(self, parent: str, *, limit: int | None = None) -> list[tuple[str, int]]:
        """返回 ``parent`` 的直接子目录大小，按大到小排序。"""
        parent = os.path.abspath(parent).rstrip(os.sep) or os.sep
        prefix_len = len(parent) + (0 if parent.endswith(os.sep) else 1)
        rows: list[tuple[str, int]] = []
        for path, size in self.dirs.items():
            if len(path) <= prefix_len or not path.startswith(parent):
                continue
            rest = path[prefix_len:]
            if os.sep in rest:
                continue
            rows.append((path, size))
        rows.sort(key=lambda item: item[1], reverse=True)
        return rows[:limit] if limit is not None else rows

    def size_of(self, path: str) -> int:
        """返回某目录的聚合大小（不在结果里就是 0）。"""
        return self.dirs.get(os.path.abspath(path), 0)


def _is_reparse(entry: os.DirEntry) -> bool:
    """是否为 junction / 符号链接 / 其他 reparse point。"""
    try:
        st = entry.stat(follow_symlinks=False)
    except OSError:
        return True
    return bool(getattr(st, "st_file_attributes", 0) & _FILE_ATTRIBUTE_REPARSE_POINT)


def _push_biggest(heap: list[tuple[int, int, str]], path: str, size: int, limit: int) -> None:
    """维护一个「最大 N 个文件」的最小堆。"""
    if limit <= 0:
        return
    if len(heap) < limit:
        heapq.heappush(heap, (size, len(heap), path))
    elif size > heap[0][0]:
        heapq.heapreplace(heap, (size, heap[0][1], path))


def scan(
    root: str,
    *,
    max_depth: int = 3,
    top_files: int = 50,
    skip_names: Iterable[str] = DEFAULT_SKIP_NAMES,
    skip_paths: Iterable[str] = (),
    progress: Callable[[int, str], None] | None = None,
    progress_every: int = 2000,
) -> ScanResult:
    """扫描 ``root`` 并返回聚合结果。

    Parameters
    ----------
    root:
        扫描起点，通常是盘符根 ``C:/`` 或某个目录。
    max_depth:
        保留聚合结果的目录深度（0 = 只保留根）。遍历本身不受限制。
    top_files:
        记录最大的多少个文件，0 表示不记录。
    skip_names:
        按目录名跳过，默认跳过回收站和系统还原区。
    skip_paths:
        按绝对路径前缀跳过。
    progress:
        回调 ``(已访问目录数, 当前目录)``，用于 CLI 进度显示。
    """
    root = os.path.abspath(root)
    started = time.perf_counter()
    skip_names = frozenset(skip_names)
    skip_prefixes = tuple(os.path.abspath(p).rstrip(os.sep) for p in skip_paths if p)

    # 目录 -> 直接文件字节数；稍后按深度逆序累加到父目录。
    direct: dict[str, int] = {root: 0}
    parent: dict[str, str | None] = {root: None}
    depth: dict[str, int] = {root: 0}
    biggest: list[tuple[int, int, str]] = []

    total_files = 0
    errors = 0
    visited = 0
    stack: list[str] = [root]

    while stack:
        current = stack.pop()
        visited += 1
        if progress is not None and visited % progress_every == 0:
            progress(visited, current)

        try:
            iterator = os.scandir(current)
        except OSError:
            errors += 1
            continue

        with iterator:
            while True:
                try:
                    entry = next(iterator)
                except StopIteration:
                    break
                except OSError:
                    errors += 1
                    continue

                try:
                    if entry.is_dir(follow_symlinks=False):
                        if entry.name in skip_names:
                            continue
                        if _is_reparse(entry):
                            continue
                        path = entry.path
                        if skip_prefixes and path.startswith(skip_prefixes):
                            continue
                        parent[path] = current
                        depth[path] = depth[current] + 1
                        direct[path] = 0
                        stack.append(path)
                    elif entry.is_file(follow_symlinks=False):
                        size = entry.stat(follow_symlinks=False).st_size
                        direct[current] = direct.get(current, 0) + size
                        total_files += 1
                        _push_biggest(biggest, entry.path, size, top_files)
                except OSError:
                    errors += 1

    # 后序累加：按深度从深到浅，把每个目录的完整大小加到父目录。
    sizes = dict(direct)
    for path in sorted(sizes, key=lambda p: depth[p], reverse=True):
        par = parent.get(path)
        if par is not None and path in sizes:
            sizes[par] = sizes.get(par, 0) + sizes[path]

    dirs = {path: size for path, size in sizes.items() if depth[path] <= max_depth}
    top = [
        FileHit(path=path, size=size)
        for size, _order, path in sorted(biggest, key=lambda item: item[0], reverse=True)
    ]

    return ScanResult(
        root=root,
        total_bytes=sizes.get(root, 0),
        total_files=total_files,
        total_dirs=len(sizes),
        dirs=dirs,
        top_files=top,
        errors=errors,
        elapsed=time.perf_counter() - started,
    )


def dir_size(path: str, *, skip_names: Iterable[str] = DEFAULT_SKIP_NAMES) -> int:
    """只算一个目录的总字节数（比 :func:`scan` 轻，用于核查单个清理项）。"""
    total = 0
    skip = frozenset(skip_names)
    stack = [os.path.abspath(path)]
    while stack:
        current = stack.pop()
        try:
            iterator = os.scandir(current)
        except OSError:
            continue
        with iterator:
            while True:
                try:
                    entry = next(iterator)
                except StopIteration:
                    break
                except OSError:
                    continue
                try:
                    if entry.is_dir(follow_symlinks=False):
                        if entry.name in skip or _is_reparse(entry):
                            continue
                        stack.append(entry.path)
                    elif entry.is_file(follow_symlinks=False):
                        total += entry.stat(follow_symlinks=False).st_size
                except OSError:
                    continue
    return total


def human_size(num: float) -> str:
    """人类可读的体积，1024 进制，保留一位小数。"""
    negative = num < 0
    value = float(abs(num))
    for unit in ("B", "KB", "MB", "GB", "TB", "PB"):
        if value < 1024 or unit == "PB":
            text = f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
            return ("-" if negative else "") + text
        value /= 1024
    return f"{value:.1f} PB"
