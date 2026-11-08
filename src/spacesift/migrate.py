"""把放错盘的大文件挪到别的盘：复制 → 校验 → 删源，绝不反过来。

安全约定（和 ``tools/migrate.py`` 一致，但这里是给界面调用的可中断版本）：

* 每个文件先复制成 ``.part``，校验大小 + SHA256 都对上，才改名成正式文件；
* **三项全过才删源**，任何一步失败源文件原样保留；
* 目标重名自动加 ``(2)`` ``(3)``，绝不覆盖已有文件；
* 目标已存在且内容一致的文件直接视为已完成，重复点不会搬两遍。
"""

from __future__ import annotations

import hashlib
import os
import shutil
from dataclasses import dataclass, field
from typing import Callable, Iterable

CHUNK = 8 << 20  # 8 MiB


def sha256_file(path: str) -> str:
    """流式 SHA256，几十 GB 的文件也不会把内存吃满。"""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while True:
            block = handle.read(CHUNK)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def human_size(num: float) -> str:
    value = float(num)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.0f} B" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} TB"


def unique_target(target_dir: str, name: str) -> str:
    """目标目录里的不冲突路径；重名时加 (2)(3)…，不覆盖任何已有文件。"""
    candidate = os.path.join(target_dir, name)
    if not os.path.exists(candidate):
        return candidate
    stem, ext = os.path.splitext(name)
    index = 2
    while True:
        candidate = os.path.join(target_dir, f"{stem} ({index}){ext}")
        if not os.path.exists(candidate):
            return candidate
        index += 1


def free_space(path: str) -> int:
    try:
        return shutil.disk_usage(path).free
    except OSError:
        return 0


@dataclass
class MoveItem:
    src: str
    dst: str
    size: int


@dataclass
class MoveReport:
    moved: list[MoveItem] = field(default_factory=list)
    failed: list[dict] = field(default_factory=list)
    stopped: bool = False
    skipped_bytes: int = 0

    @property
    def moved_bytes(self) -> int:
        return sum(item.size for item in self.moved)

    def summary(self) -> str:
        parts = [f"搬走 {len(self.moved)} 个文件 · {human_size(self.moved_bytes)}"]
        if self.failed:
            parts.append(f"失败 {len(self.failed)} 个（源文件都还在）")
        if self.stopped:
            parts.append("已中途停止")
        return " · ".join(parts)


def plan(paths: Iterable[str], target_dir: str) -> list[MoveItem]:
    """算出要搬哪些文件、各自搬到哪 —— 只读文件系统，不动任何东西。"""
    items: list[MoveItem] = []
    for src in paths:
        if not os.path.isfile(src):
            continue
        items.append(MoveItem(
            src=src,
            dst=unique_target(target_dir, os.path.basename(src)),
            size=os.path.getsize(src),
        ))
    return items


def _copy_verified(item: MoveItem,
                   on_chunk: Callable[[int], None] | None = None) -> None:
    """复制 + 双校验。成功返回时目标文件已就位，源文件仍然存在。"""
    os.makedirs(os.path.dirname(item.dst) or ".", exist_ok=True)

    if os.path.exists(item.dst):
        if (os.path.getsize(item.dst) == item.size
                and sha256_file(item.dst) == sha256_file(item.src)):
            return  # 之前就搬好了，这次只把源文件删掉
        raise FileExistsError(f"目标已存在且内容不同：{item.dst}")

    tmp = item.dst + ".part"
    try:
        # 分块复制而不是 shutil.copyfile：一个 200 GB 的文件要复制半小时，
        # 不报字节进度的话界面看起来就是卡死。
        copied = 0
        with open(item.src, "rb") as source, open(tmp, "wb") as sink:
            while True:
                block = source.read(CHUNK)
                if not block:
                    break
                sink.write(block)
                copied += len(block)
                if on_chunk is not None:
                    on_chunk(copied)
        if os.path.getsize(tmp) != item.size:
            raise OSError("复制后大小不一致")
        if sha256_file(tmp) != sha256_file(item.src):
            raise OSError("SHA256 校验不一致")
        os.replace(tmp, item.dst)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


def execute(
    items: list[MoveItem],
    *,
    on_progress: Callable[[int, int, MoveItem | None], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
    on_bytes: Callable[[int, int], None] | None = None,
) -> MoveReport:
    """逐个搬。校验通过才删源；失败只记录，源文件不动。"""
    report = MoveReport()
    total = len(items)
    base_bytes = 0
    for index, item in enumerate(items):
        if should_stop is not None and should_stop():
            report.stopped = True
            break
        if on_progress is not None:
            on_progress(index, total, item)
        chunk_cb = None
        if on_bytes is not None:
            def chunk_cb(copied_now, _base=base_bytes):
                on_bytes(_base + copied_now, copied_now)
        try:
            _copy_verified(item, on_chunk=chunk_cb)
            os.remove(item.src)
            report.moved.append(item)
            base_bytes += item.size
        except Exception as exc:  # noqa: BLE001 - 单个文件失败不该中断整批
            report.failed.append({"src": item.src, "error": str(exc), "size": item.size})
    if on_progress is not None:
        on_progress(len(items), total, None)
    return report
