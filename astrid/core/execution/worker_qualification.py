"""CPU-side validation of an already-produced worker qualification receipt.

This module deliberately does not contact RunPod or Runtime.  The deployment
owner produces the receipt; H3 consumes it before admitting the GPU child.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Mapping


class WorkerQualificationError(ValueError):
    """A worker receipt is absent, incomplete, or bound to another target."""


_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")


def _digest(value: Any, label: str) -> str:
    if not isinstance(value, str) or not _DIGEST.fullmatch(value):
        raise WorkerQualificationError(f"worker qualification {label} must be a sha256 digest")
    return value


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise WorkerQualificationError(f"worker qualification {label} is required")
    return value.strip()


_DIRECT_VOLUME_FIELDS = ("backup_volume_id", "network_volume_id", "volume_id")


def _target(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise WorkerQualificationError("worker qualification target is required")
    if value.get("kind") != "runpod":
        raise WorkerQualificationError("worker qualification target must be runpod")
    target: dict[str, Any] = {
        "kind": "runpod",
        "pod_id": _text(value.get("pod_id"), "target.pod_id"),
        "provider_account_ref": _text(
            value.get("provider_account_ref"), "target.provider_account_ref"
        ),
    }
    for field in _DIRECT_VOLUME_FIELDS:
        if field in value:
            target[field] = _text(value[field], f"target.{field}")
    if "storage" in value:
        storage = value["storage"]
        if not isinstance(storage, Mapping):
            raise WorkerQualificationError("worker qualification target.storage must be an object")
        for field in _DIRECT_VOLUME_FIELDS:
            if field in storage:
                nested_volume_id = _text(
                    storage[field], f"target.storage.{field}"
                )
                direct_volume_id = target.get(field)
                if direct_volume_id is not None and direct_volume_id != nested_volume_id:
                    raise WorkerQualificationError(
                        f"worker qualification direct and nested {field} values disagree"
                    )
        # Preserve the supplied object and all of its fields exactly.
        target["storage"] = storage
    return target


def _targets_agree(expected: Mapping[str, Any], qualified: Mapping[str, Any]) -> bool:
    # Qualification emits the complete scheduling target. Exact comparison
    # prevents omitted or additional volume identity from being silently
    # accepted when a request already pins its target.
    expected_target = {
        key: expected[key]
        for key in (
            "kind", "pod_id", "provider_account_ref",
            *_DIRECT_VOLUME_FIELDS, "storage",
        )
        if key in expected
    }
    qualified_target = {
        key: qualified[key]
        for key in (
            "kind", "pod_id", "provider_account_ref",
            *_DIRECT_VOLUME_FIELDS, "storage",
        )
        if key in qualified
    }
    return expected_target == qualified_target


def ensure_runpod_worker(
    qualification: Mapping[str, Any],
    *,
    expected_target: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate a deployment-owned qualified-worker receipt.

    The returned mapping is safe to place in ``execution_request.target``.
    No provider or Runtime operation occurs here; a missing or stale receipt
    fails before task admission.
    """

    if not isinstance(qualification, Mapping):
        raise WorkerQualificationError("worker qualification must be an object")
    if qualification.get("schema_version") != 1:
        raise WorkerQualificationError("worker qualification schema_version must be 1")
    if qualification.get("status") != "qualified":
        raise WorkerQualificationError("worker qualification status must be qualified")
    if qualification.get("worker_ready") is not True:
        raise WorkerQualificationError("worker qualification is not ready")
    target = _target(qualification.get("target"))
    if expected_target is not None and (
        not isinstance(expected_target, Mapping)
        or not _targets_agree(expected_target, target)
    ):
        raise WorkerQualificationError("worker qualification target disagrees with execution request")
    _text(qualification.get("qualification_id"), "qualification_id")
    _text(qualification.get("runtime_instance_id"), "runtime_instance_id")
    _text(qualification.get("runtime_session_id"), "runtime_session_id")
    epoch = qualification.get("runtime_epoch")
    if type(epoch) is not int or epoch <= 0:
        raise WorkerQualificationError("worker qualification runtime_epoch must be positive")
    _digest(qualification.get("capability_digest"), "capability_digest")
    _digest(qualification.get("readiness_profile_hash"), "readiness_profile_hash")
    _digest(qualification.get("model_root_digest"), "model_root_digest")
    _text(qualification.get("output_root"), "output_root")
    return {
        "kind": "runpod",
        "pod_id": target["pod_id"],
        "provider_account_ref": target["provider_account_ref"],
        **{field: target[field] for field in (*_DIRECT_VOLUME_FIELDS, "storage") if field in target},
        "qualification_id": str(qualification["qualification_id"]),
        "runtime_instance_id": str(qualification["runtime_instance_id"]),
        "runtime_session_id": str(qualification["runtime_session_id"]),
        "runtime_epoch": epoch,
        "capability_digest": str(qualification["capability_digest"]),
        "readiness_profile_hash": str(qualification["readiness_profile_hash"]),
        "model_root_digest": str(qualification["model_root_digest"]),
        "output_root": str(qualification["output_root"]),
    }


def load_worker_qualification(path: str | Path) -> dict[str, Any]:
    source = Path(path).expanduser().resolve(strict=True)
    if source.is_symlink() or not source.is_file():
        raise WorkerQualificationError("worker qualification must be a regular file")
    try:
        value = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise WorkerQualificationError(f"cannot read worker qualification: {exc}") from exc
    if not isinstance(value, dict):
        raise WorkerQualificationError("worker qualification must contain an object")
    return value


def qualification_digest(value: Mapping[str, Any]) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


__all__ = [
    "WorkerQualificationError",
    "ensure_runpod_worker",
    "load_worker_qualification",
    "qualification_digest",
]
