"""Draw the rhythm sheet PNG and its Markdown companion from a model dict.

Layout is designed in 2400 x 1350 pixels and drawn at 2x, then downsampled,
so strokes and monospace labels stay crisp. Colours follow the Astrid paper
palette: paper background, ink text, rust and orange accents.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Mapping, Sequence

from PIL import Image, ImageDraw, ImageFont

W, H = 2400, 1350
SS = 2
PAPER = (247, 244, 237)
INK = (37, 36, 31)
RUST = (169, 71, 20)
ORANGE = (237, 107, 35)
INK_SOFT = (97, 94, 85)
SAND = (217, 210, 195)
SAND_LIGHT = (232, 226, 213)
SILENT = (150, 144, 131)
KIND_FILL = {"presenter": RUST, "illustrative": ORANGE, "silent": SILENT, "joke": SILENT}
CHAPTER_FILL = (INK, RUST, ORANGE, INK_SOFT)
MONO_FONTS = (
    "/System/Library/Fonts/SFNSMono.ttf",
    "/System/Library/Fonts/Menlo.ttc",
    "/System/Library/Fonts/Supplemental/Courier New.ttf",
    "/Library/Fonts/PTMono.ttc",
    "/System/Library/Fonts/Monaco.ttf",
)

MARGIN_X = 64
TX0, TX1 = 196, 2336
CH_Y0, CH_Y1 = 172, 226
RULER_Y = 240
CUT_Y0, CUT_Y1 = 304, 560
SPEECH_Y0, SPEECH_Y1 = 612, 712
SIL_Y0, SIL_Y1 = 718, 730
ENERGY_Y0, ENERGY_Y1 = 782, 872
MUSIC_Y0, MUSIC_Y1 = 918, 940
SUM_Y0, SUM_Y1 = 966, 1262
FOOT_Y = 1296
LOG_MIN, LOG_MAX = 0.5, 16.0
LOG_TICKS = (0.5, 1.0, 2.0, 4.0, 8.0, 16.0)
TARGET_LOW, TARGET_HIGH = 1.5, 4.0

_FONTS: dict[int, ImageFont.FreeTypeFont | ImageFont.ImageFont] = {}


def _font(size: float) -> Any:
    key = round(size * SS)
    if key not in _FONTS:
        for path in MONO_FONTS:
            if Path(path).is_file():
                _FONTS[key] = ImageFont.truetype(path, key)
                break
        else:
            _FONTS[key] = ImageFont.load_default(size=key)
    return _FONTS[key]


def _text_width(text: str, size: float) -> float:
    return _font(size).getlength(text) / SS


def _fit(text: str, size: float, max_width: float) -> str:
    if _text_width(text, size) <= max_width:
        return text
    trimmed = text
    while trimmed and _text_width(trimmed + "…", size) > max_width:
        trimmed = trimmed[:-1]
    return (trimmed + "…") if trimmed else ""


def _seconds(value: float) -> str:
    return f"{value:.2f} s" if value < 10 else f"{value:.1f} s"


class _Canvas:
    def __init__(self) -> None:
        self.image = Image.new("RGB", (W * SS, H * SS), PAPER)
        self.draw = ImageDraw.Draw(self.image, "RGBA")

    @staticmethod
    def _p(*values: float) -> list[int]:
        return [round(value * SS) for value in values]

    def rect(self, x0: float, y0: float, x1: float, y1: float, fill: tuple | None = None, outline: tuple | None = None, width: float = 1.0) -> None:
        self.draw.rectangle(
            self._p(x0, y0, x1, y1),
            fill=fill,
            outline=outline,
            width=round(width * SS) if outline else 0,
        )

    def line(self, points: Sequence[tuple[float, float]], fill: tuple, width: float = 1.0) -> None:
        if len(points) < 2:
            return
        flat = [coord for point in points for coord in self._p(*point)]
        self.draw.line(flat, fill=fill, width=max(1, round(width * SS)), joint="curve")

    def dashed(self, points: Sequence[tuple[float, float]], fill: tuple, width: float = 1.0, dash: float = 9.0, gap: float = 6.0) -> None:
        for (x0, y0), (x1, y1) in zip(points, points[1:]):
            length = math.hypot(x1 - x0, y1 - y0)
            if length == 0:
                continue
            step = dash + gap
            travelled = 0.0
            while travelled < length:
                end = min(travelled + dash, length)
                t0, t1 = travelled / length, end / length
                self.line(
                    [(x0 + (x1 - x0) * t0, y0 + (y1 - y0) * t0), (x0 + (x1 - x0) * t1, y0 + (y1 - y0) * t1)],
                    fill,
                    width,
                )
                travelled += step

    def polygon(self, points: Sequence[tuple[float, float]], fill: tuple) -> None:
        self.draw.polygon([coord for point in points for coord in self._p(*point)], fill=fill)

    def text(self, x: float, y: float, value: str, size: float, fill: tuple = INK, anchor: str = "ls") -> None:
        colour = fill if len(fill) == 4 else (*fill, 255)
        self.draw.text((x * SS, y * SS), value, font=_font(size), fill=colour, anchor=anchor)

    def save(self, path: Path) -> None:
        self.image.resize((W, H), Image.Resampling.LANCZOS).save(path, format="PNG", optimize=True)


def _label_ink(fill: tuple) -> tuple:
    return PAPER if fill in (INK, RUST, INK_SOFT) else INK


def _window_scale(model: Mapping[str, Any]):
    low, high = (float(value) for value in model["window_s"])
    span = max(high - low, 1e-9)

    def x_of(t: float) -> float:
        return TX0 + (t - low) / span * (TX1 - TX0)

    return low, high, x_of


def _log_y(duration: float) -> float:
    clamped = min(max(duration, LOG_MIN), LOG_MAX)
    fraction = (math.log2(clamped) - math.log2(LOG_MIN)) / (math.log2(LOG_MAX) - math.log2(LOG_MIN))
    return CUT_Y1 - fraction * (CUT_Y1 - CUT_Y0)


def _lane_y(value: float, top: float, bottom: float, scale_max: float) -> float:
    fraction = min(max(value / scale_max, 0.0), 1.0) if scale_max > 0 else 0.0
    return bottom - fraction * (bottom - top)


def _header(c: _Canvas, model: Mapping[str, Any]) -> None:
    timeline = model.get("timeline") or {}
    summary = model["summary"]
    c.text(MARGIN_X, 58, "RHYTHM SHEET", 40, INK)
    head = str(timeline.get("head_revision_id") or "")
    head = head.split("-")[-1][:10] if head else "unknown"
    canvas = timeline.get("canvas") or {}
    canvas_text = f"{canvas.get('width', 1920)}x{canvas.get('height', 1080)} {canvas.get('fps', 30)}fps"
    subline = (
        f"{timeline.get('project') or 'project'} · {timeline.get('ref') or timeline.get('timeline_id') or 'timeline'}"
        f" · head {head} · {canvas_text} · {summary['duration_s']:.2f} s · {summary['cut_count']} cuts"
    )
    c.text(MARGIN_X, 96, _fit(subline, 21, 1500), 21, INK_SOFT)

    legend = [
        ("swatch", RUST, "presenter"),
        ("swatch", ORANGE, "illustrative"),
        ("swatch", SILENT, "silent / joke"),
        ("band", None, "target 1.5-4 s"),
        ("dash", None, "median"),
    ]
    widths = [16 + 10 + _text_width(label, 21) + 34 for _kind, _fill, label in legend]
    x = TX1 - sum(widths) + 34
    for (kind, fill, label), width in zip(legend, widths):
        if kind == "swatch":
            c.rect(x, 52, x + 16, 68, fill=fill)
        elif kind == "band":
            c.rect(x, 52, x + 16, 68, fill=(*INK, 26), outline=INK_SOFT, width=1)
        else:
            c.dashed([(x, 60), (x + 16, 60)], INK, width=2, dash=5, gap=3)
        c.text(x + 26, 68, label, 21, INK)
        x += width


def _chapters(c: _Canvas, model: Mapping[str, Any], x_of, low: float, high: float) -> None:
    c.text(MARGIN_X, CH_Y0 - 14, "CHAPTERS", 18, INK_SOFT)
    for index, chapter in enumerate(model["chapters"]):
        x0, x1 = x_of(chapter["start"]), x_of(chapter["end"])
        if x1 - x0 < 2:
            continue
        fill = CHAPTER_FILL[index % len(CHAPTER_FILL)]
        c.rect(x0 + 1, CH_Y0, x1 - 1, CH_Y1, fill=fill)
        ink = _label_ink(fill)
        width = x1 - x0 - 24
        duration = chapter["end"] - chapter["start"]
        c.text(x0 + 12, CH_Y0 + 26, _fit(chapter["name"], 24, width), 24, ink)
        c.text(x0 + 12, CH_Y0 + 46, _fit(f"{duration:.1f} s · {chapter['speech']}", 17, width), 17, ink)


def _ruler(c: _Canvas, low: float, high: float, x_of) -> None:
    c.line([(TX0, RULER_Y), (TX1, RULER_Y)], INK, 2)
    t = math.ceil(low / 5.0 - 1e-9) * 5.0
    labels: list[tuple[float, float]] = []
    while t <= high + 1e-9:
        x = x_of(t)
        is_label = abs(t / 15.0 - round(t / 15.0)) < 1e-9
        c.line([(x, RULER_Y - (12 if is_label else 7)), (x, RULER_Y)], INK, 2 if is_label else 1.5)
        if is_label:
            labels.append((t, x))
        t += 5.0
    for t, x in labels:
        c.text(x + 4, RULER_Y + 24, f"{round(t)}s", 20, INK, anchor="ls")
    if high - low > 0 and (not labels or abs(labels[-1][0] - high) > 2.0):
        c.text(x_of(high) - 4, RULER_Y + 24, f"{high:.1f}s", 20, INK, anchor="rs")
    c.text(MARGIN_X, RULER_Y + 5, "time", 18, INK_SOFT)


def _gridlines(c: _Canvas, low: float, high: float, x_of) -> None:
    t = math.ceil(low / 15.0 - 1e-9) * 15.0
    while t <= high + 1e-9:
        x = x_of(t)
        c.line([(x, RULER_Y), (x, MUSIC_Y1)], SAND_LIGHT, 1.5)
        t += 15.0


def _cut_lane(c: _Canvas, model: Mapping[str, Any], x_of) -> None:
    summary = model["summary"]
    cuts = model["cuts"]
    c.text(MARGIN_X, CUT_Y0 - 12, "CUT LENGTH · bar height = duration, log scale", 18, INK_SOFT)
    for tick in LOG_TICKS:
        y = _log_y(tick)
        c.line([(TX0, y), (TX1, y)], SAND_LIGHT, 1)
        c.text(TX0 - 14, y + 7, f"{tick:g}s", 19, INK_SOFT, anchor="rs")
    top, bottom = _log_y(TARGET_HIGH), _log_y(TARGET_LOW)
    c.rect(TX0, top, TX1, bottom, fill=(*INK, 16))
    for cut in cuts:
        x0, x1 = x_of(cut["start"]) + 2, x_of(cut["end"]) - 2
        if x1 - x0 < 2:
            x1 = x0 + 2
        y = min(_log_y(cut["duration"]), CUT_Y1 - 6)
        fill = KIND_FILL.get(cut["kind"], INK)
        c.rect(x0, y, x1, CUT_Y1, fill=fill)
        width = x1 - x0
        ink = _label_ink(fill) if fill != SILENT else INK
        if width >= 120:
            c.text(x0 + 14, y + 30, f"#{cut['index']}  {_seconds(cut['duration'])}", 22, ink)
        elif width >= 40:
            c.text(x0 + 10, y + 28, f"#{cut['index']}", 22, ink)
    median = summary.get("median_cut_s")
    if median:
        y = _log_y(median)
        c.dashed([(TX0, y), (TX1, y)], INK, width=2.5, dash=12, gap=8)
        label = f"median {median:.2f} s"
        label_width = _text_width(label, 20) + 16
        c.rect(TX1 - label_width - 8, y - 34, TX1 - 8, y - 6, fill=PAPER)
        c.text(TX1 - 16, y - 13, label, 20, INK, anchor="rs")
    c.line([(TX0, CUT_Y1), (TX1, CUT_Y1)], INK, 2)
    c.text(TX1 - 12, top + 26, "target 1.5-4 s", 19, INK_SOFT, anchor="rs")


def _beat_lines(c: _Canvas, model: Mapping[str, Any], x_of, bottom: float) -> None:
    beats = model.get("beats")
    if not beats:
        return
    for beat in beats:
        x = x_of(beat)
        c.line([(x, CUT_Y0), (x, bottom)], (*INK, 46), 1)


def _speech_lane(c: _Canvas, model: Mapping[str, Any], x_of) -> None:
    grid = model["grid"]
    times = grid["t"]
    values = grid["words_per_s"]
    top_value = max(3.0, math.ceil(max(values, default=0.0)))
    c.text(MARGIN_X, SPEECH_Y0 - 12, "SPEECH · words/s over 2 s · grey strip = silence >= 0.4 s", 18, INK_SOFT)
    for value in range(0, int(top_value) + 1):
        y = _lane_y(value, SPEECH_Y0, SPEECH_Y1, top_value)
        c.line([(TX0, y), (TX1, y)], SAND_LIGHT, 1)
        c.text(TX0 - 14, y + 7, f"{value}", 19, INK_SOFT, anchor="rs")
    stall_y = _lane_y(1.0, SPEECH_Y0, SPEECH_Y1, top_value)
    c.dashed([(TX0, stall_y), (TX1, stall_y)], INK_SOFT, width=1.5, dash=6, gap=6)
    points = [(x_of(t), _lane_y(v, SPEECH_Y0, SPEECH_Y1, top_value)) for t, v in zip(times, values)]
    if points:
        base = SPEECH_Y1
        polygon = [(points[0][0], base)] + points + [(points[-1][0], base)]
        c.polygon(polygon, (*INK, 44))
        c.line(points, INK, 3)
    c.line([(TX0, SPEECH_Y1), (TX1, SPEECH_Y1)], INK, 2)
    c.text(MARGIN_X, SIL_Y1 - 2, "gaps", 18, INK_SOFT)
    for start, end in model["silences"]:
        x0, x1 = x_of(start), x_of(end)
        c.rect(x0, SIL_Y0, x1, SIL_Y1, fill=SAND, outline=INK_SOFT, width=1)
    if not model["silences"]:
        c.text(TX0 + 10, SIL_Y1 - 2, "no silence of 0.4 s or more", 17, INK_SOFT)


def _energy_lane(c: _Canvas, model: Mapping[str, Any], x_of) -> None:
    grid = model["grid"]
    times = grid["t"]
    rates = grid["cuts_per_10s"]
    norm = grid["words_per_s_norm"]
    top_rate = max(4.0, math.ceil(max(rates, default=0.0)))
    c.text(MARGIN_X, ENERGY_Y0 - 12, "ENERGY · cuts per 10 s (orange) vs speech, normalised (ink dashed)", 18, INK_SOFT)
    for value in (0, 2, int(top_rate)):
        y = _lane_y(value, ENERGY_Y0, ENERGY_Y1, top_rate)
        c.line([(TX0, y), (TX1, y)], SAND_LIGHT, 1)
        c.text(TX0 - 14, y + 7, f"{value}", 19, INK_SOFT, anchor="rs")
    rate_points = [(x_of(t), _lane_y(v, ENERGY_Y0, ENERGY_Y1, top_rate)) for t, v in zip(times, rates)]
    speech_points = [(x_of(t), _lane_y(v, ENERGY_Y0, ENERGY_Y1, 1.0)) for t, v in zip(times, norm)]
    if speech_points:
        c.dashed(speech_points, INK, width=3, dash=10, gap=7)
    if rate_points:
        c.line(rate_points, ORANGE, 5)
    c.line([(TX0, ENERGY_Y1), (TX1, ENERGY_Y1)], INK, 2)


def _music_lane(c: _Canvas, model: Mapping[str, Any], x_of) -> None:
    c.text(MARGIN_X, MUSIC_Y0 - 12, "MUSIC · beats", 18, INK_SOFT)
    if model.get("beats") is None:
        c.text(TX0 + 10, MUSIC_Y1 - 6, "no beats linked (timeline app.beats)", 18, INK_SOFT)
        return
    c.line([(TX0, MUSIC_Y1), (TX1, MUSIC_Y1)], SAND, 1.5)
    for beat in model["beats"]:
        x = x_of(beat)
        c.line([(x, MUSIC_Y0), (x, MUSIC_Y1)], INK, 2)


def _summary_box(c: _Canvas, model: Mapping[str, Any]) -> None:
    s = model["summary"]
    x0, x1 = MARGIN_X, W - MARGIN_X
    c.rect(x0, SUM_Y0, x1, SUM_Y1, outline=INK, width=2)
    c.text(x0 + 28, SUM_Y0 + 38, "SUMMARY", 21, INK)
    col_w = (x1 - x0 - 56) / 4
    holds = s.get("longest_holds") or []
    dens = s.get("densest_window") or {}
    stalls = [st for st in model.get("stalls", [])]
    kinds = s.get("cut_kinds") or {}
    word_sources = s.get("word_sources") or {}
    window = model["window_s"]
    full = abs(window[0]) < 1e-9 and abs(window[1] - s["duration_s"]) < 1e-3

    median = s.get("median_cut_s")
    mean = s.get("mean_cut_s")
    target = s.get("target_share")
    in_target = round((target or 0.0) * s["cut_count"]) if target is not None else 0
    presenter_cuts = kinds.get("presenter", 0)
    illus = kinds.get("illustrative", 0)
    silent = kinds.get("silent", 0) + kinds.get("joke", 0)
    hold_text = " · ".join(f"{h['clip_id'].split('-')[0]} {h['duration']:.2f}s" for h in holds) or "none"
    stall_detail = "none"
    if stalls:
        first = stalls[0]
        label = first.get("clip_id") or "speech"
        stall_detail = f"{str(label).split('-')[0]} {first['duration']:.2f} s, {first['kind']}"
    speech_text = " + ".join(f"{count} {src}" for src, count in word_sources.items()) or "none"

    blocks = [
        (
            "DURATION",
            f"{s['duration_s']:.2f} s",
            "full timeline" if full else f"window {window[0]:.2f}-{window[1]:.2f} s",
        ),
        (
            "CUTS",
            f"{s['cut_count']}",
            f"{presenter_cuts} presenter · {illus} illustr. · {silent} silent/joke",
        ),
        (
            "CUT LENGTH",
            f"median {median:.2f} s" if median is not None else "no cuts",
            f"mean {mean:.2f} s · target 1.5-4 s: {in_target} of {s['cut_count']}" if mean is not None else "",
        ),
        (
            "PRESENTER",
            f"{round(s['presenter_share'] * 100)}%",
            "of screen time",
        ),
        (
            "LONGEST HOLDS",
            " · ".join(f"{h['duration']:.2f}" for h in holds) + " s" if holds else "none",
            hold_text,
        ),
        (
            "DENSEST 10 s",
            f"{dens.get('words_per_s', 0):.2f} w/s" if dens else "none",
            (
                f"{dens.get('start', 0):.1f}-{dens.get('end', 0):.1f} s"
                if s["window_span_s"] > 10
                else "whole span (under 10 s)"
            )
            if dens
            else "",
        ),
        (
            "STALLS",
            f"{s['stall_count']}",
            stall_detail,
        ),
        (
            "SPEECH",
            f"{s['word_count']} words",
            speech_text,
        ),
    ]
    for index, (label, value, sub) in enumerate(blocks):
        column, row = index % 4, index // 4
        bx = x0 + 28 + column * col_w
        by = SUM_Y0 + 64 + row * 118
        c.text(bx, by, label, 18, INK_SOFT)
        c.text(bx, by + 44, _fit(value, 40, col_w - 24), 40, INK)
        c.text(bx, by + 74, _fit(sub, 19, col_w - 24), 19, INK_SOFT)


def _footer(c: _Canvas, model: Mapping[str, Any]) -> None:
    s = model["summary"]
    speech = " + ".join(f"{count} {src}" for src, count in (s.get("word_sources") or {}).items()) or "none"
    lines = [
        "Cut = plate-track clip. Kind = app.kind, else am-presenter overlap, else silent (no speech), else illustrative. Bar height = log2 duration, 0.5-16 s.",
        f"Speech words: {speech}. Density = words whose midpoint lies within 1 s of each point, over 2 s. Stall = cut > 6 s without a deliberate flag, or < 1 w/s for > 6 s.",
    ]
    for index, line in enumerate(lines):
        c.text(MARGIN_X, FOOT_Y + index * 22, _fit(line, 17, W - 2 * MARGIN_X), 17, INK_SOFT)


def render_png(model: Mapping[str, Any], path: Path) -> None:
    """Draw the rhythm sheet at 2400 x 1350 and write it to ``path``."""
    c = _Canvas()
    low, high, x_of = _window_scale(model)
    _header(c, model)
    _gridlines(c, low, high, x_of)
    _chapters(c, model, x_of, low, high)
    _ruler(c, low, high, x_of)
    _cut_lane(c, model, x_of)
    _beat_lines(c, model, x_of, CUT_Y1)
    _speech_lane(c, model, x_of)
    _energy_lane(c, model, x_of)
    _music_lane(c, model, x_of)
    _summary_box(c, model)
    _footer(c, model)
    c.save(path)


def render_markdown(model: Mapping[str, Any], *, png_name: str = "pacing.png") -> str:
    """Markdown companion: summary, cut table, stalls and method notes."""
    s = model["summary"]
    timeline = model.get("timeline") or {}
    title = timeline.get("ref") or timeline.get("timeline_id") or "timeline"
    lines = [
        f"# Rhythm sheet: {title}",
        "",
        f"![rhythm sheet]({png_name})",
        "",
        "## Summary",
        "",
        "| Measure | Value |",
        "|---|---|",
        f"| Duration | {s['duration_s']:.2f} s (window {model['window_s'][0]:.2f}-{model['window_s'][1]:.2f} s) |",
        f"| Cuts | {s['cut_count']} ({', '.join(f'{k} {v}' for k, v in s['cut_kinds'].items()) or 'none'}) |",
        f"| Median / mean cut | {_md(s.get('median_cut_s'))} s / {_md(s.get('mean_cut_s'))} s |",
        f"| Presenter share (time) | {round(s['presenter_share'] * 100)}% |",
        f"| Cuts in 1.5-4 s target | {_md_pct(s.get('target_share'))} |",
        f"| Densest 10 s | {_densest_text(s.get('densest_window'))} |",
        f"| Stalls | {s['stall_count']} |",
        f"| Words | {s['word_count']} ({', '.join(f'{k} {v}' for k, v in s['word_sources'].items()) or 'none'}) |",
        "",
        "## Cuts",
        "",
        "| # | Chapter | Start | Duration | Kind | Speech (s) | Stall |",
        "|---|---|---|---|---|---|---|",
    ]
    for cut in model["cuts"]:
        lines.append(
            f"| {cut['index']} | {cut['chapter']} | {cut['start']:.2f} | {cut['duration']:.2f} s | "
            f"{cut['kind']} ({cut['kind_source']}) | {cut['speech_seconds']:.2f} | {'yes' if cut['stall'] else ''} |"
        )
    lines += ["", "## Stalls", ""]
    if model["stalls"]:
        lines += [f"- {st['kind']} {st['start']:.2f}-{st['end']:.2f} s: {st['note']}" for st in model["stalls"]]
    else:
        lines.append("- none")
    lines += ["", "## Silences (>= 0.4 s)", ""]
    lines += [f"- {a:.2f}-{b:.2f} s" for a, b in model["silences"]] or ["- none"]
    lines += ["", "## Method", ""]
    lines += [
        "- Cuts are plate-track clips; a shot with no plate clip is one cut.",
        "- Kind: app.kind when set, else am-presenter overlap, else silent when no speech overlaps, else illustrative.",
        "- Word timings: app.words on a VO clip (relative to clip start), else params.words on a shot element, else the text-binding word count spread over VO clips without timings.",
        "- Density: words whose midpoint lies within +/-1 s of each 0.1 s grid point, divided by 2 s.",
        "- Stall: a cut longer than 6 s without a deliberate flag, or a stretch over 6 s under 1 word/s outside deliberate holds.",
    ]
    lines += [f"- {note}" for note in model.get("notes", [])]
    lines.append("")
    return "\n".join(lines)


def _md(value: Any) -> str:
    return "n/a" if value is None else f"{value:.2f}"


def _md_pct(value: Any) -> str:
    return "n/a" if value is None else f"{round(value * 100)}%"


def _densest_text(window: Any) -> str:
    if not window:
        return "n/a"
    return f"{window['words_per_s']:.2f} w/s from {window['start']:.2f} to {window['end']:.2f} s"
