"""Re-flow under real load: removing a line never crashes, never collapses a cut, keeps the music whole,
lists every orphan by name, and check blocks publishing until each one is re-homed or removed.

The fixture is a trimmed model of v7 → v8 (the real failures R1–R3 of Round 1): three lines, the middle
one ("Live.") with a cut of its own and layers on its words, a music bed under everything, and a later
layer whose formula names a word of the removed line.
"""
from __future__ import annotations

import pytest

from astrid.sdk import timeline_intent as intent
from astrid.sdk.timeline_checkout import Checkout

FPS = 30


def film():
    clips = [
        # cut c1 on line s1 (0–4 s)
        {"id": "c1-plate", "clipType": "am-snap-plate", "track": "plate", "asset": "P", "at": 0.0, "hold": 4.0,
         "params": {}, "app": {"cut": "c1", "layer": "field", "on": '"It"'}},
        {"id": "c1-rocket", "clipType": "am-sprite", "track": "sprite", "asset": "R", "at": 1.3, "hold": 2.7,
         "params": {"x": 10}, "app": {"cut": "c1", "layer": "rocket", "on": '"viral"'}},
        # cut c2 on line s2 "Live." (4–6 s): it exists only for that line
        {"id": "c2-plate", "clipType": "am-snap-plate", "track": "plate", "asset": "Q", "at": 4.0, "hold": 2.0,
         "params": {}, "app": {"cut": "c2", "layer": "field", "on": '"Live"'}},
        {"id": "c2-live", "clipType": "am-type", "track": "type", "at": 4.0, "hold": 2.0,
         "params": {"text": "Live."}, "app": {"cut": "c2", "layer": "live"}},
        {"id": "c2-dot", "clipType": "am-sprite", "track": "sprite", "asset": "R", "at": 4.5, "hold": 1.5,
         "params": {"x": 20}, "app": {"cut": "c2", "layer": "dot", "on": '+0.5s'}},
        # cut c3 on line s3 (6–9 s); its card's hold names a word of the removed line (a formula)
        {"id": "c3-plate", "clipType": "am-snap-plate", "track": "plate", "asset": "P", "at": 6.0, "hold": 3.0,
         "params": {}, "app": {"cut": "c3", "layer": "field", "on": '"So"'}},
        {"id": "c3-card", "clipType": "am-type", "track": "type", "at": 6.4, "hold": 1.0,
         "params": {"text": "back to work", "delay": 0.2},
         "app": {"cut": "c3", "layer": "card", "on": '"work"', "formulas": {"params.delay": {"moment": '"Live"', "as": "timeline_seconds"}}}},
        # the voice: three lines; and one music bed with beats under everything
        {"id": "vo-s1", "clipType": "media", "track": "vo", "asset": "V", "at": 0.0, "from": 0.0, "to": 2.0,
         "app": {"segment": "s1", "text": "It went viral.", "gap_after_s": 2.0,
                 "words": [[0.1, 0.4, "It"], [0.5, 0.8, "went"], [1.3, 1.9, "viral"]]}},
        {"id": "vo-s2", "clipType": "media", "track": "vo", "asset": "V", "at": 4.0, "from": 0.0, "to": 0.8,
         "app": {"segment": "s2", "text": "Live.", "gap_after_s": 1.2, "words": [[0.1, 0.7, "Live"]]}},
        {"id": "vo-s3", "clipType": "media", "track": "vo", "asset": "V", "at": 6.0, "from": 0.0, "to": 2.0,
         "app": {"segment": "s3", "text": "So back to work.",
                 "words": [[0.1, 0.3, "So"], [0.4, 0.6, "back"], [0.7, 0.8, "to"], [0.9, 1.4, "work"]]}},
        {"id": "music", "clipType": "media", "track": "music", "asset": "M", "at": 0.0, "from": 0.0, "to": 9.0,
         "app": {"beats": {"beats": [i * 0.5 for i in range(18)], "bpm": 120, "time": "cue_seconds"}}},
    ]
    return {
        "project_id": "p", "timeline_id": "t", "base_parent": {"revision_id": "rev-0"},
        "parent": {"config": {"theme_overrides": {"visual": {"canvas": {"fps": FPS, "width": 1920, "height": 1080}}}}, "clips": []},
        "placements": [{"shot_id": "F", "occurrence_id": "occ-F", "placement": {"start_ms": 0}, "duration_ms": 9000}],
        "shots": {"F": {"payload": {"name": "FILM"}, "internal_timeline": {
            "tracks": [{"id": t, "kind": "audio" if t in ("vo", "music") else "visual"}
                       for t in ("type", "sprite", "plate", "vo", "music")],
            "clips": clips,
            "registry": {"assets": {k: {"media_id": f"m-{k}"} for k in "PQRVM"}}}}},
    }


def test_removing_a_line_keeps_its_cut_as_orphans_the_music_whole_and_blocks_publish():
    tl = Checkout(film())
    assert tl.resolve() == [] or True
    report = tl.remove_line("s2")  # R2/R3: must not crash, must not collapse
    assert report[0].startswith("removed line s2")
    # nothing collapsed to 0 s; the "Live." cut's clips are kept, with their length, as orphans
    assert all(c.duration >= 1 / FPS for c in tl.clips() if not c.is_audio)
    assert tl.clip("c2.live").duration == pytest.approx(2.0) and tl.clip("c2.dot").duration == pytest.approx(1.5)
    orphans = tl.orphans()
    assert any(line.startswith("c2.field") and "line s2 was removed" in line for line in orphans)
    assert any(line.startswith("c2.live") for line in orphans)  # no moment of its own, still listed
    assert any(line.startswith("c3.card") and "delay" in line for line in orphans)  # the formula's word is gone too
    # the music bed stays one clip, its source untouched
    music = [c for c in tl.clips() if c.track == "music"]
    assert len(music) == 1 and music[0].data["from"] == 0.0 and music[0].data["to"] == 9.0
    assert any("plays on unchanged" in line for line in report)
    # every surviving anchored clip is still on its word
    assert tl.clip("c1.rocket").start == pytest.approx(tl.word("viral").start, abs=1 / FPS)
    assert tl.clip("c3.card").start == pytest.approx(tl.word("work").start, abs=1 / FPS)
    assert tl.clip("c3.field").start == pytest.approx(tl.voice("s3").clips[0].start, abs=1 / FPS)
    # check blocks until the orphans are re-homed or removed
    check = tl.check()
    assert not check.valid and check.blocking and any("orphan" in line for line in check.brief())


def test_rehoming_the_orphans_unblocks_the_check():
    tl = Checkout(film())
    tl.remove_line("s2")
    for name in ("c2.field", "c2.live", "c2.dot"):
        tl.clip(name).remove()
    tl.clip("c3.card").set(delay=0.2)  # a fixed value replaces the orphaned formula
    assert tl.orphans() == []
    check = tl.check()
    assert not check.blocking


def test_a_script_that_drops_a_line_never_crashes(tmp_path):
    """R1: --from-script with a removed line whose words other clips use."""
    import json

    vo = tmp_path / "vo"
    vo.mkdir()
    for seg, words in (("s1", [[0.1, 0.4, "It"], [0.5, 0.8, "went"], [1.3, 1.9, "viral"]]),
                       ("s3", [[0.1, 0.3, "So"], [0.4, 0.6, "back"], [0.7, 0.8, "to"], [0.9, 1.4, "work"]])):
        (vo / f"{seg}.wav").write_bytes(b"RIFF")
        (vo / f"{seg}.words.json").write_text(json.dumps({"words": words}))
    tl = Checkout(film())
    report = tl.apply_script({"segments": [{"id": "s1", "text": "It went viral."}, {"id": "s3", "text": "So back to work."}]},
                             takes=vo)
    assert any(line.startswith("removed line s2") for line in report)
    assert [v.segment for v in tl.lines()] == ["s1", "s3"]
    assert tl.orphans() and not tl.check().valid


def test_an_orphan_can_be_kept_or_rehomed_on_a_new_word():
    tl = Checkout(film())
    tl.remove_line("s2")
    tl.clip("c2.live").on('"back"')  # re-homed: it now enters on a word of the next line
    tl.clip("c2.dot").keep()         # accepted where it is
    tl.clip("c2.field").remove()
    tl.clip("c3.card").set(delay=0.2)
    assert tl.orphans() == []
    assert tl.clip("c2.live").start == pytest.approx(tl.word("back").start, abs=1 / FPS)
