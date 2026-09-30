"""Darwin role-bound process custody using kernel audit tokens.

This is the product-sized form of the reviewed X3C custody broker.  A child
registers on an owner-only AF_UNIX connection before ``exec``.  The broker
authenticates that connection with ``LOCAL_PEERTOKEN``, fsyncs a hash-chained
journal and its ACK checkpoint, then permits the exec.  Admission is sealed
after the same connection exposes the refreshed post-exec audit token.

Cleanup has exactly one signal primitive: ``proc_signal_with_audittoken``.
There is deliberately no PID, process-group, or ``Popen`` signal fallback.
"""

from __future__ import annotations

import base64
import ctypes
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any, Callable, Mapping, Sequence


SOL_LOCAL = 0
LOCAL_PEERTOKEN = 0x006
TOKEN_BYTES = 32
FRAME_LIMIT = 16 * 1024
PROTOCOL_VERSION = 1


class CustodyError(RuntimeError):
    """The exact registered process incarnation cannot be proved or signalled."""


class _AuditToken(ctypes.Structure):
    _fields_ = [("value", ctypes.c_uint32 * 8)]


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _digest(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _send_frame(connection: socket.socket, value: Mapping[str, object]) -> None:
    encoded = _canonical(dict(value)) + b"\n"
    if len(encoded) > FRAME_LIMIT:
        raise CustodyError("custody frame exceeds its hard limit")
    connection.sendall(encoded)


def _read_frame(connection: socket.socket) -> dict[str, object]:
    raw = bytearray()
    while not raw.endswith(b"\n"):
        part = connection.recv(min(4096, FRAME_LIMIT + 1 - len(raw)))
        if not part:
            raise CustodyError("custody peer closed before a complete frame")
        raw.extend(part)
        if len(raw) > FRAME_LIMIT:
            raise CustodyError("custody frame exceeds its hard limit")
    if b"\n" in raw[:-1]:
        raise CustodyError("custody frame contains trailing data")
    value = json.loads(bytes(raw[:-1]).decode("utf-8"))
    if not isinstance(value, dict):
        raise CustodyError("custody frame is not an object")
    return value


def _token_details(connection: socket.socket) -> dict[str, object]:
    if sys.platform != "darwin":
        raise CustodyError("Darwin audit-token custody is unavailable")
    raw = connection.getsockopt(SOL_LOCAL, LOCAL_PEERTOKEN, TOKEN_BYTES)
    if len(raw) != TOKEN_BYTES:
        raise CustodyError("LOCAL_PEERTOKEN returned an invalid token size")
    token = _AuditToken.from_buffer_copy(raw)
    libbsm = ctypes.CDLL("/usr/lib/libbsm.0.dylib", use_errno=True)
    libbsm.audit_token_to_pid.argtypes = [_AuditToken]
    libbsm.audit_token_to_pid.restype = ctypes.c_int
    libbsm.audit_token_to_pidversion.argtypes = [_AuditToken]
    libbsm.audit_token_to_pidversion.restype = ctypes.c_int
    return {
        "pid": int(libbsm.audit_token_to_pid(token)),
        "pidversion": int(libbsm.audit_token_to_pidversion(token)),
        "uid": int(token.value[1]),
        "words": [int(value) for value in token.value],
        "sha256": _digest(raw),
    }


def _signal_token(token_words: Sequence[int], signum: int) -> None:
    if sys.platform != "darwin":
        raise CustodyError("Darwin audit-token custody is unavailable")
    if len(token_words) != 8 or any(
        isinstance(value, bool) or not isinstance(value, int) for value in token_words
    ):
        raise CustodyError("registered audit token is invalid")
    token = _AuditToken()
    for index, value in enumerate(token_words):
        token.value[index] = value
    library = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
    function = library.proc_signal_with_audittoken
    function.argtypes = [ctypes.POINTER(_AuditToken), ctypes.c_int]
    function.restype = ctypes.c_int
    ctypes.set_errno(0)
    result = int(function(ctypes.byref(token), int(signum)))
    observed_errno = int(ctypes.get_errno())
    if result != 0:
        raise CustodyError(
            "proc_signal_with_audittoken failed for registered target "
            f"(returncode={result}, errno={observed_errno})"
        )


def _write_all(descriptor: int, value: bytes) -> None:
    offset = 0
    while offset < len(value):
        written = os.write(descriptor, value[offset:])
        if written <= 0:
            raise CustodyError("custody journal write was incomplete")
        offset += written


class RoleBoundCustodyBroker:
    """One-role broker whose durable seal is the only cleanup authority."""

    def __init__(
        self,
        *,
        role: str,
        identity_provider: Callable[[int], Mapping[str, object] | None],
        ledger_root: Path | None = None,
        timeout: float = 5.0,
    ) -> None:
        if not role or any(character not in "abcdefghijklmnopqrstuvwxyz0123456789_-" for character in role):
            raise CustodyError("custody role is invalid")
        if sys.platform != "darwin":
            raise CustodyError("Darwin audit-token custody is unavailable")
        self.role = role
        self.identity_provider = identity_provider
        self.timeout = timeout
        self.run_id = _digest(os.urandom(32))
        self.root = ledger_root or Path(tempfile.mkdtemp(prefix="astrid-custody-"))
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.root, 0o700)
        self.journal_path = self.root / "custody.journal.jsonl"
        self.ledger_path = self.root / "custody.ledger.json"
        socket_root = Path(tempfile.mkdtemp(prefix="astrid-cb-"))
        os.chmod(socket_root, 0o700)
        self.socket_root = socket_root
        self.socket_path = socket_root / "s"
        self.listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.listener.bind(str(self.socket_path))
        os.chmod(self.socket_path, 0o600)
        self.listener.listen(1)
        self.listener.settimeout(timeout)
        self.sequence = 0
        self.chain_head: str | None = None
        self.state = "accepting"
        self.registration: dict[str, object] | None = None
        self.ack: dict[str, object] | None = None
        self.error: BaseException | None = None
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def child_environment(self, argv: Sequence[str], *, start_new_session: bool) -> dict[str, str]:
        executable = _resolve_executable(argv[0])
        normalized = [executable, *list(argv[1:])]
        return {
            "ASTRID_CUSTODY_SOCKET": str(self.socket_path),
            "ASTRID_CUSTODY_RUN_ID": self.run_id,
            "ASTRID_CUSTODY_ROLE": self.role,
            "ASTRID_CUSTODY_START_SESSION": "1" if start_new_session else "0",
            "ASTRID_CUSTODY_TARGET_B64": base64.b64encode(_canonical(normalized)).decode("ascii"),
        }

    def wait_until_sealed(self) -> None:
        if not self._ready.wait(self.timeout):
            raise CustodyError("custody registration timed out")
        self._thread.join(timeout=self.timeout)
        if self.error is not None:
            raise CustodyError(f"custody registration failed: {self.error}") from self.error
        if self._thread.is_alive() or self.state != "sealed" or self.registration is None:
            raise CustodyError("custody admission did not seal")

    def signal(self, signum: int, *, expected_pid: int) -> None:
        if signum not in {signal.SIGTERM, signal.SIGKILL}:
            raise CustodyError("custody signal is outside the admitted set")
        if self.state != "sealed" or self.registration is None:
            raise CustodyError("custody admission is not sealed")
        registered = self.registration
        if registered.get("pid") != expected_pid:
            raise CustodyError("registered custody PID differs from the process handle")
        observed = self.identity_provider(expected_pid)
        if observed is None or any(
            observed.get(name) != registered.get("identity", {}).get(name)  # type: ignore[union-attr]
            for name in ("pid", "birth_id", "uid")
        ):
            raise CustodyError("registered process incarnation is absent or changed")
        _signal_token(registered["audit_token_words"], signum)  # type: ignore[arg-type]

    def _persist(self, event_name: str) -> None:
        snapshot = {
            "version": PROTOCOL_VERSION,
            "run_id": self.run_id,
            "role": self.role,
            "state": self.state,
            "sequence": self.sequence,
            "registration": self.registration,
            "ack": self.ack,
        }
        event: dict[str, object] = {
            "version": PROTOCOL_VERSION,
            "event": event_name,
            "sequence": self.sequence,
            "predecessor_digest": self.chain_head,
            "snapshot_digest": _digest(_canonical(snapshot)),
            "snapshot": snapshot,
        }
        event["event_digest"] = _digest(_canonical(event))
        descriptor = os.open(
            self.journal_path,
            os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        try:
            observed = os.fstat(descriptor)
            if not stat.S_ISREG(observed.st_mode) or observed.st_uid != os.getuid():
                raise CustodyError("custody journal is not an owner file")
            _write_all(descriptor, _canonical(event) + b"\n")
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        self.chain_head = str(event["event_digest"])
        ledger = {**snapshot, "chain_digest": self.chain_head}
        temporary = self.ledger_path.with_suffix(".tmp")
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        try:
            _write_all(descriptor, _canonical(ledger) + b"\n")
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        os.replace(temporary, self.ledger_path)
        directory = os.open(str(self.root), os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)

    def _serve(self) -> None:
        connection: socket.socket | None = None
        try:
            connection, _ = self.listener.accept()
            connection.settimeout(self.timeout)
            frame = _read_frame(connection)
            required = {"version", "command", "run_id", "role", "pid", "ppid", "argv_digest"}
            if (
                set(frame) != required
                or frame["version"] != PROTOCOL_VERSION
                or frame["command"] != "register_pre_exec"
                or frame["run_id"] != self.run_id
                or frame["role"] != self.role
                or frame["ppid"] != os.getpid()
                or not isinstance(frame["pid"], int)
                or isinstance(frame["pid"], bool)
                or frame["pid"] <= 0
            ):
                raise CustodyError("custody registration frame is invalid")
            pre = _token_details(connection)
            if pre["pid"] != frame["pid"] or pre["uid"] != os.getuid():
                raise CustodyError("custody registration kernel identity differs")
            before = self.identity_provider(int(frame["pid"]))
            if before is None:
                raise CustodyError("pre-exec process identity is unavailable")
            self.sequence += 1
            self.registration = {
                "pid": frame["pid"],
                "identity": dict(before),
                "argv_digest": frame["argv_digest"],
                "pre_exec_audit_token_sha256": pre["sha256"],
                "pre_exec_pidversion": pre["pidversion"],
                "audit_token_words": pre["words"],
            }
            self._persist("registration_pre_exec")
            self.ack = {
                "version": PROTOCOL_VERSION,
                "status": "registered",
                "run_id": self.run_id,
                "role": self.role,
                "pid": frame["pid"],
                "registration_digest": _digest(_canonical(frame)),
                "ledger_state_digest": self.chain_head,
            }
            self._persist("registration_ack_checkpoint")
            _send_frame(connection, self.ack)
            deadline = time.monotonic() + self.timeout
            post: dict[str, object] | None = None
            while time.monotonic() < deadline:
                candidate = _token_details(connection)
                if candidate["pidversion"] != pre["pidversion"]:
                    post = candidate
                    break
                time.sleep(0.005)
            if post is None or post["pid"] != frame["pid"] or post["uid"] != os.getuid():
                raise CustodyError("post-exec audit token did not bind the registered process")
            after = self.identity_provider(int(frame["pid"]))
            if after is None or any(after.get(name) != before.get(name) for name in ("pid", "birth_id", "uid")):
                raise CustodyError("pre/post-exec process incarnation differs")
            self.registration.update({
                "identity": dict(after),
                "audit_token_words": post["words"],
                "audit_token_sha256": post["sha256"],
                "audit_token_pidversion": post["pidversion"],
            })
            self.sequence += 1
            self._persist("registration_post_exec")
            self.state = "sealed"
            self._persist("admission_sealed")
        except BaseException as exc:
            self.error = exc
        finally:
            self._ready.set()
            if connection is not None:
                connection.close()
            self.listener.close()
            try:
                self.socket_path.unlink()
                self.socket_root.rmdir()
            except OSError:
                pass


def _resolve_executable(value: str) -> str:
    candidate = Path(value)
    if not candidate.is_absolute():
        located = shutil.which(value)
        if located is None:
            raise CustodyError("custodied executable is unavailable")
        candidate = Path(located)
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise CustodyError("custodied executable is unavailable") from exc
    if not resolved.is_file() or not os.access(resolved, os.X_OK):
        raise CustodyError("custodied executable is not executable")
    return str(resolved)


def custody_wrapper_argv(module_name: str) -> list[str]:
    return [sys.executable, "-I", "-m", module_name, "--custody-exec"]


def child_exec_from_environment() -> int:
    encoded = os.environ.get("ASTRID_CUSTODY_TARGET_B64", "")
    try:
        argv = json.loads(base64.b64decode(encoded, validate=True).decode("utf-8"))
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CustodyError("custody target argv is invalid") from exc
    if not isinstance(argv, list) or not argv or any(not isinstance(item, str) or "\0" in item for item in argv):
        raise CustodyError("custody target argv is invalid")
    socket_path = os.environ.get("ASTRID_CUSTODY_SOCKET", "")
    run_id = os.environ.get("ASTRID_CUSTODY_RUN_ID", "")
    role = os.environ.get("ASTRID_CUSTODY_ROLE", "")
    connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    connection.settimeout(5.0)
    connection.connect(socket_path)
    os.set_inheritable(connection.fileno(), True)
    frame = {
        "version": PROTOCOL_VERSION,
        "command": "register_pre_exec",
        "run_id": run_id,
        "role": role,
        "pid": os.getpid(),
        "ppid": os.getppid(),
        "argv_digest": _digest(_canonical(argv)),
    }
    _send_frame(connection, frame)
    ack = _read_frame(connection)
    if (
        ack.get("status") != "registered"
        or ack.get("run_id") != run_id
        or ack.get("role") != role
        or ack.get("pid") != os.getpid()
        or ack.get("registration_digest") != _digest(_canonical(frame))
        or not str(ack.get("ledger_state_digest", "")).startswith("sha256:")
    ):
        raise CustodyError("custody registration acknowledgement is invalid")
    if os.environ.get("ASTRID_CUSTODY_START_SESSION") == "1":
        os.setsid()
    for name in (
        "ASTRID_CUSTODY_TARGET_B64", "ASTRID_CUSTODY_START_SESSION",
        "ASTRID_CUSTODY_SOCKET", "ASTRID_CUSTODY_RUN_ID", "ASTRID_CUSTODY_ROLE",
    ):
        os.environ.pop(name, None)
    descriptor = connection.detach()
    os.set_inheritable(descriptor, True)
    os.execve(argv[0], argv, dict(os.environ))
    raise AssertionError("execve returned")


def main() -> int:
    if sys.argv[1:] != ["--custody-exec"]:
        raise CustodyError("unsupported custody broker invocation")
    return child_exec_from_environment()


if __name__ == "__main__":
    raise SystemExit(main())
