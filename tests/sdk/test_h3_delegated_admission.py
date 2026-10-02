from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace

import pytest

from astrid.sdk import invocation
from astrid.sdk.execution_request import TARGETED_EXECUTION_BINDING_CAPABILITY
from astrid.sdk.remote import RemoteTasks, _materialize_child_delegation
from astrid.sdk.workspace_client import DelegatedWorkspaceClient, WorkspaceClient, WorkspaceClientError


DIGEST = "sha256:" + "a" * 64
BUNDLE = "sha256:" + "b" * 64


def test_h3_parent_uses_request_cas_order_and_explicit_key():
    raw = json.dumps({
        "version": 1, "operation": "generate", "source": None,
        "output": {"duration": 8}, "content": {"prompt": "A quiet landscape"},
        "changes": {"video": [], "audio": []},
        "references": [{"asset": "image", "purpose": "appearance"}], "overrides": {},
    }).encode()
    request_id = "sha256:" + hashlib.sha256(raw).hexdigest()
    request = {
        "target": {"kind": "default"},
        "inputs": [
            {"name": "request", "object_id": request_id, "filename": "request.json"},
            {"name": "input_bundle", "object_id": BUNDLE, "filename": "inputs.zip"},
        ],
    }
    captured = {}

    def create(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(ok=True, data={"task_id": "task", "run_id": "run"})

    client = SimpleNamespace(tasks=SimpleNamespace(create=create), media=SimpleNamespace(read_bytes=lambda _: raw))
    capability = SimpleNamespace(
        id="h3_av.transform", capability_type="orchestrator",
        inputs=[SimpleNamespace(name=n, type="file") for n in ("request", "input_bundle")],
    )
    invocation._kernel_invoke(
        capability, kind="orchestrator", project="project", inputs={}, outputs={},
        execution_request=request, idempotency_key="explicit", _client=client,
    )
    assert captured["input_manifest"] == [request_id, BUNDLE]
    assert captured["idempotency_key"] == "explicit"
    declaration = captured["spec"]["child_delegation"]
    assert declaration["root_inputs"] == {"request": request_id, "input_bundle": BUNDLE}
    assert declaration["final_publication"]["effect"]["target_id"] == "project"

    client.media.read_bytes = lambda _: b"tampered"
    with pytest.raises(invocation.CapabilityValidationError, match="digest check"):
        invocation._kernel_invoke(
            capability, kind="orchestrator", project="project", inputs={}, outputs={},
            execution_request=request, _client=client,
        )


def test_child_policy_resolves_root_and_producer_refs():
    policy = _materialize_child_delegation(
        {
            "capability_ids": ["prepare", "compose"],
            "root_inputs": {"input_bundle": BUNDLE},
            "stages": [
                {"name": "prepare", "capability_id": "prepare", "inputs": [
                    {"name": "input_bundle", "root_input": "input_bundle"},
                ]},
                {"name": "compose", "capability_id": "compose", "inputs": [
                    {"name": "preparation", "producer_stage": "prepare", "output_port": "preparation"},
                ]},
            ],
            "final_publication": {"stage": "compose", "effect": {"target_id": "__PROJECT_ID__"}},
        },
        [{"capability_id": n, "definition_digest": DIGEST} for n in ("prepare", "compose")],
        input_object_ids=[BUNDLE], execution_request=None, project_id="project",
    )
    assert policy["stages"][0]["inputs"] == [{"name": "input_bundle", "root_object_id": BUNDLE}]
    assert policy["stages"][1]["inputs"] == [
        {"name": "preparation", "producer_stage": "prepare", "output_port": "preparation"},
    ]
    assert policy["final_publication"]["effect"]["target_id"] == "project"


def test_staged_child_preserves_lineage_and_omits_caller_publication():
    client = DelegatedWorkspaceClient(
        "http://127.0.0.1:1", "worker", authority="authority",
        capability_rows=[{"capability_id": "h3_av.compose", "capability_digest": DIGEST}],
    )
    client.handshake = lambda *args: {"capabilities": [TARGETED_EXECUTION_BINDING_CAPABILITY]}
    captured = {}

    def admit(**kwargs):
        captured.update(kwargs)
        return {"task_id": "task", "run_id": "run"}

    client.admit_delegated_task = admit
    refs = [
        {"name": "input_bundle", "root_object_id": BUNDLE},
        {"name": "preparation", "producer_task_id": "prepare-task", "association_id": "output", "output_port": "preparation"},
    ]
    result = RemoteTasks(client).create(
        project_id="caller-project", capability="h3_av.compose", spec={},
        input_manifest=[], stage="compose", input_refs=refs,
        execution_request={"target": {"kind": "default"}},
        settlement_effect={"effect_type": "generation.publish_v1", "target_id": "caller-project"},
    )
    assert result.ok
    task = captured["task"]
    assert task["stage"] == "compose"
    assert task["input_refs"] == refs
    assert "input_object_ids" not in task
    assert "inputs" not in task["execution_request"]
    assert "settlement_effect" not in task
    assert "project_id" not in task


def test_precompiled_runtime_policy_remains_supported():
    policy = {"capabilities": [{"capability_id": "prepare", "capability_digest": DIGEST}],
              "targets": [{"kind": "default"}], "input_object_ids": [BUNDLE]}
    assert _materialize_child_delegation(
        policy, [], input_object_ids=[BUNDLE], execution_request=None, project_id="project",
    ) == policy


def test_staged_child_cannot_fall_back_to_ordinary_admission(monkeypatch):
    client = WorkspaceClient("http://127.0.0.1:1", "owner")
    monkeypatch.setattr(client, "_call_generated", lambda *args, **kwargs: pytest.fail("ordinary admission was called"))
    with pytest.raises(WorkspaceClientError, match="requires a delegated Runtime client"):
        client.admit_task(
            capability_id="h3_av.prepare", capability_digest=DIGEST,
            input_object_ids=[], idempotency_key="child", stage="prepare", input_refs=[],
        )


def test_manifest_preview_expands_project_and_execution_request():
    capability = SimpleNamespace(
        id="h3_av.transform", capability_type="orchestrator", inputs=[],
        definition={"runtime": {"command": {"argv": ["python", "--project", "{project}", "--execution-request", "{execution_request}"]}}},
    )
    command = invocation._manifest_preview_command(
        capability, inputs={}, outputs={}, brief=None, python_exec=None,
        project="project", execution_request={"target": {"kind": "default"}},
    )
    assert command == ["python", "--project", "project", "--execution-request", '{"target":{"kind":"default"}}']
