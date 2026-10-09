from __future__ import annotations

from types import SimpleNamespace

import pytest

from astrid.sdk import invocation
from astrid.sdk.exceptions import CapabilityPreconditionError


def _estimate(**overrides) -> dict:
    value = {
        "frame_sequence_bytes": 0,
        "estimated_scratch_bytes": 1000,
        "alpha_frame_working_bytes": 0,
        "estimated_output_bytes": 500,
        "frame_image_format": "png",
        "frame_capture_width": 1920,
        "frame_capture_height": 1080,
        "duration_frames": 4980,
        "review_render": False,
        "width": 640,
        "height": 360,
    }
    value.update(overrides)
    return value


def _free(monkeypatch, free_bytes: int) -> None:
    monkeypatch.setattr(
        invocation.shutil,
        "disk_usage",
        lambda _path: SimpleNamespace(total=free_bytes, used=0, free=free_bytes),
    )


def test_preflight_passes_when_the_volume_covers_scratch_and_output(monkeypatch) -> None:
    _free(monkeypatch, 1501)
    invocation._assert_render_disk_preflight(_estimate(), volume="/scratch")


def test_preflight_refuses_fast_and_names_the_options(monkeypatch) -> None:
    _free(monkeypatch, 1499)
    with pytest.raises(CapabilityPreconditionError, match="scratch and output for 4980 frames") as failure:
        invocation._assert_render_disk_preflight(_estimate(), volume="/scratch")
    message = str(failure.value)
    assert "640x360" in message
    assert "review" in message
    assert "TMPDIR" in message


def test_review_refusal_does_not_offer_the_review_it_already_is(monkeypatch) -> None:
    _free(monkeypatch, 10)
    with pytest.raises(CapabilityPreconditionError) as failure:
        invocation._assert_render_disk_preflight(_estimate(review_render=True), volume="/scratch")
    assert "already a review render" in str(failure.value)


def test_preflight_without_a_scratch_total_falls_back_to_frame_terms(monkeypatch) -> None:
    _free(monkeypatch, 1000)
    with pytest.raises(CapabilityPreconditionError):
        invocation._assert_render_disk_preflight(
            _estimate(estimated_scratch_bytes=0, alpha_frame_working_bytes=900),
            volume="/scratch",
        )


def test_estimates_without_a_frame_model_skip_the_preflight(monkeypatch) -> None:
    _free(monkeypatch, 0)
    invocation._assert_render_disk_preflight({"estimated_output_bytes": 1}, volume="/scratch")
