"""F04: compose admitted v2 executors and v3 actions before graph validation."""

from __future__ import annotations

import json

import pytest
import yaml

import astrid
from astrid.core.execution.executor import registry as executor_registry
from astrid.core.execution.orchestrator import registry as orchestrator_registry
from astrid.core.pack import discovery as pack_discovery
from astrid.core.pack.loader import load_pack_manifest
from astrid.sdk.discovery import _load_executor_registry


def _write_pack(parent, pack_id, *, version=3, dependency=None, visibility="public"):
    root = parent / pack_id
    root.mkdir(parents=True)
    manifest = {"schema_version": version, "id": pack_id, "name": pack_id, "version": "1.0.0"}
    if version == 3:
        (root / "actions").mkdir()
        (root / "actions/render.py").write_text("raise AssertionError('discovery imported action')\n")
        manifest["actions"] = {"render": {
            "description": "Render fixture.",
            "invocation": {"kind": "python", "path": "actions/render.py", "function": "render"},
            "inputs": [], "outputs": [],
            "metadata": {"visibility": visibility, "project_scope": "optional"},
            "graph": {"depends_on": [] if dependency is None else [dependency]},
        }}
    else:
        manifest["content"] = {"executors": "executors", "orchestrators": "orchestrators"}
        executor_root = root / "executors/review"
        executor_root.mkdir(parents=True)
        (executor_root / "executor.yaml").write_text(yaml.safe_dump({
            "id": f"{pack_id}.review", "name": "Review", "kind": "external", "version": "1",
            "command": {"argv": ["echo", "review"]},
            "metadata": {"project_scope": "optional"},
            "graph": {"depends_on": [] if dependency is None else [dependency]},
        }))
        orchestrator_root = root / "orchestrators/compose"
        orchestrator_root.mkdir(parents=True)
        (orchestrator_root / "orchestrator.yaml").write_text(yaml.safe_dump({
            "id": f"{pack_id}.compose", "name": "Compose", "kind": "built_in", "version": "1",
            "runtime": {"kind": "command", "command": {"argv": ["echo", "compose"]}},
            "child_executors": [] if dependency is None else [dependency],
        }))
    (root / "pack.yaml").write_text(yaml.safe_dump(manifest))
    return root


@pytest.fixture
def isolated_sources(monkeypatch, tmp_path):
    monkeypatch.setenv("ASTRID_SOURCE_STATE", str(tmp_path / "absent-source-state.json"))
    monkeypatch.delenv("ASTRID_PACKS_PATH", raising=False)
    # Keep the real layered discovery and manifest loaders; isolate source packs
    # so this bounded corpus also reproduces the defect before the SDK fix.
    for module in (executor_registry, orchestrator_registry, pack_discovery):
        monkeypatch.setattr(module, "discover_packs", lambda *args, **kwargs: ())


@pytest.fixture
def mixed_roots(tmp_path, isolated_sources):
    legacy = _write_pack(tmp_path, "legacy", version=2, dependency="modern.render")
    modern = _write_pack(tmp_path, "modern")
    return str(legacy), str(modern)


def test_v2_graph_and_orchestrator_resolve_v3_action_without_id_changes(mixed_roots):
    result = astrid.discover(kind="action", extra_pack_roots=mixed_roots)
    assert {action.id for action in result.actions} == {"legacy.review", "legacy.compose", "modern.render"}
    review = astrid.get_capability("legacy.review", kind="action", extra_pack_roots=mixed_roots)
    render = astrid.get_capability("modern.render", kind="executor", extra_pack_roots=mixed_roots)
    compose = astrid.get_capability("legacy.compose", kind="action", extra_pack_roots=mixed_roots)
    assert review.id == review.handle.canonical_id == "legacy.review"
    assert render.id == render.handle.canonical_id == "modern.render"
    assert review.definition["graph"]["depends_on"] == ["modern.render"]
    assert compose.definition["child_executors"] == ["modern.render"]
    preview = astrid.invoke("legacy.review", kind="action", extra_pack_roots=mixed_roots, dry_run=True)
    assert preview.ok and preview.capability_id == "legacy.review"
    assert preview.kernel_task_id is None


@pytest.mark.parametrize("include_internal,expected_calls", [(True, 1), (False, 2)])
def test_complete_graph_validated_once_before_existing_public_projection(
    mixed_roots, monkeypatch, include_internal, expected_calls,
):
    original = executor_registry.ExecutorRegistry.validate_all
    calls = []

    def validate(registry):
        calls.append(set(registry.as_mapping()))
        return original(registry)

    monkeypatch.setattr(executor_registry.ExecutorRegistry, "validate_all", validate)
    _load_executor_registry(extra_pack_roots=mixed_roots, include_internal=include_internal)
    assert calls == [{"legacy.review", "modern.render"}] * expected_calls


@pytest.mark.parametrize("owner", ["legacy", "modern"])
@pytest.mark.parametrize("edge", ["unknown.action", "self"])
@pytest.mark.parametrize("include_internal", [True, False])
def test_composed_graph_still_rejects_unknown_and_self_edges(
    tmp_path, isolated_sources, owner, edge, include_internal,
):
    bad_edge = f"{owner}.{'review' if owner == 'legacy' else 'render'}" if edge == "self" else edge
    legacy = _write_pack(tmp_path, "legacy", version=2,
                         dependency=bad_edge if owner == "legacy" else "modern.render")
    modern = _write_pack(tmp_path, "modern", dependency=bad_edge if owner == "modern" else None)
    match = "cannot depend on itself" if edge == "self" else "depends on unknown executor 'unknown.action'"
    with pytest.raises(executor_registry.ExecutorRegistryError, match=match):
        _load_executor_registry(extra_pack_roots=(str(legacy), str(modern)), include_internal=include_internal)


def test_internal_action_filtering_and_public_dependency_validation(tmp_path, isolated_sources):
    modern = _write_pack(tmp_path, "modern", visibility="internal")
    assert "modern.render" in _load_executor_registry(extra_pack_roots=(str(modern),), include_internal=True).as_mapping()
    assert "modern.render" not in _load_executor_registry(extra_pack_roots=(str(modern),)).as_mapping()
    legacy = _write_pack(tmp_path, "legacy", version=2, dependency="modern.render")
    roots = str(legacy), str(modern)
    assert _load_executor_registry(extra_pack_roots=roots, include_internal=True).get("legacy.review")
    with pytest.raises(executor_registry.ExecutorRegistryError, match="unknown executor 'modern.render'"):
        _load_executor_registry(extra_pack_roots=roots)


def test_invalid_internal_action_is_validated_before_filtering(tmp_path, isolated_sources):
    modern = _write_pack(tmp_path, "modern", visibility="internal", dependency="unknown.action")
    with pytest.raises(executor_registry.ExecutorRegistryError, match="unknown executor 'unknown.action'"):
        _load_executor_registry(extra_pack_roots=(str(modern),))


@pytest.mark.parametrize("version,local_id", [(2, "review"), (3, "render")])
def test_source_extra_env_order_preserves_winner_and_conflict_custody(
    tmp_path, isolated_sources, monkeypatch, version, local_id,
):
    source = _write_pack(tmp_path / "source", "overlap", version=version)
    extra = _write_pack(tmp_path / "extra", "overlap", version=version)
    env = _write_pack(tmp_path / "env", "overlap", version=version)
    pack = load_pack_manifest(source / "pack.yaml")
    for module in (executor_registry, orchestrator_registry, pack_discovery):
        monkeypatch.setattr(module, "discover_packs", lambda *args, **kwargs: (pack,))
    monkeypatch.setenv("ASTRID_PACKS_PATH", str(env))
    registry = _load_executor_registry(extra_pack_roots=(str(extra),), include_internal=True)
    identity = f"overlap.{local_id}"
    conflict, = registry.conflicts()
    assert conflict.key == identity
    assert registry.get(identity).metadata["pack_root"] == str(source)
    assert [definition.metadata["pack_root"] for definition in conflict.shadowed] == [str(extra), str(env)]
    assert all(definition.metadata["priority"] == 30 for definition in (conflict.winner, *conflict.shadowed))


def test_composition_preserves_existing_v2_before_v3_tie_break(tmp_path, isolated_sources, monkeypatch):
    source = _write_pack(tmp_path / "source", "modern")
    pack = load_pack_manifest(source / "pack.yaml")
    monkeypatch.setattr(executor_registry, "discover_packs", lambda *args, **kwargs: (pack,))
    legacy = {"id": "modern.render", "name": "Legacy winner", "kind": "built_in", "version": "1",
              "metadata": {"priority": 30, "source": "pack", "pack_root": "legacy-extra-root"}}
    monkeypatch.setattr(executor_registry, "_load_pack_executors_from_packs", lambda packs: (legacy,))
    registry = _load_executor_registry(include_internal=True)
    assert registry.get("modern.render").name == "Legacy winner"
    assert registry.get("modern.render").metadata["pack_root"] == "legacy-extra-root"
    conflict, = registry.conflicts()
    assert conflict.shadowed[0].metadata["pack_root"] == str(source)


@pytest.mark.parametrize("priority,expected", [(10, "Catalog"), (30, "Legacy"), (40, "Legacy")])
def test_banodoco_registration_order_and_priority_preserved(
    tmp_path, isolated_sources, monkeypatch, priority, expected,
):
    from astrid.core.execution.executor import banodoco_catalog

    modern = _write_pack(tmp_path, "modern")
    legacy = {"id": "modern.render", "name": "Legacy", "kind": "built_in", "version": "1"}
    catalog = {**legacy, "name": "Catalog", "metadata": {"source": "banodoco_catalog", "priority": priority}}
    monkeypatch.setattr(executor_registry, "_load_pack_executors_from_packs", lambda packs: (legacy,))
    config = banodoco_catalog.BanodocoCatalogConfig(enabled=True)
    calls = []

    def load_catalog(received):
        calls.append(received)
        return (catalog,)

    monkeypatch.setattr(banodoco_catalog, "load_banodoco_catalog_executors", load_catalog)
    registry = _load_executor_registry(extra_pack_roots=(str(modern),), banodoco_config=config, include_internal=True)
    assert calls == [config]
    assert registry.get("modern.render").name == expected
    conflict, = registry.conflicts()
    assert [definition.name for definition in (conflict.winner, *conflict.shadowed)] == {
        10: ["Catalog", "Legacy", "render"],
        30: ["Legacy", "Catalog", "render"],
        40: ["Legacy", "render", "Catalog"],
    }[priority]


def test_core_loader_composes_the_mixed_inventory_by_default(mixed_roots):
    registry = executor_registry.load_default_registry(extra_pack_roots=mixed_roots)
    assert registry.get("legacy.review").graph.depends_on == ("modern.render",)
    assert registry.get("modern.render").metadata["action_invocation"]["kind"] == "python"
    assert len(tuple(registry._iter_all())) == 2
    assert not registry.conflicts()


def test_core_loader_validates_additional_definitions_at_registration(tmp_path, isolated_sources):
    from astrid.core.execution.executor.schema import ExecutorValidationError

    with pytest.raises(ExecutorValidationError):
        executor_registry.load_default_registry(additional_executors=({"id": "invalid.action"},))


def test_selected_sources_resolve_editorial_rendering_and_reject_unrelated_bad_edge(tmp_path, monkeypatch):
    state = tmp_path / "absent-source-state.json"
    monkeypatch.setenv("ASTRID_SOURCE_STATE", str(state))
    monkeypatch.delenv("ASTRID_PACKS_PATH", raising=False)
    registry = _load_executor_registry(include_internal=True)
    render = registry.get("rendering.render")
    review = registry.get("editorial.editor_review")
    assert render.metadata["action_invocation"]["kind"] == "command"
    assert "rendering.render" in review.graph.depends_on
    discovered = astrid.discover()
    assert {"rendering.render", "editorial.editor_review"} <= {action.id for action in discovered.executors}
    bad = _write_pack(tmp_path, "unrelated", dependency="unknown.action")
    with pytest.raises(executor_registry.ExecutorRegistryError, match="unknown executor 'unknown.action'"):
        astrid.discover(kind="action", extra_pack_roots=(str(bad),))
    assert not state.exists()


def test_core_and_sdk_use_the_same_normalizer_and_inventory_once(mixed_roots, monkeypatch):
    from astrid.core.execution.executor.actions import action_executor_definition
    from astrid.sdk.actions import action_executor_definition as sdk_normalizer

    assert sdk_normalizer is action_executor_definition
    original = executor_registry._discover_executor_packs
    inventories = []

    def discover(**kwargs):
        packs = original(**kwargs)
        inventories.append(packs)
        return packs

    monkeypatch.setattr(executor_registry, "_discover_executor_packs", discover)
    # The SDK action composition must no longer perform a second inventory scan.
    monkeypatch.setattr("astrid.sdk.discovery._discover_pack_inventory",
                        lambda **kwargs: pytest.fail("SDK rescanned actions"))
    registry = _load_executor_registry(extra_pack_roots=mixed_roots, include_internal=True)
    assert len(inventories) == 1
    assert len(tuple(registry._iter_all())) == 2
    assert not registry.conflicts()


def test_shared_normalization_keeps_contracts_and_runtime_metadata(tmp_path, isolated_sources):
    from astrid.core.execution.executor.actions import action_executor_definition
    from astrid.core.pack.discovery import DiscoveredPack

    root = _write_pack(tmp_path, "modern")
    inputs = {"type": "object", "required": ["title"], "properties": {
        "title": {"type": "string", "description": "Title"},
        "settings": {"type": "object", "default": {}},
    }}
    outputs = {"type": "object", "properties": {"ok": {"type": "boolean"}}}
    (root / "inputs.json").write_text(json.dumps(inputs))
    pack = load_pack_manifest(root / "pack.yaml")
    action = {**pack.actions["render"], "inputs": "inputs.json", "outputs": outputs,
              "metadata": {"visibility": "internal", "runtime_module": "example.runtime"},
              "external_runtime": {"mode": "api"},
              "invocation": {"kind": "command", "command": {"argv": ["echo", "fixture"]}}}
    discovered = DiscoveredPack(pack=pack, source_kind="extra", priority_index=0)
    definition = action_executor_definition(discovered, "render", action)
    assert definition.id == "modern.render"
    assert definition.metadata["runtime_module"] == "example.runtime"
    assert definition.metadata["visibility"] == "internal"
    assert definition.metadata["external_runtime"] == {"mode": "api"}
    assert definition.metadata["action_inputs_schema"] == inputs
    assert definition.metadata["action_inputs_schema_base"] == (root / "inputs.json").as_uri()
    assert definition.metadata["action_outputs_schema"] == outputs
    assert [(port.name, port.type, port.required) for port in definition.inputs] == [
        ("title", "string", True), ("settings", "json", False),
    ]
    assert not definition.outputs
    assert action_executor_definition(pack, "render", action) == definition


def test_core_normalization_rejects_contract_escape_and_invalid_definition(tmp_path, isolated_sources):
    from astrid.core.execution.executor.actions import action_executor_definition
    from astrid.core.execution.executor.schema import ExecutorValidationError

    root = _write_pack(tmp_path, "modern")
    pack = load_pack_manifest(root / "pack.yaml")
    with pytest.raises(ExecutorValidationError, match="contract escapes pack root"):
        action_executor_definition(pack, "render", {**pack.actions["render"], "inputs": "../escaped.json"})
    with pytest.raises(ExecutorValidationError):
        action_executor_definition(pack, "render", {**pack.actions["render"], "inputs": [{"name": "bad", "type": "no-type"}]})


def test_raw_orchestrator_default_forwards_relative_roots_and_catalog_scope(
    tmp_path, isolated_sources, monkeypatch,
):
    from astrid.core.execution.executor import banodoco_catalog

    project = tmp_path / "project"
    legacy = _write_pack(project / "custom", "legacy", version=2, dependency="modern.render")
    modern = _write_pack(project / "custom", "modern")
    roots = ("custom/legacy", "custom/modern")
    config = banodoco_catalog.BanodocoCatalogConfig(enabled=True)
    catalog = {"id": "modern.render", "name": "Catalog winner", "version": "1",
               "kind": "built_in", "metadata": {"priority": 10}}
    received = []
    monkeypatch.setattr(banodoco_catalog, "load_banodoco_catalog_executors", lambda actual: (catalog,))
    original = orchestrator_registry.load_default_executor_registry

    def load(**kwargs):
        received.append(kwargs)
        return original(**kwargs)

    monkeypatch.setattr(orchestrator_registry, "load_default_executor_registry", load)
    registry = orchestrator_registry.load_default_registry(
        project_root=project, extra_pack_roots=roots, banodoco_config=config,
    )
    assert received == [{"project_root": project, "extra_pack_roots": roots, "banodoco_config": config}]
    assert registry.get("legacy.compose").child_executors == ("modern.render",)
    assert registry._executor_registry.get("modern.render").name == "Catalog winner"
    assert registry._executor_registry.get("legacy.review").metadata["pack_root"] == str(legacy)
    conflict, = registry._executor_registry.conflicts()
    assert conflict.shadowed[0].metadata["pack_root"] == str(modern)


def test_raw_orchestrator_explicit_executor_registry_wins(mixed_roots, monkeypatch):
    supplied = executor_registry.ExecutorRegistry(({
        "id": "modern.render", "name": "Explicit", "kind": "built_in", "version": "1",
    },))
    monkeypatch.setattr(orchestrator_registry, "load_default_executor_registry",
                        lambda **kwargs: pytest.fail("explicit registry was replaced"))
    registry = orchestrator_registry.load_default_registry(
        executor_registry=supplied, extra_pack_roots=mixed_roots, banodoco_config=object(),
    )
    assert registry._executor_registry is supplied
    assert registry.get("legacy.compose").child_executors == ("modern.render",)
