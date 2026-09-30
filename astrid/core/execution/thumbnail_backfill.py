"""Bounded, on-demand Runtime thumbnail backfill.

The backfill is a small Runtime worker: it reads committed Generations and
variants, extracts the same thumbnail recipe used by the generic host, and
settles one admitted ``generation.thumbnail.attach`` effect.  It deliberately
does not write SQLite, Supabase, or browser state.
"""

from __future__ import annotations

import hashlib
import math
import tempfile
import uuid
from collections.abc import Mapping
from dataclasses import asdict, dataclass, is_dataclass
from pathlib import Path
from typing import Any, Callable

from astrid.core.execution.thumbnails import (
    THUMBNAIL_RECIPE_VERSION,
    ThumbnailError,
    extract_thumbnail,
    is_visual_media_type,
)
from astrid.core.receipts.canonical import canonical_json
from astrid.core.timeline.authoring_bundle import open_authoring_bundle
from astrid.core.timeline.duration import clip_timeline_duration
from astrid.sdk.pagination import page_pair

THUMBNAIL_BACKFILL_CAPABILITY_ID = "media.thumbnail_backfill"
THUMBNAIL_BACKFILL_CAPABILITY_DIGEST = "sha256:" + hashlib.sha256(
    b"media.thumbnail_backfill.v1"
).hexdigest()
THUMBNAIL_BACKFILL_MAX_LIMIT = 100
THUMBNAIL_BACKFILL_PAGE_SIZE = 50
THUMBNAIL_BACKFILL_MAX_INTERLEAVED_CLAIMS = 100
THUMBNAIL_BACKFILL_MAX_VERSION_RETRIES = 1
THUMBNAIL_OUTPUT_NAME = "thumbnail"
THUMBNAIL_OUTPUT_FILENAME = "thumbnail.jpg"


class ThumbnailBackfillError(RuntimeError):
    """A bounded, actionable backfill failure."""


@dataclass(frozen=True)
class ThumbnailBackfillReport:
    project_id: str
    scanned: int = 0
    attached: int = 0
    already_ready: int = 0
    unsupported: int = 0
    unavailable: int = 0
    failed: int = 0
    dry_run: bool = False
    generation_ids: tuple[str, ...] = ()
    diagnostics: tuple[dict[str, str], ...] = ()
    receipts: tuple[dict[str, Any], ...] = ()

    def as_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["generation_ids"] = list(self.generation_ids)
        value["diagnostics"] = [dict(item) for item in self.diagnostics]
        value["receipts"] = [dict(item) for item in self.receipts]
        return value


@dataclass(frozen=True)
class VariantThumbnailBackfillReport:
    project_id: str
    scanned: int = 0
    attached: int = 0
    already_ready: int = 0
    unsupported: int = 0
    unavailable: int = 0
    failed: int = 0
    dry_run: bool = False
    variant_ids: tuple[str, ...] = ()
    diagnostics: tuple[dict[str, str], ...] = ()
    receipts: tuple[dict[str, Any], ...] = ()

    def as_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["variant_ids"] = list(self.variant_ids)
        value["diagnostics"] = [dict(item) for item in self.diagnostics]
        value["receipts"] = [dict(item) for item in self.receipts]
        return value


@dataclass(frozen=True)
class TimelineThumbnailBackfillReport:
    project_id: str
    timeline_id: str
    head_revision_id: str
    scanned: int = 0
    attached: int = 0
    already_ready: int = 0
    unsupported: int = 0
    unavailable: int = 0
    failed: int = 0
    source_fetches: int = 0
    dry_run: bool = False
    requests: tuple[dict[str, Any], ...] = ()
    diagnostics: tuple[dict[str, str], ...] = ()

    def as_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["requests"] = [dict(item) for item in self.requests]
        value["diagnostics"] = [dict(item) for item in self.diagnostics]
        return value


def _plain(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return {key: _plain(child) for key, child in asdict(value).items()}
    if isinstance(value, Mapping):
        return {str(key): _plain(child) for key, child in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(child) for child in value]
    return value


def _project_id(runtime: Any, project: str) -> str:
    """Resolve the CLI's id-or-slug selector to the immutable Runtime id."""
    resource = _plain(runtime.get_project(str(project)))
    if not isinstance(resource, Mapping):
        raise ThumbnailBackfillError("Runtime project lookup returned an invalid resource")
    value = resource.get("project_id") or resource.get("id")
    if not isinstance(value, str) or not value:
        raise ThumbnailBackfillError("Runtime project lookup returned no project id")
    return value


def _page(value: Any, *, operation: str) -> tuple[list[Any], str | None]:
    page = page_pair(_plain(value))
    if page is None:
        raise ThumbnailBackfillError(
            f"Runtime {operation} returned an invalid cursor page"
        )
    return page


def _paged(
    reader: Callable[..., Any],
    *args: Any,
    limit: int,
    operation: str,
    max_pages: int = 10_000,
    **kwargs: Any,
) -> list[Any]:
    rows: list[Any] = []
    cursor: str | None = None
    seen: set[str] = set()
    for _ in range(max_pages):
        page_rows, next_cursor = _page(
            reader(*args, cursor=cursor, limit=limit, **kwargs),
            operation=operation,
        )
        rows.extend(page_rows)
        if next_cursor is None:
            return rows
        if next_cursor == cursor or next_cursor in seen:
            raise ThumbnailBackfillError(
                f"Runtime {operation} returned cyclic pagination"
            )
        seen.add(next_cursor)
        cursor = next_cursor
    raise ThumbnailBackfillError(f"Runtime {operation} exceeded pagination bound")


def _required_string(value: Any, *, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ThumbnailBackfillError(f"Runtime resource field {field!r} is missing")
    return value


def _source_id(variant: Mapping[str, Any]) -> str | None:
    value = variant.get("object_id")
    if not isinstance(value, str) or not value.startswith("sha256:"):
        return None
    if len(value) != 71:
        return None
    return value


def _variant_media_type(
    variant: Mapping[str, Any], object_rows: Mapping[str, Mapping[str, Any]]
) -> str | None:
    metadata = variant.get("metadata")
    if isinstance(metadata, Mapping):
        media_type = metadata.get("media_type")
        if isinstance(media_type, str) and media_type.strip():
            return media_type.strip().lower()
    source_id = _source_id(variant)
    if source_id is None:
        return None
    object_row = object_rows.get(source_id)
    if object_row is None:
        return None
    media_type = object_row.get("media_type")
    return media_type.strip().lower() if isinstance(media_type, str) and media_type.strip() else None


def _primary_variant(variants: list[Mapping[str, Any]]) -> Mapping[str, Any] | None:
    if not variants:
        return None
    explicit = [
        variant
        for variant in variants
        if isinstance(variant.get("metadata"), Mapping)
        and variant["metadata"].get("is_primary") is True
    ]
    if len(explicit) > 1:
        raise ThumbnailBackfillError("generation has multiple explicit primary variants")
    if explicit:
        return explicit[0]
    original = next(
        (variant for variant in variants if variant.get("variant_type") == "original"),
        None,
    )
    return original or variants[0]


def _ready_thumbnail(
    generation: Mapping[str, Any], source_object_id: str
) -> bool:
    metadata = generation.get("metadata")
    thumbnail = metadata.get("thumbnail") if isinstance(metadata, Mapping) else None
    return (
        isinstance(thumbnail, Mapping)
        and isinstance(thumbnail.get("object_id"), str)
        and thumbnail["object_id"].startswith("sha256:")
        and isinstance(thumbnail.get("source_object_id"), str)
        and thumbnail.get("source_object_id") == source_object_id
        and thumbnail.get("recipe_version") == THUMBNAIL_RECIPE_VERSION
    )


def _ready_variant_thumbnail(variant: Mapping[str, Any]) -> bool:
    source_object_id = _source_id(variant)
    thumbnail = variant.get("thumbnail")
    return (
        source_object_id is not None
        and isinstance(thumbnail, Mapping)
        and isinstance(thumbnail.get("object_id"), str)
        and thumbnail["object_id"].startswith("sha256:")
        and thumbnail.get("source_object_id") == source_object_id
        and thumbnail.get("recipe_version") == THUMBNAIL_RECIPE_VERSION
    )


def _stable_key(*parts: str) -> str:
    payload = "\x1f".join(parts).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _output_idempotency_key(binding: Mapping[str, Any]) -> str:
    """Match GenericPackHost's canonical managed-output key exactly."""
    return "output-" + hashlib.sha256(canonical_json(dict(binding)).encode()).hexdigest()


def _capability_registration(runtime_epoch: int) -> dict[str, Any]:
    return {
        "executor_id": None,
        "max_concurrency": 1,
        "resource_keys": [],
        "capabilities": [
            {
                "capability_id": THUMBNAIL_BACKFILL_CAPABILITY_ID,
                "definition_digest": THUMBNAIL_BACKFILL_CAPABILITY_DIGEST,
                "status": "ready",
                "required_resource_keys": [],
                "estimated_scratch_bytes": 2 * 1024 * 1024,
                "estimated_output_bytes": 512 * 1024,
            }
        ],
        "protocol": "workspace.v1",
        "runtime_epoch": runtime_epoch,
    }


def _unwrap_mutation(value: Any) -> Mapping[str, Any]:
    value = _plain(value)
    if isinstance(value, Mapping) and isinstance(value.get("data"), Mapping):
        return value["data"]
    if isinstance(value, Mapping):
        return value
    raise ThumbnailBackfillError("Runtime mutation returned an invalid resource")


def ensure_imported_source_thumbnail(
    runtime: Any,
    *,
    project_id: str,
    source_object_id: str,
    source_bytes: bytes,
    media_type: str,
    idempotency_key: str,
    source_time_seconds: float = 0.001,
) -> Mapping[str, Any] | None:
    """Attach the standard typed thumbnail while importing a video object.

    This is deliberately an object-level enhancement: it never creates a
    Generation or edits a timeline. Images remain object-only because their
    source bytes are already directly displayable.
    """
    if not str(media_type).lower().startswith("video/"):
        return None
    existing = _plain(runtime.get_source_frame_thumbnail(
        project_id,
        source_object_id,
        source_time_seconds,
        recipe_version=THUMBNAIL_RECIPE_VERSION,
    ))
    if isinstance(existing, Mapping) and isinstance(existing.get("thumbnail"), Mapping):
        return existing["thumbnail"]
    identity = _stable_key(project_id, source_object_id, f"{source_time_seconds:.6f}", str(THUMBNAIL_RECIPE_VERSION))
    with tempfile.TemporaryDirectory(prefix="astrid-import-thumbnail-") as workdir:
        root = Path(workdir)
        source_path = root / "source"
        thumbnail_path = root / THUMBNAIL_OUTPUT_FILENAME
        source_path.write_bytes(source_bytes)
        extract_thumbnail(source_path, thumbnail_path, media_type, source_time_seconds=source_time_seconds)
        uploaded = _unwrap_mutation(runtime.ingest_project_object(
            project_id,
            thumbnail_path.read_bytes(),
            media_type="image/jpeg",
            filename=f"thumbnail-{identity[:16]}.jpg",
            idempotency_key=f"{idempotency_key}:thumbnail:upload",
        ))
    thumbnail_object_id = uploaded.get("object_id")
    if not isinstance(thumbnail_object_id, str):
        raise ThumbnailBackfillError("Runtime thumbnail ingest returned no object_id")
    descriptor = {
        "object_id": thumbnail_object_id,
        "source_object_id": source_object_id,
        "recipe_version": THUMBNAIL_RECIPE_VERSION,
        "selection": {"kind": "source_frame", "source_time_seconds": round(source_time_seconds, 6)},
    }
    ensured = _plain(runtime.ensure_source_frame_thumbnail(
        project_id,
        descriptor,
        idempotency_key=f"{idempotency_key}:thumbnail:ensure",
    ))
    if isinstance(ensured, Mapping) and isinstance(ensured.get("data"), Mapping):
        return ensured["data"]
    if isinstance(ensured, Mapping):
        return ensured
    raise ThumbnailBackfillError("Runtime source-frame ensure returned an invalid descriptor")


def _claim_task(runtime: Any, *, actor_id: str, epoch: int) -> Mapping[str, Any]:
    claimed = _plain(
        runtime.claim_task(
            executor_id=actor_id,
            capability_ids=[THUMBNAIL_BACKFILL_CAPABILITY_ID],
            # Claim is a new lease command each time this bounded worker is
            # invoked.  Reusing a task-derived key would replay an expired
            # attempt after a retry instead of acquiring a fresh lease.
            idempotency_key="thumbnail-backfill-claim-" + uuid.uuid4().hex,
            runtime_epoch=epoch,
        )
    )
    if not isinstance(claimed, Mapping) or not claimed.get("attempt_id"):
        raise ThumbnailBackfillError("Runtime did not return a runnable thumbnail attempt")
    return claimed


def _claim_source(
    runtime: Any,
    claim: Mapping[str, Any],
) -> tuple[str, Mapping[str, Any]]:
    """Read the immutable source/effect inputs from a claimed task."""

    effect = claim.get("expected_effect")
    if not isinstance(effect, Mapping) or effect.get("effect_type") != "generation.thumbnail.attach":
        raise ThumbnailBackfillError(
            "claimed task is not a generation.thumbnail.attach backfill"
        )
    _required_string(effect.get("target_id"), field="effect.target_id")
    payload = effect.get("payload")
    if not isinstance(payload, Mapping):
        raise ThumbnailBackfillError("claimed thumbnail effect has no payload")
    source_object_id = _source_id({"object_id": payload.get("source_object_id")})
    if source_object_id is None:
        raise ThumbnailBackfillError("claimed thumbnail effect has no valid source object")
    input_object_ids = claim.get("input_object_ids")
    if not isinstance(input_object_ids, list) or source_object_id not in input_object_ids:
        raise ThumbnailBackfillError(
            "claimed thumbnail task input does not match its settlement effect"
        )
    spec = claim.get("spec")
    media_type = spec.get("source_media_type") if isinstance(spec, Mapping) else None
    if not isinstance(media_type, str) or not media_type.strip():
        head = _plain(runtime.head_object(source_object_id))
        headers = head.get("headers") if isinstance(head, Mapping) else None
        media_type = (
            headers.get("content-type")
            or headers.get("Content-Type")
            if isinstance(headers, Mapping)
            else None
        )
    if not isinstance(media_type, str) or not media_type.strip():
        raise ThumbnailBackfillError(
            f"claimed thumbnail source {source_object_id} has no media type"
        )
    return media_type.strip().lower(), effect


def _register_worker(runtime: Any, *, actor_id: str, epoch: int) -> None:
    if not isinstance(actor_id, str) or not actor_id.strip():
        raise ThumbnailBackfillError(
            "Runtime handshake did not expose an actor id for the backfill worker"
        )
    runtime.register_capability(
        THUMBNAIL_BACKFILL_CAPABILITY_ID,
        THUMBNAIL_BACKFILL_CAPABILITY_DIGEST,
        required_resource_keys=[],
        status="ready",
        estimated_scratch_bytes=2 * 1024 * 1024,
        estimated_output_bytes=512 * 1024,
        idempotency_key=f"thumbnail-backfill-capability-{THUMBNAIL_RECIPE_VERSION}",
    )
    registration = _capability_registration(epoch)
    registration["executor_id"] = actor_id
    runtime.register_executor(
        registration,
        # Registration is also the worker lease heartbeat.  A deterministic
        # key would replay the first registration forever and leave the
        # executor stale after the liveness window; each live registration is
        # one fresh durable command, while transport retries can reuse the
        # key from that call site.
        idempotency_key=(
            f"thumbnail-backfill-executor-{actor_id}-{epoch}-{uuid.uuid4().hex}"
        ),
    )


def _fail_claimed_attempt(
    runtime: Any,
    claim: Mapping[str, Any],
    *,
    runtime_epoch: int,
    error: str,
) -> None:
    attempt_id = _required_string(claim.get("attempt_id"), field="attempt_id")
    lease_id = _required_string(claim.get("lease_id"), field="lease_id")
    fence = claim.get("fence")
    if isinstance(fence, bool) or not isinstance(fence, int) or fence < 1:
        raise ThumbnailBackfillError("claimed task returned an invalid fence")
    runtime.fail_attempt(
        attempt_id,
        lease_id=lease_id,
        fence=fence,
        runtime_epoch=int(claim.get("runtime_epoch", runtime_epoch)),
        error={"code": "thumbnail_backfill_failed", "message": error[:240]},
        idempotency_key="thumbnail-backfill-fail-" + _stable_key(attempt_id, error[:240]),
    )


def _is_version_conflict(exc: BaseException) -> bool:
    """Return whether Runtime rejected settlement because its target version moved."""

    code = getattr(exc, "code", None)
    if code not in {"conflict", "stale_version"}:
        return False
    details = getattr(exc, "details", None)
    if isinstance(details, Mapping) and {
        "expected",
        "actual",
    } <= set(details):
        return True
    if isinstance(details, Mapping) and {
        "expected_version",
        "current_version",
    } <= set(details):
        return True
    message = str(getattr(exc, "message", exc)).lower()
    return "version" in message and any(
        marker in message for marker in ("stale", "conflict", "expected")
    )


def _reread_generation_primary(
    runtime: Any,
    generation_id: str,
) -> tuple[Mapping[str, Any], str | None]:
    """Reread the generation and its primary source after a version conflict."""

    generation = _plain(runtime.get_generation(generation_id))
    if not isinstance(generation, Mapping):
        raise ThumbnailBackfillError("Runtime reread returned an invalid generation")
    variants = _paged(
        runtime.list_variants,
        generation_id,
        limit=THUMBNAIL_BACKFILL_PAGE_SIZE,
        operation="variant reread",
    )
    primary = _primary_variant(
        [item for item in variants if isinstance(item, Mapping)]
    )
    return generation, _source_id(primary) if primary is not None else None


def _admission_key(
    *,
    project_id: str,
    generation_id: str,
    source_object_id: str,
    source_media_type: str,
    effect: Mapping[str, Any],
    spec: Mapping[str, Any],
    storage_estimate: Mapping[str, int],
) -> str:
    """Bind replay identity to the complete immutable generation proposal."""

    identity = {
        "project_id": project_id,
        "generation_id": generation_id,
        "input_object_ids": [source_object_id],
        "source_media_type": source_media_type,
        "effect": dict(effect),
        "spec": dict(spec),
        "storage_estimate": dict(storage_estimate),
    }
    return "thumbnail-backfill-task-" + hashlib.sha256(
        canonical_json(identity).encode()
    ).hexdigest()


def _same_effect(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    return canonical_json(dict(left)) == canonical_json(dict(right))


def _recover_failed_admission(
    runtime: Any,
    *,
    task_id: str,
    admission_key: str,
) -> None:
    """Requeue a replayed terminal failure through Runtime's task transition."""

    task = _plain(runtime.get_task(task_id))
    if not isinstance(task, Mapping):
        raise ThumbnailBackfillError("Runtime task reread returned an invalid resource")
    state = task.get("state")
    if state != "failed":
        return
    version = task.get("version")
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        raise ThumbnailBackfillError(
            f"failed thumbnail task {task_id} has an invalid version"
        )
    runtime.retry_task(
        task_id,
        expected_version=version,
        idempotency_key="thumbnail-backfill-retry-"
        + _stable_key(task_id, admission_key, str(version)),
    )


def _settle_claimed_task(
    runtime: Any,
    *,
    claim: Mapping[str, Any],
    effect: Mapping[str, Any],
    source_object_id: str,
    thumbnail_path: Path,
    actor_id: str,
) -> Mapping[str, Any]:
    task_id = _required_string(claim.get("task_id"), field="task_id")
    project_id = _required_string(claim.get("project_id"), field="project_id")
    run_id_value = claim.get("run_id")
    if not isinstance(run_id_value, str) or not run_id_value.strip():
        task = _plain(runtime.get_task(task_id))
        run_id_value = task.get("run_id") if isinstance(task, Mapping) else None
    run_id = _required_string(run_id_value, field="run_id")
    attempt_id = _required_string(claim.get("attempt_id"), field="attempt_id")
    lease_id = _required_string(claim.get("lease_id"), field="lease_id")
    fence = claim.get("fence")
    runtime_epoch = claim.get("runtime_epoch")
    if isinstance(fence, bool) or not isinstance(fence, int) or fence < 1:
        raise ThumbnailBackfillError("Runtime claim returned an invalid fence")
    if isinstance(runtime_epoch, bool) or not isinstance(runtime_epoch, int) or runtime_epoch < 1:
        raise ThumbnailBackfillError("Runtime claim returned an invalid runtime epoch")
    data = thumbnail_path.read_bytes()
    digest = "sha256:" + hashlib.sha256(data).hexdigest()
    binding = {
        "project_id": project_id,
        "run_id": run_id,
        "task_id": task_id,
        "attempt_id": attempt_id,
        "executor_id": actor_id,
        "lease_id": lease_id,
        "fence": fence,
        "runtime_epoch": runtime_epoch,
        "output_key": THUMBNAIL_OUTPUT_NAME,
        "output_port": THUMBNAIL_OUTPUT_NAME,
        "filename": THUMBNAIL_OUTPUT_FILENAME,
        "digest": digest,
        "size": len(data),
        "media_type": "image/jpeg",
    }
    runtime.ingest_object(
        data,
        media_type="image/jpeg",
        filename=THUMBNAIL_OUTPUT_FILENAME,
        upload_binding=binding,
        idempotency_key=_output_idempotency_key(binding),
    )
    output = {
        "name": THUMBNAIL_OUTPUT_NAME,
        "kind": "object",
        "filename": THUMBNAIL_OUTPUT_FILENAME,
        "media_type": "image/jpeg",
        "digest": digest,
        "size": len(data),
        "output_port": THUMBNAIL_OUTPUT_NAME,
        "ordinal": 0,
        "role": "thumbnail",
        "durability": "durable",
        "provenance": {
            "thumbnail": {
                "source_object_ids": [source_object_id],
                "recipe_version": THUMBNAIL_RECIPE_VERSION,
            }
        },
    }
    return _plain(runtime.settle_attempt(
        attempt_id,
        {
            "attempt_id": attempt_id,
            "lease_id": lease_id,
            "fence": fence,
            "runtime_epoch": runtime_epoch,
            "outputs": [output],
            "effect": effect,
            "result": {
                "generation_id": effect.get("target_id"),
                "thumbnail_digest": digest,
            },
        },
        idempotency_key="thumbnail-backfill-settle-" + _stable_key(attempt_id, digest),
    ))


def _upload_and_settle(
    runtime: Any,
    *,
    project_id: str,
    generation: Mapping[str, Any],
    source_object_id: str,
    thumbnail_path: Path,
    actor_id: str,
    runtime_epoch: int,
    source_media_type: str,
) -> Mapping[str, Any]:
    generation_id = _required_string(generation.get("generation_id"), field="generation_id")
    expected_version = generation.get("version")
    if isinstance(expected_version, bool) or not isinstance(expected_version, int) or expected_version < 1:
        raise ThumbnailBackfillError(f"generation {generation_id} has an invalid version")
    effect = {
        "effect_type": "generation.thumbnail.attach",
        "target_id": generation_id,
        "expected_version": expected_version,
        "payload": {
            "source_object_id": source_object_id,
            "output_name": THUMBNAIL_OUTPUT_NAME,
            "output_ordinal": 0,
            "recipe_version": THUMBNAIL_RECIPE_VERSION,
        },
    }
    spec = {
        "operation": "generation.thumbnail.backfill",
        "generation_id": generation_id,
        "source_object_id": source_object_id,
        "source_media_type": source_media_type,
    }
    storage_estimate = {"scratch_bytes": 2 * 1024 * 1024, "output_bytes": 512 * 1024}
    admission_key = _admission_key(
        project_id=project_id,
        generation_id=generation_id,
        source_object_id=source_object_id,
        source_media_type=source_media_type,
        effect=effect,
        spec=spec,
        storage_estimate=storage_estimate,
    )
    admitted = _unwrap_mutation(
        runtime.admit_task(
            capability_id=THUMBNAIL_BACKFILL_CAPABILITY_ID,
            capability_digest=THUMBNAIL_BACKFILL_CAPABILITY_DIGEST,
            input_object_ids=[source_object_id],
            idempotency_key=admission_key,
            project_id=project_id,
            spec=spec,
            settlement_effect=effect,
            storage_estimate=storage_estimate,
        )
    )
    task = admitted.get("task") if isinstance(admitted.get("task"), Mapping) else admitted
    task_id = _required_string(task.get("task_id") or task.get("id"), field="task_id")
    _recover_failed_admission(
        runtime,
        task_id=task_id,
        admission_key=admission_key,
    )
    for _ in range(THUMBNAIL_BACKFILL_MAX_INTERLEAVED_CLAIMS):
        claim = _claim_task(runtime, actor_id=actor_id, epoch=runtime_epoch)
        if claim.get("task_id") == task_id:
            try:
                return _settle_claimed_task(
                    runtime,
                    claim=claim,
                    effect=_claim_source(runtime, claim)[1],
                    source_object_id=source_object_id,
                    thumbnail_path=thumbnail_path,
                    actor_id=actor_id,
                )
            except Exception as exc:
                # The requested task has the same lease obligations as an
                # interleaved task.  Upload and settlement failures must not
                # strand this attempt until the Runtime lease expires.
                _fail_claimed_attempt(
                    runtime,
                    claim,
                    runtime_epoch=runtime_epoch,
                    error=f"thumbnail task {task_id} could not be settled: {exc}",
                )
                raise

        # claim_task is intentionally queue-wide.  If an older backfill is
        # ahead of the task we just admitted, execute its immutable claim
        # before claiming again.  This keeps the lease fenced and prevents a
        # different task from being left running until lease expiry.
        foreign_id = claim.get("task_id")
        try:
            foreign_media_type, foreign_effect = _claim_source(runtime, claim)
            foreign_source_object_id = _source_id(
                {"object_id": foreign_effect["payload"]["source_object_id"]}
            )
            if foreign_source_object_id is None:
                raise ThumbnailBackfillError("claimed thumbnail source object is invalid")
            foreign_bytes = _plain(
                runtime.get_object(foreign_source_object_id)
            ).get("data")
            if not isinstance(foreign_bytes, bytes) or not foreign_bytes:
                raise ThumbnailBackfillError("claimed thumbnail source bytes are unavailable")
            if foreign_effect.get("target_id") == generation_id and not _same_effect(
                foreign_effect, effect
            ):
                _fail_claimed_attempt(
                    runtime,
                    claim,
                    runtime_epoch=runtime_epoch,
                    error="claimed thumbnail effect is stale for the requested generation",
                )
                continue
            if _same_effect(foreign_effect, effect):
                # A prior admission may have the same immutable proposal but
                # an older client-generated idempotency key.  Consume that
                # exact claim and remove the duplicate admission we just made.
                runtime.cancel_task(
                    task_id,
                    idempotency_key="thumbnail-backfill-cancel-"
                    + _stable_key(task_id, admission_key),
                )
                return _settle_claimed_task(
                    runtime,
                    claim=claim,
                    effect=foreign_effect,
                    source_object_id=source_object_id,
                    thumbnail_path=thumbnail_path,
                    actor_id=actor_id,
                )
            with tempfile.TemporaryDirectory(prefix="astrid-thumbnail-interleaved-") as workdir:
                root = Path(workdir)
                source_path = root / "source"
                foreign_thumbnail = root / THUMBNAIL_OUTPUT_FILENAME
                source_path.write_bytes(foreign_bytes)
                extract_thumbnail(source_path, foreign_thumbnail, foreign_media_type)
                _settle_claimed_task(
                    runtime,
                    claim=claim,
                    effect=foreign_effect,
                    source_object_id=foreign_source_object_id,
                    thumbnail_path=foreign_thumbnail,
                    actor_id=actor_id,
                )
        except Exception as exc:
            _fail_claimed_attempt(
                runtime,
                claim,
                runtime_epoch=runtime_epoch,
                error=f"interleaved task {foreign_id} could not be settled: {exc}",
            )
            raise ThumbnailBackfillError(
                f"claimed interleaved thumbnail task {foreign_id} was explicitly failed"
            ) from exc
    raise ThumbnailBackfillError(
        "Runtime queue did not reach the admitted thumbnail task within the claim bound"
    )


def run_thumbnail_backfill(
    runtime: Any,
    *,
    project: str,
    actor_id: str | None,
    limit: int = 50,
    generation_id: str | None = None,
    dry_run: bool = False,
) -> ThumbnailBackfillReport:
    """Backfill at most *limit* project Generations through Runtime custody."""

    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= THUMBNAIL_BACKFILL_MAX_LIMIT:
        raise ThumbnailBackfillError(
            f"limit must be an integer between 1 and {THUMBNAIL_BACKFILL_MAX_LIMIT}"
        )
    project_id = _project_id(runtime, str(project))
    if generation_id:
        generations = [_plain(runtime.get_generation(generation_id))]
        if generations[0].get("project_id") != project_id:
            raise ThumbnailBackfillError("generation does not belong to the selected project")
    else:
        generations = _paged(
            runtime.list_generations,
            project_id,
            limit=min(THUMBNAIL_BACKFILL_PAGE_SIZE, limit),
            operation="generation listing",
        )[:limit]

    object_rows: dict[str, Mapping[str, Any]] = {}
    all_generations: list[Mapping[str, Any]] = []
    for item in generations:
        if isinstance(item, Mapping):
            all_generations.append(item)
    diagnostics: list[dict[str, str]] = []
    scanned = already_ready = unsupported = unavailable = failed = attached = 0
    selected_ids: list[str] = []
    receipts: list[dict[str, Any]] = []

    for generation in all_generations:
        scanned += 1
        current_id = generation.get("generation_id")
        if not isinstance(current_id, str) or not current_id:
            failed += 1
            diagnostics.append({"generation_id": "", "reason": "missing_generation_id"})
            continue
        selected_ids.append(current_id)
        try:
            variants = _paged(
                runtime.list_variants,
                current_id,
                limit=THUMBNAIL_BACKFILL_PAGE_SIZE,
                operation="variant listing",
            )
            variants = [item for item in variants if isinstance(item, Mapping)]
            primary = _primary_variant(variants)
            if primary is None:
                unavailable += 1
                diagnostics.append({"generation_id": current_id, "reason": "no_primary_variant"})
                continue
            source_object_id = _source_id(primary)
            if source_object_id is None:
                unavailable += 1
                diagnostics.append({"generation_id": current_id, "reason": "primary_object_unavailable"})
                continue
            if _ready_thumbnail(generation, source_object_id):
                already_ready += 1
                continue
            media_type = _variant_media_type(primary, object_rows)
            if media_type is None:
                if not object_rows:
                    object_rows = {
                        str(row.get("object_id")): row
                        for row in _paged(
                            runtime.list_project_objects,
                            project_id,
                            limit=200,
                            operation="project object listing",
                        )
                        if isinstance(row, Mapping) and isinstance(row.get("object_id"), str)
                    }
                media_type = _variant_media_type(primary, object_rows)
            if not is_visual_media_type(media_type):
                unsupported += 1
                continue
            source_bytes = _plain(runtime.get_object(source_object_id)).get("data")
            if not isinstance(source_bytes, bytes) or not source_bytes:
                unavailable += 1
                diagnostics.append({"generation_id": current_id, "reason": "source_bytes_unavailable"})
                continue
            if dry_run:
                attached += 1
                continue
            with tempfile.TemporaryDirectory(prefix="astrid-thumbnail-backfill-") as workdir:
                root = Path(workdir)
                source_path = root / "source"
                thumbnail_path = root / THUMBNAIL_OUTPUT_FILENAME
                source_path.write_bytes(source_bytes)
                extract_thumbnail(source_path, thumbnail_path, media_type)
                current_generation = generation
                for version_retry in range(THUMBNAIL_BACKFILL_MAX_VERSION_RETRIES + 1):
                    epoch = int(_plain(runtime.health()).get("runtime_epoch", 0))
                    _register_worker(runtime, actor_id=actor_id or "", epoch=epoch)
                    try:
                        settlement = _upload_and_settle(
                            runtime,
                            project_id=project_id,
                            generation=current_generation,
                            source_object_id=source_object_id,
                            thumbnail_path=thumbnail_path,
                            actor_id=actor_id or "",
                            runtime_epoch=epoch,
                            source_media_type=media_type,
                        )
                    except Exception as exc:
                        if not _is_version_conflict(exc) or version_retry >= THUMBNAIL_BACKFILL_MAX_VERSION_RETRIES:
                            raise
                        reread, reread_source_object_id = _reread_generation_primary(
                            runtime, current_id
                        )
                        if reread.get("project_id") != project_id:
                            raise ThumbnailBackfillError(
                                "generation moved outside the selected project during version recovery"
                            ) from exc
                        if reread_source_object_id != source_object_id:
                            raise ThumbnailBackfillError(
                                "generation primary changed during version recovery"
                            ) from exc
                        if _ready_thumbnail(reread, source_object_id):
                            already_ready += 1
                            break
                        current_generation = reread
                        continue
                    if isinstance(settlement, Mapping) and isinstance(settlement.get("receipt"), Mapping):
                        receipts.append(dict(settlement["receipt"]))
                    attached += 1
                    break
        except ThumbnailError as exc:
            failed += 1
            diagnostics.append({"generation_id": current_id, "reason": str(exc)[:240]})
        except Exception as exc:  # noqa: BLE001 - keep one generation failure bounded.
            failed += 1
            diagnostics.append({"generation_id": current_id, "reason": str(exc)[:240]})

    return ThumbnailBackfillReport(
        project_id=project_id,
        scanned=scanned,
        attached=attached,
        already_ready=already_ready,
        unsupported=unsupported,
        unavailable=unavailable,
        failed=failed,
        dry_run=dry_run,
        generation_ids=tuple(selected_ids),
        diagnostics=tuple(diagnostics),
        receipts=tuple(receipts),
    )


def run_variant_thumbnail_backfill(
    runtime: Any,
    *,
    project: str,
    limit: int = 100,
    generation_id: str | None = None,
    dry_run: bool = False,
) -> VariantThumbnailBackfillReport:
    """Backfill source-correct posters directly through Runtime-owned APIs.

    This route is intentionally separate from the legacy generation-level
    thumbnail task. It writes no SQLite itself: bytes are ingested through the
    Runtime, then attached with a source-conditional Runtime mutation.
    """
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 500:
        raise ThumbnailBackfillError("limit must be an integer between 1 and 500")
    project_id = _project_id(runtime, str(project))
    if generation_id:
        generations = [_plain(runtime.get_generation(generation_id))]
        if generations[0].get("project_id") != project_id:
            raise ThumbnailBackfillError("generation does not belong to the selected project")
    else:
        generations = _paged(
            runtime.list_generations, project_id,
            limit=min(THUMBNAIL_BACKFILL_PAGE_SIZE, limit),
            operation="generation listing",
        )[:limit]

    object_rows = {
        str(row.get("object_id")): row
        for row in _paged(
            runtime.list_project_objects, project_id,
            limit=200,
            operation="project object listing",
        )
        if isinstance(row, Mapping) and isinstance(row.get("object_id"), str)
    }
    scanned = attached = already_ready = unsupported = unavailable = failed = 0
    variant_ids: list[str] = []
    diagnostics: list[dict[str, str]] = []
    receipts: list[dict[str, Any]] = []
    for generation in generations:
        if not isinstance(generation, Mapping):
            continue
        current_id = generation.get("generation_id")
        if not isinstance(current_id, str) or not current_id:
            continue
        variants = [
            item for item in _paged(
                runtime.list_variants, current_id,
                limit=THUMBNAIL_BACKFILL_PAGE_SIZE,
                operation="variant listing",
            ) if isinstance(item, Mapping)
        ]
        for variant in variants:
            variant_id = variant.get("variant_id")
            if not isinstance(variant_id, str) or not variant_id:
                failed += 1
                diagnostics.append({"variant_id": "", "reason": "missing_variant_id"})
                continue
            scanned += 1
            variant_ids.append(variant_id)
            if _ready_variant_thumbnail(variant):
                already_ready += 1
                continue
            source_object_id = _source_id(variant)
            media_type = _variant_media_type(variant, object_rows)
            if source_object_id is None:
                unavailable += 1
                diagnostics.append({"variant_id": variant_id, "reason": "source_object_unavailable"})
                continue
            if not is_visual_media_type(media_type):
                unsupported += 1
                continue
            if dry_run:
                attached += 1
                continue
            try:
                source_response = _plain(runtime.get_object(source_object_id))
                source_bytes = source_response.get("data") if isinstance(source_response, Mapping) else None
                if not isinstance(source_bytes, bytes) or not source_bytes:
                    unavailable += 1
                    diagnostics.append({"variant_id": variant_id, "reason": "source_bytes_unavailable"})
                    continue
                with tempfile.TemporaryDirectory(prefix="astrid-variant-thumbnail-backfill-") as workdir:
                    root = Path(workdir)
                    source_path = root / "source"
                    thumbnail_path = root / THUMBNAIL_OUTPUT_FILENAME
                    source_path.write_bytes(source_bytes)
                    extract_thumbnail(source_path, thumbnail_path, media_type)
                    thumbnail_bytes = thumbnail_path.read_bytes()
                uploaded = _unwrap_mutation(
                    runtime.ingest_project_object(
                        project_id, thumbnail_bytes, media_type="image/jpeg",
                        filename=f"thumbnail-{variant_id}.jpg",
                        idempotency_key="variant-thumbnail-upload-" + _stable_key(variant_id, source_object_id),
                    )
                )
                thumbnail_object_id = uploaded.get("object_id")
                if not isinstance(thumbnail_object_id, str):
                    raise ThumbnailBackfillError("Runtime thumbnail upload returned no object_id")
                attached_response = _plain(
                    runtime.attach_variant_thumbnail(
                        variant_id,
                        thumbnail_object_id=thumbnail_object_id,
                        source_object_id=source_object_id,
                        recipe_version=THUMBNAIL_RECIPE_VERSION,
                        idempotency_key="variant-thumbnail-attach-" + _stable_key(variant_id, source_object_id),
                    )
                )
                receipt = attached_response.get("receipt") if isinstance(attached_response, Mapping) else None
                if isinstance(receipt, Mapping):
                    receipts.append(dict(receipt))
                attached += 1
            except ThumbnailError as exc:
                failed += 1
                diagnostics.append({"variant_id": variant_id, "reason": str(exc)[:240]})
            except Exception as exc:  # noqa: BLE001 - keep other variants bounded.
                failed += 1
                diagnostics.append({"variant_id": variant_id, "reason": str(exc)[:240]})
    return VariantThumbnailBackfillReport(
        project_id=project_id,
        scanned=scanned,
        attached=attached,
        already_ready=already_ready,
        unsupported=unsupported,
        unavailable=unavailable,
        failed=failed,
        dry_run=dry_run,
        variant_ids=tuple(variant_ids),
        diagnostics=tuple(diagnostics),
        receipts=tuple(receipts),
    )


def _timeline_closure(runtime: Any, project_id: str, timeline_ref: str) -> tuple[str, str, Mapping[str, Any]]:
    """Read one exact parent/shot/internal-timeline closure from Runtime."""
    timelines = _paged(
        runtime.list_timelines, project_id, limit=THUMBNAIL_BACKFILL_PAGE_SIZE,
        operation="timeline listing",
    )
    match = next((row for row in timelines if isinstance(row, Mapping) and timeline_ref in {
        str(row.get("timeline_id", "")), str(row.get("slug", "")),
    }), None)
    if match is None:
        raise ThumbnailBackfillError(f"canonical timeline {timeline_ref!r} was not found")
    timeline_id = match.get("timeline_id") or match.get("id")
    head = match.get("head_revision_id") or match.get("parent_revision_id")
    if not isinstance(timeline_id, str) or not timeline_id:
        raise ThumbnailBackfillError("Runtime timeline listing omitted timeline_id")
    if not isinstance(head, str) or not head:
        raise ThumbnailBackfillError("Runtime timeline listing omitted its canonical head revision")
    parent = _plain(runtime.get_project_parent_composition_revision(project_id, timeline_id, head))
    if isinstance(parent, Mapping) and isinstance(parent.get("data"), Mapping):
        parent = parent["data"]
    if not isinstance(parent, Mapping) or parent.get("revision_id") != head:
        raise ThumbnailBackfillError("Runtime did not return the requested pinned parent revision")
    payload = parent.get("payload")
    occurrences = payload.get("occurrences") if isinstance(payload, Mapping) else None
    if not isinstance(occurrences, list):
        raise ThumbnailBackfillError("pinned parent revision has no occurrence list")
    shot_rows: dict[str, Mapping[str, Any]] = {}
    timeline_rows: dict[str, Mapping[str, Any]] = {}
    for occurrence in occurrences:
        if not isinstance(occurrence, Mapping):
            continue
        shot_id = occurrence.get("shot_id")
        shot_revision_id = occurrence.get("shot_revision_id") or occurrence.get("revision_id")
        if not isinstance(shot_id, str) or not isinstance(shot_revision_id, str):
            raise ThumbnailBackfillError("pinned occurrence omits its shot revision identity")
        if shot_revision_id not in shot_rows:
            shot = _plain(runtime.get_project_shot_revision(project_id, shot_id, shot_revision_id))
            if isinstance(shot, Mapping) and isinstance(shot.get("data"), Mapping):
                shot = shot["data"]
            if not isinstance(shot, Mapping) or shot.get("revision_id") != shot_revision_id:
                raise ThumbnailBackfillError(f"Runtime did not return pinned shot revision {shot_revision_id}")
            shot_rows[shot_revision_id] = shot
        shot = shot_rows[shot_revision_id]
        shot_payload = shot.get("payload")
        internal_id = shot.get("internal_timeline_revision_id")
        if not isinstance(internal_id, str) and isinstance(shot_payload, Mapping):
            internal_id = shot_payload.get("internal_timeline_revision_id")
        if not isinstance(internal_id, str):
            raise ThumbnailBackfillError(f"shot revision {shot_revision_id} omits its internal timeline revision")
        if internal_id not in timeline_rows:
            internal = _plain(runtime.get_project_timeline_revision(project_id, timeline_id, internal_id))
            if isinstance(internal, Mapping) and isinstance(internal.get("data"), Mapping):
                internal = internal["data"]
            if not isinstance(internal, Mapping) or internal.get("revision_id") != internal_id:
                raise ThumbnailBackfillError(f"Runtime did not return pinned internal revision {internal_id}")
            timeline_rows[internal_id] = internal
    # The existing authoring-bundle reader validates the immutable closure and
    # provides the exact authored clip/registry shapes used by timeline tools.
    candidate = open_authoring_bundle(
        parent,
        shot_revisions=list(shot_rows.values()),
        internal_timeline_revisions=list(timeline_rows.values()),
    )
    return timeline_id, head, candidate


def _timeline_thumbnail_requests(candidate: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Select a deterministic primary-track source frame for each placement."""
    requests: list[dict[str, Any]] = []
    placements = candidate.get("placements")
    shots = candidate.get("shots")
    if not isinstance(placements, list) or not isinstance(shots, Mapping):
        raise ThumbnailBackfillError("canonical authoring read model has invalid placements or shots")
    for placement in placements:
        if not isinstance(placement, Mapping):
            continue
        occurrence_id = str(placement.get("occurrence_id", ""))
        shot_id = placement.get("shot_id")
        shot = shots.get(shot_id) if isinstance(shot_id, str) else None
        internal = shot.get("internal_timeline") if isinstance(shot, Mapping) else None
        if not isinstance(internal, Mapping):
            requests.append({"occurrence_id": occurrence_id, "reason": "internal_timeline_unavailable"})
            continue
        tracks = internal.get("tracks")
        clips = internal.get("clips")
        registry = internal.get("registry")
        assets = registry.get("assets") if isinstance(registry, Mapping) else None
        visual_ids = [
            row.get("id") for row in tracks if isinstance(row, Mapping)
            and row.get("kind") == "visual" and isinstance(row.get("id"), str)
        ] if isinstance(tracks, list) else []
        if not visual_ids:
            requests.append({"occurrence_id": occurrence_id, "reason": "no_visual_track"})
            continue
        duration_ms = placement.get("duration_ms")
        if isinstance(duration_ms, bool) or not isinstance(duration_ms, (int, float)) or duration_ms < 0:
            requests.append({"occurrence_id": occurrence_id, "reason": "invalid_shot_duration"})
            continue
        midpoint = float(duration_ms) / 2000.0
        primary_track = visual_ids[0]
        candidates: list[dict[str, Any]] = []
        for clip in clips if isinstance(clips, list) else []:
            if not isinstance(clip, Mapping) or clip.get("track") != primary_track:
                continue
            if clip.get("active") is False or clip.get("disabled") is True:
                continue
            clip_type = clip.get("clipType", clip.get("clip_type", "media"))
            if clip_type not in {"media", "video", "image"}:
                continue
            asset_id = clip.get("asset", clip.get("asset_id"))
            asset = assets.get(asset_id) if isinstance(assets, Mapping) and isinstance(asset_id, str) else None
            if not isinstance(asset, Mapping):
                continue
            source_id = asset.get("media_id") or asset.get("object_id")
            if not isinstance(source_id, str) or not source_id.startswith("sha256:") or len(source_id) != 71:
                continue
            try:
                normalized_clip = dict(clip)
                for source, target in (("at_ms", "at"), ("from_ms", "from"), ("to_ms", "to")):
                    if source in normalized_clip and target not in normalized_clip:
                        normalized_clip[target] = float(normalized_clip[source]) / 1000.0
                if "duration_ms" in normalized_clip and "hold" not in normalized_clip and "to" not in normalized_clip:
                    normalized_clip["hold"] = float(normalized_clip["duration_ms"]) / 1000.0
                start = float(normalized_clip.get("at", 0))
                duration = clip_timeline_duration(normalized_clip)
            except (TypeError, ValueError):
                continue
            if not math.isfinite(start) or not math.isfinite(duration) or duration <= 0:
                continue
            candidates.append({"clip": normalized_clip, "asset": asset, "source_object_id": source_id,
                               "start": start, "end": start + duration,
                               "center": start + duration / 2})
        if not candidates:
            requests.append({"occurrence_id": occurrence_id, "reason": "no_visual_asset_on_primary_track"})
            continue
        covering = [row for row in candidates if row["start"] <= midpoint < row["end"]]
        selected = min(covering, key=lambda row: (row["start"], row["center"])) if covering else min(
            candidates, key=lambda row: (abs(row["center"] - midpoint), row["center"])
        )
        clip = selected["clip"]
        asset = selected["asset"]
        raw_type = asset.get("media_type") or asset.get("type")
        media_type = str(raw_type or "").lower()
        if media_type in {"image", "video", "audio"}:
            media_type += "/unknown"
        if not is_visual_media_type(media_type):
            requests.append({"occurrence_id": occurrence_id, "reason": "non_visual_asset"})
            continue
        source_from = float(clip.get("from", 0) or 0)
        speed = float(clip.get("speed", 1) or 1)
        source_time = source_from if media_type.startswith("video/") else 0.0
        if media_type.startswith("video/"):
            selected_local_time = min(max(midpoint, selected["start"]), selected["end"])
            source_time += max(0.0, selected_local_time - selected["start"]) * speed
        if not math.isfinite(source_time) or source_time < 0 or source_time > 4e9:
            requests.append({"occurrence_id": occurrence_id, "reason": "invalid_source_time"})
            continue
        source_time = round(source_time, 6)
        requests.append({
            "occurrence_id": occurrence_id,
            "shot_id": str(placement.get("shot_id", "")),
            "clip_id": str(clip.get("id", "")),
            "source_object_id": selected["source_object_id"],
            "source_time_seconds": source_time,
            "media_type": media_type,
        })
    return requests


def run_timeline_thumbnail_backfill(
    runtime: Any,
    *,
    project: str,
    timeline: str,
    limit: int = 100,
    dry_run: bool = False,
) -> TimelineThumbnailBackfillReport:
    """Ensure source-frame thumbnails for visual clips at one pinned timeline head."""
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= THUMBNAIL_BACKFILL_MAX_LIMIT:
        raise ThumbnailBackfillError(f"limit must be an integer between 1 and {THUMBNAIL_BACKFILL_MAX_LIMIT}")
    project_id = _project_id(runtime, project)
    timeline_id, head, candidate = _timeline_closure(runtime, project_id, timeline)
    requests = _timeline_thumbnail_requests(candidate)
    eligible = [row for row in requests if "source_object_id" in row]
    if len(eligible) > limit:
        eligible = eligible[:limit]
    # Identical source/time/recipe requests share one lookup, fetch, upload and association.
    deduped: dict[tuple[str, float], dict[str, Any]] = {}
    for item in eligible:
        deduped.setdefault((item["source_object_id"], item["source_time_seconds"]), item)
    ready = attached = unavailable = failed = unsupported = source_fetches = 0
    diagnostics: list[dict[str, str]] = []
    source_cache: dict[str, tuple[bytes, str]] = {}
    source_failures: dict[str, str] = {}
    for item in deduped.values():
        source_id = item["source_object_id"]
        source_time = item["source_time_seconds"]
        try:
            existing = runtime.get_source_frame_thumbnail(
                project_id, source_id, source_time, recipe_version=THUMBNAIL_RECIPE_VERSION
            )
            existing = _plain(existing)
            if isinstance(existing, Mapping) and isinstance(existing.get("data"), Mapping):
                existing = existing["data"]
            if isinstance(existing, Mapping) and existing.get("object_id"):
                ready += 1
                continue
        except Exception as exc:  # noqa: BLE001 - Runtime reports a missing relation as an HTTP error.
            if getattr(exc, "code", None) not in {"not_found", "404"} and getattr(exc, "status", None) != 404:
                failed += 1
                diagnostics.append({"occurrence_id": item["occurrence_id"], "reason": f"thumbnail_lookup_failed: {exc}"[:240]})
                continue
        if dry_run:
            attached += 1
            continue
        try:
            if source_id in source_failures:
                unavailable += 1
                diagnostics.append({"occurrence_id": item["occurrence_id"], "reason": source_failures[source_id]})
                continue
            if source_id not in source_cache:
                try:
                    response = _plain(runtime.get_object(source_id))
                except Exception as exc:  # noqa: BLE001 - Runtime transport failures are reported per source.
                    source_failures[source_id] = f"source_read_failed: {exc}"[:240]
                    unavailable += 1
                    diagnostics.append({"occurrence_id": item["occurrence_id"], "reason": source_failures[source_id]})
                    continue
                data = response.get("data") if isinstance(response, Mapping) else None
                if not isinstance(data, bytes) or not data:
                    source_failures[source_id] = "source_bytes_unavailable"
                    unavailable += 1
                    diagnostics.append({"occurrence_id": item["occurrence_id"], "reason": source_failures[source_id]})
                    continue
                media_type = item["media_type"]
                source_cache[source_id] = (data, media_type)
                source_fetches += 1
            source_bytes, media_type = source_cache[source_id]
            with tempfile.TemporaryDirectory(prefix="astrid-timeline-thumbnail-") as workdir:
                source_path = Path(workdir) / "source"
                thumbnail_path = Path(workdir) / THUMBNAIL_OUTPUT_FILENAME
                source_path.write_bytes(source_bytes)
                extract_thumbnail(source_path, thumbnail_path, media_type, source_time_seconds=source_time)
                thumbnail_bytes = thumbnail_path.read_bytes()
            identity = _stable_key(project_id, source_id, f"{source_time:.6f}", str(THUMBNAIL_RECIPE_VERSION))
            uploaded = _unwrap_mutation(runtime.ingest_project_object(
                project_id, thumbnail_bytes, media_type="image/jpeg", filename=f"thumbnail-{identity[:16]}.jpg",
                idempotency_key="source-frame-thumbnail-upload-" + identity,
            ))
            thumbnail_object_id = uploaded.get("object_id")
            if not isinstance(thumbnail_object_id, str):
                raise ThumbnailBackfillError("Runtime thumbnail ingest returned no object_id")
            descriptor = {
                "object_id": thumbnail_object_id,
                "source_object_id": source_id,
                "recipe_version": THUMBNAIL_RECIPE_VERSION,
                "selection": {"kind": "source_frame", "source_time_seconds": source_time},
            }
            result = _plain(runtime.ensure_source_frame_thumbnail(
                project_id, descriptor, idempotency_key="source-frame-thumbnail-ensure-" + identity,
            ))
            if isinstance(result, Mapping) and isinstance(result.get("receipt"), Mapping):
                pass
            attached += 1
        except ThumbnailError as exc:
            failed += 1
            diagnostics.append({"occurrence_id": item["occurrence_id"], "reason": str(exc)[:240]})
        except Exception as exc:  # noqa: BLE001 - report one failed thumbnail and continue.
            failed += 1
            diagnostics.append({"occurrence_id": item["occurrence_id"], "reason": str(exc)[:240]})
    excluded = [row for row in requests if "source_object_id" not in row]
    unsupported = sum(row.get("reason") == "non_visual_asset" for row in excluded)
    unavailable += sum(row.get("reason") in {"internal_timeline_unavailable", "no_visual_track", "no_visual_asset_on_primary_track"} for row in excluded)
    for row in excluded:
        diagnostics.append({"occurrence_id": row.get("occurrence_id", ""), "reason": str(row.get("reason", "no_visual_asset"))})
    return TimelineThumbnailBackfillReport(
        project_id=project_id, timeline_id=timeline_id, head_revision_id=head,
        scanned=len(requests), attached=attached, already_ready=ready, unsupported=unsupported,
        unavailable=unavailable, failed=failed, source_fetches=source_fetches, dry_run=dry_run,
        requests=tuple(dict(row) for row in requests), diagnostics=tuple(diagnostics),
    )


__all__ = [
    "THUMBNAIL_BACKFILL_CAPABILITY_DIGEST",
    "THUMBNAIL_BACKFILL_CAPABILITY_ID",
    "THUMBNAIL_BACKFILL_MAX_LIMIT",
    "ThumbnailBackfillError",
    "ThumbnailBackfillReport",
    "TimelineThumbnailBackfillReport",
    "ensure_imported_source_thumbnail",
    "VariantThumbnailBackfillReport",
    "run_thumbnail_backfill",
    "run_timeline_thumbnail_backfill",
    "run_variant_thumbnail_backfill",
]
