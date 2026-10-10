"""Adopt another timeline's head: the film's working copy becomes round2 (its narration, cuts, assets, intent)."""
from __future__ import annotations

import copy
import json

import pytest

from astrid.sdk import timeline_intent as intent
from astrid.sdk.timeline_adopt import adopt_content
from astrid.sdk.timeline_checkout import Checkout, TimelineEditError
from astrid.sdk.timeline_duplicate import copy_content

from tests.packs.timeline.test_edit_verbs import bundle


def _round2():
    """A duplicate of the film (new shot ids), then edited: a new take, a new asset key, a cut moved, beats."""
    film = bundle()
    dup, _ = copy_content(film, copy.deepcopy(film), new_slug="round2")
    dup["timeline_id"] = "round2"
    sid = next(iter(dup["shots"]))
    internal = dup["shots"][sid]["internal_timeline"]
    internal["registry"]["assets"]["music"] = {"media_id": "sha256:" + "9" * 64, "type": "audio/wav"}
    for clip in internal["clips"]:
        if clip["id"] == "a-music":
            clip["asset"] = "music"
            clip["app"] = {"beats": {"beats": [0.0, 0.5, 1.0], "time": "cue_seconds", "source": "run:R2/beats"}}
        if clip["id"] == "a-vo":
            clip["app"]["words"] = [[0.1, 0.3, "it"], [0.4, 0.6, "really"], [0.7, 0.9, "went"], [1.0, 1.4, "viral"]]
        if clip["id"] == "a-rocket":
            clip["app"] = {"on": '"viral"'}
    dup["parent"]["config"]["chapters"] = [{"from": "c1", "name": "01 OPEN"}]
    published = Checkout(dup)
    published.resolve()  # a published head is resolved: every clip already on its moment
    return film, published.document()


def _clips(doc):
    return sorted((json.dumps(c, sort_keys=True) for shot in doc["shots"].values()
                   for c in shot["internal_timeline"]["clips"]))


def test_adopting_makes_the_film_equal_round2_and_keeps_the_films_identity():
    film, round2 = _round2()
    adopted, notes = adopt_content(round2, film)
    assert set(adopted["shots"]) == set(film["shots"])  # the film's shots, new revisions of them
    assert [r["shot_id"] for r in adopted["placements"]] == [r["shot_id"] for r in film["placements"]]
    for sid in film["shots"]:  # the film's base is untouched: check/status/publish diff against it
        assert adopted["shots"][sid]["base_internal_timeline"] == film["shots"][sid]["base_internal_timeline"]
    assert _clips(adopted) == _clips(round2)  # every clip with its intent, words and beats, as round2 has them
    assert adopted["parent"]["config"]["chapters"] == [{"from": "c1", "name": "01 OPEN"}]
    tl = Checkout(adopted)
    music = next(c for c in tl.clips(audio=True) if c.track == "music")
    assert music.asset == "music" and intent.beats_label(music.data) == "run:R2/beats"
    assert [w.text for w in tl.words()][:4] == ["it", "really", "went", "viral"]
    assert tl.edits()["changes"] and not notes


def test_checkout_adopt_through_the_api_and_the_cli(tmp_path, monkeypatch, capsys):
    from astrid.packs.timeline import cli
    from astrid.sdk import timeline_checkout as tc

    film, round2 = _round2()
    monkeypatch.setenv("BANODOCO_LOCAL_DATA_ROOT", str(tmp_path / "data"))
    monkeypatch.setattr(tc, "resolve_ids", lambda project, timeline, client=None: ("p", "t", "rev-0"))
    heads = {"t": film, "round2": round2}
    monkeypatch.setattr(tc, "fetch_bundle", lambda project, timeline, revision_id=None, client=None: copy.deepcopy(heads[timeline]))
    parser = cli.build_parser(object())
    parsed = parser.parse_args(["checkout", "t", "--project", "P", "--from", "round2"])
    assert parsed.handler(parsed) == 0
    out = capsys.readouterr().out
    assert "adopted round2" in out and "narration" in out
    tl = Checkout.draft("P", "t")
    assert _clips(tl.document()) == _clips(round2)
    # a working copy with edits is not replaced silently
    tl.clip("b-type").set(text="Mine.")
    tl.save()
    parsed = parser.parse_args(["checkout", "t", "--project", "P", "--from", "round2"])
    assert parsed.handler(parsed) == 2
    assert "add --fresh" in capsys.readouterr().err
    # and undo takes the adopt back in one step
    fresh = Checkout.draft("P", "t", fresh=True)
    fresh.adopt("round2")
    fresh.save()
    assert fresh.undo() == ["adopt('round2')"]
    assert _clips(fresh.document()) == _clips(film)


def test_adopt_refuses_across_projects():
    film, round2 = _round2()
    round2["project_id"] = "other"
    with pytest.raises(TimelineEditError, match="inside one project"):
        adopt_content(round2, film)
