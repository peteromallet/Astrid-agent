"""F05: exact v3 Wan routing with only the upstream native API substituted.

The static contract qualifies the migrated Wan action and its direct guard;
host custody and settlement run with a synthetic upstream API only.
"""
from __future__ import annotations

import hashlib
import importlib
import json
import os
import sys
from pathlib import Path

import pytest
import yaml

from astrid.core.execution.generic_host import GenericPackHost, HostError
from astrid.core.pack.entrypoint import canonical_runtime_entrypoint
from astrid.core.pack.loader import load_pack_manifest
from astrid.core.pack.discovery import DiscoveredPack
from astrid.packs.wan2gp.src import driver
from astrid.sdk.actions import action_executor_definition
from tests.test_generic_host import FakeRuntime
from tests.test_wan2gp_adapter_session import _FAKE_API, _fake_upstream


FIXTURE = Path(__file__).parent / "fixtures" / "wan2gp_v3"
CAPABILITY = "wan2gp.generate_video"
GUARD_MODULE = "astrid.packs.wan2gp.actions.generate_video.run"


def test_v3_wan_exact_route_custody_and_fail_closed(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("ASTRID_SOURCE_STATE", str(tmp_path / "absent-state.json"))
    monkeypatch.delenv("ASTRID_PACKS_PATH", raising=False)
    monkeypatch.delenv("ASTRID_INTERNAL_INVOCATION", raising=False)
    declaration_bytes = (FIXTURE / "pack.yaml").read_bytes()
    assert len(declaration_bytes) < 16 * 1024
    assert len(_FAKE_API.encode()) < 16 * 1024
    declaration = yaml.safe_load(declaration_bytes)["actions"]["generate_video"]
    # Canonical admission requires the pack ID to match its folder name.
    # Stage only the reserved synthetic declaration, never production code.
    staged_root = tmp_path / "wan2gp"
    staged_root.mkdir()
    staged_manifest = staged_root / "pack.yaml"
    staged_manifest.write_bytes(declaration_bytes)
    assert staged_manifest.read_bytes() == declaration_bytes
    assert hashlib.sha256(staged_manifest.read_bytes()).hexdigest() == hashlib.sha256(declaration_bytes).hexdigest()
    actual_root = Path(driver.__file__).parents[1]
    runtime = FakeRuntime()
    host = GenericPackHost(pack_roots=[actual_root], client=runtime,
                           executor_id="f05-wan-v3", attempt_base=tmp_path / "attempts")
    children = []
    track = host._track_process

    def track_child(process):
        track(process)
        children.append(process)
        # The capacity monitor owns containment of this test and native stub
        # groups. This hook only observes real host-owned child creation.
        process_groups = os.environ.get("F05_PROCESS_GROUPS")
        if process_groups:
            path = Path(process_groups)
            groups = json.loads(path.read_text())
            path.write_text(json.dumps(sorted(set([*groups, process.pid]))))

    monkeypatch.setattr(host, "_track_process", track_child)
    fallback_calls = []

    def forbidden_fallback(*args, **kwargs):
        fallback_calls.append(True)
        raise AssertionError("generic command/worker fallback was reached")

    monkeypatch.setattr(host, "_run_command_definition", forbidden_fallback)
    monkeypatch.setattr(host, "invoke_capability", forbidden_fallback)
    trace = None
    proof = {"declaration_sha256": hashlib.sha256(declaration_bytes).hexdigest()}
    try:
        records = host.discover()
        assert {record.id for record in records} == {CAPABILITY, "wan2gp.validate_settings"}
        record = host.capabilities[CAPABILITY]
        definition = record.definition
        assert definition.kind == "external"
        assert definition.command.argv == ("{python_exec}", "-m", GUARD_MODULE)
        assert definition.metadata["action_invocation"] == declaration["invocation"]
        for key in ("host_session", "output_result_manifest", "runtime_file", "runtime_module"):
            assert definition.metadata[key] == declaration["metadata"][key]
        expected_host = GenericPackHost(pack_roots=[staged_root])
        expected_record, = expected_host.discover()
        expected = expected_record.definition
        expected_sdk = action_executor_definition(DiscoveredPack(load_pack_manifest(staged_manifest), "extra", 0),
                                         "generate_video", declaration)
        assert expected_sdk.to_dict() == expected.to_dict()
        actual_pack = load_pack_manifest(actual_root / "pack.yaml")
        sdk = action_executor_definition(DiscoveredPack(actual_pack, "extra", 0),
                                         "generate_video", actual_pack.actions["generate_video"])
        assert sdk.to_dict() == definition.to_dict()
        # Compare the migrated pack's public contract to the static fixture.
        # Discovery-owned source roots and authored prose differ by source.
        for key in ("id", "kind", "name", "version", "inputs", "outputs", "conditions", "cache", "isolation", "graph", "command"):
            assert definition.to_dict()[key] == expected.to_dict()[key]
        assert actual_pack.actions["generate_video"]["invocation"] == declaration["invocation"]
        assert actual_pack.actions["generate_video"]["metadata"] == declaration["metadata"]
        assert record.resource_keys == expected_record.resource_keys
        assert record.adapter == expected_record.adapter
        proof["actual_pack_manifest_sha256"] = hashlib.sha256((actual_root / "pack.yaml").read_bytes()).hexdigest()
        proof["actual_v3_matches_static_contract"] = True
        assert [port.name for port in definition.outputs] == ["generated_videos", "video_manifest"]
        host.register()

        def task(name, session=None):
            value = {"task": {"id": name, "capability": CAPABILITY, "project_id": "fixture-project",
                              "attempt_id": name + "-attempt", "fence": 1,
                              "spec": {"spec": {"inputs": {"prompt": name, "model": "wan-2.2"}}}}}
            if session is not None:
                value["task"]["wan_session"] = session
            runtime.tasks[name] = value
            return value

        # No explicit session admission: reject before child, guard, fallback,
        # output or settlement. The production guard is not imported yet.
        assert GUARD_MODULE not in sys.modules
        with pytest.raises(HostError, match="explicit wan_session admission"):
            host.run_task(task("missing"), lease_token="lease-missing")
        assert not children and not fallback_calls and not runtime.settlements
        assert GUARD_MODULE not in sys.modules
        proof["missing_admission"] = "explicit wan_session admission; no child/guard/fallback/settlement"

        # Enter the canonical guard scope to reach the actual additional Wan
        # direct-run guard, which must reject even an internal runner.
        with canonical_runtime_entrypoint(CAPABILITY):
            guard = importlib.import_module(GUARD_MODULE)
            assert guard.main(["--prompt", "direct fixture", "--model", "wan-2.2"]) == 2
        direct = json.loads(capsys.readouterr().out)
        assert direct["ok"] is False and direct["code"] == "host_session_required"
        assert not children
        proof["direct_guard"] = {"module": GUARD_MODULE, "code": direct["code"], "exit_code": 2}

        root, config, trace = _fake_upstream(tmp_path)
        api = root / "shared/api.py"
        # Only substitute the upstream native execution seam. Include both a
        # cooperative cancellation and a native job that ignores the request.
        api.write_text(api.read_text().replace(
            "        self.cancel_requested.set()",
            "        self.session._trace('native_cancel', native_job_id=self.job_id)\n"
            "        if self.settings.get('prompt') != 'unacknowledged':\n"
            "            self.cancel_requested.set()"))
        assert sum(path.stat().st_size for path in root.rglob("*") if path.is_file()) < 16 * 1024
        session = {"owner_dir": str(tmp_path / "owner"), "root": str(root), "python": sys.executable,
                   "config_path": str(config), "source_digest": "f05-synthetic-source",
                   "config_digest": "f05-synthetic-config", "readiness_timeout": 5, "release_timeout": 1}

        result = host.run_task(task("success", session), lease_token="lease-success", keep_attempt=True)
        assert result["task"]["status"] == "completed"
        assert len(runtime.settlements) == 1 and not fallback_calls
        payload = runtime.settlements[0][2]
        native = payload["result"]["wan_native"]
        mapping = payload["result"]["wan_mapping"]
        assert native["terminal"] is True and native["native_job_id"] == "native-job-1"
        assert native["result"]["success"] is True
        assert native["output_snapshots"] == driver.snapshot_native_outputs(
            native["result"]["generated_files"], native["source_root"])
        output_root = Path(mapping["output_root"])
        driver.verify_host_result(mapping, attempt_root=output_root)
        assert output_root.is_relative_to(tmp_path / "attempts")
        assert (output_root / mapping["generated_files"][0]).read_bytes() == b"native-wan-bytes-1"
        assert any(obj["data"] == b"native-wan-bytes-1" for obj in runtime.uploaded_objects.values())
        # The universal manifest is the host's custody/harvest control file;
        # its listed video result is what the existing path publishes.
        assert json.loads((output_root / "manifest.json").read_text()) == mapping["manifest"]
        assert {output["name"] for output in payload["outputs"]} == {"generated_videos"}
        assert payload["result"]["managed_tool_session"]["state"] == "settled"
        assert payload["result"]["capability_digest"] == record.capability_digest
        assert payload["result"]["source_digest"] == record.source_digest
        assert payload["result"]["process_evidence"]["process_id"] == children[0].pid
        assert host.managed_tool_session.active and children[0].poll() is None
        proof["success"] = {"task_status": "completed", "managed_state": "settled", "native_terminal": True,
                            "declared_output_ports": [port.name for port in definition.outputs],
                            "settled_output_ports": sorted(output["name"] for output in payload["outputs"]),
                            "custody_manifest_verified": True,
                            "synthetic_output_bytes": len(b"native-wan-bytes-1"),
                            "capability_digest": record.capability_digest, "source_digest": record.source_digest,
                            "resource_keys": list(record.resource_keys)}

        current_task = runtime.task

        def cancel_after_submit(name):
            value = current_task(name)
            if name in {"cancelled", "unacknowledged"} and any(
                row["event"] == "submit" and row["settings"].get("prompt") == name
                for row in json.loads(trace.read_text())):
                value["task"]["status"] = "cancelled"
            return value

        monkeypatch.setattr(runtime, "task", cancel_after_submit)
        cancelled = host.run_task(task("cancelled", session), lease_token="lease-cancelled", keep_attempt=True)
        assert cancelled["status"] == "cancelled"
        assert [row[0] for row in runtime.settlements] == ["success"]
        assert children[0].poll() is not None and not host._active_processes
        assert not host._cleanup_uncertain
        assert not list((tmp_path / "attempts").glob("*cancelled*/outputs/manifest.json"))
        proof["cancelled"] = {"task_status": "cancelled", "successful_settlement": False,
                              "child_exited": True, "output_manifest_created": False}

        # The ignored cancel returns native success; real host cancellation
        # and manager acknowledgement still prohibit successful settlement.
        with pytest.raises(HostError, match="owned cleanup incomplete"):
            host.run_task(task("unacknowledged", session), lease_token="lease-unacknowledged", keep_attempt=True)
        assert [row[0] for row in runtime.settlements] == ["success"]
        assert host._cleanup_uncertain and not host._active_processes
        assert all(child.poll() is not None for child in children)
        assert not list((tmp_path / "attempts").glob("*unacknowledged*/outputs/manifest.json"))
        with pytest.raises(HostError, match="cleanup uncertainty"):
            host.run_task(task("blocked", session), lease_token="lease-blocked")
        rows = json.loads(trace.read_text())
        assert sum(row["event"] == "native_cancel" for row in rows) >= 2
        proof["unacknowledged"] = {"successful_settlement": False, "cleanup_uncertain": True,
                                   "replacement_blocked": True, "all_children_exited": True,
                                   "output_manifest_created": False}
        proof["fallback_calls"] = len(fallback_calls)
        proof["settled_task_ids"] = [row[0] for row in runtime.settlements]
        proof["projected_kind"] = definition.kind
        proof["sdk_host_definition_equal"] = True
        proof["limits"] = "Synthetic API/bytes only; no native engine/model/media/GPU/network/Runtime realm qualification"
        print(json.dumps(proof, sort_keys=True))
    finally:
        host.shutdown()
        assert not host._active_processes
        assert all(child.poll() is not None for child in children)
