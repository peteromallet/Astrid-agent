"""Identity-fenced process cleanup for subprocesses owned by Astrid."""

from __future__ import annotations

import hashlib
import ctypes
import os
from pathlib import Path
import shutil
import signal
import stat
import struct
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import Any, Mapping

from astrid.core.execution.custody_broker import (
    CustodyError,
    RoleBoundCustodyBroker,
)


class ProcessCleanupUncertain(RuntimeError):
    """Cleanup stopped because process ownership could not be proven."""

    def __init__(self, message: str, *, process: subprocess.Popen | None = None):
        super().__init__(message)
        self.process = process


class _ProcessSnapshot(dict[int, "_ProcessInfo"]):
    """A process census whose completeness is distinct from its membership."""

    def __init__(
        self,
        values: Mapping[int, "_ProcessInfo"] | None = None,
        *,
        complete: bool,
        error: str = "",
    ) -> None:
        super().__init__(values or {})
        self.complete = complete
        self.error = error


@dataclass(frozen=True)
class _ProcessInfo:
    # Keep the first four fields in their historical order for synthetic
    # process censuses used by local callers and tests.
    pid: int
    ppid: int
    pgid: int
    birth: str
    uid: int | None = None
    sid: int | None = None
    executable: str = ""
    executable_digest: str = ""
    command_digest: str = ""


@dataclass(frozen=True)
class _OwnedMember:
    identity: _ProcessInfo
    role: str
    expected_parent_pid: int


def _sha256(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _birth_token(pid: int, fallback: str) -> str:
    """Prefer the kernel start token where procfs exposes it."""

    try:
        raw = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
        fields = raw.rsplit(")", 1)[-1].split()
        if len(fields) >= 20:
            return f"proc-start-ticks:{fields[19]}"
    except (OSError, ValueError):
        pass
    return f"ps-lstart:{fallback}" if fallback else ""


def _canonical_executable(pid: int, observed: str) -> tuple[str, str]:
    """Return the canonical executable path and its exact file digest."""

    proc_executable = Path(f"/proc/{pid}/exe")
    try:
        if proc_executable.exists():
            candidate = proc_executable.resolve(strict=True)
        else:
            raw = Path(observed).expanduser()
            if not raw.is_absolute():
                located = shutil.which(observed)
                if located is None:
                    return "", ""
                raw = Path(located)
            candidate = raw.resolve(strict=True)
        before = candidate.stat()
        if not stat.S_ISREG(before.st_mode):
            return "", ""
        hasher = hashlib.sha256()
        with candidate.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                hasher.update(chunk)
        after = candidate.stat()
        if (
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
        ) != (
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
        ):
            return "", ""
        return str(candidate), "sha256:" + hasher.hexdigest()
    except OSError:
        return "", ""


def _darwin_process_argv(pid: int) -> tuple[bytes, ...] | None:
    """Read exact argv bytes through Darwin's supported KERN_PROCARGS2 API."""

    ctl_kern = 1
    kern_procargs2 = 49
    libc = ctypes.CDLL(None, use_errno=True)
    sysctl = libc.sysctl
    sysctl.argtypes = (
        ctypes.POINTER(ctypes.c_int),
        ctypes.c_uint,
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_size_t),
        ctypes.c_void_p,
        ctypes.c_size_t,
    )
    sysctl.restype = ctypes.c_int
    mib = (ctypes.c_int * 3)(ctl_kern, kern_procargs2, int(pid))
    size = ctypes.c_size_t(0)
    if sysctl(mib, 3, None, ctypes.byref(size), None, 0) != 0 or size.value < 4:
        return None
    buffer = ctypes.create_string_buffer(size.value)
    if sysctl(mib, 3, buffer, ctypes.byref(size), None, 0) != 0:
        return None
    raw = buffer.raw[: size.value]
    argc = struct.unpack_from("=i", raw)[0]
    if argc < 1 or argc > 1_000_000:
        return None
    offset = 4
    executable_end = raw.find(b"\0", offset)
    if executable_end < 0:
        return None
    offset = executable_end + 1
    while offset < len(raw) and raw[offset] == 0:
        offset += 1
    argv: list[bytes] = []
    while len(argv) < argc and offset < len(raw):
        end = raw.find(b"\0", offset)
        if end < 0:
            return None
        argv.append(raw[offset:end])
        offset = end + 1
    return tuple(argv) if len(argv) == argc else None


def _process_argv(pid: int) -> tuple[bytes, ...] | None:
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:
        if sys.platform == "darwin":
            return _darwin_process_argv(pid)
        return None
    if not raw:
        return None
    return tuple(value for value in raw.split(b"\0") if value)


def _argv_digest(argv: tuple[bytes, ...] | list[bytes]) -> str:
    encoded = bytearray(b"astrid.argv.v1\0")
    encoded.extend(len(argv).to_bytes(8, "big"))
    for value in argv:
        encoded.extend(len(value).to_bytes(8, "big"))
        encoded.extend(value)
    return _sha256(bytes(encoded))


def _process_snapshot(
    *,
    full_pids: set[int] | None = None,
    full_pgid: int | None = None,
    full_root_pid: int | None = None,
) -> _ProcessSnapshot:
    """Read a full process census without invoking a shell."""

    ps = next(
        (candidate for candidate in ("/bin/ps", "/usr/bin/ps") if os.path.isfile(candidate)),
        "ps",
    )
    try:
        result = subprocess.run(
            [
                ps,
                "-ww",
                "-axo",
                "pid=,ppid=,uid=,pgid=,lstart=,comm=,command=",
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=1.0,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return _ProcessSnapshot(complete=False, error=f"ps failed: {exc}")
    if result.returncode != 0:
        return _ProcessSnapshot(
            complete=False,
            error=f"ps exited {result.returncode}",
        )
    rows: dict[int, tuple[int, int, int, int | None, int, str, str, str]] = {}
    parse_failed = False
    for line in result.stdout.splitlines():
        fields = line.strip().split(None, 10)
        if len(fields) != 11:
            if line.strip():
                parse_failed = True
            continue
        try:
            pid, ppid, uid, pgid = (int(value) for value in fields[:4])
        except ValueError:
            parse_failed = True
            continue
        try:
            sid = os.getsid(pid)
        except OSError:
            sid = None
        birth = _birth_token(pid, " ".join(fields[4:9]))
        rows[pid] = (pid, ppid, uid, sid, pgid, birth, fields[9], fields[10])
    selected = set(full_pids or ())
    if full_pgid is not None:
        selected.update(pid for pid, row in rows.items() if row[4] == full_pgid)
    if full_root_pid is not None:
        selected.add(full_root_pid)
        changed = True
        while changed:
            changed = False
            for pid, row in rows.items():
                if row[1] in selected and pid not in selected:
                    selected.add(pid)
                    changed = True
    if not rows:
        return _ProcessSnapshot(complete=False, error="ps census was empty")
    entries: dict[int, _ProcessInfo] = {}
    for pid, ppid, uid, sid, pgid, birth, _comm, command in rows.values():
        executable = ""
        executable_digest = ""
        command_digest = ""
        if pid in selected:
            argv = _process_argv(pid)
            if not argv:
                command_executable = ""
            else:
                command_executable = os.fsdecode(argv[0])
            executable, executable_digest = _canonical_executable(
                pid, command_executable
            )
            command_digest = _argv_digest(argv) if argv else ""
        entries[pid] = _ProcessInfo(
            pid=pid,
            ppid=ppid,
            pgid=pgid,
            birth=birth,
            uid=uid,
            sid=sid,
            executable=executable,
            executable_digest=executable_digest,
            command_digest=command_digest,
        )
    return _ProcessSnapshot(
        entries,
        complete=not parse_failed,
        error="ps census contained malformed rows" if parse_failed else "",
    )


def _require_census(
    snapshot: Mapping[int, _ProcessInfo],
    *,
    label: str,
) -> None:
    if getattr(snapshot, "complete", True) is not True:
        detail = str(getattr(snapshot, "error", "process census unavailable"))
        raise ProcessCleanupUncertain(
            f"cleanup-uncertain: {label} census is unobservable: {detail}"
        )


def _identity_complete(info: _ProcessInfo) -> bool:
    return bool(
        info.pid > 0
        and info.ppid >= 0
        and info.pgid > 0
        and info.birth
        and isinstance(info.uid, int)
        and info.uid >= 0
        and isinstance(info.sid, int)
        and info.sid > 0
        and info.executable
        and info.executable_digest.startswith("sha256:")
        and info.command_digest.startswith("sha256:")
    )


def _stable_identity(info: _ProcessInfo) -> tuple[Any, ...]:
    return (
        info.pid,
        info.birth,
        info.uid,
        info.executable,
        info.executable_digest,
        info.command_digest,
        info.sid,
        info.pgid,
    )


def _identity_mismatches(expected: _ProcessInfo, observed: _ProcessInfo) -> str:
    fields = (
        "pid",
        "birth",
        "uid",
        "executable",
        "executable_digest",
        "command_digest",
        "sid",
        "pgid",
    )
    return ",".join(
        field for field in fields if getattr(expected, field) != getattr(observed, field)
    )


def _require_complete(info: _ProcessInfo | None, *, label: str) -> _ProcessInfo:
    if info is None or not _identity_complete(info):
        raise ProcessCleanupUncertain(f"cleanup-uncertain: {label} identity is unobservable")
    if info.uid != getattr(os, "getuid", lambda: info.uid)():
        raise ProcessCleanupUncertain(f"cleanup-uncertain: {label} uid does not match")
    return info


def popen_owned_group(
    argv: list[str],
    *,
    custody_callback=None,
    **kwargs: Any,
) -> subprocess.Popen:
    """Launch a command as a fresh process-group/session leader."""

    options = dict(kwargs)
    child_environment = dict(options.pop("env", os.environ))

    def identity_provider(pid: int) -> Mapping[str, object] | None:
        observed = _process_snapshot(full_pids={pid}).get(pid)
        if observed is None or not _identity_complete(observed):
            return None
        return {
            "pid": observed.pid,
            "birth_id": observed.birth,
            "uid": observed.uid,
            "ppid": observed.ppid,
            "pgid": observed.pgid,
            "sid": observed.sid,
            "executable": observed.executable,
            "executable_digest": observed.executable_digest,
            "command_digest": observed.command_digest,
        }

    try:
        broker = RoleBoundCustodyBroker(
            role="owned_group_leader",
            identity_provider=identity_provider,
        )
        child_environment.update(
            broker.child_environment(argv, start_new_session=True)
        )
    except CustodyError as exc:
        raise ProcessCleanupUncertain(
            f"cleanup-uncertain: audit-token custody admission unavailable: {exc}"
        ) from exc
    options["env"] = child_environment
    options["start_new_session"] = False
    wrapper = [
        sys.executable,
        "-I",
        str(Path(__file__).with_name("custody_broker.py").resolve()),
        "--custody-exec",
    ]
    process = subprocess.Popen(wrapper, **options)
    process._astrid_process_group_id = process.pid  # type: ignore[attr-defined]
    process._astrid_custody_broker = broker  # type: ignore[attr-defined]
    if custody_callback is not None:
        custody_callback(process)
    try:
        broker.wait_until_sealed()
    except CustodyError as exc:
        process._astrid_cleanup_uncertain = str(exc)  # type: ignore[attr-defined]
        raise ProcessCleanupUncertain(
            f"cleanup-uncertain: audit-token custody registration failed: {exc}",
            process=process,
        ) from exc
    info: _ProcessInfo | None = None
    candidate: _ProcessInfo | None = None
    stable_since: float | None = None
    readiness_deadline = time.monotonic() + 1.0
    while time.monotonic() < readiness_deadline:
        observed = _process_snapshot(full_pids={process.pid}).get(process.pid)
        if observed is not None and _identity_complete(observed):
            if (
                candidate is not None
                and observed.ppid == candidate.ppid
                and _stable_identity(observed) == _stable_identity(candidate)
            ):
                if stable_since is not None and time.monotonic() - stable_since >= 0.03:
                    info = observed
                    break
            else:
                candidate = observed
                stable_since = time.monotonic()
        if process.poll() is not None:
            info = candidate
            break
        time.sleep(0.005)
    if info is None and process.poll() is not None:
        # A command may complete before the first census. No signal can be
        # sent to that vanished process. A later release still refuses any
        # surviving member in its numeric group because no leader anchor was
        # captured.
        return process
    try:
        info = _require_complete(info, label="launched leader")
        if info.pid != info.pgid or info.pid != info.sid:
            raise ProcessCleanupUncertain(
                "cleanup-uncertain: launched leader does not own its session and group"
            )
    except ProcessCleanupUncertain as exc:
        process._astrid_cleanup_uncertain = str(exc)  # type: ignore[attr-defined]
        exc.process = process
        raise
    process._astrid_process_birth = info.birth  # type: ignore[attr-defined]
    process._astrid_process_identity = info  # type: ignore[attr-defined]
    process._astrid_owned_group_members = {  # type: ignore[attr-defined]
        info.pid: _OwnedMember(info, "leader", info.ppid)
    }
    return process


def _group_id(process: subprocess.Popen) -> int:
    return int(getattr(process, "_astrid_process_group_id", process.pid))


def _leader_identity(process: subprocess.Popen) -> _ProcessInfo | None:
    value = getattr(process, "_astrid_process_identity", None)
    return value if isinstance(value, _ProcessInfo) else None


def _leader_birth(process: subprocess.Popen, snapshot: dict[int, _ProcessInfo]) -> str | None:
    identity = _leader_identity(process)
    if identity is not None:
        return identity.birth
    birth = getattr(process, "_astrid_process_birth", None)
    if isinstance(birth, str):
        return birth
    info = snapshot.get(process.pid)
    if info is not None:
        process._astrid_process_birth = info.birth  # type: ignore[attr-defined]
        return info.birth
    return None


def _owned_group_members(process: subprocess.Popen) -> dict[int, _OwnedMember]:
    value = getattr(process, "_astrid_owned_group_members", None)
    if isinstance(value, dict):
        return value
    identity = _leader_identity(process)
    value = {}
    if identity is not None:
        value[identity.pid] = _OwnedMember(identity, "leader", identity.ppid)
    process._astrid_owned_group_members = value  # type: ignore[attr-defined]
    return value


def _process_group_snapshot(process: subprocess.Popen) -> dict[int, _ProcessInfo]:
    return _process_snapshot(full_pgid=_group_id(process))


def _process_tree_snapshot(process: subprocess.Popen) -> dict[int, _ProcessInfo]:
    return _process_snapshot(full_root_pid=process.pid)


def _member_role_valid(
    member: _OwnedMember,
    current: _ProcessInfo,
    snapshot: Mapping[int, _ProcessInfo],
    *,
    group_id: int,
) -> bool:
    if member.role == "leader":
        return (
            current.pid == group_id
            and current.pgid == group_id
            and current.sid == group_id
            and current.ppid == member.expected_parent_pid
        )
    if current.pgid != group_id or current.sid != group_id:
        return False
    if current.ppid == member.expected_parent_pid:
        return True
    expected_parent = snapshot.get(member.expected_parent_pid)
    return expected_parent is None and current.ppid not in {
        info.pid for info in snapshot.values() if info.pgid == group_id
    }


def _discover_group(
    process: subprocess.Popen,
    snapshot: dict[int, _ProcessInfo],
) -> dict[int, _OwnedMember]:
    """Validate and extend the ownership set from exact parent lineage."""

    _require_census(snapshot, label="process group")
    group_id = _group_id(process)
    known = _owned_group_members(process)
    anchor = _leader_identity(process)
    group = {pid: info for pid, info in snapshot.items() if info.pgid == group_id}
    if anchor is None:
        if process.poll() is None or group:
            raise ProcessCleanupUncertain(
                "cleanup-uncertain: owned leader identity was not captured"
            )
        return {}
    current_leader = snapshot.get(anchor.pid)
    was_reaped = getattr(process, "returncode", None) is not None
    direct_exited = process.poll() is not None
    if (
        not was_reaped
        and direct_exited
        and current_leader is not None
        and current_leader.birth == anchor.birth
    ):
        # This census preceded the successful direct-child wait performed by
        # poll(), so the row is the now-reaped child (often a zombie with no
        # observable argv/executable/SID), never a reused PID.
        group.pop(anchor.pid, None)
        snapshot = dict(snapshot)
        snapshot.pop(anchor.pid, None)
        current_leader = None
    if current_leader is not None and _stable_identity(current_leader) != _stable_identity(anchor):
        if current_leader.pgid == group_id:
            raise ProcessCleanupUncertain(
                "cleanup-uncertain: owned leader identity changed: "
                + _identity_mismatches(anchor, current_leader)
            )
        current_leader = None
    if not direct_exited and current_leader is None:
        raise ProcessCleanupUncertain(
            "cleanup-uncertain: live direct child is missing from the census"
        )
    pending = dict(group)
    while pending:
        progressed = False
        for pid, info in tuple(pending.items()):
            _require_complete(info, label=f"group member {pid}")
            prior = known.get(pid)
            if prior is not None:
                if _stable_identity(info) != _stable_identity(prior.identity):
                    raise ProcessCleanupUncertain(
                        f"cleanup-uncertain: group member {pid} identity changed: "
                        + _identity_mismatches(prior.identity, info)
                    )
                if not _member_role_valid(prior, info, snapshot, group_id=group_id):
                    raise ProcessCleanupUncertain(
                        "cleanup-uncertain: group member "
                        f"{pid} role changed (role={prior.role}, "
                        f"expected_parent={prior.expected_parent_pid}, "
                        f"ppid={info.ppid}, pgid={info.pgid}, sid={info.sid}, "
                        f"owned_pgid={group_id})"
                    )
                pending.pop(pid)
                progressed = True
                continue
            parent = known.get(info.ppid)
            if parent is not None and snapshot.get(info.ppid) is not None:
                known[pid] = _OwnedMember(info, "descendant", info.ppid)
                pending.pop(pid)
                progressed = True
        if not progressed:
            unknown = min(pending)
            raise ProcessCleanupUncertain(
                f"cleanup-uncertain: group member {unknown} has no owned parent lineage"
            )
    return {pid: known[pid] for pid in group if pid in known}


def _snapshot_group(process: subprocess.Popen) -> dict[int, str]:
    return {
        pid: member.identity.birth
        for pid, member in _discover_group(process, _process_group_snapshot(process)).items()
    }


def _live_members(members: Mapping[int, Any]) -> list[int]:
    if not members:
        return []
    snapshot = _process_snapshot()
    live: list[int] = []
    for pid, expected in members.items():
        current = snapshot.get(pid)
        birth = expected.identity.birth if isinstance(expected, _OwnedMember) else str(expected)
        if current is not None and current.birth == birth:
            live.append(pid)
    return live


def _group_members_owned(
    process: subprocess.Popen,
    known: dict[int, Any],
    snapshot: dict[int, _ProcessInfo] | None = None,
) -> tuple[dict[int, str], bool]:
    """Compatibility view over the strict ownership census."""

    members = _discover_group(
        process,
        _process_group_snapshot(process) if snapshot is None else snapshot,
    )
    rendered = {pid: member.identity.birth for pid, member in members.items()}
    known.update(rendered)
    return rendered, True


def group_exists(process: subprocess.Popen) -> bool:
    """Report direct-process liveness; group cleanup uses its own census."""

    return process.poll() is None


def _revalidate_group(
    process: subprocess.Popen,
    expected: Mapping[int, _OwnedMember],
) -> dict[int, _OwnedMember]:
    latest = _process_group_snapshot(process)
    current = _discover_group(process, latest)
    unexpected = set(current) - set(expected)
    if unexpected:
        raise ProcessCleanupUncertain(
            "cleanup-uncertain: group changed in final signal window"
        )
    for pid, member in current.items():
        prior = expected.get(pid)
        if prior is None or _stable_identity(member.identity) != _stable_identity(prior.identity):
            raise ProcessCleanupUncertain(
                f"cleanup-uncertain: group member {pid} changed in final signal window"
            )
        if not _member_role_valid(
            prior,
            member.identity,
            latest,
            group_id=_group_id(process),
        ):
            raise ProcessCleanupUncertain(
                f"cleanup-uncertain: group member {pid} role changed in final signal window"
            )
    return current


def _signal_group_once(
    process: subprocess.Popen,
    sig: int,
    expected: Mapping[int, _OwnedMember],
) -> None:
    current = _revalidate_group(process, expected)
    if not current:
        return
    broker = getattr(process, "_astrid_custody_broker", None)
    if not isinstance(broker, RoleBoundCustodyBroker):
        raise ProcessCleanupUncertain(
            "cleanup-uncertain: role-bound audit-token custody is unavailable"
        )
    unregistered = set(current) - {process.pid}
    if unregistered:
        raise ProcessCleanupUncertain(
            "cleanup-uncertain: process group contains descendants without "
            "role-bound audit-token registrations"
        )
    if process.pid not in current:
        return
    try:
        broker.signal(sig, expected_pid=process.pid)
    except CustodyError as exc:
        raise ProcessCleanupUncertain(
            "cleanup-uncertain: registered process could not be signalled "
            "with its kernel audit token"
        ) from exc


def signal_group(process: subprocess.Popen, sig: int) -> None:
    """Signal an owned group after two complete identity observations."""

    expected = _discover_group(process, _process_group_snapshot(process))
    _signal_group_once(process, sig, expected)


def _wait_for_group_change(
    process: subprocess.Popen,
    *,
    deadline: float,
) -> dict[int, _OwnedMember]:
    members: dict[int, _OwnedMember] = {}
    while time.monotonic() < deadline:
        members = _discover_group(process, _process_group_snapshot(process))
        if not members:
            return {}
        time.sleep(min(0.02, max(0.0, deadline - time.monotonic())))
    return _discover_group(process, _process_group_snapshot(process))


def _reap_direct_child(process: subprocess.Popen, *, timeout: float) -> None:
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise ProcessCleanupUncertain(
            "cleanup-uncertain: direct child did not exit after identity-fenced cleanup"
        ) from exc


def terminate_group(process: subprocess.Popen, *, grace_seconds: float = 1.0) -> None:
    """Terminate an owned session while discovering fenced descendants."""

    initial = _discover_group(process, _process_group_snapshot(process))
    if initial:
        _signal_group_once(process, signal.SIGTERM, initial)
    members = _wait_for_group_change(
        process,
        deadline=time.monotonic() + max(0.0, grace_seconds),
    )
    kill_deadline = time.monotonic() + max(1.0, grace_seconds)
    while members and time.monotonic() < kill_deadline:
        _signal_group_once(process, signal.SIGKILL, members)
        members = _wait_for_group_change(
            process,
            deadline=min(kill_deadline, time.monotonic() + 0.02),
        )
    if members:
        raise ProcessCleanupUncertain(
            "cleanup-uncertain: owned process group survived SIGKILL"
        )
    _reap_direct_child(process, timeout=max(1.0, grace_seconds))


def _discover_tree(
    process: subprocess.Popen,
    snapshot: dict[int, _ProcessInfo],
) -> dict[int, _OwnedMember]:
    _require_census(snapshot, label="process tree")
    known = getattr(process, "_astrid_owned_tree_members", None)
    if not isinstance(known, dict):
        known = {}
        process._astrid_owned_tree_members = known  # type: ignore[attr-defined]
    root = snapshot.get(process.pid)
    prior_root = known.get(process.pid)
    if prior_root is None:
        root = _require_complete(root, label="tree root")
        prior_root = _OwnedMember(root, "tree_root", root.ppid)
        known[root.pid] = prior_root
    elif root is not None and _stable_identity(root) != _stable_identity(prior_root.identity):
        raise ProcessCleanupUncertain("cleanup-uncertain: tree root identity changed")
    if process.poll() is None and root is None:
        raise ProcessCleanupUncertain(
            "cleanup-uncertain: live tree root is missing from the census"
        )
    pending = [process.pid]
    children: dict[int, list[_ProcessInfo]] = {}
    for info in snapshot.values():
        children.setdefault(info.ppid, []).append(info)
    while pending:
        parent_pid = pending.pop()
        parent = known.get(parent_pid)
        current_parent = snapshot.get(parent_pid)
        if parent is None or current_parent is None:
            continue
        if _stable_identity(current_parent) != _stable_identity(parent.identity):
            raise ProcessCleanupUncertain(
                f"cleanup-uncertain: tree member {parent_pid} identity changed"
            )
        for info in children.get(parent_pid, ()):
            _require_complete(info, label=f"tree member {info.pid}")
            if info.uid != prior_root.identity.uid:
                raise ProcessCleanupUncertain(
                    f"cleanup-uncertain: tree member {info.pid} uid changed"
                )
            if info.sid != prior_root.identity.sid or info.pgid != prior_root.identity.pgid:
                raise ProcessCleanupUncertain(
                    f"cleanup-uncertain: tree member {info.pid} group role changed"
                )
            prior = known.get(info.pid)
            if prior is not None and _stable_identity(info) != _stable_identity(prior.identity):
                raise ProcessCleanupUncertain(
                    f"cleanup-uncertain: tree member {info.pid} identity changed"
                )
            if prior is None:
                known[info.pid] = _OwnedMember(info, "tree_descendant", parent_pid)
            pending.append(info.pid)
    return {
        pid: member
        for pid, member in known.items()
        if (current := snapshot.get(pid)) is not None
        and _stable_identity(current) == _stable_identity(member.identity)
    }


def _tree_members(
    process: subprocess.Popen,
    known: dict[int, Any],
    snapshot: dict[int, _ProcessInfo] | None = None,
) -> dict[int, str]:
    """Compatibility view over strict inherited-session tree ownership."""

    members = _discover_tree(
        process,
        _process_tree_snapshot(process) if snapshot is None else snapshot,
    )
    rendered = {pid: member.identity.birth for pid, member in members.items()}
    known.update(rendered)
    return rendered


def _revalidate_tree(
    process: subprocess.Popen,
    expected: Mapping[int, _OwnedMember],
) -> dict[int, _OwnedMember]:
    latest = _process_tree_snapshot(process)
    current = _discover_tree(process, latest)
    unexpected = set(current) - set(expected)
    if unexpected:
        raise ProcessCleanupUncertain(
            "cleanup-uncertain: process tree changed in final signal window"
        )
    for pid, member in current.items():
        prior = expected.get(pid)
        if prior is None or _stable_identity(member.identity) != _stable_identity(prior.identity):
            raise ProcessCleanupUncertain(
                f"cleanup-uncertain: tree member {pid} changed in final signal window"
            )
    return current


def _signal_tree_once(
    process: subprocess.Popen,
    sig: int,
    expected: Mapping[int, _OwnedMember],
) -> None:
    current = _revalidate_tree(process, expected)
    broker = getattr(process, "_astrid_custody_broker", None)
    if not isinstance(broker, RoleBoundCustodyBroker):
        raise ProcessCleanupUncertain(
            "cleanup-uncertain: role-bound audit-token custody is unavailable"
        )
    unregistered = set(current) - {process.pid}
    if unregistered:
        raise ProcessCleanupUncertain(
            "cleanup-uncertain: process tree contains descendants without "
            "role-bound audit-token registrations"
        )
    if process.pid not in current:
        return
    try:
        broker.signal(sig, expected_pid=process.pid)
    except CustodyError as exc:
        raise ProcessCleanupUncertain(
            "cleanup-uncertain: registered tree root could not be signalled "
            "with its kernel audit token"
        ) from exc


def terminate_tree(process: subprocess.Popen, *, grace_seconds: float = 1.0) -> None:
    """Terminate only a direct child and descendants in an inherited session."""

    members = _discover_tree(process, _process_tree_snapshot(process))
    if members:
        _signal_tree_once(process, signal.SIGTERM, members)
    deadline = time.monotonic() + max(0.0, grace_seconds)
    while time.monotonic() < deadline:
        members = _discover_tree(process, _process_tree_snapshot(process))
        if not members:
            break
        time.sleep(min(0.02, max(0.0, deadline - time.monotonic())))
    kill_deadline = time.monotonic() + max(1.0, grace_seconds)
    while members and time.monotonic() < kill_deadline:
        _signal_tree_once(process, signal.SIGKILL, members)
        time.sleep(0.02)
        members = _discover_tree(process, _process_tree_snapshot(process))
    if members:
        raise ProcessCleanupUncertain(
            "cleanup-uncertain: owned process tree survived SIGKILL"
        )
    _reap_direct_child(process, timeout=max(1.0, grace_seconds))


def _signal_valid_group_members(
    process: subprocess.Popen,
    members: Mapping[int, Any],
    sig: int,
    snapshot: dict[int, _ProcessInfo] | None = None,
) -> None:
    del snapshot
    expected = _discover_group(process, _process_group_snapshot(process))
    if set(members) != set(expected):
        raise ProcessCleanupUncertain(
            "cleanup-uncertain: requested group members do not match owned census"
        )
    _signal_group_once(process, sig, expected)


def _signal_valid_tree_members(
    process: subprocess.Popen,
    members: Mapping[int, Any],
    sig: int,
    snapshot: dict[int, _ProcessInfo] | None = None,
) -> None:
    del snapshot
    expected = _discover_tree(process, _process_tree_snapshot(process))
    if set(members) != set(expected):
        raise ProcessCleanupUncertain(
            "cleanup-uncertain: requested tree members do not match owned census"
        )
    _signal_tree_once(process, sig, expected)


def release_group(process: subprocess.Popen) -> None:
    """Release a completed group, including proven descendants."""

    terminate_group(process, grace_seconds=0.2)
