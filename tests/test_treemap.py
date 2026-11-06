"""treemap 布局测试：面积守恒、不重叠、不越界、退化输入。"""

from __future__ import annotations

import pytest

from spacesift.treemap import children_of, squarify


def _overlap(a, b) -> float:
    """两个矩形的重叠面积。"""
    _, ax, ay, aw, ah = a
    _, bx, by, bw, bh = b
    dx = min(ax + aw, bx + bw) - max(ax, bx)
    dy = min(ay + ah, by + bh) - max(ay, by)
    return dx * dy if dx > 0 and dy > 0 else 0.0


def test_area_is_conserved():
    items = [("a", 50), ("b", 30), ("c", 15), ("d", 5)]
    boxes = squarify(items, 0, 0, 400, 300)
    total = sum(w * h for _, _x, _y, w, h in boxes)
    assert total == pytest.approx(400 * 300, rel=1e-6)


def test_boxes_do_not_overlap():
    items = [(f"d{i}", v) for i, v in enumerate([120, 90, 60, 40, 30, 20, 10, 5, 3, 1])]
    boxes = squarify(items, 0, 0, 500, 400)
    for i in range(len(boxes)):
        for j in range(i + 1, len(boxes)):
            assert _overlap(boxes[i], boxes[j]) < 1e-6, (boxes[i], boxes[j])


def test_boxes_stay_inside_container():
    items = [(f"d{i}", 10 + i) for i in range(20)]
    boxes = squarify(items, 10, 20, 300, 200)
    for key, x, y, w, h in boxes:
        assert x >= 10 - 1e-6 and y >= 20 - 1e-6
        assert x + w <= 310 + 1e-6, (key, x, w)
        assert y + h <= 220 + 1e-6, (key, y, h)


def test_output_sorted_by_area_desc():
    items = [("small", 1), ("huge", 100), ("mid", 10)]
    boxes = squarify(items, 0, 0, 300, 300)
    areas = [w * h for _, _x, _y, w, h in boxes]
    assert areas == sorted(areas, reverse=True)


def test_single_item_fills_container():
    boxes = squarify([("only", 42)], 5, 5, 200, 100)
    assert len(boxes) == 1
    key, x, y, w, h = boxes[0]
    assert (key, x, y, w, h) == ("only", 5, 5, 200, 100)


def test_empty_and_degenerate():
    assert squarify([]) == []
    assert squarify([("a", 0)]) == []
    assert squarify([("a", -5)]) == []
    assert squarify([("a", 10)], w=0) == []
    assert squarify([("a", 10)], h=-1) == []


def test_zero_area_items_filtered_but_others_kept():
    boxes = squarify([("a", 10), ("b", 0), ("c", 10)], 0, 0, 100, 100)
    assert {b[0] for b in boxes} == {"a", "c"}


def test_tiny_values_still_placeable():
    boxes = squarify([("big", 1_000_000), ("tiny", 1)], 0, 0, 800, 600)
    assert len(boxes) == 2
    assert sum(w * h for _, _x, _y, w, h in boxes) == pytest.approx(800 * 600, rel=1e-6)


def test_tall_container_still_conserves_area():
    boxes = squarify([("a", 3), ("b", 2), ("c", 1)], 0, 0, 100, 900)
    assert sum(w * h for _, _x, _y, w, h in boxes) == pytest.approx(100 * 900, rel=1e-6)


def test_children_of_picks_direct_children_only(tmp_path):
    import os

    root = str(tmp_path)
    dirs = {
        root: 1000,
        os.path.join(root, "a"): 600,
        os.path.join(root, "a", "deep"): 500,
        os.path.join(root, "b"): 400,
    }
    rows = children_of(dirs, root)
    assert [os.path.basename(p) for p, _ in rows] == ["a", "b"]
    assert [s for _, s in rows] == [600, 400]
