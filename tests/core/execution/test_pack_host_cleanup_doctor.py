"""``astrid doctor`` reports a pack host that still holds unresolved cleanup.

The cleanup latch is a record, not a gate: the host keeps accepting work, so
doctor stays healthy, but the unresolved cleanup must be visible in both the
JSON envelope and the text output.
"""

from __future__ import annotations

import json

from astrid_runtime_client.contract_metadata import PROTOCOL, SCHEMA_DIGEST

from astrid.core.execution import process_group
from astrid.core.gateway import main
from astrid.runtime_cli import RuntimeResult

NOTE = "ps timed out after 5s x3; kill 4242, then reopen the Astrid client"
SINCE = "2026-10-09T12:00:00+00:00"


class _HealthyRuntime:
    def inspect(self, *, support_root: str, timeout: float = 5.0) -> RuntimeResult:
        return RuntimeResult(("runtime", "workspace", "inspect"), 0, {"ok": True, "realm_id": "selected"})

    def observe(self, command: str, *, support_root: str, timeout: float = 5.0) -> RuntimeResult:
        if command == "doctor":
            return RuntimeResult(("runtime", "doctor"), 0, {"ok": True, "healthy": True, "issues": []})
        health = {"status": "ok", "protocol": PROTOCOL, "schema_digest": SCHEMA_DIGEST}
        return RuntimeResult(("runtime", "status"), 0, {"ok": True, "health": health})


def _patch_support_root(monkeypatch, root):
    monkeypatch.setattr("astrid.runtime_cli.RuntimeCLI", lambda: _HealthyRuntime())
    monkeypatch.setattr("astrid.sdk.storage_root.resolve_runtime_data_root", lambda: root)
    (root / "runtime").mkdir(parents=True, exist_ok=True)


def _latch(root):
    process_group.write_cleanup_latch(
        root / "runtime" / process_group.CLEANUP_LATCH_NAME,
        notes=[NOTE],
        since=SINCE,
    )


def test_doctor_json_surfaces_unresolved_cleanup_latch(monkeypatch, tmp_path, capsys):
    # Resolve first: on macOS /var is a symlink, and the latch reader refuses
    # paths under a symlinked ancestor.
    root = tmp_path.resolve()
    _patch_support_root(monkeypatch, root)
    _latch(root)

    assert main(["doctor", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    latch = payload["astrid_observations"]["cleanupLatch"]
    assert latch["unresolved"] is True
    assert latch["blocked"] is False
    assert latch["record"]["since"] == SINCE
    assert latch["record"]["notes"] == [NOTE]
    assert payload["healthy"] is True


def test_doctor_text_prints_cleanup_line_only_with_latch(monkeypatch, tmp_path, capsys):
    root = tmp_path.resolve()
    _patch_support_root(monkeypatch, root)

    main(["doctor"])
    assert "unresolved cleanup" not in capsys.readouterr().out

    _latch(root)
    main(["doctor"])
    out = capsys.readouterr().out
    assert f"pack host has unresolved cleanup since {SINCE} (still accepting work): {NOTE}" in out
