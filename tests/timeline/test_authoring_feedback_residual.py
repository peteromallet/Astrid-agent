from __future__ import annotations

from tests.timeline.test_authoring_feedback import NEW, bundle, publish, replace_images


def _result(work):
    return publish(work)


def test_media_replacement_and_other_authored_properties_are_reported_together():
    work = bundle()
    shot = replace_images(work)
    timeline = shot["internal_timeline"]
    timeline["clips"][0]["cropTop"] = 0.2
    timeline["audio"][0]["gain"] = 0.5
    timeline["effects"].append({"type": "blur", "value": 0.1})

    result = _result(work)

    assert result["summary"]["media_selections"]["image"]["updated"] == 1
    assert result["summary"]["other_properties_changed"] is True
    assert "Updated 1 image selection." in result["update"]
    assert "Other properties changed." in result["update"]


def test_metadata_only_edit_is_reported_as_an_other_property_change():
    work = bundle()
    next(iter(work["shots"].values()))["payload"]["metadata"]["settings"]["look"] = "cool"

    result = _result(work)

    assert result["summary"]["other_properties_changed"] is True
    assert "Other properties changed." in result["update"]


def test_selector_and_registry_payload_mirrors_are_one_image_change_without_other_change():
    work = bundle()
    replace_images(work)

    result = _result(work)

    assert result["summary"]["media_selections"]["image"]["updated"] == 1
    assert result["summary"]["other_properties_changed"] is False
    assert result["summary"]["media_bookkeeping_changed"] is False
    assert "Other properties changed." not in result["update"]
    assert "Updated media bookkeeping." not in result["update"]


def test_alias_change_to_same_immutable_media_is_bookkeeping_only():
    work = bundle()
    timeline = next(iter(work["shots"].values()))["internal_timeline"]
    timeline["registry"]["assets"]["same-image"] = {
        **timeline["registry"]["assets"]["old-picture"],
    }
    timeline["clips"][0]["asset"] = "same-image"

    result = _result(work)

    assert result["summary"]["media_selections"]["image"]["updated"] == 0
    assert result["summary"]["other_properties_changed"] is False
    assert result["summary"]["media_bookkeeping_changed"] is True
    assert "Updated media bookkeeping." in result["update"]
    assert "Other properties changed." not in result["update"]


def test_timing_only_change_does_not_count_as_other_or_media_bookkeeping():
    work = bundle()
    clip = next(iter(work["shots"].values()))["internal_timeline"]["clips"][0]
    clip["hold"] = 2

    result = _result(work)

    assert result["summary"]["timing_changes"]["clips"] == 1
    assert result["summary"]["other_properties_changed"] is False
    assert result["summary"]["media_bookkeeping_changed"] is False
    assert "Other properties changed." not in result["update"]
    assert "Updated media bookkeeping." not in result["update"]


def test_noop_has_no_other_or_media_bookkeeping_changes():
    result = _result(bundle())

    assert result["summary"]["other_properties_changed"] is False
    assert result["summary"]["media_bookkeeping_changed"] is False
    assert "Other properties changed." not in result["update"]
    assert "Updated media bookkeeping." not in result["update"]


def test_clip_reorder_is_other_change_without_selection_replacements():
    work = bundle()
    timeline = next(iter(work["shots"].values()))["internal_timeline"]
    timeline["clips"].reverse()

    result = _result(work)

    assert result["summary"]["media_selections"]["image"]["updated"] == 0
    assert result["summary"]["other_properties_changed"] is True
    assert "Other properties changed." in result["update"]


def test_unused_registry_alternative_is_an_other_property_change():
    work = bundle()
    timeline = next(iter(work["shots"].values()))["internal_timeline"]
    timeline["registry"]["assets"]["unused-option"] = {"media_id": NEW, "type": "image"}

    result = _result(work)

    assert result["summary"]["media_selections"]["image"]["updated"] == 0
    assert result["summary"]["other_properties_changed"] is True
    assert "Other properties changed." in result["update"]


def test_selected_audio_and_audio_binding_mirror_count_once_without_other_change():
    work = bundle()
    shot = next(iter(work["shots"].values()))
    timeline = shot["internal_timeline"]
    timeline["registry"]["assets"]["voice"]["media_id"] = NEW
    shot["payload"]["audio_bindings"][0]["object_id"] = NEW

    result = _result(work)

    assert result["summary"]["media_selections"]["audio"]["updated"] == 1
    assert result["summary"]["other_properties_changed"] is False
    assert result["summary"]["media_bookkeeping_changed"] is False
    assert "Updated 1 audio selection." in result["update"]
    assert "Other properties changed." not in result["update"]


def test_opaque_registry_change_is_not_hidden_by_image_selection():
    work = bundle()
    shot = replace_images(work)
    shot["internal_timeline"]["registry"]["assets"]["old-picture"]["opaque_asset"] = "changed"
    result = _result(work)
    assert result["summary"]["other_properties_changed"] is True
    assert "Updated 1 image selection." in result["update"]
    assert "Other properties changed." in result["update"]


def test_edits_to_new_duplicate_are_not_hidden_by_added_occurrence_summary():
    from astrid.sdk.timeline_editing import duplicate_authoring_shot

    work = bundle()
    duplicate = duplicate_authoring_shot(work, work["placements"][0]["shot_id"],
                                         new_shot_id="new-shot", occurrence_id="new-occ")
    # New placements must use admitted defaults rather than inherited legacy
    # trim/scale metadata; keep the creation baseline aligned with that fixture.
    for row in (work["placements"][-1], work["source_mapping"]["placements"]["new-occ"]):
        row["source_offset"] = {"start": 0, "end": 0}
        row["transform"] = {}
    duplicate["payload"]["generation_inputs"]["visual"]["label"] = "changed after duplication"
    duplicate["internal_timeline"]["clips"][0]["rect"]["x"] = .7
    result = _result(work)
    assert result["summary"]["occurrence_changes"]["added"] == 1
    assert result["summary"]["other_properties_changed"] is True
    assert "Added 1 shot occurrence." in result["update"]
    assert "Other properties changed." in result["update"]
