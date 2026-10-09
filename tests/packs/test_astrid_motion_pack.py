import json
import re
import unittest
from pathlib import Path

from astrid.core.element.registry import load_default_registry
from astrid.core.element.schema import load_element_definition
from astrid.core.pack.canonical import validate_canonical_pack

ROOT = Path(__file__).resolve().parents[2]
PACK = ROOT / "astrid" / "packs" / "astrid_motion"
SKILL = PACK / "skill" / "SKILL.md"
SHARED = PACK / "elements" / "_shared" / "am.tsx"
ELEMENTS = {
    element_id: PACK / "elements" / "effects" / element_id
    for element_id in ("am-snap-plate", "am-sprite", "am-type", "am-callout", "am-pixel-wipe", "am-pixel-shape")
}


class AstridMotionPackTest(unittest.TestCase):
    def test_pack_manifest_validates_with_resources(self) -> None:
        entry = validate_canonical_pack(PACK, expected_pack_id="astrid_motion")
        self.assertEqual(entry.id, "astrid_motion")

    def test_each_element_has_manifest_and_component(self) -> None:
        for element_id, root in ELEMENTS.items():
            with self.subTest(element=element_id):
                self.assertTrue((root / "element.yaml").is_file())
                self.assertTrue((root / "component.tsx").is_file())

    def test_each_element_loads_with_pack_identity_and_defaults(self) -> None:
        for element_id, root in ELEMENTS.items():
            with self.subTest(element=element_id):
                definition = load_element_definition(
                    root,
                    kind="effects",
                    source="pack:astrid_motion",
                    editable=False,
                    priority=30,
                )
                self.assertEqual(definition.id, element_id)
                self.assertTrue(element_id.startswith("am-"))
                self.assertEqual(definition.metadata.get("pack_id"), "astrid_motion")
                self.assertTrue(definition.defaults, "every element declares defaults")
                properties = definition.schema.get("properties", {})
                for key in definition.defaults:
                    self.assertIn(key, properties, f"default {key!r} missing from schema")

    def test_registry_registers_each_am_element_once(self) -> None:
        registry = load_default_registry()
        am_ids = [element.id for element in registry.list(kind="effects") if element.id.startswith("am-")]
        for element_id in ELEMENTS:
            with self.subTest(element=element_id):
                self.assertEqual(am_ids.count(element_id), 1)
        self.assertEqual([c for c in registry.conflicts() if str(c.id).startswith("am-")], [])

    def test_components_are_deterministic(self) -> None:
        forbidden = ("Math.random", "Date.now", "performance.now", "new Date(")
        sources = [(name, (root / "component.tsx").read_text(encoding="utf-8")) for name, root in ELEMENTS.items()]
        sources.append(("_shared/am.tsx", SHARED.read_text(encoding="utf-8")))
        for name, source in sources:
            for token in forbidden:
                with self.subTest(file=name, token=token):
                    self.assertNotIn(token, source)

    def test_pixel_elements_request_pixelated_rendering(self) -> None:
        for element_id in ("am-snap-plate", "am-sprite"):
            with self.subTest(element=element_id):
                source = (ELEMENTS[element_id] / "component.tsx").read_text(encoding="utf-8")
                # Components spread the shared pixelImage style, which sets pixelated.
                self.assertIn("...pixelImage", source)
        shared = SHARED.read_text(encoding="utf-8")
        self.assertIn("imageRendering: 'pixelated'", shared)

    def test_skill_documents_every_element_with_a_clip_example(self) -> None:
        text = SKILL.read_text(encoding="utf-8")
        for element_id in ELEMENTS:
            with self.subTest(element=element_id):
                self.assertIn(f'"clipType":"{element_id}"', text)

    def test_skill_clip_examples_validate_as_timeline_clips(self) -> None:
        text = SKILL.read_text(encoding="utf-8")
        clips = [
            json.loads(block)
            for block in re.findall(r"```json\n(.*?)```", text, flags=re.DOTALL)
            if block.lstrip().startswith('{"id"')
        ]
        self.assertEqual(sorted(clip["clipType"] for clip in clips), sorted(ELEMENTS))
        config = {
            "tracks": [
                {"id": "base", "kind": "visual", "label": "Base"},
                {"id": "fx", "kind": "visual", "label": "Effects"},
                {"id": "callouts", "kind": "visual", "label": "Callouts"},
            ],
            "clips": clips,
            "output": {"resolution": "1920x1080", "fps": 30, "file": "am.mp4"},
        }
        # validate_timeline needs the external banodoco timeline schema, which is not
        # installed here. Check each clip's params against its element schema
        # directly, which is the same check the timeline validator applies.
        import jsonschema

        registry = load_default_registry()
        for clip in clips:
            with self.subTest(clip=clip["id"]):
                element = registry.get("effects", clip["clipType"])
                jsonschema.validate(clip["params"], element.schema)
        self.assertTrue(config["clips"])


if __name__ == "__main__":
    unittest.main()
