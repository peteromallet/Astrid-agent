"""Offline-only receiver seams for the M16 composition proof."""
from __future__ import annotations

import importlib
import json
import os
import runpy
import sys
import subprocess
from pathlib import Path

TARGETS = {
    "astrid.packs.editorial.actions.transcribe.run",
    "astrid.packs.editorial.actions.scenes.run",
    "astrid.packs.media.actions.clip_extract.run",
}
_OLD_RUN_MODULE = runpy.run_module
_OLD_RUN_MAIN = runpy._run_module_as_main


def _config() -> dict:
    return json.loads(Path(os.environ["M16_OFFLINE_CONFIG"]).read_text(encoding="utf-8"))


def _fake_transcribe(audio_path, out_dir, cache_dir, client, model, language, max_chunk_sec, vad_gate_enabled, diarize_mode, audit=None):
    out_dir = Path(out_dir)
    cache_dir = Path(cache_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    cache_dir.mkdir(parents=True, exist_ok=True)
    segments = [{"start": 0.0, "end": 0.8, "text": "Offline panel", "speaker": None}]
    json_path = out_dir / "transcript.json"
    srt_path = out_dir / "transcript.srt"
    txt_path = out_dir / "transcript.txt"
    json_path.write_text(json.dumps({"segments": segments}, indent=2), encoding="utf-8")
    srt_path.write_text("1\n00:00:00,000 --> 00:00:00,800\nOffline panel\n", encoding="utf-8")
    txt_path.write_text("Offline panel\n", encoding="utf-8")
    metadata_path = cache_dir / "chunks.json"
    metadata_path.write_text(json.dumps({"source_audio": str(audio_path), "duration_sec": 0.8, "chunks": []}, indent=2), encoding="utf-8")
    return ({"json": json_path, "srt": srt_path, "txt": txt_path}, {"chunks": 1, "skipped_silent": 0, "segments_kept": 1, "segments_filtered": 0}, metadata_path)


def _fake_scenes(video_path, threshold):
    return [{"index": 1, "start": 0.0, "end": 0.8, "duration": 0.8}]


def _fake_ffmpeg_runner(command, **kwargs):
    output = Path(command[-1])
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(b"offline-clip-bytes")
    return subprocess.CompletedProcess(command, 0, stdout="", stderr="")


def _prepare(name: str):
    module = importlib.import_module(name)
    if name.endswith("transcribe.run"):
        module.transcribe_to_outputs = _fake_transcribe
    elif name.endswith("scenes.run"):
        module.detect_scenes = _fake_scenes
    elif name.endswith("clip_extract.run"):
        original_main = module.main

        def patched_main(argv=None):
            return original_main(argv, runner=_fake_ffmpeg_runner)

        module.main = patched_main
    return module


def _execute(name: str):
    module = _prepare(name)
    raise SystemExit(module.main(sys.argv[1:]))


def _run_module(name, *args, **kwargs):
    if name in TARGETS and kwargs.get("run_name") == "__main__":
        return _execute(name)
    return _OLD_RUN_MODULE(name, *args, **kwargs)


def _run_main(name, *args, **kwargs):
    if name in TARGETS:
        return _execute(name)
    return _OLD_RUN_MAIN(name, *args, **kwargs)


runpy.run_module = _run_module
runpy._run_module_as_main = _run_main
