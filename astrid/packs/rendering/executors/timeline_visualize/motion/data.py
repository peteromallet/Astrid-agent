"""Data tracks: typed time data attached to timeline clips, one convention for all of it.

Any producer (a capability, a script, the EDL builder) writes a track onto a
clip; any layer or check reads every track through :func:`tracks`::

    clip["app"]["data"]["loudness"] = {
        "kind": "series",            # points | intervals | series | boxes
        "units": "dBFS",
        "time": "clip",              # clip: seconds from the clip start (like app.words)
                                     # source: media seconds, mapped through the clip's "from" (like app.beats)
        "source": {"handle": "sha256:…", "producer": "astrid.data.loudness@1", "params": {"step_s": 0.05}},
        "items": {"t0": 0.0, "step": 0.05, "values": [-31.2, -28.4, …]},
    }

``items`` by kind:

- ``points``: ``[[t, "label"?], …]``
- ``intervals``: ``[[t0, t1, "label"?], …]``
- ``series``: ``{"t0": t, "step": s, "values": [v, …]}`` (or ``[[t, v], …]``)
- ``boxes``: ``[[t, x, y, w, h, "label"?], …]`` in canvas px; ``t`` may be ``null`` for a static box

``source.handle`` uses the media-handle grammar (``sha256:<hex>``,
``run:<run_id>/<port>#n``, ``ref:<name>``) so provenance reads the same as
input lineage. The ad-hoc data that predates the convention is exposed as
tracks too: ``words`` (VO ``app.words``), ``beats``/``music_hits``
(``app.beats``), ``face_zone`` (``params.faceZone``) and ``presenter_words``.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

from .model import Element, _map, _num

KINDS = ("points", "intervals", "series", "boxes")
TIMES = ("clip", "source")


@dataclass
class Track:
    """One track in timeline seconds."""

    name: str
    kind: str
    clip_id: str
    units: str = ""
    source: Mapping[str, Any] = field(default_factory=dict)
    points: list[tuple[float, str]] = field(default_factory=list)
    intervals: list[tuple[float, float, str]] = field(default_factory=list)
    series: list[tuple[float, float]] = field(default_factory=list)
    boxes: list[tuple[float | None, float, float, float, float, str]] = field(default_factory=list)
    derived: bool = False  # exposed from pre-convention data (app.words, app.beats, faceZone)

    def times(self) -> list[float]:
        if self.kind == "points":
            return [t for t, _l in self.points]
        if self.kind == "intervals":
            return [t for t, _e, _l in self.intervals]
        if self.kind == "series":
            return [t for t, _v in self.series]
        return [t for t, *_rest in self.boxes if t is not None]

    def boxes_at(self, t: float, *, within: float = 0.5) -> list[tuple[float, float, float, float]]:
        """Boxes (x0, y0, x1, y1) holding at ``t``: static ones, else the nearest sample within ``within`` s."""
        static = [(x, y, x + w, y + h) for bt, x, y, w, h, _l in self.boxes if bt is None]
        timed = [(abs(bt - t), (x, y, x + w, y + h)) for bt, x, y, w, h, _l in self.boxes if bt is not None]
        near = [box for distance, box in timed if distance <= within]
        if timed and near:
            best = min(distance for distance, _box in timed)
            near = [box for distance, box in timed if distance == best]
        return static + near

    def mean(self, start: float, end: float) -> float | None:
        values = [v for t, v in self.series if start <= t < end and math.isfinite(v)]
        return sum(values) / len(values) if values else None


def validate(raw: Any) -> list[str]:
    """Problems with one ``app.data.<name>`` value (empty when it is a valid track)."""
    if not isinstance(raw, Mapping):
        return ["a data track must be an object with kind, items (and units, time, source)"]
    problems = []
    kind = raw.get("kind")
    if kind not in KINDS:
        problems.append(f"kind must be one of {', '.join(KINDS)} (got {kind!r})")
    if raw.get("time", "clip") not in TIMES:
        problems.append(f"time must be 'clip' or 'source' (got {raw.get('time')!r})")
    items = raw.get("items")
    if kind == "series":
        ok = (isinstance(items, Mapping) and isinstance(items.get("values"), list) and "step" in items) or isinstance(items, list)
        if not ok:
            problems.append("series items must be {t0, step, values: [...]} or [[t, value], ...]")
    elif kind in KINDS and not isinstance(items, list):
        problems.append(f"{kind} items must be a list")
    source = raw.get("source")
    if source is not None and not isinstance(source, Mapping):
        problems.append("source must be an object ({handle, producer, params})")
    return problems


def make_track(kind: str, items: Any, *, units: str = "", time: str = "clip",
               source: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """A track value ready for ``clip["app"]["data"][name]`` (validated)."""
    value = {"kind": kind, "units": units, "time": time, "source": dict(source or {}), "items": items}
    problems = validate(value)
    if problems:
        raise ValueError("; ".join(problems))
    return value


def attach(clip: dict[str, Any], name: str, track: Mapping[str, Any]) -> dict[str, Any]:
    """Write ``track`` as ``clip.app.data.<name>`` (replacing an older one)."""
    if not name or not name.replace("_", "").replace("-", "").isalnum():
        raise ValueError(f"track name {name!r} must be a short word")
    problems = validate(track)
    if problems:
        raise ValueError("; ".join(problems))
    clip.setdefault("app", {}).setdefault("data", {})[name] = dict(track)
    return clip


def _mapper(element: Element, time_ref: str):
    offset = _num(element.clip.get("from")) if time_ref == "source" else 0.0
    speed = _num(element.clip.get("speed"), 1.0) or 1.0
    return lambda t: element.start + (float(t) - offset) / speed


def _label(row: Any, index: int) -> str:
    return str(row[index]) if isinstance(row, (list, tuple)) and len(row) > index and row[index] is not None else ""


def _read(name: str, raw: Mapping[str, Any], element: Element) -> Track | None:
    if validate(raw):
        return None
    to_t = _mapper(element, str(raw.get("time") or "clip"))
    track = Track(name, str(raw["kind"]), element.id, str(raw.get("units") or ""), _map(raw.get("source")))
    items = raw.get("items")
    if track.kind == "points":
        track.points = [(to_t(_num(row[0])), _label(row, 1)) for row in items if isinstance(row, (list, tuple)) and row]
    elif track.kind == "intervals":
        track.intervals = [(to_t(_num(row[0])), to_t(_num(row[1])), _label(row, 2))
                           for row in items if isinstance(row, (list, tuple)) and len(row) >= 2]
    elif track.kind == "series":
        if isinstance(items, Mapping):
            t0, step = _num(items.get("t0")), _num(items.get("step"), 0.0)
            track.series = [(to_t(t0 + i * step), _num(v, math.nan)) for i, v in enumerate(items.get("values") or [])]
        else:
            track.series = [(to_t(_num(row[0])), _num(row[1], math.nan)) for row in items
                            if isinstance(row, (list, tuple)) and len(row) >= 2]
    else:
        boxes = []
        for row in items:
            if isinstance(row, (list, tuple)) and len(row) >= 5:
                t = None if row[0] is None else to_t(_num(row[0]))
                boxes.append((t, _num(row[1]), _num(row[2]), _num(row[3]), _num(row[4]), _label(row, 5)))
        track.boxes = boxes
    return track


def _derived(element: Element) -> list[Track]:
    """The pre-convention data, read as tracks."""
    found: list[Track] = []
    app = _map(element.clip.get("app"))
    words = app.get("words")
    if element.audio and isinstance(words, list) and words:
        track = Track("words", "intervals", element.id, "s", {"producer": "app.words"}, derived=True)
        for row in words:
            if isinstance(row, (list, tuple)) and len(row) >= 2:
                track.intervals.append((element.start + _num(row[0]), element.start + _num(row[1]), _label(row, 2)))
        found.append(track)
    beats = _map(app.get("beats"))
    if beats:
        to_t = _mapper(element, "source")
        source = {"producer": "app.beats", "file": beats.get("source")}
        found.append(Track("beats", "points", element.id, "s", source,
                           points=[(to_t(t), "beat") for t in beats.get("beats") or [] if isinstance(t, (int, float))],
                           derived=True))
        hits = []
        for row in beats.get("hits") or []:
            if isinstance(row, (list, tuple)) and row:
                hits.append((to_t(_num(row[0])), _label(row, 1) or "hit"))
            elif isinstance(row, Mapping):
                hits.append((to_t(_num(row.get("t"))), str(row.get("kind") or "hit")))
        if hits:
            found.append(Track("music_hits", "points", element.id, "s", source, points=hits, derived=True))
    zone = _map(element.params.get("faceZone"))
    if all(key in zone for key in ("x", "y", "w", "h")):
        found.append(Track("face_zone", "boxes", element.id, "px", {"producer": "params.faceZone"},
                           boxes=[(None, _num(zone["x"]), _num(zone["y"]), _num(zone["w"]), _num(zone["h"]), "face zone")],
                           derived=True))
    if element.type == "am-presenter" and isinstance(element.params.get("words"), list):
        found.append(Track("presenter_words", "intervals", element.id, "s", {"producer": "params.words"},
                           intervals=[(element.start + _num(r[0]), element.start + _num(r[1]), "")
                                      for r in element.params["words"] if isinstance(r, (list, tuple)) and len(r) >= 2],
                           derived=True))
    return found


def tracks(elements: Iterable[Element], *, start: float | None = None, end: float | None = None,
           derived: bool = True) -> list[Track]:
    """Every data track on these clips (``app.data.*`` plus the derived ones), in timeline seconds.

    With ``start``/``end``, only tracks of clips overlapping the window.
    """
    found: list[Track] = []
    for element in elements:
        if start is not None and end is not None and (element.end <= start or element.start >= end):
            continue
        for name, raw in _map(_map(element.clip.get("app")).get("data")).items():
            track = _read(str(name), _map(raw), element)
            if track is not None:
                found.append(track)
        if derived:
            found.extend(_derived(element))
    return found


def by_name(found: Iterable[Track], name: str) -> list[Track]:
    return [track for track in found if track.name == name]
