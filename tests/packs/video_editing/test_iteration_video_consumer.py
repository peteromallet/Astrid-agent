"""Deterministic staged consumer fixtures; no Runtime or render engine."""
from copy import deepcopy
import hashlib
import importlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from astrid.core.pack.entrypoint import canonical_runtime_entrypoint
from astrid.packs.video_editing.shared import iteration_inputs
from astrid.sdk.results import MaterializedChildOutput

with canonical_runtime_entrypoint("video_editing.iteration_video"):
    consumer = importlib.import_module("astrid.packs.video_editing.actions.iteration_video.run")

PROJECT, TARGET = "consumer-project", "consumer-run"
MEDIA = b"admitted image"
DIGEST = hashlib.sha256(MEDIA).hexdigest()


def envelope():
    quality = {
        "schema_version": 1, "target_run_id": TARGET, "total_runs": 1,
        "data_quality": 1.0,
        "dimensions": {key: True for key in ("lineage", "task_outputs", "evidence", "relations", "receipts", "events")},
        **{key: [] for key in ("unresolved_producer_runs", "missing_lineage", "missing_task_outputs", "missing_evidence", "missing_relations", "missing_receipts", "missing_events", "unavailable_sources")},
        **{key: 0 for key in ("relation_count", "evidence_count", "receipt_count", "run_event_count", "task_event_count")},
        "events_role": "observational_only", "authority": {"kind": "runtime", "project": PROJECT},
    }
    artifact = {"kind": "image", "object_id": "sha256:" + DIGEST, "sha256": DIGEST,
                "size": len(MEDIA), "media_type": "image/png", "duration": 4,
                "task_id": "source-task", "output_id": "source-output",
                "source_association_id": "source-association", "role": "result"}
    manifest = {
        "schema_version": 1, "target_run_id": TARGET,
        "runs": [{"run_id": TARGET, "label": "target", "causal_depth": 0, "selection_order": 0,
                  "parent_run_ids": [], "unresolved_parent_run_ids": [], "lineage_incomplete": False,
                  "task_ids": ["source-task"], "output_artifacts": [artifact], "relations": [],
                  "evidence": [], "receipts": [], "run_events": [], "task_events": {},
                  "lineage_gaps": [], "summary": None}],
        "quality": deepcopy(quality), "summary_cache": {"hits": 0, "misses": 0},
        "cost_estimate": {"summarize_calls": 0, "estimated_cost": 0},
        "authority": {"kind": "runtime", "project": PROJECT, "run_ids": [TARGET]},
    }
    binding = {"name": "image_0", "object_id": "sha256:" + DIGEST, "sha256": DIGEST,
               "size": len(MEDIA), "media_type": "image/png", "filename": "admitted.png",
               "associations": [{"project": PROJECT, "run_id": TARGET, "artifact_index": 0,
                                 "task_id": "source-task", "output_id": "source-output",
                                 "source_association_id": "source-association"}]}
    return iteration_inputs.freeze_inputs(manifest, quality, [binding], project=PROJECT, target_run_id=TARGET)


@pytest.fixture
def harness(tmp_path, monkeypatch):
    frozen_path, media_path, root = tmp_path / "frozen.json", tmp_path / "media.png", tmp_path / "out"
    frozen_path.write_bytes(iteration_inputs.serialize_frozen_inputs(envelope(), project=PROJECT, target_run_id=TARGET))
    media_path.write_bytes(MEDIA)
    calls = []
    assembly_calls = []
    payloads = {"video": b"rendered video", "provenance": b'{"rendering":"original provenance"}'}
    rows = [{"association_id": "child-" + port, "output_port": port,
             "object_id": "sha256:" + hashlib.sha256(data).hexdigest(),
             "digest": "sha256:" + hashlib.sha256(data).hexdigest(), "size": len(data),
             "media_type": "video/mp4" if port == "video" else "application/json"}
            for port, data in payloads.items()]

    def assemble(**kwargs):
        assert kwargs["runtime_client"] is None and kwargs["runtime_project"] is None
        assembly_calls.append(kwargs)
        assert kwargs["input_quality"] == envelope()["quality"]
        artifact = kwargs["input_manifest"]["runs"][0]["output_artifacts"][0]
        assert Path(artifact["path"]).read_bytes() == MEDIA
        assembly = kwargs["out_path"]
        assembly.mkdir(parents=True)
        timeline, assets = assembly / "timeline.json", assembly / "assets.json"
        timeline.write_text('{"schema_version":1,"tracks":[]}')
        assets.write_text(json.dumps({"assets": {f"asset_{TARGET}_0": {
            "file": artifact["path"], "content_sha256": DIGEST, "type": "image"}}}))
        return {"hype_timeline_path": str(timeline), "hype_assets_path": str(assets)}

    def materialize(association_id):
        row = next(row for row in rows if row["association_id"] == association_id)
        relative = "child-outputs/" + association_id + "/output"
        path = root / relative
        path.parent.mkdir(parents=True)
        path.write_bytes(payloads[row["output_port"]])
        return MaterializedChildOutput(output=row, filename=relative)

    result = SimpleNamespace(ok=True, outputs={"managed_outputs": rows}, materialize_output=materialize)
    def invoke(capability, **kwargs):
        calls.append((capability, kwargs))
        return result
    monkeypatch.setattr(consumer, "assemble_iteration", assemble)
    monkeypatch.setattr(consumer, "invoke", invoke)
    return SimpleNamespace(frozen=frozen_path, media=media_path, root=root, calls=calls,
                           assembly_calls=assembly_calls,
                           result=result, rows=rows, payloads=payloads,
                           run=lambda **kwargs: consumer.run(project_id=PROJECT, target_run_id=TARGET,
                               frozen_inputs=frozen_path, media_dependency=media_path, out=root, **kwargs))


@pytest.mark.parametrize("with_theme", [False, True])
def test_offline_assembly_exact_public_render_request_and_preserved_outputs(harness, with_theme):
    theme = harness.frozen.parent / "theme.json"
    theme.write_text('{"palette":"supplied"}')
    outputs = harness.run(**({"theme": theme} if with_theme else {}))
    capability, request = harness.calls[0]
    assert capability == "rendering.render"
    assert request["kind"] == "action" and request["wait"] is True
    assert request["child_key"] == "iteration-render"
    assert set(request) == {"kind", "inputs", "child_key", "wait"}
    supplied = request["inputs"]
    assert supplied["output_name"] == "iteration.mp4"
    assert set(supplied) == {"timeline", "assets_registry", "media_dependency", "output_name"} | ({"theme"} if with_theme else set())
    assert supplied["media_dependency"] == {"filename": "render-inputs/media/admitted.png", "output_port": "media_dependency", "media_type": "image/png"}
    registry = json.loads((harness.root / supplied["assets_registry"]["filename"]).read_bytes())
    asset = registry["assets"][f"asset_{TARGET}_0"]
    assert asset["object_id"] == "sha256:" + DIGEST and asset["binding"] == "media_dependency"
    assert not {"file", "path", "url", "locator"} & set(asset)
    if with_theme:
        assert (harness.root / supplied["theme"]["filename"]).read_bytes() == theme.read_bytes()
    for port, data in harness.payloads.items():
        assert Path(outputs[port]).read_bytes() == data
    assert Path(outputs["video"]).name == "iteration.mp4"
    assert Path(outputs["provenance"]).name == "iteration.mp4.provenance.json"


def test_force_is_forwarded_as_a_boolean_assembly_policy(harness):
    outputs = harness.run(force=True)
    assert set(outputs) == {"video", "provenance"}
    assert len(harness.assembly_calls) == 1
    assert harness.assembly_calls[0]["force"] is True
    with pytest.raises(consumer.IterationVideoError, match="force must be a boolean"):
        harness.run(force="true")


@pytest.mark.parametrize("failure", ["project", "target", "tampered-media", "empty-bindings", "two-bindings", "malformed-json"])
def test_invalid_frozen_or_media_identity_fails_before_public_invocation(harness, failure):
    frozen = envelope()
    if failure in {"project", "target"}:
        frozen["project" if failure == "project" else "target_run_id"] = "foreign"
    elif failure == "tampered-media":
        harness.media.write_bytes(b"tampered image")
    elif failure == "empty-bindings":
        frozen["manifest"]["runs"][0]["output_artifacts"] = []
        frozen["media_bindings"] = []
    elif failure == "two-bindings":
        artifact = deepcopy(frozen["manifest"]["runs"][0]["output_artifacts"][0])
        artifact.update(object_id="sha256:" + "b" * 64, sha256="b" * 64, source_association_id="second")
        frozen["manifest"]["runs"][0]["output_artifacts"].append(artifact)
        binding = deepcopy(frozen["media_bindings"][0])
        binding.update(name="image_1", filename="second.png", object_id="sha256:" + "b" * 64, sha256="b" * 64)
        binding["associations"][0].update(artifact_index=1, source_association_id="second")
        frozen["media_bindings"].append(binding)
    harness.frozen.write_text("{" if failure == "malformed-json" else json.dumps(frozen))
    with pytest.raises((consumer.IterationVideoError, iteration_inputs.FrozenInputError)):
        harness.run()
    assert harness.calls == []


@pytest.mark.parametrize("failure", ["failed-child", "missing-video", "missing-provenance", "duplicate-video", "missing-managed-outputs"])
def test_missing_or_ambiguous_render_outputs_fail(harness, failure):
    if failure == "failed-child":
        harness.result.ok = False
    elif failure == "missing-managed-outputs":
        harness.result.outputs = {}
    else:
        rows = harness.result.outputs["managed_outputs"]
        if failure == "duplicate-video":
            rows.append(deepcopy(rows[0]))
        else:
            port = failure.removeprefix("missing-")
            rows[:] = [row for row in rows if row["output_port"] != port]
    with pytest.raises(consumer.IterationVideoError):
        harness.run()


@pytest.mark.parametrize("failure", ["descriptor-conflict", "traversal", "missing-file", "changed-bytes"])
def test_materialized_output_cannot_replace_identity_or_escape_attempt(harness, failure):
    original = harness.result.materialize_output
    def altered(association_id):
        local = original(association_id)
        if failure == "descriptor-conflict":
            return MaterializedChildOutput(output={**local.output, "size": 999}, filename=local.filename)
        if failure == "traversal":
            return MaterializedChildOutput(output=local.output, filename="../outside")
        path = harness.root / local.filename
        if failure == "missing-file":
            path.unlink()
        else:
            path.write_bytes(b"changed")
        return local
    harness.result.materialize_output = altered
    with pytest.raises((consumer.IterationVideoError, OSError)):
        harness.run()
