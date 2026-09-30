#!/usr/bin/env python3
"""Claim an RTX 5090 on the existing Astrid ``backup`` volume.

This is an operator-facing waiter, not a second RunPod client.  It uses the
shared Astrid environment and the canonical ``runpod-lifecycle`` launch path:
the exact GPU/storage request is retried until capacity appears.  The
network-volume size is discovered and echoed back unchanged; the 200 GB
request applies only to the pod's disposable container disk.

The defaults target the prepared H3 CUDA-13 release: they request the
validated image/host profile and verify the mounted release venv before the
script reports success. Override them only for another explicitly prepared
runtime profile.

Before launch the script exclusively creates a secret-free allocation-attempt
marker.  On success it atomically replaces that marker with the claimed pod
handle and exits under the ``leave_running`` policy.  The handle can be handed
to ``runpod-lifecycle terminate <pod-id> --yes`` when an authorized owner later
chooses to terminate the pod.
"""

from __future__ import annotations

import argparse
import asyncio
import fcntl
import hashlib
import json
import os
import shlex
import sys
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from runpod_lifecycle import (
    AllocationUnknown,
    LaunchFailure,
    RunPodConfig,
    get_network_volumes,
    launch_when_available,
)


DEFAULT_GPU = "NVIDIA GeForce RTX 5090"
DEFAULT_STORAGE = "backup"
DEFAULT_CONTAINER_DISK_GB = 200
DEFAULT_POLL_SECONDS = 120
DEFAULT_CAPACITY_WINDOW_SECONDS = 3600
DEFAULT_MAX_WAIT_SECONDS = 43200
DEFAULT_READY_TIMEOUT_SECONDS = 900
DEFAULT_WORKER_IMAGE = "runpod/base:1.0.3-dev-fix-pytorch-version-verification-cuda1300-ubuntu2404"
DEFAULT_ALLOWED_CUDA_VERSIONS = ("13.0",)
DEFAULT_RELEASE_ROOT = "/workspace/h3-golden/releases/h3-cu130-v1-candidate"
DEFAULT_HANDLE_PATH = Path(
    ".otto/runs/h3-av-simplicity-20260924-T2/runpod/claim-handle.json"
)
DEFAULT_LIFECYCLE_POLICY = "leave_running"


async def _preflight_release(pod: Any, release_root: str) -> dict[str, str]:
    """Verify the mounted prepared release before keeping a claimed pod."""
    python_path = f"{release_root}/runtime/venv/bin/python"
    launcher_path = f"{release_root}/runtime/launch-comfy.sh"
    command = f"""
set -eu
test -d /workspace
test -x {shlex.quote(python_path)}
test -x {shlex.quote(launcher_path)}
nvidia-smi --query-gpu=name,driver_version --format=csv,noheader
{shlex.quote(python_path)} -B - <<'PY'
import sys
import torch

if sys.version_info[:2] != (3, 12):
    raise SystemExit(f"release requires Python 3.12, got {{sys.version}}")
if torch.version.cuda != "13.0":
    raise SystemExit(f"release requires CUDA 13.0 Torch, got {{torch.version.cuda!r}}")
if not torch.cuda.is_available():
    raise SystemExit("Torch reports CUDA unavailable")
torch.cuda.init()
print(f"python={{sys.version.split()[0]}} torch={{torch.__version__}} torch_cuda={{torch.version.cuda}} gpu={{torch.cuda.get_device_name(0)}}")
PY
"""
    exit_code, stdout, stderr = await pod.exec_ssh(command, timeout=120)
    if exit_code != 0:
        detail = (stderr or stdout).strip()[-4000:]
        raise RuntimeError(f"H3 release preflight failed for {release_root}: {detail}")
    return {
        "release_root": release_root,
        "release_python": python_path,
        "release_launcher": launcher_path,
        "probe": stdout.strip(),
    }


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _log(message: str) -> None:
    print(f"{_utc_now()} {message}", file=sys.stderr, flush=True)


def _write_json(path: Path, value: dict[str, Any]) -> None:
    """Atomically replace *path* with a private JSON document."""
    path = path.expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    data = (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")
    fd, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary_path = Path(temporary)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def _create_json_exclusive(path: Path, value: dict[str, Any]) -> None:
    """Create the first custody marker without replacing an existing owner."""
    path = path.expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    data = (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def _existing_handle_error(path: Path) -> RuntimeError:
    try:
        existing = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return RuntimeError(
            f"claim custody already exists at {path} and cannot be read safely: {exc}"
        )
    if not isinstance(existing, dict):
        return RuntimeError(
            f"claim custody already exists at {path} and is not a JSON object"
        )
    if existing.get("state") in {"allocation_pending", "allocation_unknown"}:
        return RuntimeError(
            f"allocation request {existing.get('request_name')!r} at {path} "
            "requires provider reconciliation before another launch"
        )
    if existing.get("pod_id"):
        return RuntimeError(
            f"claim handle at {path} already identifies pod {existing['pod_id']}; "
            "reconcile that pod before another launch"
        )
    return RuntimeError(f"claim custody already exists at {path}; inspect it before retrying")


def _canonical_digest(value: Mapping[str, Any]) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def _receipt_dir_for_handle(path: Path) -> Path:
    if path.parent.name == "runpod":
        return path.parent.parent / "receipts"
    return path.parent / "receipts"


def _matching_terminal_cleanup(
    path: Path, existing: Mapping[str, Any]
) -> dict[str, str] | None:
    """Find exact, identity-bound evidence that an old claimed pod is terminal."""
    pod_id = str(existing.get("pod_id") or "").strip()
    volume_id = str(existing.get("network_volume_id") or "").strip()
    if not pod_id or not volume_id:
        return None
    expected_digest = _canonical_digest(existing)
    matches: list[tuple[int, Path, str]] = []
    receipt_dir = _receipt_dir_for_handle(path)
    if not receipt_dir.is_dir():
        return None
    for receipt_path in receipt_dir.glob("*.json"):
        try:
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(receipt, dict):
            continue
        cleanup = receipt.get("cleanup")
        if not isinstance(cleanup, dict):
            continue
        if receipt.get("claim_handle_digest") != expected_digest:
            continue
        if str(receipt.get("pod_id") or "").strip() != pod_id:
            continue
        if str(cleanup.get("pod_id") or "").strip() != pod_id:
            continue
        if receipt.get("cleanup_status") != "complete":
            continue
        status = str(cleanup.get("status") or "").strip()
        if status not in {"terminated", "already_gone", "absent"}:
            continue
        if str(cleanup.get("backup_volume_id") or "").strip() != volume_id:
            continue
        if cleanup.get("backup_volume_preserved") is not True:
            continue
        matches.append((receipt_path.stat().st_mtime_ns, receipt_path, status))
    if not matches:
        return None
    _mtime, receipt_path, status = max(matches, key=lambda item: item[0])
    return {
        "pod_id": pod_id,
        "handle_digest": expected_digest,
        "cleanup_status": status,
        "cleanup_receipt_path": str(receipt_path.resolve()),
    }


def _rollover_candidate(path: Path) -> dict[str, str] | None:
    if not path.exists():
        return None
    try:
        existing = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise _existing_handle_error(path) from exc
    if not isinstance(existing, dict):
        raise _existing_handle_error(path)
    terminal = _matching_terminal_cleanup(path, existing)
    if terminal is None:
        raise _existing_handle_error(path)
    return terminal


def _archive_path(path: Path, rollover: Mapping[str, str]) -> Path:
    digest = rollover["handle_digest"].removeprefix("sha256:")[:16]
    pod_id = "".join(
        character
        for character in rollover["pod_id"]
        if character.isalnum() or character in "-_"
    )
    return path.with_name(f"{path.stem}.archived-{pod_id}-{digest}{path.suffix}")


def _archive_bytes(path: Path, data: bytes) -> None:
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        if path.read_bytes() != data:
            raise RuntimeError(f"claim archive collision at {path}; refusing rollover")
        return
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def _start_allocation_attempt(
    path: Path,
    *,
    args: argparse.Namespace,
    volume: dict[str, Any],
    rollover: dict[str, str] | None = None,
) -> dict[str, Any]:
    operation_id = uuid.uuid4().hex
    request_name = f"{args.name_prefix}-{operation_id[:12]}"
    attempt = {
        "schema_version": "astrid.runpod.allocation-attempt.v1",
        "state": "allocation_pending",
        "operation_id": operation_id,
        "request_name": request_name,
        "requested_at": _utc_now(),
        "reconciliation_required": True,
        "gpu_type": args.gpu_type,
        "storage_name": args.storage_name,
        "network_volume_id": volume.get("id"),
        "network_volume_size_gb": int(volume["size"]),
        "attach_only": True,
        "container_disk_gb": args.container_disk_gb,
        "worker_image": args.worker_image,
        "template_id": args.template_id,
        "allowed_cuda_versions": [
            part.strip() for part in args.allowed_cuda_versions.split(",") if part.strip()
        ],
        "max_wait_seconds": args.max_wait_seconds,
        "poll_seconds": args.poll_seconds,
        "lifecycle": {"mode": args.lifecycle_policy},
    }
    if rollover is not None:
        archive_path = _archive_path(path, rollover)
        attempt["previous_claim"] = {
            **rollover,
            "archive_path": str(archive_path.resolve()),
        }
        try:
            with path.open("rb") as stream:
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
                opened = os.fstat(stream.fileno())
                current_path = path.stat()
                if (opened.st_dev, opened.st_ino) != (
                    current_path.st_dev,
                    current_path.st_ino,
                ):
                    raise RuntimeError(
                        f"claim custody at {path} changed during rollover; refusing to overwrite it"
                    )
                current_bytes = stream.read()
                try:
                    current = json.loads(current_bytes)
                except json.JSONDecodeError as exc:
                    raise RuntimeError(
                        f"cannot verify stale claim custody at {path}: {exc}"
                    ) from exc
                if (
                    not isinstance(current, dict)
                    or _canonical_digest(current) != rollover["handle_digest"]
                ):
                    raise RuntimeError(
                        f"claim custody at {path} changed during rollover; refusing to overwrite it"
                    )
                refreshed = _matching_terminal_cleanup(path, current)
                if refreshed != rollover:
                    raise RuntimeError(
                        f"terminal cleanup evidence for {rollover['pod_id']} changed during rollover"
                    )
                _archive_bytes(archive_path, current_bytes)
                _write_json(path, attempt)
        except FileNotFoundError as exc:
            raise RuntimeError(f"claim custody at {path} changed during rollover") from exc
        return attempt
    try:
        _create_json_exclusive(path, attempt)
    except FileExistsError as exc:
        raise _existing_handle_error(path) from exc
    return attempt


def _replace_allocation_attempt(
    path: Path,
    value: dict[str, Any],
    *,
    operation_id: str,
) -> None:
    """Atomically transition only the marker owned by *operation_id*."""
    try:
        current = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"cannot verify claim custody at {path}: {exc}") from exc
    if current.get("operation_id") != operation_id:
        raise RuntimeError(
            f"claim custody at {path} belongs to another operation; refusing to overwrite it"
        )
    if value.get("operation_id") != operation_id:
        raise RuntimeError("replacement claim custody changed operation identity")
    _write_json(path, value)


def _volume_by_name_or_id(volumes: list[dict[str, Any]], name_or_id: str) -> dict[str, Any] | None:
    return next(
        (
            volume
            for volume in volumes
            if isinstance(volume, dict)
            and (str(volume.get("name") or "") == name_or_id or str(volume.get("id") or "") == name_or_id)
        ),
        None,
    )


async def _require_existing_volume(config: RunPodConfig, name_or_id: str) -> dict[str, Any]:
    volumes = await asyncio.to_thread(get_network_volumes, config.api_key)
    volume = _volume_by_name_or_id(volumes, name_or_id)
    if volume is None:
        raise RuntimeError(f"RunPod network volume {name_or_id!r} was not found; refusing to create one")
    size = volume.get("size")
    if isinstance(size, bool) or not isinstance(size, (int, float)) or int(size) <= 0:
        raise RuntimeError(f"RunPod network volume {name_or_id!r} has no valid size: {volume!r}")
    return volume


def _handle(
    *,
    pod: Any,
    ssh: dict[str, Any],
    config: RunPodConfig,
    volume: dict[str, Any],
    claimed_at: str,
    preflight: dict[str, str],
    attempt: dict[str, Any],
) -> dict[str, Any]:
    selected_gpu = getattr(pod, "_gpu_type", None) or config.gpu_type
    selected_storage = getattr(pod, "_storage_name", None) or config.storage_name
    return {
        "schema_version": "astrid.runpod.claim.v1",
        "state": "claimed",
        "operation_id": attempt["operation_id"],
        "request_name": attempt["request_name"],
        "pod_id": str(pod.id),
        "name": str(pod.name),
        "ssh": f"root@{ssh['ip']} -p {ssh['port']}",
        "claimed_at": claimed_at,
        "gpu_type": selected_gpu,
        "storage_name": selected_storage,
        "network_volume_id": getattr(pod, "_storage_volume", None) or volume.get("id"),
        "network_volume_size_gb": int(volume["size"]),
        "attach_only": True,
        "container_disk_gb": config.container_disk_gb,
        "volume_mount_path": config.volume_mount_path,
        "allowed_cuda_versions": list(config.allowed_cuda_versions),
        "worker_image": config.worker_image,
        "template_id": config.template_id,
        "name_prefix": config.name_prefix,
        "lifecycle": {"mode": DEFAULT_LIFECYCLE_POLICY},
        "reconciliation_required": False,
        "runtime_preflight": preflight,
        **(
            {"previous_claim": attempt["previous_claim"]}
            if "previous_claim" in attempt
            else {}
        ),
    }


async def _claim(args: argparse.Namespace) -> dict[str, Any]:
    handle_path = Path(args.handle_path).expanduser().resolve()
    rollover = _rollover_candidate(handle_path)

    # RunPodConfig.from_env() is the shared Astrid credential/config boundary;
    # explicit values below prevent ambient GPU/storage fallbacks from changing
    # the request this waiter is intended to claim.
    config = RunPodConfig.from_env(
        gpu_type=args.gpu_type,
        storage_name=args.storage_name,
        storage_volumes=(args.storage_name,),
        container_disk_gb=args.container_disk_gb,
        min_memory_gb=args.min_memory_gb,
        name_prefix=args.name_prefix,
        worker_image=args.worker_image,
        # The validated H3 image is authoritative; do not let the generic
        # runpod-torch-v240 template silently replace it.
        template_id=args.template_id,
        allowed_cuda_versions=tuple(args.allowed_cuda_versions.split(",")),
        # The existing backup volume is attachment-only. A zero volume request
        # plus attach_only prevents lifecycle from expanding or replacing it.
        disk_size_gb=0,
        attach_only=True,
    )
    volume = await _require_existing_volume(config, args.storage_name)
    attempt = _start_allocation_attempt(
        handle_path, args=args, volume=volume, rollover=rollover
    )
    operation_id = str(attempt["operation_id"])
    request_name = str(attempt["request_name"])
    _log(
        f"watching gpu={args.gpu_type!r} storage={args.storage_name!r} "
        f"volume_id={volume.get('id')!r} volume_size_gb={int(volume['size'])} "
        f"container_disk_gb={args.container_disk_gb} image={args.worker_image!r} "
        f"allowed_cuda={config.allowed_cuda_versions!r} release={args.release_root!r} "
        f"poll_seconds={args.poll_seconds} max_wait_seconds={args.max_wait_seconds} "
        f"lifecycle={args.lifecycle_policy!r} handle_path={str(handle_path)!r}"
    )

    deadline = (
        time.monotonic() + args.max_wait_seconds
        if args.max_wait_seconds > 0
        else None
    )

    while True:
        if deadline is not None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                exhausted = {
                    **attempt,
                    "state": "capacity_exhausted",
                    "reconciliation_required": False,
                    "completed_at": _utc_now(),
                }
                _replace_allocation_attempt(
                    handle_path, exhausted, operation_id=operation_id
                )
                raise TimeoutError(f"capacity did not appear within {args.max_wait_seconds}s")
            window = min(args.capacity_window_seconds, max(1, int(remaining)))
        else:
            window = args.capacity_window_seconds

        _log(f"starting capacity window={window}s")
        try:
            pod = await launch_when_available(
                config,
                name=request_name,
                max_wait_sec=window,
                retry_interval_sec=args.poll_seconds,
            )
        except AllocationUnknown as exc:
            unknown = {
                **attempt,
                "state": "allocation_unknown",
                "reconciliation_required": True,
                "unresolved_at": _utc_now(),
                "gpu_type_attempted": exc.gpu_type,
                "ram_tier_attempted": exc.ram_tier,
                "storage_name_attempted": exc.storage_name,
                "storage_volume_id_attempted": exc.storage_volume_id,
            }
            _replace_allocation_attempt(handle_path, unknown, operation_id=operation_id)
            _log(
                f"allocation outcome unknown for request={request_name!r}; "
                "provider reconciliation is required before another launch"
            )
            raise
        except LaunchFailure as exc:
            # The updated lifecycle raises AllocationUnknown separately. A
            # remaining LaunchFailure is a definite bounded capacity miss, so
            # another window is safe while the overall deadline remains.
            _log(f"capacity window exhausted: {exc}")
            continue

        allocated = {
            **attempt,
            "state": "allocated",
            "pod_id": str(pod.id),
            "name": str(pod.name),
            "allocated_at": _utc_now(),
            "reconciliation_required": False,
        }
        _replace_allocation_attempt(handle_path, allocated, operation_id=operation_id)

        failure_phase = "readiness"
        try:
            # launch_when_available returns immediately after RunPod allocates
            # the pod.  Do not report success until the canonical readiness
            # check and SSH metadata lookup both pass.
            await pod.wait_ready(timeout=args.ready_timeout_seconds)
            failure_phase = "ssh"
            ssh = await pod._ensure_ssh_details()
            failure_phase = "release_preflight"
            preflight = await _preflight_release(pod, args.release_root)
            result = _handle(
                pod=pod,
                ssh=ssh,
                config=config,
                volume=volume,
                claimed_at=_utc_now(),
                preflight=preflight,
                attempt=attempt,
            )
            result["handle_path"] = str(handle_path)
            _replace_allocation_attempt(handle_path, result, operation_id=operation_id)
            return result
        except BaseException as exc:
            failed = {
                **allocated,
                "state": "allocated_unverified",
                "failure_phase": failure_phase,
                "failure_type": type(exc).__name__,
                "failed_at": _utc_now(),
                "reconciliation_required": True,
            }
            _replace_allocation_attempt(handle_path, failed, operation_id=operation_id)
            _log(
                f"allocated pod {pod.id} failed during {failure_phase}: "
                f"{type(exc).__name__}; leave_running keeps the exact pod running"
            )
            raise


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gpu-type", default=DEFAULT_GPU)
    parser.add_argument("--storage-name", default=DEFAULT_STORAGE)
    parser.add_argument("--container-disk-gb", type=int, default=DEFAULT_CONTAINER_DISK_GB)
    parser.add_argument("--image", dest="worker_image", default=DEFAULT_WORKER_IMAGE)
    parser.add_argument(
        "--allowed-cuda-versions",
        default=",".join(DEFAULT_ALLOWED_CUDA_VERSIONS),
        help="Provider host CUDA versions compatible with the selected release.",
    )
    parser.add_argument(
        "--template-id",
        default=None,
        help="Optional provider template; defaults to none so the validated image is authoritative.",
    )
    parser.add_argument("--release-root", default=DEFAULT_RELEASE_ROOT)
    parser.add_argument("--min-memory-gb", type=int, default=32)
    parser.add_argument("--poll-seconds", type=int, default=DEFAULT_POLL_SECONDS)
    parser.add_argument(
        "--capacity-window-seconds",
        type=int,
        default=DEFAULT_CAPACITY_WINDOW_SECONDS,
        help="Bound each launch_when_available call; the outer waiter starts another window.",
    )
    parser.add_argument(
        "--max-wait-seconds",
        type=int,
        default=DEFAULT_MAX_WAIT_SECONDS,
        help="Overall capacity-wait limit (default: 43200 / 12 hours); 0 waits indefinitely.",
    )
    parser.add_argument("--ready-timeout-seconds", type=int, default=DEFAULT_READY_TIMEOUT_SECONDS)
    parser.add_argument("--name-prefix", default="astrid-claim-5090-backup")
    parser.add_argument(
        "--handle-path",
        default=str(DEFAULT_HANDLE_PATH),
        help=f"Secret-free custody marker/final handle (default: {DEFAULT_HANDLE_PATH}).",
    )
    parser.add_argument(
        "--lifecycle-policy",
        choices=(DEFAULT_LIFECYCLE_POLICY,),
        default=DEFAULT_LIFECYCLE_POLICY,
        help="Claim postcondition; leave_running never terminates an allocated pod.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    for name in (
        "container_disk_gb",
        "min_memory_gb",
        "poll_seconds",
        "capacity_window_seconds",
        "ready_timeout_seconds",
    ):
        if getattr(args, name) <= 0:
            raise SystemExit(f"--{name.replace('_', '-')} must be positive")
    if args.max_wait_seconds < 0:
        raise SystemExit("--max-wait-seconds must be zero or positive")
    if not any(part.strip() for part in args.allowed_cuda_versions.split(",")):
        raise SystemExit("--allowed-cuda-versions must contain at least one version")

    try:
        result = asyncio.run(_claim(args))
    except KeyboardInterrupt:
        _log("interrupted; inspect the claim custody handle before any retry")
        return 130
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
