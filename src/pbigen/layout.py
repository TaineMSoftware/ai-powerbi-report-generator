"""Deterministic layout engine.

Maps visuals onto a 12-column grid (90px rows on a 1280x720 canvas) using
optional NL hints ("across the top", "on the left", "below"). Defaults:
cards and slicers band across the top, remaining visuals flow two per row.
Returns the resulting page height (the grid grows downward if needed).
"""

from __future__ import annotations

from .spec import (
    CANVAS_HEIGHT,
    GRID_COLS,
    GRID_ROW_HEIGHT,
    GridPos,
    PixelPos,
    Visual,
    VisualType,
)

_MAIN_ROW_SPAN = 3


def _chunks(items: list, size: int):
    for i in range(0, len(items), size):
        yield items[i : i + size]


def apply_layout(visuals: list[Visual]) -> int:
    """Assign grid + pixel positions in place; return required page height."""
    if not visuals:
        return CANVAS_HEIGHT

    top = [
        v
        for v in visuals
        if v.layout_hint == "top"
        or (v.layout_hint is None and v.type in (VisualType.card, VisualType.slicer))
    ]
    bottom = [v for v in visuals if v.layout_hint == "bottom" and v not in top]
    main = [v for v in visuals if v not in top and v not in bottom]

    row = 0
    for chunk in _chunks(top, 4):
        span = GRID_COLS // len(chunk)
        for i, v in enumerate(chunk):
            v.grid = GridPos(col=i * span, row=row, col_span=span, row_span=1)
        row += 1

    left = next((v for v in main if v.layout_hint in ("left", "wide")), None)
    if left is not None and len(main) >= 2:
        others = [v for v in main if v is not left]
        region_rows = max(_MAIN_ROW_SPAN, _MAIN_ROW_SPAN * len(others))
        left.grid = GridPos(col=0, row=row, col_span=8, row_span=region_rows)
        each = region_rows // len(others)
        r = row
        for i, v in enumerate(others):
            span = each if i < len(others) - 1 else region_rows - each * (len(others) - 1)
            v.grid = GridPos(col=8, row=r, col_span=4, row_span=span)
            r += span
        row += region_rows
    else:
        for chunk in _chunks(main, 2):
            span = GRID_COLS // len(chunk)
            for i, v in enumerate(chunk):
                v.grid = GridPos(col=i * span, row=row, col_span=span, row_span=_MAIN_ROW_SPAN)
            row += _MAIN_ROW_SPAN

    for v in bottom:
        v.grid = GridPos(col=0, row=row, col_span=GRID_COLS, row_span=_MAIN_ROW_SPAN)
        row += _MAIN_ROW_SPAN

    for v in visuals:
        assert v.grid is not None
        v.position = PixelPos.from_grid(v.grid)

    return max(CANVAS_HEIGHT, row * GRID_ROW_HEIGHT)
