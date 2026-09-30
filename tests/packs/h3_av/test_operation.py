from __future__ import annotations

import pytest

from astrid.packs.h3_av.orchestrators.transform.run import (
    _invoke_canonical_run,
    _invoke_stage,
)
from astrid.packs.h3_av.src.operation import OperationJournal, OperationJournalError
from astrid.sdk.results import InvocationResult


def test_operation_journal_is_durable_and_request_bound(tmp_path) -> None:
    path = tmp_path / "operation-state.json"
    journal = OperationJournal(path, request_digest="request-a")
    journal.record("run", "completed", task_id="task-a", attempt_id="attempt-a")
    resumed = OperationJournal(path, request_digest="request-a")
    assert resumed.latest("run")["task_id"] == "task-a"
    with pytest.raises(OperationJournalError, match="another request"):
        OperationJournal(path, request_digest="request-b")


def test_operation_journal_rejects_malformed_events(tmp_path) -> None:
    path = tmp_path / "operation-state.json"
    path.write_text('{"schema_version": 1, "request_digest": "x", "events": {}}', encoding="utf-8")
    with pytest.raises(OperationJournalError, match="events"):
        OperationJournal(path, request_digest="x")


def test_operation_journal_freezes_admission_digest_and_phase(tmp_path) -> None:
    path = tmp_path / "operation-state.json"
    journal = OperationJournal(path, request_digest="request-a")
    journal.freeze_admission("sha256:admission-a")
    journal.set_admission_phase("admission_started")

    resumed = OperationJournal(path, request_digest="request-a")
    assert resumed.admission_digest == "sha256:admission-a"
    assert resumed.admission_phase == "admission_started"
    with pytest.raises(OperationJournalError, match="frozen operation"):
        resumed.freeze_admission("sha256:admission-b")


def test_uncertain_canonical_admission_cannot_be_replayed(tmp_path) -> None:
    journal_path = tmp_path / "operation-state.json"
    result_path = tmp_path / "run-result.json"
    calls = []

    class Client:
        def invoke_result(self, *args, **kwargs):
            calls.append((args, kwargs))
            raise TimeoutError("response was lost")

    kwargs = {
        "inputs": {"workflow": {"digest": "sha256:workflow"}},
        "execution_request": {"target": {"pod_id": "pod-1"}},
        "out": tmp_path / "run",
        "project": "project-1",
        "saved_result": result_path,
    }
    with pytest.raises(TimeoutError, match="response was lost"):
        _invoke_canonical_run(
            Client(), journal=OperationJournal(journal_path, request_digest="request-a"),
            resume=False, **kwargs,
        )

    with pytest.raises(RuntimeError, match="may have occurred"):
        _invoke_canonical_run(
            Client(), journal=OperationJournal(journal_path, request_digest="request-a"),
            resume=True, **kwargs,
        )
    assert len(calls) == 1


def test_settled_canonical_run_is_reused_with_original_identity(tmp_path) -> None:
    journal_path = tmp_path / "operation-state.json"
    result_path = tmp_path / "run-result.json"
    journal = OperationJournal(journal_path, request_digest="request-a")
    calls = []

    class Client:
        def invoke_result(self, capability_id, **kwargs):
            calls.append(capability_id)
            return InvocationResult(
                capability_id=capability_id,
                capability_type="executor",
                native_kind="executor",
                ok=True,
                kernel_task_id="canonical-task",
                kernel_run_id="canonical-run",
                kernel_attempt_id="canonical-attempt",
            )

    kwargs = {
        "inputs": {"workflow": {"digest": "sha256:workflow"}},
        "execution_request": None,
        "out": tmp_path / "run",
        "project": "project-1",
        "saved_result": result_path,
    }
    first = _invoke_canonical_run(Client(), journal=journal, resume=False, **kwargs)
    resumed = _invoke_canonical_run(
        Client(), journal=OperationJournal(journal_path, request_digest="request-a"),
        resume=True, **kwargs,
    )
    assert first.kernel_task_id == resumed.kernel_task_id == "canonical-task"
    assert resumed.kernel_run_id == "canonical-run"
    assert resumed.kernel_attempt_id == "canonical-attempt"
    assert calls == ["vibecomfy.run"]


def test_settled_stage_result_reuse_is_bound_to_inputs(tmp_path) -> None:
    journal_path = tmp_path / "operation-state.json"
    result_path = tmp_path / "prepare-result.json"
    calls = []

    class Client:
        def invoke_result(self, capability_id, **kwargs):
            calls.append(capability_id)
            return InvocationResult(
                capability_id=capability_id,
                capability_type="executor",
                native_kind="executor",
                ok=True,
            )

    journal = OperationJournal(journal_path, request_digest="request-a")
    initial = _invoke_stage(
        Client(), "h3_av.prepare", inputs={"request": {"digest": "a"}},
        out=tmp_path / "prepare", project="project-1", saved_result=result_path,
        resume=False, journal=journal, phase="prepare",
    )
    reused = _invoke_stage(
        Client(), "h3_av.prepare", inputs={"request": {"digest": "a"}},
        out=tmp_path / "prepare", project="project-1", saved_result=result_path,
        resume=True, journal=OperationJournal(journal_path, request_digest="request-a"),
        phase="prepare",
    )
    assert initial.capability_id == reused.capability_id == "h3_av.prepare"
    assert calls == ["h3_av.prepare"]

    with pytest.raises(RuntimeError, match="different stage inputs"):
        _invoke_stage(
            Client(), "h3_av.prepare", inputs={"request": {"digest": "changed"}},
            out=tmp_path / "prepare", project="project-1", saved_result=result_path,
            resume=True, journal=OperationJournal(journal_path, request_digest="request-a"),
            phase="prepare",
        )
    assert calls == ["h3_av.prepare"]
