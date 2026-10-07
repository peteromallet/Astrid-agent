"""Regression coverage for the H3 v3 transform command declaration."""

from __future__ import annotations

from pathlib import Path

import pytest

from astrid.core.execution.executor.actions import action_executor_definition
from astrid.core.execution.executor.registry import ExecutorRegistry
from astrid.core.execution.executor.runner import ExecutorRunRequest, build_executor_command
from astrid.core.pack.discovery import DiscoveredPack
from astrid.core.pack.loader import load_pack_manifest


REPO_ROOT = Path(__file__).resolve().parents[3]
PACK_ROOT = REPO_ROOT / "astrid/packs/h3_av"


def _projected_transform():
    pack = load_pack_manifest(PACK_ROOT / "pack.yaml")
    discovered = DiscoveredPack(pack, "source", 0)
    return action_executor_definition(discovered, "transform", pack.actions["transform"])


def _base_values(tmp_path: Path) -> dict[str, object]:
    return {
        "request": tmp_path / "request folder" / "request.yaml",
        "asset_map": tmp_path / "asset maps" / "assets.json",
    }


def _command(tmp_path: Path, **extra_inputs: object) -> tuple[str, ...]:
    definition = _projected_transform()
    inputs = _base_values(tmp_path)
    inputs.update(extra_inputs)
    return build_executor_command(
        ExecutorRunRequest(
            executor_id="h3_av.transform",
            out=tmp_path / "output folder",
            python_exec=str(tmp_path / "Python Runtime" / "python"),
            inputs=inputs,
        ),
        ExecutorRegistry((definition,)),
    )


def _base_command(tmp_path: Path) -> tuple[str, ...]:
    values = _base_values(tmp_path)
    return (
        str(tmp_path / "Python Runtime" / "python"),
        "-m",
        "astrid.packs.h3_av.actions.transform.run",
        "--request",
        str(values["request"]),
        "--asset-map",
        str(values["asset_map"]),
        "--out",
        str(tmp_path / "output folder"),
    )


def test_transform_projects_typed_inputs_and_structured_command_binding() -> None:
    definition = _projected_transform()
    ports = {port.name: port for port in definition.inputs}

    assert set(ports) == {
        "request",
        "asset_map",
        "execution_request",
        "project",
        "worker_qualification",
        "cleanup_receipt",
        "require_worker_qualification",
        "editorial_approved",
        "resume",
        "dry_run",
    }
    assert ports["request"].type == ports["asset_map"].type == "file"
    assert ports["request"].required and ports["asset_map"].required
    assert ports["execution_request"].type == "file"
    assert ports["project"].type == "string"
    assert ports["worker_qualification"].type == "file"
    assert ports["cleanup_receipt"].type == "file"
    for name in (
        "require_worker_qualification",
        "editorial_approved",
        "resume",
        "dry_run",
    ):
        assert ports[name].type == "boolean"
        assert ports[name].required is False
        assert ports[name].default is False

    assert definition.command is not None
    assert definition.command.argv == (
        "{python_exec}",
        "-m",
        "astrid.packs.h3_av.actions.transform.run",
        "--request",
        "{request}",
        "--asset-map",
        "{asset_map}",
        "--out",
        "{out}",
    )
    assert tuple((item.input, item.flag, item.optional) for item in definition.command.input_args) == (
        ("execution_request", "--execution-request", True),
        ("project", "--project", True),
        ("worker_qualification", "--worker-qualification", True),
        ("cleanup_receipt", "--cleanup-receipt", True),
        ("require_worker_qualification", "--require-worker-qualification", True),
        ("editorial_approved", "--editorial-approved", True),
        ("resume", "--resume", True),
        ("dry_run", "--dry-run", True),
    )
    assert "{orchestrator_args}" not in definition.command.argv


def test_transform_command_keeps_optional_values_absent_and_paths_atomic(tmp_path: Path) -> None:
    assert _command(tmp_path) == _base_command(tmp_path)

    execution_request = tmp_path / "execution requests" / "target.json"
    worker_qualification = tmp_path / "worker evidence" / "qualified.json"
    cleanup_receipt = tmp_path / "cleanup evidence" / "receipt.json"
    command = _command(
        tmp_path,
        execution_request=execution_request,
        project="project with spaces",
        worker_qualification=worker_qualification,
        cleanup_receipt=cleanup_receipt,
    )
    assert command == _base_command(tmp_path) + (
        "--execution-request",
        str(execution_request),
        "--project",
        "project with spaces",
        "--worker-qualification",
        str(worker_qualification),
        "--cleanup-receipt",
        str(cleanup_receipt),
    )
    assert str(_base_values(tmp_path)["request"]) in command
    assert str(_base_values(tmp_path)["asset_map"]) in command
    assert str(execution_request) in command
    assert str(worker_qualification) in command
    assert str(cleanup_receipt) in command


@pytest.mark.parametrize(
    ("input_name", "flag"),
    (
        ("require_worker_qualification", "--require-worker-qualification"),
        ("editorial_approved", "--editorial-approved"),
        ("resume", "--resume"),
        ("dry_run", "--dry-run"),
    ),
)
def test_transform_boolean_flags_preserve_false_and_true(
    tmp_path: Path,
    input_name: str,
    flag: str,
) -> None:
    assert _command(tmp_path, **{input_name: False}) == _base_command(tmp_path)
    assert _command(tmp_path, **{input_name: True}) == _base_command(tmp_path) + (flag,)
