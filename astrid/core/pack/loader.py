"""Pack-manifest loading/parsing, discovery, and the packs root."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from astrid.core.pack._common import (
    PackValidationError,
    _optional_string,
)
from astrid.core.pack.definition import PackDefinition
from astrid.core.pack.canonical import (
    CanonicalPackValidationError,
    LEGACY_MANIFEST_NAMES,
    canonical_manifest_path,
    validate_canonical_pack,
)
from astrid.core.pack.permissions import (
    _normalize_pack_permissions,
    _optional_pack_extensions,
)

_LOGGER = logging.getLogger(__name__)

PACK_VALIDATOR_COMMAND = "python3 -m astrid.core.pack.cli validate"

# Fail-closed trust rule (see docs/packs/contract.md, "Quarantine and fail-closed
# packs"). A pack whose manifest fails admission is quarantined: it is excluded
# from every discovered set and reported, while valid neighbours still load.
# The rule never applies to these packs; an invalid manifest there still raises.
#
# * FAIL_CLOSED_PACK_FOLDERS: runtime-owned source folders. ``_core`` is the
#   agent-facing core skill shell; its manifest, if one exists, is part of the
#   runtime contract, not an authoring workspace.
# * REQUIRED_PACK_IDS: packs the runtime marks as required (the taxonomy's
#   ``install_tier: core``). Empty today: no shipped pack is declared required.
#   Decided here by the runtime, never by the manifest itself, so a broken
#   manifest cannot opt itself into or out of fail-closed behaviour.
FAIL_CLOSED_PACK_FOLDERS: frozenset[str] = frozenset({"_core"})
REQUIRED_PACK_IDS: frozenset[str] = frozenset()


@dataclass(frozen=True)
class QuarantinedPack:
    """A source pack excluded from discovery because its manifest failed admission."""

    pack_id: str
    pack_dir: Path
    manifest_path: Path | None
    error: str

    @property
    def fix(self) -> str:
        return f"run the pack validator: {PACK_VALIDATOR_COMMAND} {self.pack_dir}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "pack_id": self.pack_id,
            "state": "quarantined",
            "pack_dir": str(self.pack_dir),
            "manifest_path": str(self.manifest_path) if self.manifest_path is not None else None,
            "error": self.error,
            "fix": self.fix,
        }


@dataclass(frozen=True)
class PackScan:
    """Result of one pack-root scan: admitted packs plus quarantine records."""

    packs: tuple[PackDefinition, ...]
    quarantined: tuple[QuarantinedPack, ...]


def packs_root() -> Path:
    return Path(__file__).resolve().parents[2] / "packs"


DEFAULT_PACKS_ROOT = packs_root()

_LOGGED_QUARANTINES: set[tuple[str, str]] = set()


def _fail_closed(folder_name: str) -> bool:
    return folder_name in FAIL_CLOSED_PACK_FOLDERS or folder_name in REQUIRED_PACK_IDS


def _quarantine_warning(record: QuarantinedPack) -> None:
    key = (str(record.pack_dir), record.error)
    if key in _LOGGED_QUARANTINES:
        return
    _LOGGED_QUARANTINES.add(key)
    _LOGGER.warning(
        "quarantined pack %r: %s; capabilities of this pack are unavailable until it is fixed (%s)",
        record.pack_id,
        record.error,
        record.fix,
    )


def scan_packs(
    root: str | Path | None = None,
    *,
    include_hidden: bool = False,
) -> PackScan:
    """Scan one pack root, quarantining invalid packs instead of aborting.

    Each child directory is admitted independently. A child that fails
    canonical admission is recorded as a :class:`QuarantinedPack` and logged,
    and its valid neighbours still load. The exception is a fail-closed folder
    or required pack: its failure raises :class:`PackValidationError`.
    """
    source_root = Path(root) if root is not None else packs_root()
    if not source_root.is_dir():
        return PackScan((), ())
    packs: list[PackDefinition] = []
    quarantined: list[QuarantinedPack] = []
    seen: dict[str, Path] = {}
    for child in sorted(source_root.iterdir(), key=lambda path: path.name):
        if not child.is_dir() or child.name.startswith(".") or child.name == "__pycache__":
            continue
        try:
            manifest_path = pack_manifest_path(child)
            if manifest_path is None:
                continue
            pack = load_pack_manifest(manifest_path)
        except Exception as exc:  # noqa: BLE001 - per-pack isolation boundary
            if _fail_closed(child.name):
                if isinstance(exc, PackValidationError):
                    raise
                raise PackValidationError(f"{child}: {exc}") from exc
            manifest = child / "pack.yaml"
            record = QuarantinedPack(
                pack_id=child.name,
                pack_dir=child,
                manifest_path=manifest if manifest.is_file() else None,
                error=str(exc),
            )
            quarantined.append(record)
            _quarantine_warning(record)
            continue
        if pack.visibility == "hidden" and not include_hidden:
            continue
        if pack.id in seen:
            raise PackValidationError(f"duplicate pack id {pack.id!r}: {seen[pack.id]} and {manifest_path}")
        seen[pack.id] = manifest_path
        packs.append(pack)
    return PackScan(tuple(packs), tuple(quarantined))


def discover_packs(
    root: str | Path | None = None,
    *,
    include_hidden: bool = False,
) -> tuple[PackDefinition, ...]:
    """Return the admitted packs under ``root``; invalid packs are quarantined.

    Quarantine records are available from :func:`scan_packs` and are logged
    once per process. Fail-closed packs (see ``FAIL_CLOSED_PACK_FOLDERS``)
    still raise.
    """
    return scan_packs(root, include_hidden=include_hidden).packs


def _raw_declared_capabilities(manifest_path: Path | None) -> tuple[str, ...]:
    """Read the declared capability labels of a manifest that failed admission.

    Used only to attribute a capability id to a quarantined pack. It never
    admits anything, so it does not need to validate the rest of the manifest.
    """
    if manifest_path is None:
        return ()
    try:
        raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return ()
    labels = raw.get("capabilities") if isinstance(raw, dict) else None
    if not isinstance(labels, list):
        return ()
    return tuple(str(label) for label in labels if isinstance(label, str))


def quarantined_pack_for_capability(
    capability_id: str,
    roots: tuple[str | Path, ...] | None = None,
) -> QuarantinedPack | None:
    """Return the quarantined pack that declares ``capability_id``, if any.

    Matches a qualified id (``<pack>.<name>``) by its pack prefix, and a bare or
    qualified id against the labels the broken manifest declares. Called only on
    the not-found path, so the extra scan costs nothing on success.
    """
    scan_roots = roots if roots is not None else _default_pack_roots()
    for root in scan_roots:
        for record in scan_packs(root).quarantined:
            if capability_id.startswith(f"{record.pack_id}."):
                return record
            labels = _raw_declared_capabilities(record.manifest_path)
            if capability_id in labels or any(capability_id == f"{record.pack_id}.{label}" for label in labels):
                return record
    return None


def _default_pack_roots() -> tuple[Path, ...]:
    raw_env = os.environ.get("ASTRID_PACKS_PATH", "")
    env_roots = tuple(Path(item).expanduser() for item in raw_env.split(os.pathsep) if item)
    return (packs_root(), *env_roots)


def pack_quarantine_section() -> dict[str, Any]:
    """Doctor section: quarantined packs with errors and fixes. Never raises.

    A fail-closed pack (``_core`` or a required pack) is reported as
    ``fail_closed_error`` instead, because discovery raises for it.
    """
    try:
        records = pack_quarantine_report()
    except PackValidationError as exc:
        return {"count": 0, "quarantined": [], "fail_closed_error": str(exc)}
    return {"count": len(records), "quarantined": records, "fail_closed_error": None}


def pack_quarantine_report(roots: tuple[str | Path, ...] | None = None) -> list[dict[str, Any]]:
    """Return JSON-shaped quarantine records for the source tree and env roots.

    Read-only. Raises only for a fail-closed pack, exactly as discovery does.
    """
    scan_roots = roots if roots is not None else _default_pack_roots()
    records: list[dict[str, Any]] = []
    for root in scan_roots:
        records.extend(record.to_dict() for record in scan_packs(root).quarantined)
    return records


def load_pack_manifest(path: str | Path, *, expected_pack_id: str | None = None) -> PackDefinition:
    """Load one strict-v2 capability manifest through the canonical parser."""
    manifest_path = Path(path).expanduser().resolve()
    if manifest_path.name != "pack.yaml" or not manifest_path.is_file():
        raise PackValidationError(
            f"canonical pack admission requires a regular pack.yaml, got {manifest_path}"
        )
    try:
        entry = validate_canonical_pack(
            manifest_path.parent, expected_pack_id=expected_pack_id
        )
    except CanonicalPackValidationError as exc:
        raise PackValidationError(str(exc)) from exc
    definition = entry.definition
    data = definition.to_dict()
    taxonomy = pack_taxonomy_from_manifest(data, status=definition.status)
    return PackDefinition(
        id=definition.id,
        name=definition.name,
        version=definition.version,
        root=entry.root,
        manifest_path=manifest_path,
        metadata={},
        description=definition.description,
        content=dict(definition.content),
        agent=dict(data["agent"]),
        status=definition.status,
        visibility=definition.visibility,
        schema_version="2",
        aliases=tuple(dict(alias) for alias in data["aliases"]),
        permissions=_normalize_pack_permissions(data["permissions"]),
        extensions=_optional_pack_extensions(data["extensions"], path="pack.extensions"),
        **taxonomy,
    )


def pack_taxonomy_from_manifest(data: dict[str, Any], *, status: str) -> dict[str, str]:
    """Return the deterministic taxonomy projection for a pack manifest.

    These defaults are the M1 taxonomy baseline for manifests that do not yet
    declare an explicit taxonomy block.
    """
    return {
        "origin": _optional_string(data, "origin", "pack.origin", default="unknown"),
        "install_tier": _optional_string(data, "install_tier", "pack.install_tier", default="default"),
        "pack_type": _optional_string(data, "pack_type", "pack.pack_type", default="capability"),
        "domain": _optional_string(data, "domain", "pack.domain", default="general"),
        "stability": _optional_string(
            data,
            "stability",
            "pack.stability",
            default=_default_stability_for_status(status),
        ),
        "support": _optional_string(data, "support", "pack.support", default="project"),
    }


def _default_stability_for_status(status: str) -> str:
    if status == "experimental":
        return "experimental"
    if status == "deprecated":
        return "deprecated"
    return "stable"


def pack_manifest_path(root: str | Path) -> Path | None:
    pack_root = Path(root)
    legacy = sorted(name for name in LEGACY_MANIFEST_NAMES if (pack_root / name).exists())
    if legacy and not (pack_root / "pack.yaml").exists():
        raise PackValidationError(
            f"canonical pack admission requires pack.yaml; found alternate manifest(s): {', '.join(legacy)}"
        )
    try:
        return canonical_manifest_path(pack_root)
    except CanonicalPackValidationError as exc:
        raise PackValidationError(str(exc)) from exc


def _load_manifest_payload(path: Path) -> dict[str, Any]:
    """Read a YAML mapping for internal static-inspection helpers.

    Pack admission remains strict in :func:`load_pack_manifest`; this helper
    also reads non-pack catalogs such as ``models.yaml`` and renderer
    manifests while building the capability ledger.
    """
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise PackValidationError(f"manifest not found: {path}") from exc
    except yaml.YAMLError as exc:
        raise PackValidationError(f"invalid YAML manifest {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise PackValidationError(f"manifest must contain a YAML object: {path}")
    return data
