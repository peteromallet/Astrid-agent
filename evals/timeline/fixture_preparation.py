"""One coordinator-owned, parameterized preparation/readiness seam.

This module only materializes inputs that already exist in the pinned fixture
or in an explicit coordinator target root.  It never fabricates media,
targets, expected answers, or a ``ready`` status from a static suite row.
"""

from __future__ import annotations

import contextlib
import copy
import hashlib
import json
import os
import shutil
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Mapping, Sequence

from .fixture import (
    Baseline,
    FixtureError,
    MediaRequirement,
    materialize_public_navigation_entrypoint,
)
from .fixture_contracts import (
    action_target_contract,
    validate_action_target_receipt,
)
from .fixture_manifest import CaseReadiness, build_readiness


@dataclass
class PreparedCase:
    """Live, case-owned inputs for one local-loop iteration.

    ``project_dir`` is ``work/project``. All cases contain a public target and
    case inputs for a real disposable Runtime target. Runtime resources remain
    alive until :meth:`close` is called after final capture.

    Public fields: ``case_id``, ``kind``, ``project_dir``, ``endpoint``,
    ``credential_file``, ``realm_id``, ``project_id``, ``timeline_id``,
    ``target``, ``entrypoint``, ``baseline_observer``, and ``fixture_receipt``.
    ``baseline_observer`` is a callable returning an independent current-head,
    timeline-head-list, semantic-digest, and exact-closure snapshot.
    """

    case_id: str
    kind: str
    project_dir: Path
    endpoint: str | None
    credential_file: Path | None
    realm_id: str | None
    project_id: str | None
    timeline_id: str | None
    target: Mapping[str, Any] | None
    entrypoint: Mapping[str, Any] | None
    baseline_observer: Any
    fixture_receipt: Mapping[str, Any]
    _resources: contextlib.ExitStack
    # Optional case-local managed render, prepared before the worker starts.
    # It is valid only for this prepared realm/project and is never a shared
    # cache or evidence for an edited final state.
    baseline_render: Mapping[str, Any] | None = None
    # Optional coordinator-owned final-output capture. The local loop calls it
    # only after the worker exits and before this prepared Runtime is closed.
    final_output_capture: Any = None
    # Runtime realm/support/contract storage is coordinator-owned.  The local
    # worker may read the credential file itself, but never the backing store.
    worker_denied_paths: tuple[Path, ...] = ()
    # The public CLI needs the server-issued actor as part of its explicit
    # case-local connection context.  Older callers may omit it; the
    # coordinator always supplies it for live disposable realms.
    actor_id: str | None = None

    def close(self) -> None:
        self._resources.close()

    def __enter__(self) -> "PreparedCase":
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        self.close()


PREPARATION_KIND = "astrid.timeline-eval.fixture-preparation.v1"
ACTION_PREPARATION_KIND = "astrid.timeline-eval.action-target-preparation.v1"

# These are the only extra, case-specific files currently pinned in the public
# action fixture.  A sidecar is useful input evidence, but it is not a target
# receipt and never makes an action case executable by itself.
_ACTION_SIDECARS: dict[str, tuple[str, ...]] = {
    "A05": ("action/A05-vo-endpoints.json",),
    "A06": ("action/A06-text-roles.json",),
    "A09": ("action/A09-images.json",),
    "A10": ("action/A10-brightness-collection.json",),
}

_SIDECAR_PREPARATION_CASES = tuple(sorted(_ACTION_SIDECARS))


def inspect_action_sidecars(*, fixture_root: Path, case_id: str) -> dict[str, Any]:
    """Verify coordinator-owned action sidecars without making a target.

    A sidecar is input evidence only: it never makes an action case launchable
    and this function intentionally does not rewrite it into a target receipt.
    Image collections are checked byte-for-byte against their declared SHA-256
    values so a later disposable preparation can ingest only verified bytes.
    """
    fixture_root = fixture_root.expanduser().absolute()
    paths = _ACTION_SIDECARS.get(case_id, ())
    result: dict[str, Any] = {
        "kind": "astrid.timeline-eval.action-sidecar-inventory.v1",
        "case_id": case_id,
        "status": "blocked",
        "sidecars": [],
        "media": [],
        "missing": [],
        "errors": [],
    }
    if not paths:
        result["missing"].append("no pinned case sidecar")
        return result
    for relative in paths:
        path = fixture_root / relative
        if path.is_symlink() or not path.is_file():
            result["missing"].append(relative)
            continue
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            result["errors"].append(f"{relative}: unreadable JSON: {exc}")
            continue
        if not isinstance(value, Mapping):
            result["errors"].append(f"{relative}: sidecar is not an object")
            continue
        declared_case = value.get("case_id")
        if declared_case is not None and declared_case != case_id:
            result["errors"].append(f"{relative}: case_id is {declared_case!r}, expected {case_id!r}")
        result["sidecars"].append(relative)
        images = value.get("images")
        if not isinstance(images, list):
            continue
        for index, image in enumerate(images):
            if not isinstance(image, Mapping):
                result["errors"].append(f"{relative}: images[{index}] is not an object")
                continue
            digest = image.get("sha256") or image.get("media_id")
            media_id = image.get("media_id")
            image_path = image.get("path")
            if not isinstance(digest, str) or not digest.removeprefix("sha256:"):
                result["errors"].append(f"{relative}: images[{index}] has no digest")
                continue
            digest = digest.removeprefix("sha256:")
            if not isinstance(image_path, str) or not image_path:
                result["errors"].append(f"{relative}: images[{index}] has no relative path")
                continue
            source = (fixture_root / "action" / image_path).resolve()
            try:
                source.relative_to((fixture_root / "action").resolve())
            except ValueError:
                result["errors"].append(f"{relative}: images[{index}] path escapes action fixture")
                continue
            if source.is_symlink() or not source.is_file():
                result["missing"].append(image_path)
                continue
            actual = hashlib.sha256(source.read_bytes()).hexdigest()
            if actual != digest:
                result["errors"].append(f"{relative}: {image_path} digest mismatch")
                continue
            if isinstance(media_id, str) and media_id.removeprefix("sha256:") != digest:
                result["errors"].append(f"{relative}: {image_path} media_id disagrees with sha256")
                continue
            result["media"].append({"path": image_path, "media_id": "sha256:" + digest})
    if result["sidecars"] and not result["missing"] and not result["errors"]:
        result["status"] = "verified-inputs"
    return result


def materialize_action_sidecar_inputs(
    *, fixture_root: Path, destination_root: Path,
    case_ids: tuple[str, ...] = _SIDECAR_PREPARATION_CASES,
) -> dict[str, dict[str, Any]]:
    """Copy verified case sidecars into isolated, non-launchable inputs.

    This is intentionally narrower than target preparation: it copies only
    sidecar JSON and the media bytes whose digests the sidecar declares.  The
    resulting ``sidecar-receipt.json`` is evidence of disposable starting
    inputs, not a public target receipt and never carries an edit route.
    Missing or invalid inputs remain blocked.  Existing destination bytes may
    only be reused when they are byte-identical, so a caller cannot silently
    overwrite another attempt's fixture.
    """
    fixture_root = fixture_root.expanduser().absolute()
    destination_root = destination_root.expanduser().absolute()
    if fixture_root.is_symlink() or not fixture_root.is_dir():
        raise FixtureError(f"action fixture root is missing or unsafe: {fixture_root}")
    if destination_root.exists() and destination_root.is_symlink():
        raise FixtureError(f"sidecar destination root is unsafe: {destination_root}")
    destination_root.mkdir(parents=True, exist_ok=True)

    rows: dict[str, dict[str, Any]] = {}
    action_root = fixture_root / "action"

    def copy_unchanged(source: Path, destination: Path) -> str:
        if source.is_symlink() or not source.is_file():
            raise FixtureError(f"sidecar input is missing or unsafe: {source}")
        raw = source.read_bytes()
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            if destination.is_symlink() or not destination.is_file() or destination.read_bytes() != raw:
                raise FixtureError(f"refusing to overwrite different sidecar input: {destination}")
        else:
            destination.write_bytes(raw)
        return hashlib.sha256(raw).hexdigest()

    for case_id in case_ids:
        inventory = inspect_action_sidecars(fixture_root=fixture_root, case_id=case_id)
        row: dict[str, Any] = {
            "kind": "astrid.timeline-eval.action-sidecar-receipt.v1",
            "case_id": case_id,
            "status": "blocked",
            "launchable": False,
            "edit_route": None,
            "target_receipt": None,
            "source": str(fixture_root),
            "destination": str(destination_root / case_id),
            "sidecars": [],
            "media": [],
            "errors": list(inventory["errors"]),
            "missing": list(inventory["missing"]),
        }
        if inventory["status"] != "verified-inputs":
            rows[case_id] = row
            continue
        case_destination = destination_root / case_id
        for relative in _ACTION_SIDECARS.get(case_id, ()):
            source = fixture_root / relative
            destination = case_destination / relative.removeprefix("action/")
            digest = copy_unchanged(source, destination)
            row["sidecars"].append({"path": relative.removeprefix("action/"), "sha256": digest})
        for media in inventory["media"]:
            relative = str(media["path"])
            source = action_root / relative
            destination = case_destination / relative
            digest = copy_unchanged(source, destination)
            expected = str(media["media_id"]).removeprefix("sha256:")
            if digest != expected:
                raise FixtureError(f"copied sidecar media changed during preparation: {relative}")
            row["media"].append({"path": relative, "media_id": "sha256:" + digest})
        row.update({
            "status": "prepared-inputs",
            "input_count": len(row["sidecars"]) + len(row["media"]),
            "reason": "verified sidecar-backed inputs only; disposable target receipt and edit route are intentionally absent",
        })
        receipt_path = case_destination / "sidecar-receipt.json"
        rendered = json.dumps(row, indent=2, ensure_ascii=False, sort_keys=True) + "\n"
        if receipt_path.exists() and (receipt_path.is_symlink() or receipt_path.read_text(encoding="utf-8") != rendered):
            raise FixtureError(f"refusing to overwrite different sidecar receipt: {receipt_path}")
        if not receipt_path.exists():
            receipt_path.parent.mkdir(parents=True, exist_ok=True)
            receipt_path.write_text(rendered, encoding="utf-8")
        rows[case_id] = row

    manifest = {
        "kind": "astrid.timeline-eval.action-sidecar-preparation.v1",
        "owner": "coordinator",
        "launchable": False,
        "source_root": str(fixture_root),
        "destination_root": str(destination_root),
        "cases": rows,
    }
    manifest_path = destination_root / "preparation.json"
    rendered_manifest = json.dumps(manifest, indent=2, ensure_ascii=False, sort_keys=True) + "\n"
    if manifest_path.exists() and (manifest_path.is_symlink() or manifest_path.read_text(encoding="utf-8") != rendered_manifest):
        raise FixtureError(f"refusing to overwrite different sidecar preparation: {manifest_path}")
    if not manifest_path.exists():
        manifest_path.write_text(rendered_manifest, encoding="utf-8")
    return rows


@dataclass(frozen=True)
class PreparationRow:
    case_id: str
    kind: str
    status: str
    input: str
    target: str
    initial_condition: str
    available_tools: tuple[str, ...]
    blockers: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "kind": self.kind,
            "status": self.status,
            "input": self.input,
            "target": self.target,
            "initial_condition": self.initial_condition,
            "available_tools": list(self.available_tools),
            "blockers": list(self.blockers),
        }


def _case_by_id(suite_path: Path) -> dict[str, Mapping[str, Any]]:
    suite = json.loads(suite_path.read_text(encoding="utf-8"))
    return {str(row["id"]): row for row in suite.get("cases", []) if isinstance(row, Mapping) and row.get("id")}


def _action_inputs(case_id: str, fixture_root: Path) -> str:
    """Return pinned paths, keeping absent prerequisites visible."""
    paths = ["action/manifest.json"]
    paths.extend(_ACTION_SIDECARS.get(case_id, ()))
    existing = [path for path in paths if (fixture_root / path).is_file()]
    missing = [path for path in paths if path not in existing]
    description = "; ".join(existing) if existing else "action/manifest.json (missing)"
    if missing:
        description += "; missing: " + ", ".join(missing)
    return description


def _row(
    case: Mapping[str, Any], readiness: CaseReadiness,
    prepared_targets_root: Path | None, fixture_root: Path,
) -> PreparationRow:
    case_id = str(case["id"])
    kind = str(case.get("kind", "unknown"))
    target_path: Path | None = None
    if kind == "navigation":
        aliases = ", ".join(str(value) for value in case.get("targets", ())) or "none"
        target = f"offline entrypoint targets: {aliases}"
        tools = ["selected-case entrypoint", "public SDK/CLI", "coordinator readback"]
        if case_id == "L10":
            tools.append(
                "afplay available" if shutil.which("afplay")
                else "afplay unavailable; headless decode only"
            )
        input_name = "informational/fixture.json plus selected pinned media"
    else:
        target_path = prepared_targets_root / case_id / "target.json" if prepared_targets_root else None
        target = str(target_path) if target_path else "coordinator target receipt (not supplied)"
        tools = ["public case-specific edit route", "coordinator before/after readback"]
        input_name = _action_inputs(case_id, fixture_root)
    preconditions = case.get("preconditions", ())
    initial = "; ".join(str(value) for value in preconditions) if isinstance(preconditions, list) else str(preconditions)
    blockers = list(readiness.reasons)
    if kind == "action":
        target_ready = False
        if target_path is not None and target_path.is_file() and not target_path.is_symlink():
            try:
                target_value = json.loads(target_path.read_text(encoding="utf-8"))
                edit = target_value.get("capabilities", {}).get("edit", {}) if isinstance(target_value, Mapping) else {}
                target_ready = isinstance(edit, Mapping) and edit.get("status") == "available"
            except (OSError, ValueError, json.JSONDecodeError):
                target_ready = False
        if not target_ready:
            contract = action_target_contract(case)
            blockers.append(
                contract.reason
                or "no coordinator-prepared target receipt with an available edit route"
            )
    blockers_tuple = tuple(dict.fromkeys(blockers))
    status = "executable" if readiness.readiness == "fixture_ready" and not blockers_tuple else "blocked-essential-input"
    return PreparationRow(case_id, kind, status, input_name, target, initial, tuple(tools), blockers_tuple)


def build_preparation_table(
    suite_path: Path,
    fixture_root: Path,
    *,
    prepared_targets_root: Path | None = None,
) -> list[PreparationRow]:
    """Build the honest 20-row preparation table from current evidence."""
    suite = _case_by_id(suite_path)
    readiness = {row.case_id: row for row in build_readiness(suite_path, fixture_root)}
    return [_row(suite[case_id], readiness[case_id], prepared_targets_root, fixture_root)
            for case_id in suite if case_id in readiness]


def _target_source(source_root: Path, case_id: str) -> Path:
    """Resolve the two supported coordinator receipt layouts."""
    # A coordinator evidence directory for one case commonly contains the
    # receipt directly.  Keep this form explicit so callers cannot accidentally
    # walk arbitrary descendants looking for a convenient JSON file.
    direct_root = source_root / "target.json"
    if direct_root.is_file() or direct_root.is_symlink():
        return direct_root
    direct = source_root / case_id / "target.json"
    if direct.is_file() or direct.is_symlink():
        return direct
    nested = source_root / "cases" / case_id / "target.json"
    return nested


def _json_sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def materialize_action_target_receipts(
    *, source_root: Path, destination_root: Path,
    case_ids: tuple[str, ...] = tuple(f"A{index:02d}" for index in range(1, 11)),
) -> dict[str, dict[str, Any]]:
    """Copy only real coordinator receipts into a disposable prepared root.

    ``source_root`` must contain receipts produced by an actual disposable
    Runtime preparation (either ``A01/target.json`` or ``cases/A01/target.json``).
    This seam deliberately does not synthesize an action target from a static
    manifest or media sidecar.  A missing route/receipt remains blocked.
    """
    source_root = source_root.expanduser().absolute()
    destination_root = destination_root.expanduser().absolute()
    if source_root.is_symlink() or not source_root.is_dir():
        raise FixtureError(f"action receipt source root is missing or unsafe: {source_root}")
    if destination_root.exists() and destination_root.is_symlink():
        raise FixtureError(f"action receipt destination root is unsafe: {destination_root}")
    destination_root.mkdir(parents=True, exist_ok=True)

    rows: dict[str, dict[str, Any]] = {}
    for case_id in case_ids:
        source = _target_source(source_root, case_id)
        row: dict[str, Any] = {
            "kind": ACTION_PREPARATION_KIND,
            "case_id": case_id,
            "status": "blocked-essential-input",
            "source": str(source),
            "destination": str(destination_root / case_id / "target.json"),
        }
        if source.is_symlink() or not source.is_file():
            row["reason"] = "no coordinator-produced disposable target receipt"
            rows[case_id] = row
            continue
        try:
            raw = source.read_bytes()
            value = json.loads(raw.decode("utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            row["reason"] = f"target receipt is unreadable: {exc}"
            rows[case_id] = row
            continue
        if not isinstance(value, Mapping):
            row["reason"] = "target receipt is not a JSON object"
            rows[case_id] = row
            continue
        errors = validate_action_target_receipt(value, action_target_contract({"id": case_id}))
        if errors:
            row["reason"] = "; ".join(errors)
            rows[case_id] = row
            continue
        owned = value.get("owned_media_ids")
        if not isinstance(owned, list) or not owned or any(not isinstance(item, str) or not item for item in owned):
            row["reason"] = "target receipt does not prove destination-owned media"
            rows[case_id] = row
            continue
        target_dir = destination_root / case_id
        if target_dir.exists() and target_dir.is_symlink():
            row["reason"] = "target destination directory is a symlink"
            rows[case_id] = row
            continue
        target_dir.mkdir(parents=True, exist_ok=True)
        destination = target_dir / "target.json"
        rendered = json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True) + "\n"
        if destination.exists() and destination.read_text(encoding="utf-8") != rendered:
            raise FixtureError(f"refusing to overwrite a different prepared receipt: {destination}")
        destination.write_text(rendered, encoding="utf-8")
        row.update({
            "status": "prepared",
            "sha256": _json_sha256(rendered.encode("utf-8")),
            "owned_media_count": len(owned),
            "project_id": value.get("project_id"),
            "timeline_id": value.get("timeline_id"),
            "edit_route": value.get("capabilities", {}).get("edit", {}).get("route"),
        })
        rows[case_id] = row

    manifest = {
        "kind": ACTION_PREPARATION_KIND,
        "owner": "coordinator",
        "source_root": str(source_root),
        "destination_root": str(destination_root),
        "cases": rows,
    }
    (destination_root / "preparation.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return rows


def prepare_public_case(
    case: Mapping[str, Any], *, fixture_root: Path, destination: Path,
    prepared_targets_root: Path | None = None,
) -> dict[str, Any]:
    """Materialize one selected-case public envelope, never a synthetic target."""
    case_id = str(case.get("id", ""))
    if not case_id:
        raise FixtureError("selected case has no id")
    destination = destination.expanduser().absolute()
    if destination.exists() and (destination.is_symlink() or not destination.is_dir()):
        raise FixtureError(f"selected case destination is unsafe: {destination}")
    destination.mkdir(parents=True, exist_ok=True)
    if case.get("kind") == "navigation":
        entrypoint = materialize_public_navigation_entrypoint(
            case_id, fixture_root=fixture_root, destination=destination,
        )
        return {"kind": PREPARATION_KIND, "case_id": case_id, "status": "prepared",
                "entrypoint": entrypoint}
    if prepared_targets_root is None:
        return {"kind": PREPARATION_KIND, "case_id": case_id,
                "status": "blocked-essential-input",
                "reason": "prepared target root is missing; no real action target was fabricated"}
    root = prepared_targets_root.expanduser().absolute()
    source = root / case_id / "target.json"
    if root.is_symlink() or not root.is_dir() or source.is_symlink() or not source.is_file():
        return {"kind": PREPARATION_KIND, "case_id": case_id,
                "status": "blocked-essential-input",
                "reason": f"prepared public target is missing or unsafe: {source}"}
    try:
        value = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return {"kind": PREPARATION_KIND, "case_id": case_id,
                "status": "blocked-essential-input",
                "reason": f"prepared public target is unreadable: {exc}"}
    if not isinstance(value, Mapping):
        raise FixtureError(f"prepared public target must be an object: {source}")
    errors = validate_action_target_receipt(value, action_target_contract(case))
    if errors:
        return {"kind": PREPARATION_KIND, "case_id": case_id,
                "status": "blocked-essential-input", "reason": "; ".join(errors)}
    (destination / "target.json").write_text(
        json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return {"kind": PREPARATION_KIND, "case_id": case_id, "status": "prepared",
            "target": dict(value)}


def _baseline_paths(fixture_root: Path) -> tuple[Path, Path]:
    """Resolve the one adopted pinned export and its media directory."""
    source_root = fixture_root.parent / "attempts" / "e05-a01" / "source"
    baseline = source_root / "baseline.json"
    if baseline.is_symlink() or not baseline.is_file():
        raise FixtureError(f"pinned source baseline is missing or unsafe: {baseline}")
    return baseline, source_root


def _case_baseline(case_id: str, baseline: Baseline, fixture_root: Path) -> tuple[Baseline, dict[str, bytes]]:
    """Apply only disclosed, factual starting-state derivatives in the copy.

    The adopted source export is shared by the suite, but three action briefs
    explicitly require an existing music bed and A06 requires an on-screen
    title.  Those are starting conditions, not worker answers, so materialize
    them in the disposable baseline before the first Runtime write and record
    the added bytes as coordinator-owned media.
    """
    if case_id == "A01":
        # Keep the adopted source export truthful when it is already old video,
        # while retaining the deterministic charcoal-to-old-video conversion
        # for historical synthetic exports.
        from .a01_smoke import derive_a01_old_video_baseline
        baseline, _ = derive_a01_old_video_baseline(baseline)
    closure = copy.deepcopy(dict(baseline.closure))
    parent = closure["parent_revision"]["payload"]
    extra: dict[str, bytes] = {}
    media = list(baseline.media)
    action_root = fixture_root / "action"
    if case_id in {"A02", "A05", "A07"}:
        music_path = action_root / "media" / "music-bed.wav"
        if music_path.is_symlink() or not music_path.is_file():
            raise FixtureError(f"{case_id} requires the pinned music bed: {music_path}")
        music = music_path.read_bytes()
        digest = "sha256:" + hashlib.sha256(music).hexdigest()
        asset_key = "eval_music_bed"
        registry = parent.setdefault("registry", {})
        assets = registry.setdefault("assets", {})
        assets.setdefault(asset_key, {
            "media_id": digest, "content_sha256": digest.removeprefix("sha256:"),
            # The derivative construction is recorded by the preparation
            # receipt and MediaRequirement below.  The renderer-facing asset
            # registry uses the canonical origin for imported opaque bytes.
            "origin": "opaque-foreign", "type": "audio/wav", "duration": 117.0667,
        })
        tracks = parent.setdefault("config", {}).setdefault("tracks", [])
        if not any(isinstance(row, Mapping) and row.get("id") == "music" for row in tracks):
            tracks.append({"id": "music", "kind": "audio", "label": "Music"})
        clips = parent.setdefault("clips", [])
        if not any(isinstance(row, Mapping) and row.get("id") == "eval_music_bed_clip" for row in clips):
            clips.append({
                "asset": asset_key, "at": 0, "from": 0, "to": 117.0667,
                "hold": 117.0667, "clipType": "media", "id": "eval_music_bed_clip",
                "track": "music", "volume": 1,
            })
        if not any(row.digest == digest for row in media):
            media.append(MediaRequirement(
                digest=digest, source_object_id=digest, media_type="audio/wav",
                required_by=(case_id,), source_handle="__fixture_extra__/music-bed.wav",
            ))
        extra[digest] = music
    if case_id == "A06":
        occurrence_id = "shot-ee383f695b10431c"
        occurrence = next((row for row in parent.get("occurrences", ())
                           if isinstance(row, Mapping) and row.get("occurrence_id") == occurrence_id), None)
        if not isinstance(occurrence, Mapping):
            raise FixtureError("A06 visible-title derivative cannot resolve its opening occurrence")
        shot = next((row for row in closure.get("shot_revisions", ())
                     if isinstance(row, Mapping) and row.get("revision_id") == occurrence.get("shot_revision_id")), None)
        internal_id = shot.get("internal_timeline_revision_id") if isinstance(shot, Mapping) else None
        internal = next((row for row in closure.get("internal_timeline_revisions", ())
                         if isinstance(row, Mapping) and row.get("revision_id") == internal_id), None)
        if not isinstance(internal, Mapping):
            raise FixtureError("A06 visible-title derivative cannot resolve its opening internal timeline")
        internal_payload = internal["payload"]
        tracks = internal_payload.setdefault("tracks", [])
        if not any(isinstance(row, Mapping) and row.get("id") == "titles" for row in tracks):
            tracks.append({"id": "titles", "kind": "visual", "label": "Titles"})
        clips = internal_payload.setdefault("clips", [])
        if not any(isinstance(row, Mapping) and row.get("id") == "a06-visible-title" for row in clips):
            clips.append({
                "id": "a06-visible-title", "clipType": "text",
                "text": {
                    "content": "Astrid", "align": "center",
                    "color": "#ffffff", "fontSize": 96,
                },
                "at": 0, "to": 7.066666666666666, "hold": 7.066666666666666,
                "track": "titles",
            })
    if closure == baseline.closure:
        return baseline, extra
    semantic = {
        "parent": parent,
        "shots": [row.get("payload") for row in closure["shot_revisions"]],
        "internal_timelines": [row.get("payload") for row in closure["internal_timeline_revisions"]],
    }
    def canonical(value: Any) -> bytes:
        return json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        ).encode()

    return replace(
        baseline, closure=closure, media=tuple(media),
        closure_digest="sha256:" + hashlib.sha256(canonical(closure)).hexdigest(),
        semantic_digest="sha256:" + hashlib.sha256(canonical(semantic)).hexdigest(),
    ), extra


def _copy_checked(source: Path, destination: Path) -> str:
    if source.is_symlink() or not source.is_file():
        raise FixtureError(f"case input is missing or unsafe: {source}")
    raw = source.read_bytes()
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        if destination.is_symlink() or not destination.is_file() or destination.read_bytes() != raw:
            raise FixtureError(f"refusing to overwrite different case input: {destination}")
    else:
        destination.write_bytes(raw)
    return hashlib.sha256(raw).hexdigest()


def _copy_action_assets(case_id: str, fixture_root: Path, project_dir: Path, baseline_media_root: Path) -> list[dict[str, Any]]:
    """Copy only assets used by the selected action into its public package."""
    action_root = fixture_root / "action"
    selected: list[tuple[str, Path]] = []
    image_files = {
        "A01": ("charcoal-pixel-mink", "A09-images/image-02-03810afd80e1.png"),
        "A04": ("alternate-image", "A09-images/image-03-1140a132d32f.png"),
        "A08": ("still-image", "A09-images/image-01-002e1ef76ef2.png"),
    }
    if case_id in image_files:
        alias, relative = image_files[case_id]
        selected.append((alias, action_root / relative))
    if case_id in {"A02", "A05", "A07"}:
        selected.append(("music-bed", action_root / "media" / "music-bed.wav"))
    # Media already in the pinned project is made locally addressable as well
    # as Runtime-owned. Copy only the task's voice/video assets, never every
    # source clip in the full composition.
    baseline, _ = _baseline_paths(fixture_root)
    raw = json.loads(baseline.read_text(encoding="utf-8"))
    manifest = json.loads((action_root / "manifest.json").read_text(encoding="utf-8"))
    action_row = next((row for row in manifest.get("cases", []) if row.get("id") == case_id), {})
    required_digests: list[tuple[str, str]] = []
    if case_id == "A06":
        sidecar = json.loads((action_root / "A06-text-roles.json").read_text(encoding="utf-8"))
        voice = next((row for row in sidecar.get("bindings", []) if row.get("role") == "voiceover"), {})
        if voice.get("media_id"):
            required_digests.append(("voiceover", str(voice["media_id"])))
    elif case_id == "A07":
        if action_row.get("media", {}).get("voice_digest"):
            required_digests.append(("voiceover", str(action_row["media"]["voice_digest"])))
    elif case_id == "A08":
        for revision in raw.get("closure", {}).get("internal_timeline_revisions", []):
            assets = revision.get("payload", {}).get("assets", {})
            if isinstance(assets, Mapping) and isinstance(assets.get("terminal_v17"), Mapping):
                required_digests.append(("terminal-source-video", str(assets["terminal_v17"].get("media_id", ""))))
                break
    by_digest = {str(row.get("digest")): row for row in raw.get("media", []) if isinstance(row, Mapping)}
    for alias, digest_value in required_digests:
        digest = digest_value.removeprefix("sha256:")
        row = by_digest.get("sha256:" + digest)
        if not row:
            raise FixtureError(f"pinned source media for {alias} is not present in exported baseline")
        selected.append((alias, baseline_media_root / str(row["source_handle"])))

    rows: list[dict[str, Any]] = []
    for alias, source in selected:
        digest = hashlib.sha256(source.read_bytes()).hexdigest() if source.is_file() and not source.is_symlink() else None
        if digest is None:
            raise FixtureError(f"required action asset is missing or unsafe: {source}")
        relative = f"media/{digest}/{source.name}"
        target = project_dir / relative
        copied_digest = _copy_checked(source, target)
        rows.append({
            "alias": alias,
            # Keep the compact project-relative path for schema compatibility,
            # but also give workers an unambiguous path from the loop's `work`
            # cwd.  The brief uses the same `project/...` convention.
            "path": relative,
            "project_path": "project/" + relative,
            "absolute_path": str(target),
            "sha256": "sha256:" + copied_digest,
        })
    return rows


def _remap_fixture_ids(value: Any, maps: Sequence[Mapping[str, str]]) -> Any:
    all_ids: dict[str, str] = {}
    for mapping in maps:
        all_ids.update(mapping)
    if isinstance(value, str):
        return all_ids.get(value, value)
    if isinstance(value, list):
        return [_remap_fixture_ids(item, maps) for item in value]
    if isinstance(value, Mapping):
        return {str(key): _remap_fixture_ids(child, maps) for key, child in value.items()}
    return copy.deepcopy(value)


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")


def _verify_actor_scoped_render_readability(
    product_client: Any, *, project_id: str, timeline_id: str, run_id: str,
) -> tuple[bool, str | None]:
    """Exercise the actor-facing composed view against the exact managed run."""
    viewed = product_client.timelines.visualize(
        project_id, timeline_id, mode="composed", options={"render_run": run_id},
    )
    if getattr(viewed, "ok", False):
        return True, None
    error = getattr(viewed, "error", None)
    return False, getattr(error, "message", str(error or "composed view failed"))


def _capture_final_managed_output(
    product_client: Any, *, project_id: str, timeline_id: str,
    render_if_missing: bool = True,
) -> dict[str, Any]:
    """Reuse an exact saved-head render, otherwise render it at most once.

    The returned status is authority evidence only; decoded-media assertions
    remain the responsibility of the existing output checkers.
    """
    from astrid.sdk.timeline_filmstrip import matching_composed_render

    started = time.perf_counter()
    result: dict[str, Any] = {
        "kind": "astrid.timeline-eval.final-render.v1",
        "project_id": project_id,
        "timeline_id": timeline_id,
        "status": "unavailable",
        "rendered_by_coordinator": False,
    }
    try:
        shown = product_client.timelines.show(project_id, timeline_id)
        if not shown.ok or not isinstance(shown.data, Mapping):
            result["reason"] = "final timeline state could not be read"
            return result
        timeline = dict(shown.data)
        result["head_revision_id"] = timeline.get("parent_revision_id") or timeline.get("head_revision_id")
        result["config_version"] = timeline.get("config_version") or timeline.get("version")
        runtime_client = product_client.remote._client
        run_id = matching_composed_render(
            runtime_client, project_id=project_id, timeline=timeline, limit=50,
        )
        if run_id:
            readable, reason = _verify_actor_scoped_render_readability(
                product_client, project_id=project_id, timeline_id=timeline_id, run_id=run_id,
            )
            result.update(
                status="reused_exact" if readable else "unreadable",
                run_id=run_id,
                authority_verified=True,
                actor_scoped_readable=readable,
            )
            if reason:
                result["reason"] = reason
            return result
        if not render_if_missing:
            result["reason"] = "no exact saved-head render found within bounded discovery"
            return result

        inputs: dict[str, Any] = {"timeline_ref": timeline_id, "review": True}
        version = timeline.get("config_version", timeline.get("version"))
        if isinstance(version, int) and not isinstance(version, bool):
            inputs["expected_version"] = version
        elif isinstance(version, str) and version.isdigit():
            inputs["expected_version"] = int(version)
        rendered = product_client.invoke_result(
            "rendering.render", kind="executor", project=project_id,
            inputs=inputs, wait=True, timeout_seconds=600,
        )
        result.update(
            rendered_by_coordinator=True,
            kernel_run_id=getattr(rendered, "kernel_run_id", None),
            kernel_task_id=getattr(rendered, "kernel_task_id", None),
            executor_version=getattr(rendered, "executor_version", None),
        )
        if not rendered.ok:
            error = getattr(rendered, "error", None)
            result.update(status="failed", reason=getattr(error, "message", str(error)))
            return result
        run_id = getattr(rendered, "run_id", None)
        exact = matching_composed_render(
            runtime_client, project_id=project_id, timeline=timeline, limit=50,
        )
        if not run_id or exact != run_id:
            result.update(
                status="unverified", run_id=run_id,
                reason="render completed but exact managed output authority was not verified",
            )
            return result
        readable, reason = _verify_actor_scoped_render_readability(
            product_client, project_id=project_id, timeline_id=timeline_id, run_id=run_id,
        )
        result.update(
            status="rendered_exact" if readable else "unreadable",
            run_id=run_id,
            authority_verified=True,
            actor_scoped_readable=readable,
        )
        if reason:
            result["reason"] = reason
        return result
    except Exception as exc:  # evidence capture must not mask actor outcome
        result.update(status="failed", reason=f"{type(exc).__name__}: {exc}")
        return result
    finally:
        result["duration_seconds"] = round(time.perf_counter() - started, 3)


class _RuntimeBaselineObserver:
    def __init__(self, adapter: Any, project_id: str, timeline_id: str):
        self.adapter = adapter
        self.project_id = project_id
        self.timeline_id = timeline_id

    def __call__(self) -> dict[str, Any]:
        head = self.adapter.current_head(self.project_id, self.timeline_id)
        closure = self.adapter.read_current_closure(self.project_id, self.timeline_id, head=head)
        return {
            "project_id": self.project_id,
            "timeline_id": self.timeline_id,
            "head_revision_id": head,
            "timeline_heads": self.adapter.list_project_timeline_heads(self.project_id),
            "semantic_digest": self.adapter.read_current_semantic_digest(self.project_id, self.timeline_id),
            "closure": closure,
        }


def _prepare_case_with_resources(
    case_id: str,
    *,
    case_root: Path,
    fixture_root: Path,
    canonical_endpoint: str,
    canonical_realm_id: str,
    canonical_root: Path,
    rendering_host_source: str | Path | None,
    resources: contextlib.ExitStack,
) -> PreparedCase:
    """Prepare one case in a fresh project; every case owns a live Runtime.

    ``case_root`` is the case's ``work/project`` directory. Call ``close()``
    only after the loop has captured post-run state and media. Each call
    starts a new disposable Runtime realm, seeds the exact pinned source with
    Runtime-assigned project/timeline IDs, and exposes an independent observer.
    """
    case_root = case_root.expanduser().absolute()
    fixture_root = fixture_root.expanduser().absolute()
    if case_root.exists() and (case_root.is_symlink() or any(case_root.iterdir())):
        raise FixtureError(f"case project directory must be new and empty: {case_root}")
    case_root.mkdir(parents=True, exist_ok=True)
    suite_path = Path(__file__).with_name("cases") / "agent_briefs.json"
    suite = json.loads(suite_path.read_text(encoding="utf-8"))
    row = next((item for item in suite.get("cases", []) if isinstance(item, Mapping) and item.get("id") == case_id), None)
    if row is None:
        raise FixtureError(f"unknown timeline case: {case_id}")
    if row.get("task") not in {"action", "navigation"}:
        raise FixtureError(f"unsupported task type for {case_id}: {row.get('task')}")
    if not (canonical_endpoint and canonical_realm_id and canonical_root):
        raise FixtureError("action preparation requires explicit canonical identity and root for isolation comparison")
    from .a01_smoke import load_baseline
    from .fixture import public_target_receipt, seed_case
    from .runtime_adapter import local_disposable_runtime

    baseline_path, source_root = _baseline_paths(fixture_root)
    baseline = load_baseline(baseline_path)
    baseline, extra_media = _case_baseline(case_id, baseline, fixture_root)
    session = resources.enter_context(local_disposable_runtime(
        # Keep disposable Runtime storage beside ``work/``.  The worker's
        # selected-case allowance is only ``work/``; the launch boundary adds
        # explicit denials for this storage and exposes just the credential.
        scratch_parent=case_root.parent.parent,
        canonical_endpoint=canonical_endpoint,
        canonical_realm_id=canonical_realm_id,
        canonical_root=canonical_root,
        rendering_host_source=rendering_host_source,
    ))
    # The fixture seeding helper currently validates its internal namespace as
    # A01–A10. L cases retain their public IDs/tasks, while using an
    # A01-compatible seed namespace to share the already-proven Runtime path.
    seed_case_id = case_id if case_id.startswith("A") else "A01"
    seed = seed_case(
        session.adapter, baseline,
        attempt_id=f"{case_root.parent.name}-{case_id}-{os.urandom(8).hex()}",
        case_id=seed_case_id,
        media_root=source_root,
        extra_media=extra_media,
    )
    project_id = str(seed["project_id"])
    timeline_id = str(seed["timeline_id"])
    identity = seed["identities"]
    public_seed = dict(seed)
    public_seed["identities"] = replace(identity, case_id=case_id)
    raw_target = public_target_receipt(
        public_seed, read_only=(row.get("task") == "navigation"),
    )
    target = dict(raw_target)
    target.update({"endpoint": session.endpoint, "realm_id": session.realm_id})
    _write_json(case_root / "target.json", target)

    maps = (identity.occurrence_ids, identity.shot_ids, identity.shot_revision_ids, identity.item_ids,
            identity.internal_timeline_ids, identity.internal_revision_ids)
    if row.get("task") == "navigation":
        # This metadata is deliberately small; the worker's source of truth is
        # the live public timeline, never an offline entrypoint export.
        prompt = row.get("prompt")
        public_case = {
            "id": case_id,
            "task": "navigation",
            "focus": prompt.get("focus") if isinstance(prompt, Mapping) else None,
            "occurrence_ids": sorted(identity.occurrence_ids.values()),
            "shot_ids": sorted(identity.shot_ids.values()),
            "shot_revision_ids": sorted(identity.shot_revision_ids.values()),
            "internal_timeline_ids": sorted(identity.internal_timeline_ids.values()),
        }
    else:
        manifest = json.loads((fixture_root / "action" / "manifest.json").read_text(encoding="utf-8"))
        manifest_case = next((item for item in manifest.get("cases", []) if item.get("id") == case_id), None)
        if manifest_case is None:
            raise FixtureError(f"action manifest has no row for {case_id}")
        public_case = _remap_fixture_ids(manifest_case, maps)
        public_case.pop("expected_invariants", None)
        public_case.pop("blockers", None)
        public_case.pop("lifecycle", None)
    # These are observed starting conditions for the disposable copy, not
    # hidden expected answers. Keeping them beside the task inputs prevents a
    # brief from referring to media or bindings that the seeded timeline does
    # not actually contain.
    if case_id == "A01":
        public_case["starting_state"] = {
            "opening_selector_asset_key": "h3_intro_revision_s01_16b8227b40f2",
            "opening_selector_media_type": "video",
            "opening_selector_clip_id": "shot_b01",
        }
    elif case_id in {"A02", "A05", "A07"}:
        public_case["starting_state"] = {
            "existing_music_clip_id": "eval_music_bed_clip",
            "existing_music_asset_key": "eval_music_bed",
            "existing_music_track": "music",
            "existing_music_start_seconds": 0,
        }
    elif case_id == "A06":
        public_case["starting_state"] = {
            "visible_title_binding_id": "a06-visible-title",
            "visible_title_text": "Astrid",
            "visible_title_track": "titles",
        }
    elif case_id == "A08":
        public_case["starting_state"] = {
            "still_image_digest": public_case.get("media", {}).get("still_image_digest"),
            "still_image_asset_alias": "still-image",
        }
    elif case_id == "A09":
        public_case.setdefault("targets", {})["montage_occurrence"] = identity.occurrence_ids.get(
            "shot-6f6b80fbe16c0877", "shot-6f6b80fbe16c0877",
        )
    assets = _copy_action_assets(case_id, fixture_root, case_root, source_root) if row.get("task") == "action" else []
    _write_json(case_root / "task-inputs.json", {
        "case_id": case_id,
        "project_root": str(case_root),
        "project_id": project_id,
        "timeline_id": timeline_id,
        "head_revision_id": seed["receipt"]["new_head"],
        "task_inputs": public_case,
        "assets": assets,
    })
    sidecar_rows = materialize_action_sidecar_inputs(
        fixture_root=fixture_root,
        destination_root=case_root / "sidecars",
        case_ids=(case_id,) if case_id in _ACTION_SIDECARS else (),
    ) if case_id in _ACTION_SIDECARS else {}
    public_sidecars: dict[str, str] = {}
    if case_id in _ACTION_SIDECARS:
        source_names = [Path(path).name for path in _ACTION_SIDECARS[case_id]]
        for name in source_names:
            source_sidecar = case_root / "sidecars" / case_id / name
            if not source_sidecar.is_file() or source_sidecar.is_symlink():
                continue
            value = json.loads(source_sidecar.read_text(encoding="utf-8"))
            value = _remap_fixture_ids(value, maps)
            if isinstance(value, dict) and isinstance(value.get("images"), list):
                for image in value["images"]:
                    if isinstance(image, dict) and isinstance(image.get("path"), str):
                        image["local_path"] = str(case_root / "sidecars" / case_id / image["path"])
            destination = case_root / "public-sidecars" / case_id / name
            _write_json(destination, value)
            public_sidecars[name] = str(destination)
    if public_sidecars:
        task_inputs = json.loads((case_root / "task-inputs.json").read_text(encoding="utf-8"))
        task_inputs["sidecars"] = public_sidecars
        _write_json(case_root / "task-inputs.json", task_inputs)
    observer = _RuntimeBaselineObserver(session.adapter, project_id, timeline_id)
    baseline_snapshot = observer()
    baseline_render: dict[str, Any] | None = None
    product_client = None
    # ``baseline_view`` is a preparation requirement for the cases that use
    # it.  The actor may still choose the render-free input view, but the
    # coordinator must prove that a same-project starting render exists before
    # launching a baseline-backed case.  A missing/failed render is setup
    # failure, never an optional evidence downgrade.
    if bool(row.get("baseline_view")):
        baseline_render = {
            "kind": "astrid.timeline-eval.baseline-render.v1",
            "case_id": case_id,
            "project_id": project_id,
            "timeline_id": timeline_id,
            "status": "not_requested",
            "reason": "required managed rendering host was not supplied",
        }
        if rendering_host_source is None:
            _write_json(case_root.parent.parent / "baseline-render.json", {
                **baseline_render,
                "status": "failed",
            })
            raise FixtureError("required baseline render host was not supplied")
        render_started = time.perf_counter()
        try:
            from astrid.sdk.client import AstridClient
            from astrid.sdk.remote import RemoteAstridClient

            product_client = AstridClient(remote=RemoteAstridClient(session.adapter.workspace))
            shown = product_client.timelines.show(project_id, timeline_id)
            version = None
            if shown.ok and isinstance(shown.data, Mapping):
                raw_version = shown.data.get("config_version", shown.data.get("version"))
                if isinstance(raw_version, int) and not isinstance(raw_version, bool):
                    version = raw_version
                elif isinstance(raw_version, str) and raw_version.isdigit():
                    version = int(raw_version)
            render_inputs: dict[str, Any] = {"timeline_ref": timeline_id, "review": True}
            if version is not None:
                render_inputs["expected_version"] = version
            rendered = product_client.invoke_result(
                "rendering.render", kind="executor", project=project_id,
                # Setup has the same ten-minute ceiling as an actor case;
                # it is measured separately and never charged to the
                # worker's timeout budget.
                inputs=render_inputs, wait=True, timeout_seconds=600,
            )
            baseline_render = {
                "kind": "astrid.timeline-eval.baseline-render.v1",
                "case_id": case_id,
                "project_id": project_id,
                "timeline_id": timeline_id,
                "status": "succeeded" if rendered.ok else "failed",
                "run_id": rendered.run_id,
                "kernel_run_id": rendered.kernel_run_id,
                "kernel_task_id": rendered.kernel_task_id,
                "executor_version": rendered.executor_version,
                "error": rendered.error,
                "head_revision_id": baseline_snapshot.get("head_revision_id"),
                "config_version": version,
                "render_profile": "default",
                "coverage": "full_timeline",
                "closure_semantic_digest": baseline_snapshot.get("semantic_digest"),
                "setup_duration_seconds": round(time.perf_counter() - render_started, 3),
            }
            if not rendered.ok:
                _write_json(case_root.parent.parent / "baseline-render.json", baseline_render)
                error = rendered.error
                detail = error.message if hasattr(error, "message") else error
                raise FixtureError(f"required baseline render failed: {detail}")
            from astrid.sdk.timeline_filmstrip import matching_composed_render

            verified_run = matching_composed_render(
                product_client.remote._client,
                project_id=project_id,
                timeline={**dict(shown.data), "parent_revision_id": baseline_snapshot.get("head_revision_id")},
                limit=50,
            ) if shown.ok and isinstance(shown.data, Mapping) else None
            if not rendered.run_id or verified_run != rendered.run_id:
                baseline_render.update(
                    status="failed",
                    authority_verified=False,
                    error="render succeeded but exact starting-head managed output authority was not verified",
                )
                _write_json(case_root.parent.parent / "baseline-render.json", baseline_render)
                raise FixtureError("required baseline render did not verify against the exact starting head")
            readable, reason = _verify_actor_scoped_render_readability(
                product_client, project_id=project_id, timeline_id=timeline_id,
                run_id=rendered.run_id,
            )
            baseline_render.update(
                authority_verified=True,
                actor_scoped_readable=readable,
            )
            if not readable:
                baseline_render.update(status="failed", error=reason or "actor-scoped composed view failed")
                _write_json(case_root.parent.parent / "baseline-render.json", baseline_render)
                raise FixtureError("required baseline output is not readable through the actor-scoped view")
        except FixtureError:
            raise
        except Exception as exc:  # setup failure is explicit and case-local
            baseline_render = {
                **baseline_render,
                "status": "failed",
                "error": f"{type(exc).__name__}: {exc}",
                "setup_duration_seconds": round(time.perf_counter() - render_started, 3),
            }
            _write_json(case_root.parent.parent / "baseline-render.json", baseline_render)
            raise FixtureError(f"required baseline render setup failed: {type(exc).__name__}: {exc}") from exc
        _write_json(case_root.parent.parent / "baseline-render.json", baseline_render)
    if rendering_host_source is not None and product_client is None:
        from astrid.sdk.client import AstridClient
        from astrid.sdk.remote import RemoteAstridClient

        product_client = AstridClient(remote=RemoteAstridClient(session.adapter.workspace))
    final_output_capture = None
    if product_client is not None:
        final_output_capture = lambda: _capture_final_managed_output(
            product_client, project_id=project_id, timeline_id=timeline_id,
        )
    # Keep the coordinator's full starting closure outside ``work/project``.
    # The worker receives only the public target/task inputs; even a sandboxed
    # file that sits beside those inputs is an unnecessary contamination risk.
    _write_json(case_root.parent.parent / "baseline-observation.json", baseline_snapshot)
    receipt = {
        "case_id": case_id,
        "kind": "runtime-navigation" if row.get("task") == "navigation" else "runtime-action",
        "task": row.get("task"),
        "endpoint": session.endpoint,
        "realm_id": session.realm_id,
        "project_id": project_id,
        "timeline_id": timeline_id,
        "head_revision_id": seed["receipt"]["new_head"],
        "semantic_digest": baseline_snapshot["semantic_digest"],
        "owned_media_count": len(seed["owned_media"]),
        "sidecars": sidecar_rows.get(case_id),
        "assets": assets,
    }
    return PreparedCase(
        case_id=case_id, kind="action", project_dir=case_root,
        endpoint=session.endpoint, credential_file=session.adapter.isolation.credential_file,
        realm_id=session.realm_id, project_id=project_id, timeline_id=timeline_id,
        target=target, entrypoint=None, baseline_observer=observer,
        fixture_receipt={**receipt, "baseline_render": baseline_render},
        baseline_render=baseline_render, final_output_capture=final_output_capture,
        _resources=resources,
        actor_id=getattr(getattr(session.adapter, "proof", None), "actor_id", None),
            worker_denied_paths=tuple(
                path for path in (
                    getattr(session, "root", None),
                    getattr(session, "root", None) / "realm" if getattr(session, "root", None) else None,
                    getattr(session, "root", None) / "support" if getattr(session, "root", None) else None,
                    getattr(session, "contract_path", None),
                ) if path is not None
            ),
    )


def prepare_case(
    case_id: str,
    *,
    case_root: Path,
    fixture_root: Path,
    canonical_endpoint: str,
    canonical_realm_id: str,
    canonical_root: Path,
    rendering_host_source: str | Path | None = None,
) -> PreparedCase:
    """Prepare a case and close partially opened resources if setup fails."""
    resources = contextlib.ExitStack()
    try:
        return _prepare_case_with_resources(
            case_id,
            case_root=case_root,
            fixture_root=fixture_root,
            canonical_endpoint=canonical_endpoint,
            canonical_realm_id=canonical_realm_id,
            canonical_root=canonical_root,
            rendering_host_source=rendering_host_source,
            resources=resources,
        )
    except BaseException:
        resources.close()
        raise


def render_case_brief(case_id: str, *, prepared: PreparedCase, doc_path: str = "docs/timeline-editing-guide.md") -> str:
    """Render the short worker brief from the existing operational prompt."""
    suite = json.loads((Path(__file__).with_name("cases") / "agent_briefs.json").read_text(encoding="utf-8"))
    row = next(item for item in suite["cases"] if item["id"] == case_id)
    if row.get("task") == "navigation":
        task = str(row.get("operational_addendum", "Inspect the selected timeline for the requested purpose."))
        target = prepared.target or {}
        project_id = str(target.get("project_id") or prepared.project_id or "")
        timeline_id = str(target.get("timeline_id") or prepared.timeline_id or "")
        endpoint = str(target.get("endpoint") or prepared.endpoint or "")
        realm_id = str(target.get("realm_id") or prepared.realm_id or "")
        head = str(target.get("head_revision_id") or "")
        credential = str(prepared.credential_file or "")
        occurrences = target.get("occurrence_ids", [])
        occurrence = str(occurrences[0]) if isinstance(occurrences, list) and occurrences else ""
        material_notes = row.get("materials", [])
        materials = "\n".join(f"- {item}" for item in material_notes) if material_notes else "- The supplied timeline and its selected media."
        show_command = (
            f"python3 -m astrid timelines show --project {project_id} {timeline_id} "
            f"--summary --detail --limit 100"
        )
        visual_command = (
            f"python3 -m astrid timelines visualize {timeline_id} --project {project_id} "
            f"--mode inputs --format md --format png"
        )
        if occurrence:
            show_command += f" --occurrence {occurrence}"
            visual_command += f" --occurrence {occurrence}"
        return (
            f"# {case_id} — timeline task\n\n{task}\n\n"
            f"Project: `{project_id}`; timeline: `{timeline_id}`; target occurrence: `{occurrence}`.\n"
            f"Use the supplied project and these materials:\n{materials}\n\n"
            "Start with these public, render-free inspection commands (using `python3`):\n"
            f"- Text: `{show_command}`\n"
            f"- Visual inputs: `{visual_command}`\n\n"
            "You may use the documented public selectors (`--occurrence`, `--shot`, `--clip`, `--track`, "
            "`--asset`, `--range`, and `--detail`) or repeat the same commands for a focused target. "
            "Do not use AstridClient internals, authoring targets, fixture/evaluator readers, a URL, token, "
            "curl, web search, or direct Runtime HTTP; if a public command fails, report that failure. "
            "The input view is render-free; use composed mode only when the task supplies or requires a "
            "specific output run.\n\n"
            "Use the public timeline tools and the short guide below as useful. Inspect the timeline "
            "or source media as needed for the request. If a named fixture, historical output, or "
            "other required material is not actually available, stop and report the missing setup "
            "input; do not substitute invented evidence.\n\n"
            f"Documentation: [{Path(doc_path).name}]({doc_path})\n"
        )

    prompt = str(row.get("prompt", ""))
    inputs_path = prepared.project_dir / "task-inputs.json"
    inputs = json.loads(inputs_path.read_text(encoding="utf-8"))
    task_inputs = inputs["task_inputs"]
    targets = task_inputs.get("targets", {})
    labels = {
        "A01": {"{opening shot}": targets.get("opening_occurrence", "opening shot")},
        "A02": {"{redundant shot}": targets.get("remove_occurrence", "redundant shot")},
        "A03": {"{closing shot}": targets.get("closing_occurrence", "closing shot"), "{middle shot}": targets.get("middle_occurrence", "middle shot")},
        "A04": {"{feature shot}": targets.get("feature_occurrence", "feature shot"), "{alternate image}": "project/media/" + str(next((asset["sha256"].removeprefix("sha256:") + "/" + Path(asset["path"]).name for asset in inputs["assets"] if asset["alias"] == "alternate-image"), "alternate-image"))},
        "A06": {"{title shot}": targets.get("title_occurrence", "title shot")},
        "A08": {"{demonstration shot}": targets.get("demonstration_occurrence", "demonstration shot"), "{cue time}": str(task_inputs.get("timing", {}).get("cue_time_seconds", 2)) + " seconds"},
        "A09": {"{montage shot}": targets.get("montage_occurrence", targets.get("montage_alias", "montage occurrence"))},
        "A10": {"{last shot}": targets.get("insert_after_occurrence", "last shot")},
    }
    for token, value in labels.get(case_id, {}).items():
        prompt = prompt.replace(token, str(value))
    if case_id == "A09":
        prompt += " The supplied cue times are 0.5, 1.0, 1.5, and 2.0 seconds; the initial quadrant order is top-left, top-right, bottom-right, bottom-left."
    if case_id == "A05":
        prompt += " Use `project/public-sidecars/A05/A05-vo-endpoints.json` for each recorded last-voice frame."
    if case_id == "A06":
        prompt += " Use `project/public-sidecars/A06/A06-text-roles.json` to distinguish the visible title from authored script, transcript, and voice."
    if case_id == "A09":
        prompt += " The four supplied images and their identities are in `project/public-sidecars/A09/A09-images.json`."
    if case_id == "A08":
        still_digest = task_inputs.get("media", {}).get("still_image_digest")
        if still_digest:
            prompt += f" The supplied still identity is `{still_digest}`; use the same identity recorded in task-inputs.json."
    if case_id == "A10":
        prompt += " Use all files under `project/sidecars/A10/A10-images/` and the brightness order in `project/public-sidecars/A10/A10-brightness-collection.json`; each image is three frames at 30 fps."
    target_path = prepared.project_dir / "target.json"
    target_value = (
        json.loads(target_path.read_text(encoding="utf-8"))
        if target_path.is_file() and not target_path.is_symlink()
        else {}
    )
    edit_value = target_value.get("capabilities", {}).get("edit", {}) if isinstance(target_value, Mapping) else {}
    if isinstance(edit_value, Mapping) and edit_value.get("status") == "available":
        prompt += f" The target advertises the public edit route `{edit_value.get('route')}`."
    else:
        prompt += " If the prepared target has no available public edit route, treat that as a setup blocker."
    prompt += (f"\n\nRuntime project: `{prepared.project_id}`; timeline: `{prepared.timeline_id}`. "
               f"Target: `{prepared.project_dir / 'target.json'}`. Task inputs: `{inputs_path}`. "
               "Use the public tools and supplied project information that help complete the request. "
               "Save the requested timeline change. A preview is optional unless the user asks for "
               "one or it helps you make the requested edit; you may make corrective edits as needed.")
    baseline_render = getattr(prepared, "baseline_render", None)
    if isinstance(baseline_render, Mapping):
        if baseline_render.get("status") == "succeeded":
            prompt += (
                f" A case-local starting render is already prepared as run "
                f"`{baseline_render.get('run_id')}` for this same project and timeline; "
                "use it only as the starting/reference view, and render again only when the edited "
                "result needs output verification."
            )
        else:
            prompt += " No starting render is available; use the render-free input view unless the task explicitly requires output evidence."
    return (f"# {case_id} — timeline action\n\n{prompt}\n\n"
            f"Only edit this case's disposable Runtime project.\n\n"
            f"Before editing, run the public text command `python3 -m astrid timelines show --project {prepared.project_id} {prepared.timeline_id} --json`; if a visual check helps, run `python3 -m astrid timelines visualize --project {prepared.project_id} {prepared.timeline_id} --mode inputs --json`. "
            "Use the public Astrid SDK/CLI edit route advertised by the target for the mutation, but never use SDK internals or authoring targets for inspection; the case connection is already in the environment. "
            "Do not use curl, direct Runtime HTTP, web search, fixture/evaluator JSON, or prior-result evidence as a substitute.\n\n"
            f"Documentation: [{Path(doc_path).name}]({doc_path})\n\n"
            )


def write_preparation_table(rows: list[PreparationRow], path: Path) -> Path:
    """Write a compact human/machine table without private answer material."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Timeline fixture preparation table", "",
        "| Case | Kind | Status | Input | Target | Initial condition | Available tools | Blockers |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for row in rows:
        values = [row.case_id, row.kind, row.status, row.input, row.target,
                  row.initial_condition, ", ".join(row.available_tools), "; ".join(row.blockers) or "—"]
        lines.append("| " + " | ".join(value.replace("|", "\\|").replace("\n", " ") for value in values) + " |")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


__all__ = ["ACTION_PREPARATION_KIND", "PREPARATION_KIND", "PreparationRow",
           "build_preparation_table", "inspect_action_sidecars",
           "materialize_action_sidecar_inputs", "materialize_action_target_receipts",
           "prepare_public_case", "prepare_case", "PreparedCase", "render_case_brief",
           "write_preparation_table"]
