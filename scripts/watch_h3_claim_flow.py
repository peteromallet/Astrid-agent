#!/usr/bin/env python3
"""Watch local evidence for one canonical RunPod claim -> H3 transform flow.

This observer only reads files.  It never allocates, retries, terminates, or
otherwise changes a provider resource.  The successful claim helper remains
the sole source of the pod handle, and the canonical H3 transform remains the
sole source of execution and receipt state.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, TextIO


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RUN_DIR = ROOT / ".otto/runs/h3-av-simplicity-20260924-T2"
DEFAULT_POLL_SECONDS = 120.0

_STAGES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("prepare", ("01-prepare", "prepare-result", "preparation")),
    ("compile", ("02-compile", "compile-result", "compilation")),
    ("validate", ("03-validate", "validate-result", "validation")),
    ("run", ("04-run", "run-result", "generation-result")),
    ("compose", ("05-compose", "compose-result", "composition")),
    ("verify", ("06-verify", "verify-result", "verification")),
    ("final", ("07-final-receipt", "final-receipt")),
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _read_json(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None, "missing"
    except (OSError, json.JSONDecodeError) as exc:
        return None, f"{type(exc).__name__}: {exc}"
    if not isinstance(value, dict):
        return None, "JSON root is not an object"
    return value, None


def _status(value: Mapping[str, Any]) -> str:
    for key in ("overall_status", "status", "state", "phase"):
        candidate = value.get(key)
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    if value.get("ok") is True:
        return "succeeded"
    if value.get("ok") is False or value.get("error") or value.get("failure"):
        return "failed"
    result = value.get("result")
    if isinstance(result, Mapping):
        nested = _status(result)
        if nested != "present":
            return nested
    return "present"


def _canonical_digest(value: Mapping[str, Any]) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def _is_exact_terminal_cleanup(
    receipt: Mapping[str, Any], claim: Mapping[str, Any] | None
) -> bool:
    if not isinstance(claim, Mapping):
        return False
    pod_id = str(claim.get("pod_id") or "").strip()
    volume_id = str(claim.get("network_volume_id") or "").strip()
    cleanup = receipt.get("cleanup")
    if not pod_id or not volume_id or not isinstance(cleanup, Mapping):
        return False
    return (
        receipt.get("claim_handle_digest") == _canonical_digest(claim)
        and str(receipt.get("pod_id") or "").strip() == pod_id
        and str(cleanup.get("pod_id") or "").strip() == pod_id
        and receipt.get("cleanup_status") == "complete"
        and str(cleanup.get("status") or "").strip()
        in {"terminated", "already_gone", "absent"}
        and str(cleanup.get("backup_volume_id") or "").strip() == volume_id
        and cleanup.get("backup_volume_preserved") is True
    )


def _claim_observations(claim_path: Path) -> tuple[dict[str, str], dict[str, str], dict[str, Any] | None]:
    claim, error = _read_json(claim_path)
    if error == "missing":
        return (
            {
                "phase": "allocation",
                "state": "allocation_pending/unknown",
                "claim_handle": str(claim_path),
            },
            {"phase": "pod", "state": "unknown", "pod_id": "unknown", "readiness": "unknown"},
            None,
        )
    if error is not None or claim is None:
        return (
            {
                "phase": "allocation",
                "state": "unknown",
                "claim_handle": str(claim_path),
                "detail": error or "invalid claim handle",
            },
            {"phase": "pod", "state": "unknown", "pod_id": "unknown", "readiness": "unknown"},
            None,
        )

    pod_id = str(claim.get("pod_id") or "").strip()
    marker_state = str(claim.get("state") or "").strip()
    if not pod_id:
        allocation_state = marker_state if marker_state in {"allocation_pending", "allocation_unknown"} else "unknown"
        return (
            {
                "phase": "allocation",
                "state": allocation_state,
                "claim_handle": str(claim_path),
            },
            {"phase": "pod", "state": "unknown", "pod_id": "unknown", "readiness": "unknown"},
            claim,
        )

    readiness = "unknown"
    explicit = claim.get("readiness")
    if isinstance(explicit, Mapping):
        readiness = _status(explicit)
    elif isinstance(explicit, str) and explicit.strip():
        readiness = explicit.strip()
    preflight = claim.get("runtime_preflight")
    if readiness == "unknown" and isinstance(preflight, Mapping):
        readiness = _status(preflight)
        if readiness == "present" and any(preflight.get(key) for key in ("probe", "release_python", "release_root")):
            readiness = "ready"
    if readiness == "unknown" and claim.get("schema_version") == "astrid.runpod.claim.v1":
        # That handle is written only after claim helper readiness and release
        # preflight complete successfully.
        readiness = "ready"

    return (
        {
            "phase": "allocation",
            "state": "claimed",
            "claim_handle": str(claim_path),
        },
        {"phase": "pod", "state": readiness, "pod_id": pod_id, "readiness": readiness},
        claim,
    )


def _execution_observation(
    request_path: Path, *, claimed_pod_id: str | None,
) -> tuple[dict[str, str], dict[str, Any] | None]:
    payload, error = _read_json(request_path)
    if error == "missing":
        return (
            {
                "phase": "execution_request",
                "state": "missing",
                "presence": "missing",
                "status": "unknown",
                "path": str(request_path),
            },
            None,
        )
    if error is not None or payload is None:
        return (
            {
                "phase": "execution_request",
                "state": "invalid",
                "presence": "present",
                "status": "invalid",
                "path": str(request_path),
                "detail": error or "invalid execution request",
            },
            None,
        )

    request = payload.get("execution_request", payload)
    if not isinstance(request, Mapping):
        return (
            {
                "phase": "execution_request",
                "state": "invalid",
                "presence": "present",
                "status": "invalid",
                "path": str(request_path),
                "detail": "execution_request is not an object",
            },
            None,
        )
    request = dict(request)
    target = request.get("target")
    target_pod_id = str(target.get("pod_id") or "").strip() if isinstance(target, Mapping) else ""
    request_status = _status(payload)
    if request_status == "present" and request is not payload:
        request_status = _status(request)
    state = "present"
    if claimed_pod_id and target_pod_id and target_pod_id != claimed_pod_id:
        state = "target_mismatch"
        request_status = "target_mismatch"
    lifecycle = request.get("lifecycle")
    mode = str(lifecycle.get("mode") or "unknown") if isinstance(lifecycle, Mapping) else "unknown"
    return (
        {
            "phase": "execution_request",
            "state": state,
            "presence": "present",
            "status": request_status,
            "target_pod_id": target_pod_id or "unknown",
            "lifecycle": mode,
            "path": str(request_path),
        },
        request,
    )


def _stage_for(path: Path, root: Path) -> str | None:
    try:
        relative = path.relative_to(root).as_posix().lower()
    except ValueError:
        relative = path.name.lower()
    for stage, markers in _STAGES:
        if any(marker in relative for marker in markers):
            return stage
    return None


def _receipt_observations(receipt_dir: Path) -> tuple[list[dict[str, str]], list[tuple[Path, dict[str, Any]]]]:
    candidates: dict[str, list[Path]] = {stage: [] for stage, _markers in _STAGES}
    parsed: list[tuple[Path, dict[str, Any]]] = []
    if receipt_dir.is_dir():
        for path in receipt_dir.rglob("*"):
            if not path.is_file() or path.suffix.lower() != ".json":
                continue
            stage = _stage_for(path, receipt_dir)
            if stage is not None:
                candidates[stage].append(path)
            value, error = _read_json(path)
            if error is None and value is not None:
                parsed.append((path, value))

    observations: list[dict[str, str]] = []
    for stage, _markers in _STAGES:
        paths = candidates[stage]
        if not paths:
            observations.append(
                {"phase": "h3_receipt", "stage": stage, "state": "missing", "receipt_dir": str(receipt_dir)}
            )
            continue
        latest = max(paths, key=lambda item: item.stat().st_mtime_ns)
        state = "artifacts_present"
        if latest.suffix.lower() == ".json":
            value, error = _read_json(latest)
            state = "invalid" if error is not None or value is None else _status(value)
        observations.append(
            {"phase": "h3_receipt", "stage": stage, "state": state, "path": str(latest)}
        )
    return observations, parsed


def _cleanup_observation(
    *,
    execution_request: Mapping[str, Any] | None,
    receipts: list[tuple[Path, dict[str, Any]]],
    pod_id: str | None,
    claim: Mapping[str, Any] | None,
) -> tuple[dict[str, str], str | None]:
    lifecycle = execution_request.get("lifecycle") if isinstance(execution_request, Mapping) else None
    mode = str(lifecycle.get("mode") or "unknown") if isinstance(lifecycle, Mapping) else "unknown"
    observed: list[tuple[int, Path, str, bool]] = []
    for path, value in receipts:
        receipt_pod = str(value.get("pod_id") or "").strip()
        cleanup = value.get("cleanup")
        if not receipt_pod and isinstance(cleanup, Mapping):
            receipt_pod = str(cleanup.get("pod_id") or "").strip()
        if pod_id and receipt_pod and receipt_pod != pod_id:
            continue
        status: str | None = None
        states = value.get("states")
        if isinstance(states, Mapping):
            cleanup_state = states.get("cleanup_verified")
            if isinstance(cleanup_state, Mapping):
                status = _status(cleanup_state)
        if status is None and isinstance(cleanup, Mapping):
            status = _status(cleanup)
        if status is None:
            raw_status = value.get("cleanup_status")
            if isinstance(raw_status, str) and raw_status.strip():
                status = raw_status.strip()
        if status is None and "cleanup" in path.name.lower():
            status = _status(value)
        if status is not None:
            observed.append(
                (path.stat().st_mtime_ns, path, status, _is_exact_terminal_cleanup(value, claim))
            )
    if observed:
        _mtime, path, status, is_terminal = max(observed, key=lambda item: item[0])
        observation = {"phase": "cleanup", "state": status, "mode": mode, "path": str(path)}
        return observation, status if is_terminal else None
    if mode == "leave_running":
        return {"phase": "cleanup", "state": "leave_running", "mode": mode}, None
    return {"phase": "cleanup", "state": "pending/unknown", "mode": mode}, None


def inspect_local_state(
    *, claim_path: Path, execution_request_path: Path, receipt_dir: Path,
) -> dict[str, dict[str, str]]:
    """Return one local-only, deterministic flow snapshot."""
    allocation, pod, claim = _claim_observations(claim_path)
    pod_id = str(claim.get("pod_id") or "").strip() if isinstance(claim, Mapping) else ""
    execution, request = _execution_observation(
        execution_request_path, claimed_pod_id=pod_id or None
    )
    stages, parsed_receipts = _receipt_observations(receipt_dir)
    cleanup, terminal_status = _cleanup_observation(
        execution_request=request,
        receipts=parsed_receipts,
        pod_id=pod_id or None,
        claim=claim,
    )
    if terminal_status is not None:
        pod = {
            "phase": "pod",
            "state": terminal_status,
            "pod_id": pod_id,
            "readiness": terminal_status,
            "cleanup_path": cleanup["path"],
        }
    snapshot: dict[str, dict[str, str]] = {
        "allocation": allocation,
        "pod": pod,
        "execution_request": execution,
    }
    for stage in stages:
        snapshot[f"h3:{stage['stage']}"] = stage
    snapshot["cleanup"] = cleanup
    return snapshot


def emit_transitions(
    current: Mapping[str, Mapping[str, str]],
    previous: Mapping[str, Mapping[str, str]] | None = None,
    *,
    stream: TextIO | None = None,
) -> int:
    """Print changed observations and return the number of emitted lines."""
    emitted = 0
    stream = stream or sys.stdout
    previous = previous or {}
    for key, observation in current.items():
        before = previous.get(key)
        if before == observation:
            continue
        state = observation["state"]
        transition = state if before is None else f"{before.get('state', 'unknown')}->{state}"
        details = " ".join(
            f"{name}={json.dumps(value, ensure_ascii=False)}"
            for name, value in observation.items()
            if name not in {"phase", "state"}
        )
        suffix = f" {details}" if details else ""
        print(
            f"{_utc_now()} phase={observation['phase']} state={transition}{suffix}",
            file=stream,
            flush=True,
        )
        emitted += 1
    return emitted


def _infer_run_dir(claim_path: Path) -> Path:
    return claim_path.parent.parent if claim_path.parent.name == "runpod" else claim_path.parent


def _resolve_paths(args: argparse.Namespace) -> tuple[Path, Path, Path, Path]:
    if args.path is not None and args.run_dir is not None:
        raise ValueError("use either the positional run directory/claim handle or --run-dir, not both")
    source = Path(args.run_dir or args.path or DEFAULT_RUN_DIR).expanduser().resolve()
    if args.claim_handle is not None:
        claim_path = Path(args.claim_handle).expanduser().resolve()
        run_dir = source if source.suffix.lower() != ".json" else _infer_run_dir(source)
    elif source.suffix.lower() == ".json":
        claim_path = source
        run_dir = _infer_run_dir(source)
    else:
        run_dir = source
        claim_path = run_dir / "runpod" / "claim-handle.json"
    execution_path = (
        Path(args.execution_request).expanduser().resolve()
        if args.execution_request is not None
        else run_dir / "execution-request.json"
    )
    receipt_dir = (
        Path(args.receipt_dir).expanduser().resolve()
        if args.receipt_dir is not None
        else run_dir / "receipts"
    )
    return run_dir, claim_path, execution_path, receipt_dir


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "path",
        nargs="?",
        type=Path,
        help=f"Run directory or claim-handle JSON (default: {DEFAULT_RUN_DIR})",
    )
    parser.add_argument("--run-dir", type=Path, help="Explicit run directory.")
    parser.add_argument("--claim-handle", type=Path, help="Override claim-handle JSON path.")
    parser.add_argument("--execution-request", type=Path, help="Optional execution-request JSON path.")
    parser.add_argument(
        "--receipt-dir",
        "--receipts-dir",
        "--output-dir",
        dest="receipt_dir",
        type=Path,
        help="H3 output/receipt directory (default: RUN_DIR/receipts).",
    )
    parser.add_argument(
        "--poll-seconds",
        type=float,
        default=DEFAULT_POLL_SECONDS,
        help=f"Local polling interval (default: {DEFAULT_POLL_SECONDS:g}s).",
    )
    parser.add_argument("--once", action="store_true", help="Print one snapshot and exit.")
    parser.add_argument(
        "--max-polls",
        type=int,
        default=0,
        help="Stop after this many polls; 0 watches until interrupted.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.poll_seconds <= 0:
        parser.error("--poll-seconds must be positive")
    if args.max_polls < 0:
        parser.error("--max-polls must be zero or positive")
    try:
        run_dir, claim_path, execution_path, receipt_dir = _resolve_paths(args)
    except ValueError as exc:
        parser.error(str(exc))
    max_polls = 1 if args.once else args.max_polls
    print(
        f"{_utc_now()} watching=local-only run_dir={json.dumps(str(run_dir))} "
        f"poll_seconds={args.poll_seconds:g}",
        flush=True,
    )
    previous: dict[str, dict[str, str]] | None = None
    polls = 0
    try:
        while True:
            current = inspect_local_state(
                claim_path=claim_path,
                execution_request_path=execution_path,
                receipt_dir=receipt_dir,
            )
            emit_transitions(current, previous)
            previous = current
            polls += 1
            if max_polls and polls >= max_polls:
                return 0
            time.sleep(args.poll_seconds)
    except KeyboardInterrupt:
        print(f"{_utc_now()} watch=stopped reason=keyboard_interrupt", file=sys.stderr, flush=True)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
