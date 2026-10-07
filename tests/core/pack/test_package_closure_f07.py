from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import yaml

from scripts.reshape.package_closure import (
    check_source_resource_closure,
    check_staged_resource_parity,
    declared_source_resource_paths,
)


def _write(path: Path, text: str = "file\n") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    packs = repo / "astrid" / "packs"
    demo = packs / "demo"
    legacy = packs / "legacy"

    _write(
        demo / "pack.yaml",
        yaml.safe_dump(
            {
                "schema_version": 3,
                "id": "demo",
                "name": "Demo",
                "version": "1.0.0",
                "actions": {
                    "echo": {
                        "description": "Echo a value.",
                        "invocation": {
                            "kind": "python",
                            "path": "actions/echo.py",
                            "function": "echo",
                        },
                        "inputs": [{"name": "value", "type": "string"}],
                        "outputs": [{"name": "result", "type": "string"}],
                    }
                },
                "ui": {
                    "editor": {
                        "type": "editor",
                        "entry": "ui/editor.tsx",
                    }
                },
                "rendering": {
                    "animations/fade": {
                        "type": "element",
                        "path": "rendering/visual/fade/custom.yaml",
                    }
                },
                "documentation": {"kind": "skill", "path": "docs/SKILL.md"},
                "resources": [
                    {"kind": "example", "path": "shared/example.json"},
                    {"kind": "marker", "path": "shared/declared/.gitkeep"},
                    {
                        "kind": "documentation",
                        "path": "actions/example/STAGE.md",
                    },
                ],
                "authoring_only": [
                    {"kind": "private", "path": "docs/private", "reason": "Local notes."}
                ],
            }
        ),
    )
    _write(demo / "actions/echo.py", "def echo(value):\n    return value\n")
    _write(demo / "ui/editor.tsx", "export default function Editor() {}\n")
    _write(
        demo / "rendering/visual/fade/custom.yaml",
        yaml.safe_dump({"id": "fade", "kind": "animation", "pack_id": "demo"}),
    )
    _write(demo / "shared/example.json", '{"ok": true}\n')
    _write(demo / "shared/declared/.gitkeep", "")
    _write(demo / "actions/example/STAGE.md", "action guide\n")
    _write(demo / "docs/SKILL.md", "---\nname: demo\ndescription: Demo.\n---\n")
    _write(demo / "docs/references/guide.md", "# Guide\n")
    _write(demo / "docs/templates/example.json", "{}\n")
    _write(demo / "docs/assets/icon.svg", "<svg/>\n")
    _write(demo / "docs/private/notes.md", "do not ship\n")
    _write(demo / "docs/tests/fixture.md", "test-only\n")
    _write(demo / "docs/build/generated.js", "generated\n")

    _write(
        legacy / "pack.yaml",
        yaml.safe_dump(
            {
                "schema_version": 2,
                "id": "legacy",
                "name": "Legacy",
                "version": "1.0.0",
                "content": {"runtime": "runtime"},
                "documentation": {"kind": "skill", "path": "skill/SKILL.md"},
            }
        ),
    )
    _write(legacy / "runtime/module.py", "VALUE = 1\n")
    _write(legacy / "runtime/manifest.yaml", "kind: runtime\n")
    _write(legacy / "runtime/assets/example.json", '{"ok": true}\n')
    _write(legacy / "runtime/assets/.gitkeep", "")
    _write(legacy / "runtime/STAGE.md", "authoring guide\n")
    _write(legacy / "runtime/requirements.txt", "example\n")
    _write(legacy / "runtime/__pycache__/module.cpython-311.pyc", "cache\n")
    _write(legacy / "skill/SKILL.md", "---\nname: legacy\ndescription: Legacy.\n---\n")
    _write(legacy / "skill/references/guide.md", "# Legacy guide\n")

    _write(packs / "_core/skill/SKILL.md", "# Astrid\n")
    _write(packs / "_core/skill/references/core.md", "# Core\n")
    _write(packs / "_core/skill/__pycache__/ignored.pyc", "cache\n")
    return repo


def test_closure_projects_v3_paths_docs_bundle_content_and_exclusions(tmp_path: Path) -> None:
    repo = _repo(tmp_path)

    closure = check_source_resource_closure(repo)
    assert closure.ok, closure.errors
    assert closure.paths == tuple(sorted(closure.digests))
    assert declared_source_resource_paths(repo) == closure.paths

    expected = {
        "astrid/packs/demo/pack.yaml",
        "astrid/packs/demo/actions/echo.py",
        "astrid/packs/demo/ui/editor.tsx",
        "astrid/packs/demo/rendering/visual/fade/custom.yaml",
        "astrid/packs/demo/shared/example.json",
        "astrid/packs/demo/shared/declared/.gitkeep",
        "astrid/packs/demo/actions/example/STAGE.md",
        "astrid/packs/demo/docs/SKILL.md",
        "astrid/packs/demo/docs/references/guide.md",
        "astrid/packs/demo/docs/templates/example.json",
        "astrid/packs/demo/docs/assets/icon.svg",
        "astrid/packs/legacy/runtime/module.py",
        "astrid/packs/legacy/runtime/manifest.yaml",
        "astrid/packs/legacy/runtime/assets/example.json",
        "astrid/packs/legacy/skill/SKILL.md",
        "astrid/packs/legacy/skill/references/guide.md",
        "astrid/packs/_core/skill/SKILL.md",
        "astrid/packs/_core/skill/references/core.md",
    }
    assert expected <= set(closure.paths)
    for excluded in (
        "astrid/packs/demo/docs/private/notes.md",
        "astrid/packs/demo/docs/tests/fixture.md",
        "astrid/packs/demo/docs/build/generated.js",
        "astrid/packs/legacy/runtime/STAGE.md",
        "astrid/packs/legacy/runtime/requirements.txt",
        "astrid/packs/legacy/runtime/assets/.gitkeep",
        "astrid/packs/legacy/runtime/__pycache__/module.cpython-311.pyc",
        "astrid/packs/_core/skill/__pycache__/ignored.pyc",
    ):
        assert excluded not in closure.paths

    skill = repo / "astrid/packs/demo/docs/SKILL.md"
    assert closure.digests["astrid/packs/demo/docs/SKILL.md"] == hashlib.sha256(
        skill.read_bytes()
    ).hexdigest()
    action_stage = repo / "astrid/packs/demo/actions/example/STAGE.md"
    assert closure.digests["astrid/packs/demo/actions/example/STAGE.md"] == hashlib.sha256(
        action_stage.read_bytes()
    ).hexdigest()


def test_staged_parity_checks_bytes_without_requiring_a_build(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    closure = check_source_resource_closure(repo)
    staged = tmp_path / "staged"
    for relative in closure.paths:
        source = repo / relative
        target = staged / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)

    assert check_staged_resource_parity(repo, staged).ok
    assert check_staged_resource_parity(repo, staged / "astrid").ok

    action_stage = staged / "astrid/packs/demo/actions/example/STAGE.md"
    action_stage.write_text(
        action_stage.read_text(encoding="utf-8") + "changed\n", encoding="utf-8"
    )
    parity = check_staged_resource_parity(repo, staged)
    assert parity.mismatched == ("astrid/packs/demo/actions/example/STAGE.md",)
    assert parity.missing == ()

    action_stage.write_bytes(
        (repo / "astrid/packs/demo/actions/example/STAGE.md").read_bytes()
    )
    changed = staged / "astrid/packs/demo/docs/SKILL.md"
    changed.write_text(changed.read_text(encoding="utf-8") + "changed\n", encoding="utf-8")
    (staged / "astrid/packs/demo/docs/references/guide.md").unlink()
    parity = check_staged_resource_parity(repo, staged)
    assert parity.mismatched == ("astrid/packs/demo/docs/SKILL.md",)
    assert parity.missing == ("astrid/packs/demo/docs/references/guide.md",)

    changed.write_bytes((repo / "astrid/packs/demo/docs/SKILL.md").read_bytes())
    (staged / "astrid/packs/demo/docs/references/guide.md").write_bytes(
        (repo / "astrid/packs/demo/docs/references/guide.md").read_bytes()
    )
    action_stage.unlink()
    parity = check_staged_resource_parity(repo, staged)
    assert parity.mismatched == ()
    assert parity.missing == ("astrid/packs/demo/actions/example/STAGE.md",)


def test_digest_projection_is_json_serializable(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    closure = check_source_resource_closure(repo)
    payload = {
        "paths": closure.paths,
        "digests": dict(closure.digests),
        "errors": closure.errors,
    }
    assert json.loads(json.dumps(payload))["digests"]
