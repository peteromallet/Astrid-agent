from __future__ import annotations

import unittest

from astrid.core.execution.executor.registry import load_default_registry as load_executor_registry
from astrid.core.execution.orchestrator.registry import load_default_registry as load_orchestrator_registry


class DefaultRegistryScopeTest(unittest.TestCase):
    def test_default_executor_registries_include_packs(self) -> None:
        canonical = load_executor_registry()
        canonical_ids = set(canonical.as_mapping())

        self.assertIn("rendering.render", canonical_ids)
        self.assertIn("youtube.upload", canonical_ids)
        self.assertIn("moirae.moirae", canonical_ids)
        self.assertIn("vibecomfy.run", canonical_ids)
        self.assertIn("vibecomfy.validate", canonical_ids)

        youtube = canonical.get("youtube.upload")
        self.assertEqual(youtube.metadata["source"], "pack")
        self.assertEqual(youtube.metadata["source_pack"], "youtube")
        self.assertNotIn("pack_id", youtube.metadata)
        self.assertTrue(youtube.metadata["pack_root"].endswith("astrid/packs/youtube"))
        youtube_declaration = youtube.metadata["action_declaration"]
        self.assertEqual(youtube_declaration["metadata"]["runtime_file"], "actions/upload/run.py")
        self.assertEqual(youtube_declaration["metadata"]["runtime_module"], "astrid.packs.youtube.actions.upload.run")
        youtube_invocation = youtube.metadata["action_invocation"]
        self.assertEqual(youtube_invocation["kind"], "python")
        self.assertEqual(youtube_invocation["path"], "actions/upload/run.py")
        self.assertEqual(youtube_invocation["function"], "run")

        for executor_id, folder in (
            ("understanding.audio_understand", "audio_understand"),
            ("understanding.visual_understand", "visual_understand"),
            ("understanding.video_understand", "video_understand"),
        ):
            with self.subTest(executor_id=executor_id):
                action = canonical.get(executor_id)
                self.assertEqual(action.metadata["source"], "pack")
                self.assertEqual(action.metadata["source_pack"], "understanding")
                self.assertNotIn("pack_id", action.metadata)
                self.assertTrue(action.metadata["pack_root"].endswith("astrid/packs/understanding"))
                action_declaration = action.metadata["action_declaration"]
                self.assertEqual(action_declaration["metadata"]["runtime_file"], f"actions/{folder}/run.py")
                module = f"astrid.packs.understanding.actions.{folder}.run"
                self.assertEqual(action_declaration["metadata"]["runtime_module"], module)
                action_invocation = action.metadata["action_invocation"]
                self.assertEqual(action_invocation["kind"], "command")
                self.assertEqual(action_invocation["command"]["argv"][:3], ["{python_exec}", "-m", module])

        vibecomfy = canonical.get("vibecomfy.run")
        self.assertEqual(vibecomfy.kind, "external")
        self.assertEqual(vibecomfy.metadata["pack_id"], "vibecomfy")
        self.assertEqual(vibecomfy.metadata["source_pack"], "vibecomfy")
        self.assertEqual(vibecomfy.metadata["source"], "pack")
        self.assertTrue(vibecomfy.metadata["pack_root"].endswith("astrid/packs/vibecomfy"))
        vibecomfy_declaration = vibecomfy.metadata["action_declaration"]
        self.assertEqual(vibecomfy_declaration["metadata"]["runtime_file"], "actions/run/run.py")
        self.assertEqual(vibecomfy_declaration["metadata"]["runtime_module"], "astrid.packs.vibecomfy.actions.run.run")
        vibecomfy_invocation = vibecomfy.metadata["action_invocation"]
        self.assertEqual(vibecomfy_invocation["kind"], "command")
        self.assertEqual(
            vibecomfy_invocation["command"]["argv"][:3],
            ["{python_exec}", "-m", "astrid.packs.vibecomfy.actions.run.run"],
        )

    def test_default_orchestrator_registries_do_not_classify_vibecomfy_as_orchestrator(self) -> None:
        canonical = load_orchestrator_registry(executor_registry=load_executor_registry())
        canonical_ids = set(canonical.as_mapping())

        self.assertIn("video_editing.hype", canonical_ids)
        self.assertIn("video_editing.event_talks", canonical_ids)
        self.assertIn("video_editing.thumbnail_maker", canonical_ids)
        self.assertFalse(any("vibecomfy" in orchestrator_id for orchestrator_id in canonical_ids))
        self.assertFalse(any(orchestrator_id == "youtube.upload" for orchestrator_id in canonical_ids))
        with self.assertRaises(KeyError):
            canonical.get("vibecomfy.run")

    def test_canonical_builtin_executor_runtime_module(self) -> None:
        canonical = load_executor_registry()
        render = canonical.get("rendering.render")
        self.assertEqual(render.metadata["runtime_module"], "astrid.packs.rendering.actions.render.run")

    def test_external_executor_roots_are_pack_native(self) -> None:
        registry = load_executor_registry()

        moirae = registry.get("moirae.moirae")
        self.assertTrue(moirae.metadata["pack_root"].endswith("astrid/packs/moirae"))
        moirae_declaration = moirae.metadata["action_declaration"]
        self.assertEqual(moirae_declaration["metadata"]["runtime_file"], "actions/moirae/run.py")
        self.assertEqual(moirae_declaration["metadata"]["runtime_module"], "astrid.packs.moirae.actions.moirae.run")
        moirae_invocation = moirae.metadata["action_invocation"]
        self.assertEqual(moirae_invocation["kind"], "command")
        self.assertEqual(
            moirae_invocation["command"]["argv"][:3],
            ["{python_exec}", "-m", "astrid.packs.moirae.actions.moirae.run"],
        )

        vibecomfy = registry.get("vibecomfy.run")
        self.assertTrue(vibecomfy.metadata["pack_root"].endswith("astrid/packs/vibecomfy"))


if __name__ == "__main__":
    unittest.main()
