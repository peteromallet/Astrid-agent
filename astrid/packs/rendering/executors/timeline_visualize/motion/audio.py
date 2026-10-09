"""A decimated loudness envelope of the timeline's audio clips (no render needed).

The contact sheet and the motion sheet draw a thin VO / music lane with
silence marks from the clips themselves: each audio clip's materialized WAV
is read only over the part the timeline plays (``from``…``to``), decimated,
and reduced to one RMS value per bin, scaled by the clip's ``volume``. Cost is
bounded: at most ``MAX_SECONDS`` of source per call and a fixed decimation, so
a 90 s film reads in well under a second. Non-WAV media is skipped and named.
"""
from __future__ import annotations

import math
import wave
from array import array
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Mapping

from .model import Element

LANES = ("vo", "music", "sfx")
TARGET_RATE = 2000  # samples per second kept after decimation (envelope only)
MAX_SECONDS = 600.0
SILENCE_DB = -45.0
SILENCE_MIN_S = 0.4
MIN_BIN_S = 0.02


@dataclass
class Envelope:
    start: float
    step: float
    lanes: dict[str, list[float]]  # RMS in 0..1 per bin
    notes: list[str] = field(default_factory=list)

    def db(self, lane: str, index: int) -> float:
        value = self.lanes[lane][index]
        return 20 * math.log10(value) if value > 1e-6 else -120.0

    def level_db(self, lane: str, begin: float, finish: float) -> float:
        """RMS level of ``lane`` over ``[begin, finish)`` in dBFS (power mean of the bins)."""
        first = max(0, int((begin - self.start) / self.step))
        last = min(len(self.lanes[lane]), max(first + 1, int((finish - self.start) / self.step)))
        values = self.lanes[lane][first:last]
        power = sum(v * v for v in values) / len(values) if values else 0.0
        return 10 * math.log10(power) if power > 1e-12 else -120.0

    def dead_air(self, *, minimum: float = 0.3) -> list[tuple[float, float]]:
        """Stretches where VO, music and sfx are all quiet (true silence on the mix)."""
        quiet = [
            all(self.db(lane, index) < SILENCE_DB for lane in LANES)
            for index in range(len(self.lanes["vo"]))
        ]
        runs, begin = [], None
        for index, value in enumerate(quiet + [False]):
            if value and begin is None:
                begin = index
            elif not value and begin is not None:
                if (index - begin) * self.step >= minimum - 1e-9:
                    runs.append((self.start + begin * self.step, self.start + index * self.step))
                begin = None
        return runs

    def silences(self, lane: str = "vo", *, minimum: float = SILENCE_MIN_S, quiet_db: float = SILENCE_DB) -> list[tuple[float, float]]:
        """Runs of bins below ``quiet_db`` lasting at least ``minimum`` seconds."""
        runs, begin = [], None
        values = self.lanes.get(lane) or []
        for index in range(len(values) + 1):
            quiet = index < len(values) and self.db(lane, index) < quiet_db
            if quiet and begin is None:
                begin = index
            elif not quiet and begin is not None:
                if (index - begin) * self.step >= minimum - 1e-9:
                    runs.append((self.start + begin * self.step, self.start + index * self.step))
                begin = None
        return runs


def lane_of(element: Element) -> str | None:
    if not element.audio:
        return None
    if element.track in ("music",):
        return "music"
    if element.track in ("sfx",):
        return "sfx"
    return "vo"


def _read_window(path: Path, offset: float, seconds: float) -> tuple[array, int]:
    """Mono 16-bit-equivalent samples of ``seconds`` from ``offset``, decimated to ~TARGET_RATE."""
    with wave.open(str(path)) as source:
        rate, channels, width = source.getframerate(), source.getnchannels(), source.getsampwidth()
        start = max(0, int(offset * rate))
        count = max(0, min(source.getnframes() - start, int(seconds * rate)))
        source.setpos(start)
        raw = source.readframes(count)
    step = max(1, rate // TARGET_RATE)
    frame_bytes = width * channels
    out = array("f")
    if width == 2:
        samples = array("h")
        samples.frombytes(raw[: len(raw) - len(raw) % 2])
        out.extend(samples[i] / 32768.0 for i in range(0, len(samples), step * channels))
    elif width == 4:
        samples = array("i")
        samples.frombytes(raw[: len(raw) - len(raw) % 4])
        out.extend(samples[i] / 2147483648.0 for i in range(0, len(samples), step * channels))
    elif width == 1:
        out.extend((raw[i] - 128) / 128.0 for i in range(0, len(raw), step * frame_bytes))
    else:
        raise ValueError(f"unsupported WAV sample width {width}")
    return out, max(1, rate // step)


def envelope(
    elements: Iterable[Element], files: Mapping[str, str | Path], start: float, end: float, *, bins: int,
) -> Envelope:
    """One RMS value per bin over ``[start, end)`` for the VO, music and sfx lanes."""
    # a bin holds at least MIN_BIN_S of signal so its RMS is a level, not a sample
    bins = max(1, min(int(bins), int((end - start) / MIN_BIN_S) or 1))
    step = max(1e-3, (end - start) / bins)
    lanes = {lane: [0.0] * bins for lane in LANES}
    energy = {lane: [0.0] * bins for lane in LANES}
    notes: list[str] = []
    budget = MAX_SECONDS
    for element in elements:
        lane = lane_of(element)
        if lane is None or element.end <= start or element.start >= end:
            continue
        key = element.asset or ""
        path = files.get(key) or files.get(key.rsplit(":", 1)[-1])
        if not path:
            notes.append(f"{element.short_id}: no materialized file")
            continue
        path = Path(path)
        lo, hi = max(start, element.start), min(end, element.end)
        if hi - lo > budget:
            notes.append(f"audio budget reached at {element.short_id}")
            break
        budget -= hi - lo
        offset = float(element.clip.get("from") or 0.0) + (lo - element.start)
        try:
            samples, rate = _read_window(path, offset, hi - lo)
        except (wave.Error, EOFError, ValueError, OSError) as exc:
            notes.append(f"{element.short_id}: not a readable WAV ({type(exc).__name__})")
            continue
        gain = float(element.clip.get("volume") if element.clip.get("volume") is not None else 1.0)
        for index, value in enumerate(samples):
            t = lo + index / rate
            slot = int((t - start) / step)
            if 0 <= slot < bins:
                energy[lane][slot] += (value * gain) ** 2
                lanes[lane][slot] += 1.0  # sample count, turned into RMS below
    for lane in LANES:
        for slot in range(bins):
            count = lanes[lane][slot]
            lanes[lane][slot] = math.sqrt(energy[lane][slot] / count) if count else 0.0
    return Envelope(start, step, lanes, notes)


def draw_lane(draw, env: Envelope, *, x0: float, x1: float, y0: float, y1: float, colours: Mapping[str, str],
              silence_colour: str, silence_lane: str = "vo", dead_air_colour: str = "#ef4444") -> None:
    """Music grows up from the midline, VO down, sfx as ticks on it; silences as a strip under the lane."""
    bins = len(next(iter(env.lanes.values())))
    width = (x1 - x0) / max(1, bins)
    mid = (y0 + y1 - 4) / 2
    half = (y1 - y0 - 5) / 2

    def height(value: float) -> float:
        # -60 dB .. 0 dB onto 0 .. half
        db = 20 * math.log10(value) if value > 1e-6 else -120.0
        return max(0.0, min(1.0, (db + 60) / 60)) * half

    for lane, sign in (("music", -1), ("vo", 1), ("sfx", 0)):
        colour = colours.get(lane)
        if not colour:
            continue
        for index, value in enumerate(env.lanes.get(lane) or ()):
            h = height(value)
            if h < 0.5:
                continue
            x = x0 + index * width
            if sign:
                top, bottom = (mid - h, mid) if sign < 0 else (mid, mid + h)
            else:
                top, bottom = mid - h / 2, mid + h / 2
            draw.rectangle((x, top, x + max(1.0, width) - 0.2, bottom), fill=colour)
    for begin, finish in env.silences(silence_lane):
        a = x0 + (begin - env.start) / env.step * width
        b = x0 + (finish - env.start) / env.step * width
        draw.rectangle((a, y1 - 3, b, y1 - 1), fill=silence_colour)
    for begin, finish in env.dead_air():
        a = x0 + (begin - env.start) / env.step * width
        b = x0 + (finish - env.start) / env.step * width
        draw.rectangle((a, y0, b, y1 - 1), outline=dead_air_colour, width=1)
        draw.rectangle((a, y1 - 3, b, y1 - 1), fill=dead_air_colour)
