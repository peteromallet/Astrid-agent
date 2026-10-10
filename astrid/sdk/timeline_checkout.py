"""Edit a timeline in one obvious step.

    from astrid.sdk.timeline_checkout import Checkout

    tl = Checkout.open("almost-ready", "01M4GGKMHZ3J8V402C8CWSCE95")   # or Checkout.load("film.json")
    tl.clip("ROCKET").enter_at("viral")          # a clip by asset, id, element or text; a word by text or id
    tl.close_gap(before="Live.")                 # ripple: everything after moves, VO/music/SFX stay in sync
    print(tl.check())                            # validate + what moved (timeline seconds) + lint on the changed cuts
    tl.publish("rocket on 'viral'")

Units, once: every time this API takes or returns is TIMELINE SECONDS (the numbers
``timelines show``, ``visualize --at`` and ``lint`` print), unless the argument is
called ``frames``. The document stores shot-relative seconds (clip ``at``) and
milliseconds (placements); the API converts, so you never do.

Intent: moving a clip *to a word* stores an anchor ``{word, text, offset_s}`` on
the clip. ``retime()`` (or a VO take swap) moves every anchored clip back onto its
word, so a re-recorded line never strands its overlays. Moving a clip to a plain
time drops its anchor; ``nudge`` keeps it and changes its offset.

Addresses are the ones the CLI prints: cut numbers (``tl.cut(17)``, the same
numbering as show, lint and visualize), clip ids or their prefix (``c22-02``),
asset keys (``ROCKET``), element ids (``am-tweet``), on-screen text, and words
(``"viral"``, ``"n20b:20"``, ``tl.word("tool", n=2)``).
"""
from __future__ import annotations

import contextlib
import copy
import dataclasses
import difflib
import json
import math
import os
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from astrid.core.timeline import moments as mo
from astrid.sdk import timeline_intent as intent
from astrid.core.timeline.cuts import (
    bundle_fps,
    clip_duration,
    occurrences_from_bundle,
    picture_cuts,
)

__all__ = [
    "Checkout", "Clip", "Cut", "Word", "Voice", "TimelineEditError", "EDITABLE_NOTE",
    "draft_path", "drafts_root", "find_draft", "resolve_ids",
]

# Fields of a checkout that are provenance, not content. They are written compactly
# (one line each) so a text edit of the content can never match them by accident.
BASE_KEYS = ("base_parent", "base_parent_payload", "base_placements", "source_mapping")
SHOT_BASE_PREFIX = "base_"
DERIVED_PREFIX = "_"          # derived, read-only helpers in the file: stripped before check/publish
EDITABLE_NOTE = (
    "Edit `at`/`hold` (shot-relative seconds) or `_timeline` (timeline seconds; check converts it). "
    "Keys starting with '_' are derived and refreshed on save; base_* and source_mapping are provenance."
)
ELEMENT_TRACK = {
    "am-pixel-wipe": "chrome", "am-type": "type", "am-callout": "fx", "am-discord": "fx", "am-flap": "fx",
    "am-ui-sketch": "fx", "am-burst": "fx", "am-tweet": "fx", "am-quote": "fx", "am-terminal": "fx",
    "am-orbit": "fx", "am-orgchart": "fx", "am-presenter": "sprite", "am-sprite": "sprite",
    "am-snap-plate": "plate", "am-churn": "plate", "am-footage": "plate", "am-droste": "plate", "am-seasons": "plate",
}
AUDIO_KINDS = {"audio", "vo", "music", "sfx", "voice", "voiceover"}
TIME_RE = re.compile(r"^@?(?:(\d+):)?(\d+(?:\.\d+)?)s?$")
WORD_ID_RE = re.compile(r"^([A-Za-z0-9_.-]+):(\d+)$")
CANVAS = (1920, 1080)
SAFE_MARGIN = (96, 54)        # title-safe at 1920x1080 (90%)


LOGICAL_PX = 6  # canvas px per logical px (the 320×180 grid of 1920×1080)
LOGICAL_GRID_ELEMENTS = frozenset({"am-sprite"})  # elements whose x/y are STORED in logical px
POSITION_KEYS = frozenset({"x", "y"})


def to_canvas(element: str, key: str, value: Any) -> Any:
    """Stored → canvas px (what every surface shows): am-sprite x/y and keyframe x/y ×6."""
    if element not in LOGICAL_GRID_ELEMENTS:
        return value
    if key in POSITION_KEYS and isinstance(value, (int, float)) and not isinstance(value, bool):
        return value * LOGICAL_PX
    if key == "keyframes" and isinstance(value, list):
        return [{**k, **{a: k[a] * LOGICAL_PX for a in POSITION_KEYS if isinstance(k.get(a), (int, float))}}
                if isinstance(k, Mapping) else k for k in value]
    return value


def from_canvas(element: str, key: str, value: Any) -> Any:
    """Canvas px (what you write) → stored. ``"1290px"`` is accepted too. am-sprite snaps to its 6-px grid."""
    if isinstance(value, str) and re.fullmatch(r"-?\d+(\.\d+)?px", value.strip()):
        value = float(value.strip()[:-2])
    if element not in LOGICAL_GRID_ELEMENTS:
        return round(value) if isinstance(value, float) and key in POSITION_KEYS and value.is_integer() else value
    if key in POSITION_KEYS and isinstance(value, (int, float)) and not isinstance(value, bool):
        return round(value / LOGICAL_PX)
    if key == "keyframes" and isinstance(value, list):
        return [{**k, **{a: round(k[a] / LOGICAL_PX) for a in POSITION_KEYS if isinstance(k.get(a), (int, float))}}
                if isinstance(k, Mapping) else k for k in value]
    return value


_FORMULA_VALUE = re.compile(r"^\s*(?:ƒ|f|mark)\(\s*(?:(?P<mark>[A-Za-z][\w-]*?)\s*)?(?P<off>[+−-]\s*\d+(?:\.\d+)?)?\s*\)\s*$")


def parse_moment_value(value: Any, current_expr: Mapping[str, Any] | None = None) -> str | None:
    """A value meant as a moment: ``ƒ("adapt" in w05c)``, or (for a param already on a moment, or plainly a
    moment: quoted words, ``after``/``beat``/``in``) ``'"adapt" in w05c'``. Canonical text, else None."""
    if not isinstance(value, str):
        return None
    text = value.strip()
    m = re.fullmatch(r"(?:ƒ|f)\((.*)\)", text)
    explicit = m is not None
    if explicit:
        text = m.group(1).strip()
    looks = explicit or text.startswith(('"', "“", "after ", "beat ", "downbeat ")) or bool(current_expr and current_expr.get("moment"))
    if not looks:
        return None
    try:
        moment = mo.parse(text)
    except mo.MomentError:
        if explicit:
            raise TimelineEditError(f"{value!r} is not a moment (e.g. ƒ(\"adapt\" in w05c), ƒ(beat 2 after \"Astrid\"))") from None
        return None
    return mo.format_moment(moment)


def parse_formula_value(value: Any) -> dict[str, Any] | None:
    """``"ƒ(B2-HAND -42)"`` / ``"f(-60)"`` / ``"mark(B2-HAND +12)"`` → {mark, offset (canvas px)}; else None."""
    if not isinstance(value, str):
        return None
    m = _FORMULA_VALUE.match(value)
    if not m or (m.group("mark") is None and m.group("off") is None):
        return None
    off = (m.group("off") or "0").replace("−", "-").replace(" ", "")
    return {"mark": m.group("mark"), "offset": float(off)}


def formula_from_spec(spec: Mapping[str, Any], element: str, key: str, current: Mapping[str, Any]) -> dict[str, Any]:
    """A mark formula from a canvas-px spec, keeping the current formula's mark/axis when not given."""
    mark = spec.get("mark") or current.get("mark")
    if not mark:
        raise TimelineEditError(f"{key}: name the slot mark, e.g. {key}=ƒ(B2-HAND -42)")
    axis = current.get("axis") or (key if key in POSITION_KEYS else "x")
    if current.get("mark") == mark and abs(mark_offset_canvas(current) - float(spec["offset"])) < 0.005:
        return dict(current)  # the same formula, however it was stored: unchanged
    offset = round(float(spec["offset"]), 2)  # stored as written, in canvas px: no ÷6 round-trip noise
    return {"mark": mark, "axis": axis, "offset": int(offset) if offset == int(offset) else offset, "unit": "canvas"}


def mark_offset_canvas(expr: Mapping[str, Any]) -> float:
    """A mark formula's offset in canvas px, whatever unit it was stored in."""
    offset = _num(expr.get("offset"))
    return offset * LOGICAL_PX if expr.get("unit", "logical") == "logical" else offset


def formula_short(expr: Any, element: str) -> str:
    """A formula as the sheet writes it, in canvas px: ``ƒ(B2-HAND -42)``; a moment: ``ƒ("viral" …)``."""
    if isinstance(expr, Mapping) and "mark" in expr:
        return f"ƒ({expr['mark']} {round(mark_offset_canvas(expr), 2):+g})"
    if isinstance(expr, Mapping) and expr.get("moment"):
        return f"ƒ({expr['moment']})"
    if isinstance(expr, Mapping) and "words_of" in expr:
        return f"ƒ(words of {expr['words_of']})"
    return "ƒ"


class TimelineEditError(ValueError):
    """An edit that cannot be applied; the message says what to do instead."""


def _norm(text: Any) -> str:
    return re.sub(r"[^\w']", "", str(text or "").lower())


def _num(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _r(value: float) -> float:
    return round(float(value), 6)


# ----------------------------------------------------------------------- views

@dataclass(frozen=True)
class Word:
    """One spoken word. ``start``/``end`` are timeline seconds; ``id`` is ``<segment>:<index>``."""

    id: str
    text: str
    start: float
    end: float
    segment: str
    index: int
    clip_id: str
    shot_id: str

    def __float__(self) -> float:
        return self.start

    def __str__(self) -> str:
        return f"{self.text!r} {self.start:.3f}–{self.end:.3f} s [{self.id}]"


class _LazyParams(dict):
    """An empty params dict that attaches itself to the clip on the first write."""

    def __init__(self, data: dict[str, Any]):
        super().__init__()
        self._data = data

    def _attach(self) -> dict[str, Any]:
        return self._data.setdefault("params", {})

    def __setitem__(self, key, value):
        self._attach()[key] = value

    def update(self, *args, **kwargs):
        self._attach().update(*args, **kwargs)

    def setdefault(self, key, default=None):
        return self._attach().setdefault(key, default)


class Clip:
    """A live view of one clip. Reads and writes go straight to the checkout document."""

    def __init__(self, tl: "Checkout", shot_id: str, data: dict[str, Any]):
        self._tl = tl
        self.shot_id = shot_id
        self.data = data

    # identity -----------------------------------------------------------
    @property
    def id(self) -> str:
        return str(self.data.get("id") or "")

    @property
    def element(self) -> str:
        return str(self.data.get("clipType") or "media")

    @property
    def asset(self) -> str | None:
        value = self.data.get("asset")
        return str(value) if isinstance(value, str) else None

    @property
    def track(self) -> str:
        return str(self.data.get("track") or "")

    @property
    def params(self) -> dict[str, Any]:
        """The element params (live: changes write through). A clip without params reads as ``{}``
        and is not given an empty one just by looking (that would read as an edit)."""
        value = self.data.get("params")
        if isinstance(value, dict):
            return value
        return _LazyParams(self.data)

    @property
    def text(self) -> str | None:
        params = self.data.get("params")
        if isinstance(params, Mapping):
            for key in ("text", "title", "body", "name"):
                if isinstance(params.get(key), str) and params[key].strip():
                    return params[key]
        return None

    @property
    def is_audio(self) -> bool:
        return self._tl._is_audio(self.shot_id, self.data)

    @property
    def anchor(self) -> str | None:
        """The moment this clip starts on (``"viral"``, ``+0.8s`` …), or None."""
        return intent.on(self.data)

    @property
    def moments(self) -> dict[str, Any]:
        """``{on, until, for}``: when this clip starts and ends, as stored (None where unset)."""
        return {"on": intent.on(self.data), "until": intent.until(self.data), "for": intent.for_s(self.data)}

    @property
    def cut_id(self) -> str | None:
        return intent.cut_of(self.data)

    @property
    def layer_name(self) -> str | None:
        return intent.layer_of(self.data)

    @property
    def address(self) -> str:
        """``c22.rocket`` when the clip has a cut and a layer name, else its clip id."""
        cut, layer = self.cut_id, self.layer_name
        return f"{cut}.{layer}" if cut and layer else self.id

    # clocks --------------------------------------------------------------
    @property
    def at(self) -> float:
        """Shot-relative start (the stored field). Prefer ``start``."""
        return _num(self.data.get("at"))

    @property
    def start(self) -> float:
        """Timeline seconds."""
        return _r(self._tl._shot_start(self.shot_id) + self.at)

    @property
    def duration(self) -> float:
        return _r(clip_duration(self.data))

    @property
    def end(self) -> float:
        return _r(self.start + self.duration)

    @property
    def start_frame(self) -> int:
        return int(round(self.start * self._tl.fps))

    @property
    def end_frame(self) -> int:
        return int(round(self.end * self._tl.fps))

    def __repr__(self) -> str:
        what = self.asset or (f'"{self.text[:28]}"' if self.text else "")
        anchor = f" on {self.anchor}" if self.anchor else ""
        return f"<Clip {self.address} {self.element} {what} {self.start:.3f}–{self.end:.3f} s{anchor}>"

    # edits ---------------------------------------------------------------
    def on(self, moment: Any) -> "Clip":
        """Start on a moment: ``"viral"``, ``after "Astrid"``, ``beat 2 after "Astrid"``, ``c22``, ``+0.8s``.

        Stored as written (seconds are only the cache), then resolved now. A plain number
        is a time: it becomes an offset from the clip's cut (or a fixed time without a cut)."""
        orphan_cut = intent.cut_of(self.data) if intent.orphan(self.data) and self.track == "plate" else None
        old_start = self.start
        for clip in self._sequence_mates():  # a new moment re-homes an orphan (a whole sequence)
            intent.set_orphan(clip.data, None)
        if orphan_cut:
            # re-homing a cut's picture brings the whole cut: its other orphans move by the same amount
            text = self._tl._moment_text(moment)
            t = self._tl._moment_time(text, self, in_point=True)
            delta = t - old_start
            for clip in self._tl._cut_clips(orphan_cut):
                if clip.data is not self.data and intent.orphan(clip.data):
                    clip._set_start(self._tl._frame(clip.start + delta + 1e-6))
                    if intent.sequence(clip.data) or not intent.on(clip.data) or intent.on(clip.data).startswith(("+", "-")):
                        intent.set_orphan(clip.data, None)  # its place is relative to the cut: home again
            self._tl._mcache = None
        self._tl._place_on(self, moment)
        self._tl.retime()  # its end follows its rule (until / for / its cut) right away
        return self

    def keep(self) -> "Clip":
        """Accept an orphan where it is (a fixed time, no moment): it stops blocking publish. Params on a
        moment that no longer resolves keep their last value as a fixed one."""
        for clip in self._sequence_mates():
            intent.set_orphan(clip.data, None)
        on = intent.on(self.data)
        if on:
            try:
                self._tl._moment_time(on, self)
            except TimelineEditError:
                intent.set_on(self.data, None)
        for path, expr in intent.formulas(self.data).items():
            if isinstance(expr, Mapping) and expr.get("moment"):
                try:
                    self._tl._evaluate(self, expr, {}, self._tl.words(), self._tl.slots)
                except TimelineEditError:
                    intent.set_formula(self.data, path, None)  # keeps its last value
        return self

    def _sequence_mates(self) -> list["Clip"]:
        seq = intent.sequence(self.data)
        if seq is None:
            return [self]
        return [c for c in self._tl.clips() if (intent.sequence(c.data) or ("",))[0] == seq[0]]

    def remove_layer(self) -> list[str]:
        """Remove this layer (a whole sequence, when it is one of its steps). Returns the clip ids removed."""
        mates = self._sequence_mates()
        for clip in mates:
            clip.remove()
        return [c.id for c in mates]

    def until(self, moment: Any) -> "Clip":
        """End on a moment (``"Astrid"``, ``after "viral"``, ``c26``, ``end``); its start does not move."""
        text = self._tl._moment_text(moment)
        t = self._tl._moment_time(text, self)
        if t <= self.start + 0.5 / self._tl.fps:
            raise TimelineEditError(f"{self.address}: {text} is at {t:.3f} s, before the clip starts ({self.start:.3f} s)")
        intent.set_until(self.data, text)
        intent.set_for(self.data, None)
        _set_length(self.data, self._tl._frame(t) - self.start)
        self._tl.retime()
        return self

    def enter_at(self, when: Any, *, offset: float = 0.0, anchor: bool = True) -> "Clip":
        """Start at a word or a time, keeping the length. A word becomes the clip's ``on`` moment."""
        word = self._tl._as_word(when)
        if word is not None and anchor:
            moment = mo.word_moment(word, self._tl.words())
            if offset:
                moment = mo.parse(f"{mo.format_moment(moment)} {mo.offset_text(offset, self._tl.fps)}")
            return self.on(moment)
        t = (word.start if word else self._tl.time(when)) + offset
        return self.on(t)

    def nudge(self, seconds: float = 0.0, *, frames: int = 0) -> "Clip":
        """Move by seconds and/or frames. A clip on a moment keeps it: the nudge becomes its offset (``"viral" +2f``)."""
        current = intent.on(self.data)
        if current:
            moment = mo.parse(current).with_offset(seconds=float(seconds), frames=int(frames))
            return self.on(moment)
        delta = float(seconds) + frames / self._tl.fps
        self._set_start(self._tl.quantize(self.start + delta))
        return self

    def hold_for(self, seconds: float) -> "Clip":
        """A literal length (no moment behind it). Prefer ``until`` when the end means something."""
        return self.set_duration(seconds)

    def set_duration(self, seconds: float) -> "Clip":
        if seconds <= 0:
            raise TimelineEditError(f"{self.address}: a duration must be positive (got {seconds})")
        _set_length(self.data, self._tl.quantize(seconds))
        if intent.cut_of(self.data) or intent.until(self.data):
            intent.set_until(self.data, None)
            intent.set_for(self.data, self._tl.quantize(seconds))
        return self

    def extend(self, seconds: float = 0.0, *, frames: int = 0) -> "Clip":
        """Lengthen (or, negative, shorten) the clip at its end. Nothing else moves."""
        return self.set_duration(self.duration + float(seconds) + frames / self._tl.fps)

    def end_at(self, when: Any, *, offset: float = 0.0) -> "Clip":
        if self._tl._as_word(when) is not None or (isinstance(when, str) and not TIME_RE.match(when.strip())):
            moment = self._tl._moment_text(when)
            if offset:
                moment = f"{moment} {mo.offset_text(offset, self._tl.fps)}"
            return self.until(moment)
        return self.set_duration(self._tl.quantize(self._tl.time(when) + offset) - self.start)

    def get(self, key: str) -> Any:
        """A param as you would write it: positions (x, y) in CANVAS px for every element.

        (``clip.params`` is the stored dict; an ``am-sprite`` stores x/y on its 320×180 grid, ×6.)"""
        return to_canvas(self.element, key, self.params.get(key))

    def set(self, **params: Any) -> "Clip":
        """Update element params (``x``, ``size``, ``text`` …). Positions are canvas px for every element.

        A param computed by a formula (``x = ƒ(B2-HAND −42)``, ``states[3].at = ƒ("adapt" in w05c)``)
        is never silently recomputed over your value: a plain value replaces the formula (and
        ``changes()`` says so); ``x="ƒ(B2-HAND -60)"`` edits a mark, and a moment
        (``'"adapt" in w05c'``, ``'ƒ(beat 2 after "Astrid")'``) puts a time-valued param on that moment.
        Nested params take their address: ``clip.set(**{"states[3].at": '"adapt" in w05c'})``."""
        for key, value in params.items():
            self.set_param(key, value)
        return self

    def set_param(self, path: str, value: Any) -> "Clip":
        """Set one param by its address inside the clip (``x``, ``states[3].at``, ``stamp.at``)."""
        top = re.split(r"[.\[]", path, maxsplit=1)[0]
        formulas = intent.formulas(self.data)
        full = f"params.{path}"
        current_expr = formulas.get(full) or {}
        spec = parse_formula_value(value)
        moment = None
        if spec is None:
            moment = parse_moment_value(value, current_expr)
        if spec is not None:  # a slot mark: ƒ(B2-HAND -42)
            expr = formula_from_spec(spec, self.element, top if path == top else path.rsplit(".", 1)[-1], current_expr)
            intent.set_formula(self.data, full, expr)
            _set_path(self.data, full, self._tl._evaluate(self, expr, {}, self._tl.words(), self._tl.slots))
            return self
        if moment is not None:  # a time-valued param on a moment
            current = _get_path(self.data, full)
            unit = current_expr.get("as") or ("clip_frame" if isinstance(current, int) and not isinstance(current, bool)
                                               else "clip_seconds")
            expr = {k: v for k, v in current_expr.items() if k in ("min", "max", "step", "snap", "offset_frames")} if current_expr.get("moment") else {}
            expr.update({"moment": moment, "as": unit})
            try:
                resolved = self._tl._evaluate(self, expr, {}, self._tl.words(), self._tl.slots)
            except TimelineEditError as exc:
                raise TimelineEditError(f"{self.address}.{path}: {exc}") from None
            intent.set_formula(self.data, full, expr)
            _set_path(self.data, full, resolved)
            return self
        for existing in [p for p in formulas if p == full or p.startswith((f"{full}[", f"{full}."))]:
            intent.set_formula(self.data, existing, None)  # a fixed value replaces the formula: never a silent revert
        if path == top:
            self.params[top] = copy.deepcopy(from_canvas(self.element, top, value))
        else:
            self.data.setdefault("params", {})
            _set_path(self.data, full, copy.deepcopy(value))
        return self

    def clear_asset(self) -> "Clip":
        """Take the asset off this clip (e.g. an am-footage slot that should draw its slot card again)."""
        self.data.pop("asset", None)
        return self

    def swap_asset(self, asset: Any) -> "Clip":
        """Point the clip at another asset: a registry key, a ``{media_id, …}`` entry, a local file (imported)
        or a media handle (``run:<id>/music``, ``ref:NAME``, ``sha256:…``).

        An audio clip that played its whole file plays the whole new one (and never runs past its end).
        A music clip takes the beat grid made with the new file: the ``beats`` output of the run that
        made it, or ``NAME.beats.json``/``beats.json`` beside a local file; every beat moment re-resolves.
        If no grid comes along, check blocks until you attach one (``set_beats``) or keep the old one."""
        registry = self._tl._registry(self.shot_id)
        old_entry = copy.deepcopy(registry.get(str(self.data.get("asset"))) or {})
        key = self._tl._register_asset(self.shot_id, asset)
        self.data["asset"] = key
        new_entry = self._tl._registry(self.shot_id).get(key) or {}
        if self.is_audio and not intent.cut_of(self.data) and new_entry.get("media_id") != old_entry.get("media_id"):
            self._tl._follow_media(self, old_entry, new_entry, asset)
        return self

    def set_beats(self, source: Any) -> "Clip":
        """Attach this music clip's beat grid: a beats.json path, a media handle (``run:<id>/beats``), a
        dict like beats.json, or ``"keep"`` (the grid it has is right for the file it now plays).

        Every beat moment (``beat 2 after "Astrid"``, ``downbeat 1 before c30``) re-resolves on the new
        grid; what moved is in ``tl.notes`` and ``tl.changes()``."""
        if isinstance(source, str) and source.strip().lower() == "keep":
            value = intent._app(self.data).get("beats")
            if not value:
                raise TimelineEditError(f"{self.address} has no beat grid to keep; attach one: set_beats(PATH|HANDLE)")
            grid = dict(value) if isinstance(value, Mapping) else {"beats": intent.beat_sources(self.data), "time": "cue_seconds"}
            label = intent.beats_label(self.data) or "its grid"
        else:
            grid, label = load_beats(str(self._tl.bundle.get("project_id") or ""), source)
        self._tl._attach_beats(self, grid, label)
        return self

    def keyframe_at(self, index: int, when: Any) -> "Clip":
        """Move element keyframe ``index`` to a word or time (clip frames, snapped to ``stepFrames``)."""
        keyframes = self.params.get("keyframes")
        if not isinstance(keyframes, list) or not 0 <= index < len(keyframes):
            raise TimelineEditError(f"{self.id} has no keyframe {index}; it has {len(keyframes or [])}")
        step = max(1, int(_num(self.params.get("stepFrames"), 1)))
        frame = int((self._tl.time(when) - self.start) * self._tl.fps + 1e-6) // step * step
        delta = frame - int(_num(keyframes[index].get("frame")))
        for key in keyframes[index:]:
            key["frame"] = int(_num(key.get("frame"))) + delta
        return self

    def remove(self) -> None:
        clips = self._tl._internal(self.shot_id)["clips"]
        clips[:] = [c for c in clips if c is not self.data]

    def _set_start(self, seconds: float) -> None:
        self.data["at"] = _r(seconds - self._tl._shot_start(self.shot_id))


class Cut:
    """One picture cut (the numbering show/lint/visualize print)."""

    def __init__(self, tl: "Checkout", raw: Mapping[str, Any]):
        self._tl = tl
        self.raw = raw
        self.n = int(raw["index"])
        self.start = float(raw["start"])
        self.end = float(raw["end"])
        self.shot_id = str(raw["shot_id"])
        self.shot = str(raw.get("shot") or self.shot_id)

    @property
    def duration(self) -> float:
        return _r(self.end - self.start)

    @property
    def picture(self) -> Clip | None:
        clip = self.raw.get("clip")
        return Clip(self._tl, self.shot_id, clip) if isinstance(clip, dict) else None

    @property
    def steps(self) -> list[Clip]:
        """The picture clips of a sequence cut (a time-lapse), in order; one item otherwise."""
        sequence = self.raw.get("sequence") or {}
        ids = list(sequence.get("clip_ids") or [])
        if not ids:
            return [self.picture] if self.picture else []
        return [self._tl.clip(i) for i in ids]

    def step(self, n: int) -> Clip:
        steps = self.steps
        if not 1 <= n <= len(steps):
            raise TimelineEditError(f"cut {self.n} has {len(steps)} step(s); ask for 1–{len(steps)}")
        return steps[n - 1]

    @property
    def layers(self) -> list[Clip]:
        return [Clip(self._tl, self.shot_id, span["clip"]) for span in self.raw.get("layers") or []]

    @property
    def clips(self) -> list[Clip]:
        return ([self.picture] if self.picture else []) + self.layers

    def layer(self, query: str) -> Clip:
        return self._tl._pick(self.clips, query, where=f"cut {self.n}")

    @property
    def words(self) -> list[Word]:
        return [w for w in self._tl.words() if self.start - 1e-6 <= w.start < self.end - 1e-6]

    def shorten(self, seconds: float, *, ripple: bool = True) -> "Cut":
        """Take ``seconds`` off the end of this cut; with ripple, everything after moves up."""
        if not 0 < seconds < self.duration:
            raise TimelineEditError(f"cut {self.n} is {self.duration:.3f} s; shorten by less than that")
        if ripple:
            self._tl.ripple_delete(self.end - seconds, self.end)
        else:
            for clip in self.clips:
                if abs(clip.end - self.end) < 1.0 / self._tl.fps:
                    clip.set_duration(clip.duration - seconds)
        return self

    def __repr__(self) -> str:
        pic = self.picture
        return f"<Cut {self.n} {self.start:.3f}–{self.end:.3f} s {self.shot} · {pic.asset if pic else '-'} · {len(self.layers)} layer(s)>"


class Voice:
    """One VO line (all its audio clips), with its words and the silence declared after it."""

    def __init__(self, tl: "Checkout", segment: str):
        self._tl = tl
        self.segment = segment

    @property
    def clips(self) -> list[Clip]:
        found = [c for c in self._tl.clips() if c.is_audio and intent.line(c.data) == self.segment]
        if not found:
            known = sorted({intent.line(c.data) for c in self._tl.clips() if c.is_audio and intent.line(c.data)})
            raise TimelineEditError(f"no VO line {self.segment!r}; lines are: {', '.join(known)}")
        return sorted(found, key=lambda c: c.start)

    @property
    def words(self) -> list[Word]:
        return [w for w in self._tl.words() if w.segment == self.segment]

    @property
    def text(self) -> str:
        """The line's script text (as declared; else its words joined)."""
        declared = intent.line_text(self.clips[0].data)
        return declared if declared is not None else " ".join(w.text for w in self.words)

    def replace(self, media: Any, *, words: Any, ripple: bool = True, text: str | None = None) -> list[tuple[str, float, float]]:
        """Swap this line for another take; anchored clips follow their words.

        ``media`` is a registry key, a ``{media_id, …}`` entry or a local WAV (imported).
        ``words`` is a words.json path, ``{"words": [...]}`` or ``[[start, end, text], …]``
        (seconds from the take's start). With ``ripple`` the timeline after the line
        opens or closes by the length difference, so the next line does not overlap.
        Returns the anchored clips that moved: ``[(clip_id, old_start, new_start)]``.
        """
        pieces = self.clips
        first = pieces[0]
        before = self._tl._piece_gaps()
        if pieces[-1].id in before:  # one take now: the line's last silence follows it
            before[first.id] = before[pieces[-1].id]
        old_end = self.speech_end
        anchored = [(c, c.start) for c in self._tl.clips() if c.anchor]
        new_words = _read_words(words)
        if not new_words:
            raise TimelineEditError("the new take has no words; pass its words.json")
        tail = 0.06
        length = new_words[-1][1] + tail
        for piece in pieces[1:]:
            piece.remove()
        key = self._tl._register_asset(first.shot_id, media)
        data = first.data
        data["asset"] = key
        data["from"], data["to"] = 0.0, _r(length)
        data.pop("hold", None)
        intent.set_line(data, self.segment, [[_r(s), _r(e), t] for s, e, t in new_words])
        if text is not None:
            intent.set_line_text(data, text)
        elif intent.line_text(data) is not None and _norm(intent.line_text(data)) != _norm(" ".join(t for _s, _e, t in new_words)):
            self._tl.notes.append(f"line {self.segment}: the new take's words differ from its script text; pass text= to update the narration")
        self._tl.report = self._tl.reflow(gaps=before, points={first.id: old_end}) if ripple else []
        self._tl.retime()
        return [(c.id, old, c.start) for c, old in anchored if abs(c.start - old) > 1e-6]

    @property
    def speech_end(self) -> float:
        words = self.words
        return words[-1].end if words else self.clips[-1].end

    @property
    def gap_after(self) -> float | None:
        """The declared silence after this line, if any."""
        return intent.gap_after(self.clips[-1].data)

    def set_gap_after(self, seconds: float, *, reflow: bool = True) -> "Voice":
        """Declare the silence after this line; with ``reflow`` everything after it moves to honour it."""
        intent.set_gap_after(self.clips[-1].data, _r(seconds))
        if reflow:
            self._tl.reflow()
        return self


class _MomentContext:
    """What a moment resolves against: the words, the music's beats, the cuts, and the clip's own cut."""

    def __init__(self, tl: "Checkout", clip: Clip | None):
        self.tl, self.clip, self.fps = tl, clip, tl.fps

    def words(self):
        cache = tl_cache(self.tl)
        if "words" not in cache:
            cache["words"] = self.tl.words()
        return cache["words"]

    def beats(self, kind: str):
        out = []
        for clip in self.tl.clips(audio=True):
            if clip.track != "music" and not intent.beat_sources(clip.data):
                continue
            speed = _num(clip.data.get("speed"), 1.0) or 1.0
            src0 = _num(clip.data.get("from"))
            for b in intent.beat_sources(clip.data, kind):
                t = clip.start + (b - src0) / speed
                if clip.start - 1e-6 <= t < clip.end - 1e-6:
                    out.append(t)
        return sorted(out)

    def cut_start(self, cut_id: str) -> float:
        cut_id = self.tl._cut_id(cut_id) or cut_id
        for g in self.tl._cut_groups():
            if g["id"] == cut_id:
                return g["start"]
        raise mo.MomentError(self.tl._no_cut(cut_id))

    def cut_end(self, cut_id: str) -> float:
        cut_id = self.tl._cut_id(cut_id) or cut_id
        spans = self.tl._cut_spans()
        if cut_id not in spans:
            raise mo.MomentError(self.tl._no_cut(cut_id))
        end = spans[cut_id][1]
        return float(end) if end is not None else self.tl.duration

    def own_cut(self):
        """The clip's cut; a moment asked of the film itself (``tl.time("end")``) is relative to the whole film."""
        return self.tl._own_cut_span(self.clip) if self.clip is not None else (0.0, self.tl.duration)

    def line_in_point(self, line: str):
        """Where the take begins on the timeline (its file's zero: a trimmed or tightened take starts later)."""
        try:
            first = Voice(self.tl, line).clips[0]
        except TimelineEditError:
            return None
        speed = _num(first.data.get("speed"), 1.0) or 1.0
        return first.start - _num(first.data.get("from")) / speed


def tl_cache(tl: "Checkout") -> dict[str, Any]:
    if tl._mcache is None:
        tl._mcache = {}
    return tl._mcache


# ------------------------------------------------------------------ checkout

class Checkout:
    """A complete, editable copy of one timeline head (parent → shots → internal timelines)."""

    def __init__(self, bundle: dict[str, Any], *, path: Path | None = None):
        self.bundle = bundle
        self.path = path
        self.fps = bundle_fps(bundle) or 30.0
        self.notes: list[str] = []
        self.report: list[str] = []  # what the last re-flow did, in plain words
        self._mcache: dict[str, Any] | None = None
        # Re-flow keeps the music bed WHOLE (one clip, its source untouched) and reports how far the
        # picture after an edit moved against its beats. "seams" cuts the bed on a beat instead.
        self.music = "whole"
        self._keep_inside = False  # a removal keeps the clips inside the window (as orphans) instead of deleting them
        self._kept: list[str] = []
        self._outside: list[str] = []
        self._journal: list[tuple[str, str | None]] = []  # (label, the document before that edit), one per edit
        self._edit_depth = 0
        self._orphan_reason = "its line was removed"
        self._disk: str | None = None  # the file text this handle loaded (or last saved): what another writer may change
        self.merged: list[str] = []      # what the last save merged in from another writer, in plain words

    # ---- open / save ------------------------------------------------------
    @classmethod
    def open(cls, project: str, timeline: str, *, revision_id: str | None = None, client: Any = None) -> "Checkout":
        """Check out the current head (read-only until ``publish``)."""
        bundle = fetch_bundle(project, timeline, revision_id=revision_id, client=client)
        return cls(bundle)

    @classmethod
    def load(cls, path: str | Path) -> "Checkout":
        """Open a checkout file (``timelines checkout`` or ``save`` wrote it). Applies `_timeline` edits."""
        path = Path(path)
        text = path.read_text(encoding="utf-8")
        raw = json.loads(text)
        tl = cls({}, path=path)
        tl.bundle = tl._absorb(raw)
        tl.fps = bundle_fps(tl.bundle) or 30.0
        tl._disk = text
        return tl

    HISTORY = 60  # edits of a working copy kept for undo

    def save(self, path: str | Path | None = None, *, force: bool = False) -> Path:
        """Write the checkout: content pretty-printed, provenance compact, both clocks on every clip.

        A working copy keeps one undo step per EDIT made since the last save (each verb, each
        API call such as ``clip.on(...)``, each applied sheet), so ``undo`` goes back one edit.

        Two writers on one working copy never lose each other's edits: if the file changed since
        this handle loaded it, the save is a three-way merge (what you loaded → your edit, onto
        what is there now), like publish. Edits to different clips merge (``self.merged`` says
        what came in); the same clip changed by both refuses, unless ``force`` (yours wins)."""
        target = Path(path or self.path or "timeline.checkout.json")
        with _file_lock(target):
            return self._save_locked(target, force=force)

    def _save_locked(self, target: Path, *, force: bool) -> Path:
        self.merged = []
        if self._disk is not None and self.path and Path(self.path) == target and target.is_file():
            now = target.read_text(encoding="utf-8")
            if now != self._disk:
                self._merge_writer(target, now, force=force)
        text = _serialize(self._annotated())
        if target.is_file() and _is_draft(target):
            previous = target.read_text(encoding="utf-8")
            journal = list(self._journal)
            if previous != text and not journal:  # edited without the API (by hand): one step
                journal = [("an edit", None)]
            if journal:
                history = _history_dir(target)
                history.mkdir(parents=True, exist_ok=True)
                for k, (label, snapshot) in enumerate(journal):
                    # the first edit's "before" is exactly what was saved; later ones are in-memory snapshots
                    _write_step(history, label, previous if (k == 0 or snapshot is None) else _draft_text(snapshot))
                for old in sorted(history.glob("*.json"))[:-self.HISTORY]:
                    old.unlink()
                import shutil

                shutil.rmtree(_redo_dir(target), ignore_errors=True)  # a new edit ends the redo chain
        tmp = target.with_name(f".{target.name}.{os.getpid()}.tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, target)  # a reader never sees half a file
        self._journal = []
        self.path = target
        self._disk = text
        return target

    def _merge_writer(self, target: Path, now: str, *, force: bool) -> None:
        """Another writer saved this file since we loaded it: put our edit on top of theirs."""
        import datetime as _dt

        mine = Checkout({}, path=target)
        base = mine._absorb(json.loads(self._disk or "{}"))
        theirs = mine._absorb(json.loads(now))
        when = _dt.datetime.fromtimestamp(target.stat().st_mtime).strftime("%H:%M:%S")
        merged, conflicts = three_way(base, theirs, self.bundle, force=force, whole=True)
        if conflicts and not force:
            raise TimelineEditError(
                f"not saved: this working copy was saved by another writer at {when}, after you opened it, and you "
                f"both changed {len(conflicts)} of the same thing(s): " + "; ".join(conflicts[:6])
                + (f"; … {len(conflicts) - 6} more" if len(conflicts) > 6 else "")
                + ". Open it again (their edit is there) and redo yours, or save(force=True) to overwrite theirs.")
        before = Checkout(copy.deepcopy(base))
        theirs_said = describe_changes(before, Checkout(copy.deepcopy(theirs)))
        self.bundle, self._mcache = merged, None
        self.merged = [f"merged with another writer's save at {when}: kept their {len(theirs_said)} change(s)"
                       + (" (yours won where you both changed something)" if conflicts else "")] + theirs_said[:8]

    def undo(self, steps: int = 1) -> list[str]:
        """Go back ``steps`` edits of this working copy (``redo`` goes forward). Returns what was undone."""
        if not self.path:
            raise TimelineEditError("only a saved working copy can undo")
        target = Path(self.path)
        history = sorted(_history_dir(target).glob("*.json"))
        if not history:
            raise TimelineEditError("nothing to undo: this working copy has no earlier edit")
        undone = []
        current = target.read_text(encoding="utf-8")
        for step in reversed(history[-max(1, min(int(steps), len(history))):]):
            label, state = _read_step(step)
            _write_step(_redo_dir(target), label, current)
            current = state
            step.unlink()
            undone.append(label)
        target.write_text(current, encoding="utf-8")
        self._reload()
        return undone

    def redo(self, steps: int = 1) -> list[str]:
        """Re-apply edits that ``undo`` took back. Returns what was redone."""
        if not self.path:
            raise TimelineEditError("only a saved working copy can redo")
        target = Path(self.path)
        stack = sorted(_redo_dir(target).glob("*.json"))
        if not stack:
            raise TimelineEditError("nothing to redo")
        redone = []
        current = target.read_text(encoding="utf-8")
        for step in reversed(stack[-max(1, min(int(steps), len(stack))):]):
            label, state = _read_step(step)
            _write_step(_history_dir(target), label, current)
            current = state
            step.unlink()
            redone.append(label)
        target.write_text(current, encoding="utf-8")
        self._reload()
        return redone

    def _reload(self) -> None:
        fresh = Checkout.load(self.path)
        self.bundle, self._mcache, self._journal, self._disk = fresh.bundle, None, [], fresh._disk

    @contextlib.contextmanager
    def step(self, label: str):
        """One undo step for everything inside (a verb that makes several changes is one edit)."""
        if self._edit_depth:
            yield self
            return
        before = _snapshot(self)
        self._edit_depth += 1
        try:
            yield self
        finally:
            self._edit_depth -= 1
        if _snapshot(self) != before:
            self._journal.append((label, before))

    # ---- working copy (draft) --------------------------------------------------
    @classmethod
    def draft(cls, project: str, timeline: str, name: str | None = None, *, client: Any = None, fresh: bool = False) -> "Checkout":
        """The timeline's working copy: the existing draft, or a new one checked out from the head.

        Drafts live in Astrid's data root (never a file you name). ``show``, ``lint``, ``diff``,
        ``visualize`` and ``render --draft`` read the same working copy; ``save()`` writes it back.
        """
        project_id, timeline_id, _head = resolve_ids(project, timeline, client=client)
        name = name or current_draft(project_id, timeline_id)
        path = draft_path(project_id, timeline_id, name)
        if path.is_file() and not fresh:
            tl = cls.load(path)
        else:
            tl = cls(fetch_bundle(project, timeline, client=client))
            path.parent.mkdir(parents=True, exist_ok=True)
            tl.save(path)
        set_current_draft(project_id, timeline_id, name)  # commands that name no draft use this one
        tl.draft_name = name
        return tl

    def adopt(self, timeline: str, *, revision_id: str | None = None, client: Any = None) -> list[str]:
        """Make this working copy equal another timeline's head (or ``revision_id``): its cuts, narration
        (lines, takes, words), clips with all their intent, assets and the music's beats, chapters and slots.

        The working copy keeps its own timeline, shots and base, so ``changes()``, ``check()``, status and
        the three-way publish guard work as for any edit (one undo step). Returns what changed, in words."""
        from astrid.sdk.timeline_adopt import adopt_content, adopt_summary

        project = str(self.bundle.get("project_id") or "")
        source = fetch_bundle(project, timeline, revision_id=revision_id, client=client)
        before = Checkout(copy.deepcopy(self.bundle))
        bundle, notes = adopt_content(source, self.bundle)
        self.bundle, self._mcache = bundle, None
        self.fps = bundle_fps(bundle) or self.fps
        self.notes.extend(notes)
        rev = str((source.get("base_parent") or {}).get("revision_id") or revision_id or "")
        self.report = adopt_summary(before, self, f"{timeline}" + (f"@{rev.removeprefix('authoring-parent-revision-')[:8]}" if rev else ""))
        self.report += [f"  note: {n}" for n in notes]
        return list(self.report)

    def discard(self) -> None:
        """Delete this working copy and its undo history (nothing published is touched)."""
        if self.path and Path(self.path).is_file():
            Path(self.path).unlink()
        if self.path:
            import shutil

            shutil.rmtree(_history_dir(Path(self.path)), ignore_errors=True)

    @property
    def base_revision(self) -> str:
        """The published revision this working copy started from."""
        return str((self.bundle.get("base_parent") or {}).get("revision_id") or "")

    def edits(self) -> Mapping[str, Any]:
        """What this working copy changes against its base (``diff_bundles`` shape, timeline seconds)."""
        from astrid.sdk.timeline_cuts import base_bundle, diff_bundles

        document = self.document()
        return diff_bundles(base_bundle(document), document)

    def changed_cut_ids(self) -> list[str]:
        """The cut ids whose clips this working copy changes (added, removed, moved or edited)."""
        from astrid.sdk.timeline_cuts import base_bundle

        before = Checkout(base_bundle(self.document()))
        old = {c.id: c for c in before.clips()}
        new = {c.id: c for c in self.clips()}
        cut_ids: set[str] = set()
        for cid in set(old) | set(new):
            a, b = old.get(cid), new.get(cid)
            if a is None or b is None or a.data != b.data or round(a.start * self.fps) != round(b.start * self.fps):
                for clip in (a, b):
                    if clip is not None and intent.cut_of(clip.data):
                        cut_ids.add(intent.cut_of(clip.data))
        order = [g["id"] for g in self._cut_groups()]
        return sorted(cut_ids, key=lambda c: order.index(c) if c in order else len(order))

    def changes(self, against: Mapping[str, Any] | None = None) -> list[str]:
        """What this working copy changes, in plain words (against its base, or another bundle).

        ``c30.cover  now holds until "Astrid"  93.400 → 93.500 s (+0.10 s)``. Clips that only
        moved because something before them moved are summed up in one line per shift."""
        from astrid.sdk.timeline_cuts import base_bundle

        before = Checkout(copy.deepcopy(dict(against)) if against is not None else base_bundle(self.document()))
        return describe_changes(before, self)

    # ---- clocks -----------------------------------------------------------
    def quantize(self, seconds: float) -> float:
        return _r(round(float(seconds) * self.fps) / self.fps)

    def time(self, when: Any) -> float:
        """Timeline seconds of anything you can point at, in the same grammar as ``--on`` and ``--at``:
        a number, ``"1:02"``/``"62.5s"``/``"@62.5"``, a moment (``'"It" in s02'``, ``after "Astrid"``,
        ``beat 2 after "Astrid"``, ``c04``, ``c41 +1.8s``, ``end of c30``/``c30.end``, ``end`` = the film's end),
        an address (``c41.mink``: where it starts), a word id (``n20b:20``), a Word or a Clip.
        Moments floor to their frame, as a clip placed on them would start."""
        if isinstance(when, bool):
            raise TimelineEditError("a time cannot be a boolean")
        if isinstance(when, (int, float)):
            return float(when)
        if isinstance(when, Word):
            return when.start
        if isinstance(when, Clip):
            return when.start
        if isinstance(when, mo.Moment):
            when = mo.format_moment(when)
        text = str(when).strip()
        match = TIME_RE.match(text)
        if match:
            minutes = int(match.group(1) or 0)
            return minutes * 60 + float(match.group(2))
        if WORD_ID_RE.match(text):
            return self.word(text).start
        from astrid.sdk import timeline_address as ta

        try:
            target = ta.resolve(self, text, prefer="time")
        except ta.AddressError as exc:
            raise TimelineEditError(str(exc)) from None
        if target.kind == "range":
            raise TimelineEditError(f"{text!r} is a range ({target.start:.3f}–{target.end:.3f} s); a time is one moment")
        return float(target.start)

    def frame(self, seconds: Any) -> int:
        return int(round(self.time(seconds) * self.fps))

    def to_shot_seconds(self, shot_id: str, seconds: float) -> float:
        return _r(float(seconds) - self._shot_start(shot_id))

    # ---- look things up ---------------------------------------------------
    @property
    def duration(self) -> float:
        return _r(max((c.end for c in self.cuts), default=0.0))

    @property
    def cuts(self) -> list[Cut]:
        return [Cut(self, raw) for raw in picture_cuts(occurrences_from_bundle(self.bundle), fps=self.fps)]

    def cut(self, selector: Any) -> Cut:
        """A cut by number (1-based, as show prints), ``"@62.5"``/``"1:02"`` (the cut on screen then), or picture clip id."""
        cuts = self.cuts
        if isinstance(selector, int) or (isinstance(selector, str) and selector.strip().isdigit()):
            n = int(selector)
            if not 1 <= n <= len(cuts):
                raise TimelineEditError(f"cut {n} does not exist; this timeline has cuts 1–{len(cuts)}")
            return cuts[n - 1]
        text = str(selector).strip()
        if mo.CUT_ID_RE.match(text.lower()):
            text = self._cut_id(text) or text  # c1 is c01
            group = next((g for g in self._cut_groups() if g["id"] == text.lower()), None)
            if group is None:
                ids = [g["id"] for g in self._cut_groups()]
                raise TimelineEditError(f"no cut {text}; cuts are {', '.join(ids) or 'not named in this timeline'}")
            if group["picture"] is not None:
                for cut in cuts:
                    if any(step.data is group["picture"].data for step in cut.steps):
                        return cut
        if TIME_RE.match(text) or isinstance(selector, float):
            t = self.time(selector if not isinstance(selector, float) else selector)
            for cut in cuts:
                if cut.start - 1e-6 <= t < cut.end - 1e-6:
                    return cut
            raise TimelineEditError(f"no cut is on screen at {t:.3f} s (the timeline is {self.duration:.3f} s)")
        for cut in cuts:
            if any(c.id == text or c.id.startswith(text) for c in cut.steps):
                return cut
        raise TimelineEditError(f"no cut matches {selector!r}; pass 1–{len(cuts)}, a time like '1:02' or a picture clip id")

    def clips(self, *, element: str | None = None, asset: str | None = None, text: str | None = None,
              cut: Any = None, shot: Any = None, track: str | None = None, audio: bool | None = None) -> list[Clip]:
        """Every clip, filtered. Complete (never paged); in timeline order."""
        if cut is not None:
            pool = self.cut(cut).clips
        else:
            pool = [Clip(self, sid, c) for sid in self._shot_ids() for c in self._internal(sid)["clips"]]
        if shot is not None:
            sid = self._shot_id(shot)
            pool = [c for c in pool if c.shot_id == sid]
        out = []
        for c in pool:
            if element and c.element != element:
                continue
            if asset and (c.asset or "").lower() != asset.lower():
                continue
            if text and _norm(text) not in _norm(c.text or ""):
                continue
            if track and c.track != track:
                continue
            if audio is not None and c.is_audio != audio:
                continue
            out.append(c)
        return sorted(out, key=lambda c: (c.start, c.id))

    def clip(self, query: str, *, cut: Any = None, near: Any = None) -> Clip:
        """One clip by id (or prefix), asset key, element id or on-screen text.

        If several match, ``near=`` (a word or time) picks the one on screen closest to it,
        ``cut=`` restricts to one cut; otherwise the error lists the choices.
        """
        if isinstance(query, str) and cut is None and near is None:
            # one address, everywhere: c41.mink, a carried alias (c30.cover), a layer name, an asset key, …
            from astrid.sdk.timeline_address import candidates

            found = [t for t in candidates(self, query, prefer="thing") if t.kind == "clip"]
            if len(found) == 1:
                return found[0].clip
            if len(found) > 1:
                listing = "\n  ".join(str(t) for t in found[:12]) + (f"\n  … {len(found) - 12} more" if len(found) > 12 else "")
                raise TimelineEditError(f"{query!r} matches {len(found)} clips; name one:\n  {listing}")
        pool = self.cut(cut).clips if cut is not None else self.clips()
        if near is not None:
            point = self.time(near)
            try:
                return self._pick(pool, query, where="the timeline")
            except TimelineEditError:
                matches = [c for c in pool if self._matches(c, query)]
                if not matches:
                    raise
                return min(matches, key=lambda c: (0 if c.start - 1e-6 <= point < c.end else 1, abs(c.start - point)))
        return self._pick(pool, query, where=f"cut {cut}" if cut is not None else "the timeline")

    def _matches(self, c: Clip, query: str) -> bool:
        text = str(query).strip()
        return (c.id == text or c.address == text or c.layer_name == text or c.id.startswith(text) or (c.asset or "").lower() == text.lower()
                or c.element == text or bool(c.text and _norm(text) in _norm(c.text)))

    def words(self, *, between: tuple[float, float] | None = None) -> list[Word]:
        """Every spoken word, timeline seconds, with a stable id ``<segment>:<index>``."""
        found: list[tuple[float, float, str, str, str, int, str]] = []
        for sid in self._shot_ids():
            start = self._shot_start(sid)
            for clip in self._internal(sid)["clips"]:
                if not self._is_audio(sid, clip):
                    continue
                segment = intent.line(clip) or str(clip.get("id") or "")
                for i, entry in enumerate(_read_words({"words": intent.words(clip)}, relative=True)):
                    s, e, t = entry
                    at = start + _num(clip.get("at"))
                    found.append((at + s, at + e, t, segment, str(clip.get("id")), i, sid))
        found.sort(key=lambda row: (row[0], row[3], row[5]))
        counters: dict[str, int] = {}
        words = []
        for s, e, t, segment, clip_id, _i, sid in found:
            k = counters.get(segment, 0)
            counters[segment] = k + 1
            words.append(Word(f"{segment}:{k}", t, _r(s), _r(e), segment, k, clip_id, sid))
        if between:
            words = [w for w in words if between[0] - 1e-6 <= w.start < between[1]]
        return words

    def word(self, query: str, *, n: int | None = None, after: Any = None, near: Any = None) -> Word:
        """A word by text (``"viral"``, a phrase ``"Hugging Face"``) or id (``"n20b:20"``).

        If the text is spoken more than once, pass ``n=`` (1-based), ``after=`` or ``near=``
        (times or words); the error lists every occurrence with its id and time.
        """
        words = self.words()
        query = str(query).strip()
        if WORD_ID_RE.match(query):
            for w in words:
                if w.id == query:
                    return w
        parts = [_norm(p) for p in query.split() if _norm(p)]
        if not parts:
            raise TimelineEditError("name a word, e.g. tl.word('viral')")
        hits = [w for i, w in enumerate(words)
                if _norm(w.text) == parts[0]
                and all(i + k < len(words) and _norm(words[i + k].text) == p for k, p in enumerate(parts[1:], 1))]
        if after is not None:
            limit = self.time(after)
            hits = [w for w in hits if w.start >= limit - 1e-6]
        if near is not None:
            point = self.time(near)
            hits.sort(key=lambda w: abs(w.start - point))
            hits = hits[:1]
        if not hits:
            vocab = sorted({w.text for w in words})
            close = difflib.get_close_matches(query, vocab, n=5)
            raise TimelineEditError(f"nobody says {query!r} in this timeline" + (f"; did you mean {', '.join(map(repr, close))}?" if close else ""))
        if n is not None:
            if not 1 <= n <= len(hits):
                raise TimelineEditError(f"{query!r} is spoken {len(hits)} time(s); n must be 1–{len(hits)}")
            return hits[n - 1]
        if len(hits) > 1:
            listing = " · ".join(f"n={i} {w.start:.2f} s [{w.id}] cut {self._cut_at(w.start)}" for i, w in enumerate(hits, 1))
            raise TimelineEditError(f"{query!r} is spoken {len(hits)} times: {listing}. Pass n=, after= or the id.")
        return hits[0]

    def voice(self, segment: str) -> Voice:
        return Voice(self, segment)

    def at(self, when: Any) -> dict[str, Any]:
        """What is on screen and being said at a time: the cut, its clips there, and the words around."""
        t = self.time(when)
        cut = self.cut(f"@{t}")
        showing = [c for c in cut.clips if c.start - 1e-6 <= t < c.end - 1e-6]
        said = [w for w in self.words() if w.end >= t - 1.0 and w.start <= t + 1.0]
        speaking = next((w for w in self.words() if w.start - 1e-6 <= t <= w.end + 1e-6), None)
        return {"time": t, "cut": cut, "showing": showing, "speaking": speaking, "words_around": said}

    # ---- structural edits ---------------------------------------------------
    def add(self, element: str, *, at: Any, hold: float = 1.0, asset: Any = None, params: Mapping[str, Any] | None = None,
            track: str | None = None, corner: str | None = None, anchor: bool = True, clip_id: str | None = None,
            standin: Any = None) -> Clip:
        """Add an overlay at a word or time. ``corner`` (top-right …) places it inside title-safe, off a centred face.

        ``standin``: if ``asset`` is not available yet (a file still to be made), use this asset
        meanwhile and remember the real one; ``fill_standins()`` swaps it in once it exists."""
        word = self._as_word(at)
        start = self.quantize(word.start if word else self.time(at))
        sid = self._shot_at(start)
        internal = self._internal(sid)
        new = {"id": clip_id or self._new_id(element), "clipType": element, "track": track or ELEMENT_TRACK.get(element, "fx"),
               "at": 0.0, "hold": self.quantize(hold), "params": copy.deepcopy(dict(params or {}))}
        if not any(str(t.get("id")) == new["track"] for t in internal.get("tracks") or []):
            raise TimelineEditError(f"shot {sid} has no track {new['track']!r}; tracks: {', '.join(str(t.get('id')) for t in internal.get('tracks') or [])}")
        if asset is not None:
            try:
                new["asset"] = self._register_asset(sid, asset)
            except TimelineEditError:
                if standin is None:
                    raise
                new["asset"] = self._register_asset(sid, standin)
                intent.set_standin(new, str(asset), {"element": element, "params": copy.deepcopy(dict(params or {}))})
        if corner:
            new["params"].update(self._corner(element, new["params"], new.get("asset"), sid, corner))
        internal["clips"].append(new)
        clip = Clip(self, sid, new)
        clip.enter_at(word if word and anchor else start)
        return clip

    def ripple_delete(self, start: Any, end: Any, *, skip: Sequence[Mapping[str, Any]] = (), keep_inside: bool = False) -> float:
        """Remove the window [start, end) from the whole timeline and close it up.

        Clips after it move up by its length; picture clips that span it get shorter;
        audio that spans it (music, a VO tail) is split, so everything after stays in
        sync with its picture. Clips wholly inside the window are removed.
        Returns the seconds removed.
        """
        a, b = self.quantize(self.time(start)), self.quantize(self.time(end))
        if b <= a:
            raise TimelineEditError("the window to remove must have start < end")
        self._keep_inside = keep_inside
        try:
            return self._shift_after(a, -(b - a), cut_window=(a, b), skip=skip)
        finally:
            self._keep_inside = False

    def close_gap(self, *, before: Any, keep: float = 0.0) -> float:
        """Close the silence before a word (and ripple everything after it). Returns the seconds removed."""
        word = self._as_word(before) or self.word(str(before))
        previous = [w for w in self.words() if w.end <= word.start + 1e-6 and w.id != word.id]
        if not previous:
            raise TimelineEditError(f"nothing is said before {word.text!r}; there is no gap to close")
        gap_start = max(w.end for w in previous) + keep
        if word.start - gap_start < 1.0 / self.fps:
            raise TimelineEditError(f"the gap before {word.text!r} is already {max(0.0, word.start - gap_start + keep):.3f} s")
        return self.ripple_delete(gap_start, word.start)

    def insert_time(self, at: Any, seconds: float, *, skip: Sequence[Mapping[str, Any]] = ()) -> float:
        """Open ``seconds`` at a time and ripple everything after it later (picture spanning it is held longer)."""
        t = self.quantize(self.time(at))
        return self._shift_after(t, self.quantize(seconds), cut_window=None, skip=skip)

    # ---- the voice track: lines back to back with declared gaps (re-flow) -------
    def lines(self) -> list[Voice]:
        """The VO lines in order."""
        seen: dict[str, float] = {}
        for clip, words in self._pieces():
            seen.setdefault(intent.line(clip.data) or clip.id, words[0].start)
        return [Voice(self, seg) for seg, _t in sorted(seen.items(), key=lambda item: item[1])]

    def _pieces(self) -> list[tuple[Clip, list[Word]]]:
        """The voice track: every VO clip with words, in speaking order. A line is one or more
        pieces (a tightened line is split into pieces with their own silences)."""
        by_clip: dict[str, list[Word]] = {}
        for w in self.words():
            by_clip.setdefault(w.clip_id, []).append(w)
        out = []
        for clip in self.clips(audio=True):
            if intent.line(clip.data) and by_clip.get(clip.id):
                out.append((clip, by_clip[clip.id]))
        return sorted(out, key=lambda item: item[1][0].start)

    def _mark(self, prev: Clip, nxt: Clip, nwords: Sequence[Word]) -> float:
        """Where the silence after ``prev`` ends: the next line's take begins (its in-point), or,
        inside a tightened line, the next word group's first word."""
        if intent.line(nxt.data) != intent.line(prev.data):
            speed = _num(nxt.data.get("speed"), 1.0) or 1.0
            return nxt.start - _num(nxt.data.get("from")) / speed
        return nwords[0].start

    def _piece_gaps(self) -> dict[str, float]:
        pieces = self._pieces()
        return {a.id: _r(self._mark(a, b, wb) - wa[-1].end) for (a, wa), (b, wb) in zip(pieces, pieces[1:])}

    def gaps(self) -> dict[str, float]:
        """The silence after each line as it is now: its last word ends → the next line's take begins
        (the ``gap_after_s`` of a VO script)."""
        lines = self.lines()
        out = {}
        for line, nxt in zip(lines, lines[1:]):
            first = nxt.clips[0]
            speed = _num(first.data.get("speed"), 1.0) or 1.0
            out[line.segment] = _r(first.start - _num(first.data.get("from")) / speed - line.speech_end)
        return out

    def declare_gaps(self) -> int:
        """Record every piece's current silence as its declared gap (once, when a timeline is migrated)."""
        n = 0
        measured = self._piece_gaps()
        for clip, _words in self._pieces():
            if intent.gap_after(clip.data) is None and clip.id in measured:
                intent.set_gap_after(clip.data, measured[clip.id])
                n += 1
        return n

    def reflow(self, *, gaps: Mapping[str, float] | None = None, points: Mapping[str, float] | None = None) -> list[str]:
        """Lay the voice track out again (each piece's speech end + its gap) and ripple everything downstream.

        This re-times the whole film after a script change: a longer or shorter take, an
        inserted or removed line, a new gap. Each piece's gap is the one declared on it, else
        the one in ``gaps`` (clip id → seconds, what it was before the change). Time opens or
        closes in the silence after a piece (at ``points[clip id]``, where its speech used to
        end, else where it ends now), so picture, overlays, SFX and music after it move as one
        and keep their sync; anchored clips are re-resolved. Music is cut on a beat, and every
        seam is reported (a seam that skips or repeats a fraction of a beat says so).
        """
        report: list[str] = []
        gaps, points = dict(gaps or {}), dict(points or {})
        notes_before = len(self.notes)
        i = 0
        while True:
            pieces = self._pieces()
            if i + 1 >= len(pieces):
                break
            (clip, words), (_nclip, nwords) = pieces[i], pieces[i + 1]
            i += 1
            declared = intent.gap_after(clip.data)
            gap = declared if declared is not None else gaps.get(clip.id)
            if gap is None:
                continue
            end, have = words[-1].end, self._mark(clip, _nclip, nwords)
            delta = self.quantize(end + gap - have)
            if abs(delta) < 0.5 / self.fps:
                continue
            point = points.get(clip.id, end)
            if delta > 0:
                self.insert_time(self.quantize(max(point, end - delta)), delta, skip=[clip.data])
            else:
                start = max(end, min(point, _nclip.start + delta))
                self.ripple_delete(self.quantize(start), self.quantize(start) - delta, skip=[clip.data])
            report.append(f"{nwords[0].segment} ({nwords[0].text!r}) {nwords[0].start:.3f} → {nwords[0].start + delta:.3f} s ({delta:+.3f} s, everything after it moved)")
        moved = self.retime()
        notes = list(dict.fromkeys(self.notes[notes_before:]))
        del self.notes[notes_before:]
        orphaned = [n for n in notes if " is not spoken" in n or "is spoken" in n]
        others = [n for n in notes if n not in orphaned]
        tail: list[str] = []
        if orphaned:
            tail.append(f"{len(orphaned)} moment(s) lost their word (orphans: re-home or remove; `timelines status` lists them):")
            tail += [f"  {n}" for n in orphaned]
        tail += others
        if moved:
            tail.append(f"{len(moved)} clip(s) on moments followed their words"
                        + (": " + ", ".join(f"{cid} {a:.2f}→{b:.2f}" for cid, a, b in moved[:6]) + (" …" if len(moved) > 6 else "")))
        music_end = max((c.end for c in self.clips(audio=True) if c.track == "music"), default=None)
        if music_end is not None and music_end < self.duration - 0.5:
            tail.append(f"music: the bed ends at {music_end:.2f} s; the film now runs {self.duration:.2f} s "
                        f"({self.duration - music_end:.2f} s without music: extend or re-cue)")
        report += tail
        self.report = report
        return report

    def insert_line(self, segment: str, media: Any, *, words: Any, after: str, gap_after: float | None = None,
                    text: str | None = None) -> list[str]:
        """Insert a new VO line after line ``after``; everything downstream moves later to make room."""
        prev = Voice(self, after)
        if segment in {v.segment for v in self.lines()}:
            raise TimelineEditError(f"there is already a line {segment!r}; pick a new id")
        new_words = _read_words(words)
        if not new_words:
            raise TimelineEditError("the new line has no words; pass its words.json")
        before = self._piece_gaps()
        last = prev.clips[-1]
        gap_prev = prev.gap_after if prev.gap_after is not None else before.get(last.id, 0.3)
        gap_new = gap_after if gap_after is not None else gap_prev
        start = self.quantize(prev.speech_end + gap_prev)  # the new take begins
        first = start + new_words[0][0]
        # open the room in the silence after the previous line: what was keyed to the next line moves with it
        self.insert_time(prev.speech_end, new_words[-1][1] + gap_new, skip=[c.data for c in prev.clips])
        sid = self._shot_at(start)
        new = {"id": f"vo-{segment}-0", "clipType": "media", "track": last.track,
               "at": _r(start - self._shot_start(sid)), "from": 0.0, "to": _r(new_words[-1][1] + 0.06)}
        for key in ("volume", "gain_db"):
            if key in last.data:
                new[key] = copy.deepcopy(last.data[key])
        intent.set_line(new, segment, [[_r(a), _r(b), t] for a, b, t in new_words])
        if text is not None:
            intent.set_line_text(new, text)
        if gap_after is not None:
            intent.set_gap_after(new, _r(gap_after))
        new["asset"] = self._register_asset(sid, media)
        self._internal(sid)["clips"].append(new)
        before[new["id"]] = gap_new
        out = [f"inserted line {segment} at {first:.3f} s ({new_words[-1][1] - new_words[0][0]:.3f} s of speech)"]
        out += self.reflow(gaps=before)
        holding = next((g["id"] for g in reversed(self._cut_groups()) if g["start"] <= first + 1e-6), None)
        if holding:
            out.append(f"  {segment} has no cut of its own yet: {holding}'s picture holds over it "
                       f"(re-home an orphan cut onto it: --cut cNN --on '\"{new_words[0][2]}\" in {segment}')")
        return out

    def remove_line(self, segment: str) -> list[str]:
        """Remove a VO line, what is on screen only for it, and its silence; everything after moves up."""
        line = Voice(self, segment)
        lines = self.lines()
        index = next(i for i, v in enumerate(lines) if v.segment == segment)
        before = self._piece_gaps()
        start = self._frame(line.clips[0].start)  # cuts open on the frame of the take's in-point
        if index + 1 < len(lines):
            end = self._frame(lines[index + 1].clips[0].start)
        else:
            end = line.speech_end + (line.gap_after or 0.0)
        for clip in line.clips:
            clip.remove()
        self._kept = []
        self._orphan_reason = f"line {segment} was removed"
        removed = self.ripple_delete(start, end, keep_inside=True)
        kept = list(self._kept)
        out = [f"removed line {segment} ({removed:.3f} s)"]
        if kept:
            out.append(f"kept {len(kept)} clip(s) that were on screen only for {segment}, where they were, as orphans "
                       "to re-home (--on) or remove")
        return out + self.reflow(gaps=before)

    def apply_script(self, spec: Any, *, takes: Any = None, gaps: bool = False) -> list[str]:
        """Bring the voice track in line with a VO script: ``{"segments": [{id, text, gap_after_s}, …]}``
        (a path or a dict), with each take at ``<takes>/<id>.wav`` and its words at ``<takes>/<id>.words.json``
        (default: a ``vo`` folder next to the script, else the script's folder).

        A line whose words changed gets its new take; a new line is inserted after the one
        before it; a line no longer in the script is removed; then the film re-flows once.
        Declared gaps are kept unless ``gaps=True`` (then the script's ``gap_after_s`` wins). A new
        line without ``gap_after_s`` gets the script's ``default_gap_after_s`` (else 0.3 s), and the
        report names every such line. The script's ``voice``/``rate`` are TTS settings: re-flow reads
        only ``id``, ``text`` and ``gap_after_s`` (and the takes' words).
        """
        path = Path(spec).expanduser() if isinstance(spec, (str, Path)) else None
        data = json.loads(path.read_text(encoding="utf-8")) if path else dict(spec)
        folder = Path(takes).expanduser() if takes else (path.parent.parent / "vo" if path and (path.parent.parent / "vo").is_dir() else (path.parent if path else Path.cwd()))
        segments = [s for s in data.get("segments") or [] if s.get("id")]
        wanted = [str(s["id"]) for s in segments]
        default_gap = float(data.get("default_gap_after_s", 0.3))
        film_before = self.duration
        report: list[str] = []
        no_gap: list[str] = []
        have = {v.segment for v in self.lines()}
        for seg in sorted(have - set(wanted)):
            report += self.remove_line(seg)
        previous = None
        for item in segments:
            seg = str(item["id"])
            words_path = folder / f"{seg}.words.json"
            take = folder / f"{seg}.wav"
            new_words = _read_words(words_path) if words_path.is_file() else []
            if seg not in have:
                if not new_words or not take.is_file() or previous is None:
                    report.append(f"line {seg}: not added (needs {take.name}, {words_path.name} and a line before it)")
                else:
                    gap = item.get("gap_after_s")
                    if gap is None:
                        no_gap.append(seg)
                        gap = default_gap
                    report += self.insert_line(seg, take, words=new_words, after=previous, text=item.get("text"),
                                               gap_after=float(gap))
            else:
                line = Voice(self, seg)
                current = [(round(w.end - w.start, 3), _norm(w.text)) for w in line.words]
                fresh = [(round(e - s, 3), _norm(t)) for s, e, t in new_words]
                if new_words and take.is_file() and current != fresh:
                    line.replace(take, words=new_words, text=item.get("text"))
                    report += [f"line {seg}: new take"] + self.report
                elif item.get("text") and intent.line_text(line.clips[0].data) != item["text"]:
                    intent.set_line_text(line.clips[0].data, item["text"])
                    report.append(f"line {seg}: script text updated (narration re-pins on publish)")
                if gaps and item.get("gap_after_s") is None:
                    no_gap.append(f"{seg} (kept {line.gap_after if line.gap_after is not None else '?'} s)")
                if gaps and item.get("gap_after_s") is not None and line.gap_after != item["gap_after_s"]:
                    line.set_gap_after(float(item["gap_after_s"]), reflow=False)
                    report.append(f"line {seg}: gap after {item['gap_after_s']} s")
            previous = seg
        report += self.reflow()
        head = [f"film {_clock(film_before)} → {_clock(self.duration)} ({self.duration - film_before:+.1f} s)"]
        if no_gap:
            head.append(f"{len(no_gap)} line(s) had no gap_after_s: new ones got {default_gap:g} s "
                        f"(the script's default_gap_after_s): {', '.join(no_gap)}")
        report = head + report
        self.report = report
        return report

    def retime(self) -> list[tuple[str, float, float]]:
        """Resolve every moment (after a VO change, a gap, an edit). Returns what moved.

        Cuts first: each cut's picture starts on its ``on`` moment and runs to the next cut,
        so the picture track never gets a hole. Then every clip of a cut starts on its own
        ``on`` (or with its cut) and ends on its ``until``, its literal ``for``, or its cut's
        end. Clips without a cut only follow their ``on``/``until``. Moments floor to the frame."""
        moved: dict[int, tuple[str, float, float]] = {}
        self._moved_ends: dict[str, tuple[float, float]] = {}
        self._mcache = None
        groups = self._cut_groups()
        orphan_cuts: set[str] = set()
        for g in groups:  # 1. cut starts
            pic = g["picture"]
            on = intent.on(pic.data) if pic else None
            if not on:
                continue
            t = self._resolve_note(on, pic, in_point=True)
            if t is None:
                orphan_cuts.add(g["id"])  # its word is gone: the cut keeps its place and its length
                continue
            if self._move_within_shot(pic, t, keep_end=True, moved=moved):
                self._mcache = None
        cut_span = self._cut_spans(skip=orphan_cuts)
        for cid in orphan_cuts:
            group = next(g for g in self._cut_groups() if g["id"] == cid)
            cut_span[cid] = (group["start"], group["picture"].end if group["picture"] else None)
        self._orphan_cuts = orphan_cuts
        self._mcache = {"cuts": cut_span}
        for clip in self.clips():  # 2. every clip on its moments
            cut = intent.cut_of(clip.data)
            if (cut in orphan_cuts and not intent.on(clip.data)) or intent.orphan(clip.data):
                continue  # an orphan keeps its time and length until it is re-homed
            seq = intent.sequence(clip.data)
            later_step = seq is not None and seq[1] > 0
            on, until, length = intent.on(clip.data), intent.until(clip.data), intent.for_s(clip.data)
            is_picture = any(g["picture"] is not None and g["picture"].data is clip.data for g in groups)
            start = clip.start
            if on and not is_picture:
                t = self._resolve_note(on, clip)
                if t is not None:
                    start = t
            elif cut and not is_picture and not later_step and not clip.is_audio and cut in cut_span:
                start = cut_span[cut][0]
            end = None
            if until:
                end = self._resolve_note(until, clip, start=start)
            elif length is not None:
                end = start + length
            elif cut and not clip.is_audio and not later_step and cut in cut_span and (seq is None or is_picture):
                end = cut_span[cut][1]
            if seq is not None and not is_picture:
                end = None
            self._place(clip, start, end, moved)
        self._mcache = None
        return list(moved.values())

    # ---- moments: the plumbing ------------------------------------------------
    def _frame(self, t: float) -> float:
        return _r(mo.floor_frame(t, self.fps))

    def _moment_text(self, moment: Any) -> str:
        """Canonical text for a moment given as text, a Word, a Moment or a number of seconds."""
        if isinstance(moment, Word):
            return mo.format_moment(mo.word_moment(moment, self.words()))
        if isinstance(moment, mo.Moment):
            return mo.format_moment(moment)
        if isinstance(moment, (int, float)):
            raise TimelineEditError("a number is a time, not a moment; use clip.enter_at(seconds) or a moment like +0.8s")
        text = str(moment).strip()
        if text.lower().startswith(("until ", "on ")):
            text = text.split(" ", 1)[1]
        try:
            return mo.format_moment(mo.parse(text))
        except mo.MomentError as exc:
            raise TimelineEditError(str(exc)) from None

    def _moment_time(self, text: str, clip: "Clip | None" = None, *, in_point: bool = False) -> float:
        try:
            return self._frame(mo.resolve(mo.parse(text), _MomentContext(self, clip), in_point=in_point))
        except mo.MomentError as exc:
            raise TimelineEditError(f"{clip.address + ': ' if clip else ''}{exc}") from None

    def _resolve_note(self, text: str, clip: "Clip", *, in_point: bool = False, start: float | None = None) -> float | None:
        try:
            return self._moment_time(text, clip, in_point=in_point)
        except TimelineEditError as exc:
            pinned = self._pin_nearest(text, clip)
            if pinned is not None:
                field = "on" if intent.on(clip.data) == text else "until"
                (intent.set_on if field == "on" else intent.set_until)(clip.data, pinned)
                self.notes.append(f"{clip.address}: {text} is now said more than once; pinned to {pinned}, the one nearest it")
                try:
                    return self._moment_time(pinned, clip, in_point=in_point)
                except TimelineEditError:
                    pass
            self.notes.append(str(exc))
            return None

    def _pin_nearest(self, text: str, clip: "Clip", at: float | None = None) -> str | None:
        """A word moment that became ambiguous (a new line says the same word): its scoped form for the
        occurrence nearest the clip's current time, or None if it is not an ambiguity."""
        try:
            moment = mo.parse(text)
        except mo.MomentError:
            return None
        if moment.kind in ("beat", "downbeat") and moment.base is not None and moment.base.kind == "word":
            # beat 2 after "Astrid": pin the word to the occurrence just before the clip
            base = moment.base
            if base.n is not None or base.line is not None:
                return None
            hits = mo.find_words(base, self.words())
            if len(hits) < 2:
                return None
            earlier = [r for r in hits if r[0].start <= clip.start + 1e-6] or hits
            run = max(earlier, key=lambda r: r[0].start)
            from astrid.sdk.timeline_address import _phrase_address

            pinned_base = mo.parse(_phrase_address(self, base, run))
            return mo.format_moment(mo.Moment(moment.kind, n=moment.n, direction=moment.direction, base=pinned_base,
                                              offset_s=moment.offset_s, offset_frames=moment.offset_frames))
        if moment.kind != "word" or moment.n is not None:
            return None
        hits = mo.find_words(moment, self.words())
        if len(hits) < 2:
            return None
        anchor_t = (at if at is not None else clip.start) - moment.offset_s - moment.offset_frames / self.fps
        run = min(hits, key=lambda r: abs((r[-1].end if moment.edge == "end" else r[0].start) - anchor_t))
        from astrid.sdk.timeline_address import _phrase_address

        return _phrase_address(self, moment, run)

    def _place_on(self, clip: "Clip", moment: Any) -> None:
        if isinstance(moment, (int, float)) and not isinstance(moment, bool):
            span = self._own_cut_span(clip)
            if span is not None:
                moment = mo.offset_text(self._frame(float(moment)) - span[0], self.fps) or None
            else:
                intent.set_on(clip.data, None)
                clip._set_start(self._frame(float(moment)))
                return
        if moment is None:
            intent.set_on(clip.data, None)
            span = self._own_cut_span(clip)
            if span is not None:
                clip._set_start(span[0])
            return
        text = self._moment_text(moment)
        is_picture = self._is_picture(clip)
        t = self._moment_time(text, clip, in_point=is_picture)
        intent.set_on(clip.data, text)
        if is_picture:
            self._move_within_shot(clip, t, keep_end=True, moved={})
        else:
            clip._set_start(t)

    def _own_cut_span(self, clip: "Clip") -> tuple[float, float] | None:
        cut = intent.cut_of(clip.data)
        if not cut:
            return None
        spans = (self._mcache or {}).get("cuts")
        if spans is None:
            spans = self._cut_spans()
        span = spans.get(cut)
        if span is None:
            return None
        return span[0], span[1] if span[1] is not None else self.duration

    def orphans(self) -> list[str]:
        """Moments that no longer resolve (their word was cut or rewritten), one readable line each:
        ``c22.rocket  on "viral": "viral" is not spoken in n20b; did you mean …``."""
        out = []
        self._mcache = None
        sequences_seen: set[str] = set()
        for clip in self.clips():
            reason = intent.orphan(clip.data)
            if reason:
                seq = intent.sequence(clip.data)
                if seq is not None:  # a time-lapse is one orphan, not one per step
                    if seq[0] in sequences_seen:
                        continue
                    sequences_seen.add(seq[0])
                    steps = [c for c in self.clips() if (intent.sequence(c.data) or ("",))[0] == seq[0]]
                    lo, hi = min(c.start for c in steps), max(c.end for c in steps)
                    out.append(f"{clip.address:<16} a sequence of {len(steps)} steps kept at {lo:.2f}–{hi:.2f} s: {reason} "
                               "(re-home its first step: --on MOMENT; --keep; or remove it)")
                    continue
                out.append(f"{clip.address:<16} kept at {clip.start:.2f}–{clip.end:.2f} s: {reason} "
                           "(re-home it: --on MOMENT; keep it as is: --keep; or remove it)")
                continue
            for field, text in (("on", intent.on(clip.data)), ("until", intent.until(clip.data))):
                if not text:
                    continue
                try:
                    mo.resolve(mo.parse(text), _MomentContext(self, clip))
                except mo.MomentError as exc:
                    why = ("the music has no beat there (the bed ends before it)" if "there is no beat" in str(exc)
                           or "there is no downbeat" in str(exc) else "its word is gone")
                    out.append(f"{clip.address:<16} {field} {text}: {exc} ({why}: re-home it or remove the clip)")
            for path, expr in intent.formulas(clip.data).items():
                if isinstance(expr, Mapping) and expr.get("moment"):
                    try:
                        mo.resolve(mo.parse(expr["moment"]), _MomentContext(self, clip))
                    except mo.MomentError as exc:
                        out.append(f"{clip.address:<16} {path} = {expr['moment']}: {exc}")
            fit = intent.sequence_fit(clip.data)
            if fit and isinstance(fit.get("land"), str):
                try:
                    mo.resolve(mo.parse(fit["land"]), _MomentContext(self, clip))
                except mo.MomentError as exc:
                    out.append(f"{clip.address:<16} fit until {fit['land']}: {exc}")
        return out

    def _cut_clips(self, cut_id: str) -> list[Clip]:
        """Every clip of a cut, orphans included (an orphaned cut is not in the cut list until re-homed)."""
        return [c for c in self.clips() if intent.cut_of(c.data) == cut_id]

    def cut_picture(self, cut_id: str) -> Clip | None:
        """A cut's picture (its first bed clip), for live and orphaned cuts alike."""
        beds = sorted((c for c in self._cut_clips(cut_id) if c.track == "plate" and not c.is_audio), key=lambda c: (c.start, c.id))
        return beds[0] if beds else None

    def _cut_id(self, text: str) -> str | None:
        """The cut id as the sheet prints it: ``c1`` and ``c001`` are ``c01``; None when there is no such cut."""
        m = re.fullmatch(r"c0*(\d+)([a-z]?)", str(text).strip().lower())
        if not m:
            return None
        ids = [g["id"] for g in self._cut_groups()] + [cid for cid in {intent.cut_of(c.data) for c in self.clips()} if cid]
        if str(text).strip().lower() in ids:
            return str(text).strip().lower()
        return next((cid for cid in ids if (mm := re.fullmatch(r"c0*(\d+)([a-z]?)", cid))
                     and (mm.group(1), mm.group(2)) == (m.group(1), m.group(2))), None)

    def _no_cut(self, cut_id: str) -> str:
        ids = [g["id"] for g in self._cut_groups()]
        plain = [c for c in ids if re.fullmatch(r"c\d+", c)]
        extra = [c for c in ids if c not in plain]
        span = (f"{plain[0]}…{plain[-1]}" if len(plain) > 1 else ", ".join(plain)) + (f" (+ {', '.join(extra)})" if extra else "")
        return f"no cut {cut_id}; cuts are {span or 'not named in this timeline'}"

    def _cut_spans(self, skip: Iterable[str] = ()) -> dict[str, tuple[float, float | None]]:
        """``{cut id: (start, end)}``: a cut runs from its start to the next cut's start (end None: the last).
        Cuts in ``skip`` (orphans) don't end the cut before them."""
        skip = set(skip) | set(getattr(self, "_orphan_cuts", set()) or ())
        groups = [g for g in self._cut_groups() if g["id"] not in skip]
        return {g["id"]: (g["start"], groups[k + 1]["start"] if k + 1 < len(groups) else None) for k, g in enumerate(groups)}

    def _cut_groups(self) -> list[dict[str, Any]]:
        """Clips grouped by cut id, in time order; each group's picture is its bed clip (first step)."""
        groups: dict[str, list[Clip]] = {}
        for clip in self.clips():
            cid = intent.cut_of(clip.data)
            if cid:
                groups.setdefault(cid, []).append(clip)
        out = []
        for cid, clips in groups.items():
            if all(intent.orphan(c.data) for c in clips):
                continue  # a cut kept only as orphans is not a cut until it is re-homed
            beds = sorted((c for c in clips if c.track == "plate" and not c.is_audio), key=lambda c: (c.start, c.id))
            out.append({"id": cid, "clips": clips, "picture": beds[0] if beds else None,
                        "start": beds[0].start if beds else min(c.start for c in clips)})
        return sorted(out, key=lambda g: (g["start"], g["id"]))

    def _is_picture(self, clip: "Clip") -> bool:
        cut = intent.cut_of(clip.data)
        if not cut or clip.track != "plate" or clip.is_audio:
            return False
        seq = intent.sequence(clip.data)
        if seq is not None and seq[1] > 0:
            return False
        beds = [c for c in self.clips(shot=clip.shot_id) if intent.cut_of(c.data) == cut and c.track == "plate" and not c.is_audio]
        return bool(beds) and min(beds, key=lambda c: (c.start, c.id)).data is clip.data

    def _move_within_shot(self, clip: "Clip", t: float, *, keep_end: bool, moved: dict) -> bool:
        """Move a cut's picture start to ``t`` (it must stay inside its shot); the picture before it follows."""
        if round(t * self.fps) == round(clip.start * self.fps):
            return False
        lo = self._shot_start(clip.shot_id)
        hi = lo + self._shot_length(clip.shot_id)
        if not lo - 1e-6 <= t < hi - 0.5 / self.fps:
            self.notes.append(f"{clip.address}: its moment is at {t:.3f} s, outside its chapter ({lo:.3f}–{hi:.3f} s); left in place")
            return False
        old = clip.start
        before = [c for c in self.clips(shot=clip.shot_id) if c.track == clip.track and c.data is not clip.data
                  and abs(c.end - old) < 1e-3 and not c.is_audio and not intent.orphan(c.data)]
        if not before and abs(old - lo) < 1e-3:
            # it opens its chapter: moving it would leave a hole at the chapter start (re-flow moves chapters)
            self.notes.append(f"{clip.address}: opens its chapter at {lo:.3f} s; its moment ({t:.3f} s) is kept for re-flow, not applied")
            return False
        end = clip.end
        clip._set_start(t)
        if keep_end:
            _set_length(clip.data, end - t)
        for prev in before:
            _set_length(prev.data, t - prev.start)
        moved[id(clip.data)] = (clip.address, old, t)
        return True

    def _place(self, clip: "Clip", start: float, end: float | None, moved: dict) -> None:
        old_start, old_end = clip.start, clip.end
        changed = False
        if round(start * self.fps) != round(old_start * self.fps):
            clip._set_start(self._frame(start + 1e-6))
            changed = True
        if end is not None and not clip.is_audio and "from" not in clip.data:
            length = self._frame(end + 1e-6) - clip.start
            if length > 0.5 / self.fps and round(length * self.fps) != round(clip.duration * self.fps):
                _set_length(clip.data, length)
                changed = True
        if changed and id(clip.data) not in moved:
            moved[id(clip.data)] = (clip.address, old_start, clip.start)
            self._moved_ends[clip.address] = (old_end, clip.end)

    def _fit_sequences(self) -> list[str]:
        """Re-lay every sequence that has a fit (see ``timeline_intent.sequence_fit``) so it lands on its word."""
        groups: dict[str, list[Clip]] = {}
        for clip in self.clips():
            tag = intent.sequence(clip.data)
            if tag:
                groups.setdefault(tag[0], []).append(clip)
        changes = []
        for seq_id, steps in groups.items():
            steps.sort(key=lambda c: intent.sequence(c.data)[1])
            fit = intent.sequence_fit(steps[0].data)
            if not fit:
                continue
            spec = fit.get("land")
            start, end = steps[0].start, steps[-1].end
            total = int(round((end - start) * self.fps))
            if isinstance(spec, str):  # a moment: "lifetime"
                try:
                    land_t = mo.resolve(mo.parse(spec), _MomentContext(self, steps[0]))
                except mo.MomentError as exc:
                    self.notes.append(f"{steps[0].address}: fit until {spec}: {exc}; the sequence is left as it is")
                    continue
                land_name = spec
            else:  # older form {word, text}
                land = self._as_word(str((spec or {}).get("word") or "")) or self.word(str((spec or {}).get("text")))
                land_t, land_name = land.start, repr(land.text)
            life = int(round((land_t - start) * self.fps)) + int(fit.get("offset_frames") or 0)
            cycle = list(fit.get("cycle") or [steps[0].asset])
            lead = [int(n) for n in fit.get("lead") or []]
            plan = [(cycle[i % len(cycle)], n) for i, n in enumerate(lead)]
            left = life - sum(lead)
            race_frames = [int(n) for n in fit.get("race") or [2]]
            race: list[int] = []
            k = 0
            while left > 0:
                n = race_frames[min(k, len(race_frames) - 1)]
                race.append(min(n, left))
                left -= race[-1]
                k += 1
            plan += [(cycle[(len(cycle) - 1 - (len(race) - 1 - i)) % len(cycle)], n) for i, n in enumerate(race)]
            plan.append((fit.get("then") or cycle[-1], max(1, total - sum(n for _a, n in plan))))
            current = [(c.asset, int(round(c.duration * self.fps))) for c in steps]
            if current == plan:
                continue
            template = copy.deepcopy(steps[0].data)
            sid = steps[0].shot_id
            rows = self._internal(sid)["clips"]
            at = _num(template.get("at"))
            for c in steps:
                c.remove()
            frame = 0
            for i, (asset, n) in enumerate(plan):
                new = copy.deepcopy(template)
                new["id"] = f"{seq_id}-{i:02d}"
                new["asset"] = self._register_asset(sid, asset)
                new["at"] = _r(at + frame / self.fps)
                new.pop("from", None), new.pop("to", None)
                new["hold"] = _r(n / self.fps)
                set_fit = intent.sequence_fit(new) if i == 0 else None
                intent.set_sequence_fit(new, set_fit)
                intent.set_sequence(new, seq_id, i)
                rows.append(new)
                frame += n
            changes.append(f"sequence {seq_id}: {len(steps)} → {len(plan)} steps, lands on {land_name} at frame {life}")
        return changes

    def resolve(self) -> list[str]:
        """Re-resolve every formula in the document (like a spreadsheet recalculating).

        An anchor puts a clip's start on a word (see ``retime``). A clip's formulas map a
        field path to an expression, and the resolved value is written to that field:

        - ``{"word": "n20b:20", "text": "viral", "offset_s": 0, "as": "clip_seconds"|"clip_frame"|"timeline_seconds"}``
          (``edge: "end"`` for the word's end; then, in order: ``offset_frames``, ``min``/``max``, ``step``
          (down to a multiple, e.g. 2 for stepFrames 2); ``snap: "floor"|"round"`` puts seconds on a frame)
        - ``{"words_of": "n20b"}``: ``[[start, end], …]`` of that line's words, clip-relative (presenter lip-sync)
        - ``{"mark": "B2-HAND", "axis": "x"|"y", "offset": -96, "unit": "canvas"}``: a slot's hand mark plus an
          offset in canvas px (older ``"logical"``/``"px"`` offsets still read)

        Paths look like ``params.words``, ``params.punchAt[0]``, ``params.keyframes[2].frame``. Returns what changed.
        """
        self._outside = []
        changes = [f"{cid}: start {old:.3f} → {new:.3f} s (anchored to its word)" for cid, old, new in self.retime()]
        changes += self._fit_sequences()
        words = self.words()
        by_id = {w.id: w for w in words}
        slots = self.slots
        for clip in self.clips():
            formulas = intent.formulas(clip.data)
            if not formulas:
                continue
            for path, expr in formulas.items():
                try:
                    value = self._evaluate(clip, expr, by_id, words, slots)
                except (TimelineEditError, mo.MomentError, ValueError, KeyError, TypeError) as exc:
                    previous_at = None  # a param's moment: pin to the occurrence nearest where the param was
                    current = _get_path(clip.data, path)
                    if isinstance(expr, Mapping) and isinstance(current, (int, float)) and not isinstance(current, bool):
                        previous_at = clip.start + (current / self.fps if expr.get("as") == "clip_frame" else
                                                    0.0 if expr.get("as") == "timeline_seconds" else current)
                        if expr.get("as") == "timeline_seconds":
                            previous_at = float(current)
                    pinned = self._pin_nearest(expr.get("moment"), clip, previous_at) if isinstance(expr, Mapping) and expr.get("moment") else None
                    if pinned is not None:
                        expr = {**expr, "moment": pinned}
                        intent.set_formula(clip.data, path, expr)
                        self.notes.append(f"{clip.address}.{path.removeprefix('params.')}: now pinned to {pinned}, the one nearest it")
                        try:
                            value = self._evaluate(clip, expr, by_id, words, slots)
                        except (TimelineEditError, mo.MomentError, ValueError, KeyError, TypeError) as again:
                            self.notes.append(f"{clip.address}.{path.removeprefix('params.')}: {again} (kept its last value)")
                            continue
                    else:
                        self.notes.append(f"{clip.address}.{path.removeprefix('params.')}: {exc} (kept its last value)")
                        continue
                unit = expr.get("as") if isinstance(expr, Mapping) else None
                length = clip.duration * (self.fps if unit == "clip_frame" else 1.0)
                if isinstance(expr, Mapping) and expr.get("moment") and unit in ("clip_frame", "clip_seconds") \
                        and isinstance(value, (int, float)) and not (-1e-6 <= value <= length + 1e-6):
                    self._outside.append(f"{clip.address}.{path.removeprefix('params.')} = {expr['moment']} → "
                                         f"{'frame' if unit == 'clip_frame' else 's'} {value}, outside the clip "
                                         f"(0–{length:g}): re-home it on a moment inside {clip.start:.2f}–{clip.end:.2f} s")
                if _set_path(clip.data, path, value):
                    changes.append(f"{clip.id}.{path} = {json.dumps(value)[:60]}")
        return changes

    def _evaluate(self, clip: Clip, expr: Any, by_id: Mapping[str, Word], words: Sequence[Word], slots: Mapping[str, Any]) -> Any:
        if not isinstance(expr, Mapping):
            raise TimelineEditError(f"a formula must be an object, got {expr!r}")
        if "moment" in expr:  # a moment in the grammar: "lifetime" -50f, beat 2 after "Astrid", c26 …
            try:
                t = mo.resolve(mo.parse(expr["moment"]), _MomentContext(self, clip))
            except mo.MomentError as exc:
                raise TimelineEditError(f"{expr['moment']}: {exc}") from None
            unit = expr.get("as", "clip_seconds")
            if unit == "clip_frame":
                return _post(int(math.floor((t - clip.start) * self.fps + 1e-6)), expr)
            value = t if unit == "timeline_seconds" else t - clip.start
            if expr.get("snap") == "floor":
                value = math.floor(value * self.fps + 1e-6) / self.fps
            elif expr.get("snap") == "round":
                value = round(value * self.fps) / self.fps
            return _r(_post(value, expr))
        if "word" in expr:
            word = by_id.get(str(expr["word"]))
            if word is None or (expr.get("text") and _norm(word.text) != _norm(expr["text"])):
                same = [w for w in words if _norm(w.text) == _norm(expr.get("text"))]
                if not same:
                    raise TimelineEditError(f"word {expr.get('text')!r} is no longer spoken")
                word = min(same, key=lambda w: abs(w.start - clip.start))
            t = (word.end if expr.get("edge") == "end" else word.start) + _num(expr.get("offset_s"))
            unit = expr.get("as", "clip_seconds")
            if unit == "clip_frame":
                return _post(int(round((t - clip.start) * self.fps)), expr)
            value = t if unit == "timeline_seconds" else t - clip.start
            if expr.get("snap") == "floor":  # down to a whole frame (a hold that must end before the word)
                value = math.floor(value * self.fps + 1e-9) / self.fps
            elif expr.get("snap") == "round":  # to the nearest frame
                value = round(value * self.fps) / self.fps
            return _r(_post(value, expr))
        if "words_of" in expr:
            segment = str(expr["words_of"])
            return [[_r(w.start - clip.start), _r(w.end - clip.start)] for w in words
                    if w.segment == segment and w.end > clip.start and w.start < clip.end]
        if "mark" in expr:
            slot = slots.get(str(expr["mark"])) or {}
            mark = slot.get("hand_mark") or {}
            axis = str(expr.get("axis", "x"))
            if axis not in mark:
                raise TimelineEditError(f"slot {expr['mark']!r} has no hand_mark.{axis}")
            # one arithmetic for every unit: canvas px (the mark + the offset), then the element's grid
            canvas = _num(mark[axis]) + mark_offset_canvas(expr)
            if clip.element in LOGICAL_GRID_ELEMENTS or expr.get("unit", "logical") == "logical":
                return round(canvas / LOGICAL_PX)
            return round(canvas)
        raise TimelineEditError(f"unknown formula {expr!r}")

    # ---- slots (real footage to film) and stand-ins ---------------------------
    @property
    def slots(self) -> dict[str, Any]:
        """The timeline's real-footage slots: ``{id: {kind, spec, face_zone, hand_mark, media}}``."""
        return intent.slots(self.bundle)

    def slot_clips(self, slot_id: str) -> list[Clip]:
        return [c for c in self.clips() if intent.slot(c.data) == slot_id]

    def fill_slot(self, slot_id: str, media: Any, *, start_in_take: float = 0.0) -> list[str]:
        """Put the filmed take into a slot: every placeholder tagged with it becomes one ``am-footage`` clip
        per cut, with the same crop (zoom and focus); overlays stay where they are."""
        if slot_id not in self.slots:
            raise TimelineEditError(f"no slot {slot_id!r}; slots: {', '.join(self.slots) or 'none'}")
        slot = self.slots[slot_id]
        changed = []
        by_cut: dict[int, list[Clip]] = {}
        for clip in self.slot_clips(slot_id):
            cut = self.cut(f"@{clip.start + 1e-3}").n
            by_cut.setdefault(cut, []).append(clip)
        for n, placeholders in sorted(by_cut.items()):
            plate = next((c for c in placeholders if c.element in ("am-snap-plate", "am-footage")), placeholders[0])
            key = self._register_asset(plate.shot_id, media)
            zoom = _num(plate.params.get("zoom"), 1.0) or 1.0
            focus = plate.params.get("focus") or {}
            fx = _num(focus.get("x"), 160) / 320 if plate.element != "am-footage" else _num(focus.get("x"), 0.5)
            fy = _num(focus.get("y"), 90) / 180 if plate.element != "am-footage" else _num(focus.get("y"), 0.5)
            params = {"slot": slot_id, "kind": slot.get("kind", ""), "placeholder": False, "zoom": zoom,
                      "focus": {"x": round(fx, 4), "y": round(fy, 4)}, "in": _r(start_in_take + (plate.start - placeholders[0].start))}
            for k in ("mosaicIn", "mosaicOut", "faceZone", "handMark"):
                if k in plate.params:
                    params[k] = copy.deepcopy(plate.params[k])
            if isinstance(plate.params.get("push"), Mapping):
                params["push"] = {"to": _num(plate.params["push"].get("to"), zoom)}
            new = {"id": f"{plate.id}-real", "clipType": "am-footage", "track": plate.track, "asset": key,
                   "at": plate.data.get("at"), "hold": _r(plate.duration), "params": params}
            intent.set_slot(new, slot_id, replaces=[c.id for c in placeholders])
            for c in placeholders:
                c.remove()
            self._internal(plate.shot_id)["clips"].append(new)
            changed.append(f"cut {n}: {', '.join(c.id for c in placeholders)} → {new['id']}")
        slot["media"] = key if isinstance(key, str) else str(key)
        return changed

    def fill_standins(self) -> list[str]:
        """Swap every stand-in whose real asset now exists for the clip it stands in for."""
        changed = []
        for clip in self.clips():
            pending = intent.standin(clip.data)
            if not pending:
                continue
            wanted, intended = pending
            try:
                key = self._register_asset(clip.shot_id, wanted)
            except TimelineEditError:
                continue
            clip.data["clipType"] = intended.get("element", clip.element)
            clip.data["asset"] = key
            if isinstance(intended.get("params"), Mapping):
                clip.data["params"] = copy.deepcopy(dict(intended["params"]))
            intent.clear_standin(clip.data)
            changed.append(f"{clip.id}: stand-in → {key}")
        return changed

    # ---- check / publish ----------------------------------------------------
    def lint(self, *, cuts: Iterable[int] | None = None) -> list[str]:
        """Lint lines for this candidate (the same checks as ``timelines lint``), optionally only some cuts."""
        try:
            from astrid.packs.rendering.executors.timeline_visualize.motion import model
            from astrid.packs.rendering.executors.timeline_visualize.motion.conditions import run_checks
        except Exception as exc:  # the visualize pack is optional for editing
            return [f"(lint unavailable here: {exc})"]
        occurrences = occurrences_from_bundle(self.bundle)
        all_cuts = picture_cuts(occurrences, fps=self.fps)
        wanted = set(cuts) if cuts is not None else None
        selected = [c for c in all_cuts if wanted is None or c["index"] in wanted]
        elements = model.elements_from_occurrences(occurrences)
        order = next((o.get("track_order") for o in occurrences if o.get("track_order")), [])
        results, timeline_findings = run_checks(selected, elements, self.fps, track_order=order, all_cuts=all_cuts)
        lines = [f.line() for f in timeline_findings if f.severity != "info"]
        for cut, findings in results:
            lines += [f.line(cut["start"]) for f in findings if f.severity != "info"]
        return lines

    def _type_mismatches(self) -> list[str]:
        """Params whose value has the wrong type: text where the element (or a formula) wants a number."""
        from astrid.sdk.timeline_address import element_schema

        out = []
        for clip in self.clips():
            params = clip.data.get("params") if isinstance(clip.data.get("params"), dict) else {}
            props = (element_schema(clip.element).get("properties") or {}) if params else {}
            for key, value in params.items():
                for path, text, want in _text_where_number(key, value, props.get(key) or {}):
                    stripped = text.strip()
                    moment = stripped[2:-1] if stripped.startswith("ƒ(") and stripped.endswith(")") else stripped
                    hint = (f"; to put it on a moment: --set '{path}=ƒ({moment})' (or a number)"
                            if moment.startswith(('"', "“", "after ", "beat ", "downbeat ", "c")) else "")
                    out.append(f"{clip.address}.{path} is the text {text!r} where {clip.element} wants a {want}{hint}")
            for path, expr in intent.formulas(clip.data).items():
                if isinstance(expr, Mapping) and expr.get("as") == "clip_frame":
                    value = _get_path(clip.data, path)
                    if value is not None and not (isinstance(value, int) and not isinstance(value, bool)):
                        out.append(f"{clip.address}.{path.removeprefix('params.')} is {value!r} where a frame number belongs")
        return out

    def check(self) -> "CheckReport":
        """Validate, say what moved (timeline seconds, against the checked-out head) and lint the changed cuts."""
        from astrid.sdk.authoring_bundle import validate_authoring_candidate
        from astrid.sdk.timeline_cuts import base_bundle, diff_bundles, render_diff

        resolved = self.resolve()  # formulas first: every anchored clip back on its word, every expression recalculated
        candidate = self.document()
        problems: list[str] = [f"resolved {line}" for line in resolved]
        try:
            validation = validate_authoring_candidate(candidate)
            valid = bool(validation.get("valid", True)) if isinstance(validation, Mapping) else True
        except Exception as exc:  # AuthoringBundleError and friends: one actionable line
            valid, validation = False, {"error": str(exc)}
            problems.append(f"invalid: {exc}")
        diff = diff_bundles(base_bundle(candidate), candidate)
        summary = render_diff(diff).splitlines()
        changed = {c["clip_id"] for c in diff["changes"]}
        cut_numbers = sorted({cut.n for cut in self.cuts for clip in cut.clips if clip.id in changed})
        from astrid.sdk.timeline_address import ordinals_to_ids

        lint = [ordinals_to_ids(line, self) for line in self.lint(cuts=cut_numbers)] if cut_numbers else []
        lint_new, lint_old = _new_findings(lint, Checkout(base_bundle(candidate)))
        names = []
        for n in cut_numbers:
            pic = self.cuts[n - 1].picture
            names.append(intent.cut_of(pic.data) if pic is not None and intent.cut_of(pic.data) else f"cut {n}")
        # Blocking: moments whose word is gone (orphans) and clips with no length. Publish refuses until
        # each is re-homed (a new moment) or removed.
        blocking = [f"orphan  {line}" for line in self.orphans()]
        blocking += [f"empty   {c.address} has no length ({c.start:.3f}–{c.end:.3f} s): give it a length or remove it"
                     for c in self.clips() if not c.is_audio and c.duration < 0.5 / self.fps]
        blocking += [f"outside {line}" for line in self._outside]
        blocking += [f"type    {line}" for line in self._type_mismatches()]
        blocking += [f"beats   {line}" for line in self._stale_beats()]
        if blocking:
            valid = False
        music_end = max((c.end for c in self.clips(audio=True) if c.track == "music"), default=None)
        if music_end is not None and music_end < self.duration - 0.5:
            problems.append(f"music: the bed ends at {music_end:.2f} s; the film runs {self.duration:.2f} s "
                            f"({self.duration - music_end:.1f} s without music)")
        return CheckReport(valid=valid, validation=validation, summary=summary, lint=lint,
                           problems=blocking + problems + list(dict.fromkeys(self.notes)), changed_cuts=cut_numbers,
                           diff=diff, cut_names=names, blocking=blocking, lint_new=lint_new, lint_old=lint_old)

    def publish(self, message: str = "", *, idempotency_key: str | None = None, client: Any = None,
                force: bool = False) -> dict[str, Any]:
        """Check, then publish one revision.

        If someone published since this checkout, the edit is merged onto their head clip by
        clip (three-way: your base, their head, your candidate). Changes to different clips
        merge; a clip both sides changed refuses with a conflict list. ``force=True``
        overwrites their change, explicitly. Nothing is ever overwritten silently.
        """
        report = self.check()
        if not report.valid:
            raise TimelineEditError("not publishing: " + "; ".join(report.problems or ["the candidate is invalid"]))
        key = idempotency_key or f"edit-{re.sub(r'[^a-z0-9]+', '-', message.lower())[:40].strip('-') or 'timeline'}-{uuid.uuid4().hex[:8]}"
        if self.edits().get("changes") or not self.narration_changes():
            receipt = publish_bundle(self.document(), key, client=client, force=force)
        else:  # only the narration binding is behind (a retry after it failed): pin it, nothing else to publish
            receipt = {"old_head": self.base_revision, "new_head": self.base_revision, "structure": "unchanged"}
        receipt["narration_pinned"] = []
        if self.narration_changes():
            # A line's text changed (or the shots are new): bind each such shot's narration now that
            # the shots exist, then publish the pins as a second, small revision.
            try:
                receipt.update(self._publish_narration(key, client=client))
            except TimelineEditError as exc:  # the structure IS published: say so, and how to finish
                receipt["narration_error"] = (f"{exc}. The cut is published (head {receipt.get('new_head')}); only the "
                                              "narration binding is behind: timelines checkout TL --fresh, then "
                                              "timelines publish TL -m \"narration\" pins it")
        receipt["message"] = message
        return receipt

    def _publish_narration(self, key: str, *, client: Any = None) -> dict[str, Any]:
        def run(c: Any) -> dict[str, Any]:
            head = Checkout(fetch_bundle(str(self.bundle["project_id"]), str(self.bundle["timeline_id"]), client=c))
            pinned = head.pin_narration(c, idempotency_key=key)
            if not pinned:
                return {"narration_pinned": []}
            second = publish_bundle(head.document(), f"{key}-narration", client=c)
            return {"narration_pinned": pinned, "new_head": second.get("new_head"), "narration_head": second.get("new_head")}

        if client is not None:
            return run(client)
        from astrid.sdk import AstridClient

        with AstridClient.open_from_launcher(start_pack_host=False) as c:
            return run(c)

    # ---- narration: the script text each shot is bound to ---------------------------
    def narration(self) -> dict[str, str]:
        """``{shot_id: text}``: each shot's lines' script text, in order (what the shot's
        voiceover_script binding should hold). Only shots whose lines all declare their text."""
        by_shot: dict[str, list[Voice]] = {}
        for line in self.lines():
            by_shot.setdefault(line.clips[0].shot_id, []).append(line)
        out = {}
        for sid, lines in by_shot.items():
            if all(intent.line_text(v.clips[0].data) is not None for v in lines):
                out[sid] = " ".join(v.text for v in lines) + "\n"
        return out

    def narration_changes(self) -> dict[str, str]:
        """Shots whose narration differs from their bound voiceover_script (by content hash)."""
        import hashlib

        changed = {}
        for sid, text in self.narration().items():
            payload = self.bundle["shots"][sid].setdefault("payload", {})
            bound = next((b for b in payload.get("text_bindings") or [] if b.get("kind") == "voiceover_script"), None)
            digest = "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()
            if not bound or bound.get("content_hash") != digest:
                changed[sid] = text
        return changed

    def pin_narration(self, client: Any, *, idempotency_key: str) -> list[str]:
        """Register the changed narration per shot and pin it in the document (publish does this)."""
        pinned = []
        for sid, text in self.narration_changes().items():
            payload = self.bundle["shots"][sid]["payload"]
            bound = next((b for b in payload.get("text_bindings") or [] if b.get("kind") == "voiceover_script"), None)
            expected = int((bound or {}).get("head") or 0)
            result = client.shots.set_text_binding(self.bundle["project_id"], shot_id=sid, kind="voiceover_script", text=text,
                                                    expected_head=expected,
                                                    idempotency_key=f"{idempotency_key}-narration-{sid}"[:120])
            error = getattr(result, "error", None)
            actual = (getattr(error, "details", None) or {}).get("actual_head") if error is not None else None
            if getattr(result, "ok", True) is False and isinstance(actual, int) and actual != expected:
                # the shot's binding moved on (adopted content, another publish): bind on its current head
                result = client.shots.set_text_binding(self.bundle["project_id"], shot_id=sid, kind="voiceover_script",
                                                        text=text, expected_head=actual,
                                                        idempotency_key=f"{idempotency_key}-narration-{sid}-h{actual}"[:120])
            pin = getattr(result, "data", result)
            if getattr(result, "ok", True) is False or not isinstance(pin, Mapping):
                raise TimelineEditError(f"could not register the narration for shot {sid}: {getattr(result, 'error', result)}")
            payload["text_bindings"] = [b for b in payload.get("text_bindings") or [] if b.get("kind") != "voiceover_script"] + [dict(pin)]
            (payload.get("metadata") or {}).pop("voiceover_script", None)
            pinned.append(sid)
        return pinned

    def document(self) -> dict[str, Any]:
        """The publishable candidate (derived `_` fields removed)."""
        return _strip_derived(copy.deepcopy(self.bundle))

    # ---- internals ----------------------------------------------------------
    def _shot_ids(self) -> list[str]:
        rows = sorted(self.bundle.get("placements") or [], key=lambda r: _row_start(r))
        return [str(r.get("shot_id")) for r in rows]

    def _row(self, shot_id: str) -> dict[str, Any]:
        for row in self.bundle.get("placements") or []:
            if str(row.get("shot_id")) == shot_id:
                return row
        raise TimelineEditError(f"no shot {shot_id!r} is placed in this timeline")

    def _shot_start(self, shot_id: str) -> float:
        return _row_start(self._row(shot_id)) / 1000.0

    def _shot_length(self, shot_id: str) -> float:
        return _num(self._row(shot_id).get("duration_ms")) / 1000.0

    def _shot_id(self, shot: Any) -> str:
        ids = self._shot_ids()
        if isinstance(shot, int) or (isinstance(shot, str) and shot.isdigit()):
            n = int(shot)
            if not 1 <= n <= len(ids):
                raise TimelineEditError(f"shot {n} does not exist; shots are 1–{len(ids)}")
            return ids[n - 1]
        text = str(shot)
        for sid in ids:
            name = str(((self.bundle["shots"].get(sid) or {}).get("payload") or {}).get("name") or "")
            if sid == text or sid.endswith(text) or _norm(name) == _norm(text) or _norm(text) in _norm(name):
                return sid
        raise TimelineEditError(f"no shot matches {shot!r}; shots: " + ", ".join(ids))

    def _internal(self, shot_id: str) -> dict[str, Any]:
        shot = self.bundle["shots"][shot_id]
        internal = shot.setdefault("internal_timeline", {})
        internal.setdefault("clips", [])
        return internal

    def _track_kind(self, shot_id: str, track: str) -> str:
        for t in self._internal(shot_id).get("tracks") or []:
            if str(t.get("id")) == track:
                return str(t.get("kind") or "").lower()
        return ""

    def _is_audio(self, shot_id: str, clip: Mapping[str, Any]) -> bool:
        track = str(clip.get("track") or "")
        return self._track_kind(shot_id, track) in AUDIO_KINDS or track in AUDIO_KINDS

    def _shot_at(self, t: float) -> str:
        for sid in self._shot_ids():
            row = self._row(sid)
            start = _row_start(row) / 1000.0
            if start - 1e-6 <= t < start + _num(row.get("duration_ms")) / 1000.0 - 1e-6:
                return sid
        raise TimelineEditError(f"{t:.3f} s is outside every shot (the timeline is {self.duration:.3f} s)")

    def _cut_at(self, t: float) -> int | str:
        for cut in self.cuts:
            if cut.start - 1e-6 <= t < cut.end - 1e-6:
                return cut.n
        return "-"

    def _as_word(self, when: Any) -> Word | None:
        if isinstance(when, Word):
            return when
        if isinstance(when, str) and not TIME_RE.match(when.strip()):
            return self.word(when)
        return None

    def _pick(self, pool: Sequence[Clip], query: str, *, where: str) -> Clip:
        text = str(query).strip()
        tiers = [
            [c for c in pool if c.id == text or c.address == text],
            [c for c in pool if c.layer_name == text],
            [c for c in pool if c.id.startswith(text + "-") or c.id.startswith(text)],
            [c for c in pool if (c.asset or "").lower() == text.lower()],
            [c for c in pool if c.element == text],
            [c for c in pool if c.text and _norm(text) in _norm(c.text)],
        ]
        for hits in tiers:
            unique = list({id(c.data): c for c in hits}.values())
            if len(unique) == 1:
                return unique[0]
            if len(unique) > 1:
                listing = "\n  ".join(f"{c.address:<18} {c.element:<14} {c.start:7.3f}–{c.end:.3f} s" for c in unique[:12])
                more = f"\n  … {len(unique) - 12} more" if len(unique) > 12 else ""
                raise TimelineEditError(f"{text!r} matches {len(unique)} clips in {where}; name one:\n  {listing}{more}")
        raise TimelineEditError(f"no clip in {where} matches {text!r} (by id, asset, element or text); "
                                f"list them with tl.clips() or `timelines show --as script`")

    def _new_id(self, element: str) -> str:
        existing = {c.id for c in self.clips()}
        k = 1
        while f"{element}-{k:02d}" in existing:
            k += 1
        return f"{element}-{k:02d}"

    def _registry(self, shot_id: str) -> dict[str, Any]:
        registry = self._internal(shot_id).setdefault("registry", {})
        return registry.setdefault("assets", {})

    def _follow_media(self, clip: "Clip", old: Mapping[str, Any], new: Mapping[str, Any], asset: Any) -> None:
        """After an audio swap: the clip's length follows the new file, and a music clip's beats come along."""
        project = str(self.bundle.get("project_id") or "")
        local = Path(str(asset)).expanduser() if isinstance(asset, (str, Path)) and Path(str(asset)).expanduser().is_file() else None
        data = clip.data
        if ("from" in data or "to" in data) and not intent.until(data) and intent.for_s(data) is None:
            new_s = media_seconds(project, local or new)
            old_s = media_seconds(project, old) if old else None
            src0, speed = _num(data.get("from")), _num(data.get("speed"), 1.0) or 1.0
            to = _num(data.get("to"))
            whole = old_s is not None and src0 <= 1e-3 and abs(to - old_s) <= 1.0 / self.fps
            before = clip.duration
            if new_s is None:
                self.notes.append(f"{clip.address}: could not read the new file's length; it keeps {before:.3f} s "
                                  "(--duration S to change it)")
            elif whole or to > new_s + 1e-3:
                _set_length(data, mo.floor_frame((new_s - src0) / speed, self.fps))
                self.notes.append(f"{clip.address}: plays the new file {'whole' if whole else 'to its end'}: "
                                  f"{before:.3f} → {clip.duration:.3f} s")
            elif abs(new_s - (to - src0)) > 1.0 / self.fps:
                self.notes.append(f"{clip.address}: kept its length {before:.3f} s (it was a part of the old file); "
                                  f"the new file is {new_s:.3f} s (--duration {new_s:.3f} plays it all)")
        if not (clip.track == "music" or intent.beat_sources(data)):
            return
        from astrid.sdk.media_handles import is_media_handle

        found = None
        if local is not None:
            found = file_beats(local)
        elif isinstance(asset, str) and is_media_handle(asset):
            found = handle_beats(project, asset)
        if found is not None:
            self._attach_beats(clip, *found)
            return
        value = intent._app(data).get("beats")
        if value:  # the grid it has was made for the old file: say so until it is replaced or kept
            grid = dict(value) if isinstance(value, Mapping) else {"beats": intent.beat_sources(data), "time": "cue_seconds"}
            grid["media_id"] = old.get("media_id") or grid.get("media_id") or "the old file"
            intent.set_beats(data, grid)
        self.notes.append(f"{clip.address}: no beat grid came with the new file; " + (
            f"its beats are still the old file's ({intent.beats_label(data)}): attach the new grid with "
            "--beats FILE|run:<id>/beats (or --beats keep)" if value else "attach one with --beats FILE|run:<id>/beats"))

    def _attach_beats(self, clip: "Clip", grid: Mapping[str, Any], label: str) -> None:
        grid = dict(grid)
        media = (self._registry(clip.shot_id).get(str(clip.asset)) or {}).get("media_id") if clip.asset else None
        if media:
            grid["media_id"] = media
        else:
            grid.pop("media_id", None)
        intent.set_beats(clip.data, grid)
        moved = [line for line in self.resolve() if line]
        detail = f"{len(grid.get('beats') or [])} beats" + (f", {grid['bpm']:g} bpm" if isinstance(grid.get("bpm"), (int, float)) else "")
        heard = _num(clip.data.get("to")) if "to" in clip.data else None
        if isinstance(grid.get("duration_s"), (int, float)) and heard is not None and heard > float(grid["duration_s"]) + 0.5:
            self.notes.append(f"{clip.address}: the beats file covers {grid['duration_s']:g} s but the clip plays to "
                              f"{heard:.3f} s of its file: is it the grid for this music?")
        self.notes.append(f"{clip.address}: beats from {label} ({detail}); " + (
            f"{len(moved)} beat moment(s) re-resolved: " + "; ".join(moved[:8]) + (f"; … {len(moved) - 8} more" if len(moved) > 8 else "")
            if moved else "no moment moved"))

    def _stale_beats(self) -> list[str]:
        """Music clips whose beat grid was made for another file (after a swap)."""
        out = []
        for clip in self.clips(audio=True):
            value = intent._app(clip.data).get("beats")
            if not isinstance(value, Mapping) or not value.get("media_id"):
                continue
            media = (self._registry(clip.shot_id).get(str(clip.asset)) or {}).get("media_id")
            if media and value["media_id"] != media:
                out.append(f"{clip.address}: its beats ({intent.beats_label(clip.data)}) were made for "
                           f"{str(value['media_id'])[:19]}…, not the file it plays ({str(media)[:19]}…): attach this "
                           "file's grid (--beats FILE|run:<id>/beats) or keep the old one (--beats keep)")
        return out

    def _register_asset(self, shot_id: str, asset: Any) -> str:
        """Make ``asset`` resolvable in this shot; return its registry key."""
        assets = self._registry(shot_id)
        if isinstance(asset, Path):
            asset = str(asset)
        if isinstance(asset, str) and asset in assets:
            return asset
        if isinstance(asset, str):
            for sid in self._shot_ids():
                entry = self._registry(sid).get(asset)
                if entry is not None:
                    assets[asset] = copy.deepcopy(entry)
                    return asset
            path = Path(asset).expanduser()
            if path.is_file():
                entry = import_media(path, self.bundle.get("project_id"))
                key = _free_key(assets, re.sub(r"[^A-Za-z0-9_-]+", "-", path.stem).strip("-") or "asset", entry)
                assets[key] = entry
                return key
            from astrid.sdk.media_handles import is_media_handle

            if is_media_handle(asset):  # run:…#n, task:…, ref:NAME, sha256:… (media already in the project)
                entry = resolve_handle_entry(str(self.bundle.get("project_id")), asset)
                key = _free_key(assets, str(entry.pop("key")), entry)
                assets[key] = {k: v for k, v in entry.items() if k in ("media_id", "content_sha256", "type", "resolution")}
                return key
            known = sorted({k for sid in self._shot_ids() for k in self._registry(sid)})
            close = difflib.get_close_matches(asset, known, n=5)
            raise TimelineEditError(f"no asset {asset!r} in this timeline" + (f"; did you mean {', '.join(close)}?" if close else "")
                                    + " (or a local file to import, or a media handle: run:RUN/PORT#n, ref:NAME, sha256:…)")
        if isinstance(asset, Mapping):
            entry = dict(asset)
            media_id = entry.get("media_id") or entry.get("digest") or entry.get("object_id")
            if not media_id:
                raise TimelineEditError("an asset entry needs media_id (or digest)")
            entry.setdefault("media_id", media_id)
            entry.setdefault("content_sha256", entry.get("digest") or media_id)
            key = str(entry.pop("key", None) or entry.get("name") or str(media_id)[-12:])
            assets[key] = {k: v for k, v in entry.items() if k in ("media_id", "content_sha256", "type", "resolution")}
            return key
        raise TimelineEditError(f"cannot use {asset!r} as an asset")

    def _corner(self, element: str, params: Mapping[str, Any], asset: str | None, shot_id: str, corner: str) -> dict[str, Any]:
        corner = corner.replace("_", "-").lower()
        if corner not in ("top-right", "top-left", "bottom-right", "bottom-left"):
            raise TimelineEditError("corner must be top-right, top-left, bottom-right or bottom-left")
        width, height = 320, 120
        if element == "am-sprite" and asset:
            res = str((self._registry(shot_id).get(asset) or {}).get("resolution") or "")
            m = re.match(r"^(\d+)x(\d+)$", res)
            scale = int(_num(params.get("scale"), 6))
            frames = params.get("frames") or {}
            if frames.get("frameWidth"):
                width, height = int(frames["frameWidth"]) * scale, int(frames["frameHeight"]) * scale
            elif m:
                width, height = int(m.group(1)) * scale, int(m.group(2)) * scale
        elif isinstance(params.get("width"), (int, float)):
            width = int(params["width"])
        x = SAFE_MARGIN[0] if "left" in corner else CANVAS[0] - SAFE_MARGIN[0] - width
        y = SAFE_MARGIN[1] if "top" in corner else CANVAS[1] - SAFE_MARGIN[1] - height
        if element == "am-sprite":  # logical px on the 6 px grid
            return {"x": int(x // 6), "y": int(y // 6)}
        return {"x": int(x), "y": int(y)}

    def _shift_after(self, t: float, delta: float, *, cut_window: tuple[float, float] | None,
                     skip: Sequence[Mapping[str, Any]] = ()) -> float:
        """Ripple core. ``delta < 0`` removes ``cut_window`` (it may cross shots); ``delta > 0`` opens time at ``t``."""
        skip_ids = {id(c) for c in skip}
        rows = sorted(self.bundle.get("placements") or [], key=_row_start)
        if delta > 0:
            for row in rows:
                s = _row_start(row) / 1000.0
                e = s + _num(row.get("duration_ms")) / 1000.0
                if s >= t - 1e-6:
                    _set_row_start(row, s + delta)
                elif e > t + 1e-6:
                    self._open_in_shot(str(row.get("shot_id")), t - s, delta, skip_ids)
                    row["duration_ms"] = int(round((e - s + delta) * 1000))
            self._shift_parent(t, delta)
            return _r(delta)
        a, b = cut_window or (t, t)
        removed = 0.0
        for row in reversed(rows):  # right to left, so earlier positions stay valid
            s = _row_start(row) / 1000.0
            e = s + _num(row.get("duration_ms")) / 1000.0
            if e <= a + 1e-6 or s >= b - 1e-6:
                continue
            ra, rb = max(a - s, 0.0), min(b - s, e - s)
            d = rb - ra
            self._close_in_shot(str(row.get("shot_id")), ra, rb, skip_ids)
            row["duration_ms"] = int(round((e - s - d) * 1000))
            for later in rows:
                if _row_start(later) / 1000.0 >= e - 1e-6 and later is not row:
                    _set_row_start(later, _row_start(later) / 1000.0 - d)
            removed += d
        self._shift_parent(b, -removed)
        return _r(removed)

    def _close_in_shot(self, sid: str, ra: float, rb: float, skip_ids: set[int]) -> None:
        d = rb - ra
        clips = self._internal(sid)["clips"]
        out = []
        for clip in clips:
            if id(clip) in skip_ids:
                out.append(clip)
                continue
            cs = _num(clip.get("at"))
            ce = cs + clip_duration(clip)
            audio = self._is_audio(sid, clip)
            if ce <= ra + 1e-6:
                out.append(clip)
            elif cs >= rb - 1e-6:
                clip["at"] = _r(cs - d)
                out.append(clip)
            elif intent.orphan(clip):  # an orphan waiting to be re-homed: never trimmed by a later edit
                out.append(clip)
            elif cs >= ra - 0.5 / self.fps and ce <= rb + 0.5 / self.fps:  # inside, to the frame (no sub-frame stubs)
                if self._keep_inside:  # kept where it was, length and all: an orphan to re-home or remove
                    intent.set_orphan(clip, self._orphan_reason)
                    out.append(clip)
                    self._kept.append(str(clip.get("id")))
                continue  # wholly inside the removed window
            elif cs < ra and ce > rb:
                if audio and self._is_music(sid, clip) and self.music == "whole":
                    self.notes.append(f"music {clip.get('id')}: the bed plays on unchanged over the cut at "
                                      f"{self._shot_start(sid) + ra:.2f} s; what follows moved {-d:+.2f} s against its beats")
                    out.append(clip)
                elif audio and self._is_music(sid, clip):
                    out.extend(self._music_seam(sid, clip, ra, cut_s=d))
                elif audio:
                    out.extend(_split_audio(clip, ra - cs, cut=d))
                else:
                    _set_length(clip, (ce - cs) - d)
                    out.append(clip)
            elif cs < ra:  # ends inside the window
                _trim_end(clip, ra - cs)
                out.append(clip)
            else:  # starts inside the window
                _trim_front(clip, rb - cs)
                clip["at"] = _r(ra)
                out.append(clip)
        clips[:] = out

    def _open_in_shot(self, sid: str, ra: float, d: float, skip_ids: set[int]) -> None:
        clips = self._internal(sid)["clips"]
        out = []
        for clip in clips:
            if id(clip) in skip_ids:
                out.append(clip)
                continue
            cs = _num(clip.get("at"))
            ce = cs + clip_duration(clip)
            if cs >= ra - 1e-6:
                clip["at"] = _r(cs + d)
            elif ce > ra + 1e-6:
                if self._is_music(sid, clip) and self.music == "whole":
                    self.notes.append(f"music {clip.get('id')}: the bed plays on unchanged over the {d:.2f} s opened at "
                                      f"{self._shot_start(sid) + ra:.2f} s; what follows moved {d:+.2f} s against its beats")
                    out.append(clip)
                    continue
                if self._is_music(sid, clip):
                    out.extend(self._music_seam(sid, clip, ra, open_s=d))
                    continue
                if self._is_audio(sid, clip):
                    if intent.line(clip):
                        out.extend(_split_audio(clip, ra - cs, gap=d))
                        continue
                    out.append(clip)  # a sound effect already playing finishes where it is
                    continue
                _set_length(clip, (ce - cs) + d)  # picture spanning the point holds longer
            out.append(clip)
        clips[:] = out

    def _is_music(self, sid: str, clip: Mapping[str, Any]) -> bool:
        return self._is_audio(sid, clip) and (str(clip.get("track") or "") == "music" or bool(intent.beat_sources(clip)))

    def _music_seam(self, sid: str, clip: dict[str, Any], ra: float, *, open_s: float = 0.0, cut_s: float = 0.0) -> list[dict[str, Any]]:
        """Split music at the last beat at or before ``ra`` (shot seconds) so the seam is on a beat.

        Opening time repeats ``open_s`` seconds of music (no silent hole); closing it skips
        ``cut_s`` seconds. Either way the music after the seam stays in sync with the picture
        after it. The seam is noted, with how many beats it repeats or skips."""
        speed = _num(clip.get("speed"), 1.0) or 1.0
        cs = _num(clip.get("at"))
        src0 = _num(clip.get("from"))
        beats = [cs + (b - src0) / speed for b in intent.beat_sources(clip)]
        on_beat = [b for b in beats if cs + 1e-3 < b <= ra + 1e-6]
        seam = max(on_beat) if on_beat else ra
        at = self._shot_start(sid) + seam
        span = open_s or cut_s
        period = intent.beat_period(clip)
        beats_txt = ""
        if period:
            n = span / period
            whole = abs(n - round(n)) < 0.08
            beats_txt = f" = {n:.2f} beats" + ("" if whole else ", not a whole beat: listen to the seam")
        verb = "repeats" if open_s else "skips"
        where = "on a beat" if on_beat else "not on a beat (no beat before it)"
        if open_s and src0 + (seam - cs) * speed - open_s * speed < -1e-6:
            self.notes.append(f"music {clip.get('id')}: {open_s:.3f} s of silence at {at:.3f} s (not enough music before the seam to repeat)")
            return _split_audio(clip, seam - cs, gap=open_s)
        self.notes.append(f"music {clip.get('id')}: seam at {at:.3f} s {where}; {verb} {span:.3f} s{beats_txt}")
        return _split_audio(clip, seam - cs, cut=cut_s, repeat=open_s)

    def _shift_parent(self, after: float, delta: float) -> None:
        for clip in (self.bundle.get("parent") or {}).get("clips") or []:
            if _num(clip.get("at")) >= after - 1e-6:
                clip["at"] = _r(_num(clip.get("at")) + delta)

    def _annotated(self) -> dict[str, Any]:
        """The bundle plus derived, read-only helpers: an index and both clocks on every clip."""
        doc = _strip_derived(copy.deepcopy(self.bundle))
        index = {
            "note": EDITABLE_NOTE,
            "fps": self.fps,
            "duration_s": self.duration,
            "cuts": [{"n": c.n, "start": _r(c.start), "end": _r(c.end), "shot": c.shot,
                      "picture": c.picture.id if c.picture else None, "layers": [l.id for l in c.layers]} for c in self.cuts],
            "words": [[w.id, w.text, w.start, w.end] for w in self.words()],
        }
        for sid in self._shot_ids():
            start = self._shot_start(sid)
            for clip in doc["shots"][sid].get("internal_timeline", {}).get("clips", []):
                cs = start + _num(clip.get("at"))
                clip["_timeline"] = {"start": _r(cs), "end": _r(cs + clip_duration(clip))}
        return {"_index": index, **doc}

    def _absorb(self, raw: dict[str, Any]) -> dict[str, Any]:
        """Apply hand edits made in `_timeline` (timeline seconds), then drop every derived field."""
        bundle = raw
        rows = {str(r.get("shot_id")): r for r in bundle.get("placements") or []}
        for sid, shot in (bundle.get("shots") or {}).items():
            row = rows.get(sid)
            if row is None:
                continue
            start = _row_start(row) / 1000.0
            for clip in (shot.get("internal_timeline") or {}).get("clips", []):
                stamp = clip.get("_timeline")
                if not isinstance(stamp, Mapping):
                    continue
                cs = start + _num(clip.get("at"))
                ce = cs + clip_duration(clip)
                new_s, new_e = _num(stamp.get("start"), cs), _num(stamp.get("end"), ce)
                if abs(new_s - cs) > 1e-4 or abs(new_e - ce) > 1e-4:
                    if abs(new_s - cs) > 1e-4 and abs(new_e - ce) <= 1e-4:
                        new_e = new_s + (ce - cs)  # moved start only: keep the length
                    clip["at"] = _r(new_s - start)
                    _set_length(clip, new_e - new_s)
                    self.notes.append(f"{clip.get('id')}: applied your _timeline edit → at {clip['at']} (shot {sid})")
        return _strip_derived(bundle)


@dataclass
class CheckReport:
    valid: bool
    validation: Any
    summary: list[str]
    lint: list[str]
    problems: list[str]
    changed_cuts: list[int]
    diff: Mapping[str, Any]
    cut_names: list[str] = dataclasses.field(default_factory=list)
    blocking: list[str] = dataclasses.field(default_factory=list)
    lint_new: list[str] | None = None   # lint findings the published head does not have (None: not computed)
    lint_old: int = 0                    # findings on the changed cuts that were already there

    def brief(self, *, full: bool = False, cuts: Iterable[str] | None = None) -> list[str]:
        """Calm lines for after an edit: valid or not, what blocks publishing, and NEW lint (findings the
        published head does not have; the ones that were already there are counted). ``cuts`` (cut ids):
        only what concerns those cuts, with a count of the rest. ``full`` (``--all``): every line."""
        new = list(self.lint if full or self.lint_new is None else self.lint_new)
        old = 0 if full or self.lint_new is None else self.lint_old
        blocking, notes = list(self.blocking), [p for p in self.problems if p not in self.blocking and not p.startswith("resolved ")]
        said = set()  # a note about a param that a blocking line already names is said once
        for line in blocking:
            m = re.match(r"^\w+\s+(\S+?)(?:\s+params\.(\S+))?\s+=", line)
            if m:
                said.add(f"{m.group(1)}.{m.group(2)}" if m.group(2) else m.group(1))
        notes = [p for p in notes if p.split(": ", 1)[0] not in said]
        elsewhere = 0
        if cuts is not None and not full:
            wanted = {c for c in cuts if c}
            pattern = re.compile(r"\b(" + "|".join(map(re.escape, sorted(wanted))) + r")\b") if wanted else None

            def mine(line: str) -> bool:
                return bool(pattern and pattern.search(line))

            kept = [line for line in new if mine(line)]
            kept_blocking = [line for line in blocking if mine(line)]
            elsewhere = (len(new) - len(kept)) + (len(blocking) - len(kept_blocking))
            new, blocking = kept, kept_blocking
        head = "check   " + ("valid" if self.valid else "NOT VALID")
        if self.blocking:
            head += f" · {len(self.blocking)} to fix before publishing" + (
                f" ({len(blocking)} here)" if cuts is not None and not full else " (re-home each orphan with a new moment, or remove it)")
        if cuts is not None and not full:
            where = ", ".join(sorted(set(cuts))) or "no cut"
            head += f" · new lint on {where if len(where) <= 60 else where[:57] + '…'}: " + ("clean" if not new else f"{len(new)}")
        else:
            where = ", ".join(self.cut_names or [f"cut {n}" for n in self.changed_cuts])
            if where:
                head += f" · new lint on the {len(self.cut_names or self.changed_cuts)} changed cut(s): " + ("clean" if not new else f"{len(new)}")
        if full or len(blocking) <= 8:
            shown_blocking = [f"  ! {p}" for p in blocking]
        else:
            by_cut: dict[str, int] = {}
            for line in blocking:
                address = line.split()[1] if len(line.split()) > 1 else "?"
                by_cut[address.split(".")[0]] = by_cut.get(address.split(".")[0], 0) + 1
            shown_blocking = [f"  ! {len(blocking)} orphan(s) in {len(by_cut)} cut(s): "
                              + ", ".join(f"{cut} ({n})" for cut, n in by_cut.items()),
                              "    each one: timelines status TL --project P --all   ·   the sheet's orphans section"]
        lint = [f"  {line}" for line in new] if full or len(new) <= 12 else (
            [f"  {line}" for line in new[:12]] + [f"  … {len(new) - 12} more new finding(s) (timelines check TL --all)"])
        tail = []
        if old or elsewhere:
            tail.append("  (" + " · ".join(x for x in (
                f"{elsewhere} more on other cuts" if elsewhere else "",
                f"{old} lint finding(s) were already there before your changes" if old else "") if x)
                + ": timelines check TL --all)")
        return [head] + shown_blocking + [f"  · {p}" for p in notes[:12]] + lint + tail

    def full_text(self) -> str:
        """Everything: the whole diff summary, every problem, every lint line on the changed cuts."""
        lines = [("valid" if self.valid else "INVALID") + " · " + (self.summary[0] if self.summary else "no changes")]
        lines += self.summary[1:]
        lines += [f"! {p}" for p in self.problems]
        if self.changed_cuts:
            lines.append(f"lint on changed cuts {', '.join(self.cut_names or map(str, self.changed_cuts))}: "
                         + ("clean" if not self.lint else f"{len(self.lint)} line(s)"))
            lines += [f"  {line}" for line in self.lint]
        return "\n".join(lines)

    def __str__(self) -> str:
        """The default report: the diff in brief, what blocks publishing, notes, and NEW lint (counts for the rest)."""
        lines = [("valid" if self.valid else "INVALID") + " · " + (self.summary[0] if self.summary else "no changes")]
        kinds: dict[str, int] = {}
        rest = []
        for line in self.summary[1:]:  # "cut added at 7.37 s" × 44 → "44 cuts added"
            m = re.match(r"^cuts? (added|removed|moved)\b", line.strip())
            if m:
                kinds[m.group(1)] = kinds.get(m.group(1), 0) + 1
            else:
                rest.append(line)
        if kinds:
            lines.append("  cut points: " + " · ".join(f"{n} {what}" for what, n in kinds.items()))
        lines += rest[:10] + ([f"  … {len(rest) - 10} more clip lines"] if len(rest) > 10 else [])
        if kinds or len(rest) > 10:
            lines.append("  (every line: timelines check TL --all · timelines diff TL)")
        orphans = sum(1 for p in self.blocking if p.startswith("orphan"))
        lines.append(f"  {orphans} orphan(s) · {len(self.blocking)} to fix before publishing")
        lines += self.brief()[1:]
        return "\n".join(lines)


# ----------------------------------------------------------------- document helpers

def _end_rule(tl: "Checkout", clip: Clip) -> str:
    """How a clip's end is decided, in words: until "Astrid" · for 1.9s · with its cut · its own length."""
    until, length = intent.until(clip.data), intent.for_s(clip.data)
    if until:
        return f"holds until {until}"
    if length is not None:
        return f"holds for {mo.offset_text(length, tl.fps).lstrip('+')}"
    if intent.cut_of(clip.data) and not clip.is_audio:
        return "ends with its cut"
    return "keeps its own length"


def _text_where_number(path: str, value: Any, spec: Mapping[str, Any]) -> list[tuple[str, str, str]]:
    """``(param path, the text, the type wanted)`` for every string where the schema wants a number
    (``allAt: 'ƒ("adapt" in w05c)'``, ``states[3].at: '"adapt"'``), nested lists and objects included."""
    want = spec.get("type")
    wants = want if isinstance(want, list) else [want]
    if isinstance(value, str):
        numeric = [w for w in wants if w in ("integer", "number")]
        return [(path, value, numeric[0])] if numeric and "string" not in wants else []
    out: list[tuple[str, str, str]] = []
    if isinstance(value, list) and isinstance(spec.get("items"), Mapping):
        for i, item in enumerate(value):
            out += _text_where_number(f"{path}[{i}]", item, spec["items"])
    elif isinstance(value, Mapping) and isinstance(spec.get("properties"), Mapping):
        for key, item in value.items():
            if isinstance(spec["properties"].get(key), Mapping):
                out += _text_where_number(f"{path}.{key}", item, spec["properties"][key])
    return out


def _lint_key(line: str) -> str:
    """A finding without its numbers: the same finding on a cut that moved is the same finding."""
    return re.sub(r"[-+]?\d+(?:\.\d+)?", "#", line)


def _new_findings(lint: list[str], base: "Checkout") -> tuple[list[str] | None, int]:
    """(findings the base does not have, how many it already had)."""
    from collections import Counter

    from astrid.sdk.timeline_address import ordinals_to_ids

    if not lint:
        return [], 0
    try:
        seen = Counter(_lint_key(ordinals_to_ids(line, base)) for line in base.lint())
    except Exception:  # noqa: BLE001 - without a base lint every finding counts as new
        return None, 0
    new, old = [], 0
    for line in lint:
        key = _lint_key(line)
        if seen[key] > 0:
            seen[key] -= 1
            old += 1
        else:
            new.append(line)
    return new, old


def describe_changes(before: "Checkout", after: "Checkout") -> list[str]:
    """Plain-words lines for what changed between two versions of a timeline (see ``Checkout.changes``)."""
    old = {c.id: c for c in before.clips()}
    new = {c.id: c for c in after.clips()}
    fps = after.fps
    lines: list[tuple[float, str]] = []
    shifts: dict[int, list[Clip]] = {}

    def secs(delta: float) -> str:
        return f"{delta:+.2f} s"

    width = max([len(c.address) for c in list(new.values()) + list(old.values())] + [8])
    for cid, clip in new.items():
        prev = old.get(cid)
        if prev is None:
            what = " ".join(x for x in (clip.element.removeprefix("am-"), clip.asset or (f'"{clip.text[:24]}"' if clip.text else "")) if x)
            on = f" on {clip.anchor}" if clip.anchor else ""
            lines.append((clip.start, f"+ {clip.address:<{width}}  added: {what}{on}  {clip.start:.3f}–{clip.end:.3f} s"))
            continue
        said: list[str] = []
        moved_start = round(prev.start * fps) != round(clip.start * fps)
        moved_end = round(prev.end * fps) != round(clip.end * fps)
        a_on, b_on = intent.on(prev.data), intent.on(clip.data)
        a_until, b_until = intent.until(prev.data), intent.until(clip.data)
        a_for, b_for = intent.for_s(prev.data), intent.for_s(clip.data)
        if a_on != b_on:
            what = f"now enters on {b_on} = {clip.start:.2f} s" if b_on else f"no longer tied to a moment: starts {clip.start:.2f} s"
            what += f" (was {prev.start:.2f} s, {secs(clip.start - prev.start)})" if moved_start else " (it was already there)"
            if not (a_until != b_until or a_for != b_for):
                what += f"; {_end_rule(after, clip)}, ending {clip.end:.2f} s"
            said.append(what)
            moved_start = False
        if a_until != b_until or a_for != b_for:
            what = f"now {_end_rule(after, clip)} = ends {clip.end:.2f} s"
            what += f" (was {prev.end:.2f} s, {secs(clip.end - prev.end)})" if moved_end else " (it already ended there)"
            said.append(what)
            moved_end = False
        if (clip.asset or None) != (prev.asset or None):
            said.append(f"asset {prev.asset} → {clip.asset}")
        if clip.element != prev.element:
            said.append(f"element {prev.element} → {clip.element}")
        fa, fb = intent.formulas(prev.data), intent.formulas(clip.data)
        formula_keys = set()
        for path in sorted(set(fa) | set(fb)):
            a_expr, b_expr = fa.get(path), fb.get(path)
            if a_expr == b_expr:
                continue
            key = path.removeprefix("params.")
            top = key.split(".")[0].split("[")[0]
            formula_keys.add(top)
            value = to_canvas(clip.element, key, clip.params.get(key)) if "." not in key and "[" not in key else None
            shown = f" = {json.dumps(value, ensure_ascii=False)}" if value is not None else ""
            if a_expr and not b_expr:
                said.append(f"{key} was {formula_short(a_expr, clip.element)} → now fixed{shown}"
                            + (f" (write {key}={formula_short(a_expr, clip.element)} to keep it computed)" if "mark" in a_expr else ""))
            elif b_expr and not a_expr:
                said.append(f"{key} now {formula_short(b_expr, clip.element)}{shown}")
            else:
                said.append(f"{key} {formula_short(a_expr, clip.element)} → {formula_short(b_expr, clip.element)}{shown}")
        for key in sorted(set(prev.params) | set(clip.params)):
            a_val, b_val = prev.params.get(key), clip.params.get(key)
            if a_val == b_val or key in formula_keys:
                continue
            if isinstance(a_val, (dict, list)) or isinstance(b_val, (dict, list)):
                said.append(f"{key} changed")
            else:
                said.append(f"{key} {json.dumps(to_canvas(clip.element, key, a_val), ensure_ascii=False)} → "
                            f"{json.dumps(to_canvas(clip.element, key, b_val), ensure_ascii=False)}")
        same_shift = abs((clip.end - prev.end) - (clip.start - prev.start)) < 0.5 / fps
        if any(x.startswith(("now enters", "no longer")) for x in said) and same_shift:
            moved_end = False  # it kept its length
        if said:
            if moved_start and not any(x.startswith(("now enters", "no longer")) for x in said):
                said.append(f"starts {prev.start:.3f} → {clip.start:.3f} s")
            if moved_end and not any(x.startswith("now holds") or x.startswith("now ends") for x in said):
                said.append(f"ends {prev.end:.3f} → {clip.end:.3f} s")
            lines.append((clip.start, f"✎ {clip.address:<{width}}  " + " · ".join(said)))
        elif moved_start or moved_end:
            if moved_start and abs((clip.end - prev.end) - (clip.start - prev.start)) < 0.5 / fps:
                shifts.setdefault(round((clip.start - prev.start) * fps), []).append(clip)
            else:
                lines.append((clip.start, f"✎ {clip.address:<{width}}  {prev.start:.3f}–{prev.end:.3f} → {clip.start:.3f}–{clip.end:.3f} s"))
    for cid, clip in old.items():
        if cid not in new:
            lines.append((clip.start, f"− {clip.address:<{width}}  removed ({clip.element.removeprefix('am-')} {clip.asset or ''})".rstrip() + ")" * 0))
    for frames, clips in sorted(shifts.items(), key=lambda item: min(c.start for c in item[1])):
        first = min(clips, key=lambda c: c.start)
        if len(clips) <= 3:
            for c in clips:
                lines.append((c.start, f"✎ {c.address:<{width}}  {c.start - frames / fps:.3f} → {c.start:.3f} s ({secs(frames / fps)})"))
        else:
            lines.append((first.start, f"  {len(clips)} clips moved {secs(frames / fps)} from {first.start - frames / fps:.3f} s on (everything after the change)"))
    ordered = [text for _t, text in sorted(lines, key=lambda item: item[0])]
    # align the addresses to the widest one actually printed
    cells = [re.match(r"^(\S\s|\s\s)(\S+)\s+(.*)$", t) for t in ordered]
    pad = max([len(m.group(2)) for m in cells if m] + [0])
    return [f"{m.group(1)}{m.group(2):<{pad}}  {m.group(3)}" if m else t for m, t in zip(cells, ordered)]


def _post(value: float, expr: Mapping[str, Any]) -> Any:
    """A formula's post-steps, in order: offset_frames, min/max, step (down to a multiple)."""
    value = value + int(expr.get("offset_frames") or 0)
    if expr.get("min") is not None:
        value = max(value, expr["min"])
    if expr.get("max") is not None:
        value = min(value, expr["max"])
    step = expr.get("step")
    if step:
        value = value - (value % step)
    return value


def _first_word_lead(line: "Voice") -> float:
    """Seconds from a line's first clip start to its first word (the take's lead-in)."""
    words = line.words
    return (words[0].start - line.clips[0].start) if words else 0.0


def _row_start(row: Mapping[str, Any]) -> float:
    if row.get("at_ms") is not None:
        return _num(row.get("at_ms"))
    return _num((row.get("placement") or {}).get("start_ms"))


def _set_row_start(row: dict[str, Any], seconds: float) -> None:
    ms = int(round(seconds * 1000))
    if row.get("at_ms") is not None:
        row["at_ms"] = ms
    else:
        row.setdefault("placement", {})["start_ms"] = ms


def _set_length(clip: dict[str, Any], seconds: float) -> None:
    speed = _num(clip.get("speed"), 1.0) or 1.0
    if "hold" in clip or ("from" not in clip and "to" not in clip):
        clip["hold"] = _r(seconds * speed)
    else:
        clip["to"] = _r(_num(clip.get("from")) + seconds * speed)


def _trim_end(clip: dict[str, Any], keep: float) -> None:
    _set_length(clip, keep)
    _filter_words(clip, 0.0, keep)


def _trim_front(clip: dict[str, Any], cut: float) -> None:
    speed = _num(clip.get("speed"), 1.0) or 1.0
    length = clip_duration(clip) - cut
    if "hold" in clip or ("from" not in clip and "to" not in clip):
        clip["hold"] = _r(length * speed)
    else:
        clip["from"] = _r(_num(clip.get("from")) + cut * speed)
    _filter_words(clip, cut, cut + length, shift=-cut)


def _filter_words(clip: dict[str, Any], lo: float, hi: float, *, shift: float = 0.0) -> None:
    intent.map_timed(clip, lambda rows: [[_r(i[0] + shift), _r(i[1] + shift), *i[2:]] for i in rows if lo - 1e-6 <= i[0] < hi])


def _split_audio(clip: dict[str, Any], at: float, *, cut: float = 0.0, gap: float = 0.0, repeat: float = 0.0) -> list[dict[str, Any]]:
    """Split an audio clip ``at`` seconds in; the second part skips ``cut`` seconds of source
    (ripple delete), starts ``gap`` seconds later (ripple insert), or replays the last
    ``repeat`` seconds (ripple insert under music, so there is no hole)."""
    speed = _num(clip.get("speed"), 1.0) or 1.0
    cs = _num(clip.get("at"))
    total = clip_duration(clip)
    second = copy.deepcopy(clip)
    second["id"] = f"{clip.get('id')}~{int(round((cs + at) * 1000))}"
    _trim_end(clip, at)
    skip_source = at + cut - repeat
    if "from" in second or "to" in second:
        second["from"] = _r(_num(second.get("from")) + skip_source * speed)
    else:
        second["hold"] = _r((total - skip_source) * speed)
    second["at"] = _r(cs + at + gap)
    _filter_words(second, skip_source, total, shift=-skip_source)
    return [clip, second] if total - skip_source > 1e-3 else [clip]


def _read_words(words: Any, *, relative: bool = False) -> list[tuple[float, float, str]]:
    """Words from a words.json path, ``{"words": [...]}`` (Edge TTS ``start_s``/``end_s`` or [s, e, text]) or a list."""
    if isinstance(words, (str, Path)) and Path(words).expanduser().is_file():
        words = json.loads(Path(words).expanduser().read_text(encoding="utf-8"))
    if isinstance(words, Mapping):
        words = words.get("words") or []
    out = []
    for item in words or []:
        if isinstance(item, Mapping):
            s = item.get("start_s", item.get("start"))
            e = item.get("end_s", item.get("end"))
            t = item.get("word", item.get("text"))
        elif isinstance(item, (list, tuple)) and len(item) >= 2:
            s, e = item[0], item[1]
            t = item[2] if len(item) > 2 else None
        else:
            continue
        if isinstance(s, (int, float)) and isinstance(e, (int, float)):
            out.append((float(s), float(e), str(t) if t is not None else ""))
    return out


_PATH_RE = re.compile(r"([A-Za-z_][\w-]*)|\[(\d+)\]")


def _get_path(data: Mapping[str, Any], path: str) -> Any:
    target: Any = data
    for m in _PATH_RE.finditer(path):
        name, index = m.group(1), m.group(2)
        try:
            target = target[name] if name is not None else target[int(index)]
        except (KeyError, IndexError, TypeError):
            return None
    return target


def _set_path(data: dict[str, Any], path: str, value: Any) -> bool:
    """Set ``params.keyframes[2].frame``-style paths; returns True when the value changed."""
    parts = [(m.group(1), m.group(2)) for m in _PATH_RE.finditer(path)]
    target: Any = data
    for i, (name, index) in enumerate(parts):
        last = i == len(parts) - 1
        if name is not None:
            if last:
                changed = target.get(name) != value
                target[name] = value
                return changed
            target = target.setdefault(name, {} if parts[i + 1][0] is not None else [])
        else:
            k = int(index)
            while len(target) <= k:
                target.append(None)
            if last:
                changed = target[k] != value
                target[k] = value
                return changed
            if target[k] is None:
                target[k] = {} if parts[i + 1][0] is not None else []
            target = target[k]
    return False


def _strip_derived(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _strip_derived(v) for k, v in value.items() if not str(k).startswith(DERIVED_PREFIX)}
    if isinstance(value, list):
        return [_strip_derived(v) for v in value]
    return value


def _serialize(doc: Mapping[str, Any]) -> str:
    """Pretty JSON for content; provenance (base_*, source_mapping) and the word index on one line each.

    Two pretty-printed copies of the same clip (content and its ``base_internal_timeline``)
    make text edits ambiguous; compact provenance keeps every content line unique.
    """
    def is_compact(key: str) -> bool:
        return key in BASE_KEYS or key.startswith(SHOT_BASE_PREFIX) or key == "words"

    def emit(value: Any, indent: int, key: str | None = None) -> str:
        pad, inner = " " * indent, " " * (indent + 2)
        if key is not None and is_compact(key):
            return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        if isinstance(value, dict) and value:
            items = [f"{inner}{json.dumps(k, ensure_ascii=False)}: {emit(v, indent + 2, str(k))}" for k, v in value.items()]
            return "{\n" + ",\n".join(items) + "\n" + pad + "}"
        if isinstance(value, list) and value:
            if all(not isinstance(v, (dict, list)) for v in value):
                return json.dumps(value, ensure_ascii=False)
            items = [f"{inner}{emit(v, indent + 2)}" for v in value]
            return "[\n" + ",\n".join(items) + "\n" + pad + "]"
        return json.dumps(value, ensure_ascii=False)

    return emit(dict(doc), 0) + "\n"


# ------------------------------------------------------------------ working copies (drafts)

def drafts_root() -> Path:
    """Where working copies live: Astrid's data root (``BANODOCO_LOCAL_DATA_ROOT``) / drafts."""
    base = os.environ.get("BANODOCO_LOCAL_DATA_ROOT")
    return (Path(base) if base else Path.home() / ".astrid") / "drafts"


def _clock(seconds: float) -> str:
    minutes, rest = divmod(max(0.0, float(seconds)), 60)
    return f"{int(minutes)}:{rest:05.2f}"


def _free_key(assets: Mapping[str, Any], key: str, entry: Mapping[str, Any]) -> str:
    """A registry key for new media: its name (``robot-native.png`` → ``robot-native``); ``-2`` if that
    name is already taken by other media."""
    base, k, n = key, key, 2
    while k in assets and (assets[k] or {}).get("media_id") != entry.get("media_id"):
        k, n = f"{base}-{n}", n + 1
    return k


def resolve_handle_entry(project: str, handle: str, *, client: Any = None) -> dict[str, Any]:
    """A media handle → a registry entry ``{key, media_id, content_sha256, type, resolution}`` (read-only)."""
    from astrid.sdk import AstridClient
    from astrid.sdk import media_handles as mh

    def run(c: Any) -> dict[str, Any]:
        resolver = getattr(mh, "resolve_timeline_asset", None)
        try:
            if callable(resolver):
                return dict(resolver(c, project, handle))
            descriptor, _lineage = mh.resolve_media_handle(c, project, handle)
        except mh.MediaHandleError as exc:
            raise TimelineEditError(f"{handle}: {exc}") from None
        name = Path(str(descriptor.get("filename") or "media")).stem
        return {"key": re.sub(r"[^A-Za-z0-9_-]+", "-", name).strip("-") or "media", "media_id": descriptor["digest"],
                "content_sha256": descriptor["digest"], "type": descriptor.get("media_type")}

    if client is not None:
        return run(client)
    with AstridClient.open_from_launcher(start_pack_host=False) as c:
        return run(c)


def _with_client(client: Any, fn: Any) -> Any:
    if client is not None:
        return fn(client)
    from astrid.sdk import AstridClient

    with AstridClient.open_from_launcher(start_pack_host=False) as c:
        return fn(c)


def load_beats(project: str, source: Any, *, client: Any = None) -> tuple[dict[str, Any], str]:
    """A beat grid from a beats file: a local JSON path, a media handle (``run:<id>/beats``, ``sha256:…``)
    or the parsed dict. Returns ``(stored grid, where it came from)``."""
    if isinstance(source, Mapping):
        label = str(source.get("source") or "a beats dict")
        return intent.beats_grid(source, label), label
    text = str(source).strip()
    path = Path(text).expanduser()
    if path.is_file():
        try:
            return intent.beats_grid(json.loads(path.read_text(encoding="utf-8")), path.name), path.name
        except (ValueError, OSError) as exc:
            raise TimelineEditError(f"{path}: not a beats file ({exc})") from None
    from astrid.sdk import media_handles as mh

    if not mh.is_media_handle(text):
        raise TimelineEditError(f"--beats {text!r}: not a file and not a media handle (run:<id>/beats, sha256:…)")

    def run(c: Any) -> tuple[dict[str, Any], str]:
        try:
            descriptor, _lineage = mh.resolve_media_handle(c, project, text)
            data = json.loads(c.media.read_bytes(descriptor["digest"]))
        except (mh.MediaHandleError, ValueError) as exc:
            raise TimelineEditError(f"--beats {text}: {exc}") from None
        try:
            return intent.beats_grid(data, text), text
        except ValueError as exc:
            raise TimelineEditError(f"--beats {text}: {exc}") from None

    return _with_client(client, run)


def handle_beats(project: str, handle: str, *, client: Any = None) -> tuple[dict[str, Any], str] | None:
    """The beat grid made with this media: the ``beats`` output of the run (or task) that made it
    (``chiptune.compose`` writes music + beats). None when that run made no beats file."""
    from astrid.sdk import media_handles as mh

    def run(c: Any) -> tuple[dict[str, Any], str] | None:
        try:
            _descriptor, lineage = mh.resolve_media_handle(c, project, handle)
        except mh.MediaHandleError:
            return None
        made = lineage.get("output") if isinstance(lineage.get("output"), Mapping) else {}
        kind, ident = ("run", made.get("run_id")) if made.get("run_id") else ("task", made.get("task_id"))
        if not ident:
            return None
        try:
            rows = mh._output_rows(c, kind, str(ident))
        except mh.MediaHandleError:
            return None
        if not any(r.get("output_port") == "beats" for r in rows):
            return None
        return load_beats(project, f"{kind}:{ident}/beats", client=c)

    try:
        return _with_client(client, run)
    except TimelineEditError:
        raise
    except Exception:  # noqa: BLE001 - no runtime: the caller says the beats did not come along
        return None


def file_beats(path: Path) -> tuple[dict[str, Any], str] | None:
    """A beats file beside a local music file: ``NAME.beats.json`` or the folder's ``beats.json``."""
    for candidate in (path.with_name(path.stem + ".beats.json"), path.with_name("beats.json")):
        if candidate.is_file():
            try:
                return load_beats("", candidate)
            except TimelineEditError:
                continue
    return None


def media_seconds(project: str, media: Any, *, client: Any = None) -> float | None:
    """How long an audio/video file plays: a local path, or a registry entry / digest in the project's
    media store. None when it cannot be read (no runtime, not media)."""
    import subprocess
    import tempfile

    from astrid.core.media import ffprobe_duration_seconds

    if isinstance(media, (str, Path)) and Path(str(media)).expanduser().is_file():
        try:
            return float(ffprobe_duration_seconds(Path(str(media)).expanduser()))
        except (subprocess.SubprocessError, ValueError, OSError):
            return None
    digest = str((media or {}).get("media_id") or (media or {}).get("content_sha256") or "") \
        if isinstance(media, Mapping) else str(media or "")
    if not digest.startswith("sha256:"):
        return None
    kind = str((media or {}).get("type") or "") if isinstance(media, Mapping) else ""

    def run(c: Any) -> float | None:
        data = c.media.read_bytes(digest)
        if kind in ("audio/wav", "audio/x-wav", "audio/wave") or data[:4] == b"RIFF":
            import io
            import wave

            try:
                with wave.open(io.BytesIO(data)) as w:
                    return w.getnframes() / float(w.getframerate())
            except (wave.Error, EOFError):
                pass
        with tempfile.NamedTemporaryFile(suffix=Path(kind.replace("/", ".")).suffix or ".bin") as tmp:
            tmp.write(data)
            tmp.flush()
            return float(ffprobe_duration_seconds(tmp.name))

    try:
        return _with_client(client, run)
    except Exception:  # noqa: BLE001 - a length we cannot read is reported, never fatal
        return None


def _snapshot(tl: "Checkout") -> str:
    """The document as compact JSON (cheap): what an undo step restores."""
    return json.dumps(tl.bundle, sort_keys=True, default=str, separators=(",", ":"))


def _draft_text(snapshot: str) -> str:
    return _serialize(Checkout(json.loads(snapshot))._annotated())


def _write_step(folder: Path, label: str, state: str) -> None:
    import time as _time

    folder.mkdir(parents=True, exist_ok=True)
    name = f"{_time.time_ns():020d}-{uuid.uuid4().hex[:4]}.json"  # monotonic: undo order never depends on a count
    (folder / name).write_text(json.dumps({"label": label, "state": state}), encoding="utf-8")


def _read_step(path: Path) -> tuple[str, str]:
    text = path.read_text(encoding="utf-8")
    try:
        record = json.loads(text)
        if isinstance(record, dict) and "state" in record:
            return str(record.get("label") or "an edit"), str(record["state"])
    except ValueError:
        pass
    return "an edit", text  # an older history file: the whole draft


def _redo_dir(path: Path) -> Path:
    return Path(path).with_suffix(".redo")


def _journaled(fn: Any, kind: str) -> Any:
    """Wrap a mutating method: one undo step per outermost call, labelled with what it did."""
    import functools

    @functools.wraps(fn)
    def wrapper(self: Any, *args: Any, **kwargs: Any) -> Any:
        tl = self if kind == "checkout" else self._tl
        if getattr(tl, "_edit_depth", 0):
            return fn(self, *args, **kwargs)
        who = getattr(self, "address", None) or getattr(self, "segment", None) or ""
        shown = ", ".join([repr(a)[:40] for a in args] + [f"{k}={v!r}"[:40] for k, v in kwargs.items()])
        with tl.step(f"{who + '.' if who else ''}{fn.__name__}({shown})"):
            return fn(self, *args, **kwargs)

    return wrapper


@contextlib.contextmanager
def _file_lock(target: Path):
    """One writer at a time for the read-compare-write of a save (other processes wait, briefly)."""
    try:
        import fcntl
    except ImportError:  # not POSIX: the merge still guards, without the lock
        yield
        return
    lock = target.with_name(f".{target.name}.lock")
    try:
        lock.parent.mkdir(parents=True, exist_ok=True)
        handle = open(lock, "w")
    except OSError:
        yield
        return
    try:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(handle, fcntl.LOCK_UN)
        finally:
            handle.close()


def _is_draft(path: Path) -> bool:
    try:
        return drafts_root().resolve() in Path(path).resolve().parents
    except OSError:
        return False


def _history_dir(path: Path) -> Path:
    return Path(path).with_suffix(".history")


def draft_path(project_id: str, timeline_id: str, name: str = "main") -> Path:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "-", name) or "main"
    return drafts_root() / str(project_id) / str(timeline_id) / f"{safe}.json"


def resolve_ids(project: str, timeline: str, *, client: Any = None) -> tuple[str, str, str]:
    """(project_id, timeline_id, head) for a project slug/id and a timeline slug/id."""
    from astrid.sdk import AstridClient

    def read(c: Any) -> tuple[str, str, str]:
        shown = c.timelines.open_composition(project, timeline)
        if not shown.ok or not isinstance(shown.data, Mapping):
            raise TimelineEditError(f"could not open timeline {timeline!r} in project {project!r}: {shown.error}")
        native = shown.data.get("native_inspection") or {}
        return str(native.get("project_id")), str(native.get("timeline_id")), str(shown.data["summary"]["head_revision_id"])

    if client is not None:
        return read(client)
    with AstridClient.open_from_launcher(start_pack_host=False) as c:
        return read(c)


def find_draft(project: str, timeline: str, name: str | None = None, *, client: Any = None) -> Path | None:
    """The working copy for this timeline, if one exists: the named one, else the CURRENT one (the
    last checked out, ``checkout --draft NAME``), else "main"."""
    project_id, timeline_id, _head = resolve_ids(project, timeline, client=client)
    path = draft_path(project_id, timeline_id, name or current_draft(project_id, timeline_id))
    return path if path.is_file() else None


def current_draft(project_id: str, timeline_id: str) -> str:
    """The working copy commands use when none is named: the one last checked out (default "main")."""
    pointer = draft_path(project_id, timeline_id, "main").parent / ".current"
    try:
        name = pointer.read_text(encoding="utf-8").strip()
    except OSError:
        return "main"
    return name if name and draft_path(project_id, timeline_id, name).is_file() else "main"


def set_current_draft(project_id: str, timeline_id: str, name: str) -> None:
    pointer = draft_path(project_id, timeline_id, "main").parent / ".current"
    pointer.parent.mkdir(parents=True, exist_ok=True)
    pointer.write_text(name, encoding="utf-8")




# ------------------------------------------------------------------ runtime I/O

def fetch_bundle(project: str, timeline: str, *, revision_id: str | None = None, client: Any = None) -> dict[str, Any]:
    """The complete pinned closure (parent → shots → internal timelines) of a head or revision."""
    from astrid.sdk import AstridClient

    def read(c: Any) -> dict[str, Any]:
        opened = c.timelines.open_bundle(project, timeline, revision_id=revision_id)
        if not opened.ok or not isinstance(opened.data, Mapping):
            raise TimelineEditError(f"could not open {timeline!r} in {project!r}: {opened.error}")
        return copy.deepcopy(dict(opened.data["bundle"]))

    if client is not None:
        return read(client)
    with AstridClient.open_from_launcher(start_pack_host=False) as c:
        return read(c)


def _units(bundle: Mapping[str, Any], *, whole: bool = False) -> dict[tuple[str, ...], Any]:
    """The bundle as mergeable units: every internal clip, every placement row, every parent clip
    (``whole``: also each film setting in ``parent.config`` and each shot's payload, for working-copy saves)."""
    units: dict[tuple[str, ...], Any] = {}
    if whole:
        for key, value in ((bundle.get("parent") or {}).get("config") or {}).items():
            units[("parent-config", str(key))] = value
        for sid, shot in (bundle.get("shots") or {}).items():
            units[("payload", str(sid))] = shot.get("payload")
    for row in bundle.get("placements") or []:
        units[("placement", str(row.get("shot_id")))] = row
    for sid, shot in (bundle.get("shots") or {}).items():
        internal = shot.get("internal_timeline") or {}
        for clip in internal.get("clips") or []:
            units[("clip", str(sid), str(clip.get("id")))] = clip
        units[("tracks", str(sid))] = internal.get("tracks")
        units[("registry", str(sid))] = internal.get("registry")
    for clip in (bundle.get("parent") or {}).get("clips") or []:
        units[("parent-clip", str(clip.get("id")))] = clip
    return units


def three_way(base: Mapping[str, Any], head: Mapping[str, Any], candidate: Mapping[str, Any], *,
              force: bool = False, whole: bool = False) -> tuple[dict[str, Any], list[str]]:
    """Merge the candidate's changes (base → candidate) onto ``head``. Returns (merged, conflicts).

    With conflicts and no ``force`` the merge is not applied. With ``force`` your version wins
    on the conflicting units; every other unit of the head is kept."""
    b, h, c = _units(base, whole=whole), _units(head, whole=whole), _units(candidate, whole=whole)
    same = lambda x, y: json.dumps(x, sort_keys=True, default=str) == json.dumps(y, sort_keys=True, default=str)
    ours = {k for k in set(b) | set(c) if not same(b.get(k), c.get(k))}
    theirs = {k for k in set(b) | set(h) if not same(b.get(k), h.get(k))}
    conflicts = [k for k in sorted(ours & theirs) if not same(c.get(k), h.get(k))]
    lines = [f"{'/'.join(k[1:])} ({k[0]}) changed in the head since your checkout and in your edit" for k in conflicts]
    merged = copy.deepcopy(dict(head))
    if lines and not force:
        return merged, lines
    for key in sorted(ours):
        value = copy.deepcopy(c.get(key))
        kind = key[0]
        if kind == "placement":
            rows = merged.setdefault("placements", [])
            rows[:] = [r for r in rows if str(r.get("shot_id")) != key[1]] + ([value] if value is not None else [])
        elif kind in ("tracks", "registry"):
            shot = merged["shots"].setdefault(key[1], copy.deepcopy(candidate["shots"][key[1]]))
            shot.setdefault("internal_timeline", {})[kind] = value
        elif kind == "clip":
            sid, cid = key[1], key[2]
            if sid not in merged["shots"]:
                merged["shots"][sid] = copy.deepcopy(candidate["shots"][sid])
                continue
            clips = merged["shots"][sid].setdefault("internal_timeline", {}).setdefault("clips", [])
            index = next((i for i, x in enumerate(clips) if str(x.get("id")) == cid), None)
            if value is None and index is not None:
                clips.pop(index)
            elif value is not None and index is None:
                clips.append(value)
            elif value is not None:
                clips[index] = value
        elif kind == "parent-clip":
            clips = merged.setdefault("parent", {}).setdefault("clips", [])
            clips[:] = [x for x in clips if str(x.get("id")) != key[1]] + ([value] if value is not None else [])
        elif kind == "parent-config":
            config = merged.setdefault("parent", {}).setdefault("config", {})
            if value is None:
                config.pop(key[1], None)
            else:
                config[key[1]] = value
        elif kind == "payload" and key[1] in merged.get("shots", {}):
            merged["shots"][key[1]]["payload"] = value
    return merged, lines


def publish_bundle(candidate: Mapping[str, Any], idempotency_key: str, *, client: Any = None, force: bool = False) -> dict[str, Any]:
    """One parent compare-and-swap publication with an overwrite guard (see ``Checkout.publish``)."""
    from astrid.sdk import AstridClient
    from astrid.sdk.authoring_bundle import publish_authoring_candidate
    from astrid.sdk.autobootstrap import ensure_runtime
    from astrid.sdk.workspace_client import WorkspaceClient, resolve_runtime_connection

    def head(c: Any) -> str:
        shown = c.timelines.open_composition(candidate["project_id"], candidate["timeline_id"])
        if not shown.ok:
            raise TimelineEditError(f"could not read the current head: {shown.error}")
        return str(shown.data["summary"]["head_revision_id"])

    if client is not None:
        current = head(client)
    else:
        with AstridClient.open_from_launcher(start_pack_host=False) as c:
            current = head(c)
    base = str((candidate.get("base_parent") or {}).get("revision_id"))
    merged_note = None
    if current != base:
        from astrid.sdk.timeline_cuts import base_bundle

        head_bundle = fetch_bundle(str(candidate["project_id"]), str(candidate["timeline_id"]), client=client)
        merged, conflicts = three_way(base_bundle(candidate), head_bundle, candidate, force=force)
        if conflicts and not force:
            names = {c.id: c.address for c in Checkout(copy.deepcopy(dict(candidate))).clips()}

            def plain(line: str) -> str:
                m = re.match(r"^\S+/(\S+) \(clip\) (.*)$", line)
                return f"{names.get(m.group(1), m.group(1))}  {m.group(2)}" if m else line

            short = lambda rev: str(rev).removeprefix("authoring-parent-revision-")[:8]  # noqa: E731
            raise TimelineEditError(f"not published: someone published {short(current)} after your checkout ({short(base)}), "
                                    "and you both changed:\n  " + "\n  ".join(plain(c) for c in conflicts)
                                    + "\nnext: timelines discard, checkout and re-apply your edit (or publish --force to overwrite theirs)")
        candidate = merged
        merged_note = f"merged onto head {current} (your checkout was {base})" + (" with force" if conflicts else "")
        base = current
    receipt = ensure_runtime(start_pack_host=False)
    endpoint, token = resolve_runtime_connection(receipt["endpoint"], Path(receipt["credential_file"]))
    transport = WorkspaceClient(endpoint, token)

    class _Writer:
        def publish_parent_composition(self, project_id, timeline_id, publication, *, idempotency_key):
            response = transport.publish_parent_composition(project_id, timeline_id, publication, idempotency_key=idempotency_key)
            return response.get("data", response)

    result = publish_authoring_candidate(dict(candidate), _Writer(), idempotency_key=idempotency_key)
    publication = result.get("publication") if isinstance(result, Mapping) else None
    return {"old_head": base, "new_head": (publication or {}).get("new_head"), "receipt": result,
            "idempotency_key": idempotency_key, "merged": merged_note}



def import_media(path: Path, project: Any) -> dict[str, Any]:
    """Import a local file into the project's media store; return a registry entry."""
    import subprocess
    import sys

    out = subprocess.run([sys.executable, "-m", "astrid", "media", "import", str(path), "--project", str(project), "--json"],
                         capture_output=True, text=True)
    try:
        data = json.loads(out.stdout)["data"]
    except Exception as exc:
        raise TimelineEditError(f"media import of {path} failed: {(out.stderr or out.stdout)[-300:]}") from exc
    entry = {"media_id": data["object_id"], "content_sha256": data.get("digest") or data["object_id"],
             "type": data.get("media_type") or "application/octet-stream"}
    if data.get("resolution"):
        entry["resolution"] = data["resolution"]
    return entry


# every edit is one undo step (the outermost call; what it calls inside is part of it)
for _name in ("on", "until", "enter_at", "nudge", "hold_for", "set_duration", "extend", "end_at", "set", "clear_asset",
              "swap_asset", "set_beats", "keyframe_at", "remove", "remove_layer", "keep"):
    setattr(Clip, _name, _journaled(getattr(Clip, _name), "clip"))
for _name in ("add", "ripple_delete", "close_gap", "insert_time", "insert_line", "remove_line", "apply_script",
              "fill_slot", "fill_standins", "declare_gaps", "adopt"):
    setattr(Checkout, _name, _journaled(getattr(Checkout, _name), "checkout"))
for _name in ("replace", "set_gap_after"):
    setattr(Voice, _name, _journaled(getattr(Voice, _name), "voice"))

