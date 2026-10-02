"""SSH transport for the one already-claimed H3 RunPod host.

The control SSH channel only carries a grant to GenericHost's inherited
activation socket. The separate inspection channel reads provider and OS state.
Neither channel creates a Runtime task or a provider claim.
"""

from __future__ import annotations

import json
import re
import shlex
import subprocess
import tarfile
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from .runpod_deployment import DeploymentOperationError


RELEASE = "/workspace/h3-golden/releases/h3-cu130-v1-candidate"
_BRIDGE = r'''
import json, os, select, signal, socket, subprocess, sys, time
spec = json.loads(sys.argv[1])
parent, child = socket.socketpair()
argv = spec['argv'] + [
    '--activation-fd', str(child.fileno()),
    '--activation-operation-id', spec['operation_id'],
    '--activation-channel-id', spec['channel_id'],
    '--activation-timeout-seconds', '900',
]
with open(spec['log'], 'ab', buffering=0) as log:
    process = subprocess.Popen(
        argv, cwd=spec['cwd'], env={**os.environ, **spec['env']},
        stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
        pass_fds=(child.fileno(),), start_new_session=True,
    )
child.close()
def control_line(timeout):
    deadline = time.monotonic() + timeout
    while True:
        if process.poll() is not None:
            raise RuntimeError('parked GenericHost exited before activation')
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError('activation control channel timed out')
        if select.select([sys.stdin], [], [], min(remaining, 1))[0]:
            break
    line = sys.stdin.readline()
    if not line:
        raise EOFError('activation control channel closed')
    return line
try:
    birth = subprocess.check_output(
        ['/bin/ps', '-p', str(process.pid), '-o', 'lstart='], text=True,
    ).strip()
    if os.getpgid(process.pid) != process.pid or os.getsid(process.pid) != process.pid:
        raise RuntimeError('parked process does not own its process group')
    identity = {'pid': process.pid, 'birth_id': birth,
                'pgid': process.pid, 'sid': process.pid}
    print(json.dumps(identity), flush=True)
    line = control_line(900)
    grant = json.loads(line)
    if (grant.get('version') != 'runtime.local-worker-activation/v1'
            or grant.get('operation_id') != spec['operation_id']
            or grant.get('channel_id') != spec['channel_id']
            or grant.get('host') != {'pid': process.pid, 'birth_id': birth}):
        raise ValueError('activation grant does not match parked host')
    parent.settimeout(30)
    parent.sendall(line.encode())
    accepted = bytearray()
    while b'\n' not in accepted and len(accepted) < 8192:
        block = parent.recv(8192 - len(accepted))
        if not block:
            raise EOFError('GenericHost closed activation socket')
        accepted.extend(block)
    expected = {'version': 'astrid.local-worker-activation-accepted/v1',
                'operation_id': grant['operation_id'],
                'channel_id': grant['channel_id'],
                'executor_incarnation': grant['executor_incarnation'],
                'evidence_digest': grant['evidence_digest'],
                'host': grant['host']}
    if json.loads(accepted.split(b'\n', 1)[0]) != expected:
        raise ValueError('GenericHost acknowledgement is invalid')
    print(json.dumps(expected), flush=True)
    commit = json.loads(control_line(30))
    if commit != {'action': 'commit', 'pid': process.pid, 'birth_id': birth,
                  'pgid': process.pid, 'sid': process.pid}:
        raise ValueError('activation commit identity is invalid')
    print(json.dumps({'committed': True}), flush=True)
    while process.poll() is None:
        if select.select([sys.stdin], [], [], 1)[0]:
            line = sys.stdin.readline()
            if not line:
                break
            command = json.loads(line)
            if command != {'action': 'abort', **identity}:
                raise ValueError('abort identity is invalid')
            break
finally:
    parent.close()
    if process.poll() is None:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait(timeout=5)
    else:
        process.wait()
'''

_OBSERVE = r'''
import hashlib, json, os, subprocess, sys
pid, expected_birth, paths = int(sys.argv[1]), sys.argv[2], json.loads(sys.argv[3])
line = subprocess.check_output(
    ['/bin/ps', '-p', str(pid), '-o', 'pid=,ppid=,pgid=,sid=,lstart='],
    text=True,
).strip().split()
if len(line) < 9:
    raise SystemExit('process census is incomplete')
birth = ' '.join(line[4:])
if birth != expected_birth:
    raise SystemExit('process birth changed')
cmd = open(f'/proc/{pid}/cmdline', 'rb').read().split(b'\0')
argv = [part.decode() for part in cmd if part]
if argv[1:5] != ['-m', 'astrid.core.execution.generic_host', 'run', '--pack-root']:
    raise SystemExit('parked process is not GenericHost')
if argv[argv.index('--max-concurrency') + 1] != '2':
    raise SystemExit('parked host lacks two lanes')
env = dict(part.decode().split('=', 1) for part in
           open(f'/proc/{pid}/environ', 'rb').read().split(b'\0') if b'=' in part)
def sha(path):
    with open(path, 'rb') as stream:
        return 'sha256:' + hashlib.sha256(stream.read()).hexdigest()
checks = {path: sha(path) for path in paths}
print(json.dumps({
    'process': {'pid': pid, 'birth_id': birth, 'pgid': int(line[2]), 'sid': int(line[3])},
    'child': {'attached': True, 'birth_id': birth, 'lanes': ['orchestration', 'executor']},
    'env': {key: env.get(key) for key in (
        'ASTRID_RUNTIME_INSTANCE_ID', 'ASTRID_RUNTIME_EPOCH', 'ASTRID_RUNTIME_SESSION_ID',
        'ASTRID_SOURCE_CLOSURE_DIGEST', 'ASTRID_DEPENDENCY_CLOSURE_DIGEST',
        'ASTRID_MODEL_ROOT', 'ASTRID_SESSION_REF', 'ASTRID_DATA_ROOT',
        'ASTRID_SUPPORT_ROOT', 'ASTRID_SESSION_CONFIG_DIGEST',
        'ASTRID_EXECUTION_TARGET_JSON',
    )}, 'checks': checks,
}))
'''

_VERIFY_PROCESS = r'''
import subprocess, sys
pid = int(sys.argv[1])
result = subprocess.run(['/bin/ps', '-p', str(pid), '-o', 'pid=,pgid=,sid=,lstart='],
                        capture_output=True, text=True)
print(result.stdout.strip() if result.returncode == 0 else 'missing')
'''

_READ_READY = r'''
import json, pathlib, sys
path = pathlib.Path(sys.argv[1])
print(path.read_text() if path.is_file() else 'null')
'''


@dataclass
class RemoteHostHandle:
    claim: Mapping[str, Any]
    launch: Mapping[str, Any]
    pid: int
    birth_id: str
    ssh_client: Any
    stdin: Any
    stdout: Any
    tunnel: Any = None
    pgid: int | None = None
    sid: int | None = None
    stopped: bool = False


def _stop_tunnel(tunnel: Any) -> None:
    if tunnel is None or tunnel.poll() is not None:
        return
    tunnel.terminate()
    try:
        tunnel.wait(timeout=5)
    except subprocess.TimeoutExpired:
        tunnel.kill()
        tunnel.wait(timeout=5)


def _identity(claim: Mapping[str, Any]) -> None:
    if claim.get("schema_version") != "astrid.runpod.claim.v1":
        raise DeploymentOperationError("claim is not a verified claim-v1 handle")
    for field in ("pod_id", "network_volume_id", "ssh", "volume_mount_path"):
        if not isinstance(claim.get(field), str) or not claim[field].strip():
            raise DeploymentOperationError(f"claim is missing exact {field}")
    if re.fullmatch(r"root@[^ ]+ -p [0-9]+", claim["ssh"]) is None:
        raise DeploymentOperationError("claim SSH endpoint is not canonical")


def _ssh(claim: Mapping[str, Any]):
    _identity(claim)
    # Importing the old watcher would register its termination handler. Reuse
    # its Paramiko connection pattern without importing its task flow.
    import paramiko

    match = re.fullmatch(r"root@([^ ]+) -p ([0-9]+)", claim["ssh"])
    if match is None:
        raise DeploymentOperationError("claim SSH endpoint is not parseable")
    host, port = match.group(1), int(match.group(2))
    key = Path("/Users/peteromalley/.ssh/services/runpod_o0glkh8fjvogi6_ed25519")
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(host, port=port, username="root", key_filename=str(key),
                   look_for_keys=False, allow_agent=False, timeout=30)
    _, out, _ = client.exec_command("true", timeout=30)
    if out.channel.recv_exit_status() != 0:
        client.close()
        raise DeploymentOperationError("exact-pod SSH probe failed")
    return client


def _exec(client: Any, argv: list[str], *, timeout: int = 60) -> str:
    command = " ".join(shlex.quote(arg) for arg in argv)
    _stdin, stdout, stderr = client.exec_command(command, timeout=timeout)
    value = stdout.read().decode("utf-8")
    if stdout.channel.recv_exit_status() != 0:
        raise DeploymentOperationError("remote inspection failed: " + stderr.read().decode("utf-8")[-1000:])
    return value


def _provider(claim: Mapping[str, Any]) -> Mapping[str, Any]:
    _identity(claim)
    pod_id = claim["pod_id"]
    try:
        result = subprocess.run(["runpod-lifecycle", "status", pod_id],
                                capture_output=True, text=True, timeout=30, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        raise DeploymentOperationError("exact-pod provider status command failed") from exc
    if result.returncode != 0:
        raise DeploymentOperationError("exact-pod provider status is unavailable")
    try:
        value = json.loads(result.stdout)
    except (TypeError, json.JSONDecodeError) as exc:
        raise DeploymentOperationError("exact-pod provider status is malformed JSON") from exc
    if not isinstance(value, Mapping):
        raise DeploymentOperationError("exact-pod provider status is malformed")
    match = re.fullmatch(r"root@([^ ]+) -p ([0-9]+)", claim["ssh"])
    expected_host, expected_port = match.group(1), int(match.group(2))
    ports = value.get("ports")
    if not isinstance(ports, list) or any(not isinstance(item, Mapping) for item in ports):
        raise DeploymentOperationError("exact-pod provider ports are malformed")
    has_exact_ssh_mapping = any(
        item.get("ip") == expected_host
        and type(item.get("privatePort")) is int and item["privatePort"] == 22
        and type(item.get("publicPort")) is int and item["publicPort"] == expected_port
        and item.get("type") == "tcp"
        for item in ports
    )
    if (value.get("runpod_id") != pod_id
            or value.get("desired_status") != "RUNNING"
            or not has_exact_ssh_mapping):
        raise DeploymentOperationError("provider status does not match the claimed SSH pod")
    return value


class H3RemotePreparer:
    """Stage the pinned source and park GenericHost behind its private FD."""

    def __init__(self, *, ssh_factory: Callable = _ssh,
                 provider: Callable = _provider):
        self.ssh_factory = ssh_factory
        self.provider = provider

    def prepare(self, launch: Mapping[str, Any]) -> RemoteHostHandle:
        claim = launch["claim"]
        _identity(claim)
        self.provider(claim)
        client = self.ssh_factory(claim)
        tunnel = None
        bridge_stdin = None
        bridge_stdout = None
        try:
            source = Path(launch["local_source"])
            remote_source = launch["source_checkout"]
            with tempfile.NamedTemporaryFile(suffix=".tar") as archive:
                with tarfile.open(fileobj=archive, mode="w") as tar:
                    for name in ("astrid", "banodoco_workspace_client", "config", "pyproject.toml"):
                        path = source / name
                        if not path.exists():
                            raise DeploymentOperationError(f"source member missing: {name}")
                        tar.add(path, arcname=name, filter=lambda info: None if (
                            "/__pycache__/" in info.name or "/.pytest_cache/" in info.name
                            or info.name.endswith(".pyc")) else info)
                archive.flush()
                _exec(client, ["mkdir", "-p", remote_source, launch["support_root"], launch["output_root"]])
                with client.open_sftp() as sftp:
                    sftp.put(archive.name, launch["remote_tar"])
            _exec(client, ["tar", "-xf", launch["remote_tar"], "-C", remote_source])
            _exec(client, ["cp", "--", launch["manifest_source"], launch["manifest_target"]])
            # Remote files are checked before the parked process is accepted.
            for path, digest in launch["file_hashes"].items():
                observed = _exec(client, ["sha256sum", path]).split()[0]
                if "sha256:" + observed != digest:
                    raise DeploymentOperationError(f"remote release file drifted: {path}")
            match = re.fullmatch(r"root@([^ ]+) -p ([0-9]+)", claim["ssh"])
            if match is None:
                raise DeploymentOperationError("claim SSH endpoint is not parseable")
            tunnel = subprocess.Popen([
                "ssh", "-N", "-T", "-o", "ExitOnForwardFailure=yes",
                "-o", "ServerAliveInterval=30", "-i",
                "/Users/peteromalley/.ssh/services/runpod_o0glkh8fjvogi6_ed25519",
                "-p", match.group(2), "-R",
                f"127.0.0.1:50604:127.0.0.1:{launch['local_runtime_port']}",
                f"root@{match.group(1)}",
            ], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE, start_new_session=True)
            time.sleep(1)
            if tunnel.poll() is not None:
                raise DeploymentOperationError("Runtime reverse SSH tunnel failed")
            spec = {key: launch[key] for key in ("argv", "env", "cwd", "log", "operation_id", "channel_id")}
            command = " ".join(shlex.quote(part) for part in (
                "python3", "-u", "-c", _BRIDGE, json.dumps(spec, sort_keys=True),
            ))
            stdin, stdout, _stderr = client.exec_command(command, timeout=30)
            bridge_stdin, bridge_stdout = stdin, stdout
            first = json.loads(stdout.readline())
            pid, birth = first.get("pid"), first.get("birth_id")
            if (not isinstance(pid, int) or pid <= 0 or not isinstance(birth, str) or not birth
                    or first.get("pgid") != pid or first.get("sid") != pid):
                raise DeploymentOperationError("remote GenericHost did not park")
            return RemoteHostHandle(claim, launch, pid, birth, client, stdin, stdout,
                                    tunnel, pid, pid)
        except Exception:
            if bridge_stdin is not None:
                try:
                    bridge_stdin.close()
                    channel = getattr(bridge_stdout, "channel", None)
                    if channel is not None:
                        channel.settimeout(10)
                        channel.recv_exit_status()
                except Exception:
                    pass
            try:
                client.close()
            except Exception:
                pass
            try:
                _stop_tunnel(tunnel)
            except Exception:
                pass
            raise

    def acknowledge(self, handle: RemoteHostHandle, grant: Mapping[str, Any]) -> Mapping[str, Any]:
        try:
            local = Path(grant["credential_file"])
            if not local.is_file() or local.stat().st_mode & 0o077:
                raise DeploymentOperationError("resident Runtime credential file is unavailable or public")
            remote = handle.launch["credential_file"]
            with handle.ssh_client.open_sftp() as sftp:
                sftp.put(str(local), remote)
                sftp.chmod(remote, 0o600)
            frame = {
                "version": "runtime.local-worker-activation/v1",
                "operation_id": handle.launch["operation_id"],
                "channel_id": handle.launch["channel_id"],
                "credential_file": remote,
                "executor_incarnation": grant["executor_incarnation"],
                "evidence_digest": grant["evidence_digest"],
                "host": {"pid": handle.pid, "birth_id": handle.birth_id},
            }
            handle.stdin.write(json.dumps(frame, sort_keys=True) + "\n")
            handle.stdin.flush()
            accepted = json.loads(handle.stdout.readline())
            if accepted != {
                "version": "astrid.local-worker-activation-accepted/v1",
                "operation_id": frame["operation_id"], "channel_id": frame["channel_id"],
                "executor_incarnation": frame["executor_incarnation"],
                "evidence_digest": frame["evidence_digest"], "host": frame["host"],
            }:
                raise DeploymentOperationError("GenericHost private FD acknowledgement is invalid")
            handle.stdin.write(json.dumps({"action": "commit", "pid": handle.pid,
                                           "birth_id": handle.birth_id,
                                           "pgid": handle.pgid, "sid": handle.sid}) + "\n")
            handle.stdin.flush()
            if json.loads(handle.stdout.readline()) != {"committed": True}:
                raise DeploymentOperationError("GenericHost activation commit failed")
            return {key: grant[key] for key in ("activation_id", "executor_incarnation", "evidence_digest")}
        except BaseException:
            try:
                self.abort(handle)
            except Exception:
                pass
            raise

    def await_ready(self, handle: RemoteHostHandle, *, timeout_seconds: float = 300) -> None:
        """Wait for GenericHost's existing registration and capacity marker."""
        ready_path = handle.launch["support_root"] + "/generic-host.ready.json"
        deadline = time.monotonic() + timeout_seconds
        last_error: Exception | None = None
        while time.monotonic() < deadline:
            try:
                raw = _exec(handle.ssh_client, ["python3", "-c", _READ_READY, ready_path])
                marker = json.loads(raw)
                if marker is None:
                    time.sleep(0.25)
                    continue
                if (not isinstance(marker, Mapping) or marker.get("status") != "ready"
                        or marker.get("pid") != handle.pid
                        or marker.get("process_birth_id") != handle.birth_id
                        or not {"h3_av.transform", "vibecomfy.run"}.issubset(
                            marker.get("ready_capabilities", []))
                        or not isinstance(marker.get("registration"), Mapping)
                        or marker.get("effective_capacity", {}).get("max_concurrency") != 2
                        or marker.get("activation", {}).get("operation_id") != handle.launch["operation_id"]
                        or marker.get("activation", {}).get("channel_id") != handle.launch["channel_id"]):
                    raise DeploymentOperationError("GenericHost readiness or capacity acknowledgement is invalid")
                return
            except (DeploymentOperationError, ValueError, TypeError) as exc:
                last_error = exc
                break
        raise DeploymentOperationError("GenericHost did not become ready for the required H3 capabilities") from last_error

    def abort(self, handle: RemoteHostHandle) -> None:
        if handle.stopped:
            return
        failure: BaseException | None = None
        try:
            observed = _exec(handle.ssh_client, ["python3", "-c", _VERIFY_PROCESS, str(handle.pid)])
            if observed.strip() != "missing":
                fields = observed.strip().split()
                if (len(fields) != 8 or int(fields[0]) != handle.pid
                        or int(fields[1]) != handle.pgid or int(fields[2]) != handle.sid
                        or " ".join(fields[3:]) != handle.birth_id):
                    raise DeploymentOperationError("remote process identity changed; abort withheld")
                handle.stdin.write(json.dumps({"action": "abort", "pid": handle.pid,
                                               "birth_id": handle.birth_id,
                                               "pgid": handle.pgid, "sid": handle.sid}) + "\n")
                handle.stdin.flush()
        except BaseException as exc:
            failure = exc
        def await_bridge() -> None:
            channel = getattr(handle.stdout, "channel", None)
            if channel is not None:
                channel.settimeout(10)
                channel.recv_exit_status()

        for close in (handle.stdin.close, await_bridge, handle.stdout.close,
                      handle.ssh_client.close, lambda: _stop_tunnel(handle.tunnel)):
            try:
                close()
            except Exception as exc:
                if failure is None:
                    failure = exc
        handle.stopped = True
        if failure is not None:
            raise failure


class H3RemoteInspector:
    """Provider status plus a fresh SSH process and release census."""

    def __init__(self, *, ssh_factory: Callable = _ssh,
                 provider: Callable = _provider):
        self.ssh_factory = ssh_factory
        self.provider = provider

    def observe(self, handle: RemoteHostHandle) -> Mapping[str, Any]:
        self.provider(handle.claim)
        client = self.ssh_factory(handle.claim)
        try:
            raw = _exec(client, ["python3", "-c", _OBSERVE, str(handle.pid),
                                 handle.birth_id, json.dumps(handle.launch["file_hashes"])])
        finally:
            client.close()
        value = json.loads(raw)
        if value["checks"] != handle.launch["file_hashes"]:
            raise DeploymentOperationError("remote release, manifest or session file drifted")
        env = value["env"]
        target = handle.launch["target"]
        if json.loads(env["ASTRID_EXECUTION_TARGET_JSON"]) != target:
            raise DeploymentOperationError("parked process has a foreign target")
        return {
            "target": target,
            "provider_identity": {"account_ref": target["provider_account_ref"], "pod_id": handle.claim["pod_id"]},
            "process": value["process"], "child": value["child"],
            "runtime_instance_id": env["ASTRID_RUNTIME_INSTANCE_ID"],
            "runtime_epoch": int(env["ASTRID_RUNTIME_EPOCH"]),
            "runtime_session_id": env["ASTRID_RUNTIME_SESSION_ID"],
            "source_closure_digest": env["ASTRID_SOURCE_CLOSURE_DIGEST"],
            "dependency_closure_digest": env["ASTRID_DEPENDENCY_CLOSURE_DIGEST"],
            "model_root": env["ASTRID_MODEL_ROOT"],
            "session_ref": env["ASTRID_SESSION_REF"],
            "data_root": env["ASTRID_DATA_ROOT"],
            "support_root": env["ASTRID_SUPPORT_ROOT"],
            "capacity": 2,
            "model_inventory_digest": value["checks"][handle.launch["release_manifest_path"]],
            "session_config_digest": env["ASTRID_SESSION_CONFIG_DIGEST"],
        }
