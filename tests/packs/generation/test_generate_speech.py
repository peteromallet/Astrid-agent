from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

os.environ.setdefault("ASTRID_INTERNAL_INVOCATION", "1")

from astrid.core.contracts.errors import AstridError  # noqa: E402
from astrid.packs.generation.executors.generate_speech import run  # noqa: E402

WORD_EVENTS = [
    {"type": "WordBoundary", "offset": 892910, "duration": 1339160, "text": "One"},
    {"type": "WordBoundary", "offset": 2232080, "duration": 1562500, "text": "year"},
    {"type": "WordBoundary", "offset": 3794580, "duration": 3682910, "text": "ago"},
]


def _fake_edge_tts(calls: list[dict], events: list[dict] | None = None):
    class Communicate:
        def __init__(self, text, voice, **kwargs):
            calls.append({"text": text, "voice": voice, **kwargs})

        async def stream(self):
            yield {"type": "audio", "data": b"ID3" + b"x" * 40}
            for event in WORD_EVENTS if events is None else events:
                yield dict(event)

    return SimpleNamespace(Communicate=Communicate)


def _fake_ffmpeg(calls: list[list[str]], *, fail: bool = False):
    def fake_run(argv, **kwargs):
        calls.append(argv)
        if fail:
            raise RuntimeError("stubbed ffmpeg failure")
        Path(argv[-1]).write_bytes(b"RIFF" + b"x" * 60)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    return fake_run


def test_speech_writes_wav_provenance_and_word_timing(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ASTRID_BROKER_PROXY", "http://127.0.0.1:43210")
    text = "One year ago, I invited testers into a Discord."
    out = tmp_path / "attempt"
    tts_calls: list[dict] = []
    ffmpeg_calls: list[list[str]] = []
    with patch.object(run, "_load_edge_tts", return_value=_fake_edge_tts(tts_calls)), \
         patch.object(run.shutil, "which", side_effect=lambda name: f"/fake/{name}"), \
         patch.object(run.subprocess, "run", side_effect=_fake_ffmpeg(ffmpeg_calls)), \
         patch.object(run, "ffprobe_metadata", return_value=SimpleNamespace(duration_seconds=2.375)):
        result = run.generate_core([
            "--text", text, "--voice", "en-US-AndrewMultilingualNeural",
            "--rate=+12%", "--volume=-2%", "--pitch=+2Hz", "--out", str(out),
        ])

    assert result.image_paths == [out.resolve() / "audio" / "speech.wav"]
    assert not (out / "audio" / "speech.mp3").exists()
    assert tts_calls == [{
        "text": text, "voice": "en-US-AndrewMultilingualNeural", "rate": "+12%",
        "volume": "-2%", "pitch": "+2Hz", "boundary": "WordBoundary",
        "proxy": "http://127.0.0.1:43210",
    }]

    words = json.loads((out / "speech-words.json").read_text())
    assert words["boundary"] == "WordBoundary"
    assert words["duration_seconds"] == 2.375
    assert words["words"] == [
        {"word": "One", "start_s": 0.089291, "end_s": 0.223207},
        {"word": "year", "start_s": 0.223208, "end_s": 0.379458},
        {"word": "ago", "start_s": 0.379458, "end_s": 0.747749},
    ]

    manifest = json.loads((out / "manifest.json").read_text())
    assert manifest["text"] == text
    assert manifest["text_sha256"] == hashlib.sha256(text.encode()).hexdigest()
    assert manifest["settings"] == {
        "provider": "edge-tts", "voice": "en-US-AndrewMultilingualNeural",
        "rate": "+12%", "volume": "-2%", "pitch": "+2Hz",
    }
    assert manifest["word_count"] == 3
    assert [row["name"] for row in manifest["outputs"]] == ["speech", "speech_manifest", "speech_words"]
    assert manifest["outputs"][2]["path"] == "speech-words.json"
    provenance = json.loads((out / "speech-provenance.json").read_text())
    assert provenance["audio_sha256"] == manifest["audio_sha256"]


def test_missing_word_timing_fails_and_removes_audio(tmp_path: Path) -> None:
    out = tmp_path / "attempt"
    with patch.object(run, "_load_edge_tts", return_value=_fake_edge_tts([], events=[])), \
         patch.object(run.shutil, "which", side_effect=lambda name: f"/fake/{name}"), \
         patch.object(run.subprocess, "run", side_effect=_fake_ffmpeg([])):
        with pytest.raises(AstridError, match="WordBoundary"):
            run.generate_core(["--text", "Hello there.", "--out", str(out)])

    assert not (out / "audio" / "speech.wav").exists()
    assert not (out / "audio" / "speech.mp3").exists()
    assert not (out / "speech-words.json").exists()
    assert not (out / "manifest.json").exists()


def test_decode_failure_removes_false_success_artifacts(tmp_path: Path) -> None:
    out = tmp_path / "attempt"
    with patch.object(run, "_load_edge_tts", return_value=_fake_edge_tts([])), \
         patch.object(run.shutil, "which", side_effect=lambda name: f"/fake/{name}"), \
         patch.object(run.subprocess, "run", side_effect=_fake_ffmpeg([], fail=True)):
        result = run.run_sdk(["--text", "Hello", "--out", str(out)])

    assert result["returncode"] == 1
    assert not (out / "audio" / "speech.wav").exists()
    assert not (out / "speech-words.json").exists()
    assert not (out / "manifest.json").exists()


def test_missing_edge_tts_package_is_a_recoverable_error(tmp_path: Path) -> None:
    missing = AstridError("edge-tts is not installed in this Astrid environment",
                          recovery_command="pip install 'astrid[speech]'")
    with patch.object(run, "_load_edge_tts", side_effect=missing):
        result = run.run_sdk(["--text", "Hello", "--out", str(tmp_path / "attempt")])
    assert result["returncode"] == 1
    assert "astrid[speech]" in result["error"]["recovery_command"]


def test_provider_is_explicitly_validated(tmp_path: Path) -> None:
    result = run.run_sdk(["--text", "Hello", "--provider", "other", "--out", str(tmp_path)])
    assert result["returncode"] == 1
    assert list(result["error"]["valid_options"]) == ["edge-tts"]
