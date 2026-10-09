from __future__ import annotations

import copy

import pytest

from astrid.core.timeline.authoring_bundle import AuthoringBundleError, open_authoring_bundle
from astrid.core.timeline.authoring_revision_diff import diff_authoring_revisions
from tests.timeline.test_authoring_bundle import NEW, OLD, _closure, _digest


def _open(parent, shots, internals):
    bundle = open_authoring_bundle(
        parent, shot_revisions=shots, internal_timeline_revisions=internals,
    )
    bundle["immutable_closure"] = copy.deepcopy({
        "parent": parent, "shots": {row["revision_id"]: row for row in shots},
        "internal_timelines": {row["revision_id"]: row for row in internals},
    })
    return bundle


def _revisions(*, shared=False):
    parent, shots, internals = _closure(shared=shared)
    before = _open(parent, shots, internals)
    after_parent, after_shots, after_internals = copy.deepcopy((parent, shots, internals))
    after_parent["revision_id"] = "parent-2"
    after_parent["content_digest"] = _digest(after_parent["payload"])
    return before, (after_parent, after_shots, after_internals)


def _seal_child_revision(parent, shots, internals):
    internal = internals[0]
    internal["revision_id"] = "internal-2"
    internal["content_digest"] = _digest(internal["payload"])
    shot = shots[0]
    shot["revision_id"] = "shot-rev-2"
    shot["internal_timeline_revision_id"] = internal["revision_id"]
    shot["payload"]["internal_timeline_revision_id"] = internal["revision_id"]
    shot["content_digest"] = _digest(shot["payload"])
    for occurrence in parent["payload"]["occurrences"]:
        occurrence["shot_revision_id"] = shot["revision_id"]
    parent["content_digest"] = _digest(parent["payload"])


def test_identical_closures_have_no_raw_or_semantic_changes():
    parent, shots, internals = _closure()
    before = _open(parent, shots, internals)
    after = _open(parent, shots, internals)

    result = diff_authoring_revisions(before, after)

    assert result["complete"] is True
    assert result["from_revision"] == result["to_revision"] == "parent-1"
    assert result["changes"] == []
    assert result["raw_change_count"] == 0
    assert result["summary"]["authored_change_count"] == 0
    assert result["summary"]["other_properties_changed"] is False
    assert result["update"].startswith("Compared parent-1 to parent-1: No authored changes.")


def test_stable_clip_reorder_is_distinct_from_media_replacement():
    before, (parent, shots, internals) = _revisions()
    internals[0]["payload"]["clips"].reverse()
    _seal_child_revision(parent, shots, internals)
    after = _open(parent, shots, internals)

    result = diff_authoring_revisions(before, after)

    assert any(change["path"].endswith("clips.order") and change["kind"] == "reordered"
               for change in result["changes"])
    assert result["summary"]["media_selections"]["image"]["updated"] == 0
    assert result["summary"]["other_properties_changed"] is True


def test_stable_item_addition_reports_identity_path_and_full_item():
    before, (parent, shots, internals) = _revisions()
    shots[0]["payload"]["items"].append({"item_id": "item-added", "media_id": NEW,
                                          "metadata": {"source": "new"}})
    _seal_child_revision(parent, shots, internals)
    after = _open(parent, shots, internals)

    result = diff_authoring_revisions(before, after)

    added = [change for change in result["changes"]
             if change["kind"] == "added" and "items[item_id=item-added]" in change["path"]]
    assert len(added) == 1
    assert added[0]["after"]["metadata"] == {"source": "new"}
    assert result["raw_change_count"] == len(result["changes"])


def test_occurrence_addition_and_removal_are_reported_by_stable_occurrence_id():
    before, (parent, shots, internals) = _revisions()
    repeated = copy.deepcopy(parent["payload"]["occurrences"][0])
    repeated["occurrence_id"] = "occ-2"
    repeated["placement"]["start_ms"] = 1000
    parent["payload"]["occurrences"].append(repeated)
    parent["content_digest"] = _digest(parent["payload"])
    after_added = _open(parent, shots, internals)

    added = diff_authoring_revisions(before, after_added)
    added_occurrence = next(change for change in added["changes"]
                            if change["path"] == "occurrences[occurrence_id=occ-2]"
                            and change["kind"] == "added")
    assert added_occurrence["after"]["shot_revision_id"] == "shot-rev-1"
    added_shot = next(change for change in added["changes"]
                      if change["path"] == "shots.occ-2" and change["kind"] == "added")
    assert added_shot["after"]["internal_timeline_revision_id"] == "internal-1"
    assert added_shot["after"]["payload"]["items"][0]["item_id"] == "item-1"
    assert added["summary"]["occurrence_changes"]["added"] == 1

    shared_parent, shared_shots, shared_internals = _closure(shared=True)
    shared_before = _open(shared_parent, shared_shots, shared_internals)
    removed_parent, removed_shots, removed_internals = copy.deepcopy(
        (shared_parent, shared_shots, shared_internals)
    )
    removed_parent["revision_id"] = "parent-2"
    removed_parent["payload"]["occurrences"].pop()
    removed_parent["content_digest"] = _digest(removed_parent["payload"])
    after_removed = _open(removed_parent, removed_shots, removed_internals)
    removed = diff_authoring_revisions(shared_before, after_removed)
    removed_occurrence = next(change for change in removed["changes"]
                              if change["path"] == "occurrences[occurrence_id=occ-2]"
                              and change["kind"] == "removed")
    assert removed_occurrence["before"]["shot_revision_id"] == "shot-rev-1"
    removed_shot = next(change for change in removed["changes"]
                        if change["path"] == "shots.occ-2" and change["kind"] == "removed")
    assert removed_shot["before"]["payload"]["items"][0]["item_id"] == "item-1"
    assert removed["summary"]["occurrence_changes"]["removed"] == 1


def test_shared_source_revisions_are_compared_per_stable_occurrence():
    before, (parent, shots, internals) = _revisions(shared=True)
    internals[0]["payload"]["clips"][0]["rect"]["x"] = 0.8
    _seal_child_revision(parent, shots, internals)
    after = _open(parent, shots, internals)

    result = diff_authoring_revisions(before, after)
    rect_paths = {change["path"] for change in result["changes"] if ".rect.x" in change["path"]}

    assert any("shots.occ-1" in path for path in rect_paths)
    assert any("shots.occ-2" in path for path in rect_paths)
    assert result["summary"]["media_selections"]["image"]["updated"] == 0
    assert result["summary"]["other_properties_changed"] is True


def test_media_and_unknown_property_changes_are_both_preserved():
    before, (parent, shots, internals) = _revisions()
    internals[0]["payload"]["registry"]["assets"]["old-picture"]["media_id"] = NEW
    internals[0]["payload"]["opaque_timeline"]["new_field"] = {"value": 4}
    shots[0]["payload"]["items"][0]["media_id"] = NEW
    _seal_child_revision(parent, shots, internals)
    after = _open(parent, shots, internals)

    result = diff_authoring_revisions(before, after)

    assert any("opaque_timeline.new_field" in change["path"] for change in result["changes"])
    assert any("registry.assets.old-picture.media_id" in change["path"] for change in result["changes"])
    assert result["summary"]["media_selections"]["image"]["updated"] == 1
    assert result["summary"]["other_properties_changed"] is True


def test_project_or_timeline_scope_mismatch_is_rejected():
    parent, shots, internals = _closure()
    before = _open(parent, shots, internals)
    after = _open(parent, shots, internals)
    after["timeline_id"] = "another-timeline"

    with pytest.raises(AuthoringBundleError, match="same project and timeline"):
        diff_authoring_revisions(before, after)


def test_raw_details_keep_source_fields_removed_by_authoring_normalization():
    parent, shots, internals = _closure()
    parent["payload"]["occurrences"][0]["revision_id"] = "source-note-before"
    parent["content_digest"] = _digest(parent["payload"])
    before = _open(parent, shots, internals)
    parent["revision_id"] = "parent-2"
    parent["payload"]["occurrences"][0]["revision_id"] = "source-note-after"
    parent["content_digest"] = _digest(parent["payload"])
    after = _open(parent, shots, internals)

    result = diff_authoring_revisions(before, after)

    change = next(row for row in result["changes"]
                  if row["path"] == "occurrences[occurrence_id=occ-1].revision_id")
    assert change["before"] == "source-note-before"
    assert change["after"] == "source-note-after"


def test_removal_of_entire_shot_retains_complete_before_payloads():
    parent, shots, internals = _closure(shared=False)
    before = _open(parent, shots, internals)
    parent["revision_id"] = "parent-2"
    parent["payload"]["occurrences"] = []
    parent["content_digest"] = _digest(parent["payload"])
    after = _open(parent, [], [])

    result = diff_authoring_revisions(before, after)

    removed = next(row for row in result["changes"] if row["path"] == "shots.occ-1")
    assert removed["kind"] == "removed"
    assert removed["before"]["payload"] == shots[0]["payload"]
    assert removed["before"]["internal_timeline"] == internals[0]["payload"]
    assert result["summary"]["occurrence_changes"]["removed"] == 1


def test_occurrence_timing_change_preserves_exact_values_without_false_selection_change():
    before, (parent, shots, internals) = _revisions()
    parent["payload"]["occurrences"][0]["placement"]["start_ms"] = 400
    parent["content_digest"] = _digest(parent["payload"])
    result = diff_authoring_revisions(before, _open(parent, shots, internals))

    change = next(row for row in result["changes"]
                  if row["path"].endswith("placement.start_ms"))
    assert (change["before"], change["after"]) == (0, 400)
    assert result["summary"]["timing_changes"]["occurrences"] == 1
    assert result["summary"]["media_selections"]["image"]["updated"] == 0


def test_structured_locations_and_verified_context_preserve_punctuation_identity():
    parent, shots, internals = _closure(shared=False)
    identity = "occ.one[2]"
    parent["payload"]["occurrences"][0]["occurrence_id"] = identity
    shots[0]["payload"]["metadata"]["a.b[0]"] = None
    for row in (parent, *shots, *internals):
        row["content_digest"] = _digest(row["payload"])
    before = _open(parent, shots, internals)
    shots[0]["payload"]["metadata"]["a.b[0]"] = {"content_digest": "user metadata"}
    _seal_child_revision(parent, shots, internals)
    after = _open(parent, shots, internals)

    result = diff_authoring_revisions(before, after)

    change = next(row for row in result["changes"]
                  if row.get("location") == ["shots", identity, "payload", "metadata", "a.b[0]"])
    assert change["before"] is None
    assert change["after"] == {"content_digest": "user metadata"}
    context = result["context"]["occurrences"][identity]
    assert context["before"]["name"] == context["after"]["name"] == "Opening"
    assert context["before"]["clips"]["picture-1"]["track"] == "picture"
    assert context["before"]["clips"]["picture-1"]["selection"] == [OLD, "image"]
