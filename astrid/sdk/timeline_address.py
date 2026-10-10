"""One address, everywhere: every way of pointing at something in a timeline.

``show``, ``edit``, ``visualize``, ``lint``, ``apply`` and the Python API all resolve what
you type through ``resolve``, and every output prints the canonical address back:

    c41.mink               a layer (cut id . layer name); a carried layer also answers to the
                           later cut it is still on screen in (c30.cover → c29.cover)
    c30                    a cut (its whole span)          c30..c31   cuts, inclusive
    "Building" in v27 #2   a spoken word (scope a line, pick the nth); after "x" = its end
    93.5  1:33.5  @93.5    a time (timeline seconds)
    MYSTERY  cover         an asset key, a layer name, a clip id (bare names)
    "Astrid."              on-screen text, when no spoken word matches

Ambiguity is never guessed: the error lists every choice in its canonical form.
"""
from __future__ import annotations

import dataclasses
import re
from dataclasses import dataclass, field
from typing import Any

from astrid.core.timeline import moments as mo
from astrid.sdk import timeline_intent as intent

TIME_RE = re.compile(r"^@?(?:(\d+):)?(\d+(?:\.\d+)?)s?$")
CLIP_ADDR_RE = re.compile(r"^(c\d+[a-z]?)\.(\S+)$")
MOMENT_HINT = re.compile(r'^(after |beat |downbeat |on |end\b)|^c\d+[a-z]?\.end\b|"|“| in [A-Za-z]|#\d|[+-]\d+(\.\d+)?[fs]\b')


class AddressError(ValueError):
    """An address that matches nothing, or more than one thing; the message lists the choices."""


@dataclass
class Target:
    kind: str                       # clip | word | cut | time | range
    address: str                    # canonical, printable, pasteable
    start: float
    end: float
    clip: Any = None
    word: Any = None
    cut: str | None = None
    note: str | None = None         # e.g. how an alias was read
    param: str | None = None        # kind param: c41.tool-16.x

    def __str__(self) -> str:
        span = f"{self.start:.2f} s" if abs(self.end - self.start) < 1e-6 else f"{self.start:.2f}–{self.end:.2f} s"
        return f"{self.address} ({span})" + (f" · {self.note}" if self.note else "")


# ---------------------------------------------------------------- canonical forms

def word_address(tl: Any, word: Any, *, edge: str = "start") -> str:
    return mo.format_moment(mo.word_moment(word, tl.words(), edge=edge))


def clip_target(tl: Any, clip: Any, note: str | None = None) -> Target:
    return Target("clip", clip.address, clip.start, clip.end, clip=clip, cut=intent.cut_of(clip.data), note=note)


def cut_target(tl: Any, cut_id: str) -> Target:
    spans = tl._cut_spans()
    lo, hi = spans[cut_id]
    return Target("cut", cut_id, lo, hi if hi is not None else tl.duration, cut=cut_id)


# ---------------------------------------------------------------- resolve

def resolve(tl: Any, text: Any, *, prefer: str = "thing") -> Target:
    """One target, or ``AddressError`` (no match, or several: the message lists them).

    ``prefer="time"`` (visualize/show --at, words --at) tries spoken words before names;
    ``prefer="thing"`` (show/edit) tries layers, assets and clip ids first."""
    found = candidates(tl, text, prefer=prefer)
    if len(found) == 1:
        return found[0]
    raw = str(text).strip()
    if not found:
        raise AddressError(_nothing(tl, raw))
    listing = "\n  ".join(str(t) for t in found[:12]) + (f"\n  … {len(found) - 12} more" if len(found) > 12 else "")
    raise AddressError(f"{raw!r} matches {len(found)} things; name one:\n  {listing}")


def candidates(tl: Any, text: Any, *, prefer: str = "thing") -> list[Target]:
    """Every target the text could mean, in the first tier that matches (an empty list: nothing)."""
    raw = str(text).strip()
    if not raw:
        return []
    # a range: A..B (outside quotes)
    parts = re.split(r'\.\.(?=(?:[^"]*"[^"]*")*[^"]*$)', raw, maxsplit=1)
    if len(parts) == 2:
        lo = resolve(tl, parts[0], prefer="time") if parts[0].strip() else None
        hi = resolve(tl, parts[1], prefer="time") if parts[1].strip() else None
        start = lo.start if lo else 0.0
        end = (hi.end if hi and hi.kind in ("cut", "clip") else hi.start if hi else tl.duration)
        return [Target("range", f"{lo.address if lo else ''}..{hi.address if hi else ''}", start, end)]
    if TIME_RE.match(raw):
        t = tl.time(raw)
        return [Target("time", f"{t:.2f}", t, t)]
    lowered = raw.lower()
    if mo.CUT_ID_RE.match(lowered):
        lowered = tl._cut_id(lowered) or lowered  # c1 is c01
        if lowered in tl._cut_spans():
            return [cut_target(tl, lowered)]
        orphans = tl._cut_clips(lowered)
        if orphans:  # an orphaned cut (its line was removed): still addressable, to re-home or remove
            lo, hi = min(c.start for c in orphans), max(c.end for c in orphans)
            return [Target("cut", lowered, lo, hi, cut=lowered, note="orphaned: re-home it with --cut %s --on MOMENT" % lowered)]
        raise AddressError(tl._no_cut(lowered))
    if MOMENT_HINT.match(lowered) and (lowered.startswith("end") or re.match(r"^c\d+[a-z]?\.end\b", lowered)):
        return _moment(tl, raw)  # end, end of c30, c30.end (+offsets)
    by_id = [c for c in tl.clips() if c.id == raw]
    if by_id:  # a clip id (c22-02-am-sprite): the clip, printed back by its address
        return [clip_target(tl, c, note=f"clip id {raw}" if c.address != raw else None) for c in by_id]
    m = CLIP_ADDR_RE.match(raw)
    if m:
        found = _clip_address(tl, m.group(1).lower(), m.group(2))
        if not found and "." in m.group(2):  # params are part of the address: c41.tool-16.x
            layer, _, param = m.group(2).rpartition(".")
            clips = _clip_address(tl, m.group(1).lower(), layer)
            return [Target("param", f"{c.address}.{param}", c.start, c.end, clip=c.clip, cut=c.cut, note=c.note, param=param)
                    for c in clips]
        return found
    if MOMENT_HINT.search(raw):
        words = _moment(tl, raw)
        if words:
            return words
        if raw[0] in "\"“":  # a quoted phrase nobody says: on-screen text?
            return _text(tl, raw.strip('"“”'))
        return []
    if prefer == "thing" and _registry_entry(tl, raw) is not None:
        users = [c for c in tl.clips() if c.asset == raw]
        return [Target("asset", raw, min((c.start for c in users), default=0.0), max((c.end for c in users), default=0.0),
                       note=f"used by {len(users)} clip(s)")]
    names = _names(tl, raw)
    spoken = _moment(tl, f'"{raw}"')
    text_hits = _text(tl, raw)
    tiers = [spoken, names, text_hits] if prefer == "time" else [names, spoken, text_hits]
    return next((tier for tier in tiers if tier), [])


def _registry_entry(tl: Any, key: str) -> dict[str, Any] | None:
    for sid in tl._shot_ids():
        entry = tl._registry(sid).get(key)
        if isinstance(entry, dict):
            return entry
    return None


def _drawn_size(clip: Any, entry: dict[str, Any]) -> str:
    """How big an asset is drawn by this clip, in canvas px (am-sprite draws each art px scale×scale)."""
    res = str(entry.get("resolution") or "")
    m = re.fullmatch(r"(\d+)x(\d+)", res)
    if not m:
        return ""
    w, h = int(m.group(1)), int(m.group(2))
    if clip.element == "am-sprite":
        scale = int((clip.data.get("params") or {}).get("scale") or element_schema(clip.element).get("defaults", {}).get("scale") or 6)
        return f"drawn {w * scale}×{h * scale} canvas px (scale {scale}: one art px = {scale}×{scale} canvas px)"
    return ""


def _clip_address(tl: Any, cut_id: str, layer: str) -> list[Target]:
    hits = [c for c in tl.clips() if intent.cut_of(c.data) == cut_id and intent.layer_of(c.data) == layer]
    if hits:
        return [clip_target(tl, c) for c in hits]
    spans = tl._cut_spans()
    if cut_id not in spans:
        return []
    lo, hi = spans[cut_id]
    hi = hi if hi is not None else tl.duration
    carried = [c for c in tl.clips() if intent.layer_of(c.data) == layer and c.start < lo - 1e-6 and c.end > lo + 0.5 / tl.fps]
    return [clip_target(tl, c, note=f"{cut_id}.{layer} is {c.address}, carried over {cut_id}") for c in carried]


def _names(tl: Any, raw: str) -> list[Target]:
    clips = tl.clips()
    tiers = [
        [c for c in clips if c.id == raw or c.address == raw],
        [c for c in clips if intent.layer_of(c.data) == raw],
        [c for c in clips if (c.asset or "") == raw or (c.asset or "").lower() == raw.lower() and raw.isupper()],
        [c for c in clips if c.id.startswith(raw) and len(raw) >= 4],
    ]
    for tier in tiers:
        unique = list({id(c.data): c for c in tier}.values())
        if unique:
            return [clip_target(tl, c) for c in sorted(unique, key=lambda c: c.start)]
    return []


def _moment(tl: Any, raw: str) -> list[Target]:
    try:
        moment = mo.parse(raw)
    except mo.MomentError:
        return []
    if moment.kind != "word":
        if moment.cut:
            moment = dataclasses.replace(moment, cut=tl._cut_id(moment.cut) or moment.cut)
        try:
            t = mo.floor_frame(mo.resolve(moment, _ctx(tl)), tl.fps)
        except mo.MomentError as exc:
            if moment.kind in ("cut", "cut_end"):
                raise AddressError(str(exc)) from None
            return []
        return [Target("time", mo.format_moment(moment), t, t)]
    hits = mo.find_words(moment, tl.words())
    if moment.n is not None:
        hits = hits[moment.n - 1:moment.n] if 1 <= moment.n <= len(hits) else []
    out = []
    for run in hits:
        word = run[-1] if moment.edge == "end" else run[0]
        t = (word.end if moment.edge == "end" else word.start) + moment.offset_s + moment.offset_frames / tl.fps
        t = mo.floor_frame(t, tl.fps)  # one quantisation everywhere: the frame a clip on this moment starts
        address = _phrase_address(tl, moment, run)
        out.append(Target("word", address, t, t, word=word))
    return out


def _phrase_address(tl: Any, moment: Any, run: list[Any]) -> str:
    if len(run) == 1:
        canonical = mo.word_moment(run[0], tl.words(), edge=moment.edge)
    else:  # a phrase: scope it like a single word would be
        all_runs = mo.find_words(mo.Moment("word", text=moment.text), tl.words())
        same_line = [r for r in all_runs if r[0].segment == run[0].segment]
        n = None if len(same_line) == 1 else 1 + [r[0].index for r in same_line].index(run[0].index)
        line = run[0].segment if len(all_runs) > 1 else None
        canonical = mo.Moment("word", text=moment.text, line=line, n=n, edge=moment.edge)
    canonical = canonical.with_offset(seconds=moment.offset_s, frames=moment.offset_frames)
    return mo.format_moment(canonical)


def _text(tl: Any, raw: str) -> list[Target]:
    needle = re.sub(r"\s+", " ", raw).strip().lower()
    if not needle:
        return []
    hits = [c for c in tl.clips() if c.text and needle in re.sub(r"\s+", " ", c.text).lower()]
    return [clip_target(tl, c, note=f'on screen: "{c.text[:40]}"') for c in hits]


def _ctx(tl: Any) -> Any:
    from astrid.sdk.timeline_checkout import _MomentContext

    return _MomentContext(tl, None)


def _nothing(tl: Any, raw: str) -> str:
    import difflib

    if MOMENT_HINT.search(raw):  # a moment that names a word: say what is wrong with it, in its terms
        try:
            moment = mo.parse(raw)
        except mo.MomentError as exc:
            return str(exc)
        base = moment.base if moment.kind in ("beat", "downbeat") else moment
        if base is not None and base.kind == "word":
            lines = list(dict.fromkeys(w.segment for w in tl.words()))
            if base.line and base.line not in lines:
                close = difflib.get_close_matches(base.line, lines, n=4)
                return (f"there is no line {base.line}" + (f"; did you mean {', '.join(close)}?" if close else "")
                        + f" (lines: timelines lines TL; {len(lines)} lines, {lines[0]}…{lines[-1]})" if lines else "")
            return mo._missing(base, tl.words())

    layers = sorted({intent.layer_of(c.data) for c in tl.clips() if intent.layer_of(c.data)})
    words = sorted({re.sub(r"[^\w']", "", w.text.lower()) for w in tl.words()})
    close = difflib.get_close_matches(raw.lower().strip('"'), layers + words + list(tl._cut_spans()), n=5)
    return (f"nothing in this timeline is called {raw!r}" + (f"; did you mean {', '.join(close)}?" if close else "")
            + " (a layer c41.mink, a cut c30, a word \"Building\" in v27, a time 93.5, an asset key, or on-screen text)")


# ---------------------------------------------------------------- the complete record

_SCHEMAS: dict[str, Any] = {}


def element_schema(element: str) -> dict[str, Any]:
    """The element's declared params (``element.yaml`` schema properties), or {} if unknown."""
    if element in _SCHEMAS:
        return _SCHEMAS[element]
    from pathlib import Path

    import yaml

    root = Path(__file__).resolve().parents[1] / "packs"
    found: dict[str, Any] = {}
    for path in root.glob(f"*/elements/**/{element}/element.yaml"):
        try:
            doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except Exception:  # noqa: BLE001 - an unreadable manifest just means "unknown"
            continue
        schema = doc.get("schema") or {}
        found = {"properties": dict(schema.get("properties") or {}), "defaults": dict(doc.get("defaults") or {}),
                 "additional": bool(schema.get("additionalProperties", True)), "path": str(path),
                 "units": dict((doc.get("metadata") or {}).get("units") or {})}
        break
    _SCHEMAS[element] = found
    return found


def formula_text(expr: Any) -> str:
    """A formula in words: ``"Building" in v27 (clip frame, ≥4, step 2)`` · ``mark B2-HAND x -20``."""
    if not isinstance(expr, dict):
        return str(expr)
    if "mark" in expr:
        from astrid.sdk.timeline_checkout import mark_offset_canvas

        return f"mark {expr['mark']} {expr.get('axis', 'x')} {round(mark_offset_canvas(expr), 2):+g} canvas px"
    if "words_of" in expr:
        return f"words of line {expr['words_of']}"
    what = expr.get("moment") or (f'"{expr.get("text")}"' if expr.get("text") else str(expr.get("word")))
    bits = [str(expr.get("as", "clip_seconds")).replace("_", " ")]
    if expr.get("offset_frames"):
        bits.append(f"{int(expr['offset_frames']):+d}f")
    if expr.get("min") is not None:
        bits.append(f"≥{expr['min']}")
    if expr.get("max") is not None:
        bits.append(f"≤{expr['max']}")
    if expr.get("step"):
        bits.append(f"step {expr['step']}")
    if expr.get("snap"):
        bits.append(f"snap {expr['snap']}")
    return f"{what} ({', '.join(bits)})"


def _type_of(spec: dict[str, Any]) -> str:
    kind = spec.get("type") or ("enum" if spec.get("enum") else "any")
    if spec.get("enum"):
        return f"{kind} {'|'.join(str(v) for v in spec['enum'])}"
    if "minimum" in spec or "maximum" in spec:
        return f"{kind} {spec.get('minimum', '')}–{spec.get('maximum', '')}"
    return str(kind)


def unknown_params(element: str, keys: list[str], existing: dict[str, Any] | None = None) -> list[str]:
    """Keys the element does not declare (and the clip does not already have)."""
    schema = element_schema(element)
    props = schema.get("properties") or {}
    if not props:
        return []
    return [k for k in keys if k not in props and k not in (existing or {})]


def _resolved(tl: Any, clip: Any, path: str, expr: Any) -> str:
    """`` → frame 12 (93.40 s)``: what a computed param is now, and when that is on the timeline."""
    from astrid.sdk.timeline_checkout import _get_path

    value = _get_path(clip.data, path)
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not isinstance(expr, dict):
        return ""
    unit = expr.get("as") or ("clip_seconds" if expr.get("moment") or expr.get("word") else None)
    if unit == "clip_frame":
        return f" → frame {value:g} ({clip.start + value / tl.fps:.2f} s)"
    if unit == "clip_seconds":
        return f" → {value:g} s into the clip ({clip.start + value:.2f} s)"
    if unit == "timeline_seconds":
        return f" → {value:.2f} s"
    return f" → {value:g}"


def describe_target(tl: Any, target: Target, *, timeline: str = "TL", project: str = "P") -> str:
    """The complete record of a target, as plain text: nothing truncated, every time resolved."""
    import json

    out: list[str] = []
    where = f"{timeline} --project {project}"
    if target.kind == "clip":
        clip = target.clip
        cut = intent.cut_of(clip.data)
        chapter = _chapter_of(tl, cut)
        out.append(f"{clip.address} · {clip.element} · track {clip.track} · " + (f"cut {cut}" + (f" ({chapter})" if chapter else "") if cut else "no cut")
                   + f" · clip id {clip.id}")
        if target.note:
            out.append(f"  ({target.note})")
        on, until, length = intent.on(clip.data), intent.until(clip.data), intent.for_s(clip.data)
        span = tl._own_cut_span(clip) if cut else None
        start_rule = f"on {on}" if on else ("with its cut" if span else "a fixed time")
        out.append(f"  starts  {clip.start:8.3f} s   {start_rule}" + (f"   (cut {cut} starts {span[0]:.3f} s)" if span else ""))
        if until:
            end_rule = f"until {until}"
        elif length is not None:
            end_rule = f"for {mo.offset_text(length, tl.fps).lstrip('+')} (a literal length)"
        elif span:
            end_rule = "with its cut"
        else:
            end_rule = "its own length"
        out.append(f"  ends    {clip.end:8.3f} s   {end_rule}   ({clip.duration:.3f} s, frames {clip.start_frame}–{clip.end_frame})")
        if clip.asset:
            entry = tl._registry(clip.shot_id).get(clip.asset) or {}
            media = entry.get("media_id") or entry.get("content_sha256") or ""
            size = f" · {entry['resolution'].replace('x', '×')} px" if entry.get("resolution") else ""
            drawn = _drawn_size(clip, entry)
            out.append(f"  asset   {clip.asset}{size}" + (f" · {drawn}" if drawn else "") + (f"   media {media}" if media else ""))
        if intent.standin(clip.data):
            out.append(f"  stand-in for {intent.standin(clip.data)[0]}")
        if intent.slot(clip.data):
            out.append(f"  slot    {intent.slot(clip.data)}")
        from astrid.sdk.timeline_checkout import formula_short, to_canvas

        params = dict(clip.data.get("params") or {})
        formulas = intent.formulas(clip.data)
        schema = element_schema(clip.element)
        props = schema.get("properties") or {}
        defaults = schema.get("defaults") or {}
        out.append("  params  (every one, as you write them: positions in canvas px; ƒ = computed by a formula; "
                   "address one with " + f"{clip.address}.KEY)")
        width = max([len(k) for k in params] + [6])
        for key in sorted(params):
            value = to_canvas(clip.element, key, params[key])
            exact = formulas.get(f"params.{key}")
            shown = (f"{formula_short(exact, clip.element)} = " if exact else "") + json.dumps(value, ensure_ascii=False)
            kind = _type_of(props[key]) if key in props else type(value).__name__.replace("str", "string").replace("dict", "object").replace("list", "array")
            from astrid.sdk.timeline_checkout import param_unit

            declared = param_unit(clip.element, key) or next(
                (param_unit(clip.element, f"{key}[0].{sub}") and f"{key}[].{sub}: " + str(param_unit(clip.element, f"{key}[0].{sub}"))
                 for sub in ("at", "frame", "appearAt") if param_unit(clip.element, f"{key}[0].{sub}")), None)
            unit = f" · {declared}" if declared else ""
            flagged = [p for p in formulas if p == f"params.{key}" or p.startswith(f"params.{key}[") or p.startswith(f"params.{key}.")]
            mark = "ƒ " if flagged else "  "
            note = "" if key in props or not props else "   (not declared by the element)"
            default = f"  default {json.dumps(to_canvas(clip.element, key, defaults[key]))}" if key in defaults else ""
            out.append(f"    {mark}{key:<{width}}  {shown}   [{kind}{unit}]{default}{note}")
            spec = props.get(key) or {}
            if isinstance(value, dict) and isinstance(spec.get("properties"), dict):  # an object: each of its keys
                if spec.get("description"):
                    out.append(f"        {spec['description']}")
                for sub, sub_spec in spec["properties"].items():
                    sub_value = json.dumps(value[sub], ensure_ascii=False) if sub in value else "(not set)"
                    said = f" — {sub_spec['description']}" if isinstance(sub_spec, dict) and sub_spec.get("description") else ""
                    out.append(f"        {key}.{sub} = {sub_value}   [{_type_of(sub_spec) if isinstance(sub_spec, dict) else 'any'}]{said}")
                for sub in [k for k in value if k not in spec["properties"]]:
                    out.append(f"        {key}.{sub} = {json.dumps(value[sub], ensure_ascii=False)}   (not declared by the element)")
            for path in [p for p in flagged if p != f"params.{key}"]:  # every computed part, each with its value
                out.append(f"        {path.removeprefix('params.')} = {formula_text(formulas[path])}{_resolved(tl, clip, path, formulas[path])}")
        if props:
            missing = [k for k in props if k not in params]
            if missing:
                out.append(f"  not set (the element's default applies): " + " · ".join(
                    f"{k}={json.dumps(to_canvas(clip.element, k, defaults[k]))}" if k in defaults else f"{k} ({_type_of(props[k])})"
                    for k in missing))
            out.append(f"  (from {schema.get('path', 'element.yaml').split('/packs/', 1)[-1]})")
        said = [w for w in tl.words() if w.start < clip.end and w.end > clip.start]
        if said:
            out.append(f'  while it is on: "{" ".join(w.text for w in said)}"')
        out.append(f"next: timelines edit {where} --clip {clip.address} --set KEY=VALUE  ·  --on MOMENT  ·  --until MOMENT")
    elif target.kind == "asset":
        entry = _registry_entry(tl, target.address) or {}
        res = str(entry.get("resolution") or "?").replace("x", "×")
        out.append(f"{target.address} · {entry.get('type', '?')} · {res} px · media {entry.get('media_id') or entry.get('content_sha256') or '?'}")
        users = [c for c in tl.clips() if c.asset == target.address]
        for c in users:
            drawn = _drawn_size(c, entry)
            out.append(f"  used by {c.address:<16} {c.element:<14} {c.start:7.2f}–{c.end:.2f} s" + (f"  {drawn}" if drawn else ""))
        if not users:
            out.append("  used by nothing (it is in the registry only)")
        out.append(f"next: timelines edit {where} --clip ADDRESS --swap-asset {target.address}   ·   timelines show {where} ADDRESS")
    elif target.kind == "param":
        from astrid.sdk.timeline_checkout import formula_short, to_canvas

        clip, key = target.clip, target.param
        schema = element_schema(clip.element)
        spec = (schema.get("properties") or {}).get(key) or {}
        stored = (clip.data.get("params") or {}).get(key)
        value = to_canvas(clip.element, key, stored)
        expr = intent.formulas(clip.data).get(f"params.{key}")
        unit = "canvas px" if key in ("x", "y") else (spec.get("type") or "value")
        out.append(f"{target.address} · {clip.element} param · {unit}" + (f"  ({target.note})" if target.note else ""))
        out.append(f"  value    {json.dumps(value, ensure_ascii=False) if stored is not None else '(not set: the default applies)'}"
                   + (f"   (stored {stored} on the sprite's 320×180 grid)" if value != stored and stored is not None else ""))
        if expr:
            out.append(f"  formula  {formula_short(expr, clip.element)} = {formula_text(expr)} (re-computed on every check)")
        if key in (schema.get("defaults") or {}):
            out.append(f"  default  {json.dumps(to_canvas(clip.element, key, schema['defaults'][key]))}")
        if spec:
            out.append(f"  schema   {_type_of(spec)}" + (f" — {spec.get('description')}" if spec.get("description") else ""))
        elif schema.get("properties"):
            out.append(f"  schema   not declared by {clip.element} (it takes: {', '.join(sorted(schema['properties']))})")
        hint = f"--set {key}=VALUE" + (" (replaces the formula)  ·  --set '" + key + "=ƒ(MARK ±px)' (keeps it computed)" if expr else "")
        out.append(f"next: timelines edit {where} --clip {clip.address} {hint}")
    elif target.kind == "word":
        word = target.word
        try:
            cut = tl.cut(f"@{word.start + 1e-3}")
            pic = cut.picture
            cut_id = intent.cut_of(pic.data) if pic is not None else None
        except Exception:  # noqa: BLE001
            cut_id = None
        line = tl.voice(word.segment)
        out.append(f"{target.address} · {word.start:.3f}–{word.end:.3f} s (frame {round(target.start * tl.fps)}) · line {word.segment}: \"{line.text}\""
                   + (f" · in cut {cut_id}" if cut_id else ""))
        showing = [c for c in tl.clips() if not c.is_audio and c.start - 1e-6 <= word.start < c.end - 1e-6]
        out.append("  on screen: " + (", ".join(c.address for c in showing) or "nothing"))
        entering = [c for c in tl.clips() if abs(c.start - word.start) < 1.5 / tl.fps]
        if entering:
            out.append("  entering on it: " + ", ".join(c.address for c in entering))
        out.append(f"next: timelines visualize {where} --preset motion --at '{target.address}'")
    elif target.kind == "cut":
        from astrid.sdk.timeline_sheet import render_sheet

        out.append(render_sheet(tl, start=target.start, end=target.end).rstrip())
        out.append(f"next: timelines show {where} {target.cut}.LAYER  (one layer's complete record)")
    else:
        t = target.start
        state = tl.at(t)
        cut = state["cut"]
        pic = cut.picture
        cut_id = intent.cut_of(pic.data) if pic is not None else f"cut {cut.n}"
        speaking = state["speaking"]
        out.append(f"{t:.3f} s · in {cut_id}" + (f" · saying {word_address(tl, speaking)}" if speaking else " · no one is speaking"))
        showing = [c for c in tl.clips() if not c.is_audio and c.start - 1e-6 <= t < c.end - 1e-6]
        out.append("  on screen: " + (", ".join(c.address for c in showing) or "nothing"))
    return "\n".join(out)


def _chapter_of(tl: Any, cut_id: str | None) -> str | None:
    if not cut_id:
        return None
    labels = {row.get("from"): row.get("name") for row in intent.chapters(tl.bundle)}
    order = [g["id"] for g in tl._cut_groups()]
    if cut_id not in order:
        return None
    return next((labels[c] for c in reversed(order[:order.index(cut_id) + 1]) if c in labels), None)


# ---------------------------------------------------------------- naming things in findings

def name_things(text: str, tl: Any, lo: float, hi: float) -> str:
    """Rewrite element/asset mentions in a finding line as addresses (``am-sprite ICON`` → ``c30.icon``),
    using the clips on screen in [lo, hi]. Repeated mentions map, in order, to clips in time order."""
    clips = sorted((c for c in tl.clips() if not c.is_audio and c.start < hi + 1e-6 and c.end > lo - 1e-6
                    and intent.layer_of(c.data)), key=lambda c: (c.start, c.address))
    if not clips:
        return text

    def by(pred: Any) -> list[Any]:
        return [c for c in clips if pred(c)]

    counters: dict[str, int] = {}

    def pick(key: str, pool: list[Any]) -> str | None:
        if not pool:
            return None
        k = counters.get(key, 0)
        counters[key] = k + 1
        return pool[min(k, len(pool) - 1)].address if (len(pool) == 1 or k < len(pool)) else None

    def element_asset(m: re.Match) -> str:
        element, asset = m.group(1), m.group(2)
        hit = pick(f"{element} {asset}", by(lambda c: c.element == element and (c.asset or "") == asset))
        return hit or m.group(0)

    def element_text(m: re.Match) -> str:
        element, said = m.group(1), m.group(2).rstrip("…")
        hit = pick(f"{element} \"{said}", by(lambda c: c.element == element and (c.text or "").startswith(said)))
        return hit or m.group(0)

    out = re.sub(r'\b(am-[a-z-]+) "([^"]*)"', element_text, text)
    out = re.sub(r"\b(am-[a-z-]+) ([A-Z][A-Z0-9_-]+)\b", element_asset, out)
    assets = {c.asset for c in clips if c.asset}

    def bare(m: re.Match) -> str:
        token = m.group(0)
        if token not in assets:
            return token
        return pick(f"bare {token}", by(lambda c: c.asset == token)) or token

    return re.sub(r"(?<![\w.-])[A-Z][A-Z0-9_-]{1,}(?![\w.-])", bare, out)


def cut_ids_by_ordinal(tl: Any) -> dict[int, str]:
    """``{picture-cut number: cut id}``: show/lint/visualize count picture cuts; the id is what you type."""
    out = {}
    for cut in tl.cuts:
        pic = cut.picture
        cid = intent.cut_of(pic.data) if pic is not None else None
        if cid:
            out[cut.n] = cid
    return out


def ordinals_to_ids(text: str, tl: Any, mapping: dict[int, str] | None = None) -> str:
    """``cut 31`` → ``c30b``; ``cuts 30–31`` → ``c30–c30b`` (one numbering: cut ids)."""
    ids = mapping if mapping is not None else cut_ids_by_ordinal(tl)
    if not ids:
        return text

    def one(m: re.Match) -> str:
        n = int(m.group(1))
        return ids.get(n, m.group(0)) if m.group(0).startswith("cut ") is False else ids.get(n, m.group(0))

    text = re.sub(r"\bcuts (\d+)[–-](\d+)\b",
                  lambda m: f"cuts {ids.get(int(m.group(1)), m.group(1))}–{ids.get(int(m.group(2)), m.group(2))}", text)
    return re.sub(r"\bcut #?(\d+)\b", lambda m: ids.get(int(m.group(1)), m.group(0)), text)


# ---------------------------------------------------------------- find by what you see or hear

@dataclass
class Found:
    """One thing a search found: where it is, and its address to paste into show/edit/visualize."""
    kind: str                        # spoken | text | asset | layer
    address: str                     # "the conclusion" in n21 · c30.cover · ROCKET
    start: float
    end: float
    cut: str | None = None
    line: str | None = None          # spoken: the narration line id
    words: str | None = None         # spoken: word ids, n21:1–2
    detail: str = ""                 # text on screen, element, asset size …
    clips: list[Any] = dataclasses.field(default_factory=list)   # what is on screen then (Clip objects)


def _cut_at(tl: Any, t: float) -> str | None:
    spans = sorted(((lo, hi if hi is not None else tl.duration, cid) for cid, (lo, hi) in tl._cut_spans().items()))
    return next((cid for lo, hi, cid in reversed(spans) if lo - 1e-6 <= t < hi - 1e-6), None)


def find(tl: Any, query: str | None = None, *, text: str | None = None, asset: str | None = None) -> list[Found]:
    """Everything that matches what you SEE or HEAR, with its address and place.

    ``query``: a spoken phrase (``the conclusion``, or a moment like ``"tool" in v20a``), a layer name
    (``claw``), on-screen text, or an asset key: every kind that matches. ``text=``: only on-screen text.
    ``asset=``: only assets (key, or part of it, any case)."""
    found: list[Found] = []
    if query:
        found += _find_spoken(tl, query) + _find_layers(tl, query) + _find_text(tl, query) + _find_assets(tl, query)
    if text:
        found += _find_text(tl, text)
    if asset:
        found += _find_assets(tl, asset)
    seen, out = set(), []
    for item in found:  # one thing, said once (a layer named like its asset is one hit)
        key = ("clip", item.address) if item.kind in ("layer", "text") else (item.kind, item.address)
        if key not in seen:
            seen.add(key)
            out.append(item)
    return out


def _on_screen(tl: Any, lo: float, hi: float) -> list[Any]:
    return sorted((c for c in tl.clips() if not c.is_audio and c.start < max(hi, lo + 1e-3) - 1e-6 and c.end > lo + 1e-6),
                  key=lambda c: (c.start, c.address))


def _find_spoken(tl: Any, raw: str) -> list[Found]:
    phrase = raw.strip()
    try:
        moment = mo.parse(phrase if MOMENT_HINT.search(phrase) else f'"{phrase.strip(chr(34))}"')
    except mo.MomentError:
        return []
    if moment.kind != "word":
        return []
    runs = mo.find_words(moment, tl.words())
    if moment.n is not None:
        runs = runs[moment.n - 1:moment.n] if 1 <= moment.n <= len(runs) else []
    out = []
    for run in runs:
        lo, hi = run[0].start, run[-1].end
        ids = run[0].id if len(run) == 1 else f"{run[0].id}–{run[-1].index}"
        cut = _cut_at(tl, lo)
        out.append(Found("spoken", _phrase_address(tl, moment, run), lo, hi, cut=cut, line=run[0].segment, words=ids,
                         detail=" ".join(w.text for w in run), clips=_on_screen(tl, lo, hi)))
    return out


def _find_layers(tl: Any, raw: str) -> list[Found]:
    name = raw.strip().strip('"')
    hits = [c for c in tl.clips() if intent.layer_of(c.data) == name or c.id == name]
    return [Found("layer", c.address, c.start, c.end, cut=intent.cut_of(c.data), detail=f"{c.element} · track {c.track}"
                  + (f" · {c.asset}" if c.asset else ""), clips=[c]) for c in sorted(hits, key=lambda c: c.start)]


def _find_text(tl: Any, raw: str) -> list[Found]:
    needle = re.sub(r"\s+", " ", raw.strip().strip('"“”')).lower()
    if not needle:
        return []
    hits = [c for c in tl.clips() if c.text and needle in re.sub(r"\s+", " ", c.text).lower()]
    return [Found("text", c.address, c.start, c.end, cut=intent.cut_of(c.data), detail=f'on screen: "{c.text}" · {c.element}',
                  clips=[c]) for c in sorted(hits, key=lambda c: c.start)]


def _find_assets(tl: Any, raw: str) -> list[Found]:
    want = raw.strip().strip('"').lower()
    if not want:
        return []
    keys = sorted({k for sid in tl._shot_ids() for k in tl._registry(sid)})
    exact = [k for k in keys if k.lower() == want]
    chosen = exact or [k for k in keys if want in k.lower()]
    out = []
    for key in chosen:
        users = sorted((c for c in tl.clips() if c.asset == key), key=lambda c: c.start)
        entry = _registry_entry(tl, key) or {}
        size = f" · {str(entry['resolution']).replace('x', '×')} px" if entry.get("resolution") else ""
        out.append(Found("asset", key, min((c.start for c in users), default=0.0), max((c.end for c in users), default=0.0),
                         detail=f"{entry.get('type', '?')}{size} · used by {len(users)} clip(s)", clips=users))
    return out


def describe_found(tl: Any, found: list[Found], *, timeline: str = "TL", project: str = "P") -> list[str]:
    """Plain lines: each hit's address and place, and the clips there (address, element, start–end)."""
    lines: list[str] = []
    where = f"{timeline} --project {project}"
    for item in found:
        span = f"{item.start:.2f}–{item.end:.2f} s"
        cut = f" · in {item.cut}" if item.cut else ""
        if item.kind == "spoken":
            lines.append(f'heard  {item.address}  · {span} · line {item.line}, words {item.words}{cut}')
        elif item.kind == "asset":
            lines.append(f"asset  {item.address}  · {item.detail}" + (f" · {span}" if item.clips else ""))
        else:
            lines.append(f"{'seen ' if item.kind == 'text' else 'layer'}  {item.address}  · {span}{cut} · {item.detail}")
        width = max([len(c.address) for c in item.clips] + [8])
        shown = item.clips if item.kind in ("spoken", "asset") else []  # a layer or text hit IS its clip
        for clip in shown[:12]:
            lines.append(f"         {clip.address:<{width}}  {clip.element:<14} {clip.start:7.2f}–{clip.end:.2f} s"
                         + (f"  {clip.asset}" if clip.asset else "") + (f'  "{clip.text[:30]}"' if clip.text else ""))
        if len(shown) > 12:
            lines.append(f"         … {len(shown) - 12} more")
    if found:
        first = found[0]
        target = first.clips[0].address if first.kind != "spoken" and first.clips else first.address
        lines.append(f"next: timelines show {where} {shlex_quote(target)}   ·   timelines visualize {where} --at {shlex_quote(first.address if first.kind == 'spoken' else target)}")
    return lines


def shlex_quote(text: str) -> str:
    import shlex

    return shlex.quote(text)
