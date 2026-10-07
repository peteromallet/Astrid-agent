from __future__ import annotations

import copy
import hashlib
import importlib
import json
import os
import shutil
import sys
from collections.abc import Mapping
from pathlib import Path

import pytest
import yaml

from astrid.core.pack.discovery import DiscoveredPack
from astrid.core.pack.loader import load_pack_manifest
from astrid.core.execution.generic_host import GenericPackHost, RuntimeProtocolClient
from astrid.sdk.actions import action_executor_definition
from astrid.sdk.client import AstridClient

ROOT = Path(__file__).resolve().parents[2]
VIBECOMFY_PACK_ROOT = ROOT / "astrid" / "packs" / "vibecomfy"


def _write_fake_vibecomfy_package(pack_root: Path) -> None:
    package_root = pack_root / "vibecomfy"
    package_root.mkdir(parents=True)
    actions_root = package_root / "actions"
    service_root = package_root / "porting"
    edit_service_root = service_root / "edit"
    actions_root.mkdir()
    service_root.mkdir(parents=True)
    edit_service_root.mkdir(parents=True)

    source_manifest = yaml.safe_load(
        (VIBECOMFY_PACK_ROOT / "pack.yaml").read_text(encoding="utf-8")
    )
    action_declarations = {
        action_id: copy.deepcopy(source_manifest["actions"][action_id])
        for action_id in ("import", "edit")
    }
    fixture_manifest = {
        "schema_version": 3,
        "id": "vibecomfy",
        "name": "Fixture VibeComfy",
        "version": "1.0.0",
        "capabilities": ["import_workflow", "edit_workflow_ir"],
        "actions": action_declarations,
    }
    (package_root / "pack.yaml").write_text(
        yaml.safe_dump(fixture_manifest, sort_keys=False), encoding="utf-8"
    )
    copied_resource_bytes = 0
    for action_id, declaration in action_declarations.items():
        for resource in declaration["resources"]:
            relative_path = Path(resource["path"])
            source = VIBECOMFY_PACK_ROOT / relative_path
            destination = package_root / relative_path
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
            source_bytes = source.read_bytes()
            assert destination.read_bytes() == source_bytes
            copied_resource_bytes += len(source_bytes)
    assert copied_resource_bytes == 24_732
    (package_root / "__init__.py").write_text("\n", encoding="utf-8")
    (service_root / "__init__.py").write_text("\n", encoding="utf-8")
    (service_root / "import_service.py").write_text(
        "import hashlib\n"
        "from types import SimpleNamespace\n"
        "def _digest(data): return 'sha256:' + hashlib.sha256(data).hexdigest()\n"
        "def import_workflow_bytes(source_bytes, *, workflow_id):\n"
        "    python = b\"workflow = load('fixture')\\n\"\n"
        "    companion = b'{\\\"revision_id\\\":\\\"origin-revision\\\"}\\n'\n"
        "    members = {'workflow.py': python, 'workflow.vibe.json': companion, 'source.json': source_bytes}\n"
        "    digests = {name: _digest(data) for name, data in members.items()}\n"
        "    report = {'schema_version': 1, 'transition_kind': 'origin', 'workflow_id': workflow_id,\n"
        "        'workflow_identity': workflow_id, 'revision_id': 'origin-revision',\n"
        "        'parent_revision': None, 'parent_task_id': None, 'origin_task_id': None, 'before': None,\n"
        "        'after': {'revision_id': 'origin-revision', 'members': digests}, 'members': digests,\n"
        "        'readiness': {'status': 'ready'}, 'validation': {'status': 'structural'}}\n"
        "    return SimpleNamespace(source_bytes=source_bytes, python_bytes=python,\n"
        "        companion_bytes=companion, report=report)\n",
        encoding="utf-8",
    )
    (edit_service_root / "__init__.py").write_text("\n", encoding="utf-8")
    (edit_service_root / "bundle_service.py").write_text(
        "from pathlib import Path\n"
        "from types import SimpleNamespace\n"
        "def transition_bundle(reference, *, output, expected_parent_revision, tool_calls=None, capture=False, candidate_python=None, **kwargs):\n"
        "    if capture:\n"
        "        from vibecomfy.security import _active_gate\n"
        "        gate = _active_gate.get()\n"
        "        assert gate is not None and gate.non_interactive and gate.assume_yes\n"
        "        gate.audit.append({'kind': 'explicit_manual_capture', 'non_interactive': gate.non_interactive, 'assume_yes': gate.assume_yes})\n"
        "        ops = []\n"
        "        python = Path(candidate_python).read_bytes()\n"
        "        revision = 'captured-revision'\n"
        "    else:\n"
        "        ops = tool_calls[0]['args']['ops']\n"
        "        if expected_parent_revision == 'origin-revision':\n"
        "            assert len(ops) == 2\n"
        "            python = b\"workflow = load('edited')\\n\"\n"
        "            revision = 'edited-revision'\n"
        "        else:\n"
        "            assert expected_parent_revision == 'edited-revision' and len(ops) == 1\n"
        "            python = b\"workflow = load('edited-twice')\\n\"\n"
        "            revision = 'edited-twice-revision'\n"
        "    output = Path(output)\n"
        "    output.parent.mkdir(parents=True, exist_ok=True)\n"
        "    output.write_bytes(python)\n"
        "    output.with_name('workflow.vibe.json').write_text('{\"revision_id\":\"' + revision + '\"}\\n')\n"
        "    output.with_name('source.json').write_bytes(Path(reference).with_name('source.json').read_bytes())\n"
        "    return SimpleNamespace(status='saved', revision_id=revision, to_dict=lambda: {\n"
        "        'status': 'saved', 'kind': 'edit', 'parent_revision': expected_parent_revision,\n"
        "        'revision': revision, 'operations': ops, 'diff': ops, 'diagnostics': []})\n",
        encoding="utf-8",
    )
    (package_root / "security.py").write_text(
        "from contextvars import ContextVar\n"
        "_active_gate = ContextVar('fixture_gate', default=None)\n"
        "class GateContext:\n"
        "    def __init__(self, *, non_interactive, assume_yes):\n"
        "        self.non_interactive = non_interactive\n"
        "        self.assume_yes = assume_yes\n"
        "        self.audit = []\n"
        "def set_gate_context(context): return _active_gate.set(context)\n",
        encoding="utf-8",
    )
    (package_root / "workflow_bundle.py").write_text(
        "from pathlib import Path\n"
        "from types import SimpleNamespace\n"
        "def load_bundle(path, schema_provider=None):\n"
        "    current = Path(path).read_bytes()\n"
        "    captured = b'captured' in current\n"
        "    edited_twice = b'edited-twice' in current\n"
        "    edited = b'edited' in current\n"
        "    return SimpleNamespace(workflow_identity='portrait',\n"
        "        revision_id='captured-revision' if captured else ('edited-twice-revision' if edited_twice else ('edited-revision' if edited else 'origin-revision')),\n"
        "        parent_revision='edited-twice-revision' if captured else ('edited-revision' if edited_twice else ('origin-revision' if edited else None)),\n"
        "        semantic_digest='semantic:captured' if captured else ('semantic:edited-twice' if edited_twice else ('semantic:edited' if edited else 'semantic:origin')),\n"
        "        ui_digest='ui:captured' if captured else ('ui:edited-twice' if edited_twice else ('ui:edited' if edited else 'ui:origin')))\n",
        encoding="utf-8",
    )


def _assert_module_under(module, root: Path, label: str) -> Path:
    module_file = getattr(module, "__file__", None)
    if not module_file:
        raise RuntimeError(f"{label} has no source file: {module!r}")
    actual_path = Path(module_file).resolve()
    try:
        actual_path.relative_to(root)
    except ValueError as exc:
        raise RuntimeError(f"{label} loaded outside {root}: {actual_path}") from exc
    return actual_path


@pytest.fixture
def runtime_context(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    runtime_selection = os.environ.get("BANODOCO_RUNTIME_CHECKOUT", "").strip()
    if not runtime_selection:
        pytest.skip("BANODOCO_RUNTIME_CHECKOUT must select the Runtime integration worktree")
    runtime_root = Path(runtime_selection).expanduser().resolve()
    if not runtime_root.is_dir() or not (runtime_root / "runtime_protocol").is_dir():
        raise RuntimeError(f"BANODOCO_RUNTIME_CHECKOUT is not a Runtime checkout: {runtime_root}")

    runtime_module_names = (
        "runtime_protocol",
        "runtime_protocol.daemon",
        "runtime_protocol.store",
        "runtime_protocol.service",
    )
    for module_name in runtime_module_names:
        loaded = sys.modules.get(module_name)
        if loaded is not None:
            _assert_module_under(loaded, runtime_root, module_name)
    monkeypatch.syspath_prepend(str(runtime_root))

    runtime_package = importlib.import_module("runtime_protocol")
    daemon_module = importlib.import_module("runtime_protocol.daemon")
    store_module = importlib.import_module("runtime_protocol.store")
    service_module = importlib.import_module("runtime_protocol.service")
    for label, module in (
        ("runtime_protocol", runtime_package),
        ("runtime_protocol.daemon", daemon_module),
        ("runtime_protocol.store", store_module),
        ("runtime_protocol.service", service_module),
    ):
        _assert_module_under(module, runtime_root, label)

    astrid_modules = (
        sys.modules["astrid.core.execution.generic_host"],
        sys.modules["astrid.sdk.client"],
        importlib.import_module("astrid.sdk.workspace_client"),
        importlib.import_module("astrid.sdk.remote"),
        importlib.import_module("banodoco_workspace_client.generated"),
        importlib.import_module("banodoco_workspace_client.contract_metadata"),
    )
    for module in astrid_modules:
        _assert_module_under(module, ROOT, module.__name__)

    pack_root = tmp_path / "packs"
    pack_root.mkdir()
    _write_fake_vibecomfy_package(pack_root)
    pack_manifest_path = pack_root / "vibecomfy" / "pack.yaml"
    manifest_data = yaml.safe_load(pack_manifest_path.read_text(encoding="utf-8"))
    pack_definition = load_pack_manifest(pack_manifest_path, expected_pack_id="vibecomfy")
    discovered_pack = DiscoveredPack(pack_definition, "extra", 0)

    realm_root = tmp_path / "realm"
    store_module.RealmStore.initialize(realm_root).close()
    daemon = daemon_module.RuntimeDaemon(realm_root, support_root=tmp_path / "support")
    hosts: list[GenericPackHost] = []

    def new_host(attempt_root: Path) -> GenericPackHost:
        host = GenericPackHost(
            pack_roots=[pack_root],
            attempt_root=attempt_root,
            client=RuntimeProtocolClient(
                daemon.endpoint, daemon.credential_path.read_text().strip()
            ),
            executor_id="vibecomfy-import-test-host",
        )
        hosts.append(host)
        return host

    closed = False

    def close() -> None:
        nonlocal closed
        if closed:
            return
        closed = True
        shutdown_error = None
        for host in hosts:
            try:
                host.shutdown()
            except Exception as exc:  # Continue teardown for the remaining owned hosts.
                if shutdown_error is None:
                    shutdown_error = exc
        try:
            daemon.stop()
        finally:
            if shutdown_error is not None:
                raise shutdown_error

    try:
        daemon.start()
        yield {
            "daemon": daemon,
            "pack_root": pack_root,
            "manifest_data": manifest_data,
            "discovered_pack": discovered_pack,
            "new_host": new_host,
            "close": close,
        }
    finally:
        close()


def _discover_register_host(host, manifest_data: Mapping[str, object], discovered_pack):
    declarations = manifest_data["actions"]
    records = host.discover()
    assert {record.id for record in records} == {"vibecomfy.import", "vibecomfy.edit"}
    assert manifest_data["schema_version"] == 3
    capability_digests = {}
    for record in records:
        local_id = record.id.removeprefix("vibecomfy.")
        declaration = declarations[local_id]
        projected = host.capabilities[record.id].definition.to_dict()
        expected = action_executor_definition(
            discovered_pack, local_id, declaration
        ).to_dict()
        for contract_key in (
            "command", "inputs", "outputs", "isolation", "graph", "conditions", "cache"
        ):
            assert projected.get(contract_key) == expected.get(contract_key)
        assert record.definition.metadata["action_invocation"] == declaration["invocation"]
        assert [port.name for port in record.definition.outputs] == [
            "python", "companion", "source", "report"
        ]
        capability_digests[record.id] = record.capability_digest

    registration = host.register()
    registered_rows = registration["registration"].capabilities
    assert {
        row.capability_id: row.definition_digest for row in registered_rows
    } == capability_digests
    return capability_digests


def _open_client(daemon) -> AstridClient:
    return AstridClient.open(
        endpoint=daemon.endpoint,
        credential=daemon.credential_path,
        realm_id=daemon.service.realm["id"],
        actor_id="owner",
        client_name="astrid-vibecomfy-import-test",
        client_version="test",
        protocol_version="workspace.v1",
    )


def _task_events(client: AstridClient, task_id: str) -> list[dict[str, object]]:
    events = client.tasks.events(task_id)
    assert events.ok
    rows = events.data
    if isinstance(rows, dict):
        rows = rows.get("items", rows.get("events", []))
    elif isinstance(rows, (list, tuple)) and len(rows) == 2 and isinstance(rows[0], list):
        rows = rows[0]
    assert isinstance(rows, list)
    return [dict(row) for row in rows if isinstance(row, dict)]


def _assert_lifecycle_events(client: AstridClient, task_id: str) -> None:
    records = _task_events(client, task_id)
    event_types = [
        item.get("event_type", item.get("kind"))
        or (item.get("payload", {}).get("kind") if isinstance(item.get("payload"), dict) else None)
        for item in records
    ]
    assert event_types.count("task.admitted") == 1
    assert event_types.count("task.completed") == 1


def _project_task_ids(client: AstridClient, project_id: str) -> set[str]:
    listed = client.tasks.list(project_id)
    assert listed.ok
    rows = listed.data
    if (
        isinstance(rows, (list, tuple))
        and len(rows) == 2
        and isinstance(rows[0], list)
    ):
        rows, cursor = rows
        assert cursor is None
    elif isinstance(rows, dict):
        cursor = rows.get("next_cursor")
        rows = rows.get("items", [])
        assert cursor is None
    while isinstance(rows, list) and len(rows) == 1 and isinstance(rows[0], list):
        rows = rows[0]
    assert isinstance(rows, list)
    task_ids = {
        row.get("task_id", row.get("id"))
        if isinstance(row, Mapping)
        else getattr(row, "task_id", None)
        for row in rows
    }
    assert None not in task_ids
    return {str(task_id) for task_id in task_ids}


def test_import_edit_capture_history_runs_as_runtime_tasks_and_settles_lineage(
    tmp_path: Path, runtime_context
) -> None:
    daemon = runtime_context["daemon"]
    manifest_data = runtime_context["manifest_data"]
    discovered_pack = runtime_context["discovered_pack"]
    client = _open_client(daemon)
    project = client.projects.create(
        slug="vibecomfy-origin",
        name="VibeComfy Origin",
        idempotency_key="vibecomfy-origin-project",
    )
    assert project.ok
    project_id = project.data["project_id"]

    host = runtime_context["new_host"](tmp_path / "attempt")
    capability_digests = _discover_register_host(host, manifest_data, discovered_pack)

    source_bytes = b'{"nodes":[],"links":[]}\r\n'
    source_path = tmp_path / "source.json"
    source_path.write_bytes(source_bytes)
    imported = client.media.import_file(
        project=project_id,
        path=source_path,
        idempotency_key="vibecomfy-origin-source",
    )
    assert imported.ok
    source_digest = imported.data["digest"]
    task_result = client.tasks.create(
        project_id=project_id,
        capability="vibecomfy.import",
        spec={
            "inputs": {"workflow_id": "portrait"},
            "input_digests": [{"name": "source", "digest": source_digest}],
            "transition_kind": "origin",
            "parent_task_id": None,
            "origin_task_id": None,
        },
        input_manifest=[imported.data["object_id"]],
        idempotency_key="vibecomfy-origin-task",
        capability_digest=capability_digests["vibecomfy.import"],
    )
    assert task_result.ok
    task_id = task_result.data["task_id"]

    settled = host.run(once=True)
    assert len(settled) == 1 and settled[0].state == "succeeded"
    task = client.tasks.show(task_id)
    assert task.ok
    assert task.data["state"] == "succeeded"
    assert task.data["capability_digest"] == capability_digests["vibecomfy.import"]

    output_manifest = task.data["result"]["outputs"]
    assert [item["name"] for item in output_manifest] == [
        "python",
        "companion",
        "source",
        "report",
    ]
    origin_outputs = {item["name"]: item for item in output_manifest}
    object_bytes = {
        "python": b"workflow = load('fixture')\n",
        "companion": b'{"revision_id":"origin-revision"}\n',
        "source": source_bytes,
    }
    for item in output_manifest:
        if item["name"] in object_bytes:
            assert client.media.read_bytes(item["digest"]) == object_bytes[item["name"]]
    report_output = origin_outputs["report"]
    report_bytes = client.media.read_bytes(report_output["digest"])
    assert "sha256:" + hashlib.sha256(report_bytes).hexdigest() == report_output["digest"]
    report = json.loads(report_bytes)
    assert report["transition_kind"] == "origin"
    assert report["parent_task_id"] is None
    assert report["origin_task_id"] is None
    assert report["members"]["source.json"] == source_digest
    assert report["after"]["members"] == {
        "workflow.py": origin_outputs["python"]["digest"],
        "workflow.vibe.json": origin_outputs["companion"]["digest"],
        "source.json": origin_outputs["source"]["digest"],
    }

    _assert_lifecycle_events(client, task_id)

    # A downstream edit consumes the exact project-owned artifacts settled
    # by the origin task. Its two typed operations run as one atomic batch.
    operation_bytes = json.dumps(
        {
            "schema_version": 1,
            "expected_revision": 0,
            "ops": [
                {"op": "add_node", "class_type": "FixtureNode", "uid": "new-node"},
                {"op": "edit_node", "target": "new-node", "field": "value", "value": 7},
            ],
        },
        sort_keys=True,
    ).encode()
    operations_path = tmp_path / "operations.json"
    operations_path.write_bytes(operation_bytes)
    operations_media = client.media.import_file(
        project=project_id,
        path=operations_path,
        idempotency_key="vibecomfy-edit-operations",
    )
    assert operations_media.ok

    edit_input_digests = {
        "python": origin_outputs["python"]["digest"],
        "companion": origin_outputs["companion"]["digest"],
        "source": origin_outputs["source"]["digest"],
        "operations": operations_media.data["digest"],
    }
    edit_task_result = client.tasks.create(
        project_id=project_id,
        capability="vibecomfy.edit",
        spec={
            "inputs": {
                "workflow_id": "portrait",
                "parent_revision": "origin-revision",
                "parent_task_id": task_id,
                "origin_task_id": task_id,
                "transition_kind": "typed_edit",
                "python_execution_consent": "confirmed",
            },
            "input_digests": [
                {"name": name, "digest": digest}
                for name, digest in edit_input_digests.items()
            ],
            "workflow_id": "portrait",
            "parent_revision": "origin-revision",
            "parent_task_id": task_id,
            "origin_task_id": task_id,
            "transition_kind": "typed_edit",
        },
        input_manifest=list(edit_input_digests.values()),
        idempotency_key="vibecomfy-typed-edit-task",
        capability_digest=capability_digests["vibecomfy.edit"],
    )
    assert edit_task_result.ok
    edit_task_id = edit_task_result.data["task_id"]
    edit_host = runtime_context["new_host"](tmp_path / "edit-attempt")
    assert _discover_register_host(edit_host, manifest_data, discovered_pack) == capability_digests
    edit_settled = edit_host.run(once=True)
    assert len(edit_settled) == 1 and edit_settled[0].state == "succeeded"

    edit_task = client.tasks.show(edit_task_id)
    assert edit_task.ok and edit_task.data["state"] == "succeeded"
    assert edit_task.data["capability_digest"] == capability_digests["vibecomfy.edit"]
    edit_output_manifest = edit_task.data["result"]["outputs"]
    assert [item["name"] for item in edit_output_manifest] == [
        "python", "companion", "source", "report"
    ]
    edit_outputs = {item["name"]: item for item in edit_output_manifest}
    assert set(edit_outputs) == {"python", "companion", "source", "report"}
    for name, expected_bytes in {
        "python": b"workflow = load('edited')\n",
        "companion": b'{"revision_id":"edited-revision"}\n',
        "source": source_bytes,
    }.items():
        assert client.media.read_bytes(edit_outputs[name]["digest"]) == expected_bytes
    edit_report_output = edit_outputs["report"]
    edit_report_bytes = client.media.read_bytes(edit_report_output["digest"])
    assert "sha256:" + hashlib.sha256(edit_report_bytes).hexdigest() == edit_report_output["digest"]
    edit_report = json.loads(edit_report_bytes)
    assert edit_report["transition_kind"] == "typed_edit"
    assert edit_report["python_execution_consent"] == "confirmed"
    assert edit_report["workflow_id"] == "portrait"
    assert edit_report["revision_id"] == "edited-revision"
    assert edit_report["parent_revision"] == "origin-revision"
    assert edit_report["parent_task_id"] == task_id
    assert edit_report["origin_task_id"] == task_id
    assert edit_report["before"]["members"] == {
        "workflow.py": origin_outputs["python"]["digest"],
        "workflow.vibe.json": origin_outputs["companion"]["digest"],
        "source.json": origin_outputs["source"]["digest"],
    }
    assert edit_report["after"]["members"] == {
        "workflow.py": edit_outputs["python"]["digest"],
        "workflow.vibe.json": edit_outputs["companion"]["digest"],
        "source.json": edit_outputs["source"]["digest"],
    }
    assert edit_report["requested_operations"] == json.loads(operation_bytes)["ops"]
    assert len(edit_report["operations"]) == 2
    assert edit_report["requested_operations"][1]["target"] == edit_report[
        "requested_operations"
    ][0]["uid"]
    _assert_lifecycle_events(client, edit_task_id)

    edit_two_operations = {
        "schema_version": 1,
        "expected_revision": 0,
        "ops": [
            {"op": "edit_node", "target": "new-node", "field": "value", "value": 9}
        ],
    }
    edit_two_operations_bytes = json.dumps(
        edit_two_operations, sort_keys=True
    ).encode()
    edit_two_operations_path = tmp_path / "operations-edit-two.json"
    edit_two_operations_path.write_bytes(edit_two_operations_bytes)
    edit_two_operations_media = client.media.import_file(
        project=project_id,
        path=edit_two_operations_path,
        idempotency_key="vibecomfy-edit-two-operations",
    )
    assert edit_two_operations_media.ok
    edit_two_input_digests = {
        "python": edit_outputs["python"]["digest"],
        "companion": edit_outputs["companion"]["digest"],
        "source": edit_outputs["source"]["digest"],
        "operations": edit_two_operations_media.data["digest"],
    }
    edit_two_result = client.tasks.create(
        project_id=project_id,
        capability="vibecomfy.edit",
        spec={
            "inputs": {
                "workflow_id": "portrait",
                "parent_revision": "edited-revision",
                "parent_task_id": edit_task_id,
                "origin_task_id": task_id,
                "transition_kind": "typed_edit",
                "python_execution_consent": "confirmed",
            },
            "input_digests": [
                {"name": name, "digest": digest}
                for name, digest in edit_two_input_digests.items()
            ],
            "workflow_id": "portrait",
            "parent_revision": "edited-revision",
            "parent_task_id": edit_task_id,
            "origin_task_id": task_id,
            "transition_kind": "typed_edit",
        },
        input_manifest=list(edit_two_input_digests.values()),
        idempotency_key="vibecomfy-typed-edit-two-task",
        capability_digest=capability_digests["vibecomfy.edit"],
    )
    assert edit_two_result.ok
    edit_two_task_id = edit_two_result.data["task_id"]
    edit_two_host = runtime_context["new_host"](tmp_path / "edit-two-attempt")
    assert _discover_register_host(edit_two_host, manifest_data, discovered_pack) == capability_digests
    edit_two_settled = edit_two_host.run(once=True)
    assert len(edit_two_settled) == 1 and edit_two_settled[0].state == "succeeded"

    edit_two_task = client.tasks.show(edit_two_task_id)
    assert edit_two_task.ok and edit_two_task.data["state"] == "succeeded"
    assert edit_two_task.data["capability_digest"] == capability_digests["vibecomfy.edit"]
    edit_two_outputs = {
        item["name"]: item for item in edit_two_task.data["result"]["outputs"]
    }
    assert [item["name"] for item in edit_two_task.data["result"]["outputs"]] == [
        "python", "companion", "source", "report"
    ]
    for name, expected_bytes in {
        "python": b"workflow = load('edited-twice')\n",
        "companion": b'{"revision_id":"edited-twice-revision"}\n',
        "source": source_bytes,
    }.items():
        assert client.media.read_bytes(edit_two_outputs[name]["digest"]) == expected_bytes
    edit_two_report_bytes = client.media.read_bytes(edit_two_outputs["report"]["digest"])
    assert (
        "sha256:" + hashlib.sha256(edit_two_report_bytes).hexdigest()
        == edit_two_outputs["report"]["digest"]
    )
    edit_two_report = json.loads(edit_two_report_bytes)
    assert edit_two_report["transition_kind"] == "typed_edit"
    assert edit_two_report["python_execution_consent"] == "confirmed"
    assert edit_two_report["revision_id"] == "edited-twice-revision"
    assert edit_two_report["parent_revision"] == "edited-revision"
    assert edit_two_report["parent_task_id"] == edit_task_id
    assert edit_two_report["origin_task_id"] == task_id
    assert edit_two_report["before"]["members"] == {
        "workflow.py": edit_outputs["python"]["digest"],
        "workflow.vibe.json": edit_outputs["companion"]["digest"],
        "source.json": edit_outputs["source"]["digest"],
    }
    assert edit_two_report["after"]["members"] == {
        "workflow.py": edit_two_outputs["python"]["digest"],
        "workflow.vibe.json": edit_two_outputs["companion"]["digest"],
        "source.json": edit_two_outputs["source"]["digest"],
    }
    assert edit_two_report["requested_operations"] == edit_two_operations["ops"]
    _assert_lifecycle_events(client, edit_two_task_id)

    # Manual Python capture is a separate, explicitly admitted successor.
    candidate_path = tmp_path / "capture-candidate.py"
    candidate_path.write_bytes(b"workflow = load('captured')\n")
    candidate_media = client.media.import_file(
        project=project_id,
        path=candidate_path,
        idempotency_key="vibecomfy-capture-candidate",
    )
    assert candidate_media.ok
    capture_inputs = {
        "python": edit_two_outputs["python"]["digest"],
        "companion": edit_two_outputs["companion"]["digest"],
        "source": edit_two_outputs["source"]["digest"],
        "capture_python": candidate_media.data["digest"],
    }
    capture_result = client.tasks.create(
        project_id=project_id,
        capability="vibecomfy.edit",
        spec={
            "inputs": {
                "workflow_id": "portrait",
                "parent_revision": "edited-twice-revision",
                "parent_task_id": edit_two_task_id,
                "origin_task_id": task_id,
                "transition_kind": "manual_capture",
                "python_execution_consent": "confirmed",
            },
            "input_digests": [
                {"name": name, "digest": digest}
                for name, digest in capture_inputs.items()
            ],
            "workflow_id": "portrait",
            "parent_revision": "edited-twice-revision",
            "parent_task_id": edit_two_task_id,
            "origin_task_id": task_id,
            "transition_kind": "manual_capture",
        },
        input_manifest=list(capture_inputs.values()),
        idempotency_key="vibecomfy-manual-capture-task",
        capability_digest=capability_digests["vibecomfy.edit"],
    )
    assert capture_result.ok
    capture_task_id = capture_result.data["task_id"]
    capture_host = runtime_context["new_host"](tmp_path / "capture-attempt")
    assert _discover_register_host(capture_host, manifest_data, discovered_pack) == capability_digests
    capture_settled = capture_host.run(once=True)
    assert len(capture_settled) == 1 and capture_settled[0].state == "succeeded"
    capture_task = client.tasks.show(capture_task_id)
    assert capture_task.ok and capture_task.data["state"] == "succeeded"
    assert capture_task.data["capability_digest"] == capability_digests["vibecomfy.edit"]
    capture_outputs = {
        item["name"]: item for item in capture_task.data["result"]["outputs"]
    }
    assert [item["name"] for item in capture_task.data["result"]["outputs"]] == [
        "python", "companion", "source", "report"
    ]
    for name, expected_bytes in {
        "python": b"workflow = load('captured')\n",
        "companion": b'{"revision_id":"captured-revision"}\n',
        "source": source_bytes,
    }.items():
        assert client.media.read_bytes(capture_outputs[name]["digest"]) == expected_bytes
    capture_report_output = capture_outputs["report"]
    capture_report_bytes = client.media.read_bytes(capture_report_output["digest"])
    assert (
        "sha256:" + hashlib.sha256(capture_report_bytes).hexdigest()
        == capture_report_output["digest"]
    )
    capture_report = json.loads(capture_report_bytes)
    assert capture_report["transition_kind"] == "manual_capture"
    assert capture_report["python_execution_consent"] == "confirmed"
    assert capture_report["revision_id"] == "captured-revision"
    assert capture_report["parent_revision"] == "edited-twice-revision"
    assert capture_report["parent_task_id"] == edit_two_task_id
    assert capture_report["origin_task_id"] == task_id
    assert capture_report["security_gate_audit"] == [
        {
            "kind": "explicit_manual_capture",
            "non_interactive": True,
            "assume_yes": True,
        }
    ]
    assert capture_report["before"]["members"] == {
        "workflow.py": edit_two_outputs["python"]["digest"],
        "workflow.vibe.json": edit_two_outputs["companion"]["digest"],
        "source.json": edit_two_outputs["source"]["digest"],
    }
    assert capture_report["after"]["members"] == {
        "workflow.py": capture_outputs["python"]["digest"],
        "workflow.vibe.json": capture_outputs["companion"]["digest"],
        "source.json": capture_outputs["source"]["digest"],
    }
    _assert_lifecycle_events(client, capture_task_id)
    assert _project_task_ids(client, project_id) == {
        task_id,
        edit_task_id,
        edit_two_task_id,
        capture_task_id,
    }
