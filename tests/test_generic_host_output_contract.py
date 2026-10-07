"""Pure GenericPackHost output-contract preservation checks."""

from __future__ import annotations

import base64
import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from astrid.core.execution.generic_host import GenericPackHost, HostError, RuntimeProtocolClient


_METADATA = {
    "role": "auxiliary",
    "is_primary": False,
    "durability": "temporary",
    "regeneration": {
        "available": True,
        "capability_id": "fixture.render",
        "source_refs": ["sha256:" + "a" * 64],
        "recipe_digest": "sha256:" + "b" * 64,
        "exact_inputs": {"seed": 7},
    },
    "coverage": {
        "sampling": {
            "mode": "interval",
            "range": {"start": 0, "end": 24},
            "every_frames": 6,
            "cards": [],
        }
    },
    "producer": {"capability_id": "fixture.render", "view": "test"},
    "provenance": {"render_run_id": "run-1", "source": "fixture"},
}


@pytest.mark.parametrize("inline", [False, True])
def test_upload_outputs_preserves_explicit_contract_fields_without_gen_metadata(
    tmp_path: Path, inline: bool
) -> None:
    payload = b"derived-output"
    staged = tmp_path / "derived.bin"
    staged.write_bytes(payload)
    digest = "sha256:" + hashlib.sha256(payload).hexdigest()

    class Client:
        INLINE_SETTLEMENT_OUTPUTS = inline

        def upload_object(self, path: Path, **kwargs: object) -> object:
            assert path == staged
            assert kwargs == {
                "project_id": None,
                "media_type": "application/octet-stream",
                "filename": "derived.bin",
            }
            return SimpleNamespace(digest=digest, size=len(payload))

    host = object.__new__(GenericPackHost)
    host.client = Client()
    descriptor = {
        "name": "derived",
        "path": str(staged),
        "filename": "derived.bin",
        "artifact_type": "application/octet-stream",
        **_METADATA,
    }

    settled = host._upload_outputs([descriptor], project_id=None)

    assert settled[0]["digest"] == digest
    for field, value in _METADATA.items():
        assert settled[0][field] == value
    assert "generation_id" not in settled[0]
    if inline:
        assert base64.b64decode(settled[0]["data_base64"]) == payload


@pytest.mark.parametrize("inline", [False, True])
def test_upload_outputs_preserves_generic_multi_file_ordinals(
    tmp_path: Path, inline: bool
) -> None:
    attempt = tmp_path / "attempt"
    output_root = attempt / "outputs"
    output_root.mkdir(parents=True)
    output_port = "ordinary_files"
    filenames = ["first.bin", "second.bin", "third.bin"]
    payloads = [b"equal-output", b"equal-output", b"different-output"]
    ordinals = [0, 5, 9]
    digests = ["sha256:" + hashlib.sha256(data).hexdigest() for data in payloads]
    harvested = []
    calls: list[tuple[Path, dict[str, object]]] = []
    for filename, data, ordinal, digest in zip(filenames, payloads, ordinals, digests):
        path = output_root / filename
        path.write_bytes(data)
        harvested.append(
            {
                "name": output_port,
                "path": str(path),
                "ordinal": ordinal,
                "content_hash": digest,
                "bytes": len(data),
            }
        )

    class Client:
        INLINE_SETTLEMENT_OUTPUTS = inline

        def upload_object(self, path: Path, **kwargs: object) -> object:
            calls.append((path, dict(kwargs)))
            data = path.read_bytes()
            return SimpleNamespace(
                digest="sha256:" + hashlib.sha256(data).hexdigest(), size=len(data)
            )

    host = object.__new__(GenericPackHost)
    host.client = Client()
    record = SimpleNamespace(
        definition=SimpleNamespace(
            outputs=[SimpleNamespace(name=output_port, artifact_type="application/octet-stream")]
        )
    )
    typed = host._typed_outputs(record, harvested, attempt)
    assert [item["ordinal"] for item in typed] == ordinals
    assert all(
        not any(field in item for field in ("output_port", "group_key", "variant_key", "selector"))
        for item in typed
    )

    settled = host._upload_outputs(typed, project_id="project-1")

    assert [item["ordinal"] for item in settled] == ordinals
    assert [item["filename"] for item in settled] == filenames
    assert [item["name"] for item in settled] == [output_port] * 3
    assert [item["digest"] for item in settled] == digests
    assert [item["size"] for item in settled] == [len(data) for data in payloads]
    assert all(item["media_type"] == "application/octet-stream" for item in settled)
    assert all(
        not any(field in item for field in ("output_port", "group_key", "variant_key", "selector"))
        for item in settled
    )
    assert digests[0] == digests[1] != digests[2]
    assert [item["ordinal"] for item in typed] == ordinals
    if inline:
        assert calls == []
        assert [base64.b64decode(item["data_base64"]) for item in settled] == payloads
    else:
        assert calls == [
            (
                output_root / filename,
                {
                    "project_id": "project-1",
                    "media_type": "application/octet-stream",
                    "filename": filename,
                },
            )
            for filename in filenames
        ]


@pytest.mark.parametrize("inline", [False, True])
@pytest.mark.parametrize("explicit_output_port", [False, True])
@pytest.mark.parametrize(
    ("staged_filename", "runtime_filename", "output_name", "artifact_type", "payload"),
    [
        ("cache/chunks.json", "chunks.json", "chunk_plan", "metadata/transcript-chunks", b'{"chunks": []}'),
        ("segments/segments.json", "segments.json", "segments_manifest", None, b'{"segments": []}'),
    ],
)
def test_upload_outputs_maps_typed_json_namespace_without_changing_identity(
    tmp_path: Path,
    inline: bool,
    explicit_output_port: bool,
    staged_filename: str,
    runtime_filename: str,
    output_name: str,
    artifact_type: str | None,
    payload: bytes,
) -> None:
    attempt = tmp_path / "attempt"
    staged = attempt / "outputs" / staged_filename
    staged.parent.mkdir(parents=True)
    staged.write_bytes(payload)
    digest = "sha256:" + hashlib.sha256(payload).hexdigest()
    calls: list[tuple[Path, dict[str, object]]] = []

    class Client:
        INLINE_SETTLEMENT_OUTPUTS = inline

        def upload_object(self, path: Path, **kwargs: object) -> object:
            assert path == staged
            assert path.read_bytes() == payload
            calls.append((path, dict(kwargs)))
            return SimpleNamespace(digest=digest, size=len(payload))

    host = object.__new__(GenericPackHost)
    host.client = Client()
    record = SimpleNamespace(
        definition=SimpleNamespace(
            outputs=[
                SimpleNamespace(
                    name=output_name, artifact_type=artifact_type
                )
            ]
        )
    )
    harvested = {
        "name": output_name,
        "path": str(staged),
        "ordinal": 5,
        "content_hash": digest,
        "bytes": len(payload),
        **_METADATA,
    }
    original_harvested = dict(harvested)
    typed = host._typed_outputs(record, [harvested], attempt)
    assert typed[0]["name"] == output_name
    assert typed[0]["filename"] == staged_filename
    assert typed[0]["artifact_type"] == artifact_type
    assert typed[0]["digest"] == digest
    assert typed[0]["size"] == len(payload)
    assert "output_port" not in typed[0]
    if explicit_output_port:
        typed[0]["output_port"] = output_name
    original_descriptor = dict(typed[0])
    binding = {
        "project_id": "project-1",
        "run_id": "run-1",
        "task_id": "task-1",
        "attempt_id": "attempt-1",
        "lease_id": "lease-1",
        "fence": 1,
        "runtime_epoch": 1,
    }

    settled = host._upload_outputs(typed, **binding)

    assert len(settled) == 1
    assert settled[0]["filename"] == runtime_filename
    assert settled[0]["name"] == output_name
    assert settled[0]["ordinal"] == 5
    assert settled[0]["digest"] == digest
    assert settled[0]["size"] == len(payload)
    assert settled[0]["media_type"] == "application/json"
    if explicit_output_port:
        assert settled[0]["output_port"] == output_name
    else:
        assert "output_port" not in settled[0]
    for field, value in _METADATA.items():
        assert settled[0][field] == value
    assert typed[0] == original_descriptor
    assert harvested == original_harvested
    assert harvested["content_hash"] == digest
    assert harvested["bytes"] == len(payload)
    assert staged.read_bytes() == payload
    if inline:
        assert calls == []
        assert base64.b64decode(settled[0]["data_base64"]) == payload
    else:
        assert calls == [
            (
                staged,
                {
                    **binding,
                    "media_type": "application/json",
                    "filename": runtime_filename,
                    "output_key": output_name,
                    "output_port": output_name,
                },
            )
        ]


@pytest.mark.parametrize("inline", [False, True])
@pytest.mark.parametrize("explicit_output_port", [False, True])
def test_upload_outputs_maps_video_windows_without_changing_receipt_identity(
    tmp_path: Path, inline: bool, explicit_output_port: bool
) -> None:
    attempt = tmp_path / "attempt"
    staged_filename = "video-windows/window.mp4"
    staged = attempt / "outputs" / staged_filename
    staged.parent.mkdir(parents=True)
    payload = b"receipt-backed-video-window"
    staged.write_bytes(payload)
    digest = "sha256:" + hashlib.sha256(payload).hexdigest()
    output_name = "window_clips"
    metadata = {
        **_METADATA,
        "producer": {"capability_id": "understanding.video_understand"},
        "provenance": {
            "receipt": {"content_hash": digest, "bytes": len(payload)},
            "window": {"start_s": 2.0, "end_s": 4.0},
        },
    }
    calls: list[tuple[Path, dict[str, object]]] = []

    class Client:
        INLINE_SETTLEMENT_OUTPUTS = inline

        def upload_object(self, path: Path, **kwargs: object) -> object:
            assert path == staged
            assert path.read_bytes() == payload
            calls.append((path, dict(kwargs)))
            return SimpleNamespace(digest=digest, size=len(payload))

    host = object.__new__(GenericPackHost)
    host.client = Client()
    record = SimpleNamespace(
        definition=SimpleNamespace(
            outputs=[SimpleNamespace(name=output_name, artifact_type="video/mp4")]
        )
    )
    harvested = {
        "name": output_name,
        "path": str(staged),
        "ordinal": 5,
        "content_hash": digest,
        "bytes": len(payload),
        **metadata,
    }
    original_harvested = dict(harvested)
    typed = host._typed_outputs(record, [harvested], attempt)
    assert typed[0]["filename"] == staged_filename
    assert typed[0]["path"] == str(staged)
    assert typed[0]["digest"] == digest
    assert typed[0]["size"] == len(payload)
    assert "output_port" not in typed[0]
    if explicit_output_port:
        typed[0]["output_port"] = output_name
    original_descriptor = dict(typed[0])
    binding = {
        "project_id": "project-1",
        "run_id": "run-1",
        "task_id": "task-1",
        "attempt_id": "attempt-1",
        "lease_id": "lease-1",
        "fence": 1,
        "runtime_epoch": 1,
    }

    settled = host._upload_outputs(typed, **binding)

    assert len(settled) == 1
    assert settled[0]["filename"] == "window.mp4"
    assert settled[0]["name"] == output_name
    assert settled[0]["ordinal"] == 5
    assert settled[0]["digest"] == digest
    assert settled[0]["size"] == len(payload)
    assert settled[0]["media_type"] == "video/mp4"
    assert settled[0]["kind"] == "object"
    assert settled[0]["role"] == "auxiliary"
    assert settled[0]["is_primary"] is False
    if explicit_output_port:
        assert settled[0]["output_port"] == output_name
    else:
        assert "output_port" not in settled[0]
    for field, value in metadata.items():
        assert settled[0][field] == value
    assert typed[0] == original_descriptor
    assert harvested == original_harvested
    assert staged.read_bytes() == payload
    if inline:
        assert calls == []
        assert base64.b64decode(settled[0]["data_base64"]) == payload
    else:
        assert calls == [
            (
                staged,
                {
                    **binding,
                    "media_type": "video/mp4",
                    "filename": "window.mp4",
                    "output_key": output_name,
                    "output_port": output_name,
                },
            )
        ]


@pytest.mark.parametrize(
    ("staged_filename", "runtime_filename", "artifact_type", "output_name"),
    [
        ("images/output_000.png", "output_000.png", "image/png", "generated_images"),
        ("videos/output_000.mp4", "output_000.mp4", "video/mp4", "generated_videos"),
        ("audio/output_000.wav", "output_000.wav", "audio/wav", "generated_audio"),
        ("tiles/tile_000.png", "tile_000.png", "image/png", "tiles"),
        ("frames/frame_000.jpg", "frame_000.jpg", "image/jpeg", "frames"),
        ("outputs/output_000.mp4", "output_000.mp4", "video/mp4", "generated_videos"),
        ("artifacts/output_000.mp4", "output_000.mp4", "video/mp4", "generated_videos"),
        ("agent-view/structure.md", "structure.md", "text/markdown", "structure"),
        ("filmstrip-view/filmstrip.html", "filmstrip.html", "text/html", "html"),
    ],
)
def test_upload_outputs_maps_known_namespaces_at_runtime_boundary(
    tmp_path: Path,
    staged_filename: str,
    runtime_filename: str,
    artifact_type: str,
    output_name: str,
) -> None:
    payload = b"generation-output"
    staged = tmp_path / "attempt" / "outputs" / staged_filename
    staged.parent.mkdir(parents=True)
    staged.write_bytes(payload)
    calls: list[tuple[Path, dict[str, object]]] = []

    class Client:
        INLINE_SETTLEMENT_OUTPUTS = False

        def upload_object(self, path: Path, **kwargs: object) -> object:
            assert path.read_bytes() == payload
            calls.append((path, dict(kwargs)))
            return SimpleNamespace(
                digest="sha256:" + hashlib.sha256(payload).hexdigest(),
                size=len(payload),
            )

    host = object.__new__(GenericPackHost)
    host.client = Client()
    descriptor = {
        "name": output_name,
        "path": str(staged),
        "filename": staged_filename,
        "artifact_type": artifact_type,
        "output_port": output_name,
        "group_key": "main",
        "variant_key": "original",
        "selector": {"group_key": "main", "variant_key": "original"},
        "ordinal": 0,
        "role": "result",
        "is_primary": True,
        "producer": {"capability_id": "fixture.generation"},
        "provenance": {"source": "fixture"},
    }

    settled = host._upload_outputs(
        [descriptor],
        project_id="project-1",
        run_id="run-1",
        task_id="task-1",
        attempt_id="attempt-1",
        lease_id="lease-1",
        fence=1,
        runtime_epoch=1,
    )

    assert calls == [
        (
            staged,
            {
                "project_id": "project-1",
                "media_type": artifact_type,
                "filename": runtime_filename,
                "run_id": "run-1",
                "task_id": "task-1",
                "attempt_id": "attempt-1",
                "lease_id": "lease-1",
                "fence": 1,
                "output_key": output_name,
                "output_port": output_name,
                "runtime_epoch": 1,
            },
        )
    ]
    assert settled[0]["filename"] == runtime_filename
    assert settled[0]["name"] == output_name
    assert settled[0]["media_type"] == artifact_type
    assert settled[0]["digest"] == "sha256:" + hashlib.sha256(payload).hexdigest()
    assert settled[0]["size"] == len(payload)
    assert settled[0]["output_port"] == output_name
    assert settled[0]["group_key"] == "main"
    assert settled[0]["variant_key"] == "original"
    assert settled[0]["selector"] == {"group_key": "main", "variant_key": "original"}
    assert settled[0]["ordinal"] == 0
    assert settled[0]["role"] == "result"
    assert settled[0]["producer"] == {"capability_id": "fixture.generation"}
    assert settled[0]["provenance"] == {"source": "fixture"}
    assert descriptor["filename"] == staged_filename
    assert descriptor["path"] == str(staged)


@pytest.mark.parametrize("inline", [False, True])
@pytest.mark.parametrize(
    ("first_filename", "second_filename"),
    [
        ("agent-view/structure.md", "agent-view/structure.md"),
        ("tiles/output.png", "tiles/output.png"),
        ("frames/output.png", "frames/output.png"),
        ("tiles/output.png", "frames/output.png"),
        ("tiles/output.png", "images/output.png"),
        ("frames/output.png", "output.png"),
        ("cache/chunks.json", "chunks.json"),
        ("chunks.json", "cache/chunks.json"),
        ("segments/segments.json", "segments.json"),
        ("segments.json", "segments/segments.json"),
        ("video-windows/window.mp4", "window.mp4"),
        ("window.mp4", "video-windows/window.mp4"),
        ("video-windows/window.mp4", "videos/window.mp4"),
        ("videos/window.mp4", "video-windows/window.mp4"),
    ],
)
def test_upload_outputs_rejects_known_namespace_leaf_collisions(
    tmp_path: Path, first_filename: str, second_filename: str, inline: bool
) -> None:
    first = tmp_path / "first.bin"
    second = tmp_path / "second.bin"
    first.write_bytes(b"first")
    second.write_bytes(b"second")
    calls: list[Path] = []

    def upload_object(path: Path, **_kwargs: object) -> object:
        calls.append(path)
        data = path.read_bytes()
        return SimpleNamespace(
            digest="sha256:" + hashlib.sha256(data).hexdigest(),
            size=len(data),
        )

    host = object.__new__(GenericPackHost)
    host.client = SimpleNamespace(
        INLINE_SETTLEMENT_OUTPUTS=inline,
        upload_object=upload_object,
    )

    with pytest.raises(HostError, match="collide on managed filename"):
        host._upload_outputs(
            [
                {"name": "first", "path": str(first), "filename": first_filename},
                {"name": "second", "path": str(second), "filename": second_filename},
            ],
            project_id=None,
        )
    assert calls == ([] if inline else [first])


@pytest.mark.parametrize(
    ("staged_filename", "runtime_filename", "media_type", "output_key", "output_port"),
    [
        ("images/output_000.png", "output_000.png", "image/png", "generated_images", "generated_images"),
        ("tiles/tile_000.png", "tile_000.png", "image/png", "tile_000", "tiles"),
        ("frames/frame_000.jpg", "frame_000.jpg", "image/jpeg", "frame_000", "frames"),
        ("cache/chunks.json", "chunks.json", "application/json", "chunk_plan", "chunk_plan"),
        ("segments/segments.json", "segments.json", "application/json", "segments_manifest", "segments_manifest"),
        ("video-windows/window.mp4", "window.mp4", "video/mp4", "window_clips", "window_clips"),
    ],
)
def test_runtime_upload_binding_uses_leaf_and_stable_replay_key(
    tmp_path: Path,
    staged_filename: str,
    runtime_filename: str,
    media_type: str,
    output_key: str,
    output_port: str,
) -> None:
    payload = b"replay-safe-generation-output"
    staged = tmp_path / "attempt" / "outputs" / staged_filename
    staged.parent.mkdir(parents=True)
    staged.write_bytes(payload)
    calls: list[dict[str, object]] = []

    def ingest_object(data: bytes, **kwargs: object) -> object:
        calls.append({"data": data, **kwargs})
        digest = "sha256:" + hashlib.sha256(data).hexdigest()
        return SimpleNamespace(digest=digest, size=len(data))

    client = object.__new__(RuntimeProtocolClient)
    client.executor_id = "executor-1"
    client._attempt_runtime_epochs = {"attempt-1": 1}
    client.INLINE_SETTLEMENT_OUTPUTS = False
    client.generated = SimpleNamespace(
        health=lambda: {"runtime_epoch": 1},
        ingest_object=ingest_object,
    )
    host = object.__new__(GenericPackHost)
    host.client = client
    descriptor = {
        "name": output_key,
        "path": str(staged),
        "filename": staged_filename,
        "artifact_type": media_type,
        "output_port": output_port,
        "group_key": "main",
        "variant_key": "original",
        "selector": {"group_key": "main", "variant_key": "original"},
        "ordinal": 0,
    }
    kwargs = {
        "project_id": "project-1",
        "run_id": "run-1",
        "task_id": "task-1",
        "attempt_id": "attempt-1",
        "lease_id": "lease-1",
        "fence": 1,
        "runtime_epoch": 1,
    }

    host._upload_outputs([descriptor], **kwargs)
    host._upload_outputs([descriptor], **kwargs)

    assert len(calls) == 2
    expected_binding = {
        **kwargs,
        "executor_id": "executor-1",
        "output_key": output_key,
        "output_port": output_port,
        "filename": runtime_filename,
        "digest": "sha256:" + hashlib.sha256(payload).hexdigest(),
        "size": len(payload),
        "media_type": media_type,
    }
    assert all(call["filename"] == runtime_filename for call in calls)
    assert all(call["media_type"] == media_type for call in calls)
    assert all(call["upload_binding"] == expected_binding for call in calls)
    assert calls[0]["idempotency_key"] == calls[1]["idempotency_key"]
    assert calls[0]["data"] == calls[1]["data"] == payload


@pytest.mark.parametrize("inline", [False, True])
@pytest.mark.parametrize(
    "filename",
    [
        "../output.png",
        "images/../output.png",
        "images/nested/output.png",
        "videos/nested/output.mp4",
        "video-windows/nested/window.mp4",
        "video-windows/../window.mp4",
        "../video-windows/window.mp4",
        "video-windows\\window.mp4",
        "video-windows/window\\clip.mp4",
        "video-windows/\x00.mp4",
        "video-windows/\x01.mp4",
        "video-windows/window\n.mp4",
        "video-windows/window\r.mp4",
        "video-windows/window\t.mp4",
        "video-windows/",
        "video-windows//window.mp4",
        "video-windows/./window.mp4",
        "video-windows/.",
        "video-windows/..",
        "video-windows/" + "a" * 513,
        "/video-windows/window.mp4",
        "./video-windows/window.mp4",
        "video-windows/window.mp4/",
        "video-windows/window.mp4/clip.mp4",
        "audio/nested/output.wav",
        "tiles/nested/output.png",
        "frames/nested/output.jpg",
        "cache/nested/chunks.json",
        "cache/../chunks.json",
        "cache\\chunks.json",
        "cache/\x01.json",
        "cache/",
        "cache//chunks.json",
        "cache/./chunks.json",
        "cache/.",
        "cache/" + "a" * 513,
        "/cache/chunks.json",
        "segments/nested/segments.json",
        "segments/segments/segments.json",
        "segments/../segments.json",
        "../segments/segments.json",
        "segments\\segments.json",
        "segments/segments\\manifest.json",
        "segments/\x00.json",
        "segments/\x01.json",
        "segments/segments\n.json",
        "segments/segments\r.json",
        "segments/segments\t.json",
        "segments/",
        "segments//segments.json",
        "segments/./segments.json",
        "segments/.",
        "segments/..",
        "segments/" + "a" * 513,
        "/segments/segments.json",
        "./segments/segments.json",
        "segments/segments.json/",
        "segments.json/segments.json",
        "nested/segments.json",
        "tiles/../output.png",
        "frames/../output.jpg",
        "tiles\\output.png",
        "frames\\output.jpg",
        "tiles/\x01.png",
        "frames/\x01.jpg",
        "tiles/",
        "frames/",
        "tiles//output.png",
        "frames//output.jpg",
        "tiles/./output.png",
        "frames/./output.jpg",
        "tiles/.",
        "frames/.",
        "tiles/" + "a" * 513,
        "frames/" + "a" * 513,
        "outputs/nested/output.mp4",
        "artifacts/nested/output.mp4",
        "agent-view/nested/output.png",
        "agent-view/../output.png",
        "nested/output.png",
        "/tmp/output.png",
        "images\\output.png",
        "images/\x01.png",
        ".",
        "",
    ],
)
def test_upload_outputs_rejects_traversal_and_arbitrary_filename_nesting(
    tmp_path: Path, filename: str, inline: bool
) -> None:
    staged = tmp_path / "output.bin"
    staged.write_bytes(b"output")
    host = object.__new__(GenericPackHost)
    host.client = SimpleNamespace(
        INLINE_SETTLEMENT_OUTPUTS=inline,
        upload_object=lambda *_args, **_kwargs: pytest.fail("upload must not be attempted"),
    )

    with pytest.raises(HostError, match="invalid managed filename"):
        host._upload_outputs(
            [{"name": "generated_images", "path": str(staged), "filename": filename}],
            project_id="project-1",
        )
