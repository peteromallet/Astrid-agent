"""Two more ways to read one timeline: as a word-anchored script and as code.

``timelines show`` prints the cut table by default (``timeline_cuts``). These
views answer the questions an editor asks next:

- **script** (``--as script``): the voiceover in time order, with every cut,
  layer entrance/exit and element keyframe placed between the words it lands
  on, and how far each cut sits from the nearest word onset. This is the view
  for timing decisions: "does the cut land on the word?", "what fires on
  'rebuilt'?", "is there a dead stretch?".
- **code** (``--as code``): the composition as a small readable program, one
  block per shot and cut, each element called like a function with its
  authored props, its keyframes resolved to timeline seconds, and the
  element's source file one hop away. This is the view for understanding and
  changing what each element does.

Both are pure text over an authoring bundle, so two revisions can be diffed
line by line (``timelines diff --as script|code``).

Keyframes: astrid_motion elements key internal motion by clip-relative
*frames* (``at``, ``appearAt``, ``atFrame``, ``startAt``; see each
element.yaml). They are resolved here as ``clip start + frame / fps``.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping, Sequence

from astrid.sdk.timeline_cuts import (
    _clip_text,
    _fps,
    _internal,
    _is_audio,
    _list,
    _map,
    _num,
    _shot_name,
    _track_kinds,
    _words_for_occurrence,
    build_cut_table,
    clip_duration,
    timecode,
)

__all__ = ["element_source", "clip_keyframes", "render_script", "render_code"]

FRAME_KEYS = ("at", "appearAt", "atFrame", "startAt")
LABEL_KEYS = ("text", "label", "title", "name", "glyph", "author", "interface", "highlight")
_PACKS = Path(__file__).resolve().parents[1] / "packs"


@lru_cache(maxsize=None)
def element_source(clip_type: str) -> str | None:
    """Repository-relative path of an element's component source, if it is in this checkout."""
    if not clip_type or "/" in clip_type or clip_type == "media":
        return None
    for manifest in sorted(_PACKS.glob(f"*/elements/*/{clip_type}/element.yaml")):
        folder = manifest.parent
        for name in ("component.tsx", "component.jsx", "index.tsx"):
            if (folder / name).is_file():
                return str((folder / name).relative_to(_PACKS.parent.parent))
        return str(manifest.relative_to(_PACKS.parent.parent))
    return None


def _item_label(item: Mapping[str, Any]) -> str:
    for key in LABEL_KEYS:
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            text = " ".join(value.split())
            return text if len(text) <= 28 else text[:27] + "…"
    lines = item.get("lines")
    if isinstance(lines, list) and lines and isinstance(lines[0], str):
        return lines[0][:27] + ("…" if len(lines[0]) > 27 else "")
    keys = [key for key in item if key not in FRAME_KEYS]
    return ",".join(keys[:3]) or "step"


def clip_keyframes(clip: Mapping[str, Any], clip_start: float, fps: float) -> list[tuple[float, str]]:
    """``(timeline seconds, 'path → label')`` for each frame-keyed item in a clip's params."""
    found: list[tuple[float, str]] = []

    def walk(value: Any, path: str) -> None:
        if isinstance(value, Mapping):
            frame_key = next((key for key in FRAME_KEYS if isinstance(value.get(key), (int, float))
                              and not isinstance(value.get(key), bool)), None)
            if frame_key is not None and path:
                frame = float(value[frame_key])
                found.append((clip_start + frame / fps, f"{path} → {_item_label(value)} (f{int(frame)})"))
            for key, child in value.items():
                if isinstance(child, (Mapping, list)):
                    walk(child, f"{path}.{key}" if path else key)
        elif isinstance(value, list):
            for index, child in enumerate(value):
                if isinstance(child, Mapping):
                    walk(child, f"{path}[{index}]")

    walk(_map(clip.get("params")), "")
    return sorted(found)


def _placements(bundle: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    rows = [_map(item) for item in _list(bundle.get("placements")) if isinstance(item, Mapping)]
    return sorted(rows, key=lambda row: _num(row.get("at_ms", _map(row.get("placement")).get("start_ms"))))


def _in_window(start: float, end: float, window: Sequence[float] | None) -> bool:
    return window is None or (end > window[0] and start < window[1])


# ------------------------------------------------------------------ script

def render_script(
    bundle: Mapping[str, Any],
    *,
    window: Sequence[float] | None = None,
    shot_ids: set[str] | None = None,
    pause: float = 0.3,
    width: int = 150,
) -> str:
    """Words in time order with cuts, layer entrances/exits and keyframes between them."""
    fps = _fps(bundle)
    table = build_cut_table(bundle)
    cut_rows = {(row["occurrence_id"], round(row["start"], 4)): row for row in table["rows"]}
    shots = _map(bundle.get("shots"))
    lines = [
        "Script view: words in time order; ┃ cut, + enters, − exits, ◆ element keyframe (clip frame resolved to "
        f"timeline seconds at {fps:g} fps). 'Δ' is the cut's distance to the nearest word onset (− = before the word).",
    ]
    for placement in _placements(bundle):
        shot_id = str(placement.get("shot_id") or "")
        if shot_ids and shot_id not in shot_ids:
            continue
        occurrence_id = str(placement.get("occurrence_id") or shot_id)
        start = _num(placement.get("at_ms", _map(placement.get("placement")).get("start_ms"))) / 1000.0
        end = start + _num(placement.get("duration_ms")) / 1000.0
        if not _in_window(start, end, window):
            continue
        shot = _map(shots.get(shot_id))
        internal = _internal(shot)
        kinds = _track_kinds(internal)
        clips = [_map(c) for c in _list(internal.get("clips")) if isinstance(c, Mapping)]
        words, source = _words_for_occurrence(shot, start, clips, kinds)
        onsets = [s for s, _e, _t in words]
        events: list[tuple[float, int, str]] = []  # (time, order, text)
        for row in table["rows"]:
            if row["occurrence_id"] != occurrence_id:
                continue
            delta = ""
            if onsets:
                nearest = min(onsets, key=lambda onset: abs(onset - row["start"]))
                gap = row["start"] - nearest
                said = next((t for s, _e, t in words if s == nearest and t), None)
                delta = (
                    f"  Δ{gap:+.2f}s" + (f" to {said!r}" if said else "")
                    if abs(gap) <= 0.6 else "  (no word onset within 0.6 s)"
                )
            picture = row.get("asset") or row.get("type") or "-"
            events.append((row["start"], 0, f"┃ CUT {row['index']}  {row.get('clip_id') or '(no picture clip)'} · {picture}{delta}"))
        for clip in clips:
            if _is_audio(clip, kinds) or clip_duration(clip) <= 0:
                continue
            c_start = start + _num(clip.get("at"))
            c_end = c_start + clip_duration(clip)
            label = str(clip.get("clipType") or "media")
            if isinstance(clip.get("asset"), str):
                label += f" {clip['asset']}"
            text = _clip_text(clip)
            if text:
                label += f' "{text}"'
            is_cut = (occurrence_id, round(c_start, 4)) in cut_rows and cut_rows[(occurrence_id, round(c_start, 4))].get("clip_id") == clip.get("id")
            if not is_cut:
                events.append((c_start, 1, f"+ {label}"))
                if c_end < end - 0.5 / fps and not any(abs(c_end - r["start"]) < 0.5 / fps for r in table["rows"] if r["occurrence_id"] == occurrence_id):
                    events.append((c_end, 1, f"− {label}"))
            for at, what in clip_keyframes(clip, c_start, fps):
                if at > c_start + 0.5 / fps:
                    events.append((at, 2, f"◆ {clip.get('clipType')} {what}"))
        # Phrases: consecutive words not split by a pause or an event.
        event_times = sorted({round(t, 4) for t, _o, _x in events})
        phrases: list[tuple[float, float, list[str]]] = []
        for s, e, t in words:
            split = (
                not phrases
                or s - phrases[-1][1] >= pause
                or any(phrases[-1][1] - 1e-6 <= et <= s + 1e-6 for et in event_times)
            )
            token = t or "·"
            if split:
                phrases.append((s, e, [token]))
            else:
                phrases[-1] = (phrases[-1][0], e, phrases[-1][2] + [token])
        block = [f"{_shot_name(shot, shot_id)} · {start:.2f}–{end:.2f} s · words: {source}"]
        merged = [(t, o, x) for t, o, x in events] + [(s, 3, f'│ "{" ".join(ws)}"  [{s:.2f}–{e:.2f}]') for s, e, ws in phrases]
        previous_end = None
        for at, _order, text in sorted(merged, key=lambda item: (item[0], item[1])):
            if not _in_window(at, at + 1e-3, window):
                continue
            if text.startswith("│") and previous_end is not None and at - previous_end >= 1.0:
                block.append(f"{'':>8} │   … {at - previous_end:.2f} s without speech")
            if text.startswith("│"):
                previous_end = float(text.rsplit("–", 1)[1].rstrip("]"))
            entry = f"{at:8.2f} {text}"
            block.append(entry if len(entry) <= width else entry[: width - 1] + "…")
        if len(block) > 1:
            lines.extend([""] + block)
    return "\n".join(lines)


# -------------------------------------------------------------------- code

def _py(value: Any, budget: int = 48) -> str:
    if isinstance(value, str):
        text = " ".join(value.split())
        return json.dumps(text if len(text) <= budget else text[: budget - 1] + "…", ensure_ascii=False)
    if isinstance(value, bool):
        return "True" if value else "False"
    if isinstance(value, (int, float)):
        return f"{value:g}" if isinstance(value, float) else str(value)
    if isinstance(value, list):
        if value and all(isinstance(item, list) and len(item) >= 2 and all(isinstance(x, (int, float)) for x in item[:2]) for item in value):
            return f"<{len(value)} timed words>"
        if len(value) > 4 or any(isinstance(item, (Mapping, list)) for item in value):
            return f"<list[{len(value)}]>"
        return "[" + ", ".join(_py(item, 16) for item in value) + "]"
    if isinstance(value, Mapping):
        if set(value) <= {"x", "y"} and all(isinstance(v, (int, float)) for v in value.values()):
            return f"({_py(value.get('x'))}, {_py(value.get('y'))})"
        inner = ", ".join(f"{k}={_py(v, 20)}" for k, v in list(value.items())[:4])
        return "{" + inner + (", …" if len(value) > 4 else "") + "}"
    return repr(value)


def _call(clip: Mapping[str, Any]) -> str:
    kind = str(clip.get("clipType") or "media").replace("-", "_")
    args = []
    if isinstance(clip.get("asset"), str):
        args.append(f"asset={json.dumps(clip['asset'])}")
    for key, value in _map(clip.get("params")).items():
        args.append(f"{key}={_py(value)}")
    if clip.get("clipType") == "media" and clip.get("to") is not None:
        args.append(f"src={_num(clip.get('from')):.2f}..{_num(clip.get('to')):.2f}")
    return f"{kind}({', '.join(args)})"


def render_code(
    bundle: Mapping[str, Any],
    *,
    window: Sequence[float] | None = None,
    shot_ids: set[str] | None = None,
    width: int = 160,
    header: str = "",
) -> str:
    """The composition as a readable program: shot → cut → element calls with resolved keyframes."""
    fps = _fps(bundle)
    table = build_cut_table(bundle)
    shots = _map(bundle.get("shots"))
    used: set[str] = set()
    body: list[str] = []
    for placement in _placements(bundle):
        shot_id = str(placement.get("shot_id") or "")
        if shot_ids and shot_id not in shot_ids:
            continue
        occurrence_id = str(placement.get("occurrence_id") or shot_id)
        start = _num(placement.get("at_ms", _map(placement.get("placement")).get("start_ms"))) / 1000.0
        length = _num(placement.get("duration_ms")) / 1000.0
        if not _in_window(start, start + length, window):
            continue
        shot = _map(shots.get(shot_id))
        internal = _internal(shot)
        kinds = _track_kinds(internal)
        clips = [_map(c) for c in _list(internal.get("clips")) if isinstance(c, Mapping)]
        words, _source = _words_for_occurrence(shot, start, clips, kinds)
        rows = [row for row in table["rows"] if row["occurrence_id"] == occurrence_id]
        body.append("")
        body.append(f'with shot({json.dumps(_shot_name(shot, shot_id), ensure_ascii=False)}, id="{shot_id}", at={start:.2f}, dur={length:.2f}):')
        for clip in sorted((c for c in clips if _is_audio(c, kinds)), key=lambda c: _num(c.get("at"))):
            c_start = start + _num(clip.get("at"))
            c_end = c_start + clip_duration(clip)
            if not _in_window(c_start, c_end, window):
                continue
            said = " ".join(t for s, _e, t in words if t and c_start - 1e-6 <= s < c_end)
            line = f"    {clip.get('track')}: {_call(clip)}  @ {c_start:.2f}–{c_end:.2f}"
            body.append(line + (f'  # "{said[:60]}{"…" if len(said) > 60 else ""}"' if said else ""))
        # Painter's order inside a cut: bottom track first (the shot lists tracks top -> bottom).
        depth = {str(_map(t).get("id")): -i for i, t in enumerate(_list(internal.get("tracks")))}
        visual = sorted(
            (c for c in clips if not _is_audio(c, kinds) and clip_duration(c) > 0),
            key=lambda c: (_num(c.get("at")), depth.get(str(c.get("track")), 0)),
        )
        for row in rows:
            if not _in_window(row["start"], row["end"], window):
                continue
            said = row.get("say") or ""
            body.append(
                f"    with cut({row['index']}, at={row['start']:.2f}, dur={row['duration']:.2f}):  # {row['tc_in']}"
                + (f' "{said[:70]}{"…" if len(said) > 70 else ""}"' if said else "")
            )
            members = [
                c for c in visual
                if row["start"] - 0.5 / fps <= start + _num(c.get("at")) < row["end"] - 0.5 / fps
            ]
            if not members:
                body.append("        pass  # layers continue from earlier cuts")
            for clip in members:
                used.add(str(clip.get("clipType") or "media"))
                c_start = start + _num(clip.get("at"))
                c_end = c_start + clip_duration(clip)
                timing = f"+{c_start - row['start']:.2f}" if c_start > row["start"] + 0.5 / fps else ""
                span = "" if abs(c_end - row["end"]) < 0.5 / fps else f" until {c_end:.2f}"
                line = f"        {str(clip.get('track')):<6} = {_call(clip)}"
                suffix = "  # " + " ".join(part for part in (f"enters {timing}" if timing else "", span.strip(), f"id {clip.get('id')}") if part)
                text = line + suffix
                body.append(text if len(text) <= width else line[: width - len(suffix) - 1] + "…" + suffix)
                for at, what in clip_keyframes(clip, c_start, fps):
                    body.append(f"            # ◆ {at:.2f} {what}")
    legend = [header] if header else []
    legend.append(f"# {fps:g} fps · times are timeline seconds · ◆ = element keyframe (clip frame → timeline seconds)")
    legend.append("# element sources (props are each element's schema in element.yaml beside it):")
    for name in sorted(used):
        legend.append(f"#   {name:<14} {element_source(name) or '(not in this checkout)'}")
    return "\n".join(legend + body)
