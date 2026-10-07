"""V3 declarations bind to the existing rendering and element hosts."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from unittest import mock

import pytest
import yaml

from astrid.core.element import registry as element_registry
from astrid.core.element.catalog import _element_descriptor
from astrid.core.element.schema import ElementValidationError, load_element_definition, to_capability_handle
from astrid.core.pack import PackValidationError, load_pack_manifest, pack_rendering_manifest_paths
from astrid.core.pack.discovery import DiscoveredPack
from astrid.core.pack.walkers import iter_element_manifest_paths, iter_element_roots
from astrid.core.rendering import registry as rendering_registry
from astrid.core.rendering.registry import RenderingRegistryError, load_default_registries
from astrid.core.rendering.service import RenderService, _select_capability
from tests.core.rendering.test_service import FakeTransport, _request


def _write(root: Path, relative: str, payload) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(payload) if isinstance(payload, dict) else payload, encoding="utf-8")
    return path


def _pack(root: Path, *, pack_id: str = "rendering", owned_element: bool = True) -> Path:
    declarations = {}
    for key, role, operation in (
        ("remotion", "renderer", "render"),
        ("ffmpeg", "renderer", "render"),
        ("plan", "planner", "plan"),
        ("ffmpeg-finalizer", "finalizer", "finalize"),
    ):
        path = f"rendering/{key}/descriptor.yaml"
        declarations[key] = {"type": role, "path": path}
        _write(root, path, {
            "schema_version": 1, "protocol_version": 1, "id": f"{pack_id}.{key}",
            "name": key, "version": "1.0.0", "command": ["python3", "rendering/backend.py"],
            "operations": [operation, "support"], "required_permissions": ["subprocess"],
        })
    _write(root, "rendering/backend.py", 'raise AssertionError("discovery imported backend")\n')
    element = {
        "id": "fade", "kind": "animation", "metadata": {"label": "Fade"},
        "schema": {"type": "object", "properties": {"opacity": {"type": "number"}}},
        "defaults": {"opacity": 0.5}, "runtime": {"adapter": "remotion"},
        "assets": {"palette": "assets/palette.json"},
    }
    if owned_element:
        element["pack_id"] = pack_id
    _write(root, "rendering/visual/fade/custom.yaml", element)
    _write(root, "rendering/visual/fade/component.tsx", "export default function Fade() { return null; }\n")
    _write(root, "rendering/visual/fade/assets/palette.json", '{"color":"#ffffff"}\n')
    # A standard filename beside the declaration must not replace its exact file.
    _write(root, "rendering/visual/fade/element.yaml", {**element, "id": "private-helper"})
    declarations["animations/fade"] = {"type": "element", "path": "rendering/visual/fade/custom.yaml"}
    _write(root, "pack.yaml", {
        "schema_version": 3, "id": pack_id, "name": "Rendering fixture", "version": "1.0.0",
        "permissions": [{"id": "subprocess", "reason": "Fixture only."}], "rendering": declarations,
    })
    # Undeclared v2-style content never enters a v3 registry.
    _write(root, "elements/effects/hidden/element.yaml", {**element, "id": "hidden", "kind": "effect"})
    _write(root, "elements/effects/hidden/component.tsx", "export default () => null;\n")
    return root


def _discovered(root: Path, *, source_kind: str = "source", priority: int = 0):
    return DiscoveredPack(pack=load_pack_manifest(root / "pack.yaml"), source_kind=source_kind, priority_index=priority)


def _registries(monkeypatch, *items):
    monkeypatch.setattr(rendering_registry, "discover_pack_metadata", lambda **_kwargs: tuple(items))
    return load_default_registries()


def test_v3_roles_keep_ids_protocols_digests_and_static_discovery(tmp_path, monkeypatch):
    item = _discovered(_pack(tmp_path / "rendering"))
    with mock.patch("subprocess.Popen", side_effect=AssertionError("spawn during discovery")):
        renderers, planners, finalizers = _registries(monkeypatch, item)
    assert {c.id for c in renderers.list()} == {"rendering.ffmpeg", "rendering.remotion"}
    assert [c.id for c in planners.list()] == ["rendering.plan"]
    assert [c.id for c in finalizers.list()] == ["rendering.ffmpeg-finalizer"]
    for registry in (renderers, planners, finalizers):
        for candidate in registry.list():
            assert candidate.manifest.schema_version == candidate.manifest.protocol_version == 1
            assert candidate.pack_root == item.pack.root.resolve()
            assert candidate.manifest.command == ("python3", "rendering/backend.py")
            assert candidate.manifest_digest == hashlib.sha256(candidate.manifest_path.read_bytes()).hexdigest()
            assert registry.resolve_evidence(candidate.id)["trust_method"] == "source_tree"
    assert _select_capability(None, (renderers, planners, finalizers)).kind == "renderer"
    assert _select_capability("rendering.plan", (renderers, planners, finalizers)).kind == "planner"


@pytest.mark.parametrize("selector,backend", [(None, "rendering.remotion"), ("rendering.ffmpeg", "rendering.ffmpeg")])
@pytest.mark.parametrize("finalize", [False, True])
def test_loaded_v3_selection_support_output_and_finalizer(tmp_path, monkeypatch, selector, backend, finalize):
    registries = _registries(monkeypatch, _discovered(_pack(tmp_path / "rendering")))
    transport = FakeTransport()
    service = RenderService(
        registries=registries, transport=transport, validator=lambda result, **_kwargs: result,
        finalizer_id="rendering.ffmpeg-finalizer" if finalize else None,
    )
    output = tmp_path / "published" / "movie.mp4"
    assert service.render_request(_request(tmp_path), selector=selector, out_path=output) == output
    assert transport.calls[:2] == [("support", backend), ("render", backend)]
    expected = "rendering.ffmpeg-finalizer" if finalize else backend
    assert output.read_bytes() == f"{'finalize' if finalize else 'render'}:{expected}:10".encode()
    if finalize:
        assert ("finalize", "rendering.ffmpeg-finalizer") in transport.calls
    else:
        assert len(transport.calls) == 2
    assert Path(f"{output}.provenance.json").is_file()


def test_v3_elements_keep_unqualified_identity_assets_and_editor_projection(tmp_path, monkeypatch):
    item = _discovered(_pack(tmp_path / "rendering"))
    monkeypatch.setattr(element_registry, "discover_pack_metadata", lambda **_kwargs: (item,))
    registry = element_registry.load_default_registry(project_root=tmp_path / "project")
    definition = registry.get("animation", "fade")
    assert [(e.kind, e.id) for e in registry.list()] == [("animations", "fade")]
    assert definition.root == (item.pack.root / "rendering/visual/fade").resolve()
    assert definition.component.is_file()
    assert (definition.root / definition.assets[0].path).read_text() == '{"color":"#ffffff"}\n'
    handle = to_capability_handle(definition)
    assert handle.canonical_id == "animations/fade" and handle.local_id == "fade"
    descriptor = _element_descriptor(definition)
    assert descriptor["id"] == "fade" and descriptor["packId"] == "rendering"
    assert descriptor["kind"] == "animation"
    assert descriptor["defaults"] == {"opacity": 0.5}
    assert descriptor["renderability"]["preview"] == "supported"
    assert descriptor["revision"].startswith("sha256:")
    assert iter_element_roots(item.pack, kind="animation") == (("animations", definition.root),)
    assert iter_element_roots(item.pack, kind="effects") == ()
    assert iter_element_manifest_paths(item.pack)[0][1].name == "custom.yaml"


def test_v3_element_owner_can_be_derived_from_declaration(tmp_path, monkeypatch):
    item = _discovered(_pack(tmp_path / "rendering", owned_element=False))
    monkeypatch.setattr(element_registry, "discover_pack_metadata", lambda **_kwargs: (item,))
    element = element_registry.load_default_registry(project_root=tmp_path / "project").get("animations", "fade")
    assert element.metadata["pack_id"] == "rendering"


@pytest.mark.parametrize("role", ["renderer", "planner", "finalizer"])
def test_v3_nested_protocol_validation_stays_with_existing_host(tmp_path, monkeypatch, role):
    item = _discovered(_pack(tmp_path / "rendering"))
    declaration = next(d for d in item.pack.rendering.values() if d["type"] == role)
    path = item.pack.root / declaration["path"]
    payload = yaml.safe_load(path.read_text())
    payload["operations"] = ["invalid-operation"]
    _write(item.pack.root, declaration["path"], payload)
    with pytest.raises(RenderingRegistryError) as caught:
        _registries(monkeypatch, item)
    assert caught.value.code == "invalid_manifest" and caught.value.capability_kind == role


def test_v3_descriptor_identity_cannot_drift_after_admission(tmp_path, monkeypatch):
    item = _discovered(_pack(tmp_path / "rendering"))
    path = item.pack.root / "rendering/ffmpeg/descriptor.yaml"
    payload = yaml.safe_load(path.read_text())
    payload["id"] = "rendering.other"
    _write(item.pack.root, "rendering/ffmpeg/descriptor.yaml", payload)
    with pytest.raises(RenderingRegistryError, match="must match declared"):
        _registries(monkeypatch, item)


def test_v3_element_identity_cannot_drift_after_admission(tmp_path, monkeypatch):
    item = _discovered(_pack(tmp_path / "rendering"))
    path = item.pack.root / "rendering/visual/fade/custom.yaml"
    payload = yaml.safe_load(path.read_text())
    payload["id"] = "other"
    _write(item.pack.root, "rendering/visual/fade/custom.yaml", payload)
    monkeypatch.setattr(element_registry, "discover_pack_metadata", lambda **_kwargs: (item,))
    with pytest.raises(PackValidationError, match="must match declared"):
        element_registry.load_default_registry(project_root=tmp_path / "project")


def test_v3_env_candidate_is_inspectable_without_shadowing_trusted_source(tmp_path, monkeypatch):
    source = _discovered(_pack(tmp_path / "source" / "rendering"))
    env = _discovered(_pack(tmp_path / "env" / "rendering"), source_kind="env", priority=1)
    renderers, _, _ = _registries(monkeypatch, source, env)
    assert renderers.get("rendering.remotion").pack_root == source.pack.root
    assert len(renderers.inspect("rendering.remotion")) == 2
    assert not renderers.candidates("rendering.remotion", eligible=False)[0].execution_eligible
    renderers, _, _ = _registries(monkeypatch, env)
    with pytest.raises(RenderingRegistryError) as caught:
        renderers.get("rendering.remotion")
    assert caught.value.code == "execution_ineligible"


@pytest.mark.parametrize("role", ["renderer", "element"])
def test_v3_path_projection_rejects_descriptor_symlink_escape(tmp_path, role):
    item = _discovered(_pack(tmp_path / "rendering"))
    key = "remotion" if role == "renderer" else "animations/fade"
    path = item.pack.root / item.pack.rendering[key]["path"]
    outside = tmp_path / "outside.yaml"
    outside.write_bytes(path.read_bytes())
    path.unlink()
    path.symlink_to(outside)
    with pytest.raises(PackValidationError, match="stay within"):
        if role == "renderer":
            pack_rendering_manifest_paths(item.pack)
        else:
            iter_element_manifest_paths(item.pack)


def test_explicit_element_descriptor_must_be_inside_its_element_root(tmp_path):
    root = _pack(tmp_path / "rendering")
    with pytest.raises(ElementValidationError, match="regular file"):
        load_element_definition(
            root / "rendering/visual/fade", kind="animations", source="fixture", editable=False,
            priority=0, manifest_path=root / "rendering/remotion/descriptor.yaml",
        )


def test_v3_missing_element_descriptor_is_not_silently_skipped(tmp_path):
    item = _discovered(_pack(tmp_path / "rendering"))
    (item.pack.root / "rendering/visual/fade/custom.yaml").unlink()
    with pytest.raises(PackValidationError, match="regular file"):
        iter_element_manifest_paths(item.pack)


def test_v3_empty_rendering_does_not_infer_legacy_roots(tmp_path, monkeypatch):
    root = _pack(tmp_path / "rendering")
    payload = yaml.safe_load((root / "pack.yaml").read_text())
    payload.pop("rendering")
    payload["ui"] = {"editor": {"type": "editor", "entry": "ui/extension.tsx"}}
    _write(root, "ui/extension.tsx", "export default {};\n")
    _write(root, "pack.yaml", payload)
    item = _discovered(root)
    assert pack_rendering_manifest_paths(item.pack) == ((), (), ())
    assert iter_element_roots(item.pack) == ()
    assert all(registry.list() == () for registry in _registries(monkeypatch, item))


def test_v2_element_manifest_precedence_is_preserved(tmp_path):
    root = tmp_path / "legacy"
    _write(root, "pack.yaml", {"schema_version": 2, "id": "legacy", "name": "Legacy", "version": "1.0.0", "content": {"elements": "elements"}})
    element_root = root / "elements/animations/fade"
    for name in ("element.yaml", "element.json"):
        _write(root, f"elements/animations/fade/{name}", {"id": "fade", "kind": "animation", "pack_id": "legacy"})
    pack = load_pack_manifest(root / "pack.yaml")
    assert iter_element_manifest_paths(pack) == (("animations", element_root / "element.yaml"),)


def test_v3_aliases_resolve_through_existing_renderer_registry(tmp_path, monkeypatch):
    root = _pack(tmp_path / "rendering")
    payload = yaml.safe_load((root / "pack.yaml").read_text())
    payload["aliases"] = [{"kind": "renderer", "alias": "rendering.compat", "canonical_id": "rendering.remotion"}]
    _write(root, "pack.yaml", payload)
    renderers, _, _ = _registries(monkeypatch, _discovered(root))
    assert renderers.get("rendering.compat").id == "rendering.remotion"
    assert renderers.resolve_evidence("rendering.compat")["alias_chain"] == ["rendering.compat", "rendering.remotion"]
