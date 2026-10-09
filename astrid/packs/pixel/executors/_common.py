"""Shared Pillow + numpy algorithms for the pixel pack.

Pure functions only: no runtime state, no network, no subprocesses. The two
executors (``pixel.snap`` and ``pixel.cutout``) import these helpers.
"""

from __future__ import annotations

import ast
import json
import re
from collections import deque
from typing import Any, Mapping

import numpy as np
from PIL import Image, ImageDraw

from astrid.core.contracts.errors import AstridError

# Brand palette from the Astrid style guide, plus pure white and black so
# neutral backgrounds and outlines remain representable.
ASTRID_PALETTE_HEX: tuple[str, ...] = (
    "F7F4ED", "FFFEFA", "F4E9D7", "F5DCC7", "25241F", "151311", "615E55", "A94714",
    "ED6B23", "FF7A2E", "D9531E", "F7E6CF", "E5E1DA", "334155", "1F1F1F", "FFFBF2",
    "FFFFFF", "000000",
)
PALETTE_PRESETS: dict[str, tuple[str, ...]] = {"astrid": ASTRID_PALETTE_HEX}

# Colour-change threshold (max channel delta between neighbours) that counts as
# an edge when detecting the pixel lattice. JPEG-style noise stays below this.
EDGE_DELTA = 24
# Quantisation step for mode voting: neighbours within one bin vote together.
MODE_BIN = 16
# Minimum lattice coherence (|mean phasor| over edges) to accept a block size.
LATTICE_POWER = 0.85
LATTICE_MIN_EDGES = 8
_HEX_RE = re.compile(r"^#?([0-9a-fA-F]{6})$")
_CROP_KEYS = ("x", "y", "width", "height")


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------


def parse_hex(value: Any) -> tuple[int, int, int]:
    match = _HEX_RE.match(str(value).strip())
    if match is None:
        raise AstridError(
            f"invalid hex colour {value!r}",
            recovery_command="use a colour such as #FF00FF",
        )
    digits = match.group(1)
    return (int(digits[0:2], 16), int(digits[2:4], 16), int(digits[4:6], 16))


def hex_of(rgb: Any) -> str:
    r, g, b = (int(round(float(c))) for c in list(rgb)[:3])
    return f"#{r:02X}{g:02X}{b:02X}"


def parse_palette(value: Any) -> tuple[str, np.ndarray, str] | None:
    """Return (source, colours uint8 (n,3), label) or None when no palette is set."""
    if value is None:
        return None
    if isinstance(value, (list, tuple)):
        items: list[Any] = list(value)
        source = "custom"
    else:
        text = str(value).strip()
        if not text or text.lower() in {"none", "off", "false"}:
            return None
        if text.lower() in PALETTE_PRESETS:
            items = list(PALETTE_PRESETS[text.lower()])
            source = f"preset:{text.lower()}"
        else:
            items = [part for part in re.split(r"[,\s]+", text) if part]
            source = "custom"
    colours = [parse_hex(item) for item in items]
    unique = list(dict.fromkeys(colours))
    if len(unique) < 2:
        raise AstridError(
            "palette needs at least two distinct colours",
            recovery_command="pass a preset such as astrid, or a comma-separated list of hex colours",
        )
    return source, np.array(unique, dtype=np.uint8), "palette"


def parse_crop(value: Any) -> dict[str, int] | None:
    """Parse a crop given as a mapping, JSON/Python literal, or ``x,y,w,h`` text."""
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
        if not text or text.lower() in {"none", "off", "null"}:
            return None
        parsed: Any
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            try:
                parsed = ast.literal_eval(text)
            except (ValueError, SyntaxError):
                parts = [part for part in re.split(r"[,\s]+", text) if part]
                if len(parts) != 4:
                    raise AstridError(
                        f"crop must be {{x, y, width, height}} or 'x,y,width,height', got {text!r}",
                        recovery_command="pass crop as {\"x\":60,\"y\":190,\"width\":460,\"height\":210}",
                    ) from None
                parsed = dict(zip(_CROP_KEYS, parts))
        if isinstance(parsed, (list, tuple)) and len(parsed) == 4:
            parsed = dict(zip(_CROP_KEYS, parsed))
        value = parsed
    if not isinstance(value, Mapping):
        raise AstridError(
            "crop must be an object with x, y, width and height",
            recovery_command="pass crop as {\"x\":60,\"y\":190,\"width\":460,\"height\":210}",
        )
    missing = [key for key in _CROP_KEYS if key not in value]
    if missing:
        raise AstridError(f"crop is missing {', '.join(missing)}")
    try:
        crop = {key: int(value[key]) for key in _CROP_KEYS}
    except (TypeError, ValueError) as exc:
        raise AstridError("crop x, y, width and height must be integers") from exc
    if crop["x"] < 0 or crop["y"] < 0 or crop["width"] <= 0 or crop["height"] <= 0:
        raise AstridError(
            "crop needs x >= 0, y >= 0, width > 0 and height > 0",
            recovery_command="use crop coordinates in source pixels",
        )
    return crop


def apply_crop(arr: np.ndarray, crop: Mapping[str, int] | None) -> np.ndarray:
    """Crop in source pixels. Always the first step of both executors."""
    if crop is None:
        return arr
    height, width = arr.shape[:2]
    x, y, cw, ch = crop["x"], crop["y"], crop["width"], crop["height"]
    if x + cw > width or y + ch > height:
        raise AstridError(
            f"crop {x},{y},{cw},{ch} exceeds the {width}x{height} source image",
            recovery_command="choose a crop inside the source image bounds",
        )
    return arr[y : y + ch, x : x + cw].copy()


def parse_grid(value: Any) -> tuple[int, int] | str | None:
    """Parse cutout ``grid``: ``auto``, ``WxH``, or None."""
    if value is None:
        return None
    if isinstance(value, Mapping):
        return (int(value["width"]), int(value["height"]))
    text = str(value).strip().lower()
    if not text or text in {"none", "off", "false"}:
        return None
    if text in {"auto", "true"}:
        return "auto"
    match = re.fullmatch(r"(\d+)\s*[x,]\s*(\d+)", text)
    if match is None:
        raise AstridError(
            f"grid must be 'auto' or 'WIDTHxHEIGHT', got {value!r}",
            recovery_command="use grid=auto or grid=320x180",
        )
    return (int(match.group(1)), int(match.group(2)))


def load_rgba(path: str) -> np.ndarray:
    with Image.open(path) as image:
        return np.asarray(image.convert("RGBA"), dtype=np.uint8).copy()


def save_rgba(arr: np.ndarray, path: str) -> None:
    Image.fromarray(arr, mode="RGBA").save(path, format="PNG", optimize=True)


# ---------------------------------------------------------------------------
# Pixel lattice detection
# ---------------------------------------------------------------------------


def _edge_positions(arr: np.ndarray, axis: int) -> tuple[np.ndarray, np.ndarray]:
    """Boundary positions (between p-1 and p) along ``axis`` with contrast weights.

    Each boundary is weighted by the squared contrast of the steps that cross it.
    Block edges are high-contrast; JPEG ringing and anti-aliasing are not, so this
    keeps soft artefacts from pulling the lattice phase off.
    """
    signed = arr.astype(np.int16)
    if axis == 1:
        delta = np.abs(signed[:, 1:, :] - signed[:, :-1, :]).max(axis=2).astype(np.float64)
        contrast = np.where(delta > EDGE_DELTA, delta, 0.0) ** 2
        weights = contrast.sum(axis=0)
        positions = np.arange(1, arr.shape[1])
    else:
        delta = np.abs(signed[1:, :, :] - signed[:-1, :, :]).max(axis=2).astype(np.float64)
        contrast = np.where(delta > EDGE_DELTA, delta, 0.0) ** 2
        weights = contrast.sum(axis=1)
        positions = np.arange(1, arr.shape[0])
    keep = weights > 0
    return positions[keep].astype(np.float64), weights[keep].astype(np.float64)


def detect_lattice(arr: np.ndarray, axis: int) -> dict[str, Any]:
    """Find the dominant block size along one axis from edge phase coherence.

    For every candidate period ``b`` the edge positions are projected onto the
    circle of circumference ``b``. A real block grid puts almost all edges on a
    single phase, so the mean phasor has magnitude near 1. We return the largest
    period still coherent: its divisors are coherent too, but larger multiples
    are not. Periods below 1.5 px, or too few edges, mean "no lattice": block 1.
    """
    length = arr.shape[1] if axis == 1 else arr.shape[0]
    positions, weights = _edge_positions(arr, axis)
    none = {"block": 1.0, "origin": 0.0, "power": 0.0, "edges": int(positions.size), "detected": False}
    if positions.size < LATTICE_MIN_EDGES:
        return none
    total = weights.sum()
    upper = length / 6.0
    if upper <= 1.5:
        return none
    periods = np.geomspace(1.5, upper, 20000)
    power = np.empty(periods.size, dtype=np.float64)
    chunk = 1024
    for start in range(0, periods.size, chunk):
        block = periods[start : start + chunk]
        phasor = (weights[:, None] * np.exp(2j * np.pi * positions[:, None] / block[None, :])).sum(axis=0)
        power[start : start + chunk] = np.abs(phasor) / total
    peak = float(power.max())
    hint_index = int(power.argmax())
    none["hint"] = {"block": round(float(periods[hint_index]), 3), "power": round(peak, 4)}
    if peak < LATTICE_POWER:
        return none
    # Largest period within a small margin of the peak coherence: divisors of the
    # true block are equally coherent, multiples and near-misses drift off.
    coherent = np.flatnonzero(power >= max(LATTICE_POWER, peak - 0.05))
    index = int(coherent[-1])
    period = float(periods[index])
    if abs(period - round(period)) < 0.03:
        period = float(round(period))
    phasor = (weights * np.exp(2j * np.pi * positions / period)).sum()
    # Signed origin in (-period/2, period/2]: a lattice starting at 0 must not read as ~period.
    origin = (float(np.angle(phasor)) / (2 * np.pi)) * period
    origin = ((origin + period / 2) % period) - period / 2
    if abs(origin) < 1e-6:
        origin = 0.0
    return {
        "block": round(period, 4),
        "origin": round(origin, 3),
        "power": round(float(power[index]), 4),
        "edges": int(positions.size),
        "detected": True,
    }


def _cell_edges(length: int, origin: float, block: float) -> list[int]:
    """Cell boundaries along one axis. The leading sliver joins the first cell."""
    count = max(1, int(round((length - origin) / block)))
    edges = [0]
    for k in range(1, count):
        edge = int(round(origin + k * block))
        if edge <= edges[-1] or edge >= length:
            continue
        edges.append(edge)
    edges.append(length)
    return edges


# ---------------------------------------------------------------------------
# Mode downsampling and palettes
# ---------------------------------------------------------------------------


def _bin_keys(arr: np.ndarray) -> np.ndarray:
    """Vote keys: 16-level RGB bins, with -1 for transparent pixels."""
    bins = arr[..., :3].astype(np.int64) // MODE_BIN
    keys = (bins[..., 0] * 256) + (bins[..., 1] * 16) + bins[..., 2]
    return np.where(arr[..., 3] >= 128, keys, -1)


def mode_downsample(arr: np.ndarray, xedges: list[int], yedges: list[int]) -> np.ndarray:
    """Each cell takes the modal colour bin; the output colour is that bin's median.

    Mode, not bilinear, so a 1 px outline survives downsampling as one cell.
    """
    keys = _bin_keys(arr)
    grid_h, grid_w = len(yedges) - 1, len(xedges) - 1
    out = np.zeros((grid_h, grid_w, 4), dtype=np.uint8)
    for j in range(grid_h):
        y0, y1 = yedges[j], yedges[j + 1]
        for i in range(grid_w):
            x0, x1 = xedges[i], xedges[i + 1]
            cell_keys = keys[y0:y1, x0:x1].ravel()
            values, counts = np.unique(cell_keys, return_counts=True)
            winner = values[int(np.argmax(counts))]
            if winner < 0:
                continue
            pixels = arr[y0:y1, x0:x1].reshape(-1, 4)[cell_keys == winner]
            out[j, i, :3] = np.round(np.median(pixels[:, :3], axis=0)).astype(np.uint8)
            out[j, i, 3] = 255
    return out


def _nearest(rgb: np.ndarray, palette: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    diff = rgb[:, None, :] - palette[None, :, :].astype(np.float64)
    dist = np.sqrt((diff * diff).sum(axis=2))
    index = dist.argmin(axis=1)
    return index, dist[np.arange(rgb.shape[0]), index]


def map_to_palette(native: np.ndarray, palette: np.ndarray, dither: bool) -> tuple[np.ndarray, np.ndarray]:
    """Map opaque cells to the nearest palette colour. Returns (out, distances)."""
    out = native.copy()
    opaque = native[..., 3] >= 128
    distances = np.zeros(opaque.sum(), dtype=np.float64)
    if not dither:
        rgb = native[opaque][:, :3].astype(np.float64)
        index, dist = _nearest(rgb, palette)
        out[opaque, :3] = palette[index]
        distances = dist
        return out, distances
    # Floyd-Steinberg error diffusion across the grid, row by row.
    work = native[..., :3].astype(np.float64)
    height, width = opaque.shape
    recorded: list[float] = []
    for y in range(height):
        for x in range(width):
            if not opaque[y, x]:
                continue
            old = work[y, x].copy()
            index, dist = _nearest(old[None, :], palette)
            new = palette[index[0]].astype(np.float64)
            out[y, x, :3] = palette[index[0]]
            recorded.append(float(dist[0]))
            error = old - new
            if x + 1 < width:
                work[y, x + 1] += error * 7 / 16
            if y + 1 < height:
                if x > 0:
                    work[y + 1, x - 1] += error * 3 / 16
                work[y + 1, x] += error * 5 / 16
                if x + 1 < width:
                    work[y + 1, x + 1] += error * 1 / 16
    return out, np.array(recorded, dtype=np.float64)


def kmeans_palette(native: np.ndarray, k: int, iterations: int = 12) -> np.ndarray:
    """Deterministic weighted k-means over opaque cell colours."""
    opaque = native[..., 3] >= 128
    pixels = native[opaque][:, :3]
    if pixels.size == 0:
        raise AstridError("max_colors needs at least one opaque cell")
    unique, counts = np.unique(pixels, axis=0, return_counts=True)
    if unique.shape[0] <= k:
        return unique.astype(np.uint8)
    points = unique.astype(np.float64)
    weights = counts.astype(np.float64)
    centers = [points[int(np.argmax(weights))]]
    while len(centers) < k:
        dist = np.min(
            np.sqrt(((points[:, None, :] - np.array(centers)[None, :, :]) ** 2).sum(axis=2)),
            axis=1,
        )
        centers.append(points[int(np.argmax(dist * weights))])
    centers_arr = np.array(centers)
    for _ in range(iterations):
        dist = np.sqrt(((points[:, None, :] - centers_arr[None, :, :]) ** 2).sum(axis=2))
        assign = dist.argmin(axis=1)
        for c in range(k):
            members = assign == c
            if members.any():
                centers_arr[c] = np.average(points[members], axis=0, weights=weights[members])
    return np.unique(np.round(centers_arr).astype(np.uint8), axis=0)


# ---------------------------------------------------------------------------
# pixel.snap
# ---------------------------------------------------------------------------


def snap_image(
    arr: np.ndarray,
    *,
    grid_width: int = 320,
    grid_height: int = 180,
    auto_grid: bool = False,
    fit: str = "cover",
    palette: Any = None,
    max_colors: int = 0,
    scale: int = 6,
    dither: bool = False,
) -> tuple[np.ndarray, np.ndarray, dict[str, Any]]:
    """Snap an RGBA array to a logical pixel grid. Returns (native, preview, report)."""
    if fit not in {"cover", "contain", "none"}:
        raise AstridError(f"fit must be cover, contain or none, got {fit!r}")
    if scale < 1:
        raise AstridError("scale must be an integer >= 1")
    if max_colors and not 2 <= max_colors <= 256:
        raise AstridError("max_colors must be 0 (off) or between 2 and 256")
    height, width = arr.shape[:2]
    report: dict[str, Any] = {"source": {"width": width, "height": height}, "fit": fit}

    if auto_grid:
        lattice_x = detect_lattice(arr, axis=1)
        lattice_y = detect_lattice(arr, axis=0)
        xedges = _cell_edges(width, lattice_x["origin"], lattice_x["block"])
        yedges = _cell_edges(height, lattice_y["origin"], lattice_y["block"])
        report["grid"] = {
            "mode": "auto",
            "detected": bool(lattice_x["detected"] or lattice_y["detected"]),
            "x": lattice_x,
            "y": lattice_y,
        }
        work = arr
    else:
        if grid_width < 1 or grid_height < 1:
            raise AstridError("grid_width and grid_height must be positive integers")
        work, xedges, yedges, fit_info = _explicit_grid(arr, grid_width, grid_height, fit)
        report["grid"] = {"mode": "explicit", "detected": False, "fit_info": fit_info}

    if len(xedges) == work.shape[1] + 1 and len(yedges) == work.shape[0] + 1:
        # Identity grid: every cell is one source pixel, so mode voting is a no-op.
        native = work.copy()
        native[..., 3] = np.where(native[..., 3] >= 128, 255, 0)
    else:
        native = mode_downsample(work, xedges, yedges)
    grid_h, grid_w = native.shape[:2]
    report["grid"].update({"width": grid_w, "height": grid_h})

    palette_info: dict[str, Any] = {"source": None, "colours": [], "dither": bool(dither)}
    distances = np.zeros(0)
    parsed = parse_palette(palette)
    if parsed is not None or max_colors:
        if parsed is not None:
            source, colours, _label = parsed
        else:
            source = f"kmeans:{max_colors}"
            colours = kmeans_palette(native, max_colors)
        native, distances = map_to_palette(native, colours, dither=bool(dither))
        palette_info = {
            "source": source,
            "colours": [hex_of(c) for c in colours],
            "dither": bool(dither),
        }
    report["palette"] = palette_info
    report["palette_distance"] = _distance_stats(distances)

    opaque = native[..., 3] >= 128
    flat = native[opaque][:, :3]
    uniq, counts = np.unique(flat, axis=0, return_counts=True) if flat.size else (np.zeros((0, 3)), np.zeros(0))
    top = np.argsort(-counts, kind="stable")[:32]
    report["colours_used"] = [
        {"hex": hex_of(uniq[i]), "cells": int(counts[i])} for i in top
    ]
    report["cells"] = {
        "opaque": int(opaque.sum()),
        "transparent": int((~opaque).sum()),
        "distinct_colours": int(uniq.shape[0]),
    }
    report["alpha_values"] = sorted({int(v) for v in np.unique(native[..., 3])})

    preview = np.repeat(np.repeat(native, scale, axis=0), scale, axis=1)
    report["scale"] = scale
    return native, preview, report


def _explicit_grid(
    arr: np.ndarray, grid_width: int, grid_height: int, fit: str
) -> tuple[np.ndarray, list[int], list[int], dict[str, Any]]:
    height, width = arr.shape[:2]
    work = arr
    info: dict[str, Any] = {"requested": [grid_width, grid_height]}
    aspect = grid_width / grid_height
    if fit == "cover":
        if width / height > aspect:
            new_w = max(1, int(round(height * aspect)))
            x0 = (width - new_w) // 2
            work = arr[:, x0 : x0 + new_w]
            info["cropped_x"] = [x0, x0 + new_w]
        else:
            new_h = max(1, int(round(width / aspect)))
            y0 = (height - new_h) // 2
            work = arr[y0 : y0 + new_h, :]
            info["cropped_y"] = [y0, y0 + new_h]
    elif fit == "contain":
        if width / height > aspect:
            new_h = max(1, int(round(width / aspect)))
            padded = np.zeros((new_h, width, 4), dtype=np.uint8)
            top = (new_h - height) // 2
            padded[top : top + height] = arr
            work = padded
            info["padded_y"] = top
        else:
            new_w = max(1, int(round(height * aspect)))
            padded = np.zeros((height, new_w, 4), dtype=np.uint8)
            left = (new_w - width) // 2
            padded[:, left : left + width] = arr
            work = padded
            info["padded_x"] = left
    work_h, work_w = work.shape[:2]
    if grid_width > work_w or grid_height > work_h:
        raise AstridError(
            f"grid {grid_width}x{grid_height} is larger than the {work_w}x{work_h} source after fit",
            recovery_command="request a smaller grid, or use auto_grid for an already pixelated image",
        )
    xedges = _cell_edges(work_w, 0.0, work_w / grid_width)
    yedges = _cell_edges(work_h, 0.0, work_h / grid_height)
    return work, xedges, yedges, info


def _distance_stats(distances: np.ndarray) -> dict[str, Any]:
    if distances.size == 0:
        return {"count": 0, "mean": 0.0, "p95": 0.0, "max": 0.0}
    return {
        "count": int(distances.size),
        "mean": round(float(distances.mean()), 3),
        "p95": round(float(np.percentile(distances, 95)), 3),
        "max": round(float(distances.max()), 3),
    }


# ---------------------------------------------------------------------------
# pixel.cutout
# ---------------------------------------------------------------------------


def _border_connected(candidate: np.ndarray) -> np.ndarray:
    """Mask of candidate pixels 4-connected to the image border (Pillow flood fill)."""
    height, width = candidate.shape
    # .copy(): a fromarray() image is backed by read-only memory and Pillow's
    # flood fill silently leaves it unchanged.
    image = Image.fromarray(np.where(candidate, 255, 0).astype(np.uint8), mode="L").copy()
    seeds = [(x, 0) for x in range(width)] + [(x, height - 1) for x in range(width)]
    seeds += [(0, y) for y in range(height)] + [(width - 1, y) for y in range(height)]
    for xy in seeds:
        if image.getpixel(xy) == 255:
            ImageDraw.floodfill(image, xy, 128)
    return np.asarray(image) == 128


def _border_key(arr: np.ndarray, tolerance: int) -> tuple[int, int, int] | None:
    """Dominant colour of the opaque border, or None when the border is transparent."""
    ring = np.concatenate([arr[0], arr[-1], arr[1:-1, 0], arr[1:-1, -1]])
    opaque_ring = ring[ring[:, 3] >= 128][:, :3]
    if ring.shape[0] == 0 or opaque_ring.shape[0] < 0.5 * ring.shape[0]:
        return None
    bins = opaque_ring.astype(np.int64) // MODE_BIN
    keys = bins[:, 0] * 256 + bins[:, 1] * 16 + bins[:, 2]
    values, counts = np.unique(keys, return_counts=True)
    winner = values[int(np.argmax(counts))]
    members = opaque_ring[keys == winner].astype(np.float64)
    key = np.round(np.median(members, axis=0))
    coverage = float((np.sqrt(((opaque_ring - key) ** 2).sum(axis=1)) <= tolerance).mean())
    if coverage < 0.5:
        raise AstridError(
            "flat mode found no dominant border colour",
            recovery_command="use mode=chroma with an explicit key, or mode=luma for a light backdrop",
            state_snapshot={"border_coverage": round(coverage, 3)},
        )
    return (int(key[0]), int(key[1]), int(key[2]))


def _components(foreground: np.ndarray) -> tuple[np.ndarray, list[int]]:
    """8-connected component labels (1-based, 0 = background) and their sizes."""
    height, width = foreground.shape
    flat = foreground.ravel().tolist()
    labels = [0] * (height * width)
    sizes: list[int] = []
    for seed, is_fg in enumerate(flat):
        if not is_fg or labels[seed]:
            continue
        label = len(sizes) + 1
        labels[seed] = label
        stack = [seed]
        size = 0
        while stack:
            current = stack.pop()
            size += 1
            y, x = divmod(current, width)
            for ny in (y - 1, y, y + 1):
                if ny < 0 or ny >= height:
                    continue
                for nx in (x - 1, x, x + 1):
                    if nx < 0 or nx >= width:
                        continue
                    neighbour = ny * width + nx
                    if flat[neighbour] and not labels[neighbour]:
                        labels[neighbour] = label
                        stack.append(neighbour)
        sizes.append(size)
    return np.array(labels, dtype=np.int32).reshape(height, width), sizes


def cutout_image(
    arr: np.ndarray,
    *,
    mode: str = "chroma",
    key: str = "#FF00FF",
    tolerance: int = 48,
    grid: Any = None,
    fit: str = "contain",
    trim: bool = True,
    trim_padding: int = 2,
    keep_largest: bool = True,
    holes: bool = False,
) -> tuple[np.ndarray, dict[str, Any]]:
    """Remove a background into hard alpha. Returns (rgba, report).

    Background = candidate pixels connected to the image border. Pixels inside
    the sprite (for example magenta enclosed by an outline) are kept, unless
    ``holes`` is true: then every candidate pixel is background, so the key
    colour showing through gaps (between a tower's legs, inside a chain link)
    is removed too. Use it for chroma keys that never occur in the art.
    Alpha is strictly 0 or 255. When ``grid`` is set the cutout runs on the
    snapped native image, so every logical pixel is either fully in or out.
    """
    if mode not in {"chroma", "flat", "luma"}:
        raise AstridError(f"mode must be chroma, flat or luma, got {mode!r}")
    if tolerance < 0 or tolerance > 255:
        raise AstridError("tolerance must be between 0 and 255")
    report: dict[str, Any] = {"mode": mode, "tolerance": int(tolerance), "source": {"width": arr.shape[1], "height": arr.shape[0]}}

    parsed_grid = parse_grid(grid)
    if parsed_grid is not None:
        if parsed_grid == "auto":
            native, _preview, snap_report = snap_image(arr, auto_grid=True, palette=None, scale=1)
        else:
            native, _preview, snap_report = snap_image(
                arr,
                grid_width=parsed_grid[0],
                grid_height=parsed_grid[1],
                fit=fit,
                palette=None,
                scale=1,
            )
        arr = native
        report["grid"] = snap_report["grid"]
    else:
        report["grid"] = None

    rgb = arr[..., :3].astype(np.float64)
    transparent = arr[..., 3] < 128
    warnings: list[str] = []
    if mode == "chroma":
        key_rgb = parse_hex(key)
        distance = np.sqrt(((rgb - np.array(key_rgb, dtype=np.float64)) ** 2).sum(axis=2))
        candidate = (distance <= tolerance) | transparent
        report["key"] = hex_of(key_rgb)
    elif mode == "flat":
        key_rgb_opt = _border_key(arr, tolerance)
        if key_rgb_opt is None:
            candidate = transparent.copy()
            report["key"] = None
        else:
            distance = np.sqrt(((rgb - np.array(key_rgb_opt, dtype=np.float64)) ** 2).sum(axis=2))
            candidate = (distance <= tolerance) | transparent
            report["key"] = hex_of(key_rgb_opt)
            if max(key_rgb_opt) < 80:
                warnings.append("detected border colour is dark; a dark outline on a dark border may be removed")
    else:
        luminance = 0.2126 * rgb[..., 0] + 0.7152 * rgb[..., 1] + 0.0722 * rgb[..., 2]
        candidate = (luminance >= 255 - tolerance) | transparent
        report["key"] = None

    background = candidate.copy() if holes else _border_connected(candidate)
    foreground = ~background
    report["holes"] = bool(holes)
    report["background_pixels"] = int(background.sum())

    specks = {"components": 0, "pixels": 0}
    if keep_largest and foreground.any():
        labels, sizes = _components(foreground)
        largest = max(sizes)
        keep_labels = [index + 1 for index, size in enumerate(sizes) if size >= max(2, 0.05 * largest)]
        keep = np.isin(labels, keep_labels)
        dropped = foreground & ~keep
        specks = {
            "components": len(sizes) - len(keep_labels),
            "pixels": int(dropped.sum()),
        }
        foreground = keep
    report["specks_dropped"] = specks

    if not foreground.any():
        raise AstridError(
            "cutout found no foreground pixels",
            recovery_command="check the background key/tolerance, or try mode=flat or mode=luma",
            state_snapshot={"mode": mode, "background_pixels": report["background_pixels"]},
        )

    alpha = np.where(foreground, 255, 0).astype(np.uint8)
    out = np.zeros(arr.shape[:2] + (4,), dtype=np.uint8)
    out[..., :3] = np.where(foreground[..., None], arr[..., :3], 0)
    out[..., 3] = alpha

    rows = np.flatnonzero(foreground.any(axis=1))
    cols = np.flatnonzero(foreground.any(axis=0))
    bbox = {"x": int(cols[0]), "y": int(rows[0]), "width": int(cols[-1] - cols[0] + 1), "height": int(rows[-1] - rows[0] + 1)}
    if trim:
        pad = max(0, int(trim_padding))
        x0 = max(0, bbox["x"] - pad)
        y0 = max(0, bbox["y"] - pad)
        x1 = min(out.shape[1], bbox["x"] + bbox["width"] + pad)
        y1 = min(out.shape[0], bbox["y"] + bbox["height"] + pad)
        out = out[y0:y1, x0:x1].copy()
        report["trim"] = {"padding": pad, "crop": {"x": x0, "y": y0, "width": x1 - x0, "height": y1 - y0}}
    else:
        report["trim"] = None
    report["bbox"] = bbox
    report["alpha_values"] = sorted({int(v) for v in np.unique(out[..., 3])})
    report["hard_alpha"] = report["alpha_values"] in ([0, 255], [255], [0])
    report["foreground_pixels"] = int((out[..., 3] == 255).sum())
    report["warnings"] = warnings
    report["output"] = {"width": out.shape[1], "height": out.shape[0]}
    return out, report


__all__ = [
    "ASTRID_PALETTE_HEX",
    "apply_crop",
    "cutout_image",
    "detect_lattice",
    "hex_of",
    "kmeans_palette",
    "load_rgba",
    "map_to_palette",
    "mode_downsample",
    "parse_crop",
    "parse_grid",
    "parse_hex",
    "parse_palette",
    "save_rgba",
    "snap_image",
]
