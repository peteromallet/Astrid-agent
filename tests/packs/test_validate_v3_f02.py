from __future__ import annotations

import json
from pathlib import Path

import yaml

from astrid.core.pack.validate import validate_pack


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _pack(tmp_path: Path, *, files: dict[str, str] | None = None, **sections) -> Path:
    root = tmp_path / "demo"
    root.mkdir()
    for name, content in (files or {}).items():
        _write(root / name, content)
    manifest = {
        "schema_version": 3,
        "id": "demo",
        "name": "Demo",
        "version": "1.0.0",
        **sections,
    }
    (root / "pack.yaml").write_text(yaml.safe_dump(manifest), encoding="utf-8")
    return root


def _action(path: str = "actions/echo.py") -> dict:
    return {
        "description": "Echo a value.",
        "invocation": {"kind": "python", "path": path, "function": "echo"},
        "inputs": [{"name": "message", "type": "string"}],
        "outputs": [{"name": "result", "type": "string"}],
    }


def test_v3_minimal_action_needs_no_empty_role_folders_and_does_not_import_code(
    tmp_path: Path,
) -> None:
    root = _pack(
        tmp_path,
        files={"actions/echo.py": "raise RuntimeError('validator must not import me')\n"},
        actions={"echo": _action()},
    )

    errors, warnings = validate_pack(root)

    assert errors == []
    assert warnings == []


def test_v3_authored_skill_requires_name_and_description_frontmatter(tmp_path: Path) -> None:
    root = _pack(
        tmp_path,
        files={
            "docs/SKILL.md": "---\nname: Demo\n---\n# Guidance\n",
        },
        documentation={"kind": "skill", "path": "docs/SKILL.md"},
    )

    errors, _warnings = validate_pack(root)

    assert any("docs/SKILL.md" in error and "description" in error for error in errors)


def test_v3_authored_skill_and_ordinary_support_files_are_allowed(tmp_path: Path) -> None:
    root = _pack(
        tmp_path,
        files={
            "docs/SKILL.md": "---\nname: Demo\ndescription: Use the demo pack.\n---\n# Guidance\n",
            "docs/reference.md": "Human reference notes.\n",
            "actions/echo.py": "def echo(message): return message\n",
        },
        documentation={"kind": "skill", "path": "docs/SKILL.md"},
        actions={"echo": _action()},
    )

    errors, _warnings = validate_pack(root)

    assert errors == []


def test_v3_role_path_escape_is_reported_by_static_validator(tmp_path: Path) -> None:
    root = _pack(
        tmp_path,
        files={"actions/echo.py": "def echo(): return None\n"},
        actions={"echo": _action("../outside.py")},
    )

    errors, _warnings = validate_pack(root)

    assert any("actions.echo.invocation" in error for error in errors)


def test_v3_local_json_schema_reference_must_exist_and_be_valid(tmp_path: Path) -> None:
    schema = {"type": "object", "$ref": "missing.json"}
    root = _pack(
        tmp_path,
        files={
            "actions/echo.py": "def echo(value): return value\n",
            "shared/input.schema.json": json.dumps(schema),
        },
        actions={
            "echo": {
                **_action(),
                "inputs": "shared/input.schema.json",
            }
        },
    )

    errors, _warnings = validate_pack(root)

    assert any("referenced JSON Schema file not found" in error for error in errors)


def test_v3_rejects_a_second_skill_metadata_authority(tmp_path: Path) -> None:
    root = _pack(
        tmp_path,
        files={
            "docs/SKILL.md": "---\nname: Demo\ndescription: Use the demo pack.\n---\n",
            "docs/references/SKILL.md": "---\nname: Extra\ndescription: Extra guidance.\n---\n",
        },
        documentation={"kind": "skill", "path": "docs/SKILL.md"},
    )

    errors, _warnings = validate_pack(root)

    assert any("docs/references/SKILL.md" in error and "one authored skill" in error for error in errors)
