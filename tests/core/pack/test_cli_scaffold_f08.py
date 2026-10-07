from __future__ import annotations

import json
import sys
from pathlib import Path
from types import ModuleType

import yaml

from astrid.core.pack.cli import main
from astrid.core.pack.discovery import DiscoveredPack
from astrid.core.pack.loader import load_pack_manifest
from astrid.core.pack.validate import validate_pack
from astrid.sdk.actions import action_executor_definition


def _manifest(root: Path) -> dict:
    return yaml.safe_load((root / "pack.yaml").read_text(encoding="utf-8"))


def _normalized_action(root: Path, local_id: str):
    discovered = DiscoveredPack(load_pack_manifest(root / "pack.yaml"), "extra", 0)
    return action_executor_definition(discovered, local_id, discovered.pack.actions[local_id])


def _load_function(path: Path, function: str):
    namespace: dict = {}
    exec(compile(path.read_text(encoding="utf-8"), str(path), "exec"), namespace)
    return namespace[function]


def _assert_scalar_action_contract(root: Path, local_id: str) -> None:
    action = _manifest(root)["actions"][local_id]
    assert action["outputs"] == {"type": "string"}
    definition = _normalized_action(root, local_id)
    assert definition.outputs == ()
    assert definition.metadata["action_outputs_schema"] == {"type": "string"}


def test_new_default_is_a_valid_standalone_action_starter(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)

    assert main(["new", "demo_pack"]) == 0
    root = tmp_path / "demo_pack"
    manifest = _manifest(root)

    assert manifest["schema_version"] == 3
    assert manifest["documentation"] == {"kind": "skill", "path": "docs/SKILL.md"}
    assert manifest["actions"]["echo"]["invocation"]["path"] == "actions/echo.py"
    skill = (root / "docs/SKILL.md").read_text(encoding="utf-8")
    assert skill.startswith("---\n")
    assert "name: demo_pack" in skill
    assert "description:" in skill
    assert "[the authoring reference](references/guide.md)" in skill
    assert "[input template](templates/input.json)" in skill
    _assert_scalar_action_contract(root, "echo")
    assert _load_function(root / "actions/echo.py", "run")("value") == "value"
    assert not (root / "ui").exists()
    assert not (root / "rendering").exists()
    assert not (root / "shared").exists()
    assert validate_pack(root) == ([], [])


def test_wrapper_starter_keeps_external_source_outside_the_pack(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)

    assert main(
        [
            "new",
            "adapter_pack",
            "--starter",
            "wrapper",
            "--dependency",
            "clean-client",
            "--external-module",
            "clean_client",
        ]
    ) == 0
    root = tmp_path / "adapter_pack"
    manifest = _manifest(root)

    assert manifest["dependencies"] == {"python": ["clean-client"]}
    adapter = (root / "actions/adapter.py").read_text(encoding="utf-8")
    assert "import_module(\"clean_client\")" in adapter
    assert "public_api" in adapter
    assert "[the authoring reference](references/guide.md)" in (
        root / "docs/SKILL.md"
    ).read_text(encoding="utf-8")
    _assert_scalar_action_contract(root, "adapt")
    external = ModuleType("clean_client")
    external.public_api = lambda value: value
    monkeypatch.setitem(sys.modules, "clean_client", external)
    assert _load_function(root / "actions/adapter.py", "run")("value") == "value"
    assert not (root / "vendor").exists()
    assert validate_pack(root) == ([], [])


def test_nested_starter_uses_pack_subpath_and_can_add_roles(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)

    assert main(
        [
            "new",
            "astrid",
            "--starter",
            "nested",
            "--role",
            "action",
            "--role",
            "ui",
            "--role",
            "shared",
        ]
    ) == 0
    root = tmp_path / "integrations" / "astrid"
    manifest = _manifest(root)

    assert manifest["documentation"] == {"kind": "skill", "path": "docs/SKILL.md"}
    assert set(manifest["actions"]) == {"run"}
    assert set(manifest["ui"]) == {"editor"}
    assert manifest["ui"]["editor"]["target"] == "video-editor"
    assert not (root / "rendering").exists()
    source_example = json.loads(
        (root / "docs/references/source-declaration.example.json").read_text(encoding="utf-8")
    )
    assert source_example["sources"][0]["pack_subpath"] == "integrations/astrid"
    assert validate_pack(root) == ([], [])


def test_validate_json_reuses_the_author_validator(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    assert main(["new", "demo_pack"]) == 0
    capsys.readouterr()

    assert main(["validate", "demo_pack", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["valid"] is True
    assert payload["errors"] == []
    assert payload["path"].endswith("/demo_pack")


def test_inspect_human_output_shows_declared_docs_and_roles(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    monkeypatch.chdir(tmp_path)
    assert main(["new", "demo_pack", "--role", "action", "--role", "ui"]) == 0
    capsys.readouterr()

    assert main(["inspect", "demo_pack", "--pack-root", str(tmp_path)]) == 0
    output = capsys.readouterr().out
    assert "documentation: {'kind': 'skill', 'path': 'docs/SKILL.md'}" in output
    assert "actions:" in output
    assert "ui:" in output
