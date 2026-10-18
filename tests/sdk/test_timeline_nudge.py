"""Nudging a clip: a cut's clip with no moment keeps its nudge (as the sheet's "on +0.1s" does), and a no-change records no undo step."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from astrid.packs.timeline import cli
from astrid.sdk import timeline_checkout as tc
from astrid.sdk import timeline_intent as intent
from astrid.sdk.timeline_checkout import Checkout

ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "timeline_editing"


def tiny():
    return json.loads((ROOT / "tiny.json").read_text(encoding="utf-8"))


def _tl():
    tl = Checkout(tiny())
    tl.resolve()
    return tl


def _run(*argv) -> int:
    parsed = cli.build_parser(None).parse_args(list(argv))
    return parsed.handler(parsed)


def test_nudging_a_cut_card_writes_a_moment_and_keeps_it_through_retime():
    tl = _tl()
    tl.clip("c2.card").nudge(0.1)
    card = tl.clip("c2.card")
    assert intent.on(card.data) == "+0.1s"  # the same moment the sheet's "on +0.1s" writes
    assert card.start == pytest.approx(4.1)
    tl.retime()
    assert tl.clip("c2.card").start == pytest.approx(4.1)


def test_nudging_a_cut_card_survives_check():
    tl = _tl()
    tl.clip("c2.card").nudge(0.1)
    tl.check()
    assert tl.clip("c2.card").start == pytest.approx(4.1)


def test_nudging_a_cut_card_matches_the_sheet_document():
    by_python = _tl()
    by_python.clip("c2.card").nudge(0.1)
    by_sheet = _tl()
    by_sheet.clip("c2.card").on("+0.1s")
    assert by_python.bundle == by_sheet.bundle


def test_nudging_a_clip_on_a_moment_keeps_its_word():
    tl = _tl()
    rocket = tl.clip("c1.rocket")
    assert intent.on(rocket.data) == '"viral"'
    rocket.nudge(0.1)
    on = intent.on(tl.clip("c1.rocket").data)
    assert on.startswith('"viral"') and on != '"viral"'  # the nudge became the moment's offset
    tl.retime()
    assert intent.on(tl.clip("c1.rocket").data).startswith('"viral"')


def test_a_clip_with_no_cut_and_no_moment_moves_by_at():
    tl = _tl()
    music = tl.clip("a-music")
    assert intent.cut_of(music.data) is None and intent.on(music.data) is None
    before = music.start
    music.nudge(0.1)
    assert intent.on(music.data) is None
    assert "at" in music.data
    assert tl.clip("a-music").start == pytest.approx(before + 0.1)


def test_a_zero_nudge_changes_nothing():
    tl = _tl()
    before = copy.deepcopy(tl.bundle)
    tl.clip("c2.card").nudge(0)
    tl.clip("c1.rocket").nudge(0.0)
    assert tl.bundle == before


@pytest.fixture
def tiny_draft(tmp_path, monkeypatch):
    monkeypatch.setenv("ASTRID_RUNTIME_DATA_ROOT", str(tmp_path / "data"))
    monkeypatch.setattr(tc, "resolve_ids", lambda project, timeline, client=None: ("p", "t", "rev-0"))
    monkeypatch.setattr(tc, "fetch_bundle", lambda project, timeline, revision_id=None, client=None: tiny())
    assert _run("checkout", "t", "--project", "P") == 0
    return tc.find_draft("P", "t")


def test_a_zero_nudge_on_a_draft_records_no_undo_step(tiny_draft, capsys):
    assert _run("edit", "t", "--project", "P", "--clip", "c2.card", "--nudge", "0") == 3
    assert "no change (the working copy already matches)" in capsys.readouterr().out
    from astrid.sdk.timeline import store
    assert not list(store._history_dir(tiny_draft).glob("*.json"))
    assert _run("undo", "t", "--project", "P") != 0  # nothing was recorded, so nothing to undo (one line, not a traceback)
    assert "nothing to undo" in capsys.readouterr().err
    assert Checkout.load(tiny_draft).clip("c2.card").start == pytest.approx(4.0)


def test_the_cli_nudge_of_a_cut_card_changes_the_document(tiny_draft, capsys):
    before = Checkout.load(tiny_draft).document()
    assert _run("edit", "t", "--project", "P", "--clip", "c2.card", "--nudge", "0.1") == 0
    out = capsys.readouterr().out
    assert "no change" not in out
    after = Checkout.load(tiny_draft)
    assert after.document() != before
    assert after.clip("c2.card").start == pytest.approx(4.1)
    after.retime()
    assert after.clip("c2.card").start == pytest.approx(4.1)
