"""Where a timeline's *intent* lives on disk: the one module that reads and writes it.

Intent is what an editor means, as opposed to where things happen to sit: a clip that
enters on a word (an anchor), a field computed from the voice or a slot (a formula), the
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


# ---- anchors: a clip's start (or end) sits on a word --------------------------------
def anchor(clip: Mapping[str, Any]) -> dict[str, Any] | None:
    """``{word: "<line>:<index>", text, offset_s, edge: "start"|"end"}`` or None."""
    value = _app(clip).get("anchor")
    return dict(value) if isinstance(value, Mapping) else None


def set_anchor(clip: dict[str, Any], value: Mapping[str, Any] | None) -> None:
    if value:
        _app_w(clip)["anchor"] = dict(value)
    else:
        _app_w(clip).pop("anchor", None)
        _tidy(clip)


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


def beat_sources(clip: Mapping[str, Any]) -> list[float]:
    """Music beat times in SOURCE seconds (the music file's own clock, like ``from``/``to``).

    Stored either as ``{"beats": [...], "bpm": …, "time": "cue_seconds"}`` (source seconds,
    so trims and splits never need to touch them) or as a clip-relative list (older form)."""
    value = _app(clip).get("beats")
    if isinstance(value, Mapping):
        return [float(b) for b in value.get("beats") or [] if isinstance(b, (int, float))]
    if isinstance(value, list):
        src0 = float(clip.get("from") or 0.0)
        speed = float(clip.get("speed") or 1.0) or 1.0
        return [src0 + float(b[0] if isinstance(b, (list, tuple)) else b) * speed for b in value]
    return []


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


def clear_standin(clip: dict[str, Any]) -> None:
    app = _app_w(clip)
    app.pop("standin_for", None)
    app.pop("intended", None)
    _tidy(clip)


def has_intent(clip: Mapping[str, Any]) -> bool:
    """True if a clip carries anything this module owns (used for display: the ⚓ / ƒ marks)."""
    return bool(anchor(clip) or formulas(clip) or slot(clip) or standin(clip))
