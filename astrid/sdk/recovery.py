"""Local, opt-in exact admission receipt; Runtime remains task authority."""
from __future__ import annotations

import fcntl
import hashlib
import os
import tempfile
from pathlib import Path
from typing import Any, Mapping

from astrid.core.receipts.canonical import canonical_json, parse_json
from .exceptions import CapabilityInvocationError, CapabilityValidationError
from .results import _json_safe
from .workspace_client import WorkspaceClientError


def _digest(value: Any) -> str:
    return hashlib.sha256(canonical_json(_json_safe(value)).encode("utf-8")).hexdigest()


def _transport(client: Any) -> Any:
    remote = getattr(client, "_remote", client)
    return getattr(remote, "_transport", remote)


def current_realm(client: Any) -> str:
    transport = _transport(client)
    handshake_reader = getattr(transport, "handshake", None)
    if not callable(handshake_reader):
        raise CapabilityInvocationError("recovery requires an authenticated Runtime client")
    try:
        handshake = handshake_reader("astrid-recovery", "1", [])
    except WorkspaceClientError as exc:
        raise CapabilityInvocationError("Runtime recovery authorization or connection failed",
            details={"code": exc.code}) from exc
    realm = handshake.get("realm_id") if isinstance(handshake, Mapping) else None
    if not isinstance(realm, str) or not realm:
        raise CapabilityInvocationError("recovery requires authenticated Runtime realm identity")
    return realm


class AdmissionReceipt:
    """One writer owns a receipt throughout admission and observation.

    The lock is local and nonblocking; it is never removed (unlinking would
    allow a second inode/owner). A receipt is frozen arguments and locators,
    never a task status or proof of success.
    """

    def __init__(self, path: str | Path, *, request: Mapping[str, Any], client: Any, resume: bool):
        self.path = Path(path).expanduser().absolute()
        self.request_digest = _digest(request)
        self.client = client
        self.resume = resume
        self.data: dict[str, Any] | None = None
        self._lock: int | None = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._lock = os.open(str(self.path) + ".lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
            try:
                fcntl.flock(self._lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise CapabilityInvocationError("recovery receipt is already in use") from exc
            if self.path.is_symlink():
                raise CapabilityValidationError("recovery receipt must not be a symlink")
            if self.resume:
                try:
                    data = parse_json(self.path.read_bytes(), max_bytes=4 * 1024 * 1024)
                    if not isinstance(data, dict):
                        raise ValueError("receipt must be an object")
                    saved_digest = data.pop("integrity_digest")
                    if saved_digest != _digest(data):
                        raise ValueError("integrity mismatch")
                    if set(data) != {"schema_version", "realm_id", "request_digest", "admission", "metadata", "locator"} or data["schema_version"] != 1:
                        raise ValueError("unsupported receipt schema")
                    admission = data["admission"]
                    if not isinstance(admission, dict) or not isinstance(admission.get("idempotency_key"), str) or not admission["idempotency_key"]:
                        raise ValueError("missing frozen admission/key")
                    metadata = data["metadata"]
                    if (not isinstance(metadata, dict)
                            or metadata.get("capability_id") != admission.get("capability_id")
                            or metadata.get("capability_type") != "executor"
                            or not isinstance(metadata.get("native_kind"), str)):
                        raise ValueError("invalid invocation metadata")
                    locator = data["locator"]
                    if locator is not None and (not isinstance(locator, dict) or set(locator) != {"task_id", "run_id"} or not all(isinstance(v, str) and v for v in locator.values())):
                        raise ValueError("invalid task/run locator")
                except (OSError, ValueError, KeyError, TypeError) as exc:
                    raise CapabilityValidationError("recovery receipt is missing or corrupt") from exc
                if data["request_digest"] != self.request_digest:
                    raise CapabilityValidationError("recovery request/project identity mismatch")
                if data["realm_id"] != current_realm(self.client):
                    raise CapabilityValidationError("recovery Runtime realm identity mismatch")
                self.data = data
            elif self.path.exists():
                raise CapabilityValidationError("recovery receipt already exists; use resume=True or a new path")
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def __exit__(self, *_args):
        if self._lock is not None:
            os.close(self._lock)
            self._lock = None

    def _persist(self, data: dict[str, Any]):
        encoded = canonical_json({**data, "integrity_digest": _digest(data)}).encode("utf-8")
        descriptor, name = tempfile.mkstemp(prefix=f".{self.path.name}.", dir=self.path.parent)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                os.fchmod(handle.fileno(), 0o600)
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(name, self.path)
            parent = os.open(self.path.parent, os.O_RDONLY)
            try:
                os.fsync(parent)
            finally:
                os.close(parent)
        finally:
            Path(name).unlink(missing_ok=True)
        self.data = data

    def freeze(self, admission: Mapping[str, Any], *, metadata: Mapping[str, Any]):
        if self.data is not None:
            raise CapabilityValidationError("recovery admission is already frozen")
        # Serialize the same JSON values sent by generated admission; no
        # redaction or alternate execution-request representation is allowed.
        arguments = parse_json(canonical_json(_json_safe(admission)), max_bytes=4 * 1024 * 1024)
        self._persist({"schema_version": 1, "realm_id": current_realm(self.client),
                       "request_digest": self.request_digest, "admission": arguments,
                       "metadata": _json_safe(dict(metadata)), "locator": None})

    def locate(self, task: Mapping[str, Any]):
        locator = {name: task.get(name) for name in ("task_id", "run_id")}
        if not all(isinstance(value, str) and value for value in locator.values()):
            raise CapabilityInvocationError("Runtime admission returned incomplete task/run identity")
        if self.data is None:
            raise CapabilityInvocationError("cannot locate an unfrozen admission")
        admission = self.data["admission"]
        for field in ("project_id", "capability_id", "capability_digest", "idempotency_key"):
            if field in task and task[field] != admission.get(field):
                raise CapabilityValidationError(f"recovery {field} identity mismatch")
        if self.data["locator"] is not None and self.data["locator"] != locator:
            raise CapabilityValidationError("recovery task/run identity mismatch")
        self._persist({**self.data, "locator": locator})

    def recover_task(self):
        if self.data is None:
            raise CapabilityInvocationError("cannot recover an unfrozen admission")
        tasks = self.client.tasks
        locator = self.data["locator"]
        result = tasks.show(locator["task_id"]) if locator else tasks.dispatch_admission(self.data["admission"])
        if result.ok:
            task = result.data
            if not isinstance(task, Mapping):
                raise CapabilityInvocationError("Runtime recovery returned no task resource")
            self.locate(task)
        return result
