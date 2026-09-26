from __future__ import annotations

from astrid.sdk.remote import RemoteRuns


class _Runtime:
    def __init__(self) -> None:
        self.calls: list[tuple[object, ...]] = []

    def get_run(self, run_id):
        self.calls.append(("get_run", run_id))
        return {"run_id": run_id, "state": "failed"}

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
    assert runtime.calls == [("get_run", "run-1")]

    runtime.calls.clear()
    with_evidence = runs.show("run-1", evidence=True)
    assert with_evidence.ok
    assert [item["event_id"] for item in with_evidence.data["evidence"]] == [
        "event-1", "event-2"
    ]
    assert runtime.calls == [
        ("get_run", "run-1"),
        ("list_run_events", "run-1", None, 200),
    ]
