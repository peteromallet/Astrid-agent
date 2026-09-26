"""Disjoint N1 tests for Runtime aliases and bounded provenance."""

from __future__ import annotations

import pytest

from astrid.sdk import local_compat
from astrid.sdk.autobootstrap import _configured
from astrid.sdk.storage_root import resolve_runtime_data_root
from astrid.sdk.workspace_client import resolve_runtime_connection
from astrid.sdk.local_compat import (
    canonical_value,
    LocalCompatibilityError,
    assert_same_runtime_identity,
    project_runtime_identity,
    resolve_environment,
)


def _identity(**overrides):
    value = {
        "implementation_owner": "banodoco_local.cli:main",
        "module_origin": "/venv/lib/python3.11/site-packages/banodoco_local/cli.py",
        "artifact_sha256": "sha256:" + "a" * 64,
        "support_root": "/tmp/astrid-root",
        "realm_id": "realm-1",
    }
    value.update(overrides)
    return value


def test_projection_keeps_one_owner_root_and_realm() -> None:
    projected = project_runtime_identity(_identity(command_alias="astrid-local"))
    assert projected["implementation_owner"] == "banodoco_local.cli:main"
    assert projected["support_root"] == "/tmp/astrid-root"
    assert projected["realm_id"] == "realm-1"


def test_aliases_must_share_identity() -> None:
    assert_same_runtime_identity([
        _identity(command_alias="astrid-local"),
        _identity(command_alias="banodoco-local"),
    ])


def test_aliases_with_different_root_fail() -> None:
    with pytest.raises(LocalCompatibilityError, match="disagree"):
        assert_same_runtime_identity([
            _identity(),
            _identity(support_root="/tmp/other-root"),
        ])


def test_noncanonical_owner_fails() -> None:
    with pytest.raises(LocalCompatibilityError, match="owner is not canonical"):
        project_runtime_identity(_identity(implementation_owner="other:main"))


def test_bad_artifact_digest_fails() -> None:
    with pytest.raises(LocalCompatibilityError, match="artifact digest"):
        project_runtime_identity(_identity(artifact_sha256="sha256:short"))


def test_old_only_environment_warns_and_resolves() -> None:
    resolution = resolve_environment({"BANODOCO_LOCAL_DATA_ROOT": "/tmp/legacy"})
    assert canonical_value("ASTRID_LOCAL_DATA_ROOT", {"BANODOCO_LOCAL_DATA_ROOT": "/tmp/legacy"}) == "/tmp/legacy"
    assert resolution.warnings == (
        "BANODOCO_LOCAL_DATA_ROOT is deprecated; use ASTRID_LOCAL_DATA_ROOT instead",
    )


def test_environment_conflicts_and_relative_roots_fail_closed() -> None:
    with pytest.raises(LocalCompatibilityError, match="conflicting environment"):
        resolve_environment({
            "ASTRID_LOCAL_DATA_ROOT": "/tmp/new",
            "BANODOCO_LOCAL_DATA_ROOT": "/tmp/old",
        })
    with pytest.raises(LocalCompatibilityError, match="absolute path"):
        resolve_environment({"ASTRID_LOCAL_DATA_ROOT": "relative"})


def test_storage_root_consumer_accepts_legacy_and_warns(monkeypatch, capsys, tmp_path) -> None:
    monkeypatch.delenv("ASTRID_LOCAL_DATA_ROOT", raising=False)
    monkeypatch.setenv("BANODOCO_LOCAL_DATA_ROOT", str(tmp_path / "legacy-root"))
    local_compat._EMITTED_WARNINGS.clear()
    assert resolve_runtime_data_root() == (tmp_path / "legacy-root").resolve()
    assert "BANODOCO_LOCAL_DATA_ROOT is deprecated" in capsys.readouterr().err


def test_runtime_cli_and_launcher_consumers_use_canonical_or_legacy(monkeypatch, capsys) -> None:
    monkeypatch.delenv("ASTRID_LOCAL_CLI", raising=False)
    monkeypatch.setenv("ASTRID_RUNTIME_CLI", "/opt/astrid-local")
    local_compat._EMITTED_WARNINGS.clear()
    from astrid.runtime_cli import _runtime_command

    assert _runtime_command() == ("/opt/astrid-local",)
    assert "ASTRID_RUNTIME_CLI is deprecated" in capsys.readouterr().err

    monkeypatch.delenv("ASTRID_LOCAL_LAUNCHER", raising=False)
    monkeypatch.setenv("BANODOCO_LOCAL_LAUNCHER", "/opt/astrid-local")
    local_compat._EMITTED_WARNINGS.clear()
    assert _configured("ASTRID_LOCAL_LAUNCHER") == "/opt/astrid-local"
    assert "BANODOCO_LOCAL_LAUNCHER is deprecated" in capsys.readouterr().err


def test_workspace_client_consumer_uses_legacy_connection_aliases(monkeypatch, capsys) -> None:
    monkeypatch.delenv("ASTRID_RUNTIME_ENDPOINT", raising=False)
    monkeypatch.delenv("ASTRID_RUNTIME_CREDENTIAL", raising=False)
    monkeypatch.setenv("BANODOCO_RUNTIME_ENDPOINT", "http://127.0.0.1:8443")
    monkeypatch.setenv("BANODOCO_RUNTIME_CREDENTIAL", "token")
    local_compat._EMITTED_WARNINGS.clear()
    assert resolve_runtime_connection() == ("http://127.0.0.1:8443", "token")
    stderr = capsys.readouterr().err
    assert "BANODOCO_RUNTIME_ENDPOINT is deprecated" in stderr
    assert "BANODOCO_RUNTIME_CREDENTIAL is deprecated" in stderr


def test_child_process_legacy_keys_are_registered_aliases() -> None:
    from astrid.sdk.local_compat import legacy_environment_name

    assert legacy_environment_name("ASTRID_RUNTIME_ENDPOINT") == "BANODOCO_RUNTIME_ENDPOINT"
    assert legacy_environment_name("ASTRID_RUNTIME_CREDENTIAL") == "BANODOCO_RUNTIME_CREDENTIAL"
