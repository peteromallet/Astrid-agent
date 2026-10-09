import json
import tempfile
import unittest
from pathlib import Path

from astrid.core import element as element_module
from astrid.core.element import load_default_registry


_KIND_SINGULAR = {
    "effects": "effect",
    "animations": "animation",
    "transitions": "transition",
    "widgets": "widget",
}


def write_pack_element(
    pack_root: Path,
    kind: str,
    element_id: str,
    *,
    pack_id: str,
    label: str,
    js_packages: list[str] | None = None,
) -> Path:
    if not any((pack_root / name).exists() for name in ("pack.yaml", "pack.yml", "pack.json")):
        pack_root.mkdir(parents=True, exist_ok=True)
        (pack_root / "pack.yaml").write_text(f"id: {pack_id}\nname: {pack_id}\nversion: 0.1.0\n", encoding="utf-8")
    element_root = pack_root / "elements" / kind / element_id
    element_root.mkdir(parents=True)
    (element_root / "component.tsx").write_text("export default function Element() { return null; }\n", encoding="utf-8")
    (element_root / "element.yaml").write_text(
        json.dumps(
            {
                "id": element_id,
                "kind": _KIND_SINGULAR[kind],
                "pack_id": pack_id,
                "metadata": {"label": label},
                "schema": {"type": "object"},
                "defaults": {"enabled": True},
                "dependencies": {
                    "js_packages": list(js_packages or []),
                    "python_requirements": [],
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    return element_root


class ElementRegistryTest(unittest.TestCase):
    def test_element_public_surface_exports_dynamic_kind_registry(self) -> None:
        self.assertIs(element_module.ElementKindRegistry, type(element_module.ELEMENT_KIND_REGISTRY))
        self.assertTrue(hasattr(element_module, "ElementKindDescriptor"))
        self.assertTrue(hasattr(element_module, "load_source_elements"))

    def test_singular_aliases_normalize_to_builtin_kind_keys(self) -> None:
        registry = load_default_registry()

        effect = registry.get("effect", "text-card")
        animation = registry.get("animation", "fade")
        transitions = registry.list("transition")

        self.assertEqual(effect.kind, "effects")
        self.assertEqual(animation.kind, "animations")
        self.assertIn("cross-fade", {item.id for item in transitions})

    def test_fade_animation_and_fade_transition_coexist_under_kind_keys(self) -> None:
        registry = load_default_registry()
        animation_fade = registry.get("animations", "fade")
        transition_fade = registry.get("transitions", "fade")
        self.assertEqual(animation_fade.kind, "animations")
        self.assertEqual(transition_fade.kind, "transitions")
        self.assertNotEqual(animation_fade.root, transition_fade.root)
        self.assertTrue(str(animation_fade.root).endswith("astrid/packs/rendering/elements/animations/fade"))
        self.assertTrue(str(transition_fade.root).endswith("astrid/packs/rendering/elements/transitions/fade"))

    def test_rendering_pack_defaults_are_discovered_with_pack_source(self) -> None:
        from unittest import mock

        from astrid.core.element import registry as registry_module
        from astrid.core.pack import discover_packs as real_discover_packs

        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "project"
            with mock.patch.object(
                registry_module,
                "discover_packs",
                side_effect=lambda root=None: tuple(p for p in real_discover_packs() if p.id != "local"),
            ):
                registry = load_default_registry(project_root=project)

            by_key = registry.as_mapping()

            self.assertIn(("effects", "text-card"), by_key)
            self.assertIn(("animations", "fade"), by_key)
            self.assertIn(("transitions", "cross-fade"), by_key)
            text_card = registry.get("effects", "text-card")
            self.assertEqual(text_card.source, "pack:rendering")
            self.assertFalse(text_card.editable)
            self.assertEqual(text_card.metadata["label"], "Text Card")
            self.assertEqual(text_card.metadata["pack_id"], "rendering")


class PerElementFaultToleranceTest(unittest.TestCase):
    """One invalid element is skipped and reported; its pack's others still load."""

    def _pack_with_good_and_bad_element(self, tmp: str) -> tuple[Path, Path]:
        extra = Path(tmp) / "extra"
        (extra / "demo").mkdir(parents=True)
        (extra / "demo" / "pack.yaml").write_text(
            "schema_version: 2\nid: demo\nname: Demo\nversion: 0.1.0\ncapabilities: [elements]\n",
            encoding="utf-8",
        )
        write_pack_element(extra / "demo", "effects", "good-glow", pack_id="demo", label="Good")
        bad_root = write_pack_element(extra / "demo", "effects", "bad-glow", pack_id="demo", label="Bad")
        # Declares the wrong kind for its folder: a per-element ElementValidationError.
        manifest = bad_root / "element.yaml"
        payload = json.loads(manifest.read_text(encoding="utf-8"))
        payload["kind"] = "transition"
        manifest.write_text(json.dumps(payload) + "\n", encoding="utf-8")
        return extra, manifest

    def test_invalid_element_is_skipped_and_its_pack_siblings_still_load(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            extra, manifest = self._pack_with_good_and_bad_element(tmp)
            registry = load_default_registry(project_root=Path(tmp) / "project", extra_pack_roots=(str(extra),))

            demo_ids = {item.id for item in registry.list("effects") if item.metadata.get("pack_id") == "demo"}
            self.assertEqual(demo_ids, {"good-glow"})
            self.assertEqual(registry.get("effects", "good-glow").metadata["label"], "Good")
            with self.assertRaises(KeyError):
                registry.get("effects", "bad-glow")

            self.assertEqual(len(registry.diagnostics), 1)
            skipped = registry.diagnostics[0]
            self.assertEqual((skipped.pack_id, skipped.kind), ("demo", "effects"))
            self.assertEqual(skipped.path.resolve(), manifest.resolve())
            self.assertIn("does not match folder kind", skipped.error)

    def test_validate_cli_reports_the_skipped_file_and_fails(self) -> None:
        import contextlib
        import io

        from astrid.core.element import cli

        with tempfile.TemporaryDirectory() as tmp:
            extra, manifest = self._pack_with_good_and_bad_element(tmp)
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = cli.main(
                    ["--project-root", str(Path(tmp) / "project"), "--pack-root", str(extra), "validate"]
                )
            report = out.getvalue()
            self.assertEqual(code, 1)
            self.assertIn(str(manifest), report)
            self.assertIn("does not match folder kind", report)
            self.assertIn("1 skipped as invalid", report)

            # A single-element check names the skip instead of "unknown element".
            with self.assertRaisesRegex(Exception, "invalid, skipped at"):
                cli.main(
                    [
                        "--project-root",
                        str(Path(tmp) / "project"),
                        "--pack-root",
                        str(extra),
                        "validate",
                        "effects",
                        "bad-glow",
                    ]
                )


if __name__ == "__main__":
    unittest.main()
