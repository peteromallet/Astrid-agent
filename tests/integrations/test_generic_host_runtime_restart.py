"""A running pack host follows a Runtime restart; no Astrid client needs to open."""

from __future__ import annotations

import hashlib
import json
import threading
from pathlib import Path

import pytest

pytest.importorskip("runtime_protocol")
from astrid_runtime_client import WorkspaceClient  # noqa: E402
from runtime_protocol.daemon import RuntimeDaemon  # noqa: E402
from runtime_protocol.store import RealmStore  # noqa: E402

from astrid.core.execution import generic_host  # noqa: E402
from astrid.core.execution.generic_host import GenericPackHost, RuntimeProtocolClient  # noqa: E402
from astrid.sdk.host_bootstrap import PACK_HOST_ACTOR  # noqa: E402


def _executor(pack: Path) -> None:
    pack.mkdir()
    (pack / "executor.yaml").write_text(json.dumps({
        "schema_version": 1, "id": "render.basic", "name": "Echo", "kind": "external", "version": "1.0",
        "command": {"argv": ["{python_exec}", "-c",
                             "from pathlib import Path; Path('{out}/answer.txt').write_text('hello')"]},
        "outputs": [{"name": "answer", "type": "file", "path_template": "{out}/answer.txt",
                     "artifact_type": "text/plain"}],
        "metadata": {"adapter_family": "cpu", "resource_keys": ["cpu"]},
    }), encoding="utf-8")


@pytest.mark.parametrize("concurrency", [1, 2])
def test_idle_host_claims_work_admitted_after_the_runtime_restarts(tmp_path: Path, monkeypatch, concurrency) -> None:
    # Renew at once, so the host's own loop (not a client) is what must notice the restart.
    monkeypatch.setattr(generic_host, "_EXECUTOR_REFRESH_SECONDS", 0.0)
    pack = tmp_path / "pack"
    _executor(pack)
    record = GenericPackHost(pack_roots=[pack]).discover()[0]
    realm, support = tmp_path / "realm", tmp_path / "support"
    RealmStore.initialize(realm).close()

    def start(port: int = 0) -> RuntimeDaemon:
        return RuntimeDaemon(realm, support_root=support, port=port,
                             production_worker_credentials=True).start()

    daemon = start()
    port = daemon.httpd.server_port
    first_epoch = WorkspaceClient(daemon.endpoint, daemon.token).health().runtime_epoch
    host = GenericPackHost(
        pack_roots=[pack], executor_id=PACK_HOST_ACTOR, max_concurrency=concurrency,
        client=RuntimeProtocolClient(daemon.endpoint, daemon.worker_credential_path.read_text().strip(),
                                     credential_file=daemon.worker_credential_path),
    )
    host.register()
    served: list = []
    thread = threading.Thread(target=lambda: served.extend(host.run(poll_seconds=0.05, max_tasks=1)), daemon=True)
    thread.start()
    try:
        # The Runtime restarts under the idle, still-running host (new epoch, rotated worker credential).
        daemon.stop()
        daemon = start(port)
        api = WorkspaceClient(daemon.endpoint, daemon.token)
        assert api.health().runtime_epoch != first_epoch
        project = api.create_project("Restart", slug="restart", idempotency_key="restart-project")
        task = api.admit_task(capability_id=record.id, capability_digest=record.capability_digest,
                              input_object_ids=[], project_id=project.project_id, idempotency_key="restart-task")
        # No Astrid client opens: the host alone must register under the new epoch and claim it.
        thread.join(timeout=60)
        assert not thread.is_alive(), f"host never claimed the task: {host._runtime_registration_fence!r}"
        assert api.get_task(task.task_id).state == "succeeded"
        digest = "sha256:" + hashlib.sha256(b"hello").hexdigest()
        assert api.get_object(digest).data == b"hello"
    finally:
        host.shutdown()
        thread.join(timeout=10)
        daemon.stop()
