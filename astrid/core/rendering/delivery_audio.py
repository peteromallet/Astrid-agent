"""Opt-in delivery audio master: an integrated-loudness target and a true-peak ceiling.

Applied to the final mix of a render, after the renderer (or finalizer) has
produced the video and before publication. Video is stream-copied; only the
audio track is re-encoded. Off by default: callers that do not request a
target never reach this module, so their outputs are unchanged.

Method: measure the mix with ebur128 (I, LRA, true peak), then run ffmpeg
``loudnorm`` in two passes (measured values fed back, ``linear=true``, which
ffmpeg itself falls back from to dynamic mode when a constant gain cannot meet
the true-peak ceiling). If the re-measured true peak is still above the
ceiling, a second attempt adds ``alimiter`` with headroom under the ceiling.
The output is re-measured and never accepted above the ceiling.
"""

from __future__ import annotations

import json
import math
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

DEFAULT_LUFS = -14.0
DEFAULT_TRUE_PEAK = -1.0
LRA_TARGET = 11.0
AAC_BITRATE = "320k"  # the same AAC bitrate the Remotion mux uses
# Both passes aim this far under the ceiling: the AAC re-encode of the master adds
# a few tenths of a dB of inter-sample overshoot, which the final check must not see.
TRUE_PEAK_HEADROOM_DB = 0.3
LUFS_TOLERANCE = 0.5
METADATA_KEY = "delivery_audio"
FRAGMENT_NAMESPACE = "rendering.delivery-audio"

_NUMBER = r"([+-]?\d+(?:\.\d+)?|[+-]?inf)"


class DeliveryAudioError(RuntimeError):
    """The delivery audio master could not be applied or did not meet its ceiling."""


@dataclass(frozen=True)
class DeliveryAudioTarget:
    lufs: float = DEFAULT_LUFS
    true_peak: float = DEFAULT_TRUE_PEAK

    def __post_init__(self) -> None:
        for name, value, low, high in (
            ("lufs", self.lufs, -40.0, -5.0),
            ("true_peak", self.true_peak, -9.0, 0.0),
        ):
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"delivery audio {name} must be a number")
            if not math.isfinite(value) or not (low <= value <= high):
                raise ValueError(
                    f"delivery audio {name} must be between {low:g} and {high:g}, got {value!r}"
                )
        object.__setattr__(self, "lufs", float(self.lufs))
        object.__setattr__(self, "true_peak", float(self.true_peak))

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "DeliveryAudioTarget":
        if not isinstance(value, Mapping):
            raise ValueError("delivery audio target must be a JSON object")
        unknown = sorted(set(value) - {"lufs", "true_peak"})
        if unknown:
            raise ValueError(f"unknown delivery audio keys: {', '.join(unknown)}")
        return cls(
            lufs=value.get("lufs", DEFAULT_LUFS),
            true_peak=value.get("true_peak", DEFAULT_TRUE_PEAK),
        )

    def to_dict(self) -> dict[str, float]:
        return {"lufs": self.lufs, "true_peak": self.true_peak}


@dataclass(frozen=True)
class LoudnessReading:
    integrated: float
    lra: float
    true_peak: float

    def to_dict(self) -> dict[str, float]:
        return {"integrated_lufs": self.integrated, "lra_lu": self.lra, "true_peak_dbtp": self.true_peak}


def parse_ebur128_summary(text: str) -> LoudnessReading:
    """Integrated loudness, LRA and true peak from ffmpeg ebur128's summary block."""
    integrated = re.search(r"Integrated loudness:\s+I:\s+" + _NUMBER + r"\s+LUFS", text)
    lra = re.search(r"Loudness range:\s+LRA:\s+" + _NUMBER + r"\s+LU", text)
    peak = re.search(r"True peak:\s+Peak:\s+" + _NUMBER + r"\s+dBFS", text)
    if not (integrated and lra and peak):
        raise DeliveryAudioError("ffmpeg ebur128 produced no loudness summary; is the input audio?")
    return LoudnessReading(
        integrated=float(integrated.group(1)),
        lra=float(lra.group(1)),
        true_peak=float(peak.group(1)),
    )


def parse_loudnorm_json(text: str) -> dict[str, str]:
    """The measurement JSON that ffmpeg loudnorm prints on the first pass."""
    blocks = re.findall(r"\{[^{}]*\}", text)
    for block in reversed(blocks):
        try:
            data = json.loads(block)
        except json.JSONDecodeError:
            continue
        if "input_i" in data:
            return {key: str(value) for key, value in data.items()}
    raise DeliveryAudioError("ffmpeg loudnorm printed no measurement block")


def find_ffmpeg() -> str:
    binary = shutil.which("ffmpeg")
    if binary is None:
        raise DeliveryAudioError("ffmpeg is required for delivery audio but was not found on PATH")
    return binary


def _run_ffmpeg(ffmpeg: str, args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [ffmpeg, "-hide_banner", "-nostdin", "-nostats", *args],
        capture_output=True,
        text=True,
        check=False,
    )


def measure(path: Path, ffmpeg: str) -> LoudnessReading:
    proc = _run_ffmpeg(
        ffmpeg,
        ["-i", str(path), "-map", "0:a:0", "-af", "ebur128=peak=true", "-f", "null", "-"],
    )
    if proc.returncode != 0:
        raise DeliveryAudioError(f"ffmpeg could not measure audio: {proc.stderr.strip()[-400:]}")
    return parse_ebur128_summary(proc.stderr)


def _aim_tp(target: DeliveryAudioTarget) -> float:
    return target.true_peak - TRUE_PEAK_HEADROOM_DB


def _first_pass(path: Path, target: DeliveryAudioTarget, ffmpeg: str) -> dict[str, str]:
    proc = _run_ffmpeg(
        ffmpeg,
        [
            "-i", str(path), "-map", "0:a:0",
            "-af", f"loudnorm=I={target.lufs:g}:TP={_aim_tp(target):g}:LRA={LRA_TARGET:g}:print_format=json",
            "-f", "null", "-",
        ],
    )
    if proc.returncode != 0:
        raise DeliveryAudioError(f"ffmpeg loudnorm measurement failed: {proc.stderr.strip()[-400:]}")
    measured = parse_loudnorm_json(proc.stderr)
    if measured["input_i"] in {"-inf", "inf"}:
        raise DeliveryAudioError("the final mix is silent; there is no loudness to master")
    return measured


def _loudnorm_filter(target: DeliveryAudioTarget, measured: Mapping[str, str]) -> str:
    return (
        f"loudnorm=I={target.lufs:g}:TP={_aim_tp(target):g}:LRA={LRA_TARGET:g}"
        f":measured_I={measured['input_i']}:measured_TP={measured['input_tp']}"
        f":measured_LRA={measured['input_lra']}:measured_thresh={measured['input_thresh']}"
        f":offset={measured['target_offset']}:linear=true:print_format=summary"
    )


def _encode(
    source: Path, dest: Path, audio_filter: str, ffmpeg: str
) -> None:
    proc = _run_ffmpeg(
        ffmpeg,
        [
            "-y", "-i", str(source),
            "-map", "0:v:0", "-map", "0:a:0", "-map_metadata", "0",
            "-c:v", "copy",
            "-af", audio_filter,
            "-c:a", "aac", "-b:a", AAC_BITRATE,
            "-movflags", "+faststart",
            str(dest),
        ],
    )
    if proc.returncode != 0:
        raise DeliveryAudioError(f"ffmpeg audio master failed: {proc.stderr.strip()[-400:]}")


def apply_delivery_audio(
    source: Path,
    dest: Path,
    target: DeliveryAudioTarget,
    *,
    ffmpeg: str | None = None,
) -> dict[str, Any]:
    """Write ``dest`` = ``source`` with its audio mastered to ``target``.

    Returns a receipt with the before/after readings and what was applied. Raises
    DeliveryAudioError (and leaves no accepted output) if the true-peak ceiling
    cannot be met.
    """
    binary = ffmpeg or find_ffmpeg()
    before = measure(source, binary)
    measured = _first_pass(source, target, binary)
    loudnorm = _loudnorm_filter(target, measured)
    limiter_limit = 10 ** (_aim_tp(target) / 20)
    attempts = [
        ("loudnorm", loudnorm),
        ("loudnorm+alimiter", f"{loudnorm},alimiter=limit={limiter_limit:.4f}:attack=5:release=50:level=disabled"),
    ]
    tried: list[dict[str, Any]] = []
    for name, audio_filter in attempts:
        _encode(source, dest, audio_filter, binary)
        after = measure(dest, binary)
        tried.append({"stage": name, **after.to_dict()})
        if after.true_peak <= target.true_peak + 1e-9:
            return {
                "target": target.to_dict(),
                "before": before.to_dict(),
                "after": after.to_dict(),
                "applied": {
                    "filters": [name],
                    "loudnorm": {"linear_requested": True, "measured": measured},
                    "alimiter_limit": limiter_limit if name.endswith("alimiter") else None,
                    "aac_bitrate": AAC_BITRATE,
                    "video": "stream copy",
                },
                "integrated_within_tolerance": abs(after.integrated - target.lufs) <= LUFS_TOLERANCE,
                "attempts": tried,
            }
    dest.unlink(missing_ok=True)
    raise DeliveryAudioError(
        f"true peak {tried[-1]['true_peak_dbtp']:+.2f} dBTP is still above the "
        f"{target.true_peak:g} dBTP ceiling after loudnorm and alimiter; not published"
    )


__all__ = [
    "AAC_BITRATE",
    "DEFAULT_LUFS",
    "DEFAULT_TRUE_PEAK",
    "DeliveryAudioError",
    "DeliveryAudioTarget",
    "FRAGMENT_NAMESPACE",
    "LoudnessReading",
    "METADATA_KEY",
    "apply_delivery_audio",
    "find_ffmpeg",
    "measure",
    "parse_ebur128_summary",
    "parse_loudnorm_json",
]
