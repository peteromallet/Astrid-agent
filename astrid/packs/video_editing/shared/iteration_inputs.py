"""Pathless Iteration inputs and an in-memory offline assembly adapter.

No filesystem or Runtime operation belongs here. Materialized mappings come
from F05's verified confined handoff; their Python type supplies no authority.
Authenticated Runtime CAS/input identity supplies envelope integrity and replay
identity. This module validates structure and consistency only.
"""
from __future__ import annotations

import json
import re
from typing import Any, Mapping, Sequence

SCHEMA_VERSION = 1
# Runtime's per-object transport hard ceiling; admitted actual-byte policy is F05's.
MAX_OBJECT_BYTES = 64 * 1024 * 1024
MAX_DOCUMENT_BYTES = MAX_OBJECT_BYTES
# Implementation recursion safety only, never a product/discovery count limit.
MAX_DEPTH = 64
_LOCATORS = frozenset({
    "path", "file", "file_path", "local_path", "locator", "out_path",
    "source_path", "storage_path", "uri", "url", "storage_uri", "download_url",
    "prepare_dir", "repo_root",
})
_MEDIA_KINDS = frozenset({"video", "audio", "image", "model_3d"})
_BINDING_FIELDS = frozenset({
    "name", "object_id", "sha256", "size", "media_type", "filename", "associations",
})
_ASSOCIATION_FIELDS = frozenset({
    "project", "run_id", "artifact_index", "task_id", "output_id", "source_association_id",
})
_ROOT_FIELDS = frozenset({
    "schema_version", "project", "target_run_id", "manifest", "quality",
    "media_bindings",
})
_MATERIALIZED_FIELDS = frozenset({
    "path", "object_id", "sha256", "size", "media_type", "filename",
})


# Event transport is observational metadata only, never payload authority.
EVENT_PROJECTION_VERSION = 1
_EVENT_TEXT_FIELDS = frozenset({
    "event_id", "aggregate_id", "aggregate_type", "event_type", "type", "occurred_at",
    "timestamp", "created_at",
})
_EVENT_ORDER_FIELDS = frozenset({"sequence", "version", "stream_seq"})
_EVENT_FIELDS = _EVENT_TEXT_FIELDS | _EVENT_ORDER_FIELDS
_EVENT_PROJECTION_FIELDS = frozenset({"projection_version", "omitted_field_count"})


class FrozenInputError(ValueError):
    """Invalid, incomplete, or transport-oversized frozen inputs."""


def _fail(message: str) -> None:
    raise FrozenInputError(message)


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 1024 or any(ord(c) < 32 for c in value):
        _fail(f"{label} must be a nonempty bounded string")
    return value


def _integer(value: Any, label: str, maximum: int | None = None) -> int:
    if type(value) is not int or value < 0:
        _fail(f"{label} must be a nonnegative integer")
    if maximum is not None and value > maximum:
        _fail(f"{label} exceeds its transport byte limit")
    return value


def _digest(value: Any, label: str) -> str:
    value = _text(value, label).removeprefix("sha256:").lower()
    if not re.fullmatch(r"[0-9a-f]{64}", value):
        _fail(f"{label} must be a SHA-256 digest")
    return value


def _filename(value: Any) -> str:
    value = _text(value, "filename")
    # Match the Runtime producer-registration safe flat basename contract.
    if len(value) > 512 or value in {".", ".."} or "/" in value or "\\" in value or any(ord(c) == 127 for c in value):
        _fail("filename must be a safe flat transport basename of at most 512 characters")
    return value


def _json_copy(value: Any, *, strip_locators: bool = False) -> Any:
    def visit(item: Any, depth: int) -> Any:
        if depth > MAX_DEPTH:
            _fail("parser nesting safety limit exceeded")
        if isinstance(item, Mapping):
            result = {}
            for key, val in item.items():
                if not isinstance(key, str):
                    _fail("JSON object keys must be strings")
                if key in _LOCATORS:
                    if strip_locators:
                        continue
                    _fail(f"executable locator field {key!r} is forbidden in frozen inputs")
                result[key] = visit(val, depth + 1)
            return result
        if isinstance(item, (list, tuple)):
            return [visit(val, depth + 1) for val in item]
        if item is None or type(item) in (str, bool, int, float):
            return item
        _fail("inputs must contain only JSON values")

    result = visit(value, 0)
    try:
        _canonical(result)
    except (ValueError, TypeError, OverflowError) as exc:
        raise FrozenInputError("inputs must contain finite JSON values") from exc
    return result


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _requires_media(artifact: Mapping[str, Any]) -> bool:
    media_type = artifact.get("media_type") or artifact.get("mime_type") or ""
    return (
        artifact.get("kind") in _MEDIA_KINDS
        or str(media_type).split("/", 1)[0] in {"image", "audio", "video", "model"}
    )


def project_event(event: Mapping[str, Any]) -> dict[str, Any]:
    """Retain explicit identity/type/order/time fields without inspecting payloads."""
    if not isinstance(event, Mapping):
        _fail("event must be an object")
    result = {}
    for key in _EVENT_FIELDS:
        if key not in event:
            continue
        value = event[key]
        result[key] = _text(value, f"event {key}") if key in _EVENT_TEXT_FIELDS else _integer(value, f"event {key}")
    for aliases in (("event_type", "type"), ("occurred_at", "timestamp", "created_at")):
        if len({result[key] for key in aliases if key in result}) > 1:
            _fail("conflicting event metadata aliases")
    prior_omitted = 0
    if "projection_version" in event or "omitted_field_count" in event:
        # A supplied marker is not authority: only a closed, validated metadata
        # form can be retained, and unknown fields may not hide behind it.
        if (set(event) - _EVENT_FIELDS - _EVENT_PROJECTION_FIELDS
                or not _EVENT_PROJECTION_FIELDS <= set(event)):
            _fail("raw event payload or unknown event fields in claimed projection")
        if type(event.get("projection_version")) is not int or event["projection_version"] != EVENT_PROJECTION_VERSION:
            _fail("unsupported event projection version")
        prior_omitted = _integer(event.get("omitted_field_count"), "event omitted_field_count")
    result["projection_version"] = EVENT_PROJECTION_VERSION
    result["omitted_field_count"] = prior_omitted + len(set(event) - _EVENT_FIELDS - _EVENT_PROJECTION_FIELDS)
    return result


def _project_manifest_events(manifest: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(manifest)
    runs = manifest.get("runs")
    if not isinstance(runs, list):
        _fail("manifest runs must be an ordered list")
    result["runs"] = []
    for raw in runs:
        if not isinstance(raw, Mapping):
            _fail("run must be an object")
        run = dict(raw)
        if "run_events" in run:
            if not isinstance(run["run_events"], list):
                _fail("run_events must be an ordered list")
            run["run_events"] = [project_event(event) for event in run["run_events"]]
        if "task_events" in run:
            if not isinstance(run["task_events"], Mapping):
                _fail("task_events must be an object")
            run["task_events"] = {}
            for task_id, events in raw["task_events"].items():
                if not isinstance(events, list):
                    _fail("task events must be an ordered list")
                run["task_events"][task_id] = [project_event(event) for event in events]
        result["runs"].append(run)
    return result


def _validate_projected_events(run: Mapping[str, Any]) -> None:
    events = run.get("run_events", [])
    task_events = run.get("task_events", {})
    if not isinstance(events, list) or not isinstance(task_events, dict):
        _fail("projected events have invalid collections")
    all_events = list(events)
    for task_id, values in task_events.items():
        _text(task_id, "event task_id")
        if not isinstance(values, list):
            _fail("task events must be an ordered list")
        all_events.extend(values)
    for event in all_events:
        if (not isinstance(event, dict) or set(event) - _EVENT_FIELDS - _EVENT_PROJECTION_FIELDS
                or not _EVENT_PROJECTION_FIELDS <= set(event)):
            _fail("raw event payload or unknown event fields are forbidden in frozen inputs")
        if project_event(event) != event:
            _fail("invalid event projection")


def _documents(manifest: Any, quality: Any, project: str, target: str) -> dict[tuple[str, int], dict[str, Any]]:
    for label, doc in (("manifest", manifest), ("quality", quality)):
        if not isinstance(doc, dict) or type(doc.get("schema_version")) is not int or doc["schema_version"] != 1:
            _fail(f"{label} must use existing schema_version 1")
        if doc.get("target_run_id") != target:
            _fail(f"{label} target mismatch")
        authority = doc.get("authority")
        if not isinstance(authority, dict) or authority.get("project") != project:
            _fail(f"{label} project mismatch")
    runs = manifest.get("runs")
    if not isinstance(runs, list) or not runs:
        _fail("manifest runs must be a nonempty ordered list")
    if "quality" in manifest and manifest["quality"] != quality:
        _fail("embedded manifest quality conflicts with quality document")
    ids = []
    seen_ids = set()
    artifacts = {}
    for run in runs:
        if not isinstance(run, dict):
            _fail("run must be an object")
        _validate_projected_events(run)
        run_id = _text(run.get("run_id"), "run_id")
        if run_id in seen_ids:
            _fail("duplicate run_id")
        seen_ids.add(run_id)
        ids.append(run_id)
        outputs = run.get("output_artifacts", [])
        if not isinstance(outputs, list):
            _fail("output_artifacts must be an ordered list")
        for index, artifact in enumerate(outputs):
            if not isinstance(artifact, dict):
                _fail("output artifact must be an object")
            if artifact.get("kind") is not None:
                _text(artifact["kind"], "artifact kind")
            for field in ("object_id", "task_id", "output_id", "source_association_id"):
                if artifact.get(field) is not None:
                    _text(artifact[field], f"artifact {field}")
            if artifact.get("project") is not None and artifact["project"] != project:
                _fail("artifact project mismatch")
            if artifact.get("run_id") is not None and artifact["run_id"] != run_id:
                _fail("artifact run_id mismatch")
            artifacts[run_id, index] = artifact
    if target not in ids:
        _fail("target run is absent")
    declared_ids = manifest["authority"].get("run_ids")
    if declared_ids is not None and declared_ids != ids:
        _fail("manifest run identity/order conflicts with authority metadata")
    if "total_runs" in quality and (type(quality["total_runs"]) is not int or quality["total_runs"] != len(runs)):
        _fail("quality total_runs mismatch")
    return artifacts


def _bindings(raw: Any, artifacts: Mapping[tuple[str, int], dict[str, Any]], project: str) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        _fail("media bindings must be an explicit list")
    result = []
    names, objects, digests, filenames, references = set(), set(), set(), set(), set()
    for binding in raw:
        if not isinstance(binding, dict) or set(binding) != _BINDING_FIELDS:
            _fail("binding has missing or unknown fields")
        item = dict(binding)
        name = _text(item["name"], "binding name")
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,63}", name):
            _fail("unsafe binding name")
        object_id = _text(item["object_id"], "object_id")
        digest = item["sha256"] = _digest(item["sha256"], "binding sha256")
        if object_id != f"sha256:{digest}":
            _fail("object_id must be canonical sha256:<digest> and match the binding digest")
        _integer(item["size"], "object size", MAX_OBJECT_BYTES)
        media_type = _text(item["media_type"], "media_type")
        if not re.fullmatch(r"[a-z0-9!#$&^_.+-]+/[a-z0-9!#$&^_.+-]+", media_type):
            _fail("media_type must be an exact MIME type")
        filename = _filename(item["filename"])
        if name in names or object_id in objects or digest in digests or filename.casefold() in filenames:
            _fail("duplicate/conflicting object binding; share associations in one binding")
        names.add(name)
        objects.add(object_id)
        digests.add(digest)
        filenames.add(filename.casefold())
        associations = item["associations"]
        if not isinstance(associations, list) or not associations:
            _fail("binding requires source associations")
        normalized = []
        for association in associations:
            if not isinstance(association, dict) or not {"project", "run_id", "artifact_index"} <= set(association) or not set(association) <= _ASSOCIATION_FIELDS:
                _fail("association has missing or unknown fields")
            if association["project"] != project:
                _fail("association project mismatch")
            run_id = _text(association["run_id"], "association run_id")
            index = _integer(association["artifact_index"], "artifact_index")
            reference = (run_id, index)
            if reference in references:
                _fail("duplicate/conflicting artifact reference")
            artifact = artifacts.get(reference)
            if artifact is None:
                _fail("unresolved artifact reference")
            if not _requires_media(artifact):
                _fail("binding association must select render media")
            if artifact.get("_resolution_error"):
                _fail("required artifact already carries a resolution error")
            if artifact.get("object_id") is not None and artifact["object_id"] != object_id:
                _fail("artifact object_id conflicts with binding")
            for field in ("sha256", "content_sha256", "digest"):
                if artifact.get(field) is not None and _digest(artifact[field], field) != digest:
                    _fail("artifact digest conflicts with binding")
            if "size" in artifact and (type(artifact["size"]) is not int or artifact["size"] != item["size"]):
                _fail("artifact size conflicts with binding")
            for field in ("media_type", "mime_type"):
                if artifact.get(field) is not None and artifact[field] != media_type:
                    _fail("artifact media_type conflicts with binding")
            # Runtime can omit these identities. When available they travel
            # explicitly, and neither side may silently invent or discard them.
            for field in ("task_id", "output_id", "source_association_id"):
                if field in association:
                    _text(association[field], field)
                if artifact.get(field) is not None and association.get(field) != artifact[field]:
                    _fail(f"artifact {field} conflicts with association")
            references.add(reference)
            normalized.append(dict(association))
        item["associations"] = sorted(normalized, key=lambda a: (a["run_id"], a["artifact_index"]))
        result.append(item)
    required = {reference for reference, artifact in artifacts.items() if _requires_media(artifact)}
    if not required <= references:
        _fail("unresolved required media references; refusing a silent card fallback")
    return sorted(result, key=lambda item: item["name"])


def _normalize(body: Mapping[str, Any]) -> dict[str, Any]:
    result = _json_copy(body)
    if type(result.get("schema_version")) is not int or result["schema_version"] != SCHEMA_VERSION:
        _fail("unsupported frozen schema_version")
    project = _text(result.get("project"), "project")
    target = _text(result.get("target_run_id"), "target_run_id")
    artifacts = _documents(result.get("manifest"), result.get("quality"), project, target)
    result["media_bindings"] = _bindings(result.get("media_bindings"), artifacts, project)
    for label in ("manifest", "quality"):
        if len(_canonical(result[label])) > MAX_DOCUMENT_BYTES:
            _fail(f"{label} document byte limit exceeded")
    if len(_canonical(result)) > MAX_DOCUMENT_BYTES:
        _fail("frozen envelope byte limit exceeded")
    return result


def freeze_inputs(
    manifest: Mapping[str, Any], quality: Mapping[str, Any], media_bindings: Sequence[Mapping[str, Any]],
    *, project: str, target_run_id: str,
) -> dict[str, Any]:
    """Normalize admitted documents; remove locator fields without using them.

    Authenticated Runtime CAS/input identity establishes envelope integrity and
    replay identity. F05 verifies custody and enforces actual child closure bytes
    against the persisted admitted policy. Discovery metadata and local retained
    copies have separate budgets. This helper grants no authority.
    """
    if not isinstance(media_bindings, (list, tuple)):
        _fail("media bindings must be an explicit list")
    return _normalize({
        "schema_version": SCHEMA_VERSION, "project": project, "target_run_id": target_run_id,
        "manifest": _json_copy(_project_manifest_events(manifest), strip_locators=True),
        "quality": _json_copy(quality, strip_locators=True),
        "media_bindings": list(media_bindings),
    })


def normalize_frozen_inputs(frozen: Mapping[str, Any], *, project: str, target_run_id: str) -> dict[str, Any]:
    """Validate structure and expected identities; return an independent copy."""
    if not isinstance(frozen, Mapping) or set(frozen) != _ROOT_FIELDS:
        _fail("frozen inputs have missing or unknown fields")
    if frozen["project"] != project or frozen["target_run_id"] != target_run_id:
        _fail("expected project or target mismatch")
    return _normalize(frozen)


def serialize_frozen_inputs(frozen: Mapping[str, Any], *, project: str, target_run_id: str) -> bytes:
    return _canonical(normalize_frozen_inputs(frozen, project=project, target_run_id=target_run_id))


def parse_frozen_inputs(data: bytes, *, project: str, target_run_id: str) -> dict[str, Any]:
    if not isinstance(data, bytes) or len(data) > MAX_DOCUMENT_BYTES:
        _fail("serialized envelope byte limit exceeded")

    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result = {}
        for key, value in items:
            if key in result:
                _fail("duplicate JSON field")
            result[key] = value
        return result

    try:
        value = json.loads(data, object_pairs_hook=pairs)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise FrozenInputError("invalid frozen JSON") from exc
    return normalize_frozen_inputs(value, project=project, target_run_id=target_run_id)


def assembly_inputs(
    frozen: Mapping[str, Any], materialized: Mapping[str, Mapping[str, Any]], *, project: str, target_run_id: str,
) -> dict[str, Any]:
    """Return kwargs for assemble_iteration(..., runtime_client=None).

    Paths come exclusively from host-owned mappings; JSON locators are forbidden.
    Exact metadata/path shape is checked before returning, but mapping/type
    identity does not authenticate custody. F05's confined verification supplies
    that trust boundary. Caller supplies out_path/repo_root.
    """
    value = normalize_frozen_inputs(frozen, project=project, target_run_id=target_run_id)
    bindings = value["media_bindings"]
    if not isinstance(materialized, Mapping) or set(materialized) != {item["name"] for item in bindings}:
        _fail("materialized map must match the exact admitted binding set")
    runs = {run["run_id"]: run for run in value["manifest"]["runs"]}
    paths: dict[str, str] = {}
    for binding in bindings:
        entry = materialized[binding["name"]]
        if not isinstance(entry, Mapping) or set(entry) != _MATERIALIZED_FIELDS:
            _fail("materialized binding mapping has missing or unknown fields")
        path = entry["path"]
        if not isinstance(path, str) or not path.startswith("/") or "\\" in path or any(ord(c) < 32 for c in path) or any(part in {".", ".."} for part in path.split("/")):
            _fail("materialized path must be an absolute staging path")
        if path in paths and paths[path] != binding["object_id"]:
            _fail("conflicting materialized paths")
        paths[path] = binding["object_id"]
        for field in ("object_id", "sha256", "size", "media_type", "filename"):
            actual = entry[field]
            if field == "size" and type(actual) is not int:
                _fail("materialized size must be an integer")
            if actual != binding[field]:
                _fail(f"materialized {field} mismatch")
        for association in binding["associations"]:
            artifact = runs[association["run_id"]]["output_artifacts"][association["artifact_index"]]
            artifact.update({key: binding[key] for key in ("object_id", "sha256", "size", "media_type")})
            artifact["path"] = path
    return {"input_manifest": value["manifest"], "input_quality": value["quality"], "runtime_client": None, "runtime_project": None}


def pathless_render_registry(registry: Mapping[str, Any], frozen: Mapping[str, Any], *, project: str, target_run_id: str) -> dict[str, Any]:
    """Replace M09 asset file locators with exact frozen dependency identities.

    Match by M09's deterministic asset_<run_id>_<artifact_index> key, never by a
    file path. The later F04/F05 bridge must admit these dependencies separately.
    """
    value = normalize_frozen_inputs(frozen, project=project, target_run_id=target_run_id)
    by_asset = {}
    for binding in value["media_bindings"]:
        for association in binding["associations"]:
            key = f"asset_{association['run_id']}_{association['artifact_index']}"
            if key in by_asset:
                _fail("ambiguous assembly asset identity")
            by_asset[key] = binding
    if not isinstance(registry, Mapping) or set(registry) != {"assets"} or not isinstance(registry["assets"], Mapping):
        _fail("invalid assembly registry")
    result = {}
    for key, asset in registry["assets"].items():
        if key not in by_asset or not isinstance(asset, Mapping):
            _fail("registry contains an unadmitted asset")
        binding = by_asset[key]
        if asset.get("content_sha256") != binding["sha256"]:
            _fail("registry asset digest mismatch")
        entry = _json_copy(asset, strip_locators=True)
        entry.update({"binding": binding["name"], "object_id": binding["object_id"], "content_sha256": binding["sha256"], "size": binding["size"], "media_type": binding["media_type"]})
        result[key] = entry
    if set(result) != set(by_asset):
        _fail("registry is missing required media; refusing a silent card fallback")
    return {"assets": result}
