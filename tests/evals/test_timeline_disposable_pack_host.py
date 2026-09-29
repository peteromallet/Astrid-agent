from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from astrid.sdk import host_bootstrap
from evals.timeline.runtime_adapter import _ensure_disposable_pack_host


def test_disposable_render_host_uses_runtime_issued_worker_handoff(monkeypatch, tmp_path: Path) -> None:
    source = tmp_path / "trusted-source"
    (source / "astrid" / "packs").mkdir(parents=True)
    (source / "pyproject.toml").write_text("[project]\nname = 'test-host'\n", encoding="utf-8")
    runtime_root = tmp_path / "runtime"
    realm_root = runtime_root / "realm"
    support_root = runtime_root / "support"
    realm_root.mkdir(parents=True)
    support_root.mkdir()
    credential = support_root / "credentials" / "astrid-pack-host.token"
    credential.parent.mkdir(parents=True)
    credential.write_text("opaque", encoding="utf-8")
    daemon = SimpleNamespace(
        endpoint="http://127.0.0.1:45678",
        worker_credential_path=credential,
        instance_id="runtime-instance-1",
        root=realm_root,
        support_root=support_root,
    )
    adapter = SimpleNamespace(workspace=SimpleNamespace(health=lambda: {
        "status": "ok", "runtime_epoch": 7, "schema_digest": "schema-1",
    }))
    captured: dict = {}

    def start(value, *, reconfigure_action):
        captured.update(value)
        captured["reconfigure_action"] = reconfigure_action
        return {"host_status": "ready", "host_pid": 1234}

    monkeypatch.setattr(host_bootstrap, "ensure_pack_host", start)

    result = _ensure_disposable_pack_host(daemon, adapter, source)

    assert result == {"host_status": "ready", "host_pid": 1234}
    assert captured["endpoint"] == daemon.endpoint
    assert captured["worker_credential_file"] == str(credential)
    assert captured["worker_actor"] == host_bootstrap.PACK_HOST_ACTOR
    assert captured["worker_scopes"] == list(host_bootstrap.PACK_HOST_SCOPES)
    assert captured["source_checkout"] == str((runtime_root / "host-source").resolve())
    assert captured["runtime_instance_id"] == daemon.instance_id
    assert captured["runtime_epoch"] == 7
    assert captured["schema_digest"] == "schema-1"


def test_disposable_render_host_refuses_missing_checkout(tmp_path: Path) -> None:
    daemon = SimpleNamespace(
        endpoint="http://127.0.0.1:45678",
        worker_credential_path=tmp_path / "worker.token",
        instance_id="runtime-instance-1",
    )
    adapter = SimpleNamespace(workspace=SimpleNamespace(health=lambda: {"status": "ok"}))

    import pytest
    from evals.timeline.runtime_adapter import RuntimeAdapterError

    with pytest.raises(RuntimeAdapterError, match="source checkout does not exist"):
        _ensure_disposable_pack_host(daemon, adapter, tmp_path / "missing")
