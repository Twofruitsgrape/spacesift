"""方块图（treemap）布局。

SpaceSniffer 那种「一个方块 = 一个目录，面积 = 占用」的图，这里用纯 Python
算好每个方块的位置和大小，前端只负责画 div，不需要任何 JS 布局库。

用的是 Bruls / Huizing / van Wijk 那套 squarified 思路的简化实现：每次先挑出
一行元素让宽高比最接近 1，再把这一行贴到矩形的一条边上，剩下的一块递归处理。
和真 treemap 库的区别是这里不追求绝对最优，只保证：

* 面积守恒 —— 所有方块面积之和等于容器面积；
* 不重叠、不越界；
* 面积大的方块更接近正方形（可读性远好于「一列细长条」）。

这两条正是「一眼看出谁最大」的全部要求，剩下的交给颜色和标签。
"""

from __future__ import annotations

from typing import Iterable, Sequence

#: 一个方块：(key, x, y, w, h)，坐标原点在左上角，单位由调用方决定（这里用像素）。
Box = tuple[str, float, float, float, float]


def _worst_ratio(row: Sequence[float], length: float) -> float:
    """把 ``row`` 铺在长度为 ``length`` 的边上时，最差宽高比。"""
    if not row or length <= 0:
        return float("inf")
    total = sum(row)
    if total <= 0:
        return float("inf")
    largest = max(row)
    smallest = min(row)
    if smallest <= 0:
        return float("inf")
    return max((length * length * largest) / (total * total),
               (total * total) / (length * length * smallest))


def _layout_row(row: Sequence[float], keys: Sequence[str],
                x: float, y: float, w: float, h: float, out: list[Box]) -> None:
    """把一行元素贴进矩形，沿长边方向排开。"""
    total = sum(row)
    if total <= 0:
        return
    if w >= h:
        offset = 0.0
        for key, area in zip(keys, row):
            width = w * area / total
            out.append((key, x + offset, y, width, h))
            offset += width
    else:
        offset = 0.0
        for key, area in zip(keys, row):
            height = h * area / total
            out.append((key, x, y + offset, w, height))
            offset += height


def squarify(items: Iterable[tuple[str, float]],
             x: float = 0.0, y: float = 0.0,
             w: float = 100.0, h: float = 100.0) -> list[Box]:
    """把 ``(key, value)`` 列表铺成方块图。

    Parameters
    ----------
    items:
        ``(key, value)``；value 是相对权重（通常就是目录占用的字节数）。
        0 或负值会被丢掉，避免除零。
    x, y, w, h:
        容器矩形。

    Returns
    -------
    ``[(key, x, y, w, h), ...]``，顺序与传入顺序无关（已按面积降序）。
    """
    if w <= 0 or h <= 0:
        return []

    cleaned = [(key, float(value)) for key, value in items if value and value > 0]
    if not cleaned:
        return []
    cleaned.sort(key=lambda pair: pair[1], reverse=True)

    total = sum(value for _, value in cleaned)
    scale = (w * h) / total
    keys = [key for key, _ in cleaned]
    areas = [value * scale for _, value in cleaned]

    out: list[Box] = []
    _squarify(areas, keys, x, y, w, h, out)
    return out


def _squarify(areas: list[float], keys: list[str],
              x: float, y: float, w: float, h: float, out: list[Box]) -> None:
    """递归铺满矩形。"""
    if not areas:
        return
    if len(areas) == 1:
        out.append((keys[0], x, y, w, h))
        return

    short_side = min(w, h)
    row: list[float] = []
    row_keys: list[str] = []
    index = 0

    # 贪心：不断往里加，直到宽高比开始变差
    while index < len(areas):
        candidate = row + [areas[index]]
        if not row or _worst_ratio(candidate, short_side) <= _worst_ratio(row, short_side):
            row.append(areas[index])
            row_keys.append(keys[index])
            index += 1
        else:
            break

    if index >= len(areas):
        # 剩下的全塞进这一行
        _layout_row(row, row_keys, x, y, w, h, out)
        return

    remaining = areas[index:]
    remaining_keys = keys[index:]
    row_share = sum(row) / (sum(row) + sum(remaining))

    if w >= h:
        row_width = w * row_share
        _layout_row(row, row_keys, x, y, row_width, h, out)
        _squarify(remaining, remaining_keys, x + row_width, y, w - row_width, h, out)
    else:
        row_height = h * row_share
        _layout_row(row, row_keys, x, y, w, row_height, out)
        _squarify(remaining, remaining_keys, x, y + row_height, w, h - row_height, out)


def children_of(result: dict[str, int], parent: str) -> list[tuple[str, int]]:
    """从扫描结果里取出某个目录的直接子目录，按大小降序。

    ``result`` 是 ``ScanResult.dirs``（绝对路径 -> 字节数）。
    """
    import os

    parent_key = os.path.abspath(parent).rstrip("\\/")
    prefix = parent_key + os.sep
    rows: list[tuple[str, int]] = []
    for path, size in result.items():
        if not path.startswith(prefix):
            continue
        rest = path[len(prefix):]
        if os.sep in rest:
            continue
        rows.append((path, size))
    rows.sort(key=lambda pair: pair[1], reverse=True)
    return rows
