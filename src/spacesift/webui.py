"""本地 Web 界面。

为什么是「本地 Web」而不是 Electron / Tk：

* 零依赖 —— 只用标准库 ``http.server``，安装包几十 KB；
* 有真可视化 —— 浏览器里画方块图（treemap），比终端字符画「一目了然」；
* 不用打包原生 GUI 运行时 —— 那才是几百 MB 起步的东西。

安全模型：只监听 ``127.0.0.1``，启动时随机生成一次性 token，前端从 URL 拿 token
放进请求头；没有 token 的请求一律 403。这样别的网页没法跨站打到本地服务上删文件。
"""

from __future__ import annotations

import json
import os
import secrets
import threading
import time
import traceback
import webbrowser
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

from .analyze import Opportunity, build_opportunities, totals_by_category
from .clean import clean_findings
from .report import disk_info
from .rules import detect_all
from .scan import ScanResult, human_size, scan

_UI_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ui.html")


@dataclass
class App:
    """一次界面会话的全部状态。"""

    root: str = ""
    depth: int = 3
    token: str = field(default_factory=lambda: secrets.token_urlsafe(16))

    status: str = "idle"          # idle | scanning | done | error
    visited: int = 0
    current: str = ""
    error: str = ""

    result: ScanResult | None = None
    findings: list = field(default_factory=list)
    opportunities: list[Opportunity] = field(default_factory=list)

    _lock: threading.Lock = field(default_factory=threading.Lock)
    _scan_thread: threading.Thread | None = None
    _clean_log: list[dict] = field(default_factory=list)

    # ---------------------------------------------------------------- 扫描

    def start_scan(self, path: str | None = None, depth: int | None = None) -> None:
        if path:
            self.root = os.path.abspath(path)
        if depth:
            self.depth = depth
        if not self.root:
            self.root = os.path.abspath(os.sep)

        with self._lock:
            if self.status == "scanning":
                return
            self.status = "scanning"
            self.visited = 0
            self.current = ""
            self.error = ""

        self._scan_thread = threading.Thread(target=self._scan_worker, daemon=True)
        self._scan_thread.start()

    def _scan_worker(self) -> None:
        try:
            def progress(visited: int, current: str) -> None:
                with self._lock:
                    self.visited = visited
                    self.current = current

            result = scan(self.root, max_depth=self.depth, top_files=60, progress=progress)
            findings = detect_all()
            opportunities = build_opportunities(result, findings)
            with self._lock:
                self.result = result
                self.findings = findings
                self.opportunities = opportunities
                self.status = "done"
        except Exception as exc:  # noqa: BLE001 - 界面要显示失败原因而不是静默
            with self._lock:
                self.status = "error"
                self.error = f"{exc}\n{traceback.format_exc(limit=3)}"

    # ---------------------------------------------------------------- 状态

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            payload: dict[str, Any] = {
                "status": self.status,
                "visited": self.visited,
                "current": self.current,
                "error": self.error,
                "root": self.root,
                "depth": self.depth,
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
                    "tree": result.dirs,
                    "top_files": [
                        {"path": hit.path, "size": hit.size, "text": human_size(hit.size)}
                        for hit in result.top_files[:20]
                    ],
                }
                payload["opportunities"] = [
                    {
                        "id": row.id,
                        "title": row.title,
                        "category": row.category,
                        "category_label": row.category_label,
                        "size": row.size,
                        "size_text": row.size_text,
                        "reason": row.reason,
                        "action": row.action,
                        "paths": row.paths[:12],
                    }
                    for row in self.opportunities
                ]
                payload["totals"] = {
                    key: {"bytes": value, "text": human_size(value)}
                    for key, value in totals_by_category(self.opportunities).items()
                }
            return payload

    # ---------------------------------------------------------------- 清理

    def clean(self, ids: list[str], *, dry_run: bool) -> dict[str, Any]:
        """按 id 清理。id 必须在当前 opportunities 里，其他一律忽略。"""
        with self._lock:
            known = {row.id: row for row in self.opportunities}
            findings = {f.rule.id: f for f in self.findings}

        selected = [known[i] for i in ids if i in known]
        # 只有能对应到某个规则的机会才可执行（archive 类没有规则，永远不会被删）
        targets = [findings[o.id] for o in selected if o.id in findings]

        if not targets:
            return {"ok": False, "message": "没有可执行的项目（归档建议不会自动删除）",
                    "reports": []}

        reports = clean_findings(targets, dry_run=dry_run, include_caution=True)
        rows = [
            {
                "id": r.rule_id,
                "name": r.rule_name,
                "freed": r.freed_bytes,
                "freed_text": human_size(r.freed_bytes),
                "files": r.deleted_files,
                "errors": r.errors[:5],
            }
            for r in reports
        ]
        total = sum(r.freed_bytes for r in reports)
        with self._lock:
            self._clean_log.append({"at": time.time(), "dry_run": dry_run,
                                    "freed": total, "ids": [r.rule_id for r in reports]})

        return {
            "ok": True,
            "dry_run": dry_run,
            "freed": total,
            "freed_text": human_size(total),
            "reports": rows,
            "message": ("演练完成，没有删除任何文件" if dry_run
                        else f"已释放 {human_size(total)}"),
        }


class Handler(BaseHTTPRequestHandler):
    app: App
    server_version = "SpaceSift"

    # 关掉每个请求的 stderr 噪音
    def log_message(self, format: str, *args) -> None:  # noqa: A002, D102
        return

    # ---------------------------------------------------------------- 工具

    def _json(self, payload: Any, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self._write(body)

    def _html(self, text: str) -> None:
        body = text.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self._write(body)

    def _write(self, body: bytes) -> None:
        """写响应。浏览器刷新/关页面会让连接中途消失，那不是错误。"""
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            return

    def _authorized(self, query: dict[str, list[str]]) -> bool:
        token = self.headers.get("X-Token") or (query.get("t", [""])[0])
        return secrets.compare_digest(token or "", self.app.token)

    def _body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return {}

    # ---------------------------------------------------------------- 路由

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)

        if parsed.path in ("/", "/index.html"):
            try:
                with open(_UI_PATH, encoding="utf-8") as handle:
                    self._html(handle.read())
            except OSError:
                self._html("<h1>ui.html 缺失</h1><p>安装不完整。</p>")
            return

        if not self._authorized(query):
            self._json({"error": "forbidden"}, 403)
            return

        if parsed.path == "/api/state":
            self._json(self.app.snapshot())
            return

        if parsed.path == "/api/favicon.ico":
            self._json({}, 204)
            return

        self._json({"error": "not found"}, 404)

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        if not self._authorized(query):
            self._json({"error": "forbidden"}, 403)
            return

        body = self._body()

        if parsed.path == "/api/scan":
            self.app.start_scan(body.get("path"), body.get("depth"))
            self._json({"ok": True})
            return

        if parsed.path == "/api/clean":
            ids = body.get("ids") or []
            if not isinstance(ids, list):
                self._json({"ok": False, "message": "ids 必须是数组"}, 400)
                return
            dry_run = bool(body.get("dry_run", True))
            result = self.app.clean([str(i) for i in ids], dry_run=dry_run)
            self._json(result)
            return

        self._json({"error": "not found"}, 404)


def _bind(port: int) -> ThreadingHTTPServer:
    """绑定端口；``port=0`` 时由系统分配。"""
    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


def serve(root: str, *, port: int = 0, depth: int = 3,
          open_browser: bool = True, quiet: bool = False) -> None:
    """启动本地界面，阻塞到 Ctrl-C。"""
    app = App(root=os.path.abspath(root) if root else os.path.abspath(os.sep), depth=depth)
    Handler.app = app

    try:
        server = _bind(port)
    except OSError:
        server = _bind(0)

    actual_port = server.server_address[1]
    url = f"http://127.0.0.1:{actual_port}/?t={app.token}"

    app.start_scan()

    print()
    print("  SpaceSift 已启动")
    print(f"  界面：{url}")
    print("  按 Ctrl-C 退出（退出后服务立即停止，不会残留进程）")
    print()

    if open_browser:
        threading.Thread(target=lambda: (time.sleep(0.4), webbrowser.open(url)),
                         daemon=True).start()

    try:
        server.serve_forever(poll_interval=0.3)
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
        server.server_close()
        if not quiet:
            print("  已停止。")
