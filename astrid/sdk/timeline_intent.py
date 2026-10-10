"""Where a timeline's *intent* lives on disk: the one module that reads and writes it.

Intent is what an editor means, as opposed to where things happen to sit: a clip that
starts and ends on moments (``on "viral"``, ``until "Astrid"``), its cut and layer names
and the cut's ``why``, a field computed from the voice or a slot (a formula), the
VO lines and the silence declared after each (the voice track), music beats, real-footage
slots, and stand-ins waiting for a real asset.

The storage format is NOT frozen. Today it uses keys the validator already tolerates
(``app.*`` on a clip, ``parent.config.slots`` on the parent). The editing API
(``timeline_checkout``) and the CLI only go through these functions, so the format can
change here, in one place, without touching them.
"""
from __future__ import annotations

from typing import Any, Callable, Iterable, Mapping

Rows = list[list[Any]]


def _app(clip: Mapping[str, Any]) -> Mapping[str, Any]:
    app = clip.get("app")
    return app if isinstance(app, Mapping) else {}


def _app_w(clip: dict[str, Any]) -> dict[str, Any]:
    app = clip.get("app")
    if not isinstance(app, dict):
        app = clip["app"] = {}
    return app


def _tidy(clip: dict[str, Any]) -> None:
    if isinstance(clip.get("app"), dict) and not clip["app"]:
        clip.pop("app")


# ---- moments: when a clip starts and ends, in words (see astrid.core.timeline.moments) -
def on(clip: Mapping[str, Any]) -> str | None:
    """The moment the clip starts (``"viral"``, ``beat 2 after "Astrid"``, ``+0.8s``), or None."""
    value = _app(clip).get("on")
    return str(value) if isinstance(value, str) and value.strip() else None


def set_on(clip: dict[str, Any], moment: str | None) -> None:
    _set_or_drop(clip, "on", moment)


def until(clip: Mapping[str, Any]) -> str | None:
    """The moment the clip ends (``"Astrid"``, ``c26``), or None (then ``for_s`` or its cut's end)."""
    value = _app(clip).get("until")
    return str(value) if isinstance(value, str) and value.strip() else None


def set_until(clip: dict[str, Any], moment: str | None) -> None:
    _set_or_drop(clip, "until", moment)


def for_s(clip: Mapping[str, Any]) -> float | None:
    """A literal length in seconds (a decision with no moment behind it), or None."""
    value = _app(clip).get("for")
    return float(value) if isinstance(value, (int, float)) else None


def set_for(clip: dict[str, Any], seconds: float | None) -> None:
    _set_or_drop(clip, "for", None if seconds is None else round(float(seconds), 6))


# ---- cuts and layers: stable names ---------------------------------------------------
def cut_of(clip: Mapping[str, Any]) -> str | None:
    """The cut id this clip belongs to (``c21``; ids, not positions), or None."""
    value = _app(clip).get("cut")
    return str(value) if value else None


def set_cut(clip: dict[str, Any], cut_id: str | None) -> None:
    _set_or_drop(clip, "cut", cut_id)


def layer_of(clip: Mapping[str, Any]) -> str | None:
    """The layer's short name inside its cut (``rocket``), or None. Address: ``c22.rocket``."""
    value = _app(clip).get("layer")
    return str(value) if value else None


def set_layer(clip: dict[str, Any], name: str | None) -> None:
    _set_or_drop(clip, "layer", name)


def deliberate(clip: Mapping[str, Any]) -> bool:
    """The cut is a deliberate hold (lint's HOLD/STILL checks leave it alone). On the cut's picture clip."""
    app = _app(clip)
    return bool(app.get("deliberate_hold") or app.get("deliberate"))


def set_deliberate(clip: dict[str, Any], on: bool) -> None:
    _app_w(clip).pop("deliberate", None)
    _set_or_drop(clip, "deliberate_hold", True if on else None)


def why_layers(clip: Mapping[str, Any]) -> list[str] | None:
    """The layer names the cut had when its why was written (to flag a why that may be stale)."""
    value = _app(clip).get("why_layers")
    return [str(v) for v in value] if isinstance(value, list) else None


def why(clip: Mapping[str, Any]) -> str | None:
    """Why the cut is there (on the cut's picture clip)."""
    value = _app(clip).get("why")
    return str(value) if isinstance(value, str) and value.strip() else None


def set_why(clip: dict[str, Any], text: str | None) -> None:
    _set_or_drop(clip, "why", text)


def _set_or_drop(clip: dict[str, Any], key: str, value: Any) -> None:
    if value is None or value == "":
        _app_w(clip).pop(key, None)
        _tidy(clip)
    else:
        _app_w(clip)[key] = value


# ---- formulas: a field computed from words, lines or slot marks ---------------------
def formulas(clip: Mapping[str, Any]) -> dict[str, Any]:
    """``{field path: expression}``; see ``Checkout.resolve`` for the expressions."""
    value = _app(clip).get("formulas")
    return dict(value) if isinstance(value, Mapping) else {}


def set_formula(clip: dict[str, Any], path: str, expr: Mapping[str, Any] | None) -> None:
    table = dict(formulas(clip))
    if expr is None:
        table.pop(path, None)
    else:
        table[path] = dict(expr)
    if table:
        _app_w(clip)["formulas"] = table
    else:
        _app_w(clip).pop("formulas", None)
        _tidy(clip)


# ---- the voice track: VO lines, their words, the silence after each -----------------
def line(clip: Mapping[str, Any]) -> str | None:
    """The VO line (segment id) this audio clip belongs to."""
    value = _app(clip).get("segment")
    return str(value) if value else None


def words(clip: Mapping[str, Any]) -> Rows:
    """The clip's spoken words, ``[[start, end, text], …]`` in seconds from the clip's start."""
    value = _app(clip).get("words")
    return [list(row) for row in value] if isinstance(value, list) else []


def set_line(clip: dict[str, Any], segment: str, rows: Iterable[Iterable[Any]]) -> None:
    app = _app_w(clip)
    app["segment"] = segment
    app["words"] = [list(row) for row in rows]


def line_text(clip: Mapping[str, Any]) -> str | None:
    """The line's script text as written (punctuation and all), on its first clip; None if not declared."""
    value = _app(clip).get("text")
    return str(value) if isinstance(value, str) else None


def set_line_text(clip: dict[str, Any], text: str | None) -> None:
    if text is None:
        _app_w(clip).pop("text", None)
        _tidy(clip)
    else:
        _app_w(clip)["text"] = str(text)


def gap_after(clip: Mapping[str, Any]) -> float | None:
    """Declared silence (seconds) after the line whose last clip this is, or None if undeclared."""
    value = _app(clip).get("gap_after_s")
    return float(value) if isinstance(value, (int, float)) else None


def set_gap_after(clip: dict[str, Any], seconds: float | None) -> None:
    if seconds is None:
        _app_w(clip).pop("gap_after_s", None)
        _tidy(clip)
    else:
        _app_w(clip)["gap_after_s"] = round(float(seconds), 6)


def beat_sources(clip: Mapping[str, Any], kind: str = "beat") -> list[float]:
    """Music beat times in SOURCE seconds (the music file's own clock, like ``from``/``to``).

    Stored either as ``{"beats": [...], "bpm": …, "time": "cue_seconds"}`` (source seconds,
    so trims and splits never need to touch them) or as a clip-relative list (older form)."""
    value = _app(clip).get("beats")
    if isinstance(value, Mapping):
        key = "downbeats" if kind == "downbeat" else "hits" if kind == "hit" else "beats"
        return [float(b[0] if isinstance(b, (list, tuple)) else b) for b in value.get(key) or []
                if isinstance(b[0] if isinstance(b, (list, tuple)) else b, (int, float))]
    if kind != "beat":
        return []
    if isinstance(value, list):
        src0 = float(clip.get("from") or 0.0)
        speed = float(clip.get("speed") or 1.0) or 1.0
        return [src0 + float(b[0] if isinstance(b, (list, tuple)) else b) * speed for b in value]
    return []


def beats_grid(data: Mapping[str, Any], source: str | None = None) -> dict[str, Any]:
    """A beats file (``chiptune.compose``'s beats.json: ``{bpm, beats, downbeats, bars, hits, …}``) → the
    stored form: source seconds of the music file, hits as ``[t, kind]``."""
    def times(key: str) -> list[float]:
        out = []
        for b in data.get(key) or []:
            t = b.get("t") if isinstance(b, Mapping) else b[0] if isinstance(b, (list, tuple)) and b else b
            if isinstance(t, (int, float)) and not isinstance(t, bool):
                out.append(round(float(t), 6))
        return out

    if not times("beats"):
        raise ValueError("a beats file needs a non-empty beats list (seconds of the music file)")
    grid: dict[str, Any] = {"beats": times("beats"), "downbeats": times("downbeats"), "time": "cue_seconds"}
    if data.get("bars"):
        grid["bars"] = times("bars")
    hits = []
    for h in data.get("hits") or []:
        if isinstance(h, Mapping):
            t, kind = h.get("t"), h.get("kind")
        elif isinstance(h, (list, tuple)) and h:
            t, kind = h[0], (h[1] if len(h) > 1 else None)
        else:
            t, kind = h, None
        if isinstance(t, (int, float)) and not isinstance(t, bool):
            hits.append([round(float(t), 6), kind] if kind else [round(float(t), 6)])
    if hits:
        grid["hits"] = hits
    for key in ("bpm", "duration_s"):
        if isinstance(data.get(key), (int, float)):
            grid[key] = data[key]
    if source:
        grid["source"] = source
    return grid


def set_beats(clip: dict[str, Any], grid: Mapping[str, Any] | None) -> None:
    """Attach a beat grid (``beats_grid``) to a music clip, or remove it (None)."""
    if grid is None:
        _app_w(clip).pop("beats", None)
        _tidy(clip)
    else:
        _app_w(clip)["beats"] = dict(grid)


def beats_label(clip: Mapping[str, Any]) -> str | None:
    """Where the clip's beats came from (a file name or a media handle), if it has a grid."""
    value = _app(clip).get("beats")
    if isinstance(value, Mapping):
        return str(value.get("source") or "a beat grid")
    return "a beat list" if isinstance(value, list) and value else None


def beat_period(clip: Mapping[str, Any]) -> float | None:
    """Seconds per beat (from ``bpm``, else the median beat spacing)."""
    value = _app(clip).get("beats")
    if isinstance(value, Mapping) and isinstance(value.get("bpm"), (int, float)) and value["bpm"] > 0:
        return 60.0 / float(value["bpm"])
    times = sorted(beat_sources(clip))
    steps = sorted(b - a for a, b in zip(times, times[1:]) if b - a > 1e-3)
    return steps[len(steps) // 2] if steps else None


def map_timed(clip: dict[str, Any], fn: Callable[[Rows], Rows]) -> None:
    """Apply ``fn`` to every clip-relative timed list (words, beats) after a trim or split."""
    app = clip.get("app")
    if not isinstance(app, dict):
        return
    for key in ("words", "beats"):
        rows = app.get(key)
        if isinstance(rows, list) and rows and isinstance(rows[0], (list, tuple)):
            app[key] = fn([list(r) for r in rows])


# ---- real-footage slots and stand-ins ------------------------------------------------
def slots(bundle: dict[str, Any]) -> dict[str, Any]:
    """The timeline's slot table ``{id: {kind, spec, face_zone, hand_mark, media}}`` (live, writable)."""
    config = bundle.setdefault("parent", {}).setdefault("config", {})
    table = config.get("slots")
    if not isinstance(table, dict):
        table = config["slots"] = {}
    return table


def slot(clip: Mapping[str, Any]) -> str | None:
    value = _app(clip).get("slot")
    return str(value) if value else None


def set_slot(clip: dict[str, Any], slot_id: str, *, replaces: list[str] | None = None) -> None:
    app = _app_w(clip)
    app["slot"] = slot_id
    if replaces:
        app["replaces"] = list(replaces)


def standin(clip: Mapping[str, Any]) -> tuple[Any, dict[str, Any]] | None:
    """``(wanted asset, intended {element, params})`` for a stand-in, or None."""
    app = _app(clip)
    wanted = app.get("standin_for")
    if not wanted:
        return None
    intended = app.get("intended")
    return wanted, dict(intended) if isinstance(intended, Mapping) else {}


def set_standin(clip: dict[str, Any], wanted: Any, intended: Mapping[str, Any] | None = None) -> None:
    """Mark a clip as standing in for ``wanted`` (a file path or registry key not available yet)."""
    app = _app_w(clip)
    app["standin_for"] = wanted
    if intended:
        app["intended"] = dict(intended)


def clear_standin(clip: dict[str, Any]) -> None:
    app = _app_w(clip)
    app.pop("standin_for", None)
    app.pop("intended", None)
    _tidy(clip)


def has_intent(clip: Mapping[str, Any]) -> bool:
    """True if a clip carries anything this module owns (used for display: the ⚓ / ƒ marks)."""
    return bool(on(clip) or until(clip) or formulas(clip) or slot(clip) or standin(clip) or sequence_fit(clip))


# ---- sequences: one picture cut made of stepped clips (a time-lapse) -----------------
def sequence(clip: Mapping[str, Any]) -> tuple[str, int] | None:
    """``(sequence id, step index)`` for a step of a sequence, or None."""
    app = _app(clip)
    if not app.get("sequence"):
        return None
    return str(app["sequence"]), int(app.get("sequence_index") or 0)


def set_sequence(clip: dict[str, Any], sequence_id: str, index: int) -> None:
    app = _app_w(clip)
    app["sequence"] = sequence_id
    app["sequence_index"] = int(index)


def sequence_fit(clip: Mapping[str, Any]) -> dict[str, Any] | None:
    """How a sequence lays its steps out against the voice (on its first step), or None.

    ``{"land": {"word": "v20c:9", "text": "lifetime"}, "lead": [12, 10, 10, 10, 14],
    "race": [6, 5, 4, 4, 3, 3, 3, 2], "cycle": ["RW-00", …], "then": "RW-04"}``: the
    ``lead`` steps (frames each, assets from ``cycle``), then a race through ``cycle`` with
    shrinking steps (the last value repeats) that ends exactly on the word, then ``then``
    to the end of the sequence."""
    value = _app(clip).get("sequence_fit")
    return dict(value) if isinstance(value, Mapping) else None


def set_sequence_fit(clip: dict[str, Any], spec: Mapping[str, Any] | None) -> None:
    if spec:
        _app_w(clip)["sequence_fit"] = dict(spec)
    else:
        _app_w(clip).pop("sequence_fit", None)
        _tidy(clip)


# ---- chapters: labels over runs of cuts (not containers) -----------------------------
def chapters(bundle: Mapping[str, Any]) -> list[dict[str, str]]:
    """``[{"name": "06 ASTRID", "from": "c30"}, …]``: each chapter starts at a cut."""
    config = (bundle.get("parent") or {}).get("config") or {}
    value = config.get("chapters")
    return [dict(row) for row in value if isinstance(row, Mapping)] if isinstance(value, list) else []


def set_chapters(bundle: dict[str, Any], table: list[Mapping[str, Any]]) -> None:
    config = bundle.setdefault("parent", {}).setdefault("config", {})
    if table:
        config["chapters"] = [dict(row) for row in table]
    else:
        config.pop("chapters", None)


# ---- orphans: clips kept after their line was removed, waiting to be re-homed ----------
def orphan(clip: Mapping[str, Any]) -> str | None:
    """Why this clip is an orphan (``line s06 was removed``), or None."""
    value = _app(clip).get("orphan")
    return str(value) if value else None


def set_orphan(clip: dict[str, Any], reason: str | None) -> None:
    _set_or_drop(clip, "orphan", reason)
