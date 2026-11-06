"""大文件归档：搬走不是垃圾但放错盘的东西。

`spacesift` 只负责看清和清垃圾，这个脚本处理另一半问题：170 GB 的仿真结果
躺在 C 盘桌面上。它做的事很朴素 —— 复制、校验、再删源，绝不反过来。

    # 先看会搬哪些文件（不动任何东西）
    python tools/migrate.py --src "C:/Users/me/Desktop/models" --dst "D:/models"

    # 真搬
    python tools/migrate.py --src "C:/Users/me/Desktop/models" --dst "D:/models" \
        --min-size 1G --execute

    # 只搬指定的几个文件
    python tools/migrate.py --file a.mph --file b.mph --dst "D:/models" --execute

安全约定
--------
* 默认 dry-run，``--execute`` 才动手。
* 每个文件先复制，再比大小，再比 SHA256；**三项全过才删源**。
* 校验失败只报告，源文件原样保留。
* 目标已存在且校验通过的文件直接跳过，重复跑是安全的。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from typing import Iterable

CHUNK = 8 << 20  # 8 MiB


def human(num: float) -> str:
    value = float(num)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{value:.0f} B"
        value /= 1024
    return f"{value:.1f} TB"


def _parse_size(text: str) -> int:
    """``1G`` / ``500M`` / ``123456`` → 字节。"""
    text = text.strip().upper()
    units = {"": 1, "B": 1, "K": 1024, "KB": 1024, "M": 1024**2, "MB": 1024**2,
             "G": 1024**3, "GB": 1024**3, "T": 1024**4, "TB": 1024**4}
    for suffix in ("TB", "GB", "MB", "KB", "T", "G", "M", "K", "B"):
        if text.endswith(suffix):
            return int(float(text[: -len(suffix)]) * units[suffix])
    return int(text)


def sha256_file(path: str, *, quiet: bool = True) -> str:
    """流式计算 SHA256，大文件也不会吃内存。"""
    digest = hashlib.sha256()
    total = os.path.getsize(path)
    done = 0
    started = time.time()
    with open(path, "rb") as handle:
        while True:
            block = handle.read(CHUNK)
            if not block:
                break
            digest.update(block)
            done += len(block)
            if not quiet and total:
                pct = done / total * 100
                speed = done / max(time.time() - started, 0.001) / 1024**2
                sys.stderr.write(f"\r    校验 {pct:5.1f}%  {human(done)}/{human(total)}  {speed:.0f} MB/s   ")
                sys.stderr.flush()
    if not quiet:
        sys.stderr.write("\r" + " " * 70 + "\r")
    return digest.hexdigest()


def copy_file(src: str, dst: str) -> None:
    """用 robocopy 复制（``/J`` 无缓冲 IO，``/Z`` 支持断点续传）。"""
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    if os.name != "nt":
        import shutil

        shutil.copy2(src, dst)
        return
    result = subprocess.run(
        ["robocopy", os.path.dirname(src), os.path.dirname(dst), os.path.basename(src),
         "/J", "/Z", "/NP", "/NFL", "/NDL", "/NJH", "/NJS", "/R:1", "/W:5"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    # robocopy 的 0-7 都是「成功」，>=8 才是失败
    if result.returncode >= 8:
        raise RuntimeError(f"robocopy 失败（{result.returncode}）：{result.stdout.strip()[:300]}")


@dataclass
class Item:
    src: str
    dst: str
    size: int
    status: str = "pending"      # pending | skipped | copied | verified | deleted | failed
    sha256: str = ""
    note: str = ""


@dataclass
class Report:
    items: list[Item] = field(default_factory=list)
    started: float = field(default_factory=time.time)

    def add(self, item: Item) -> None:
        self.items.append(item)

    def summary(self) -> dict:
        counters: dict[str, int] = {}
        for item in self.items:
            counters[item.status] = counters.get(item.status, 0) + 1
        moved = sum(i.size for i in self.items if i.status in {"deleted", "verified"})
        return {
            "items": len(self.items),
            "by_status": counters,
            "bytes_handled": moved,
            "human": human(moved),
            "elapsed_s": round(time.time() - self.started, 1),
        }


def collect(src: str, dst: str, *, min_size: int, names: Iterable[str] | None) -> list[Item]:
    """展开要搬的文件清单。"""
    src = os.path.abspath(src)
    dst = os.path.abspath(dst)
    items: list[Item] = []

    if names:
        for name in names:
            path = name if os.path.isabs(name) else os.path.join(src, name)
            path = os.path.abspath(path)
            if not os.path.isfile(path):
                raise SystemExit(f"找不到文件：{path}")
            rel = os.path.relpath(path, src)
            items.append(Item(src=path, dst=os.path.join(dst, rel), size=os.path.getsize(path)))
        return items

    for root, _dirs, files in os.walk(src):
        for name in files:
            path = os.path.join(root, name)
            try:
                size = os.path.getsize(path)
            except OSError:
                continue
            if size < min_size:
                continue
            rel = os.path.relpath(path, src)
            items.append(Item(src=path, dst=os.path.join(dst, rel), size=size))
    items.sort(key=lambda item: item.size, reverse=True)
    return items


def _free_space(path: str) -> int:
    probe = path
    while probe and not os.path.exists(probe):
        probe = os.path.dirname(probe)
    return __import__("shutil").disk_usage(probe or os.path.abspath(os.sep)).free


def run(src: str, dst: str, *, min_size: int, names: Iterable[str] | None,
        execute: bool, delete_source: bool) -> Report:
    items = collect(src, dst, min_size=min_size, names=names)
    report = Report()

    if not items:
        print("没有符合条件的文件。")
        return report

    total = sum(item.size for item in items)
    print(f"待处理 {len(items)} 个文件，合计 {human(total)}")
    print(f"  源：{os.path.abspath(src)}")
    print(f"  目标：{os.path.abspath(dst)}")
    free = _free_space(os.path.abspath(dst))
    print(f"  目标剩余空间：{human(free)}" + ("  ⚠ 不够！" if free < total else "  ✓"))
    if free < total:
        raise SystemExit("目标盘空间不足，已中止（不会删任何东西）。")
    print()

    if not execute:
        for item in items:
            print(f"  {human(item.size):>10}  {item.src}")
            print(f"  {'':>10}  → {item.dst}")
        print("\n演练结束，没有复制或删除任何文件。加 --execute 才真执行。")
        report.items = items
        return report

    # 同一个文件系统内移动不需要复制
    same_volume = os.path.splitdrive(os.path.abspath(src))[0].lower() == \
        os.path.splitdrive(os.path.abspath(dst))[0].lower()

    for index, item in enumerate(items, 1):
        prefix = f"[{index}/{len(items)}] {os.path.basename(item.src)} ({human(item.size)})"
        print(prefix)

        # 1) 目标已存在 → 校验后跳过
        if os.path.exists(item.dst) and os.path.getsize(item.dst) == item.size:
            print("    目标已存在且大小一致，校验哈希 …")
            try:
                if sha256_file(item.src, quiet=False) == sha256_file(item.dst, quiet=False):
                    item.status = "skipped"
                    item.note = "目标已存在且 SHA256 一致"
                    print("    ✓ 跳过（已归档）")
                    report.add(item)
                    if delete_source:
                        _delete_source(item)
                    continue
            except OSError as exc:
                print(f"    ! 校验出错：{exc}")

        # 2) 复制
        try:
            if same_volume:
                os.replace(item.src, item.dst)
                item.status = "verified"
                print("    ✓ 同卷直接移动")
                report.add(item)
                continue
            print("    复制中 …")
            copy_file(item.src, item.dst)
        except Exception as exc:  # noqa: BLE001 - 单个文件失败不应中断整批
            item.status = "failed"
            item.note = str(exc)
            print(f"    ✗ 复制失败：{exc}")
            report.add(item)
            continue

        # 3) 校验：大小 → SHA256
        if os.path.getsize(item.dst) != item.size:
            item.status = "failed"
            item.note = "目标大小与源不一致"
            print("    ✗ 大小不一致")
            report.add(item)
            continue

        print("    校验 SHA256 …")
        try:
            source_hash = sha256_file(item.src, quiet=False)
            target_hash = sha256_file(item.dst, quiet=False)
        except OSError as exc:
            item.status = "failed"
            item.note = f"校验失败：{exc}"
            print(f"    ✗ {exc}")
            report.add(item)
            continue

        if source_hash != target_hash:
            item.status = "failed"
            item.note = "SHA256 不一致"
            print("    ✗ SHA256 不一致，源文件保留")
            report.add(item)
            continue

        item.sha256 = source_hash
        item.status = "verified"
        print(f"    ✓ 校验通过 {source_hash[:16]}…")
        report.add(item)

        # 4) 校验通过后才删源
        if delete_source:
            _delete_source(item)

    print()
    return report


def _delete_source(item: Item) -> None:
    try:
        os.remove(item.src)
        item.status = "deleted"
        item.note = "源文件已删除"
        print("    ✓ 已删除源文件")
    except OSError as exc:
        item.note = f"源文件删除失败：{exc}"
        print(f"    ! 源文件删除失败：{exc}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="migrate",
        description="把大文件从 C 盘搬到别的盘：复制 → SHA256 校验 → 删源。默认演练。",
    )
    parser.add_argument("--src", help="源目录")
    parser.add_argument("--dst", required=False, help="目标目录")
    parser.add_argument("--file", action="append", dest="files",
                        help="只处理指定文件（相对 --src 或绝对路径），可重复")
    parser.add_argument("--min-size", default="1G", help="最小文件大小，默认 1G")
    parser.add_argument("--execute", action="store_true", help="真的复制（不加就是演练）")
    parser.add_argument("--delete-source", action="store_true",
                        help="校验通过后删除源文件（不加就只复制）")
    parser.add_argument("--json", help="把结果写入 JSON")
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass

    args = build_parser().parse_args(argv)
    if not args.dst:
        raise SystemExit("必须指定 --dst")
    if args.files and not args.src:
        # 允许 --file 用绝对路径
        args.src = os.path.dirname(os.path.abspath(args.files[0]))

    report = run(
        args.src or ".",
        args.dst,
        min_size=_parse_size(args.min_size),
        names=args.files,
        execute=args.execute,
        delete_source=args.delete_source,
    )

    summary = report.summary()
    print("结果：", json.dumps(summary, ensure_ascii=False))
    failed = [i for i in report.items if i.status == "failed"]
    for item in failed:
        print(f"  ✗ {item.src}：{item.note}")

    if args.json:
        with open(args.json, "w", encoding="utf-8") as handle:
            json.dump(
                {"summary": summary,
                 "items": [{"src": i.src, "dst": i.dst, "size": i.size,
                            "status": i.status, "sha256": i.sha256, "note": i.note}
                           for i in report.items]},
                handle, ensure_ascii=False, indent=1,
            )

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
