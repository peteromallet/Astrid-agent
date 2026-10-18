import json
from pathlib import Path

import pytest

from astrid.sdk.storage_root import ensure_no_unmigrated_runtime, resolve_runtime_data_root


def test_checkout_default_is_stable_and_cwd_independent(monkeypatch, tmp_path):
    monkeypatch.delenv("ASTRID_RUNTIME_DATA_ROOT", raising=False)
    monkeypatch.setenv("ASTRID_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.chdir(tmp_path)

    assert resolve_runtime_data_root() == Path(__file__).resolve().parents[2] / ".astrid-data"


def test_explicit_data_root_wins(monkeypatch, tmp_path):
    target = tmp_path / "runtime-state"
    monkeypatch.setenv("ASTRID_RUNTIME_DATA_ROOT", str(target))
    assert resolve_runtime_data_root() == target


def test_explicit_data_root_must_be_absolute(monkeypatch):
    monkeypatch.setenv("ASTRID_RUNTIME_DATA_ROOT", ".astrid-data")
    with pytest.raises(ValueError, match="absolute"):
        resolve_runtime_data_root()


def test_missing_checkout_config_uses_persistent_user_root(monkeypatch, tmp_path):
    monkeypatch.delenv("ASTRID_RUNTIME_DATA_ROOT", raising=False)
    monkeypatch.setenv("ASTRID_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(
        "astrid.sdk.storage_root.CONFIG_PATH", tmp_path / "missing-config.json"
    )

    assert resolve_runtime_data_root() == tmp_path / ".astrid-data"


def test_default_does_not_create_blank_realm_over_existing_external_catalog(
    monkeypatch, tmp_path
):
    monkeypatch.delenv("ASTRID_RUNTIME_DATA_ROOT", raising=False)
    monkeypatch.setenv("ASTRID_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("HOME", str(tmp_path))
    old_catalog = tmp_path / "Library" / "Application Support" / "Astrid Runtime" / "runtime" / "catalog.json"
    old_catalog.parent.mkdir(parents=True)
    old_catalog.write_text('{"selected_realm_id":"existing"}', encoding="utf-8")

    with pytest.raises(ValueError, match="existing neutral runtime") as caught:
        ensure_no_unmigrated_runtime(tmp_path / "Astrid" / ".astrid-data")
    assert "astrid-upgrade" in str(caught.value)


def test_persisted_setup_selection_is_reused_when_install_config_is_only_a_default(
    monkeypatch, tmp_path
):
    monkeypatch.delenv("ASTRID_RUNTIME_DATA_ROOT", raising=False)
    monkeypatch.setenv("ASTRID_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setattr("astrid.sdk.storage_root.CONFIG_PATH", tmp_path / "missing-config.json")
    selected = tmp_path / "selected-support"
    state_path = tmp_path / "state" / "astrid" / "skills.json"
    state_path.parent.mkdir(parents=True)
    state_path.write_text(
        json.dumps({"version": 1, "setup_selection": {"support_root": str(selected)}}),
        encoding="utf-8",
    )

    assert resolve_runtime_data_root() == selected


def test_persisted_and_configured_existing_realms_fail_closed_as_ambiguous(
    monkeypatch, tmp_path
):
    monkeypatch.delenv("ASTRID_RUNTIME_DATA_ROOT", raising=False)
    monkeypatch.setenv("ASTRID_STATE_HOME", str(tmp_path / "state"))
    selected = tmp_path / "selected-support"
    configured = tmp_path / "configured-support"
    for root, realm in ((selected, "selected"), (configured, "configured")):
        catalog = root / "runtime" / "catalog.json"
        catalog.parent.mkdir(parents=True)
        catalog.write_text(json.dumps({"selected_realm_id": realm}), encoding="utf-8")
    state_path = tmp_path / "state" / "astrid" / "skills.json"
    state_path.parent.mkdir(parents=True)
    state_path.write_text(
        json.dumps({"version": 1, "setup_selection": {"support_root": str(selected)}}),
        encoding="utf-8",
    )
    config_path = tmp_path / "astrid-runtime.json"
    config_path.write_text(json.dumps({"version": 1, "data_root": str(configured)}), encoding="utf-8")
    monkeypatch.setattr("astrid.sdk.storage_root.CONFIG_PATH", config_path)

    with pytest.raises(ValueError, match="ambiguous"):
        resolve_runtime_data_root()


def test_explicit_legacy_root_is_retained_without_migration(monkeypatch, tmp_path):
    legacy = tmp_path / "Library" / "Application Support" / "Astrid Runtime"
    catalog = legacy / "runtime" / "catalog.json"
    catalog.parent.mkdir(parents=True)
    catalog.write_text(json.dumps({"selected_realm_id": "legacy"}), encoding="utf-8")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("ASTRID_RUNTIME_DATA_ROOT", str(legacy))

    assert resolve_runtime_data_root() == legacy
    ensure_no_unmigrated_runtime(legacy)


def test_different_existing_legacy_and_configured_realms_fail_before_side_effects(
    monkeypatch, tmp_path
):
    monkeypatch.delenv("ASTRID_RUNTIME_DATA_ROOT", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    target = tmp_path / "Astrid" / ".astrid-data"
    target_catalog = target / "runtime" / "catalog.json"
    target_catalog.parent.mkdir(parents=True)
    target_catalog.write_text(json.dumps({"selected_realm_id": "new"}), encoding="utf-8")
    legacy_catalog = tmp_path / "Library" / "Application Support" / "Astrid Runtime" / "runtime" / "catalog.json"
    legacy_catalog.parent.mkdir(parents=True)
    legacy_catalog.write_text(json.dumps({"selected_realm_id": "old"}), encoding="utf-8")

    with pytest.raises(ValueError, match="ambiguous"):
        ensure_no_unmigrated_runtime(target)
    assert not (target / "runtime" / "realms").exists()
