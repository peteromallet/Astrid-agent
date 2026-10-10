"""The cut sheet: a timeline as a two-column A/V script you can read, edit and apply.

``timelines show --as sheet`` prints it; ``timelines apply`` reads an edited copy back
into the working copy. The same edit can be made as text, as a verb or in Python, and
all three end up as the same change.

    91.50 ┃ c30  on "And"                                                    · 2.50 s
          > And the conclusion of all that… is Astrid.
            plate   field  snap-plate  FIELD-PEACH
            sprite  cover  sprite      MYSTERY  scale=5 x=226 y=33 enter=stamp     for 1.9s
    93.50   chrome  icon   sprite      ICON     scale=1 x=225 y=35 enter=stamp     on "Astrid"
            why: held on the covered tool; on "Astrid" the cover drops: the real icon.

What you can change: a cut's ``on`` and ``why``; a layer's track, element, asset, text,
params and ``on``/``until``/``for``; add a layer line (a new name) or delete one; a line's
text and ``gap``. Derived parts are printed but ignored when applied: the time gutter, the
``· 2.50 s`` length, the ``>`` words, chapter labels (``# 06 ASTRID``) and the header.
A param shown as ``k=…`` is too long to print and stays as it is; ``k=ƒ`` is computed by a
formula; ``~k=v`` is read-only information (a sequence's steps and what it lands on).
"""
from __future__ import annotations

import copy
import json
import re
from typing import Any, Iterable

from astrid.core.timeline import moments as mo
from astrid.sdk import timeline_intent as intent

TRACK_ORDER = ["plate", "sprite", "fx", "type", "chrome"]  # bottom to top, as the eye builds a frame
ELIDED = object()
CLAUSES = ("on", "until", "for")
_TOKEN = re.compile(r'"(?:[^"\\]|\\.)*"|\S+')


class SheetError(ValueError):
    """A sheet line that cannot be applied; the message names the line and what to write."""


# ---------------------------------------------------------------- render

def render_sheet(tl: Any, *, start: float | None = None, end: float | None = None, banner: str | None = None,
                 film: str | None = None, cuts: Iterable[str] | None = None, detail: bool = False) -> str:
    """The cut sheet for the cuts that overlap [start, end) (all cuts by default), or exactly ``cuts``.
    ``detail``: long values (``lines=``, ``badges=``, ``inset=``) in full, as editable JSON, instead of ``…``."""
    groups = tl._cut_groups()
    if not groups:
        return "this timeline has no named cuts yet (import its intent first)\n"
    spans = tl._cut_spans()
    wanted = set(cuts) if cuts is not None else None
    picked = [g for g in groups
              if (wanted is not None and g["id"] in wanted) or (wanted is None
              and (end is None or g["start"] < end - 1e-6) and (start is None or (spans[g["id"]][1] or tl.duration) > start + 1e-6))]
    if wanted is not None:
        start = min((spans[g["id"]][0] for g in picked), default=0.0)
    fps = tl.fps
    canvas = _canvas(tl)
    version = sheet_version(tl)
    exported = tl.__dict__.setdefault("_exported", {})  # what each exported version was (apply merges against it)
    if version not in exported:
        exported[version] = copy.deepcopy(tl.bundle)
        for old in list(exported)[:-5]:
            exported.pop(old, None)
    out = [f"film {film or tl.bundle.get('project_id', '')} · {tl.bundle.get('timeline_id', '')} · {canvas} · {fps:g} fps"
           f" · v {version}"]
    if banner:
        out.append(banner)
    if start is not None or end is not None:
        ids = [g["id"] for g in picked]
        named = ", ".join(ids) if wanted is not None else f"{ids[0]}..{ids[-1]}" if ids else ""
        out.append(f"scope {named} ({len(ids)} cuts): apply changes only these cuts" if ids else "scope: no cuts")
    lines_used = []
    for g in picked:
        lo, hi = spans[g["id"]]
        for w in tl.words(between=(lo, hi if hi is not None else tl.duration + 1)):
            if w.segment not in lines_used:
                lines_used.append(w.segment)
    if lines_used:
        out += ["", "lines" + " " * 44 + "# the narration: everything hangs on it"]
        width = max(len(s) for s in lines_used)
        texts = {seg: '"' + tl.voice(seg).text.replace('"', "'") + '"' for seg in lines_used}
        tw = max(len(t) for t in texts.values())
        for seg in lines_used:
            gap = tl.voice(seg).gap_after
            out.append((f"  {seg:<{width}}  {texts[seg]:<{tw}}" + (f"  gap {round(gap, 2):g}" if gap is not None else "")).rstrip())
    sound = [c for c in sound_clips(tl) if (end is None or c.start < end - 1e-6) and (start is None or c.end > start + 1e-6)]
    if sound:
        out += ["", "sound" + " " * 44 + "# under the film, in no cut: asset, for, volume= (the grid: --beats)"]
        out += _align([_sound_row(tl, c) for c in sound])
    orphans = tl.orphans()
    if orphans:
        out += ["", "orphans" + " " * 42 + "# moments whose words are gone: re-home or remove"]
        out += [f"  {o}" for o in orphans]
    chapter = None
    labels = {row.get("from"): row.get("name") for row in intent.chapters(tl.bundle)}
    order = [g["id"] for g in groups]
    carried = _carried(tl, groups, spans)
    for g in picked:
        lo, hi = spans[g["id"]]
        hi = hi if hi is not None else tl.duration
        first = g["picture"] or min(g["clips"], key=lambda c: c.start)
        if labels:
            # the chapter a cut is in: the last label at or before it
            name = next((labels[cid] for cid in reversed(order[:order.index(g["id"]) + 1]) if cid in labels), chapter)
        else:
            name = _chapter(tl, first.shot_id)
        if name and name != chapter:
            out += ["", f"# {name}"]
            chapter = name
        pic = g["picture"]
        on = intent.on(pic.data) if pic else None
        head = f"{lo:6.2f} ┃ {g['id']:<5}" + (f" on {on}" if on else "")
        out.append(f"{head:<74}· {hi - lo:.2f} s")
        said = " ".join(w.text for w in tl.words(between=(lo, hi)))
        if said:
            out.append(f"       > {said}")
        layers = _ordered(g["clips"] + _loose_in(tl, lo, hi))
        rows = [_layer_row(tl, c, lo, hi, is_picture=(pic is not None and c.data is pic.data), detail=detail) for c in layers]
        out += _align(rows)
        for clip in carried.get(g["id"], []):
            out.append(f"         ↳ {clip.address} carries on over this cut (until {clip.end:.2f} s)")
        why = next((intent.why(c.data) for c in ([pic] if pic else []) + layers if intent.why(c.data)), None)
        if why:
            out.append(f"         why: {why}")
        if pic is not None and intent.deliberate(pic.data):
            out.append("         hold: deliberate")
    return "\n".join(out) + "\n"


def _loose_in(tl: Any, lo: float, hi: float) -> list[Any]:
    """Picture-side clips in no cut that start in [lo, hi): shown (by clip id) in the cut on screen, so the
    sheet never hides them and applying it never adds a second copy; an applied line puts them in the cut."""
    return [c for c in tl.clips() if not c.is_audio and not intent.cut_of(c.data) and lo - 1e-6 <= c.start < hi - 1e-6]


def sound_clips(tl: Any) -> list[Any]:
    """Audio under the film that belongs to no cut and is not a narration take: the music bed, room tone, sfx."""
    return sorted((c for c in tl.clips(audio=True) if not intent.cut_of(c.data) and not intent.line(c.data)),
                  key=lambda c: (c.start, c.id))


def _sound_row(tl: Any, clip: Any) -> list[str]:
    bits = []
    if isinstance(clip.data.get("volume"), (int, float)):
        bits.append(f"volume={clip.data['volume']:g}")
    label = intent.beats_label(clip.data)
    if label:
        bits.append(f"~beats={label}")
    until = intent.until(clip.data)
    timing = f"until {until}" if until else f"for {_length_text(clip.duration, tl.fps)}"
    return [f"{clip.start:6.2f}", clip.track or "sfx", intent.layer_of(clip.data) or clip.id, _short(clip.element),
            clip.asset or "", " ".join(bits), timing]


def _carried(tl: Any, groups: list[dict[str, Any]], spans: dict[str, Any]) -> dict[str, list[Any]]:
    """Layers that started in an earlier cut and are still on screen when a cut opens."""
    out: dict[str, list[Any]] = {}
    for g in groups:
        lo = g["start"]
        for other in groups:
            if other["start"] >= lo - 1e-6:
                continue
            for clip in other["clips"]:
                if not clip.is_audio and clip.start < lo - 1e-6 and clip.end > lo + 0.5 / tl.fps \
                        and not (intent.sequence(clip.data) or ("", 0))[1] and other["picture"] is not None \
                        and clip.data is not other["picture"].data:
                    out.setdefault(g["id"], []).append(clip)
    return out


def _canvas(tl: Any) -> str:
    try:
        canvas = tl.bundle["parent"]["config"]["theme_overrides"]["visual"]["canvas"]
        return f"{canvas['width']}×{canvas['height']}"
    except (KeyError, TypeError):
        return "1920×1080"


def _chapter(tl: Any, shot_id: str) -> str:
    payload = (tl.bundle.get("shots", {}).get(shot_id) or {}).get("payload") or {}
    return str(payload.get("name") or shot_id)


def _ordered(clips: list[Any]) -> list[Any]:
    def rank(c: Any) -> tuple:
        if c.is_audio:
            return (len(TRACK_ORDER) + 1, c.start, c.id)
        return (TRACK_ORDER.index(c.track) if c.track in TRACK_ORDER else len(TRACK_ORDER), c.start, c.id)

    shown = [c for c in clips if not ((intent.sequence(c.data) or ("", 0))[1] > 0)]
    return sorted(shown, key=rank)


def _short(element: str) -> str:
    return element[3:] if element.startswith("am-") else element


def _value(v: Any) -> str:
    if isinstance(v, str):
        return v if re.fullmatch(r"[A-Za-z0-9_.#%:/+-]+", v) and v not in ("true", "false", "null") and not _is_number(v) else json.dumps(v, ensure_ascii=False)
    return json.dumps(v, ensure_ascii=False, separators=(",", ":"))


def _is_number(text: str) -> bool:
    try:
        float(text)
        return True
    except ValueError:
        return False


def _layer_row(tl: Any, clip: Any, lo: float, hi: float, *, is_picture: bool, detail: bool = False) -> list[str]:
    gutter = f"{clip.start:6.2f}" if abs(clip.start - lo) > 0.5 / tl.fps else ""
    name = intent.layer_of(clip.data) or clip.id
    params = dict(clip.params)
    text = params.pop("text", None) if clip.element == "am-type" else None
    what = clip.asset or ""
    if text is not None:
        what = (what + " " if what else "") + json.dumps(str(text), ensure_ascii=False)
    seq = intent.sequence(clip.data)
    bits = []
    if seq is not None:
        steps = [c for c in tl.clips() if (intent.sequence(c.data) or ("",))[0] == seq[0]]
        bits.append(f"~steps={len(steps)}")
        fit = intent.sequence_fit(clip.data)
        if fit and isinstance(fit.get("land"), str):
            bits.append(f"~fit-until={fit['land']}")
    from astrid.sdk.timeline_checkout import formula_short, to_canvas

    formulas = intent.formulas(clip.data)
    for key, value in params.items():
        exact = formulas.get(f"params.{key}")
        if exact is not None:  # computed: show the formula (canvas px), never a bare number that edits would fight
            bits.append(f"{key}={formula_short(exact, clip.element)}")
            continue
        if any(p.startswith((f"params.{key}[", f"params.{key}.")) for p in formulas):
            bits.append(f"{key}=ƒ…")  # parts of it are computed: edit them by address (--set {key}[0].at=…)
            continue
        shown = _value(to_canvas(clip.element, key, value))
        if detail and len(shown) > 28:
            shown = json.dumps(to_canvas(clip.element, key, value), ensure_ascii=False, separators=(",", ":"))
            bits.append(f"{key}={shown}")
            continue
        bits.append(f"{key}={shown}" if len(shown) <= 28 else f"{key}=…")
    timing = []
    on = intent.on(clip.data)
    if on and not is_picture:
        timing.append(f"on {on}")
    until = intent.until(clip.data)
    length = intent.for_s(clip.data)
    if until:
        timing.append(f"until {until}")
    elif length is not None:
        timing.append(f"for {_length_text(length, tl.fps)}")
    track = clip.track if not clip.is_audio else (clip.track or "sfx")
    return [gutter, track, name, _short(clip.element), what, " ".join(bits), " ".join(timing)]


def _length_text(seconds: float, fps: float) -> str:
    """``1.9s`` for tenths of a second, ``88f`` for whole frames, else seconds to the millisecond."""
    text = mo.offset_text(seconds, fps).lstrip("+")
    return text or "0s"


def _align(rows: list[list[str]]) -> list[str]:
    """Columns: time · track · layer · element · asset/text · when (on/until/for) · params."""
    if not rows:
        return []
    order = [1, 2, 3, 4, 6, 5]
    widths = {i: max(len(r[i]) for r in rows) for i in order}
    out = []
    for r in rows:
        cells = [r[i].ljust(widths[i]) for i in order[:-1]] + [r[5]]
        out.append((f"{r[0]:>6}   " + "  ".join(cells)).rstrip())
    return out


# ---------------------------------------------------------------- parse

def parse_sheet(text: str) -> dict[str, Any]:
    """``{"lines": {id: {text, gap}}, "cuts": [{id, on, why, layers: [...]}]}`` from sheet text."""
    sheet: dict[str, Any] = {"lines": {}, "cuts": []}
    section = None
    cut = None
    fps = 30.0
    for number, raw in enumerate(text.splitlines(), 1):
        if raw.startswith("film "):
            m = re.search(r"([\d.]+) fps", raw)
            fps = float(m.group(1)) if m else fps
            v = re.search(r"· v ([0-9a-f]{8,})", raw)
            if v:
                sheet["version"] = v.group(1)
        line = _strip_comment(raw).rstrip()
        if not line.strip():
            continue
        body = line.strip()
        if raw.startswith(("film ", "WORKING COPY", "PUBLISHED", "next:", "scope ")) or body.startswith((">", "↳")) or raw.startswith("# "):
            continue
        if body in ("lines", "orphans", "sound"):
            section, cut = body, None
            continue
        if "┃" in line:
            section = "cuts"
            try:
                cut = _parse_header(line, number)
            except SheetError as exc:
                raise SheetError(_with_text(str(exc), number, body)) from None
            sheet["cuts"].append(cut)
            continue
        if section == "lines" and cut is None:
            try:
                entry = _parse_line_entry(body, number)
            except SheetError as exc:
                raise SheetError(_with_text(str(exc), number, body)) from None
            sheet["lines"][entry["id"]] = entry
            continue
        if section == "orphans":
            continue
        if section == "sound" and cut is None:
            try:
                sheet.setdefault("sound", []).append(_parse_layer(body, number, fps))
            except SheetError as exc:
                raise SheetError(_with_text(str(exc), number, body)) from None
            sheet["sound"][-1]["text_line"] = body
            continue
        if cut is None:
            raise SheetError(f"line {number}: {body[:60]!r} is outside any cut (a cut starts with a '┃ cNN' line)")
        if body.startswith("why:"):
            cut["why"] = body[4:].strip()
            continue
        if body.startswith("hold:"):
            value = body[5:].strip().lower()
            if value not in ("deliberate", "off"):
                raise SheetError(_with_text(f"line {number}: hold: takes deliberate (or off)", number, body))
            cut["hold"] = value
            continue
        try:
            cut["layers"].append(_parse_layer(body, number, fps))
        except SheetError as exc:
            raise SheetError(_with_text(str(exc), number, body)) from None
        cut["layers"][-1]["text_line"] = body
    return sheet


def _with_text(message: str, number: int, body: str) -> str:
    """``line 13: …`` → ``line 13  «sprite cover … until "Astird"»: …`` (the offending line, shortened)."""
    shown = body if len(body) <= 90 else body[:87] + "…"
    prefix = f"line {number}: "
    rest = message[len(prefix):] if message.startswith(prefix) else message
    return f"line {number}  «{shown}»\n  {rest}"


def _strip_comment(raw: str) -> str:
    out, quote = [], False
    for i, ch in enumerate(raw):
        if ch == '"':
            quote = not quote
        if ch == "#" and not quote and (i + 1 < len(raw) and raw[i + 1] == " ") and (i == 0 or raw[i - 1] == " "):
            if raw[:i].strip() == "":
                return ""  # a whole-line comment or a chapter label
            return "".join(out)
        out.append(ch)
    return "".join(out)


def _parse_header(line: str, number: int) -> dict[str, Any]:
    rest = line.split("┃", 1)[1]
    rest = re.sub(r"\s·\s*[\d.]+\s*s\s*$", "", rest).strip()
    tokens = rest.split(None, 1)
    if not tokens or not mo.CUT_ID_RE.match(tokens[0]):
        raise SheetError(f"line {number}: a cut line reads '┃ c21 on \"word\"'; got {line.strip()[:60]!r}")
    on = None
    if len(tokens) > 1:
        tail = tokens[1].strip()
        if not tail.startswith("on "):
            raise SheetError(f"line {number}: after the cut id write on <moment>, e.g. on \"viral\"")
        on = _canon(tail[3:], number)
    return {"id": tokens[0], "on": on, "why": None, "layers": [], "line": number}


def _parse_line_entry(body: str, number: int) -> dict[str, Any]:
    m = re.match(r'^(\S+)\s+"(.*)"\s*(?:gap[ =]\s*([\d.]+))?\s*$', body)
    if not m:
        raise SheetError(f"line {number}: a narration line reads  n21  \"text\"  gap 1.25")
    return {"id": m.group(1), "text": m.group(2), "gap": float(m.group(3)) if m.group(3) else None, "line": number}


def _tokens(body: str) -> list[str]:
    """Split on spaces outside quotes and brackets (so label="ONE YEAR" and {"a": 1} stay whole)."""
    out, buf, quote, depth, escape = [], [], False, 0, False
    for ch in body:
        if escape:
            buf.append(ch)
            escape = False
            continue
        if ch == "\\" and quote:
            buf.append(ch)
            escape = True
            continue
        if ch == '"':
            quote = not quote
        elif not quote and ch in "[{(":
            depth += 1
        elif not quote and ch in "]})":
            depth = max(0, depth - 1)
        if ch.isspace() and not quote and depth == 0:
            if buf:
                out.append("".join(buf))
                buf = []
            continue
        buf.append(ch)
    if buf:
        out.append("".join(buf))
    return out


def _parse_layer(body: str, number: int, fps: float = 30.0) -> dict[str, Any]:
    tokens = _tokens(body)
    gutter = None
    if tokens and re.fullmatch(r"\d+(\.\d+)?", tokens[0]):
        gutter, tokens = float(tokens[0]), tokens[1:]  # the time gutter
    if len(tokens) < 3:
        raise SheetError(f"line {number}: a layer reads  track name element [ASSET] [\"text\"] [k=v …] [on …] [until …|for …]")
    layer: dict[str, Any] = {"track": tokens[0], "name": tokens[1], "element": tokens[2], "asset": None, "text": None,
                             "params": {}, "on": None, "until": None, "for": None, "line": number, "at": gutter}
    rest = tokens[3:]
    current, buf = None, []

    def close() -> None:
        if current is None:
            return
        value = " ".join(buf).strip()
        if not value:
            raise SheetError(f"line {number}: {current} needs a value (e.g. {current} \"Astrid\")")
        if current == "for":
            layer["for"] = _read_seconds(value, number, fps)
        else:
            layer[current] = _canon(value, number)

    for token in rest:
        if token in CLAUSES:
            close()
            current, buf = token, []
            continue
        is_param = "=" in token and not token.startswith('"')
        if current is not None and not is_param and not token.startswith("~"):
            buf.append(token)  # part of the moment: "Astrid", after, beat 2, in n21, #2, +2f …
            continue
        close()
        current, buf = None, []
        if token.startswith("~"):
            continue  # read-only information (a sequence's steps, its fit)
        if is_param:
            key, value = token.split("=", 1)
            layer["params"][key] = _read_value(value, number)
        elif token.startswith('"'):
            layer["text"] = json.loads(token)
        elif layer["asset"] is None:
            layer["asset"] = token
        else:
            raise SheetError(f"line {number}: did not understand {token!r} (params are k=v; text goes in quotes)")
    close()
    return layer


def _read_value(value: str, number: int) -> Any:
    if value in ("…", "...", "ƒ") or value.endswith("…"):
        return ELIDED
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def _read_seconds(value: str, number: int, fps: float = 30.0) -> float:
    m = re.fullmatch(r"([\d.]+)\s*(s|f)?", value.strip())
    if not m:
        raise SheetError(f"line {number}: for takes a length like 1.9s or 12f; got {value!r}")
    return float(m.group(1)) / fps if m.group(2) == "f" else float(m.group(1))


def _canon(text: str, number: int) -> str:
    try:
        return mo.format_moment(mo.parse(text))
    except mo.MomentError as exc:
        raise SheetError(f"line {number}: {exc}") from None


# ---------------------------------------------------------------- apply

def apply_sheet(tl: Any, text: str) -> list[str]:
    """Apply an edited sheet (or a fragment of one) to a checkout. Returns the changes in plain words.

    Only the cuts in the text are touched. A layer line missing from a cut that is in the
    text is removed; a new layer name adds a layer. Then every moment resolves again."""
    from astrid.sdk.timeline_checkout import Checkout, TimelineEditError, describe_changes

    sheet = parse_sheet(text)
    before = Checkout(copy.deepcopy(tl.bundle))
    if sheet.get("version") and sheet["version"] != sheet_version(tl):
        # exported from an earlier version: merge three-way (what the sheet changed since it was exported,
        # onto what is here now), like save and publish. Never revert an edit made after the export.
        base = find_version(tl, sheet["version"])
        if base is None:
            raise SheetError(f"this sheet was exported from version {sheet['version']}, which this working copy no longer "
                             f"has (it is now {sheet_version(tl)}): re-export it (timelines show TL --as sheet) and make "
                             "your change again, so nothing edited since is undone")
        sheet = merge_sheet(sheet, base, tl)
    try:
        return _apply_parsed(tl, sheet, before)
    except Exception:
        tl.bundle = before.bundle  # all or nothing: a sheet that cannot be applied changes nothing
        tl._mcache = None
        raise


def sheet_version(tl: Any) -> str:
    """The version a sheet is exported from: a digest of the document (what apply merges against)."""
    import hashlib

    return hashlib.sha256(json.dumps(tl.document(), sort_keys=True, default=str).encode("utf-8")).hexdigest()[:12]


def find_version(tl: Any, version: str) -> Any:
    """The Checkout this working copy was at ``version``: now, an earlier saved edit (undo history), or its
    published base. None when it is not kept any more."""
    from astrid.sdk.timeline_checkout import Checkout, _history_dir, _read_step
    from astrid.sdk.timeline_cuts import base_bundle

    if sheet_version(tl) == version:
        return tl
    kept = (getattr(tl, "_exported", None) or {}).get(version)
    if kept is not None:
        return Checkout(copy.deepcopy(kept))
    candidates: list[Any] = []
    path = getattr(tl, "path", None)
    if path:
        from pathlib import Path

        target = Path(path)
        if target.is_file():
            candidates.append(lambda: target.read_text(encoding="utf-8"))
        for step in sorted(_history_dir(target).glob("*.json"), reverse=True):
            candidates.append(lambda step=step: _read_step(step)[1])
    for read in candidates:
        try:
            other = Checkout({})
            other.bundle = other._absorb(json.loads(read()))
            other = Checkout(other.bundle)
        except Exception:  # noqa: BLE001 - an unreadable step is skipped
            continue
        if sheet_version(other) == version:
            return other
    try:
        base = Checkout(base_bundle(tl.document()))
        if sheet_version(base) == version:
            return base
    except Exception:  # noqa: BLE001
        pass
    return None


def _norm_layer(layer: dict[str, Any] | None) -> str | None:
    if layer is None:
        return None
    keep = {k: v for k, v in layer.items() if k not in ("line", "text_line", "at")}
    keep["params"] = {k: ("…" if v is ELIDED else v) for k, v in (keep.get("params") or {}).items()}
    return json.dumps(keep, sort_keys=True, default=str)


def merge_sheet(sheet: dict[str, Any], base_tl: Any, tl: Any) -> dict[str, Any]:
    """Three-way, line by line: a line the sheet left as exported takes what is here now; a line the sheet
    changed applies; both changed (differently) is a clash, named, never a silent choice."""
    base, now = parse_sheet(render_sheet(base_tl)), parse_sheet(render_sheet(tl))
    base_cuts, now_cuts = {c["id"]: c for c in base["cuts"]}, {c["id"]: c for c in now["cuts"]}
    clashes: list[str] = []
    merged: dict[str, Any] = {"lines": {}, "cuts": [], "version": sheet.get("version")}

    def pick(what: str, b: Any, s: Any, c: Any) -> Any:
        if s == b:
            return c
        if c == b or s == c:
            return s
        clashes.append(what)
        return s

    for seg, entry in sheet["lines"].items():
        b, c = base["lines"].get(seg), now["lines"].get(seg)
        key = lambda e: None if e is None else (e.get("text"), e.get("gap"))
        chosen = pick(f"line {seg} (narration)", key(b), key(entry), key(c))
        if chosen is not None and chosen != key(c):
            merged["lines"][seg] = {**entry, "text": chosen[0], "gap": chosen[1]}
    if sheet.get("sound"):
        b_sound = {r["name"]: r for r in base.get("sound") or []}
        c_sound = {r["name"]: r for r in now.get("sound") or []}
        rows = []
        for row in sheet["sound"]:
            b, c = _norm_layer(b_sound.get(row["name"])), _norm_layer(c_sound.get(row["name"]))
            s = _norm_layer(row)
            if s == b:
                continue
            if c != b and s != c:
                clashes.append(f"sound {row['name']} (line {row['line']})")
            rows.append(row)
        if rows:
            merged["sound"] = rows
    for cut in sheet["cuts"]:
        b_cut, c_cut = base_cuts.get(cut["id"]), now_cuts.get(cut["id"])
        if b_cut is None:  # new in the sheet: as written
            merged["cuts"].append(cut)
            continue
        if c_cut is None:
            clashes.append(f"{cut['id']} (line {cut['line']}): it is gone since the sheet was exported")
            continue
        out = dict(c_cut)
        for field in ("on", "why", "hold"):
            value = pick(f"{cut['id']} {field} (line {cut['line']})", b_cut.get(field), cut.get(field), c_cut.get(field))
            out[field] = value
        b_l = {l["name"]: l for l in b_cut["layers"]}
        s_l = {l["name"]: l for l in cut["layers"]}
        c_l = {l["name"]: l for l in c_cut["layers"]}
        layers = []
        for name in list(dict.fromkeys(list(c_l) + list(s_l) + list(b_l))):
            b, s, c = _norm_layer(b_l.get(name)), _norm_layer(s_l.get(name)), _norm_layer(c_l.get(name))
            line = (s_l.get(name) or {}).get("line") or (b_l.get(name) or {}).get("line")
            if s == b:
                chosen = c_l.get(name)
            elif c == b or s == c:
                chosen = s_l.get(name)
            else:
                clashes.append(f"{cut['id']}.{name} (line {line})")
                chosen = s_l.get(name)
            if chosen is not None:
                layers.append(chosen)
        out["layers"] = layers
        merged["cuts"].append(out)
    if clashes:
        raise SheetError("not applied: since this sheet was exported, these were changed both here and in the sheet: "
                         + "; ".join(clashes[:10]) + (f"; … {len(clashes) - 10} more" if len(clashes) > 10 else "")
                         + ". Re-export (timelines show TL --as sheet) and make your change again.")
    return merged


def _apply_parsed(tl: Any, sheet: dict[str, Any], before: Any) -> list[str]:
    from astrid.sdk.timeline_checkout import TimelineEditError, describe_changes

    before = type(before)(copy.deepcopy(before.bundle))
    reflow = False
    for seg, entry in sheet["lines"].items():
        voice = tl.voice(seg)
        if entry["text"] and entry["text"] != voice.text.replace('"', "'"):
            intent.set_line_text(voice.clips[0].data, entry["text"])
        if entry["gap"] is not None and (voice.gap_after is None or abs(voice.gap_after - entry["gap"]) > 0.0051):
            voice.set_gap_after(entry["gap"], reflow=False)
            reflow = True
    if sheet.get("sound"):
        _apply_sound(tl, sheet["sound"])
    groups = {g["id"]: g for g in tl._cut_groups()}
    for cut in sheet["cuts"]:
        group = groups.get(cut["id"])
        if group is None:  # a new cut: its header's moment and a plate line (its picture) make it
            _add_cut(tl, cut)
            continue
        pic = group["picture"]
        if cut["on"] is not None and pic is not None and cut["on"] != intent.on(pic.data):
            pic.on(cut["on"])
        if cut["why"] is not None and pic is not None and cut["why"] != intent.why(pic.data):
            tl.set_cut_note(cut["id"], why=cut["why"])
        if pic is not None and cut.get("hold") is not None and (cut["hold"] == "deliberate") != intent.deliberate(pic.data):
            tl.set_cut_note(cut["id"], hold=cut["hold"] == "deliberate")
        lo, hi = tl._cut_spans()[cut["id"]]
        loose = _loose_in(tl, lo, hi if hi is not None else tl.duration)
        existing = {(intent.layer_of(c.data) or c.id): c for c in _ordered(group["clips"] + loose)}
        named: dict[str, int] = {}
        for layer in cut["layers"]:  # one name, one layer: never merge two lines into one clip
            if layer["name"] in named:
                raise SheetError(_with_text(
                    f"line {layer['line']}: {cut['id']} already has a layer named {layer['name']!r} (line {named[layer['name']]}); "
                    f"give each its own name ({layer['name']}, {layer['name']}-2, …)", layer["line"], layer.get("text_line", "")))
            named[layer["name"]] = layer["line"]
        seen = set()
        for layer in cut["layers"]:
            name = layer["name"]
            seen.add(name)
            clip = existing.get(name)
            try:
                if clip is None:
                    _add_layer(tl, cut["id"], group, layer)
                else:
                    loose = not intent.cut_of(clip.data)
                    was = json.dumps(clip.data, sort_keys=True, default=str) if loose else None
                    _update_layer(tl, clip, layer, is_picture=pic is not None and clip.data is pic.data)
                    if loose and json.dumps(clip.data, sort_keys=True, default=str) != was:
                        # a clip in no cut that this sheet edits joins the cut, keeping its length
                        if not intent.until(clip.data) and intent.for_s(clip.data) is None:
                            intent.set_for(clip.data, tl.quantize(clip.duration))
                        intent.set_cut(clip.data, cut["id"])
                        if name != clip.id:
                            intent.set_layer(clip.data, name)
            except (TimelineEditError, mo.MomentError) as exc:
                said = str(exc)
                said = said if said.startswith(f"{cut['id']}.{name}") else f"{cut['id']}.{name}: {said}"
                raise SheetError(_with_text(f"line {layer['line']}: {said}", layer["line"], layer.get("text_line", ""))) from None
        for name, clip in existing.items():
            if name not in seen and intent.cut_of(clip.data) == cut["id"]:  # a loose clip is never removed by omission
                clip.remove()
    tl._mcache = None
    tl.resolve()
    if reflow:
        tl.reflow()
    return describe_changes(before, tl)


def _add_cut(tl: Any, cut: dict[str, Any]) -> None:
    """A cut the timeline does not have yet: ``┃ c05b on "word"`` plus a ``plate`` line (the picture) and any
    other layers. It is a split of the cut on screen there (``Checkout.add_cut``): layers starting after it
    move into it, layers spanning it carry over it; then the sheet's lines for it are applied."""
    from astrid.sdk.timeline_checkout import TimelineEditError

    line = cut["line"]
    if not cut["on"]:
        raise SheetError(f"line {line}: a new cut needs its moment: ┃ {cut['id']} on \"word\" (or on c05 +1.2s)")
    plates = [layer for layer in cut["layers"] if layer["track"] == "plate"]
    if not plates:
        raise SheetError(f"line {line}: new cut {cut['id']} needs a plate line (its picture), e.g.  plate  field  snap-plate  FIELD")
    names = [layer["name"] for layer in cut["layers"]]
    if len(set(names)) != len(names):
        raise SheetError(f"line {line}: {cut['id']} names a layer twice; give each its own name")
    try:
        new = tl.add_cut(cut["on"], id=cut["id"], picture=plates[0]["asset"] or None)
    except (TimelineEditError, mo.MomentError) as exc:
        raise SheetError(_with_text(f"line {line}: {cut['id']}: {exc}", line, f"┃ {cut['id']} on {cut['on']}")) from None
    pic = new.picture
    intent.set_layer(pic.data, plates[0]["name"])
    tl._mcache = None
    group = {"id": cut["id"], "clips": [], "picture": pic, "start": pic.start}
    existing = {(intent.layer_of(c.data) or c.id): c for c in tl.clips() if intent.cut_of(c.data) == cut["id"]}
    for layer in cut["layers"]:
        try:
            clip = existing.get(layer["name"])
            if clip is None:
                _add_layer(tl, cut["id"], group, layer)
            else:
                _update_layer(tl, clip, layer, is_picture=clip.data is pic.data)
        except (TimelineEditError, mo.MomentError) as exc:
            raise SheetError(_with_text(f"line {layer['line']}: {cut['id']}.{layer['name']}: {exc}", layer["line"],
                                        layer.get("text_line", ""))) from None
    if cut["why"]:
        intent.set_why(pic.data, cut["why"])


def _apply_sound(tl: Any, rows: list[dict[str, Any]]) -> None:
    """The sound section: a line changes its clip's asset, start, length (for/until) or volume. A line left
    out changes nothing (the bed is never removed from a sheet)."""
    from astrid.sdk.timeline_checkout import TimelineEditError

    clips = {(intent.layer_of(c.data) or c.id): c for c in sound_clips(tl)}
    for row in rows:
        clip = clips.get(row["name"])
        if clip is None:
            raise SheetError(_with_text(f"line {row['line']}: no sound clip {row['name']!r} (sound clips: "
                                        f"{', '.join(clips) or 'none'}); add one with timelines edit", row["line"], row.get("text_line", "")))
        try:
            if row["asset"] and row["asset"] != clip.asset:
                clip.swap_asset(row["asset"])
            if row.get("at") is not None and abs(row["at"] - clip.start) > 0.0051:
                clip.enter_at(row["at"], anchor=False)
            if row["until"] and row["until"] != intent.until(clip.data):
                clip.until(row["until"])
            elif row["for"] is not None and abs(row["for"] - clip.duration) > 0.5 / tl.fps:
                clip.set_duration(row["for"])
            for key, value in row["params"].items():
                if key != "volume":
                    raise SheetError(f"line {row['line']}: a sound line takes volume= only (got {key}=)")
                if value is not ELIDED and value != clip.data.get("volume"):
                    clip.data["volume"] = value
        except (TimelineEditError, mo.MomentError) as exc:
            raise SheetError(_with_text(f"line {row['line']}: {clip.address}: {exc}", row["line"], row.get("text_line", ""))) from None


def _element(token: str, current: str | None = None) -> str:
    if current and _short(current) == token:
        return current
    if token in ("media",) or token.startswith("am-") or "/" in token:
        return token
    return "am-" + token


def _update_layer(tl: Any, clip: Any, layer: dict[str, Any], *, is_picture: bool) -> None:
    from astrid.sdk.timeline_checkout import TimelineEditError

    data = clip.data
    element = _element(layer["element"], clip.element)
    if element != clip.element:
        data["clipType"] = element
    if layer["track"] != clip.track and not clip.is_audio:
        clip.set_track(layer["track"])  # its timing stays (a picture's in-point becomes an explicit offset)
    if (layer["asset"] or None) != (clip.asset or None):
        if layer["asset"]:
            clip.swap_asset(layer["asset"])
        else:
            data.pop("asset", None)
    params = data.get("params") if isinstance(data.get("params"), dict) else None
    current = dict(params or {})
    wanted = dict(layer["params"])
    if element == "am-type":
        if layer["text"] is not None:
            wanted["text"] = layer["text"]
        elif "text" in current:
            wanted["text"] = current["text"]
    for key in list(current):
        if key not in wanted and key != "text":
            if params is not None:
                params.pop(key, None)
    from astrid.sdk.timeline_address import element_schema, unknown_params

    bad = unknown_params(element, [k for k, v in wanted.items() if v is not ELIDED], existing=current)
    if bad:
        props = sorted(element_schema(element).get("properties") or {})
        raise TimelineEditError(f"{element} has no param {', '.join(bad)}; it takes: {', '.join(props)}")
    from astrid.sdk.timeline_checkout import formula_from_spec, formula_short, parse_formula_value, to_canvas

    formulas = intent.formulas(data)
    for key, value in wanted.items():
        if value is ELIDED:
            continue
        exact = formulas.get(f"params.{key}")
        if exact is not None and isinstance(value, str) and value.strip() == formula_short(exact, element):
            continue  # the formula as the sheet printed it: unchanged (a sheet always round-trips)
        spec = parse_formula_value(value)
        if spec is not None:
            current_expr = formulas.get(f"params.{key}") or {}
            new_expr = formula_from_spec(spec, element, key, current_expr)
            if not (current_expr and current_expr.get("mark") == new_expr["mark"]
                    and abs(float(current_expr.get("offset") or 0) - new_expr["offset"]) < 1e-6):
                clip.set(**{key: value})
            continue
        has_formula = any(p == f"params.{key}" or p.startswith((f"params.{key}[", f"params.{key}.")) for p in formulas)
        if has_formula or to_canvas(element, key, current.get(key)) != value:
            clip.set(**{key: value})
    if not is_picture:
        if layer["on"] != intent.on(data):
            clip.on(layer["on"]) if layer["on"] else tl._place_on(clip, None)
    same_for = (layer["for"] is None and intent.for_s(data) is None) or (
        layer["for"] is not None and intent.for_s(data) is not None and abs(layer["for"] - intent.for_s(data)) < 0.5 / tl.fps)
    if layer["until"] != intent.until(data) or not same_for:
        if layer["until"]:
            clip.until(layer["until"])
        elif layer["for"] is not None:
            clip.hold_for(layer["for"])
        else:
            intent.set_until(data, None)
            intent.set_for(data, None)


def _add_layer(tl: Any, cut_id: str, group: dict[str, Any], layer: dict[str, Any]) -> None:
    from astrid.sdk.timeline_address import element_schema, unknown_params
    from astrid.sdk.timeline_checkout import TimelineEditError

    from astrid.sdk.timeline_checkout import from_canvas

    from astrid.sdk.timeline_checkout import parse_formula_value, parse_moment_value

    element = _element(layer["element"])
    computed = {k: v for k, v in layer["params"].items()
                if v is not ELIDED and (parse_formula_value(v) is not None or parse_moment_value(v) is not None)}
    params = {k: from_canvas(element, k, v) for k, v in layer["params"].items() if v is not ELIDED and k not in computed}
    bad = unknown_params(element, list(params) + list(computed))
    if bad:
        raise TimelineEditError(f"{element} has no param {', '.join(bad)}; it takes: "
                                + ", ".join(sorted(element_schema(element).get("properties") or {})))
    if layer["text"] is not None:
        params["text"] = layer["text"]
    start = group["start"]
    clip = tl.add(element, at=start, asset=layer["asset"], params=params, track=layer["track"], anchor=False,
                  cut=cut_id, layer=layer["name"])
    intent.set_cut(clip.data, cut_id)
    intent.set_layer(clip.data, layer["name"])
    tl._mcache = None
    if layer["on"]:
        clip.on(layer["on"])
    if layer["until"]:
        clip.until(layer["until"])
    elif layer["for"] is not None:
        clip.hold_for(layer["for"])
    for key, value in computed.items():  # x=ƒ(B2-HAND -42), at=ƒ("adapt" in w05c): a formula, not text
        clip.set_param(key, value)


def range_cuts(tl: Any, text: str) -> list[str]:
    """The cut ids a range list names: ``c10,c29,c33a`` · ``c10..c12,c29`` · ``"viral"..c05`` (comma-separated
    windows; each window is every cut it overlaps, by time). In time order, each once."""
    spans = tl._cut_spans()
    order = [g["id"] for g in tl._cut_groups()]
    chosen: set[str] = set()
    for part in re.split(r',(?=(?:[^"]*"[^"]*")*[^"]*$)', text):
        if not part.strip():
            continue
        lo, hi = moment_range(tl, part.strip())
        for cid in order:
            a, b = spans[cid][0], spans[cid][1] if spans[cid][1] is not None else tl.duration
            if (hi is None or a < hi - 1e-6) and (lo is None or b > lo + 1e-6):
                chosen.add(cid)
    return [c for c in order if c in chosen]


def moment_range(tl: Any, text: str) -> tuple[float | None, float | None]:
    """Seconds for a range or a single address: ``c30`` (one cut), ``c30..c31`` (both, inclusive),
    ``'"and the conclusion".."Named"'``, ``91.5..96``, ``c41.mink`` (its span)."""
    from astrid.sdk.timeline_address import AddressError, resolve

    try:
        target = resolve(tl, text, prefer="time")
    except AddressError as exc:
        raise SheetError(str(exc)) from None
    if target.kind == "time" or target.kind == "word":
        return target.start, target.start + 1e-3
    return target.start, target.end


def iter_layers(sheet: dict[str, Any]) -> Iterable[tuple[str, dict[str, Any]]]:
    for cut in sheet["cuts"]:
        for layer in cut["layers"]:
            yield cut["id"], layer
