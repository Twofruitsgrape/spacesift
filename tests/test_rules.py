"""rules 模块测试：路径展开、单条检测、排序。"""

from __future__ import annotations

import os

from spacesift.rules import CAUTION, MANUAL, SAFE, CleanRule, detect, detect_all, expand, rule_targets


def _touch(path, size):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as handle:
        handle.write(b"x" * size)


def test_expand_env_and_user(monkeypatch, tmp_path):
    monkeypatch.setenv("DISKGUARD_TEST_DIR", str(tmp_path))
    assert expand("%DISKGUARD_TEST_DIR%") == os.path.abspath(str(tmp_path))
    assert expand("~") == os.path.abspath(os.path.expanduser("~"))


def test_detect_dir_rule(tmp_path):
    _touch(str(tmp_path / "a" / "x.bin"), 300)
    _touch(str(tmp_path / "a" / "y.bin"), 200)
    rule = CleanRule(id="t", name="测试", paths=(str(tmp_path / "a"),), risk=SAFE, kind="dir")
    finding = detect(rule)
    assert finding.size == 500
    assert finding.files == 2
    assert finding.rule.id == "t"


def test_detect_file_rule(tmp_path):
    target = tmp_path / "huge.sys"
    _touch(str(target), 4096)
    rule = CleanRule(id="f", name="文件", paths=(str(target),), risk=MANUAL, kind="file")
    finding = detect(rule)
    assert finding.size == 4096
    assert finding.files == 1


def test_detect_glob_rule(tmp_path):
    _touch(str(tmp_path / "thumbcache_1.db"), 10)
    _touch(str(tmp_path / "thumbcache_2.db"), 20)
    _touch(str(tmp_path / "other.db"), 999)
    rule = CleanRule(id="g", name="通配", paths=(str(tmp_path / "thumbcache_*.db"),), kind="glob")
    assert detect(rule).size == 30


def test_detect_missing_path_is_zero(tmp_path):
    rule = CleanRule(id="m", name="不存在", paths=(str(tmp_path / "nope"),))
    finding = detect(rule)
    assert finding.size == 0
    assert finding.paths == []


def test_rule_targets_dedupes_missing(tmp_path):
    existing = tmp_path / "yes"
    existing.mkdir()
    rule = CleanRule(id="d", name="d", paths=(str(existing), str(tmp_path / "no")))
    assert rule_targets(rule) == [str(existing)]


def test_detect_all_sorted_safe_first(tmp_path, monkeypatch):
    small = tmp_path / "small"
    big = tmp_path / "big"
    _touch(str(small / "a"), 100)
    _touch(str(big / "a"), 10_000)
    rules = [
        CleanRule(id="danger", name="危险", paths=(str(big),), risk=MANUAL),
        CleanRule(id="safe", name="安全", paths=(str(small),), risk=SAFE),
        CleanRule(id="warn", name="注意", paths=(str(big / "a"),), risk=CAUTION, kind="file"),
    ]
    findings = detect_all(rules)
    assert [f.rule.id for f in findings] == ["safe", "warn", "danger"]


def test_detect_all_drops_empty(tmp_path):
    rules = [CleanRule(id="x", name="空", paths=(str(tmp_path / "none"),))]
    assert detect_all(rules) == []


def test_rule_applies_here():
    import sys

    win = CleanRule(id="w", name="w", paths=("x",), platforms=("win32",))
    lin = CleanRule(id="l", name="l", paths=("x",), platforms=("linux",))
    assert win.applies_here() == sys.platform.startswith("win")
    assert lin.applies_here() == sys.platform.startswith("linux")
