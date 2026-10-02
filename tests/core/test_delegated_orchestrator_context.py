"""Runtime context placeholders remain explicit and closed during delegation."""
from __future__ import annotations

from pathlib import Path

import pytest

from astrid.core.execution.orchestrator.runner import (
    OrchestratorRunRequest,
    _expand_command_runtime,
)
from astrid.core.execution.orchestrator.schema import (
    OrchestratorValidationError,
    load_orchestrator_manifest,
    validate_orchestrator_definition,
)


def _h3_definition():
    return load_orchestrator_manifest(
        Path(__file__).resolve().parents[2]
        / "astrid/packs/h3_av/orchestrators/transform/orchestrator.yaml"
    )


def test_h3_command_forwards_runtime_owned_project_and_execution_context(tmp_path):
    definition = _h3_definition()
    contract = tmp_path / "admitted-execution.json"
    request = OrchestratorRunRequest(
        orchestrator_id=definition.id,
        project="admitted-project",
        out=tmp_path / "out",
        execution_request=contract,
    )
    argv, _, _ = _expand_command_runtime(definition, request, {
        "request": str(tmp_path / "request.json"),
        "input_bundle": str(tmp_path / "input-bundle.zip"),
        "project": "foreign-input-project",
        "execution_request": str(tmp_path / "foreign-execution.json"),
    })
    assert argv[argv.index("--project") + 1] == "admitted-project"
    assert argv[argv.index("--execution-request") + 1] == str(contract.resolve())
    assert "foreign-input-project" not in argv
    assert str(tmp_path / "foreign-execution.json") not in argv


def test_unrecognized_context_placeholder_is_rejected():
    raw = _h3_definition().to_dict()
    raw["runtime"]["command"]["argv"] += ["{project_typo}"]
    with pytest.raises(OrchestratorValidationError, match="unknown placeholder"):
        validate_orchestrator_definition(raw)
