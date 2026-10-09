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
                 film: str | None = None) -> str:
    """The cut sheet for the cuts that overlap [start, end) (all cuts by default)."""
    groups = tl._cut_groups()
    if not groups:
        return "this timeline has no named cuts yet (import its intent first)\n"
    spans = tl._cut_spans()
    picked = [g for g in groups
              if (end is None or g["start"] < end - 1e-6) and (start is None or (spans[g["id"]][1] or tl.duration) > start + 1e-6)]
    fps = tl.fps
    canvas = _canvas(tl)
    out = [f"film {film or tl.bundle.get('project_id', '')} · {tl.bundle.get('timeline_id', '')} · {canvas} · {fps:g} fps"]
    if banner:
        out.append(banner)
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
        layers = _ordered(g["clips"])
        rows = [_layer_row(tl, c, lo, hi, is_picture=(pic is not None and c.data is pic.data)) for c in layers]
        out += _align(rows)
        for clip in carried.get(g["id"], []):
            out.append(f"         ↳ {clip.address} carries on over this cut (until {clip.end:.2f} s)")
        why = next((intent.why(c.data) for c in ([pic] if pic else []) + layers if intent.why(c.data)), None)
        if why:
            out.append(f"         why: {why}")
    return "\n".join(out) + "\n"


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


def _layer_row(tl: Any, clip: Any, lo: float, hi: float, *, is_picture: bool) -> list[str]:
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
    for key, value in params.items():
        if key in intent.formulas(clip.data) or key == "keyframes" and any(p.startswith("params.keyframes") for p in intent.formulas(clip.data)):
            bits.append(f"{key}=ƒ")
            continue
        shown = _value(value)
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
        line = _strip_comment(raw).rstrip()
        if not line.strip():
            continue
        body = line.strip()
        if raw.startswith(("film ", "WORKING COPY", "PUBLISHED", "next:")) or body.startswith((">", "↳")) or raw.startswith("# "):
            continue
        if body in ("lines", "orphans"):
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
        if cut is None:
            raise SheetError(f"line {number}: {body[:60]!r} is outside any cut (a cut starts with a '┃ cNN' line)")
        if body.startswith("why:"):
            cut["why"] = body[4:].strip()
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
        elif not quote and ch in "[{":
            depth += 1
        elif not quote and ch in "]}":
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
    if tokens and re.fullmatch(r"\d+(\.\d+)?", tokens[0]):
        tokens = tokens[1:]  # the time gutter
    if len(tokens) < 3:
        raise SheetError(f"line {number}: a layer reads  track name element [ASSET] [\"text\"] [k=v …] [on …] [until …|for …]")
    layer: dict[str, Any] = {"track": tokens[0], "name": tokens[1], "element": tokens[2], "asset": None, "text": None,
                             "params": {}, "on": None, "until": None, "for": None, "line": number}
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
    reflow = False
    for seg, entry in sheet["lines"].items():
        voice = tl.voice(seg)
        if entry["text"] and entry["text"] != voice.text.replace('"', "'"):
            intent.set_line_text(voice.clips[0].data, entry["text"])
        if entry["gap"] is not None and (voice.gap_after is None or abs(voice.gap_after - entry["gap"]) > 0.0051):
            voice.set_gap_after(entry["gap"], reflow=False)
            reflow = True
    groups = {g["id"]: g for g in tl._cut_groups()}
    for cut in sheet["cuts"]:
        group = groups.get(cut["id"])
        if group is None:
            raise SheetError(f"line {cut['line']}: there is no cut {cut['id']} (adding cuts from a sheet is not supported yet)")
        pic = group["picture"]
        if cut["on"] is not None and pic is not None and cut["on"] != intent.on(pic.data):
            pic.on(cut["on"])
        if cut["why"] is not None and pic is not None and cut["why"] != intent.why(pic.data):
            intent.set_why(pic.data, cut["why"])
        existing = {(intent.layer_of(c.data) or c.id): c for c in _ordered(group["clips"])}
        seen = set()
        for layer in cut["layers"]:
            name = layer["name"]
            seen.add(name)
            clip = existing.get(name)
            try:
                if clip is None:
                    _add_layer(tl, cut["id"], group, layer)
                else:
                    _update_layer(tl, clip, layer, is_picture=pic is not None and clip.data is pic.data)
            except (TimelineEditError, mo.MomentError) as exc:
                said = str(exc)
                said = said if said.startswith(f"{cut['id']}.{name}") else f"{cut['id']}.{name}: {said}"
                raise SheetError(_with_text(f"line {layer['line']}: {said}", layer["line"], layer.get("text_line", ""))) from None
        for name, clip in existing.items():
            if name not in seen:
                clip.remove()
    tl._mcache = None
    tl.resolve()
    if reflow:
        tl.reflow()
    return describe_changes(before, tl)


def _element(token: str, current: str | None = None) -> str:
    if current and _short(current) == token:
        return current
    if token in ("media",) or token.startswith("am-") or "/" in token:
        return token
    return "am-" + token


def _update_layer(tl: Any, clip: Any, layer: dict[str, Any], *, is_picture: bool) -> None:
    data = clip.data
    element = _element(layer["element"], clip.element)
    if element != clip.element:
        data["clipType"] = element
    if layer["track"] != clip.track and not clip.is_audio:
        data["track"] = layer["track"]
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
    for key, value in wanted.items():
        if value is ELIDED:
            continue
        if current.get(key) != value:
            clip.set(**{key: value})
            intent.set_formula(data, f"params.{key}", None)
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
    element = _element(layer["element"])
    params = {k: v for k, v in layer["params"].items() if v is not ELIDED}
    if layer["text"] is not None:
        params["text"] = layer["text"]
    start = group["start"]
    clip = tl.add(element, at=start, hold=1.0, asset=layer["asset"], params=params, track=layer["track"], anchor=False)
    intent.set_cut(clip.data, cut_id)
    intent.set_layer(clip.data, layer["name"])
    tl._mcache = None
    if layer["on"]:
        clip.on(layer["on"])
    if layer["until"]:
        clip.until(layer["until"])
    elif layer["for"] is not None:
        clip.hold_for(layer["for"])


def moment_range(tl: Any, text: str) -> tuple[float | None, float | None]:
    """``'"and the conclusion".."Named"'`` or ``'91.5..96'`` or ``c30..c31`` → (start, end) seconds."""
    parts = re.split(r'\.\.(?=(?:[^"]*"[^"]*")*[^"]*$)', text, maxsplit=1)
    if len(parts) != 2:
        raise SheetError(f"a range reads A..B (times, \"words\" or cut ids); got {text!r}")

    def one(side: str) -> float | None:
        side = side.strip()
        if not side:
            return None
        if _is_number(side):
            return float(side)
        return tl._moment_time(mo.format_moment(mo.parse(side)))

    return one(parts[0]), one(parts[1])


def iter_layers(sheet: dict[str, Any]) -> Iterable[tuple[str, dict[str, Any]]]:
    for cut in sheet["cuts"]:
        for layer in cut["layers"]:
            yield cut["id"], layer
