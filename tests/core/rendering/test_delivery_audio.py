"""Opt-in delivery audio master: loudness target and true-peak ceiling on the final mix."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from astrid.core.rendering.delivery_audio import (
    DeliveryAudioError,
    DeliveryAudioTarget,
    apply_delivery_audio,
    parse_ebur128_summary,
    parse_loudnorm_json,
)
from astrid.packs.rendering.executors.render.run import _delivery_audio_from_args

EBUR128_SUMMARY = """
[Parsed_ebur128_0 @ 0x1] Summary:

  Integrated loudness:
    I:         -13.8 LUFS
    Threshold: -24.1 LUFS

  Loudness range:
    LRA:         6.4 LU
    Threshold: -34.1 LUFS
    LRA low:   -17.0 LUFS
    LRA high:  -10.6 LUFS

  True peak:
    Peak:        +0.4 dBFS
"""

LOUDNORM_JSON = """
[Parsed_loudnorm_0 @ 0x1]
{
	"input_i" : "-15.61",
	"input_tp" : "1.00",
	"input_lra" : "1.70",
	"input_thresh" : "-25.61",
	"output_i" : "-15.76",
	"output_tp" : "-1.00",
	"output_lra" : "0.50",
	"output_thresh" : "-25.76",
	"normalization_type" : "dynamic",
	"target_offset" : "1.76"
}
"""


def test_parse_ebur128_summary_reads_i_lra_and_true_peak() -> None:
    reading = parse_ebur128_summary(EBUR128_SUMMARY)
    assert reading.integrated == pytest.approx(-13.8)
    assert reading.lra == pytest.approx(6.4)
    assert reading.true_peak == pytest.approx(0.4)


def test_parse_ebur128_summary_rejects_missing_summary() -> None:
    with pytest.raises(DeliveryAudioError):
        parse_ebur128_summary("no summary here")


def test_parse_loudnorm_json_returns_measurements() -> None:
    measured = parse_loudnorm_json(LOUDNORM_JSON)
    assert measured["input_i"] == "-15.61"
    assert measured["target_offset"] == "1.76"


@pytest.mark.parametrize(
    "value",
    [{"lufs": -4}, {"lufs": -60}, {"true_peak": 0.5}, {"true_peak": True}, {"lufs": "loud"}, {"bogus": 1}],
)
def test_target_rejects_out_of_range_or_unknown_values(value: dict) -> None:
    with pytest.raises(ValueError):
        DeliveryAudioTarget.from_mapping(value)


def test_target_defaults_to_minus_14_lufs_and_minus_1_dbtp() -> None:
    assert DeliveryAudioTarget.from_mapping({}).to_dict() == {"lufs": -14.0, "true_peak": -1.0}


def test_no_flags_means_no_delivery_audio() -> None:
    """Default renders carry no master, so their output is unchanged."""
    args = argparse.Namespace(delivery_audio=None, audio_target=None, true_peak=None)
    assert _delivery_audio_from_args(args) is None


def test_shorthand_flags_build_the_target() -> None:
    args = argparse.Namespace(delivery_audio=None, audio_target=-14.0, true_peak=-1.0)
    assert _delivery_audio_from_args(args) == {"lufs": -14.0, "true_peak": -1.0}


def _ffmpeg() -> str:
    binary = shutil.which("ffmpeg")
    if binary is None:
        pytest.skip("ffmpeg is not installed")
    return binary


@pytest.fixture()
def film_like_fixture(tmp_path: Path) -> Path:
    """Synthetic 6 s clip near -14 LUFS whose sample peaks sit above -1 dBTP.

    A quiet 220 Hz bed with a 2 kHz burst every 1.5 s: the same shape of problem
    as the film (loudness on target, true peak over the ceiling).
    """
    ffmpeg = _ffmpeg()
    out = tmp_path / "fixture.mp4"
    subprocess.run(
        [
            ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "color=c=black:s=64x64:r=10:d=6",
            "-f", "lavfi", "-i",
            "aevalsrc=exprs='0.23*sin(2*PI*220*t)+0.85*sin(2*PI*2000*t)*(lt(mod(t,1.5),0.05))':d=6:s=48000",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "320k", "-shortest",
            str(out),
        ],
        check=True,
    )
    return out


def _probe_streams(path: Path) -> dict[str, str]:
    ffprobe = shutil.which("ffprobe")
    if ffprobe is None:
        pytest.skip("ffprobe is not installed")
    proc = subprocess.run(
        [ffprobe, "-v", "error", "-show_entries", "stream=codec_type,codec_name", "-of", "json", str(path)],
        capture_output=True, text=True, check=True,
    )
    return {stream["codec_type"]: stream["codec_name"] for stream in json.loads(proc.stdout)["streams"]}


def test_master_meets_true_peak_ceiling_and_loudness_target(film_like_fixture: Path, tmp_path: Path) -> None:
    ffmpeg = _ffmpeg()
    dest = tmp_path / "delivery-audio.mp4"
    target = DeliveryAudioTarget(lufs=-14.0, true_peak=-1.0)

    receipt = apply_delivery_audio(film_like_fixture, dest, target, ffmpeg=ffmpeg)

    assert receipt["before"]["true_peak_dbtp"] > -1.0, "fixture must start above the ceiling"
    assert receipt["after"]["true_peak_dbtp"] <= -1.0
    assert abs(receipt["after"]["integrated_lufs"] - target.lufs) <= 0.5
    assert receipt["integrated_within_tolerance"] is True
    assert receipt["applied"]["video"] == "stream copy"
    assert receipt["applied"]["aac_bitrate"] == "320k"
    streams = _probe_streams(dest)
    assert streams.get("video") == "h264"
    assert streams.get("audio") == "aac"


def test_video_is_stream_copied_bit_for_bit(film_like_fixture: Path, tmp_path: Path) -> None:
    ffmpeg = _ffmpeg()
    dest = tmp_path / "delivery-audio.mp4"
    apply_delivery_audio(film_like_fixture, dest, DeliveryAudioTarget(), ffmpeg=ffmpeg)

    def video_packets(path: Path) -> bytes:
        proc = subprocess.run(
            [ffmpeg, "-v", "error", "-i", str(path), "-map", "0:v:0", "-c", "copy", "-f", "h264", "-"],
            capture_output=True, check=True,
        )
        return proc.stdout

    assert video_packets(dest) == video_packets(film_like_fixture)


def test_unreachable_ceiling_is_refused_and_leaves_no_output(film_like_fixture: Path, tmp_path: Path) -> None:
    ffmpeg = _ffmpeg()
    dest = tmp_path / "delivery-audio.mp4"
    # -9 dBTP with a -5 LUFS target forces a loud master that the ceiling cannot hold.
    with pytest.raises(DeliveryAudioError):
        apply_delivery_audio(film_like_fixture, dest, DeliveryAudioTarget(lufs=-5.0, true_peak=-9.0), ffmpeg=ffmpeg)
    assert not dest.exists()


def test_service_hook_is_inert_without_a_target() -> None:
    from astrid.core.rendering.contracts import RenderRequest
    from astrid.core.rendering.service import RenderService

    request = RenderRequest.from_dict(
        {
            "schema_version": 1,
            "timeline_path": "/nonexistent/timeline.json",
            "assets_registry_path": None,
            "output_name": "hype.mp4",
            "window": None,
            "audio": None,
            "profile": None,
            "backend_config": {},
            "metadata": {},
            "materialized_root": None,
            "materialized_objects": {},
        }
    )
    assert RenderService._delivery_audio_target(request) is None


def test_service_hook_masters_the_final_mix_and_returns_a_receipt(film_like_fixture: Path, tmp_path: Path) -> None:
    from types import SimpleNamespace

    from astrid.core.rendering.delivery_audio import METADATA_KEY
    from astrid.core.rendering.service import RenderService

    ffmpeg = _ffmpeg()
    result = SimpleNamespace(video=SimpleNamespace(profile=SimpleNamespace(has_audio=True), sha256="0" * 64))
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = RenderService._delivery_audio_target(
        SimpleNamespace(metadata={METADATA_KEY: json.dumps({"lufs": -14, "true_peak": -1})})
    )
    assert target == DeliveryAudioTarget(lufs=-14.0, true_peak=-1.0)

    mastered, receipt = RenderService._master_delivery_audio(
        result, film_like_fixture, workspace=workspace, target=target, backend="rendering.ffmpeg"
    )
    assert mastered == workspace / "delivery-audio.mp4"
    assert receipt["after"]["true_peak_dbtp"] <= -1.0
    assert receipt["source_sha256"] == "0" * 64
    assert ffmpeg  # ran the real filter chain
