"""Bounded semantic feedback for the existing authoring publication receipt."""

from __future__ import annotations

import shlex
from collections.abc import Mapping
from typing import Any

_TIMING_FIELDS = ("at", "from", "to", "hold", "speed", "duration", "duration_ms")


def _selection(clip, timeline):
    # Share the compiler's selector/immutable-identity authority. This module is
    # called only after compilation has validated the complete candidate.
    from .authoring_bundle import AuthoringBundleError, _clip_selected_media

    if not any(key in clip for key in ("asset", "asset_id", "media_id", "object_id")):
        return None
    try:
        identity = _clip_selected_media(clip, timeline, "publication feedback clip")
    except AuthoringBundleError:
        # Parameterized/unsupported summary shapes remain ordinary authored
        # changes; feedback must not guess a selected image from a filename.
        return None
    assets = timeline.get("registry", {}).get("assets", {})
    entry = assets.get(clip.get("asset", clip.get("asset_id")), {})
    if not entry:
        entry = next((value for value in assets.values() if isinstance(value, Mapping) and
                      identity.removeprefix("sha256:") in
                      [str(value.get(key, "")).removeprefix("sha256:")
                       for key in ("media_id", "object_id", "digest", "content_digest")]), {})
    kind = "media"
    for field in ("type", "media_type", "mime_type", "kind"):
        declared = str(entry.get(field, "")).split("/", 1)[0]
        if declared in ("image", "audio", "video"):
            kind = declared
            break
    return identity, kind


def _clips(timeline):
    return {clip["id"]: clip for clip in timeline.get("clips", [])
            if isinstance(clip, Mapping) and clip.get("id")}


def _text(clip):
    params = clip.get("params")
    return clip.get("text"), params.get("text") if isinstance(params, Mapping) else None


def _placement_timing(row):
    return (row.get("at_ms", row.get("placement", {}).get("start_ms")),
            row.get("duration_ms"), row.get("source_offset"), row.get("speed"))


def authoring_change_summary(candidate: Mapping[str, Any], *, media_imports=None) -> dict[str, Any]:
    """Compare stable placed identities; never count payload/registry mirrors."""
    from .authoring_bundle import _effective_parent_assets, diff_authoring_candidate
    from .authoring_feedback_residual import residual_authoring_changes

    selections = {kind: {"updated": 0, "replaced": 0, "added": 0, "removed": 0}
                  for kind in ("image", "audio", "video", "media")}
    timing = {"clips": 0, "occurrences": 0}
    text_changes = 0

    def compare(before, after):
        nonlocal text_changes
        old, new = _clips(before), _clips(after)
        for clip_id in old.keys() | new.keys():
            previous, current = old.get(clip_id), new.get(clip_id)
            old_selection = _selection(previous, before) if previous else None
            new_selection = _selection(current, after) if current else None
            if old_selection != new_selection:
                if old_selection and new_selection and old_selection[0] == new_selection[0]:
                    pass  # Same immutable media, different alias/metadata.
                elif old_selection and new_selection and old_selection[1] == new_selection[1]:
                    selections[new_selection[1]]["replaced"] += 1
                else:
                    if old_selection:
                        selections[old_selection[1]]["removed"] += 1
                    if new_selection:
                        selections[new_selection[1]]["added"] += 1
            if previous and current:
                if any(previous.get(key) != current.get(key) for key in _TIMING_FIELDS):
                    timing["clips"] += 1
                if _text(previous) != _text(current):
                    text_changes += 1

    # Parent config and top-level clips can mirror one another. The root clips
    # are authoritative when present; fall back to config for older payloads.
    def parent_timeline(payload):
        return {"clips": payload.get("clips", payload.get("config", {}).get("clips", [])),
                "registry": {"assets": _effective_parent_assets(payload)}}

    compare(parent_timeline(candidate["base_parent_payload"]), parent_timeline(candidate["parent"]))
    base_rows = candidate["base_placements"]
    current_ids = {row["occurrence_id"] for row in candidate["placements"]}
    occurrence_changes = {
        "added": len(current_ids - base_rows.keys()),
        "removed": len(base_rows.keys() - current_ids),
    }
    unavailable_selection_baselines = []
    for row in candidate["placements"]:
        shot = candidate["shots"][row["shot_id"]]
        base_row = base_rows.get(row["occurrence_id"])
        if base_row:
            base_shot = candidate["shots"].get(base_row["shot_id"], {})
            if not base_shot:
                unavailable_selection_baselines.append(row["occurrence_id"])
            else:
                compare(base_shot["base_internal_timeline"], shot["internal_timeline"])
            if _placement_timing(base_row) != _placement_timing(row):
                timing["occurrences"] += 1
            if base_shot and base_shot["base_payload"].get("text_bindings") != shot["payload"].get("text_bindings"):
                text_changes += 1
        else:
            compare({}, shot["internal_timeline"])
    # Removed occurrences retain their pinned shot identity in base rows. If an
    # editor also removed that shot from the bundle, do not invent clip counts.
    for occurrence_id, row in base_rows.items():
        if occurrence_id not in current_ids:
            base_shot = candidate["shots"].get(row["shot_id"])
            if base_shot:
                compare(base_shot["base_internal_timeline"], {})
            else:
                unavailable_selection_baselines.append(occurrence_id)
    for counts in selections.values():
        counts["updated"] = counts["replaced"] + counts["added"]
    imports = None if media_imports is None else {
        "imported": sum(receipt.get("reused") is not True for receipt in media_imports),
        "reused": sum(receipt.get("reused") is True for receipt in media_imports),
    }
    return {"media_selections": selections, "timing_changes": timing,
            "occurrence_changes": occurrence_changes,
            "unavailable_selection_baselines": unavailable_selection_baselines,
            "text_changes": text_changes, "catalog_media": imports,
            **residual_authoring_changes(candidate),
            "authored_change_count": diff_authoring_candidate(candidate)["change_count"]}


def publication_feedback(summary: Mapping[str, Any], publication: Mapping[str, Any],
                         *, project_id: str, timeline_id: str) -> dict[str, Any]:
    """Attach actual saved heads and supported, shell-quoted scoped actions."""
    summary = dict(summary)
    publication = publication if isinstance(publication, Mapping) else {}
    publication = publication.get("data", publication)
    publication = publication if isinstance(publication, Mapping) else {}
    summary["new_head"] = next((publication[key] for key in
                                ("new_head", "parent_revision_id", "revision_id")
                                if isinstance(publication.get(key), str) and publication[key]), None)
    summary["old_head"] = publication.get("old_head") if isinstance(publication.get("old_head"), str) else None
    statements = []

    def sentence(verb, count, singular):
        if count:
            statements.append(f"{verb} {count} {singular}{'' if count == 1 else 's'}.")

    for kind, counts in summary["media_selections"].items():
        noun = "media selection" if kind == "media" else f"{kind} selection"
        sentence("Updated", counts["updated"], noun)
        sentence("Removed", counts["removed"], noun)
    for subject, count in summary["timing_changes"].items():
        if count:
            statements.append(f"Adjusted timing on {count} {subject[:-1] if count == 1 else subject}.")
    sentence("Updated", summary["text_changes"], "text binding or clip text")
    sentence("Added", summary["occurrence_changes"]["added"], "shot occurrence")
    sentence("Removed", summary["occurrence_changes"]["removed"], "shot occurrence")
    if summary["unavailable_selection_baselines"]:
        statements.append("Selection counts are partial: a prior shot baseline is unavailable.")
    if summary["other_properties_changed"]:
        statements.append("Other properties changed.")
    elif summary["media_bookkeeping_changed"]:
        statements.append("Updated media bookkeeping.")
    if not statements:
        statements.append("Saved timeline changes." if summary["authored_change_count"] else "No authored changes.")
    imports = summary["catalog_media"]
    if imports is not None:
        if not imports["imported"] and not imports["reused"]:
            statements.append("No catalog media imports.")
        sentence("Imported", imports["imported"], "catalog media item")
        sentence("Reused", imports["reused"], "catalog media item")
    if summary["new_head"]:
        statements.append(f"Saved revision {summary['new_head']}.")
    else:
        statements.append("Publication completed; no saved revision was returned.")
    prefix = ["python3", "-m", "astrid", "timelines"]
    actions = [
        {"command": shlex.join(prefix + ["show", timeline_id, "--project", project_id, "--json"]),
         "scope": "current_head", "description": "Use timelines show to view the timeline (current head)."},
        {"command": shlex.join(prefix + ["visualize", timeline_id, "--project", project_id, "--mode", "inputs"]),
         "scope": "current_head", "description": "Use timelines visualize to visualize its declared layout (current head; input diagram)."},
    ]
    if summary["old_head"] and summary["new_head"]:
        saved_actions = [{
            "command": shlex.join(prefix + ["diff", timeline_id, "--project", project_id,
                                           "--from-revision", summary["old_head"],
                                           "--to-revision", summary["new_head"], "--format", mode]),
            "scope": "saved_revision_pair",
            "format": mode,
            "description": description,
            "from_revision": summary["old_head"], "to_revision": summary["new_head"],
        } for mode, description in (
            ("readable", "Read exactly what this saved batch changed, grouped by shot and clip."),
            ("details", "View every saved change, including full revision/hash/reference details (use --json for machine data)."),
        )]
        actions = saved_actions + actions
    update = " ".join(statements) + "\n" + "\n".join(
        f"{action['description']}\n{action['command']}" for action in actions
    )
    return {"update": update, "summary": summary, "next_actions": actions}
