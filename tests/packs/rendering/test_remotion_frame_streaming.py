"""Opaque Remotion renders stream frames into the encoder and report frame progress."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest import mock

from astrid.core.subprocess_env import build_child_subprocess_env
from astrid.packs.rendering.backends.remotion import run as remotion
from astrid.packs.rendering.backends.remotion.progress import (
    RemotionProgressRelay,
    parse_progress_line,
)
from tests.packs.rendering.test_remotion_backend import (  # noqa: F401 - autouse fixture
    _is_remotion_render_command,
    _remotion_exec_env,
    _write_fake_remotion_output,
    _write_inputs,
    _write_project,
)


def test_parse_progress_line_reads_frames_and_eta() -> None:
    assert parse_progress_line("Rendered 2175/4977, time remaining: 6m 3s") == {
        "phase": "rendered 2175/4977 frames · ETA 6m 3s",
        "percent": 10 + 75 * 2175 // 4977,
        "current": 2175,
        "total": 4977,
    }
    assert parse_progress_line("Rendered 4977/4977")["percent"] == 85
    assert parse_progress_line("Encoded 4977/4977") == {
        "phase": "encoded 4977/4977 frames",
        "percent": 90,
        "current": 4977,
        "total": 4977,
    }
    assert parse_progress_line("Bundling 45%") is None
    assert parse_progress_line("Rendered 0/0") is None


def test_relay_publishes_only_the_newest_complete_line(tmp_path: Path) -> None:
    log = tmp_path / "remotion-stdout.log"
    progress = tmp_path / ".astrid-progress.json"
    relay = RemotionProgressRelay(log, progress_path=progress)
    log.write_text("Bundling 100%\nRendered 1/90\nRendered 2/90, time remain", encoding="utf-8")
    assert relay.poll()["current"] == 1
    with log.open("a", encoding="utf-8") as handle:
        handle.write("ing: 4s\n")
    assert relay.poll()["phase"] == "rendered 2/90 frames · ETA 4s"
    assert json.loads(progress.read_text(encoding="utf-8"))["current"] == 2


def test_relay_without_a_progress_path_is_inert(tmp_path: Path) -> None:
    log = tmp_path / "remotion-stdout.log"
    log.write_text("Rendered 5/90\n", encoding="utf-8")
    with mock.patch.dict(remotion.os.environ, {}, clear=False) as environ:
        environ.pop("ASTRID_PROGRESS_PATH", None)
        with RemotionProgressRelay(log) as relay:
            pass
    assert relay.progress_path is None
    assert relay.latest["current"] == 5


def test_progress_path_crosses_the_backend_process_boundary() -> None:
    env = build_child_subprocess_env(
        base={"PATH": "/usr/bin", "ASTRID_PROGRESS_PATH": "/attempt/.astrid-progress.json"},
        parent={"ASTRID_PROGRESS_PATH": "/attempt/.astrid-progress.json"},
    )
    assert env["ASTRID_PROGRESS_PATH"] == "/attempt/.astrid-progress.json"


def test_opaque_render_streams_frames_and_relays_progress(tmp_path: Path, monkeypatch) -> None:
    timeline_path, assets_path = _write_inputs(tmp_path)
    project = _write_project(tmp_path)
    output_path = tmp_path / "review.mp4"
    progress = tmp_path / ".astrid-progress.json"
    monkeypatch.setenv("ASTRID_PROGRESS_PATH", str(progress))
    seen: list[dict[str, object]] = []

    def fake_run(command, **kwargs):
        normalized = [str(part) for part in command]
        if _is_remotion_render_command(normalized):
            seen.append(kwargs)
            sink = kwargs["stdout"]
            sink.write("Bundling 100%\nRendered 45/90, time remaining: 3s\nEncoded 90/90\n")
            sink.flush()
            _write_fake_remotion_output(normalized)
        return subprocess.CompletedProcess(command, 0, stdout=None, stderr="")

    with (
        mock.patch.object(remotion, "_regenerate_element_registries"),
        mock.patch.object(
            remotion,
            "_effective_registry_state",
            return_value={"version": 1, "hash": "registry-hash"},
        ),
        mock.patch.object(remotion, "_available_remotion_port", return_value=3001),
        mock.patch.object(remotion.subprocess, "run", side_effect=fake_run),
    ):
        remotion._execute_remotion(
            timeline_path,
            assets_path,
            output_path,
            provenance_out_path=output_path,
            project_dir=project,
            composition_id="TimelineComposition",
            theme_path=None,
            min_free_gb=None,
            review={"shots": []},
            render_scale=1 / 3,
        )

    assert len(seen) == 1
    assert seen[0]["env"][remotion.REMOTION_STREAM_FRAMES_ENV] == "1"
    assert seen[0]["stderr"] is subprocess.PIPE
    assert json.loads(progress.read_text(encoding="utf-8")) == {
        "phase": "encoded 90/90 frames",
        "percent": 90,
        "current": 90,
        "total": 90,
    }
