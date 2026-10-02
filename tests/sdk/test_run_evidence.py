from __future__ import annotations

import pytest

from astrid.sdk.remote import RemoteRuns
from astrid.sdk.workspace_client import WorkspaceClientError


class _Runtime:
    def __init__(self) -> None:
        self.calls: list[tuple[object, ...]] = []

    def get_run(self, run_id):
        self.calls.append(("get_run", run_id))
        return {
            "id": run_id,
            "project_id": "project-1",
            "status": "completed",
            "task_ids": ["task-1"],
        }

    def get_task(self, task_id):
        self.calls.append(("get_task", task_id))
        return {
            "task_id": task_id,
            "run_id": "run-1",
            "project_id": "project-1",
            "state": "succeeded",
        }

    def list_run_events(self, run_id, *, cursor=None, limit=50):
        self.calls.append(("list_run_events", run_id, cursor, limit))
        return [[
            {"event_id": "event-1", "sequence": 1},
            {"event_id": "event-2", "sequence": 2},
        ], None]


def test_run_show_fetches_ordered_evidence_only_when_requested() -> None:
    runtime = _Runtime()
    runs = RemoteRuns(runtime)

    plain = runs.show("run-1")
    assert plain.ok and "evidence" not in plain.data
    assert plain.data["status"] == "completed"
    assert plain.data["progress"] == {
        "total": 1,
        "succeeded": 1,
        "failed": 0,
        "cancelled": 0,
        "ordered": [{"task_id": "task-1", "status": "succeeded"}],
    }
    assert runtime.calls == [("get_run", "run-1"), ("get_task", "task-1")]

    runtime.calls.clear()
    with_evidence = runs.show("run-1", evidence=True)
    assert with_evidence.ok
    assert [item["event_id"] for item in with_evidence.data["evidence"]] == [
        "event-1", "event-2"
    ]
    assert runtime.calls == [
        ("get_run", "run-1"),
        ("get_task", "task-1"),
        ("list_run_events", "run-1", None, 200),
    ]


class _InvalidRuntime(_Runtime):
    def __init__(self, mutation: str) -> None:
        super().__init__()
        self.mutation = mutation

    def get_run(self, run_id):
        value = super().get_run(run_id)
        if self.mutation == "run-id": value["id"] = "other-run"
        elif self.mutation == "task-ids-shape": value["task_ids"] = {"task-1": True}
        elif self.mutation == "duplicate-task": value["task_ids"] = ["task-1", "task-1"]
        return value

    def get_task(self, task_id):
        value = super().get_task(task_id)
        if self.mutation == "task-id": value["task_id"] = "other-task"
        elif self.mutation == "task-run": value["run_id"] = "other-run"
        elif self.mutation == "task-project": value["project_id"] = "other-project"
        elif self.mutation == "task-state": value["state"] = "invented"
        return value


@pytest.mark.parametrize(
    "mutation",
    [
        "run-id", "task-ids-shape", "duplicate-task", "task-id",
        "task-run", "task-project", "task-state",
    ],
)
def test_run_show_rejects_mismatched_child_projection(mutation: str) -> None:
    result = RemoteRuns(_InvalidRuntime(mutation)).show("run-1", evidence=True)
    assert result.ok is False
    assert result.error is not None
    assert result.error.code == "transport_error"


def test_run_show_propagates_child_read_failure() -> None:
    class FailedChildRuntime(_Runtime):
        def get_task(self, task_id):
            raise WorkspaceClientError(503, "unavailable", "child unavailable", {})

    result = RemoteRuns(FailedChildRuntime()).show("run-1", evidence=True)
    assert result.ok is False
    assert result.error is not None
    assert result.error.code == "unavailable"
    assert "child unavailable" in result.error.message


class _ProgressRuntime:
    def __init__(self, states: list[str]) -> None:
        self.states = states
        self.calls: list[tuple[object, ...]] = []

    def get_run(self, run_id):
        self.calls.append(("get_run", run_id))
        return {
            "id": run_id, "project_id": "project-1", "status": "completed",
            "task_ids": [f"task-{index}" for index in range(len(self.states))],
        }

    def get_task(self, task_id):
        self.calls.append(("get_task", task_id))
        index = int(task_id.removeprefix("task-"))
        return {
            "task_id": task_id, "run_id": "run-1", "project_id": "project-1",
            "state": self.states[index],
        }


def test_run_show_derives_deterministic_multi_child_counts_and_order() -> None:
    runtime = _ProgressRuntime(["succeeded", "failed", "cancelled", "running"])
    result = RemoteRuns(runtime).show("run-1")
    assert result.ok is True
    assert result.data["progress"] == {
        "total": 4, "succeeded": 1, "failed": 1, "cancelled": 1,
        "ordered": [
            {"task_id": "task-0", "status": "succeeded"},
            {"task_id": "task-1", "status": "failed"},
            {"task_id": "task-2", "status": "cancelled"},
            {"task_id": "task-3", "status": "running"},
        ],
    }


def test_run_show_derives_empty_progress_for_zero_child_run() -> None:
    runtime = _ProgressRuntime([])
    result = RemoteRuns(runtime).show("run-1")
    assert result.ok is True
    assert result.data["progress"] == {
        "total": 0, "succeeded": 0, "failed": 0, "cancelled": 0,
        "ordered": [],
    }
    assert runtime.calls == [("get_run", "run-1")]


def test_run_show_failed_run_preserves_evidence_and_current_failed_child() -> None:
    class FailedRuntime(_ProgressRuntime):
        def get_run(self, run_id):
            value = super().get_run(run_id)
            value["status"] = "failed"
            return value

        def list_run_events(self, run_id, *, cursor=None, limit=50):
            self.calls.append(("list_run_events", run_id, cursor, limit))
            return [[{"event_id": "failure-1", "event_type": "task.failed"}], None]

    result = RemoteRuns(FailedRuntime(["failed"])).show("run-1", evidence=True)
    assert result.ok is True
    assert result.data["status"] == "failed"
    assert result.data["progress"] == {
        "total": 1, "succeeded": 0, "failed": 1, "cancelled": 0,
        "ordered": [{"task_id": "task-0", "status": "failed"}],
    }
    assert result.data["evidence"] == [
        {"event_id": "failure-1", "event_type": "task.failed"}
    ]
