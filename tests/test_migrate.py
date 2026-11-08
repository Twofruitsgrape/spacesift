"""归档迁移的测试：复制 → 校验 → 删源，失败不动源文件。"""
from __future__ import annotations

import os

from spacesift.migrate import execute, plan, sha256_file, unique_target


def _make(path: str, size: int = 8192) -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as handle:
        handle.write(os.urandom(size))
    return path


def test_move_copies_verifies_and_removes_source(tmp_path):
    src_dir = tmp_path / "src"
    dst_dir = tmp_path / "dst"
    dst_dir.mkdir()
    sources = [_make(str(src_dir / f"f{i}.bin")) for i in range(3)]
    before = {p: sha256_file(p) for p in sources}

    items = plan(sources, str(dst_dir))
    assert len(items) == 3

    report = execute(items)

    assert report.stopped is False
    assert report.failed == []
    assert len(report.moved) == 3
    for path, digest in before.items():
        assert not os.path.exists(path), "源文件应该已经删掉"
        moved = os.path.join(str(dst_dir), os.path.basename(path))
        assert os.path.exists(moved)
        assert sha256_file(moved) == digest


def test_target_name_collision_does_not_overwrite(tmp_path):
    dst_dir = tmp_path / "dst"
    dst_dir.mkdir()
    existing = _make(str(dst_dir / "same.bin"), 100)

    picked = unique_target(str(dst_dir), "same.bin")

    assert picked != existing
    assert os.path.basename(picked) == "same (2).bin"
    assert os.path.getsize(existing) == 100, "已有文件不能被覆盖"


def test_stop_keeps_remaining_sources(tmp_path):
    src_dir = tmp_path / "src"
    dst_dir = tmp_path / "dst"
    dst_dir.mkdir()
    sources = [_make(str(src_dir / f"f{i}.bin")) for i in range(4)]

    items = plan(sources, str(dst_dir))
    calls = {"n": 0}

    def should_stop():
        calls["n"] += 1
        return calls["n"] > 1          # 搬完第一个就喊停

    report = execute(items, should_stop=should_stop)

    assert report.stopped is True
    assert len(report.moved) == 1
    remaining = [p for p in sources if os.path.exists(p)]
    assert len(remaining) == 3, "没搬的源文件必须原样保留"


def test_bytes_progress_reaches_total(tmp_path):
    src_dir = tmp_path / "src"
    dst_dir = tmp_path / "dst"
    dst_dir.mkdir()
    sources = [_make(str(src_dir / f"f{i}.bin"), 65536) for i in range(2)]

    seen: list[int] = []
    execute(plan(sources, str(dst_dir)), on_bytes=lambda written, chunk: seen.append(written))

    assert seen, "应该收到字节进度回调"
    assert max(seen) >= 65536 * 2 - 1024
