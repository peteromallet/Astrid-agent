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

import re
from dataclasses import dataclass, field
from typing import Any

from astrid.core.timeline import moments as mo
from astrid.sdk import timeline_intent as intent

TIME_RE = re.compile(r"^@?(?:(\d+):)?(\d+(?:\.\d+)?)s?$")
CLIP_ADDR_RE = re.compile(r"^(c\d+[a-z]?)\.(\S+)$")
MOMENT_HINT = re.compile(r'^(after |beat |downbeat |on )|"|“| in [A-Za-z]|#\d|[+-]\d+(\.\d+)?[fs]\b')


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
        if lowered in tl._cut_spans():
            return [cut_target(tl, lowered)]
        orphans = tl._cut_clips(lowered)
        if orphans:  # an orphaned cut (its line was removed): still addressable, to re-home or remove
            lo, hi = min(c.start for c in orphans), max(c.end for c in orphans)
            return [Target("cut", lowered, lo, hi, cut=lowered, note="orphaned: re-home it with --cut %s --on MOMENT" % lowered)]
        ids = list(tl._cut_spans())
        plain = [c for c in ids if re.fullmatch(r"c\d+", c)]
        extra = [c for c in ids if c not in plain]
        span = (f"{plain[0]}…{plain[-1]}" if len(plain) > 1 else ", ".join(plain)) + (f" (+ {', '.join(extra)})" if extra else "")
        raise AddressError(f"no cut {lowered}; cuts are {span}")
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
    names = _names(tl, raw)
    spoken = _moment(tl, f'"{raw}"')
    text_hits = _text(tl, raw)
    tiers = [spoken, names, text_hits] if prefer == "time" else [names, spoken, text_hits]
    return next((tier for tier in tiers if tier), [])


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
        try:
            t = mo.floor_frame(mo.resolve(moment, _ctx(tl)), tl.fps)
        except mo.MomentError:
            return []
        return [Target("time", mo.format_moment(moment), t, t)]
    hits = mo.find_words(moment, tl.words())
    if moment.n is not None:
        hits = hits[moment.n - 1:moment.n] if 1 <= moment.n <= len(hits) else []
    out = []
    for run in hits:
        word = run[-1] if moment.edge == "end" else run[0]
        t = (word.end if moment.edge == "end" else word.start) + moment.offset_s + moment.offset_frames / tl.fps
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
                 "additional": bool(schema.get("additionalProperties", True)), "path": str(path)}
        break
    _SCHEMAS[element] = found
    return found


def formula_text(expr: Any) -> str:
    """A formula in words: ``"Building" in v27 (clip frame, ≥4, step 2)`` · ``mark B2-HAND x -20``."""
    if not isinstance(expr, dict):
        return str(expr)
    if "mark" in expr:
        offset = float(expr.get("offset") or 0)
        return f"mark {expr['mark']} {expr.get('axis', 'x')} {offset:+g}" + (" px" if expr.get("unit") == "px" else "")
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
            out.append(f"  asset   {clip.asset}" + (f"   media {media}" if media else ""))
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
            unit = " canvas px" if key in ("x", "y") else ""
            flagged = [p for p in formulas if p == f"params.{key}" or p.startswith(f"params.{key}[") or p.startswith(f"params.{key}.")]
            mark = "ƒ " if flagged else "  "
            note = "" if key in props or not props else "   (not declared by the element)"
            default = f"  default {json.dumps(to_canvas(clip.element, key, defaults[key]))}" if key in defaults else ""
            out.append(f"    {mark}{key:<{width}}  {shown}   [{kind}{unit}]{default}{note}")
            flagged = [p for p in flagged if p != f"params.{key}"]
            groups: dict[str, list[str]] = {}
            for path in flagged:
                head, _, leaf = path.removeprefix("params.").rpartition(".")
                groups.setdefault(head, []).append((f"{leaf} = " if head else "= ") + formula_text(formulas[path]))
            for head, parts in groups.items():
                out.append(f"        {head + ': ' if head else ''}" + " · ".join(parts))
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
        out.append(f"{target.address} · {word.start:.3f}–{word.end:.3f} s · line {word.segment}: \"{line.text}\""
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
