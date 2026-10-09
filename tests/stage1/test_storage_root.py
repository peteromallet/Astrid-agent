from pathlib import Path

import pytest

from astrid.sdk.storage_root import ensure_no_unmigrated_runtime, resolve_runtime_data_root


def test_checkout_default_is_stable_and_cwd_independent(monkeypatch, tmp_path):
    monkeypatch.delenv("BANODOCO_LOCAL_DATA_ROOT", raising=False)
    monkeypatch.chdir(tmp_path)

    assert resolve_runtime_data_root() == Path(__file__).resolve().parents[2] / ".astrid-data"


def test_explicit_data_root_wins(monkeypatch, tmp_path):
    target = tmp_path / "runtime-state"
    monkeypatch.setenv("BANODOCO_LOCAL_DATA_ROOT", str(target))
    assert resolve_runtime_data_root() == target


def test_explicit_data_root_must_be_absolute(monkeypatch):
    monkeypatch.setenv("BANODOCO_LOCAL_DATA_ROOT", ".astrid-data")
    with pytest.raises(ValueError, match="absolute"):
        resolve_runtime_data_root()


def test_missing_checkout_config_uses_persistent_user_root(monkeypatch, tmp_path):
    monkeypatch.delenv("BANODOCO_LOCAL_DATA_ROOT", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(
        "astrid.sdk.storage_root.CONFIG_PATH", tmp_path / "missing-config.json"
    )

    assert resolve_runtime_data_root() == tmp_path / ".astrid-data"


def test_default_does_not_create_blank_realm_over_existing_external_catalog(
    monkeypatch, tmp_path
):
    monkeypatch.delenv("BANODOCO_LOCAL_DATA_ROOT", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    old_catalog = tmp_path / "Library" / "Application Support" / "Banodoco" / "runtime" / "catalog.json"
    old_catalog.parent.mkdir(parents=True)
    old_catalog.write_text('{"selected_realm_id":"existing"}', encoding="utf-8")

    target = tmp_path / "Astrid" / ".astrid-data"
    with pytest.raises(ValueError, match="existing neutral runtime") as caught:
        ensure_no_unmigrated_runtime(target)
    message = str(caught.value)
    assert "astrid-upgrade" in message
    assert "MOVES the existing realm" in message
    assert str(old_catalog.parent) in message
    assert str(target) in message
    assert "BANODOCO_LOCAL_DATA_ROOT" in message
    assert "separate workspace" in message or "its own empty workspace" in message
    assert "is not touched" in message


def test_migration_hint_offers_the_non_destructive_path_in_order(monkeypatch, tmp_path):
    monkeypatch.delenv("BANODOCO_LOCAL_DATA_ROOT", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    old_catalog = tmp_path / "Library" / "Application Support" / "Banodoco" / "runtime" / "catalog.json"
    old_catalog.parent.mkdir(parents=True)
    old_catalog.write_text('{"selected_realm_id":"realm-abc"}', encoding="utf-8")

    with pytest.raises(ValueError) as caught:
        ensure_no_unmigrated_runtime(tmp_path / "Astrid" / ".astrid-data")
    message = str(caught.value)
    assert "realm-abc" in message
    move = message.index("astrid-upgrade")
    keep = message.index("BANODOCO_LOCAL_DATA_ROOT")
    assert move < keep


def test_explicit_data_root_env_bypasses_the_migration_hint(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("BANODOCO_LOCAL_DATA_ROOT", str(tmp_path / "separate"))
    old_catalog = tmp_path / "Library" / "Application Support" / "Banodoco" / "runtime" / "catalog.json"
    old_catalog.parent.mkdir(parents=True)
    old_catalog.write_text('{"selected_realm_id":"existing"}', encoding="utf-8")

    ensure_no_unmigrated_runtime(tmp_path / "separate")
