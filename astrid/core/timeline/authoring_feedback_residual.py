"""Conservative residual coverage for the existing publication summary.

Only established selector and selected-media mirror fields are consumed. Unknown
properties remain visible through a boolean fallback, never a guessed edit count.
"""

from collections.abc import Mapping
from copy import deepcopy

_MEDIA_KEYS = ("asset", "asset_id", "media_id", "object_id")
_DESCRIPTOR_KEYS = {
    "media_id", "object_id", "digest", "content_digest", "type", "media_type", "mime_type",
    "width", "height", "duration", "duration_seconds", "filename", "size", "byte_size",
}


def residual_authoring_changes(candidate):
    from .authoring_bundle import _effective_parent_assets
    from .authoring_feedback import _TIMING_FIELDS, _clips, _selection

    other = False
    bookkeeping = False

    def compare_timeline(before, after):
        nonlocal other, bookkeeping
        old_clips, new_clips = _clips(before), _clips(after)
        old_selected = {choice[0] for clip in old_clips.values()
                        if (choice := _selection(clip, before))}
        new_selected = {choice[0] for clip in new_clips.values()
                        if (choice := _selection(clip, after))}
        old_order = [clip["id"] for clip in before.get("clips", []) if clip["id"] in new_clips]
        new_order = [clip["id"] for clip in after.get("clips", []) if clip["id"] in old_clips]
        other |= old_order != new_order
        for clip_id in old_clips.keys() | new_clips.keys():
            old, new = old_clips.get(clip_id), new_clips.get(clip_id)
            old_choice = _selection(old, before) if old else None
            new_choice = _selection(new, after) if new else None
            if old is None or new is None:
                # A selected media clip's creation/removal is already one
                # semantic selection change; its initial fields are that clip.
                other |= not bool(old_choice or new_choice)
                continue
            left, right = deepcopy(old), deepcopy(new)
            if old_choice and new_choice:
                bookkeeping |= old_choice[0] == new_choice[0] and any(left.get(key) != right.get(key) for key in _MEDIA_KEYS)
                for key in _MEDIA_KEYS:
                    left.pop(key, None)
                    right.pop(key, None)
            for key in _TIMING_FIELDS + ("text",):
                left.pop(key, None)
                right.pop(key, None)
            for clip in (left, right):
                if isinstance(clip.get("params"), Mapping):
                    clip["params"].pop("text", None)
                    if not clip["params"]:
                        clip.pop("params", None)
            other |= left != right

        def registry(value, selected):
            nonlocal bookkeeping
            result = deepcopy(value.get("registry", {}))
            assets = result.get("assets", {})
            selected_metadata = {}
            for key, descriptor in list(assets.items()):
                choice = _selection({"asset": key}, value)
                if choice and choice[0] in selected:
                    for field in _DESCRIPTOR_KEYS:
                        descriptor.pop(field, None)
                    if not descriptor:
                        del assets[key]
                    else:
                        # Equivalent aliases of one selected object do not
                        # multiply identical metadata. Divergent opaque
                        # properties still remain visible as residual data.
                        old_alias = next((asset_key for asset_key in before.get("registry", {}).get("assets", {})
                                          if (previous := _selection({"asset": asset_key}, before))
                                          and previous[0] == choice[0]), key)
                        metadata = selected_metadata.setdefault(old_alias, [])
                        if descriptor not in metadata:
                            metadata.append(descriptor)
                        del assets[key]
            if selected_metadata:
                result["selected_metadata"] = selected_metadata
            if not assets:
                result.pop("assets", None)
            return result

        old_registry, new_registry = registry(before, old_selected), registry(after, new_selected)
        other |= old_registry != new_registry
        bookkeeping |= old_selected == new_selected and before.get("registry", {}) != after.get("registry", {}) and old_registry == new_registry
        left, right = deepcopy(before), deepcopy(after)
        for value in (left, right):
            value.pop("clips", None)
            value.pop("registry", None)
        # Explicit asset descriptors are selected-media mirrors; generation
        # inputs and arbitrary extension maps are intentionally never stripped.
        for value, selected in ((left, old_selected), (right, new_selected)):
            value["assets"] = descriptors(value.get("assets", []), selected)
            if not value["assets"]:
                value.pop("assets", None)
        other |= left != right
        return old_selected, new_selected

    def descriptors(rows, selected):
        result = deepcopy(rows)
        values = result.values() if isinstance(result, Mapping) else result
        for row in values:
            if not isinstance(row, dict):
                continue
            identity = str(row.get("media_id", row.get("object_id", "")))
            identity = "sha256:" + identity.removeprefix("sha256:")
            if identity in selected:
                for field in _DESCRIPTOR_KEYS:
                    row.pop(field, None)
        # Empty descriptors are bookkeeping, but retain all opaque attributes.
        if isinstance(result, Mapping):
            return {key: row for key, row in result.items() if row}
        return [row for row in result if row]

    def payload(value, selected, paired_items):
        result = deepcopy(value)
        result.pop("text_bindings", None)
        result.pop("internal_timeline_revision_id", None)
        for key in ("items", "audio_bindings", "assets"):
            rows = result.get(key, [])
            if key == "items":
                rows = deepcopy(rows)
                for row in rows:
                    identity = "sha256:" + str(row.get("media_id", "")).removeprefix("sha256:")
                    if identity in selected and row.get("item_id", row.get("id")) in paired_items:
                        row.pop("media_id", None)
            else:
                rows = descriptors(rows, selected)
            if rows:
                result[key] = rows
            else:
                result.pop(key, None)
        return result

    def parent(value):
        result = deepcopy(value)
        config = result.get("config", {})
        # The compiler treats the root/config clip copies and effective asset
        # namespace as one authority. Only proven identical copies are removed.
        if result.get("clips") == config.get("clips"):
            config.pop("clips", None)
        if "registry" in config and config["registry"].get("assets", {}) == _effective_parent_assets(value):
            config["registry"].pop("assets", None)
            if not config["registry"]:
                config.pop("registry", None)
        result["registry"] = {**result.get("registry", {}), "assets": _effective_parent_assets(value)}
        return result

    compare_timeline(parent(candidate["base_parent_payload"]), parent(candidate["parent"]))
    for row in candidate["placements"]:
        base_row = candidate["base_placements"].get(row["occurrence_id"])
        shot = candidate["shots"][row["shot_id"]]
        if not base_row:
            # A newly duplicated shot retains its local creation baseline;
            # edits after creation must not hide behind the addition count.
            base_shot = shot
            base_row = candidate["source_mapping"]["placements"].get(row["occurrence_id"])
        else:
            base_shot = candidate["shots"].get(base_row["shot_id"])
        if not base_shot:
            other = True  # Cannot assert complete residual coverage.
            continue
        old_selected, new_selected = compare_timeline(
            base_shot["base_internal_timeline"], shot["internal_timeline"]
        )
        selected = old_selected | new_selected
        def selected_items(value):
            return {item.get("item_id", item.get("id")) for item in value.get("items", [])
                    if "sha256:" + str(item.get("media_id", "")).removeprefix("sha256:") in selected}
        paired_items = selected_items(base_shot["base_payload"]) & selected_items(shot["payload"])
        other |= payload(base_shot["base_payload"], selected, paired_items) != payload(shot["payload"], selected, paired_items)
        if base_row is None:
            continue
        left, right = deepcopy(base_row), deepcopy(row)
        for value in (left, right):
            for key in ("at_ms", "duration_ms", "source_offset", "speed"):
                value.pop(key, None)
            value.get("placement", {}).pop("start_ms", None)
        other |= left != right
    old_order = [key for key in candidate["base_placements"]
                 if key in {row["occurrence_id"] for row in candidate["placements"]}]
    new_order = [row["occurrence_id"] for row in candidate["placements"]
                 if row["occurrence_id"] in candidate["base_placements"]]
    other |= old_order != new_order
    placed_shots = {row["shot_id"] for row in candidate["placements"]}
    for shot_id, shot in candidate["shots"].items():
        if shot_id not in placed_shots:
            other |= shot["payload"] != shot["base_payload"] or shot["internal_timeline"] != shot["base_internal_timeline"]
    return {"other_properties_changed": bool(other), "media_bookkeeping_changed": bool(bookkeeping)}
