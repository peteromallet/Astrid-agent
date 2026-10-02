"""W2.5: Vibe -> Wan A/B -> Vibe through one host's executor MTS lane."""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

from astrid.core.execution import generic_host as gh
from astrid.core.execution.managed_tool_session import SessionCapacityError, StaleAdmissionError
from astrid.core.generation.backends import vibecomfy
from tests.test_generic_host_vibecomfy_lifecycle import (
    FakeRuntime, _FakeCheckout, _install_fixture, _profile, _task,
)
from tests.test_wan2gp_adapter_session import _fake_upstream


def test_vibe_wan_vibe_serial_on_same_host_lane(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    digest, data = _install_fixture(tmp_path)
    root, config, trace = _fake_upstream(tmp_path)
    capability = tmp_path / "wan-fixture"
    capability.mkdir()
    (capability / "executor.yaml").write_text(json.dumps({
        "schema_version": 1, "id": "wan2gp.generate_video", "name": "Wan lifecycle fixture",
        "kind": "external", "version": "1.0",
        "command": {"argv": ["{python_exec}", "-c", "raise AssertionError('must use host Wan child')"]},
        "inputs": [],
        "outputs": [{"name": "generated_videos", "type": "file", "artifact_type": "video/mp4"}],
        "metadata": {"adapter_family": "local_generation", "resource_keys": ["gpu"]},
    }))
    runtime = FakeRuntime()
    runtime.get_object = lambda requested: data
    host = gh.GenericPackHost(pack_roots=[tmp_path], client=runtime,
                              executor_id="w25-serial", attempt_base=tmp_path / "attempts")
    manager = host.managed_tool_session
    events: list[str] = []
    checkouts: list[_FakeCheckout] = []
    wan_children = []

    def profile():
        document = _profile()
        birth = len(checkouts) + 1
        document["vibecomfy_session"].update({
            "process_birth_id": f"vibe-{birth}", "comfy_process_birth_id": f"comfy-{birth}",
        })
        return document

    monkeypatch.setattr(gh, "_read_readiness_profile_document", profile)
    monkeypatch.setattr(gh, "_prepare_vibecomfy_execution_identity", lambda *args: (
        "execution-a", "model-a", "template-a", {"model_id": "model-a", "resident_metadata": {}}
    ))

    def checkout(cls, **kwargs):
        assert host.managed_tool_session is manager
        assert all(child.poll() is not None for child in wan_children)
        assert all(old.child.poll() is not None for old in checkouts)
        assert not host._active_processes
        events.append("vibe-start")
        adapter = _FakeCheckout(events, spawn_child=True)
        checkouts.append(adapter)
        return adapter

    monkeypatch.setattr(vibecomfy.CheckoutServerAdapter, "from_host_session", classmethod(checkout))
    start = gh._ManagedWanChildAdapter.start
    release = gh._ManagedWanChildAdapter.release

    def wan_start(adapter):
        assert host.managed_tool_session is manager
        assert all(old.child.poll() is not None for old in checkouts)
        assert "release:capacity_replacement" in events
        assert not host._active_processes
        events.append("wan-start")
        start(adapter)
        wan_children.append(adapter.process)

    def wan_release(adapter, **kwargs):
        evidence = release(adapter, **kwargs)
        assert evidence["ok"] and evidence["released"]
        assert adapter.process.poll() is not None
        assert adapter.process not in host._active_processes
        assert not any(info.pgid == adapter.process.pid for info in gh._process_snapshot().values())
        events.append("wan-released")
        return evidence

    monkeypatch.setattr(gh._ManagedWanChildAdapter, "start", wan_start)
    monkeypatch.setattr(gh._ManagedWanChildAdapter, "release", wan_release)
    wan_session = {
        "owner_dir": str(tmp_path / "owner"), "root": str(root), "python": sys.executable,
        "config_path": str(config), "source_digest": "fixture-source", "config_digest": "fixture-config",
        "readiness_timeout": 5, "release_timeout": 5,
    }

    def execute(task):
        task_id = task["task"]["id"]
        runtime.tasks[task_id] = task
        result = host.run_task(task, lease_token=f"lease-{task_id}")
        assert result["task"]["status"] == "completed"
        assert host.managed_tool_session is manager
        assert manager.active and manager.occupied
        assert runtime.settlements[-1][0] == task_id
        return runtime.settlements[-1][2]

    try:
        host.discover()
        first = execute(_task("vibe-first", digest))
        first_binding = manager.current_binding
        first_adapter = manager.current_adapter
        wan = []
        wan_binding = None
        for name in ("wan-A", "wan-B"):
            task = {"task": {
                "id": name, "capability": "wan2gp.generate_video", "project_id": "fixture-project",
                "attempt_id": f"{name}-attempt", "fence": 1, "wan_session": wan_session,
                "spec": {"spec": {"inputs": {"prompt": name, "model": "wan-2.2"}}},
            }}
            settled = execute(task)
            evidence = settled["result"]["wan_native"]
            assert evidence["terminal"] is True
            assert evidence["process_id"] == wan_children[0].pid
            assert evidence["returncode"] == 0
            assert wan_children[0].poll() is None
            assert settled["result"]["process_evidence"] == {
                "capability_id": "wan2gp.generate_video",
                "attempt_id": f"{name}-attempt", "fence": 1,
                "child_boundary": "subprocess",
                "process_id": wan_children[0].pid, "returncode": 0,
            }
            assert evidence["invocation_id"] == f"{name}:{name}-attempt:1"
            assert [event["event_kind"] for event in evidence["events"]] == ["progress", "progress"]
            assert settled["attempt_id"] == f"{name}-attempt"
            assert settled["result"]["managed_tool_session"]["binding"]["process_birth_id"] == manager.current_binding.process_birth_id
            if wan_binding is None:
                wan_binding = manager.current_binding
            else:
                assert manager.current_binding == wan_binding
            wan.append(settled)
        assert first_adapter is not manager.current_adapter
        assert len(wan_children) == 1 and wan_children[0].poll() is None
        snapshots = [item["result"]["wan_native"]["output_snapshots"] for item in wan]
        assert snapshots[0] != snapshots[1]
        payloads = [Path(item["result"]["wan_native"]["result"]["generated_files"][0]).read_bytes() for item in wan]
        assert payloads == [b"native-wan-bytes-1", b"native-wan-bytes-2"]
        assert hashlib.sha256(payloads[0]).hexdigest() != hashlib.sha256(payloads[1]).hexdigest()
        assert wan[0]["outputs"] != wan[1]["outputs"]
        assert wan[0]["result"]["wan_mapping"]["output_root"] != wan[1]["result"]["wan_mapping"]["output_root"]

        # An unverified Wan release must keep the live child in the same slot.
        adapter = manager.current_adapter
        with monkeypatch.context() as patch:
            patch.setattr(adapter, "release", lambda **kwargs: {"ok": False, "released": False})
            with pytest.raises(SessionCapacityError, match="release was not verified"):
                manager.release(reason="capacity_replacement")
            assert manager.current_adapter is adapter
            assert wan_children[0].poll() is None
            assert events.count("vibe-start") == 1
            with pytest.raises(StaleAdmissionError):
                manager.admit(capability_id="vibecomfy.run", invocation_id="blocked-successor")

        last = execute(_task("vibe-last", digest))
        last_binding = manager.current_binding
        assert last_binding.process_birth_id != first_binding.process_birth_id
        assert last_binding.engine_birth_id != first_binding.engine_birth_id
        assert last_binding.execution_identity == first_binding.execution_identity
        assert manager.current_adapter is not first_adapter
        envelopes = [item["result"]["managed_tool_session"] for item in (first, *wan, last)]
        assert {item["terminal"]["invocation_id"] for item in envelopes} == {
            f"{name}:{name}-attempt:1" for name in ("vibe-first", "wan-A", "wan-B", "vibe-last")
        }
        assert len({item["admission_token"] for item in envelopes}) == 4
        assert events.index("old-child-exited") < events.index("release:capacity_replacement") < events.index("wan-start")
        assert events.index("wan-start") < events.index("wan-released") < events.index("vibe-start", events.index("vibe-start") + 1)
        assert [row["event"] for row in json.loads(trace.read_text())] == ["init", "submit", "submit", "close"]
        assert len(runtime.settlements) == 4
    finally:
        host.shutdown()
    assert all(old.child.poll() is not None for old in checkouts)
    assert all(child.poll() is not None for child in wan_children)
    assert not host._active_processes
