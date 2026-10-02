#!/usr/bin/env python3
"""Explicit candidate/lane CPU preflight and existing H3 settlement resume.

The existing Runtime/provider/H3 owners are supplied by the caller. This thin
adapter owns no provider client and cannot admit a helper task. New live
activation stays blocked pending connected host and managed-output acceptance.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Mapping

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
PRODUCT_SOURCE_ROOT = REPOSITORY_ROOT
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))


class SourceCustodyError(RuntimeError):
    """The canonical front end was imported from the wrong source custody."""


def _git_value(*args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(PRODUCT_SOURCE_ROOT), *args],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise SourceCustodyError(
            f"cannot establish candidate source identity with git {args!r}: "
            f"{result.stderr.strip()}"
        )
    return result.stdout.strip()


def _assert_import_custody() -> dict[str, str]:
    """Reject a foreign import before loading deployment adapters."""
    source_root = PRODUCT_SOURCE_ROOT.resolve()
    if source_root != PRODUCT_SOURCE_ROOT or PRODUCT_SOURCE_ROOT.is_symlink():
        raise SourceCustodyError(f"source root must be the real checkout: {PRODUCT_SOURCE_ROOT}")
    if not (source_root / ".git").exists() and not (source_root / ".git").is_file():
        raise SourceCustodyError(f"source root is not a Git checkout: {source_root}")

    astrid_module = sys.modules.get("astrid")
    if astrid_module is None:
        import astrid as astrid_module  # noqa: PLC0415
    module_file = getattr(astrid_module, "__file__", None)
    if not module_file:
        raise SourceCustodyError("source import identity has no astrid.__file__")
    module_path = Path(module_file).resolve()
    expected_package = source_root / "astrid"
    if not module_path.is_relative_to(expected_package):
        raise SourceCustodyError(
            "parent-main fallback rejected: "
            f"astrid.__file__={module_path} is outside candidate {expected_package}"
        )
    return {"source_root": str(source_root), "astrid_module": str(module_path)}


def candidate_content_digest(source_root: Path) -> str:
    """Hash the staged source plus the three launcher/claim scripts.

    Match H3RemotePreparer's source members and cache exclusions. Include dirty
    and untracked executable bytes, not just HEAD or the pack corpus digest.
    """
    from astrid.core.foundation.hash import canonical_json_digest, sha256_file

    entries = []
    members = ("astrid", "banodoco_workspace_client", "config", "pyproject.toml",
               "scripts/run_h3_canonical_on_runpod.py", "scripts/h3_runpod_qualification.py",
               "scripts/claim_runpod_5090_backup.py")
    for member in members:
        root = source_root / member
        if not root.exists() or root.is_symlink():
            raise SourceCustodyError(f"candidate source member missing or symlinked: {member}")
        paths = sorted(root.rglob("*")) if root.is_dir() else [root]
        for path in paths:
            relative = path.relative_to(source_root)
            if {"__pycache__", ".pytest_cache"}.intersection(relative.parts) or path.suffix == ".pyc":
                continue
            if path.is_symlink():
                raise SourceCustodyError(f"candidate source symlink rejected: {relative}")
            if path.is_file():
                entries.append((str(relative), sha256_file(path)))
    return "sha256:" + canonical_json_digest(entries)


def assert_product_source_identity(
    *, expected_branch: str | None = None, expected_head: str | None = None,
    content_digest: str | None = None, capsule: Path | None = None,
    source_root: Path | None = None,
) -> dict[str, Any]:
    """Require caller-pinned identity; a capsule uses these same field names."""
    identity = _assert_import_custody()
    supplied = {"expected_branch": expected_branch, "expected_head": expected_head,
                "content_digest": content_digest,
                "source_root": str(source_root) if source_root is not None else None}
    if capsule is not None:
        try:
            manifest = json.loads(capsule.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise SourceCustodyError("candidate capsule is unreadable") from exc
        if not isinstance(manifest, dict) or manifest.get("schema_version") != "astrid.h3.source-capsule.v1":
            raise SourceCustodyError("candidate capsule schema is unsupported")
        for key in supplied:
            if supplied[key] is not None and supplied[key] != manifest.get(key):
                raise SourceCustodyError(f"candidate capsule conflicts with explicit {key}")
            supplied[key] = manifest.get(key)
        if not supplied["content_digest"]:
            raise SourceCustodyError("candidate capsule requires content_digest")
    expected_branch, expected_head = supplied["expected_branch"], supplied["expected_head"]
    if not isinstance(expected_branch, str) or not expected_branch.strip() or not isinstance(expected_head, str) or re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", expected_head) is None:
        raise SourceCustodyError("explicit candidate branch/HEAD or capsule is required")
    if supplied["source_root"] is not None and supplied["source_root"] != identity["source_root"]:
        raise SourceCustodyError("candidate source root mismatch")

    branch = _git_value("branch", "--show-current")
    head = _git_value("rev-parse", "HEAD")
    if branch != expected_branch:
        raise SourceCustodyError(
            f"candidate branch mismatch: expected {expected_branch}, got {branch or '<detached>'}"
        )
    if head != expected_head:
        raise SourceCustodyError(
            f"candidate HEAD mismatch: expected {expected_head}, got {head}"
        )
    dirty = bool(_git_value("status", "--porcelain", "--untracked-files=all"))
    expected_digest = supplied["content_digest"]
    if dirty and not expected_digest:
        raise SourceCustodyError("dirty/untracked candidate requires an explicit content digest")
    observed_digest = candidate_content_digest(PRODUCT_SOURCE_ROOT)
    if expected_digest is not None and expected_digest != observed_digest:
        raise SourceCustodyError("candidate content digest mismatch")

    from astrid.core.execution.generic_host import source_checkout_digest  # noqa: PLC0415
    from astrid.core.pack.source_setup import (  # noqa: PLC0415
        active_source_inventory,
        source_inventory_identity,
    )

    try:
        scoped_digest = source_checkout_digest(PRODUCT_SOURCE_ROOT)
        inventory = active_source_inventory()
        inventory_identity = source_inventory_identity(inventory.sources)
    except Exception as exc:  # noqa: BLE001
        raise SourceCustodyError(f"candidate scoped source identity is unavailable: {exc}") from exc
    return {
        **identity,
        "branch": branch,
        "head": head,
        "content_digest": observed_digest,
        "dirty": dirty,
        "scoped_source_digest": scoped_digest,
        "source_inventory_identity": inventory_identity,
        "interpreter": str(Path(sys.executable).resolve()),
    }


# This import-time check is intentionally before deployment/provider imports.
SOURCE_IDENTITY = _assert_import_custody()

from astrid.core.execution.runpod_deployment import (  # noqa: E402
    CANONICAL_TASK_ID,
    CANONICAL_RUN_ID,
    AstridRuntimeTaskAdapter,
    ConcreteDeploymentOperations,
    DeploymentOperations,
    DeploymentOperationError,
    DeploymentRequest,
    RunPodClaimHelperAdapter,
    RunPodLifecycleCleanupAdapter,
    AstridManagedOutputSettlementAdapter,
    QualifiedRunPodDeploymentOwner,
    run_existing_h3_task,
    resume_h3_settlement,
)


class H3LiveQuarantineError(DeploymentOperationError):
    """Stable machine-readable rejection for the quarantined live H3 CLI."""

    code = "h3_live_runpod_quarantined"

    def __init__(self) -> None:
        super().__init__(
            "live H3 RunPod submit/qualification is quarantined; "
            "only offline H3 processing and cleanup_pending settlement resume are enabled"
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "status": "blocked",
            "code": self.code,
            "operation": "h3_live_runpod_submit",
            "offline_only": True,
            "provider_allocation": False,
            "runtime_task_admission": False,
            "runtime_task_retry": False,
            "credential_issuance": False,
            "activation": False,
            "message": str(self),
        }


LIVE_BOUNDARY_BLOCKER = (
    "live activation remains quarantined pending connected task/run-scoped host and "
    "managed-output acceptance; only explicit connected CPU preflight is enabled"
)


def _assert_live_h3_quarantined(*, cleanup_only: bool, preflight_only: bool = False,
                                lane: str | None = None, authorized_lane: str | None = None) -> None:
    """Only the explicit lane path may reach the connected CPU boundary."""
    if cleanup_only:
        return
    if not lane or authorized_lane != lane:
        raise H3LiveQuarantineError()
    if not preflight_only:
        raise DeploymentOperationError(LIVE_BOUNDARY_BLOCKER)


QualificationOwnerFactory = Callable[..., Any]


def build_qualified_owner(
    *,
    runtime: Any,
    launcher: Any,
    task_reader: Callable[[str], Mapping[str, Any]],
    launch_factory: Callable[[Mapping[str, Any], Mapping[str, Any]], Any],
    reference_factory: Callable[[Mapping[str, Any], Mapping[str, Any]], Any],
    loss_observer: Callable[[Mapping[str, Any]], Mapping[str, Any]],
    owner_identity: Mapping[str, Any],
    operation_id: str,
    task_id: str = CANONICAL_TASK_ID,
    run_id: str = CANONICAL_RUN_ID,
) -> QualifiedRunPodDeploymentOwner:
    """Construct the existing Runtime-owned qualification/T12 owner.

    The external provider/process adapters are deliberately explicit inputs.
    This front end does not guess at SSH, process inspection, or Runtime
    custody, and therefore cannot create a fake qualified worker.
    """
    required = {
        "runtime": runtime,
        "launcher": launcher,
        "task_reader": task_reader,
        "launch_factory": launch_factory,
        "reference_factory": reference_factory,
        "loss_observer": loss_observer,
        "owner_identity": owner_identity,
    }
    if any(value is None or not callable(value)
           for name, value in required.items()
           if name in {"task_reader", "launch_factory", "reference_factory", "loss_observer"}):
        raise DeploymentOperationError(
            "explicit Runtime/provider/process qualification adapters are required"
        )
    if runtime is None or launcher is None or not isinstance(owner_identity, Mapping):
        raise DeploymentOperationError(
            "explicit Runtime/provider/process qualification adapters are required"
        )
    if owner_identity.get("actor") != "owner" or "admin" not in set(owner_identity.get("scopes", ())):
        raise DeploymentOperationError("qualification owner requires Runtime owner authority")
    return QualifiedRunPodDeploymentOwner(
        runtime=runtime,
        launcher=launcher,
        task_reader=task_reader,
        launch_factory=launch_factory,
        reference_factory=reference_factory,
        loss_observer=loss_observer,
        owner_identity=owner_identity,
        operation_id=operation_id,
        task_id=task_id,
        run_id=run_id,
    )


def claim_helper_argv(
    handle_path: Path,
    *,
    python_executable: Path | None = None,
) -> tuple[str, ...]:
    """Build the only supported provider claim command, without shell parsing."""
    python = Path(
        python_executable
        or os.environ.get("ASTRID_PYTHON", "")
        or sys.executable
    )
    helper = REPOSITORY_ROOT / "scripts" / "claim_runpod_5090_backup.py"
    if not python.is_file() or not helper.is_file():
        raise FileNotFoundError("repository venv or canonical claim helper is unavailable")
    if not handle_path.is_absolute():
        raise ValueError("handle_path must be absolute")
    return (
        str(python), str(helper),
        "--max-wait-seconds", "0",
        "--handle-path", str(handle_path),
        "--stop-after-allocated-failure",
    )


def build_concrete_operations(
    client: Any,
    *,
    handle_path: Path,
    operation_id: str = "",
    python_executable: Path | None = None,
    owner_credential: Path | None = None,
    qualification_factory: QualificationOwnerFactory | None = None,
) -> ConcreteDeploymentOperations:
    """Build the existing-owner composition used by the executable front end.

    A caller with the complete explicit deployment boundary supplies a factory
    for ``QualifiedRunPodDeploymentOwner``. Without it, the returned concrete
    operation remains fail-closed and rejects before the provider claim.
    """
    python = Path(
        python_executable
        or os.environ.get("ASTRID_PYTHON", "")
        or sys.executable
    )
    qualification = None
    if qualification_factory is not None:
        qualification = qualification_factory(
            client=client,
            handle_path=handle_path,
            operation_id=operation_id,
            owner_credential=owner_credential,
        )
        if qualification is None:
            raise DeploymentOperationError(
                "qualification owner factory returned no Runtime-owned owner"
            )
        try:
            from runtime_protocol.remote_worker_activation import QualifiedRemoteWorkerLauncher
        except ImportError as exc:
            raise DeploymentOperationError("Runtime remote activation contract is unavailable") from exc

        if (not isinstance(qualification, QualifiedRunPodDeploymentOwner)
                or not isinstance(qualification.launcher, QualifiedRemoteWorkerLauncher)):
            raise DeploymentOperationError(
                "qualification factory must supply a real Runtime-qualified remote launcher"
            )
        if not callable(getattr(getattr(client, "tasks", None), "control_remote_credential", None)):
            raise DeploymentOperationError("Runtime remote activation and credential control is unavailable")
        for method in ("prepare_and_qualify", "assert_fresh"):
            if not callable(getattr(qualification, method, None)):
                raise DeploymentOperationError(
                    f"qualification owner is missing {method}"
                )
    return ConcreteDeploymentOperations(
        runtime=AstridRuntimeTaskAdapter(client),
        claim=RunPodClaimHelperAdapter(
            python_executable=python,
            helper_path=REPOSITORY_ROOT / "scripts" / "claim_runpod_5090_backup.py",
            existing_only=True,
        ),
        cleanup=RunPodLifecycleCleanupAdapter(),
        qualification=qualification,
        settlement=AstridManagedOutputSettlementAdapter(client),
        activation_control=getattr(getattr(client, "tasks", None), "control_remote_credential", None),
    )


def run(
    *,
    operation_id: str,
    receipt_path: Path,
    handle_path: Path,
    output_path: Path,
    operations: DeploymentOperations,
    task_id: str,
    run_id: str,
) -> Mapping[str, Any]:
    # Retain the callable entry point, but do not bypass the CLI's live gate.
    raise DeploymentOperationError(LIVE_BOUNDARY_BLOCKER)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--existing-task", default=os.environ.get("ASTRID_H3_TASK_ID"))
    parser.add_argument("--expected-run", default=os.environ.get("ASTRID_H3_RUN_ID"))
    parser.add_argument("--claim-adapter", choices=("h3-5090-backup",), default="h3-5090-backup")
    parser.add_argument("--operation-id", required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--resume-settlement", action="store_true")
    parser.add_argument("--handle-path", type=Path)
    parser.add_argument("--astrid-python", type=Path)
    parser.add_argument("--preflight-only", action="store_true",
                        help="connect to the resident Runtime; no provider, SSH, activation or retry")
    for flag, env in (
        ("expected-branch", "EXPECTED_BRANCH"), ("expected-head", "EXPECTED_HEAD"),
        ("content-digest", "CONTENT_DIGEST"), ("lane", "LANE"),
        ("authorize-lane", "AUTHORIZED_LANE"), ("lane-root", "LANE_ROOT"),
        ("release-root", "RELEASE_ROOT"), ("executor-id", "EXECUTOR_ID"),
        ("session-ref", "SESSION_REF"), ("expected-pod", "EXPECTED_POD"),
        ("runtime-endpoint", "RUNTIME_ENDPOINT"), ("runtime-realm", "RUNTIME_REALM"),
        ("runtime-instance-id", "RUNTIME_INSTANCE_ID"),
        ("runtime-session-id", "RUNTIME_SESSION_ID"),
    ):
        parser.add_argument("--" + flag, default=os.environ.get("ASTRID_H3_" + env))
    for flag, env in (("capsule", "CAPSULE"), ("source-root", "SOURCE_ROOT"),
                      ("data-root", "DATA_ROOT"), ("owner-credential", "OWNER_CREDENTIAL")):
        parser.add_argument("--" + flag, type=Path, default=os.environ.get("ASTRID_H3_" + env))
    parser.add_argument("--runtime-epoch", type=int, default=os.environ.get("ASTRID_H3_RUNTIME_EPOCH"))
    return parser


def main(
    argv: list[str] | None = None,
    *,
    qualification_factory: QualificationOwnerFactory | None = None,
) -> int:
    args = _parser().parse_args(argv)
    handle_path = (args.handle_path or args.receipt.with_name("claim-handle.json")).resolve()
    output_path = (args.output or args.receipt.with_name("verified-candidate.mkv")).resolve()
    try:
        cleanup_only = False
        if args.resume_settlement:
            try:
                existing = json.loads(args.receipt.read_text(encoding="utf-8"))
                cleanup_only = existing.get("phase") == "cleanup_pending"
            except (OSError, ValueError, AttributeError) as exc:
                raise DeploymentOperationError("resume receipt is unreadable") from exc
        _assert_live_h3_quarantined(
            cleanup_only=cleanup_only, preflight_only=args.preflight_only,
            lane=args.lane, authorized_lane=args.authorize_lane,
        )

        if not cleanup_only:
            identity = assert_product_source_identity(
                expected_branch=args.expected_branch, expected_head=args.expected_head,
                content_digest=args.content_digest, capsule=args.capsule, source_root=args.source_root,
            )
            from scripts.h3_runpod_qualification import H3LaneConfig, default_qualification_factory

            if args.data_root is None or not args.existing_task or not args.expected_run:
                raise DeploymentOperationError("explicit task/run and data root are required")
            if not args.runtime_endpoint or not args.runtime_realm or args.owner_credential is None:
                raise DeploymentOperationError("explicit Runtime endpoint/realm and owner credential are required")
            config = H3LaneConfig(
                lane=args.lane, lane_root=args.lane_root, release_root=args.release_root,
                executor_id=args.executor_id, session_ref=args.session_ref,
                local_data_root=args.data_root, expected_pod=args.expected_pod,
                runtime_instance_id=args.runtime_instance_id, runtime_session_id=args.runtime_session_id,
                runtime_epoch=args.runtime_epoch,
            )
            from astrid.sdk import AstridClient
            with AstridClient.open(
                endpoint=args.runtime_endpoint, credential=args.owner_credential,
                realm_id=args.runtime_realm, actor_id="owner",
                client_name="astrid-h3-lane-preflight", client_version="stage1",
                protocol_version="workspace.v1",
            ) as client:
                result = (qualification_factory or default_qualification_factory)(
                    client=client, handle_path=handle_path, operation_id=args.operation_id,
                    owner_credential=args.owner_credential, lane_config=config,
                    source_identity=identity, task_id=args.existing_task, run_id=args.expected_run,
                    preflight_only=True,
                )
            print(json.dumps({**result, "live_blocker": LIVE_BOUNDARY_BLOCKER}, indent=2, sort_keys=True))
            return 0

        from astrid.sdk import AstridClient
        # Settlement uses the receipt's existing identity; it does not select a
        # candidate, qualify a host, or claim a pod.
        if args.data_root is None:
            raise DeploymentOperationError("cleanup resume requires an explicit data root")
        data_root = args.data_root.expanduser().resolve()
        task_id = args.existing_task or existing.get("task_id")
        run_id = args.expected_run or existing.get("run_id")
        with AstridClient.open_from_launcher(
            start_pack_host=False, data_root=data_root
        ) as client:
            operations = build_concrete_operations(
                client,
                handle_path=handle_path,
                operation_id=args.operation_id,
                python_executable=args.astrid_python,
                owner_credential=args.owner_credential or data_root / "runtime" / "credentials" / "owner.token",
                qualification_factory=None,
            )
            request = DeploymentRequest(
                task_id=task_id,
                run_id=run_id,
                operation_id=args.operation_id,
                receipt_path=args.receipt.resolve(),
                handle_path=handle_path,
                output_path=output_path,
            )
            result = (
                resume_h3_settlement(request, operations)
                if args.resume_settlement
                else run_existing_h3_task(request, operations)
            )
        print(json.dumps(result, indent=2, sort_keys=True))
        return 1 if result.get("status") in {"cleanup_pending", "failed"} else 0
    except (DeploymentOperationError, SourceCustodyError, OSError, ValueError) as exc:
        print(f"H3 lane blocked: {exc}", file=sys.stderr)
        return 2


__all__ = [
    "build_concrete_operations", "build_qualified_owner", "claim_helper_argv",
    "main", "run",
]


if __name__ == "__main__":
    raise SystemExit(main())
