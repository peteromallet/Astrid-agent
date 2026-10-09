"""``astrid doctor`` reports a pack host blocked by cleanup uncertainty."""

from __future__ import annotations

import json

from astrid.core.execution import process_group
from astrid.core.gateway import main
from astrid.runtime_cli import RuntimeResult
from banodoco_workspace_client.contract_metadata import SCHEMA_DIGEST


class _HealthyRuntime:
    def inspect(self, *, support_root: str, timeout: float = 5.0) -> RuntimeResult:
        return RuntimeResult(("runtime", "workspace", "inspect"), 0, {"ok": True, "realm_id": "selected"})

    def observe(self, command: str, *, support_root: str, timeout: float = 5.0) -> RuntimeResult:
        if command == "doctor":
            return RuntimeResult(("runtime", "doctor"), 0, {"ok": True, "healthy": True, "issues": []})
        health = {"status": "ok", "schema_digest": SCHEMA_DIGEST}
        return RuntimeResult(("runtime", "status"), 0, {"ok": True, "health": health})


def _latch(tmp_path):
    process_group.write_cleanup_latch(
        tmp_path / process_group.CLEANUP_LATCH_NAME,
        reason="ps timed out after 5s x3",
        since="2026-10-09T12:00:00+00:00",
        recover_with="kill 4242, then reopen the Astrid client",
    )


def test_doctor_json_reports_blocked_pack_host_and_is_unhealthy(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr("astrid.runtime_cli.RuntimeCLI", lambda: _HealthyRuntime())
    monkeypatch.setattr("astrid.sdk.storage_root.resolve_runtime_data_root", lambda: tmp_path)
    _latch(tmp_path)

    assert main(["doctor", "--json"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["pack_host_cleanup"]["reason"] == "ps timed out after 5s x3"
    assert payload["healthy"] is False
    assert (
        "pack host blocked by cleanup uncertainty since 2026-10-09T12:00:00+00:00; "
        "cause ps timed out after 5s x3; recover with kill 4242, then reopen the Astrid client"
    ) in payload["issues"]


def test_doctor_text_prints_blocked_line_and_stays_healthy_without_latch(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr("astrid.runtime_cli.RuntimeCLI", lambda: _HealthyRuntime())
    monkeypatch.setattr("astrid.sdk.storage_root.resolve_runtime_data_root", lambda: tmp_path)

    assert main(["doctor"]) == 0
    assert "pack host blocked" not in capsys.readouterr().out

    _latch(tmp_path)
    assert main(["doctor"]) == 1
    assert "pack host blocked by cleanup uncertainty since" in capsys.readouterr().out
