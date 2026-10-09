"""The timeline editing API: timeline seconds everywhere, anchors that keep intent, ripple that keeps sync."""
from __future__ import annotations

import copy
from pathlib import Path
import json

import pytest

from astrid.sdk.timeline_checkout import Checkout, TimelineEditError, three_way

FPS = 30


def _shot(clips, tracks=None):
    return {
        "payload": {"name": "SHOT"},
        "internal_timeline": {
            "tracks": tracks or [
                {"id": "type", "kind": "visual"}, {"id": "sprite", "kind": "visual"}, {"id": "plate", "kind": "visual"},
                {"id": "vo", "kind": "audio"}, {"id": "music", "kind": "audio"},
            ],
            "clips": clips,
            "registry": {"assets": {"P": {"media_id": "m-p"}, "R": {"media_id": "m-r"}, "Q": {"media_id": "m-q"}}},
        },
    }


def bundle():
    """Two shots: A (0–4 s) and B (4–8 s). The VO says "it went viral" in A and "live now" in B."""
    a = [
        {"id": "a-plate", "clipType": "am-snap-plate", "track": "plate", "asset": "P", "at": 0.0, "hold": 4.0, "params": {}},
        {"id": "a-rocket", "clipType": "am-sprite", "track": "sprite", "asset": "R", "at": 1.0, "hold": 1.0, "params": {"x": 10}},
        {"id": "a-vo", "clipType": "media", "track": "vo", "asset": "P", "at": 0.5, "from": 0.0, "to": 2.0,
         "app": {"segment": "s1", "words": [[0.1, 0.3, "it"], [0.4, 0.6, "went"], [0.8, 1.2, "viral"]]}},
        {"id": "a-music", "clipType": "media", "track": "music", "asset": "Q", "at": 0.0, "from": 0.0, "to": 4.0},
    ]
    b = [
        {"id": "b-plate", "clipType": "am-snap-plate", "track": "plate", "asset": "Q", "at": 0.0, "hold": 4.0, "params": {}},
        {"id": "b-type", "clipType": "am-type", "track": "type", "at": 2.0, "hold": 1.0, "params": {"text": "Live."}},
        {"id": "b-vo", "clipType": "media", "track": "vo", "asset": "P", "at": 1.9, "from": 0.0, "to": 1.0,
         "app": {"segment": "s2", "words": [[0.1, 0.4, "Live"], [0.5, 0.8, "now"]]}},
    ]
    return {
        "project_id": "p", "timeline_id": "t",
        "base_parent": {"revision_id": "rev-0"},
        "parent": {"config": {"theme_overrides": {"visual": {"canvas": {"fps": FPS, "width": 1920, "height": 1080}}}}, "clips": []},
        "placements": [
            {"shot_id": "A", "occurrence_id": "occ-A", "placement": {"start_ms": 0}, "duration_ms": 4000},
            {"shot_id": "B", "occurrence_id": "occ-B", "placement": {"start_ms": 4000}, "duration_ms": 4000},
        ],
        "shots": {"A": _shot(a), "B": _shot(b)},
    }


def test_words_are_in_timeline_seconds_with_stable_ids():
    tl = Checkout(bundle())
    viral = tl.word("viral")
    assert (viral.id, viral.start, viral.end) == ("s1:2", pytest.approx(1.3), pytest.approx(1.7))
    live = tl.word("Live")
    assert live.start == pytest.approx(4.0 + 1.9 + 0.1)  # shot B starts at 4 s
    assert tl.word("s2:1").text == "now"
    assert tl.word("went viral").id == "s1:1"  # phrases match their first word


def test_ambiguous_and_missing_words_say_what_to_do():
    data = bundle()
    data["shots"]["B"]["internal_timeline"]["clips"][2]["app"]["words"].append([0.9, 1.0, "viral"])
    tl = Checkout(data)
    with pytest.raises(TimelineEditError, match="spoken 2 times"):
        tl.word("viral")
    assert tl.word("viral", n=2).segment == "s2"
    assert tl.word("viral", after=3).segment == "s2"
    with pytest.raises(TimelineEditError, match="did you mean"):
        tl.word("virl")


def test_clip_lookup_by_asset_id_prefix_text_and_near():
    tl = Checkout(bundle())
    assert tl.clip("R").id == "a-rocket"
    assert tl.clip("a-rock").id == "a-rocket"
    assert tl.clip("Live.").id == "b-type"
    with pytest.raises(TimelineEditError, match="matches 3 clips"):
        tl.clip("P")  # a-plate and a-vo share the asset key
    assert tl.clip("P", near=0.1).id == "a-plate"


def test_enter_at_a_word_moves_in_timeline_seconds_and_stores_the_moment():
    tl = Checkout(bundle())
    rocket = tl.clip("R").enter_at("viral")
    assert rocket.start == pytest.approx(1.3)
    assert rocket.data["at"] == pytest.approx(1.3)  # shot A starts at 0
    assert rocket.duration == pytest.approx(1.0)
    assert rocket.anchor == '"viral"'  # stored as a moment; the seconds are only the cache
    plate = tl.clip("b-type").enter_at(5.0)  # a plain time: shot-relative at = 1.0 in shot B
    assert plate.data["at"] == pytest.approx(1.0)
    assert plate.anchor is None


def test_nudge_frames_keeps_the_anchor_offset():
    tl = Checkout(bundle())
    rocket = tl.clip("R").enter_at("viral").nudge(frames=3)
    assert rocket.start == pytest.approx(1.4)
    assert rocket.anchor == '"viral" +3f'  # a nudge becomes an offset, never a fixed number
    rocket.extend(0.5)
    assert rocket.duration == pytest.approx(1.5)


def test_ripple_delete_across_a_shot_boundary_keeps_audio_in_sync():
    tl = Checkout(bundle())
    before = {c.id: c.start for c in tl.clips()}
    removed = tl.ripple_delete(3.5, 4.5)  # 0.5 s of shot A and 0.5 s of shot B
    assert removed == pytest.approx(1.0)
    assert tl.duration == pytest.approx(7.0)
    assert tl.clip("b-vo").start == pytest.approx(before["b-vo"] - 1.0)
    assert tl.word("Live").start == pytest.approx(6.0 - 1.0)
    assert tl.clip("b-type").start == pytest.approx(before["b-type"] - 1.0)
    music = [c for c in tl.clips() if c.track == "music"]
    assert [round(m.duration, 3) for m in music] == [3.5]  # music ends at the cut, nothing after it shifts out of sync


def test_close_gap_before_a_word_ripples_everything_after():
    tl = Checkout(bundle())
    gap_start = tl.word("viral").end  # 1.7 s; "Live" starts at 6.0 s
    later = tl.clip("b-type").start
    removed = tl.close_gap(before="Live")
    assert removed == pytest.approx(6.0 - gap_start)
    assert tl.word("Live").start == pytest.approx(gap_start)
    assert tl.clip("b-type").start == pytest.approx(later - removed)


def test_voice_replace_moves_anchored_clips_with_their_words():
    tl = Checkout(bundle())
    tl.clip("b-type").enter_at("now")
    old_now = tl.word("now").start
    moved = tl.voice("s1").replace("Q", words=[[0.1, 0.5, "it"], [0.6, 1.0, "went"], [1.4, 2.4, "viral"]])
    assert tl.word("viral").start == pytest.approx(0.5 + 1.4)
    assert tl.word("now").start > old_now  # the longer take rippled the next line later
    assert tl.clip("b-type").start == pytest.approx(tl.word("now").start, abs=1 / FPS)  # its anchor followed (on a frame)
    assert any(cid == "b-type" for cid, _a, _b in moved)


def test_formulas_resolve_like_a_spreadsheet():
    data = bundle()
    data["parent"]["config"]["slots"] = {"HAND": {"hand_mark": {"x": 1200, "y": 600}}}
    data["shots"]["A"]["internal_timeline"]["clips"][1]["app"] = {"formulas": {
        "params.x": {"mark": "HAND", "axis": "x", "offset": -10},
        "params.words": {"words_of": "s1"},
        "params.keyframes[1].frame": {"word": "s1:2", "text": "viral", "as": "clip_frame"},
    }}
    tl = Checkout(data)
    tl.resolve()
    rocket = tl.clip("R")
    assert rocket.params["x"] == 190
    # only the words that overlap the clip (1.0–2.0 s): "went" (0.9–1.1) and "viral" (1.3–1.7), clip-relative
    assert rocket.params["words"] == [[pytest.approx(-0.1), pytest.approx(0.1)], [pytest.approx(0.3), pytest.approx(0.7)]]
    assert rocket.params["keyframes"][1]["frame"] == 9  # viral at 1.3 s, the clip starts at 1.0 s


def test_save_load_round_trip_shows_both_clocks_and_honours_timeline_edits(tmp_path):
    tl = Checkout(bundle())
    path = tl.save(tmp_path / "t.json")
    raw = json.loads(path.read_text())
    clip = raw["shots"]["B"]["internal_timeline"]["clips"][1]
    assert clip["_timeline"] == {"start": 6.0, "end": 7.0} and clip["at"] == 2.0
    assert raw["_index"]["words"][0] == ["s1:0", "it", 0.6, 0.8]
    clip["_timeline"]["start"] = 6.5  # edit in timeline seconds
    path.write_text(json.dumps(raw))
    again = Checkout.load(path)
    assert again.clip("b-type").data["at"] == pytest.approx(2.5)
    assert again.clip("b-type").duration == pytest.approx(1.0)
    assert "_index" not in again.document() and '"_timeline"' not in json.dumps(again.document())


def test_three_way_merges_disjoint_edits_and_refuses_overlapping_ones():
    base = bundle()
    ours, theirs = copy.deepcopy(base), copy.deepcopy(base)
    Checkout(ours).clip("R").nudge(0.5)
    Checkout(theirs).clip("b-type").set(text="LIVE!")
    merged, conflicts = three_way(base, theirs, ours)
    assert not conflicts
    m = Checkout(merged)
    assert m.clip("R").start == pytest.approx(1.5) and m.clip("b-type").params["text"] == "LIVE!"
    Checkout(theirs).clip("R").nudge(-0.5)
    _merged, conflicts = three_way(base, theirs, ours)
    assert conflicts and "a-rocket" in conflicts[0]


def test_add_overlay_on_a_word_in_a_corner():
    tl = Checkout(bundle())
    sparkle = tl.add("am-type", at="Live", hold=0.5, params={"text": "*", "width": 200}, corner="top-right")
    assert sparkle.start == pytest.approx(tl.word("Live").start)
    assert sparkle.shot_id == "B" and sparkle.params["x"] == 1920 - 96 - 200 and sparkle.params["y"] == 54
    assert sparkle.anchor == '"Live"'


# ---- re-flow: the voice track lays out the film ------------------------------------------

def _with_beats(data):
    music = data["shots"]["A"]["internal_timeline"]["clips"][3]
    music["app"] = {"beats": {"beats": [i * 0.5 for i in range(8)], "bpm": 120, "time": "cue_seconds"}}
    return data


def test_a_longer_take_reflows_the_film_and_keeps_the_gap_overlays_and_music():
    tl = Checkout(_with_beats(bundle()))
    tl.clip("b-type").enter_at("now")
    gap = tl.gaps()["s1"]  # 1.7 → 6.0
    moved = tl.voice("s1").replace("Q", words=[[0.1, 0.5, "it"], [0.6, 1.0, "went"], [1.4, 2.4, "viral"]])
    assert tl.word("viral").end == pytest.approx(2.9)
    assert tl.word("Live").start - tl.word("viral").end == pytest.approx(gap, abs=1 / FPS)  # the silence is kept
    assert tl.clip("b-type").start == pytest.approx(tl.word("now").start, abs=1 / FPS)  # the overlay is still on its word
    assert [m[0] for m in moved] == ["b-type"]
    music = sorted((c for c in tl.clips() if c.track == "music"), key=lambda c: c.start)
    assert [round(m.start, 3) for m in music] == [0.0, 1.5]  # the seam is on the beat before the change
    assert music[1].data["from"] == pytest.approx(1.5 - 1.2)  # it repeats 1.2 s: no hole, and in sync after it
    assert any("repeats 1.200 s = 2.40 beats" in line for line in tl.report)


def test_a_declared_gap_is_honoured():
    tl = Checkout(bundle())
    tl.voice("s1").set_gap_after(0.5)
    assert tl.word("Live").start == pytest.approx(tl.word("viral").end + 0.5, abs=1 / FPS)
    assert tl.voice("s1").gap_after == 0.5
    tl.voice("s1").replace("Q", words=[[0.1, 0.3, "it"], [0.4, 0.6, "went"], [0.8, 1.0, "viral"]])  # shorter
    assert tl.word("Live").start == pytest.approx(tl.word("viral").end + 0.5, abs=1 / FPS)


def test_insert_and_remove_a_line():
    tl = Checkout(bundle())
    tl.clip("b-type").enter_at("now")
    gap = tl.gaps()["s1"]
    report = tl.insert_line("s1b", "Q", words=[[0.0, 0.4, "and"], [0.5, 1.0, "then"]], after="s1", gap_after=0.3)
    assert report[0].startswith("inserted line s1b")
    assert [v.segment for v in tl.lines()] == ["s1", "s1b", "s2"]
    assert tl.word("and").start == pytest.approx(tl.word("viral").end + gap, abs=1 / FPS)
    assert tl.word("Live").start == pytest.approx(tl.word("then").end + 0.3, abs=1 / FPS)
    assert tl.clip("b-type").start == pytest.approx(tl.word("now").start, abs=1 / FPS)
    tl.remove_line("s1b")
    assert [v.segment for v in tl.lines()] == ["s1", "s2"]
    assert tl.word("Live").start == pytest.approx(tl.word("viral").end + gap, abs=1 / FPS)
    assert tl.clip("b-type").start == pytest.approx(tl.word("now").start, abs=1 / FPS)


def test_formula_post_steps_cover_the_generator_maths():
    data = bundle()
    data["shots"]["A"]["internal_timeline"]["clips"][1]["app"] = {"formulas": {
        # the LIFETIME flip: 50 frames before "viral", never below 0
        "params.flip": {"word": "s1:2", "as": "clip_frame", "offset_frames": -50, "min": 0},
        # a hop keyframe on an even frame (stepFrames 2), at least frame 4, then +4
        "params.keyframes[0].frame": {"word": "s1:2", "as": "clip_frame", "min": 4, "step": 2},
        "params.keyframes[1].frame": {"word": "s1:2", "as": "clip_frame", "offset_frames": 4, "min": 8, "step": 2},
        # held until "Live", floored to a whole frame
        "hold": {"word": "s2:0", "as": "clip_seconds", "snap": "floor"},
    }}
    tl = Checkout(data)
    tl.resolve()
    rocket = tl.clip("R")  # starts at 1.0; "viral" at 1.3 (frame 9); "Live" at 6.0
    assert rocket.params["flip"] == 0
    assert rocket.params["keyframes"][0]["frame"] == 8 and rocket.params["keyframes"][1]["frame"] == 12
    assert rocket.duration == pytest.approx(5.0)


def test_an_anchored_cut_rolls_so_the_picture_track_has_no_hole():
    data = bundle()
    clips = data["shots"]["A"]["internal_timeline"]["clips"]
    clips[0]["hold"] = 2.0
    clips.insert(1, {"id": "a-plate2", "clipType": "am-snap-plate", "track": "plate", "asset": "Q", "at": 2.0, "hold": 2.0, "params": {}})
    clips[0]["app"] = {"cut": "c1"}
    clips[1]["app"] = {"cut": "c2", "on": '"viral"'}
    tl = Checkout(data)
    tl.retime()  # "viral" is at 1.3: the cut rolls back from 2.0 to 1.3
    assert tl.clip("a-plate2").start == pytest.approx(1.3) and tl.clip("a-plate2").end == pytest.approx(4.0)
    assert tl.clip("a-plate").end == pytest.approx(1.3)


def test_a_tightened_line_keeps_its_inner_silence():
    data = bundle()
    vo = data["shots"]["A"]["internal_timeline"]["clips"][2]
    first = copy.deepcopy(vo)
    first.update({"id": "a-vo-0", "to": 0.7})
    first["app"] = {"segment": "s1", "words": [[0.1, 0.3, "it"], [0.4, 0.6, "went"]], "gap_after_s": 0.5}
    vo.update({"id": "a-vo-1", "at": 1.3, "from": 0.7, "to": 2.0})
    vo["app"] = {"segment": "s1", "words": [[0.1, 0.5, "viral"]]}
    data["shots"]["A"]["internal_timeline"]["clips"].insert(2, first)
    tl = Checkout(data)
    assert [w.text for w in tl.voice("s1").words] == ["it", "went", "viral"]
    report = tl.reflow()  # "went" ends at 1.1; "viral" starts at 1.4: the declared 0.5 s moves it (and all after) to 1.6
    assert tl.word("viral").start == pytest.approx(1.6, abs=1 / FPS)
    assert tl.word("Live").start == pytest.approx(6.2, abs=1 / FPS)
    assert report and report[0].startswith("s1 ('viral')")


def test_a_sequence_fit_races_through_its_steps_and_lands_on_the_word():
    data = bundle()
    clips = data["shots"]["A"]["internal_timeline"]["clips"]
    plate = clips.pop(0)
    for i in range(3):
        step = copy.deepcopy(plate)
        step.update({"id": f"seq-{i}", "at": round(i * 4 / 3, 6), "hold": round(4 / 3, 6), "asset": "P" if i % 2 else "Q"})
        step["app"] = {"sequence": "seq", "sequence_index": i}
        clips.append(step)
    clips[-3]["app"]["sequence_fit"] = {"land": {"word": "s1:2", "text": "viral"}, "lead": [12], "race": [6, 4, 2],
                                        "cycle": ["P", "Q"], "then": "R"}
    tl = Checkout(data)
    changes = tl.resolve()  # "viral" at 1.3 s = frame 39: 12 lead, then 6+4+2+2+2+2+2+2+2+2+2 = 27, then R to frame 120
    steps = sorted((c for c in tl.clips() if c.id.startswith("seq-")), key=lambda c: c.start)
    assert any("lands on 'viral' at frame 39" in line for line in changes)
    assert [round(c.duration * FPS) for c in steps][:4] == [12, 6, 4, 2]
    landing = next(c for c in steps if c.asset == "R")
    assert landing.start == pytest.approx(1.3) and landing.end == pytest.approx(4.0)
    assert len(tl.cuts) == 2  # still one cut per shot: the steps are one sequence
    assert tl.resolve() == []  # resolving again changes nothing


def test_canvas_pixels_convert_to_the_elements_unit():
    tl = Checkout(bundle())
    assert tl.clip("R").set(x="1290px", y="-12px").params["x"] == 215  # am-sprite: logical px
    assert tl.clip("b-type").set(x="144px").params["x"] == 144


def test_narration_comes_from_the_lines_and_is_re_pinned_only_when_it_changes():
    import hashlib

    data = bundle()
    data["shots"]["A"]["internal_timeline"]["clips"][2]["app"]["text"] = "It went viral."
    data["shots"]["A"]["payload"]["text_bindings"] = [{"kind": "voiceover_script", "head": 3,
                                                       "content_hash": "sha256:" + hashlib.sha256(b"It went viral.\n").hexdigest()}]
    tl = Checkout(data)
    assert tl.narration() == {"A": "It went viral.\n"}  # shot B's line declares no text: not managed
    assert tl.narration_changes() == {}
    tl.voice("s1").replace("Q", words=[[0.1, 0.5, "it"], [0.6, 1.0, "really"], [1.1, 1.4, "went"], [1.5, 2.0, "viral"]],
                           text="It really went viral.")

    class Shots:
        calls = []

        def set_text_binding(self, project, **kw):
            self.calls.append(kw)
            return {"kind": "voiceover_script", "head": kw["expected_head"] + 1, "content_hash": "new"}

    class Client:
        shots = Shots()

    assert tl.pin_narration(Client(), idempotency_key="k") == ["A"]
    call = Shots.calls[0]
    assert call["text"] == "It really went viral.\n" and call["expected_head"] == 3
    assert tl.bundle["shots"]["A"]["payload"]["text_bindings"][0]["head"] == 4


def test_a_stand_in_is_remembered_and_swapped_when_the_real_asset_exists():
    tl = Checkout(bundle())
    clip = tl.add("am-sprite", at="Live", asset="TOWER", standin="R", params={"x": 10})
    assert clip.asset == "R" and "TOWER" in json.dumps(clip.data)
    assert tl.fill_standins() == []  # still missing
    tl.bundle["shots"]["B"]["internal_timeline"]["registry"]["assets"]["TOWER"] = {"media_id": "m-t"}
    assert tl.fill_standins() == [f"{clip.id}: stand-in → TOWER"]
    assert tl.clip(clip.id).asset == "TOWER"


def test_apply_script_swaps_changed_takes_and_adds_new_lines(tmp_path, monkeypatch):
    import astrid.sdk.timeline_checkout as tc

    monkeypatch.setattr(tc, "import_media", lambda path, project: {"media_id": f"m-{Path(path).stem}"})
    vo = tmp_path / "vo"
    vo.mkdir()
    (vo / "s1.wav").write_bytes(b"RIFF")
    (vo / "s1.words.json").write_text(json.dumps({"words": [{"start_s": 0.1, "end_s": 0.5, "word": "it"},
                                                             {"start_s": 0.6, "end_s": 1.0, "word": "went"},
                                                             {"start_s": 1.4, "end_s": 2.4, "word": "viral"}]}))
    (vo / "s1b.wav").write_bytes(b"RIFF")
    (vo / "s1b.words.json").write_text(json.dumps({"words": [[0.0, 0.5, "really"]]}))
    (vo / "s2.wav").write_bytes(b"RIFF")
    (vo / "s2.words.json").write_text(json.dumps({"words": [[0.1, 0.4, "Live"], [0.5, 0.8, "now"]]}))  # unchanged
    script = {"segments": [{"id": "s1", "text": "It went viral."}, {"id": "s1b", "text": "Really.", "gap_after_s": 0.4},
                           {"id": "s2", "text": "Live now."}]}
    tl = Checkout(bundle())
    tl.clip("b-type").enter_at("now")
    report = tl.apply_script(script, takes=vo)
    assert [v.segment for v in tl.lines()] == ["s1", "s1b", "s2"]
    assert "line s1: new take" in report and any(r.startswith("inserted line s1b") for r in report)
    assert tl.word("viral").end == pytest.approx(2.9)
    assert tl.word("Live").start == pytest.approx(tl.word("really").end + 0.4, abs=1 / FPS)
    assert tl.clip("b-type").start == pytest.approx(tl.word("now").start, abs=1 / FPS)
    assert tl.voice("s2").text == "Live now."
