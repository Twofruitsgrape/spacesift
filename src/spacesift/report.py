"""报告渲染：Markdown 与单文件 HTML。

报告的目标读者是「想清 C 盘但不知道从哪下手的人」，所以顺序是：

1. 空间去哪了（让人认得出自己的东西）
2. 能清什么、清了会发生什么（分档，带命令）
3. 按阶段的执行计划（预期回收多少）
"""

from __future__ import annotations

import datetime as _dt
import html
import os
import shutil
from dataclasses import dataclass
from typing import Iterable, Sequence

from .analyze import (
    CATEGORY_ARCHIVE,
    CATEGORY_JUNK,
    CATEGORY_MANUAL,
    CATEGORY_REVIEW,
    Opportunity,
    group_by_category,
    plan_phases,
    totals_by_category,
)
from .scan import ScanResult, human_size


@dataclass
class DiskInfo:
    """一个卷的容量信息。"""

    path: str
    total: int
    used: int
    free: int

    @property
    def percent_used(self) -> float:
        return (self.used / self.total * 100) if self.total else 0.0


def disk_info(path: str) -> DiskInfo:
    """读取盘符/目录所在卷的容量。"""
    usage = shutil.disk_usage(path)
    return DiskInfo(path=path, total=usage.total, used=usage.used, free=usage.free)


def _bar(ratio: float, width: int = 20) -> str:
    filled = max(0, min(width, round(ratio * width)))
    return "█" * filled + "░" * (width - filled)


def _pct(part: int, whole: int) -> str:
    return f"{(part / whole * 100):.1f}%" if whole else "-"


def _md_escape(text: str) -> str:
    return text.replace("|", "\\|")


def _table(headers: Sequence[str], rows: Iterable[Sequence[str]]) -> list[str]:
    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join(["---"] * len(headers)) + "|"]
    for row in rows:
        lines.append("| " + " | ".join(row) + " |")
    return lines


def render_markdown(
    scan: ScanResult,
    opportunities: Sequence[Opportunity],
    *,
    disk: DiskInfo | None = None,
    generated_at: _dt.datetime | None = None,
    top_dirs: int = 12,
    title: str = "磁盘空间分析报告",
) -> str:
    """生成 Markdown 报告。"""
    now = generated_at or _dt.datetime.now()
    disk = disk or (_disk_for(scan.root))
    totals = totals_by_category(opportunities)
    grouped = group_by_category(opportunities)
    phases = plan_phases(opportunities)

    lines: list[str] = []
    add = lines.append

    add(f"# {title}")
    add("")
    add(f"- 生成时间：{now:%Y-%m-%d %H:%M}")
    add(f"- 扫描路径：`{scan.root}`")
    add(f"- 扫描规模：{human_size(scan.total_bytes)} / {scan.total_files:,} 个文件 / {scan.total_dirs:,} 个目录")
    add(f"- 扫描耗时：{scan.elapsed:.1f} 秒（{scan.errors} 个目录因权限或占用未能读取）")
    add("")

    # ---------------- 总体 ----------------
    add("## 一、总体情况")
    add("")
    if disk and disk.total:
        add(f"| 指标 | 数值 |")
        add("|---|---|")
        add(f"| 卷容量 | {human_size(disk.total)} |")
        add(f"| 已用 | {human_size(disk.used)} |")
        add(f"| 剩余 | {human_size(disk.free)} |")
        add(f"| 使用率 | {disk.percent_used:.1f}% |")
        add("")
        add(f"`[{_bar(disk.percent_used / 100, 30)}]` {disk.percent_used:.1f}%")
        add("")

    reclaimable = totals.get(CATEGORY_JUNK, 0) + totals.get(CATEGORY_REVIEW, 0)
    if reclaimable:
        after = disk.free + reclaimable if disk else 0
        add(f"> **只清垃圾**：可回收约 **{human_size(reclaimable)}**"
            + (f"，清理后剩余 {human_size(after)}（{_pct(after, disk.total)} 可用）" if disk else ""))
    if totals.get(CATEGORY_ARCHIVE):
        add(f">")
        add(f"> **把大文件挪出 C 盘**：另有 **{human_size(totals[CATEGORY_ARCHIVE])}** 是可归档数据，"
            "这部分才是大头，但需要你决定放到哪个盘。")
    add("")

    # ---------------- 目录排行 ----------------
    add("## 二、空间去哪了")
    add("")
    children = scan.child_sizes(scan.root, limit=top_dirs)
    if children:
        biggest = children[0][1] or 1
        rows = []
        for path, size in children:
            name = os.path.basename(path.rstrip("\\/")) or path
            rows.append([
                f"`{_md_escape(name)}`",
                human_size(size),
                _pct(size, scan.total_bytes),
                f"`{_bar(size / biggest, 12)}`",
            ])
        lines.extend(_table(["目录", "占用", "占已扫描", "相对大小"], rows))
        add("")

    # ---------------- 大文件 ----------------
    if scan.top_files:
        add("### 最大的文件")
        add("")
        rows = [
            [
                f"`{_md_escape(os.path.basename(hit.path))}`",
                human_size(hit.size),
                f"`{_md_escape(hit.path)}`",
            ]
            for hit in scan.top_files[:10]
        ]
        lines.extend(_table(["文件", "大小", "完整路径"], rows))
        add("")

    # ---------------- 机会 ----------------
    add("## 三、能清什么")
    add("")
    sections = [
        (CATEGORY_JUNK, "3.1 可直接清理", "删了系统或程序会自己重建，不丢数据。"),
        (CATEGORY_REVIEW, "3.2 确认后清理", "清了有副作用（要重新下载、丢失回滚点等），先看一眼。"),
        (CATEGORY_ARCHIVE, "3.3 建议归档到其它盘", "不是垃圾，是放错位置的大文件。"),
        (CATEGORY_MANUAL, "3.4 只能人工处理", "工具不会动，给出操作路径。"),
    ]
    for category, heading, note in sections:
        rows = grouped.get(category) or []
        if not rows:
            continue
        total = sum(r.size for r in rows)
        add(f"### {heading}")
        add("")
        add(f"{note} **合计 {human_size(total)}**")
        add("")
        table_rows = []
        for row in rows:
            action = f"`{_md_escape(row.action)}`" if row.command else row.action
            table_rows.append([
                _md_escape(row.title),
                human_size(row.size),
                _md_escape(row.reason),
                _md_escape(action) if row.command else action,
            ])
        lines.extend(_table(["项目", "大小", "为什么", "怎么做"], table_rows))
        add("")

    # ---------------- 计划 ----------------
    add("## 四、推荐执行顺序")
    add("")
    cumulative = 0
    for index, (name, note, size) in enumerate(phases, 1):
        cumulative += size
        add(f"{index}. **{name}** —— 约 {human_size(size)}。{note}")
        add(f"   - 累计可回收：{human_size(cumulative)}")
    add("")
    add("工具直接执行的命令：")
    add("")
    add("```bash")
    add("# 先看不动手（默认就是 dry-run）")
    add("spacesift clean")
    add("")
    add("# 确认后真清，只清安全项")
    add("spacesift clean --yes")
    add("")
    add("# 连「确认后清理」的一起清")
    add("spacesift clean --yes --include-caution")
    add("```")
    add("")

    # ---------------- 风险 ----------------
    add("## 五、注意")
    add("")
    add("- 清缓存后首次启动程序、首次访问网页会变慢，属于正常现象。")
    add("- `Windows 更新下载缓存`、`组件存储备份`、`系统还原点` 需要管理员权限；"
        "后者请用微软官方命令（DISM / vssadmin），不要手删。")
    add("- `休眠文件`、`页面文件` 不是垃圾，是系统功能占位；"
        "关掉休眠会同时失去「快速启动」。")
    add("- 归档建议里的文件属于个人数据，工具永远不会自动删除。")
    add("")
    add("---")
    add("")
    add("由 [SpaceSift](https://github.com/Twofruitsgrape/spacesift) 生成。")
    return "\n".join(lines)


def _disk_for(path: str) -> DiskInfo | None:
    try:
        return disk_info(path)
    except OSError:
        return None


_HTML_CSS = """
:root{--bg:#0f1115;--card:#171a21;--fg:#e6e8ee;--muted:#98a0b3;--line:#262b36;
--safe:#3fb950;--warn:#d29922;--danger:#f85149;--blue:#58a6ff;--accent:#7ee787}
*{box-sizing:border-box}
body{margin:0;padding:32px;background:var(--bg);color:var(--fg);
font:15px/1.65 -apple-system,"Segoe UI","Microsoft YaHei",sans-serif;max-width:1080px;margin:0 auto}
h1{font-size:26px;margin:0 0 4px}
h2{font-size:19px;margin:34px 0 12px;padding-bottom:6px;border-bottom:1px solid var(--line)}
h3{font-size:16px;margin:24px 0 8px;color:var(--accent)}
.meta{color:var(--muted);font-size:13px;margin-bottom:24px}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px;margin:16px 0}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px 16px}
.card .k{color:var(--muted);font-size:12px;letter-spacing:.02em}
.card .v{font-size:22px;font-weight:600;margin-top:4px}
.card .v.safe{color:var(--safe)} .card .v.warn{color:var(--warn)} .card .v.danger{color:var(--danger)}
table{width:100%;border-collapse:collapse;margin:10px 0 4px;font-size:13.5px}
th,td{text-align:left;padding:8px 10px;border-bottom:1px solid var(--line);vertical-align:top}
th{color:var(--muted);font-weight:600;font-size:12px;text-transform:uppercase;letter-spacing:.04em}
tr:hover td{background:#1b1f28}
code{background:#11141a;border:1px solid var(--line);border-radius:5px;padding:1px 5px;
font:12.5px/1.5 ui-monospace,Consolas,monospace;color:#c9d1d9;word-break:break-all}
.bar{height:8px;background:#11141a;border-radius:4px;overflow:hidden;margin-top:6px}
.bar>i{display:block;height:100%;background:linear-gradient(90deg,#2f81f7,var(--accent))}
.tag{display:inline-block;font-size:11px;padding:1px 7px;border-radius:999px;border:1px solid var(--line);color:var(--muted)}
.tag.safe{color:var(--safe);border-color:#1f4429} .tag.warn{color:var(--warn);border-color:#3d3117}
.tag.danger{color:var(--danger);border-color:#4a2222}
.note{background:#11141a;border-left:3px solid var(--blue);padding:10px 14px;border-radius:0 8px 8px 0;margin:14px 0;color:#c9d1d9}
.phases li{margin:8px 0}
footer{margin-top:36px;color:var(--muted);font-size:12px;border-top:1px solid var(--line);padding-top:14px}
"""


def render_html(
    scan: ScanResult,
    opportunities: Sequence[Opportunity],
    *,
    disk: DiskInfo | None = None,
    generated_at: _dt.datetime | None = None,
    top_dirs: int = 12,
    title: str = "磁盘空间分析报告",
) -> str:
    """生成单文件 HTML 报告（无外部依赖，可直接双击打开）。"""
    now = generated_at or _dt.datetime.now()
    disk = disk or _disk_for(scan.root)
    totals = totals_by_category(opportunities)
    grouped = group_by_category(opportunities)
    phases = plan_phases(opportunities)
    e = html.escape

    parts: list[str] = []
    parts.append(f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{e(title)}</title><style>{_HTML_CSS}</style></head><body>""")
    parts.append(f"<h1>{e(title)}</h1>")
    parts.append(
        f'<div class="meta">生成于 {now:%Y-%m-%d %H:%M} · 扫描 <code>{e(scan.root)}</code> · '
        f"{human_size(scan.total_bytes)} / {scan.total_files:,} 个文件 · "
        f"耗时 {scan.elapsed:.1f}s · {scan.errors} 个目录无权访问</div>"
    )

    cards = []
    if disk:
        cards.append(("卷容量", human_size(disk.total), ""))
        cards.append(("已用", human_size(disk.used), "danger" if disk.percent_used > 85 else ""))
        cards.append(("剩余", human_size(disk.free), "safe" if disk.percent_used < 80 else "warn"))
        cards.append(("使用率", f"{disk.percent_used:.1f}%", "danger" if disk.percent_used > 85 else ""))
    cards.append(("可清垃圾", human_size(totals.get(CATEGORY_JUNK, 0)), "safe"))
    cards.append(("确认后可清", human_size(totals.get(CATEGORY_REVIEW, 0)), "warn"))
    cards.append(("可归档数据", human_size(totals.get(CATEGORY_ARCHIVE, 0)), ""))
    parts.append('<div class="cards">')
    for key, value, cls in cards:
        parts.append(f'<div class="card"><div class="k">{e(key)}</div>'
                     f'<div class="v {cls}">{e(value)}</div></div>')
    parts.append("</div>")

    if disk:
        parts.append('<div class="bar"><i style="width:%.1f%%"></i></div>' % disk.percent_used)

    # 目录排行
    parts.append("<h2>空间去哪了</h2>")
    children = scan.child_sizes(scan.root, limit=top_dirs)
    if children:
        biggest = children[0][1] or 1
        parts.append("<table><tr><th>目录</th><th>占用</th><th>占比</th><th></th></tr>")
        for path, size in children:
            name = os.path.basename(path.rstrip("\\/")) or path
            parts.append(
                f'<tr><td><code>{e(name)}</code></td><td>{human_size(size)}</td>'
                f'<td>{_pct(size, scan.total_bytes)}</td>'
                f'<td style="width:180px"><div class="bar"><i style="width:{size / biggest * 100:.1f}%"></i></div></td></tr>'
            )
        parts.append("</table>")

    # 大文件
    if scan.top_files:
        parts.append("<h2>最大的文件</h2><table><tr><th>文件</th><th>大小</th><th>路径</th></tr>")
        for hit in scan.top_files[:10]:
            parts.append(
                f'<tr><td><code>{e(os.path.basename(hit.path))}</code></td>'
                f'<td>{human_size(hit.size)}</td><td><code>{e(hit.path)}</code></td></tr>'
            )
        parts.append("</table>")

    # 机会
    sections = [
        (CATEGORY_JUNK, "可直接清理", "safe", "删了系统或程序会自己重建，不丢数据。"),
        (CATEGORY_REVIEW, "确认后清理", "warn", "清了有副作用，先看一眼。"),
        (CATEGORY_ARCHIVE, "建议归档到其它盘", "", "不是垃圾，是放错位置的大文件。"),
        (CATEGORY_MANUAL, "只能人工处理", "danger", "工具不会动，给出操作路径。"),
    ]
    parts.append("<h2>能清什么</h2>")
    for category, heading, tag_cls, note in sections:
        rows = grouped.get(category) or []
        if not rows:
            continue
        total = sum(r.size for r in rows)
        parts.append(
            f'<h3>{e(heading)} <span class="tag {tag_cls}">{human_size(total)}</span></h3>'
            f'<div class="note">{e(note)}</div>'
        )
        parts.append("<table><tr><th>项目</th><th>大小</th><th>为什么</th><th>怎么做</th></tr>")
        for row in rows:
            action = f"<code>{e(row.action)}</code>" if row.command else e(row.action)
            parts.append(
                f"<tr><td>{e(row.title)}</td><td>{human_size(row.size)}</td>"
                f"<td>{e(row.reason)}</td><td>{action}</td></tr>"
            )
        parts.append("</table>")

    # 计划
    parts.append("<h2>推荐执行顺序</h2><ol class=\"phases\">")
    cumulative = 0
    for name, note, size in phases:
        cumulative += size
        parts.append(f"<li><b>{e(name)}</b> —— 约 {human_size(size)}。{e(note)}"
                     f"<br><small style='color:var(--muted)'>累计可回收 {human_size(cumulative)}</small></li>")
    parts.append("</ol>")
    parts.append(
        "<div class='note'><b>一条命令看全部</b><br>"
        "<code>spacesift clean</code> 只统计不删除；"
        "确认无误后加 <code>--yes</code> 才真正清理。</div>"
    )

    parts.append(
        "<footer>由 SpaceSift 生成 · 所有删除动作都经过路径白名单校验，"
        "归档建议中的个人文件永远不会被自动删除。</footer></body></html>"
    )
    return "".join(parts)


def write_report(
    path: str,
    scan: ScanResult,
    opportunities: Sequence[Opportunity],
    **kwargs,
) -> str:
    """按扩展名写出 Markdown 或 HTML 报告，返回实际写入的路径。"""
    text = render_html(scan, opportunities, **kwargs) if path.lower().endswith((".html", ".htm")) \
        else render_markdown(scan, opportunities, **kwargs)
    directory = os.path.dirname(os.path.abspath(path))
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)
    return path
