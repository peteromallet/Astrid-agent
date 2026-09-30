"""Validated, digest-bound model-root bindings for VibeComfy execution.

The readiness profile is the existing worker-owned authority for this fact.  This
module only validates and transports the profile's declared binding; it does not
discover models, download them, or create another model registry.  Reusable byte
qualification also needs a trusted deployment-provided ``qualification_identity``
that binds the installed model storage or release. Current readiness profiles do
not provide that identity; absent it, every qualification hashes the full inventory
and no stat-only receipt is reused or issued.
"""

from __future__ import annotations

import hashlib
import json
import os
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Mapping

MODEL_ROOT_ENV = "ASTRID_VIBECOMFY_MODELS_ROOT"
MODEL_ROOT_BINDING_ENV = "ASTRID_VIBECOMFY_MODEL_ROOT_BINDING"
MODEL_ROOT_QUALIFICATION_RECEIPT_DIR_ENV = (
    "ASTRID_VIBECOMFY_MODEL_QUALIFICATION_RECEIPT_DIR"
)
_SHA256 = "sha256:"
_QUALIFICATION_VERIFIER_VERSION = "model-root-bytes-v1"


class ModelRootBindingError(ValueError):
    """A model-root binding is absent, malformed, or no longer true."""


@contextmanager
def use_attested_vibecomfy_models_root(bundle: Any, root: Path) -> Iterator[None]:
    """Compile *bundle* against one verified VibeComfy model root.

    The pinned VibeComfy compiler receives the same root through its explicit
    ``models_root`` keyword; the scoped environment value remains available to
    lower-level runtime consumers. Reject workflow metadata that would silently
    redirect model reconciliation elsewhere.
    """
    workflow = getattr(bundle, "workflow", None)
    metadata = getattr(workflow, "metadata", {})
    if isinstance(metadata, Mapping) and metadata.get("models_root") is not None:
        declared = metadata["models_root"]
        if not isinstance(declared, (str, Path)) or not Path(declared).is_absolute():
            raise ModelRootBindingError("workflow models_root must be an absolute path")
        if Path(declared).resolve() != root.resolve():
            raise ModelRootBindingError(
                "workflow models_root disagrees with the attested model-root binding"
            )

    env_name = "VIBECOMFY_MODELS_ROOT"
    root = _root_path(str(root), verify_files=False)
    previous = os.environ.get(env_name)
    os.environ[env_name] = str(root)
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop(env_name, None)
        else:
            os.environ[env_name] = previous


def _digest(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or not value.startswith(_SHA256):
        raise ModelRootBindingError(f"model-root {label} must be a sha256 digest")
    suffix = value[len(_SHA256):]
    if len(suffix) != 64 or any(char not in "0123456789abcdef" for char in suffix):
        raise ModelRootBindingError(f"model-root {label} must be a lowercase sha256 digest")
    return value


def _canonical_inventory_digest(entries: list[dict[str, Any]]) -> str:
    normalized_entries = []
    for entry in entries:
        normalized = dict(entry)
        if normalized.get("subdir") == ".":
            normalized["subdir"] = ""
        normalized_entries.append(normalized)
    payload = json.dumps(
        normalized_entries,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return _SHA256 + hashlib.sha256(payload).hexdigest()


def _safe_relative(value: Any, *, label: str, allow_empty: bool) -> str:
    if not isinstance(value, str):
        raise ModelRootBindingError(f"model-root {label} must be a string")
    if not allow_empty and not value:
        raise ModelRootBindingError(f"model-root {label} must not be empty")
    if allow_empty and value in {"", "."}:
        return ""
    if "\\" in value:
        raise ModelRootBindingError(f"model-root {label} must use POSIX path components")
    path = Path(value)
    if path.is_absolute() or not path.parts or any(part in {"", ".", ".."} for part in path.parts):
        raise ModelRootBindingError(f"model-root {label} must be a safe relative path")
    return path.as_posix()


def _root_path(value: Any, *, verify_files: bool) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise ModelRootBindingError("model-root path must be a non-empty string")
    if not os.path.isabs(value):
        raise ModelRootBindingError("model-root path must be absolute")
    normalized = os.path.normpath(value)
    if value != normalized:
        raise ModelRootBindingError("model-root path must not contain lexical aliases")
    path = Path(value)
    if path.is_symlink():
        raise ModelRootBindingError("model-root path must not be a symlink alias")
    try:
        resolved = path.resolve(strict=verify_files)
    except OSError as exc:
        raise ModelRootBindingError(f"model-root path is not resolvable: {exc}") from exc
    if resolved != path:
        raise ModelRootBindingError("model-root path resolves through an alias")
    if verify_files and (not path.is_dir() or path.is_symlink()):
        raise ModelRootBindingError("model-root path must be an existing directory")
    return path


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return _SHA256 + digest.hexdigest()


def _stat_identity(path: Path) -> dict[str, int]:
    stat = path.stat()
    return {
        "dev": int(stat.st_dev),
        "inode": int(stat.st_ino),
        "size": int(stat.st_size),
        "mtime_ns": int(stat.st_mtime_ns),
    }


def _qualification_receipt_path(
    receipt_dir: Path,
    root: Path,
    inventory_digest: str,
    qualification_identity: str,
) -> Path:
    key = hashlib.sha256(
        f"{root}\0{inventory_digest}\0{qualification_identity}\0{_QUALIFICATION_VERIFIER_VERSION}".encode()
    ).hexdigest()
    return receipt_dir / f"{key}.json"


def _cached_qualification_matches(
    receipt_dir: Path,
    root: Path,
    inventory: list[dict[str, Any]],
    inventory_digest: str,
    qualification_identity: str | None,
) -> bool:
    if qualification_identity is None:
        return False
    receipt_path = _qualification_receipt_path(
        receipt_dir, root, inventory_digest, qualification_identity
    )
    try:
        value = json.loads(receipt_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return False
    if not isinstance(value, Mapping):
        return False
    if (
        value.get("schema_version") != 1
        or value.get("verifier_version") != _QUALIFICATION_VERIFIER_VERSION
        or value.get("path") != str(root)
        or value.get("inventory_digest") != inventory_digest
        or value.get("qualification_identity") != qualification_identity
    ):
        return False
    raw_files = value.get("files")
    if not isinstance(raw_files, list) or len(raw_files) != len(inventory):
        return False
    expected_files = [
        {"subdir": entry["subdir"], "name": entry["name"], "sha256": entry["sha256"], "size": entry["size"]}
        for entry in inventory
    ]
    observed_files: list[dict[str, Any]] = []
    for raw in raw_files:
        if not isinstance(raw, Mapping):
            return False
        observed_files.append(
            {
                "subdir": raw.get("subdir", ""),
                "name": raw.get("name"),
                "sha256": raw.get("sha256"),
                "size": raw.get("size"),
            }
        )
    if observed_files != expected_files:
        return False
    for raw in raw_files:
        relative = Path(str(raw.get("subdir", ""))) / str(raw.get("name"))
        candidate = root / relative
        try:
            if candidate.is_symlink() or not candidate.is_file():
                return False
            stat = _stat_identity(candidate)
        except OSError:
            return False
        if stat != raw.get("stat"):
            return False
    return True


def _write_qualification_receipt(
    receipt_dir: Path,
    root: Path,
    inventory: list[dict[str, Any]],
    inventory_digest: str,
    qualification_identity: str,
) -> None:
    receipt_dir.mkdir(parents=True, exist_ok=True)
    files: list[dict[str, Any]] = []
    for entry in inventory:
        candidate = root / Path(entry["subdir"]) / entry["name"]
        files.append({**entry, "stat": _stat_identity(candidate)})
    payload = {
        "schema_version": 1,
        "verifier_version": _QUALIFICATION_VERIFIER_VERSION,
        "path": str(root),
        "inventory_digest": inventory_digest,
        "qualification_identity": qualification_identity,
        "files": files,
    }
    destination = _qualification_receipt_path(
        receipt_dir, root, inventory_digest, qualification_identity
    )
    temporary = destination.with_name(f".{destination.name}.tmp")
    temporary.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(destination)


@dataclass(frozen=True)
class ModelRootBinding:
    """The exact root and immutable file inventory admitted for one run."""

    path: Path
    inventory: tuple[dict[str, Any], ...]
    inventory_digest: str
    qualification_identity: str | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "path": str(self.path),
            "inventory": [dict(entry) for entry in self.inventory],
            "inventory_digest": self.inventory_digest,
            "qualification_identity": self.qualification_identity,
        }


def validate_model_root_binding(
    value: Any,
    *,
    verify_files: bool = True,
    qualification_receipt_dir: str | Path | None = None,
) -> ModelRootBinding:
    """Validate one explicit profile binding and optionally its file bytes.

    ``verify_files=True`` is used at host qualification.  Later child/backend
    boundaries can re-check the pinned declaration without hashing a large
    release volume again; the profile hash and inventory digest remain fixed.
    """
    if not isinstance(value, Mapping):
        raise ModelRootBindingError("readiness profile launch.model_root must be an object binding")
    if value.get("schema_version") != 1:
        raise ModelRootBindingError("model-root binding has an unsupported schema")
    path = _root_path(value.get("path"), verify_files=verify_files)
    qualification_identity = value.get("qualification_identity")
    if qualification_identity is not None and (
        not isinstance(qualification_identity, str)
        or not qualification_identity.strip()
    ):
        raise ModelRootBindingError(
            "model-root qualification_identity must be a non-empty deployment identity"
        )
    raw_inventory = value.get("inventory")
    if not isinstance(raw_inventory, list):
        raise ModelRootBindingError("model-root binding inventory must be a list")

    inventory: list[dict[str, Any]] = []
    seen_paths: set[tuple[str, str]] = set()
    for raw in raw_inventory:
        if not isinstance(raw, Mapping):
            raise ModelRootBindingError("model-root inventory entry must be an object")
        if "alias" in raw or "aliases" in raw:
            raise ModelRootBindingError(
                "model-root aliases require an authoritative canonical mapping"
            )
        subdir = _safe_relative(raw.get("subdir", ""), label="inventory subdir", allow_empty=True)
        name = _safe_relative(raw.get("name"), label="inventory name", allow_empty=False)
        if "/" in name:
            raise ModelRootBindingError("model-root inventory name must be a basename")
        size = raw.get("size")
        if isinstance(size, bool) or not isinstance(size, int) or size < 0:
            raise ModelRootBindingError("model-root inventory size must be a non-negative integer")
        sha256 = _digest(raw.get("sha256"), label=f"{subdir}/{name} digest")
        key = (subdir, name)
        if key in seen_paths:
            raise ModelRootBindingError(f"model-root inventory contains a duplicate path: {subdir}/{name}")
        seen_paths.add(key)
        inventory.append({"name": name, "sha256": sha256, "size": size, "subdir": subdir})

    inventory.sort(key=lambda entry: (entry["subdir"], entry["name"]))
    expected_inventory_digest = _digest(value.get("inventory_digest"), label="inventory digest")
    observed_inventory_digest = _canonical_inventory_digest(inventory)
    if expected_inventory_digest != observed_inventory_digest:
        raise ModelRootBindingError(
            "model-root inventory digest does not match its declared entries"
        )

    receipt_dir = (
        Path(qualification_receipt_dir).expanduser()
        if qualification_receipt_dir is not None
        else None
    )
    if (
        verify_files
        and receipt_dir is not None
        and _cached_qualification_matches(
            receipt_dir,
            path,
            inventory,
            expected_inventory_digest,
            qualification_identity,
        )
    ):
        return ModelRootBinding(
            path=path,
            inventory=tuple(inventory),
            inventory_digest=expected_inventory_digest,
            qualification_identity=qualification_identity,
        )

    if verify_files:
        actual_paths: set[tuple[str, str]] = set()
        for child in path.rglob("*"):
            if child.is_symlink():
                raise ModelRootBindingError(
                    f"model-root contains a symlink or shadow path: {child.name}"
                )
            if child.is_file():
                relative = child.relative_to(path)
                subdir = relative.parent.as_posix()
                actual_paths.add(("" if subdir == "." else subdir, relative.name))
        declared_paths = set(seen_paths)
        missing = sorted(declared_paths - actual_paths)
        unlisted = sorted(actual_paths - declared_paths)
        declared_names = {name for _subdir, name in declared_paths}
        shadow_paths = [item for item in unlisted if item[1] in declared_names]
        if shadow_paths:
            rendered = ",".join("/".join(part for part in item if part) for item in shadow_paths)
            raise ModelRootBindingError(
                f"model-root inventory has a shadow file: {rendered}"
            )
        if missing or unlisted:
            details: list[str] = []
            if missing:
                details.append("missing=" + ",".join("/".join(part for part in item if part) for item in missing))
            if unlisted:
                details.append("unlisted=" + ",".join("/".join(part for part in item if part) for item in unlisted))
            raise ModelRootBindingError(
                "model-root inventory is incomplete: " + "; ".join(details)
            )
        for entry in inventory:
            relative = Path(entry["subdir"]) / entry["name"]
            candidate = path / relative
            try:
                resolved_candidate = candidate.resolve(strict=True)
            except OSError as exc:
                raise ModelRootBindingError(
                    f"model-root inventory file is not readable: {relative}"
                ) from exc
            if resolved_candidate != candidate or not candidate.is_file():
                raise ModelRootBindingError(
                    f"model-root inventory file is an alias or not a regular file: {relative}"
                )
            observed_size = candidate.stat().st_size
            if observed_size != entry["size"]:
                raise ModelRootBindingError(
                    f"model-root inventory size mismatch for {relative}"
                )
            if _file_sha256(candidate) != entry["sha256"]:
                raise ModelRootBindingError(
                    f"model-root inventory hash mismatch for {relative}"
                )
            shadows = sorted(
                other
                for other in path.rglob(entry["name"])
                if other.is_file() and other != candidate
            )
            if shadows:
                raise ModelRootBindingError(
                    f"model-root inventory has a shadow file for {entry['name']}"
                )

        if receipt_dir is not None and qualification_identity is not None:
            _write_qualification_receipt(
                receipt_dir,
                path,
                inventory,
                expected_inventory_digest,
                qualification_identity,
            )

    return ModelRootBinding(
        path=path,
        inventory=tuple(inventory),
        inventory_digest=expected_inventory_digest,
        qualification_identity=qualification_identity,
    )


def model_root_binding_from_profile(
    profile: Mapping[str, Any] | None,
    *,
    verify_files: bool = True,
    qualification_receipt_dir: str | Path | None = None,
) -> ModelRootBinding:
    if not isinstance(profile, Mapping):
        raise ModelRootBindingError("VibeComfy readiness profile is missing")
    launch = profile.get("launch")
    if not isinstance(launch, Mapping):
        raise ModelRootBindingError("VibeComfy readiness profile launch facts are missing")
    return validate_model_root_binding(
        launch.get("model_root"),
        verify_files=verify_files,
        qualification_receipt_dir=qualification_receipt_dir,
    )


def model_root_binding_from_environment(*, verify_files: bool = False) -> ModelRootBinding:
    raw = os.environ.get(MODEL_ROOT_BINDING_ENV)
    if not raw:
        raise ModelRootBindingError(f"{MODEL_ROOT_BINDING_ENV} is missing")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ModelRootBindingError(f"{MODEL_ROOT_BINDING_ENV} is not valid JSON") from exc
    receipt_dir = os.environ.get(MODEL_ROOT_QUALIFICATION_RECEIPT_DIR_ENV)
    binding = validate_model_root_binding(
        payload,
        verify_files=verify_files,
        qualification_receipt_dir=receipt_dir,
    )
    if os.environ.get(MODEL_ROOT_ENV) != str(binding.path):
        raise ModelRootBindingError(
            f"{MODEL_ROOT_ENV} disagrees with the attested model-root binding"
        )
    return binding


def canonical_model_inventory_digest(entries: list[dict[str, Any]]) -> str:
    """Expose the deterministic digest recipe for fixture/profile authors."""
    return _canonical_inventory_digest(entries)


__all__ = [
    "MODEL_ROOT_BINDING_ENV",
    "MODEL_ROOT_ENV",
    "MODEL_ROOT_QUALIFICATION_RECEIPT_DIR_ENV",
    "ModelRootBinding",
    "ModelRootBindingError",
    "canonical_model_inventory_digest",
    "model_root_binding_from_environment",
    "model_root_binding_from_profile",
    "use_attested_vibecomfy_models_root",
    "validate_model_root_binding",
]
