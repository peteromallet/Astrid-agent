"""Pre-admission selection and finite reads through the real generated client."""
from copy import deepcopy
import hashlib
import io
import json
from types import SimpleNamespace
import urllib.error
import urllib.request

import pytest

from astrid.packs.video_editing.actions.iteration_video import admission
from astrid.packs.video_editing.shared.iteration_inputs import parse_frozen_inputs
from astrid.sdk.remote import RemoteAstridClient
from astrid.sdk.workspace_client import WorkspaceClient
from banodoco_workspace_client.generated import ApiError, ManagedOutput
from tests.packs.video_editing.test_iteration_video_consumer import envelope, PROJECT, TARGET, DIGEST, MEDIA


def digest(data):
    return "sha256:" + hashlib.sha256(data).hexdigest()


@pytest.fixture
def selected(monkeypatch):
    frozen = envelope()
    definitions = {name: {"id": name, "fixture": "pinned declaration"}
                   for name in (admission.ROOT, admission.RENDER)}
    pins = {name: {"capability_id": name, "capability_digest": digest(admission._canonical(definition))}
            for name, definition in definitions.items()}
    selection = {"project_id": PROJECT, "run_id": TARGET, "task_id": "source-task",
                 "attempt_id": "historical-attempt", "association_id": "source-association", "artifact_index": 0}
    grant = {**{key: selection[key] for key in ("project_id", "run_id", "task_id", "attempt_id")},
             **pins[admission.ROOT], "limits": {"max_discovery_rows": 750,
             "max_discovery_metadata_bytes": 67108864, "max_selected_output_objects": 2,
             "max_selected_output_bytes": 16777216, "max_child_media_bindings": 1,
             "max_child_media_bytes": 16777216}}
    row = {**{key: selection[key] for key in selection if key != "artifact_index"},
           "output_id": "source-output", "object_id": "sha256:" + DIGEST, "digest": "sha256:" + DIGEST,
           "size": len(MEDIA), "media_type": "image/png", "filename": "admitted.png",
           "output_port": "image", "group_key": "default", "variant_key": "default", "ordinal": 0,
           "role": "result", "durability": "durable", "state": "available", "version": 1}
    catalog = [{"capability_id": name, "definition_digest": pin["capability_digest"], "status": "ready"}
               for name, pin in pins.items()]
    responses = {
        "/v1/projects/" + PROJECT: {"project_id": PROJECT, "realm_id": "realm", "name": "Fixture",
            "version": 1, "created_at": "now", "updated_at": "now"},
        "/v1/runs/" + TARGET: {"run_id": TARGET, "project_id": PROJECT, "task_ids": ["source-task"]},
        "/v1/tasks/source-task": {"task_id": "source-task", "run_id": TARGET, "project_id": PROJECT,
            "attempt_id": "newer-current-attempt", "state": "completed", "version": 1,
            "capability_id": "fixture.source", "capability_digest": digest(b"source"),
            "idempotency_key": "source", "created_at": "now", "updated_at": "now", "runtime_epoch": 1},
        "/v1/managed-outputs/source-association": row,
        "/v1/capabilities": {"items": catalog, "next_cursor": None},
    }
    f = SimpleNamespace(calls=[], uploads=[], invocations=[], responses=responses, row=row,
                        definitions=definitions, catalog=catalog, ingestion_change={}, fail_path=None)

    def transport(method, path, headers, body):
        assert headers["Authorization"] == "Bearer explicit-auth"
        f.calls.append((method, path))
        if path == f.fail_path:
            return 404, {}, b'{"code":"not_found","message":"missing association"}'
        if method == "POST":
            assert path == "/v1/projects/" + PROJECT + "/objects"
            f.uploads.append(bytes(body))
            result = {"object_id": digest(body), "digest": digest(body), "size": len(body), "project": PROJECT}
            return 200, {}, json.dumps({"data": {**result, **f.ingestion_change}, "receipt": {"fixture": "committed"}}).encode()
        return 200, {}, json.dumps(responses[path], ensure_ascii=False).encode()

    transport_client = WorkspaceClient("http://127.0.0.1:1", "explicit-auth")
    transport_client._generated._transport = transport
    f.client = RemoteAstridClient(transport_client)
    monkeypatch.setattr(admission.astrid, "get_capability", lambda name, **kwargs:
                        SimpleNamespace(id=name, definition=definitions[name]))
    f.result = object()

    def invoke(name, **kwargs):
        f.invocations.append((name, kwargs))
        return f.result

    monkeypatch.setattr(admission.astrid, "invoke", invoke)
    f.kwargs = {"client": f.client, "selection": selection, "manifest": frozen["manifest"],
        "quality": frozen["quality"], "discovery_grant": grant,
        "parent_capability": pins[admission.ROOT], "rendering_capability": pins[admission.RENDER],
        "rendering_policy": {"capabilities": [pins[admission.RENDER]], "targets": [{"kind": "default"}],
                             "input_object_ids": [], "limits": {"max_children": 1}}}
    f.run = lambda: admission.admit_iteration_video(**f.kwargs)
    return f


def test_exact_frozen_descriptor_policy_and_historical_identity(selected):
    f = selected
    before = deepcopy({key: value for key, value in f.kwargs.items() if key != "client"})
    shared = dict(vars(f.client._transport._generated))
    assert isinstance(f.client._transport._generated.get_managed_output("source-association"), ManagedOutput)
    assert f.run() is f.result
    assert len(f.uploads) == len(f.invocations) == 1
    name, kwargs = f.invocations[0]
    assert name == admission.ROOT and kwargs["client"] is f.client
    assert set(kwargs["inputs"]) == {"project_id", "target_run_id", "frozen_inputs"}
    assert kwargs["child_delegation"] == {**before["rendering_policy"], "discovery_grant": before["discovery_grant"]}
    data = f.uploads[0]
    assert kwargs["inputs"]["frozen_inputs"] == {"object_id": digest(data), "digest": digest(data),
        "size": len(data), "filename": "iteration-frozen.json", "media_type": "application/json"}
    frozen = parse_frozen_inputs(data, project=PROJECT, target_run_id=TARGET)
    assert frozen["manifest"] == before["manifest"] and frozen["quality"] == before["quality"]
    assert frozen["media_bindings"][0]["associations"][0]["source_association_id"] == f.row["association_id"]
    assert vars(f.client._transport._generated) == shared
    assert {path for method, path in f.calls if method == "POST"} == {"/v1/projects/" + PROJECT + "/objects"}


def test_force_is_explicit_root_input_and_strictly_typed(selected):
    f = selected
    f.kwargs["force"] = True
    assert f.run() is f.result
    assert f.invocations[0][1]["inputs"]["force"] is True
    assert len(f.uploads) == 1

    f.calls.clear()
    f.uploads.clear()
    f.invocations.clear()
    f.kwargs["force"] = "false"
    with pytest.raises(admission.IterationAdmissionError, match="force must be a boolean"):
        f.run()
    assert not f.calls and not f.uploads and not f.invocations


@pytest.mark.parametrize("resource,field", [
    ("project", "project_id"), ("run", "run_id"), ("run", "project_id"), ("run", "task_ids"),
    ("task", "task_id"), ("task", "run_id"), ("task", "project_id"),
    *( ("association", field) for field in ("project_id", "run_id", "task_id", "attempt_id", "association_id")),
])
def test_foreign_or_mismatched_metadata_stops_before_mutations(selected, resource, field):
    f = selected
    paths = {"project": "/v1/projects/" + PROJECT, "run": "/v1/runs/" + TARGET,
             "task": "/v1/tasks/source-task", "association": "/v1/managed-outputs/source-association"}
    f.responses[paths[resource]][field] = [] if field == "task_ids" else "foreign"
    with pytest.raises(admission.IterationAdmissionError):
        f.run()
    assert not f.uploads and not f.invocations


@pytest.mark.parametrize("field,value", [("attempt_id", "foreign"), ("project_id", "foreign"),
    ("capability_digest", digest(b"wrong")), ("limits", {}), ("run_id", "*"), ("limits", {"max_discovery_rows": True})])
def test_malformed_or_mismatched_grant_precedes_reads(selected, field, value):
    selected.kwargs["discovery_grant"][field] = value
    with pytest.raises(ValueError):
        selected.run()
    assert not selected.calls and not selected.invocations


@pytest.mark.parametrize("fault", ["local-root", "local-render", "catalog-root", "catalog-render", "policy-render", "duplicate-grant"])
def test_capability_mismatch_precedes_ingestion_and_admission(selected, fault):
    f = selected
    if fault.startswith("local"):
        f.definitions[admission.ROOT if fault.endswith("root") else admission.RENDER]["fixture"] = "changed"
    elif fault.startswith("catalog"):
        f.catalog[0 if fault.endswith("root") else 1]["definition_digest"] = digest(b"wrong")
    elif fault == "policy-render":
        f.kwargs["rendering_policy"]["capabilities"] = []
    else:
        f.kwargs["rendering_policy"]["discovery_grant"] = f.kwargs["discovery_grant"]
    with pytest.raises(ValueError):
        f.run()
    assert not f.uploads and not f.invocations


@pytest.mark.parametrize("field,value", [("object_id", digest(b"wrong")), ("digest", digest(b"wrong")),
    ("size", 1), ("size", True), ("project", "foreign")])
def test_changed_ingestion_identity_stops_parent_admission(selected, field, value):
    selected.ingestion_change[field] = value
    with pytest.raises(admission.IterationAdmissionError, match="ingested frozen CAS identity"):
        selected.run()
    assert len(selected.uploads) == 1 and not selected.invocations


@pytest.mark.parametrize("fault", ["missing-association", "metadata-bytes", "metadata-rows", "object-digest", "media-budget", "document-identity"])
def test_selection_metadata_and_document_failure_stops_admission(selected, fault):
    f = selected
    if fault == "missing-association":
        f.fail_path = "/v1/managed-outputs/source-association"
    elif fault == "metadata-bytes":
        f.responses["/v1/projects/" + PROJECT]["unknown"] = "x" * 2000
        f.kwargs["discovery_grant"]["limits"]["max_discovery_metadata_bytes"] = 1000
    elif fault == "metadata-rows":
        f.kwargs["discovery_grant"]["limits"]["max_discovery_rows"] = 1
    elif fault == "object-digest":
        f.row["object_id"] = digest(b"wrong")
    elif fault == "media-budget":
        f.kwargs["discovery_grant"]["limits"]["max_selected_output_bytes"] = 1
    else:
        f.kwargs["manifest"]["runs"][0]["output_artifacts"][0]["source_association_id"] = "caller-claim"
    with pytest.raises((ValueError, ApiError, RuntimeError)):
        f.run()
    assert not f.uploads and not f.invocations


def test_replay_uses_completed_remote_admission_identity(selected, monkeypatch):
    f = selected
    admissions = []
    monkeypatch.setattr(f.client._transport, "admit_task", lambda **kwargs:
        admissions.append(deepcopy(kwargs)) or {"task_id": kwargs["idempotency_key"]})

    def invoke(name, **kwargs):
        return f.client.tasks.create(project_id=kwargs["project"], capability=name,
            spec={"inputs": kwargs["inputs"]}, input_manifest=[kwargs["inputs"]["frozen_inputs"]["object_id"]],
            child_delegation=kwargs["child_delegation"], deterministic_idempotency=True)

    monkeypatch.setattr(admission.astrid, "invoke", invoke)
    first = f.run()
    replay = f.run()
    assert first.ok and replay.ok and admissions[0] == admissions[1]
    f.kwargs["quality"]["data_quality"] = 0.2
    f.kwargs["quality"]["unavailable_sources"] = ["honest missing evidence"]
    f.kwargs["manifest"]["quality"] = deepcopy(f.kwargs["quality"])
    assert f.run().ok
    assert admissions[2]["idempotency_key"] != admissions[0]["idempotency_key"]
    changed = admissions[2]["idempotency_key"]
    f.kwargs["rendering_policy"]["limits"]["max_children"] = 2
    assert f.run().ok and admissions[3]["idempotency_key"] != changed
    # A second exact historical association to the same CAS object changes the
    # envelope's selected identity even when its media bytes are identical.
    f.row["association_id"] = "other-association"
    f.responses["/v1/managed-outputs/other-association"] = f.row
    f.kwargs["selection"]["association_id"] = "other-association"
    f.kwargs["manifest"]["runs"][0]["output_artifacts"][0]["source_association_id"] = "other-association"
    assert f.run().ok and admissions[4]["idempotency_key"] != admissions[3]["idempotency_key"]


@pytest.mark.parametrize("http_error", [False, True])
def test_production_reader_finite_sentinel_reads_and_terminal_failure(selected, monkeypatch, http_error):
    f = selected
    generated = f.client._transport._generated
    generated._transport = None
    body = b'{"unknown":"' + b"x" * 500 + b'"}'
    requests, sizes = [], []

    class Stream(io.BytesIO):
        status, headers = 200, {}
        def read(self, size=-1):
            assert size > 0
            sizes.append(size)
            return super().read(min(size, 7))

    stream = Stream(body)
    def open_response(request, timeout):
        requests.append(request.full_url)
        if http_error:
            raise urllib.error.HTTPError(request.full_url, 400, "Bad", {}, stream)
        return stream

    monkeypatch.setattr(urllib.request, "urlopen", open_response)
    reader = admission._SelectedMetadata(generated, {"max_discovery_metadata_bytes": 32, "max_discovery_rows": 5})
    with pytest.raises(admission.IterationAdmissionError, match="byte budget"):
        reader.read("get_project", PROJECT)
    assert stream.closed
    assert sum(min(size, 7) for size in sizes) == 33 and sizes[0] == 33
    with pytest.raises(admission.IterationAdmissionError, match="already ended"):
        reader.read("get_project", PROJECT)
    assert len(requests) == 1 and not f.uploads and not f.invocations
