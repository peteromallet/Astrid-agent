"""Regression coverage for the Iteration v3 review-session command binding."""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest

from astrid.core.execution.executor.actions import action_executor_definition
from astrid.core.execution.executor.registry import ExecutorRegistry
from astrid.core.execution.executor.runner import ExecutorRunRequest, build_executor_command
from astrid.core.pack.discovery import DiscoveredPack
from astrid.core.pack.entrypoint import canonical_runtime_entrypoint
from astrid.core.pack.loader import load_pack_manifest


REPO_ROOT = Path(__file__).resolve().parents[3]
PACK_ROOT = REPO_ROOT / "astrid/packs/iteration"


def _projected_action():
    pack = load_pack_manifest(PACK_ROOT / "pack.yaml")
    discovered = DiscoveredPack(pack, "source", 0)
    return action_executor_definition(
        discovered,
        "experiment_review_session",
        pack.actions["experiment_review_session"],
    )


def _base_values(tmp_path: Path) -> dict[str, object]:
    return {
        "experiment": tmp_path / "experiment files" / "experiment.json",
        "runs_dir": tmp_path / "project runs",
    }


def _fixed_command(tmp_path: Path) -> tuple[str, ...]:
    values = _base_values(tmp_path)
    return (
        str(tmp_path / "Python Runtime" / "python"),
        "-m",
        "astrid.packs.iteration.actions.experiment_review_session.run",
        "--experiment",
        str(values["experiment"]),
        "--runs-dir",
        str(values["runs_dir"]),
        "--out",
        str(tmp_path / "session output"),
    )


def _default_command(tmp_path: Path) -> tuple[str, ...]:
    return _fixed_command(tmp_path) + (
        "--reviewer-id",
        "reviewer",
        "--reviewer-type",
        "human",
        "--port",
        "0",
        "--timeout",
        "0",
    )


def _command(tmp_path: Path, **extra_inputs: object) -> tuple[str, ...]:
    definition = _projected_action()
    inputs = _base_values(tmp_path)
    inputs.update(extra_inputs)
    return build_executor_command(
        ExecutorRunRequest(
            executor_id="iteration.experiment_review_session",
            out=tmp_path / "session output",
            python_exec=str(tmp_path / "Python Runtime" / "python"),
            inputs=inputs,
        ),
        ExecutorRegistry((definition,)),
    )


def _parser():
    with canonical_runtime_entrypoint("iteration.experiment_review_session"):
        module = importlib.import_module(
            "astrid.packs.iteration.actions.experiment_review_session.run"
        )
    return module.build_parser()


def test_review_session_projects_exact_typed_contract() -> None:
    definition = _projected_action()
    ports = {port.name: port for port in definition.inputs}

    assert definition.id == "iteration.experiment_review_session"
    assert set(ports) == {
        "experiment",
        "runs_dir",
        "reviewer_id",
        "reviewer_type",
        "conclusions",
        "port",
        "timeout",
        "no_open",
        "skip_server",
    }
    assert (ports["experiment"].type, ports["experiment"].required) == ("file", True)
    assert (ports["runs_dir"].type, ports["runs_dir"].required) == ("directory", True)
    assert (ports["reviewer_id"].type, ports["reviewer_id"].default) == ("string", "reviewer")
    assert (ports["reviewer_type"].type, ports["reviewer_type"].default) == ("string", "human")
    assert (ports["conclusions"].type, ports["conclusions"].required) == ("file", False)
    assert (ports["port"].type, ports["port"].default) == ("integer", 0)
    assert (ports["timeout"].type, ports["timeout"].default) == ("integer", 0)
    assert (ports["no_open"].type, ports["no_open"].default) == ("boolean", False)
    assert (ports["skip_server"].type, ports["skip_server"].default) == ("boolean", False)

    assert definition.command is not None
    assert definition.command.argv == (
        "{python_exec}",
        "-m",
        "astrid.packs.iteration.actions.experiment_review_session.run",
        "--experiment",
        "{experiment}",
        "--runs-dir",
        "{runs_dir}",
        "--out",
        "{out}",
    )
    assert tuple((item.input, item.flag, item.optional) for item in definition.command.input_args) == (
        ("reviewer_id", "--reviewer-id", True),
        ("reviewer_type", "--reviewer-type", True),
        ("conclusions", "--conclusions", True),
        ("port", "--port", True),
        ("timeout", "--timeout", True),
        ("no_open", "--no-open", True),
        ("skip_server", "--skip-server", True),
    )
    assert "{orchestrator_args}" not in definition.command.argv

    declaration = definition.metadata["action_declaration"]
    assert declaration["outputs"] == [
        {
            "name": name,
            "type": "file",
            "mode": "create_or_replace",
            "path_template": "{out}/" + path,
            "description": description,
        }
        for name, path, description in (
            ("review", "review.json", "Normalized experiment review, copied from the local preparation."),
            ("data", "data.json", "Immutable interactive review data."),
            ("session_html", "review_session.html", "Self-contained interactive review page."),
            ("response_schema", "response_schema.json", "Final rubric response schema."),
            ("media_map", "media_map.json", "Relative run-to-mount prefix mapping."),
            ("state", "review.state.json", "Versioned review draft, or settled final child state."),
            ("validated_final", "review.final.validated.json",
             "Validated final review; published only after final submission."),
        )
    ]
    assert declaration["graph"] == {
        "child_actions": ["iteration.experiment_prepare", "editorial.human_review"],
        "child_orchestrators": [],
    }
    assert declaration["resources"] == [
        {"kind": "implementation", "path": "actions/experiment_review_session/__init__.py"},
        {"kind": "implementation", "path": "actions/experiment_review_session/run.py"},
        {"kind": "documentation", "path": "actions/experiment_review_session/STAGE.md"},
    ]


def test_review_session_zero_and_false_defaults_round_trip_through_parser(tmp_path: Path) -> None:
    command = _command(tmp_path)
    assert command == _default_command(tmp_path)

    parsed = _parser().parse_args(command[3:])
    values = _base_values(tmp_path)
    assert parsed.experiment == values["experiment"]
    assert parsed.runs_dir == values["runs_dir"]
    assert parsed.out == tmp_path / "session output"
    assert parsed.reviewer_id == "reviewer"
    assert parsed.reviewer_type == "human"
    assert parsed.conclusions is None
    assert parsed.port == 0
    assert parsed.timeout == 0
    assert parsed.no_open is False
    assert parsed.skip_server is False


def test_review_session_optional_values_and_paths_are_exact_argv_members(tmp_path: Path) -> None:
    conclusions = tmp_path / "review evidence" / "conclusions.json"
    command = _command(
        tmp_path,
        reviewer_id="reviewer with spaces",
        reviewer_type="agent",
        conclusions=conclusions,
        port=48123,
        timeout=75,
    )
    assert command == _fixed_command(tmp_path) + (
        "--reviewer-id",
        "reviewer with spaces",
        "--reviewer-type",
        "agent",
        "--conclusions",
        str(conclusions),
        "--port",
        "48123",
        "--timeout",
        "75",
    )

    parsed = _parser().parse_args(command[3:])
    assert parsed.reviewer_id == "reviewer with spaces"
    assert parsed.reviewer_type == "agent"
    assert parsed.conclusions == conclusions
    assert parsed.port == 48123
    assert parsed.timeout == 75


@pytest.mark.parametrize(
    ("input_name", "flag"),
    (("no_open", "--no-open"), ("skip_server", "--skip-server")),
)
def test_review_session_boolean_flags_preserve_false_and_true(
    tmp_path: Path,
    input_name: str,
    flag: str,
) -> None:
    assert _command(tmp_path, **{input_name: False}) == _default_command(tmp_path)
    assert _command(tmp_path, **{input_name: True}) == _default_command(tmp_path) + (flag,)
