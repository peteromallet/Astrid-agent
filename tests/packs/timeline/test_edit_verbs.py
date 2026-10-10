"""The timelines working-copy verbs: parsing, edits on a local file, drafts (offline), words, duplicate ids."""
from __future__ import annotations

import copy
import json
import re

import pytest

from astrid.packs.timeline import cli
from astrid.sdk import timeline_checkout as tc
from astrid.sdk.timeline_checkout import Checkout
from astrid.sdk.timeline_duplicate import copy_content, id_map

FPS = 30


def _shot(clips, tracks=None):
    internal = {
            "tracks": tracks or [
                {"id": "type", "kind": "visual"}, {"id": "sprite", "kind": "visual"}, {"id": "plate", "kind": "visual"},
                {"id": "vo", "kind": "audio"}, {"id": "music", "kind": "audio"},
            ],
            "clips": clips,
            "registry": {"assets": {"P": {"media_id": "sha256:" + "1" * 64}, "R": {"media_id": "sha256:" + "2" * 64}, "Q": {"media_id": "sha256:" + "3" * 64}}},
    }
    payload = {"name": "SHOT", "items": [], "pools": [], "audio_bindings": [], "text_bindings": []}
    return {
        "payload": payload, "base_payload": copy.deepcopy(payload),
        "internal_timeline": internal, "base_internal_timeline": copy.deepcopy(internal),
    }


def _placement_rows():
    return {
        "occ-A": {"shot_id": "A", "occurrence_id": "occ-A", "placement": {"start_ms": 0}, "duration_ms": 4000},
        "occ-B": {"shot_id": "B", "occurrence_id": "occ-B", "placement": {"start_ms": 4000}, "duration_ms": 4000},
    }


def _shot_identity(sid):
    digest = "sha256:" + "0" * 64
    return {"shot_id": sid, "revision_id": f"rev-{sid}", "content_digest": digest, "internal_timeline_id": "t",
            "internal_timeline_revision_id": f"itr-{sid}", "internal_timeline_content_digest": digest, "item_id_map": {}}


def bundle():
    """Two shots: A (0–4 s) and B (4–8 s). Shot A's VO says "it went viral"; B says "Live now"."""
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
        "project_id": "p", "timeline_id": "t", "schema_version": 1,
        "base_parent": {"revision_id": "rev-0", "content_digest": "sha256:" + "0" * 64},
        "base_parent_payload": {"config": {"theme_overrides": {"visual": {"canvas": {"fps": FPS, "width": 1920, "height": 1080}}}}, "clips": [], "registry": {}},
        "base_placements": {"placements": _placement_rows()},
        "parent": {"config": {"theme_overrides": {"visual": {"canvas": {"fps": FPS, "width": 1920, "height": 1080}}}}, "clips": []},
        "placements": [
            {"shot_id": "A", "occurrence_id": "occ-A", "placement": {"start_ms": 0}, "duration_ms": 4000},
            {"shot_id": "B", "occurrence_id": "occ-B", "placement": {"start_ms": 4000}, "duration_ms": 4000},
        ],
        "shots": {sid: {**_shot(clips), "shot_id": sid} for sid, clips in (("A", a), ("B", b))},
        "source_mapping": {"placements": _placement_rows(), "shots": {sid: _shot_identity(sid) for sid in ("A", "B")}},
    }


def _parse(*argv):
    parser = cli.build_parser(None)
    return parser.parse_args(["timelines", *argv][1:] if argv and argv[0] == "timelines" else list(argv))


def _run(*argv) -> int:
    parsed = _parse(*argv)
    return parsed.handler(parsed)


@pytest.fixture
def local(tmp_path):
    path = tmp_path / "t.checkout.json"
    Checkout(bundle()).save(path)
    return path


# ---- argument parsing ------------------------------------------------------------------

@pytest.mark.parametrize("argv, handler", [
    (["checkout", "tl1", "--project", "P"], cli._cmd_checkout),
    (["checkout", "tl1", "--project", "P", "--draft", "alt", "--fresh"], cli._cmd_checkout),
    (["edit", "tl1", "--project", "P", "--clip", "ROCKET", "--near", "viral", "--at-word", "viral"], cli._cmd_edit),
    (["edit", "--file", "x.json", "--cut", "3", "--nudge-frames", "-2", "--set", "x=5", "--set", 'y="a"'], cli._cmd_edit),
    (["words", "tl1", "--project", "P", "--find", "viral", "--range", "1:02..1:10"], cli._cmd_words),
    (["status", "tl1", "--project", "P"], cli._cmd_status),
    (["check", "tl1"], cli._cmd_check),
    (["publish", "tl1", "-m", "rocket on viral", "--force"], cli._cmd_publish),
    (["discard", "tl1", "--project", "P"], cli._cmd_discard),
    (["duplicate", "tl1", "--project", "P", "--slug", "dup", "--name", "Dup"], cli._cmd_duplicate),
    (["create", "--project", "P", "--slug", "abc"], cli._cmd_create),
])
def test_each_verb_parses_to_its_handler(argv, handler):
    assert _parse(*argv).handler is handler


def test_edit_parses_operations_and_values():
    parsed = _parse("edit", "--file", "x.json", "--at-word", "viral", "--n", "2", "--offset", "0.2",
                    "--nudge-frames", "-3", "--set", "x=5", "--set", 'label="hi"', "--insert", "1:02:0.5")
    assert (parsed.at_word, parsed.n, parsed.offset, parsed.nudge_frames) == ("viral", 2, 0.2, -3)
    assert parsed.insert == ("1:02", 0.5)
    assert cli._parse_set(["x=5", 'label="hi"', "raw=text"]) == {"x": 5, "label": "hi", "raw": "text"}


def test_publish_requires_a_message():
    with pytest.raises(SystemExit):
        _parse("publish", "tl1")


# ---- edit on a local checkout file ----------------------------------------------------

def test_edit_at_word_moves_the_clip_and_says_so(local, capsys):
    assert _run("edit", "--file", str(local), "--clip", "R", "--at-word", "viral") == 0
    out = capsys.readouterr().out
    assert "✎ a-rocket" in out and 'now enters on "viral" = 1.30 s (was 1.00 s, +0.30 s)' in out
    assert 'now enters on "viral"' in out
    assert "next: timelines show" in out.splitlines()[-1] or "next:" in out
    reloaded = Checkout.load(local)
    rocket = reloaded.clip("a-rocket")
    assert rocket.anchor == '"viral"'


def test_edit_nudge_frames_set_and_close_gap(local, capsys):
    assert _run("edit", "--file", str(local), "--clip", "R", "--nudge-frames", "3", "--set", "x=42") == 0
    rocket = Checkout.load(local).clip("a-rocket")
    assert rocket.get("x") == 42 and rocket.params["x"] == 7  # canvas px in, stored on the sprite grid (÷6)
    assert rocket.start == pytest.approx(1.1)
    capsys.readouterr()

    assert _run("edit", "--file", str(local), "--close-gap-before", "now") == 0
    out = capsys.readouterr().out
    assert "next:" in out.splitlines()[-1]


def test_edit_needs_one_selector_and_an_operation(local, capsys):
    assert _run("edit", "--file", str(local)) == 2
    assert _run("edit", "--file", str(local), "--clip", "R") == 2
    assert "say what to do" in capsys.readouterr().err


def test_edit_error_exits_2_with_the_message(local, capsys):
    assert _run("edit", "--file", str(local), "--clip", "R", "--at-word", "nosuchword") == 2
    assert "nobody says" in capsys.readouterr().err


# ---- words -----------------------------------------------------------------------------

def test_words_find_format_is_one_line_per_match(local, capsys):
    assert _run("words", "--file", str(local), "--find", "viral") == 0
    lines = capsys.readouterr().out.splitlines()
    assert re.fullmatch(r"  \d+\.\d{3}–\d+\.\d{3}  viral  \[s1:2\]  (cut \d+|c\d+[a-z]?)", lines[0]), lines[0]
    assert len(lines) == 2  # one match plus the next-step line
    assert lines[-1].startswith("next: ")


def test_words_range_limits_the_listing(local, capsys):
    assert _run("words", "--file", str(local), "--range", "0..2") == 0
    texts = [ln.split()[1] for ln in capsys.readouterr().out.splitlines() if ln.startswith("  ")]
    assert texts == ["it", "went", "viral"]


# ---- the duplicate id mapping (pure part) ----------------------------------------------

def test_id_map_keeps_suffixes_and_is_one_to_one():
    assert id_map(["almost-ready-90-v7-ch01-tomorrow", "almost-ready-90-v7-ch02-vision"], "dup") == {
        "almost-ready-90-v7-ch01-tomorrow": "dup-ch01-tomorrow",
        "almost-ready-90-v7-ch02-vision": "dup-ch02-vision",
    }
    assert id_map(["A", "B"], "dup") == {"A": "dup-A", "B": "dup-B"}
    with pytest.raises(tc.TimelineEditError):
        id_map(["A", "A"], "dup")


def test_copy_content_gives_new_shot_and_occurrence_ids_and_keeps_identity():
    source = bundle()
    target = {
        "project_id": "p", "timeline_id": "dup-tl", "base_parent": {"revision_id": "rev-empty"},
        "parent": {"config": {}, "clips": []}, "placements": [], "shots": {},
        "source_mapping": {"placements": {}, "shots": {}},
    }
    out, notes = copy_content(source, target, new_slug="dup")
    assert out["base_parent"] == {"revision_id": "rev-empty"} and out["timeline_id"] == "dup-tl"
    assert sorted(out["shots"]) == ["dup-A", "dup-B"]
    assert [row["occurrence_id"] for row in out["placements"]] == ["occ-dup-A", "occ-dup-B"]
    assert out["placements"][0]["duration_ms"] == 4000
    # the internal clips are copied with their own ids (they are per shot)
    assert [c["id"] for c in out["shots"]["dup-A"]["internal_timeline"]["clips"]] == \
        [c["id"] for c in source["shots"]["A"]["internal_timeline"]["clips"]]
    assert notes == []


def test_copy_content_reports_dropped_text_bindings():
    source = bundle()
    source["shots"]["A"]["payload"]["text_bindings"] = [{"binding_id": "b1"}]
    target = {"project_id": "p", "timeline_id": "t2", "base_parent": {}, "parent": {}, "placements": [], "shots": {},
              "source_mapping": {"placements": {}, "shots": {}}}
    out, notes = copy_content(source, target, new_slug="dup")
    assert out["shots"]["dup-A"]["payload"]["text_bindings"] == []
    assert notes and "1 narration text binding" in notes[0]


# ---- drafts, offline: data root in tmp, resolve_ids and fetch_bundle replaced ----------------

def test_draft_flow_checkout_edit_status_discard(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("BANODOCO_LOCAL_DATA_ROOT", str(tmp_path / "data"))
    monkeypatch.setattr(tc, "resolve_ids", lambda project, timeline, client=None: ("p", "t", "rev-0"))
    monkeypatch.setattr(tc, "fetch_bundle", lambda project, timeline, revision_id=None, client=None: copy.deepcopy(bundle()))

    assert _run("checkout", "t", "--project", "P") == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0].startswith('working copy "main" of t · from published rev-0 · ')
    assert "next:" in out and any(line.strip().startswith("publish  timelines status t --project P") for line in out)

    assert _run("status", "t", "--project", "P") == 0
    assert capsys.readouterr().out.startswith('WORKING COPY "main" · 0 clips changed')

    assert _run("edit", "t", "--project", "P", "--clip", "R", "--at-word", "viral") == 0
    out = capsys.readouterr().out
    assert 'now enters on "viral"' in out

    assert _run("status", "t", "--project", "P") == 0
    status = capsys.readouterr().out
    assert status.startswith('WORKING COPY "main" · 1 clip changed vs published rev-0')
    assert "a-rocket" in status

    assert _run("discard", "t", "--project", "P") == 0
    assert "1 clip changed, dropped" in capsys.readouterr().out
    assert _run("status", "t", "--project", "P") == 0
    assert "no working copy" in capsys.readouterr().out


# ---- voice lines (timeline-level; the film re-flows) -----------------------------------

def _words_file(tmp_path, name, rows):
    path = tmp_path / name
    path.write_text(json.dumps(rows), encoding="utf-8")
    return str(path)


def test_voice_line_swap_reflows_and_reports(local, tmp_path, capsys):
    words = _words_file(tmp_path, "s1.json", [[0.0, 0.5, "it"], [0.6, 1.1, "went"], [1.2, 2.0, "viral"]])
    assert _run("edit", "--file", str(local), "--line", "s1", "--take", "P", "--words", words) == 0
    out = capsys.readouterr().out
    assert out.splitlines()[-1].startswith("next: timelines check")
    line = Checkout.load(local).voice("s1")
    assert [w.text for w in line.words] == ["it", "went", "viral"]


def test_voice_line_swap_needs_take_and_words(local, capsys):
    assert _run("edit", "--file", str(local), "--line", "s1") == 2
    assert "--take" in capsys.readouterr().err


def test_insert_line_after_another(local, tmp_path, capsys):
    words = _words_file(tmp_path, "s3.json", [[0.0, 0.4, "new"], [0.5, 0.9, "line"]])
    assert _run("edit", "--file", str(local), "--insert-line", "s3", "--after", "s1", "--take", "Q",
                "--words", words, "--text", "new line") == 0
    segments = [v.segment for v in Checkout.load(local).lines()]
    assert segments == ["s1", "s3", "s2"]


def test_remove_line(local, capsys):
    assert _run("edit", "--file", str(local), "--remove-line", "s2") == 0
    assert [v.segment for v in Checkout.load(local).lines()] == ["s1"]


def test_gap_after_is_set_and_reflowed_once(local, capsys):
    assert _run("edit", "--file", str(local), "--gap-after", "s1=0.5") == 0
    assert Checkout.load(local).voice("s1").gap_after == pytest.approx(0.5)


def test_gap_after_rejects_a_bad_value(local, capsys):
    assert _run("edit", "--file", str(local), "--gap-after", "s1") == 2
    assert "SEG=SECONDS" in capsys.readouterr().err


def test_from_script_with_unchanged_words_is_quiet(local, tmp_path, capsys):
    takes = tmp_path / "vo"
    takes.mkdir()
    (takes / "s1.words.json").write_text(json.dumps([[0.1, 0.3, "it"], [0.4, 0.6, "went"], [0.8, 1.2, "viral"]]))
    (takes / "s2.words.json").write_text(json.dumps([[0.1, 0.4, "Live"], [0.5, 0.8, "now"]]))
    script = tmp_path / "vo.json"
    script.write_text(json.dumps({"segments": [{"id": "s1", "text": "it went viral"},
                                               {"id": "s2", "text": "Live now"}]}))
    assert _run("edit", "--file", str(local), "--from-script", str(script), "--takes", str(takes)) == 0
    assert [v.segment for v in Checkout.load(local).lines()] == ["s1", "s2"]


def test_timeline_level_voice_ops_exclude_a_clip_selector(local, tmp_path, capsys):
    assert _run("edit", "--file", str(local), "--clip", "R", "--remove-line", "s2") == 2
    assert "separate" in capsys.readouterr().err


# ---- lint and diff read the working copy (stub client: the published head is the synthetic bundle)

class _Opened:
    def __init__(self, data):
        self.ok, self.data, self.error, self.receipt, self.idempotency_key = True, data, None, None, None


class _Timelines:
    def open_bundle(self, project, ref, revision_id=None):
        return _Opened({"bundle": copy.deepcopy(bundle()), "timeline_id": "t", "revision_id": "rev-0",
                        "project_id": "p", "is_current_head": True, "head_revision_id": "rev-0"})


class _Client:
    timelines = _Timelines()


def _run_with_client(*argv):
    parsed = _parse(*argv)
    parsed.client = _Client()
    return parsed.handler(parsed)


@pytest.fixture
def draft_with_edit(tmp_path, monkeypatch):
    monkeypatch.setenv("BANODOCO_LOCAL_DATA_ROOT", str(tmp_path / "data"))
    monkeypatch.setattr(tc, "resolve_ids", lambda project, timeline, client=None: ("p", "t", "rev-0"))
    monkeypatch.setattr(tc, "fetch_bundle", lambda project, timeline, revision_id=None, client=None: copy.deepcopy(bundle()))
    assert _run("checkout", "t", "--project", "P") == 0
    assert _run("edit", "t", "--project", "P", "--clip", "R", "--nudge-frames", "3") == 0


def test_lint_reads_the_working_copy_by_default(draft_with_edit, capsys):
    capsys.readouterr()
    _run_with_client("lint", "t", "--project", "P")
    first = capsys.readouterr().out.splitlines()[0]
    assert first.startswith('WORKING COPY · 1 clip changed vs published rev-0')


def test_lint_published_has_no_banner(draft_with_edit, capsys):
    capsys.readouterr()
    _run_with_client("lint", "t", "--project", "P", "--published")
    assert not capsys.readouterr().out.startswith("WORKING COPY")


def test_diff_without_from_compares_head_with_working_copy(draft_with_edit, capsys):
    capsys.readouterr()
    assert _run_with_client("diff", "t", "--project", "P") == 0
    out = capsys.readouterr().out
    assert out.startswith('WORKING COPY · 1 clip changed vs published rev-0')
    assert "a-rocket" in out
    assert "working copy" in out


def test_edit_set_on_a_formula_param_replaces_it_and_says_so(tmp_path, capsys):
    from tests.sdk.test_timeline_checkout import _formula_bundle

    path = tmp_path / "f.checkout.json"
    tl = Checkout(_formula_bundle())
    tl.resolve()
    tl.save(path)
    _run("edit", "--file", str(path), "--clip", "c1.rocket", "--set", "x=120")  # (the synthetic bundle is not publishable)
    out = capsys.readouterr().out
    assert "x was ƒ(HAND -42) → now fixed = 120" in out and "no change" not in out
    assert Checkout.load(path).clip("c1.rocket").get("x") == 120


def test_edit_set_refuses_a_param_the_element_does_not_declare(local, capsys):
    assert _run("edit", "--file", str(local), "--clip", "R", "--set", "sclae=3") == 2
    err = capsys.readouterr().err
    assert "has no param sclae (did you mean scale?)" in err


def test_undo_steps_back_through_the_working_copy(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("BANODOCO_LOCAL_DATA_ROOT", str(tmp_path / "data"))
    monkeypatch.setattr(tc, "resolve_ids", lambda project, timeline, client=None: ("p", "t", "rev-0"))
    monkeypatch.setattr(tc, "fetch_bundle", lambda project, timeline, revision_id=None, client=None: copy.deepcopy(bundle()))
    assert _run("checkout", "t", "--project", "P") == 0
    _run("edit", "t", "--project", "P", "--clip", "R", "--nudge-frames", "3")
    _run("edit", "t", "--project", "P", "--clip", "R", "--nudge-frames", "3")
    path = tc.find_draft("P", "t")
    assert Checkout.load(path).clip("a-rocket").start == pytest.approx(1.2)
    capsys.readouterr()
    assert _run("undo", "t", "--project", "P") == 0
    assert "undid 1 edit(s)" in capsys.readouterr().out
    assert Checkout.load(path).clip("a-rocket").start == pytest.approx(1.1)
    assert _run("undo", "t", "--project", "P") == 0
    assert Checkout.load(path).clip("a-rocket").start == pytest.approx(1.0)


def test_undo_is_one_edit_per_step_and_redo_goes_forward(tmp_path, monkeypatch, capsys):
    """P1: several edits (verbs and Python, some before one save), undo one at a time, then redo."""
    monkeypatch.setenv("BANODOCO_LOCAL_DATA_ROOT", str(tmp_path / "data"))
    monkeypatch.setattr(tc, "resolve_ids", lambda project, timeline, client=None: ("p", "t", "rev-0"))
    monkeypatch.setattr(tc, "fetch_bundle", lambda project, timeline, revision_id=None, client=None: copy.deepcopy(bundle()))
    assert _run("checkout", "t", "--project", "P") == 0
    for _ in range(3):
        _run("edit", "t", "--project", "P", "--clip", "R", "--nudge-frames", "3")  # three verb edits
    tl = Checkout.draft("P", "t")
    for _ in range(12):  # twelve API edits, ONE save
        tl.clip("a-rocket").nudge(frames=3)
    tl.save()
    path = tc.find_draft("P", "t")
    start = lambda: round(Checkout.load(path).clip("a-rocket").start * 30)  # noqa: E731
    assert start() == 30 + 45
    capsys.readouterr()
    assert _run("undo", "t", "--project", "P") == 0
    out = capsys.readouterr().out
    assert "undid" in out and "nudge" in out and start() == 30 + 42  # exactly one edit back
    assert "restored:" in out and "a-rocket" in out and "2.500 → 2.400" in out  # what is back, not the flags
    assert _run("undo", "t", "13", "--project", "P") == 0
    assert start() == 30 + 3
    assert _run("redo", "t", "--project", "P", "--steps", "2") == 0
    assert start() == 30 + 9


def test_edit_beats_attaches_a_grid_from_a_file_and_says_what_moved(local, tmp_path, capsys):
    tl = Checkout.load(local)
    tl.clip("a-music").data["app"] = {"beats": {"beats": [i * 0.5 for i in range(8)], "time": "cue_seconds"}}
    tl.clip("a-rocket").on('beat 2 after "went"')
    tl.save(local)
    grid = tmp_path / "beats.json"
    grid.write_text(json.dumps({"bpm": 100, "beats": [0.0, 0.6, 1.2, 1.8, 2.4, 3.0, 3.6]}))
    capsys.readouterr()
    assert _run("edit", "--file", str(local), "--clip", "a-music", "--beats", str(grid)) == 0
    out = capsys.readouterr().out
    assert "beats from beats.json (7 beats, 100 bpm)" in out and "a-rocket" in out
    assert Checkout.load(local).clip("a-rocket").start == pytest.approx(1.8)  # beat 2 after "went" (0.9 s): 1.2, 1.8
    assert _run("words", "--file", str(local), "went", "--beats") == 0
    assert "grid: beats.json" in capsys.readouterr().out


def test_two_draft_handles_saving_at_once_keep_both_edits(draft_with_edit, capsys):
    """Concurrency: two Checkout.draft handles on one working copy (one re-homing, one building loops)."""
    one = Checkout.draft("P", "t")
    two = Checkout.draft("P", "t")
    one.clip("b-type").set(text="Now.")
    two.clip("a-rocket").nudge(0.2)
    one.save()
    two.save()
    assert two.merged and "merged with another writer's save" in two.merged[0]
    after = Checkout.draft("P", "t")
    assert after.clip("b-type").params["text"] == "Now."
    assert after.clip("a-rocket").start == pytest.approx(1.0 + 3 / 30 + 0.2)
    three = Checkout.draft("P", "t")
    capsys.readouterr()
    assert _run("edit", "t", "--project", "P", "--clip", "R", "--nudge-frames", "1") == 0
    three.clip("a-rocket").nudge(0.5)
    with pytest.raises(tc.TimelineEditError, match="another writer"):
        three.save()


def test_edit_split_and_add_cut_from_the_cli(tmp_path, capsys):
    from pathlib import Path

    root = Path(__file__).resolve().parents[2] / "fixtures" / "timeline_editing"
    path = tmp_path / "tiny.checkout.json"
    Checkout(json.loads((root / "tiny.json").read_text(encoding="utf-8"))).save(path)
    assert _run("edit", "--file", str(path), "--split", "c1", "--on", '"viral"') == 0
    out = capsys.readouterr().out
    assert "c1a" in out
    tl = Checkout.load(path)
    assert "c1a" in tl._cut_spans()
    assert _run("edit", "--file", str(path), "--add-cut", "--on", "c2 +1s", "--after", "c2", "--cut-id", "c2x") == 0
    assert "c2x" in Checkout.load(path)._cut_spans()
    assert _run("edit", "--file", str(path), "--split", "c1") == 2  # needs --on
    help_text = cli.build_parser(object())._subparsers._group_actions[0].choices["edit"].format_help()
    assert help_text.index("--split c33 --on MOMENT") < help_text.index("clip selector (one)")  # the common five first


def test_a_cut_takes_a_why_and_a_deliberate_hold_and_a_stale_why_is_flagged(tmp_path, capsys):
    from pathlib import Path

    from astrid.core.timeline.cuts import is_deliberate
    from astrid.sdk import timeline_intent as intent
    from astrid.sdk.timeline_sheet import apply_sheet, render_sheet

    root = Path(__file__).resolve().parents[2] / "fixtures" / "timeline_editing"
    path = tmp_path / "tiny.checkout.json"
    Checkout(json.loads((root / "tiny.json").read_text(encoding="utf-8"))).save(path)
    assert _run("edit", "--file", str(path), "--cut", "c1", "--hold", "deliberate", "--why", "a reading hold") == 0
    tl = Checkout.load(path)
    pic = tl.cut_picture("c1")
    assert is_deliberate(pic.data) and tl.cut_note("c1")["why"] == "a reading hold"  # lint reads is_deliberate
    sheet = render_sheet(tl)
    assert "hold: deliberate" in sheet and apply_sheet(tl, sheet) == []
    apply_sheet(tl, sheet.replace("hold: deliberate", "hold: off"))
    assert not intent.deliberate(tl.cut_picture("c1").data) and "hold" not in tl.cut_note("c1")
    tl.clip("c1.rocket").remove()
    assert any(p.startswith("why     c1: its why was written") for p in tl.check().problems)


def test_a_sheet_exported_before_an_edit_never_reverts_it(tmp_path, monkeypatch, capsys):
    """TOP: export → edit via the verb → apply the OLD sheet with an unrelated change: both survive; a clash refuses."""
    from pathlib import Path

    from astrid.sdk.timeline_sheet import SheetError, apply_sheet, render_sheet

    root = Path(__file__).resolve().parents[2] / "fixtures" / "timeline_editing"
    tiny = json.loads((root / "tiny.json").read_text(encoding="utf-8"))
    monkeypatch.setenv("BANODOCO_LOCAL_DATA_ROOT", str(tmp_path / "data"))
    monkeypatch.setattr(tc, "resolve_ids", lambda project, timeline, client=None: ("p", "t", "rev-0"))
    monkeypatch.setattr(tc, "fetch_bundle", lambda project, timeline, revision_id=None, client=None: copy.deepcopy(tiny))
    assert _run("checkout", "t", "--project", "P") == 0
    old = render_sheet(Checkout.draft("P", "t"))
    sheet_file = tmp_path / "old.sheet"
    assert _run("edit", "t", "--project", "P", "--clip", "c1.rocket", "--set", "x=120") == 0  # after the export
    sheet_file.write_text(old.replace('"Live."', '"Now live."'), encoding="utf-8")  # an unrelated change
    capsys.readouterr()
    assert _run("apply", "t", "--project", "P", str(sheet_file)) == 0
    tl = Checkout.draft("P", "t")
    assert tl.clip("c1.rocket").get("x") == 120 and tl.clip("c2.card").text == "Now live."
    # the same line changed in both: refused, named, nothing applied
    sheet_file.write_text(old.replace("x=60", "x=90"), encoding="utf-8")
    assert _run("apply", "t", "--project", "P", str(sheet_file)) == 2
    err = capsys.readouterr().err
    assert "c1.rocket" in err and "Re-export" in err
    assert Checkout.draft("P", "t").clip("c1.rocket").get("x") == 120
