from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from astrid.core.pack.loader import _load_manifest_payload
from astrid.packs.h3_av.executors.publication_finalizer.run import main as finalize
from astrid.packs.h3_av.orchestrators.transform.run import staged_child_delegation
from astrid.packs.h3_av.src.provenance import compilation_provenance, require_compilation_provenance
from astrid.sdk.remote import _materialize_child_delegation


ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.parametrize("schema,workflow,raw_port", [
    (1, {"python", "companion", "source"}, "vibecomfy_run"),
    (2, {"python", "companion", "source"}, "vibecomfy_run"),
])
def test_parent_staged_policy_uses_exact_refs_and_fixed_publication(schema, workflow, raw_port):
    request_id = "sha256:" + "a" * 64
    bundle_id = "sha256:" + "b" * 64
    declaration = staged_child_delegation(
        project_id="project-1", request_schema=schema,
        request_object_id=request_id, input_bundle_object_id=bundle_id,
        run_target={"kind": "default"},
    )
    capabilities = [
        {"capability_id": capability_id, "definition_digest": "digest:" + capability_id}
        for capability_id in declaration["capability_ids"]
    ]
    policy = _materialize_child_delegation(
        declaration, capabilities,
        input_object_ids=[request_id, bundle_id], execution_request=None, project_id="project-1",
    )
    assert [stage["name"] for stage in policy["stages"]] == [
        "prepare", "compile", "validate", "run", "compose", "verify", "finalize",
    ]
    assert policy["stages"][0]["inputs"] == [
        {"name": "request", "root_object_id": request_id},
        {"name": "input_bundle", "root_object_id": bundle_id},
    ]
    expected_workflow = set(workflow)
    if schema in {1, 2}:
        # Schema-1 validation consumes the same managed media archive that the
        # canonical run stage receives; this is part of the sealed contract.
        expected_workflow.add("managed_assets")
    assert {row["name"] for row in policy["stages"][2]["inputs"]} == expected_workflow
    compose = policy["stages"][4]
    assert {row["name"]: row["output_port"] for row in compose["inputs"] if "producer_stage" in row}["generated"] == raw_port
    assert policy["stages"][-1]["inputs"] == [{
        "name": "verified_candidate", "producer_stage": "verify", "output_port": "verified_candidate",
    }]
    assert policy["final_publication"] == {"stage": "finalize", "verify_stage": "verify", "verify_output_port": "verified_candidate", "effect": {
        "effect_type": "generation.publish_v1", "target_id": "project-1",
        "payload": {
            "version": 1, "modality": "video", "generation_type": "h3_av.publication_finalizer",
            "metadata": {"source_capability": "h3_av.transform"},
            "partial_success_policy": "reject",
            "groups": [{"group_key": "main", "selectors": [{
                "selector": "main-0", "ordinal": 0, "variant_key": "original",
                "output_port": "verified_candidate", "required": True,
            }]}],
        },
    }}
    assert "compilation_digest" not in json.dumps(policy["final_publication"])
    assert len([stage for stage in policy["stages"] if stage["capability_id"] == "vibecomfy.run"]) == 1


def test_schema1_native_continuation_uses_one_muxed_runtime_output():
    declaration = staged_child_delegation(
        project_id="project-1", request_schema=1,
        request_object_id="sha256:" + "a" * 64,
        input_bundle_object_id="sha256:" + "b" * 64,
        operation="continue",
    )
    compose = declaration["stages"][4]
    assert {
        row["name"]: row["output_port"]
        for row in compose["inputs"]
        if "producer_stage" in row
    }["generated"] == "vibecomfy_run"
    assert "generated_audio" not in {row["name"] for row in compose["inputs"]}


def test_only_finalizer_declares_publication_and_forwards_verified_bytes(tmp_path):
    transform_manifest = _load_manifest_payload(ROOT / "astrid/packs/h3_av/orchestrators/transform/orchestrator.yaml")
    verify_manifest = _load_manifest_payload(ROOT / "astrid/packs/h3_av/executors/verify/executor.yaml")
    final_manifest = _load_manifest_payload(ROOT / "astrid/packs/h3_av/executors/publication_finalizer/executor.yaml")
    assert transform_manifest["metadata"]["child_execution_plan"]["final_publication"]["effect"]["payload"]["groups"][0]["selectors"][0]["output_port"] == "verified_candidate"
    assert "generation_publication" not in verify_manifest["metadata"]
    assert final_manifest["metadata"]["generation_publication"] == {
        "version": 1, "modality": "video", "output_port": "verified_candidate",
    }
    verified = tmp_path / "verify" / "candidate.mkv"
    verified.parent.mkdir()
    verified.write_bytes(b"verified media bytes")
    out = tmp_path / "final"
    assert finalize(["--verified-candidate", str(verified), "--out", str(out)]) == 0
    manifest = json.loads((out / "manifest.json").read_text())
    assert (out / "verified-candidate.mkv").read_bytes() == verified.read_bytes()
    assert len(manifest["outputs"]) == 1
    assert manifest["outputs"][0]["content_hash"] == "sha256:" + hashlib.sha256(verified.read_bytes()).hexdigest()
    assert manifest["outputs"][0]["output_port"] == "verified_candidate"


def test_public_v2_declaration_materializes_the_same_parent_effect_and_refs():
    manifest = _load_manifest_payload(ROOT / "astrid/packs/h3_av/orchestrators/transform/orchestrator.yaml")
    plan = manifest["metadata"]["child_execution_plan"]
    roots = {"request": "sha256:" + "a" * 64, "input_bundle": "sha256:" + "b" * 64}
    declaration = {
        "capability_ids": manifest["child_executors"],
        "root_inputs": roots,
        "stages": plan["stages"],
        "final_publication": plan["final_publication"],
    }
    capabilities = [
        {"capability_id": capability_id, "definition_digest": "digest:" + capability_id}
        for capability_id in declaration["capability_ids"]
    ]
    policy = _materialize_child_delegation(
        declaration, capabilities, input_object_ids=list(roots.values()),
        execution_request=None, project_id="project-1",
    )
    expected = staged_child_delegation(
        project_id="project-1", request_schema=2,
        request_object_id=roots["request"], input_bundle_object_id=roots["input_bundle"],
    )
    assert policy["final_publication"] == expected["final_publication"]
    assert policy["stages"][-1]["inputs"] == [{
        "name": "verified_candidate", "producer_stage": "verify", "output_port": "verified_candidate",
    }]
    assert [stage["name"] for stage in policy["stages"]] == [stage["name"] for stage in expected["stages"]]


def test_compilation_request_digest_and_graph_provenance_fail_closed():
    preparation = {"request_digest": "request", "mask_schedule": {"digest": "schedule"}, "assets": []}
    compilation = {
        "schema_version": 2, "kind": "h3_av_compilation", "request_digest": "request",
        "graph_binding": {"bundle_identity": "bundle", "sha256": "binding"},
        "graph": {"sha256": "graph"},
        "managed_assets": {"sha256": "assets", "manifest": {}},
    }
    digest_payload = {
        "schema_version": 2, "kind": "h3_av_compilation", "request_digest": "request",
        "graph_binding": {"bundle_identity": "bundle"},
        "managed_assets": {"sha256": "assets", "manifest": {}},
    }
    compilation["compilation_digest"] = hashlib.sha256(
        json.dumps(digest_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    composition = {"provenance": compilation_provenance(preparation, compilation)}
    require_compilation_provenance(preparation, compilation, composition)
    changed = {**compilation, "graph": {"sha256": "different"}}
    with pytest.raises(ValueError, match="compilation digest/artifact provenance"):
        require_compilation_provenance(preparation, changed, composition)
    changed = {**compilation, "request_digest": "other"}
    with pytest.raises(ValueError, match="request provenance"):
        compilation_provenance(preparation, changed)
    changed = {**compilation, "compilation_digest": "0" * 64}
    with pytest.raises(ValueError, match="compilation digest"):
        compilation_provenance(preparation, changed)
