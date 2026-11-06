"""命令行入口。

    spacesift              扫一遍 + 检测 + 打印摘要（不动任何文件）
    spacesift scan C:/     只做占用扫描
    spacesift detect       只检测可清理项
    spacesift clean        清理（默认 dry-run，--yes 才真删）
    spacesift report       生成 Markdown / HTML 报告

任何会删文件的路径都要求显式 ``--yes``，且只清规则表里声明的目录。
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import sys
from typing import Sequence

from . import __version__
from .analyze import build_opportunities, totals_by_category
from .clean import clean_findings
from .report import disk_info, render_markdown, render_html, write_report
from .rules import RULES, detect_all, rules_by_id
from .scan import ScanResult, human_size, scan

_CATEGORY_COLOR = {"junk": "\033[32m", "review": "\033[33m", "archive": "\033[36m", "manual": "\033[31m"}
_RESET = "\033[0m"


def _supports_color() -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    return sys.stdout.isatty() and os.environ.get("TERM") != "dumb"


def _c(text: str, color: str) -> str:
    if not _supports_color() or not color:
        return text
    return f"{color}{text}{_RESET}"


def _configure_stdout() -> None:
    """Windows 控制台默认 GBK，中文/字符画会炸，这里强制 UTF-8。"""
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass


def _progress_printer():
    def report(count: int, current: str) -> None:
        if _supports_color():
            sys.stderr.write(f"\r  已访问 {count:,} 个目录 … {current[:60]:<60}")
            sys.stderr.flush()
    return report


def _scan_result_from_json(path: str) -> ScanResult:
    data = json.load(open(path, encoding="utf-8"))
    from .scan import FileHit

    return ScanResult(
        root=data["root"],
        total_bytes=data["total_bytes"],
        total_files=data["total_files"],
        total_dirs=data["total_dirs"],
        dirs=data["dirs"],
        top_files=[FileHit(path=f["path"], size=f["size"]) for f in data.get("top_files", [])],
        errors=data.get("errors", 0),
        elapsed=data.get("elapsed", 0.0),
    )


def _print_summary(result: ScanResult, opportunities) -> None:
    totals = totals_by_category(opportunities)
    print()
    try:
        info = disk_info(result.root)
        print(f"  {info.path}  {human_size(info.total)} 总容量 · "
              f"{human_size(info.used)} 已用 · {human_size(info.free)} 可用 "
              f"({info.percent_used:.1f}%)")
    except OSError:
        pass
    print(f"  扫描到 {human_size(result.total_bytes)} / {result.total_files:,} 个文件 "
          f"（{result.elapsed:.1f}s，{result.errors} 个目录无权访问）")
    print()
    print("  空间去向：")
    for path, size in result.child_sizes(result.root, limit=8):
        name = os.path.basename(path.rstrip("\\/")) or path
        print(f"    {human_size(size):>10}  {name}")
    print()
    labels = [("junk", "可直接清理"), ("review", "确认后清理"),
              ("archive", "可归档"), ("manual", "需人工")]
    for key, label in labels:
        size = totals.get(key, 0)
        if not size:
            continue
        print(f"    {_c(human_size(size).rjust(10), _CATEGORY_COLOR[key])}  {label}")
    print()


def cmd_scan(args: argparse.Namespace) -> int:
    result = scan(
        args.path,
        max_depth=args.depth,
        top_files=args.top,
        progress=_progress_printer() if not args.quiet else None,
    )
    if not args.quiet and _supports_color():
        sys.stderr.write("\r" + " " * 100 + "\r")
    if args.json:
        payload = {
            "root": result.root,
            "total_bytes": result.total_bytes,
            "total_files": result.total_files,
            "total_dirs": result.total_dirs,
            "errors": result.errors,
            "elapsed": result.elapsed,
            "dirs": result.dirs,
            "top_files": [{"path": hit.path, "size": hit.size} for hit in result.top_files],
        }
        with open(args.json, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False)
    _print_summary(result, [])
    return 0


def cmd_detect(args: argparse.Namespace) -> int:
    findings = detect_all()
    rules = rules_by_id()
    selected = findings
    if args.only:
        wanted = {item.strip().lower() for item in args.only.split(",") if item.strip()}
        unknown = wanted - set(rules)
        if unknown:
            print(f"未知的规则 id：{', '.join(sorted(unknown))}", file=sys.stderr)
            return 2
        selected = [f for f in findings if f.rule.id.lower() in wanted]

    total = sum(f.size for f in selected)
    print(f"{'大小':>10}  {'风险':<8} {'项目':<26} 为什么")
    for finding in selected:
        color = _CATEGORY_COLOR.get(
            {"safe": "junk", "caution": "review", "manual": "manual"}[finding.rule.risk], "")
        print(f"{_c(human_size(finding.size).rjust(10), color)}  {finding.rule.risk:<8} "
              f"{finding.rule.name:<26} {finding.rule.why}")
    print(f"\n合计可清理/待处理：{human_size(total)}")

    if args.json:
        payload = [
            {
                "id": f.rule.id,
                "name": f.rule.name,
                "risk": f.rule.risk,
                "size": f.size,
                "files": f.files,
                "paths": f.paths,
                "why": f.rule.why,
                "hint": f.rule.hint,
            }
            for f in selected
        ]
        with open(args.json, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=1)
    return 0


def cmd_clean(args: argparse.Namespace) -> int:
    findings = detect_all()
    reports = clean_findings(
        findings,
        dry_run=not args.yes,
        include_caution=args.include_caution,
        only=[item for item in args.only.split(",")] if args.only else None,
    )
    if not reports:
        print("没有匹配的可清理项。")
        return 0

    freed = sum(r.freed_bytes for r in reports)
    header = "演练（不会删除任何文件）" if not args.yes else "清理中"
    print(f"== {header} ==")
    for report in reports:
        flag = "OK " if not report.errors else "!! "
        print(f"{flag}{human_size(report.freed_bytes).rjust(10)}  {report.rule_name}"
              f"  ({report.deleted_files} 个文件)")
        for error in report.errors[:5]:
            print(f"      {error}")
        if len(report.errors) > 5:
            print(f"      … 另有 {len(report.errors) - 5} 条错误")

    if args.yes:
        print(f"\n共释放 {human_size(freed)}")
    else:
        print(f"\n预计可释放 {human_size(freed)}（加 --yes 才会真的删除）")

    if args.json:
        payload = [
            {
                "rule": r.rule_id,
                "name": r.rule_name,
                "dry_run": r.dry_run,
                "freed": r.freed_bytes,
                "files": r.deleted_files,
                "errors": r.errors,
            }
            for r in reports
        ]
        with open(args.json, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=1)
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    if args.scan_json:
        result = _scan_result_from_json(args.scan_json)
    else:
        result = scan(args.path, max_depth=args.depth, top_files=args.top,
                      progress=_progress_printer() if not args.quiet else None)
    findings = detect_all()
    opportunities = build_opportunities(result, findings)
    stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M")
    out = args.out or os.path.join("reports", f"disk-report-{stamp}.md")
    write_report(out, result, opportunities, top_dirs=args.top_dirs)
    print(f"报告已写入 {os.path.abspath(out)}")
    # 同时产出一份 HTML，方便直接看
    html_out = os.path.splitext(out)[0] + ".html"
    if getattr(args, "html", False) and not out.lower().endswith((".html", ".htm")):
        write_report(html_out, result, opportunities, top_dirs=args.top_dirs)
        print(f"HTML 版：{os.path.abspath(html_out)}")
    totals = totals_by_category(opportunities)
    print(f"可清垃圾 {human_size(totals.get('junk', 0))} · "
          f"待确认 {human_size(totals.get('review', 0))} · "
          f"可归档 {human_size(totals.get('archive', 0))}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="spacesift",
        description="磁盘一键清理 —— 先看清，再动手。默认只扫描不删除。",
    )
    parser.add_argument("--version", action="version", version=f"spacesift {__version__}")
    sub = parser.add_subparsers(dest="command")

    p_scan = sub.add_parser("scan", help="扫描目录占用")
    p_scan.add_argument("path", nargs="?", default=os.path.abspath(os.sep), help="扫描起点，默认当前盘根")
    p_scan.add_argument("--depth", type=int, default=3, help="保留聚合结果的目录深度")
    p_scan.add_argument("--top", type=int, default=50, help="记录最大的多少个文件")
    p_scan.add_argument("--json", help="把结果写入 JSON")
    p_scan.add_argument("--quiet", action="store_true")
    p_scan.set_defaults(func=cmd_scan)

    p_detect = sub.add_parser("detect", help="检测可清理项")
    p_detect.add_argument("--only", help="只看这些规则 id（逗号分隔）")
    p_detect.add_argument("--json", help="把结果写入 JSON")
    p_detect.set_defaults(func=cmd_detect)

    p_clean = sub.add_parser("clean", help="清理（默认演练）")
    p_clean.add_argument("--yes", action="store_true", help="真的删除，不加就是 dry-run")
    p_clean.add_argument("--include-caution", action="store_true", help="连 caution 级一起清")
    p_clean.add_argument("--only", help="只清这些规则 id（逗号分隔）")
    p_clean.add_argument("--json", help="把结果写入 JSON")
    p_clean.set_defaults(func=cmd_clean)

    p_report = sub.add_parser("report", help="生成 Markdown / HTML 报告")
    p_report.add_argument("path", nargs="?", default=os.path.abspath(os.sep))
    p_report.add_argument("--out", help="输出文件，.md 或 .html")
    p_report.add_argument("--html", action="store_true", help="顺带写一份 HTML")
    p_report.add_argument("--scan-json", help="复用已有扫描结果，跳过重新扫描")
    p_report.add_argument("--depth", type=int, default=3)
    p_report.add_argument("--top", type=int, default=50)
    p_report.add_argument("--top-dirs", type=int, default=12)
    p_report.add_argument("--quiet", action="store_true")
    p_report.set_defaults(func=cmd_report)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    _configure_stdout()
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)

    if not getattr(args, "command", None):
        # 不带子命令：扫描当前盘 + 检测 + 打印摘要，绝不删除
        args.path = os.path.abspath(os.sep)
        args.depth, args.top, args.json, args.quiet = 3, 30, None, False
        result = scan(args.path, max_depth=args.depth, top_files=args.top)
        findings = detect_all()
        opportunities = build_opportunities(result, findings)
        _print_summary(result, opportunities)
        print("下一步：")
        print("  spacesift clean              先演练（不删任何东西）")
        print("  spacesift clean --yes        真的清安全项")
        print("  spacesift report --html      生成完整报告")
        return 0

    return int(args.func(args) or 0)
