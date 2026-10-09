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

import copy
import difflib
import json
import math
import os
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

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
        return self.data.setdefault("params", {})

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
    def anchor(self) -> dict[str, Any] | None:
        return intent.anchor(self.data)

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
        anchor = f" ⚓{self.anchor['text']}" if self.anchor else ""
        return f"<Clip {self.id} {self.element} {what} {self.start:.3f}–{self.end:.3f} s{anchor}>"

    # edits ---------------------------------------------------------------
    def enter_at(self, when: Any, *, offset: float = 0.0, anchor: bool = True) -> "Clip":
        """Start this clip at a word or a time, keeping its length. A word becomes its anchor."""
        word = self._tl._as_word(when)
        target = self._tl.quantize((word.start if word else self._tl.time(when)) + offset)
        self._set_start(target)
        if word is not None and anchor:
            intent.set_anchor(self.data, {"word": word.id, "text": word.text, "offset_s": _r(offset), "edge": "start"})
        else:
            intent.set_anchor(self.data, None)
        return self

    def nudge(self, seconds: float = 0.0, *, frames: int = 0) -> "Clip":
        """Move by seconds and/or frames (keeps an anchor; its offset absorbs the move)."""
        delta = float(seconds) + frames / self._tl.fps
        self._set_start(self._tl.quantize(self.start + delta))
        anchor = self.anchor
        if anchor:
            anchor["offset_s"] = _r(_num(anchor.get("offset_s")) + delta)
            intent.set_anchor(self.data, anchor)
        return self

    def set_duration(self, seconds: float) -> "Clip":
        if seconds <= 0:
            raise TimelineEditError(f"{self.id}: a duration must be positive (got {seconds})")
        _set_length(self.data, self._tl.quantize(seconds))
        return self

    def extend(self, seconds: float = 0.0, *, frames: int = 0) -> "Clip":
        """Lengthen (or, negative, shorten) the clip at its end. Nothing else moves."""
        return self.set_duration(self.duration + float(seconds) + frames / self._tl.fps)

    def end_at(self, when: Any, *, offset: float = 0.0) -> "Clip":
        return self.set_duration(self._tl.quantize(self._tl.time(when) + offset) - self.start)

    def set(self, **params: Any) -> "Clip":
        """Update element params (``x``, ``size``, ``text`` …)."""
        self.params.update(copy.deepcopy(params))
        return self

    def swap_asset(self, asset: Any) -> "Clip":
        """Point the clip at another asset: a registry key, a ``{media_id, …}`` entry, or a local file (imported)."""
        key = self._tl._register_asset(self.shot_id, asset)
        self.data["asset"] = key
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

    def replace(self, media: Any, *, words: Any, ripple: bool = True) -> list[tuple[str, float, float]]:
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


# ------------------------------------------------------------------ checkout

class Checkout:
    """A complete, editable copy of one timeline head (parent → shots → internal timelines)."""

    def __init__(self, bundle: dict[str, Any], *, path: Path | None = None):
        self.bundle = bundle
        self.path = path
        self.fps = bundle_fps(bundle) or 30.0
        self.notes: list[str] = []
        self.report: list[str] = []  # what the last re-flow did, in plain words

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
        raw = json.loads(path.read_text(encoding="utf-8"))
        tl = cls({}, path=path)
        tl.bundle = tl._absorb(raw)
        tl.fps = bundle_fps(tl.bundle) or 30.0
        return tl

    def save(self, path: str | Path | None = None) -> Path:
        """Write the checkout: content pretty-printed, provenance compact, both clocks on every clip."""
        target = Path(path or self.path or "timeline.checkout.json")
        target.write_text(_serialize(self._annotated()), encoding="utf-8")
        self.path = target
        return target

    # ---- working copy (draft) --------------------------------------------------
    @classmethod
    def draft(cls, project: str, timeline: str, name: str = "main", *, client: Any = None, fresh: bool = False) -> "Checkout":
        """The timeline's working copy: the existing draft, or a new one checked out from the head.

        Drafts live in Astrid's data root (never a file you name). ``show``, ``lint``, ``diff``,
        ``visualize`` and ``render --draft`` read the same working copy; ``save()`` writes it back.
        """
        project_id, timeline_id, _head = resolve_ids(project, timeline, client=client)
        path = draft_path(project_id, timeline_id, name)
        if path.is_file() and not fresh:
            tl = cls.load(path)
        else:
            tl = cls(fetch_bundle(project, timeline, client=client))
            path.parent.mkdir(parents=True, exist_ok=True)
            tl.save(path)
        tl.draft_name = name
        return tl

    def discard(self) -> None:
        """Delete this working copy (nothing published is touched)."""
        if self.path and Path(self.path).is_file():
            Path(self.path).unlink()

    @property
    def base_revision(self) -> str:
        """The published revision this working copy started from."""
        return str((self.bundle.get("base_parent") or {}).get("revision_id") or "")

    def edits(self) -> Mapping[str, Any]:
        """What this working copy changes against its base (``diff_bundles`` shape, timeline seconds)."""
        from astrid.sdk.timeline_cuts import base_bundle, diff_bundles

        document = self.document()
        return diff_bundles(base_bundle(document), document)

    # ---- clocks -----------------------------------------------------------
    def quantize(self, seconds: float) -> float:
        return _r(round(float(seconds) * self.fps) / self.fps)

    def time(self, when: Any) -> float:
        """Timeline seconds from a number, ``"1:02"``/``"62.5s"``/``"@62.5"``, a Word, a Clip or a word text/id."""
        if isinstance(when, bool):
            raise TimelineEditError("a time cannot be a boolean")
        if isinstance(when, (int, float)):
            return float(when)
        if isinstance(when, Word):
            return when.start
        if isinstance(when, Clip):
            return when.start
        text = str(when).strip()
        match = TIME_RE.match(text)
        if match:
            minutes = int(match.group(1) or 0)
            return minutes * 60 + float(match.group(2))
        return self.word(text).start

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
        return (c.id == text or c.id.startswith(text) or (c.asset or "").lower() == text.lower()
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
            track: str | None = None, corner: str | None = None, anchor: bool = True, clip_id: str | None = None) -> Clip:
        """Add an overlay at a word or time. ``corner`` (top-right …) places it inside title-safe, off a centred face."""
        word = self._as_word(at)
        start = self.quantize(word.start if word else self.time(at))
        sid = self._shot_at(start)
        internal = self._internal(sid)
        new = {"id": clip_id or self._new_id(element), "clipType": element, "track": track or ELEMENT_TRACK.get(element, "fx"),
               "at": 0.0, "hold": self.quantize(hold), "params": copy.deepcopy(dict(params or {}))}
        if not any(str(t.get("id")) == new["track"] for t in internal.get("tracks") or []):
            raise TimelineEditError(f"shot {sid} has no track {new['track']!r}; tracks: {', '.join(str(t.get('id')) for t in internal.get('tracks') or [])}")
        if asset is not None:
            new["asset"] = self._register_asset(sid, asset)
        if corner:
            new["params"].update(self._corner(element, new["params"], new.get("asset"), sid, corner))
        internal["clips"].append(new)
        clip = Clip(self, sid, new)
        clip.enter_at(word if word and anchor else start)
        return clip

    def ripple_delete(self, start: Any, end: Any, *, skip: Sequence[Mapping[str, Any]] = ()) -> float:
        """Remove the window [start, end) from the whole timeline and close it up.

        Clips after it move up by its length; picture clips that span it get shorter;
        audio that spans it (music, a VO tail) is split, so everything after stays in
        sync with its picture. Clips wholly inside the window are removed.
        Returns the seconds removed.
        """
        a, b = self.quantize(self.time(start)), self.quantize(self.time(end))
        if b <= a:
            raise TimelineEditError("the window to remove must have start < end")
        return self._shift_after(a, -(b - a), cut_window=(a, b), skip=skip)

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

    def _piece_gaps(self) -> dict[str, float]:
        pieces = self._pieces()
        return {a.id: _r(wb[0].start - wa[-1].end) for (a, wa), (_b, wb) in zip(pieces, pieces[1:])}

    def gaps(self) -> dict[str, float]:
        """The silence after each line as it is now (its speech end to the next line's first word)."""
        lines = self.lines()
        return {line.segment: _r(nxt.words[0].start - line.speech_end) for line, nxt in zip(lines, lines[1:]) if nxt.words}

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
            end, have = words[-1].end, nwords[0].start
            delta = self.quantize(end + gap - have)
            if abs(delta) < 0.5 / self.fps:
                continue
            point = points.get(clip.id, end)
            if delta > 0:
                self.insert_time(self.quantize(max(point, end - delta)), delta, skip=[clip.data])
            else:
                start = max(end, min(point, _nclip.start + delta))
                self.ripple_delete(self.quantize(start), self.quantize(start) - delta, skip=[clip.data])
            report.append(f"{nwords[0].segment} ({nwords[0].text!r}) {have:.3f} → {have + delta:.3f} s ({delta:+.3f} s, everything after it moved)")
        report += [f"{cid}: {a:.3f} → {b:.3f} s (anchored)" for cid, a, b in self.retime()]
        report += self.notes[notes_before:]
        del self.notes[notes_before:]
        self.report = report
        return report

    def insert_line(self, segment: str, media: Any, *, words: Any, after: str, gap_after: float | None = None) -> list[str]:
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
        first = self.quantize(prev.speech_end + gap_prev)  # the new line's first word
        # open the room in the silence after the previous line: what was keyed to the next line moves with it
        self.insert_time(prev.speech_end, new_words[-1][1] - new_words[0][0] + gap_new, skip=[c.data for c in prev.clips])
        sid = self._shot_at(first)
        start = first - new_words[0][0]
        new = {"id": f"vo-{segment}-0", "clipType": "media", "track": last.track,
               "at": _r(start - self._shot_start(sid)), "from": 0.0, "to": _r(new_words[-1][1] + 0.06)}
        for key in ("volume", "gain_db"):
            if key in last.data:
                new[key] = copy.deepcopy(last.data[key])
        intent.set_line(new, segment, [[_r(a), _r(b), t] for a, b, t in new_words])
        if gap_after is not None:
            intent.set_gap_after(new, _r(gap_after))
        new["asset"] = self._register_asset(sid, media)
        self._internal(sid)["clips"].append(new)
        before[new["id"]] = gap_new
        return [f"inserted line {segment} at {first:.3f} s ({new_words[-1][1] - new_words[0][0]:.3f} s of speech)"] + self.reflow(gaps=before)

    def remove_line(self, segment: str) -> list[str]:
        """Remove a VO line, what is on screen only for it, and its silence; everything after moves up."""
        line = Voice(self, segment)
        lines = self.lines()
        index = next(i for i, v in enumerate(lines) if v.segment == segment)
        before = self._piece_gaps()
        start = line.clips[0].start
        if index + 1 < len(lines):
            end = lines[index + 1].clips[0].start
        else:
            end = line.speech_end + (line.gap_after or 0.0)
        for clip in line.clips:
            clip.remove()
        removed = self.ripple_delete(start, end)
        return [f"removed line {segment} ({removed:.3f} s)"] + self.reflow(gaps=before)

    def retime(self) -> list[tuple[str, float, float]]:
        """Move every anchored clip back onto its word (after a VO change). Returns what moved.

        An anchored cut (a picture clip) rolls: the picture before it ends where it now starts,
        and it keeps its own end, so the picture track never gets a hole or an overlap."""
        pictures = {id(step.data) for cut in self.cuts for step in cut.steps}
        words = self.words()
        by_id = {w.id: w for w in words}
        moved = []
        for clip in self.clips():
            anchor = clip.anchor
            if not anchor:
                continue
            word = by_id.get(str(anchor.get("word")))
            if word is None or _norm(word.text) != _norm(anchor.get("text")):
                segment = str(anchor.get("word", "")).split(":")[0]
                same = [w for w in words if _norm(w.text) == _norm(anchor.get("text"))]
                pool = [w for w in same if w.segment == segment] or same
                if not pool:
                    self.notes.append(f"{clip.id}: its word {anchor.get('text')!r} is no longer spoken; left in place")
                    continue
                word = min(pool, key=lambda w: abs(w.start - (clip.start - _num(anchor.get("offset_s")))))
            target = self.quantize((word.end if anchor.get("edge") == "end" else word.start) + _num(anchor.get("offset_s")))
            if abs(target - clip.start) > 1e-6:
                old = clip.start
                if id(clip.data) in pictures:
                    self._roll(clip, target)  # a cut is a boundary: the picture before it ends where this one starts
                else:
                    clip._set_start(target)
                moved.append((clip.id, old, clip.start))
            anchor["word"], anchor["text"] = word.id, word.text
            intent.set_anchor(clip.data, anchor)
        return moved

    def _roll(self, clip: Clip, target: float) -> None:
        """Move a picture clip's start to ``target`` and keep its end; the abutting picture before it follows."""
        old_start, old_end = clip.start, clip.end
        if target >= old_end - 0.5 / self.fps:
            self.notes.append(f"{clip.id}: its anchor is after its own end; left in place")
            return
        before = [c for c in self.clips(shot=clip.shot_id) if c.track == clip.track and c.id != clip.id
                  and abs(c.end - old_start) < 1e-3]
        if before and target <= before[0].start + 0.5 / self.fps:
            self.notes.append(f"{clip.id}: its anchor is before the previous picture starts; left in place")
            return
        clip._set_start(target)
        _set_length(clip.data, self.quantize(old_end - target))
        for prev in before:
            _set_length(prev.data, self.quantize(target - prev.start))
        if not before and abs(old_start - self._shot_start(clip.shot_id)) < 1e-3:
            self.notes.append(f"{clip.id}: rolled its start but it opens a shot; the previous shot is unchanged")

    def resolve(self) -> list[str]:
        """Re-resolve every formula in the document (like a spreadsheet recalculating).

        An anchor puts a clip's start on a word (see ``retime``). A clip's formulas map a
        field path to an expression, and the resolved value is written to that field:

        - ``{"word": "n20b:20", "text": "viral", "offset_s": 0, "as": "clip_seconds"|"clip_frame"|"timeline_seconds"}``
          (``edge: "end"`` for the word's end; then, in order: ``offset_frames``, ``min``/``max``, ``step``
          (down to a multiple, e.g. 2 for stepFrames 2); ``snap: "floor"`` floors seconds to a frame)
        - ``{"words_of": "n20b"}``: ``[[start, end], …]`` of that line's words, clip-relative (presenter lip-sync)
        - ``{"mark": "B2-HAND", "axis": "x"|"y", "offset": -16, "unit": "logical"|"px"}``: a slot's hand mark

        Paths look like ``params.words``, ``params.punchAt[0]``, ``params.keyframes[2].frame``. Returns what changed.
        """
        changes = [f"{cid}: start {old:.3f} → {new:.3f} s (anchored to its word)" for cid, old, new in self.retime()]
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
                except TimelineEditError as exc:
                    self.notes.append(f"{clip.id}.{path}: {exc}")
                    continue
                if _set_path(clip.data, path, value):
                    changes.append(f"{clip.id}.{path} = {json.dumps(value)[:60]}")
        return changes

    def _evaluate(self, clip: Clip, expr: Any, by_id: Mapping[str, Word], words: Sequence[Word], slots: Mapping[str, Any]) -> Any:
        if not isinstance(expr, Mapping):
            raise TimelineEditError(f"a formula must be an object, got {expr!r}")
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
            value = _num(mark[axis]) + _num(expr.get("offset"))
            return round(_num(mark[axis]) / 6 + _num(expr.get("offset"))) if expr.get("unit", "logical") == "logical" else round(value)
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
        lint = self.lint(cuts=cut_numbers) if cut_numbers else []
        return CheckReport(valid=valid, validation=validation, summary=summary, lint=lint,
                           problems=problems + self.notes, changed_cuts=cut_numbers, diff=diff)

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
        receipt = publish_bundle(self.document(), key, client=client, force=force)
        receipt["message"] = message
        return receipt

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
            [c for c in pool if c.id == text],
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
                listing = "\n  ".join(repr(c) for c in unique[:12])
                more = f"\n  … {len(unique) - 12} more" if len(unique) > 12 else ""
                raise TimelineEditError(f"{text!r} matches {len(unique)} clips in {where}; pass an id (or cut=N):\n  {listing}{more}")
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

    def _register_asset(self, shot_id: str, asset: Any) -> str:
        """Make ``asset`` resolvable in this shot; return its registry key."""
        assets = self._registry(shot_id)
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
                key = re.sub(r"[^A-Za-z0-9_-]+", "-", path.stem).strip("-") or "asset"
                assets[key] = entry
                return key
            known = sorted({k for sid in self._shot_ids() for k in self._registry(sid)})
            close = difflib.get_close_matches(asset, known, n=5)
            raise TimelineEditError(f"no asset {asset!r} in this timeline" + (f"; did you mean {', '.join(close)}?" if close else "")
                                    + " (or pass a local file to import, or a {media_id, content_sha256, type} entry)")
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
            elif cs >= ra - 1e-6 and ce <= rb + 1e-6:
                continue  # wholly inside the removed window
            elif cs < ra and ce > rb:
                if audio and self._is_music(sid, clip):
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

    def __str__(self) -> str:
        lines = [("valid" if self.valid else "INVALID") + " · " + (self.summary[0] if self.summary else "no changes")]
        lines += self.summary[1:]
        lines += [f"! {p}" for p in self.problems]
        if self.changed_cuts:
            lines.append(f"lint on changed cuts {', '.join(map(str, self.changed_cuts))}: " + ("clean" if not self.lint else f"{len(self.lint)} line(s)"))
            lines += [f"  {line}" for line in self.lint]
        return "\n".join(lines)


# ----------------------------------------------------------------- document helpers

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


def find_draft(project: str, timeline: str, name: str = "main", *, client: Any = None) -> Path | None:
    """The working copy for this timeline, if one exists."""
    project_id, timeline_id, _head = resolve_ids(project, timeline, client=client)
    path = draft_path(project_id, timeline_id, name)
    return path if path.is_file() else None




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


def _units(bundle: Mapping[str, Any]) -> dict[tuple[str, ...], Any]:
    """The bundle as mergeable units: every internal clip, every placement row, every parent clip."""
    units: dict[tuple[str, ...], Any] = {}
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
              force: bool = False) -> tuple[dict[str, Any], list[str]]:
    """Merge the candidate's changes (base → candidate) onto ``head``. Returns (merged, conflicts).

    With conflicts and no ``force`` the merge is not applied. With ``force`` your version wins
    on the conflicting units; every other unit of the head is kept."""
    b, h, c = _units(base), _units(head), _units(candidate)
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
            raise TimelineEditError("the timeline moved since this checkout and both sides changed the same clips "
                                    f"(head {current}, your base {base}):\n  " + "\n  ".join(conflicts)
                                    + "\nCheck out again and re-apply (a script re-applies in one step), or publish with force to overwrite them.")
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
