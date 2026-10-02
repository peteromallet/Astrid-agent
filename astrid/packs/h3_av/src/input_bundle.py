"""Portable H3 dependencies using the existing verified asset archive contract.

Only asset IDs declared by the request are resolved. JSON strings are never
scanned for paths. Each executor derives private paths from the same immutable
bundle; preparation records carry member identities, not previous-attempt paths.
"""

from __future__ import annotations

import hashlib
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

from astrid.packs.vibecomfy.asset_manifest import read_archive

from .compile import _write_asset_bundle
from .prepare import PreparationError, _asset_ids
from .request import H3Request, normalize_request


def primary_baseline_asset(preparation: Mapping[str, Any]) -> str | None:
    """Select the existing main baseline without importing a new graph model."""
    request = normalize_request(preparation["request"])
    if request.value.get("version") != 2:
        source = request.value.get("source")
        return str(source["asset"]) if source else None
    from .request_v2 import branch_for
    if branch_for(request) in {"source_free", "audio_only"}:
        return None
    videos = [item for item in request.value["media"]
              if item["role"] == "timeline" and item.get("modality") == "video"]
    return str(videos[0]["asset"]) if len(videos) == 1 else None


def bundle_generated_pair(video: Path, audio: Path, destination: Path) -> Path:
    """Adapt managed video/audio ports to main's existing composition archive."""
    import json
    import zipfile
    records = []
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destination, "w") as archive:
        for role, path in (("video", video), ("audio", audio)):
            data = path.read_bytes()
            member = f"outputs/{role}-{path.name}"
            records.append({"role": role, "member": member, "filename": path.name,
                            "sha256": hashlib.sha256(data).hexdigest(), "size": len(data)})
            archive.writestr(member, data)
        archive.writestr("manifest.json", json.dumps({
            "schema_version": 1, "kind": "h3_av_generated_av", "outputs": records,
        }, sort_keys=True))
    return destination


def build_input_bundle(request: H3Request, asset_map: Mapping[str, Any], destination: Path) -> Path:
    bindings: dict[str, Path] = {}
    for asset_id in _asset_ids(request):
        value = asset_map.get(asset_id)
        if not isinstance(value, str) or not value:
            raise PreparationError(f"asset {asset_id!r} needs a file in asset_map before task submission")
        bindings[asset_id] = Path(value).expanduser().resolve(strict=True)
    _write_asset_bundle(destination, bindings)
    # Re-read the completed bytes before importing: also catches a file changed
    # between manifest construction and archive writing.
    read_archive(destination)
    return destination


def materialize_input_bundle(
    request: H3Request, bundle: Path, destination: Path,
) -> tuple[dict[str, str], list[dict[str, Any]]]:
    resolved = read_archive(bundle)
    records = resolved.manifest["assets"]
    if {row["binding"] for row in records} != set(_asset_ids(request)):
        raise PreparationError("input bundle must contain exactly the request's declared assets")
    destination = destination.resolve()
    paths: dict[str, str] = {}
    identities: list[dict[str, Any]] = []
    for index, row in enumerate(records):
        # The archive has already verified this digest. Strip only its exact
        # managed prefix, once, so rebuilding does not prefix it a second time.
        # Private per-record directories keep equal semantic names independent.
        name = PurePosixPath(row["member"]).name.removeprefix(f"{row['sha256'][:16]}-")
        if not name or name in {".", ".."}:
            raise PreparationError("input bundle member has no safe semantic basename")
        target = (destination / "assets" / str(index) / name).resolve()
        if not target.is_relative_to(destination):
            raise PreparationError("input bundle member escapes its attempt directory")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(resolved.members[row["member"]])
        paths[row["binding"]] = str(target)
        identities.append({
            "asset": row["binding"], "member": row["member"],
            "sha256": row["sha256"], "size": row["size"],
            "kind": "bundle_member", "status": "resolved",
        })
    return paths, identities


def bundle_digest(bundle: Path) -> str:
    return hashlib.sha256(bundle.read_bytes()).hexdigest()


def resolve_preparation_assets(
    preparation: Mapping[str, Any], bundle: Path, destination: Path,
) -> dict[str, Any]:
    if preparation.get("input_bundle_sha256") != bundle_digest(bundle):
        raise PreparationError("input bundle digest does not match preparation")
    request = normalize_request(preparation["request"])
    paths, identities = materialize_input_bundle(request, bundle, destination)
    if preparation.get("assets") != identities:
        raise PreparationError("input bundle members do not match preparation asset evidence")
    return {**preparation, "assets": [
        {**record, "path": paths[record["asset"]]} for record in identities
    ]}
