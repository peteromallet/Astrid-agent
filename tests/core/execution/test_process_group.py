from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
from types import SimpleNamespace

import pytest

from astrid.core.execution import process_group as groups


def _process():
    return SimpleNamespace(pid=100, _astrid_process_birth="root", poll=lambda: None, wait=lambda **_: 0)


def _snapshot():
    return {
        100: groups._ProcessInfo(100, 1, 100, "root"),
        200: groups._ProcessInfo(200, 100, 200, "detached-child"),
        900: groups._ProcessInfo(900, 1, 900, "unrelated-census-process"),
    }


@pytest.mark.parametrize("failure", ["first-census", "leader-missing", "pid-reuse", "parent-missing"])
def test_observation_uncertainty_persists_without_adopting_unknown_children(monkeypatch, failure):
    process = _process()
    census = _snapshot()
    if failure in {"pid-reuse", "parent-missing"}:
        monkeypatch.setattr(groups, "_process_snapshot", lambda: census)
        groups.observe_tree(process)
    bad = dict(census)
    if failure == "first-census":
        bad = {}
    elif failure == "leader-missing":
        # The escaped writer is now reparented and cannot be adopted by name.
        bad = {200: groups._ProcessInfo(200, 1, 200, "detached-child"), 900: census[900]}
    elif failure == "pid-reuse":
        bad[200] = groups._ProcessInfo(200, 1, 200, "reused-child")
        bad[300] = groups._ProcessInfo(300, 200, 200, "unrelated-grandchild")
    else:
        del bad[200]
        bad[300] = groups._ProcessInfo(300, 200, 300, "unverified-escaped-child")
    monkeypatch.setattr(groups, "_process_snapshot", lambda: bad)
    monkeypatch.setattr(groups.os, "kill", lambda *_: pytest.fail("uncertain tree was signalled"))
    with pytest.raises(groups.CleanupUncertainError):
        groups.observe_tree(process)
    assert 300 not in getattr(process, "_astrid_tree_members", {})
    if failure == "first-census":
        assert not hasattr(process, "_astrid_tree_members")
    monkeypatch.setattr(groups, "_process_snapshot", lambda: census)
    with pytest.raises(groups.CleanupUncertainError):
        groups.verify_tree_absent(process)
    with pytest.raises(groups.CleanupUncertainError):
        groups.terminate_tree(process, grace_seconds=0)


def test_keyboard_interrupt_during_first_census_fails_closed(monkeypatch):
    process = _process()
    signals = []

    def interrupted_census():
        raise KeyboardInterrupt()

    monkeypatch.setattr(groups, "_process_snapshot", interrupted_census)
    monkeypatch.setattr(groups.os, "kill", lambda *args: signals.append(args))
    with pytest.raises(KeyboardInterrupt):
        groups.observe_tree(process)
    assert process._astrid_tree_uncertain == "KeyboardInterrupt"

    monkeypatch.setattr(groups, "_process_snapshot", _snapshot)
    with pytest.raises(groups.CleanupUncertainError):
        groups.terminate_tree(process, grace_seconds=0)
    assert signals == []


@pytest.mark.parametrize("failure", ["signal-census", "signal-permission", "survivor"])
def test_termination_uncertainty_is_persistent(monkeypatch, failure):
    process = _process()
    census = _snapshot()
    calls = []

    def snapshot():
        return {} if failure == "signal-census" and calls else census

    def kill(pid, sig):
        calls.append((pid, sig))
        if failure == "signal-permission":
            raise PermissionError("injected test signal denial")

    if failure == "signal-census":
        snapshots = iter((census, {}))
        monkeypatch.setattr(groups, "_process_snapshot", lambda: next(snapshots))
    else:
        monkeypatch.setattr(groups, "_process_snapshot", snapshot)
    monkeypatch.setattr(groups.os, "kill", kill)
    ticks = iter(index * 0.2 for index in range(100))
    monkeypatch.setattr(groups.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(groups.time, "sleep", lambda _: None)
    if failure == "survivor":
        def wait(**_):
            raise subprocess.TimeoutExpired("owned test leader", 0)
        process.wait = wait
    with pytest.raises(groups.CleanupUncertainError):
        groups.terminate_tree(process, grace_seconds=0)
    assert hasattr(process, "_astrid_tree_uncertain")
    monkeypatch.setattr(groups, "_process_snapshot", lambda: {900: census[900]})
    with pytest.raises(groups.CleanupUncertainError):
        groups.verify_tree_absent(process)
    assert all(pid != 900 for pid, _ in calls)


def test_verified_detached_tree_stops_leaves_before_ancestors_and_leader(monkeypatch):
    process = _process()
    census = _snapshot()
    census[300] = groups._ProcessInfo(300, 200, 200, "node")
    census[400] = groups._ProcessInfo(400, 300, 400, "browser")
    census[500] = groups._ProcessInfo(500, 400, 400, "worker")
    monkeypatch.setattr(groups, "_process_snapshot", lambda: dict(census))
    calls = []

    def kill(pid, sig):
        calls.append((pid, sig))
        del census[pid]

    monkeypatch.setattr(groups.os, "kill", kill)
    groups.terminate_tree(process, grace_seconds=0)
    groups.verify_tree_absent(process)
    assert calls == [(pid, signal.SIGTERM) for pid in (500, 400, 300, 200, 100)]
    assert list(census) == [900]


def test_previously_observed_detached_child_remains_owned_after_leader_exit(monkeypatch):
    process = _process()
    census = _snapshot()
    monkeypatch.setattr(groups, "_process_snapshot", lambda: dict(census))
    groups.observe_tree(process)
    del census[100]
    census[200] = groups._ProcessInfo(200, 1, 200, "detached-child")
    process.poll = lambda: 0
    calls = []

    def kill(pid, sig):
        calls.append((pid, sig))
        del census[pid]

    monkeypatch.setattr(groups.os, "kill", kill)
    groups.terminate_tree(process, grace_seconds=0)
    groups.verify_tree_absent(process)
    assert calls == [(200, signal.SIGTERM)]
    assert list(census) == [900]


@pytest.mark.parametrize("failure", ["group-census", "group-survivor"])
def test_final_verification_uncertainty_cannot_be_cleared_by_a_later_census(monkeypatch, failure):
    process = _process()
    process._astrid_process_group_id = process.pid
    census = _snapshot()
    monkeypatch.setattr(groups, "_process_snapshot", lambda: census)
    groups.observe_tree(process)
    absent = {900: census[900]}
    bad = {} if failure == "group-census" else {
        **absent, 700: groups._ProcessInfo(700, 1, 100, "late-group-member"),
    }
    snapshots = iter((absent, bad))
    monkeypatch.setattr(groups, "_process_snapshot", lambda: next(snapshots))
    with pytest.raises(groups.CleanupUncertainError):
        groups.verify_tree_absent(process)
    monkeypatch.setattr(groups, "_process_snapshot", lambda: absent)
    with pytest.raises(groups.CleanupUncertainError):
        groups.verify_tree_absent(process)


def test_snapshot_retries_transient_ps_timeouts_and_reads_census(monkeypatch):
    real_run = subprocess.run
    timeouts = []

    def slow_then_ok(argv, **kwargs):
        timeouts.append(kwargs["timeout"])
        if len(timeouts) <= 2:
            raise subprocess.TimeoutExpired(argv, kwargs["timeout"])
        return real_run(argv, **kwargs)

    monkeypatch.setattr(subprocess, "run", slow_then_ok)
    monkeypatch.setattr(groups, "_CENSUS_RETRY_DELAY_SECONDS", 0)
    census = groups._process_snapshot()
    assert os.getpid() in census and census.failure is None
    assert timeouts == [5.0, 5.0, 5.0]


def test_transient_census_timeouts_do_not_latch_owned_tree_uncertainty(monkeypatch):
    process = groups.popen_owned_group([sys.executable, "-c", "import time; time.sleep(30)"])
    real_run = subprocess.run
    calls = []

    def slow_then_ok(argv, **kwargs):
        calls.append(argv)
        if len(calls) <= 2:
            raise subprocess.TimeoutExpired(argv, kwargs["timeout"])
        return real_run(argv, **kwargs)

    try:
        monkeypatch.setattr(subprocess, "run", slow_then_ok)
        monkeypatch.setattr(groups, "_CENSUS_RETRY_DELAY_SECONDS", 0)
        assert process.pid in groups.observe_tree(process)
        assert not hasattr(process, "_astrid_tree_uncertain")
    finally:
        monkeypatch.undo()
        groups.terminate_tree(process, grace_seconds=0.5)


def test_permanent_census_failure_names_its_reason(monkeypatch):
    calls = []

    def always_timeout(argv, **kwargs):
        calls.append(argv)
        raise subprocess.TimeoutExpired(argv, kwargs["timeout"])

    monkeypatch.setattr(subprocess, "run", always_timeout)
    monkeypatch.setattr(groups, "_CENSUS_RETRY_DELAY_SECONDS", 0)
    census = groups._process_snapshot()
    assert not census and census.failure == "ps timed out after 5s x3"
    assert len(calls) == 3
    process = SimpleNamespace(pid=100)
    with pytest.raises(groups.CleanupUncertainError) as raised:
        groups.observe_tree(process)
    assert str(raised.value) == "owned process census unavailable: ps timed out after 5s x3"
    assert process._astrid_tree_uncertain == str(raised.value)
    with pytest.raises(groups.CleanupUncertainError, match="ps timed out after 5s x3"):
        groups.observe_tree(process)


def test_nonzero_ps_exit_is_retried_and_reports_stderr(monkeypatch):
    calls = []

    def failing(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 1, stdout="", stderr="ps: sysctl failed\n")

    monkeypatch.setattr(subprocess, "run", failing)
    monkeypatch.setattr(groups, "_CENSUS_RETRY_DELAY_SECONDS", 0)
    census = groups._process_snapshot()
    assert not census and census.failure == "ps rc=1: ps: sysctl failed x3"
    assert len(calls) == 3


def test_missing_ps_is_reported_without_retry(monkeypatch):
    calls = []

    def missing(argv, **kwargs):
        calls.append(argv)
        raise FileNotFoundError("no ps")

    monkeypatch.setattr(subprocess, "run", missing)
    census = groups._process_snapshot()
    assert not census and census.failure.startswith("ps could not run")
    assert len(calls) == 1


def test_cleanup_latch_record_is_visible_only_while_its_host_lives(tmp_path, monkeypatch):
    latch = tmp_path / groups.CLEANUP_LATCH_NAME
    groups.write_cleanup_latch(
        latch,
        reason="ps timed out after 5s x3",
        since="2026-10-09T12:00:00+00:00",
        recover_with="kill 1",
    )
    record = groups.read_cleanup_latch(latch)
    assert record["pid"] == os.getpid() and record["liveness"] == "verified"
    assert groups.describe_cleanup_latch(record) == (
        "pack host blocked by cleanup uncertainty since 2026-10-09T12:00:00+00:00; "
        "cause ps timed out after 5s x3; recover with kill 1"
    )
    latch.write_text(json.dumps(dict(record, pid=2147483646)))
    assert groups.read_cleanup_latch(latch) is None
    latch.write_text(json.dumps(dict(record, process_birth_id="reused-pid")))
    assert groups.read_cleanup_latch(latch) is None
    latch.write_text("not json")
    assert groups.read_cleanup_latch(latch) is None
    monkeypatch.setattr(groups, "_process_snapshot", lambda: groups._failed_census("ps timed out after 5s x3"))
    latch.write_text(json.dumps(record))
    assert groups.read_cleanup_latch(latch)["liveness"] == "unverified: ps timed out after 5s x3"

