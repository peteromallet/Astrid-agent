"""Element admission is one check: validation, discovery and render agree.

Discovery skips an element that ``load_element_definition`` rejects (stderr
warning only). These tests pin that the pack validator rejects the same
element, that the timeline error names the skip reason, and that doctor lists
skipped elements.
"""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from astrid.core.element.registry import ElementRegistry, SkippedElement, element_skip_section
from astrid.core.element.schema import ElementValidationError, load_element_definition
from astrid.core.pack.validate import validate_pack
from astrid.packs.rendering.backends.remotion import run as remotion_run

LONG = "x" * 121
LIMIT_MESSAGE = "short_description is 121 chars; max is 120"


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _pack(short_description: str) -> Path:
    base = Path(tempfile.mkdtemp(prefix="test-element-admission-"))
    root = base / "test_pack"
    root.mkdir()
    _write(
        root / "pack.yaml",
        """schema_version: 2
id: test_pack
name: Test Pack
version: 0.1.0
description: A test pack.
content:
  elements: elements
agent:
  purpose: Testing
""",
    )
    _write(root / "skill" / "SKILL.md", "# Test Pack\n\nAgent guide.")
    comp = root / "elements" / "effects" / "long-effect"
    _write(
        comp / "element.yaml",
        f"""schema_version: 1
id: long-effect
kind: effect
pack_id: test_pack
short_description: '{short_description}'
metadata:
  name: Long Effect
schema: {{}}
defaults: {{}}
dependencies: {{}}
""",
    )
    _write(comp / "component.tsx", "export const Component = () => null;\n")
    return root


class ElementShortDescriptionAdmissionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.roots: list[Path] = []

    def tearDown(self) -> None:
        for root in self.roots:
            shutil.rmtree(root.parent, ignore_errors=True)

    def _make(self, short_description: str) -> Path:
        root = _pack(short_description)
        self.roots.append(root)
        return root

    def test_validator_rejects_short_description_over_limit(self) -> None:
        root = self._make(LONG)
        errors, _warnings = validate_pack(root)
        matching = [e for e in errors if LIMIT_MESSAGE in e]
        self.assertEqual(len(matching), 1, errors)
        self.assertIn("elements/effects/long-effect/element.yaml", matching[0])
        self.assertIn("effects/long-effect", matching[0])

    def test_validator_accepts_short_description_at_limit(self) -> None:
        root = self._make("x" * 120)
        errors, _warnings = validate_pack(root)
        self.assertFalse([e for e in errors if "short_description" in e], errors)

    def test_validator_message_is_discovery_message(self) -> None:
        """Validator and discovery share one loader, so the text is identical."""
        root = self._make(LONG)
        element_root = root / "elements" / "effects" / "long-effect"
        with self.assertRaises(ElementValidationError) as ctx:
            load_element_definition(
                element_root,
                kind="effects",
                source="pack:test_pack",
                editable=False,
                priority=0,
            )
        errors, _warnings = validate_pack(root)
        self.assertIn(str(ctx.exception), " ".join(errors))


class UnregisteredEffectDiagnosticTest(unittest.TestCase):
    def test_unregistered_type_names_the_discovery_skip_reason(self) -> None:
        skipped = SkippedElement(
            pack_id="test_pack",
            kind="effects",
            path=Path("/packs/test_pack/elements/effects/am-footage/element.yaml"),
            error="effects/am-footage: short_description is 166 chars; max is 120",
        )
        registry = ElementRegistry(diagnostics=[skipped])
        config = {"clips": [{"id": "c1", "clipType": "am-footage", "at": 0, "hold": 1, "track": "v"}]}
        with mock.patch.object(remotion_run, "load_default_registry", return_value=registry):
            with self.assertRaises(ValueError) as ctx:
                remotion_run._stage_effect_assets_for_timeline(
                    config,
                    project_dir=Path(tempfile.mkdtemp(prefix="test-element-admission-render-")),
                    theme_path=None,
                    render_hash="fixture",
                )
        message = str(ctx.exception)
        self.assertIn("timeline uses unregistered effect clip type(s): am-footage (skipped at discovery:", message)
        self.assertIn("short_description is 166 chars; max is 120", message)

    def test_unknown_type_with_no_skip_keeps_plain_message(self) -> None:
        config = {"clips": [{"id": "c1", "clipType": "not-an-effect", "at": 0, "hold": 1, "track": "v"}]}
        with mock.patch.object(remotion_run, "load_default_registry", return_value=ElementRegistry()):
            with self.assertRaises(ValueError) as ctx:
                remotion_run._stage_effect_assets_for_timeline(
                    config,
                    project_dir=Path(tempfile.mkdtemp(prefix="test-element-admission-render-")),
                    theme_path=None,
                    render_hash="fixture",
                )
        self.assertEqual(
            str(ctx.exception),
            "timeline uses unregistered effect clip type(s): not-an-effect",
        )


class DoctorElementSkipSectionTest(unittest.TestCase):
    def test_section_lists_skipped_elements_with_reason(self) -> None:
        skipped = SkippedElement(
            pack_id="test_pack",
            kind="effects",
            path=Path("/packs/test_pack/elements/effects/am-footage/element.yaml"),
            error="effects/am-footage: short_description is 166 chars; max is 120",
        )
        registry = ElementRegistry(diagnostics=[skipped])
        with mock.patch("astrid.core.element.registry.load_default_registry", return_value=registry):
            section = element_skip_section()
        self.assertEqual(section["count"], 1)
        record = section["skipped"][0]
        self.assertEqual(record["element_id"], "am-footage")
        self.assertEqual(record["pack_id"], "test_pack")
        self.assertIn("short_description is 166 chars", record["error"])
        self.assertIn("fix", record)

    def test_section_never_raises(self) -> None:
        with mock.patch(
            "astrid.core.element.registry.load_default_registry",
            side_effect=RuntimeError("boom"),
        ):
            section = element_skip_section()
        self.assertEqual(section, {"count": 0, "skipped": [], "error": "boom"})


if __name__ == "__main__":
    unittest.main()
