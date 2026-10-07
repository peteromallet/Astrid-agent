from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("ASTRID_INTERNAL_INVOCATION", "1")

from astrid.packs.seedance_local.actions.reference_video import run


class FakeHttpClient:
    def __init__(self):
        self.secrets = []

    def register_secret(self, value: str) -> None:
        self.secrets.append(value)


def test_dry_run_and_generation_emit_experiment_ready_manifests(
    tmp_path: Path,
    monkeypatch,
):
    prompt_file = tmp_path / "prompt.txt"
    prompt_file.write_text(
        "Use @Video1 as the motion and camera reference; replace its appearance.",
        encoding="utf-8",
    )
    video = tmp_path / "reference.mp4"
    video.write_bytes(b"fake-video")
    monkeypatch.setattr(
        run,
        "_probe_video",
        lambda _: {
            "duration_seconds": 15.0,
            "width": 1280,
            "height": 704,
            "codec": "h264",
        },
    )

    parser = run._parser()
    dry_args = parser.parse_args(
        [
            "--out",
            str(tmp_path / "dry"),
            "--prompt-file",
            str(prompt_file),
            "--video-ref",
            str(video),
            "--dry-run",
        ]
    )
    run._validate_args(dry_args, parser)
    returncode, draft = run.execute(dry_args)
    assert returncode == 0
    assert draft["status"] == "draft"
    assert draft["inputs"]["ordered_artifacts"][0]["role"] == "motion_reference"

    live_args = parser.parse_args(
        [
            "--out",
            str(tmp_path / "live"),
            "--prompt-file",
            str(prompt_file),
            "--video-ref",
            str(video),
            "--seed",
            "35635348",
        ]
    )
    run._validate_args(live_args, parser)

    client = FakeHttpClient()

    def fake_upload(_client, path: Path, api_key: str) -> str:
        assert path.name == "video1.mp4"
        assert api_key == "test-key"
        return "https://fal.example/uploaded-reference.mp4"

    def fake_submit(
        _client,
        endpoint: str,
        arguments: dict,
        api_key: str,
        *,
        max_wait_sec: int,
    ):
        assert endpoint == run.ENDPOINT
        assert api_key == "test-key"
        assert max_wait_sec == 3600
        assert arguments["video_urls"] == [
            "https://fal.example/uploaded-reference.mp4"
        ]
        return {"video": {"url": "https://fal.example/generated.mp4"}}

    def fake_download(
        _client,
        _url: str,
        destination: Path,
        _timeout: int,
    ) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"generated-video")

    returncode, complete = run.execute(
        live_args,
        client=client,
        api_key="test-key",
        uploader=fake_upload,
        submitter=fake_submit,
        downloader=fake_download,
    )
    assert returncode == 0
    assert complete["status"] == "completed"
    assert complete["kind"] == "fal.seedance_reference_video"
    assert complete["inputs"]["prompt_capture"] == "exact"
    assert complete["inputs"]["seed"] == 35635348
    assert complete["outputs"][0]["path"] == (
        "outputs/seedance-2-reference-video.mp4"
    )
    assert complete["outputs"][0]["content_hash"].startswith("sha256:")
    assert client.secrets == ["test-key"]
    assert "fal.example" not in "\n".join(
        path.read_text(encoding="utf-8", errors="replace")
        for path in (tmp_path / "live").rglob("*")
        if path.is_file()
    )


def test_ordered_image_references_are_staged_and_submitted(
    tmp_path: Path,
):
    prompt_file = tmp_path / "prompt.txt"
    prompt_file.write_text(
        "Animate @Image1 into @Image2, then continue into @Image3.",
        encoding="utf-8",
    )
    images = []
    for index in range(1, 4):
        image = tmp_path / f"frame-{index}.png"
        image.write_bytes(f"image-{index}".encode())
        images.append(image)

    parser = run._parser()
    args = parser.parse_args(
        [
            "--out",
            str(tmp_path / "images-live"),
            "--prompt-file",
            str(prompt_file),
            *[
                item
                for image in images
                for item in ("--image-ref", str(image))
            ],
            "--duration",
            "8",
        ]
    )
    run._validate_args(args, parser)
    client = FakeHttpClient()
    uploaded: list[str] = []

    def fake_upload(_client, path: Path, api_key: str) -> str:
        assert api_key == "test-key"
        uploaded.append(path.name)
        return f"https://fal.example/{path.name}"

    def fake_submit(
        _client,
        endpoint: str,
        arguments: dict,
        api_key: str,
        *,
        max_wait_sec: int,
    ):
        assert endpoint == run.ENDPOINT
        assert api_key == "test-key"
        assert max_wait_sec == 3600
        assert arguments["image_urls"] == [
            "https://fal.example/image1.png",
            "https://fal.example/image2.png",
            "https://fal.example/image3.png",
        ]
        assert "video_urls" not in arguments
        return {"video": {"url": "https://fal.example/generated.mp4"}}

    def fake_download(
        _client,
        _url: str,
        destination: Path,
        _timeout: int,
    ) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"generated-video")

    returncode, manifest = run.execute(
        args,
        client=client,
        api_key="test-key",
        uploader=fake_upload,
        submitter=fake_submit,
        downloader=fake_download,
    )

    assert returncode == 0
    assert manifest["status"] == "completed"
    assert uploaded == ["image1.png", "image2.png", "image3.png"]
    assert [
        item["reference_label"]
        for item in manifest["inputs"]["ordered_artifacts"]
    ] == ["@Image1", "@Image2", "@Image3"]
    assert manifest["outputs"][0]["path"] == (
        "outputs/seedance-2-reference-media.mp4"
    )
