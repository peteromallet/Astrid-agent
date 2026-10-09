"""Tests for ``python -m astrid dev {status,promote}`` with real git worktrees and fakes.

The runtime, the pack host, and process signalling are faked. Digests use a
small tree hash so the tests do not need the full Astrid source closure.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path
from typing import Any

import pytest

from astrid.core.gateway.dev_operator import (
    DevOperatorError,
    in_flight_tasks,
    pair_report,
    run_promote,
    run_status,
)

GIT_ENV = {
    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid",
    "PATH": os.environ.get("PATH", ""), "HOME": os.environ.get("HOME", ""),
}
DEAD_PID = 2_999_991  # no such process on any reasonable machine


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True,
                          check=True, env=GIT_ENV)
    return proc.stdout.strip()


def _tree_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted((root / "astrid" / "packs").rglob("*")):
        if path.is_file():
            digest.update(str(path.relative_to(root)).encode())
            digest.update(path.read_bytes())
    return "sha256:" + digest.hexdigest()


def _closure(root: Path) -> str:
    return "closure:" + _tree_digest(root)


def _commit(repo: Path, message: str, *, name: str = "pack.yaml", body: str = "schema_version: 2\n") -> str:
    target = repo / "astrid" / "packs" / "demo" / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "-c", "commit.gpgsign=false", "commit", "-q", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


@pytest.fixture()
def trees(tmp_path: Path) -> dict[str, Path]:
    """A dev repository with a linked serve worktree, plus a runtime data root."""
    dev = tmp_path / "Astrid"
    dev.mkdir()
    _git(dev, "init", "-q", "-b", "video/almost-ready")
    first = _commit(dev, "first")
    serve = tmp_path / "Astrid-serve"
    _git(dev, "worktree", "add", "-q", "--detach", str(serve), first)
    data = tmp_path / "data"
    (data / "runtime" / "source-profiles").mkdir(parents=True)
    (data / "runtime" / "source-profiles" / "astrid.json").write_text(
        json.dumps({"profile": "astrid", "source_checkout": str(serve),
                    "runtime_checkout": str(tmp_path / "Runtime")}), encoding="utf-8")
    return {"dev": dev, "serve": serve, "data": data, "runtime": data / "runtime"}


def _write_host(runtime: Path, *, pid: int, digest: str | None, ready: bool = True) -> None:
    runtime.mkdir(parents=True, exist_ok=True)
    (runtime / "generic-host.json").write_text(json.dumps({
        "pid": pid, "ready_file": str(runtime / "generic-host.ready.json"),
        "source_checkout": "unused", "source_checkout_digest": digest,
        "source_closure_digest": "closure"}), encoding="utf-8")
    (runtime / "generic-host.ready.json").write_text(json.dumps({
        "pid": pid, "status": "ready" if ready else "starting",
        "source_checkout_digest": digest}), encoding="utf-8")


@pytest.fixture()
def live(monkeypatch: pytest.MonkeyPatch) -> set[int]:
    """Pids the fake process table reports alive; identity never verifies by default."""
    from astrid.sdk import host_bootstrap

    alive: set[int] = set()
    monkeypatch.setattr(host_bootstrap, "_host_pid_alive", lambda pid: int(pid) in alive)
    monkeypatch.setattr(host_bootstrap, "_host_identity_matches", lambda state: False)
    return alive


class FakeRuntime:
    """Context-manager client with the projects/tasks listings promote reads.

    Tasks come in pages of ``page_size``; a non-final page carries its cursor as a
    string item in ``data``, the way the Runtime's listings do.
    """

    def __init__(self, task_rows: list[dict[str, Any]], page_size: int = 50) -> None:
        self._rows = task_rows
        self._page_size = page_size
        self.projects = self
        self.tasks = self

    def list(self, *args: Any, cursor: str | None = None, **kwargs: Any) -> dict[str, Any]:
        if args and args[0] == "almost-ready":
            start = int(cursor or 0)
            page = self._rows[start:start + self._page_size]
            data: list[Any] = [page]
            if start + self._page_size < len(self._rows):
                data.append(str(start + self._page_size))
            return {"ok": True, "data": data}
        return {"ok": True, "data": [[{"slug": "almost-ready"}], None]}

    def __enter__(self) -> "FakeRuntime":
        return self

    def __exit__(self, *exc: object) -> bool:
        return False


def _opener(trees: dict[str, Path], live: set[int], *, task_rows: list[dict[str, Any]] | None = None,
            reopen_pid: int = 2_999_992, reopen_error: Exception | None = None,
            calls: list[bool] | None = None):
    """A fake ``AstridClient.open_from_launcher``; the reopen starts a live host on the served tree."""
    rows = task_rows or []

    def open_client(*, start_pack_host: bool = True) -> FakeRuntime:
        if calls is not None:
            calls.append(start_pack_host)
        if start_pack_host:
            if reopen_error is not None:
                raise reopen_error
            live.add(reopen_pid)
            _write_host(trees["runtime"], pid=reopen_pid,
                        digest=_tree_digest(trees["serve"]))
        return FakeRuntime(rows)

    return open_client


def test_status_reports_paired_and_drift_honestly(trees: dict[str, Path], live: set[int]) -> None:
    served = _tree_digest(trees["serve"])
    _write_host(trees["runtime"], pid=DEAD_PID, digest=served)
    payload = run_status(data_root=trees["data"], client=trees["dev"],
                         digest_fn=_tree_digest, closure_fn=_closure)
    assert payload["served"]["head"] == payload["dev"]["head"]
    assert payload["pair"]["status"] == "paired"
    assert payload["host"]["alive"] is False
    assert payload["host"]["ready"] is False  # a stale ready file for a dead pid is not readiness
    # Uncommitted dev work is drift, and the reason says so.
    (trees["dev"] / "astrid" / "packs" / "demo" / "pack.yaml").write_text("changed\n", encoding="utf-8")
    drift = run_status(data_root=trees["data"], client=trees["dev"],
                       digest_fn=_tree_digest, closure_fn=_closure)
    assert drift["pair"]["status"] == "drift"
    assert "differs" in drift["pair"]["reason"]
    assert drift["dev"]["dirty"] is True


def test_pair_reports_host_attesting_a_stale_tree_as_drift(trees: dict[str, Path]) -> None:
    host = {"recorded": True, "source_checkout_digest": "sha256:stale"}
    report = pair_report(served=trees["serve"], client=trees["serve"], host=host,
                         digest_fn=_tree_digest, closure_fn=_closure)
    assert report["status"] == "drift"
    assert report["host_matches_served"] is False


def test_status_without_profile_is_unavailable(tmp_path: Path) -> None:
    with pytest.raises(DevOperatorError) as raised:
        run_status(data_root=tmp_path / "missing", client=tmp_path)
    assert raised.value.state == "unavailable"
    assert "source-manifest" in (raised.value.next_action or "")


def test_promote_checks_out_ref_and_reopens_host(trees: dict[str, Path], live: set[int]) -> None:
    second = _commit(trees["dev"], "second", body="schema_version: 2\n# v2\n")
    _write_host(trees["runtime"], pid=DEAD_PID, digest=_tree_digest(trees["serve"]))
    calls: list[bool] = []
    payload = run_promote(data_root=trees["data"], client=trees["dev"],
                          open_client=_opener(trees, live, calls=calls),
                          terminate_host=lambda state: pytest.fail("no live host to stop"),
                          digest_fn=_tree_digest, closure_fn=_closure, ready_wait_seconds=0.2)
    assert payload["ok"] is True and payload["state"] == "promoted"
    assert payload["new_sha"] == second
    assert _git(trees["serve"], "rev-parse", "HEAD") == second
    assert payload["host_pid_after"] == 2_999_992
    assert payload["pair"]["status"] == "paired"
    assert calls == [False, True]  # idle probe without starting a host, then the normal reopen


def test_promote_refuses_dirty_serve_worktree(trees: dict[str, Path], live: set[int]) -> None:
    (trees["serve"] / "astrid" / "packs" / "demo" / "pack.yaml").write_text("edited in serve\n",
                                                                           encoding="utf-8")
    with pytest.raises(DevOperatorError, match="local changes"):
        run_promote(data_root=trees["data"], client=trees["dev"], open_client=_opener(trees, live),
                    digest_fn=_tree_digest, closure_fn=_closure, ready_wait_seconds=0.1)


def test_promote_refuses_when_served_tree_is_the_dev_tree(trees: dict[str, Path], live: set[int]) -> None:
    (trees["data"] / "runtime" / "source-profiles" / "astrid.json").write_text(
        json.dumps({"profile": "astrid", "source_checkout": str(trees["dev"])}), encoding="utf-8")
    with pytest.raises(DevOperatorError, match="serves the dev tree itself"):
        run_promote(data_root=trees["data"], client=trees["dev"], open_client=_opener(trees, live),
                    digest_fn=_tree_digest, closure_fn=_closure, ready_wait_seconds=0.1)


def test_promote_refuses_a_served_checkout_from_another_repository(trees: dict[str, Path], tmp_path: Path,
                                                                   live: set[int]) -> None:
    other = tmp_path / "Other"
    other.mkdir()
    _git(other, "init", "-q")
    _commit(other, "other")
    (trees["data"] / "runtime" / "source-profiles" / "astrid.json").write_text(
        json.dumps({"profile": "astrid", "source_checkout": str(other)}), encoding="utf-8")
    with pytest.raises(DevOperatorError, match="different repository"):
        run_promote(data_root=trees["data"], client=trees["dev"], open_client=_opener(trees, live),
                    digest_fn=_tree_digest, closure_fn=_closure, ready_wait_seconds=0.1)


def test_promote_refuses_inflight_tasks_unless_forced(trees: dict[str, Path], live: set[int]) -> None:
    second = _commit(trees["dev"], "second", body="schema_version: 2\n# v2\n")
    running = [{"task_id": "task-1", "state": "running"}, {"task_id": "task-2", "state": "succeeded"}]
    before = _git(trees["serve"], "rev-parse", "HEAD")
    with pytest.raises(DevOperatorError, match="in-flight work") as refused:
        run_promote(data_root=trees["data"], client=trees["dev"],
                    open_client=_opener(trees, live, task_rows=running),
                    digest_fn=_tree_digest, closure_fn=_closure, ready_wait_seconds=0.1)
    assert refused.value.state == "busy"
    assert "task task-1" in str(refused.value) and "task-2" not in str(refused.value)
    assert _git(trees["serve"], "rev-parse", "HEAD") == before  # refused before any checkout

    payload = run_promote(
        data_root=trees["data"], client=trees["dev"], open_client=_opener(trees, live, task_rows=running),
        digest_fn=_tree_digest, closure_fn=_closure, ready_wait_seconds=0.2, force=True)
    assert payload["new_sha"] == second
    assert payload["abandoned"] == ["task task-1"]


def test_promote_stops_only_the_recorded_host_pid(trees: dict[str, Path], live: set[int],
                                                  monkeypatch: pytest.MonkeyPatch) -> None:
    from astrid.sdk import host_bootstrap

    recorded_pid = 2_999_993
    _write_host(trees["runtime"], pid=recorded_pid, digest=_tree_digest(trees["serve"]))
    live.add(recorded_pid)
    monkeypatch.setattr(host_bootstrap, "_host_identity_matches", lambda state: True)
    monkeypatch.setattr(host_bootstrap, "_descendant_snapshot", lambda pid: {})
    monkeypatch.setattr(os, "kill", lambda *a, **k: pytest.fail("promote must not signal by hand"))
    stopped: list[dict[str, Any]] = []

    def terminate(state: dict[str, Any]) -> None:
        stopped.append(dict(state))
        live.discard(recorded_pid)

    payload = run_promote(data_root=trees["data"], client=trees["dev"],
                          open_client=_opener(trees, live, reopen_pid=2_999_994),
                          terminate_host=terminate, digest_fn=_tree_digest, closure_fn=_closure,
                          ready_wait_seconds=0.2)
    assert [item["pid"] for item in stopped] == [recorded_pid]
    assert payload["host_pid_before"] == recorded_pid and payload["host_pid_after"] == 2_999_994


def test_promote_refuses_an_unverifiable_live_host(trees: dict[str, Path], live: set[int]) -> None:
    _write_host(trees["runtime"], pid=2_999_995, digest=_tree_digest(trees["serve"]))
    live.add(2_999_995)  # alive, but _host_identity_matches stays False
    with pytest.raises(DevOperatorError, match="cannot be verified"):
        run_promote(data_root=trees["data"], client=trees["dev"], open_client=_opener(trees, live),
                    digest_fn=_tree_digest, closure_fn=_closure, ready_wait_seconds=0.1)


def test_promote_reports_reopen_failure_with_the_last_host_error(trees: dict[str, Path], live: set[int]) -> None:
    (trees["runtime"] / "generic-host.log").write_text("boot ok\nTraceback...\nRuntimeError: no matrix row\n",
                                                       encoding="utf-8")
    with pytest.raises(DevOperatorError, match="reopen through the normal bootstrap failed") as failed:
        run_promote(data_root=trees["data"], client=trees["dev"],
                    open_client=_opener(trees, live, reopen_error=RuntimeError("bootstrap refused")),
                    digest_fn=_tree_digest, closure_fn=_closure, ready_wait_seconds=0.1)
    assert "RuntimeError: no matrix row" in (failed.value.next_action or "")


def test_in_flight_tasks_walks_nested_rows_and_ignores_terminal_states() -> None:
    client = FakeRuntime([
        {"task_id": "a", "state": "queued"},
        {"task_id": "b", "state": "failed"},
        {"task_id": "c", "state": "running"},
    ])
    assert in_flight_tasks(client) == ["a", "c"]


def test_in_flight_tasks_follows_every_page_to_a_late_running_task() -> None:
    done = [{"task_id": f"done-{i}", "state": "succeeded"} for i in range(120)]
    client = FakeRuntime(done + [{"task_id": "late", "state": "running"}])
    assert in_flight_tasks(client) == ["late"]


def test_dispatch_dev_status_without_profile_exits_nonzero(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                           capsys: pytest.CaptureFixture[str]) -> None:
    from astrid.core.gateway import dispatch

    monkeypatch.setenv("BANODOCO_LOCAL_DATA_ROOT", str(tmp_path / "empty"))
    assert dispatch._dispatch_dev(["status", "--json"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False and payload["state"] == "unavailable"
    assert "dev" in dispatch._TOP_LEVEL_HANDLERS


def test_dev_is_a_gateway_family_with_both_verbs(capsys: pytest.CaptureFixture[str]) -> None:
    from astrid.core.gateway.help import _print_entrypoint_help

    _print_entrypoint_help()
    assert "python3 -m astrid dev {status,promote}" in capsys.readouterr().out
