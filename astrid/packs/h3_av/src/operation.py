"""Durable phase journal for one H3 operation.

This is an operation receipt, not a second task ledger. Runtime remains the
authority for task state; the journal lets delivery resume after a process
interruption without sampling an already-settled child again.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


class OperationJournalError(ValueError):
    """The journal is malformed or belongs to another request."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class OperationJournal:
    def __init__(self, path: str | Path, *, request_digest: str) -> None:
        self.path = Path(path)
        self.request_digest = request_digest
        self.events: list[dict[str, Any]] = []
        self.admission_digest: str | None = None
        self.admission_phase: str | None = None
        if self.path.is_file():
            try:
                value = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise OperationJournalError(f"cannot read operation journal: {exc}") from exc
            if not isinstance(value, Mapping) or value.get("schema_version") != 1:
                raise OperationJournalError("operation journal schema is unsupported")
            if value.get("request_digest") != request_digest:
                raise OperationJournalError("operation journal belongs to another request")
            raw_events = value.get("events", [])
            if not isinstance(raw_events, list) or not all(isinstance(item, Mapping) for item in raw_events):
                raise OperationJournalError("operation journal events are malformed")
            self.events = [dict(item) for item in raw_events]
            admission_digest = value.get("admission_digest")
            admission_phase = value.get("admission_phase")
            if admission_digest is not None and not isinstance(admission_digest, str):
                raise OperationJournalError("operation journal admission digest is malformed")
            if admission_phase is not None and not isinstance(admission_phase, str):
                raise OperationJournalError("operation journal admission phase is malformed")
            self.admission_digest = admission_digest
            self.admission_phase = admission_phase

    @property
    def has_history(self) -> bool:
        return bool(self.events or self.admission_digest or self.admission_phase)

    def freeze_admission(self, digest: str) -> None:
        if not digest:
            raise OperationJournalError("admission digest is required")
        if self.admission_digest is not None and self.admission_digest != digest:
            raise OperationJournalError("admission payload disagrees with the frozen operation")
        if self.admission_digest is None:
            self.admission_digest = digest
            self.admission_phase = "frozen"
            self._persist()

    def set_admission_phase(self, phase: str) -> None:
        if not phase:
            raise OperationJournalError("admission phase is required")
        if self.admission_digest is None:
            raise OperationJournalError("cannot advance an admission before its digest is frozen")
        self.admission_phase = phase
        self._persist()

    def record(self, phase: str, status: str, **evidence: Any) -> None:
        if not phase or not status:
            raise OperationJournalError("phase and status are required")
        self.events.append({"phase": phase, "status": status, "at": _now(), **evidence})
        self._persist()

    def _persist(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": 1,
            "request_digest": self.request_digest,
            "admission_digest": self.admission_digest,
            "admission_phase": self.admission_phase,
            "events": self.events,
        }
        temporary = self.path.with_name(f".{self.path.name}.tmp")
        temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        temporary.replace(self.path)

    def latest(self, phase: str) -> dict[str, Any] | None:
        for event in reversed(self.events):
            if event.get("phase") == phase:
                return dict(event)
        return None


__all__ = ["OperationJournal", "OperationJournalError"]
