"""把文件移到回收站 —— 可恢复的删除。

推荐模式删的是白名单缓存（删了自动重建），深度模式里用户可能勾任何东西：
桌面上的论文、照片、模型文件。那些不能用 `os.remove`，删了就没了。

这里走 Windows 的 ``SHFileOperationW`` + ``FOF_ALLOWUNDO``，文件进回收站，
用户后悔了能自己还原。受保护目录（Windows / Program Files / ProgramData）
无论如何都拒绝，和清理逻辑用同一套底线。
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field

# 这些目录下的东西不进回收站操作：搬走删掉会让系统或程序坏掉
PROTECTED_PREFIXES = (
    r"C:\Windows",
    r"C:\Program Files",
    r"C:\Program Files (x86)",
    r"C:\ProgramData",
)


def _norm(path: str) -> str:
    return os.path.normcase(os.path.abspath(path)).replace("\\", "/").rstrip("/")


def is_protected(path: str) -> bool:
    target = _norm(path)
    for prefix in PROTECTED_PREFIXES:
        base = _norm(prefix)
        if target == base or target.startswith(base + "/"):
            return True
    return False


def is_drive_root(path: str) -> bool:
    stripped = os.path.abspath(path).rstrip("\\/")
    return len(stripped) <= 2 or stripped.endswith(":")


@dataclass
class TrashReport:
    removed: list[str] = field(default_factory=list)
    refused: list[dict] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return bool(self.removed)

    def summary(self) -> str:
        text = f"已移入回收站 {len(self.removed)} 项"
        if self.refused:
            text += f"，跳过 {len(self.refused)} 项"
        return text


def _delete_one(path: str) -> bool:
    """调 SHFileOperationW 把单个路径送进回收站。"""
    import ctypes
    from ctypes import wintypes

    class SHFILEOPSTRUCTW(ctypes.Structure):
        _fields_ = [
            ("hwnd", wintypes.HWND),
            ("wFunc", wintypes.UINT),
            ("pFrom", wintypes.LPCWSTR),
            ("pTo", wintypes.LPCWSTR),
            ("fFlags", ctypes.c_ushort),
            ("fAnyOperationsAborted", wintypes.BOOL),
            ("hNameMappings", ctypes.c_void_p),
            ("lpszProgressTitle", wintypes.LPCWSTR),
        ]

    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    shell32.SHFileOperationW.argtypes = [ctypes.POINTER(SHFILEOPSTRUCTW)]
    shell32.SHFileOperationW.restype = ctypes.c_int

    FO_DELETE = 0x0003
    FOF_SILENT = 0x0004
    FOF_NOCONFIRMATION = 0x0010
    FOF_ALLOWUNDO = 0x0040
    FOF_NOERRORUI = 0x0400

    operation = SHFILEOPSTRUCTW()
    operation.hwnd = None
    operation.wFunc = FO_DELETE
    # 必须以双 \0 结尾，否则 SHFileOperation 会把内存后面当路径读
    operation.pFrom = path + "\0\0"
    operation.pTo = None
    operation.fFlags = FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_SILENT | FOF_NOERRORUI
    result = shell32.SHFileOperationW(ctypes.byref(operation))
    if result != 0:
        return False
    return not operation.fAnyOperationsAborted


def trash_paths(paths: list[str]) -> TrashReport:
    """逐个移入回收站。受保护目录、盘符根、不存在的路径一律拒绝并说明原因。"""
    report = TrashReport()

    if not sys.platform.startswith("win"):
        for path in paths:
            report.refused.append({"path": path, "reason": "回收站操作只支持 Windows"})
        return report

    for raw in paths:
        path = os.path.abspath(raw) if raw else ""
        if not path or not os.path.exists(path):
            report.refused.append({"path": raw, "reason": "路径不存在"})
            continue
        if is_drive_root(path):
            report.refused.append({"path": path, "reason": "不能删盘符根目录"})
            continue
        if is_protected(path):
            report.refused.append({"path": path, "reason": "系统目录，工具不动"})
            continue
        try:
            if _delete_one(path):
                report.removed.append(path)
            else:
                report.refused.append({"path": path, "reason": "系统拒绝（可能正被占用）"})
        except OSError as exc:
            report.refused.append({"path": path, "reason": str(exc)})

    return report
