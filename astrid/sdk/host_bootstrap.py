"""Process boundary for Astrid's one generic pack executor host.

The neutral runtime owns data and credentials.  This module only starts the
generic executor, waits for its registration/preflight readiness record, and
keeps a small support-directory marker so an Astrid relaunch reuses one host.
"""

from __future__ import annotations

import json
import hashlib
import os
import secrets
import shlex
import signal
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from astrid.core.execution.process_group import _process_snapshot, popen_owned_group, terminate_group
from astrid.core.generation.vibecomfy_dependency import (
    VibeComfyDependencyError,
    dependency_pythonpath,
)

PACK_HOST_ACTOR = "astrid-pack-host"
PACK_HOST_SCOPES = (
    "handshake",
    "worker:register",
    "worker:execute",
    "projects:read",
    "tasks:read",
    "objects:read",
    "objects:write",
)
PACK_HOST_PYTHON_ENV = "ASTRID_PACK_HOST_PYTHON"


class PackHostBootstrapError(RuntimeError):
    """A bounded, secret-free failure while starting the generic host."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "pack_host_bootstrap_failed",
        request_id: str = "",
        terminal: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.request_id = request_id
        self.terminal = terminal

def _pack_host_python_executable() -> str:
    """Return the selected pack-host interpreter without resolving venv links."""
    configured = os.environ.get(PACK_HOST_PYTHON_ENV, "").strip()
    if not configured:
        return os.path.abspath(sys.executable)

    candidate = Path(configured).expanduser()
    if not candidate.is_absolute():
        raise PackHostBootstrapError(
            f"{PACK_HOST_PYTHON_ENV} must be an absolute path to an executable file"
        )
    try:
        executable = candidate.is_file() and os.access(candidate, os.X_OK)
    except (OSError, ValueError):
        executable = False
    if not executable:
        raise PackHostBootstrapError(
            f"{PACK_HOST_PYTHON_ENV} must be an absolute path to an executable file"
        )
    return os.path.abspath(str(candidate))

def _host_pid_alive(pid: Any) -> bool:
    try:
        value = int(pid)
    except (TypeError, ValueError):
        return False
    if value <= 0:
        return False
    try:
        os.kill(value, 0)
    except PermissionError:
        return True
    except OSError:
        return False
    # A terminated process can remain as a zombie until its original parent
    # reaps it.  It is not a live host and must not block a fresh launch.  The
    # Linux /proc probe is not available on macOS, which is the primary local
    # deployment, so keep a portable ``ps`` fallback as well.  Without this,
    # a host killed alongside an interrupted CLI remains recorded forever and
    # every next launch attempts to terminate the zombie until it times out.
    try:
        stat = Path(f"/proc/{value}/stat").read_text(encoding="ascii")
        state = stat.rsplit(")", 1)[-1].lstrip().split(None, 1)[0]
        if state == "Z":
            return False
    except (OSError, UnicodeDecodeError, IndexError):
        try:
            # ``subprocess.run`` is deliberately avoided here: the bootstrap
            # tests (and some embedded launchers) replace ``Popen`` while
            # modelling host startup.  The PID is already parsed as an int,
            # so this small, read-only ps probe has no shell-input surface.
            with os.popen(f"/bin/ps -p {value} -o state=", "r") as probe:
                state_text = probe.read()
            state = state_text.strip().split(None, 1)[0] if state_text.strip() else ""
            if state.upper().startswith("Z"):
                return False
        except (OSError, ValueError, IndexError):
            pass
    return True


def _host_command(pid: Any) -> str:
    try:
        value = int(pid)
    except (TypeError, ValueError):
        return ""
    try:
        return Path(f"/proc/{value}/cmdline").read_bytes().decode("utf-8", "ignore").replace("\x00", " ")
    except (OSError, ValueError):
        try:
            probe = subprocess.run(
                ["ps", "-p", str(value), "-o", "command="],
                capture_output=True, text=True, check=False, timeout=1.0,
            )
            return probe.stdout.strip()
        except (OSError, subprocess.SubprocessError, ValueError):
            return ""


def _our_host(pid: Any) -> bool:
    return _host_pid_alive(pid) and "astrid.core.execution.generic_host" in _host_command(pid)


def _host_birth_identity(pid: Any) -> str:
    """Return the OS birth token for *pid*, or an empty value if unavailable."""
    try:
        value = int(pid)
    except (TypeError, ValueError):
        return ""
    if value <= 0:
        return ""
    try:
        info = _process_snapshot().get(value)
    except Exception:
        info = None
    return str(info.birth) if info is not None else ""


def _host_identity_matches(state: Mapping[str, Any]) -> bool:
    """Verify PID, birth, process group, and every launch binding."""
    pid = state.get("pid")
    if not _host_pid_alive(pid):
        return False
    expected_birth = str(state.get("process_birth_id") or "")
    if not expected_birth or _host_birth_identity(pid) != expected_birth:
        return False
    if not _our_host(pid):
        return False
    try:
        if os.getpgid(int(pid)) != int(pid):
            return False
    except (OSError, TypeError, ValueError):
        return False
    command = _host_command(pid)
    required = (
        "astrid.core.execution.generic_host",
        str(state.get("ready_file") or ""),
        str(state.get("source_checkout") or ""),
        str(state.get("credential_file") or ""),
        str(state.get("support_root") or ""),
        str(state.get("endpoint") or ""),
        str(state.get("source_closure_digest") or ""),
        str(state.get("boot_manifest_path") or ""),
        str(state.get("boot_manifest_hash") or ""),
    )
    return all(value and value in command for value in required)


def _read_object(path: Path) -> Mapping[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, Mapping) else None


def _write_object(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    os.close(descriptor)
    temporary = Path(name)
    try:
        temporary.write_text(json.dumps(dict(value), sort_keys=True), encoding="utf-8")
        temporary.chmod(0o600)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _dependency_pythonpath() -> tuple[str, ...]:
    """Keep only approved dependency roots across the host boundary."""
    try:
        return dependency_pythonpath()
    except VibeComfyDependencyError as exc:
        raise PackHostBootstrapError(str(exc)) from exc


def _provision_render_runtime_env(
    source_path: Path, child_env: dict[str, str]
) -> None:
    """Bind a trusted local source profile to its concrete render toolchain.

    Packaged deployments may supply all three absolute settings themselves.
    For a source checkout, the launcher turns its installed Remotion bundle,
    schema package, and resolved Node executable into the same explicit
    settings before strict host preflight runs.
    """
    from astrid.core.env_vars import (
        ASTRID_NODE_EXECUTABLE,
        ASTRID_REMOTION_PROJECT_DIR,
        ASTRID_TIMELINE_SCHEMA_PYTHONPATH,
    )

    project_dir = (source_path / "remotion").resolve()
    if (
        ASTRID_REMOTION_PROJECT_DIR not in child_env
        and (project_dir / "package.json").is_file()
        and (project_dir / "node_modules").is_dir()
    ):
        child_env[ASTRID_REMOTION_PROJECT_DIR] = str(project_dir)

    schema_root = (
        project_dir
        / "node_modules"
        / "@banodoco"
        / "timeline-schema"
        / "python"
    )
    if (
        ASTRID_TIMELINE_SCHEMA_PYTHONPATH not in child_env
        and (schema_root / "banodoco_timeline_schema" / "__init__.py").is_file()
    ):
        child_env[ASTRID_TIMELINE_SCHEMA_PYTHONPATH] = str(schema_root.resolve())

    if ASTRID_NODE_EXECUTABLE not in child_env:
        node = shutil.which("node", path=child_env.get("PATH"))
        if node:
            child_env[ASTRID_NODE_EXECUTABLE] = str(Path(node).resolve())


def _descendant_snapshot(pid: int) -> dict[int, tuple[str, int]]:
    """Capture descendant birth/PGID pairs before stopping a host."""
    try:
        snapshot = _process_snapshot()
    except Exception:
        return {}
    descendants: dict[int, tuple[str, int]] = {}
    frontier = [pid]
    while frontier:
        parent = frontier.pop()
        for info in snapshot.values():
            if info.ppid == parent and info.pid not in descendants:
                descendants[info.pid] = (str(info.birth), int(info.pgid))
                frontier.append(info.pid)
    return descendants


def _terminate_descendants(members: Mapping[int, tuple[str, int]]) -> None:
    """Clean groups/children captured from the verified host, with birth checks."""
    if not members:
        return
    groups: dict[int, str] = {
        pgid: birth
        for pid, (birth, pgid) in members.items()
        if pid == pgid
    }
    for sig, seconds in ((signal.SIGTERM, 1.0), (signal.SIGKILL, 1.0)):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            snapshot = _process_snapshot()
            live = [
                (pid, birth)
                for pid, (birth, _pgid) in members.items()
                if (info := snapshot.get(pid)) is not None and info.birth == birth
            ]
            if not live:
                return
            # A child launched by GenericPackHost is a fresh session leader.
            # Kill its whole group only while that leader's birth token still
            # matches; otherwise fall back to exact individual members so a
            # reused PGID can never receive the signal.
            for pgid, birth in groups.items():
                leader = snapshot.get(pgid)
                if leader is not None and leader.birth == birth and leader.pgid == pgid:
                    try:
                        os.killpg(pgid, sig)
                    except OSError:
                        pass
            # Signal individual birth-verified processes whose group leader is
            # already gone, including late descendants observed by the group
            # signal above on the next census.
            for pid, _birth in live:
                info = snapshot.get(pid)
                if info is not None and info.pgid in groups:
                    leader = snapshot.get(info.pgid)
                    if leader is not None and leader.birth == groups[info.pgid]:
                        continue
                try:
                    os.kill(pid, sig)
                except OSError:
                    pass
            time.sleep(0.03)


def _terminate_old_host(state: Mapping[str, Any]) -> None:
    """TERM, bounded wait, then KILL one exact prior host and its children."""
    pid_value = state.get("pid")
    try:
        pid = int(pid_value)
    except (TypeError, ValueError):
        return
    if not _host_pid_alive(pid):
        return
    if not _host_identity_matches(state):
        raise PackHostBootstrapError(
            "existing generic Astrid host cannot be verified safely; remove its stale marker and retry"
        )
    members = _descendant_snapshot(pid)

    def signal_verified(sig: int) -> None:
        if not _host_pid_alive(pid):
            return
        if not _host_identity_matches(state):
            return
        try:
            if os.getpgid(pid) == pid and hasattr(os, "killpg"):
                os.killpg(pid, sig)
            else:
                os.kill(pid, sig)
        except OSError:
            pass

    signal_verified(signal.SIGTERM)
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline and _host_pid_alive(pid):
        time.sleep(0.05)
    if _host_pid_alive(pid):
        signal_verified(signal.SIGKILL)
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and _host_pid_alive(pid):
            time.sleep(0.05)
    _terminate_descendants(members)
    if _host_pid_alive(pid):
        raise PackHostBootstrapError("prior generic Astrid host did not terminate")


@dataclass
class _ParkedPackHostHandle:
    process: Any
    control: socket.socket
    reference: Any
    state: dict[str, Any]
    operation_id: str
    channel_id: str
    grant_sent: bool = False


class _ParkedPackHostPreparer:
    """Private local adapter for Runtime's existing launch projection/callback.

    The projection is injected by the resident owner; this module imports no
    Runtime store or authority implementation. Preparation returns while the
    canonical host is parked, before registration or credential consumption.
    """

    def __init__(self, project_launch: Callable, *, timeout_seconds: float = 20) -> None:
        if not 0 < timeout_seconds <= 900:
            raise ValueError("parked host timeout is invalid")
        self.project_launch = project_launch
        self.timeout_seconds = timeout_seconds
        self._handle: _ParkedPackHostHandle | None = None

    def prepare(self, reference: Any) -> _ParkedPackHostHandle:
        if self._handle is not None:
            raise PackHostBootstrapError("parked host preparer already owns an incarnation")
        if (reference.effective_target != {"kind": "machine", "id": socket.gethostname()}
                or reference.source_checkout is None or not reference.pack_roots
                or reference.ready_file is None or Path(reference.ready_file).exists()):
            raise PackHostBootstrapError("explicit local source, placement and fresh ready path are required")
        launch = self.project_launch(reference)
        if launch.deployment_digest != reference.digest():
            raise PackHostBootstrapError("parked host launch projection is foreign")
        parent, child = socket.socketpair()
        operation_id, channel_id = secrets.token_hex(16), secrets.token_hex(16)
        argv = [*launch.argv,
                "--source-closure-digest", reference.source_closure_digest.removeprefix("sha256:"),
                "--activation-fd", str(child.fileno()),
                "--activation-operation-id", operation_id,
                "--activation-channel-id", channel_id,
                "--activation-timeout-seconds", str(self.timeout_seconds)]
        from astrid.core.subprocess_env import build_child_subprocess_env

        projected_env = dict(launch.env())
        projected_env.pop("ASTRID_CREDENTIAL_REF", None)  # Already bound by --credential-file.
        projected_env.pop("ASTRID_IDEMPOTENCY_KEY", None)  # Admission remains with Runtime.
        env = build_child_subprocess_env(parent={}, explicit_env=projected_env)
        env.pop("ASTRID_PACKS_PATH", None)
        env["PYTHONPATH"] = os.pathsep.join((
            str(reference.source_checkout), *_dependency_pythonpath(),
            *(str(item.path) for item in reference.dependency_closure if item.path.is_dir()),
        ))
        env["PYTHONUNBUFFERED"] = "1"
        _provision_render_runtime_env(reference.source_checkout, env)
        log_path = Path(reference.support_root) / f"generic-host-{operation_id}.log"
        try:
            with log_path.open("xb") as log:
                os.fchmod(log.fileno(), 0o600)
                process = popen_owned_group(
                    argv, cwd=str(launch.cwd), env=env, stdin=subprocess.DEVNULL,
                    stdout=log, stderr=log, close_fds=True, pass_fds=(child.fileno(),),
                )
        except Exception:
            parent.close()
            raise
        finally:
            child.close()
        state = {
            "pid": process.pid, "process_birth_id": _host_birth_identity(process.pid),
            "ready_file": str(reference.ready_file), "source_checkout": str(reference.source_checkout),
            "credential_file": reference.credential_ref.removeprefix("file:"),
            "support_root": str(reference.support_root), "endpoint": reference.runtime_endpoint,
            "source_closure_digest": reference.source_closure_digest.removeprefix("sha256:"),
            "boot_manifest_path": str(reference.boot_manifest_path),
            "boot_manifest_hash": reference.boot_manifest_hash.removeprefix("sha256:"),
        }
        handle = _ParkedPackHostHandle(process, parent, reference, state, operation_id, channel_id)
        self._handle = handle
        if not state["process_birth_id"]:
            parent.close()
            terminate_group(process)
            raise PackHostBootstrapError("parked host birth identity could not be captured")
        return handle

    def _assert_owned(self, handle: object) -> _ParkedPackHostHandle:
        if not isinstance(handle, _ParkedPackHostHandle) or handle is not self._handle:
            raise PackHostBootstrapError("parked host handle is foreign")
        return handle

    def acknowledge(self, handle: object, grant: Mapping[str, Any], *, accept: Callable) -> Mapping[str, Any]:
        from astrid.core.execution.generic_host import (
            _ACTIVATION_VERSION, _ACTIVATION_RECEIPT_MODE, _ACTIVATION_REQUEST_VERSION,
            _ACTIVATION_RECEIPT_VERSION, _ACTIVATION_ACCEPTED_VERSION,
            _read_activation_frame, _send_activation_frame,
        )
        handle = self._assert_owned(handle)
        if handle.grant_sent or handle.process.poll() is not None or not _host_identity_matches(handle.state):
            raise PackHostBootstrapError("parked host grant is consumed or its incarnation changed")
        if (set(grant) != {"activation_id", "credential_file", "executor_incarnation", "evidence_digest"}
                or grant["credential_file"] != handle.state["credential_file"]):
            raise PackHostBootstrapError("parked host grant is foreign")
        credential_path = Path(grant["credential_file"])
        if (credential_path.is_symlink() or not credential_path.is_file()
                or credential_path.stat().st_mode & 0o777 != 0o600):
            raise PackHostBootstrapError("parked host credential reference is unsafe")
        process = {"pid": handle.process.pid, "birth_id": handle.state["process_birth_id"]}
        # Freeze the exact grant before delivery; callback code cannot mutate
        # the expected request/receipt by retaining an alias to this mapping.
        frozen_grant = json.loads(json.dumps(dict(grant)))
        request = {"version": _ACTIVATION_REQUEST_VERSION, "operation_id": handle.operation_id,
                   "channel_id": handle.channel_id, "grant": frozen_grant, "host": process}
        handle.grant_sent = True  # A transport exception never permits resend.
        try:
            handle.control.settimeout(self.timeout_seconds)
            _send_activation_frame(handle.control, {
                **frozen_grant, "version": _ACTIVATION_VERSION,
                "acceptance_mode": _ACTIVATION_RECEIPT_MODE,
                "operation_id": handle.operation_id, "channel_id": handle.channel_id, "host": process,
            })
            received = _read_activation_frame(handle.control)
            if received != request or not _host_identity_matches(handle.state):
                raise PackHostBootstrapError("parked host acceptance is foreign or stale")
            # This is the sole owner-process callback invocation. Its return
            # means Runtime committed acceptance; no owner capability crosses
            # the process boundary, and no ACK substitutes for this receipt.
            accept(received["grant"], received["host"])
            _send_activation_frame(handle.control, {**request, "version": _ACTIVATION_RECEIPT_VERSION})
            handle.control.shutdown(socket.SHUT_WR)
            acknowledgement = _read_activation_frame(handle.control)
            expected = {
                "version": _ACTIVATION_ACCEPTED_VERSION, "operation_id": handle.operation_id,
                "channel_id": handle.channel_id, "activation_id": frozen_grant["activation_id"],
                "executor_incarnation": frozen_grant["executor_incarnation"],
                "evidence_digest": frozen_grant["evidence_digest"], "host": process,
            }
            if acknowledgement != expected or handle.control.recv(1):
                raise PackHostBootstrapError("parked host final acknowledgement is invalid")
            return {key: frozen_grant[key] for key in (
                "activation_id", "executor_incarnation", "evidence_digest",
            )}
        finally:
            handle.control.close()

    def await_ready(self, handle: object) -> None:
        from astrid.core.execution.generic_host import RuntimeProtocolClient, _await_enabled_runtime_credential

        handle = self._assert_owned(handle)
        reference = handle.reference
        client = RuntimeProtocolClient(reference.runtime_endpoint, Path(handle.state["credential_file"]).read_text().strip())
        _await_enabled_runtime_credential(client, executor_id=reference.executor_id,
                                          timeout_seconds=self.timeout_seconds)
        deadline = time.monotonic() + self.timeout_seconds
        while time.monotonic() < deadline:
            ready = _read_object(Path(reference.ready_file))
            if ready and ready.get("status") == "ready":
                if (not _host_identity_matches(handle.state)
                        or ready.get("pid") != handle.process.pid
                        or ready.get("process_birth_id") != handle.state["process_birth_id"]
                        or ready.get("executor_id") != reference.executor_id
                        or any(ready.get(key) != value for key, value in handle.state.items()
                               if key not in {"pid", "process_birth_id"})):
                    raise PackHostBootstrapError("parked host readiness is foreign")
                return
            if handle.process.poll() is not None or (ready and ready.get("status") == "failed"):
                break
            time.sleep(0.02)
        raise PackHostBootstrapError("parked host did not become ready")

    def abort(self, handle: object) -> None:
        handle = self._assert_owned(handle)
        handle.control.close()
        if handle.process.poll() is None:
            _terminate_old_host(handle.state)
        terminate_group(handle.process)
        handle.process.wait(timeout=1)
        ready_path = Path(handle.state["ready_file"])
        ready = _read_object(ready_path)
        if ready and (ready.get("pid"), ready.get("process_birth_id")) == (
                handle.process.pid, handle.state["process_birth_id"]):
            ready_path.unlink()


def _host_artifact_digest(path: Path) -> str:
    if (not path.exists() or path.is_symlink()
            or (path.is_dir() and any(item.is_symlink() for item in path.rglob("*")))):
        raise PackHostBootstrapError("parked host artifact path is aliased")
    from astrid.core.execution.generic_host import _source_digest
    return "sha256:" + _source_digest(path).removeprefix("sha256:")


class _ParkedPackHostInspector:
    """Independent local OS/artifact witness; never trusts the ready marker."""

    def __init__(self, runtime_health: Callable, *, session_config_path: Path) -> None:
        self.runtime_health = runtime_health
        self.session_config_path = session_config_path

    def observe(self, handle: object) -> Mapping[str, Any]:
        from astrid.core.execution.generic_host import _canonical_digest, source_checkout_closure_digest
        from astrid.core.generation.model_root import model_root_binding_from_profile

        if not isinstance(handle, _ParkedPackHostHandle) or not _host_identity_matches(handle.state):
            raise PackHostBootstrapError("parked host OS identity changed")
        reference = handle.reference
        info = _process_snapshot().get(handle.process.pid)
        command = shlex.split(_host_command(handle.process.pid))
        uid = subprocess.run(["ps", "-p", str(handle.process.pid), "-o", "uid="],
                             capture_output=True, text=True, check=True, timeout=1).stdout.strip()
        if (info is None or info.birth != handle.state["process_birth_id"]
                or not command or command[0] != str(reference.executable.path)
                or int(uid) != os.getuid()):
            raise PackHostBootstrapError("parked host executable, uid or birth identity changed")
        capacity = int(command[command.index("--max-concurrency") + 1])
        if capacity != reference.capacity:
            raise PackHostBootstrapError("parked host capacity changed")
        profile_path = Path(reference.readiness_profile_path)
        if ("sha256:" + hashlib.sha256(profile_path.read_bytes()).hexdigest()) != reference.readiness_profile_hash:
            raise PackHostBootstrapError("parked host readiness profile changed")
        profile = _read_object(profile_path)
        binding = model_root_binding_from_profile(profile, verify_files=True)
        session = _read_object(self.session_config_path)
        if binding.path != reference.model_root or not session or session.get("session_ref") != reference.session_ref:
            raise PackHostBootstrapError("parked host model root or session changed")
        dependencies = []
        for item in reference.dependency_closure:
            digest = _host_artifact_digest(item.path)
            if digest != item.digest:
                raise PackHostBootstrapError("parked host dependency changed")
            dependencies.append({"name": item.name, "path": str(item.path), "digest": digest})
        health = self.runtime_health()
        def field(name):
            return health.get(name) if isinstance(health, Mapping) else getattr(health, name, None)
        return {
            "target": {"kind": "machine", "id": socket.gethostname()},
            "machine_identity": {"id": socket.gethostname(), "uid": int(uid)},
            "process": {"pid": info.pid, "birth_id": info.birth, "pgid": info.pgid,
                        "sid": os.getsid(info.pid), "uid": int(uid), "executable": command[0],
                        "artifact_digest": _host_artifact_digest(Path(command[0]))},
            "runtime_instance_id": field("runtime_instance_id"), "runtime_epoch": field("runtime_epoch"),
            "runtime_session_id": field("runtime_session_id"),
            "source_closure_digest": "sha256:" + source_checkout_closure_digest(reference.source_checkout),
            "dependency_closure_digest": "sha256:" + _canonical_digest(dependencies),
            "model_root": str(binding.path), "model_inventory_digest": binding.inventory_digest,
            "session_ref": session["session_ref"],
            "session_config_digest": "sha256:" + hashlib.sha256(self.session_config_path.read_bytes()).hexdigest(),
            "data_root": str(Path(reference.data_root).resolve(strict=True)),
            "support_root": str(Path(reference.support_root).resolve(strict=True)),
            "capacity": capacity,
        }


def ensure_pack_host(value: Mapping[str, Any], *, reconfigure_action: str) -> Mapping[str, Any]:
    """Ensure the runtime-issued pack host is registered and preflight-ready."""
    worker_file = value.get("worker_credential_file")
    source_checkout = value.get("source_checkout")
    if not worker_file or not source_checkout:
        # Tiny fake launcher boundaries intentionally model only the runtime
        # handoff.  Real neutral-runtime results contain both fields.
        return {}
    from astrid.sdk.workspace_client import _safe_local_path

    try:
        worker_path = _safe_local_path(str(worker_file), field="worker credential")
        source_path = _safe_local_path(str(source_checkout), field="source checkout")
    except Exception as exc:
        raise PackHostBootstrapError(f"generic Astrid pack host handoff is unsafe; {reconfigure_action}") from exc
    if (not worker_path.is_file() or worker_path.is_symlink()
            or not source_path.is_dir() or source_path.is_symlink()
            or worker_path.stat().st_mode & 0o777 != 0o600):
        raise PackHostBootstrapError(f"generic Astrid pack host handoff is unavailable; {reconfigure_action}")
    host_python = _pack_host_python_executable()
    try:
        from astrid.core.pack.source_setup import active_source_inventory

        managed_inventory = active_source_inventory()
    except Exception as exc:
        raise PackHostBootstrapError(
            f"managed source inventory could not be verified; {reconfigure_action}"
        ) from exc
    inventory_identity = managed_inventory.identity if managed_inventory.sources else ""
    pack_root = source_path / "astrid" / "packs"
    if not pack_root.is_dir() or pack_root.is_symlink():
        raise PackHostBootstrapError(f"Astrid source checkout has no pack root; {reconfigure_action}")
    scopes = tuple(str(scope) for scope in (value.get("worker_scopes") or ()))
    if str(value.get("worker_actor")) != PACK_HOST_ACTOR or scopes != PACK_HOST_SCOPES:
        raise PackHostBootstrapError(f"runtime worker credential is not the least-privilege pack-host contract; {reconfigure_action}")

    # Bind the process to the exact source and runtime instance it registered
    # against.  The health read is intentionally performed with the worker
    # credential, never the owner credential or an ambient environment token.
    from astrid.core.execution.generic_host import (
        RuntimeProtocolClient,
        source_checkout_closure_digest,
        source_checkout_digest,
    )

    try:
        source_digest = source_checkout_digest(source_path)
        source_closure_digest = source_checkout_closure_digest(source_path)
    except (OSError, ValueError) as exc:
        raise PackHostBootstrapError(
            f"generic Astrid pack source tree is not a safe checkout; {reconfigure_action}"
        ) from exc
    try:
        worker_token = worker_path.read_text(encoding="utf-8").strip()
        if not worker_token:
            raise ValueError("worker credential is empty")
        runtime_client = RuntimeProtocolClient(str(value["endpoint"]).rstrip("/"), worker_token)
        health = runtime_client.health()
    except Exception as exc:
        raise PackHostBootstrapError(
            f"generic Astrid pack host could not verify runtime identity; {reconfigure_action}"
        ) from exc
    finally:
        worker_token = ""
    health_value = dict(health) if isinstance(health, Mapping) else {
        "status": getattr(health, "status", None),
        "runtime_epoch": getattr(health, "runtime_epoch", None),
        "schema_digest": getattr(health, "schema_digest", None),
        "runtime_instance_id": getattr(health, "runtime_instance_id", None),
        "coordinator_epoch": getattr(health, "coordinator_epoch", None),
    }
    if str(health_value.get("status", "")) != "ok":
        raise PackHostBootstrapError(
            f"generic Astrid pack host runtime is not healthy; {reconfigure_action}",
            code="runtime_not_ready",
            terminal=True,
        )
    runtime_epoch = health_value.get("runtime_epoch", value.get("runtime_epoch"))
    runtime_instance_id = (
        value.get("runtime_instance_id")
        or health_value.get("runtime_instance_id")
        or value.get("coordinator_epoch")
        or health_value.get("coordinator_epoch")
        or (f"epoch:{runtime_epoch}" if runtime_epoch is not None else None)
    )
    schema_digest = health_value.get("schema_digest") or value.get("schema_digest")
    if runtime_epoch is None or runtime_instance_id is None:
        raise PackHostBootstrapError(
            f"generic Astrid pack host runtime identity is incomplete; {reconfigure_action}"
        )

    runtime_support = worker_path.parent.parent
    host_root = runtime_support / "astrid-host"
    boot_manifest_path = host_root / "boot-manifest.json"
    from astrid.core.gateway.dispatch import compose_profile_handoff
    try:
        boot_handoff = compose_profile_handoff(
            boot_manifest_path, support_root=runtime_support
        )
    except Exception as exc:
        raise PackHostBootstrapError(
            f"generic Astrid pack boot manifest could not be composed; {reconfigure_action}"
        ) from exc
    boot_manifest_hash = str(boot_handoff["sha256"])
    state_path = runtime_support / "generic-host.json"
    ready_path = runtime_support / "generic-host.ready.json"
    lock_path = runtime_support / "generic-host.lock"
    endpoint = str(value["endpoint"]).rstrip("/")
    try:
        lock_handle = lock_path.open("a+")
        os.fchmod(lock_handle.fileno(), 0o600)
    except OSError as exc:
        raise PackHostBootstrapError(f"generic Astrid pack host lock is unavailable; {reconfigure_action}") from exc
    try:
        try:
            import fcntl
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
        except (ImportError, OSError) as exc:
            raise PackHostBootstrapError(f"generic Astrid pack host lock is unavailable; {reconfigure_action}") from exc
        current = _read_object(state_path)
        ready = _read_object(ready_path)
        expected = {
            # Preserve the venv executable path: resolving its symlink would
            # collapse different dependency environments to the base Python.
            "python_executable": host_python,
            "endpoint": endpoint,
            "executor_id": PACK_HOST_ACTOR,
            "ready_file": str(ready_path),
            "credential_file": str(worker_path),
            "support_root": str(runtime_support),
            "source_checkout": str(source_path),
            "source_checkout_digest": source_digest,
            "source_closure_digest": source_closure_digest,
            "source_inventory_identity": inventory_identity,
            "runtime_instance_id": str(runtime_instance_id),
            "runtime_epoch": runtime_epoch,
            "schema_digest": schema_digest,
            "boot_manifest_path": str(boot_manifest_path),
            "boot_manifest_hash": boot_manifest_hash,
        }
        if (current and ready
                and all(current.get(key) == expected_value for key, expected_value in expected.items())
                and _host_identity_matches(current)
                and str(ready.get("status")) == "ready"
                and all(ready.get(key) == expected_value for key, expected_value in expected.items())
                and str(ready.get("pid")) == str(current.get("pid"))
                and str(ready.get("process_birth_id")) == str(current.get("process_birth_id"))):
            return {
                "host_status": "ready",
                "host_pid": int(current["pid"]),
                "host_executor_id": PACK_HOST_ACTOR,
                "host_ready_file": str(ready_path),
                "host_ready_capabilities": list(ready.get("ready_capabilities", [])),
                "host_runtime_instance_id": str(runtime_instance_id),
                "host_runtime_epoch": runtime_epoch,
                "host_source_checkout_digest": source_digest,
                "host_source_closure_digest": source_closure_digest,
                "host_source_inventory_identity": inventory_identity,
                "host_boot_manifest_path": str(boot_manifest_path),
                "host_boot_manifest_hash": boot_manifest_hash,
            }
        if current:
            # Reconfiguration is not cancellation. In particular, a status
            # command from another interpreter or after a documentation edit
            # must not terminate a render already owned by this host.
            if _host_identity_matches(current) and _descendant_snapshot(int(current["pid"])):
                raise PackHostBootstrapError(
                    "generic Astrid pack host is busy with active child processes; "
                    "reconfiguration is deferred until its work finishes"
                )
            _terminate_old_host(current)
        ready_path.unlink(missing_ok=True)
        log_path = runtime_support / "generic-host.log"
        matrix = source_path / "config" / "astrid-beta-capabilities.json"
        argv = [
            host_python,
            "-m", "astrid.core.execution.generic_host", "run",
            "--pack-root", str(pack_root),
            "--runtime-endpoint", endpoint,
            "--credential-file", str(worker_path),
            "--executor-id", PACK_HOST_ACTOR,
            "--ready-file", str(ready_path),
            "--support-root", str(runtime_support),
            "--source-checkout", str(source_path),
            "--source-checkout-digest", source_digest,
            "--source-closure-digest", source_closure_digest,
            "--runtime-instance-id", str(runtime_instance_id),
            "--register",
            "--source-inventory-identity", inventory_identity,
            "--boot-manifest-path", str(boot_manifest_path),
            "--boot-manifest-hash", boot_manifest_hash,
        ]
        for managed_root in managed_inventory.roots:
            argv.extend(("--pack-root", str(managed_root)))
        if matrix.is_file():
            argv.extend(("--capability-matrix", str(matrix)))
        child_env = dict(os.environ)
        # The selected source profile is the complete pack-discovery fence;
        # ambient pack roots/PYTHONPATH entries must not silently add another
        # checkout to this host.
        child_env.pop("ASTRID_PACKS_PATH", None)
        child_env["PYTHONPATH"] = os.pathsep.join(
            (str(source_path), *_dependency_pythonpath())
        )
        _provision_render_runtime_env(source_path, child_env)
        try:
            log = log_path.open("ab")
            process = subprocess.Popen(
                argv,
                cwd=str(source_path),
                env=child_env,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=log,
                start_new_session=True,
                close_fds=True,
            )
        except OSError as exc:
            raise PackHostBootstrapError(f"generic Astrid pack host could not be started; {reconfigure_action}") from exc
        finally:
            try:
                log.close()
            except UnboundLocalError:
                pass
        process_state = {
            **expected,
            "version": 2,
            "pid": process.pid,
            "process_birth_id": _host_birth_identity(process.pid),
        }
        if not process_state["process_birth_id"]:
            _terminate_old_host(process_state)
            raise PackHostBootstrapError(f"generic Astrid pack host identity could not be captured; {reconfigure_action}")
        deadline = time.monotonic() + 20.0
        ready = None
        terminal_failure = None
        while time.monotonic() < deadline:
            ready = _read_object(ready_path)
            if ready and str(ready.get("status")) == "ready":
                break
            if (
                ready
                and str(ready.get("status")) == "failed"
                and ready.get("terminal") is True
                and str(ready.get("pid")) == str(process.pid)
                and str(ready.get("process_birth_id"))
                == str(process_state["process_birth_id"])
            ):
                terminal_failure = ready
                break
            if process.poll() is not None:
                break
            time.sleep(0.05)
        if terminal_failure is not None:
            _terminate_old_host(process_state)
            error = terminal_failure.get("error")
            diagnostic = error if isinstance(error, Mapping) else {}
            code = str(diagnostic.get("code") or "host_registration_failed")[:128]
            request_id = str(diagnostic.get("request_id") or "")[:128]
            message = str(
                diagnostic.get("message") or "generic Astrid pack host registration failed"
            )[:512]
            suffix = f" [request_id={request_id}]" if request_id else ""
            raise PackHostBootstrapError(
                message + suffix,
                code=code,
                request_id=request_id,
                terminal=True,
            )
        if (not ready or ready.get("status") != "ready"
                or str(ready.get("pid")) != str(process.pid)
                or str(ready.get("process_birth_id")) != str(process_state["process_birth_id"])
                or not all(ready.get(key) == expected_value for key, expected_value in expected.items())
                or process.poll() is not None):
            _terminate_old_host(process_state)
            raise PackHostBootstrapError(f"generic Astrid pack host did not become ready; inspect {log_path}")
        _write_object(state_path, process_state)
        return {
            "host_status": "ready",
            "host_pid": process.pid,
            "host_executor_id": PACK_HOST_ACTOR,
            "host_ready_file": str(ready_path),
            "host_ready_capabilities": list(ready.get("ready_capabilities", [])),
            "host_runtime_instance_id": str(runtime_instance_id),
            "host_runtime_epoch": runtime_epoch,
            "host_source_checkout_digest": source_digest,
            "host_source_closure_digest": source_closure_digest,
            "host_source_inventory_identity": inventory_identity,
            "host_boot_manifest_path": str(boot_manifest_path),
            "host_boot_manifest_hash": boot_manifest_hash,
        }
    finally:
        try:
            import fcntl
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
        except (ImportError, OSError):
            pass
        lock_handle.close()


__all__ = ["PACK_HOST_ACTOR", "PACK_HOST_SCOPES", "PackHostBootstrapError", "ensure_pack_host"]
