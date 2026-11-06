"""clean 模块测试 —— 这是最要紧的一组。

工具会删文件，所以测试要证明：

1. 受保护路径一律拒绝（哪怕规则写错了）。
2. 规则外的路径一律拒绝（哪怕调用方构造了假的 Finding）。
3. dry-run 一个字节都不删。
4. 真删时只删目标内容，保留目录本身。
5. manual 级规则永远不会被批量清理带上。
"""

from __future__ import annotations

import os

import pytest

from spacesift.clean import clean_finding, clean_findings, is_forbidden, is_within
from spacesift.rules import CAUTION, MANUAL, SAFE, CleanRule, Finding


def _touch(path, size):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as handle:
        handle.write(b"x" * size)
    return path


def _dir_rule(path):
    return CleanRule(id="t", name="测试", paths=(path,), risk=SAFE, kind="dir")


def _finding(rule, paths=None, size=None):
    targets = paths if paths is not None else [p for p in rule.paths if os.path.exists(p)]
    total = size if size is not None else sum(
        os.path.getsize(p) if os.path.isfile(p) else 0 for p in targets
    )
    return Finding(rule=rule, size=total, files=len(targets), paths=list(targets))


# ---------------------------------------------------------------- 白名单


def test_is_forbidden_core_paths():
    assert is_forbidden("C:/")
    assert is_forbidden("C:\\")
    assert is_forbidden("C:/Windows")
    assert is_forbidden("C:/Windows/System32")
    assert is_forbidden("C:/Program Files")
    assert is_forbidden("C:/Users")


def test_forbidden_trees_block_children():
    # System32 的子树整个禁止
    assert is_forbidden("C:/Windows/System32/kernel32.dll")
    assert is_forbidden("C:/Windows/SysWOW64/ntdll.dll")


def test_temp_under_windows_is_allowed():
    # 但 C:\Windows\Temp 是合法清理目标，不能被 C:\Windows 的禁令连坐
    assert not is_forbidden("C:/Windows/Temp")
    assert not is_forbidden("C:/Windows/SoftwareDistribution/Download")
    assert not is_forbidden("C:/Users/me/AppData/Local/Temp")


def test_is_forbidden_short_paths():
    assert is_forbidden("/")
    assert is_forbidden("C:/")


def test_is_within():
    assert is_within("/a/b/c", ["/a/b"])
    assert is_within("/a/b", ["/a/b"])
    assert not is_within("/a/bc", ["/a/b"])
    assert not is_within("/x", ["/a/b"])


# ---------------------------------------------------------------- dry-run


def test_dry_run_deletes_nothing(tmp_path):
    target = tmp_path / "cache"
    _touch(str(target / "one.bin"), 100)
    _touch(str(target / "sub" / "two.bin"), 200)
    rule = _dir_rule(str(target))

    report = clean_finding(_finding(rule), dry_run=True)

    assert report.dry_run is True
    assert report.freed_bytes == 300
    assert report.deleted_files == 2
    assert (target / "one.bin").exists()
    assert (target / "sub" / "two.bin").exists()


# ---------------------------------------------------------------- 真删


def test_real_clean_removes_contents_keeps_dir(tmp_path):
    target = tmp_path / "cache"
    _touch(str(target / "one.bin"), 100)
    _touch(str(target / "sub" / "two.bin"), 200)
    _touch(str(target / "sub" / "deep" / "three.bin"), 300)
    rule = _dir_rule(str(target))

    report = clean_finding(_finding(rule), dry_run=False)

    assert report.freed_bytes == 600
    assert report.deleted_files == 3
    assert target.exists(), "目录本身必须保留"
    assert not (target / "one.bin").exists()
    assert not (target / "sub").exists()
    assert report.errors == []


def test_real_clean_file_rule(tmp_path):
    target = _touch(str(tmp_path / "thumbcache_1.db"), 64)
    rule = CleanRule(id="f", name="文件", paths=(target,), risk=SAFE, kind="file")
    report = clean_finding(_finding(rule), dry_run=False)
    assert report.freed_bytes == 64
    assert not os.path.exists(target)


# ---------------------------------------------------------------- 拒绝


def test_refuses_path_outside_rule(tmp_path):
    """构造一个 paths 指向规则外的 Finding —— 必须拒绝且不删。"""
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    innocent = _touch(str(tmp_path / "innocent.txt"), 10)

    rule = _dir_rule(str(allowed))
    fake = Finding(rule=rule, size=10, files=1, paths=[innocent])

    report = clean_finding(fake, dry_run=False)

    assert os.path.exists(innocent), "规则外的文件绝不能被删"
    assert any("规则外" in error for error in report.errors)
    assert report.freed_bytes == 0


def test_refuses_forbidden_path():
    """规则故意指向受保护目录时，必须拒绝且不删。

    用真实系统路径跑 dry-run —— 代码不会走到删除，但失败时也不能真的动手。
    """
    target = "C:/Windows/System32/kernel32.dll"
    rule = CleanRule(id="bad", name="坏规则", paths=(target,), kind="file")
    finding = Finding(rule=rule, size=10, files=1, paths=[target])

    report = clean_finding(finding, dry_run=True)

    assert any("受保护" in error for error in report.errors)
    assert report.freed_bytes == 0
    assert report.deleted_files == 0


def test_forbidden_tree_blocks_subpath():
    target = "C:/Windows/System32/config/SAM"
    rule = CleanRule(id="bad2", name="坏规则2", paths=(target,), kind="file")
    report = clean_finding(Finding(rule=rule, size=1, paths=[target]), dry_run=True)
    assert any("受保护" in error for error in report.errors)


# ---------------------------------------------------------------- 批量策略


def test_batch_skips_manual_and_caution(tmp_path):
    safe_dir = tmp_path / "safe"
    caution_dir = tmp_path / "caution"
    manual_dir = tmp_path / "manual"
    for directory in (safe_dir, caution_dir, manual_dir):
        _touch(str(directory / "x.bin"), 10)

    findings = [
        Finding(rule=_dir_rule(str(safe_dir)), size=10, paths=[str(safe_dir)]),
        Finding(rule=CleanRule(id="c", name="c", paths=(str(caution_dir),), risk=CAUTION),
                size=10, paths=[str(caution_dir)]),
        Finding(rule=CleanRule(id="m", name="m", paths=(str(manual_dir),), risk=MANUAL),
                size=10, paths=[str(manual_dir)]),
    ]

    reports = clean_findings(findings, dry_run=False)
    assert [r.rule_id for r in reports] == ["t"]
    assert (caution_dir / "x.bin").exists()
    assert (manual_dir / "x.bin").exists()
    assert not (safe_dir / "x.bin").exists()


def test_batch_include_caution(tmp_path):
    caution_dir = tmp_path / "caution"
    _touch(str(caution_dir / "x.bin"), 10)
    findings = [Finding(rule=CleanRule(id="c", name="c", paths=(str(caution_dir),), risk=CAUTION),
                        size=10, paths=[str(caution_dir)])]

    reports = clean_findings(findings, dry_run=False, include_caution=True)
    assert len(reports) == 1
    assert not (caution_dir / "x.bin").exists()


def test_batch_only_filter(tmp_path):
    a = tmp_path / "a"
    b = tmp_path / "b"
    _touch(str(a / "x"), 1)
    _touch(str(b / "x"), 1)
    findings = [
        Finding(rule=CleanRule(id="a", name="a", paths=(str(a),)), size=1, paths=[str(a)]),
        Finding(rule=CleanRule(id="b", name="b", paths=(str(b),)), size=1, paths=[str(b)]),
    ]
    clean_findings(findings, dry_run=False, only=["b"])
    assert (a / "x").exists()
    assert not (b / "x").exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows 只读属性才有意义")
def test_readonly_file_is_removed(tmp_path):
    target = _touch(str(tmp_path / "cache" / "ro.bin"), 5)
    os.chmod(target, 0o444)
    report = clean_finding(_finding(_dir_rule(str(tmp_path / "cache"))), dry_run=False)
    assert report.deleted_files == 1
    assert not os.path.exists(target)
