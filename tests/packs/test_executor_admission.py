"""Executor and orchestrator admission is one check: validation and discovery agree.

Discovery (``GenericPackHost.discover`` and ``admit``) skips an executor or
orchestrator manifest that its folder loader rejects, silently. These tests pin
that the pack validator rejects the same manifest with the same message, that a
limit of exactly 500 description chars passes, and that doctor names the skipped
executor so a pack never vanishes without a reason.
"""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from astrid.core.execution.executor.folder import load_folder_executor
from astrid.core.execution.executor.schema import ExecutorValidationError
from astrid.core.execution.generic_host import GenericPackHost, executor_skip_section
from astrid.core.pack.validate import validate_pack

DESCRIPTION_LIMIT_MESSAGE = "description is 501 chars; max is 500"


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _pack(description: str) -> Path:
    base = Path(tempfile.mkdtemp(prefix="test-executor-admission-"))
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
  executors: executors
agent:
  purpose: Testing
""",
    )
    _write(root / "skill" / "SKILL.md", "# Test Pack\n\nAgent guide.")
    comp = root / "executors" / "long"
    _write(
        comp / "executor.yaml",
        json.dumps({
            "schema_version": 1,
            "id": "test_pack.long",
            "name": "Long",
            "kind": "external",
            "version": "1.0",
            "description": description,
            "command": {"argv": ["{python_exec}", "-c", "pass"]},
            "outputs": [{"name": "answer", "type": "file", "path_template": "{out}/answer.txt", "artifact_type": "text/plain"}],
            "metadata": {"resource_keys": ["cpu"], "estimated_scratch_bytes": 1},
        }),
    )
    _write(comp / "STAGE.md", "# Long\n")
    return root


class ExecutorDescriptionAdmissionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.roots: list[Path] = []

    def tearDown(self) -> None:
        for root in self.roots:
            shutil.rmtree(root.parent, ignore_errors=True)

    def _make(self, description: str) -> Path:
        root = _pack(description)
        self.roots.append(root)
        return root

    def test_validator_rejects_description_over_limit(self) -> None:
        root = self._make("x" * 501)
        errors, _warnings = validate_pack(root)
        matching = [e for e in errors if DESCRIPTION_LIMIT_MESSAGE in e]
        self.assertEqual(len(matching), 1, errors)
        self.assertIn("executors/long/executor.yaml", matching[0])
        self.assertIn("test_pack.long", matching[0])

    def test_validator_accepts_description_at_limit(self) -> None:
        root = self._make("x" * 500)
        errors, _warnings = validate_pack(root)
        self.assertFalse([e for e in errors if "description" in e], errors)

    def test_validator_message_is_discovery_message(self) -> None:
        """Validator and discovery share one loader, so the text is identical."""
        root = self._make("x" * 501)
        folder = root / "executors" / "long"
        with self.assertRaises(ExecutorValidationError) as ctx:
            load_folder_executor(folder)
        errors, _warnings = validate_pack(root)
        self.assertIn(str(ctx.exception), " ".join(errors))

    def test_discovery_skips_the_bad_executor_and_names_it_in_doctor(self) -> None:
        root = self._make("x" * 501)
        host = GenericPackHost(pack_roots=[root.parent], capability_matrix=None)
        self.assertEqual(host.discover(), ())
        section = executor_skip_section(pack_roots=[root.parent])
        self.assertEqual(section["count"], 1)
        record = section["skipped"][0]
        self.assertEqual(record["kind"], "executor")
        self.assertTrue(record["folder"].endswith("executors/long"))
        self.assertIn(DESCRIPTION_LIMIT_MESSAGE, record["error"])
        self.assertIn("fix", record)


class OrchestratorDescriptionAdmissionTest(unittest.TestCase):
    """Orchestrators go through the same loader as executors (discovery admits them in ``admit``)."""

    def test_validator_rejects_orchestrator_description_over_limit(self) -> None:
        base = Path(tempfile.mkdtemp(prefix="test-orchestrator-admission-"))
        self.addCleanup(shutil.rmtree, base, True)
        root = base / "test_pack"
        _write(
            root / "pack.yaml",
            "schema_version: 2\nid: test_pack\nname: Test Pack\nversion: 0.1.0\ndescription: A test pack.\n"
            "content:\n  orchestrators: orchestrators\nagent:\n  purpose: Testing\n",
        )
        _write(root / "skill" / "SKILL.md", "# Test Pack\n\nAgent guide.")
        comp = root / "orchestrators" / "long"
        _write(
            comp / "orchestrator.yaml",
            json.dumps({
                "schema_version": 1,
                "id": "test_pack.long_flow",
                "name": "Long Flow",
                "kind": "built_in",
                "version": "1.0",
                "description": "x" * 501,
                "child_executors": [],
                "child_orchestrators": [],
                "runtime": {"kind": "command", "command": {"argv": ["{python_exec}", "-c", "pass"]}},
            }),
        )
        _write(comp / "STAGE.md", "# Long\n")
        errors, _warnings = validate_pack(root)
        matching = [e for e in errors if DESCRIPTION_LIMIT_MESSAGE in e]
        self.assertEqual(len(matching), 1, errors)
        self.assertIn("orchestrators/long/orchestrator.yaml", matching[0])


if __name__ == "__main__":
    unittest.main()
