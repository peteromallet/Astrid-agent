from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from astrid.core._shared.result_manifest import HarvestError, harvest_staged_outputs
from astrid.core.execution.generic_host import GenericPackHost
from astrid.packs.editorial.actions.transcribe import run as transcribe_run


PACK_ROOT = Path(__file__).resolve().parents[3] / "astrid" / "packs"
EXPECTED_OUTPUTS = {
    "transcript": ("transcript.json", "transcript"),
    "subtitle": ("transcript.srt", "subtitle"),
    "transcript_text": ("transcript.txt", "transcript/text"),
    "chunk_plan": ("cache/chunks.json", "metadata/transcript-chunks"),
}


def test_transcribe_emits_harvestable_typed_result_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    audio = tmp_path / "source.wav"
    audio.write_bytes(b"small audio fixture")
    out = tmp_path / "attempt" / "outputs"
    external_cache = tmp_path / "external-cache"

    def fake_transcribe_to_outputs(
        audio_path: Path,
        out_dir: Path,
        cache_dir: Path,
        client: Any,
        model: str,
        language: str,
        max_chunk_sec: float,
        vad_gate_enabled: bool,
        diarize_mode: str | None,
        audit: Any,
    ) -> tuple[dict[str, Path], dict[str, int], Path]:
        assert audio_path == audio.resolve()
        assert cache_dir == external_cache.resolve()
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "transcript.json").write_text(
            json.dumps({"segments": [{"start": 0.0, "end": 0.5, "text": "hello"}]}),
            encoding="utf-8",
        )
        (out_dir / "transcript.srt").write_text(
            "1\n00:00:00,000 --> 00:00:00,500\nhello\n",
            encoding="utf-8",
        )
        (out_dir / "transcript.txt").write_text("hello\n", encoding="utf-8")
        cache_dir.mkdir(parents=True, exist_ok=True)
        metadata_path = cache_dir / "chunks.json"
        metadata_path.write_text(
            json.dumps({"chunks": [{"start_sec": 0.0, "end_sec": 0.5}]}),
            encoding="utf-8",
        )
        return (
            {
                "json": out_dir / "transcript.json",
                "srt": out_dir / "transcript.srt",
                "txt": out_dir / "transcript.txt",
            },
            {"chunks": 1, "skipped_silent": 0, "segments_kept": 1, "segments_filtered": 0},
            metadata_path,
        )

    monkeypatch.setattr(transcribe_run, "transcribe_to_outputs", fake_transcribe_to_outputs)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    assert (
        transcribe_run.main(
            [
                "--audio",
                str(audio),
                "--out",
                str(out),
                "--cache-dir",
                str(external_cache),
            ]
        )
        == 0
    )

    manifest_path = out / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["inputs"] == {
        "audio": str(audio.resolve()),
        "model": "whisper-1",
        "language": "en",
    }
    assert [entry["name"] for entry in manifest["outputs"]] == list(EXPECTED_OUTPUTS)
    assert all(not Path(entry["path"]).is_absolute() for entry in manifest["outputs"])
    assert "manifest.json" not in [entry["path"] for entry in manifest["outputs"]]

    for ordinal, (name, (relative_path, _artifact_type)) in enumerate(EXPECTED_OUTPUTS.items()):
        entry = manifest["outputs"][ordinal]
        concrete = out / relative_path
        assert entry["path"] == relative_path
        assert entry["ordinal"] == ordinal
        assert entry["content_hash"] == f"sha256:{hashlib.sha256(concrete.read_bytes()).hexdigest()}"
        assert entry["bytes"] == concrete.stat().st_size
        assert entry["bytes"] >= 0
    assert (out / "cache" / "chunks.json").read_bytes() == (external_cache / "chunks.json").read_bytes()

    capability_matrix = tmp_path / "empty-capability-matrix.json"
    capability_matrix.write_text(
        json.dumps({"schema_version": 1, "capabilities": []}),
        encoding="utf-8",
    )
    host = GenericPackHost(pack_roots=[PACK_ROOT], capability_matrix=capability_matrix)
    host.discover()
    record = host.capabilities["editorial.transcribe"]
    harvested = harvest_staged_outputs(
        out,
        definition=record.definition,
        declared_outputs=record.definition.outputs,
        require=True,
    )
    assert [(item["name"], Path(item["path"]).relative_to(out).as_posix()) for item in harvested] == [
        (name, relative_path) for name, (relative_path, _artifact_type) in EXPECTED_OUTPUTS.items()
    ]

    typed = host._typed_outputs(record, harvested, out.parent)
    typed_by_name = {item["name"]: item for item in typed}
    declared_by_name = {output.name: output for output in record.definition.outputs}
    assert set(typed_by_name) == set(EXPECTED_OUTPUTS)
    for name, (relative_path, artifact_type) in EXPECTED_OUTPUTS.items():
        bound = typed_by_name[name]
        assert bound["artifact_type"] == declared_by_name[name].artifact_type == artifact_type
        assert bound["filename"] == relative_path
        assert bound["ordinal"] == list(EXPECTED_OUTPUTS).index(name)
        assert bound["digest"] == manifest["outputs"][bound["ordinal"]]["content_hash"]
        assert bound["size"] == manifest["outputs"][bound["ordinal"]]["bytes"]

    escaping = dict(manifest)
    escaping["outputs"] = [dict(manifest["outputs"][0], path="../outside.json")]
    manifest_path.write_text(json.dumps(escaping), encoding="utf-8")
    with pytest.raises(HarvestError, match="parent traversal is not allowed"):
        harvest_staged_outputs(
            out,
            definition=record.definition,
            declared_outputs=record.definition.outputs,
            require=True,
        )
