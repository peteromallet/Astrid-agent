from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

import pytest
from runpod_lifecycle import AllocationUnknown

from scripts import claim_runpod_5090_backup as claim


_TEST_API_KEY = "rpa_test_secret_must_not_persist"
_VOLUME = {"id": "vol-backup", "name": "backup", "size": 250}


def _args(handle_path: Path, *extra: str):
    return claim._parser().parse_args(["--handle-path", str(handle_path), *extra])


def _offline_volume(monkeypatch: pytest.MonkeyPatch) -> None:
    async def require_existing_volume(config: Any, name: str) -> dict[str, Any]:
        assert name == "backup"
        assert config.storage_name == "backup"
        return dict(_VOLUME)

    monkeypatch.setattr(claim, "_require_existing_volume", require_existing_volume)
    monkeypatch.setenv("RUNPOD_API_KEY", _TEST_API_KEY)


def _write_terminal_cleanup(handle_path: Path, handle: dict[str, Any]) -> Path:
    receipt_path = handle_path.parent.parent / "receipts" / "terminal-cleanup.json"
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    receipt_path.write_text(
        json.dumps(
            {
                "claim_handle_digest": claim._canonical_digest(handle),
                "pod_id": handle["pod_id"],
                "cleanup_status": "complete",
                "cleanup": {
                    "pod_id": handle["pod_id"],
                    "status": "terminated",
                    "backup_volume_id": handle["network_volume_id"],
                    "backup_volume_preserved": True,
                },
            }
        ),
        encoding="utf-8",
    )
    return receipt_path


def test_defaults_match_the_canonical_twelve_hour_claim() -> None:
    args = claim._parser().parse_args([])

    assert args.handle_path == str(claim.DEFAULT_HANDLE_PATH)
    assert args.max_wait_seconds == 43200
    assert args.poll_seconds == 120
    assert args.lifecycle_policy == "leave_running"
    assert args.storage_name == "backup"
    assert args.worker_image == claim.DEFAULT_WORKER_IMAGE
    assert args.template_id is None
    assert args.allowed_cuda_versions == "13.0"


def test_allocation_unknown_persists_secret_free_marker_and_never_opens_another_window(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    handle_path = tmp_path / "claim-handle.json"
    args = _args(
        handle_path,
        "--max-wait-seconds",
        "2",
    )
    _offline_volume(monkeypatch)
    launches: list[str] = []

    async def ambiguous_launch(config: Any, *, name: str, **kwargs: Any) -> Any:
        launches.append(name)
        if len(launches) > 1:
            raise AssertionError("allocation uncertainty opened another capacity window")
        marker = json.loads(handle_path.read_text(encoding="utf-8"))
        assert marker["schema_version"] == "astrid.runpod.allocation-attempt.v1"
        assert marker["state"] == "allocation_pending"
        assert marker["request_name"] == name
        assert marker["storage_name"] == "backup"
        assert marker["network_volume_id"] == "vol-backup"
        assert marker["lifecycle"] == {"mode": "leave_running"}
        assert config.attach_only is True
        assert config.disk_size_gb == 0
        assert config.storage_name == "backup"
        assert config.storage_volumes == ("backup",)
        assert config.worker_image == claim.DEFAULT_WORKER_IMAGE
        assert config.template_id is None
        assert config.allowed_cuda_versions == ("13.0",)
        assert 1 <= kwargs["max_wait_sec"] <= 2
        assert kwargs["retry_interval_sec"] == 120
        raise AllocationUnknown(name, claim.DEFAULT_GPU, 72, "backup", "vol-backup")

    monkeypatch.setattr(claim, "launch_when_available", ambiguous_launch)

    with pytest.raises(AllocationUnknown):
        asyncio.run(claim._claim(args))

    assert len(launches) == 1
    marker_text = handle_path.read_text(encoding="utf-8")
    marker = json.loads(marker_text)
    assert marker["state"] == "allocation_unknown"
    assert marker["reconciliation_required"] is True
    assert marker["request_name"] == launches[0]
    assert marker["gpu_type_attempted"] == claim.DEFAULT_GPU
    assert marker["ram_tier_attempted"] == 72
    assert marker["storage_name_attempted"] == "backup"
    assert marker["storage_volume_id_attempted"] == "vol-backup"
    assert "pod_id" not in marker
    assert _TEST_API_KEY not in marker_text


def test_success_atomically_replaces_marker_with_claim_handle_and_leaves_pod_running(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    handle_path = tmp_path / "claim-handle.json"
    args = _args(handle_path)
    _offline_volume(monkeypatch)
    replace_destinations: list[Path] = []
    real_replace = os.replace

    def recording_replace(source: str | os.PathLike[str], destination: str | os.PathLike[str]) -> None:
        replace_destinations.append(Path(destination))
        real_replace(source, destination)

    monkeypatch.setattr(claim.os, "replace", recording_replace)

    class Pod:
        id = "pod-claimed"
        name = "astrid-claim-5090-backup-test"
        _gpu_type = claim.DEFAULT_GPU
        _storage_name = "backup"
        _storage_volume = "vol-backup"

        def __init__(self) -> None:
            self.terminate_calls = 0

        async def wait_ready(self, *, timeout: int) -> None:
            assert timeout == claim.DEFAULT_READY_TIMEOUT_SECONDS

        async def _ensure_ssh_details(self) -> dict[str, Any]:
            return {"ip": "203.0.113.10", "port": 22022}

        async def terminate(self) -> None:
            self.terminate_calls += 1

    pod = Pod()

    async def successful_launch(config: Any, *, name: str, **kwargs: Any) -> Pod:
        marker = json.loads(handle_path.read_text(encoding="utf-8"))
        assert marker["state"] == "allocation_pending"
        assert marker["request_name"] == name
        assert config.attach_only is True
        assert config.disk_size_gb == 0
        assert kwargs == {"max_wait_sec": 3600, "retry_interval_sec": 120}
        return pod

    async def successful_preflight(pod_arg: Any, release_root: str) -> dict[str, str]:
        assert pod_arg is pod
        assert release_root == claim.DEFAULT_RELEASE_ROOT
        return {
            "release_root": release_root,
            "release_python": f"{release_root}/runtime/venv/bin/python",
            "release_launcher": f"{release_root}/runtime/launch-comfy.sh",
            "probe": "cpu fake",
        }

    monkeypatch.setattr(claim, "launch_when_available", successful_launch)
    monkeypatch.setattr(claim, "_preflight_release", successful_preflight)

    result = asyncio.run(claim._claim(args))

    final_text = handle_path.read_text(encoding="utf-8")
    final = json.loads(final_text)
    assert final == result
    assert final["schema_version"] == "astrid.runpod.claim.v1"
    assert final["state"] == "claimed"
    assert final["pod_id"] == "pod-claimed"
    assert final["storage_name"] == "backup"
    assert final["network_volume_id"] == "vol-backup"
    assert final["network_volume_size_gb"] == 250
    assert final["attach_only"] is True
    assert final["container_disk_gb"] == 200
    assert final["worker_image"] == claim.DEFAULT_WORKER_IMAGE
    assert final["template_id"] is None
    assert final["allowed_cuda_versions"] == ["13.0"]
    assert final["lifecycle"] == {"mode": "leave_running"}
    assert final["reconciliation_required"] is False
    assert final["request_name"].startswith("astrid-claim-5090-backup-")
    assert final["operation_id"][:12] in final["request_name"]
    assert final["handle_path"] == str(handle_path.resolve())
    assert pod.terminate_calls == 0
    assert replace_destinations[-1] == handle_path.resolve()
    assert len(replace_destinations) == 2
    assert not list(tmp_path.glob(".claim-handle.json.*.tmp"))
    assert _TEST_API_KEY not in final_text


def test_allocated_failure_is_recorded_and_leave_running_does_not_retry_or_terminate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    handle_path = tmp_path / "claim-handle.json"
    args = _args(handle_path)
    _offline_volume(monkeypatch)
    launches = 0

    class Pod:
        id = "pod-unverified"
        name = "astrid-claim-5090-backup-unverified"

        def __init__(self) -> None:
            self.terminate_calls = 0

        async def wait_ready(self, *, timeout: int) -> None:
            raise RuntimeError("offline readiness failure")

        async def terminate(self) -> None:
            self.terminate_calls += 1

    pod = Pod()

    async def allocated_launch(config: Any, *, name: str, **kwargs: Any) -> Pod:
        nonlocal launches
        launches += 1
        return pod

    monkeypatch.setattr(claim, "launch_when_available", allocated_launch)

    with pytest.raises(RuntimeError, match="offline readiness failure"):
        asyncio.run(claim._claim(args))

    marker = json.loads(handle_path.read_text(encoding="utf-8"))
    assert launches == 1
    assert pod.terminate_calls == 0
    assert marker["state"] == "allocated_unverified"
    assert marker["pod_id"] == "pod-unverified"
    assert marker["failure_phase"] == "readiness"
    assert marker["failure_type"] == "RuntimeError"
    assert marker["lifecycle"] == {"mode": "leave_running"}
    assert marker["reconciliation_required"] is True


def test_existing_custody_blocks_before_any_provider_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    handle_path = tmp_path / "claim-handle.json"
    handle_path.write_text(
        json.dumps(
            {
                "schema_version": "astrid.runpod.allocation-attempt.v1",
                "state": "allocation_unknown",
                "operation_id": "existing-operation",
                "request_name": "existing-request",
            }
        ),
        encoding="utf-8",
    )
    args = _args(handle_path)

    async def forbidden_volume(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise AssertionError("provider volume lookup must not run")

    async def forbidden_launch(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("provider launch must not run")

    monkeypatch.setattr(claim, "_require_existing_volume", forbidden_volume)
    monkeypatch.setattr(claim, "launch_when_available", forbidden_launch)

    with pytest.raises(RuntimeError, match="requires provider reconciliation"):
        asyncio.run(claim._claim(args))


def test_stale_terminal_claim_is_archived_before_one_new_claim(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    handle_path = tmp_path / "runpod" / "claim-handle.json"
    handle_path.parent.mkdir(parents=True)
    old_handle = {
        "schema_version": "astrid.runpod.claim.v1",
        "pod_id": "pod-terminated",
        "network_volume_id": "vol-backup",
        "storage_name": "backup",
    }
    old_bytes = (json.dumps(old_handle, indent=2, sort_keys=True) + "\n").encode()
    handle_path.write_bytes(old_bytes)
    receipt_path = _write_terminal_cleanup(handle_path, old_handle)
    args = _args(handle_path)
    _offline_volume(monkeypatch)
    launches = 0

    class Pod:
        id = "pod-new"
        name = "astrid-claim-5090-backup-new"
        _gpu_type = claim.DEFAULT_GPU
        _storage_name = "backup"
        _storage_volume = "vol-backup"

        async def wait_ready(self, *, timeout: int) -> None:
            assert timeout == claim.DEFAULT_READY_TIMEOUT_SECONDS

        async def _ensure_ssh_details(self) -> dict[str, Any]:
            return {"ip": "203.0.113.20", "port": 22022}

    async def launch(config: Any, *, name: str, **kwargs: Any) -> Pod:
        nonlocal launches
        launches += 1
        assert launches == 1
        marker = json.loads(handle_path.read_text(encoding="utf-8"))
        assert marker["state"] == "allocation_pending"
        assert marker["previous_claim"]["pod_id"] == "pod-terminated"
        return Pod()

    async def preflight(_pod: Any, release_root: str) -> dict[str, str]:
        return {"release_root": release_root, "probe": "offline ready"}

    monkeypatch.setattr(claim, "launch_when_available", launch)
    monkeypatch.setattr(claim, "_preflight_release", preflight)

    result = asyncio.run(claim._claim(args))

    previous = result["previous_claim"]
    archive_path = Path(previous["archive_path"])
    assert result["pod_id"] == "pod-new"
    assert launches == 1
    assert previous["cleanup_receipt_path"] == str(receipt_path.resolve())
    assert previous["handle_digest"] == claim._canonical_digest(old_handle)
    assert archive_path.read_bytes() == old_bytes
    assert json.loads(handle_path.read_text(encoding="utf-8")) == result


def test_active_claim_handle_refuses_before_any_provider_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    handle_path = tmp_path / "runpod" / "claim-handle.json"
    handle_path.parent.mkdir(parents=True)
    handle_path.write_text(
        json.dumps(
            {
                "schema_version": "astrid.runpod.claim.v1",
                "state": "claimed",
                "pod_id": "pod-active",
                "network_volume_id": "vol-backup",
            }
        ),
        encoding="utf-8",
    )

    async def forbidden_volume(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise AssertionError("provider volume lookup must not run")

    async def forbidden_launch(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("provider launch must not run")

    monkeypatch.setattr(claim, "_require_existing_volume", forbidden_volume)
    monkeypatch.setattr(claim, "launch_when_available", forbidden_launch)

    with pytest.raises(RuntimeError, match="already identifies pod pod-active"):
        asyncio.run(claim._claim(_args(handle_path)))
