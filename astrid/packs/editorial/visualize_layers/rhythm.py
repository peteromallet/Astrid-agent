"""``rhythm``: the cut's length against its neighbours and the pacing target.

Contributed by the editorial pack the way any pack adds a visualize layer:
this module defines ``LAYER`` and uses only the public layer interface
(``LayerContext``: the cut, every cut, a panel factory) plus this pack's own
pacing constants. Nothing in the rendering pack names it.
"""
from __future__ import annotations

import statistics

from PIL import ImageDraw

from astrid.packs.editorial.executors.pacing.model import TARGET_RANGE
from astrid.packs.rendering.executors.timeline_visualize.layers import Layer, LayerResult
from astrid.packs.rendering.executors.timeline_visualize.layers.base import PALETTE, draw_text

NEIGHBOURS = 6


def render(ctx) -> LayerResult:
    index = int(ctx.cut["index"])
    cuts = [c for c in ctx.cuts if abs(int(c["index"]) - index) <= NEIGHBOURS]
    image = ctx.panel(120, title="rhythm", axis=False)
    draw = ImageDraw.Draw(image)
    low, high = TARGET_RANGE
    longest = max(float(c["duration"]) for c in cuts) or 1.0
    top, bottom = 34, 104
    left, right = ctx.plot_left, ctx.plot_right
    width = (right - left) / max(1, len(cuts))

    def y_of(seconds: float) -> float:
        return bottom - min(1.0, seconds / max(longest, high)) * (bottom - top)

    draw.rectangle((left, y_of(high), right, y_of(low)), fill="#1d3a2f")
    draw_text(draw, (10, 30), f"target {low:g}–{high:g} s", 11, PALETTE["muted"])
    for position, cut in enumerate(cuts):
        x0 = left + position * width + 3
        seconds = float(cut["duration"])
        colour = PALETTE["cut"] if int(cut["index"]) == index else (
            PALETTE["good"] if low <= seconds <= high else PALETTE["warn"])
        draw.rectangle((x0, y_of(seconds), x0 + width - 6, bottom), fill=colour)
        draw_text(draw, (x0, bottom + 2), f"#{cut['index']} {seconds:.1f}s", 11, PALETTE["ink"])
    durations = [float(c["duration"]) for c in cuts]
    seconds = float(ctx.cut["duration"])
    rank = sorted(durations, reverse=True).index(seconds) + 1
    verdict = "in range" if low <= seconds <= high else ("long" if seconds > high else "short")
    finding = (f"RHYTHM cut {index} is {seconds:.2f} s ({verdict}; target {low:g}–{high:g} s), "
               f"#{rank} longest of cuts {cuts[0]['index']}–{cuts[-1]['index']} (median {statistics.median(durations):.2f} s)")
    return LayerResult(image, [finding], "rhythm")


LAYER = Layer("rhythm", "this cut's length against its neighbours and the pacing target (editorial pack)",
              render, needs=("doc",), order=15)
