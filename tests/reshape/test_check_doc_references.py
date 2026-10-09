"""Tests for the docs <-> code integrity gate (scripts/reshape/check_doc_references.py).

Most cases inject a small capability surface and CLI tree so they do not depend
on the live registry. The final group pins the real introspection, so a gate
that silently stops seeing executors or commands fails here.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.reshape import check_doc_references as gate

REPO_ROOT = Path(__file__).resolve().parents[2]


def _surface(
    *,
    capabilities: tuple[str, ...] = ("generation.generate_image",),
    packs: tuple[str, ...] = ("generation", "fakepack", "vibecomfy", "media", "pixel"),
    pack_errors: dict[str, str] | None = None,
    registry_error: str | None = None,
) -> gate.CapabilitySurface:
    return gate.CapabilitySurface(
        pack_ids=frozenset(packs),
        capability_ids=frozenset(capabilities),
        pack_errors=pack_errors or {},
        registry_error=registry_error,
    )


def _tree() -> gate.CliTree:
    tree = gate.CliTree()
    tree.top_level = {
        "timelines": {
            "create": {},
            "show": {},
            "visualize": {},
            "render": {},
            "shots": {
                "list": {},
                "create": {},
                "text": {"list": {}, "show": {}, "set": {}},
            },
        },
        "projects": {"create": {}, "list": {}},
        "doctor": None,
        "backup": None,
        "hivemind": {"search": {}},
    }
    return tree


def _check(text: str, *, surface: gate.CapabilitySurface | None = None) -> list[gate.Finding]:
    findings, _cap, _cli = gate.check_docs(
        {"docs/example/SKILL.md": text}, surface or _surface(), _tree()
    )
    return findings


def _errors(findings: list[gate.Finding]) -> list[gate.Finding]:
    return [finding for finding in findings if finding.level == "error"]


# --- 1. Capability references -------------------------------------------------


def test_unresolved_backticked_capability_fails_with_file_and_line() -> None:
    text = "intro\n\nUse `fakepack.missing_cap` for this.\n"

    findings = _errors(_check(text))

    assert len(findings) == 1
    assert findings[0].path == "docs/example/SKILL.md"
    assert findings[0].line == 3
    assert "fakepack.missing_cap" in findings[0].message


def test_resolved_capability_passes() -> None:
    assert _errors(_check("Run `generation.generate_image --json` now.\n")) == []


def test_dotted_identifiers_that_are_not_capabilities_pass() -> None:
    text = (
        "Output file `clip.json` and module `python -m vibecomfy.cli run`.\n"
        "Media kind `media.mp4` is a file name.\n"
    )

    assert _errors(_check(text)) == []


def test_unprefixed_dotted_names_are_ignored() -> None:
    # `astrid.generate` is not a first-party pack id, so it is not a capability reference.
    assert _errors(_check("Call `astrid.generate` directly.\n")) == []


def test_pack_that_failed_to_load_is_reported_and_hinted() -> None:
    surface = _surface(pack_errors={"pixel": "keywords.1: 'pixel art' is invalid"})

    findings = _errors(_check("Use `pixel.snap` on the image.\n", surface=surface))
    messages = [finding.message for finding in findings]

    assert any("first-party pack failed to load" in message for message in messages)
    unresolved = [message for message in messages if "unresolved capability" in message]
    assert unresolved and "pixel" in unresolved[0] and "failed to load" in unresolved[0]


def test_registry_failure_is_a_single_error_and_skips_reference_checks() -> None:
    surface = _surface(registry_error="RuntimeError: boom")

    findings = _errors(_check("Use `fakepack.missing_cap`.\n", surface=surface))

    assert len(findings) == 1
    assert "capability registry failed to load" in findings[0].message


# --- 2. CLI references --------------------------------------------------------


def test_retired_verb_in_fenced_block_fails_with_file_and_line() -> None:
    text = "# Usage\n\n```bash\npython3 -m astrid timelines shots group t1 --project p\n```\n"

    findings = _errors(_check(text))

    assert len(findings) == 1
    assert findings[0].line == 4
    assert "'group'" in findings[0].message


def test_retired_shorthand_span_fails() -> None:
    findings = _errors(_check("Save with `timelines save` when ready.\n"))

    assert len(findings) == 1
    assert "'save'" in findings[0].message


def test_unknown_top_level_option_fails() -> None:
    text = "```bash\npython3 -m astrid --brief brief.txt --out runs/x\n```\n"

    findings = _errors(_check(text))

    assert len(findings) == 1
    assert "--brief" in findings[0].message


def test_valid_invocations_pass() -> None:
    text = (
        "```bash\n"
        "python3 -m astrid --help\n"
        "python3 -m astrid timelines visualize --project PROJECT [--timeline-slug REF]\n"
        "python3 -m astrid timelines shots text list --project P --shot S\n"
        "python3 -m astrid timelines show <timeline> --project P --json\n"
        "python3 -m astrid backup create --json\n"
        "python3 -m astrid hivemind search QUERY --limit 3\n"
        "python3 -m astrid timelines shots text set --project P {create,list}\n"
        "```\n"
    )

    assert _errors(_check(text)) == []


def test_python_module_path_in_cli_form_is_not_a_capability() -> None:
    # The gateway-shaped line must not be parsed as a `vibecomfy.cli` capability.
    findings = _check("Run `python -m vibecomfy.cli run workflow.json`.\n")

    assert _errors(findings) == []


# --- 3. Ignore marker ---------------------------------------------------------


def test_marker_on_preceding_line_suppresses_that_line() -> None:
    text = "<!-- doc-ref: historical -->\nThe retired route was `timelines save`.\n"

    assert _errors(_check(text)) == []


def test_marker_suppresses_only_the_next_line() -> None:
    text = (
        "<!-- doc-ref: historical -->\n"
        "Old: `timelines save`.\n"
        "New: `timelines save` again.\n"
    )

    findings = _errors(_check(text))

    assert [finding.line for finding in findings] == [3]


def test_marker_before_fence_suppresses_the_whole_block() -> None:
    text = (
        "<!-- doc-ref: historical -->\n"
        "```bash\n"
        "python3 -m astrid timelines shots group t1\n"
        "python3 -m astrid timelines script t1\n"
        "```\n"
        "After the block `timelines save` is checked again.\n"
    )

    findings = _errors(_check(text))

    assert [finding.line for finding in findings] == [6]


def test_same_line_marker_suppresses_that_line() -> None:
    text = "Old route `timelines save`. <!-- doc-ref: historical -->\n"

    assert _errors(_check(text)) == []


# --- 4. Catalog freshness -----------------------------------------------------

_FAKE_GENERATOR = """\
import argparse, pathlib, re
parser = argparse.ArgumentParser()
parser.add_argument("--target", required=True)
args = parser.parse_args()
path = pathlib.Path(args.target)
text = path.read_text()
block = "<!-- BEGIN CAPABILITY INDEX (auto-generated) -->\\nGENERATED ROW\\n<!-- END CAPABILITY INDEX -->"
path.write_text(re.sub(r"<!-- BEGIN CAPABILITY INDEX.*?<!-- END CAPABILITY INDEX -->", block, text, flags=re.S))
"""


def _catalog_repo(tmp_path: Path, committed_block: str) -> Path:
    root = tmp_path / "repo"
    (root / "astrid/packs/_core/skill/references").mkdir(parents=True)
    (root / "scripts").mkdir()
    (root / "astrid/packs/_core/skill/references/capabilities.md").write_text(
        f"# Catalog\n\n{committed_block}\n\n### Hivemind contract\n", encoding="utf-8"
    )
    (root / "scripts/gen_capability_index.py").write_text(_FAKE_GENERATOR, encoding="utf-8")
    return root


def test_fresh_catalog_passes(tmp_path: Path) -> None:
    root = _catalog_repo(
        tmp_path,
        "<!-- BEGIN CAPABILITY INDEX (auto-generated) -->\nGENERATED ROW\n<!-- END CAPABILITY INDEX -->",
    )

    assert gate.check_catalog(root, python=sys.executable) == []


def test_stale_catalog_fails_and_names_the_generator(tmp_path: Path) -> None:
    root = _catalog_repo(
        tmp_path,
        "<!-- BEGIN CAPABILITY INDEX (auto-generated) -->\nOLD ROW\n<!-- END CAPABILITY INDEX -->",
    )

    findings = gate.check_catalog(root, python=sys.executable)

    assert [finding.level for finding in findings] == ["error"]
    assert "scripts/gen_capability_index.py" in findings[0].message


def test_broken_generator_fails_closed(tmp_path: Path) -> None:
    root = _catalog_repo(tmp_path, "<!-- BEGIN CAPABILITY INDEX -->\nX\n<!-- END CAPABILITY INDEX -->")
    (root / "scripts/gen_capability_index.py").write_text("raise SystemExit('pack broke')\n", encoding="utf-8")

    findings = gate.check_catalog(root, python=sys.executable)

    assert len(findings) == 1
    assert "generator failed" in findings[0].message
    assert "pack broke" in findings[0].message


# --- 5. Untracked pack code (warning only) ------------------------------------


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
    )


@pytest.fixture
def pack_repo(tmp_path: Path) -> Path:
    if shutil.which("git") is None:
        pytest.skip("git is required for the untracked-pack check")
    root = tmp_path / "repo"
    (root / "astrid/packs/generation").mkdir(parents=True)
    (root / "astrid/packs/generation/pack.yaml").write_text("id: generation\n", encoding="utf-8")
    (root / "docs").mkdir()
    _git(root, "init", "-q")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "base")
    return root


def test_untracked_executor_referenced_by_tracked_doc_warns(pack_repo: Path) -> None:
    doc = pack_repo / "docs/voice.md"
    doc.write_text("Use `generation.generate_speech` for narration.\n", encoding="utf-8")
    _git(pack_repo, "add", "docs/voice.md")
    _git(pack_repo, "commit", "-q", "-m", "doc")
    executor = pack_repo / "astrid/packs/generation/executors/generate_speech"
    executor.mkdir(parents=True)
    (executor / "STAGE.md").write_text("# speech\n", encoding="utf-8")

    docs = {"docs/voice.md": doc.read_text(encoding="utf-8")}
    findings = gate.check_untracked_pack_code(pack_repo, docs)

    assert [finding.level for finding in findings] == ["warning"]
    assert findings[0].path == "docs/voice.md"
    assert "astrid/packs/generation/executors/generate_speech" in findings[0].message


def test_untracked_pack_code_nobody_references_does_not_warn(pack_repo: Path) -> None:
    doc = pack_repo / "docs/other.md"
    doc.write_text("Nothing relevant here.\n", encoding="utf-8")
    _git(pack_repo, "add", "docs/other.md")
    _git(pack_repo, "commit", "-q", "-m", "doc")
    executor = pack_repo / "astrid/packs/generation/executors/orphan"
    executor.mkdir(parents=True)
    (executor / "STAGE.md").write_text("# orphan\n", encoding="utf-8")

    docs = {"docs/other.md": doc.read_text(encoding="utf-8")}

    assert gate.check_untracked_pack_code(pack_repo, docs) == []


# --- 6. Scope and introspection (real code) -----------------------------------


@pytest.mark.parametrize(
    ("path", "in_scope"),
    [
        ("astrid/packs/rendering/skill/SKILL.md", True),
        ("astrid/packs/generation/executors/x/STAGE.md", True),
        ("astrid/packs/pixel/skill/references/notes.md", True),
        ("docs/guides/cli-journeys.md", True),
        ("docs/getting-started.md", True),
        ("AGENTS.md", True),
        ("docs/testing/some-qa-record.md", False),
        ("docs/architecture/plan.md", False),
        ("tools/notes.md", False),
    ],
)
def test_doc_scope(path: str, in_scope: bool) -> None:
    assert gate.in_doc_scope(path) is in_scope


def test_real_capability_surface_resolves_known_executors() -> None:
    surface = gate.load_capability_surface()

    assert surface.registry_error is None
    assert "generation.generate_image_codex" in surface.capability_ids
    assert "rendering.render" in surface.capability_ids
    assert "rendering.remotion" in surface.capability_ids  # renderer kind
    assert "generation" in surface.pack_ids


def test_real_cli_tree_matches_the_gateway() -> None:
    tree = gate.load_cli_tree()

    assert gate.check_cli_invocation(["timelines", "visualize", "--project", "P"], tree) is None
    assert gate.check_cli_invocation(["timelines", "shots", "text", "list"], tree) is None
    assert gate.check_cli_invocation(["timelines", "shots", "group", "t"], tree) is not None
    assert gate.check_cli_invocation(["timelines", "script", "t"], tree) is not None
    assert gate.check_cli_invocation(["--brief", "x"], tree) is not None
    assert gate.check_cli_invocation(["doctor", "--json"], tree) is None
