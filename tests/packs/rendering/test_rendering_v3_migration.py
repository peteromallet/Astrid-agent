"""The migrated source must carry the complete declared runtime/UI/doc closure."""

import hashlib
import importlib
import json
import shutil
from pathlib import Path

import yaml
import pytest

from astrid.core.element.schema import load_element_definition
from astrid.core.pack.canonical import validate_canonical_pack
from astrid.core.pack.loader import load_pack_manifest
from astrid.core.pack.walkers import iter_element_manifest_paths
from scripts.reshape.package_closure import check_source_resource_closure

ROOT = Path(__file__).resolve().parents[3]
PACK = ROOT / "astrid/packs/rendering"
ACTION_IDS = {
    "assemble_timeline",
    "html_canvas_effect",
    "render",
    "sprite_sheet",
    "timeline_storyboard",
    "timeline_visualize",
}
ELEMENT_IDS = {
    "animations/fade",
    "animations/fade-up",
    "animations/scale-in",
    "animations/slide-left",
    "animations/slide-up",
    "animations/type-on",
    "effects/audio-reactive-colour",
    "effects/text-card",
    "transitions/cross-fade",
    "transitions/fade",
}


def test_one_declared_authority_preserves_public_ids_and_ui_fences():
    entry = validate_canonical_pack(PACK)
    definition = entry.definition
    assert definition.schema_version == 3
    assert set(definition.actions) == ACTION_IDS
    assert {
        key for key, item in definition.rendering.items() if item["type"] == "element"
    } == ELEMENT_IDS
    assert {key for key, item in definition.rendering.items() if item["type"] == "renderer"} == {
        "ffmpeg",
        "remotion",
        "threejs",
    }
    assert {key for key, item in definition.rendering.items() if item["type"] == "finalizer"} == {
        "ffmpeg-finalizer",
        "ffmpeg-compositor",
    }
    assert definition.content == {} and definition.extensions == {}
    assert not (PACK / "executors").exists()
    assert not (PACK / "backends").exists()
    assert not (PACK / "finalizers").exists()
    assert not (PACK / "editor/live-scenes").exists()
    assert not (PACK / "skill").exists()
    ui = definition.ui["live-scenes"]
    assert ui["entry"] == "ui/live-scenes/extension.tsx"
    assert ui["target"] == "video-editor"
    assert ui["compatibility"]["sdk"] == "1"
    source = (PACK / ui["entry"]).read_text()
    assert "allowBrowserExport: false" in source and "allowWorkerExport: false" in source
    assert "import('./SceneSurface')" in source and "import('./authoring')" in source
    assert len(tuple(PACK.rglob("SKILL.md"))) == 1
    frontmatter = yaml.safe_load((PACK / "docs/SKILL.md").read_text().split("---", 2)[1])
    assert frontmatter["name"] == "rendering" and frontmatter["description"]


def test_render_declares_mutually_exclusive_file_and_managed_modes():
    manifest = yaml.safe_load((PACK / "pack.yaml").read_text())
    render = manifest["actions"]["render"]
    inputs = {item["name"]: item for item in render["inputs"]}
    assert inputs["timeline"]["type"] == "file"
    assert inputs["timeline"]["required"] is False
    assert inputs["timeline_ref"]["type"] == "string"
    assert inputs["timeline_ref"]["required"] is False
    assert inputs["expected_version"]["required"] is False
    command = render["invocation"]["command"]["argv"]
    assert command[command.index("--timeline") + 1] == "{timeline}"
    assert "--timeline-ref" not in command
    assert inputs["media_dependency"]["type"] == "file"
    assert inputs["media_dependency"]["required"] is False
    bindings = render["invocation"]["command"]["input_args"]
    assert [item for item in bindings if item["input"] == "media_dependency"] == [
        {"input": "media_dependency", "flag": "--media-dependency", "optional": True}]
    for name in ("assets_registry", "theme", "materialized_root", "materialized_objects"):
        assert inputs[name]["required"] is False


@pytest.fixture
def render_action():
    from astrid.core.pack.entrypoint import canonical_runtime_entrypoint
    with canonical_runtime_entrypoint("rendering.render"):
        return importlib.import_module("astrid.packs.rendering.actions.render.run")


def _single_media_registry(tmp_path, *, bound=False):
    data = b"one admitted media object"
    sha = hashlib.sha256(data).hexdigest()
    dependency = tmp_path / "admitted-media.mp4"
    dependency.write_bytes(data)
    asset = {"object_id": "sha256:" + sha, "content_sha256": sha,
             "size": len(data), "media_type": "video/mp4"}
    if bound: asset["binding"] = "media_dependency"
    registry = tmp_path / "assets.json"
    registry.write_text(json.dumps({"assets": {"first": asset}}))
    return registry, dependency, asset


def test_media_dependency_preserves_untriggered_legacy_multi_object_registry(render_action, tmp_path):
    registry, _, first = _single_media_registry(tmp_path)
    second = {"object_id": "legacy-object-id", "digest": "legacy-digest"}
    registry.write_text(json.dumps({"assets": {"first": first, "second": second}}))
    assert render_action._validate_media_dependency(registry, None) is None


@pytest.mark.parametrize("bound", [False, True])
def test_media_dependency_accepts_one_identity_at_different_materialized_paths(render_action, tmp_path, bound):
    registry, dependency, asset = _single_media_registry(tmp_path, bound=bound)
    managed = tmp_path / "managed-objects" / "source.mp4"
    managed.parent.mkdir()
    managed.write_bytes(dependency.read_bytes())
    asset["file"] = str(managed)
    registry.write_text(json.dumps({"assets": {"first": asset, "second_alias": dict(asset)}}))
    assert dependency != managed
    assert render_action._validate_media_dependency(registry, dependency) is None
    assert json.loads(registry.read_text())["assets"]["first"]["file"] == str(managed)


def test_media_dependency_binding_without_port_fails_before_render_service(render_action, tmp_path, monkeypatch):
    registry, _, _ = _single_media_registry(tmp_path, bound=True)
    monkeypatch.setattr(render_action, "_default_service", lambda: pytest.fail("invalid media reached renderer"))
    with pytest.raises(ValueError, match="requires the admitted media file"):
        render_action.render(tmp_path / "timeline.json", registry, tmp_path / "hype.mp4")


@pytest.mark.parametrize("change", ["extra_identity", "object_alias", "digest_alias", "object_digest", "size_alias",
                                    "missing_object", "missing_digest", "boolean_size", "empty_assets"])
def test_media_dependency_rejects_missing_additional_and_conflicting_identity(render_action, tmp_path, change):
    registry, dependency, asset = _single_media_registry(tmp_path)
    assets = {"first": asset}
    other = "0" * 64
    if change == "extra_identity": assets["other"] = {**asset, "object_id": "sha256:" + other, "content_sha256": other}
    elif change == "object_alias": asset["media_id"] = "sha256:" + other
    elif change == "digest_alias": asset["digest"] = "sha256:" + other
    elif change == "object_digest": asset["content_sha256"] = other
    elif change == "size_alias": assets["same_object"] = {**asset, "size": asset["size"] + 1}
    elif change == "missing_object": asset.pop("object_id")
    elif change == "missing_digest": asset.pop("content_sha256")
    elif change == "boolean_size": asset["size"] = True
    else: assets = {}
    registry.write_text(json.dumps({"assets": assets}))
    with pytest.raises(ValueError): render_action._validate_media_dependency(registry, dependency)


@pytest.mark.parametrize("mismatch", ["size", "digest", "symlink", "absent"])
def test_media_dependency_rejects_wrong_or_missing_admitted_bytes(render_action, tmp_path, mismatch):
    registry, dependency, _ = _single_media_registry(tmp_path)
    if mismatch == "size": dependency.write_bytes(b"short")
    elif mismatch == "digest": dependency.write_bytes(b"X" * len(dependency.read_bytes()))
    elif mismatch == "symlink":
        original = tmp_path / "original.mp4"
        dependency.rename(original)
        dependency.symlink_to(original)
    else: dependency.unlink()
    with pytest.raises((ValueError, OSError)):
        render_action._validate_media_dependency(registry, dependency)


def test_media_dependency_cli_passes_exact_typed_file_binding(render_action, tmp_path, monkeypatch):
    registry, dependency, _ = _single_media_registry(tmp_path)
    captured = []
    def fake_render(*args, **kwargs):
        captured.append((args, kwargs))
        return args[2]
    monkeypatch.setattr(render_action, "render", fake_render)
    output = tmp_path / "hype.mp4"
    assert render_action.main(["--timeline", str(tmp_path / "timeline.json"), "--assets", str(registry),
                               "--media-dependency", str(dependency), "--out", str(output)]) == 0
    assert captured[0][1]["media_dependency"] == dependency
    assert captured[0][0][1] == registry
    assert captured[0][1]["materialized_root"] is None
    assert captured[0][1]["materialized_objects"] is None


def test_declared_projection_contains_every_owned_code_asset_and_stages_elements(tmp_path):
    closure = check_source_resource_closure(ROOT)
    assert closure.ok, closure.errors
    prefix = "astrid/packs/rendering/"
    paths = {path.removeprefix(prefix) for path in closure.paths if path.startswith(prefix)}
    authored_files = {
        path.relative_to(PACK).as_posix()
        for path in PACK.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc"
    }
    assert paths == authored_files
    staged = tmp_path / "rendering"
    for relative in paths:
        destination = staged / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(PACK / relative, destination)
    staged_entry = validate_canonical_pack(staged)
    source_entry = validate_canonical_pack(PACK)
    assert staged_entry.definition.to_dict() == source_entry.definition.to_dict()
    assert {r.path: r.digest for r in staged_entry.resources} == {
        r.path: r.digest for r in source_entry.resources
    }
    definitions = [
        load_element_definition(
            path.parent, manifest_path=path, kind=kind, source="source", editable=False, priority=0
        )
        for kind, path in iter_element_manifest_paths(load_pack_manifest(staged / "pack.yaml"))
    ]
    assert len(definitions) == 10
    for element in definitions:
        assert element.metadata["pack_id"] == "rendering"
        assert (element.root / "component.tsx").is_file()
        for asset in element.assets:
            assert (element.root / asset.path).is_file()
    assert (staged / "shared/element_contracts.ts").is_file()
    assert (staged / "actions/timeline_visualize/fonts/PowerGrotesk-Regular.ttf").is_file()
    assert (staged / "actions/html_canvas_effect/templates/card/component.tsx").is_file()
    assert (staged / "actions/timeline_visualize/inspector_assets/inspector.js").is_file()
    assert (staged / "docs/templates/two-shot-threejs.ts").is_file()
