"""Shared hash helpers for Astrid core."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any


# Discovery attaches these fields so a process can locate and execute a
# capability locally. They are not part of the capability's portable
# contract: the same checkout is routinely installed under different roots
# on the developer machine, a Runtime host, and a RunPod worker.
_DISCOVERY_METADATA_KEYS = frozenset(
    {
        "content_root",
        "folder_id",
        "manifest_file",
        "orchestrator_file",
        "orchestrator_root",
        "pack_id",
        "pack_root",
        "priority",
        "pyproject_file",
        "requirements_file",
        "source",
        "source_pack",
        "stage_file",
    }
)


def sha256_file(path: Path) -> str:
    """Return the SHA-256 hex digest of *path* using 1 MB chunked reads."""

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_digest(digest: object) -> str:
    """Validate and return one lowercase bare SHA-256 digest.

    This is a neutral byte-identity primitive.  It deliberately has no
    filesystem, CAS, project, or media-ingest knowledge so live consumers can
    validate runtime-managed object handoffs without importing retired storage
    code.
    """

    if not isinstance(digest, str):
        raise TypeError("digest must be a string")
    if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
        raise ValueError("digest must be a lowercase 64-hex SHA-256")
    return digest


def canonical_json_digest(obj: Any) -> str:
    """Return the stable digest used by protocol identity contracts."""

    raw = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def capability_identity_projection(value: Any) -> Any:
    """Return the portable, execution-semantic view of a capability.

    Capability definitions contain discovery metadata alongside their actual
    contract. The former includes filesystem locations and source precedence
    details; those must not affect admission identity. Keep the projection
    deliberately narrow so new semantic fields still participate by default.
    """

    if isinstance(value, Mapping):
        projected: dict[str, Any] = {}
        for key, item in value.items():
            if key == "metadata" and isinstance(item, Mapping):
                item = {
                    metadata_key: metadata_value
                    for metadata_key, metadata_value in item.items()
                    if metadata_key not in _DISCOVERY_METADATA_KEYS
                }
            projected[str(key)] = capability_identity_projection(item)
        return projected
    if isinstance(value, (list, tuple)):
        return [capability_identity_projection(item) for item in value]
    return value


def executor_definition_digest(executor_def: Any) -> str:
    """Digest an executor definition without consulting storage."""

    return canonical_json_digest(capability_identity_projection(executor_def.to_dict()))


__all__ = [
    "capability_identity_projection",
    "canonical_json_digest",
    "executor_definition_digest",
    "sha256_file",
    "validate_digest",
]
