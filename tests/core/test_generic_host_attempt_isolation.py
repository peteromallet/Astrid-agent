from pathlib import Path
import threading

import pytest

from astrid.core.execution.generic_host import (
    GenericPackHost,
    HostError,
    _ManagedTaskAdapter,
)


def test_managed_task_cancel_acknowledges_only_empty_process_census() -> None:
    cancel_signal = threading.Event()
    busy = _ManagedTaskAdapter(cancel_signal, process_census=lambda: True)
    assert busy.cancel(reason="runtime_cancelled")["ok"] is False
    assert cancel_signal.is_set()

    idle = _ManagedTaskAdapter(threading.Event(), process_census=lambda: False)
    assert idle.cancel(reason="runtime_cancelled") == {
        "ok": True,
        "cancelled": True,
        "reason": "runtime_cancelled",
    }


def test_attempt_base_allocates_one_child_per_attempt(tmp_path: Path) -> None:
    host = GenericPackHost(
        pack_roots=[tmp_path / "pack"],
        attempt_base=tmp_path / "attempts",
    )

    first = host._allocate_attempt_root("task-1", "attempt-1")
    second = host._allocate_attempt_root("task-1", "attempt-2")

    assert first != second
    assert first.parent == second.parent == tmp_path / "attempts"
    assert first.is_dir() and second.is_dir()


def test_attempt_root_and_base_cannot_be_combined(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="mutually exclusive"):
        GenericPackHost(
            pack_roots=[tmp_path / "pack"],
            attempt_root=tmp_path / "attempt",
            attempt_base=tmp_path / "attempts",
        )


def test_attempt_base_rejects_reused_child(tmp_path: Path) -> None:
    host = GenericPackHost(
        pack_roots=[tmp_path / "pack"],
        attempt_base=tmp_path / "attempts",
    )
    host._allocate_attempt_root("task-1", "attempt-1")

    with pytest.raises(HostError, match="already exists"):
        host._allocate_attempt_root("task-1", "attempt-1")
