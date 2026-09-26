"""Astrid-side adapter for the canonical Runtime local compatibility contract.

The Runtime package is the owner of environment translation and lifecycle
identity.  Astrid keeps this adapter narrow: it can consume the Runtime
identity receipt and validate that aliases still describe the same owner.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import os
from pathlib import Path
import sys
from typing import Any


CANONICAL_ENVIRONMENT_NAMES = (
    "ASTRID_LOCAL_DATA_ROOT",
    "ASTRID_LOCAL_HOME",
    "ASTRID_LOCAL_SOURCE_MANIFEST",
    "ASTRID_LOCAL_LAUNCHER",
    "ASTRID_LOCAL_ENABLE_REAL_REBOOT",
    "ASTRID_RUNTIME_ADMISSION_TIMEOUT_SECONDS",
)

LEGACY_ENVIRONMENT_NAMES = (
    "BANODOCO_LOCAL_DATA_ROOT",
    "BANODOCO_LOCAL_HOME",
    "BANODOCO_LOCAL_SOURCE_MANIFEST",
    "BANODOCO_LOCAL_LAUNCHER",
    "BANODOCO_LOCAL_ENABLE_REAL_REBOOT",
    "BANODOCO_RUNTIME_ADMISSION_TIMEOUT_SECONDS",
    "ASTRID_RUNTIME_CLI",
    "BANODOCO_RUNTIME_ENDPOINT",
    "BANODOCO_RUNTIME_CREDENTIAL",
)

ENVIRONMENT_ALIASES = dict(zip(
    CANONICAL_ENVIRONMENT_NAMES + (
        "ASTRID_LOCAL_CLI", "ASTRID_RUNTIME_ENDPOINT", "ASTRID_RUNTIME_CREDENTIAL",
    ),
    LEGACY_ENVIRONMENT_NAMES,
))

_EMITTED_WARNINGS: set[str] = set()

_IDENTITY_FIELDS = (
    "implementation_owner",
    "module_origin",
    "artifact_sha256",
    "support_root",
    "realm_id",
)


class LocalCompatibilityError(ValueError):
    """A Runtime identity receipt cannot be safely consumed by Astrid."""


@dataclass(frozen=True)
class EnvironmentResolution:
    values: dict[str, str]
    warnings: tuple[str, ...]


def resolve_environment(
    environ: Mapping[str, str] | None = None,
) -> EnvironmentResolution:
    """Resolve canonical Astrid names and legacy Banodoco aliases."""

    source = os.environ if environ is None else environ
    values: dict[str, str] = {}
    warnings: list[str] = []
    for canonical, legacy in ENVIRONMENT_ALIASES.items():
        canonical_value = str(source.get(canonical, "") or "")
        legacy_value = str(source.get(legacy, "") or "")
        if canonical_value and legacy_value and canonical_value != legacy_value:
            raise LocalCompatibilityError(
                f"conflicting environment values for {canonical} and {legacy}"
            )
        value = canonical_value or legacy_value
        if value:
            if canonical.endswith(("_DATA_ROOT", "_HOME", "_SOURCE_MANIFEST")):
                if not Path(value).expanduser().is_absolute():
                    raise LocalCompatibilityError(f"{canonical} must be an absolute path")
            values[canonical] = value
        if legacy_value and not canonical_value:
            warnings.append(f"{legacy} is deprecated; use {canonical} instead")
    return EnvironmentResolution(values=values, warnings=tuple(warnings))


def emit_environment_warnings(
    resolution: EnvironmentResolution | None = None,
) -> None:
    """Print each compatibility warning once per process."""

    current = resolution or resolve_environment()
    for warning in current.warnings:
        if warning in _EMITTED_WARNINGS:
            continue
        print(f"astrid-local: warning: {warning}", file=sys.stderr)
        _EMITTED_WARNINGS.add(warning)


def canonical_value(name: str, environ: Mapping[str, str] | None = None) -> str:
    """Read one setting through the canonical migration resolver."""

    canonical = name
    if name in ENVIRONMENT_ALIASES.values():
        canonical = next(key for key, value in ENVIRONMENT_ALIASES.items() if value == name)
    resolution = resolve_environment(environ)
    emit_environment_warnings(resolution)
    return resolution.values.get(canonical, "")


def legacy_environment_name(name: str) -> str:
    """Return the compatibility spelling for an explicit child-process key."""

    if name not in ENVIRONMENT_ALIASES:
        raise LocalCompatibilityError(f"no compatibility alias is registered for {name}")
    return ENVIRONMENT_ALIASES[name]


def project_runtime_identity(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Return the bounded identity fields Astrid may display or compare."""

    if not isinstance(payload, Mapping):
        raise LocalCompatibilityError("Runtime identity must be an object")
    missing = [field for field in _IDENTITY_FIELDS if field not in payload]
    if missing:
        raise LocalCompatibilityError(
            "Runtime identity is missing: " + ", ".join(missing)
        )
    owner = str(payload["implementation_owner"] or "")
    if owner != "banodoco_local.cli:main":
        raise LocalCompatibilityError("Runtime identity owner is not canonical")
    digest = str(payload["artifact_sha256"] or "")
    if not digest.startswith("sha256:") or len(digest) != len("sha256:") + 64:
        raise LocalCompatibilityError("Runtime identity artifact digest is invalid")
    return {field: payload[field] for field in _IDENTITY_FIELDS}


def assert_same_runtime_identity(
    identities: list[Mapping[str, Any]],
) -> dict[str, Any]:
    """Check that all accepted aliases describe one Runtime implementation."""

    if not identities:
        raise LocalCompatibilityError("at least one Runtime identity is required")
    projected = [project_runtime_identity(identity) for identity in identities]
    first = projected[0]
    if any(item != first for item in projected[1:]):
        raise LocalCompatibilityError("Runtime aliases disagree on implementation identity")
    return first
