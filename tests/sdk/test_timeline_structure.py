"""Chapters as labels: one authoring shot, music joined, a layer carried across a chapter wall."""
from __future__ import annotations

import pytest

from astrid.sdk import timeline_intent as intent
from astrid.sdk.timeline_checkout import Checkout
from astrid.sdk.timeline_structure import carry_across, join_music, label_chapters, one_shot

from tests.sdk.test_timeline_checkout import bundle


def two_chapters():
    data = bundle()
    a, b = data["shots"]["A"]["internal_timeline"]["clips"], data["shots"]["B"]["internal_timeline"]["clips"]
    data["shots"]["A"]["payload"]["name"] = "01 ONE"
    data["shots"]["B"]["payload"]["name"] = "02 TWO"
    a[0]["app"] = {"cut": "c1"}
    b[0]["app"] = {"cut": "c2"}
    a.append({"id": "a-tower", "clipType": "am-sprite", "track": "sprite", "asset": "R", "at": 3.0, "hold": 1.0,
              "params": {"x": 5, "enter": "stamp"}, "app": {"cut": "c1"}})
    b.append({"id": "b-tower", "clipType": "am-sprite", "track": "sprite", "asset": "R", "at": 0.0, "hold": 2.0,
              "params": {"x": 5, "enter": "stamp"}, "app": {"cut": "c2", "for": 2.0}})
    b.append({"id": "b-music", "clipType": "media", "track": "music", "asset": "Q", "at": 0.0, "from": 4.0, "to": 8.0})
    return data


def test_one_shot_keeps_every_time_and_labels_the_chapters():
    tl = Checkout(two_chapters())
    before = {c.id: (c.start, c.end) for c in tl.clips()}
    walls = one_shot(tl, shot_id="film", name="FILM")
    assert tl._shot_ids() == ["film"] and walls == [{"name": "01 ONE", "at": 0.0}, {"name": "02 TWO", "at": 4.0}]
    assert {c.id: (c.start, c.end) for c in tl.clips()} == {k: (pytest.approx(s), pytest.approx(e)) for k, (s, e) in before.items()}
    assert tl.clip("b-type").data["at"] == pytest.approx(6.0)  # at is now timeline seconds
    assert join_music(tl) == ["b-music"]
    music = [c for c in tl.clips() if c.track == "music"]
    assert len(music) == 1 and music[0].end == pytest.approx(8.0)
    assert label_chapters(tl, walls) == [{"name": "01 ONE", "from": "c1"}, {"name": "02 TWO", "from": "c2"}]
    assert intent.chapters(tl.bundle)[1]["from"] == "c2"


def test_a_layer_duplicated_at_a_wall_becomes_one_clip_that_carries_across():
    tl = Checkout(two_chapters())
    walls = one_shot(tl, shot_id="film", name="FILM")
    merged = carry_across(tl, [w["at"] for w in walls[1:]])
    assert merged and "a-tower carries across 4.00 s" in merged[0]
    tower = [c for c in tl.clips() if c.id.endswith("-tower")]
    assert [(c.id, c.start, c.end) for c in tower] == [("a-tower", pytest.approx(3.0), pytest.approx(6.0))]
    assert intent.for_s(tower[0].data) == pytest.approx(3.0)  # it kept b-tower's literal end
    assert {c.id for c in tl.clips() if c.track == "plate"} == {"a-plate", "b-plate"}  # pictures are never merged
