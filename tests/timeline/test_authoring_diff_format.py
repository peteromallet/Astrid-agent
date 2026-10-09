from __future__ import annotations

import pytest

from astrid.core.timeline.authoring_diff_format import format_authoring_revision_diff


def _change(path, location, before=None, after=None, kind="changed", *, absent_before=False,
            absent_after=False):
    row = {"path": path, "location": location, "kind": kind}
    if not absent_before:
        row["before"] = before
    if not absent_after:
        row["after"] = after
    return row


def _result(changes, *, context=None, summary=None, update=None):
    return {
        "project_id": "project-1", "timeline_id": "main",
        "from_revision": "parent-1", "to_revision": "parent-2", "complete": True,
        "changes": changes, "raw_change_count": len(changes),
        "summary": summary or {
            "media_selections": {kind: {"updated": 0, "removed": 0} for kind in
                                 ("image", "audio", "video", "media")},
            "timing_changes": {"clips": 0, "occurrences": 0},
            "occurrence_changes": {"added": 0, "removed": 0},
            "text_changes": 0, "other_properties_changed": bool(changes),
            "media_bookkeeping_changed": False,
        },
        "update": update or "Compared parent-1 to parent-2: Other properties changed.",
        "context": context or {},
    }


def _clip(name="Picture", track="picture", selection=None):
    return {"name": name, "track": track, "selection": selection}


def _side(name="Opening", shot_id="shot-a", *, clips=None, tracks=None):
    return {"name": name, "shot_id": shot_id,
            "clips": clips or {}, "tracks": tracks or {"picture": {"name": "Visual", "kind": "visual", "id": "picture"}}}


def test_readable_groups_by_occurrence_and_clip_using_names_and_stable_ids():
    changes = [
        _change("shots.occ-1.internal_timeline.clips[id=picture-1].rect.x",
                ["shots", "occ-1", "internal_timeline", "clips", {"key": "id", "value": "picture-1"}, "rect", "x"],
                0.1, 0.8),
        _change("shots.occ-2.internal_timeline.clips[id=picture-1].rect.x",
                ["shots", "occ-2", "internal_timeline", "clips", {"key": "id", "value": "picture-1"}, "rect", "x"],
                0.1, 0.8),
    ]
    clips = {"picture-1": _clip(selection=["sha256:" + "a" * 64, "image"])}
    context = {"occurrences": {
        "occ-1": {"before": _side(clips=clips), "after": _side(clips=clips)},
        "occ-2": {"before": _side(shot_id="shot-b", clips=clips),
                   "after": _side(shot_id="shot-b", clips=clips)},
    }}

    text = format_authoring_revision_diff(_result(changes, context=context))

    assert "project-1 / main" in text and "parent-1 → parent-2" in text
    assert "Opening (shot-a) — occurrence occ-1, shot shot-a" in text
    assert "Opening (shot-b) — occurrence occ-2, shot shot-b" in text
    assert "Image clip Picture (picture-1) — track Visual (picture)" in text
    assert "Rectangle x (rect.x)" in text
    assert "shots.occ-1.internal_timeline" not in text


def test_media_selection_and_related_references_are_shown_without_binding_inference():
    old_id = "sha256:" + "a" * 64
    new_id = "sha256:" + "a" * 12 + "b" * 52
    changes = [
        _change("shots.occ-1.internal_timeline.registry.assets.picture.media_id",
                ["shots", "occ-1", "internal_timeline", "registry", "assets", "picture", "media_id"],
                old_id, new_id),
        _change("shots.occ-1.payload.items[item_id=item-1].media_id",
                ["shots", "occ-1", "payload", "items", {"key": "item_id", "value": "item-1"}, "media_id"],
                old_id, new_id),
    ]
    summary = _result(changes)["summary"]
    summary["media_selections"]["image"]["updated"] = 1
    summary["other_properties_changed"] = False
    summary["media_bookkeeping_changed"] = False
    context = {"occurrences": {"occ-1": {
        "before": _side(clips={"picture-1": _clip(selection=[old_id, "image"])}),
        "after": _side(clips={"picture-1": _clip(selection=[new_id, "image"])}),
    }}}

    text = format_authoring_revision_diff(_result(changes, context=context, summary=summary,
                                                   update="Compared parent-1 to parent-2: Updated 1 image selection."))

    assert "Image clip Picture (picture-1)" in text
    assert old_id in text and new_id in text  # Shared-prefix collision gets full hashes.
    references = text.split("Media reference data also changed:", 1)[1]
    assert old_id in references and new_id in references
    assert "Media reference data also changed" in text
    assert "asset picture" in text and "item item-1 (shot-level reference)" in text
    assert "no item-to-clip binding is assumed" in text


def test_details_include_each_raw_row_once_and_keep_missing_distinct_from_null():
    changes = [
        _change("shots.occ-1.payload.metadata.editor_note",
                ["shots", "occ-1", "payload", "metadata", "editor_note"],
                kind="added", after="first line\nsecond line", absent_before=True),
        _change("shots.occ-1.payload.metadata.explicit_null",
                ["shots", "occ-1", "payload", "metadata", "explicit_null"],
                False, None),
        _change("shots.occ-1.payload.metadata.object",
                ["shots", "occ-1", "payload", "metadata", "object"],
                {"before": [1, 2]}, {"after": {"deep": True}}),
    ]
    text = format_authoring_revision_diff(_result(changes), mode="details")
    raw_section = text.split("Complete changed-field details follow.", 1)[1]

    for row in changes:
        assert raw_section.count(row["path"]) == 1
    assert "before: <absent>" in raw_section
    assert 'location: ["shots", "occ-1", "payload", "metadata", "editor_note"]' in raw_section
    assert "after:  null" in raw_section
    assert '"first line\\nsecond line"' in raw_section
    assert '"deep": true' in raw_section


def test_only_exact_source_pin_paths_are_bookkeeping():
    changes = [
        _change("parent.revision_id", ["parent", "revision_id"], "parent-1", "parent-2"),
        _change("shots.occ-1.shot_record.content_digest",
                ["shots", "occ-1", "shot_record", "content_digest"], "old", "new"),
        _change("shots.occ-1.payload.metadata.content_digest",
                ["shots", "occ-1", "payload", "metadata", "content_digest"], "user-old", "user-new"),
    ]
    text = format_authoring_revision_diff(_result(changes), mode="details")

    assert "Revision/hash bookkeeping (2 raw entries)" in text
    assert "Authored property details (1 raw entry)" in text
    assert "metadata.content_digest" in text
    assert '"user-old"' in text and '"user-new"' in text


def test_unknown_media_fields_and_nested_clips_words_remain_authored():
    changes = [
        _change("shots.occ-1.payload.metadata.clips.0.media_id",
                ["shots", "occ-1", "payload", "metadata", "clips", 0, "media_id"],
                "old", "new"),
    ]
    text = format_authoring_revision_diff(_result(changes))

    assert "Other shot properties" in text
    assert "metadata.clips" in text
    assert "Media reference data also changed" not in text


def test_unknown_clip_nested_media_field_preserves_its_full_location_and_identity():
    selector = {"key": "id", "value": "picture-1"}
    nested = {"key": "item_id", "value": "custom.one[2]"}
    changes = [_change("shots.occ-1.internal_timeline.clips[id=picture-1].params.items[item_id=custom.one[2]].media_id",
                       ["shots", "occ-1", "internal_timeline", "clips", selector,
                        "params", "items", nested, "media_id"], "old", "new")]
    text = format_authoring_revision_diff(_result(changes))
    assert "params.items.[item_id=custom.one[2]].media_id" in text
    assert "Media reference data also changed" not in text


def test_readable_reorder_locator_is_labeled_as_order():
    changes = [_change("shots.occ-1.internal_timeline.clips.order",
                       ["shots", "occ-1", "internal_timeline", "clips", {"order": True}],
                       ["a", "b"], ["b", "a"], kind="reordered")]
    text = format_authoring_revision_diff(_result(changes))
    assert "clips.order" in text
    assert "key=?" not in text


def test_same_media_identity_with_changed_type_does_not_display_replacement():
    media = "sha256:" + "a" * 64
    changes = [_change("shots.occ-1.internal_timeline.registry.assets.picture.type",
                       ["shots", "occ-1", "internal_timeline", "registry", "assets", "picture", "type"],
                       "image", "media")]
    context = {"occurrences": {"occ-1": {
        "before": _side(clips={"picture-1": _clip(selection=[media, "image"])}),
        "after": _side(clips={"picture-1": _clip(selection=[media, "media"])}),
    }}}
    text = format_authoring_revision_diff(_result(changes, context=context))
    assert "registry.assets.picture.type" in text
    assert "Selected image:" not in text and "Selected media:" not in text


def test_unclassified_raw_property_prevents_false_no_authored_changes_fallback():
    changes = [
        _change("parent.revision_id", ["parent", "revision_id"], "parent-1", "parent-2"),
        _change("shots.occ-1.internal_timeline.registry.assets.picture.type",
                ["shots", "occ-1", "internal_timeline", "registry", "assets", "picture", "type"],
                "image", "media"),
    ]
    result = _result(changes, update="Compared parent-1 to parent-2: No authored changes.")
    result["summary"]["other_properties_changed"] = False
    text = format_authoring_revision_diff(result)
    assert "Other properties changed (listed below)." in text
    assert "No authored changes" not in text
    assert "registry.assets.picture.type" in text


def test_order_locator_and_unknown_property_are_visible_in_full_details():
    changes = [
        _change("shots.occ-1.internal_timeline.clips.order",
                ["shots", "occ-1", "internal_timeline", "clips", {"order": True}],
                ["a", "b"], ["b", "a"], kind="reordered"),
        _change("shots.occ-1.internal_timeline.effects.0.future_property",
                ["shots", "occ-1", "internal_timeline", "effects", 0, "future_property"],
                1, 2),
    ]
    text = format_authoring_revision_diff(_result(changes), mode="details")
    assert "clips.order" in text and "reordered" in text
    assert "effects.0.future_property" in text


def test_readable_mode_escapes_control_characters_in_context_labels():
    occurrence_id = "occ-1\nFORGED"
    clip_id = "pic].one\nFORGED"
    loc = ["shots", occurrence_id, "internal_timeline", "clips", {"key": "id", "value": clip_id}, "rect", "x"]
    changes = [_change("user supplied path\nFORGED", loc, 1, 2)]
    clips = {clip_id: _clip("Name\nFORGED")}
    context = {"occurrences": {occurrence_id: {
        "before": _side(name="Opening\nFORGED", clips=clips),
        "after": _side(name="Opening\nFORGED", clips=clips),
    }}}

    text = format_authoring_revision_diff(_result(changes, context=context))

    assert "\nFORGED —" not in text
    assert "Name\\nFORGED" in text
    assert "pic].one\\nFORGED" in text


def test_noop_and_bookkeeping_only_reports_are_honest():
    no_op = _result([], update="Compared parent-1 to parent-2: No authored changes.")
    assert "No authored changes." in format_authoring_revision_diff(no_op)
    assert "0 raw entries total" in format_authoring_revision_diff(no_op, mode="details")

    bookkeeping = _result([
        _change("parent.content_digest", ["parent", "content_digest"], "a", "b"),
    ], update="Compared parent-1 to parent-2: No authored changes.")
    bookkeeping["summary"]["other_properties_changed"] = False
    bookkeeping["summary"]["media_bookkeeping_changed"] = False
    assert "No authored changes; revision/reference bookkeeping changed." in format_authoring_revision_diff(bookkeeping)


def test_legacy_results_fall_back_to_complete_json_instead_of_inventing_context():
    old = {"from_version": 2, "to_version": 3, "data": {"changes": [{"secret": "kept"}]}}
    text = format_authoring_revision_diff(old)
    assert "structured locations unavailable" in text
    assert '"secret": "kept"' in text
    assert "<from>" not in text


def test_rejects_unknown_modes():
    with pytest.raises(ValueError, match="mode must be"):
        format_authoring_revision_diff(_result([]), mode="json")
