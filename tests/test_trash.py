"""回收站删除的测试：真的调用 SHFileOperation，但只用临时文件。"""
from __future__ import annotations

import os

import pytest

from spacesift.trash import is_drive_root, is_protected, trash_paths


def test_protected_paths_are_refused():
    for path in (
        r"C:\Windows\System32\drivers\etc\hosts",
        r"C:\Program Files\SpaceSift\x.dll",
        r"C:\ProgramData\Microsoft\Windows\WER\ReportQueue",
    ):
        assert is_protected(path), path


def test_normal_paths_are_allowed():
    assert not is_protected(r"C:\Users\ALICE\Desktop\论文.docx")
    assert not is_protected(r"D:\projects\model\a.mph")


def test_drive_root_detection():
    assert is_drive_root("C:\\")
    assert is_drive_root("D:/")
    assert not is_drive_root(r"C:\Users\ALICE")


def test_refuses_protected_and_missing(tmp_path):
    missing = str(tmp_path / "not-here.bin")
    report = trash_paths([missing, r"C:\Windows\System32"])

    assert report.removed == []
    reasons = [item["reason"] for item in report.refused]
    assert any("不存在" in reason for reason in reasons)
    assert any("系统目录" in reason for reason in reasons)


@pytest.mark.skipif(not os.path.exists(r"C:\$Recycle.Bin"), reason="需要 Windows 回收站")
def test_real_file_goes_to_recycle_bin(tmp_path):
    target = tmp_path / "spacesift_trash_test.txt"
    target.write_text("往回收站里送一个测试文件", encoding="utf-8")
    assert target.exists()

    report = trash_paths([str(target)])

    assert report.ok, report.refused
    assert report.removed == [str(target)]
    assert not target.exists(), "文件应该已经从原位置消失（进回收站了）"
