from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import datetime
from pathlib import Path

from astrid.core._shared.result_manifest import harvest_staged_outputs
from astrid.core.execution.generic_host import GenericPackHost
from astrid.packs.media.actions.clip_extract import run as clip_extract_run


PACK_ROOT = Path(__file__).resolve().parents[3] / "astrid" / "packs"
CLIP_BYTES = b"deterministic clip bytes\x00\xff"


def test_clip_extract_emits_harvestable_typed_result_manifest(tmp_path: Path) -> None:
    source = tmp_path / "source.mp4"
    source.write_bytes(b"deterministic source bytes")
    attempt = (tmp_path / "absolute-parent-staging" / "attempt").resolve()
    out = attempt / "outputs"
    clip = out / "clip.mp4"

    def fake_runner(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        assert kwargs == {"check": False}
        assert command == [
            "ffmpeg",
            "-y",
            "-i",
            str(source.resolve()),
            "-ss",
            "2.5",
            "-t",
            "1.25",
            "-c",
            "copy",
            str(clip.resolve()),
        ]
        clip.write_bytes(CLIP_BYTES)
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    assert (
        clip_extract_run.main(
            [
                "--input",
                str(source),
                "--start",
                "2.5",
                "--dur",
                "1.25",
                "--output",
                str(clip),
            ],
            runner=fake_runner,
        )
        == 0
    )

    digest = "sha256:" + hashlib.sha256(CLIP_BYTES).hexdigest()
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["schema_version"] == 1
    assert manifest["kind"] == "clip_extract"
    assert manifest["inputs"] == {
        "input": str(source.resolve()),
        "start": 2.5,
        "dur": 1.25,
    }
    datetime.fromisoformat(manifest["created"])
    assert manifest["warnings"] == []
    assert manifest["outputs"] == [
        {
            "name": "output",
            "path": "clip.mp4",
            "type": "file",
            "content_hash": digest,
            "bytes": len(CLIP_BYTES),
        }
    ]
    assert not Path(manifest["outputs"][0]["path"]).is_absolute()

    capability_matrix = tmp_path / "empty-capability-matrix.json"
    capability_matrix.write_text(
        json.dumps({"schema_version": 1, "capabilities": []}),
        encoding="utf-8",
    )
    host = GenericPackHost(pack_roots=[PACK_ROOT], capability_matrix=capability_matrix)
    host.discover()
    record = host.capabilities["media.clip_extract"]
    assert [
        (output.name, output.type, output.path_template, output.artifact_type)
        for output in record.definition.outputs
    ] == [("output", "file", "{out}/clip.mp4", None)]
    declared_output = record.definition.outputs[0]

    harvested = harvest_staged_outputs(
        out,
        definition=record.definition,
        declared_outputs=record.definition.outputs,
        require=True,
    )
    assert harvested == [
        {
            "name": "output",
            "path": str(clip.resolve()),
            "ordinal": 0,
            "content_hash": digest,
            "bytes": len(CLIP_BYTES),
            "role": "result",
            "is_primary": False,
            "ordinal_explicit": False,
        }
    ]

    typed = host._typed_outputs(record, harvested, attempt)
    assert typed == [
        {
            "name": "output",
            "ordinal": 0,
            "artifact_type": declared_output.artifact_type,
            "digest": digest,
            "size": len(CLIP_BYTES),
            "path": str(clip.resolve()),
            "filename": "clip.mp4",
            "role": "result",
            "is_primary": False,
        }
    ]
    assert Path(typed[0]["path"]).read_bytes() == CLIP_BYTES
