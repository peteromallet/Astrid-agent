"""Typed, deterministic inputs for the one GenericHost launcher.

The Runtime owns admission and credentials; Astrid owns the local filesystem
adaptation needed to start the already-admitted host.  This module is a small
boundary object, not a second scheduler or authority.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping


class HostLaunchSpecError(ValueError):
    """The local handoff cannot be projected into a safe host launch."""


def _path(value: Any, field: str, *, regular: bool | None = None) -> Path:
    try:
        path = Path(value).expanduser()
    except (TypeError, ValueError) as exc:
        raise HostLaunchSpecError(f"{field} must be a path") from exc
    if not path.is_absolute() or path.is_symlink():
        raise HostLaunchSpecError(f"{field} must be an absolute non-symlink path")
    if regular is True and not path.is_file():
        raise HostLaunchSpecError(f"{field} must be a regular file")
    if regular is False and not path.is_dir():
        raise HostLaunchSpecError(f"{field} must be a directory")
    return path.resolve(strict=False)


def _executable_path(value: Any) -> Path:
    """Keep the selected venv spelling; do not collapse its symlink."""
    try:
        path = Path(value).expanduser()
    except (TypeError, ValueError) as exc:
        raise HostLaunchSpecError("executable must be a path") from exc
    if not path.is_absolute():
        raise HostLaunchSpecError("executable must be absolute")
    return path.absolute()


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise HostLaunchSpecError(f"{field} is required")
    value = value.strip()
    if any(ord(char) < 32 for char in value):
        raise HostLaunchSpecError(f"{field} contains a control character")
    return value


def _optional_text(value: Any, field: str) -> str | None:
    if value is None or value == "":
        return None
    return _text(value, field)


def _bare_sha256(value: Any, field: str) -> str:
    digest = _text(value, field).removeprefix("sha256:").lower()
    if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
        raise HostLaunchSpecError(f"{field} must be a SHA-256 digest")
    return digest


def _credential_path(value: Any) -> Path:
    raw = _text(value, "credential file")
    if raw.startswith("file:"):
        raw = raw[5:]
    path = _path(raw, "credential file", regular=True)
    try:
        metadata = path.lstat()
        if metadata.st_uid != os.getuid() or stat.S_IMODE(metadata.st_mode) != 0o600:
            raise HostLaunchSpecError("credential file must be owner-owned with mode 0600")
    except OSError as exc:
        raise HostLaunchSpecError("credential file is unavailable") from exc
    return path


def _target(value: Any) -> dict[str, Any] | None:
    if value is None or value == "":
        return None
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError) as exc:
            raise HostLaunchSpecError("execution target must be valid JSON") from exc
    if not isinstance(value, Mapping) or not value:
        raise HostLaunchSpecError("execution target must be a non-empty object")
    try:
        encoded = json.dumps(dict(value), sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise HostLaunchSpecError("execution target must be JSON-compatible") from exc
    return json.loads(encoded)


def _env_digest(items: Mapping[str, str]) -> str:
    payload = json.dumps(dict(sorted(items.items())), sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class HostLaunchSpec:
    """The complete, immutable input to the existing GenericHost process."""

    executable: Path
    cwd: Path
    source_checkout: Path
    source_checkout_digest: str
    source_closure_digest: str
    pack_roots: tuple[Path, ...]
    source_inventory_identity: str
    runtime_endpoint: str
    runtime_instance_id: str
    runtime_epoch: int
    runtime_schema_digest: str
    credential_file: Path
    executor_id: str
    max_concurrency: int
    ready_file: Path
    support_root: Path
    data_root: Path
    boot_manifest_path: Path
    boot_manifest_hash: str
    readiness_profile_path: Path | None = None
    readiness_profile_hash: str | None = None
    capability_matrix: Path | None = None
    model_root: Path | None = None
    session_ref: str | None = None
    output_root: Path | None = None
    execution_target: Mapping[str, Any] | None = None
    register: bool = True

    @classmethod
    def from_legacy(
        cls,
        value: Mapping[str, Any],
        *,
        executable: Path,
        cwd: Path,
        source_checkout: Path,
        source_checkout_digest: str,
        source_closure_digest: str,
        pack_roots: tuple[Path, ...],
        source_inventory_identity: str,
        runtime_endpoint: str,
        runtime_instance_id: str,
        runtime_epoch: int,
        runtime_schema_digest: str,
        ready_file: Path,
        support_root: Path,
        data_root: Path,
        boot_manifest_path: Path,
        boot_manifest_hash: str,
        readiness_profile_path: str | None,
        readiness_profile_hash: str | None,
        capability_matrix: Path | None,
    ) -> "HostLaunchSpec":
        """Normalize the existing neutral-runtime handoff without changing it."""
        if not isinstance(value, Mapping):
            raise HostLaunchSpecError("legacy handoff must be an object")
        credential_values = [
            item for item in (value.get("worker_credential_file"), value.get("credential_ref"))
            if item not in (None, "")
        ]
        if not credential_values:
            raise HostLaunchSpecError("credential file is required")
        credentials = {_credential_path(item) for item in credential_values}
        if len(credentials) != 1:
            raise HostLaunchSpecError("credential file and credential_ref conflict")

        profile_values = {
            key: value.get(key)
            for key in ("readiness_profile_path", "readiness_profile_hash")
            if value.get(key) not in (None, "")
        }
        if profile_values and (
            profile_values.get("readiness_profile_path") != readiness_profile_path
            or profile_values.get("readiness_profile_hash") != readiness_profile_hash
        ):
            raise HostLaunchSpecError("legacy readiness profile conflicts with selected profile")

        targets = [
            item for item in (
                value.get("execution_target_json"),
                value.get("execution_target"),
                value.get("target"),
            ) if item not in (None, "")
        ]
        normalized_targets = [_target(item) for item in targets]
        if normalized_targets and any(item != normalized_targets[0] for item in normalized_targets[1:]):
            raise HostLaunchSpecError("legacy execution target fields conflict")
        if value.get("target_ref") not in (None, "") and not normalized_targets:
            raise HostLaunchSpecError("target_ref requires the exact structured execution target")

        model_values = [
            item for item in (value.get("model_root"), value.get("vibecomfy_models_root"))
            if item not in (None, "")
        ]
        if len({_path(item, "model root") for item in model_values}) > 1:
            raise HostLaunchSpecError("model root fields conflict")
        model_root = _path(model_values[0], "model root") if model_values else None
        output_root = value.get("output_root")
        session_ref = value.get("session_ref")
        return cls(
            executable=executable,
            cwd=cwd,
            source_checkout=source_checkout,
            source_checkout_digest=source_checkout_digest,
            source_closure_digest=source_closure_digest,
            pack_roots=pack_roots,
            source_inventory_identity=source_inventory_identity,
            runtime_endpoint=runtime_endpoint,
            runtime_instance_id=runtime_instance_id,
            runtime_epoch=runtime_epoch,
            runtime_schema_digest=runtime_schema_digest,
            credential_file=next(iter(credentials)),
            executor_id=str(value.get("executor_id") or "astrid-pack-host"),
            max_concurrency=2,
            ready_file=ready_file,
            support_root=support_root,
            data_root=data_root,
            boot_manifest_path=boot_manifest_path,
            boot_manifest_hash=boot_manifest_hash,
            readiness_profile_path=readiness_profile_path,
            readiness_profile_hash=readiness_profile_hash,
            capability_matrix=capability_matrix,
            model_root=model_root,
            session_ref=_optional_text(session_ref, "session ref"),
            output_root=_path(output_root, "output root") if output_root not in (None, "") else None,
            execution_target=normalized_targets[0] if normalized_targets else None,
            register=True,
        )

    def __post_init__(self) -> None:
        object.__setattr__(self, "executable", _executable_path(self.executable))
        for field in (
            "cwd", "source_checkout", "credential_file", "ready_file",
            "support_root", "data_root", "boot_manifest_path",
        ):
            object.__setattr__(self, field, _path(getattr(self, field), field))
        object.__setattr__(self, "pack_roots", tuple(_path(item, "pack root") for item in self.pack_roots))
        for field in ("readiness_profile_path", "capability_matrix", "model_root", "output_root"):
            value = getattr(self, field)
            if value is not None:
                object.__setattr__(self, field, _path(value, field))
        object.__setattr__(self, "source_checkout_digest", _bare_sha256(self.source_checkout_digest, "source checkout digest"))
        object.__setattr__(self, "source_closure_digest", _bare_sha256(self.source_closure_digest, "source closure digest"))
        object.__setattr__(self, "boot_manifest_hash", _text(self.boot_manifest_hash, "boot manifest hash"))
        object.__setattr__(self, "runtime_endpoint", _text(self.runtime_endpoint, "runtime endpoint").rstrip("/"))
        object.__setattr__(self, "runtime_instance_id", _text(self.runtime_instance_id, "runtime instance id"))
        object.__setattr__(self, "runtime_schema_digest", _text(self.runtime_schema_digest, "runtime schema digest"))
        object.__setattr__(self, "executor_id", _text(self.executor_id, "executor id"))
        object.__setattr__(self, "source_inventory_identity", str(self.source_inventory_identity or ""))
        # The neutral-runtime handoff is authoritative for the existing local
        # layout.  Canonical /runtime placement is verified by the bootstrap's
        # existing handoff/readiness checks when the runtime supplies it; the
        # projector must remain usable for its profile-free CPU fixtures too.
        if self.readiness_profile_path is None and self.readiness_profile_hash is not None:
            raise HostLaunchSpecError("readiness profile path and hash must be supplied together")
        if self.readiness_profile_path is not None and not self.readiness_profile_hash:
            raise HostLaunchSpecError("readiness profile path and hash must be supplied together")
        if isinstance(self.max_concurrency, bool) or self.max_concurrency != 2:
            raise HostLaunchSpecError("max concurrency must be the canonical value 2")
        object.__setattr__(self, "execution_target", _target(self.execution_target))
        if self.model_root is not None and self.model_root.is_symlink():
            raise HostLaunchSpecError("model root must not be a symlink")

    def project(self) -> "ProjectedHostLaunch":
        argv: list[str] = [
            str(self.executable), "-m", "astrid.core.execution.generic_host", "run",
        ]
        for root in self.pack_roots:
            argv.extend(("--pack-root", str(root)))
        argv.extend((
            "--runtime-endpoint", self.runtime_endpoint,
            "--credential-file", str(self.credential_file),
            "--executor-id", self.executor_id,
            "--max-concurrency", str(self.max_concurrency),
            "--ready-file", str(self.ready_file),
            "--support-root", str(self.support_root),
            "--source-checkout", str(self.source_checkout),
            "--source-checkout-digest", self.source_checkout_digest,
            "--source-closure-digest", self.source_closure_digest,
            "--runtime-instance-id", self.runtime_instance_id,
            "--source-inventory-identity", self.source_inventory_identity,
            "--boot-manifest-path", str(self.boot_manifest_path),
            "--boot-manifest-hash", self.boot_manifest_hash,
        ))
        if self.register:
            argv.append("--register")
        if self.readiness_profile_path is not None:
            argv.extend(("--readiness-profile-path", str(self.readiness_profile_path), "--readiness-profile-hash", str(self.readiness_profile_hash)))
        if self.capability_matrix is not None:
            argv.extend(("--capability-matrix", str(self.capability_matrix)))
        env: dict[str, str] = {
            "BANODOCO_LOCAL_DATA_ROOT": str(self.data_root),
            "ASTRID_RUNTIME_ENDPOINT": self.runtime_endpoint,
            "ASTRID_RUNTIME_INSTANCE_ID": self.runtime_instance_id,
            "ASTRID_RUNTIME_EPOCH": str(self.runtime_epoch),
            "ASTRID_RUNTIME_SCHEMA_DIGEST": self.runtime_schema_digest,
            "ASTRID_CREDENTIAL_REF": str(self.credential_file),
        }
        if self.model_root is not None:
            env["ASTRID_VIBECOMFY_MODELS_ROOT"] = str(self.model_root)
        if self.session_ref is not None:
            env["ASTRID_SESSION_REF"] = self.session_ref
        if self.output_root is not None:
            env["ASTRID_OUTPUT_ROOT"] = str(self.output_root)
        if self.execution_target is not None:
            env["ASTRID_EXECUTION_TARGET_JSON"] = json.dumps(self.execution_target, sort_keys=True, separators=(",", ":"))
        env["ASTRID_HOST_LAUNCH_ENV_DIGEST"] = _env_digest(env)
        return ProjectedHostLaunch(tuple(argv), self.cwd, MappingProxyType(dict(sorted(env.items()))))


@dataclass(frozen=True)
class ProjectedHostLaunch:
    argv: tuple[str, ...]
    cwd: Path
    env: Mapping[str, str]


def project_launch(spec: HostLaunchSpec) -> ProjectedHostLaunch:
    """Project one validated host spec into argv, environment, and cwd."""
    if not isinstance(spec, HostLaunchSpec):
        raise HostLaunchSpecError("project_launch requires a HostLaunchSpec")
    return spec.project()


__all__ = ["HostLaunchSpec", "HostLaunchSpecError", "ProjectedHostLaunch", "project_launch"]
