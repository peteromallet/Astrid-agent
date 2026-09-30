from __future__ import annotations

import copy
import json

import pytest

from astrid.packs.h3_av.src.receipt import (
    ReceiptError,
    attest_runtime_managed_composition,
    attest_runtime_managed_publication,
    build_final_receipt,
)
from astrid.sdk.results import InvocationResult


_REQUEST_DIGEST = "sha256:request"
_RAW_SHA256 = "b" * 64
_CANDIDATE_SHA256 = "a" * 64
_CLEANUP_TARGET = {
    "kind": "runpod",
    "pod_id": "pod-123",
    "provider_account_ref": "runpod-account-1",
    "backup_volume_id": "volume-456",
}


def _task_evidence() -> dict[str, object]:
    return {
        "run_id": "run-1",
        "task_id": "task-1",
        "attempt_id": "attempt-1",
        "capability_id": "vibecomfy.run",
        "target": dict(_CLEANUP_TARGET),
    }


def _candidate_verified() -> dict[str, object]:
    return {
        "verification_path": "/local/verification.json",
        "verification": {
            "request_digest": _REQUEST_DIGEST,
            "candidate_sha256": _CANDIDATE_SHA256,
            "candidate_size": 456,
            "status": "verified",
        },
    }


def _cleanup(*, verified: bool = True) -> dict[str, object]:
    return {
        "target": dict(_CLEANUP_TARGET),
        "resources": [
            {
                "kind": "runpod.pod",
                "id": "pod-123",
                "owned": True,
                "owner_target": dict(_CLEANUP_TARGET),
                "expected_postcondition": "terminated and absent from provider list",
                "observed_postcondition": "terminated and absent from provider list",
                "verified": verified,
            },
            {
                "kind": "runpod.volume",
                "id": "volume-456",
                "owned": False,
                "owner_target": dict(_CLEANUP_TARGET),
                "expected_postcondition": "preserved",
                "observed_postcondition": "preserved",
                "verified": verified,
            },
        ]
    }


def _generation_intent() -> dict[str, object]:
    return {
        "version": 1,
        "modality": "video",
        "partial_success_policy": "reject",
        "groups": [
            {
                "group_key": "main",
                "selectors": [
                    {
                        "selector": "main-0",
                        "ordinal": 0,
                        "variant_key": "original",
                        "required": True,
                    }
                ],
            }
        ],
        "metadata": {
            "compiled_generation_contract": True,
            "h3_av": {"request_digest": _REQUEST_DIGEST},
        },
    }


def _runtime_result() -> tuple[InvocationResult, dict[str, object]]:
    intent = _generation_intent()
    selector = {
        "selector": "main-0",
        "ordinal": 0,
        "variant_key": "original",
        "output_port": "vibecomfy_run",
        "required": True,
    }
    effect = {
        "effect_type": "generation.publish_v1",
        "target_id": "project-1",
        "payload": {
            "version": 1,
            "modality": "video",
            "generation_type": "vibecomfy.run",
            "metadata": intent["metadata"],
            "partial_success_policy": "reject",
            "groups": [{"group_key": "main", "selectors": [selector]}],
        },
    }
    output = {
        "name": "vibecomfy_run",
        "filename": "raw-generation.mp4",
        "kind": "object",
        "role": "result",
        "size": 123,
        "digest": "sha256:" + _RAW_SHA256,
        "object_id": "sha256:" + _RAW_SHA256,
        "output_port": "vibecomfy_run",
        "group_key": "main",
        "variant_key": "original",
        "ordinal": 0,
    }
    settled = {
        "outputs": [output],
        "generation_publish_v1": {
            "effect_type": "generation.publish_v1",
            "publications": [
                {
                    "group_key": "main",
                    "generation_id": "generation-1",
                    "missing_selectors": [],
                    "variants": [
                        {
                            "generation_id": "generation-1",
                            "variant_id": "variant-1",
                            "object_id": "sha256:" + _RAW_SHA256,
                            "output_port": "vibecomfy_run",
                            "ordinal": 0,
                            "variant_key": "original",
                        }
                    ],
                }
            ],
        },
    }
    task = {
        "task_id": "task-1",
        "run_id": "run-1",
        "attempt_id": "attempt-1",
        "project_id": "project-1",
        "state": "succeeded",
        "generation_intent": intent,
        "expected_effect": effect,
        "result": settled,
    }
    managed = {
        **output,
        "association_id": "association-1",
        "task_id": "task-1",
        "run_id": "run-1",
        "attempt_id": "attempt-1",
        "project_id": "project-1",
        "generation_id": "generation-1",
    }
    raw_result = {
        "ok": True,
        "state": "completed",
        "kernel_run_id": "run-1",
        "kernel_task_id": "task-1",
        "kernel_attempt_id": "attempt-1",
        "task": task,
        "result": settled,
        "outputs": {"artifacts": [output]},
        "managed_outputs": [managed],
    }
    return (
        InvocationResult(
            capability_id="vibecomfy.run",
            capability_type="executor",
            native_kind="executor",
            ok=True,
            raw_result=raw_result,
            outputs={"managed_outputs": [managed]},
            kernel_run_id="run-1",
            kernel_task_id="task-1",
            kernel_attempt_id="attempt-1",
        ),
        output,
    )


def _publication(
    runtime_result: InvocationResult | None = None,
    retrieved_output: dict[str, object] | None = None,
):
    default_result, default_output = _runtime_result()
    return attest_runtime_managed_publication(
        runtime_result=runtime_result or default_result,
        request_digest=_REQUEST_DIGEST,
        generation_intent=_generation_intent(),
        retrieved_outputs=[retrieved_output or default_output],
    )


def _finalizer_result() -> tuple[InvocationResult, dict[str, object]]:
    base_result, _ = _runtime_result()
    raw = copy.deepcopy(base_result.raw_result)
    task = raw["task"]
    settled = raw["result"]
    payload = task["expected_effect"]["payload"]
    payload["generation_type"] = "h3_av.publication_finalizer"
    selector = payload["groups"][0]["selectors"][0]
    selector["output_port"] = "verified_candidate"
    output = settled["outputs"][0]
    output.update(
        {
            "name": "verified-candidate.mp4",
            "filename": "verified-candidate.mp4",
            "size": 456,
            "digest": "sha256:" + _CANDIDATE_SHA256,
            "object_id": "sha256:" + _CANDIDATE_SHA256,
            "output_port": "verified_candidate",
        }
    )
    variant = settled["generation_publish_v1"]["publications"][0]["variants"][0]
    variant.update(
        {
            "object_id": "sha256:" + _CANDIDATE_SHA256,
            "output_port": "verified_candidate",
        }
    )
    managed = raw["managed_outputs"][0]
    managed.update(
        {
            "name": "verified-candidate.mp4",
            "filename": "verified-candidate.mp4",
            "size": 456,
            "digest": "sha256:" + _CANDIDATE_SHA256,
            "object_id": "sha256:" + _CANDIDATE_SHA256,
            "output_port": "verified_candidate",
        }
    )
    raw["outputs"]["artifacts"] = [output]
    result = InvocationResult(
        **{
            **base_result.__dict__,
            "capability_id": "h3_av.publication_finalizer",
            "raw_result": raw,
            "outputs": {"managed_outputs": [managed]},
        }
    )
    retrieved = {
        "object_id": "sha256:" + _CANDIDATE_SHA256,
        "digest": "sha256:" + _CANDIDATE_SHA256,
        "size": 456,
        "verified": True,
    }
    return result, retrieved


def _final_publication(
    runtime_result: InvocationResult | None = None,
    retrieved_output: dict[str, object] | None = None,
    *,
    raw_managed_publication=None,
):
    default_result, default_output = _finalizer_result()
    return attest_runtime_managed_composition(
        runtime_result=runtime_result or default_result,
        request_digest=_REQUEST_DIGEST,
        candidate_verified=_candidate_verified(),
        retrieved_outputs=[default_output if retrieved_output is None else retrieved_output],
        raw_managed_publication=raw_managed_publication,
    )


def test_raw_publication_and_cleanup_do_not_complete_without_finalizer() -> None:
    receipt = build_final_receipt(
        request_digest=_REQUEST_DIGEST,
        task_succeeded=_task_evidence(),
        candidate_verified=_candidate_verified(),
        cleanup=_cleanup(),
        raw_managed_publication=_publication(),
    )

    assert receipt["overall_status"] == "candidate_verified"
    assert {name: value["status"] for name, value in receipt["states"].items()} == {
        "task_succeeded": "passed",
        "raw_managed_publication": "passed",
        "candidate_verified": "passed",
        "final_composition_publication": "not_claimed",
        "editorially_approved": "not_claimed",
        "cleanup_verified": "passed",
    }
    assert receipt["publication_contract"] == {
        "published_scope": "raw_internal_lineage",
        "effect_type": "generation.publish_v1",
        "final_composition_publication": "required",
    }
    json.dumps(receipt)


def test_verified_final_composition_is_required_for_complete_publication() -> None:
    raw_publication = _publication()
    final_publication = _final_publication(
        raw_managed_publication=raw_publication,
    )
    receipt = build_final_receipt(
        request_digest=_REQUEST_DIGEST,
        task_succeeded=_task_evidence(),
        candidate_verified=_candidate_verified(),
        cleanup=_cleanup(),
        raw_managed_publication=raw_publication,
        final_managed_publication=final_publication,
    )

    assert receipt["overall_status"] == "complete"
    assert receipt["states"]["final_composition_publication"]["status"] == "passed"
    assert receipt["publication_contract"] == {
        "published_scope": "final_composition",
        "effect_type": "generation.publish_v1",
        "final_composition_publication": "verified",
    }
    assert receipt["states"]["raw_managed_publication"]["evidence"]["publication"]["object_id"] != receipt["states"]["final_composition_publication"]["evidence"]["publication"]["object_id"]


def test_finalizer_accepts_same_identity_readback_after_lost_reply() -> None:
    result, _ = _finalizer_result()

    first = _final_publication(result)
    retry_readback = _final_publication(result)

    assert first.status == retry_readback.status == "passed"
    assert first.evidence["task"] == retry_readback.evidence["task"]
    assert first.evidence["publication"] == retry_readback.evidence["publication"]


def test_raw_publication_does_not_claim_the_different_composed_candidate() -> None:
    receipt = build_final_receipt(
        request_digest=_REQUEST_DIGEST,
        task_succeeded=_task_evidence(),
        candidate_verified=_candidate_verified(),
        cleanup=_cleanup(),
        raw_managed_publication=_publication(),
    )

    publication = receipt["states"]["raw_managed_publication"]["evidence"]
    assert publication["publication"]["object_id"] == "sha256:" + _RAW_SHA256
    assert receipt["states"]["candidate_verified"]["evidence"]["verification"]["candidate_sha256"] == _CANDIDATE_SHA256
    assert publication["final_composition_publication"] == "deferred"


def test_caller_authored_publication_mapping_is_rejected() -> None:
    with pytest.raises(ReceiptError, match="attest_runtime_managed_publication"):
        build_final_receipt(
            request_digest=_REQUEST_DIGEST,
            task_succeeded=_task_evidence(),
            candidate_verified=_candidate_verified(),
            cleanup=_cleanup(),
            raw_managed_publication={"status": "passed"},  # type: ignore[arg-type]
        )


def test_publication_is_not_claimed_when_runtime_evidence_is_absent() -> None:
    receipt = build_final_receipt(
        request_digest=_REQUEST_DIGEST,
        task_succeeded=_task_evidence(),
        candidate_verified=_candidate_verified(),
        cleanup=_cleanup(),
    )

    assert receipt["overall_status"] == "candidate_verified"
    assert receipt["states"]["raw_managed_publication"]["status"] == "not_claimed"


@pytest.mark.parametrize(
    "mutation, expected_reason",
    [
        (
            lambda raw: raw["managed_outputs"][0].update({"project_id": "foreign-project"}),
            "uniquely associated",
        ),
        (
            lambda raw: raw["managed_outputs"][0].update({"association_id": ""}),
            "uniquely associated",
        ),
    ],
    ids=["foreign-association", "tampered-association"],
)
def test_finalizer_rejects_foreign_or_tampered_association(mutation, expected_reason: str) -> None:
    result, _ = _finalizer_result()
    mutable = copy.deepcopy(result.raw_result)
    mutation(mutable)
    result = InvocationResult(**{**result.__dict__, "raw_result": mutable})
    publication = _final_publication(result)

    assert publication.status == "failed"
    assert expected_reason in publication.evidence["validation_error"]


def test_finalizer_rejects_partial_local_retrieval() -> None:
    publication = _final_publication(retrieved_output={})

    assert publication.status == "failed"
    assert "uniquely associated and locally retrieved" in publication.evidence["validation_error"]


def test_cleanup_only_recovery_remains_incomplete_until_finalizer_readback() -> None:
    receipt = build_final_receipt(
        request_digest=_REQUEST_DIGEST,
        task_succeeded=_task_evidence(),
        candidate_verified=_candidate_verified(),
        cleanup=_cleanup(verified=False),
        raw_managed_publication=_publication(),
    )

    assert receipt["overall_status"] == "candidate_verified"
    assert receipt["states"]["final_composition_publication"]["status"] == "not_claimed"
    assert receipt["cleanup"]["status"] == "cleanup_pending"


@pytest.mark.parametrize(
    "mutation, expected_reason",
    [
        (
            lambda raw: raw["result"].pop("generation_publish_v1"),
            "no canonical generation.publish_v1 result",
        ),
        (
            lambda raw: raw["task"]["expected_effect"].update({"target_id": "other-project"}),
            "target disagrees",
        ),
        (
            lambda raw: raw["managed_outputs"][0].update({"task_id": "other-task"}),
            "no unique Runtime managed-output association",
        ),
        (
            lambda raw: raw["result"]["generation_publish_v1"]["publications"][0]["variants"][0].update(
                {"object_id": "sha256:" + "c" * 64}
            ),
            "settled task output",
        ),
    ],
    ids=["missing-result", "wrong-project", "unrelated-association", "different-object"],
)
def test_conflicting_runtime_publication_evidence_cannot_complete(
    mutation,
    expected_reason: str,
) -> None:
    result, output = _runtime_result()
    mutable = copy.deepcopy(result.raw_result)
    mutation(mutable)
    result = InvocationResult(
        **{
            **result.__dict__,
            "raw_result": mutable,
        }
    )
    publication = _publication(result, output)
    receipt = build_final_receipt(
        request_digest=_REQUEST_DIGEST,
        task_succeeded=_task_evidence(),
        candidate_verified=_candidate_verified(),
        cleanup=_cleanup(),
        raw_managed_publication=publication,
    )

    assert receipt["overall_status"] == "candidate_verified"
    assert receipt["states"]["raw_managed_publication"]["status"] == "failed"
    assert expected_reason in receipt["states"]["raw_managed_publication"]["reason"]


def test_publication_requires_digest_verified_local_raw_retrieval() -> None:
    result, _output = _runtime_result()
    publication = attest_runtime_managed_publication(
        runtime_result=result,
        request_digest=_REQUEST_DIGEST,
        generation_intent=_generation_intent(),
        retrieved_outputs=[],
    )
    receipt = build_final_receipt(
        request_digest=_REQUEST_DIGEST,
        task_succeeded=_task_evidence(),
        candidate_verified=_candidate_verified(),
        cleanup=_cleanup(),
        raw_managed_publication=publication,
    )

    assert receipt["states"]["raw_managed_publication"]["status"] == "failed"
    assert "retrieved" in receipt["states"]["raw_managed_publication"]["reason"]


def test_task_identity_must_match_runtime_publication() -> None:
    task = _task_evidence()
    task["attempt_id"] = "other-attempt"
    receipt = build_final_receipt(
        request_digest=_REQUEST_DIGEST,
        task_succeeded=task,
        candidate_verified=_candidate_verified(),
        cleanup=_cleanup(),
        raw_managed_publication=_publication(),
    )

    assert receipt["overall_status"] == "candidate_verified"
    assert receipt["states"]["raw_managed_publication"]["status"] == "failed"
    assert "attempt_id disagrees" in receipt["states"]["raw_managed_publication"]["reason"]


def test_unverified_composition_cannot_complete() -> None:
    receipt = build_final_receipt(
        request_digest=_REQUEST_DIGEST,
        task_succeeded=_task_evidence(),
        candidate_verified={"verification": {"status": "verified"}},
        cleanup=_cleanup(),
        raw_managed_publication=_publication(),
    )

    assert receipt["overall_status"] == "task_failed"
    assert receipt["states"]["candidate_verified"]["status"] == "failed"


def test_uncertain_cleanup_cannot_be_hidden_by_successful_delivery() -> None:
    receipt = build_final_receipt(
        request_digest=_REQUEST_DIGEST,
        task_succeeded=_task_evidence(),
        candidate_verified=_candidate_verified(),
        cleanup=_cleanup(verified=False),
        raw_managed_publication=_publication(),
    )

    assert receipt["overall_status"] == "candidate_verified"
    assert receipt["cleanup"]["status"] == "cleanup_pending"
    assert receipt["states"]["cleanup_verified"]["status"] == "cleanup_pending"


def test_cleanup_requires_exact_unique_resource_postconditions() -> None:
    cleanup = _cleanup()
    cleanup["resources"] = [cleanup["resources"][0], cleanup["resources"][0]]  # type: ignore[index]
    with pytest.raises(ReceiptError, match="duplicate"):
        build_final_receipt(
            request_digest=_REQUEST_DIGEST,
            task_succeeded=_task_evidence(),
            candidate_verified=_candidate_verified(),
            cleanup=cleanup,
            raw_managed_publication=_publication(),
        )


def test_cleanup_cannot_complete_for_a_different_pod_or_resource() -> None:
    cleanup = _cleanup()
    cleanup["resources"][0]["id"] = "unrelated-pod"  # type: ignore[index]

    receipt = build_final_receipt(
        request_digest=_REQUEST_DIGEST,
        task_succeeded=_task_evidence(),
        candidate_verified=_candidate_verified(),
        cleanup=cleanup,
        raw_managed_publication=_publication(),
    )

    assert receipt["overall_status"] == "candidate_verified"
    assert receipt["cleanup"]["status"] == "failed"
    assert receipt["states"]["cleanup_verified"]["status"] == "failed"


def test_nested_target_storage_volume_is_preserved_and_required_for_cleanup() -> None:
    target = {
        "kind": "runpod",
        "pod_id": "pod-123",
        "provider_account_ref": "runpod-account-1",
        "storage": {"network_volume_id": "network-volume-789"},
    }
    task = {**_task_evidence(), "target": target}
    cleanup = _cleanup()
    cleanup["target"] = dict(target)
    cleanup["resources"][1]["id"] = "network-volume-789"  # type: ignore[index]
    for resource in cleanup["resources"]:
        resource["owner_target"] = dict(target)

    receipt = build_final_receipt(
        request_digest=_REQUEST_DIGEST,
        task_succeeded=task,
        candidate_verified=_candidate_verified(),
        cleanup=cleanup,
        raw_managed_publication=_publication(),
    )

    assert receipt["cleanup"]["target"]["storage"] == {
        "network_volume_id": "network-volume-789"
    }
    assert receipt["cleanup"]["status"] == "passed"

    cleanup["resources"] = cleanup["resources"][:1]  # type: ignore[index]
    missing_volume = build_final_receipt(
        request_digest=_REQUEST_DIGEST,
        task_succeeded=task,
        candidate_verified=_candidate_verified(),
        cleanup=cleanup,
        raw_managed_publication=_publication(),
    )
    assert missing_volume["cleanup"]["status"] == "failed"


@pytest.mark.parametrize(
    "target",
    [
        {
            **_CLEANUP_TARGET,
            "storage": {"network_volume_id": "different-volume"},
        },
        {
            "kind": "runpod",
            "pod_id": "pod-123",
            "provider_account_ref": "runpod-account-1",
            "storage": {"network_volume_id": ""},
        },
    ],
    ids=["contradictory-identities", "missing-nested-identity"],
)
def test_cleanup_rejects_contradictory_or_missing_nested_volume_identity(target) -> None:
    task = {**_task_evidence(), "target": target}
    cleanup = _cleanup()
    cleanup["target"] = dict(target)

    with pytest.raises(ReceiptError, match="contradictory|missing or empty"):
        build_final_receipt(
            request_digest=_REQUEST_DIGEST,
            task_succeeded=task,
            candidate_verified=_candidate_verified(),
            cleanup=cleanup,
            raw_managed_publication=_publication(),
        )


def test_cleanup_rejects_contradictory_target_evidence() -> None:
    cleanup = _cleanup()
    cleanup["target"] = {**_CLEANUP_TARGET, "pod_id": "other-pod"}

    with pytest.raises(ReceiptError, match="cleanup target disagrees"):
        build_final_receipt(
            request_digest=_REQUEST_DIGEST,
            task_succeeded=_task_evidence(),
            candidate_verified=_candidate_verified(),
            cleanup=cleanup,
            raw_managed_publication=_publication(),
        )


def test_cleanup_rejects_resource_owner_binding_to_another_target() -> None:
    cleanup = _cleanup()
    cleanup["resources"][0]["owner_target"] = {  # type: ignore[index]
        **_CLEANUP_TARGET,
        "pod_id": "other-pod",
    }

    with pytest.raises(ReceiptError, match="owner_target disagrees"):
        build_final_receipt(
            request_digest=_REQUEST_DIGEST,
            task_succeeded=_task_evidence(),
            candidate_verified=_candidate_verified(),
            cleanup=cleanup,
            raw_managed_publication=_publication(),
        )


@pytest.mark.parametrize(
    "pending_evidence",
    [
        {"status": "cleanup_pending"},
        {"state": "cleanup_pending"},
        {"cleanup_pending": True},
    ],
)
def test_uncertain_cleanup_stays_pending_and_cannot_complete(pending_evidence) -> None:
    cleanup = {**_cleanup(), **pending_evidence}

    receipt = build_final_receipt(
        request_digest=_REQUEST_DIGEST,
        task_succeeded=_task_evidence(),
        candidate_verified=_candidate_verified(),
        cleanup=cleanup,
        raw_managed_publication=_publication(),
    )

    assert receipt["overall_status"] == "candidate_verified"
    assert receipt["cleanup"]["status"] == "cleanup_pending"
    assert receipt["states"]["cleanup_verified"]["status"] == "cleanup_pending"


def test_cleanup_requires_a_bound_admitted_target() -> None:
    task = _task_evidence()
    task.pop("target")

    receipt = build_final_receipt(
        request_digest=_REQUEST_DIGEST,
        task_succeeded=task,
        candidate_verified=_candidate_verified(),
        cleanup=_cleanup(),
        raw_managed_publication=_publication(),
    )

    assert receipt["overall_status"] == "candidate_verified"
    assert receipt["cleanup"]["status"] == "failed"
    assert "cannot be bound" in receipt["cleanup"]["reason"]
