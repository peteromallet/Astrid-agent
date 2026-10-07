from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest
import yaml

import astrid
from astrid.sdk import invocation
from astrid.core.execution.executor.registry import ExecutorRegistry
from astrid.core.execution.orchestrator.registry import OrchestratorRegistry
from astrid.core.execution.orchestrator.schema import OrchestratorDefinition, RuntimeSpec
from astrid.core.contracts.schema import CommandSpec


def write_pack(parent: Path, pack_id="ordinary", *, actions=None) -> Path:
    root = parent / pack_id
    (root / "actions").mkdir(parents=True, exist_ok=True)
    (root / "actions/echo.py").write_text("def echo(message):\n    return {'message': message}\n")
    (root / "actions/composed.py").write_text(
        "import astrid\n"
        "def compose(message):\n"
        "    result = astrid.invoke('ordinary.echo', kind='action', inputs={'message': message}, "
        "extra_pack_roots=(_pack_root,), client=_client)\n"
        "    return {'child': result.raw_result['payload']['action_result']}\n"
    )
    if actions is None:
        actions = {
            name: {"description": f"Run {name}.",
                   "invocation": {"kind": "python", "path": path, "function": function},
                   "inputs": [{"name": "message", "type": "string", "required": True}],
                   "metadata": {"project_scope": "optional"}}
            for name, path, function in (("echo", "actions/echo.py", "echo"),
                                         ("compose", "actions/composed.py", "compose"))
        }
    for action in actions.values():
        action.setdefault("inputs", [])
        action.setdefault("outputs", [])
    (root / "pack.yaml").write_text(yaml.safe_dump({"schema_version": 3, "id": pack_id,
                                                  "name": "Ordinary", "version": "1.0.0", "actions": actions}))
    return root


@pytest.fixture
def isolated(monkeypatch, tmp_path):
    from astrid.core.execution.executor import registry as executor_registry
    from astrid.core.execution.orchestrator import registry as orchestrator_registry
    from astrid.core.pack import discovery as pack_discovery

    monkeypatch.setenv("ASTRID_SOURCE_STATE", str(tmp_path / "absent-state.json"))
    monkeypatch.delenv("ASTRID_PACKS_PATH", raising=False)
    # These tests own their fixture packs. Keep real external discovery while
    # avoiding unrelated bundled resource reads on every fixture lookup; the
    # F04 composition regression separately exercises actual selected roots.
    for module in (executor_registry, orchestrator_registry, pack_discovery):
        monkeypatch.setattr(module, "discover_packs", lambda *args, **kwargs: ())


def describe(root, name="ordinary.echo"):
    return astrid.get_capability(name, kind="action", extra_pack_roots=(str(root),))


def test_action_discovery_does_not_import_sources_and_preserves_identity(tmp_path, isolated):
    root = write_pack(tmp_path)
    (root / "actions/echo.py").write_text("raise RuntimeError('discovery imported code')\n")
    discovered = astrid.discover(kind="action", extra_pack_roots=(str(root),))
    actions = {c.id: c for c in discovered.actions if c.id.startswith("ordinary.")}
    assert set(actions) == {"ordinary.echo", "ordinary.compose"}
    assert not discovered.elements
    assert discovered.capabilities == discovered.actions
    assert set(c.id for c in discovered.actions) == set(c.id for c in discovered.executors + discovered.orchestrators)
    capability = describe(root)
    assert capability.id == capability.handle.canonical_id == "ordinary.echo"
    assert capability.handle.pack_id == "ordinary"
    assert capability.handle.local_id == "echo"
    assert capability.capability_type == "executor"
    assert describe(root, "echo").id == "ordinary.echo"
    assert capability.definition["metadata"]["action_invocation"] == {
        "kind": "python", "path": "actions/echo.py", "function": "echo"}
    assert json.loads(json.dumps(discovered.to_dict()))["actions"]
    preview = astrid.invoke("ordinary.echo", kind="action", extra_pack_roots=(str(root),),
                            inputs={"message": "hello"}, dry_run=True)
    assert preview.ok and preview.capability_id == "ordinary.echo"
    assert preview.kernel_task_id is None


def test_simple_and_composed_actions_use_same_call_surface(tmp_path, isolated, monkeypatch):
    root = write_pack(tmp_path)
    calls = []
    client = object()

    def admitted(capability, **kwargs):
        # A bounded execution double runs fixture code after SDK admission.
        # F05 separately owns production path/function loading and custody.
        calls.append((capability.id, kwargs["kind"], kwargs["_client"]))
        metadata = capability.definition["metadata"]
        declared = metadata["action_invocation"]
        spec = importlib.util.spec_from_file_location("f04_fixture", root / declared["path"])
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        # Fixture composition uses an explicit private connection; no new
        # host context argument is injected into the action's call signature.
        module._pack_root = str(root)
        module._client = kwargs["_client"]
        values = dict(kwargs["inputs"])
        output = getattr(module, declared["function"])(**values)
        return "R-1", "T-1", "A-1", None, {"ok": True, "payload": {"action_result": output}}, True, None

    monkeypatch.setattr(invocation, "_kernel_invoke", admitted)
    simple = astrid.invoke("ordinary.echo", kind="action", extra_pack_roots=(str(root),),
                           inputs={"message": "hello"}, client=client)
    composed = astrid.invoke("ordinary.compose", kind="action", extra_pack_roots=(str(root),),
                             inputs={"message": "again"}, client=client)
    assert simple.ok and simple.raw_result["payload"]["action_result"] == {"message": "hello"}
    assert composed.ok and composed.raw_result["payload"]["action_result"] == {"child": {"message": "again"}}
    assert simple.outputs == composed.outputs == {}
    assert calls == [("ordinary.echo", "executor", client), ("ordinary.compose", "executor", client),
                     ("ordinary.echo", "executor", client)]


def test_command_declaration_preserves_binding_and_existing_consumer(tmp_path, isolated):
    action = {"description": "Adapter command.", "name": "Command", "version": "2.3.4",
              "invocation": {"kind": "command", "command": {
                  "argv": ["{python_exec}", "-m", "dependency.tool"], "cwd": "{out}",
                  "env": {"MODE": "test"}, "input_args": [{"input": "message", "flag": "--message"}]}},
              "inputs": [{"name": "message", "type": "string"}],
              "outputs": [{"name": "result", "type": "json", "mode": "create"}],
              "isolation": {"mode": "subprocess", "network": False},
              "metadata": {"project_scope": "optional"},
              "resources": [{"kind": "support", "path": "actions/echo.py"}]}
    root = write_pack(tmp_path, actions={"echo": action})
    capability = astrid.get_capability("ordinary.echo", kind="executor", extra_pack_roots=(str(root),))
    assert capability.definition["version"] == "2.3.4"
    assert capability.definition["metadata"]["action_declaration"] == action
    result = astrid.invoke("ordinary.echo", kind="action", extra_pack_roots=(str(root),),
                           inputs={"message": "two words"}, out=tmp_path / "out", dry_run=True)
    assert result.ok
    assert result.raw_result["command"] == ["python", "-m", "dependency.tool", "--message", "two words"]


@pytest.mark.parametrize("contract", ["inline", "local", "boolean"])
def test_input_schema_is_enforced_before_runtime_admission(tmp_path, isolated, monkeypatch, contract):
    schema = {"type": "object", "properties": {"message": {"type": "string", "minLength": 3}},
              "required": ["message"], "additionalProperties": False}
    value = schema if contract == "inline" else "actions/input.schema.json" if contract == "local" else False
    root = write_pack(tmp_path, actions={"echo": {
        "description": "Schema contract.", "invocation": {"kind": "python", "path": "actions/echo.py", "function": "echo"},
        "inputs": value, "metadata": {"project_scope": "optional"}}})
    if contract == "local":
        (root / value).write_text(json.dumps(schema))
    monkeypatch.setattr(invocation, "_kernel_invoke", lambda *args, **kwargs: pytest.fail("invalid input admitted"))
    for dry_run in (True, False):
        with pytest.raises(astrid.CapabilityValidationError):
            astrid.invoke("ordinary.echo", kind="action", extra_pack_roots=(str(root),),
                           inputs={"message": "x"}, dry_run=dry_run)


def test_local_schema_references_are_resolved_without_network(tmp_path, isolated):
    root = write_pack(tmp_path, actions={"echo": {
        "description": "Schema reference.", "invocation": {"kind": "python", "path": "actions/echo.py", "function": "echo"},
        "inputs": "actions/input.schema.json"}})
    (root / "actions/input.schema.json").write_text(json.dumps({"type": "object", "properties": {
        "message": {"$ref": "message.schema.json"}}}))
    (root / "actions/message.schema.json").write_text(json.dumps({"type": "string", "minLength": 3}))
    assert astrid.invoke("ordinary.echo", kind="action", extra_pack_roots=(str(root),),
                         inputs={"message": "valid"}, dry_run=True).ok
    with pytest.raises(astrid.CapabilityValidationError, match="too short"):
        astrid.invoke("ordinary.echo", kind="action", extra_pack_roots=(str(root),),
                      inputs={"message": "x"}, dry_run=True)
    (root / "actions/input.schema.json").write_text('{"$ref":"https://example.invalid/schema.json"}')
    with pytest.raises(astrid.CapabilityValidationError, match="could not be resolved"):
        astrid.invoke("ordinary.echo", kind="action", extra_pack_roots=(str(root),), inputs={}, dry_run=True)


def test_action_selector_preserves_legacy_routes_and_ambiguity():
    from astrid.sdk.discovery import _resolve_capability
    executor = ExecutorRegistry([{"id": "legacy.echo", "name": "Echo", "kind": "built_in", "version": "1"}])
    orchestrator = OrchestratorRegistry(executor_registry=executor)
    orchestrator.register(OrchestratorDefinition(
        id="legacy.compose", name="Compose", kind="built_in", version="1",
        runtime=RuntimeSpec(kind="command", command=CommandSpec(argv=("echo", "ok")))))
    kwargs = dict(kind="action", element_kind=None, executor_registry=executor,
                  orchestrator_registry=orchestrator, element_registry=None)
    assert _resolve_capability("legacy.echo", **kwargs).capability_type == "executor"
    assert _resolve_capability("legacy.compose", **kwargs).capability_type == "orchestrator"
    orchestrator.register(OrchestratorDefinition(
        id="legacy.echo", name="Duplicate", kind="built_in", version="1",
        runtime=RuntimeSpec(kind="command", command=CommandSpec(argv=("echo", "ok")))))
    with pytest.raises(astrid.CapabilityAmbiguousError, match="legacy.echo"):
        _resolve_capability("legacy.echo", **kwargs)


def test_internal_visibility_is_preserved(tmp_path, isolated):
    root = write_pack(tmp_path, actions={"echo": {
        "description": "Internal action.", "invocation": {"kind": "python", "path": "actions/echo.py", "function": "echo"},
        "metadata": {"visibility": "internal"}}})
    assert "ordinary.echo" not in {c.id for c in astrid.discover(kind="action", extra_pack_roots=(str(root),)).actions}
    with pytest.raises(astrid.CapabilityNotFoundError):
        describe(root)


def test_object_return_schema_does_not_create_file_outputs(tmp_path, isolated, monkeypatch):
    from astrid.core._shared.result_manifest import outputs_required
    from astrid.core.execution.executor.schema import validate_executor_definition

    schema = {"type": "object", "properties": {"message": {"type": "string"}},
              "required": ["message"]}
    root = write_pack(tmp_path, actions={"echo": {
        "description": "Return an object.",
        "invocation": {"kind": "python", "path": "actions/echo.py", "function": "echo"},
        "inputs": [{"name": "message", "type": "string"}],
        "outputs": schema, "metadata": {"project_scope": "optional"}}})
    capability = describe(root)
    assert capability.outputs == ()
    assert capability.definition["metadata"]["action_outputs_schema"] == schema
    assert not outputs_required(validate_executor_definition(dict(capability.definition)))
    def admitted(capability, **kwargs):
        return "R-1", "T-1", "A-1", None, {"ok": True, "payload": {"action_result": {"message": "hello"}}}, True, None
    monkeypatch.setattr(invocation, "_kernel_invoke", admitted)
    result = astrid.invoke("ordinary.echo", kind="action", extra_pack_roots=(str(root),),
                           inputs={"message": "hello"}, client=object())
    assert result.ok and result.raw_result["payload"]["action_result"] == {"message": "hello"}
    assert result.outputs == {}
    assert not list(root.rglob("*.json"))


def normalized_definition(tmp_path, *, input_schema=None, output_schema=None):
    from astrid.sdk.actions import action_executor_definition
    from astrid.core.pack.discovery import DiscoveredPack
    from astrid.core.pack.loader import load_pack_manifest

    root = write_pack(tmp_path, actions={"echo": {
        "description": "Shared validation.",
        "invocation": {"kind": "python", "path": "actions/echo.py", "function": "echo"},
        "inputs": [] if input_schema is None else input_schema,
        "outputs": [] if output_schema is None else output_schema}})
    discovered = DiscoveredPack(load_pack_manifest(root / "pack.yaml"), "extra", 0)
    return action_executor_definition(discovered, "echo", discovered.pack.actions["echo"])


def test_definition_input_validation_matches_sdk_wrapper(tmp_path):
    from types import SimpleNamespace
    from astrid.sdk.actions import validate_action_inputs, validate_action_inputs_definition

    definition = normalized_definition(tmp_path, input_schema={"type": "object", "properties": {
        "message": {"type": "string", "minLength": 3}}, "required": ["message"]})
    for validate in (lambda values: validate_action_inputs_definition(definition, values),
                     lambda values: validate_action_inputs(SimpleNamespace(definition=definition.to_dict()), values)):
        assert validate({"message": "valid"}) is None
        with pytest.raises(astrid.CapabilityValidationError, match="too short"):
            validate({"message": "x"})
        with pytest.raises(astrid.CapabilityValidationError, match="required"):
            validate({})


@pytest.mark.parametrize("value", [None, False, 0, "", 1.5, [1, {"nested": True}],
                                   {"ok": False, "outputs": "forged", "returncode": 42}])
def test_definition_output_accepts_json_values_without_mutation(tmp_path, value):
    from astrid.sdk.actions import validate_action_output_definition

    definition = normalized_definition(tmp_path)
    before = json.dumps(value)
    assert validate_action_output_definition(definition, value) is None
    assert json.dumps(value) == before


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf"),
                                   Path("output.txt"), {1: "bad key"}, {"nested": {1, 2}},
                                   (1, 2), object(), {"nested": float("nan")}])
def test_definition_output_rejects_lossy_or_nonfinite_values(tmp_path, value):
    from astrid.sdk.actions import validate_action_output_definition

    definition = normalized_definition(tmp_path)
    with pytest.raises(astrid.CapabilityValidationError, match="strict JSON"):
        validate_action_output_definition(definition, value)


def test_definition_output_rejects_cycles_and_dataclasses(tmp_path):
    from dataclasses import dataclass
    from astrid.sdk.actions import validate_action_output_definition

    @dataclass
    class Value:
        answer: int
    definition = normalized_definition(tmp_path)
    cycle = []
    cycle.append(cycle)
    for value in (cycle, Value(42)):
        with pytest.raises(astrid.CapabilityValidationError, match="strict JSON"):
            validate_action_output_definition(definition, value)


def test_definition_output_enforces_object_and_boolean_schemas(tmp_path):
    from astrid.sdk.actions import validate_action_output_definition

    definition = normalized_definition(tmp_path, output_schema={"type": "object", "properties": {
        "answer": {"type": "integer"}}, "required": ["answer"], "additionalProperties": False})
    assert validate_action_output_definition(definition, {"answer": 42}) is None
    for value in ({"answer": "wrong"}, {"answer": 42, "ok": True}, 42):
        with pytest.raises(astrid.CapabilityValidationError):
            validate_action_output_definition(definition, value)
    definition = normalized_definition(tmp_path, output_schema=False)
    with pytest.raises(astrid.CapabilityValidationError):
        validate_action_output_definition(definition, None)


def test_definition_output_uses_contained_local_schema_references(tmp_path):
    from dataclasses import replace
    from astrid.sdk.actions import validate_action_output_definition

    definition = normalized_definition(tmp_path, output_schema=True)
    root = Path(definition.metadata["pack_root"])
    (root / "actions/answer.schema.json").write_text('{"type":"integer"}')
    metadata = dict(definition.metadata, action_outputs_schema={"$ref": "answer.schema.json"},
                    action_outputs_schema_base=(root / "actions/result.schema.json").as_uri())
    definition = replace(definition, metadata=metadata)
    assert validate_action_output_definition(definition, 42) is None
    with pytest.raises(astrid.CapabilityValidationError):
        validate_action_output_definition(definition, "wrong")
    for ref in ("../../outside.schema.json", "https://example.invalid/schema.json"):
        definition = replace(definition, metadata=dict(metadata, action_outputs_schema={"$ref": ref}))
        with pytest.raises(astrid.CapabilityValidationError, match="could not be resolved"):
            validate_action_output_definition(definition, 42)
