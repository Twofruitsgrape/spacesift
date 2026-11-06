"""analyze + report 测试：分档、去重、报告要点。"""

from __future__ import annotations

import datetime as dt
import os

from spacesift.analyze import (
    CATEGORY_ARCHIVE,
    CATEGORY_JUNK,
    CATEGORY_MANUAL,
    CATEGORY_REVIEW,
    build_opportunities,
    group_by_category,
    plan_phases,
    totals_by_category,
)
from spacesift.report import DiskInfo, disk_info, render_html, render_markdown
from spacesift.rules import CAUTION, MANUAL, SAFE, CleanRule, Finding
from spacesift.scan import FileHit, ScanResult, human_size


def _scan(tmp_path, hits=None, dirs=None):
    return ScanResult(
        root=str(tmp_path),
        total_bytes=100 * 1024**3,
        total_files=1000,
        total_dirs=50,
        dirs=dirs or {str(tmp_path): 100 * 1024**3},
        top_files=hits or [],
        errors=0,
        elapsed=1.0,
    )


def test_categories_map_from_risk(tmp_path):
    findings = [
        Finding(rule=CleanRule(id="s", name="安全", paths=("/s",), risk=SAFE), size=10),
        Finding(rule=CleanRule(id="c", name="注意", paths=("/c",), risk=CAUTION), size=20),
        Finding(rule=CleanRule(id="m", name="人工", paths=("/m",), risk=MANUAL), size=30),
    ]
    rows = build_opportunities(_scan(tmp_path), findings)
    categories = {row.id: row.category for row in rows}
    assert categories == {"s": CATEGORY_JUNK, "c": CATEGORY_REVIEW, "m": CATEGORY_MANUAL}


def test_archive_skips_program_dirs(tmp_path):
    hits = [
        FileHit(path="C:/Data/Movies/big.mkv", size=3 * 1024**3),
        FileHit(path="C:/Program Files/App/big.dll", size=5 * 1024**3),
        FileHit(path="C:/Users/x/AppData/Local/App/cache.bin", size=9 * 1024**3),
        FileHit(path="C:/Windows/Installer/big.msi", size=2 * 1024**3),
    ]
    rows = build_opportunities(_scan(tmp_path, hits=hits), [])
    archive = [row for row in rows if row.category == CATEGORY_ARCHIVE]
    assert len(archive) == 1
    assert archive[0].size == 3 * 1024**3


def test_archive_merges_same_directory(tmp_path):
    hits = [
        FileHit(path="C:/Data/models/a.mph", size=2 * 1024**3),
        FileHit(path="C:/Data/models/b.mph", size=3 * 1024**3),
    ]
    rows = build_opportunities(_scan(tmp_path, hits=hits), [])
    archive = [row for row in rows if row.category == CATEGORY_ARCHIVE]
    assert len(archive) == 1
    assert archive[0].size == 5 * 1024**3
    assert len(archive[0].paths) == 2


def test_archive_skips_drive_root_files(tmp_path):
    """hiberfil.sys / pagefile.sys 在盘根，属于系统文件，不进归档建议。"""
    hits = [FileHit(path="C:/hiberfil.sys", size=12 * 1024**3)]
    rows = build_opportunities(_scan(tmp_path, hits=hits), [])
    assert not [row for row in rows if row.category == CATEGORY_ARCHIVE]


def test_archive_respects_covered_rule_paths(tmp_path):
    """Downloads 已经作为 manual 规则出现时，归档里不能再算一次。"""
    downloads = "C:/Users/me/Downloads"
    hits = [FileHit(path=downloads + "/big.zip", size=4 * 1024**3)]
    findings = [Finding(rule=CleanRule(id="d", name="下载", paths=(downloads,), risk=MANUAL),
                        size=4 * 1024**3, paths=[downloads])]
    rows = build_opportunities(_scan(tmp_path, hits=hits), findings)
    assert not [row for row in rows if row.category == CATEGORY_ARCHIVE]
    assert sums(rows)[CATEGORY_MANUAL] == 4 * 1024**3


def test_archive_threshold_respected(tmp_path):
    hits = [FileHit(path="C:/Data/x/small.bin", size=1024)]
    assert not [r for r in build_opportunities(_scan(tmp_path, hits=hits), [])
                if r.category == CATEGORY_ARCHIVE]


def test_group_and_totals():
    rows = build_opportunities(_scan_stub(), [])
    grouped = group_by_category(rows)
    totals = totals_by_category(rows)
    assert set(totals) <= set(grouped)
    for key, items in grouped.items():
        assert totals.get(key, 0) == sum(item.size for item in items)


def sums(rows):
    return totals_by_category(rows)


def _scan_stub():
    return ScanResult(root="C:/", total_bytes=10, total_files=1)


def test_plan_phases_ordered():
    rows = build_opportunities(
        _scan_stub(),
        [
            Finding(rule=CleanRule(id="s", name="安全", paths=("/s",)), size=100),
            Finding(rule=CleanRule(id="m", name="人工", paths=("/m",), risk=MANUAL), size=50),
        ],
    )
    phases = plan_phases(rows)
    assert phases[0][0].startswith("第一步")
    assert phases[-1][0].startswith("第四步")
    assert sum(size for _name, _note, size in phases) == 150


# ---------------------------------------------------------------- report


def _report_inputs(tmp_path):
    scan = _scan(tmp_path, hits=[FileHit(path=str(tmp_path / "big.bin"), size=7 * 1024**3)])
    findings = [
        Finding(rule=CleanRule(id="npm", name="npm 缓存", paths=("/npm",)), size=5 * 1024**3),
        Finding(rule=CleanRule(id="rb", name="回收站", paths=("/rb",), risk=CAUTION), size=200),
        Finding(rule=CleanRule(id="hib", name="休眠文件", paths=("/hib",), risk=MANUAL), size=1024),
    ]
    return scan, build_opportunities(scan, findings)


def test_markdown_contains_sections(tmp_path):
    scan, rows = _report_inputs(tmp_path)
    text = render_markdown(scan, rows, generated_at=dt.datetime(2026, 1, 2, 3, 4),
                           disk=DiskInfo(path="C:/", total=1000, used=900, free=100))
    for needle in ["磁盘空间分析报告", "总体情况", "空间去哪了", "能清什么",
                   "推荐执行顺序", "可直接清理", "确认后清理", "只能人工处理",
                   "2026-01-02 03:04", "spacesift clean"]:
        assert needle in text, needle
    assert "|" in text


def test_markdown_reports_free_space_after_cleanup(tmp_path):
    scan, rows = _report_inputs(tmp_path)
    text = render_markdown(scan, rows, disk=DiskInfo(path="C:/", total=1000, used=900, free=100))
    assert "只清垃圾" in text


def test_html_is_self_contained(tmp_path):
    scan, rows = _report_inputs(tmp_path)
    html = render_html(scan, rows, disk=DiskInfo(path="C:/", total=1000, used=900, free=100))
    assert html.startswith("<!doctype html>")
    assert "<style>" in html
    assert "http://" not in html.replace("http://www.w3.org", "")
    assert "npm 缓存" in html
    assert html.count("<table>") >= 2


def test_html_escapes_paths(tmp_path):
    scan = ScanResult(root=str(tmp_path), total_bytes=1, dirs={str(tmp_path): 1},
                      top_files=[FileHit(path="<script>alert(1)</script>", size=1)])
    html = render_html(scan, [])
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


def test_disk_info_reads_real_volume(tmp_path):
    info = disk_info(str(tmp_path))
    assert info.total > 0
    assert info.free >= 0
    assert 0 <= info.percent_used <= 100


def test_human_size_used_in_report(tmp_path):
    scan, rows = _report_inputs(tmp_path)
    text = render_markdown(scan, rows)
    assert human_size(scan.total_bytes) in text
