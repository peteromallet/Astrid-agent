"""The look verbs (--fade-in, --entrance, --exit, --opacity, --volume): parsing, values, and apply_look."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from astrid.packs.timeline import cli, cli_look
from astrid.sdk import timeline_checkout as tc
from astrid.sdk.timeline_checkout import Checkout

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "timeline_editing" / "tiny.json"


def _parse(*args: str):
    return cli.build_parser(None).parse_args(["edit", "t", "--project", "p", *args])


@pytest.fixture
def draft(tmp_path, monkeypatch):
    bundle = json.loads(FIXTURE.read_text(encoding="utf-8"))
    monkeypatch.setenv("ASTRID_RUNTIME_DATA_ROOT", str(tmp_path / "root"))
    monkeypatch.setattr(tc, "resolve_ids", lambda project, timeline, client=None: ("p", "t", "rev-0"))
    monkeypatch.setattr(tc, "fetch_bundle",
                        lambda project, timeline, revision_id=None, client=None: copy.deepcopy(bundle))
    return Checkout.draft("p", "t")


def test_entrance_takes_one_or_two_tokens():
    assert _parse("--clip", "c2.card", "--entrance", "fade").entrance == "fade"
    assert _parse("--clip", "c2.card", "--entrance", "fade", "8f").entrance == "fade 8f"


def test_entrance_takes_an_ease_token_as_a_third_word():
    assert _parse("--clip", "c2.card", "--entrance", "fade", "12f", "ease=out").entrance == "fade 12f ease=out"


def test_entrance_with_four_tokens_is_an_error(capsys):
    with pytest.raises(SystemExit):
        _parse("--clip", "c2.card", "--entrance", "fade", "8f", "ease=out", "extra")
    err = capsys.readouterr().err
    assert "takes NAME [DURATION] [ease=E]" in err
    assert "e.g. --entrance fade 12f ease=out" in err


def test_fade_in_takes_duration_and_an_ease_token(capsys):
    assert _parse("--clip", "c2.card", "--fade-in", "8f").fade_in == "8f"
    assert _parse("--clip", "c2.card", "--fade-in", "8f", "ease=out").fade_in == "8f ease=out"
    with pytest.raises(SystemExit):
        _parse("--clip", "c2.card", "--fade-in", "8f", "ease=out", "extra")
    assert "takes DURATION [ease=E|curve=NAME]" in capsys.readouterr().err


def test_audio_fade_accepts_registered_curve_and_persists_it(draft):
    from astrid.core.timeline.audio_curves import names

    assert "equal-power" in names()
    parsed = _parse("--clip", "a-music", "--fade-in", "3s", "curve=equal-power")
    assert parsed.fade_in == "3s curve=equal-power"
    clip = draft.clip("a-music")
    cli_look.apply_look(draft, clip, cli_look.look_values(parsed))
    assert clip.params["fadeIn"] == 3
    assert clip.params["fadeInCurve"] == "equal-power"


def test_checkout_validation_rejects_an_unknown_stored_audio_curve(draft):
    clip = draft.clip("a-music")
    clip.params["fadeInCurve"] = "wat"
    assert any("fadeInCurve is invalid" in problem for problem in draft._type_mismatches())


def test_crossfade_parser_moves_b_start_and_writes_both_fades(draft, capsys):
    a = draft.clip("a-music")
    b_data = copy.deepcopy(a.data)
    b_data.update(id="b-music", at=5.0)
    draft._internal(a.shot_id)["clips"].append(b_data)
    parsed = _parse("--crossfade", "a-music", "b-music", "curve=equal-power", "--over", "1")
    assert parsed.crossfade == ["a-music", "b-music", "curve=equal-power"]
    assert parsed.overlap == 1
    cli._apply_edit(draft, parsed)
    b = draft.clip("b-music")
    assert b.start == 3.0  # A stays 0..4; B moved to create a one-second overlap.
    assert a.params["fadeOut"] == b.params["fadeIn"] == 1
    assert a.params["fadeOutCurve"] == b.params["fadeInCurve"] == "equal-power"
    assert "moved b-music start" in capsys.readouterr().out


@pytest.mark.parametrize("track,overlap,error", [
    ("music", None, "do not overlap"),
    ("vo", 1, "same audio track"),
])
def test_crossfade_refuses_disjoint_or_different_tracks(draft, track, overlap, error):
    a = draft.clip("a-music")
    b_data = copy.deepcopy(a.data)
    b_data.update(id="b-music", at=5.0, track=track)
    draft._internal(a.shot_id)["clips"].append(b_data)
    draft._mcache = None
    args = ["--crossfade", "a-music", "b-music"]
    if overlap:
        args.extend(("--over", str(overlap)))
    parsed = _parse(*args)
    with pytest.raises(cli._VerbError, match=error):
        cli._apply_edit(draft, parsed)


def test_fade_out_takes_an_ease_token():
    assert _parse("--clip", "c2.card", "--fade-out", "8f", "ease=in").fade_out == "8f ease=in"


def test_volume_with_equals_takes_a_negative_db_value():
    parsed = _parse("--clip", "a-music", "--volume=-3dB")
    assert parsed.volume == "-3dB"


def test_volume_with_a_space_before_a_negative_db_is_refused(capsys):
    with pytest.raises(SystemExit):
        _parse("--clip", "a-music", "--volume", "-3dB")
    assert "expected one argument" in capsys.readouterr().err


def test_volume_plain_gain_and_mute_still_parse():
    assert _parse("--clip", "a-music", "--volume", "0.5").volume == "0.5"
    assert _parse("--clip", "a-music", "--volume", "mute").volume == "mute"


def test_gain_db_takes_a_negative_number_and_becomes_the_dB_volume_value():
    parsed = _parse("--clip", "a-music", "--gain-db", "-3")
    assert parsed.gain_db == -3.0
    assert cli_look.look_values(parsed) == {"volume": "-3dB"}
    assert cli_look.has_look(parsed)


def test_gain_db_positive_becomes_a_dB_volume_value():
    assert cli_look.look_values(_parse("--clip", "a-music", "--gain-db", "2")) == {"volume": "2dB"}


def test_both_volume_and_gain_db_is_a_usage_error(capsys):
    with pytest.raises(SystemExit) as exc:
        _parse("--clip", "a-music", "--volume=-3dB", "--gain-db", "-3")
    assert exc.value.code == 2
    assert "not allowed with" in capsys.readouterr().err


def test_gain_db_writes_the_same_clip_volume_as_volume_equals(draft):
    clip = draft.clip("a-music")
    cli_look.apply_look(draft, clip, cli_look.look_values(_parse("--clip", "a-music", "--gain-db", "-3")))
    assert clip.data["volume"] == 0.7079
    assert "volume" not in (clip.data.get("params") or {})


def test_gain_db_positive_writes_the_clip_volume(draft):
    clip = draft.clip("a-music")
    cli_look.apply_look(draft, clip, cli_look.look_values(_parse("--clip", "a-music", "--gain-db", "2")))
    assert clip.data["volume"] == 1.2589


def test_volume_gain_and_mute_are_written_through_the_clip(draft):
    clip = draft.clip("a-music")
    cli_look.apply_look(draft, clip, cli_look.look_values(_parse("--clip", "a-music", "--volume", "0.5")))
    assert clip.data["volume"] == 0.5
    cli_look.apply_look(draft, clip, cli_look.look_values(_parse("--clip", "a-music", "--volume", "mute")))
    assert clip.data["volume"] == 0


def test_cli_look_no_longer_touches_argparse_private_matcher():
    source = Path(cli_look.__file__).read_text(encoding="utf-8")
    assert "_negative_number_matcher" not in source


def test_look_values_and_has_look():
    parsed = _parse("--clip", "c2.card", "--fade-in", "8f", "--opacity", "0.6", "--exit", "fade", "12f")
    assert cli_look.has_look(parsed)
    assert cli_look.look_values(parsed) == {"exit": "fade 12f", "fade_in": "8f", "opacity": "0.6"}
    assert not cli_look.has_look(_parse("--clip", "c2.card", "--on", "viral"))


def test_fade_in_writes_the_clip_level_entrance(draft):
    clip = draft.clip("c2.card")
    lines = cli_look.apply_look(draft, clip, {"fade_in": "8f"})
    assert clip.data["entrance"] == {"type": "fade", "duration": 0.2667}
    assert lines == ["c2.card entrance: none → fade 8f"]


def test_the_same_value_writes_nothing(draft):
    clip = draft.clip("c2.card")
    cli_look.apply_look(draft, clip, {"entrance": "fade 8f"})
    before = copy.deepcopy(clip.data)
    lines = cli_look.apply_look(draft, clip, {"entrance": "fade 8f"})
    assert lines == ["c2.card entrance: fade 8f (unchanged)"]
    assert clip.data == before


def test_audio_volume_is_clip_level_and_leaves_params_alone(draft):
    clip = draft.clip("a-music")
    lines = cli_look.apply_look(draft, clip, {"volume": "-3dB"})
    assert clip.data["volume"] == 0.7079
    assert "volume" not in (clip.data.get("params") or {})
    assert lines == ["a-music volume: none → -3.0dB"]








def test_entrance_with_ease_writes_the_stored_params(draft):
    clip = draft.clip("c2.card")
    lines = cli_look.apply_look(draft, clip, cli_look.look_values(_parse("--clip", "c2.card", "--entrance", "fade", "12f", "ease=out")))
    assert clip.data["entrance"] == {"type": "fade", "duration": 0.4, "params": {"easing": "out"}}
    assert lines == ["c2.card entrance: none → fade 12f ease=out"]


def test_fade_in_with_ease_writes_the_clip_level_entrance(draft):
    clip = draft.clip("c2.card")
    cli_look.apply_look(draft, clip, cli_look.look_values(_parse("--clip", "c2.card", "--fade-in", "8f", "ease=out")))
    assert clip.data["entrance"] == {"type": "fade", "duration": 0.2667, "params": {"easing": "out"}}


def test_ease_on_an_audio_fade_is_refused_and_changes_nothing(draft):
    clip = draft.clip("a-music")
    before = copy.deepcopy(clip.data)
    with pytest.raises(ValueError, match="linear volume ramp"):
        cli_look.apply_look(draft, clip, cli_look.look_values(_parse("--clip", "a-music", "--fade-in", "8f", "ease=out")))
    assert clip.data == before
