"""scan 模块测试：大小聚合、排序、跳过规则、人类可读格式。"""

from __future__ import annotations

import os

from spacesift.scan import ScanResult, dir_size, human_size, scan


def _make_tree(root):
    """造一棵已知大小的目录树。

    root/
      a.txt        100
      sub/
        b.bin      2048
        deep/
          c.bin    10000
      skipme/
        d.bin      500
    """
    os.makedirs(os.path.join(root, "sub", "deep"), exist_ok=True)
    os.makedirs(os.path.join(root, "skipme"), exist_ok=True)
    for path, size in [
        (("a.txt",), 100),
        (("sub", "b.bin"), 2048),
        (("sub", "deep", "c.bin"), 10000),
        (("skipme", "d.bin"), 500),
    ]:
        with open(os.path.join(root, *path), "wb") as handle:
            handle.write(b"x" * size)


def test_scan_totals_and_children(tmp_path):
    _make_tree(tmp_path)
    result = scan(str(tmp_path), max_depth=2, top_files=10)

    assert result.total_files == 4
    assert result.total_bytes == 100 + 2048 + 10000 + 500

    children = dict(result.child_sizes(str(tmp_path)))
    assert children[str(tmp_path / "sub")] == 2048 + 10000
    assert children[str(tmp_path / "skipme")] == 500


def test_scan_skips_named_directories(tmp_path):
    _make_tree(tmp_path)
    result = scan(str(tmp_path), max_depth=2, top_files=10, skip_names={"skipme"})
    assert result.total_bytes == 100 + 2048 + 10000
    assert str(tmp_path / "skipme") not in result.dirs


def test_scan_top_files_sorted_and_limited(tmp_path):
    _make_tree(tmp_path)
    result = scan(str(tmp_path), max_depth=2, top_files=2)
    sizes = [hit.size for hit in result.top_files]
    assert sizes == sorted(sizes, reverse=True)
    assert len(sizes) == 2
    assert sizes[0] == 10000


def test_scan_handles_missing_root(tmp_path):
    missing = tmp_path / "nope"
    result = scan(str(missing))
    assert result.total_bytes == 0
    assert result.errors >= 1


def test_child_sizes_only_direct_children(tmp_path):
    _make_tree(tmp_path)
    result = scan(str(tmp_path), max_depth=3)
    names = [os.path.basename(path) for path, _ in result.child_sizes(str(tmp_path))]
    assert names == ["sub", "skipme"]


def test_dir_size_matches_scan(tmp_path):
    _make_tree(tmp_path)
    assert dir_size(str(tmp_path / "sub")) == 2048 + 10000


def test_size_of_and_defaults():
    result = ScanResult(root="/x")
    assert result.size_of("/x/none") == 0
    assert result.child_sizes("/x") == []


def test_human_size():
    assert human_size(0) == "0 B"
    assert human_size(999) == "999 B"
    assert human_size(1024) == "1.0 KB"
    assert human_size(1024 * 1024 * 1.5) == "1.5 MB"
    assert human_size(1024**3) == "1.0 GB"
    assert human_size(-1024) == "-1.0 KB"


def test_checkpoint_can_stop_scan_early(tmp_path):
    """用户点结束后，scan 应该收尾返回部分结果，而不是把扫到的全扔掉。"""
    for index in range(60):
        child = tmp_path / ("d%02d" % index)
        child.mkdir()
        (child / "payload.bin").write_bytes(b"x" * 64)

    calls = {"n": 0}

    def checkpoint():
        calls["n"] += 1
        return calls["n"] < 2      # 第二次检查就要求结束

    result = scan(str(tmp_path), max_depth=2, checkpoint=checkpoint, checkpoint_every=8)

    assert result.interrupted is True
    assert result.total_files == 0 or result.total_files < 60
    assert calls["n"] >= 2


def test_finished_scan_is_not_marked_interrupted(tmp_path):
    (tmp_path / "a.txt").write_bytes(b"x" * 32)
    result = scan(str(tmp_path), max_depth=2, checkpoint=lambda: True, checkpoint_every=8)
    assert result.interrupted is False
    assert result.total_files == 1
