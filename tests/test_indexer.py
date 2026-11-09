"""文件名索引与搜索的测试。"""
from __future__ import annotations

import os
import time

from spacesift import indexer


def _seed(root) -> None:
    """在被索引目录里造点东西。注意 root 是「数据目录」，库文件放在它外面。"""
    data = root / "data"
    (data / "docs").mkdir(parents=True)
    (data / "media").mkdir()
    (data / "docs" / "2026.11.08.xlsx").write_bytes(b"x" * 100)
    (data / "docs" / "报关单.pdf").write_bytes(b"x" * 200)
    (data / "media" / "video.mp4").write_bytes(b"x" * 300)
    (data / "node_modules").mkdir()
    (data / "node_modules" / "kept.js").write_bytes(b"x" * 10)
    (data / "$RECYCLE.BIN").mkdir()
    (data / "$RECYCLE.BIN" / "junk.bin").write_bytes(b"x" * 10)


def _target(root):
    return str(root / "data")


def test_build_and_search(tmp_path):
    _seed(tmp_path)
    db = str(tmp_path / "index.db")

    report = indexer.build(_target(tmp_path), db)

    assert report.files == 4, "node_modules 里的文件也要索引（用户要能搜到）"
    assert report.dirs == 3

    hits = indexer.search(db, "xlsx")
    assert len(hits) == 1
    assert hits[0].name == "2026.11.08.xlsx"
    assert hits[0].size == 100


def test_search_is_case_insensitive(tmp_path):
    _seed(tmp_path)
    db = str(tmp_path / "index.db")
    indexer.build(_target(tmp_path), db)

    assert indexer.search(db, "VIDEO")
    assert indexer.search(db, "video")
    assert indexer.search(db, "Video")


def test_search_by_extension_only(tmp_path):
    _seed(tmp_path)
    db = str(tmp_path / "index.db")
    indexer.build(_target(tmp_path), db)

    hits = indexer.search(db, "", ext=".pdf")
    assert [hit.name for hit in hits] == ["报关单.pdf"]


def test_chinese_name_search(tmp_path):
    _seed(tmp_path)
    db = str(tmp_path / "index.db")
    indexer.build(_target(tmp_path), db)

    assert indexer.search(db, "报关")
    assert indexer.search(db, "单.pdf")


def test_only_recycle_bin_is_skipped(tmp_path):
    """只跳回收站和系统还原点；node_modules 这类要能搜到。"""
    _seed(tmp_path)
    db = str(tmp_path / "index.db")
    indexer.build(_target(tmp_path), db)

    assert indexer.search(db, "junk.bin") == [], "回收站里的不索引"
    assert indexer.search(db, "kept.js"), "node_modules 里的必须能搜到"


def test_freshness_depends_on_root_and_age(tmp_path):
    _seed(tmp_path)
    db = str(tmp_path / "index.db")
    indexer.build(_target(tmp_path), db)

    assert indexer.is_fresh(db, _target(tmp_path)) is True
    assert indexer.is_fresh(db, str(tmp_path / "data" / "docs")) is False
    assert indexer.is_fresh(db, _target(tmp_path), max_age_hours=0) is False


def test_search_before_index_returns_empty(tmp_path):
    db = str(tmp_path / "nope.db")
    assert indexer.search(db, "anything") == []
    assert indexer.count(db) == 0


def test_stop_keeps_partial_index(tmp_path):
    for index in range(60):
        folder = tmp_path / f"d{index:02d}"
        folder.mkdir()
        (folder / f"file{index}.txt").write_bytes(b"x" * 10)
    db = str(tmp_path / "index.db")

    calls = {"n": 0}

    def should_stop():
        calls["n"] += 1
        return calls["n"] > 3

    report = indexer.build(str(tmp_path), db, should_stop=should_stop, batch_size=10)

    assert report.stopped is True
    assert os.path.exists(db), "中断也要把已索引的部分留下"
    assert indexer.count(db) > 0
