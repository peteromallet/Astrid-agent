import json
import shutil
import subprocess
import unittest
from pathlib import Path

from astrid.core.element.schema import load_element_definition

ROOT = Path(__file__).resolve().parents[2]
ELEMENT = ROOT / "astrid" / "packs" / "astrid_motion" / "elements" / "effects" / "am-pixel-shape"
CELLS = ELEMENT / "shape-cells.ts"


def _cells(shape: str, w: int, h: int, stroke: int) -> list[list[int]]:
    script = (
        f"const m = await import({json.dumps(CELLS.as_uri())});"
        f"console.log(JSON.stringify(m.shapeCells({json.dumps(shape)}, {w}, {h}, {stroke})));"
    )
    out = subprocess.run(
        ["node", "--input-type=module", "-e", script], check=True, capture_output=True, text=True
    ).stdout
    return json.loads(out)


@unittest.skipUnless(shutil.which("node"), "node is needed to run the shape geometry")
class AmPixelShapeGeometryTest(unittest.TestCase):
    def test_dot_three_by_three_is_a_full_square(self) -> None:
        cells = _cells("dot", 3, 3, 1)
        self.assertEqual(len(cells), 9)

    def test_ring_is_hollow_and_inside_the_box(self) -> None:
        cells = _cells("ring", 12, 12, 2)
        self.assertTrue(cells)
        self.assertNotIn([5, 5], cells)  # centre is empty
        self.assertIn([0, 6], cells)  # left edge is inked
        self.assertTrue(all(0 <= c < 12 and 0 <= r < 12 for c, r in cells))
        filled = _cells("dot", 12, 12, 2)
        self.assertLess(len(cells), len(filled))

    def test_underline_is_bottom_rows_only(self) -> None:
        cells = _cells("underline", 10, 6, 2)
        self.assertEqual(len(cells), 20)
        self.assertEqual({r for _, r in cells}, {4, 5})

    def test_arrow_points_right(self) -> None:
        cells = _cells("arrow", 12, 6, 1)
        self.assertEqual(len(cells), 30)
        self.assertIn([11, 2], cells)  # tip reaches the right edge
        self.assertNotIn([0, 0], cells)


class AmPixelShapeElementTest(unittest.TestCase):
    def test_element_loads_with_sticker_grid_defaults(self) -> None:
        definition = load_element_definition(
            ELEMENT, kind="effects", source="pack:astrid_motion", editable=False, priority=30
        )
        self.assertEqual(definition.id, "am-pixel-shape")
        self.assertEqual(definition.defaults["px_scale"], 6)
        self.assertEqual(
            definition.schema["properties"]["shape"]["enum"], ["ring", "dot", "underline", "arrow"]
        )

    def test_component_uses_the_shared_logical_grid(self) -> None:
        source = (ELEMENT / "component.tsx").read_text(encoding="utf-8")
        self.assertIn("LOGICAL_PX", source)
        self.assertIn("shapeCells(shape, w, h, stroke)", source)
        self.assertIn('shapeRendering="crispEdges"', source)


if __name__ == "__main__":
    unittest.main()
