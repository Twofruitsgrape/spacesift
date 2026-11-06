"""可清理项规则表。

每条规则描述「一类可以安全回收的空间」。风险等级决定 ``clean`` 肯不肯动手：

* ``safe``    —— 由系统或程序自动重建，删了不会丢数据，可以自动清理。
* ``caution`` —— 删了要重新下载 / 重新生成 / 需要重新登录，默认只在报告里出现，
                 用户显式 ``--include-caution`` 才会清。
* ``manual``  —— 绝不能由本工具自动删除（休眠文件、系统还原点、页面文件、
                 用户文档），只做检测 + 给出人工操作命令。

路径支持 ``%WINDIR%`` / ``$HOME`` / ``~`` 形式，并按 ``kind`` 处理：

* ``dir``  —— 清空目录里的内容（保留目录本身）。
* ``glob`` —— 按通配符匹配文件后删除。
* ``file`` —— 精确到单个文件（通常配 ``manual``，只报告）。
"""

from __future__ import annotations

import glob as globlib
import os
import sys
from dataclasses import dataclass, field
from typing import Iterable, Iterator

from .scan import dir_size

SAFE = "safe"
CAUTION = "caution"
MANUAL = "manual"

_RISK_ORDER = {SAFE: 0, CAUTION: 1, MANUAL: 2}


@dataclass(frozen=True)
class CleanRule:
    """一条清理规则。"""

    id: str
    name: str
    paths: tuple[str, ...]
    risk: str = SAFE
    kind: str = "dir"
    why: str = ""
    hint: str = ""
    requires_admin: bool = False
    platforms: tuple[str, ...] = ("win32",)

    def applies_here(self) -> bool:
        return sys.platform in self.platforms or (
            sys.platform.startswith("win") and "win32" in self.platforms
        )


@dataclass
class Finding:
    """一条规则的检测结果。"""

    rule: CleanRule
    size: int = 0
    files: int = 0
    paths: list[str] = field(default_factory=list)


def expand(path: str) -> str:
    """展开 ``%VAR%`` / ``$VAR`` / ``~``，返回绝对路径。"""
    expanded = os.path.expandvars(os.path.expanduser(path))
    # POSIX 上 os.path.expandvars 不认 %VAR%，反过来 Windows 也认 $VAR，
    # 这里补一刀，保证规则表在两个平台都能用同一套写法。
    if "%" in expanded:
        for key, value in os.environ.items():
            expanded = expanded.replace(f"%{key}%", value)
    return os.path.abspath(expanded)


def _iter_files(root: str) -> Iterator[tuple[str, int]]:
    """遍历目录下所有普通文件（跳过 reparse point）。"""
    stack = [root]
    while stack:
        current = stack.pop()
        try:
            iterator = os.scandir(current)
        except OSError:
            continue
        with iterator:
            while True:
                try:
                    entry = next(iterator)
                except StopIteration:
                    break
                except OSError:
                    continue
                try:
                    if entry.is_dir(follow_symlinks=False):
                        if bool(getattr(entry.stat(follow_symlinks=False), "st_file_attributes", 0) & 0x400):
                            continue
                        stack.append(entry.path)
                    elif entry.is_file(follow_symlinks=False):
                        yield entry.path, entry.stat(follow_symlinks=False).st_size
                except OSError:
                    continue


def rule_targets(rule: CleanRule) -> list[str]:
    """把规则展开成实际存在的目标路径列表。"""
    targets: list[str] = []
    for raw in rule.paths:
        pattern = expand(raw)
        if rule.kind == "glob":
            targets.extend(sorted(p for p in globlib.glob(pattern) if os.path.exists(p)))
        elif os.path.exists(pattern):
            targets.append(pattern)
    return targets


def detect(rule: CleanRule) -> Finding:
    """检测一条规则当前占用多少空间。"""
    finding = Finding(rule=rule)
    for target in rule_targets(rule):
        if rule.kind == "dir" and os.path.isdir(target):
            size = 0
            count = 0
            for _path, file_size in _iter_files(target):
                size += file_size
                count += 1
            finding.size += size
            finding.files += count
            finding.paths.append(target)
        else:
            try:
                size = os.path.getsize(target)
            except OSError:
                continue
            finding.size += size
            finding.files += 1
            finding.paths.append(target)
    return finding


def detect_all(rules: Iterable[CleanRule] | None = None) -> list[Finding]:
    """检测全部规则，只返回有内容的结果。

    排序：先按「能不能自动做」（safe → caution → manual），同档内按占用降序。
    这样用户第一眼看到的就是可以直接清的东西。
    """
    findings = [detect(rule) for rule in (rules if rules is not None else RULES)]
    findings = [f for f in findings if f.size > 0]
    findings.sort(key=lambda f: (_RISK_ORDER.get(f.rule.risk, 9), -f.size))
    return findings


def _win(*paths: str) -> tuple[str, ...]:
    return paths


RULES: tuple[CleanRule, ...] = (
    # ---------------- safe ----------------
    CleanRule(
        id="user-temp",
        name="用户临时文件",
        paths=(r"%TEMP%",),
        risk=SAFE,
        why="程序和安装包留下的临时文件，重启后大多已经没用。",
    ),
    CleanRule(
        id="windows-temp",
        name="Windows 临时目录",
        paths=(r"%WINDIR%\Temp",),
        risk=SAFE,
        why="系统与安装程序共用的临时目录。",
        requires_admin=True,
    ),
    CleanRule(
        id="update-download",
        name="Windows 更新下载缓存",
        paths=(r"%WINDIR%\SoftwareDistribution\Download",),
        risk=SAFE,
        why="已安装更新的安装包缓存，删除后不会影响已装补丁。",
        requires_admin=True,
    ),
    CleanRule(
        id="delivery-optimization",
        name="传递优化缓存",
        paths=(
            r"%WINDIR%\ServiceProfiles\NetworkService\AppData\Local\Microsoft\Windows\DeliveryOptimization",
        ),
        risk=SAFE,
        why="Windows 更新点对点分发的缓存。",
        requires_admin=True,
    ),
    CleanRule(
        id="thumbnail-cache",
        name="缩略图缓存",
        paths=(r"%LOCALAPPDATA%\Microsoft\Windows\Explorer\thumbcache_*.db",),
        risk=SAFE,
        kind="glob",
        why="资源管理器的缩略图数据库，会自动重建。",
    ),
    CleanRule(
        id="crash-dumps",
        name="崩溃转储",
        paths=(r"%LOCALAPPDATA%\CrashDumps",),
        risk=SAFE,
        why="程序崩溃时写下的转储文件，排查完就没用了。",
    ),
    CleanRule(
        id="wer-reports",
        name="Windows 错误报告",
        paths=(r"%PROGRAMDATA%\Microsoft\Windows\WER",),
        risk=SAFE,
        why="错误报告队列，可以安全清空。",
        requires_admin=True,
    ),
    CleanRule(
        id="winsxs-backup",
        name="组件存储备份 (WinSxS)",
        paths=(r"%WINDIR%\WinSxS\Backup",),
        risk=CAUTION,
        why="微软官方建议用 DISM 而不是手删；手删会破坏组件存储。",
        hint="DISM /Online /Cleanup-Image /StartComponentCleanup /ResetBase",
        requires_admin=True,
    ),
    CleanRule(
        id="pip-cache",
        name="pip 缓存",
        paths=(r"%LOCALAPPDATA%\pip\Cache", "~/.cache/pip"),
        risk=SAFE,
        why="下载过的 wheel 包缓存，重装时会重新下载。",
        platforms=("win32", "linux", "darwin"),
    ),
    CleanRule(
        id="uv-cache",
        name="uv 缓存",
        paths=(r"%LOCALAPPDATA%\uv\cache", "~/.cache/uv"),
        risk=SAFE,
        why="uv 的包缓存，可用 `uv cache clean` 重建。",
        platforms=("win32", "linux", "darwin"),
    ),
    CleanRule(
        id="npm-cache",
        name="npm 缓存",
        paths=(r"%LOCALAPPDATA%\npm-cache", "~/.npm/_cacache"),
        risk=SAFE,
        why="npm 的本地包缓存。",
        platforms=("win32", "linux", "darwin"),
    ),
    CleanRule(
        id="pnpm-store",
        name="pnpm 全局存储",
        paths=(r"%LOCALAPPDATA%\pnpm\store", "~/.local/share/pnpm/store"),
        risk=CAUTION,
        why="pnpm 用硬链接共享这个存储，删了已装项目的依赖会一起坏。",
        hint="pnpm store prune  # 只清未被引用的包",
    ),
    CleanRule(
        id="browser-cache-chrome",
        name="Chrome 缓存",
        paths=(
            r"%LOCALAPPDATA%\Google\Chrome\User Data\Default\Cache",
            r"%LOCALAPPDATA%\Google\Chrome\User Data\Default\Code Cache",
            r"%LOCALAPPDATA%\Google\Chrome\User Data\Default\GPUCache",
        ),
        risk=SAFE,
        why="网页缓存，删除后首次访问会稍慢，不影响登录状态。",
    ),
    CleanRule(
        id="browser-cache-edge",
        name="Edge 缓存",
        paths=(
            r"%LOCALAPPDATA%\Microsoft\Edge\User Data\Default\Cache",
            r"%LOCALAPPDATA%\Microsoft\Edge\User Data\Default\Code Cache",
            r"%LOCALAPPDATA%\Microsoft\Edge\User Data\Default\GPUCache",
        ),
        risk=SAFE,
        why="网页缓存，可安全清理。",
    ),
    CleanRule(
        id="chrome-ondevice-model",
        name="Chrome 端侧 AI 模型",
        paths=(
            r"%LOCALAPPDATA%\Google\Chrome\User Data\OptGuideOnDeviceModel",
            r"%LOCALAPPDATA%\Google\Chrome Beta\User Data\OptGuideOnDeviceModel",
            "~/.config/freeagent/chrome/OptGuideOnDeviceModel",
        ),
        risk=CAUTION,
        why="Chrome 内置的端侧 AI 模型，每份约 4 GB，多个浏览器副本各存一份。删了下次用到会重新下载。",
        hint="关闭 Chrome 的「本地 AI 功能」可避免它再次下载",
    ),
    CleanRule(
        id="electron-cache",
        name="Electron 应用缓存",
        paths=(
            r"%APPDATA%\Code\Cache",
            r"%APPDATA%\Code\CachedData",
            r"%APPDATA%\Code\logs",
            r"%APPDATA%\discord\Cache",
            r"%APPDATA%\Slack\Cache",
            r"%APPDATA%\obs-studio\logs",
            r"%APPDATA%\腾讯会议\logs",
        ),
        risk=SAFE,
        why="VS Code / Slack / Discord 等 Electron 应用的缓存与日志。",
    ),
    CleanRule(
        id="logs",
        name="Windows 日志",
        paths=(r"%WINDIR%\Logs", r"%WINDIR%\Panther"),
        risk=CAUTION,
        why="系统安装与排错日志，出问题时还有用，平时可以清。",
        requires_admin=True,
    ),
    # ---------------- caution ----------------
    CleanRule(
        id="recycle-bin",
        name="回收站",
        paths=("C:/$Recycle.Bin",),
        risk=CAUTION,
        why="回收站里的东西是用户主动删的，但清空后不可恢复。",
        hint="清空前先确认里面没有还要的文件",
    ),
    CleanRule(
        id="prefetch",
        name="预读取数据 (Prefetch)",
        paths=(r"%WINDIR%\Prefetch",),
        risk=CAUTION,
        why="删除后头几次启动程序会变慢，之后自动重建。收益通常很小。",
        requires_admin=True,
    ),
    CleanRule(
        id="windows-old",
        name="旧系统备份 (Windows.old)",
        paths=("C:/Windows.old",),
        risk=CAUTION,
        why="升级前的旧系统，能回滚。确认不需要回滚再用磁盘清理删除。",
        hint="设置 → 系统 → 存储 → 临时文件 → 以前的 Windows 安装",
        requires_admin=True,
    ),
    CleanRule(
        id="downloads",
        name="下载文件夹",
        paths=("~/Downloads",),
        risk=MANUAL,
        why="里面是你的文件，工具不会动，只报告大小。",
        hint="按修改时间手动挑旧文件删",
        platforms=("win32", "linux", "darwin"),
    ),
    CleanRule(
        id="wechat-files",
        name="微信聊天文件",
        paths=("~/Documents/xwechat_files", "~/Documents/WeChat Files"),
        risk=MANUAL,
        why="微信接收的图片/视频/文件，往往几十 GB。删了聊天记录里的附件就打不开了。",
        hint="微信 → 设置 → 存储空间 → 管理，按聊天清理",
    ),
    CleanRule(
        id="qq-files",
        name="QQ 接收文件",
        paths=("~/Documents/Tencent Files",),
        risk=MANUAL,
        why="QQ 保存的聊天文件与图片。",
        hint="QQ → 设置 → 文件管理 → 清理",
    ),
    CleanRule(
        id="vm-images",
        name="WSL 备份与磁盘镜像",
        paths=(
            "~/AppData/Local/wsl_backup",
            "~/AppData/Local/Packages/CanonicalGroupLimitedUbuntu/ext4.vhdx",
            "~/AppData/Local/Docker/wsl",
        ),
        risk=MANUAL,
        kind="dir",
        why="WSL 的 ext4.vhdx 与备份文件通常十几 GB，删了等于删掉整个 Linux 环境。",
        hint="迁移：wsl --export 到 D 盘后 --unregister 再 --import",
        platforms=("win32",),
    ),
    # ---------------- manual ----------------
    CleanRule(
        id="hiberfil",
        name="休眠文件 (hiberfil.sys)",
        paths=("C:/hiberfil.sys",),
        risk=MANUAL,
        kind="file",
        why="快速启动和休眠用的内存镜像，通常等于内存大小。关掉休眠可立即回收。",
        hint="powercfg /h off   # 需要管理员，关掉后「休眠」菜单消失",
        requires_admin=True,
    ),
    CleanRule(
        id="pagefile",
        name="页面文件 (pagefile.sys)",
        paths=("C:/pagefile.sys", "C:/swapfile.sys"),
        risk=MANUAL,
        kind="file",
        why="虚拟内存交换文件，不能直接删，只能在系统属性里调小或移到别的盘。",
        hint="系统属性 → 高级 → 性能设置 → 高级 → 虚拟内存",
        requires_admin=True,
    ),
    CleanRule(
        id="system-restore",
        name="系统还原点",
        paths=("C:/System Volume Information",),
        risk=MANUAL,
        why="系统还原的卷影副本，占用常常很大。删掉就没有回滚点了。",
        hint="vssadmin delete shadows /for=C: /all  # 或调小还原点配额",
        requires_admin=True,
    ),
)


def rules_by_id() -> dict[str, CleanRule]:
    return {rule.id: rule for rule in RULES}
