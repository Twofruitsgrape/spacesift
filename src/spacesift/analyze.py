"""把「扫描结果」和「规则检测结果」合成为可执行的建议清单。

分四类，按「能不能自动做」排序，而不是按大小：

* ``junk``    —— 点一下就能清，删了系统自己重建。
* ``review``  —— 能清但要看一眼（回收站、旧系统、pnpm 存储…）。
* ``manual``  —— 工具永远不动手，只给命令/操作路径。
* ``archive`` —— 不是垃圾，是「东西放错盘了」：大模型、下载包、虚拟机镜像。

最后一项往往是 C 盘真正的空间黑洞 —— 清垃圾只能回收几 GB，把 170 GB 的
仿真结果从 C 盘挪走才是真解。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Iterable

from .rules import CAUTION, MANUAL, SAFE, Finding
from .scan import ScanResult, human_size

CATEGORY_JUNK = "junk"
CATEGORY_REVIEW = "review"
CATEGORY_MANUAL = "manual"
CATEGORY_ARCHIVE = "archive"

_CATEGORY_LABEL = {
    CATEGORY_JUNK: "可直接清理",
    CATEGORY_REVIEW: "确认后清理",
    CATEGORY_MANUAL: "只能人工处理",
    CATEGORY_ARCHIVE: "建议归档到其它盘",
}

_CATEGORY_ORDER = [CATEGORY_JUNK, CATEGORY_REVIEW, CATEGORY_ARCHIVE, CATEGORY_MANUAL]

#: 超过这个大小的单个文件会被提出来作为「归档」建议。
DEFAULT_BIG_FILE = 1 << 30  # 1 GiB


@dataclass
class Opportunity:
    """一条可执行的空间回收建议。"""

    id: str
    title: str
    category: str
    size: int
    action: str
    reason: str = ""
    paths: list[str] = field(default_factory=list)
    command: str = ""

    @property
    def category_label(self) -> str:
        return _CATEGORY_LABEL.get(self.category, self.category)

    @property
    def size_text(self) -> str:
        return human_size(self.size)


def _from_finding(finding: Finding) -> Opportunity:
    rule = finding.rule
    category = {
        SAFE: CATEGORY_JUNK,
        CAUTION: CATEGORY_REVIEW,
        MANUAL: CATEGORY_MANUAL,
    }.get(rule.risk, CATEGORY_REVIEW)

    if rule.risk == SAFE:
        action = f"spacesift clean --only {rule.id} --yes"
    elif rule.risk == CAUTION:
        action = f"spacesift clean --only {rule.id} --include-caution --yes"
    else:
        action = rule.hint or "手动处理"

    return Opportunity(
        id=rule.id,
        title=rule.name,
        category=category,
        size=finding.size,
        action=action,
        reason=rule.why,
        paths=list(finding.paths),
        command=rule.hint,
    )


def _archive_opportunities(
    scan: ScanResult,
    *,
    min_size: int,
    limit: int,
    exclude_prefixes: tuple[str, ...] = (),
) -> list[Opportunity]:
    """按「所在目录」聚合大文件，生成归档建议。

    * 只取文件所在目录（一层），不往上塌缩 —— 否则 Downloads 里的大文件会
      被归到 ``C:\\Users\\ALICE`` 名下，用户看不出该挪哪个目录。
    * 盘符根目录下的文件（hiberfil.sys / pagefile.sys）不参与：那是系统文件，
      已经由 manual 规则单独处理。
    * ``exclude_prefixes`` 里已有的路径跳过，避免和 manual / caution 项重复计数。
    """
    groups: dict[str, list] = {}
    for hit in scan.top_files:
        if hit.size < min_size:
            continue
        directory = os.path.dirname(hit.path)
        if not directory or len([p for p in directory.replace("\\", "/").split("/") if p]) < 2:
            continue
        # AppData / 程序目录里的东西该「清」或「卸载」，搬走程序就坏了。
        # 按路径段判断而不是子串：``C:\MyAppdata备份`` 这种目录名不该被误杀。
        parts = [part.lower() for part in directory.replace("\\", "/").split("/")[1:] if part]
        if "appdata" in parts or "programdata" in parts:
            continue
        if "program files" in parts or "program files (x86)" in parts:
            continue
        if "windows" in parts:
            continue
        if any(part in {".local", ".config", ".cache"} for part in parts):
            continue
        if exclude_prefixes and os.path.normcase(directory).startswith(exclude_prefixes):
            continue
        groups.setdefault(directory, []).append(hit)

    rows: list[Opportunity] = []
    for directory, hits in groups.items():
        total = sum(h.size for h in hits)
        rows.append(
            Opportunity(
                id=f"archive:{directory}",
                title=f"大文件集中地：{directory}",
                category=CATEGORY_ARCHIVE,
                size=total,
                action="移动到 D:/E: 等数据盘，或归档压缩后删除",
                reason=f"{len(hits)} 个文件超过 {human_size(min_size)}，合计 {human_size(total)}",
                paths=[h.path for h in hits],
            )
        )
    rows.sort(key=lambda row: row.size, reverse=True)
    return rows[:limit]


def build_opportunities(
    scan: ScanResult,
    findings: Iterable[Finding],
    *,
    min_archive_size: int = DEFAULT_BIG_FILE,
    max_archive: int = 8,
) -> list[Opportunity]:
    """生成完整建议清单：垃圾 → 待确认 → 归档 → 人工。"""
    findings = list(findings)
    rows = [_from_finding(f) for f in findings]

    # 已经被「人工处理」规则覆盖的目录，不再出现在归档建议里，否则同一批
    # 字节会被算两次（下载文件夹 42 GB 既算 manual 又算 archive）。
    covered = []
    for finding in findings:
        if finding.rule.risk != CAUTION and finding.rule.risk != MANUAL:
            continue
        for path in finding.paths:
            prefix = os.path.normcase(os.path.abspath(path)).rstrip("\\/")
            covered.append(prefix + os.sep)
            covered.append(prefix)

    rows.extend(
        _archive_opportunities(
            scan,
            min_size=min_archive_size,
            limit=max_archive,
            exclude_prefixes=tuple(covered),
        )
    )
    rows = [row for row in rows if row.size > 0]
    rows.sort(key=lambda row: (_CATEGORY_ORDER.index(row.category), -row.size))
    return rows


def group_by_category(rows: Iterable[Opportunity]) -> dict[str, list[Opportunity]]:
    grouped: dict[str, list[Opportunity]] = {key: [] for key in _CATEGORY_ORDER}
    for row in rows:
        grouped.setdefault(row.category, []).append(row)
    return grouped


def totals_by_category(rows: Iterable[Opportunity]) -> dict[str, int]:
    totals: dict[str, int] = {}
    for row in rows:
        totals[row.category] = totals.get(row.category, 0) + row.size
    return totals


def plan_phases(rows: Iterable[Opportunity]) -> list[tuple[str, str, int]]:
    """给出可执行的阶段计划：``(阶段名, 说明, 预期回收字节)``。"""
    totals = totals_by_category(rows)
    phases: list[tuple[str, str, int]] = []
    if totals.get(CATEGORY_JUNK):
        phases.append(("第一步：清缓存与临时文件", "全程自动，删了系统自己重建", totals[CATEGORY_JUNK]))
    if totals.get(CATEGORY_REVIEW):
        phases.append(("第二步：确认后清", "看一眼清单再决定，收益不如第一步稳定", totals[CATEGORY_REVIEW]))
    if totals.get(CATEGORY_ARCHIVE):
        phases.append(("第三步：把大文件挪出 C 盘", "收益最大的一步，需要你自己选目标盘", totals[CATEGORY_ARCHIVE]))
    if totals.get(CATEGORY_MANUAL):
        phases.append(("第四步：系统级开关", "休眠文件、页面文件、微信缓存等，需要手动操作", totals[CATEGORY_MANUAL]))
    return phases
