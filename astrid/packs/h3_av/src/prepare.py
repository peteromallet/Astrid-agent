"""Attempt-local preparation for an H3 audiovisual request."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from .masks import MaskScheduleError, build_mask_schedule
from .request import H3Request
from .request_v2 import branch_for


class PreparationError(ValueError):
    """The request or its declared assets cannot be prepared safely."""


def _file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _asset_ids(request: H3Request) -> list[str]:
    value = request.value
    if value.get("version") == 2:
        result = [str(item["asset"]) for item in value["media"]]
        for item in value["media"]:
            for edit in item.get("edit", []):
                mask = edit.get("mask", {})
                if isinstance(mask, Mapping) and "asset" in mask:
                    result.append(str(mask["asset"]))
        return list(dict.fromkeys(result))
    result: list[str] = []
    source = value.get("source")
    if isinstance(source, Mapping):
        result.append(source["asset"])
    result.extend(reference["asset"] for reference in value["references"])
    for domain in ("video", "audio"):
        for change in value["changes"][domain]:
            if "mask_asset" in change:
                result.append(change["mask_asset"])
            area = change.get("area", {})
            if "mask_asset" in area:
                result.append(area["mask_asset"])
    return list(dict.fromkeys(result))


def _resolve_assets(request: H3Request, asset_map: Mapping[str, str] | None) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for asset_id in _asset_ids(request):
        raw_path = asset_map.get(asset_id) if asset_map else None
        record: dict[str, Any] = {"asset": asset_id}
        if raw_path is None:
            record.update({"kind": "managed_asset", "status": "unresolved"})
        else:
            path = Path(raw_path).expanduser().resolve()
            if not path.is_file():
                raise PreparationError(f"asset {asset_id!r} does not resolve to a file: {path}")
            record.update(
                {
                    "kind": "file",
                    "status": "resolved",
                    "path": str(path),
                    "size": path.stat().st_size,
                    "sha256": _file_digest(path),
                }
            )
        records.append(record)
    return records


def prepare_request(
    request: H3Request,
    *,
    asset_map: Mapping[str, str] | None = None,
    fps: float = 24.0,
    width: int = 1024,
    height: int = 576,
    sample_rate: int = 48000,
) -> dict[str, Any]:
    """Create a deterministic preparation manifest without touching a runtime."""

    if fps <= 0 or width <= 0 or height <= 0 or sample_rate <= 0:
        raise PreparationError("fps, width, height, and sample_rate must be positive")
    if request.value.get("version") == 2:
        assets = _resolve_assets(request, asset_map)
        unresolved = [record["asset"] for record in assets if record["status"] != "resolved"]
        branch = branch_for(request)
        schedule = {
            "schema_version": 2,
            "profile": request.value.get("profile"),
            "branch": branch,
            "status": "ready" if not unresolved else "requires_resolution",
            "digest": hashlib.sha256(
                json.dumps(
                    {"branch": branch, "request_digest": request.digest, "assets": assets},
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest(),
        }
        return {
            "schema_version": 2,
            "kind": "h3_av_preparation",
            "request_digest": request.digest,
            "request": request.value,
            "media": {"fps": float(fps), "width": int(width), "height": int(height), "sample_rate": int(sample_rate)},
            "assets": assets,
            "unresolved_assets": unresolved,
            "mask_schedule": schedule,
            "status": "prepared" if not unresolved else "requires_resolution",
            "runtime_submission": "eligible" if not unresolved else "blocked_until_resolution",
        }
    try:
        schedule = build_mask_schedule(request)
    except MaskScheduleError as exc:
        raise PreparationError(str(exc)) from exc
    assets = _resolve_assets(request, asset_map)
    unresolved = [record["asset"] for record in assets if record["status"] != "resolved"]
    # Managed identifiers are intentionally retained for the runtime, but a
    # local asset map must never silently contain a missing path.
    return {
        "schema_version": 1,
        "kind": "h3_av_preparation",
        "request_digest": request.digest,
        "request": request.value,
        "media": {
            "fps": float(fps),
            "width": int(width),
            "height": int(height),
            "sample_rate": int(sample_rate),
        },
        "assets": assets,
        "unresolved_assets": unresolved,
        "mask_schedule": schedule,
        "status": "prepared" if schedule["status"] == "ready" else "requires_resolution",
        "runtime_submission": "blocked_until_resolution" if schedule["status"] != "ready" else "eligible",
    }


def write_preparation(path: str | Path, manifest: Mapping[str, Any]) -> Path:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    return destination


__all__ = ["PreparationError", "prepare_request", "write_preparation"]
