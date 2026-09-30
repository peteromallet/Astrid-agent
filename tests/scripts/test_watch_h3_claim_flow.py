from __future__ import annotations

import io
import json
from pathlib import Path

from scripts import watch_h3_claim_flow as watcher


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def _snapshot(run_dir: Path, *, receipt_dir: Path | None = None) -> dict[str, dict[str, str]]:
    return watcher.inspect_local_state(
        claim_path=run_dir / "runpod" / "claim-handle.json",
        execution_request_path=run_dir / "execution-request.json",
        receipt_dir=receipt_dir or run_dir / "receipts",
    )


def test_missing_files_are_local_pending_or_unknown(tmp_path: Path) -> None:
    receipts = tmp_path / "receipts"
    receipts.mkdir()
    (receipts / "historical-composition-review.md").write_text("not a stage receipt", encoding="utf-8")
    snapshot = _snapshot(tmp_path)

    assert snapshot["allocation"]["state"] == "allocation_pending/unknown"
    assert snapshot["pod"] == {
        "phase": "pod",
        "state": "unknown",
        "pod_id": "unknown",
        "readiness": "unknown",
    }
    assert snapshot["execution_request"]["presence"] == "missing"
    assert snapshot["h3:prepare"]["state"] == "missing"
    assert snapshot["h3:compose"]["state"] == "missing"
    assert snapshot["h3:final"]["state"] == "missing"
    assert snapshot["cleanup"]["state"] == "pending/unknown"


def test_claim_execution_h3_stages_and_cleanup_are_reported_from_local_json(tmp_path: Path) -> None:
    pod_id = "pod-local-1"
    _write_json(
        tmp_path / "runpod" / "claim-handle.json",
        {
            "schema_version": "astrid.runpod.claim.v1",
            "pod_id": pod_id,
            "runtime_preflight": {"probe": "CUDA ready"},
        },
    )
    _write_json(
        tmp_path / "execution-request.json",
        {
            "status": "queued",
            "target": {"kind": "runpod", "pod_id": pod_id},
            "lifecycle": {"mode": "leave_running"},
        },
    )
    receipt_dir = tmp_path / "h3-output"
    for number, name in enumerate(("prepare", "compile", "validate", "run", "compose", "verify"), 1):
        _write_json(receipt_dir / f"{number:02d}-{name}-result.json", {"status": "completed"})
    _write_json(
        receipt_dir / "07-final-receipt.json",
        {
            "kind": "h3_av_final_receipt",
            "overall_status": "complete",
            "states": {"cleanup_verified": {"status": "passed", "evidence": {}}},
        },
    )

    snapshot = _snapshot(tmp_path, receipt_dir=receipt_dir)

    assert snapshot["allocation"]["state"] == "claimed"
    assert snapshot["pod"]["pod_id"] == pod_id
    assert snapshot["pod"]["readiness"] == "ready"
    assert snapshot["execution_request"]["presence"] == "present"
    assert snapshot["execution_request"]["status"] == "queued"
    assert snapshot["execution_request"]["lifecycle"] == "leave_running"
    assert all(snapshot[f"h3:{stage}"]["state"] == "completed" for stage in (
        "prepare", "compile", "validate", "run", "compose", "verify"
    ))
    assert snapshot["h3:final"]["state"] == "complete"
    assert snapshot["cleanup"]["state"] == "passed"


def test_leave_running_is_explicit_without_cleanup_receipt(tmp_path: Path) -> None:
    _write_json(
        tmp_path / "runpod" / "claim-handle.json",
        {"schema_version": "astrid.runpod.claim.v1", "pod_id": "pod-1"},
    )
    _write_json(
        tmp_path / "execution-request.json",
        {
            "target": {"kind": "runpod", "pod_id": "pod-1"},
            "lifecycle": {"mode": "leave_running"},
        },
    )

    snapshot = _snapshot(tmp_path)

    assert snapshot["cleanup"] == {
        "phase": "cleanup",
        "state": "leave_running",
        "mode": "leave_running",
    }


def test_exact_terminal_cleanup_takes_precedence_over_stale_ready_handle(
    tmp_path: Path,
) -> None:
    claim = {
        "schema_version": "astrid.runpod.claim.v1",
        "pod_id": "pod-terminated",
        "network_volume_id": "vol-backup",
        "runtime_preflight": {"probe": "CUDA ready"},
    }
    _write_json(tmp_path / "runpod" / "claim-handle.json", claim)
    _write_json(
        tmp_path / "receipts" / "terminal-cleanup.json",
        {
            "claim_handle_digest": watcher._canonical_digest(claim),
            "pod_id": "pod-terminated",
            "cleanup_status": "complete",
            "cleanup": {
                "pod_id": "pod-terminated",
                "status": "terminated",
                "backup_volume_id": "vol-backup",
                "backup_volume_preserved": True,
            },
        },
    )

    snapshot = _snapshot(tmp_path)

    assert snapshot["pod"]["state"] == "terminated"
    assert snapshot["pod"]["readiness"] == "terminated"
    assert snapshot["pod"]["pod_id"] == "pod-terminated"
    assert snapshot["cleanup"]["state"] == "terminated"


def test_allocation_unknown_and_target_mismatch_fail_closed(tmp_path: Path) -> None:
    _write_json(
        tmp_path / "runpod" / "claim-handle.json",
        {"state": "allocation_unknown", "request_name": "claim-1"},
    )
    first = _snapshot(tmp_path)
    assert first["allocation"]["state"] == "allocation_unknown"
    assert first["pod"]["pod_id"] == "unknown"

    _write_json(
        tmp_path / "runpod" / "claim-handle.json",
        {"schema_version": "astrid.runpod.claim.v1", "pod_id": "pod-1"},
    )
    _write_json(
        tmp_path / "execution-request.json",
        {"target": {"kind": "runpod", "pod_id": "pod-2"}},
    )
    second = _snapshot(tmp_path)
    assert second["execution_request"]["state"] == "target_mismatch"
    assert second["execution_request"]["status"] == "target_mismatch"


def test_emit_transitions_is_quiet_until_local_state_changes(tmp_path: Path) -> None:
    before = _snapshot(tmp_path)
    initial = io.StringIO()
    assert watcher.emit_transitions(before, stream=initial) == len(before)
    assert "phase=allocation state=allocation_pending/unknown" in initial.getvalue()

    unchanged = io.StringIO()
    assert watcher.emit_transitions(before, before, stream=unchanged) == 0
    assert unchanged.getvalue() == ""

    _write_json(
        tmp_path / "runpod" / "claim-handle.json",
        {"schema_version": "astrid.runpod.claim.v1", "pod_id": "pod-1"},
    )
    after = _snapshot(tmp_path)
    changed = io.StringIO()
    assert watcher.emit_transitions(after, before, stream=changed) == 2
    assert "state=allocation_pending/unknown->claimed" in changed.getvalue()
    assert "phase=pod state=unknown->ready" in changed.getvalue()


def test_cli_once_accepts_a_claim_handle_and_never_sleeps(tmp_path: Path, capsys) -> None:
    claim = tmp_path / "runpod" / "claim-handle.json"
    _write_json(claim, {"schema_version": "astrid.runpod.claim.v1", "pod_id": "pod-1"})

    assert watcher.main([str(claim), "--once", "--poll-seconds", "3"]) == 0

    output = capsys.readouterr().out
    assert "watching=local-only" in output
    assert "poll_seconds=3" in output
    assert "pod_id=\"pod-1\"" in output
