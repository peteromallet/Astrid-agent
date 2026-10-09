from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

from astrid.core.execution.guards import (
    EVIDENCE_STATUS_NAME,
    ExecutionDeadlineError,
    ExecutionGuardPolicy,
    EvidenceCapError,
    WarmReuseExpectationError,
    read_evidence_status,
    write_evidence_status,
)
import astrid.core.execution.generic_host as generic_host
from astrid.core.execution.generic_host import GenericPackHost


def test_generated_evidence_cap_is_enforced(tmp_path) -> None:
    (tmp_path / "evidence.bin").write_bytes(b"12345")
    policy = ExecutionGuardPolicy(evidence_cap_bytes=4)
    with pytest.raises(EvidenceCapError, match="over its 4-byte cap") as failure:
        policy.assert_evidence_cap(tmp_path)
    diagnostic = failure.value.diagnostic
    assert diagnostic["category"] == "attempt_cap_exceeded"
    assert diagnostic["attempt_observed_bytes"] == 5
    assert diagnostic["cap_bytes"] == 4
    assert diagnostic["observed_bytes"] == 5
    assert diagnostic["generated_file_count"] == 1
    assert diagnostic["path_classes"] == {"evidence.bin": {"files": 1, "bytes": 5}}
    assert diagnostic["largest_paths"] == [
        {"path": "evidence.bin", "bytes": 5, "classification": "generated"}
    ]

    policy = ExecutionGuardPolicy(evidence_cap_bytes=5)
    assert policy.assert_evidence_cap(tmp_path)["observed_bytes"] == 5


def test_generated_evidence_scan_tolerates_a_file_vanishing_during_render(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vanished = tmp_path / "element-2744.jpeg"
    retained = tmp_path / "element-2745.jpeg"
    vanished.write_bytes(b"gone")
    retained.write_bytes(b"kept")

    original_stat = Path.stat

    def stat_without_vanished(path, *args, **kwargs):
        if path == vanished:
            vanished.unlink(missing_ok=True)
            raise FileNotFoundError(path)
        return original_stat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", stat_without_vanished)
    policy = ExecutionGuardPolicy(evidence_cap_bytes=64)
    assert policy.evidence_bytes(tmp_path) == len(b"kept")


def test_generated_evidence_scan_tolerates_outer_walk_directory_vanishing(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original_rglob = Path.rglob

    def rglob_with_vanished_directory(path, pattern):
        if path == tmp_path:
            raise FileNotFoundError(path / "render-service")
        return original_rglob(path, pattern)

    monkeypatch.setattr(Path, "rglob", rglob_with_vanished_directory)
    policy = ExecutionGuardPolicy(evidence_cap_bytes=64)
    measurement = policy.evidence_measurement(tmp_path)
    assert measurement["observed_bytes"] == 0
    assert measurement["vanished_file_count"] == 1


def test_generated_evidence_scan_tolerates_a_file_vanishing_during_input_hash(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source.bin"
    source.write_bytes(b"input")
    policy = ExecutionGuardPolicy(evidence_cap_bytes=64)
    baseline = policy.immutable_input_baseline(tmp_path)

    original_read_bytes = Path.read_bytes

    def read_without_source(path):
        if path == source:
            source.unlink(missing_ok=True)
            raise FileNotFoundError(path)
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", read_without_source)
    assert policy.evidence_bytes(tmp_path, immutable_inputs=baseline) == 0


def test_generated_evidence_scan_still_fails_closed_on_other_os_errors(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    evidence = tmp_path / "evidence.bin"
    evidence.write_bytes(b"evidence")
    original_stat = Path.stat

    def stat_with_permission_error(path, *args, **kwargs):
        if path == evidence:
            raise PermissionError(path)
        return original_stat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", stat_with_permission_error)
    policy = ExecutionGuardPolicy(evidence_cap_bytes=64)
    with pytest.raises(EvidenceCapError, match="cannot measure generated evidence"):
        policy.evidence_bytes(tmp_path)


def test_generated_evidence_scan_error_has_distinct_diagnostic(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    evidence = tmp_path / "evidence.bin"
    evidence.write_bytes(b"evidence")
    original_stat = Path.stat

    def stat_with_permission_error(path, *args, **kwargs):
        if path == evidence:
            raise PermissionError(path)
        return original_stat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", stat_with_permission_error)
    policy = ExecutionGuardPolicy(evidence_cap_bytes=64)
    with pytest.raises(EvidenceCapError) as failure:
        policy.evidence_measurement(tmp_path)
    assert failure.value.diagnostic == {
        "category": "scan_error",
        "error_type": "PermissionError",
    }


def test_generated_budget_accumulates_and_excludes_immutable_inputs(tmp_path) -> None:
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    (inputs / "source.bin").write_bytes(b"input-bytes")
    output = tmp_path / "generated.log"
    output.write_bytes(b"123")
    policy = ExecutionGuardPolicy(evidence_cap_bytes=5)
    baseline = policy.immutable_input_baseline(inputs)
    first = policy.assert_evidence_cap(tmp_path, immutable_inputs=baseline)
    assert first["observed_bytes"] == 3
    output.write_bytes(b"12345")
    second = policy.assert_evidence_cap(tmp_path, immutable_inputs=baseline)
    assert second["attempt_delta_bytes"] == 2
    assert second["run_observed_bytes"] == 5
    output.write_bytes(b"123456")
    with pytest.raises(EvidenceCapError, match="over its 5-byte cap"):
        policy.assert_evidence_cap(tmp_path, immutable_inputs=baseline)


def _attempt(root: Path, size: int) -> Path:
    root.mkdir()
    (root / "out.bin").write_bytes(b"x" * size)
    return root


def test_sequential_tasks_each_under_cap_all_succeed_though_they_sum_over_it(tmp_path) -> None:
    policy = ExecutionGuardPolicy(evidence_cap_bytes=5)
    for index in range(6):  # 18 bytes in total, far over the 5-byte cap
        attempt = _attempt(tmp_path / f"attempt-{index}", 3)
        assert policy.assert_evidence_cap(attempt)["observed_bytes"] == 3
        shutil.rmtree(attempt)  # the ephemeral attempt root is removed at cleanup
    policy.assert_budget_available()
    assert policy.evidence_budget.charged_bytes == 0


def test_one_overrun_fails_only_its_task_and_the_next_task_succeeds(tmp_path) -> None:
    policy = ExecutionGuardPolicy(evidence_cap_bytes=5)
    small = _attempt(tmp_path / "small", 3)
    policy.assert_evidence_cap(small)
    shutil.rmtree(small)
    large = _attempt(tmp_path / "large", 6)
    with pytest.raises(EvidenceCapError, match="over its 5-byte cap") as failure:
        policy.assert_evidence_cap(large)
    assert failure.value.diagnostic["category"] == "attempt_cap_exceeded"
    shutil.rmtree(large)  # the failed task's root is cleaned up
    policy.assert_budget_available()
    following = _attempt(tmp_path / "following", 4)
    assert policy.assert_evidence_cap(following)["observed_bytes"] == 4


def test_retained_roots_count_until_removed_then_admission_recovers(tmp_path) -> None:
    policy = ExecutionGuardPolicy(evidence_cap_bytes=5)
    first = _attempt(tmp_path / "retained-1", 3)
    policy.assert_evidence_cap(first)
    second = _attempt(tmp_path / "retained-2", 3)
    with pytest.raises(EvidenceCapError, match="held by 1 other attempt root") as failure:
        policy.assert_evidence_cap(second)
    assert failure.value.diagnostic["category"] == "live_budget_exceeded"
    assert failure.value.diagnostic["other_live_bytes"] == 3
    shutil.rmtree(first)  # an operator removes the retained root; no restart
    assert policy.assert_evidence_cap(second)["observed_bytes"] == 3


def test_admission_gate_refuses_only_while_bytes_on_disk_exceed_cap(tmp_path) -> None:
    policy = ExecutionGuardPolicy(evidence_cap_bytes=5)
    policy.assert_budget_available()
    held = _attempt(tmp_path / "held", 6)
    with pytest.raises(EvidenceCapError, match="over its 5-byte cap"):
        policy.assert_evidence_cap(held)
    with pytest.raises(EvidenceCapError, match="over the 5-byte cap"):
        policy.assert_budget_available()
    shutil.rmtree(held)
    policy.assert_budget_available()


def test_explicit_release_drops_the_charge_at_once(tmp_path) -> None:
    policy = ExecutionGuardPolicy(evidence_cap_bytes=10)
    root = _attempt(tmp_path / "attempt", 4)
    policy.assert_evidence_cap(root)
    assert policy.evidence_budget.charged_bytes == 4
    policy.evidence_budget.release(str(root.resolve()))
    assert policy.evidence_budget.charged_bytes == 0


def test_envelope_cap_admits_a_render_that_the_generic_cap_would_refuse(tmp_path) -> None:
    policy = ExecutionGuardPolicy(evidence_cap_bytes=5)
    generic = _attempt(tmp_path / "generic", 9)
    with pytest.raises(EvidenceCapError, match="over its 5-byte cap"):
        policy.assert_evidence_cap(generic)
    shutil.rmtree(generic)
    admitted = _attempt(tmp_path / "admitted", 9)
    assert policy.assert_evidence_cap(admitted, cap_bytes=12)["cap_bytes"] == 12


def test_attempt_cap_is_raised_only_to_an_admitted_envelope() -> None:
    assert generic_host._attempt_evidence_cap(5, None) == 5
    assert generic_host._attempt_evidence_cap(5, {"scratch_bytes": 7, "output_bytes": 1}) == 8
    assert generic_host._attempt_evidence_cap(10, {"scratch_bytes": 1, "output_bytes": 1}) == 10


def test_live_scratch_overrun_names_the_budget_and_the_next_step(tmp_path) -> None:
    root = tmp_path / "attempt"
    (root / "outputs").mkdir(parents=True)
    (root / "frames").mkdir()
    (root / "frames" / "frame-0001.png").write_bytes(b"x" * 5)
    with pytest.raises(generic_host.StorageEnvelopeError, match="scratch budget") as failure:
        generic_host._assert_live_storage_envelope(
            {"scratch_bytes": 1, "output_bytes": 10},
            root,
            root / "outputs",
        )
    assert "review render" in str(failure.value)
    assert "no host restart" not in str(failure.value).lower() or "restart" in str(failure.value)


def test_evidence_status_round_trip_reports_host_liveness(tmp_path) -> None:
    path = tmp_path / EVIDENCE_STATUS_NAME
    assert read_evidence_status(path) is None
    payload = {"pid": os.getpid(), "charged_bytes": 7, "cap_bytes": 9, "live_attempts": 1}
    write_evidence_status(path, payload)
    record = read_evidence_status(path)
    assert record["charged_bytes"] == 7
    assert record["cap_bytes"] == 9
    assert record["host_alive"] is True
    finished = subprocess.Popen([sys.executable, "-c", "pass"])
    finished.wait(timeout=10)
    write_evidence_status(path, {**payload, "pid": finished.pid})
    assert read_evidence_status(path)["host_alive"] is False


def test_modified_or_managed_input_is_not_exempt(tmp_path) -> None:
    inputs = tmp_path / "inputs"
    managed = tmp_path / "managed-objects"
    inputs.mkdir()
    managed.mkdir()
    input_file = inputs / "source.bin"
    managed_file = managed / "asset.bin"
    input_file.write_bytes(b"input-data")
    managed_file.write_bytes(b"managed-data")
    policy = ExecutionGuardPolicy(evidence_cap_bytes=64)
    baseline = policy.immutable_input_baseline(inputs)
    baseline.update(policy.immutable_input_baseline(managed))
    assert policy.assert_evidence_cap(tmp_path, immutable_inputs=baseline)["observed_bytes"] == 0
    input_file.write_bytes(b"changedata")
    assert policy.assert_evidence_cap(tmp_path, immutable_inputs=baseline)["observed_bytes"] == len(b"changedata")


def test_running_writer_is_detected_while_still_alive(tmp_path) -> None:
    output = tmp_path / "running-output.bin"
    process = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "from pathlib import Path; import sys, time; Path(sys.argv[1]).write_bytes(b'x' * 2048); time.sleep(30)",
            str(output),
        ]
    )
    policy = ExecutionGuardPolicy(evidence_cap_bytes=1024)
    try:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if output.exists():
                assert process.poll() is None
                with pytest.raises(EvidenceCapError, match="over its 1024-byte cap"):
                    policy.assert_evidence_cap(tmp_path)
                break
            time.sleep(0.01)
        else:
            pytest.fail("writer did not publish evidence before watchdog deadline")
    finally:
        process.terminate()
        process.wait(timeout=5)


def test_deadline_gate_rejects_expired_attempt() -> None:
    policy = ExecutionGuardPolicy(evidence_cap_bytes=1, deadline_seconds=1)
    with pytest.raises(ExecutionDeadlineError, match="deadline"):
        policy.assert_deadline(time.monotonic() - 0.001)


def test_warm_expectation_is_not_derived_from_a_listener_port() -> None:
    cold = ExecutionGuardPolicy(evidence_cap_bytes=1)
    assert cold.warm_expectation() == {
        "warm_reuse_expected": False,
        "listener_port": None,
        "port_independent": True,
    }
    warm = ExecutionGuardPolicy(
        evidence_cap_bytes=1,
        warm_reuse_expected=True,
    )
    assert warm.warm_expectation()["warm_reuse_expected"] is True
    with pytest.raises(WarmReuseExpectationError):
        warm.warm_expectation(listener_port=0)


def test_warm_expectation_requires_a_boolean() -> None:
    with pytest.raises(WarmReuseExpectationError):
        ExecutionGuardPolicy(
            evidence_cap_bytes=1,
            warm_reuse_expected=1,
        )


def test_generic_host_carries_the_guard_policy_without_a_listener_port(tmp_path) -> None:
    policy = ExecutionGuardPolicy(
        evidence_cap_bytes=1,
        warm_reuse_expected=False,
    )
    host = GenericPackHost(pack_roots=[tmp_path], execution_policy=policy)
    try:
        assert host.execution_policy is policy
        assert host.execution_policy.warm_expectation()["listener_port"] is None
    finally:
        host.shutdown()


def test_cleanup_helper_does_not_claim_absence_after_recursive_delete_error(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "attempt"
    root.mkdir()
    monkeypatch.setattr(
        generic_host.shutil,
        "rmtree",
        lambda _path: (_ for _ in ()).throw(FileNotFoundError("descendant vanished")),
    )
    with pytest.raises(generic_host.HostError, match="cleanup was not verified"):
        generic_host._cleanup_ephemeral_attempt(root)


def test_cleanup_helper_fails_closed_on_root_observation_error(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "attempt"
    root.mkdir()
    monkeypatch.setattr(
        generic_host,
        "_strict_root_exists",
        lambda _path: (_ for _ in ()).throw(generic_host.HostError("injected observation failure")),
    )
    with pytest.raises(generic_host.HostError, match="injected observation failure"):
        generic_host._cleanup_ephemeral_attempt(root)


def test_cleanup_failure_latches_and_blocks_new_admissions(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    host = GenericPackHost(pack_roots=[tmp_path])
    root = tmp_path / "attempt"
    root.mkdir()
    monkeypatch.setattr(
        generic_host,
        "_cleanup_ephemeral_attempt",
        lambda _path: (_ for _ in ()).throw(generic_host.HostError("injected cleanup failure")),
    )
    with pytest.raises(generic_host.HostError, match="injected cleanup failure"):
        host._cleanup_ephemeral_attempt_or_latch(root)
    assert host.last_cleanup_receipt == {
        "path": str(root),
        "intended_disposition": "deleted",
        "status": "uncertain",
        "errors": ["injected cleanup failure"],
    }
    with pytest.raises(generic_host.HostError, match="cleanup uncertainty"):
        host.claim_once()
    with pytest.raises(generic_host.HostError, match="cleanup uncertainty"):
        host.run_task({}, lease_token="", attempt_id="", fence=1)
