"""原生窗口界面（pywebview + 系统 WebView2）。

不是浏览器标签页：``create_window`` 打开的是一个真正的窗口，Windows 上由 Edge
WebView2 控件渲染界面 —— 所以既能用 HTML 画出方块图，又不用背 Electron 那一百多兆。

体积账：pywebview + WebView2 绑定打包后约 14 MB，界面素材 40 KB；对比 Electron
起步 150 MB，而且不需要随包分发浏览器内核（系统自带）。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import threading
import time
import traceback
from dataclasses import dataclass, field
from typing import Any

import webview

from .analyze import Opportunity, build_opportunities, totals_by_category
from .clean import clean_findings
from .report import disk_info
from .rules import detect_all
from .scan import ScanResult, human_size, scan

_UI_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ui.html")

APP_NAME = "SpaceSift"


def ui_html() -> str:
    with open(_UI_PATH, encoding="utf-8") as handle:
        return handle.read()


def list_drives() -> list[dict[str, Any]]:
    """枚举可用的盘符，带占用和剩余空间。"""
    rows: list[dict[str, Any]] = []
    for letter in "CDEFGHIJKLMNOPQRSTUVWXYZ":
        root = f"{letter}:\\"
        if not os.path.exists(root):
            continue
        try:
            usage = shutil.disk_usage(root)
        except OSError:
            continue
        percent = (usage.used / usage.total * 100) if usage.total else 0
        rows.append({
            "path": root,
            "total": usage.total,
            "used": usage.used,
            "free": usage.free,
            "percent": round(percent, 1),
            "used_text": human_size(usage.used),
            "total_text": human_size(usage.total),
            "free_text": human_size(usage.free),
        })
    return rows


@dataclass
class GuiState:
    """界面状态。后端线程写、前端轮询读，全程加锁。"""

    root: str = ""
    depth: int = 3
    status: str = "idle"          # idle | scanning | paused | done | error
    visited: int = 0
    current: str = ""
    error: str = ""
    stopping: bool = False        # 用户点了结束，worker 正在收尾

    result: ScanResult | None = None
    findings: list = field(default_factory=list)
    opportunities: list[Opportunity] = field(default_factory=list)
    partial: dict[str, int] = field(default_factory=dict)

    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _thread: threading.Thread | None = field(default=None, repr=False)
    _paused: bool = field(default=False, repr=False)
    _stopped: bool = field(default=False, repr=False)

    # ---------------------------------------------------------------- 扫描

    def start_scan(self, path: str | None, depth: int | None = None) -> None:
        target = os.path.abspath(path) if path else (self.root or os.path.abspath(os.sep))
        if not os.path.isdir(target):
            with self._lock:
                self.status = "error"
                self.error = f"目录不存在：{target}"
            return

        with self._lock:
            if self.status in ("scanning", "paused"):
                return
            self.root = target
            if depth:
                self.depth = depth
            self.status = "scanning"
            self.visited = 0
            self.current = ""
            self.error = ""
            self.partial = {}
            self.result = None
            self.stopping = False
            self._paused = False
            self._stopped = False

        self._thread = threading.Thread(target=self._worker, name="spacesift-scan", daemon=True)
        self._thread.start()

    def _gate(self) -> bool:
        """扫描的节流阀：暂停时在这里原地等，返回 False 表示要收尾。"""
        while True:
            with self._lock:
                if self._stopped:
                    return False
                paused = self._paused
            if not paused:
                return True
            time.sleep(0.08)

    def pause_scan(self) -> dict[str, Any]:
        with self._lock:
            if self.status != "scanning":
                return {"ok": False, "status": self.status}
            self._paused = True
            self.status = "paused"
        return {"ok": True, "status": "paused"}

    def resume_scan(self) -> dict[str, Any]:
        with self._lock:
            if self.status != "paused":
                return {"ok": False, "status": self.status}
            self._paused = False
            self.status = "scanning"
        return {"ok": True, "status": "scanning"}

    def stop_scan(self) -> dict[str, Any]:
        """结束扫描：不是扔异常，而是让 scan 收尾返回已扫到的部分结果。"""
        with self._lock:
            if self.status not in ("scanning", "paused"):
                return {"ok": False, "status": self.status}
            self._stopped = True
            self._paused = False      # 暂停中也能立刻结束
            self.stopping = True
        return {"ok": True, "status": "stopping"}

    def _worker(self) -> None:
        try:
            def on_progress(visited: int, current: str) -> None:
                with self._lock:
                    self.visited = visited
                    self.current = current

            def on_partial(snapshot: dict[str, int]) -> None:
                with self._lock:
                    self.partial = snapshot

            result = scan(
                self.root,
                max_depth=self.depth,
                top_files=80,
                progress=on_progress,
                partial=on_partial,
                checkpoint=self._gate,
            )
            findings = detect_all()
            opportunities = build_opportunities(result, findings)
            with self._lock:
                self.result = result
                self.findings = findings
                self.opportunities = opportunities
                self.status = "done"
                self.stopping = False      # 收尾完成，别让「正在结束…」留在界面上
        except Exception as exc:  # noqa: BLE001 - 界面要显示原因
            with self._lock:
                self.status = "error"
                self.stopping = False
                self.error = f"{exc}\n{traceback.format_exc(limit=2)}"

    # ---------------------------------------------------------------- 快照

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            payload: dict[str, Any] = {
                "status": self.status,
                "stopping": self.stopping,
                "visited": self.visited,
                "current": self.current,
                "error": self.error,
                "root": self.root,
                "depth": self.depth,
                "partial": self.partial,
            }
            result = self.result
            if result is not None:
                try:
                    info = disk_info(self.root)
                    payload["disk"] = {
                        "total": info.total, "used": info.used, "free": info.free,
                        "percent": round(info.percent_used, 1),
                        "total_text": human_size(info.total),
                        "used_text": human_size(info.used),
                        "free_text": human_size(info.free),
                    }
                except OSError:
                    payload["disk"] = None

                payload["scan"] = {
                    "total": result.total_bytes,
                    "total_text": human_size(result.total_bytes),
                    "files": result.total_files,
                    "dirs": len(result.dirs),
                    "errors": result.errors,
                    "elapsed": round(result.elapsed, 1),
                    "interrupted": result.interrupted,
                    # 只带浅层的一份做兜底（浏览器版用它离线渲染）；
                    # 原生窗口下钻走 get_children，不需要整棵树。
                    "tree": {
                        path: size for path, size in result.dirs.items()
                        if path.count(os.sep) <= result.root.count(os.sep) + 3
                    },
                    "top_files": [
                        {"path": hit.path, "size": hit.size, "text": human_size(hit.size)}
                        for hit in result.top_files[:20]
                    ],
                }
                payload["opportunities"] = [
                    {
                        "id": row.id, "title": row.title, "category": row.category,
                        "category_label": row.category_label, "size": row.size,
                        "size_text": row.size_text, "reason": row.reason,
                        "action": row.action, "paths": row.paths[:12],
                    }
                    for row in self.opportunities
                ]
                payload["totals"] = {
                    key: {"bytes": value, "text": human_size(value)}
                    for key, value in totals_by_category(self.opportunities).items()
                }
            return payload

    # ---------------------------------------------------------------- 清理

    def clean(self, ids: list[str], dry_run: bool) -> dict[str, Any]:
        with self._lock:
            known = {row.id: row for row in self.opportunities}
            findings = {f.rule.id: f for f in self.findings}

        selected = [known[i] for i in ids if i in known]
        targets = [findings[o.id] for o in selected if o.id in findings]
        if not targets:
            return {"ok": False, "message": "没有可执行的项目", "reports": []}

        reports = clean_findings(targets, dry_run=dry_run, include_caution=True)
        total = sum(r.freed_bytes for r in reports)
        return {
            "ok": True,
            "dry_run": dry_run,
            "freed": total,
            "freed_text": human_size(total),
            "reports": [
                {
                    "id": r.rule_id, "name": r.rule_name, "freed": r.freed_bytes,
                    "freed_text": human_size(r.freed_bytes), "files": r.deleted_files,
                    "errors": r.errors[:5],
                }
                for r in reports
            ],
            "message": ("演练完成，没有删除任何文件" if dry_run else f"已释放 {human_size(total)}"),
        }


class Api:
    """暴露给前端的接口（``window.pywebview.api.xxx()``）。"""

    def __init__(self) -> None:
        self.state = GuiState()
        self._window: Any = None

    # -- 窗口 --------------------------------------------------------

    def attach(self, window: Any) -> None:
        self._window = window

    # -- 路径选择 ----------------------------------------------------

    def list_drives(self) -> list[dict[str, Any]]:
        return list_drives()

    def pick_folder(self) -> str:
        """弹原生「选择文件夹」对话框，取消时返回空串。"""
        if self._window is None:
            return ""
        try:
            picked = self._window.create_file_dialog(webview.FOLDER_DIALOG)
        except Exception:  # noqa: BLE001 - 对话框失败不该崩界面
            return ""
        if not picked:
            return ""
        return picked[0] if isinstance(picked, (list, tuple)) else str(picked)

    def reveal(self, path: str) -> bool:
        """在资源管理器里打开：目录直接进，文件则定位并选中它。"""
        try:
            target = os.path.abspath(path)
            if os.path.isdir(target):
                os.startfile(target)  # type: ignore[attr-defined]
                return True
            if os.path.isfile(target):
                # explorer 的 /select 必须和路径拼成一个参数：
                # ["explorer", "/select,", path] 会被拆成两个参数，explorer 只打开默认目录。
                subprocess.run(["explorer", f"/select,{target}"], check=False)
                return True
        except OSError:
            return False
        return False

    def open_scan(self, path: str) -> dict[str, Any]:
        """把某个目录设为新的扫描起点（右键菜单里的「从这里扫描」）。"""
        if os.path.isdir(path):
            self.state.start_scan(path)
            return {"ok": True}
        return {"ok": False}

    # -- 扫描 --------------------------------------------------------

    def start_scan(self, path: str = "", depth: int = 0) -> dict[str, Any]:
        self.state.start_scan(path or None, depth or None)
        return {"ok": True}

    def get_state(self) -> dict[str, Any]:
        return self.state.snapshot()

    def pause_scan(self) -> dict[str, Any]:
        return self.state.pause_scan()

    def resume_scan(self) -> dict[str, Any]:
        return self.state.resume_scan()

    def stop_scan(self) -> dict[str, Any]:
        return self.state.stop_scan()

    def get_children(self, path: str) -> dict[str, Any]:
        """返回某个目录的**直接**子目录（从已扫描结果里切片）。

        整棵树一次性传给前端在 depth 大时会撑爆 JSON（40 万条几十 MB），
        前端拿到的是不完整数据 —— 明明有子目录却退化成显示文件。
        所以只按需给一层。
        """
        with self.state._lock:
            result = self.state.result
        if result is None:
            return {"dirs": [], "files": []}

        base = os.path.abspath(path).rstrip("\\/")
        prefix = base + os.sep
        rows: list[dict[str, Any]] = []
        for entry, size in result.dirs.items():
            if not entry.startswith(prefix):
                continue
            rest = entry[len(prefix):]
            # rest 为空说明 entry 就是 path 自己（dirs 里存了根目录的聚合值），
            # 它不是子目录；放进去会变成一个没有名字、点了也进不去的方块。
            if not rest or os.sep in rest:
                continue
            rows.append({"path": entry, "name": rest, "size": size, "text": human_size(size)})
        rows.sort(key=lambda row: row["size"], reverse=True)

        if rows:
            # 有子目录也有散落文件：把文件聚成一块，否则方块图面积对不上
            # （根目录的 pagefile.sys、hiberfil.sys 这类大文件会凭空消失）。
            file_bytes, file_count = self._direct_files(path)
            if file_bytes:
                rows.append({
                    "path": "", "name": f"（{file_count} 个文件）", "size": file_bytes,
                    "text": human_size(file_bytes), "file_group": True,
                })
                rows.sort(key=lambda row: row["size"], reverse=True)
            return {"dirs": rows, "files": []}

        # 已经是叶子目录：把文件列出来，作为下钻的终点
        return {"dirs": [], "files": self.list_files(path)}

    def _direct_files(self, path: str) -> tuple[int, int]:
        """某个目录下**直接**放着的文件总量和个数（不递归）。"""
        total = count = 0
        try:
            with os.scandir(path) as iterator:
                for entry in iterator:
                    try:
                        if entry.is_file(follow_symlinks=False):
                            total += entry.stat(follow_symlinks=False).st_size
                            count += 1
                    except OSError:
                        continue
        except OSError:
            return 0, 0
        return total, count

    def list_files(self, path: str) -> list[dict[str, Any]]:
        """实时列一个目录下的文件 —— 下钻到叶子时用来展示「再往下就是文件了」。

        只做一个 ``os.scandir``，不递归，毫秒级；大的排在前面，最多 200 个。
        """
        rows: list[dict[str, Any]] = []
        try:
            with os.scandir(path) as iterator:
                for entry in iterator:
                    try:
                        if entry.is_file(follow_symlinks=False):
                            rows.append({"name": entry.name,
                                         "size": entry.stat(follow_symlinks=False).st_size,
                                         "text": human_size(entry.stat(follow_symlinks=False).st_size)})
                    except OSError:
                        continue
        except OSError:
            return []
        rows.sort(key=lambda row: row["size"], reverse=True)
        return rows[:200]

    def clean(self, ids: list[str], dry_run: bool = True) -> dict[str, Any]:
        return self.state.clean([str(i) for i in (ids or [])], bool(dry_run))


def default_root() -> str:
    """双击启动时的默认扫描位置：系统盘，而不是 exe 所在的盘。

    ``os.path.abspath(os.sep)`` 取的是「当前工作目录所在的盘」——双击 exe 时那就是
    exe 自己所在的盘（可能是个 U 盘），扫出来的结果毫无意义。
    """
    system_drive = os.environ.get("SystemDrive") or "C:"
    root = system_drive.rstrip("\\/") + "\\"
    return root if os.path.isdir(root) else os.path.abspath(os.sep)


def run(path: str = "", *, depth: int = 6, width: int = 1280, height: int = 820) -> None:
    """打开窗口，阻塞到窗口关闭。

    ``depth`` 决定能下钻几层。3 层只够看盘符根的两级，点两下就到头；
    6 层基本能钻到任何用户目录底部（再多也没有遍历代价，只是多留点条目）。
    """
    api = Api()
    api.state.depth = depth
    api.state.root = os.path.abspath(path) if path else default_root()

    window = webview.create_window(
        APP_NAME,
        html=ui_html(),
        js_api=api,
        width=width,
        height=height,
        min_size=(960, 620),
        background_color="#0d1017",
        text_select=False,
    )
    api.attach(window)

    # 只有命令行明确给了路径才自动开扫。双击 exe 打开时先让用户自己选：
    # 没人愿意一开程序就被迫等它把整个 C 盘走一遍。
    if path:
        api.state.start_scan(api.state.root)

    webview.start(debug=False)
