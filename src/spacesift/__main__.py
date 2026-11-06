"""``python -m spacesift`` 入口，同时也是 PyInstaller 的打包入口。

PyInstaller 把本文件当普通脚本执行，这时 ``__package__`` 为空、相对导入会炸；
源码运行时又有包上下文。两条路都走通，才能让「克隆下来跑」和「双击 exe」是同一份代码。
"""

from __future__ import annotations

import sys

if __package__ in (None, ""):
    from spacesift.cli import main  # type: ignore[import-not-found]
else:
    from .cli import main

if __name__ == "__main__":
    sys.exit(main())
