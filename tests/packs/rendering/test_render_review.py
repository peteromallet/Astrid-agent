from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from astrid.packs.rendering.executors.timeline_visualize import render_review as rr
from astrid.packs.rendering.executors.timeline_visualize.filmstrip_options import _render_every, filmstrip_options

FFMPEG = shutil.which("ffmpeg")

LOUDNESS_SUMMARY = """
[Parsed_ebur128_0 @ 0x1] Summary:

  Integrated loudness:
    I:         -18.4 LUFS
    Threshold: -28.4 LUFS

  Loudness range:
    LRA:         3.2 LU
    Threshold: -38.4 LUFS
    LRA low:   -20.1 LUFS
    LRA high:  -16.9 LUFS

  True peak:
    Peak:       -0.6 dBFS
"""

SILENCE_LOG = """
[silencedetect @ 0x2] silence_start: 2.01
[silencedetect @ 0x2] silence_end: 4.5 | silence_duration: 2.49
[silencedetect @ 0x2] silence_start: 6.0
"""


def test_sample_times_every_n_seconds_before_the_end() -> None:
    assert rr.sample_times(6.0, 2.0) == [0.0, 2.0, 4.0]
    assert rr.sample_times(5.0, 2.0) == [0.0, 2.0, 4.0]
    assert rr.sample_times(0.0, 2.0) == [0.0]


def test_sample_times_is_capped() -> None:
    assert len(rr.sample_times(10_000.0, 1.0)) == rr.MAX_SAMPLES


def test_sample_times_rejects_non_positive_interval() -> None:
    with pytest.raises(ValueError):
        rr.sample_times(10.0, 0)


def test_parse_loudness_reads_integrated_lra_and_true_peak() -> None:
    parsed = rr.parse_loudness(LOUDNESS_SUMMARY)
    assert parsed == {"integrated_lufs": -18.4, "lra_lu": 3.2, "true_peak_dbtp": -0.6}


def test_parse_silences_pairs_start_and_end_and_keeps_open_silence() -> None:
    silences = rr.parse_silences(SILENCE_LOG)
    assert silences == [
        {"start": 2.01, "end": 4.5, "duration": 2.49},
        {"start": 6.0, "end": None, "duration": None},
    ]


def test_findings_name_loudness_peak_and_silences() -> None:
    review = {
        "integrated_lufs": -18.4,
        "lra_lu": 3.2,
        "true_peak_dbtp": -0.6,
        "silences": [{"start": 2.01, "end": 4.5, "duration": 2.49}],
    }
    lines = rr.findings(review)
    assert lines[0] == "LOUDNESS integrated -18.4 LUFS, true peak -0.6 dBTP, LRA 3.2 LU"
    assert lines[1].startswith("PEAK true peak -0.6 dBTP is above -1 dBTP")
    assert lines[2] == "SILENCE 2.01–4.50s (2.49 s, below -50 dB)"


def test_findings_without_audio_say_so() -> None:
    lines = rr.findings({"integrated_lufs": None, "true_peak_dbtp": None, "silences": []})
    assert lines == ["LOUDNESS no audio stream in the render"]


def test_render_every_option_is_validated_and_defaults_to_five_seconds() -> None:
    base = {"view": "contact", "project_slug": "x"}
    with pytest.raises(ValueError):
        filmstrip_options({**base, "render_every": 0.1})
    with pytest.raises(ValueError):
        filmstrip_options({**base, "render_every": True})
    assert _render_every(None) == 5.0
    assert _render_every(2) == 2.0


def test_render_review_cli_flag_is_declared_on_the_executor() -> None:
    import yaml

    root = Path(rr.__file__).resolve().parent
    manifest = yaml.safe_load((root / "executor.yaml").read_text(encoding="utf-8"))
    names = {port["name"] for port in manifest["inputs"]}
    assert "render_every" in names
    flags = {arg.get("input"): arg.get("flag") for arg in manifest["command"]["input_args"]}
    assert flags["render_every"] == "--render-every"


@pytest.mark.skipif(FFMPEG is None, reason="ffmpeg is needed to render a test video")
def test_review_samples_a_real_render_and_measures_its_audio(tmp_path: Path) -> None:
    video = tmp_path / "render.mp4"
    # 4 s: a 1 kHz tone for 2 s, then digital silence for 2 s.
    subprocess.run(
        [
            FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "testsrc=size=320x180:rate=10:duration=4",
            "-f", "lavfi", "-i", "aevalsrc='if(lt(t,2),0.5*sin(2*PI*1000*t),0)':s=48000:d=4",
            "-c:v", "mpeg4", "-c:a", "aac", "-shortest", str(video),
        ],
        check=True,
    )
    out = tmp_path / "out"
    review = rr.review_render(video, out, every=2.0, ffmpeg=FFMPEG)
    assert review["sample_times_s"] == [0.0, 2.0]
    assert (out / rr.CONTACT_NAME).is_file()
    saved = json.loads((out / rr.REVIEW_NAME).read_text(encoding="utf-8"))
    assert saved["findings"] == review["findings"]
    assert review["integrated_lufs"] is not None and review["integrated_lufs"] < 0
    assert review["true_peak_dbtp"] is not None
    starts = [s["start"] for s in review["silences"]]
    assert any(1.5 <= start <= 2.5 for start in starts), review["silences"]
    assert not (out / "render-frames").exists()


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__]))
