"""`tasks follow` shows the progress a running task reports (SM-17)."""

from __future__ import annotations

import io
from datetime import datetime, timezone
from types import SimpleNamespace

from astrid.core.cli.task_progress import follow_task, task_observation
from astrid.sdk.contracts import DomainResult

_RUNNING = {
    "task_id": "t1",
    "run_id": "r1",
    "state": "running",
    "version": 2,
    "attempt_id": "a1",
    "created_at": "2026-10-09T14:00:00Z",
    "updated_at": "2026-10-09T14:05:00Z",
}


def test_observation_reads_runtime_current_and_total() -> None:
    observation = task_observation(
        {**_RUNNING, "progress": {"phase": "rendered 2175/4977 frames · ETA 6m 3s", "percent": 42, "current": 2175, "total": 4977}},
        kind="observed",
        followed_for_seconds=0,
        now=datetime(2026, 10, 9, 14, 5, tzinfo=timezone.utc),
    )
    assert observation["phase"] == "rendered 2175/4977 frames · ETA 6m 3s"
    assert observation["progress_percent"] == 42
    assert observation["completed_units"] == 2175
    assert observation["total_units"] == 4977


def test_follow_fills_progress_the_generated_task_model_drops() -> None:
    states = iter([dict(_RUNNING), {**_RUNNING, "state": "succeeded", "version": 3}])
    progress = {"phase": "rendered 2175/4977 frames · ETA 6m 3s", "percent": 42, "current": 2175, "total": 4977}
    transport = SimpleNamespace(task_progress=lambda task_id: progress if task_id == "t1" else None)
    tasks = SimpleNamespace(show=lambda task_id: DomainResult.success(next(states)), _client=transport)
    stream = io.StringIO()
    result = follow_task(
        SimpleNamespace(tasks=tasks),
        "t1",
        project="p",
        poll_seconds=0,
        timeout_seconds=60,
        stream=stream,
        sleep=lambda _seconds: None,
    )
    assert result.ok
    assert "phase=rendered 2175/4977 frames · ETA 6m 3s" in stream.getvalue()
    assert "progress=42%" in stream.getvalue()


def test_follow_without_a_progress_reader_is_unchanged() -> None:
    states = iter([dict(_RUNNING), {**_RUNNING, "state": "succeeded", "version": 3}])
    tasks = SimpleNamespace(show=lambda task_id: DomainResult.success(next(states)))
    stream = io.StringIO()
    result = follow_task(
        SimpleNamespace(tasks=tasks),
        "t1",
        project="p",
        poll_seconds=0,
        timeout_seconds=60,
        stream=stream,
        sleep=lambda _seconds: None,
    )
    assert result.ok
    assert "phase=running" in stream.getvalue()
