"""An edit that does not change what the film shows changes nothing: no document change, no undo step, a note.

Covers the two known cases: ``until`` ``end`` on a clip that already ends with its cut (AUDIT bug 12), and
``extend(0)`` / ``hold_for(<current length>)`` on an anchored clip (which used to write an explicit ``for``).
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from astrid.sdk import timeline_checkout as tc
from astrid.sdk.timeline_checkout import Checkout

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "timeline_editing" / "tiny.json"
PROJECT, TIMELINE = "P", "t"
RESOLVED = ("p", "t", "rev-0")


def _load() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _dump(tl: Checkout) -> str:
    return json.dumps(tl.document(), sort_keys=True)


@pytest.fixture
def draft_env(tmp_path, monkeypatch):
    """A temp data root, and the head resolved to a deep copy of the fixture (as the conformance runner does)."""
    bundle = _load()
    monkeypatch.setenv("ASTRID_RUNTIME_DATA_ROOT", str(tmp_path))
    monkeypatch.setattr(tc, "resolve_ids", lambda project, timeline, client=None: RESOLVED)
    monkeypatch.setattr(tc, "fetch_bundle",
                        lambda project, timeline, revision_id=None, client=None: copy.deepcopy(bundle))
    return tmp_path


def _tl() -> Checkout:
    return Checkout(_load())


# --- until: the clip already ends with its cut -------------------------------------------------------

def test_until_end_on_a_clip_that_ends_with_its_cut_changes_nothing():
    tl = _tl()
    before = _dump(tl)
    tl.clip("c1.rocket").until("end")
    assert _dump(tl) == before
    assert tl.edits()["changes"] == []


def test_until_end_of_its_own_cut_changes_nothing():
    tl = _tl()
    before = _dump(tl)
    tl.clip("c1.rocket").until("end of c1")
    assert _dump(tl) == before


def test_until_end_on_a_noop_writes_the_note_and_no_intent():
    tl = _tl()
    tl.clip("c1.rocket").until("end")
    assert "c1.rocket: already ends with its cut (no change)" in tl.notes
    clip = tl.clip("c1.rocket")
    assert clip.moments["until"] is None and clip.moments["for"] is None


def test_until_a_different_moment_still_changes_the_clip():
    tl = _tl()
    before = _dump(tl)
    tl.clip("c2.card").until('"now"')
    assert _dump(tl) != before
    assert tl.clip("c2.card").moments["until"] == '"now"'
    assert not any("already ends with its cut" in n for n in tl.notes)


def test_until_end_on_a_clip_that_does_not_end_with_its_cut_still_changes_it():
    tl = _tl()
    clip = tl.clip("c2.card")  # held for 1 s (a literal for): not ending with its cut
    before = _dump(tl)
    clip.until("end")
    assert _dump(tl) != before


# --- set_duration: the length does not change --------------------------------------------------------

def test_extend_zero_on_an_anchored_clip_changes_nothing():
    tl = _tl()
    before = _dump(tl)
    tl.clip("c1.rocket").extend(0)
    assert _dump(tl) == before
    assert "c1.rocket: already ends with its cut (no change)" in tl.notes


def test_hold_for_the_current_length_changes_nothing():
    tl = _tl()
    clip = tl.clip("c1.rocket")
    before = _dump(tl)
    clip.hold_for(clip.duration)
    assert _dump(tl) == before


def test_extend_by_a_real_amount_still_changes_the_clip():
    tl = _tl()
    before = _dump(tl)
    tl.clip("c1.rocket").extend(0.5)
    assert _dump(tl) != before
    assert tl.clip("c1.rocket").duration == pytest.approx(3.2)
    assert tl.clip("c1.rocket").moments["for"] == pytest.approx(3.2)


# --- drafts: no undo step for a no-op ----------------------------------------------------------------

def _history_count(project: str = PROJECT) -> int:
    draft = tc.find_draft(project, TIMELINE)
    return len(sorted(tc._history_dir(draft).glob("*.json"))) if draft else 0


def test_until_end_on_a_draft_then_save_records_no_undo_step(draft_env):
    tl = Checkout.draft(PROJECT, TIMELINE)
    tl.clip("c1.rocket").until("end")
    tl.save()
    assert _history_count() == 0


def test_a_real_edit_on_a_draft_records_an_undo_step(draft_env):
    tl = Checkout.draft(PROJECT, TIMELINE)
    tl.clip("c2.card").until('"now"')
    tl.save()
    assert _history_count() >= 1
