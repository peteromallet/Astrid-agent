"""One address, everywhere: the cases the loop hit (S13, S14, S22, S4, params, assets)."""
from __future__ import annotations

import pytest

from astrid.sdk.timeline_address import AddressError, describe_target, resolve
from astrid.sdk import timeline_intent as intent
from astrid.sdk.timeline_checkout import Checkout

from tests.sdk.test_timeline_reflow_golden import film


@pytest.fixture
def tl():
    t = Checkout(film())
    t.resolve()
    return t


def test_layer_param_cut_word_time_and_asset(tl):
    assert resolve(tl, "c1.rocket").clip.id == "c1-rocket"
    param = resolve(tl, "c1.rocket.x")
    assert param.kind == "param" and param.param == "x"
    assert "canvas px" in describe_target(tl, param)
    assert resolve(tl, "c2").kind == "cut"
    assert resolve(tl, '"viral"', prefer="time").start == pytest.approx(1.3, abs=1 / 30)
    assert resolve(tl, "1.5").kind == "time"
    asset = resolve(tl, "R")
    assert asset.kind == "asset" and "used by c1.rocket" in describe_target(tl, asset)


def test_scoped_word_forms_resolve_and_ambiguity_lists_them(tl):
    assert resolve(tl, '"back" in s3', prefer="time").word.segment == "s3"
    tl.voice("s1").clips[0].data["app"]["words"].append([1.95, 1.99, "back"])
    with pytest.raises(AddressError) as err:
        resolve(tl, "back", prefer="time")
    assert '"back" in s1' in str(err.value) and '"back" in s3' in str(err.value)


def test_unknown_cut_lists_the_valid_ids(tl):
    with pytest.raises(AddressError, match=r"no cut c43; cuts are c1…c3"):
        resolve(tl, "c43")


def test_a_carried_layer_answers_to_the_later_cut(tl):
    rocket = tl.clip("c1.rocket")
    rocket.hold_for(5.0)  # now on screen into c2
    target = resolve(tl, "c2.rocket")
    assert target.clip.id == "c1-rocket" and "carried over c2" in target.note


def test_cut_ranges_are_inclusive(tl):
    target = resolve(tl, "c1..c2", prefer="time")
    assert (target.start, target.end) == (pytest.approx(0.0), pytest.approx(tl.clip("c3.field").start))


def test_time_takes_the_moment_grammar_cut_ends_and_padded_ids(tl):
    """P8: tl.time() reads what --on and --at read; c01 and c1 are one cut; end works without a clip."""
    c2 = resolve(tl, "c2")
    assert tl.time("c2") == pytest.approx(c2.start)
    assert tl.time("c02") == tl.time("c2")  # T15a: c1 is c01 (and the other way round)
    assert tl.time("c2 +1.8s") == pytest.approx(c2.start + 1.8)
    assert tl.time("end of c2") == pytest.approx(c2.end) == tl.time("c2.end")
    assert tl.time("c2.end -2f") == pytest.approx(c2.end - 2 / 30)
    assert tl.time("end") == pytest.approx(tl.duration)
    assert tl.time('"back" in s3') == pytest.approx(resolve(tl, '"back" in s3', prefer="time").start)
    assert resolve(tl, "c02..c3").start == pytest.approx(c2.start)
    with pytest.raises(Exception, match="no cut c9; cuts are c1…c3"):
        tl.time("c9 +1s")
    with pytest.raises(Exception, match="there is no line s9"):
        tl.time('"back" in s9')


def test_a_bare_layer_name_used_in_several_cuts_lists_each_with_its_time(tl):
    """T15a(e): "claw" is in four cuts; the bare name is ambiguous and the error lists every one, timed."""
    for cut in tl.cuts[:2]:
        for clip in cut.clips:
            if clip.track == "sprite":
                clip.data.setdefault("app", {})["layer"] = "claw"
    claws = [c for c in tl.clips() if intent.layer_of(c.data) == "claw"]
    if len(claws) < 2:
        pytest.skip("the fixture has one sprite")
    with pytest.raises(AddressError) as err:
        resolve(tl, "claw")
    for clip in claws:
        assert f"{clip.address} ({clip.start:.2f}" in str(err.value)
