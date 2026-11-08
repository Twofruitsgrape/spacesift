"""界面层几处路径/剪贴板的回归测试。

这几个 bug 都是「路径给空了或给短了」引起的：
- 聚合块 path="" → 右键打开会落到程序的工作目录（exe 所在文件夹）；
- 文件方块只给了文件名 → 同样定位不到；
- 复制走 navigator.clipboard → 内联页面里被拒。
"""
from __future__ import annotations

import os

import pytest

pytest.importorskip("webview", reason="界面层测试需要 pywebview")

from spacesift.gui import Api, GuiState, set_clipboard  # noqa: E402
from spacesift.scan import ScanResult  # noqa: E402


def test_reveal_rejects_empty_path():
    """空路径绝不能退化成 os.path.abspath("") —— 那是程序的工作目录。"""
    api = Api()
    assert api.reveal("") is False


def test_reveal_rejects_missing_path(tmp_path):
    api = Api()
    assert api.reveal(str(tmp_path / "根本不存在的目录")) is False


def test_leaf_files_carry_full_path(tmp_path):
    target = tmp_path / "leaf"
    target.mkdir()
    (target / "a.log").write_bytes(b"z" * 64)

    state = GuiState()
    state.result = ScanResult(root=str(target), dirs={str(target): 64})
    api = Api()
    api.state = state

    view = api.get_children(str(target))

    assert view["dirs"] == []
    assert len(view["files"]) == 1
    assert view["files"][0]["path"] == str(target / "a.log")
    assert os.path.isabs(view["files"][0]["path"])


def _read_clipboard() -> str:
    """读系统剪贴板。签名必须声明，否则 64 位下句柄会被截断（和写入端同一个坑）。"""
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    user32.OpenClipboard.argtypes = [wintypes.HWND]
    user32.OpenClipboard.restype = wintypes.BOOL
    user32.GetClipboardData.argtypes = [wintypes.UINT]
    user32.GetClipboardData.restype = wintypes.HANDLE
    user32.CloseClipboard.restype = wintypes.BOOL
    kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalLock.restype = wintypes.LPVOID
    kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]

    CF_UNICODETEXT = 13
    assert user32.OpenClipboard(None), "剪贴板被其它程序占用"
    try:
        handle = user32.GetClipboardData(CF_UNICODETEXT)
        assert handle, "剪贴板里没有文本"
        pointer = kernel32.GlobalLock(handle)
        assert pointer
        text = ctypes.wstring_at(pointer)
        kernel32.GlobalUnlock(handle)
        return text
    finally:
        user32.CloseClipboard()


def test_set_clipboard_roundtrip():
    """写进剪贴板的内容再读回来应当是同一份（顺便证明不是走浏览器 API）。"""
    if not hasattr(os, "startfile"):        # 只有 Windows 走 Win32 剪贴板
        pytest.skip("仅 Windows 有系统剪贴板实现")

    marker = "SpaceSift 剪贴板自检 / path: C:\\Users\\x\\a b\\c.log"
    assert set_clipboard(marker) is True
    assert _read_clipboard() == marker


def test_loose_files_are_listed_one_by_one(tmp_path):
    """目录里散落的文件要逐个显示，不能糊成一个「（N 个文件）」。"""
    for name in ("alpha", "beta"):
        (tmp_path / name).mkdir()
    made = []
    for index in range(8):
        target = tmp_path / ("loose%02d.bin" % index)
        target.write_bytes(b"x" * (100 * (index + 1)))
        made.append(target.name)

    state = GuiState()
    state.result = ScanResult(
        root=str(tmp_path),
        dirs={str(tmp_path): 5000, str(tmp_path / "alpha"): 200, str(tmp_path / "beta"): 200},
    )
    api = Api()
    api.state = state

    view = api.get_children(str(tmp_path))
    names = [row["name"] for row in view["dirs"]]

    for name in made:
        assert name in names, "每个散落文件都该有自己的方块"
    assert not any("个文件" in name for name in names), "不该再出现聚合灰块"

    files = [row for row in view["dirs"] if row.get("is_file")]
    assert len(files) == 8
    assert all(os.path.isabs(row["path"]) for row in files)


def test_many_files_fall_back_to_group_with_remainder(tmp_path):
    """超过 200 个文件时，多出来的零碎用一个块兜住，保证面积对得上。"""
    sub = tmp_path / "sub"
    sub.mkdir()
    for index in range(205):
        (tmp_path / ("f%03d.bin" % index)).write_bytes(b"x" * 10)

    state = GuiState()
    state.result = ScanResult(
        root=str(tmp_path),
        dirs={str(tmp_path): 2550, str(sub): 500},   # 有子目录才会走 dirs 分支
    )
    api = Api()
    api.state = state

    view = api.get_children(str(tmp_path))
    rows = view["dirs"]

    listed = [row for row in rows if row.get("is_file")]
    groups = [row for row in rows if row.get("file_group")]
    assert len(listed) == 200
    assert len(groups) == 1
    assert "较小文件" in groups[0]["name"]
    assert groups[0]["path"] == str(tmp_path), "兜底聚合块也要带真实目录"
    assert sum(row["size"] for row in rows) == 2550, "面积必须守恒"
