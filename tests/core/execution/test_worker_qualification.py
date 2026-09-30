from __future__ import annotations

import json

import pytest

from astrid.core.execution.worker_qualification import (
    WorkerQualificationError,
    ensure_runpod_worker,
    load_worker_qualification,
)


def _digest(char: str = "a") -> str:
    return "sha256:" + char * 64


def _receipt() -> dict[str, object]:
    return {
        "schema_version": 1,
        "status": "qualified",
        "worker_ready": True,
        "qualification_id": "qualification-1",
        "target": {"kind": "runpod", "pod_id": "pod-1", "provider_account_ref": "runpod"},
        "runtime_instance_id": "runtime-1",
        "runtime_session_id": "session-1",
        "runtime_epoch": 4,
        "capability_digest": _digest("a"),
        "readiness_profile_hash": _digest("b"),
        "model_root_digest": _digest("c"),
        "output_root": "/tmp/astrid-output",
    }


def test_qualified_receipt_returns_exact_runpod_target() -> None:
    result = ensure_runpod_worker(_receipt())
    assert result["kind"] == "runpod"
    assert result["pod_id"] == "pod-1"
    assert result["runtime_epoch"] == 4


def test_qualified_receipt_preserves_nested_storage_and_legacy_volume_fields() -> None:
    receipt = _receipt()
    storage = {"network_volume_id": "volume-1", "mount_path": "/workspace"}
    receipt["target"] = {
        **receipt["target"],
        "network_volume_id": "volume-1",
        "storage": storage,
    }

    result = ensure_runpod_worker(receipt)

    assert result["storage"] is storage
    assert result["network_volume_id"] == "volume-1"


@pytest.mark.parametrize(
    "storage",
    [None, [], "volume-1", {"network_volume_id": ""}, {"network_volume_id": "  "}],
)
def test_malformed_or_empty_nested_storage_volume_fails(storage: object) -> None:
    receipt = _receipt()
    receipt["target"]["storage"] = storage
    with pytest.raises(WorkerQualificationError, match="storage"):
        ensure_runpod_worker(receipt)


def test_contradictory_direct_and_nested_volume_ids_fail() -> None:
    receipt = _receipt()
    receipt["target"].update({
        "network_volume_id": "direct-volume",
        "storage": {"network_volume_id": "nested-volume"},
    })
    with pytest.raises(WorkerQualificationError, match="disagree"):
        ensure_runpod_worker(receipt)


@pytest.mark.parametrize("field", ["backup_volume_id", "network_volume_id", "volume_id"])
def test_all_direct_and_nested_volume_id_aliases_must_agree(field: str) -> None:
    receipt = _receipt()
    receipt["target"].update({field: "direct-volume", "storage": {field: "nested-volume"}})
    with pytest.raises(WorkerQualificationError, match="disagree"):
        ensure_runpod_worker(receipt)


@pytest.mark.parametrize("field", ["backup_volume_id", "network_volume_id", "volume_id"])
def test_empty_direct_volume_ids_fail(field: str) -> None:
    receipt = _receipt()
    receipt["target"][field] = "  "
    with pytest.raises(WorkerQualificationError, match=field):
        ensure_runpod_worker(receipt)


@pytest.mark.parametrize(
    "field,value",
    [
        ("status", "ready"),
        ("worker_ready", False),
        ("runtime_epoch", 0),
        ("capability_digest", "wrong"),
        ("model_root_digest", "wrong"),
    ],
)
def test_incomplete_receipts_fail_closed(field: str, value: object) -> None:
    receipt = _receipt()
    receipt[field] = value
    with pytest.raises(WorkerQualificationError):
        ensure_runpod_worker(receipt)


def test_target_mismatch_fails_before_admission() -> None:
    with pytest.raises(WorkerQualificationError, match="target disagrees"):
        ensure_runpod_worker(
            _receipt(),
            expected_target={"kind": "runpod", "pod_id": "other", "provider_account_ref": "runpod"},
        )


def test_nested_storage_mismatch_fails_before_admission() -> None:
    receipt = _receipt()
    receipt["target"]["storage"] = {"network_volume_id": "qualified-volume"}
    with pytest.raises(WorkerQualificationError, match="target disagrees"):
        ensure_runpod_worker(
            receipt,
            expected_target={
                "kind": "runpod",
                "pod_id": "pod-1",
                "provider_account_ref": "runpod",
                "storage": {"network_volume_id": "request-volume"},
            },
        )


def test_expected_target_must_match_direct_volume_ids_exactly() -> None:
    receipt = _receipt()
    receipt["target"]["network_volume_id"] = "volume-1"
    with pytest.raises(WorkerQualificationError, match="target disagrees"):
        ensure_runpod_worker(
            receipt,
            expected_target={
                "kind": "runpod",
                "pod_id": "pod-1",
                "provider_account_ref": "runpod",
            },
        )


def test_load_rejects_non_object_and_accepts_regular_file(tmp_path) -> None:
    path = tmp_path / "qualification.json"
    path.write_text(json.dumps(_receipt()), encoding="utf-8")
    assert load_worker_qualification(path)["qualification_id"] == "qualification-1"
    path.write_text("[]", encoding="utf-8")
    with pytest.raises(WorkerQualificationError, match="must contain an object"):
        load_worker_qualification(path)
