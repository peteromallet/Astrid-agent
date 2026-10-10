"""Frame owners belong to the host that serves them: they stop with it, idle out, and leave no files."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

import pytest

from astrid.packs.rendering.backends.remotion import run as remotion_run

WORKER = Path(__file__).resolve().parents[3] / "remotion" / "src" / "astrid-frame-worker.mjs"
STUB_RENDERER = """
export const ensureBrowser = async () => ({ path: null });
export const openBrowser = async () => ({ close: async () => {} });
export const renderFrames = async () => {};
export const selectComposition = async () => ({});
"""


def test_the_owner_belongs_to_the_pack_host_above_it(monkeypatch):
    monkeypatch.setenv(remotion_run.OWNER_HOST_ENV, "4242")
    assert remotion_run.owning_host_pid() == 4242
    monkeypatch.delenv(remotion_run.OWNER_HOST_ENV)
    table = {os.getpid(): "500 python -m astrid.packs.rendering.executors.timeline_visualize.run",
             500: "400 /usr/bin/python3 -m astrid.core.execution.generic_host run --profile x"}

    class Done:
        def __init__(self, out):
            self.stdout = out

    monkeypatch.setattr(remotion_run.subprocess, "run", lambda argv, **k: Done(table.get(int(argv[-1]), "")))
    assert remotion_run.owning_host_pid() == 500
    table = {os.getpid(): "1 python my_script.py"}  # no host above: the starting process owns it
    assert remotion_run.owning_host_pid() == os.getpid()


def test_the_launch_hands_the_owner_its_host(monkeypatch, tmp_path):
    calls = []

    class Done:
        returncode, stdout, stderr = 0, "", ""

    monkeypatch.setattr(remotion_run.sys, "platform", "darwin")
    monkeypatch.setattr(remotion_run.Path, "is_file", lambda self: True)
    monkeypatch.setattr(remotion_run.subprocess, "run", lambda command, **k: calls.append(command) or Done())
    remotion_run.PersistentRemotionFrameSession._launch_owner(
        socket_path=tmp_path / ("owner-" + "b" * 32 + ".sock"), project_dir=tmp_path, node_executable=tmp_path / "node",
        helper=tmp_path / "w.mjs",
        child_environment={"ASTRID_FRAME_WORKER_ROOT": str(tmp_path), remotion_run.OWNER_HOST_ENV: "777"})
    submit = calls[-1]
    assert submit[:3] == ["/bin/launchctl", "submit", "-l"] and f"{remotion_run.OWNER_HOST_ENV}=777" in submit


def test_records_name_their_host_and_stale_owner_files_are_swept(tmp_path, monkeypatch):
    monkeypatch.setenv("ASTRID_NODE_EXECUTABLE", "/n")
    root = tmp_path / "rfo"
    root.mkdir()
    remotion_run.write_owner_record(root / "owner-live.sock", project_dir=tmp_path, host_pid=99)
    assert json.loads((root / "owner-live.json").read_text())["host_pid"] == 99
    old = time.time() - 2 * remotion_run.OWNER_FILE_MAX_AGE_S
    for name in ("owner-stale.lock", "owner-stale.json", "owner-young.lock", "owner-live.lock", "owner-mine.lock",
                 "owner-held.lock"):
        (root / name).write_text("")
        if name != "owner-young.lock":
            os.utime(root / name, (old, old))
    os.utime(root / "owner-live.json", (old, old))
    (root / "owner-live.sock").write_text("")  # a live owner keeps its files
    held = (root / "owner-held.lock").open("a+")
    import fcntl

    fcntl.flock(held.fileno(), fcntl.LOCK_EX)
    try:
        removed = remotion_run.sweep_owner_files(root, keep=root / "owner-mine.sock")
    finally:
        held.close()
    assert sorted(removed) == ["owner-stale.json", "owner-stale.lock"]
    assert sorted(p.name for p in root.iterdir()) == ["owner-held.lock", "owner-live.json", "owner-live.lock",
                                                      "owner-live.sock", "owner-mine.lock", "owner-young.lock"]


def _start_owner(tmp_path: Path, env: dict[str, str]) -> tuple[subprocess.Popen, Path]:
    node = os.environ.get("ASTRID_NODE_EXECUTABLE") or shutil.which("node")
    if not node:
        pytest.skip("no Node")
    source = WORKER.read_text(encoding="utf-8")
    (tmp_path / "worker.mjs").write_text(source.replace("from '@remotion/renderer';", "from './renderer.mjs';"))
    (tmp_path / "renderer.mjs").write_text(STUB_RENDERER)
    short = Path(tempfile.mkdtemp(prefix="rfo", dir="/tmp"))  # Unix sockets need a short path
    sock = short / "owner-lifecycletest.sock"
    for suffix in (".json", ".lock"):
        sock.with_suffix(suffix).write_text("{}")
    process = subprocess.Popen([node, str(tmp_path / "worker.mjs"), "--server", str(sock)], stdin=subprocess.DEVNULL,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env={**os.environ, **env})
    deadline = time.time() + 10
    while not sock.exists() and time.time() < deadline:
        time.sleep(0.05)
    assert sock.exists(), "the owner did not start"
    return process, sock


def _gone(process: subprocess.Popen, sock: Path, seconds: float) -> bool:
    try:
        process.wait(timeout=seconds)
    except subprocess.TimeoutExpired:
        process.kill()
        return False
    leftovers = [p.name for p in sock.parent.iterdir()]
    shutil.rmtree(sock.parent, ignore_errors=True)
    return not leftovers


def test_an_owner_stops_when_its_host_is_gone_and_removes_its_files(tmp_path):
    host = subprocess.Popen(["/bin/sleep", "30"])
    process, sock = _start_owner(tmp_path, {remotion_run.OWNER_HOST_ENV: str(host.pid), "ASTRID_FRAME_OWNER_POLL_MS": "100",
                                            "ASTRID_TIMELINE_FRAME_IDLE_SECONDS": "600"})
    time.sleep(0.5)
    assert process.poll() is None  # the host lives: the owner stays
    host.kill()
    host.wait()
    assert _gone(process, sock, 10), "the owner outlived its host or left its socket/record/lock"


def test_an_idle_owner_exits_and_removes_its_files(tmp_path):
    process, sock = _start_owner(tmp_path, {"ASTRID_TIMELINE_FRAME_IDLE_SECONDS": "1"})
    assert _gone(process, sock, 10)
