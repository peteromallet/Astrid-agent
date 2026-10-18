"""A1 undo fix: opening a pre-A1 working copy (intent in the old places) and saving it must not be an edit.

The migration to ``app.astrid`` happens on open (``Checkout.load`` absorbs it), so the first save writes a
different text than the file on disk. That difference is the migration, not a user edit: it must record no
undo step and no journal ``save`` record. A real edit made after the open is still exactly one undo step.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from astrid.sdk.timeline import journal as _journal
from astrid.sdk.timeline import store as _store
from astrid.sdk.timeline_checkout import Checkout
from tests.sdk.test_intent_app_astrid import TINY, _legacy_bundle

PROJECT = "P"
TIMELINE = "t"
RESOLVED = ("p", "t", "rev-0")


@pytest.fixture
def head(tmp_path, monkeypatch):
    """A published head (tiny.json, new shape) behind the store, and a data root of this test's own."""
    monkeypatch.setenv("ASTRID_RUNTIME_DATA_ROOT", str(tmp_path / "data"))
    bundle = json.loads(TINY.read_text(encoding="utf-8"))
    monkeypatch.setattr(_store, "resolve_ids", lambda project, timeline, client=None: RESOLVED)
    monkeypatch.setattr(_store, "fetch_bundle",
                        lambda project, timeline, revision_id=None, client=None: copy.deepcopy(bundle))
    return tmp_path


def _legacy_draft(tmp_path: Path) -> Path:
    """Write the working copy the way a pre-A1 tool left it: intent in the old places, no app.astrid."""
    path = _store.draft_path(RESOLVED[0], RESOLVED[1], "main")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_legacy_bundle(), indent=2), encoding="utf-8")
    _store.set_current_draft(RESOLVED[0], RESOLVED[1], "main")
    return path


def _steps(path: Path) -> list[Path]:
    return sorted(_store._history_dir(path).glob("*.json"))


def _save_records(path: Path) -> list:
    return [r for r in _journal.read(_journal.journal_path(RESOLVED[0], RESOLVED[1])) if r.kind == "save"]


def test_the_legacy_fixture_really_is_pre_a1(head):
    doc = json.loads(_legacy_draft(head).read_text(encoding="utf-8"))
    assert "chapters" in doc["parent"]["config"]  # a loose film-level key: the pre-A1 shape
    assert "astrid" not in (doc["parent"]["config"].get("app") or {})


def test_opening_and_saving_a_legacy_working_copy_with_no_edit_records_no_step(head):
    path = _legacy_draft(head)
    tl = Checkout.draft(PROJECT, TIMELINE)
    tl.save()
    assert _steps(path) == [], "a migration-only save recorded an undo step"
    assert _save_records(path) == [], "a migration-only save wrote a journal 'save' record"


def test_one_real_edit_on_a_legacy_working_copy_is_exactly_one_undo_step(head):
    path = _legacy_draft(head)
    tl = Checkout.draft(PROJECT, TIMELINE)
    tl.clip("a-rocket").set(x=120)
    tl.save()
    steps = _steps(path)
    assert len(steps) == 1, f"one edit must be one undo step, got {len(steps)}"
    assert len(_save_records(path)) == 1
    # undo restores the legacy file as it was on disk before the edit
    before = json.loads(steps[0].read_text(encoding="utf-8"))["state"]
    assert json.loads(before)["parent"]["config"]["chapters"] == [{"name": "01 OPEN", "from": "c1"}]
    tl.undo()
    assert json.loads(path.read_text(encoding="utf-8")) == json.loads(before)


def test_open_then_no_edit_save_then_one_edit_is_exactly_one_undo_step(head):
    """The no-op save of an opened legacy copy adds no step; the later edit is the only one."""
    path = _legacy_draft(head)
    Checkout.draft(PROJECT, TIMELINE).save()
    tl = Checkout.draft(PROJECT, TIMELINE)
    tl.clip("a-rocket").set(x=120)
    tl.save()
    assert len(_steps(path)) == 1, "the open-plus-no-edit save must not add a step"
