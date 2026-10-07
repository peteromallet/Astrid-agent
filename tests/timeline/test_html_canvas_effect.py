import json
import tempfile
import unittest
from pathlib import Path

import yaml

from astrid.core.contracts.errors import AstridError
from astrid.core.element.schema import load_element_definition
from astrid.core.pack.canonical import validate_canonical_pack
from astrid.packs.rendering.actions.html_canvas_effect.run import main, scaffold


class HtmlCanvasEffectExecutorTest(unittest.TestCase):
    @staticmethod
    def _element_root(project: Path, effect_id: str) -> Path:
        return project / "astrid" / "packs" / "local" / "rendering" / "elements" / "effects" / effect_id

    @staticmethod
    def _write_existing_local_pack(project: Path) -> tuple[Path, dict]:
        local_pack = project / "astrid" / "packs" / "local"
        existing_root = local_pack / "rendering" / "elements" / "effects" / "existing-card"
        sibling_root = local_pack / "rendering" / "elements" / "effects" / "sibling-card"
        for root, effect_id in ((existing_root, "existing-card"), (sibling_root, "sibling-card")):
            root.mkdir(parents=True, exist_ok=True)
            (root / "component.tsx").write_text(
                f"export const {effect_id.replace('-', '')} = true;\n", encoding="utf-8"
            )
            (root / "element.yaml").write_text(
                yaml.safe_dump({"id": effect_id, "kind": "effect", "pack_id": "local"}),
                encoding="utf-8",
            )
        payload = {
            "schema_version": 3,
            "id": "local",
            "name": "Existing Local Pack",
            "version": "0.1.0",
            "status": "active",
            "domain": "media",
            "stability": "stable",
            "support": "project",
            "visibility": "visible",
            "description": "Preserve this pack.",
            "agent": {"purpose": "Keep this project-specific purpose."},
            "rendering": {
                "effects/existing-card": {
                    "type": "element",
                    "path": "rendering/elements/effects/existing-card/element.yaml",
                    "resources": [
                        {
                            "kind": "implementation",
                            "path": "rendering/elements/effects/existing-card/component.tsx",
                        }
                    ],
                },
                "effects/sibling-card": {
                    "type": "element",
                    "path": "rendering/elements/effects/sibling-card/element.yaml",
                    "resources": [
                        {
                            "kind": "implementation",
                            "path": "rendering/elements/effects/sibling-card/component.tsx",
                        }
                    ],
                },
            },
        }
        manifest_path = local_pack / "pack.yaml"
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
        return manifest_path, payload

    def test_pack_action_declares_new_runtime(self) -> None:
        pack_path = Path(__file__).resolve().parents[2] / "astrid" / "packs" / "rendering" / "pack.yaml"
        pack = yaml.safe_load(pack_path.read_text(encoding="utf-8"))
        executor = pack["actions"]["html_canvas_effect"]
        self.assertEqual(
            executor["metadata"]["runtime_module"],
            "astrid.packs.rendering.actions.html_canvas_effect.run",
        )
        self.assertEqual(
            executor["metadata"]["creates"],
            "astrid/packs/local/rendering/elements/effects/<effect_id>",
        )

    def test_scaffold_bootstraps_valid_v3_local_pack_and_declares_closure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "project"
            report_path = project / "runs" / "effect" / "report.json"

            report = scaffold(
                effect_id="glass-product-card",
                label="Glass Product Card",
                description="A test effect.",
                project_root=project,
                out_path=report_path,
            )

            local_pack = project / "astrid" / "packs" / "local"
            element_root = self._element_root(project, "glass-product-card")
            local_pack_manifest = local_pack / "pack.yaml"
            self.assertEqual(Path(report["element_root"]), element_root)
            self.assertTrue(local_pack_manifest.is_file())
            self.assertTrue((element_root / "component.tsx").is_file())
            self.assertTrue((element_root / "element.yaml").is_file())
            self.assertTrue(report_path.is_file())

            manifest = yaml.safe_load(local_pack_manifest.read_text(encoding="utf-8"))
            self.assertEqual(manifest["schema_version"], 3)
            self.assertEqual(manifest["id"], "local")
            declaration = manifest["rendering"]["effects/glass-product-card"]
            self.assertEqual(
                declaration["path"],
                "rendering/elements/effects/glass-product-card/element.yaml",
            )
            self.assertEqual(
                declaration["resources"],
                [
                    {
                        "kind": "implementation",
                        "path": "rendering/elements/effects/glass-product-card/component.tsx",
                    }
                ],
            )
            entry = validate_canonical_pack(local_pack, expected_pack_id="local")
            self.assertEqual(entry.definition.schema_version, 3)

            element_manifest = yaml.safe_load((element_root / "element.yaml").read_text(encoding="utf-8"))
            self.assertEqual(element_manifest["id"], "glass-product-card")
            self.assertEqual(element_manifest["pack_id"], "local")
            self.assertTrue(element_manifest["metadata"]["render_requirements"]["uses_html_in_canvas"])
            element = load_element_definition(
                element_root, kind="effects", source="pack:local", editable=True, priority=10
            )
            self.assertEqual(element.id, "glass-product-card")
            self.assertEqual(element.metadata["pack_id"], "local")

    def test_existing_v3_local_pack_preserves_unrelated_content_and_updates_selected_closure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "project"
            local_pack_manifest, before = self._write_existing_local_pack(project)
            sibling_component = self._element_root(project, "sibling-card") / "component.tsx"

            scaffold(
                effect_id="new-card",
                label="New Card",
                description="A new effect.",
                project_root=project,
                out_path=project / "runs" / "new" / "report.json",
            )

            after = yaml.safe_load(local_pack_manifest.read_text(encoding="utf-8"))
            self.assertEqual(after["name"], before["name"])
            self.assertEqual(after["agent"], before["agent"])
            self.assertEqual(
                after["rendering"]["effects/existing-card"],
                before["rendering"]["effects/existing-card"],
            )
            self.assertEqual(
                after["rendering"]["effects/sibling-card"],
                before["rendering"]["effects/sibling-card"],
            )
            self.assertEqual(
                sibling_component.read_text(encoding="utf-8"),
                "export const siblingcard = true;\n",
            )
            self.assertIn("effects/new-card", after["rendering"])
            validate_canonical_pack(local_pack_manifest.parent, expected_pack_id="local")

    def test_scaffold_refuses_overwrite_without_force_and_force_is_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "project"
            local_pack_manifest, before = self._write_existing_local_pack(project)
            report_path = project / "report.json"
            existing_root = self._element_root(project, "existing-card")
            before_component = (existing_root / "component.tsx").read_text(encoding="utf-8")

            with self.assertRaisesRegex(FileExistsError, "already exists"):
                scaffold(
                    effect_id="existing-card",
                    label="Replacement",
                    description=None,
                    project_root=project,
                    out_path=report_path,
                )
            self.assertEqual(
                (existing_root / "component.tsx").read_text(encoding="utf-8"),
                before_component,
            )
            self.assertEqual(
                yaml.safe_load(local_pack_manifest.read_text(encoding="utf-8")),
                before,
            )

            scaffold(
                effect_id="existing-card",
                label="Replacement",
                description=None,
                project_root=project,
                out_path=report_path,
                force=True,
            )
            after = yaml.safe_load(local_pack_manifest.read_text(encoding="utf-8"))
            self.assertEqual(
                after["rendering"]["effects/sibling-card"],
                before["rendering"]["effects/sibling-card"],
            )
            self.assertTrue((self._element_root(project, "sibling-card") / "component.tsx").is_file())
            self.assertIn("Replacement", (existing_root / "element.yaml").read_text(encoding="utf-8"))
            validate_canonical_pack(local_pack_manifest.parent, expected_pack_id="local")

    def test_main_validates_effect_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(AstridError, "kebab-case"):
                main(["--effect-id", "Bad_ID", "--project-root", tmp, "--out", str(Path(tmp) / "report.json")])

    def test_main_writes_result_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "project"
            report_path = project / "runs" / "effect" / "report.json"
            timeline_path = project / "runs" / "effect" / "timeline.json"
            assets_path = project / "runs" / "effect" / "assets.json"

            result = main(
                [
                    "--effect-id", "glass-product-card",
                    "--label", "Glass Product Card",
                    "--description", "A test effect.",
                    "--project-root", str(project),
                    "--out", str(report_path),
                    "--timeline", str(timeline_path),
                    "--assets", str(assets_path),
                ]
            )

            self.assertEqual(result, 0)
            manifest_path = report_path.parent / "manifest.json"
            self.assertTrue(manifest_path.is_file(), f"manifest not found at {manifest_path}")
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(manifest["kind"], "html_canvas_effect")
            self.assertEqual(manifest["schema_version"], 1)
            self.assertIsInstance(manifest["inputs"], dict)
            self.assertIn("effect_id", manifest["inputs"])
            self.assertEqual(manifest["inputs"]["effect_id"], "glass-product-card")
            self.assertIsInstance(manifest["outputs"], list)
            output_paths = {o["path"] for o in manifest["outputs"]}
            self.assertIn(report_path.name, output_paths)
            self.assertTrue(
                any("glass-product-card" in p for p in output_paths),
                f"element_root path not found in {output_paths}",
            )
            self.assertIsInstance(manifest["warnings"], list)

if __name__ == "__main__":
    unittest.main()
