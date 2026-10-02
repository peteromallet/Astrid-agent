from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path

import pytest

from astrid.core.execution.generic_host import GenericPackHost, HostError
from astrid.packs.wan2gp.src.driver import (
    WAN2GP_PIN_SHA,
    compile_host_settings,
    materialize_host_result,
    resolve_wan2gp_root,
)


_FAKE_API = r'''
import json
import threading
import time
from pathlib import Path


class Event(dict):
    def __init__(self, kind, data):
        super().__init__(kind=kind, data=data)
        self.kind = kind
        self.data = data


class Result:
    def __init__(self, success, generated_files=(), errors=()):
        self.success = success
        self.generated_files = list(generated_files)
        self.errors = list(errors)
        self.total_tasks = 1
        self.successful_tasks = 1 if success else 0
        self.failed_tasks = 0 if success else 1


class Events:
    def __init__(self, job):
        self.job = job

    def iter(self, timeout=0.1):
        yield Event("progress", {"phase": "native", "progress": 0.25, "current_step": 1, "total_steps": 4})
        while not self.job.done.wait(timeout):
            pass
        yield Event("progress", {"phase": "native", "progress": 1.0, "current_step": 4, "total_steps": 4})


class Job:
    def __init__(self, session, settings, number):
        self.session = session
        self.settings = dict(settings)
        self.number = number
        self.job_id = "native-job-" + str(number)
        self.done = threading.Event()
        self.cancel_requested = threading.Event()
        self.events = Events(self)

    def result(self):
        self.cancel_requested.wait(0.3)
        if self.cancel_requested.is_set():
            self.done.set()
            return Result(False, errors=["cooperatively cancelled"])
        if self.settings.get("raise_error"):
            self.done.set()
            raise RuntimeError("native result failure")
        time.sleep(0.03)
        output = self.session.output_dir / ("native-" + str(self.number) + ".mp4")
        output.write_bytes(b"native-wan-bytes-" + str(self.number).encode())
        self.done.set()
        return Result(True, generated_files=[str(output)])

    def cancel(self):
        self.cancel_requested.set()


class Session:
    def __init__(self, trace_path, output_dir):
        self.trace_path = Path(trace_path)
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.jobs = []

    def _trace(self, event, **payload):
        current = json.loads(self.trace_path.read_text()) if self.trace_path.exists() else []
        current.append({"event": event, **payload})
        self.trace_path.write_text(json.dumps(current, sort_keys=True))

    def submit_task(self, settings):
        job = Job(self, settings, len(self.jobs) + 1)
        self.jobs.append(job)
        self._trace("submit", settings=dict(settings), native_job_id=job.job_id)
        return job

    def close(self):
        self._trace("close", jobs=len(self.jobs))


def init(*, config_path, output_dir, **kwargs):
    config = json.loads(Path(config_path).read_text())
    trace_path = Path(config["trace_path"])
    current = json.loads(trace_path.read_text()) if trace_path.exists() else []
    current.append({"event": "init", "root": str(kwargs.get("root")), "output_dir": str(output_dir)})
    trace_path.write_text(json.dumps(current, sort_keys=True))
    return Session(trace_path, output_dir)
'''


def _fake_upstream(tmp_path: Path) -> tuple[Path, Path, Path]:
    root = tmp_path / "wan-upstream"
    (root / "shared").mkdir(parents=True)
    (root / "shared" / "__init__.py").write_text("")
    (root / "shared" / "api.py").write_text(_FAKE_API)
    trace = tmp_path / "native-trace.json"
    trace.write_text("[]")
    config = tmp_path / "wan-config.json"
    config.write_text(json.dumps({"trace_path": str(trace)}))
    return root, config, trace


def _admit(host: GenericPackHost, number: int):
    return host.managed_tool_session.admit(
        capability_id="wan2gp.generate_video",
        invocation_id=f"task-{number}:attempt-{number}:1",
    )


def _settle(host: GenericPackHost, token, evidence: dict) -> None:
    host.managed_tool_session.settle(
        token,
        result_evidence={"generation": token.generation, **evidence},
    )


def test_host_owned_wan_session_maps_native_lifecycle_and_reuses_once(tmp_path: Path) -> None:
    root, config, trace_path = _fake_upstream(tmp_path)
    owner = tmp_path / "owner"
    host = GenericPackHost(pack_roots=[], executor_id="wan-adapter-test")
    spec = {
        "owner_dir": owner,
        "root": root,
        "python": sys.executable,
        "source_digest": "sha256:source",
        "config_digest": "sha256:config",
        "readiness_timeout": 5.0,
        "release_timeout": 5.0,
        "init_options": {"config_path": str(config)},
    }
    settings = compile_host_settings({"prompt": "a kite", "model": "wan-2.2", "frames": 4})
    try:
        binding = host.prepare_wan_session(**spec)
        token = _admit(host, 1)
        evidence = host.invoke_wan_session(
            token,
            settings,
            timeout=5.0,
            cancelled=lambda: False,
            progress_path=tmp_path / "attempt" / ".astrid-progress.json",
        )
        assert evidence["native_job_id"] == "native-job-1"
        assert [event["event_kind"] for event in evidence["events"]] == ["progress", "progress"]
        assert json.loads((tmp_path / "attempt" / ".astrid-progress.json").read_text())["percent"] == 1.0
        _settle(host, token, evidence)

        same_binding = host.prepare_wan_session(**spec)
        assert same_binding.process_birth_id == binding.process_birth_id
        token2 = _admit(host, 2)
        evidence2 = host.invoke_wan_session(
            token2, settings, timeout=5.0, cancelled=lambda: False
        )
        assert evidence2["native_job_id"] == "native-job-2"
        _settle(host, token2, evidence2)

        mapped = materialize_host_result(
            evidence2,
            attempt_root=tmp_path / "published-attempt",
            inputs={"prompt": "a kite", "model": "wan-2.2"},
        )
        assert (tmp_path / "published-attempt" / mapped["generated_files"][0]).read_bytes() == b"native-wan-bytes-2"
        assert mapped["disclosed_engine"]["pin_sha"] == WAN2GP_PIN_SHA
        assert mapped["manifest"]["outputs"][0]["name"] == "generated_videos"

        token3 = _admit(host, 3)
        failure = host.invoke_wan_session(
            token3,
            {"raise_error": True},
            timeout=5.0,
            cancelled=lambda: False,
        )
        assert failure["result"]["success"] is False
        assert failure["native_error"] == "native result failure"
        host.managed_tool_session.fence(reason="test_native_failure")
    finally:
        host.shutdown()

    trace = json.loads(trace_path.read_text())
    assert [item["event"] for item in trace].count("init") == 1
    assert [item["event"] for item in trace].count("submit") == 3
    assert [item["event"] for item in trace].count("close") == 1
    assert trace[1]["settings"] == settings


def test_host_owned_wan_session_maps_cooperative_cancel_and_failure(tmp_path: Path) -> None:
    root, config, _trace_path = _fake_upstream(tmp_path)
    host = GenericPackHost(pack_roots=[], executor_id="wan-cancel-test")
    host.prepare_wan_session(
        owner_dir=tmp_path / "owner",
        root=root,
        python=sys.executable,
        source_digest="sha256:source",
        config_digest="sha256:config",
        readiness_timeout=5.0,
        release_timeout=5.0,
        init_options={"config_path": str(config)},
    )
    token = _admit(host, 1)
    cancelled = threading.Event()
    result: dict[str, object] = {}

    def invoke() -> None:
        result["evidence"] = host.invoke_wan_session(
            token,
            {"prompt": "cancel me"},
            timeout=5.0,
            cancelled=cancelled.is_set,
        )

    thread = threading.Thread(target=invoke)
    thread.start()
    time.sleep(0.15)
    cancelled.set()
    thread.join(timeout=5.0)
    try:
        assert not thread.is_alive()
        evidence = result["evidence"]
        assert evidence["native_job_id"] == "native-job-1"
        assert evidence["result"]["success"] is False
        assert evidence["result"]["errors"] == ["cooperatively cancelled"]
        host.managed_tool_session.cancel(token, outcome="confirmed")
    finally:
        host.shutdown()


def test_wan_session_fences_stale_event_identity_and_rejects_worker_relative_root(
    tmp_path: Path,
) -> None:
    root, config, _trace_path = _fake_upstream(tmp_path)
    assert resolve_wan2gp_root(None) is None
    host = GenericPackHost(pack_roots=[], executor_id="wan-fence-test")
    host.prepare_wan_session(
        owner_dir=tmp_path / "owner",
        root=root,
        python=sys.executable,
        source_digest="sha256:source",
        config_digest="sha256:config",
        readiness_timeout=5.0,
        release_timeout=5.0,
        init_options={"config_path": str(config)},
    )
    first = _admit(host, 1)
    try:
        evidence = host.invoke_wan_session(
            first, {"prompt": "first"}, timeout=5.0, cancelled=lambda: False
        )
        _settle(host, first, evidence)
        adapter = host.managed_tool_session.current_adapter
        adapter._buffer.extend(
            (json.dumps({
                "birth": adapter.binding.process_birth_id,
                "kind": "event",
                "identity": evidence,
                "native_job_id": evidence["native_job_id"],
                "event": {"kind": "progress", "data": {"progress": 0.5}},
            }) + "\n").encode()
        )
        second = _admit(host, 2)
        with pytest.raises(HostError, match="stale or unexpected"):
            host.invoke_wan_session(
                second, {"prompt": "second"}, timeout=5.0, cancelled=lambda: False
            )
        assert host.managed_tool_session.active is False
    finally:
        host.shutdown()


def test_host_context_requires_independent_timeout_and_source_inputs(tmp_path: Path) -> None:
    root, config, _trace_path = _fake_upstream(tmp_path)
    host = GenericPackHost(pack_roots=[])
    with pytest.raises(HostError, match="explicit wan_session"):
        host._wan_session_context({"capability": "wan2gp.generate_video"})
    context = host._wan_session_context(
        {
            "wan_session": {
                "owner_dir": str(tmp_path / "owner"),
                "root": str(root),
                "config_path": str(config),
                "python": sys.executable,
                "source_digest": "sha256:source",
                "config_digest": "sha256:config",
                "readiness_timeout": 5,
                "release_timeout": 5,
            }
        }
    )
    assert context["root"] == root.resolve()
    assert context["init_options"]["config_path"] == str(config.resolve())
