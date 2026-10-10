"""Measured text boxes for the type elements: how wide a line of am-type really is.

The renderer sets type in the browser (Gelasio for ``display``, Departure Mono for ``label``,
uppercase, with letter spacing in em, ``white-space: pre-wrap`` inside ``width``). This module
measures the same text with the same fonts' advance widths, so a typed record, lint and the
visualize highlight can say where the text actually ends, and which size fits. Kerning is
ignored (it moves a line by a few px at most).

Pure apart from reading the bundled font files once (``remotion/public/fonts``). Without them
(or without fontTools) ``available()`` is False and callers fall back to their estimates.
"""
from __future__ import annotations

import functools
from pathlib import Path
from typing import Any

FONT_DIR = Path(__file__).resolve().parents[3] / "remotion" / "public" / "fonts"
FONTS = {"display": "Gelasio-Regular.woff2", "italic": "Gelasio-Italic.woff2", "label": "DepartureMono-Regular.woff2"}
DEFAULTS = {  # what am-type uses when a param is not set (component.tsx)
    "display": {"size": 96.0, "tracking": -0.01, "line_height": 0.95},
    "label": {"size": 28.0, "tracking": 0.12, "line_height": 1.3},
}


@functools.lru_cache(maxsize=None)
def _metrics(name: str) -> tuple[dict[int, str], dict[str, int], int] | None:
    try:
        from fontTools.ttLib import TTFont

        font = TTFont(str(FONT_DIR / FONTS[name]))
        return dict(font.getBestCmap()), {g: adv for g, (adv, _lsb) in font["hmtx"].metrics.items()}, int(font["head"].unitsPerEm)
    except Exception:  # noqa: BLE001 - no fonts here: callers estimate instead
        return None


def available(font: str = "display") -> bool:
    return _metrics(font) is not None


def line_width(text: str, *, font: str = "display", size: float, tracking: float | None = None) -> float:
    """Canvas px of one line (letter spacing follows every character, as CSS does)."""
    metrics = _metrics(font)
    if metrics is None:
        raise RuntimeError("the type fonts are not available here")
    cmap, advances, upm = metrics
    tracking = DEFAULTS["label" if font == "label" else "display"]["tracking"] if tracking is None else tracking
    if font == "label":
        text = text.upper()
    fallback = advances.get(cmap.get(ord("n"), ""), upm // 2)
    units = sum(advances.get(cmap.get(ord(ch), ""), fallback) for ch in text)
    return units * size / upm + len(text) * tracking * size


def layout(text: str, *, font: str = "display", size: float | None = None, width: float = 1200.0,
           tracking: float | None = None, line_height: float | None = None) -> dict[str, Any]:
    """``{lines, widths, w, h, overflow}``: the wrapped lines (pre-wrap: newlines kept, words never broken),
    each line's width, the box (widest line × lines × line height) and how far an unbreakable word runs
    past ``width`` (0 when it fits)."""
    kind = "label" if font == "label" else "display"
    size = float(size or DEFAULTS[kind]["size"])
    line_height = float(line_height or DEFAULTS[kind]["line_height"])
    lines: list[str] = []
    for paragraph in str(text or "").split("\n"):
        current = ""
        for word in paragraph.split(" "):
            trial = f"{current} {word}" if current else word
            if current and line_width(trial, font=kind, size=size, tracking=tracking) > width + 0.5:
                lines.append(current)
                current = word
            else:
                current = trial
        lines.append(current)
    widths = [line_width(line, font=kind, size=size, tracking=tracking) for line in lines]
    widest = max(widths, default=0.0)
    return {"lines": lines, "widths": widths, "w": widest, "h": len(lines) * size * line_height,
            "overflow": max(0.0, widest - width), "size": size}


def max_size(text: str, *, font: str = "display", width: float, lines: int = 1, tracking: float | None = None,
             line_height: float | None = None) -> float:
    """The largest size (whole px) at which the text fits ``width`` in at most ``lines`` lines."""
    lo, hi = 1.0, 1000.0
    while hi - lo > 0.5:
        mid = (lo + hi) / 2
        got = layout(text, font=font, size=mid, width=width, tracking=tracking, line_height=line_height)
        if len(got["lines"]) <= lines and got["overflow"] <= 0:
            lo = mid
        else:
            hi = mid
    return float(int(lo))


def type_box(params: dict[str, Any]) -> dict[str, Any] | None:
    """An am-type's measured box from its params: ``{x0, y0, x1, y1, lines, w, h, overflow, size}`` in canvas
    px, or None when the fonts are not here."""
    font = "label" if params.get("font") == "label" else "display"
    if not available(font):
        return None
    size = _num(params.get("size"), DEFAULTS[font]["size"])
    width = _num(params.get("width"), 1200.0)
    got = layout(str(params.get("text") or ""), font=font, size=size, width=width,
                 tracking=_num(params.get("tracking"), DEFAULTS[font]["tracking"]),
                 line_height=_num(params.get("lineHeight"), DEFAULTS[font]["line_height"]))
    x, y = _num(params.get("x"), 96.0), _num(params.get("y"), 96.0)
    align = str(params.get("align") or "left")
    used = min(got["w"], width) if not got["overflow"] else got["w"]
    x0 = x + (width - used) / 2 if align == "center" else x + (width - used) if align == "right" else x
    return {"x0": x0, "y0": y, "x1": x0 + used, "y1": y + got["h"], **got}


def _num(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)
