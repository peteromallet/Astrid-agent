"""F05/M21 composed offline proof, using the existing isolated D18 Runtime.

One process shares immutable source media, definitions and frozen documents;
each case gets a separately admitted parent attempt and confined bridge. The
real M21 main/assembly and SDK invocation run in process. Only the rendering
executor is substituted with finite bytes settled through Runtime's public
admission/claim/settlement APIs. This is not a live witness or manifest cutover.
"""
from __future__ import annotations

import base64
from copy import deepcopy
import importlib
import json
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, unquote, urlsplit

import pytest
import yaml

from astrid.core.execution._child_bridge import HostChildBridge
from astrid.core.execution.generic_host import (
    GenericPackHost, HostError, _iteration_video_discovery,
)
from astrid.core.pack.entrypoint import canonical_runtime_entrypoint
from astrid.packs.video_editing.shared import iteration_inputs
from astrid.packs.video_editing.actions.iteration_video.admission import admit_iteration_video
from astrid.sdk import _child_bridge as sdk_bridge
from astrid.sdk.remote import RemoteAstridClient
from astrid.sdk.workspace_client import WorkspaceClient
from tests.core.execution.test_generic_host_child_bridge_d18 import SECRET, digest, world
from tests.packs.video_editing.test_iteration_video_consumer import envelope


ROOT = "video_editing.iteration_video"
RENDER = "rendering.render"
SOURCE = "fixture.iteration_source"
MEDIA = b"admitted image"
OUTPUTS = {
    "video": (b"deterministic rendered video", "video/mp4", "iteration.mp4"),
    "provenance": (b'{"rendering":"preserved provenance"}', "application/json", "iteration.mp4.provenance.json"),
}


def _lease(claim):
    return {key: claim[key] for key in ("lease_id", "fence", "runtime_epoch")}


def _claim(world, capability, key):
    claim = world.service.claim_next(
        {"executor_id": "worker", "capability_ids": [capability], "runtime_epoch": 1,
         "target": world.identity["execution_binding"]["actual"]},
        idempotency_key=key, identity=world.identity,
    )
    assert claim, f"no claim for {capability}/{key}"
    world.client._attempt_runtime_epochs[claim["attempt_id"]] = claim["runtime_epoch"]
    return claim


def _settle(world, claim, outputs, key):
    return world.service.settle_attempt(
        claim["attempt_id"], {**_lease(claim), "outputs": [
            {"name": port, "digest": digest(data), "data_base64": base64.b64encode(data).decode(),
             "media_type": media_type, "filename": filename}
            for port, (data, media_type, filename) in outputs.items()
        ]}, idempotency_key=key, identity=world.identity,
    )


def _setup(world, monkeypatch):
    """Reuse the realm/client lifecycle; extend the ordinary admission routes."""
    original_transport = world.transport

    def transport(method, path, headers, body):
        # Generated client path parts are escaped; the real HTTP dispatcher
        # decodes them before resource lookup. Mirror that in this adapter.
        path = unquote(path)
        parts = path.split("/")
        if method == "POST" and path == "/v1/handshake":
            with world.lock:
                assert headers.get("Authorization") == "Bearer " + SECRET
                payload = json.loads(body)
                world.calls.append((method, path, deepcopy(payload)))
                value = world.service.handshake({**payload, "authenticated_actor": world.identity["actor"],
                                                  "authenticated_scopes": world.identity["scopes"]})
                return 200, {}, json.dumps(value).encode()
        if method == "POST" and path == "/v1/tasks":
            with world.lock:
                assert headers.get("Authorization") == "Bearer " + SECRET
                payload, key = json.loads(body), headers["Idempotency-Key"]
                world.calls.append((method, path, deepcopy(payload)))
                value = world.service.create_task({**payload, "idempotency_key": key}, enforce_readiness=True)
                project = value["run"]["project_id"]
                response = {"data": world.service._task_resource(value),
                    "receipt": world.service.committed_receipt("task.create", project, key, project_id=project)}
                return 201, {}, json.dumps(response).encode()
        if method == "GET" and urlsplit(path).path == "/v1/capabilities":
            with world.lock:
                assert headers.get("Authorization") == "Bearer " + SECRET
                world.calls.append((method, path, None))
                query = parse_qs(urlsplit(path).query)
                value = world.service.list_capabilities(limit=int(query.get("limit", ["50"])[0]),
                                                       cursor=query.get("cursor", [None])[0])
                return 200, {}, json.dumps(value).encode()
        if method == "POST" and path == "/v1/projects/" + world.project + "/objects":
            with world.lock:
                assert headers.get("Authorization") == "Bearer " + SECRET
                world.calls.append((method, path, None))
                value = world.service.ingest(world.project, body, media_type=headers["Content-Type"],
                    original_name=headers.get("X-Original-Name"), idempotency_key=headers["Idempotency-Key"])
                return 200, {}, json.dumps(value).encode()
        if method == "GET" and len(parts) == 4 and parts[2] in {"projects", "runs"}:
            with world.lock:
                assert headers.get("Authorization") == "Bearer " + SECRET
                world.calls.append((method, path, None))
                value = (world.service._project_resource(world.service.get_project(parts[3]))
                         if parts[2] == "projects" else world.service.run(parts[3]))
                return 200, {}, json.dumps(value).encode()
        return original_transport(method, path, headers, body)

    monkeypatch.setattr(world.client.generated, "_transport", transport)
    connection = WorkspaceClient("http://127.0.0.1:1", SECRET)
    connection._generated = world.client.generated
    caller = RemoteAstridClient(connection)
    # Public iteration declaration is intentionally a later gate. This local
    # staged declaration points at the actual M21 command, without publishing it.
    staged = world.root / "video_editing"
    staged.mkdir()
    argv = ["{python_exec}", "-m", "astrid.packs.video_editing.actions.iteration_video.run"]
    for name in ("project_id", "target_run_id", "frozen_inputs", "media_dependency", "out"):
        argv.extend(["--" + name.replace("_", "-"), "{" + name + "}"])
    (staged / "pack.yaml").write_text(yaml.safe_dump({
        "schema_version": 3, "id": "video_editing", "name": "Staged iteration proof", "version": "1.0.0",
        "actions": {"iteration_video": {
            "description": "Test-only staged M21 command",
            "invocation": {"kind": "command", "command": {
                "argv": argv,
                "input_args": [{"input": "force", "flag": "--force", "optional": True}],
            }},
            "inputs": [{"name": name, "type": kind, "required": required}
                       for name, kind, required in (
                           ("project_id", "string", True), ("target_run_id", "string", True),
                           ("frozen_inputs", "file", True), ("media_dependency", "file", False),
                           ("theme", "file", False), ("force", "boolean", False))],
        }},
    }))
    rendering = Path(__file__).resolve().parents[3] / "astrid/packs/rendering"
    roots = (str(staged), str(rendering))
    host = GenericPackHost(pack_roots=roots, client=world.client, executor_id="worker", max_concurrency=2)
    host.discover()
    for capability in (ROOT, RENDER):
        world.service.register_capability({"capability_id": capability,
            "definition_digest": host.capabilities[capability].capability_digest})
    world.service.register_capability({"capability_id": SOURCE, "definition_digest": digest(SOURCE.encode())})
    world.service.register_executor({"executor_id": "worker", "capabilities": [ROOT, RENDER, SOURCE],
                                    "max_concurrency": 2}, idempotency_key="iteration-register")
    # Reuse the accepted public registry composition and its complete graph.
    # The root is staged locally and enters through ordinary SDK admission.
    import astrid.sdk as sdk
    registries = sdk._load_registries(extra_pack_roots=roots)
    monkeypatch.setattr(sdk, "_load_registries", lambda **kwargs: registries)
    world.service.create_task({"project": world.project, "capability_id": SOURCE,
        "capability_digest": digest(SOURCE.encode()), "idempotency_key": "iteration-source",
        "execution_request": {"schema_version": 1, "target": world.identity["execution_binding"]["actual"]}},
        enforce_readiness=True)
    source_claim = _claim(world, SOURCE, "iteration-source-claim")
    _settle(world, source_claim, {"image": (MEDIA, "image/png", "admitted.png")}, "iteration-source-settle")
    source = world.service.managed_outputs(source_claim["task_id"])[0]
    frozen = envelope()
    target = source["run_id"]
    quality = frozen["quality"]
    quality.update(target_run_id=target, authority={"kind": "runtime", "project": world.project})
    manifest = frozen["manifest"]
    manifest.update(target_run_id=target, quality=deepcopy(quality),
                    authority={"kind": "runtime", "project": world.project, "run_ids": [target]})
    run = manifest["runs"][0]
    run.update(run_id=target, task_ids=[source["task_id"]])
    association = {"project": world.project, "run_id": target, "artifact_index": 0,
                   "task_id": source["task_id"], "source_association_id": source["association_id"]}
    if "output_id" in source:
        association["output_id"] = source["output_id"]
    run["output_artifacts"] = [{"kind": "image", "object_id": source["object_id"],
        "sha256": source["digest"][7:], "size": source["size"], "media_type": source["media_type"],
        "duration": 4, **{key: value for key, value in association.items()
                         if key in {"task_id", "source_association_id", "output_id"}}}]
    binding = {"name": "image_0", "object_id": source["object_id"], "sha256": source["digest"][7:],
        "size": source["size"], "media_type": source["media_type"], "filename": "admitted.png",
        "associations": [association]}
    frozen = iteration_inputs.freeze_inputs(manifest, quality, [binding], project=world.project, target_run_id=target)
    grant = {"project_id": world.project, "run_id": target, "task_id": source["task_id"],
             "attempt_id": source_claim["attempt_id"], "capability_id": ROOT,
             "capability_digest": host.capabilities[ROOT].capability_digest,
             "limits": {"max_discovery_rows": 750, "max_discovery_metadata_bytes": 67108864,
                        "max_selected_output_objects": 2, "max_selected_output_bytes": 16777216,
                        "max_child_media_bindings": 1, "max_child_media_bytes": 16777216}}
    with canonical_runtime_entrypoint(ROOT):
        consumer = importlib.import_module("astrid.packs.video_editing.actions.iteration_video.run")
    return SimpleNamespace(host=host, source=source, target=target, frozen=frozen, grant=grant,
                           consumer=consumer, caller=caller, roots=roots)


def _attempt(world, shared, case, monkeypatch):
    frozen = deepcopy(shared.frozen)
    if case == "foreign-project":
        frozen["project"] = "foreign"
    elif case == "foreign-target":
        frozen["target_run_id"] = "foreign"
    elif case == "foreign-association":
        frozen["media_bindings"][0]["associations"][0]["source_association_id"] = "foreign"
    elif case == "binding-size":
        frozen["media_bindings"][0]["size"] += 1
    import astrid
    ordinary_invoke = astrid.invoke
    root_pin = {"capability_id": ROOT, "capability_digest": shared.host.capabilities[ROOT].capability_digest}
    render_pin = {"capability_id": RENDER, "capability_digest": shared.host.capabilities[RENDER].capability_digest}
    # Distinct honest document content gives each case its own deterministic
    # admission identity while exact replay remains tested by the helper suite.
    manifest = deepcopy(shared.frozen["manifest"])
    manifest["integration_case"] = case

    def admission_fault(name, **kwargs):
        # Preserve downstream malformed-envelope coverage with an explicit
        # fault at the SDK boundary, after the helper verified honest input.
        # These documents are fixture attacks, never helper-produced envelopes.
        if case in {"foreign-project", "foreign-target", "foreign-association", "binding-size"}:
            data = json.dumps(frozen, sort_keys=True, separators=(",", ":")).encode()
            world.service.ingest(world.project, data, media_type="application/json", original_name="fault.json",
                                 idempotency_key="fault-" + case)
            kwargs["inputs"]["frozen_inputs"] = {"object_id": digest(data), "digest": digest(data),
                "size": len(data), "filename": "fault.json", "media_type": "application/json"}
        return ordinary_invoke(name, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(astrid, "invoke", admission_fault)
        result = admit_iteration_video(client=shared.caller,
            selection={**{key: shared.grant[key] for key in ("project_id", "run_id", "task_id", "attempt_id")},
                       "association_id": shared.source["association_id"], "artifact_index": 0},
            manifest=manifest, quality=shared.frozen["quality"], discovery_grant=shared.grant,
            parent_capability=root_pin, rendering_capability=render_pin,
            rendering_policy={"capabilities": [render_pin], "targets": [{"kind": "default"}],
                              "input_object_ids": [], "limits": {"max_children": 1}},
            extra_pack_roots=shared.roots,
            execution_request={"schema_version": 1, "target": world.identity["execution_binding"]["actual"]})
    assert result.ok, json.dumps(result.raw_result, sort_keys=True)
    claim = _claim(world, ROOT, "iteration-parent-claim-" + case)
    assert claim["task_id"] == result.kernel_task_id
    task = world.service._task_resource(world.service.task(claim["task_id"]))
    descriptor = task["spec"]["spec"]["inputs"]["frozen_inputs"]
    data = world.service.cas.path_for(descriptor["digest"][7:]).read_bytes()
    assert task["spec"]["child_delegation"]["discovery_grant"] == shared.grant
    attempt = world.tmp / "attempts" / case
    (attempt / "outputs").mkdir(parents=True)
    cancelled = [False]
    bridge = HostChildBridge(shared.host, task, attempt_id=claim["attempt_id"],
        lease_id=claim["lease_id"], fence=claim["fence"], runtime_epoch=claim["runtime_epoch"],
        output_root=attempt / "outputs", cancelled=lambda: cancelled[0])
    world.bridges.append(bridge)
    inputs = shared.host._materialize_inputs(task["spec"], attempt,
        authorized_input_object_ids=claim["input_object_ids"], file_input_names=frozenset({"frozen_inputs"}))
    assert Path(inputs["frozen_inputs"]).read_bytes() == data
    if case == "changed-document":
        Path(inputs["frozen_inputs"]).write_bytes(b"{}")
    return SimpleNamespace(claim=claim, task=task, attempt=attempt, bridge=bridge,
                           inputs=inputs, cancelled=cancelled, frames=[], errors=[], child_claim=None)


def test_iteration_video_composed_runtime_path(world, monkeypatch, capsys):
    shared = _setup(world, monkeypatch)
    setup_rejects = ("foreign-project", "foreign-target", "foreign-association", "binding-size", "changed-document")
    submit_rejects = ("tampered-media", "changed-frozen", "cancelled", "authority-loss")
    cases = ("success", "success-theme", *setup_rejects, *submit_rejects)
    completed = []
    for case in cases:
        current = _attempt(world, shared, case, monkeypatch)
        bridge, claim = current.bridge, current.claim
        calls_before = len(world.calls)
        try:
            def prepare():
                return _iteration_video_discovery(shared.host, current.task, shared.host.capabilities[ROOT],
                    bridge, current.inputs, attempt_id=claim["attempt_id"], lease_id=claim["lease_id"],
                    fence=claim["fence"], runtime_epoch=claim["runtime_epoch"], cancelled=lambda: current.cancelled[0])
            if case in setup_rejects:
                with pytest.raises(HostError):
                    prepare()
                assert bridge._iteration_video_submit is None
            else:
                reader = prepare()
                assert Path(current.inputs["media_dependency"]).is_relative_to(bridge.output_root)
                assert Path(current.inputs["media_dependency"]).read_bytes() == MEDIA
                original_dispatch = bridge.dispatch

                def dispatch(frame):
                    current.frames.append(deepcopy(frame))
                    if frame["op"] == "submit":
                        if case == "tampered-media":
                            (bridge.output_root / frame["inputs"]["media_dependency"]["filename"]).write_bytes(b"tampered")
                        elif case == "changed-frozen":
                            Path(current.inputs["frozen_inputs"]).write_bytes(b"{}")
                        elif case == "cancelled":
                            current.cancelled[0] = True
                        elif case == "authority-loss":
                            world.service.cancel_task_canonical(claim["task_id"], {}, idempotency_key="cancel-" + case)
                    try:
                        result = original_dispatch(frame)
                    except Exception as exc:
                        current.errors.append((type(exc).__name__, str(exc)))
                        raise
                    if frame["op"] == "submit":
                        current.child_claim = _claim(world, RENDER, "render-claim-" + case)
                        assert current.child_claim["task_id"] == result["task_id"]
                        _settle(world, current.child_claim, OUTPUTS, "render-settle-" + case)
                    return result

                bridge.dispatch = dispatch
                sdk = sdk_bridge._install_child_bridge(bridge.child_channel)
                try:
                    argv = ["--project-id", world.project, "--target-run-id", shared.target,
                            "--frozen-inputs", current.inputs["frozen_inputs"],
                            "--media-dependency", current.inputs["media_dependency"], "--out", str(bridge.output_root)]
                    if case == "success-theme":
                        theme = current.attempt / "inputs/theme.json"
                        theme.write_bytes(b'{"palette":"supplied"}')
                        argv.extend(["--theme", str(theme)])
                    code = shared.consumer.main(argv)
                    captured = capsys.readouterr()
                    assert code == (0 if case.startswith("success") else 2), (case, current.errors, captured)
                    if case in submit_rejects:
                        if case in {"cancelled", "authority-loss"}:
                            # dispatch fences authority before entering the
                            # discovery adapter, so its reader need not latch.
                            assert bridge._revoked.is_set(), case
                        else:
                            assert reader.failed, case
                        assert current.child_claim is None
                    else:
                        outputs = json.loads(captured.out)
                        for port, (data, _, _) in OUTPUTS.items():
                            assert Path(outputs[port]).is_relative_to(bridge.output_root)
                            assert Path(outputs[port]).read_bytes() == data
                        snapshot = bridge.admitted_children_snapshot().children
                        assert len(snapshot) == 1
                        provenance = json.loads(snapshot[0].discovery_provenance_json)["media_dependency"]
                        assert provenance["parent_attempt_id"] == claim["attempt_id"]
                        assert provenance["selected_association_id"] == shared.source["association_id"]
                        assert provenance["source_association_ids"] == [shared.source["association_id"]]
                        submitted = next(frame for frame in current.frames if frame["op"] == "submit")
                        assert submitted["child_key"] == "iteration-render"
                        assert submitted["child"]["capability_id"] == RENDER
                        descriptors = json.loads(snapshot[0].descriptors_json)
                        names = [row["name"] for row in descriptors]
                        assert names == ["timeline", "assets_registry", "media_dependency"] + (["theme"] if case.endswith("theme") else [])
                        assert json.loads(snapshot[0].inputs_json)["media_dependency"]["filename"].startswith("discovery-objects/")
                        registry = json.loads((bridge.output_root / "render-inputs/assets.json").read_bytes())
                        assert registry["assets"][f"asset_{shared.target}_0"]["object_id"] == shared.source["object_id"]
                        assert all(not {"file", "path", "url", "locator"} & set(row) for row in registry["assets"].values())
                        assert [frame["op"] for frame in current.frames].count("materialize_output") == 2
                        rows = world.service.managed_outputs(current.child_claim["task_id"])
                        assert {row["output_port"] for row in rows} == set(OUTPUTS)
                        materialized = [frame["association_id"] for frame in current.frames if frame["op"] == "materialize_output"]
                        assert materialized == [next(row["association_id"] for row in rows if row["output_port"] == port)
                                                for port in OUTPUTS]
                        bridge.finish()
                        _settle(world, claim, OUTPUTS, "parent-settle-" + case)
                        assert world.service.task(claim["task_id"])["task"]["status"] == "completed"
                finally:
                    sdk.close()
                    monkeypatch.setattr(sdk_bridge, "_bridge", None)
            mutations = [call for call in world.calls[calls_before:] if call[0] == "POST"]
            if case in (*setup_rejects, *submit_rejects):
                assert mutations == [], (case, mutations)
                assert not world.service.store.delegated_children(claim["task_id"], claim["attempt_id"])
            else:
                uploads = [call for call in mutations if call[1] == "/v1/objects"]
                assert len(uploads) == (4 if case.endswith("theme") else 3)
                assert len([call for call in mutations if call[1].endswith("child-authority")]) == 1
                assert len([call for call in mutations if call[1] == "/v1/delegated-tasks"]) == 1
                delegated = world.service.store.delegated_children(claim["task_id"], claim["attempt_id"])
                assert len(delegated) == 1
            completed.append(case)
        finally:
            bridge.revoke()
            assert not bridge._thread.is_alive(), case
            if world.service.task(claim["task_id"])["task"]["status"] == "running":
                world.service.cancel_task_canonical(claim["task_id"], {}, idempotency_key="cleanup-" + case)
    assert completed == list(cases)
    assert world.service.managed_outputs(shared.source["task_id"]) == [shared.source]
    assert world.service.cas.path_for(shared.source["digest"][7:]).read_bytes() == MEDIA
