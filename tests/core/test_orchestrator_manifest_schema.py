from __future__ import annotations

from copy import deepcopy
from typing import Any

import jsonschema

from astrid.core.execution.orchestrator.registry import load_default_registry
from astrid.core.execution.orchestrator.runner import OrchestratorRunRequest, run_orchestrator
from astrid.core.execution.orchestrator.schema import load_orchestrator_manifest
from astrid.core.foundation.paths import REPO_ROOT
from astrid.core.pack.manifest import load_manifest_mapping
from astrid.core.pack.validate import KNOWN_SCHEMA_VERSIONS, PackValidator

PACKS_ROOT = REPO_ROOT / "astrid" / "packs"
PACK_ROOTS = tuple(sorted(path for path in PACKS_ROOT.iterdir() if (path / "pack.yaml").is_file()))
BUILTIN_ORCHESTRATOR_MANIFESTS = tuple(
    sorted(path for pack_root in PACK_ROOTS for path in (pack_root / "orchestrators").glob("*/orchestrator.yaml"))
)


def _orchestrator_validator() -> jsonschema.Draft7Validator:
    schema_path = KNOWN_SCHEMA_VERSIONS[1]["orchestrator"]
    schema, registry = PackValidator(REPO_ROOT)._load_schema(
        schema_path,
        "orchestrator",
        1,
    )
    return jsonschema.Draft7Validator(schema, registry=registry)


def _schema_errors(payload: dict[str, Any]) -> list[str]:
    validator = _orchestrator_validator()
    return sorted(error.message for error in validator.iter_errors(payload))


def _pack_root_for(manifest_path):
    relative = manifest_path.relative_to(PACKS_ROOT)
    return PACKS_ROOT / relative.parts[0]


def test_all_builtin_orchestrator_manifests_validate_against_json_schema() -> None:
    assert BUILTIN_ORCHESTRATOR_MANIFESTS

    failures: dict[str, list[str]] = {}
    for manifest_path in BUILTIN_ORCHESTRATOR_MANIFESTS:
        payload = load_manifest_mapping(manifest_path, manifest_kind="orchestrator")
        if payload.get("schema_version") != 1:
            failures[str(manifest_path.relative_to(REPO_ROOT))] = [
                f"schema_version must be 1, got {payload.get('schema_version')!r}"
            ]
            continue

        errors = _schema_errors(payload)
        if errors:
            failures[str(manifest_path.relative_to(REPO_ROOT))] = errors

    assert failures == {}


def test_pack_validator_loads_every_builtin_orchestrator_manifest() -> None:
    failures: dict[str, list[str]] = {}

    for manifest_path in BUILTIN_ORCHESTRATOR_MANIFESTS:
        validator = PackValidator(_pack_root_for(manifest_path))
        before = list(validator.errors)
        payload = validator.validate_component_manifest(manifest_path, "orchestrator")
        if payload is None or validator.errors != before:
            failures[str(manifest_path.relative_to(REPO_ROOT))] = validator.errors[len(before) :]

    assert failures == {}


def test_all_builtin_orchestrator_manifests_parse_through_runtime_and_registry() -> None:
    registry = load_default_registry()
    failures: dict[str, str] = {}

    for manifest_path in BUILTIN_ORCHESTRATOR_MANIFESTS:
        parsed = load_orchestrator_manifest(manifest_path)
        try:
            registered = registry.get(parsed.id)
        except KeyError as exc:
            failures[str(manifest_path.relative_to(REPO_ROOT))] = str(exc)
            continue

        if registered.runtime != parsed.runtime:
            failures[str(manifest_path.relative_to(REPO_ROOT))] = (
                f"registry runtime {registered.runtime!r} != parsed runtime {parsed.runtime!r}"
            )

    assert failures == {}


def test_h3_transform_declares_vibecomfy_import_environment() -> None:
    payload = load_manifest_mapping(
        PACKS_ROOT / "h3_av" / "orchestrators" / "transform" / "orchestrator.yaml",
        manifest_kind="orchestrator",
    )

    declared = set(payload["isolation"]["env_passthrough"])
    assert {
        "ASTRID_RUNPOD_ACCOUNT_REF",
        "VIBECOMFY_CHECKOUT",
        "PYTHONPATH",
        "VIBECOMFY_HEADLESS",
    } <= declared


def test_h3_sdk_orchestrator_subprocess_receives_selected_runpod_account_ref(
    tmp_path, monkeypatch,
) -> None:
    import json
    from types import SimpleNamespace

    import astrid.core.execution.orchestrator.runner as runner

    request_path = tmp_path / "request.json"
    request_path.write_text("{}\n", encoding="utf-8")
    asset_map_path = tmp_path / "asset-map.json"
    asset_map_path.write_text("{}\n", encoding="utf-8")
    execution_request_path = tmp_path / "execution-request.json"
    execution_request_path.write_text(
        json.dumps({
            "target": {
                "kind": "runpod",
                "pod_id": "pod-fixture",
                "provider_account_ref": "runpod-default",
                "storage": {"network_volume_id": "volume-fixture"},
            },
            "lifecycle": {"mode": "leave_running"},
        }),
        encoding="utf-8",
    )
    selection_path = tmp_path / "prepared-worker-selection.json"
    selection_path.write_text("{}\n", encoding="utf-8")
    registry = load_default_registry()
    monkeypatch.setenv("ASTRID_RUNPOD_ACCOUNT_REF", "runpod-default")
    captured = {}

    def capture_subprocess(argv, *, cwd=None, env=None, check=False):
        captured.update(env or {})
        captured["argv"] = list(argv)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(runner.subprocess, "run", capture_subprocess)
    result = run_orchestrator(
        OrchestratorRunRequest(
            orchestrator_id="h3_av.transform",
            out=tmp_path / "out",
            project="prepared-worker-env-test",
            project_was_auto_resolved=True,
                inputs={
                    "request": request_path,
                    "asset_map": asset_map_path,
                    "execution_request": execution_request_path,
                },
            orchestrator_args=(
                "--prepared-worker", str(selection_path),
                "--require-worker-qualification",
            ),
        ),
        registry,
    )

    assert result.returncode == 0
    assert captured["ASTRID_RUNPOD_ACCOUNT_REF"] == "runpod-default"
    assert "--prepared-worker" in captured["argv"]
    assert str(selection_path) in captured["argv"]
    assert "RUNPOD_API_KEY" not in captured


def test_orchestrator_schema_accepts_legacy_python_cli_runtime_shape() -> None:
    payload = load_manifest_mapping(
        PACKS_ROOT / "video_editing" / "orchestrators" / "hype" / "orchestrator.yaml",
        manifest_kind="orchestrator",
    )
    legacy_payload = deepcopy(payload)
    legacy_payload["runtime"] = {
        "type": "python-cli",
        "entrypoint": "run.py",
        "callable": "main",
    }

    errors = _schema_errors(legacy_payload)

    assert errors == []


def test_orchestrator_schema_rejects_missing_schema_version() -> None:
    payload = load_manifest_mapping(
        PACKS_ROOT / "video_editing" / "orchestrators" / "hype" / "orchestrator.yaml",
        manifest_kind="orchestrator",
    )
    payload.pop("schema_version")

    errors = _schema_errors(payload)

    assert "'schema_version' is a required property" in errors
