from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("ASTRID_INTERNAL_INVOCATION", "1")

from astrid.packs.generation.executors.generate_speech import run  # noqa: E402


def _tools(tmp_path: Path, *, fail_call: int | None = None):
    calls: list[list[str]] = []

    def fake_run(argv, **kwargs):
        calls.append(argv)
        call_index = len(calls)
        if call_index == fail_call:
            raise RuntimeError("stubbed subprocess failure")
        output = Path(argv[argv.index("--write-media") + 1]) if "--write-media" in argv else Path(argv[-1])
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"ID3" + b"x" * 20 if output.suffix == ".mp3" else b"RIFF" + b"x" * 60)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    return calls, fake_run


def test_speech_records_exact_text_settings_hashes_and_wav_duration(tmp_path: Path) -> None:
    text = "Take the next left at the old station."
    out = tmp_path / "attempt"
    calls, fake_run = _tools(tmp_path)
    probe = SimpleNamespace(duration_seconds=2.375)
    with patch.object(run.shutil, "which", side_effect=lambda name: f"/fake/{name}"), \
         patch.object(run.subprocess, "run", side_effect=fake_run), \
         patch.dict(os.environ, {"ASTRID_BROKER_PROXY": "http://127.0.0.1:43210"}), \
         patch.object(run, "ffprobe_metadata", return_value=probe):
        result = run.generate_core([
            "--text", text, "--provider", "edge-tts", "--voice", "en-US-ChristopherNeural",
            "--rate", "+5%", "--volume=-2%", "--pitch", "+2Hz", "--out", str(out),
        ])

    wav = out / "audio/speech.wav"
    manifest = json.loads((out / "manifest.json").read_text())
    assert result.path == wav
    assert wav.read_bytes().startswith(b"RIFF")
    assert not (out / "audio/speech.mp3").exists()
    assert calls[0][calls[0].index("--text") + 1] == text
    assert calls[0][calls[0].index("--voice") + 1] == "en-US-ChristopherNeural"
    assert "--rate=+5%" in calls[0]
    assert "--volume=-2%" in calls[0]
    assert "--pitch=+2Hz" in calls[0]
    assert calls[0][calls[0].index("--proxy") + 1] == "http://127.0.0.1:43210"
    assert manifest["text"] == text
    assert manifest["text_sha256"] == hashlib.sha256(text.encode()).hexdigest()
    assert manifest["settings"] == {
        "provider": "edge-tts", "voice": "en-US-ChristopherNeural",
        "rate": "+5%", "volume": "-2%", "pitch": "+2Hz",
    }
    assert manifest["audio_sha256"] == hashlib.sha256(wav.read_bytes()).hexdigest()
    assert manifest["duration_seconds"] == 2.375
    assert manifest["outputs"][0]["name"] == "speech"
    assert manifest["outputs"][0]["duration_seconds"] == 2.375
    assert manifest["outputs"][1]["name"] == "speech_manifest"
    provenance = json.loads((out / "speech-provenance.json").read_text())
    assert provenance["audio_sha256"] == manifest["audio_sha256"]
    assert provenance["text"] == text
    assert provenance["settings"] == manifest["settings"]


def test_subprocess_failure_removes_false_success_artifacts(tmp_path: Path) -> None:
    out = tmp_path / "attempt"
    calls, fake_run = _tools(tmp_path, fail_call=2)
    with patch.object(run.shutil, "which", side_effect=lambda name: f"/fake/{name}"), \
         patch.object(run.subprocess, "run", side_effect=fake_run):
        result = run.run_sdk(["--text", "Hello", "--out", str(out)])

    assert result["returncode"] == 1
    assert not (out / "audio/speech.wav").exists()
    assert not (out / "audio/speech.mp3").exists()
    assert not (out / "manifest.json").exists()
    assert len(calls) == 2


def test_provider_is_explicitly_validated(tmp_path: Path) -> None:
    result = run.run_sdk(["--text", "Hello", "--provider", "other", "--out", str(tmp_path)])
    assert result["returncode"] == 1
    assert list(result["error"]["valid_options"]) == ["edge-tts"]
