"""Versioned adapters for the public timeline-eval worker result contract.

The worker result is deliberately a small, case-owned JSON envelope.  This
module keeps the accepted projection paths explicit for cases whose useful
observations have equivalent representations (L02 identity/media projection
and L06 diagnostic projection).  It never guesses between arbitrary aliases:
conflicting values are a contract error.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


RESULT_ADAPTER_KIND = "astrid.timeline-eval.result-adapter.v1"
RESULT_SCHEMA = "astrid.timeline-eval.worker-result.v1"
OUTCOME_RECORD_KIND = "astrid.timeline-eval.outcome-record.v1"


class ResultContractError(ValueError):
    """The worker result conflicts with the versioned public contract."""


@dataclass(frozen=True)
class ArtifactSpec:
    role: str
    path: str
    owner: str
    format: str = "json"
    required: bool = True

    def as_dict(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "path": self.path,
            "owner": self.owner,
            "format": self.format,
            "required": self.required,
        }


def _artifact_specs(case: Mapping[str, Any]) -> tuple[ArtifactSpec, ...]:
    case_id = str(case.get("id", ""))
    required = [str(value) for value in case.get("required_artifacts", ())]
    role_by_path = {
        "brief.json": "public_brief",
        "target.json": "prepared_target",
        "before.json": "coordinator_before_readback",
        "after.json": "coordinator_after_readback",
        "result.json": "worker_result",
        "trace.jsonl": "worker_trace",
        "evidence/identity-map.json": "identity_projection",
        "evidence/diagnostic.json": "invalid_candidate_diagnostic",
    }
    specs: list[ArtifactSpec] = []
    for path in required:
        role = role_by_path.get(path, f"declared:{path}")
        owner = "coordinator" if role in {
            "public_brief", "prepared_target", "coordinator_before_readback",
            "coordinator_after_readback",
        } else "worker"
        specs.append(ArtifactSpec(role=role, path=path, owner=owner))
    # The coordinator sidecar is never a worker-owned artifact and is not a
    # required public output.  Declaring it here makes provenance explicit.
    if case_id in {"L02", "L06"}:
        specs.append(ArtifactSpec(
            role="coordinator_readback", path=f"coordinator/cases/{case_id}/readback.json",
            owner="coordinator", required=False,
        ))
    return tuple(specs)


def public_result_contract(case: Mapping[str, Any]) -> dict[str, Any]:
    """Return the worker-visible, versioned result/artifact contract."""
    specs = _artifact_specs(case)
    return {
        "kind": RESULT_ADAPTER_KIND,
        "schema": RESULT_SCHEMA,
        "case_id": str(case.get("id", "")),
        # Worker narration is useful when present, but final-state evidence is
        # coordinator-owned and does not depend on this file being written.
        "result": {"path": "result.json", "owner": "worker", "format": "json", "required": False},
        "artifacts": [spec.as_dict() for spec in specs],
        "ownership": {
            "worker_may_write": [spec.path for spec in specs if spec.owner == "worker"],
            "coordinator_only": [spec.path for spec in specs if spec.owner == "coordinator"],
            "worker_read_only": [spec.path for spec in specs if spec.owner == "coordinator"],
        },
        "status_fields": {
            "agent": ["agent_status", "status"],
            "launcher": ["launcher_process_status", "execution_status"],
            "conflict_policy": "agent_status and status must agree when both are present",
        },
    }


def _conflicting_values(values: list[tuple[str, Any]]) -> str | None:
    concrete = [(key, value) for key, value in values if value not in (None, "")]
    if len({repr(value) for _, value in concrete}) > 1:
        return ", ".join(f"{key}={value!r}" for key, value in concrete)
    return None


def _agent_status(raw: Mapping[str, Any]) -> str | None:
    conflict = _conflicting_values([
        ("agent_status", raw.get("agent_status")),
        ("status", raw.get("status")),
    ])
    if conflict:
        raise ResultContractError(f"conflicting agent terminal statuses: {conflict}")
    for key in ("agent_status", "status"):
        value = raw.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip().lower()
    return None


def _selected_media_id(record: Mapping[str, Any]) -> str | None:
    direct = record.get("selected_image_media_id")
    labelled: list[Any] = []
    handles = record.get("media_handles")
    if isinstance(handles, list):
        for handle in handles:
            if isinstance(handle, Mapping) and handle.get("role") == "selected_image":
                labelled.append(handle.get("media_id"))
    labelled_values = {value for value in labelled if value not in (None, "")}
    if direct not in (None, "") and labelled_values and direct not in labelled_values:
        raise ResultContractError(
            "L02 selected media conflict: selected_image_media_id disagrees with "
            "media_handles[role=selected_image].media_id"
        )
    if len(labelled_values) > 1:
        raise ResultContractError("L02 selected media conflict: multiple labelled selected-image IDs")
    return direct if direct not in (None, "") else next(iter(labelled_values), None)


def _adapt_l02(raw: Mapping[str, Any]) -> dict[str, Any]:
    observations = raw.get("observations")
    if observations is None:
        return {"expanded_occurrences": None}
    if not isinstance(observations, Mapping):
        raise ResultContractError("L02 observations must be an object")
    rows = observations.get("expanded_occurrences")
    if rows is None:
        return {"expanded_occurrences": None}
    if not isinstance(rows, list):
        raise ResultContractError("L02 observations.expanded_occurrences must be an array")
    normalized: list[dict[str, Any]] = []
    required = ("occurrence_id", "shot_id", "shot_revision_id", "internal_timeline_revision_id")
    for index, row in enumerate(rows):
        if not isinstance(row, Mapping):
            raise ResultContractError(f"L02 expanded occurrence {index} must be an object")
        missing = [key for key in required if not row.get(key)]
        if missing:
            raise ResultContractError(f"L02 expanded occurrence {index} missing: {', '.join(missing)}")
        item = dict(row)
        media_id = _selected_media_id(row)
        if media_id is not None:
            item["selected_image_media_id"] = media_id
        normalized.append(item)
    return {"expanded_occurrences": normalized}


def _adapt_l06(raw: Mapping[str, Any]) -> dict[str, Any]:
    observations = raw.get("observations")
    if observations is None:
        return {"diagnostic": None}
    if not isinstance(observations, Mapping):
        raise ResultContractError("L06 observations must be an object")
    nested = observations.get("diagnostic")
    if nested is not None and not isinstance(nested, Mapping):
        raise ResultContractError("L06 observations.diagnostic must be an object")
    flat_keys = {
        "status": "candidate_status",
        "error_type": "candidate_error_type",
        "base_parent_revision_id": "base_parent_revision_id",
    }
    flat = {
        normalized: observations.get(source)
        for normalized, source in flat_keys.items()
        if observations.get(source) not in (None, "")
    }
    if nested is None and not flat:
        return {"diagnostic": None}
    diagnostic = dict(nested or {})
    for key, value in flat.items():
        if diagnostic.get(key) not in (None, "") and diagnostic[key] != value:
            raise ResultContractError(
                f"L06 diagnostic conflict for {key}: nested and flat projections disagree"
            )
        diagnostic.setdefault(key, value)
    required = ("status", "error_type", "base_parent_revision_id")
    missing = [key for key in required if not diagnostic.get(key)]
    if missing:
        raise ResultContractError(f"L06 diagnostic missing: {', '.join(missing)}")
    return {"diagnostic": dict(diagnostic)}


def _adapt_l08(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Expose an exact ordered text projection while retaining segment data."""
    observations = raw.get("observations")
    if observations is None:
        return {"available_segment_titles": None}
    if not isinstance(observations, Mapping):
        raise ResultContractError("L08 observations must be an object")
    segments = observations.get("segments")
    titles = observations.get("available_segment_titles")
    if segments is not None:
        if not isinstance(segments, list):
            raise ResultContractError("L08 observations.segments must be an array")
        projected: list[str] = []
        for index, segment in enumerate(segments):
            if not isinstance(segment, Mapping) or not isinstance(segment.get("text"), str):
                raise ResultContractError(f"L08 segment {index} must be an object with string text")
            projected.append(segment["text"])
        if titles is not None and titles != projected:
            raise ResultContractError(
                "L08 segment conflict: available_segment_titles disagrees with ordered segments.text"
            )
        return {"available_segment_titles": projected}
    if titles is not None:
        if not isinstance(titles, list) or any(not isinstance(title, str) for title in titles):
            raise ResultContractError("L08 available_segment_titles must be an array of strings")
        return {"available_segment_titles": list(titles)}
    return {"available_segment_titles": None}


def _validate_artifact_ownership(case: Mapping[str, Any], raw: Mapping[str, Any]) -> None:
    """Reject an optional worker declaration that contradicts the contract."""
    declared = raw.get("artifact_ownership")
    if declared is None:
        return
    if not isinstance(declared, Mapping):
        raise ResultContractError("artifact_ownership must be an object when present")
    expected = {
        spec.path: spec.owner for spec in _artifact_specs(case)
    }
    for path, owner in declared.items():
        if path not in expected:
            raise ResultContractError(f"artifact ownership declares unknown path: {path}")
        if owner != expected[path]:
            raise ResultContractError(
                f"artifact ownership conflict for {path}: {owner!r} != {expected[path]!r}"
            )


def artifact_owner(case: Mapping[str, Any], path: str) -> str:
    """Return the declared owner for a case artifact path."""
    for spec in _artifact_specs(case):
        if spec.path == path:
            return spec.owner
    if path in {"before.json", "after.json", "target.json", "brief.json"}:
        return "coordinator"
    return "worker"


def adapt_worker_result(case: Mapping[str, Any], raw: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and normalize one result without mutating the raw output."""
    if not isinstance(raw, Mapping):
        raise ResultContractError("worker result must be a JSON object")
    case_id = str(case.get("id", ""))
    status = _agent_status(raw)
    _validate_artifact_ownership(case, raw)
    if case_id == "L02":
        observations = _adapt_l02(raw)
    elif case_id == "L06":
        observations = _adapt_l06(raw)
    elif case_id == "L08":
        observations = _adapt_l08(raw)
    else:
        observations = {}
    return {
        "kind": RESULT_ADAPTER_KIND,
        "schema": RESULT_SCHEMA,
        "case_id": case_id,
        "agent_status": status,
        "observations": observations,
        "raw_preserved": True,
    }


def worker_protocol_record(
    case: Mapping[str, Any], raw: Any, *, parse_error: str | None = None,
) -> dict[str, Any]:
    """Capture worker JSON/protocol health without grading the user request.

    A malformed or conflicting worker envelope is useful evidence, but it is
    not a semantic verdict. The original value is represented as preserved
    evidence and the coordinator can still judge any independently captured
    before/after/readback artifacts.
    """
    errors: list[str] = []
    normalized: Mapping[str, Any] | None = None
    if parse_error:
        errors.append(str(parse_error))
    elif not isinstance(raw, Mapping):
        errors.append("worker result is not a JSON object")
    else:
        try:
            normalized = adapt_worker_result(case, raw)
        except ResultContractError as exc:
            errors.append(str(exc))
    return {
        "schema": RESULT_SCHEMA,
        "valid": not errors,
        "errors": errors,
        "raw_preserved": True,
        "normalized": dict(normalized) if normalized is not None else None,
    }


def build_outcome_record(
    case: Mapping[str, Any], *, worker_protocol: Mapping[str, Any],
    conclusion: Any = None, independent_before: Any = None,
    independent_after: Any = None, independent_readback: Any = None,
    render_artifacts: Any = None, playback_artifacts: Any = None,
    execution: str | None = None,
) -> dict[str, Any]:
    """Build a bounded coordinator assessment from captured end-state evidence.

    This does not claim that a changed timeline satisfies the requested edit:
    case-specific semantic/output checks are still needed for that. It can
    establish a failed no-op action, or preserve an explicit evidence gap, and
    keeps execution state (including timeout) separate from semantic state.
    Worker JSON validity never grades the task.
    """
    assessment = assess_coordinator_outcome(
        case, independent_before=independent_before,
        independent_after=independent_after,
        independent_readback=independent_readback,
        conclusion=conclusion,
    )
    return {
        "kind": OUTCOME_RECORD_KIND,
        "schema": "astrid.timeline-eval.outcome-evidence.v1",
        "case_id": str(case.get("id", "")),
        "semantic_outcome": assessment,
        "execution": execution,
        "worker_protocol": dict(worker_protocol),
        "conclusion": conclusion,
        "independent_evidence": {
            "before": independent_before,
            "after": independent_after,
            "readback": independent_readback,
        },
        "render_artifacts": render_artifacts,
        "playback_artifacts": playback_artifacts,
    }


def assess_coordinator_outcome(
    case: Mapping[str, Any], *, independent_before: Any = None,
    independent_after: Any = None, independent_readback: Any = None,
    conclusion: Any = None,
) -> dict[str, Any]:
    """Make only the end-state determination supported by captured evidence.

    A committed action that leaves the prepared semantic state unchanged is a
    bounded failure. A changed state is not enough to prove that the requested
    edit was correct, so it remains undetermined pending a case checker.
    Navigation answers likewise need a case-specific oracle. Missing or
    malformed independent evidence always remains undetermined.
    """
    base = {"judge": "coordinator", "final_text_present": False}
    if isinstance(conclusion, Mapping):
        base["final_text_present"] = bool(
            isinstance(conclusion.get("final_text"), str)
            and conclusion["final_text"].strip()
        )
    before = independent_before if isinstance(independent_before, Mapping) else None
    after = independent_after if isinstance(independent_after, Mapping) else None
    readback = independent_readback if isinstance(independent_readback, Mapping) else None
    if before is None or after is None or readback is None:
        return {**base, "status": "undetermined", "reason": "required independent before/after/readback evidence is missing"}
    if readback.get("before_observed") is not True or readback.get("after_observed") is not True:
        return {**base, "status": "undetermined", "reason": "independent before/after capture is incomplete"}
    before_digest, after_digest = before.get("semantic_digest"), after.get("semantic_digest")
    before_head, after_head = before.get("head_revision_id"), after.get("head_revision_id")
    if not all(isinstance(value, str) and value for value in (before_digest, after_digest, before_head, after_head)):
        return {**base, "status": "undetermined", "reason": "independent snapshots lack semantic digest or revision identity"}
    if str(case.get("kind", "")).lower() != "action":
        return {**base, "status": "undetermined", "reason": "navigation correctness requires a case-specific answer check"}
    changed = before_digest != after_digest or before_head != after_head
    if not changed:
        return {
            **base, "status": "failed",
            "reason": "requested action left the independently observed timeline state unchanged",
            "evidence": {"before_head": before_head, "after_head": after_head,
                         "before_digest": before_digest, "after_digest": after_digest},
        }
    return {
        **base, "status": "undetermined",
        "reason": "timeline state changed, but no case-specific semantic checker established correctness",
        "evidence": {"before_head": before_head, "after_head": after_head,
                     "before_digest": before_digest, "after_digest": after_digest},
    }


__all__ = [
    "ArtifactSpec", "OUTCOME_RECORD_KIND", "RESULT_ADAPTER_KIND", "RESULT_SCHEMA",
    "ResultContractError", "adapt_worker_result", "artifact_owner",
    "assess_coordinator_outcome", "build_outcome_record", "public_result_contract", "worker_protocol_record",
]
