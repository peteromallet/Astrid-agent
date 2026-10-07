"""Private, attempt-scoped SDK channel. Runtime authority stays in the host.

F05 installs an inherited socket before calling pack code. There is deliberately
no environment discovery, credential access, generic RPC, or admission fallback.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import socket
import threading
import time
from collections.abc import Mapping
from pathlib import PurePosixPath
from typing import Any

from .exceptions import CapabilityInvocationError, CapabilityValidationError

_MAX_FRAME = 1024 * 1024
_bridge: ChildBridge | None = None


class _BridgeRejected(CapabilityInvocationError):
    def __init__(self, error: dict[str, Any]):
        super().__init__("child bridge rejected request", details=error)
        self.error = error


def _strict_json(value: Any) -> bytes:
    def check(item: Any, depth: int = 0) -> None:
        if depth > 32:
            raise ValueError("JSON nesting exceeds 32")
        if item is None or type(item) in (str, bool, int):
            return
        if type(item) is float and math.isfinite(item):
            return
        if type(item) is list:
            for nested in item:
                check(nested, depth + 1)
            return
        if type(item) is dict and all(type(key) is str for key in item):
            for nested in item.values():
                check(nested, depth + 1)
            return
        raise ValueError("requires finite strict JSON")
    try:
        check(value)
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":"),
                             ensure_ascii=False, allow_nan=False).encode("utf-8")
        if len(encoded) > _MAX_FRAME - 1024:
            raise ValueError("JSON exceeds bridge frame bound")
        return encoded
    except (ValueError, TypeError, RecursionError, UnicodeError) as exc:
        raise CapabilityValidationError(f"child bridge request {exc}") from exc


def _validate_child_policy(value: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise CapabilityValidationError("child_delegation must be an object")
    copied = dict(value)
    required = {"capabilities", "targets", "input_object_ids"}
    if not required <= copied.keys() or copied.keys() - required - {"limits", "stages", "final_publication", "recoverable_outputs"}:
        raise CapabilityValidationError("child_delegation requires a finite policy shape")
    for name, maximum, nonempty in (("capabilities", 32, True), ("targets", 32, True),
                                    ("input_object_ids", 256, False), ("stages", 32, True)):
        if name not in copied:
            continue
        items = copied[name]
        if type(items) is not list or len(items) > maximum or (nonempty and not items):
            raise CapabilityValidationError(f"child_delegation.{name} must be a bounded list")
    limits = copied.get("limits", {})
    if type(limits) is not dict or any(type(n) is not int or n <= 0 for n in limits.values()):
        raise CapabilityValidationError("child_delegation.limits must contain positive finite integers")
    if "recoverable_outputs" in copied:
        grants = copied["recoverable_outputs"]
        if type(grants) is not list or len(grants) != 1:
            raise CapabilityValidationError("recoverable_outputs requires one exact pinned Human Review grant")
        grant = grants[0]
        if (type(grant) is not dict
                or set(grant) != {"capability_id", "capability_digest", "output_ports"}
                or grant.get("capability_id") != "editorial.human_review"
                or grant.get("output_ports") != ["state_result"]
                or type(grant.get("capability_digest")) is not str
                or re.fullmatch(r"sha256:[0-9a-f]{64}", grant["capability_digest"]) is None
                or {key: grant[key] for key in ("capability_id", "capability_digest")} not in copied["capabilities"]):
            raise CapabilityValidationError("recoverable_outputs must pin editorial.human_review/state_result in capabilities")
    # Runtime resolves defaults, permitted names, numeric ceilings and policy authority.
    return json.loads(_strict_json(copied))


class ChildBridge:
    """Four fixed operations over an inherited private stream socket.

    A complete request/reply holds the lock for at most io_timeout_seconds.
    Waiting for task progress happens outside this lock. Any channel failure
    revokes this client permanently; outstanding calls cannot fall back.
    """

    def __init__(self, channel: socket.socket, *, io_timeout_seconds: float = 5.0):
        if not math.isfinite(io_timeout_seconds) or not 0 < io_timeout_seconds <= 5:
            raise ValueError("bridge I/O timeout must be in (0, 5]")
        self._channel = channel
        self._timeout = float(io_timeout_seconds)
        self._lock = threading.Lock()
        self._sequence = 0
        self._closed = False

    def close(self) -> None:
        self._closed = True
        try:
            self._channel.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self._channel.close()

    def _exchange(self, op: str, body: dict[str, Any], *, timeout_seconds: float | None = None) -> Any:
        budget = min(self._timeout, timeout_seconds) if timeout_seconds is not None else self._timeout
        deadline = time.monotonic() + budget
        if budget <= 0 or not self._lock.acquire(timeout=budget):
            raise CapabilityInvocationError("child bridge request timed out")
        try:
            if self._closed:
                raise CapabilityInvocationError("child bridge is closed")
            self._sequence += 1
            request = {"v": 1, "request_id": self._sequence, "op": op, **body}
            encoded = _strict_json(request) + b"\n"
            self._channel.settimeout(max(0.000001, deadline - time.monotonic()))
            self._channel.sendall(encoded)
            frame = bytearray()
            while not frame.endswith(b"\n"):
                self._channel.settimeout(max(0.000001, deadline - time.monotonic()))
                chunk = self._channel.recv(min(65536, _MAX_FRAME + 1 - len(frame)))
                if not chunk or len(frame) >= _MAX_FRAME or time.monotonic() >= deadline:
                    raise ValueError("closed, oversized or timed out reply")
                frame.extend(chunk)
                if b"\n" in frame[:-1] or len(frame) > _MAX_FRAME:
                    raise ValueError("invalid frame boundary")
            reply = json.loads(frame)
            try:
                _strict_json(reply)
            except CapabilityValidationError as exc:
                raise ValueError("invalid reply JSON") from exc
            if (type(reply) is not dict or reply.get("v") != 1
                    or reply.get("request_id") != self._sequence or type(reply.get("ok")) is not bool
                    or set(reply) != {"v", "request_id", "ok", "data" if reply["ok"] else "error"}):
                raise ValueError("invalid reply envelope")
            if not reply["ok"]:
                # Host errors carry only public SDK data, never authority/credentials.
                error = reply["error"]
                if type(error) is not dict or set(error) != {"code", "message", "details"}:
                    raise ValueError("invalid error envelope")
                if type(error["code"]) is not str or type(error["message"]) is not str or type(error["details"]) is not dict:
                    raise ValueError("invalid error fields")
                raise _BridgeRejected(error)
            data = reply["data"]
            if op == "publish_snapshot":
                if (type(data) is not dict
                        or set(data) != {"association_id", "receipt_id", "revision", "output_port",
                                         "digest", "size", "durability"}
                        or any(type(data[name]) is not str or not data[name]
                               for name in ("association_id", "receipt_id"))
                        or type(data["revision"]) is not int or data["revision"] != body["revision"]
                        or type(data["output_port"]) is not str or data["output_port"] != body["output_port"]
                        or type(data["digest"]) is not str
                        or re.fullmatch(r"sha256:[0-9a-f]{64}", data["digest"]) is None
                        or type(data["size"]) is not int or data["size"] < 0
                        or data["durability"] != "durable"):
                    raise ValueError("invalid durable snapshot receipt")
            return data
        except CapabilityValidationError:
            raise
        except CapabilityInvocationError:
            raise
        except (OSError, ValueError, TypeError) as exc:
            self.close()
            raise CapabilityInvocationError("child bridge is unavailable or returned an invalid reply") from exc
        finally:
            self._lock.release()

    def submit(self, request: dict[str, Any]) -> Any:
        return self._exchange("submit", request)

    def status(self, child_key: str, task_id: str, *, timeout_seconds: float | None = None) -> Any:
        return self._exchange("status", {"child_key": child_key, "task_id": task_id}, timeout_seconds=timeout_seconds)

    def outputs(self, child_key: str, task_id: str, *, timeout_seconds: float | None = None) -> Any:
        return self._exchange("outputs", {"child_key": child_key, "task_id": task_id}, timeout_seconds=timeout_seconds)

    def publish_snapshot(self, *, filename: str, output_port: str, revision: int) -> dict[str, Any]:
        """Request host-owned durable publication of an attempt-relative file."""
        if (type(filename) is not str or not filename or PurePosixPath(filename).is_absolute()
                or PurePosixPath(filename) == PurePosixPath(".") or ".." in filename.split("/")
                or "\\" in filename or "\x00" in filename):
            raise CapabilityValidationError("snapshot filename must be contained and attempt-relative")
        if type(output_port) is not str or not output_port:
            raise CapabilityValidationError("snapshot output_port must be a nonempty string")
        if type(revision) is not int or revision <= 0:
            raise CapabilityValidationError("snapshot revision must be a positive integer")
        return self._exchange("publish_snapshot", {
            "filename": filename, "output_port": output_port, "revision": revision,
        })


def _install_child_bridge(channel: socket.socket) -> ChildBridge:
    """Host bootstrap only; channel arrives via inherited launch controls."""
    global _bridge
    if _bridge is not None:
        raise CapabilityInvocationError("child bridge already installed")
    _bridge = ChildBridge(channel)
    return _bridge


def _child_request(capability: Any, inputs: dict[str, Any], *, digest: str,
                   child_key: str | None, wait: bool, timeout_seconds: float,
                   poll_seconds: float) -> dict[str, Any]:
    from astrid.core.execution.managed_inputs import managed_file_digest
    descriptors = []
    values = dict(inputs)
    for port in capability.inputs:
        if str(port.type).lower() != "file" or values.get(port.name) is None:
            continue
        value = values[port.name]
        if isinstance(value, Mapping) and "filename" in value and not ({"digest", "object_id"} & value.keys()):
            if (set(value) != {"filename", "media_type", "output_port"}
                    or any(type(item) is not str or not item for item in value.values())):
                raise CapabilityValidationError("producer file requires filename/media_type/output_port strings")
            filename = value["filename"]
            if (PurePosixPath(filename).is_absolute() or ".." in filename.split("/")
                    or "\\" in filename or "\x00" in filename or filename in {".", ""}):
                raise CapabilityValidationError("producer filename must be contained and attempt-relative")
            descriptor = {"name": port.name, "kind": "producer_file", **value}
        else:
            if isinstance(value, Mapping) and set(value) - {"object_id", "digest", "filename"}:
                raise CapabilityValidationError("object file binding contains unsupported fields")
            try:
                descriptor = {"name": port.name, "kind": "object", "object_id": managed_file_digest(value, port.name)}
            except ValueError as exc:
                raise CapabilityValidationError(str(exc)) from exc
        descriptors.append(descriptor)
    exact = {"child": {"capability_id": capability.id, "capability_digest": digest},
             "inputs": values, "input_descriptors": descriptors}
    encoded = _strict_json(exact)
    if child_key is None:
        child_key = "sdk-child-" + hashlib.sha256(encoded).hexdigest()
    if type(child_key) is not str or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._~-]{0,127}", child_key):
        raise CapabilityValidationError("child_key must start alphanumeric and contain at most 128 ASCII key characters")
    if type(wait) is not bool:
        raise CapabilityValidationError("child wait must be boolean")
    for name, number in (("timeout_seconds", timeout_seconds), ("poll_seconds", poll_seconds)):
        if type(number) not in (int, float) or not math.isfinite(number) or number <= 0:
            raise CapabilityValidationError(f"child {name} must be positive and finite")
    return {**exact, "child_key": child_key, "wait": wait,
            "timeout_seconds": float(timeout_seconds), "poll_seconds": float(poll_seconds)}
