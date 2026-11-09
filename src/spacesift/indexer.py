"""全盘文件名索引 —— 让「搜一个文件」不用等整个扫描跑完。

Everything 快是因为它直接读 NTFS 的 MFT；那需要管理员权限和一堆卷结构解析，
换台机器、换个文件系统就未必成立。这里走一条更朴素但到处能跑的路：

* 第一次用：后台遍历指定盘/目录，把 ``路径 / 文件名 / 大小`` 写进 SQLite；
* 之后：直接查库，几十万到几百万行也是毫秒级；
* 库放在用户 AppData 下，按盘符分文件，可以随时删掉重建。

遍历速度参考实测：C 盘约 320 万个文件，约 1 分钟。
"""

from __future__ import annotations

import os
import sqlite3
import time
from dataclasses import dataclass
from typing import Callable, Iterable

# 只跳过真正的垃圾区：回收站和系统还原点。里面的文件既搜不出意义，
# 又可能占几十 GB。
#
# 早期版本还跳过了 Windows / WinSxS / node_modules / .git —— 实测在 D 盘上
# 因此少索引了 36,003 个文件（186,967 -> 150,964）。用户要的是「搜得到所有文件」，
# 那个取舍站不住脚，已经放开。
SKIP_DIRS = {"$RECYCLE.BIN", "System Volume Information"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
    path  TEXT PRIMARY KEY,
    name  TEXT NOT NULL,
    lower TEXT NOT NULL,
    size  INTEGER NOT NULL,
    is_dir INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_files_lower ON files(lower);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""


def data_dir() -> str:
    """索引放用户 AppData，不塞进程序目录（可能装在只读位置）。"""
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    target = os.path.join(base, "SpaceSift")
    try:
        os.makedirs(target, exist_ok=True)
    except OSError:
        target = os.path.join(os.path.expanduser("~"), ".SpaceSift")
        os.makedirs(target, exist_ok=True)
    return target


def index_path(data_dir: str, root: str) -> str:
    """每个盘/目录一份库，避免互相覆盖。"""
    safe = "".join(ch if ch.isalnum() else "_" for ch in os.path.abspath(root))[:60]
    return os.path.join(data_dir, f"index_{safe}.db")


def read_meta(db_path: str) -> dict[str, str]:
    if not os.path.exists(db_path):
        return {}
    try:
        with sqlite3.connect(db_path) as conn:
            return {key: value for key, value in conn.execute("SELECT key, value FROM meta")}
    except sqlite3.Error:
        return {}


def is_fresh(db_path: str, root: str, max_age_hours: float = 12) -> bool:
    """库里记录的是同一个 root、同一套跳过规则、且不太旧，就当它还能用。"""
    meta = read_meta(db_path)
    if meta.get("root", "").lower() != os.path.normcase(os.path.abspath(root)).lower():
        return False
    expected = ",".join(sorted({name.lower() for name in SKIP_DIRS}))
    if meta.get("skip_sig", "") != expected:
        return False        # 跳过规则改过，旧库的内容不完整，重扫
    try:
        built = float(meta.get("built_at", "0"))
    except ValueError:
        return False
    return (time.time() - built) < max_age_hours * 3600


@dataclass
class IndexReport:
    files: int = 0
    dirs: int = 0
    bytes: int = 0
    elapsed: float = 0.0
    stopped: bool = False

    def summary(self) -> str:
        text = f"索引 {self.files:,} 个文件"
        if self.dirs:
            text += f" · {self.dirs:,} 个目录"
        text += f" · {self.elapsed:.0f}s"
        if self.stopped:
            text += "（已中断，只索引了一部分）"
        return text


def build(
    root: str,
    db_path: str,
    *,
    progress: Callable[[int, str], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
    progress_every: int = 3000,
    batch_size: int = 4000,
    skip_names: Iterable[str] = SKIP_DIRS,
) -> IndexReport:
    """遍历 ``root`` 把文件名写进 SQLite。可中断，中断后已写入的部分照样能搜。"""
    started = time.perf_counter()
    report = IndexReport()
    root = os.path.abspath(root)
    skip = frozenset(name.lower() for name in skip_names)

    os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
    tmp_path = db_path + ".building"
    for leftover in (tmp_path, tmp_path + "-journal", tmp_path + "-wal"):
        if os.path.exists(leftover):
            try:
                os.remove(leftover)
            except OSError:
                pass

    # 索引文件可能恰好落在被索引的目录里（比如索引整个 AppData），
    # 不排除的话会把自己写进去，越扫越大
    own_files = {
        os.path.normcase(os.path.abspath(db_path)),
        os.path.normcase(os.path.abspath(tmp_path)),
        os.path.normcase(os.path.abspath(tmp_path + "-journal")),
        os.path.normcase(os.path.abspath(tmp_path + "-wal")),
    }

    conn = sqlite3.connect(tmp_path)
    try:
        conn.executescript(SCHEMA)
        batch: list[tuple[str, str, str, int, int]] = []
        stack = [root]

        while stack:
            if should_stop is not None and should_stop():
                report.stopped = True
                break
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
                        is_dir = entry.is_dir(follow_symlinks=False)
                        if is_dir:
                            # 跳过系统/依赖目录：白扫一遍拖慢索引，还塞满噪音
                            if entry.name.lower() in skip:
                                continue
                            if getattr(entry.stat(follow_symlinks=False), "st_file_attributes", 0) & 0x400:
                                continue        # reparse point，跟进去会成环
                            size = 0
                            report.dirs += 1
                            stack.append(entry.path)
                        else:
                            if os.path.normcase(os.path.abspath(entry.path)) in own_files:
                                continue
                            size = entry.stat(follow_symlinks=False).st_size
                            report.files += 1
                            report.bytes += size
                        batch.append((entry.path, entry.name, entry.name.lower(), size, 1 if is_dir else 0))
                    except OSError:
                        continue

                    if len(batch) >= batch_size:
                        conn.executemany(
                            "INSERT OR REPLACE INTO files(path,name,lower,size,is_dir) VALUES(?,?,?,?,?)",
                            batch,
                        )
                        batch.clear()
                        if progress is not None and report.files % progress_every < batch_size:
                            progress(report.files, current)

        if batch:
            conn.executemany(
                "INSERT OR REPLACE INTO files(path,name,lower,size,is_dir) VALUES(?,?,?,?,?)",
                batch,
            )
        conn.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('root',?)", (root,))
        conn.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('built_at',?)", (str(time.time()),))
        conn.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('files',?)", (str(report.files),))
        conn.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('skip_sig',?)",
                     (",".join(sorted(skip)),))
        conn.commit()
    finally:
        conn.close()

    report.elapsed = time.perf_counter() - started
    if report.stopped:
        # 中断时也要把已有的部分换成正式库，否则这次白跑
        try:
            if os.path.exists(db_path):
                os.remove(db_path)
        except OSError:
            pass
    try:
        os.replace(tmp_path, db_path)
    except PermissionError as exc:
        # 另一个 SpaceSift 窗口正在读这个库，Windows 不允许替换
        raise RuntimeError(
            "索引库正被另一个 SpaceSift 窗口占用，先关掉它再重建"
        ) from exc
    return report


@dataclass
class Hit:
    path: str
    name: str
    size: int
    is_dir: bool


def search(db_path: str, query: str, *, limit: int = 300, ext: str = "") -> list[Hit]:
    """按文件名子串查。``ext`` 可以额外按扩展名过滤（``.xlsx``）。"""
    if not os.path.exists(db_path):
        return []
    text = (query or "").strip().lower()
    if not text and not ext:
        return []

    clauses: list[str] = []
    params: list[object] = []
    if text:
        clauses.append("lower LIKE ?")
        params.append(f"%{text}%")
    if ext:
        suffix = ext.lower() if ext.startswith(".") else "." + ext.lower()
        clauses.append("lower LIKE ?")
        params.append(f"%{suffix}")
    where = " AND ".join(clauses) if clauses else "1=1"
    params.append(limit)

    try:
        with sqlite3.connect(db_path) as conn:
            rows = conn.execute(
                f"SELECT path, name, size, is_dir FROM files WHERE {where} ORDER BY size DESC LIMIT ?",
                params,
            ).fetchall()
    except sqlite3.Error:
        return []
    return [Hit(path=r[0], name=r[1], size=r[2], is_dir=bool(r[3])) for r in rows]


def count(db_path: str) -> int:
    if not os.path.exists(db_path):
        return 0
    try:
        with sqlite3.connect(db_path) as conn:
            return int(conn.execute("SELECT COUNT(*) FROM files").fetchone()[0])
    except sqlite3.Error:
        return 0
