from __future__ import annotations

import json
from pathlib import Path

import yaml

from astrid.core.pack.cli import main
from astrid.core.pack.validate import (
    validate_first_party_packs_root_report,
    validate_pack_roots,
)


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _pack(
    parent: Path,
    pack_id: str,
    *,
    documentation: dict[str, str],
    files: dict[str, str],
    actions: dict | None = None,
) -> Path:
    root = parent / pack_id
    root.mkdir(parents=True)
    manifest = {
        "schema_version": 3,
        "id": pack_id,
        "name": pack_id.replace("_", " ").title(),
        "version": "1.0.0",
        "documentation": documentation,
    }
    if actions is not None:
        manifest["actions"] = actions
    _write(root / "pack.yaml", yaml.safe_dump(manifest, sort_keys=False))
    for relative, content in files.items():
        _write(root / relative, content)
    return root


def _skill() -> str:
    return "---\nname: Demo\ndescription: Use this pack.\n---\n# Demo\n"


def test_explicit_roots_are_checked_in_assembled_context_with_derived_counts(tmp_path: Path) -> None:
    skill = _pack(
        tmp_path,
        "skill_pack",
        documentation={"kind": "skill", "path": "docs/SKILL.md"},
        files={"docs/SKILL.md": _skill()},
    )
    agents = _pack(
        tmp_path,
        "agents_pack",
        documentation={"kind": "agents", "path": "AGENTS.md"},
        files={"AGENTS.md": "# Agent guidance\n"},
    )
    none = _pack(
        tmp_path,
        "none_pack",
        documentation={"kind": "none", "reason": "No agent guidance is shipped."},
        files={},
    )
    bad = _pack(
        tmp_path,
        "bad_pack",
        documentation={"kind": "skill", "path": "docs/SKILL.md"},
        files={"docs/SKILL.md": _skill()},
        actions={
            "run": {
                "description": "Missing implementation.",
                "invocation": {
                    "kind": "python",
                    "path": "actions/missing.py",
                    "function": "run",
                },
                "inputs": [],
                "outputs": [],
            }
        },
    )

    report = validate_pack_roots(
        [skill, agents, none, bad],
        context="assembled",
    )

    assert report.context == "assembled"
    assert report.counts == {
        "roots": 4,
        "active": 4,
        "deprecated": 0,
        "hidden": 0,
        "valid": 3,
        "invalid": 1,
        "skill": 2,
        "agents": 1,
        "none": 1,
        "undocumented": 0,
        "skill_exports": 2,
    }
    assert not report.valid
    assert any("bad_pack" in error and "missing.py" in error for error in report.errors)


def test_aggregate_rejects_a_second_skill_export(tmp_path: Path) -> None:
    root = _pack(
        tmp_path,
        "multi_skill",
        documentation={"kind": "skill", "path": "docs/SKILL.md"},
        files={
            "docs/SKILL.md": _skill(),
            "docs/references/SKILL.md": _skill(),
        },
    )

    report = validate_pack_roots([root], context="assembled")

    assert not report.valid
    assert any("one authored skill" in error for error in report.errors)
    assert report.to_dict()["roots"][0]["skill_exports"] == [
        "docs/SKILL.md",
        "docs/references/SKILL.md",
    ]


def test_first_party_report_accepts_p01_explicit_root_set_and_exposes_dispositions(
    tmp_path: Path,
) -> None:
    packs_root = tmp_path / "packs"
    skill = _pack(
        packs_root,
        "skill_pack",
        documentation={"kind": "skill", "path": "docs/SKILL.md"},
        files={"docs/SKILL.md": _skill()},
    )
    none = _pack(
        packs_root,
        "none_pack",
        documentation={"kind": "none", "reason": "No guidance."},
        files={},
    )
    report = validate_first_party_packs_root_report(
        packs_root,
        pack_roots=[skill, none],
        context="assembled",
    )

    assert report.valid
    assert report.counts["roots"] == 2
    assert report.counts["skill"] == 1
    assert report.counts["none"] == 1
    assert report.runtime_mounts == ()
    assert report.excluded == ()


def test_validate_json_keeps_standalone_context_explicit(tmp_path: Path, capsys) -> None:
    assert main(["new", "json_pack", "--destination", str(tmp_path / "json_pack")]) == 0
    capsys.readouterr()

    assert main(["validate", str(tmp_path / "json_pack"), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["valid"] is True
    assert payload["context"] == "standalone"
    assert payload["counts"]["roots"] == 1
    assert payload["dispositions"] == {"excluded": [], "runtime_mounts": []}
