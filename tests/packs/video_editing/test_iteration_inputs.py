"""Offline contract tests; no Runtime, provider, or project fixture required."""
import builtins
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from astrid.packs.video_editing.shared import iteration_inputs as inputs

PROJECT = "frozen-project"
TARGET = "run-target"
DIGEST = hashlib.sha256(b"admitted image").hexdigest()
OBJECT_ID = f"sha256:{DIGEST}"
SIZE = len(b"admitted image")


def documents():
    quality = {
        "schema_version": 1, "target_run_id": TARGET, "total_runs": 2,
        "data_quality": 0.667,
        "dimensions": {"lineage": True, "task_outputs": True, "evidence": False,
                       "relations": True, "receipts": False, "events": True},
        "unresolved_producer_runs": [], "missing_lineage": [],
        "missing_task_outputs": [], "missing_evidence": ["run-parent"],
        "missing_relations": [], "missing_receipts": [TARGET], "missing_events": [],
        "unavailable_sources": ["receipts_unavailable"],
        "relation_count": 1, "evidence_count": 1, "receipt_count": 0,
        "run_event_count": 1, "task_event_count": 1, "events_role": "observational_only",
        "authority": {"kind": "runtime", "project": PROJECT},
    }
    artifact = {"kind": "image", "object_id": OBJECT_ID, "sha256": DIGEST,
                "size": SIZE, "media_type": "image/png", "duration": 4,
                "task_id": "task-image", "output_id": "output-image",
                "source_association_id": "association-image", "role": "result"}
    manifest = {
        "schema_version": 1, "target_run_id": TARGET,
        "runs": [
            {"run_id": "run-parent", "label": "pulled_by_ancestry", "causal_depth": 1,
             "selection_order": 0, "parent_run_ids": [], "unresolved_parent_run_ids": [],
             "lineage_incomplete": False, "task_ids": [], "output_artifacts": [],
             "relations": [{"run_id": TARGET}], "evidence": [], "receipts": [],
             "run_events": [], "task_events": {}, "lineage_gaps": [], "summary": None},
            {"run_id": TARGET, "label": "target", "causal_depth": 0,
             "selection_order": 1, "parent_run_ids": [{"run_id": "run-parent"}],
             "unresolved_parent_run_ids": [], "lineage_incomplete": False,
             "task_ids": ["task-image"], "output_artifacts": [artifact],
             "relations": [], "evidence": [{"note": "observed"}], "receipts": [],
             "run_events": [inputs.project_event({"type": "finished"})], "task_events": {},
             "lineage_gaps": ["receipts_unavailable"], "summary": None},
        ],
        "quality": deepcopy(quality), "summary_cache": {"hits": 0, "misses": 0},
        "cost_estimate": {"summarize_calls": 0, "estimated_cost": 0.0},
        "authority": {"kind": "runtime", "project": PROJECT, "run_ids": ["run-parent", TARGET]},
    }
    binding = {
        "name": "image_0", "object_id": OBJECT_ID, "sha256": DIGEST,
        "size": SIZE, "media_type": "image/png", "filename": "image-0.png",
        "associations": [{"project": PROJECT, "run_id": TARGET, "artifact_index": 0,
                          "task_id": "task-image", "output_id": "output-image",
                          "source_association_id": "association-image"}],
    }
    return manifest, quality, [binding]


def freeze(manifest=None, quality=None, bindings=None, **kwargs):
    original = documents()
    return inputs.freeze_inputs(
        original[0] if manifest is None else manifest,
        original[1] if quality is None else quality,
        original[2] if bindings is None else bindings,
        project=PROJECT, target_run_id=TARGET, **kwargs,
    )


def normalize(value, **kwargs):
    return inputs.normalize_frozen_inputs(value, project=kwargs.get("project", PROJECT),
                                          target_run_id=kwargs.get("target_run_id", TARGET))


def receipts(value):
    return {b["name"]: {
        "path": f"/host-confined-stage/{b['filename']}", "object_id": b["object_id"],
        "sha256": b["sha256"], "size": b["size"], "media_type": b["media_type"], "filename": b["filename"],
    } for b in value["media_bindings"]}


def test_existing_documents_round_trip_without_quality_or_lineage_invention():
    manifest, quality, bindings = documents()
    frozen = freeze(manifest, quality, bindings)
    encoded = inputs.serialize_frozen_inputs(frozen, project=PROJECT, target_run_id=TARGET)
    decoded = inputs.parse_frozen_inputs(encoded, project=PROJECT, target_run_id=TARGET)
    assert decoded == frozen
    assert decoded["manifest"] == manifest
    assert decoded["quality"] == quality
    assert decoded["quality"]["dimensions"]["evidence"] is False
    assert decoded["quality"]["missing_receipts"] == [TARGET]
    assert [r["run_id"] for r in decoded["manifest"]["runs"]] == ["run-parent", TARGET]
    assert inputs.serialize_frozen_inputs(decoded, project=PROJECT, target_run_id=TARGET) == encoded
    assert encoded == json.dumps(frozen, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    assert set(frozen) == {"schema_version", "project", "target_run_id", "manifest", "quality", "media_bindings"}
    assert manifest == documents()[0]  # Caller input was not mutated.


@pytest.mark.parametrize("field,value", [("project", "other"), ("target_run_id", "other")])
def test_expected_identity_mismatch(field, value):
    with pytest.raises(inputs.FrozenInputError, match="mismatch"):
        normalize(freeze(), **{field: value})


@pytest.mark.parametrize("document,field", [("manifest", "target_run_id"), ("quality", "target_run_id"),
                                           ("manifest", "project"), ("quality", "project")])
def test_document_identity_mismatch(document, field):
    manifest, quality, bindings = documents()
    doc = manifest if document == "manifest" else quality
    if field == "project":
        doc["authority"]["project"] = "other"
    else:
        doc[field] = "other"
    with pytest.raises(inputs.FrozenInputError, match="mismatch"):
        freeze(manifest, quality, bindings)


def test_strip_locators_including_nested_supporting_facts_without_reading_them():
    manifest, quality, bindings = documents()
    manifest["runs"][1]["out_path"] = "/private/project"
    artifact = manifest["runs"][1]["output_artifacts"][0]
    artifact.update({"path": "/etc/passwd", "file": "../../secret", "locator": "https://untrusted"})
    manifest["runs"][1]["evidence"][0]["nested"] = {"local_path": "/credentials", "note": "kept"}
    frozen = freeze(manifest, quality, bindings)
    assert "out_path" not in frozen["manifest"]["runs"][1]
    assert not {"path", "file", "locator"} & set(frozen["manifest"]["runs"][1]["output_artifacts"][0])
    assert frozen["manifest"]["runs"][1]["evidence"][0]["nested"] == {"note": "kept"}
    kwargs = inputs.assembly_inputs(frozen, receipts(frozen), project=PROJECT, target_run_id=TARGET)
    assert kwargs["input_manifest"]["runs"][1]["output_artifacts"][0]["path"] == "/host-confined-stage/image-0.png"
    assert kwargs["runtime_client"] is None and kwargs["runtime_project"] is None
    assert kwargs["input_quality"] == quality
    assert "path" not in frozen["manifest"]["runs"][1]["output_artifacts"][0]
    frozen["manifest"]["runs"][1]["output_artifacts"][0]["path"] = "/etc/passwd"
    with pytest.raises(inputs.FrozenInputError, match="locator"):
        normalize(frozen)


def test_shared_object_retains_associations_and_deduplicates_bytes():
    manifest, quality, bindings = documents()
    manifest["runs"][0]["output_artifacts"] = [deepcopy(manifest["runs"][1]["output_artifacts"][0])]
    bindings[0]["associations"].append({**bindings[0]["associations"][0], "run_id": "run-parent"})
    frozen = freeze(manifest, quality, bindings)
    bindings[0]["associations"].reverse()
    assert freeze(manifest, quality, bindings) == frozen
    assert len(frozen["media_bindings"]) == 1
    assert len(frozen["media_bindings"][0]["associations"]) == 2
    kwargs = inputs.assembly_inputs(frozen, receipts(frozen), project=PROJECT, target_run_id=TARGET)
    assert {run["output_artifacts"][0]["path"] for run in kwargs["input_manifest"]["runs"]} == {"/host-confined-stage/image-0.png"}


@pytest.mark.parametrize("variation", ["duplicate-name", "duplicate-object", "duplicate-filename", "duplicate-reference", "unresolved-reference"])
def test_duplicate_conflicting_and_unresolved_bindings(variation):
    manifest, quality, bindings = documents()
    if variation == "duplicate-reference":
        bindings[0]["associations"] *= 2
    elif variation == "unresolved-reference":
        bindings[0]["associations"][0]["artifact_index"] = 99
    else:
        second = deepcopy(bindings[0])
        second.update({"name": "other", "filename": "other.png", "sha256": "b" * 64,
                       "object_id": "sha256:" + "b" * 64})
        if variation == "duplicate-name":
            second["name"] = bindings[0]["name"]
        elif variation == "duplicate-object":
            second.update({"object_id": OBJECT_ID, "sha256": DIGEST})
        else:
            second["filename"] = bindings[0]["filename"].upper()
        bindings.append(second)
    with pytest.raises(inputs.FrozenInputError, match="duplicate|conflicting|unresolved"):
        freeze(manifest, quality, bindings)


@pytest.mark.parametrize("field,value", [("object_id", "tampered"), ("sha256", "b" * 64),
                                         ("size", SIZE + 1), ("media_type", "video/mp4"),
                                         ("task_id", "other-task"), ("output_id", "other-output"),
                                         ("source_association_id", "other-association")])
def test_required_reference_metadata_conflict(field, value):
    manifest, quality, bindings = documents()
    manifest["runs"][1]["output_artifacts"][0][field] = value
    with pytest.raises(inputs.FrozenInputError, match="conflicts"):
        freeze(manifest, quality, bindings)


def test_missing_media_fails_instead_of_card_even_after_locator_stripping():
    manifest, quality, _ = documents()
    manifest["runs"][1]["output_artifacts"][0] = {"kind": "image", "path": "/missing/source.png"}
    with pytest.raises(inputs.FrozenInputError, match="silent card fallback"):
        freeze(manifest, quality, [])
    manifest["runs"][1]["output_artifacts"] = []
    value = freeze(manifest, quality, [])
    assert value["quality"] == quality  # Missing source evidence stays missing.


def test_envelope_claims_no_self_authentication_or_child_reservations():
    frozen = freeze()
    removed = {"documents_sha256", "contract_sha256", "child_dependency_bytes", "generated_document_bytes"}
    assert not removed & set(frozen)
    # Runtime authenticated input identity, outside this helper, verifies bytes.
    frozen["manifest"]["runs"][0]["label"] = "updated claim"
    assert normalize(frozen)["manifest"]["runs"][0]["label"] == "updated claim"
    for field in removed:
        with pytest.raises(inputs.FrozenInputError, match="unknown fields"):
            normalize({**frozen, field: "unadmitted"})


@pytest.mark.parametrize("object_id", ["opaque-object", DIGEST, "sha256:" + "b" * 64,
                                        "sha256:" + DIGEST.upper(), "SHA256:" + DIGEST])
def test_binding_requires_exact_canonical_cas_identity(object_id):
    manifest, quality, bindings = documents()
    bindings[0]["object_id"] = object_id
    with pytest.raises(inputs.FrozenInputError, match="canonical sha256"):
        freeze(manifest, quality, bindings)


def test_cas_identity_matches_normalized_media_digest():
    manifest, quality, bindings = documents()
    bindings[0]["sha256"] = "sha256:" + DIGEST.upper()
    assert freeze(manifest, quality, bindings)["media_bindings"][0]["sha256"] == DIGEST


@pytest.mark.parametrize("selection", [{"kind": kind} for kind in ("image", "audio", "video", "model_3d")]
                         + [{field: mime} for field in ("media_type", "mime_type")
                            for mime in ("image/png", "audio/wav", "video/mp4", "model/gltf-binary")])
def test_kind_or_media_mime_selects_required_bindings(selection):
    manifest, quality, _ = documents()
    manifest["runs"][1]["output_artifacts"] = [{**selection, "object_id": OBJECT_ID, "sha256": DIGEST}]
    with pytest.raises(inputs.FrozenInputError, match="silent card fallback"):
        freeze(manifest, quality, [])


def test_nonmedia_identity_metadata_and_diagnostics_are_preserved():
    manifest, quality, bindings = documents()
    nonmedia = {"kind": "report", "media_type": "application/json", "object_id": "metadata-object",
                "sha256": "b" * 64, "content_sha256": "c" * 64, "digest": "d" * 64,
                "_resolution_error": "report source unavailable", "note": "observed only"}
    manifest["runs"][1]["output_artifacts"].append(nonmedia)
    manifest["runs"][0]["output_artifacts"] = [{"kind": "note", "note": "no output bytes"}]
    frozen = freeze(manifest, quality, bindings)
    kwargs = inputs.assembly_inputs(frozen, receipts(frozen), project=PROJECT, target_run_id=TARGET)
    assert frozen["manifest"] == manifest
    assert kwargs["input_manifest"]["runs"][1]["output_artifacts"][1] == nonmedia
    assert kwargs["input_manifest"]["runs"][0]["output_artifacts"] == manifest["runs"][0]["output_artifacts"]
    assert kwargs["input_quality"] == quality
    assert len(frozen["media_bindings"]) == 1


def test_binding_cannot_materialize_nonmedia_metadata_as_render_media():
    manifest, quality, bindings = documents()
    manifest["runs"][1]["output_artifacts"][0].update({"kind": "report", "media_type": "application/json"})
    bindings[0]["media_type"] = "application/json"
    with pytest.raises(inputs.FrozenInputError, match="select render media"):
        freeze(manifest, quality, bindings)


@pytest.mark.parametrize("filename", [".hidden", "a..png", "..leading", "trailing..", "CON.png", "NUL",
                                      "a:stream", "image with spaces.png", "画像-é.png", "a" * 129,
                                      "a" * 512, "画" * 512])
def test_runtime_safe_flat_transport_filenames_are_accepted(filename):
    manifest, quality, bindings = documents()
    bindings[0]["filename"] = filename
    value = freeze(manifest, quality, bindings)
    assert value["media_bindings"][0]["filename"] == filename
    encoded = inputs.serialize_frozen_inputs(value, project=PROJECT, target_run_id=TARGET)
    assert inputs.parse_frozen_inputs(encoded, project=PROJECT, target_run_id=TARGET) == value


@pytest.mark.parametrize("filename", [None, 123, "", ".", "..", "../a.png", "/a.png", "a/b.png",
                                      "a/../b.png", "a/", "a\\b.png", "a\n.png", "a\t.png", "a\0.png",
                                      "a\x1f.png", "a\x7f.png", "a" * 513, "画" * 513])
def test_unsafe_transport_filenames(filename):
    manifest, quality, bindings = documents()
    bindings[0]["filename"] = filename
    with pytest.raises(inputs.FrozenInputError, match="filename"):
        freeze(manifest, quality, bindings)


def test_parser_nesting_safeguard_is_implementation_safety():
    manifest, quality, bindings = documents()
    nested = "leaf"
    for _ in range(inputs.MAX_DEPTH + 1):
        nested = [nested]
    manifest["supporting_facts"] = nested
    with pytest.raises(inputs.FrozenInputError, match="parser nesting safety"):
        freeze(manifest, quality, bindings)
    value = freeze()
    value["manifest"]["supporting_facts"] = nested
    with pytest.raises(inputs.FrozenInputError, match="parser nesting safety"):
        inputs.parse_frozen_inputs(json.dumps(value).encode(), project=PROJECT, target_run_id=TARGET)


def test_structure_with_many_runs_objects_associations_and_records_has_no_semantic_cap():
    manifest, quality, _ = documents()
    manifest["runs"].extend({"run_id": f"run-{i}", "output_artifacts": []} for i in range(260))
    manifest["authority"]["run_ids"] = [run["run_id"] for run in manifest["runs"]]
    quality["total_runs"] = len(manifest["runs"])
    manifest["quality"] = deepcopy(quality)
    manifest["supporting_facts"] = [None] * 35000
    outputs, bindings = [], []
    for i in range(130):
        digest = hashlib.sha256(f"media-{i}".encode()).hexdigest()
        associations = []
        for _ in range(8):
            associations.append({"project": PROJECT, "run_id": TARGET, "artifact_index": len(outputs)})
            outputs.append({"kind": "image", "object_id": f"sha256:{digest}", "sha256": digest})
        bindings.append({"name": f"image_{i}", "object_id": f"sha256:{digest}", "sha256": digest,
                         "size": SIZE, "media_type": "image/png", "filename": f"image-{i}.png",
                         "associations": associations})
    manifest["runs"][1]["output_artifacts"] = outputs
    value = freeze(manifest, quality, bindings)
    assert value["manifest"] == manifest
    assert len(value["media_bindings"]) == 130
    assert sum(len(binding["associations"]) for binding in value["media_bindings"]) == 1040


def test_object_and_document_byte_overflow(monkeypatch):
    manifest, quality, bindings = documents()
    bindings[0]["size"] = inputs.MAX_OBJECT_BYTES + 1
    with pytest.raises(inputs.FrozenInputError, match="object size"):
        freeze(manifest, quality, bindings)
    monkeypatch.setattr(inputs, "MAX_DOCUMENT_BYTES", 16)
    with pytest.raises(inputs.FrozenInputError, match="document byte"):
        freeze()


def test_document_and_envelope_transport_boundaries_use_actual_serialized_bytes(monkeypatch):
    value = freeze()
    encoded = inputs.serialize_frozen_inputs(value, project=PROJECT, target_run_id=TARGET)
    document_bytes = max(len(json.dumps(value[label], ensure_ascii=False, sort_keys=True,
                                       separators=(",", ":")).encode()) for label in ("manifest", "quality"))
    assert len(encoded) > document_bytes
    monkeypatch.setattr(inputs, "MAX_DOCUMENT_BYTES", len(encoded))
    assert inputs.parse_frozen_inputs(encoded, project=PROJECT, target_run_id=TARGET) == value
    assert freeze() == value
    monkeypatch.setattr(inputs, "MAX_DOCUMENT_BYTES", len(encoded) - 1)
    with pytest.raises(inputs.FrozenInputError, match="serialized envelope byte"):
        inputs.parse_frozen_inputs(encoded, project=PROJECT, target_run_id=TARGET)
    with pytest.raises(inputs.FrozenInputError, match="frozen envelope byte"):
        freeze()
    monkeypatch.setattr(inputs, "MAX_DOCUMENT_BYTES", document_bytes - 1)
    with pytest.raises(inputs.FrozenInputError, match="manifest document byte"):
        freeze()


def test_object_transport_boundary_does_not_reserve_child_bytes():
    manifest, quality, bindings = documents()
    manifest["runs"][1]["output_artifacts"][0]["size"] = inputs.MAX_OBJECT_BYTES
    bindings[0]["size"] = inputs.MAX_OBJECT_BYTES
    assert freeze(manifest, quality, bindings)["media_bindings"][0]["size"] == inputs.MAX_OBJECT_BYTES


@pytest.mark.parametrize("variation", ["missing", "extra", "tampered", "not-mapping", "missing-field",
                                      "extra-field", "relative", "traversal", "wrong-size-type"])
def test_materialized_host_mapping_requires_exact_metadata_and_path_shape(variation):
    value = freeze()
    host = receipts(value)
    if variation == "missing":
        host.clear()
    elif variation == "extra":
        host["other"] = host["image_0"]
    elif variation == "not-mapping":
        host["image_0"] = object()
    else:
        receipt = host["image_0"]
        if variation == "tampered":
            receipt["sha256"] = "b" * 64
        elif variation == "missing-field":
            receipt.pop("filename")
        elif variation == "extra-field":
            receipt["verified"] = True
        elif variation == "wrong-size-type":
            receipt["size"] = True
        else:
            receipt["path"] = "relative.png" if variation == "relative" else "/host/../secret.png"
    with pytest.raises(inputs.FrozenInputError):
        inputs.assembly_inputs(value, host, project=PROJECT, target_run_id=TARGET)


@pytest.mark.parametrize("field,value", [("object_id", "opaque-object"), ("sha256", "b" * 64),
                                         ("size", SIZE + 1), ("media_type", "video/mp4"),
                                         ("filename", "other.png")])
def test_host_mapping_metadata_must_match_admitted_binding(field, value):
    frozen = freeze()
    host = receipts(frozen)
    host["image_0"][field] = value
    with pytest.raises(inputs.FrozenInputError, match="mismatch"):
        inputs.assembly_inputs(frozen, host, project=PROJECT, target_run_id=TARGET)


def test_pathless_registry_has_exact_dependency_set_and_no_locator():
    value = freeze()
    registry = {"assets": {f"asset_{TARGET}_0": {"file": "/host/image.png", "type": "image",
                                                    "duration": 4, "content_sha256": DIGEST}}}
    result = inputs.pathless_render_registry(registry, value, project=PROJECT, target_run_id=TARGET)
    entry = result["assets"][f"asset_{TARGET}_0"]
    assert "file" not in entry
    assert entry["binding"] == "image_0" and entry["object_id"] == OBJECT_ID
    assert registry["assets"][f"asset_{TARGET}_0"]["file"] == "/host/image.png"
    with pytest.raises(inputs.FrozenInputError, match="silent card fallback"):
        inputs.pathless_render_registry({"assets": {}}, value, project=PROJECT, target_run_id=TARGET)
    registry["assets"]["unadmitted"] = entry
    with pytest.raises(inputs.FrozenInputError, match="unadmitted"):
        inputs.pathless_render_registry(registry, value, project=PROJECT, target_run_id=TARGET)


def test_invalid_json_duplicate_fields_nonfinite_unknown_fields_and_resolution_error():
    with pytest.raises(inputs.FrozenInputError):
        inputs.parse_frozen_inputs(b'{"schema_version":1,"schema_version":1}', project=PROJECT, target_run_id=TARGET)
    manifest, quality, bindings = documents()
    manifest["cost_estimate"]["estimated_cost"] = float("nan")
    with pytest.raises(inputs.FrozenInputError, match="finite JSON"):
        freeze(manifest, quality, bindings)
    value = freeze()
    value["credentials"] = "unaccepted"
    with pytest.raises(inputs.FrozenInputError, match="unknown fields"):
        normalize(value)
    manifest, quality, bindings = documents()
    manifest["runs"][1]["output_artifacts"][0]["_resolution_error"] = "missing bytes"
    with pytest.raises(inputs.FrozenInputError, match="resolution error"):
        freeze(manifest, quality, bindings)


def test_adapter_does_not_open_resolve_read_or_inspect_files(monkeypatch):
    value = freeze()
    host = receipts(value)

    def forbidden(*args, **kwargs):
        raise AssertionError("offline adapter attempted filesystem access")

    monkeypatch.setattr(builtins, "open", forbidden)
    for name in ("open", "resolve", "read_bytes", "read_text", "stat", "exists", "is_file", "iterdir"):
        monkeypatch.setattr(Path, name, forbidden)
    encoded = inputs.serialize_frozen_inputs(value, project=PROJECT, target_run_id=TARGET)
    decoded = inputs.parse_frozen_inputs(encoded, project=PROJECT, target_run_id=TARGET)
    assert inputs.assembly_inputs(decoded, host, project=PROJECT, target_run_id=TARGET)["runtime_client"] is None


@pytest.mark.parametrize("include_nonmedia", [False, True])
def test_adapter_kwargs_feed_existing_assemble_without_runtime(tmp_path, include_nonmedia):
    # Coordinator-only execution: this proves the unmodified M09 call boundary.
    from astrid.packs.iteration.actions.assemble import run as assemble

    value = freeze()
    if include_nonmedia:
        value["manifest"]["runs"][1]["output_artifacts"].append({
            "kind": "report", "media_type": "application/json", "object_id": "metadata-object",
            "sha256": "b" * 64, "note": "observed only",
        })
    source = tmp_path / "image-0.png"
    source.write_bytes(b"admitted image")
    binding = value["media_bindings"][0]
    host = {"image_0": {
        "path": str(source), "object_id": binding["object_id"], "sha256": DIGEST,
        "size": SIZE, "media_type": "image/png", "filename": "image-0.png",
    }}
    kwargs = inputs.assembly_inputs(value, host, project=PROJECT, target_run_id=TARGET)
    result = assemble.assemble_iteration(out_path=tmp_path / "assembled", repo_root=tmp_path,
                                         force=True, **kwargs)
    timeline = json.loads(Path(result["timeline_path"]).read_text())
    assert timeline["clips"][0]["clipType"] == "media"
    if include_nonmedia:
        assert len(timeline["clips"]) == 2
        assert timeline["clips"][1]["clipType"] != "media"
        assert result["diagnostics"]
        assert all("renderer-fallback:" in diagnostic for diagnostic in result["diagnostics"])
    else:
        assert result["diagnostics"] == []
    registry = json.loads(Path(result["hype_assets_path"]).read_text())
    transported = inputs.pathless_render_registry(registry, value, project=PROJECT, target_run_id=TARGET)
    assert transported["assets"][f"asset_{TARGET}_0"]["binding"] == "image_0"
    assert "file" not in transported["assets"][f"asset_{TARGET}_0"]
    assert set(transported["assets"]) == {f"asset_{TARGET}_0"}


@pytest.mark.parametrize("field,value", [("project", "other"), ("run_id", "other"), ("kind", [])])
def test_artifact_identity_and_shape(field, value):
    manifest, quality, bindings = documents()
    manifest["runs"][1]["output_artifacts"][0][field] = value
    with pytest.raises(inputs.FrozenInputError):
        freeze(manifest, quality, bindings)


def test_event_projection_omits_credentials_and_payload_before_freezing():
    manifest, quality, bindings = documents()
    raw = {"event_id": "event-1", "aggregate_id": TARGET, "aggregate_type": "run",
           "event_type": "finished", "sequence": 7, "occurred_at": "2026-10-06T00:00:00Z",
           "payload": {"admission_token": "CREDENTIAL_SENTINEL", "message": "MESSAGE_SENTINEL"},
           "result": {"credential": "RESULT_SENTINEL"}, "spec": {"secret": "SPEC_SENTINEL"},
           "unknown": "UNKNOWN_SENTINEL"}
    manifest["runs"][1]["run_events"] = [raw]
    manifest["runs"][1]["task_events"] = {"task-image": [deepcopy(raw)]}
    frozen = freeze(manifest, quality, bindings)
    encoded = inputs.serialize_frozen_inputs(frozen, project=PROJECT, target_run_id=TARGET)
    for forbidden in (b"payload", b"admission_token", b"credential", b"SENTINEL", b"unknown"):
        assert forbidden not in encoded
    event = frozen["manifest"]["runs"][1]["run_events"][0]
    assert event["omitted_field_count"] == 4 and event["projection_version"] == 1
    assert event["event_id"] == "event-1" and event["sequence"] == 7
    assert inputs.parse_frozen_inputs(encoded, project=PROJECT, target_run_id=TARGET) == frozen
    assert frozen["quality"] == quality
    assert raw["payload"]["admission_token"] == "CREDENTIAL_SENTINEL"


@pytest.mark.parametrize("field", ["payload", "result", "spec", "message", "error", "tool_session", "unknown"])
@pytest.mark.parametrize("location", ["run_events", "task_events"])
def test_frozen_parser_rejects_injected_raw_event_fields(field, location):
    frozen = freeze()
    run = frozen["manifest"]["runs"][1]
    if location == "task_events":
        run[location] = {"task-image": [deepcopy(run["run_events"][0])]}
        event = run[location]["task-image"][0]
    else:
        event = run[location][0]
    event[field] = {"admission_token": "CREDENTIAL_SENTINEL"}
    with pytest.raises(inputs.FrozenInputError, match="raw event payload|unknown event fields"):
        inputs.parse_frozen_inputs(json.dumps(frozen).encode(), project=PROJECT, target_run_id=TARGET)


@pytest.mark.parametrize("bad", [
    {"event_type": "first", "type": "second"},
    {"occurred_at": "first", "timestamp": "second"},
    {"timestamp": "first", "created_at": "second"},
    {"event_id": {"admission_token": "SENTINEL"}}, {"sequence": True},
    {"projection_version": 1, "omitted_field_count": 0, "payload": {"secret": "SENTINEL"}},
    {"projection_version": True, "omitted_field_count": 0},
])
def test_event_projection_rejects_alias_conflicts_and_spoofed_forms(bad):
    with pytest.raises(inputs.FrozenInputError):
        inputs.project_event(bad)


def _iteration_run_module(monkeypatch):
    import importlib
    monkeypatch.setenv("ASTRID_INTERNAL_INVOCATION", "1")
    return importlib.import_module("astrid.packs.video_editing.orchestrators.iteration_video.run")


def test_selected_route_hydrates_exact_ids_and_preserves_missing_lineage(monkeypatch):
    from types import SimpleNamespace
    module = _iteration_run_module(monkeypatch)
    calls = []
    run = {"run_id": TARGET, "project_id": PROJECT, "task_ids": ["task-image"], "attempt_id": "attempt-image"}
    task = {"task_id": "task-image", "run_id": TARGET, "project_id": PROJECT, "attempt_id": "attempt-image"}
    def forbidden(*args, **kwargs):
        raise AssertionError("project-wide list was called")
    def show_run(identity):
        calls.append(("runs.show", identity))
        return dict(run)
    def show_task(identity):
        calls.append(("tasks.show", identity))
        return dict(task)
    client = SimpleNamespace(runs=SimpleNamespace(show=show_run, list=forbidden),
                             tasks=SimpleNamespace(show=show_task, list=forbidden))
    records = module._load_runtime_records(client, PROJECT, project_identities={PROJECT}, target_run_id=TARGET)
    assert calls == [("runs.show", TARGET), ("tasks.show", "task-image")]
    nodes = module._collect_runtime_graph(records, TARGET)
    manifest, quality = module._build_runtime_inputs(nodes, target_run_id=TARGET, project_slug=PROJECT)
    assert quality["dimensions"]["lineage"] is False
    assert quality["missing_evidence"] == [TARGET] and quality["missing_receipts"] == [TARGET]
    run["parent_run_ids"] = []
    records = module._load_runtime_records(client, PROJECT, project_identities={PROJECT}, target_run_id=TARGET)
    assert records[TARGET]["runtime_parent_lineage_available"] is True
    del run["task_ids"]
    records = module._load_runtime_records(client, PROJECT, project_identities={PROJECT}, target_run_id=TARGET)
    assert records[TARGET]["runtime_tasks_available"] is False


@pytest.mark.parametrize("field", ["project_id", "run_id", "task_id", "attempt_id"])
def test_selected_task_hydration_rejects_foreign_owner_run_task_attempt(monkeypatch, field):
    from types import SimpleNamespace
    module = _iteration_run_module(monkeypatch)
    run = {"run_id": TARGET, "task_ids": ["task-image"], "attempt_id": "attempt-image"}
    task = {"task_id": "task-image", "run_id": TARGET, "project_id": PROJECT, "attempt_id": "attempt-image"}
    task[field] = "foreign"
    client = SimpleNamespace(tasks=SimpleNamespace(show=lambda identity: task))
    with pytest.raises(module.IterationVideoError, match="identity mismatch"):
        module._runtime_exact_task_records(client, run, project_identities={PROJECT})


def test_evidence_guard_is_terminal_through_pagination_and_latches_before_reads(monkeypatch):
    import importlib.util
    worktree = Path(__file__).resolve().parents[3]
    evidence = worktree.parents[2] / ".otto/runs/pack-authoring-convergence-20261001/evidence/b01/selected-run-closure-02"
    spec = importlib.util.spec_from_file_location("b01_closure_02_guard", evidence / "measurement.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "MAX_METADATA_BYTES", 10)
    meter = module.Meter()
    reads = []
    class Family:
        def list(self, *args, **kwargs):
            reads.append(1)
            return [[{"metadata": "large-response"}], None]
    wrapped = module.MeterFamily(Family(), "runs", meter)
    iteration = _iteration_run_module(monkeypatch)
    assert not issubclass(module.GuardStop, Exception)
    with pytest.raises(module.GuardStop):
        iteration._runtime_paged_read(wrapped.list, PROJECT)
    assert meter.rejected_operation["operation"] == "runs.list"
    assert meter.rejected_operation["row_occurrences"] == 1
    assert meter.rejected_operation["attempted_call_index"] == 1
    assert meter.rejected_operation["accepted_bytes_before_rejection"] == 0
    assert meter.rejected_operation["cumulative_metadata_bytes_lower_bound"] > 10
    assert meter.attempted_calls == 1
    assert meter.rejected_operation["canonical_metadata_bytes_lower_bound"] > 10
    assert meter.calls == [] and meter.metadata_bytes == 0
    with pytest.raises(module.GuardStop):
        wrapped.list(PROJECT)
    assert len(reads) == 1
    with pytest.raises(module.GuardStop):
        iteration._runtime_run_show(type("Client", (), {"runs": type("Runs", (), {"show": wrapped.list})()})(), PROJECT, TARGET)
    assert len(reads) == 1
