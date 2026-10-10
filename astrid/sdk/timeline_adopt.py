"""Adopt another timeline's head: make a working copy equal to it, keeping the target's identity.

A finished cut is often made on a duplicate (round2) and has to become the film. A sheet cannot
carry that (it brings no narration, no new cuts, no assets the film has never seen). Adopting
copies the WHOLE document onto the film's working copy:

- each source shot's content lands on the film's shot in the same place (the film keeps its shot
  and occurrence ids, so publishing makes a new revision of the same shots); extra source shots
  become new shots, film shots with no counterpart are removed;
- the internal timelines whole: every clip with its intent (moments, formulas, sequences, cut ids,
  layers, why), the narration takes and their words, the registry (assets by media digest, with
  the music's beat grid on its clip);
- the film settings: chapters, slots and their marks, theme, tracks.

The target's base (what it was checked out from) is untouched, so check, status, diff and the
three-way publish guard work as for any edit. The film keeps its own narration binding; publish
re-pins it with the adopted script text.

Pure: ``adopt_content`` takes and returns plain dicts.
"""
from __future__ import annotations

import copy
import uuid
from collections.abc import Mapping
from typing import Any

from astrid.sdk.timeline_checkout import Checkout, TimelineEditError, describe_changes
from astrid.sdk.timeline_duplicate import _remap_values, id_map
from astrid.sdk.timeline_editing import add_authoring_shot

__all__ = ["adopt_content", "adopt_summary"]

# Payload fields bound to the target shot's own runtime records: the target keeps its own (publish then
# re-pins the narration on the film's own binding, at its current head, because the text differs).
_TARGET_BOUND_PAYLOAD_KEYS = ("internal_timeline_revision_id", "text_bindings")


def _rows(bundle: Mapping[str, Any]) -> list[dict[str, Any]]:
    return sorted((dict(r) for r in bundle.get("placements") or []),
                  key=lambda r: (int((r.get("placement") or {}).get("start_ms") or 0), str(r.get("shot_id"))))


def adopt_content(source: Mapping[str, Any], target: Mapping[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """``target`` (identity, base and provenance kept) holding ``source``'s content. Returns ``(bundle, notes)``."""
    if source.get("project_id") and target.get("project_id") and source["project_id"] != target["project_id"]:
        raise TimelineEditError(f"adopt works inside one project (its media is shared): {source.get('timeline_id')} is in "
                                f"{source['project_id']}, the film in {target['project_id']}")
    notes: list[str] = []
    src_rows, dst_rows = _rows(source), _rows(target)
    pairs = list(zip(src_rows, dst_rows))
    shot_map = {str(s["shot_id"]): str(d["shot_id"]) for s, d in pairs}
    occ_map = {str(s["occurrence_id"]): str(d["occurrence_id"]) for s, d in pairs}
    extra = src_rows[len(pairs):]
    if extra:  # more shots in the source: new ids, never the source's (shot ids are global per project)
        slug = f"{target.get('timeline_id') or 'film'}-adopt-{uuid.uuid4().hex[:6]}"
        shot_map.update(id_map([str(r["shot_id"]) for r in extra], slug))
        occ_map.update(id_map([str(r["occurrence_id"]) for r in extra], f"occ-{slug}"))
        notes.append(f"{len(extra)} shot(s) more than the film: added as new shots")
    dropped = dst_rows[len(pairs):]
    if dropped:
        notes.append(f"{len(dropped)} film shot(s) with no counterpart removed: {', '.join(str(r['shot_id']) for r in dropped)}")
    ids = {**shot_map, **occ_map}

    out = copy.deepcopy(dict(target))
    src_parent = source.get("parent") or {}
    out["parent"] = {
        **dict(out.get("parent") or {}),
        "config": copy.deepcopy(dict(src_parent.get("config") or {})),
        "registry": _remap_values(copy.deepcopy(src_parent.get("registry") or {}), ids),
        "clips": _remap_values(copy.deepcopy(src_parent.get("clips") or []), ids),
    }
    shots = {sid: shot for sid, shot in (out.get("shots") or {}).items() if sid not in {str(r["shot_id"]) for r in dropped}}
    out["shots"] = shots
    out["placements"] = []
    mapping = out.setdefault("source_mapping", {})
    mapping["placements"] = {}
    for row in src_rows:
        old_shot, old_occ = str(row["shot_id"]), str(row["occurrence_id"])
        new_shot, new_occ = shot_map[old_shot], occ_map[old_occ]
        src_shot = (source.get("shots") or {}).get(old_shot)
        if not isinstance(src_shot, Mapping):
            raise TimelineEditError(f"{source.get('timeline_id')}: shot {old_shot!r} has no content")
        if new_shot not in shots:
            add_authoring_shot(out, shot_id=new_shot, occurrence_id=new_occ,
                               start_ms=(row.get("placement") or {}).get("start_ms", 0))
            out["placements"].pop()  # the row is written below, from the source's
        shot = out["shots"][new_shot]
        payload = copy.deepcopy(dict(src_shot.get("payload") or src_shot.get("base_payload") or {}))
        own = dict(shot.get("payload") or {})
        for key in _TARGET_BOUND_PAYLOAD_KEYS:
            if key in own:
                payload[key] = own[key]
            else:
                payload.pop(key, None)
        shot["payload"] = payload
        shot["internal_timeline"] = _remap_values(copy.deepcopy(dict(src_shot.get("internal_timeline") or {})), shot_map)
        new_row = _remap_values(copy.deepcopy(row), ids)
        new_row["shot_id"], new_row["occurrence_id"] = new_shot, new_occ
        out["placements"].append(new_row)
        mapping["placements"][new_occ] = copy.deepcopy(new_row)
    return out, notes


def adopt_summary(before: Checkout, after: Checkout, source_name: str) -> list[str]:
    """What adopting changed against the film's head: length, cuts, narration lines, clips."""
    def lines(tl: Checkout) -> dict[str, str]:
        out: dict[str, str] = {}
        for w in tl.words():
            out[w.segment] = (out.get(w.segment, "") + " " + w.text).strip()
        return out

    old_cuts = [g["id"] for g in before._cut_groups()]
    new_cuts = [g["id"] for g in after._cut_groups()]
    added = [c for c in new_cuts if c not in old_cuts]
    removed = [c for c in old_cuts if c not in new_cuts]
    old_lines, new_lines = lines(before), lines(after)
    said = [f"adopted {source_name}: the film is now {after.duration:.2f} s (was {before.duration:.2f} s), "
            f"{len(new_cuts)} cuts (was {len(old_cuts)})"
            + (f" showing {len(after.cuts)} pictures (a sequence's steps are pictures of one cut)"
               if len(after.cuts) != len(new_cuts) else "")
            + f", {len(after.orphans())} orphans"]
    if added or removed:
        said.append("  cuts " + " · ".join(x for x in (f"added {', '.join(added)}" if added else "",
                                                      f"removed {', '.join(removed)}" if removed else "") if x))
    line_bits = []
    if set(new_lines) - set(old_lines):
        line_bits.append(f"added {', '.join(sorted(set(new_lines) - set(old_lines)))}")
    if set(old_lines) - set(new_lines):
        line_bits.append(f"removed {', '.join(sorted(set(old_lines) - set(new_lines)))}")
    reworded = sorted(k for k in set(old_lines) & set(new_lines) if old_lines[k] != new_lines[k])
    if reworded:
        line_bits.append(f"reworded {', '.join(reworded)}")
    said.append("  narration " + (" · ".join(line_bits) if line_bits else "unchanged") + f" ({len(new_lines)} lines)")
    changes = describe_changes(before, after)
    said.append(f"  {len(changes)} change line(s) against the film's head (timelines status TL --all lists them)")
    return said
