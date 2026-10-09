"""Unit tests for the editorial.pacing rhythm model and sheet (in-memory fixtures)."""

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from astrid.packs.editorial.executors.pacing import model, sheet

TRACKS = [
    {"id": "plate", "kind": "visual"},
    {"id": "sprite", "kind": "visual"},
    {"id": "type", "kind": "visual"},
    {"id": "fx", "kind": "visual"},
    {"id": "vo", "kind": "audio"},
]


def occurrence(occurrence_id, shot_id, start_ms, duration_ms):
    return {
        "occurrence_id": occurrence_id,
        "shot_id": shot_id,
        "placement": {"start_ms": start_ms},
        "duration_ms": duration_ms,
        "speed": {"numerator": 1, "denominator": 1},
    }


def shot(shot_id, name, clips, texts=()):
    return {
        "shot_id": shot_id,
        "payload": {"name": name, "text_bindings": [{"text": text} for text in texts]},
        "internal_timeline": {"clips": list(clips), "tracks": TRACKS},
    }


def plate(clip_id, at, hold, **extra):
    return {"id": clip_id, "clipType": "am-snap-plate", "track": "plate", "at": at, "hold": hold, **extra}


def vo(clip_id, at, length, words=None):
    clip = {"id": clip_id, "clipType": "media", "track": "vo", "at": at, "from": 0.0, "to": length}
    if words is not None:
        clip["app"] = {"words": words}
    return clip


def render_smoke_bundle():
    """The three-cut WP7 proof, shaped like the authoring bundle checkout."""
    step = (7.02 - 0.089) / 29  # the real presenter words end near 7.02 s
    words = [[round(0.089 + k * step, 3), round(0.089 + (k + 1) * step, 3)] for k in range(29)]
    presenter_params = {"words": words, "seed": 3}
    return {
        "placements": [occurrence("occ-ch01", "ch01", 0, 9433)],
        "shots": {
            "ch01": shot(
                "ch01",
                "01 TOMORROW",
                [
                    vo("vo-s01", 0.0, 7.080083),
                    vo("vo-s02", 7.370083, 0.629208),
                    {**plate("m01-00", 0.0, 7.366667), "params": {"words": words}},
                    {"id": "m01-01", "clipType": "am-presenter", "track": "sprite", "at": 0.0, "hold": 7.366667, "params": presenter_params},
                    plate("m02-00", 7.366667, 1.166667),
                    {"id": "m02-01", "clipType": "am-type", "track": "type", "at": 7.366667, "hold": 1.166667},
                    plate("m03-00", 8.533333, 0.9),
                    {"id": "m03-01", "clipType": "am-callout", "track": "fx", "at": 8.533333, "hold": 0.9},
                ],
                texts=[" ".join(["word"] * 31)],
            )
        },
    }


def simple_bundle(clips, duration_ms=4000, texts=(), occ_id="occ-a", shot_id="a"):
    return {
        "placements": [occurrence(occ_id, shot_id, 0, duration_ms)],
        "shots": {shot_id: shot(shot_id, "A", clips, texts=texts)},
    }


class RhythmModelTest(unittest.TestCase):
    def test_render_smoke_proof_has_three_cuts_with_expected_kinds(self):
        rhythm = model.build_rhythm(render_smoke_bundle())
        cuts = rhythm["cuts"]
        self.assertEqual([cut["kind"] for cut in cuts], ["presenter", "illustrative", "silent"])
        self.assertEqual([round(cut["duration"], 3) for cut in cuts], [7.367, 1.167, 0.9])
        self.assertEqual(rhythm["summary"]["cut_count"], 3)
        self.assertAlmostEqual(rhythm["summary"]["median_cut_s"], 1.167, places=3)
        self.assertAlmostEqual(rhythm["summary"]["presenter_share"], 7.366667 / 9.433, places=3)
        self.assertEqual(rhythm["summary"]["word_sources"], {"params.words": 29, "text-binding": 2})
        self.assertEqual(rhythm["summary"]["word_count"], 31)

    def test_render_smoke_flags_visual_stall_and_trailing_silence(self):
        rhythm = model.build_rhythm(render_smoke_bundle())
        self.assertEqual([stall["kind"] for stall in rhythm["stalls"]], ["visual"])
        self.assertTrue(rhythm["cuts"][0]["stall"])
        self.assertEqual(len(rhythm["silences"]), 1)
        start, end = rhythm["silences"][0]
        self.assertAlmostEqual(start, 7.999, places=2)
        self.assertAlmostEqual(end, 9.433, places=2)

    def test_app_words_are_relative_to_clip_start_and_preferred(self):
        bundle = simple_bundle(
            [
                plate("p1", 0.0, 4.0),
                vo("v1", 2.0, 2.0, words=[[0.0, 0.5], [0.5, 1.0]]),
            ],
            texts=["one two"],
        )
        rhythm = model.build_rhythm(bundle)
        starts = [word["start"] for word in rhythm["words"]]
        self.assertEqual(starts, [2.0, 2.5])
        self.assertEqual({word["source"] for word in rhythm["words"]}, {"app.words"})
        self.assertEqual(rhythm["summary"]["word_sources"], {"app.words": 2})

    def test_text_fallback_is_exact_and_proportional_to_duration(self):
        bundle = simple_bundle(
            [plate("p1", 0.0, 8.0), vo("v-short", 0.0, 2.0), vo("v-long", 2.0, 6.0)],
            duration_ms=8000,
            texts=["one two three four five six seven eight"],
        )
        rhythm = model.build_rhythm(bundle)
        self.assertEqual(rhythm["summary"]["word_count"], 8)
        self.assertEqual(rhythm["summary"]["word_sources"], {"text-binding": 8})
        short_words = [w for w in rhythm["words"] if w["end"] <= 2.0 + 1e-9]
        long_words = [w for w in rhythm["words"] if w["start"] >= 2.0 - 1e-9]
        self.assertEqual(len(short_words), 2)
        self.assertEqual(len(long_words), 6)

    def test_text_without_vo_clip_counts_no_speech_and_says_so(self):
        bundle = simple_bundle([plate("p1", 0.0, 4.0)], texts=["lonely words here"])
        rhythm = model.build_rhythm(bundle)
        self.assertEqual(rhythm["words"], [])
        self.assertTrue(any("no VO clip" in note for note in rhythm["notes"]))
        self.assertEqual(rhythm["cuts"][0]["kind"], "silent")

    def test_kind_rules_presenter_overlap_and_app_override(self):
        bundle = simple_bundle(
            [
                plate("pres", 0.0, 2.0),
                {"id": "pres-el", "clipType": "am-presenter", "track": "sprite", "at": 0.0, "hold": 2.0},
                plate("joke", 2.0, 1.0, app={"kind": "joke"}),
                plate("plain", 3.0, 1.0),
            ],
            duration_ms=4000,
            texts=[],
        )
        kinds = [(cut["clip_id"], cut["kind"], cut["kind_source"]) for cut in model.build_rhythm(bundle)["cuts"]]
        self.assertEqual(
            kinds,
            [("pres", "presenter", "element"), ("joke", "joke", "app"), ("plain", "silent", "speech")],
        )

    def test_density_is_words_in_two_second_window(self):
        words = [[1.0, 1.05], [1.1, 1.15], [1.2, 1.25], [1.3, 1.35]]
        bundle = simple_bundle([plate("p1", 0.0, 3.0), vo("v1", 0.0, 3.0, words=words)], duration_ms=3000)
        rhythm = model.build_rhythm(bundle)
        grid = rhythm["grid"]
        index = grid["t"].index(1.5)
        self.assertAlmostEqual(grid["words_per_s"][index], 2.0, places=3)

    def test_silence_marks_gaps_of_point_four_seconds_or_more(self):
        bundle = {
            "placements": [occurrence("occ-a", "a", 0, 3800)],
            "shots": {
                "a": shot(
                    "a",
                    "A",
                    [
                        plate("p1", 0.0, 3.8),
                        vo("v1", 0.0, 1.0, words=[[0.0, 1.0]]),
                        vo("v2", 1.3, 1.0, words=[[0.0, 1.0]]),
                        vo("v3", 2.8, 1.0, words=[[0.0, 1.0]]),
                    ],
                )
            },
        }
        rhythm = model.build_rhythm(bundle)
        self.assertEqual(len(rhythm["silences"]), 1)
        start, end = rhythm["silences"][0]
        self.assertAlmostEqual(start, 2.3, places=3)
        self.assertAlmostEqual(end, 2.8, places=3)

    def test_long_silent_cut_is_visual_and_speech_stall_unless_deliberate(self):
        plain = model.build_rhythm(simple_bundle([plate("hold", 0.0, 8.0)], duration_ms=8000))
        self.assertEqual({stall["kind"] for stall in plain["stalls"]}, {"visual", "speech"})
        deliberate = model.build_rhythm(
            simple_bundle([plate("hold", 0.0, 8.0, app={"deliberate_hold": True})], duration_ms=8000)
        )
        self.assertEqual(deliberate["stalls"], [])
        self.assertTrue(deliberate["cuts"][0]["deliberate_hold"])

    def test_densest_ten_second_window(self):
        words = [[5.0 + k * 0.1, 5.05 + k * 0.1] for k in range(10)]
        bundle = simple_bundle([plate("p1", 0.0, 20.0), vo("v1", 0.0, 20.0, words=words)], duration_ms=20000)
        densest = model.build_rhythm(bundle)["summary"]["densest_window"]
        self.assertEqual(densest["start"], 0.0)
        self.assertAlmostEqual(densest["words_per_s"], 1.0, places=3)

    def test_window_clips_cuts_and_restricts_statistics(self):
        rhythm = model.build_rhythm(render_smoke_bundle(), window=[7.0, 9.0])
        self.assertEqual(rhythm["summary"]["cut_count"], 3)
        self.assertAlmostEqual(rhythm["cuts"][0]["duration"], 0.367, places=3)
        self.assertAlmostEqual(rhythm["summary"]["window_span_s"], 2.0, places=3)
        self.assertAlmostEqual(rhythm["summary"]["presenter_share"], 0.1833, places=3)

    def test_beats_lane_is_limited_to_window(self):
        rhythm = model.build_rhythm(render_smoke_bundle(), window=[0.0, 5.0], beats=[0.5, 1.0, 9.0])
        self.assertEqual(rhythm["beats"], [0.5, 1.0])
        self.assertEqual(rhythm["summary"]["beat_count"], 2)
        self.assertIsNone(model.build_rhythm(render_smoke_bundle())["beats"])

    def test_load_beats_accepts_list_object_and_dicts(self):
        self.assertEqual(model.load_beats([2.0, 0.5]), [0.5, 2.0])
        self.assertEqual(model.load_beats({"beats": [1.0, 3.0]}), [1.0, 3.0])
        self.assertEqual(model.load_beats([{"time": 0.25}, {"t": 0.75}]), [0.25, 0.75])
        self.assertIsNone(model.load_beats(None))

    def test_invalid_requests_raise_pacing_error(self):
        bundle = render_smoke_bundle()
        with self.assertRaises(model.PacingError):
            model.build_rhythm(bundle, window=[5.0, 5.0])
        with self.assertRaises(model.PacingError):
            model.build_rhythm(bundle, window=[20.0, 30.0])
        with self.assertRaises(model.PacingError):
            model.build_rhythm({"placements": [], "shots": {}})


class RhythmSheetTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="pacing-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_sheet_png_is_2400_by_1350_and_markdown_summarises(self):
        rhythm = model.build_rhythm(render_smoke_bundle(), timeline={"ref": "render-smoke-proof"})
        png = self.tmp / "pacing.png"
        sheet.render_png(rhythm, png)
        with Image.open(png) as image:
            self.assertEqual(image.size, (2400, 1350))
        markdown = sheet.render_markdown(rhythm)
        self.assertIn("## Summary", markdown)
        self.assertIn("| 1 | 01 TOMORROW | 0.00 | 7.37 s | presenter (element) |", markdown)
        self.assertIn("Stalls", markdown)
        json.loads(json.dumps(rhythm))

    def test_empty_speech_and_single_cut_still_render(self):
        rhythm = model.build_rhythm(simple_bundle([plate("only", 0.0, 4.0)], duration_ms=4000))
        png = self.tmp / "empty.png"
        sheet.render_png(rhythm, png)
        self.assertTrue(png.is_file())
        markdown = sheet.render_markdown(rhythm)
        self.assertIn("## Cuts", markdown)
        self.assertIn("- none", markdown)


if __name__ == "__main__":
    unittest.main()
