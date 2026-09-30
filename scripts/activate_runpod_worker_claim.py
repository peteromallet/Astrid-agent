#!/usr/bin/env python3
"""Activate one already-running RunPod GenericHost for targeted Runtime work.

This is the narrow owner-side bridge for the current targeted Runtime contract.
It deliberately does not accept caller-authored execution_binding data: the
binding is derived only after checking the provider inventory and the exact
remote GenericHost process.  The resulting worker credential is issued by the
Runtime CredentialStore, then copied to the pod's container disk.

The long-term home for this bridge is the daemon-owned RunPod controller.  It
is kept as an explicit operator command while that controller is not deployed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import time
import uuid
from typing import Any, Mapping


SCOPES = [
    "handshake",
    "worker:register",
    "worker:execute",
    "tasks:read",
    "objects:read",
    "objects:write",
]


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value)).hexdigest()


def _json_value(stdout: str) -> Any:
    decoder = json.JSONDecoder()
    for index, char in enumerate(stdout):
        if char not in "[{":
            continue
        try:
            value, _ = decoder.raw_decode(stdout[index:])
        except json.JSONDecodeError:
            continue
        return value
    raise RuntimeError("provider inventory did not contain JSON")


def _provider_inventory() -> list[dict[str, Any]]:
    command = [
        sys.executable,
        "-m",
        "astrid.core.util.credential_exec",
        "--provider",
        "runpod",
        "--",
        "runpod-lifecycle",
        "list",
        "--json",
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=False, timeout=120)
    if result.returncode != 0:
        raise RuntimeError(f"RunPod inventory failed: {result.stderr[-1000:]}")
    value = _json_value(result.stdout)
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise RuntimeError("RunPod inventory has an invalid shape")
    return [dict(item) for item in value]


def _ssh_args(args: argparse.Namespace) -> list[str]:
    return [
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        "StrictHostKeyChecking=no",
        "-i",
        str(Path(args.ssh_key).expanduser()),
        "-p",
        str(args.ssh_port),
        f"root@{args.ssh_host}",
    ]


def _remote_probe(args: argparse.Namespace) -> dict[str, Any]:
    command = (
        "set -eu; "
        f"pid={int(args.host_pid)}; "
        "test -r /proc/$pid/cmdline; "
        "cmd=$(tr '\\0' ' ' </proc/$pid/cmdline); "
        "env=$(tr '\\0' '\\n' </proc/$pid/environ); "
        "printf '%s\\n' \"$cmd\"; "
        "printf '%s\\n' \"$env\" | grep '^ASTRID_EXECUTION_TARGET_JSON='; "
        f"test \"$cmd\" = *{shlex.quote(args.source_checkout)}*; "
        f"test \"$cmd\" = *{shlex.quote(args.ready_file)}*; "
        "test \"$cmd\" = *astrid.core.execution.generic_host*; "
        "test -r " + shlex.quote(args.ready_file) + "; "
        "printf '\\nREADY\\n'; "
        "cat " + shlex.quote(args.ready_file)
    )
    result = subprocess.run(
        _ssh_args(args) + ["--", "sh", "-lc", command],
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    if result.returncode != 0:
        raise RuntimeError(f"remote GenericHost identity check failed: {result.stderr[-1200:]}")
    if "\nREADY\n" not in result.stdout:
        raise RuntimeError("remote GenericHost readiness witness was not returned")
    return {
        "ssh_host": args.ssh_host,
        "ssh_port": int(args.ssh_port),
        "host_pid": int(args.host_pid),
        "stdout_sha256": hashlib.sha256(result.stdout.encode()).hexdigest(),
        "ready_file": args.ready_file,
    }


def _matching_port(pod: Mapping[str, Any], host: str, port: int) -> bool:
    for item in pod.get("ports", []) or []:
        if not isinstance(item, Mapping):
            continue
        if int(item.get("privatePort") or 0) == 22 and str(item.get("ip") or "") == host and int(item.get("publicPort") or 0) == port:
            return True
    return False


def _rotate_worker_credential(args: argparse.Namespace, evidence: Mapping[str, Any]) -> tuple[Path, dict[str, Any]]:
    runtime_checkout = Path(args.runtime_checkout).expanduser().resolve()
    sys.path.insert(0, str(runtime_checkout))
    from runtime_protocol.auth import CredentialStore  # type: ignore

    support = Path(args.runtime_support_root).expanduser().resolve()
    credentials = CredentialStore(support / "credentials")
    incarnation = f"runpod/{args.pod_id}/{uuid.uuid4().hex}"
    binding = {
        "actual": {
            "kind": "runpod",
            "pod_id": args.pod_id,
            "provider_account_ref": args.provider_account_ref,
        },
        "verification": {
            "method": "credential_claim",
            "verified": True,
            "evidence_digest": _digest(evidence),
        },
        "executor_incarnation": incarnation,
    }
    receipt = {
        "version": 1,
        "kind": "astrid.runpod.worker-activation.v1",
        "pod_id": args.pod_id,
        "provider_account_ref": args.provider_account_ref,
        "runtime_instance_id": args.runtime_instance_id,
        "runtime_epoch": int(args.runtime_epoch),
        "activated_at": time.time(),
        "remote": dict(evidence["remote"]),
        "provider": dict(evidence["provider"]),
        "binding": binding,
    }
    token, token_path = credentials.provision(
        "astrid-pack-host",
        SCOPES,
        metadata={"execution_binding": binding, "runpod_activation_receipt": receipt},
        rotate=True,
        enabled=True,
    )
    remote_token = Path(args.remote_token).expanduser()
    result = subprocess.run(
        [
            "scp",
            "-q",
            "-o",
            "BatchMode=yes",
            "-o",
            "StrictHostKeyChecking=no",
            "-i",
            str(Path(args.ssh_key).expanduser()),
            "-P",
            str(args.ssh_port),
            str(token_path),
            f"root@{args.ssh_host}:{remote_token}",
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    if result.returncode != 0:
        raise RuntimeError(f"worker credential delivery failed: {result.stderr[-1000:]}")
    chmod = subprocess.run(
        _ssh_args(args) + ["--", "chmod", "600", str(remote_token)],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    if chmod.returncode != 0:
        raise RuntimeError(f"remote worker credential permission check failed: {chmod.stderr[-1000:]}")
    receipt_path = support / f"runpod-worker-activation-{args.pod_id}-{incarnation.rsplit('/', 1)[-1]}.json"
    receipt_path.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    receipt_path.chmod(0o600)
    return token_path, receipt


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pod-id", required=True)
    parser.add_argument("--provider-account-ref", required=True)
    parser.add_argument("--ssh-host", required=True)
    parser.add_argument("--ssh-port", required=True, type=int)
    parser.add_argument("--ssh-key", required=True)
    parser.add_argument("--host-pid", required=True, type=int)
    parser.add_argument("--source-checkout", required=True)
    parser.add_argument("--ready-file", required=True)
    parser.add_argument("--runtime-support-root", required=True)
    parser.add_argument("--runtime-checkout", required=True)
    parser.add_argument("--runtime-instance-id", required=True)
    parser.add_argument("--runtime-epoch", required=True, type=int)
    parser.add_argument("--remote-token", default="/tmp/astrid-pack-host.token")
    args = parser.parse_args()

    pods = _provider_inventory()
    matches = [pod for pod in pods if str(pod.get("id") or "") == args.pod_id]
    if len(matches) != 1:
        raise RuntimeError(f"expected exactly one provider record for pod {args.pod_id}")
    pod = matches[0]
    if str(pod.get("desired_status") or "") != "RUNNING":
        raise RuntimeError(f"pod {args.pod_id} is not RUNNING")
    if not _matching_port(pod, args.ssh_host, args.ssh_port):
        raise RuntimeError("SSH endpoint does not match the provider's exact pod record")

    remote = _remote_probe(args)
    evidence = {
        "provider": {
            "id": pod.get("id"),
            "name": pod.get("name"),
            "desired_status": pod.get("desired_status"),
            "gpu_type": pod.get("gpu_type"),
            "image": pod.get("image"),
            "ports": pod.get("ports"),
        },
        "remote": remote,
    }
    token_path, receipt = _rotate_worker_credential(args, evidence)
    # Fence only the old GenericHost. ComfyUI and the pod remain untouched.
    stop = subprocess.run(
        _ssh_args(args) + ["--", "kill", "-TERM", str(args.host_pid)],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    if stop.returncode != 0:
        raise RuntimeError(f"failed to fence old GenericHost: {stop.stderr[-1000:]}")
    print(json.dumps({"status": "activated", "token_path": str(token_path), "receipt": receipt}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
