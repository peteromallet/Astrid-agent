from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from astrid.core.execution import process_group
from astrid.core.rendering import RenderPlan, RenderResult, SupportReport
from astrid.core.rendering import transport as transport_module
from astrid.core.rendering.errors import (
    RendererBinaryMissingError,
    RendererInternalError,
    RendererProtocolError,
    RendererTimeoutError,
)
from astrid.core.rendering.transport import CommandTransport, _resolve_executable

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures"
WIRE_FIXTURE_DIR = FIXTURE_DIR / "v1"
BACKEND_SCRIPT = FIXTURE_DIR / "transport_backend.py"
RENDERER_ID = "acme.visual"


def test_absolute_executable_symlink_is_preserved(tmp_path: Path) -> None:
    executable = tmp_path / "python"
    executable.symlink_to(sys.executable)

    resolved = _resolve_executable(
        str(executable),
        cwd=tmp_path,
        child_env={"PATH": os.defpath},
        backend=RENDERER_ID,
    )

    assert resolved == str(executable)


def _wire_fixture(name: str) -> dict[str, Any]:
    return json.loads((WIRE_FIXTURE_DIR / name).read_text(encoding="utf-8"))


def _request(tmp_path: Path, payload: dict[str, Any]) -> Path:
    path = tmp_path / "request.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _run(
    tmp_path: Path,
    payload: dict[str, Any],
    *,
    verb: str = "render",
    backend: str = RENDERER_ID,
    timeout: float = 5,
    env: dict[str, str] | None = None,
    transport: CommandTransport | None = None,
):
    selected = transport or CommandTransport(backend, termination_grace=0.15)
    result_path = tmp_path / "result.json"
    value = selected.run(
        verb,
        [sys.executable, BACKEND_SCRIPT],
        request_path=_request(tmp_path, payload),
        result_path=result_path,
        cwd=FIXTURE_DIR,
        env=env,
        timeout=timeout,
    )
    return selected, value


def _assert_pid_disappears(pid: int, *, timeout: float = 3) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.02)
    pytest.fail(f"process {pid} survived process-group cleanup")


def _tree_request(tmp_path: Path) -> tuple[dict[str, Any], Path, Path]:
    parent_pid_path = tmp_path / "parent.pid"
    child_pid_path = tmp_path / "child.pid"
    return (
        {
            "action": "sleep-tree",
            "ignore_term": True,
            "parent_pid_path": str(parent_pid_path),
            "child_pid_path": str(child_pid_path),
        },
        parent_pid_path,
        child_pid_path,
    )


def test_successful_render_uses_authoritative_result_file(tmp_path: Path) -> None:
    transport, result = _run(
        tmp_path,
        {"action": "result", "payload": _wire_fixture("result.json")},
    )

    assert isinstance(result, RenderResult)
    assert result.video.path == "outputs/visual.mp4"
    assert transport.last_logs == {"stdout": "", "stderr": ""}


def test_none_timeout_waits_for_renderer_result(tmp_path: Path) -> None:
    result = CommandTransport(RENDERER_ID, termination_grace=0.15).run(
        "render",
        [sys.executable, BACKEND_SCRIPT],
        request_path=_request(
            tmp_path,
            {"action": "result", "payload": _wire_fixture("result.json")},
        ),
        result_path=tmp_path / "result.json",
        cwd=FIXTURE_DIR,
        timeout=None,
    )

    assert isinstance(result, RenderResult)
    assert result.video.path == "outputs/visual.mp4"


def test_bare_python3_uses_the_runtime_interpreter_not_child_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    fake_python = fake_bin / "python3"
    fake_python.write_text("#!/bin/sh\nexit 97\n", encoding="utf-8")
    fake_python.chmod(0o755)
    monkeypatch.setenv("PATH", str(fake_bin))

    result = CommandTransport(RENDERER_ID).run(
        "render",
        ["python3", BACKEND_SCRIPT],
        request_path=_request(
            tmp_path,
            {"action": "result", "payload": _wire_fixture("result.json")},
        ),
        result_path=tmp_path / "result.json",
        cwd=FIXTURE_DIR,
        timeout=5,
    )

    assert isinstance(result, RenderResult)


@pytest.mark.parametrize("command_name", ["python3.11", "custom-python"])
def test_explicit_or_versioned_python_command_is_not_rewritten(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    command_name: str,
) -> None:
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    fake_python = fake_bin / command_name
    fake_python.write_text("#!/bin/sh\nexit 97\n", encoding="utf-8")
    fake_python.chmod(0o755)
    monkeypatch.setenv("PATH", str(fake_bin))
    command = str(fake_python) if command_name == "custom-python" else command_name

    with pytest.raises(RendererInternalError) as caught:
        CommandTransport(RENDERER_ID).run(
            "render",
            [command, BACKEND_SCRIPT],
            request_path=_request(tmp_path, {"action": "absent"}),
            result_path=tmp_path / "result.json",
            cwd=FIXTURE_DIR,
            timeout=5,
        )

    assert caught.value.details["returncode"] == 97


@pytest.mark.parametrize(
    ("verb", "fixture_name", "backend", "result_type"),
    [
        ("support", "support.json", "acme.visual", SupportReport),
        ("plan", "plan.json", "fixture.planner", RenderPlan),
        ("finalize", "result.json", "rendering.ffmpeg-finalizer", RenderResult),
    ],
)
def test_each_protocol_verb_uses_its_frozen_result_dto(
    tmp_path: Path,
    verb: str,
    fixture_name: str,
    backend: str,
    result_type: type,
) -> None:
    _, result = _run(
        tmp_path,
        {"action": "result", "payload": _wire_fixture(fixture_name)},
        verb=verb,
        backend=backend,
    )

    assert isinstance(result, result_type)


def test_missing_binary_is_renderer_qualified(tmp_path: Path) -> None:
    request_path = _request(tmp_path, {"action": "absent"})

    with pytest.raises(RendererBinaryMissingError) as caught:
        CommandTransport(RENDERER_ID).run(
            "render",
            ["astrid-renderer-that-does-not-exist"],
            request_path=request_path,
            result_path=tmp_path / "result.json",
            cwd=FIXTURE_DIR,
            timeout=1,
        )

    assert caught.value.error.kind == "binary_missing"
    assert caught.value.error.backend == RENDERER_ID


def test_nonzero_exit_is_internal_and_captures_both_streams(tmp_path: Path) -> None:
    with pytest.raises(RendererInternalError) as caught:
        _run(
            tmp_path,
            {
                "action": "nonzero",
                "returncode": 23,
                "stdout": "renderer stdout",
                "stderr": "renderer stderr",
            },
        )

    assert caught.value.error.kind == "internal"
    assert caught.value.error.backend == RENDERER_ID
    assert caught.value.details["returncode"] == 23
    assert "renderer stdout" in caught.value.details["stdout"]
    assert "renderer stderr" in caught.value.details["stderr"]


def test_timeout_kills_process_group_and_reaps_direct_child(tmp_path: Path) -> None:
    payload, parent_pid_path, child_pid_path = _tree_request(tmp_path)

    with pytest.raises(RendererTimeoutError) as caught:
        _run(tmp_path, payload, timeout=0.5)

    parent_pid = int(parent_pid_path.read_text(encoding="utf-8"))
    assert caught.value.error.kind == "timeout"
    assert caught.value.error.backend == RENDERER_ID
    with pytest.raises(ChildProcessError):
        os.waitpid(parent_pid, os.WNOHANG)
    _assert_pid_disappears(parent_pid)


def test_sigterm_ignoring_child_is_escalated_and_reaped(tmp_path: Path) -> None:
    """A child tree that ignores SIGTERM must still be SIGKILLed and reaped."""
    payload, parent_pid_path, child_pid_path = _tree_request(tmp_path)

    with pytest.raises(RendererTimeoutError) as caught:
        _run(tmp_path, payload, timeout=0.5)

    assert caught.value.error.kind == "timeout"
    parent_pid = int(parent_pid_path.read_text(encoding="utf-8"))
    _assert_pid_disappears(parent_pid)


def test_sigint_kills_process_group_reaps_and_reraises(tmp_path: Path) -> None:
    payload, parent_pid_path, child_pid_path = _tree_request(tmp_path)

    def interrupt_when_started() -> None:
        deadline = time.monotonic() + 5
        while not child_pid_path.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        if child_pid_path.exists():
            os.kill(os.getpid(), signal.SIGINT)

    interrupter = threading.Thread(target=interrupt_when_started, daemon=True)
    interrupter.start()
    with pytest.raises(KeyboardInterrupt) as caught:
        _run(tmp_path, payload, timeout=10)
    interrupter.join(timeout=1)

    parent_pid = int(parent_pid_path.read_text(encoding="utf-8"))
    child_pid = int(child_pid_path.read_text(encoding="utf-8"))
    assert caught.value.renderer_error.kind == "interrupted"
    assert caught.value.renderer_error.backend == RENDERER_ID
    with pytest.raises(ChildProcessError):
        os.waitpid(parent_pid, os.WNOHANG)
    _assert_pid_disappears(parent_pid)
    _assert_pid_disappears(child_pid)


@pytest.mark.parametrize("inherited", [False, True])
@pytest.mark.parametrize("interrupted", [False, True])
def test_detached_tree_timeout_and_interrupt_preserve_host_and_sibling(
    tmp_path, monkeypatch, inherited, interrupted,
):
    registry = tmp_path / "identities"
    fixture = FIXTURE_DIR / "owned_tree_backend.py"
    if inherited:
        monkeypatch.setenv("ASTRID_RENDER_INHERIT_PROCESS_GROUP", "1")
    else:
        monkeypatch.delenv("ASTRID_RENDER_INHERIT_PROCESS_GROUP", raising=False)
    sibling = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(15)"])
    original_observe = transport_module.observe_tree
    original_killpg = os.killpg
    observed = []
    raised_interrupt = []

    def observe(process):
        live = original_observe(process)
        if not observed:
            observed.append(process)
        if interrupted and len(live) == 4 and not raised_interrupt:
            raised_interrupt.append(True)
            raise KeyboardInterrupt()
        return live

    def killpg(pgid, sig):
        assert pgid != os.getpgrp(), "transport signalled its containing group"
        original_killpg(pgid, sig)

    monkeypatch.setattr(transport_module, "observe_tree", observe)
    monkeypatch.setattr(os, "killpg", killpg)
    try:
        expected = KeyboardInterrupt if interrupted else RendererTimeoutError
        with pytest.raises(expected):
            CommandTransport(RENDERER_ID, termination_grace=0.05).run(
                "render", [sys.executable, fixture, "backend", tmp_path, registry],
                request_path=_request(tmp_path, {}), result_path=tmp_path / "result.json",
                cwd=FIXTURE_DIR, timeout=3 if interrupted else 0.5,
            )
        assert len(observed[0]._astrid_tree_members) == 4
        for pid in observed[0]._astrid_tree_members:
            _assert_pid_disappears(pid)
        assert sibling.poll() is None
        assert os.getpid() in process_group._process_snapshot()
        stopped_write = (registry / "last-write.txt").read_text()
        time.sleep(0.1)
        assert (registry / "last-write.txt").read_text() == stopped_write
        assert hasattr(observed[0], "_astrid_process_group_id") is not inherited
    finally:
        for process in observed:
            snapshot = process_group._process_snapshot()
            for pid, birth in reversed(tuple(getattr(process, "_astrid_tree_members", {}).items())):
                info = snapshot.get(pid)
                if info is not None and info.birth == birth:
                    os.kill(pid, signal.SIGKILL)
            process.wait(timeout=3)
        sibling.terminate()
        sibling.wait(timeout=3)


def test_absent_result_file_is_protocol_failure(tmp_path: Path) -> None:
    with pytest.raises(RendererProtocolError) as caught:
        _run(tmp_path, {"action": "absent"})

    assert caught.value.error.kind == "protocol"
    assert caught.value.error.backend == RENDERER_ID


def test_malformed_result_json_is_protocol_failure(tmp_path: Path) -> None:
    with pytest.raises(RendererProtocolError) as caught:
        _run(tmp_path, {"action": "malformed"})

    assert caught.value.error.kind == "protocol"
    assert caught.value.error.backend == RENDERER_ID


def test_incompatible_result_version_is_protocol_failure(tmp_path: Path) -> None:
    payload = _wire_fixture("result.json")
    payload["schema_version"] = 2

    with pytest.raises(RendererProtocolError) as caught:
        _run(tmp_path, {"action": "result", "payload": payload})

    assert caught.value.error.kind == "protocol"
    assert caught.value.error.backend == RENDERER_ID
    assert caught.value.details["received"] == 2


def test_success_logs_capture_and_redact_stdout_and_stderr(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "transport-log-secret-value"
    monkeypatch.setenv("TRANSPORT_LOG_SECRET", secret)

    transport, result = _run(
        tmp_path,
        {
            "action": "result",
            "payload": _wire_fixture("result.json"),
            "stdout": "render complete",
            "stderr": f"OPENAI_API_KEY={secret}",
        },
    )

    assert isinstance(result, RenderResult)
    assert any("render complete" in log for log in result.logs)
    assert secret not in json.dumps(result.logs)
    assert "[redacted]" in transport.last_logs["stderr"]


def test_environment_is_allowlisted_and_host_secrets_are_not_passed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TRANSPORT_HOST_SECRET_TOKEN", "must-not-reach-child")

    _, result = _run(
        tmp_path,
        {
            "action": "environment",
            "name": "TRANSPORT_HOST_SECRET_TOKEN",
            "safe_name": "LANG",
            "payload": _wire_fixture("result.json"),
        },
        env={
            "TRANSPORT_HOST_SECRET_TOKEN": "overlay-must-not-reach-child",
            "LANG": "transport-safe-locale",
        },
    )

    assert isinstance(result, RenderResult)
    assert result.metadata == {
        "secret_value": "absent",
        "safe_value": "transport-safe-locale",
    }


def test_pinned_renderer_runtime_is_propagated_to_protocol_child(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ASTRID_NODE_EXECUTABLE", "/srv/astrid/node")
    monkeypatch.setenv("ASTRID_REMOTION_PROJECT_DIR", "/srv/astrid/remotion")
    monkeypatch.setenv(
        "ASTRID_TIMELINE_SCHEMA_PYTHONPATH", "/srv/astrid/timeline-schema"
    )

    _, result = _run(
        tmp_path,
        {
            "action": "environment",
            "name": "ASTRID_NODE_EXECUTABLE",
            "safe_name": "ASTRID_REMOTION_PROJECT_DIR",
            "payload": _wire_fixture("result.json"),
        },
    )

    assert isinstance(result, RenderResult)
    assert result.metadata == {
        "secret_value": "/srv/astrid/node",
        "safe_value": "/srv/astrid/remotion",
    }
