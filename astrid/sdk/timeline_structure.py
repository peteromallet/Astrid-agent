"""Chapters as labels: lay a timeline out as ONE authoring shot for the whole film.

A chapter used to be a container (one authoring shot per chapter). That made a clip
stop at every chapter wall (a sprite that stays on screen had to be duplicated, and
re-played its entrance at the wall), split the music into one clip per chapter, gave
every clip a second clock (shot-relative ``at``), and kept a cut from moving across a
wall. With one shot that starts at 0, a clip's ``at`` *is* timeline seconds, a clip can
carry across any cut, the music is one clip, and a chapter is just a label that starts
at a cut (``parent.config.chapters``, see ``timeline_intent.chapters``).

Pure: these functions change a checkout's bundle in memory; publishing is the usual
``Checkout.publish`` (check, overwrite guard, narration binding).
"""
from __future__ import annotations

import copy
from collections.abc import Mapping
from typing import Any

from astrid.sdk import timeline_intent as intent
from astrid.sdk.timeline_editing import add_authoring_shot

__all__ = ["one_shot", "join_music", "carry_across", "label_chapters"]


def _r(value: float) -> float:
    return round(float(value), 6)


def one_shot(tl: Any, *, shot_id: str, name: str) -> list[dict[str, Any]]:
    """Move every clip of every placed shot into one new authoring shot (timeline seconds).

    Returns the chapter walls: ``[{"name": "06 ASTRID", "at": 91.5}, …]`` (one per old shot),
    for ``label_chapters`` to turn into labels once cuts have ids."""
    bundle = tl.bundle
    rows = sorted(bundle.get("placements") or [], key=lambda r: (r.get("placement") or {}).get("start_ms", r.get("at_ms", 0)))
    if not rows:
        return []
    walls: list[dict[str, Any]] = []
    tracks: list[dict[str, Any]] = []
    seen_tracks: set[str] = set()
    clips: list[dict[str, Any]] = []
    assets: dict[str, Any] = {}
    end = 0.0
    first_row = rows[0]
    for row in rows:
        sid = str(row["shot_id"])
        start = tl._shot_start(sid)
        end = max(end, start + float(row.get("duration_ms") or 0) / 1000.0)
        shot = bundle["shots"][sid]
        internal = shot.get("internal_timeline") or {}
        payload = shot.get("payload") or {}
        walls.append({"name": str(payload.get("name") or sid), "at": _r(start)})
        for track in internal.get("tracks") or []:
            if str(track.get("id")) not in seen_tracks:
                seen_tracks.add(str(track.get("id")))
                tracks.append(copy.deepcopy(dict(track)))
        for clip in internal.get("clips") or []:
            moved = copy.deepcopy(dict(clip))
            moved["at"] = _r(float(moved.get("at") or 0.0) + start)
            clips.append(moved)
        for key, entry in ((internal.get("registry") or {}).get("assets") or {}).items():
            assets.setdefault(key, copy.deepcopy(entry))
    occurrence = f"occ-{shot_id}"
    bundle["shots"] = {}
    bundle["placements"] = []
    bundle["source_mapping"] = {"placements": {}, "shots": {}}
    shot = add_authoring_shot(bundle, shot_id=shot_id, occurrence_id=occurrence, start_ms=0, name=name)
    row = bundle["placements"][-1]
    row["duration_ms"] = int(round(end * 1000))
    for key in ("track",):
        if key in first_row:
            row[key] = copy.deepcopy(first_row[key])
    bundle["source_mapping"]["placements"][occurrence] = copy.deepcopy(row)
    shot["internal_timeline"]["tracks"] = tracks
    shot["internal_timeline"]["clips"] = clips
    shot["internal_timeline"]["registry"] = {"assets": assets}
    shot["payload"]["name"] = name
    shot["payload"]["text_bindings"] = []  # bound again from the lines' text when published
    tl._mcache = None
    return walls


def join_music(tl: Any) -> list[str]:
    """Join music clips that play on from one another (same asset, contiguous in time and source)."""
    joined = []
    for sid in tl._shot_ids():
        rows = tl._internal(sid)["clips"]
        music = sorted((c for c in rows if str(c.get("track")) == "music" and "from" in c), key=lambda c: float(c.get("at") or 0))
        keep = []
        for clip in music:
            prev = keep[-1] if keep else None
            if (prev is not None and prev.get("asset") == clip.get("asset")
                    and abs(float(prev["at"]) + (float(prev["to"]) - float(prev["from"])) - float(clip["at"])) < 2e-3
                    and abs(float(prev["to"]) - float(clip["from"])) < 2e-3
                    and prev.get("volume") == clip.get("volume")):
                prev["to"] = clip["to"]
                beats_a, beats_b = (prev.get("app") or {}).get("beats"), (clip.get("app") or {}).get("beats")
                if isinstance(beats_a, dict) and isinstance(beats_b, dict):
                    for key in ("beats", "downbeats", "hits"):
                        merged = list(beats_a.get(key) or []) + [b for b in beats_b.get(key) or [] if b not in (beats_a.get(key) or [])]
                        beats_a[key] = sorted(merged, key=lambda b: b[0] if isinstance(b, (list, tuple)) else b)
                rows.remove(clip)
                joined.append(str(clip.get("id")))
            else:
                keep.append(clip)
    tl._mcache = None
    return joined


def _same_layer(a: Any, b: Any) -> bool:
    if a.track != b.track or a.element != b.element or (a.asset or None) != (b.asset or None) or a.is_audio:
        return False
    pa = {k: v for k, v in a.params.items() if k != "enter"}
    pb = {k: v for k, v in b.params.items() if k != "enter"}
    return pa == pb


def carry_across(tl: Any, walls: list[float]) -> list[str]:
    """Where a layer was duplicated at a chapter wall (same element, asset and look on both sides),
    keep one clip that carries across: no second entrance at the wall. Returns what was merged."""
    merged = []
    fps = tl.fps
    for wall in walls:
        ending = [c for c in tl.clips() if abs(c.end - wall) < 0.5 / fps and not c.is_audio]
        starting = [c for c in tl.clips() if abs(c.start - wall) < 0.5 / fps and not c.is_audio]
        for a in ending:
            b = next((c for c in starting if c.data is not a.data and _same_layer(a, c)), None)
            if b is None:
                continue
            spans = tl._cut_spans()
            b_cut = intent.cut_of(b.data)
            until, length = intent.until(b.data), intent.for_s(b.data)
            end = b.end
            a.data["hold"] = _r(end - a.start)
            if until:
                intent.set_until(a.data, until)
                intent.set_for(a.data, None)
            elif length is not None:
                intent.set_until(a.data, None)
                intent.set_for(a.data, _r(end - a.start))
            elif b_cut:
                order = list(spans)
                nxt = order[order.index(b_cut) + 1] if b_cut in order and order.index(b_cut) + 1 < len(order) else None
                intent.set_until(a.data, nxt)
                intent.set_for(a.data, None if nxt else _r(end - a.start))
            merged.append(f"{a.address} carries across {wall:.2f} s (was also {b.address}, which re-entered there)")
            b.remove()
            starting = [c for c in starting if c.data is not b.data]
    tl._mcache = None
    return merged


def label_chapters(tl: Any, walls: list[Mapping[str, Any]]) -> list[dict[str, str]]:
    """Turn chapter walls into labels: each chapter starts at the cut that opens at its wall."""
    table = []
    groups = tl._cut_groups()
    for wall in walls:
        opening = min(groups, key=lambda g: abs(g["start"] - float(wall["at"]))) if groups else None
        if opening is not None and abs(opening["start"] - float(wall["at"])) < 0.5 / tl.fps:
            table.append({"name": str(wall["name"]), "from": opening["id"]})
    intent.set_chapters(tl.bundle, table)
    return table
