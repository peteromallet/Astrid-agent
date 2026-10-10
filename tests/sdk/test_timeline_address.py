"""One address, everywhere: the cases the loop hit (S13, S14, S22, S4, params, assets)."""
from __future__ import annotations

import pytest

from astrid.sdk.timeline_address import AddressError, describe_target, resolve
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
