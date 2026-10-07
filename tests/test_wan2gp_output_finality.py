"""W2.3 CPU proofs through the retained child and existing attempt handoff."""
from __future__ import annotations

import copy
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from astrid.core.execution import generic_host as gh
from astrid.core.execution.managed_tool_session import (
    SessionCapacityError, StaleAdmissionError, UncertainCancellation,
)
from astrid.packs.wan2gp.src import driver
from tests.test_wan2gp_adapter_session import _FAKE_API, _fake_upstream


INPUTS = {"prompt": "native metadata stays intact", "model": "wan-2.2"}


@pytest.fixture
def owned(tmp_path):
    root, config, trace = _fake_upstream(tmp_path.resolve())
    host = gh.GenericPackHost(pack_roots=[], executor_id="w23-cpu")
    spec = dict(owner_dir=tmp_path.resolve() / "owner", root=root,
                python=sys.executable, source_digest="fixture-source",
                config_digest="fixture-config", readiness_timeout=5,
                release_timeout=0.2, init_options={"config_path": str(config)})
    yield host, spec, trace
    host.shutdown()
    assert not host._active_processes


def admit(host, name):
    return host.managed_tool_session.admit(
        capability_id="wan2gp.generate_video", invocation_id=name)


def invoke(host, token, **settings):
    return host.invoke_wan_session(token, settings or INPUTS,
                                   timeout=5, cancelled=lambda: False)


def run(host, token, attempt):
    return host._run_wan_session(
        inputs=INPUTS, output_root=attempt / "outputs", attempt=attempt,
        managed_token=token, execution_deadline=time.monotonic() + 5,
        cancelled=lambda: False)


def wait_for(predicate):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("fixture observation deadline expired")


def test_distinct_attempt_bytes_terminal_before_copy_and_settlement(owned, tmp_path, monkeypatch):
    host, spec, trace = owned
    binding = host.prepare_wan_session(**spec)
    materialize = driver.materialize_host_result
    observed = []

    def guarded(evidence, **kwargs):
        assert evidence["terminal"] is True
        assert evidence["output_snapshots"] == driver.snapshot_native_outputs(
            evidence["result"]["generated_files"], evidence["source_root"])
        assert not Path(kwargs["attempt_root"]).exists()
        with pytest.raises(SessionCapacityError):
            admit(host, "premature-successor")
        observed.append(copy.deepcopy(evidence))
        return materialize(evidence, **kwargs)

    monkeypatch.setattr(driver, "materialize_host_result", guarded)
    results = []
    for number, name in enumerate(("A", "B"), 1):
        assert host.prepare_wan_session(**spec) == binding
        token = admit(host, name)
        attempt = tmp_path.resolve() / name
        result = run(host, token, attempt)
        mapped = result.payload["wan_mapping"]
        evidence = result.payload["wan_native"]
        driver.verify_host_result(mapped, attempt_root=result.output_root)
        path = result.output_root / mapped["generated_files"][0]
        assert path.read_bytes() == b"native-wan-bytes-" + str(number).encode()
        assert path.is_relative_to(attempt)
        assert evidence == observed[-1]  # native list/metadata are preserved
        assert driver.snapshot_native_outputs(evidence["result"]["generated_files"],
                                               evidence["source_root"]) == evidence["output_snapshots"]
        with pytest.raises(SessionCapacityError):
            admit(host, "before-settlement")
        host.managed_tool_session.settle(token, result_evidence=evidence)
        results.append((token, mapped, path.read_bytes()))
    assert results[0][0].token_id != results[1][0].token_id
    assert results[0][1]["output_root"] != results[1][1]["output_root"]
    assert results[0][2] != results[1][2]
    rows = json.loads(trace.read_text())
    assert [row["event"] for row in rows].count("init") == 1


@pytest.mark.parametrize("field,value", [("terminal", False), ("output_snapshots", None)])
def test_missing_terminal_observation_blocks_materialization_and_successor(
    owned, tmp_path, monkeypatch, field, value
):
    host, spec, _ = owned
    host.prepare_wan_session(**spec)
    token = admit(host, "A")
    native = host.invoke_wan_session

    def incomplete(*args, **kwargs):
        evidence = native(*args, **kwargs)
        evidence[field] = value
        return evidence

    monkeypatch.setattr(host, "invoke_wan_session", incomplete)
    with pytest.raises(RuntimeError, match="terminal"):
        run(host, token, tmp_path.resolve() / "A")
    assert not (tmp_path / "A" / "outputs").exists()
    with pytest.raises(SessionCapacityError):
        admit(host, "B")


@pytest.mark.parametrize("kind", ["event", "result"])
@pytest.mark.parametrize("field", ["birth", "token_id", "invocation_id", "session_id",
                                  "generation", "binding_identity", "native_job_id"])
def test_stale_or_foreign_A_frames_cannot_publish_B(owned, tmp_path, kind, field):
    host, spec, _ = owned
    host.prepare_wan_session(**spec)
    a = admit(host, "A")
    result = run(host, a, tmp_path.resolve() / "A")
    evidence = result.payload["wan_native"]
    host.managed_tool_session.settle(a, result_evidence=evidence)
    b = admit(host, "B")
    adapter = host.managed_tool_session.current_adapter
    with pytest.raises(StaleAdmissionError):
        host.managed_tool_session.settle(b, result_evidence=evidence)
    with pytest.raises(RuntimeError, match="another admission"):
        driver.materialize_host_result(evidence, attempt_root=tmp_path / "B" / "outputs",
            inputs=INPUTS, expected_identity=adapter.identity(b))
    identity = adapter.identity(b)
    frame = {"birth": adapter.binding.process_birth_id, "kind": kind,
             "identity": identity, "native_job_id": "native-job-2",
             "result": evidence["result"], "output_snapshots": evidence["output_snapshots"]}
    if field == "birth":
        frame[field] = "old-birth"
    elif field == "native_job_id":
        start = {**frame, "kind": "job_started"}
        adapter._buffer.extend(json.dumps(start).encode() + b"\n")
        frame[field] = evidence[field]
    else:
        identity[field] = adapter.identity(a)[field] if field in {"token_id", "invocation_id"} else "foreign"
    adapter._buffer.extend(json.dumps(frame).encode() + b"\n")
    with pytest.raises(gh.HostError):
        run(host, b, tmp_path.resolve() / "B")
    assert not (tmp_path / "B" / "outputs").exists()
    assert host.managed_tool_session.occupied and not host.managed_tool_session.active
    with pytest.raises(StaleAdmissionError):
        admit(host, "C")


def native_evidence(tmp_path):
    spool = tmp_path.resolve() / "native"
    spool.mkdir()
    media = spool / "movie.mp4"
    media.write_bytes(b'\x00native-video\x00{"creation_date":"keep","generation_time":42}')
    evidence = {"terminal": True, "native_job_id": "native-A", "source_root": str(spool),
                "result": {"success": True, "generated_files": [str(media)],
                           "artifacts": {"metadata": "unchanged"}},
                "output_snapshots": driver.snapshot_native_outputs([str(media)], spool)}
    return evidence, media


@pytest.mark.parametrize("case", ["escape", "traversal", "symlink", "parent_symlink",
                                 "hardlink", "missing", "duplicate", "same_basename",
                                 "reserved", "mutation", "replacement", "destination_symlink",
                                 "destination_parent_symlink", "shared_destination"])
def test_output_custody_failures_write_nothing_outside_attempt(tmp_path, case):
    evidence, media = native_evidence(tmp_path)
    spool = media.parent
    outside = tmp_path.resolve() / "outside"
    outside.mkdir()
    sentinel = outside / "sentinel.mp4"
    sentinel.write_bytes(b"outside-must-stay")
    destination = tmp_path.resolve() / "A" / "outputs"
    files = evidence["result"]["generated_files"]
    if case in {"escape", "traversal"}:
        files[0] = str(sentinel if case == "escape" else spool / ".." / "outside" / sentinel.name)
    elif case == "symlink":
        media.unlink()
        media.symlink_to(sentinel)
    elif case == "parent_symlink":
        (spool / "linked").symlink_to(outside, target_is_directory=True)
        files[0] = str(spool / "linked" / sentinel.name)
    elif case == "hardlink":
        os.link(media, spool / "alias.mp4")
    elif case == "missing":
        media.unlink()
    elif case == "duplicate":
        files.append(str(media))
    elif case in {"same_basename", "reserved"}:
        other = spool / ("sub/movie.mp4" if case == "same_basename" else "manifest.json")
        other.parent.mkdir(exist_ok=True)
        other.write_bytes(b"other")
        files.append(str(other))
        evidence["output_snapshots"] = driver.snapshot_native_outputs(files, spool)
    elif case == "mutation":
        media.write_bytes(b"late-A-mutation")
    elif case == "replacement":
        data = media.read_bytes()
        media.unlink()
        media.write_bytes(data)
    elif case == "destination_symlink":
        destination.parent.mkdir()
        destination.symlink_to(outside, target_is_directory=True)
    elif case == "destination_parent_symlink":
        destination.parent.symlink_to(outside, target_is_directory=True)
    else:
        destination.mkdir(parents=True)
        (destination / "B-owned").write_bytes(b"B")
    with pytest.raises((RuntimeError, OSError)):
        driver.materialize_host_result(evidence, attempt_root=destination, inputs=INPUTS)
    assert sentinel.read_bytes() == b"outside-must-stay"
    assert list(outside.iterdir()) == [sentinel]
    assert not (destination / "manifest.json").exists()


def test_native_bytes_and_metadata_survive_attempt_handoff(tmp_path):
    evidence, media = native_evidence(tmp_path)
    before = copy.deepcopy(evidence)
    payload = media.read_bytes()
    mapped = driver.materialize_host_result(evidence, attempt_root=tmp_path / "A", inputs=INPUTS)
    assert (tmp_path / "A" / mapped["generated_files"][0]).read_bytes() == payload
    assert evidence == before
    assert driver.snapshot_native_outputs([str(media)], media.parent) == before["output_snapshots"]
    driver.verify_host_result(mapped, attempt_root=tmp_path / "A")


def test_mutation_during_copy_and_after_staging_fail_closed(tmp_path, monkeypatch):
    evidence, media = native_evidence(tmp_path)
    fsync = driver.os.fsync

    def mutate(fd):
        fsync(fd)
        media.write_bytes(b"changed-during-copy")

    with monkeypatch.context() as patch:
        patch.setattr(driver.os, "fsync", mutate)
        with pytest.raises(RuntimeError, match="changed during copy"):
            driver.materialize_host_result(evidence, attempt_root=tmp_path / "failed", inputs=INPUTS)
    assert not (tmp_path / "failed" / "manifest.json").exists()
    evidence["output_snapshots"] = driver.snapshot_native_outputs([str(media)], media.parent)
    mapped = driver.materialize_host_result(evidence, attempt_root=tmp_path / "A", inputs=INPUTS)
    staged = tmp_path / "A" / mapped["generated_files"][0]
    driver.verify_host_result(mapped, attempt_root=tmp_path / "A")
    staged.write_bytes(b"late-staged-mutation")
    with pytest.raises(RuntimeError, match="changed after terminal custody"):
        driver.verify_host_result(mapped, attempt_root=tmp_path / "A")


@pytest.mark.parametrize("mode", ["failure", "timeout", "cancel"])
def test_failure_uncertainty_blocks_successor_until_observed_release(owned, tmp_path, monkeypatch, mode):
    host, spec, _ = owned
    host.prepare_wan_session(**spec)
    adapter = host.managed_tool_session.current_adapter
    child = adapter.process
    token = admit(host, "A")
    if mode == "failure":
        result = invoke(host, token, raise_error=True)
        assert result["result"]["success"] is False
        host.managed_tool_session.fence(reason="native_failure")
    elif mode == "timeout":
        with pytest.raises(TimeoutError):
            host.invoke_wan_session(token, INPUTS, timeout=0.05, cancelled=lambda: False)
    else:
        with pytest.raises(UncertainCancellation):
            host.managed_tool_session.cancel(token, outcome="uncertain")
    assert host.managed_tool_session.occupied and not host.managed_tool_session.active
    with pytest.raises(StaleAdmissionError):
        admit(host, "B")
    with monkeypatch.context() as patch:
        patch.setattr(adapter, "release", lambda **kwargs: {"ok": False, "released": False})
        with pytest.raises(SessionCapacityError, match="release was not verified"):
            host.prepare_wan_session(**{**spec, "config_digest": "B"})
        assert host.managed_tool_session.current_adapter is adapter
        assert child.poll() is None
    original_start = gh._ManagedWanChildAdapter.start

    def after_release(successor):
        assert child.poll() is not None
        assert child not in host._active_processes
        assert not any(info.pgid == child.pid for info in gh._process_snapshot().values())
        original_start(successor)

    monkeypatch.setattr(gh._ManagedWanChildAdapter, "start", after_release)
    host.prepare_wan_session(**{**spec, "config_digest": "B"})
    b = admit(host, "B")
    with pytest.raises(StaleAdmissionError):
        host.managed_tool_session.fence_admission(token, reason="late-A")
    result = run(host, b, tmp_path.resolve() / "B")
    host.managed_tool_session.settle(b, result_evidence=result.payload["wan_native"])


@pytest.mark.parametrize("failure", [False, True])
def test_child_terminal_emission_and_admission_are_atomic(owned, monkeypatch, failure):
    """Queue B while A's terminal write is blocked: B must not start early."""
    host, spec, trace = owned
    root = spec["root"]
    gate = root / "release-terminal"
    blocked = root / "terminal-blocked"
    # Fixture instrumentation runs inside the real child. It blocks at the
    # result frame serializer, after the terminal snapshot but before emission.
    instrument = r'''
    real_dumps = json.dumps
    def gated_dumps(value, *args, **kwargs):
        if isinstance(value, dict) and value.get("kind") in {"result", "error"} and value.get("native_job_id") == "native-job-1":
            Path(__file__).parent.parent.joinpath("terminal-blocked").touch()
            deadline = time.monotonic() + 5
            while not Path(__file__).parent.parent.joinpath("release-terminal").exists():
                if time.monotonic() >= deadline:
                    raise RuntimeError("terminal fixture gate expired")
                time.sleep(0.01)
        return real_dumps(value, *args, **kwargs)
    json.dumps = gated_dumps
'''
    api = _FAKE_API.replace('    return Session(trace_path, output_dir)',
                           instrument + '\n    return Session(trace_path, output_dir)')
    (root / "shared" / "api.py").write_text(api)
    reference = os.environ.get("W23_REFERENCE_WORKER")
    if reference:
        popen = gh.popen_owned_group
        def reference_child(command, **kwargs):
            return popen([command[0], reference, "--wan-session"], **kwargs)
        monkeypatch.setattr(gh, "popen_owned_group", reference_child)
    host.prepare_wan_session(**spec)
    adapter = host.managed_tool_session.current_adapter
    a = admit(host, "A")
    # Deliberately inject a raw B command to exercise the child lock even
    # though the parent MTS already prevents normal early admission.
    b_identity = {**adapter.identity(a), "token_id": "B", "invocation_id": "B"}
    with ThreadPoolExecutor(1) as pool:
        running = pool.submit(invoke, host, a, **({"raise_error": True} if failure else {}))
        wait_for(blocked.exists)
        adapter._write({"birth": adapter.binding.process_birth_id, "op": "run",
                        "identity": b_identity, "settings": INPUTS}, time.monotonic() + 5)
        try:
            # Child may have read B, but it cannot submit B before A's
            # blocked terminal frame and active-state release finish.
            time.sleep(0.15)
            assert len([r for r in json.loads(trace.read_text()) if r["event"] == "submit"]) == 1
        finally:
            gate.touch()
            evidence = running.result(timeout=5)
        assert evidence["native_job_id"] == "native-job-1"
        deadline = time.monotonic() + 5
        frames = []
        while not frames or frames[-1].get("kind") != "result":
            frames.append(adapter._read(deadline))
        assert frames[0]["kind"] == "job_started"
        assert all(f["identity"] == b_identity and f["native_job_id"] == "native-job-2" for f in frames)
        assert frames[-1]["output_snapshots"]
