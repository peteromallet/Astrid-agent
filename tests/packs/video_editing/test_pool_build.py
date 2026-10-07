import argparse
import contextlib
import inspect
import io
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from astrid.core import timeline
from astrid.packs.training.actions.pool_build import run as pool_build
from astrid.packs.video_editing.actions.hype import run as hype_callbacks
from astrid.packs.video_editing.actions.hype import steps as action_steps
from astrid.packs.video_editing.orchestrators.hype import steps as legacy_steps


class PoolCommandBuilderFacadeTests(unittest.TestCase):
    def test_callback_export_identity_and_signature(self) -> None:
        self.assertIs(hype_callbacks.build_pool_steps, action_steps.build_pool_steps)
        self.assertEqual(
            hype_callbacks.build_pool_steps.__module__,
            "astrid.packs.video_editing.actions.hype.steps",
        )
        self.assertEqual(
            inspect.signature(hype_callbacks.build_pool_steps),
            inspect.signature(legacy_steps.build_pool_steps),
        )
        self.assertEqual(hype_callbacks.__all__, ["build_pool_steps", "STEP_ORDER"])

    def test_generated_commands_preserve_source_audio_and_generative_options(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pool-callback-parity-") as temp:
            out = Path(temp) / "pool"
            out.mkdir()
            cases = (
                ("source_defaults", {}),
                ("source_options", {
                    "env_file": Path(temp) / "env file",
                    "theme_explicit": True,
                    "theme": "bright",
                    "primary_asset": "main",
                    "editor_iteration": 3,
                    "allow_generative_effects": True,
                    "sidecars": True,
                }),
                ("source_skip_shots", {"skip": ["shots"], "sidecars": True}),
                ("implicit_theme", {"theme": "bright"}),
                ("audio_only", {"video": None}),
                ("generative", {"video": None, "audio": None}),
                ("brief_generative_visuals", {"brief_allow_generative_visuals": True}),
            )
            for case, overrides in cases:
                with self.subTest(case=case):
                    for filename in ("scenes.json", "transcript.json", "shots.json"):
                        path = out / filename
                        if overrides.get("sidecars"):
                            path.write_text("{}\n", encoding="utf-8")
                        elif path.exists():
                            path.unlink()
                    values = dict(
                        python_exec="/python with spaces",
                        out=out,
                        brief_out=out / "brief with spaces",
                        brief_copy=Path(temp) / "brief.md",
                        audio=Path(temp) / "audio.wav",
                        video=Path(temp) / "video.mp4",
                        env_file=None,
                        source_slug="source",
                        brief_slug="brief",
                        asset_pairs=[("logo", Path(temp) / "logo with spaces.png")],
                        primary_asset=None,
                        skip=[],
                        theme_explicit=False,
                        theme=None,
                        target_duration=81.5,
                        allow_generative_effects=False,
                        brief_allow_generative_visuals=False,
                        editor_iteration=1,
                        extra_args={
                            step.name: ["--fixture-extra", step.name]
                            for step in legacy_steps.build_pool_steps()
                        },
                    )
                    values.update({key: value for key, value in overrides.items() if key != "sidecars"})
                    args = argparse.Namespace(**values)

                    def resolved_argv(executor_id: str, python_exec: str) -> list[str]:
                        return [python_exec, "-m", "fixture." + executor_id]

                    with (
                        patch.object(legacy_steps, "executor_argv", side_effect=resolved_argv) as legacy_resolve,
                        patch.object(action_steps, "executor_argv", side_effect=resolved_argv) as action_resolve,
                        patch.object(legacy_steps, "probe_audio_duration", return_value=12.345678) as legacy_probe,
                        patch.object(action_steps, "probe_audio_duration", return_value=12.345678) as action_probe,
                    ):
                        legacy = legacy_steps.build_pool_steps()
                        current = hype_callbacks.build_pool_steps()
                        self.assertEqual(len(current), len(legacy))
                        for old, new in zip(legacy, current):
                            with self.subTest(step=new.name):
                                if old.name == "verdict":
                                    with self.assertRaises(NotImplementedError) as old_error:
                                        old.build_cmd(args)
                                    with self.assertRaises(NotImplementedError) as new_error:
                                        new.build_cmd(args)
                                    self.assertEqual(str(new_error.exception), str(old_error.exception))
                                    continue
                                expected = old.build_cmd(args)
                                actual = new.build_cmd(args)
                                self.assertEqual(actual, expected)
                                self.assertEqual(actual[-2:], ["--fixture-extra", new.name])
                        self.assertEqual(action_resolve.call_args_list, legacy_resolve.call_args_list)
                        self.assertEqual(action_probe.call_args_list, legacy_probe.call_args_list)
                        if case == "audio_only":
                            action_probe.assert_called_once_with(args.audio)
                        else:
                            action_probe.assert_not_called()


class PoolBuildMainTests(unittest.TestCase):
    def make_tempdir(self) -> Path:
        path = Path(tempfile.mkdtemp(prefix="pool-build-test-"))
        self.addCleanup(shutil.rmtree, path, True)
        return path

    def test_main_renders_astrid_error_when_no_survivors_exist(self) -> None:
        tmp_dir = self.make_tempdir()
        triage = tmp_dir / "triage.json"
        scene_descriptions = tmp_dir / "scene_descriptions.json"
        quote_candidates = tmp_dir / "quote_candidates.json"
        transcript = tmp_dir / "transcript.json"
        scenes = tmp_dir / "scenes.json"

        triage.write_text(
            json.dumps({"entries": [{"scene_id": "scene_001", "triage_score": 0}]}),
            encoding="utf-8",
        )
        scene_descriptions.write_text(json.dumps({"entries": []}), encoding="utf-8")
        quote_candidates.write_text(json.dumps({"candidates": []}), encoding="utf-8")
        transcript.write_text(json.dumps({"segments": []}), encoding="utf-8")
        scenes.write_text(
            json.dumps(
                [{"index": 1, "start": 0.0, "end": 1.0, "duration": 1.0}]
            ),
            encoding="utf-8",
        )
        stderr = io.StringIO()

        with contextlib.redirect_stderr(stderr):
            rc = pool_build.main(
                [
                    "--triage",
                    str(triage),
                    "--scene-descriptions",
                    str(scene_descriptions),
                    "--quote-candidates",
                    str(quote_candidates),
                    "--transcript",
                    str(transcript),
                    "--scenes",
                    str(scenes),
                    "--source-slug",
                    "demo",
                    "--out",
                    str(tmp_dir / "out"),
                ]
            )

        rendered = stderr.getvalue()
        self.assertEqual(rc, 2)
        self.assertIn(
            "pool_build requires at least one surviving visual and one surviving dialogue entry",
            rendered,
        )
        self.assertIn("recovery:", rendered)
        self.assertNotIn("Traceback", rendered)

    def test_pool_build_writes_universal_result_manifest(self) -> None:
        """training.pool_build writes manifest.json as sibling to pool.json,
        preserves POOL_VERSION, and uses kind='pool'."""
        tmp_dir = self.make_tempdir()
        out_dir = tmp_dir / "out"

        triage = tmp_dir / "triage.json"
        scene_descriptions = tmp_dir / "scene_descriptions.json"
        quote_candidates = tmp_dir / "quote_candidates.json"
        transcript = tmp_dir / "transcript.json"
        scenes = tmp_dir / "scenes.json"

        scenes.write_text(
            json.dumps([{"index": 1, "start": 0.0, "end": 3.0, "duration": 3.0}]),
            encoding="utf-8",
        )
        triage.write_text(
            json.dumps({
                "version": 1,
                "generated_at": "2026-04-21T12:00:00Z",
                "entries": [{"scene_id": "scene_001", "triage_score": 4, "triage_tag": "speaker"}],
            }),
            encoding="utf-8",
        )
        scene_descriptions.write_text(
            json.dumps({
                "version": 1,
                "generated_at": "2026-04-21T12:00:00Z",
                "entries": [{
                    "scene_id": "scene_001",
                    "description": "speaker on stage",
                    "mood": "energetic",
                    "motion_level": "high",
                    "speaker_visible": True,
                    "dialogue_salient": True,
                    "motion_tags": ["walk"],
                    "mood_tags": ["bright"],
                    "deep_score": 0.85,
                }],
            }),
            encoding="utf-8",
        )
        quote_candidates.write_text(
            json.dumps({
                "version": 1,
                "generated_at": "2026-04-21T12:00:00Z",
                "candidates": [{
                    "segment_ids": [0],
                    "power": 4,
                    "text": "hello world",
                    "speaker": "Alice",
                    "quote_kind": "one_liner",
                }],
            }),
            encoding="utf-8",
        )
        transcript.write_text(
            json.dumps({
                "segments": [{"start": 0.5, "end": 1.5, "text": "hello world"}],
            }),
            encoding="utf-8",
        )

        rc = pool_build.main([
            "--triage", str(triage),
            "--scene-descriptions", str(scene_descriptions),
            "--quote-candidates", str(quote_candidates),
            "--transcript", str(transcript),
            "--scenes", str(scenes),
            "--source-slug", "demo",
            "--out", str(out_dir),
        ])

        self.assertEqual(rc, 0)

        # pool.json preserved with original shape and POOL_VERSION
        pool_path = out_dir / "pool.json"
        self.assertTrue(pool_path.is_file())
        pool_data = json.loads(pool_path.read_text(encoding="utf-8"))
        self.assertEqual(pool_data["version"], timeline.POOL_VERSION)
        self.assertIn("generated_at", pool_data)
        self.assertEqual(pool_data["source_slug"], "demo")
        self.assertIn("entries", pool_data)
        self.assertIsInstance(pool_data["entries"], list)
        self.assertGreater(len(pool_data["entries"]), 0)

        # manifest.json written as sibling
        manifest_path = out_dir / "manifest.json"
        self.assertTrue(manifest_path.is_file(), f"manifest.json not found at {manifest_path}")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(manifest["schema_version"], 1)
        self.assertEqual(manifest["kind"], "pool")
        self.assertIsInstance(manifest["inputs"], dict)
        self.assertEqual(manifest["inputs"]["triage"], str(triage.resolve()))
        self.assertEqual(manifest["inputs"]["scene_descriptions"], str(scene_descriptions.resolve()))
        self.assertEqual(manifest["inputs"]["quote_candidates"], str(quote_candidates.resolve()))
        self.assertEqual(manifest["inputs"]["transcript"], str(transcript.resolve()))
        self.assertEqual(manifest["inputs"]["scenes"], str(scenes.resolve()))
        self.assertEqual(manifest["inputs"]["source_slug"], "demo")
        self.assertIsInstance(manifest["outputs"], list)
        self.assertEqual(len(manifest["outputs"]), 1)
        self.assertEqual(manifest["outputs"][0]["type"], "file")
        self.assertIn("content_hash", manifest["outputs"][0])
        self.assertIn("bytes", manifest["outputs"][0])

        # created is a non-empty ISO string
        self.assertIsInstance(manifest["created"], str)
        self.assertGreater(len(manifest["created"]), 0)
        self.assertIsInstance(manifest["warnings"], list)


if __name__ == "__main__":
    unittest.main()
