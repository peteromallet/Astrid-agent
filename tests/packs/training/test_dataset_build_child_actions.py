"""M17 acquisition driver and bounded offline caller fixtures.

Full production captioning, filtering, Human Review and training are not run.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import signal
import subprocess
import sys
import threading
import time
import traceback
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest

from astrid.packs.training.actions.dataset_build.budget import ChildWorkMeter, delegation_preflight
from astrid.packs.training.actions.dataset_build.services import DatasetRunServices
from astrid.packs.training.actions.dataset_build.source_providers import youtube
from astrid.packs.training.actions.dataset_build.filter_stages import transcript_keyword
from astrid.sdk.results import MaterializedChildOutput

PARENT, DOWNLOAD, SCENES, TRANSCRIBE = (
    "training.dataset_build",
    "youtube.youtube_audio",
    "editorial.scenes",
    "editorial.transcribe",
)


def digest(data):
    return "sha256:" + hashlib.sha256(data).hexdigest()


def config(**limits):
    return {
        "sources": [{"provider": "youtube", "config": {"urls": ["fixture-one"]}}],
        "caption": {"provider": "fixture"},
        "review": {"enabled": False, "top_up": {"max_rounds": 1}},
        "budgets": {"delegation": {"max_derived_bytes": 1024, **limits}},
    }


def row(data=b"media", *, association="association", **changes):
    return {
        "association_id": association,
        "run_id": "run",
        "task_id": "task",
        "attempt_id": "attempt",
        "object_id": digest(data),
        "digest": digest(data),
        "size": len(data),
        "filename": "media.mp4",
        "output_port": "media",
        "media_type": "video/mp4",
        "ordinal": 0,
        **changes,
    }


def meter(**limits):
    return ChildWorkMeter(
        {
            "max_children": 8,
            "max_active_children": 1,
            "max_derived_objects": 8,
            "max_derived_bytes": 1024,
            "max_child_inputs": 1,
            "max_child_bytes": 1024,
            **limits,
        }
    )


def producer(filename="child-outputs/association/media.mp4", **changes):
    return {"filename": filename, "output_port": "media", "media_type": "video/mp4", **changes}


@pytest.mark.parametrize(
    "limit,value",
    [
        ("max_derived_bytes", None),
        ("max_derived_bytes", 0),
        ("max_children", 3),
        ("max_derived_objects", 3),
        ("max_child_bytes", 256 * 1024**2 + 1),
    ],
)
def test_invalid_known_plan_overage_precedes_provider_work(monkeypatch, limit, value):
    cfg = config(**{limit: value})
    if value is None:
        cfg["budgets"]["delegation"].pop(limit)
    calls = []
    monkeypatch.setattr(youtube.sdk, "invoke", lambda *a, **kw: calls.append(kw))
    with pytest.raises(ValueError):
        DatasetRunServices.from_config(cfg)
    assert calls == []


def test_preflight_has_six_limits_and_counts_discardable_attempts():
    cfg = config()
    cfg["sources"][0]["config"]["urls"] *= 2
    cfg["sources"][0]["config"]["processed_source_ids"] = ["anything"]
    plan = delegation_preflight(cfg)
    assert plan["planned_children"] == 8
    assert plan["derived_accounting"]["planned_retained_outputs"] == 8
    assert set(plan["limits"]) == set(meter().limits)
    assert plan["limits"]["max_active_children"] == 1


@pytest.mark.parametrize("boundary", ["retain", "register"])
def test_exact_byte_object_boundary_zero_bytes_and_next_charge(boundary):
    m = meter(max_derived_bytes=5, max_derived_objects=2)

    def charge(r):
        if boundary == "retain":
            m.retain(r)
        else:
            m.register(r, producer(), name="video")

    charge(row())
    charge(row(b"", association="empty"))
    with pytest.raises(RuntimeError, match="budget exceeded"):
        charge(row(b"x", association="next"))
    counts = m.as_dict()
    prefix = "retained" if boundary == "retain" else "registered"
    assert counts[prefix + "_objects"] == 2 and counts[prefix + "_bytes"] == 5
    byte_meter = meter(max_derived_bytes=5)
    if boundary == "retain":
        byte_meter.retain(row())
        with pytest.raises(RuntimeError):
            byte_meter.retain(row(b"x", association="next"))
    else:
        byte_meter.register(row(), producer(), name="video")
        with pytest.raises(RuntimeError):
            byte_meter.register(row(b"x"), producer(), name="video")


def test_replay_same_digest_association_retention_and_registration_union():
    m = meter()
    first = row()
    second = row(association="second")
    m.retain(first)
    m.retain(first)
    m.retain(second)
    m.register(first, producer(), name="video")
    m.register(second, producer(), name="video")
    assert m.as_dict() == {
        "children": 0,
        "retained_objects": 2,
        "retained_bytes": 10,
        "registered_objects": 1,
        "registered_bytes": 5,
    }
    with pytest.raises(ValueError, match="conflicting producer"):
        m.register(second, producer("other/media.mp4"), name="video")
    with pytest.raises(ValueError, match="descriptor changed"):
        m.retain({**first, "attempt_id": "replacement"})


@pytest.mark.parametrize("size", [None, -1, True, 64 * 1024**2 + 1])
def test_unknown_over_object_size_before_either_boundary(size):
    m = meter()
    for boundary in (m.retain, m.check_registration):
        with pytest.raises(ValueError, match="size must be known"):
            boundary(row(size=size))
    assert m.retained_bytes == m.registered_bytes == 0


def test_uncertain_admission_and_materialization_are_not_refunded():
    m = meter(max_children=1, max_derived_objects=1, max_derived_bytes=5)
    m.admit("operation", DOWNLOAD, {"query": "one"})
    m.admit("operation", DOWNLOAD, {"query": "one"})
    with pytest.raises(ValueError, match="immutable operation"):
        m.admit("operation", DOWNLOAD, {"query": "changed"})
    with pytest.raises(RuntimeError):
        m.admit("next", DOWNLOAD, {"query": "two"})
    m.retain(row())
    with pytest.raises(RuntimeError):
        m.retain(row(b"", association="next"))
    assert m.as_dict()["children"] == 1 and m.retained_bytes == 5


def child_result(root, *, data=b"media", output=None, ok=True, state="completed", corrupt=False):
    descriptor = output or row(data)
    calls = []

    def materialize(association):
        calls.append(association)
        path = root / "child-outputs" / association / descriptor["filename"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"wrong" if corrupt else data)
        return MaterializedChildOutput(descriptor, path.relative_to(root).as_posix())

    return SimpleNamespace(
        ok=ok,
        error={"code": state} if not ok else None,
        raw_result={"state": state},
        kernel_run_id="run",
        kernel_task_id="task",
        kernel_attempt_id="attempt",
        outputs={"managed_outputs": [descriptor]},
        materialize_output=materialize,
        calls=calls,
    )


@pytest.mark.parametrize(
    "failure",
    ["failed", "cancelled", "unknown", "missing", "identity", "corrupt", "size", "child_bytes"],
)
def test_provider_custody_failure_retains_provenance_and_blocks_scenes(
    tmp_path, monkeypatch, failure
):
    root = tmp_path / "outputs"
    root.mkdir()
    output = row()
    if failure == "identity":
        output["attempt_id"] = "foreign"
    if failure == "size":
        output["size"] = None
    failed = failure in {"failed", "cancelled", "unknown"}
    result = child_result(
        root,
        output=output,
        corrupt=failure == "corrupt",
        ok=not failed,
        state=failure if failed else "completed",
    )
    if failure == "missing":
        result.outputs["managed_outputs"] = []
    calls = []
    monkeypatch.setattr(youtube.sdk, "invoke", lambda cap, **kw: calls.append(cap) or result)
    m = meter(max_child_bytes=4 if failure == "child_bytes" else 1024)
    provider = youtube.YouTubeSourceProvider(child_work_meter=m, materialization_root=root)
    cfg = {"out_dir": str(root / "sources"), "urls": ["one"]}
    with pytest.raises((ValueError, RuntimeError)):
        list(provider.acquire(cfg))
    assert calls == [DOWNLOAD] and cfg["acquisition_result"]["considered"] == 1
    assert list((root / "sources/downloads").glob("*.child.json"))
    if failure != "corrupt":
        assert result.calls == []
    else:
        assert m.retained_bytes == 5


def test_actual_overage_before_materialization(tmp_path):
    result = child_result(tmp_path)
    provider = youtube.YouTubeSourceProvider(
        child_work_meter=meter(max_derived_bytes=4), materialization_root=tmp_path
    )
    with pytest.raises(RuntimeError):
        provider._materialize(result, "media")
    assert result.calls == []


def test_uncertain_provider_call_is_charged_and_immutable(tmp_path, monkeypatch):
    m = meter()
    p = youtube.YouTubeSourceProvider(child_work_meter=m, materialization_root=tmp_path)

    def fail(*args, **kwargs):
        raise RuntimeError("transport lost")

    monkeypatch.setattr(youtube.sdk, "invoke", fail)
    receipt = tmp_path / "receipt.json"
    for _ in range(2):
        with pytest.raises(RuntimeError):
            p._invoke(DOWNLOAD, {"query": "one"}, "same", receipt)
    assert (
        m.as_dict()["children"] == 1 and json.loads(receipt.read_text())["admission"] == "uncertain"
    )


@pytest.fixture
def m17_world(tmp_path, monkeypatch):
    assert (
        os.environ.get("ASTRID_D18_RUNTIME_SOURCE")
        and os.environ.get("ASTRID_D18_USE_VENDORED_CLIENT") == "1"
    ), "explicit audited environment required"
    from tests.core.execution.test_generic_host_child_bridge_d18 import world

    yield from world.__wrapped__(tmp_path, monkeypatch)


def offline_receivers(host, monkeypatch):
    # Use pinned receiver definitions and their real main/manifest producers.
    # Only native media execution and network operations are replaced. This
    # proves caller custody/accounting, not provider egress or media detection.
    import shutil

    from astrid.packs.editorial.actions.scenes import run as scenes_run
    from astrid.packs.editorial.actions.transcribe import run as transcribe_run
    from astrid.packs.youtube.actions.youtube_audio import run as download_run

    original_which = shutil.which
    monkeypatch.setattr(
        shutil,
        "which",
        lambda binary: sys.executable if binary == "yt-dlp" else original_which(binary),
    )
    monkeypatch.setenv("OPENAI_API_KEY", "offline-fixture-openai-key")
    for capability_id in (DOWNLOAD, SCENES, TRANSCRIBE):
        record = host.preflight(capability_id)[0]
        assert record.ready, f"{record.id} preflight failed: {record.preflight}"

    def execute(record, inputs, output_root, attempt, **kwargs):
        assert record.id in {DOWNLOAD, SCENES, TRANSCRIBE}
        if record.id == DOWNLOAD:

            def download(command, **options):
                assert command[0] == "yt-dlp"
                (output_root / "media.mp4").write_bytes(inputs["query"].encode())
                return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

            # The original receiver makes its normal yt-dlp request against
            # deterministic local bytes and emits the normal result manifest.
            original_run = download_run.subprocess.run
            download_run.subprocess.run = download
            try:
                assert (
                    download_run.main(
                        [
                            "--query",
                            inputs["query"],
                            "--mode",
                            "video",
                            "--out",
                            str(output_root / "media"),
                        ]
                    )
                    == 0
                )
            finally:
                download_run.subprocess.run = original_run
        elif record.id == SCENES:
            assert Path(inputs["video"]).read_bytes().startswith(b"fixture-")
            assert (
                scenes_run.main(["--video", str(inputs["video"]), "--out", str(output_root)]) == 0
            )
        else:
            from types import SimpleNamespace as Namespace

            assert record.id == TRANSCRIBE
            assert Path(inputs["audio"]).read_bytes().startswith(b"selected clip ")
            monkeypatch.setitem(sys.modules, "openai", Namespace(OpenAI=lambda **kwargs: object()))
            monkeypatch.setattr(transcribe_run.AuditContext, "from_env", classmethod(lambda cls: None))

            def transcribe_stub(audio_path, out_dir, cache_dir, client, model, language, max_chunk_sec, vad_gate_enabled, diarize_mode, audit=None):
                cache_dir.mkdir(parents=True, exist_ok=True)
                metadata_path = cache_dir / "provider-stub-chunks.json"
                metadata_path.write_text("{}", encoding="utf-8")
                paths = transcribe_run.write_transcripts(
                    out_dir,
                    [{"start": 0.0, "end": 1.0, "text": "fixture speech", "speaker": None}],
                )
                return paths, {"chunks": 1, "skipped_silent": 0, "segments_kept": 1, "segments_filtered": 0}, metadata_path

            monkeypatch.setattr(transcribe_run, "transcribe_to_outputs", transcribe_stub)
            assert transcribe_run.main(["--audio", str(inputs["audio"]), "--out", str(output_root)]) == 0
        return SimpleNamespace(
            payload={"m17_offline_media_stub": True},
            returncode=0,
            process_id=os.getpid(),
            output_root=output_root,
        )

    monkeypatch.setattr(scenes_run, "detect_scenes", lambda *args: [])
    monkeypatch.setattr(host, "_run_command_definition", execute)
    # No listener or upstream operation is needed for an in-process media
    # stub; egress enforcement/evidence is outside this bounded offline proof.
    monkeypatch.setattr(host, "_start_network_broker", lambda *args, **kwargs: None)
    monkeypatch.setattr(host, "_network_evidence", lambda *args, **kwargs: None)


def public_client(world, monkeypatch):
    from astrid import sdk
    from astrid.sdk.client import PROTOCOL
    from banodoco_workspace_client import WorkspaceClient
    from tests.core.execution.test_generic_host_child_bridge_d18 import SECRET

    original_init = WorkspaceClient.__init__

    def transport(method, path, headers, body):
        parsed = urlsplit(path)
        if parsed.path == "/v1/capabilities":
            query = parse_qs(parsed.query)
            with world.lock:
                page = world.service.list_capabilities(
                    cursor=query.get("cursor", [None])[0], limit=int(query.get("limit", [50])[0])
                )
            return 200, {}, json.dumps(page).encode()
        if parsed.path == "/v1/handshake":
            payload = json.loads(body)
            with world.lock:
                data = world.service.handshake(
                    {
                        **payload,
                        "authenticated_actor": "worker",
                        "authenticated_scopes": payload["requested_scopes"],
                    }
                )
            return 200, {}, json.dumps(data).encode()
        status, response_headers, response = world.transport(method, path, headers, body)
        if parsed.path == "/v1/tasks" and method == "POST":
            # The central low-level fixture adds a task wrapper and a receipt
            # placeholder. Project the one real mutation exactly as Runtime's
            # HTTP handler does (server.py POST /v1/tasks), reading its genuine
            # persisted ledger receipt. No second admission or receipt minting.
            envelope = json.loads(response)
            assert set(envelope["data"]) == {"task"}
            task = envelope["data"]["task"]
            project_id = task.get("project_id") or "unscoped"
            with world.lock:
                receipt = world.service.committed_receipt(
                    "task.create", project_id, headers["Idempotency-Key"], project_id=project_id
                )
            assert receipt is not None
            assert receipt["project_id"] == project_id == world.project
            assert receipt["idempotency_key"] == headers["Idempotency-Key"]
            assert receipt["result"]["task"]["id"] == task["task_id"]
            assert receipt["result"]["run"]["id"] == task["run_id"]
            world.parent_receipt = receipt
            return status, response_headers, json.dumps({"data": task, "receipt": receipt}).encode()
        return status, response_headers, response

    def initialize(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        self._transport = transport

    monkeypatch.setattr(WorkspaceClient, "__init__", initialize)
    return sdk.AstridClient.open(
        endpoint="http://127.0.0.1:1",
        credential=SECRET,
        realm_id=world.service.realm["id"],
        actor_id="worker",
        client_name="m17-proof",
        client_version="1",
        protocol_version=PROTOCOL,
    )


DRIVER = """
import json, socket, sys
from pathlib import Path
from astrid.sdk._child_bridge import _install_child_bridge
_install_child_bridge(socket.socket(fileno=int(sys.argv[1])))
from astrid import sdk
from astrid.packs.training.actions.dataset_build.services import DatasetRunServices
from astrid.packs.training.actions.dataset_build.filter_stages.transcript_keyword import TranscriptKeywordFilter
from astrid.packs.training.actions.dataset_build.source_providers import youtube
roots = tuple(json.loads(sys.argv[2])); cfg = json.loads(sys.argv[3]); mode = sys.argv[4]
original_invoke = sdk.invoke
submitted = []
def invoke(cap, **kwargs):
    submitted.append([cap, kwargs['inputs']])
    return original_invoke(cap, extra_pack_roots=roots, **kwargs)
youtube.sdk.invoke = invoke
root = Path('outputs'); root.mkdir(exist_ok=True)
services = DatasetRunServices.from_config(cfg)
def clip(source, *, out_path, **kwargs): out_path.write_bytes(b'selected clip ' + source.read_bytes())
youtube.extract_clip_ffmpeg = clip
providers = []; acquired = []; error = None
for index in range(2 if mode == 'success' else 1):
    provider = youtube.YouTubeSourceProvider(child_work_meter=services.child_work_meter,
        prober=lambda path: {'duration_s': 1.0}, materialization_root=root)
    providers.append(provider)
    source = {'urls': ['fixture-' + str(index)], 'out_dir': str(root / 'source'),
              'acquisition_request': {'round_index': index}, 'max_duration_s': 2}
    try:
        items = list(provider.acquire(source))
        assert len(items) == 1
        acquired.extend(items)
    except Exception as exc:
        error = str(exc)
        break
if mode == 'success':
    assert error is None, error
    assert providers[0]._meter is providers[1]._meter is services.child_work_meter
    transcript = TranscriptKeywordFilter()._transcript(acquired[0], {
        'out_dir': str((root / 'transcripts').resolve()),
        'child_work_meter': services.child_work_meter,
        'attempt_output_root': root,
    })
    assert transcript['segments'][0]['text'] == 'fixture speech'
    try:
        rejected = original_invoke('editorial.scenes', kind='action', extra_pack_roots=roots,
            inputs={'video': submitted[-2][1]['video']}, child_key='over-budget',
            wait=True, timeout_seconds=2, poll_seconds=0.01)
    except Exception as exc: error = str(exc)
    else:
        assert rejected.ok is False and not rejected.kernel_task_id, 'persisted child limit did not enforce'
        error = json.dumps(rejected.error)
(root / 'proof.json').write_text(json.dumps({'meter': services.child_work_meter.as_dict(),
    'submitted': submitted, 'error': error, 'same_meter': len(providers) == 2 and providers[0]._meter is providers[1]._meter}))
"""


@pytest.mark.parametrize("grant", ["success", "missing", "insufficient"])
def test_public_sdk_parent_real_inherited_child_edges(m17_world, monkeypatch, grant):
    from runtime_protocol.service import CHILD_LIMITS

    from astrid import sdk
    from astrid.core.execution._child_bridge import HostChildBridge, task_resource
    from astrid.core.execution.generic_host import GenericPackHost

    world = m17_world
    candidate = Path(__file__).resolve().parents[3]
    roots = [candidate / "astrid/packs" / name for name in ("training", "youtube", "editorial")]
    host = GenericPackHost(
        pack_roots=roots,
        client=world.client,
        executor_id="worker",
        max_concurrency=2,
        attempt_root=world.tmp / "children",
    )
    host.discover()
    offline_receivers(host, monkeypatch)
    for cid in (PARENT, DOWNLOAD, SCENES, TRANSCRIBE):
        world.service.register_capability(
            {"capability_id": cid, "definition_digest": host.capabilities[cid].capability_digest}
        )
    world.service.register_executor(
        {"executor_id": "worker", "capabilities": [PARENT, DOWNLOAD, SCENES, TRANSCRIBE], "max_concurrency": 2},
        idempotency_key="m17-register",
    )
    cfg = config(max_children=5)
    plan = delegation_preflight(cfg)
    policy = {
        "capabilities": [
            {"capability_id": cid, "capability_digest": host.capabilities[cid].capability_digest}
            for cid in (DOWNLOAD, SCENES, TRANSCRIBE)
        ],
        "targets": [{"kind": "default"}],
        "input_object_ids": [],
        "limits": {**CHILD_LIMITS, **plan["limits"]},
    }
    if grant == "insufficient":
        policy["capabilities"] = policy["capabilities"][:1]
    client = public_client(world, monkeypatch)
    cfg_path = world.tmp / "config.json"
    cfg_path.write_text(json.dumps(cfg))
    result = sdk.invoke(
        PARENT,
        kind="action",
        inputs={"config": str(cfg_path), "out": str(world.tmp / "parent/outputs")},
        extra_pack_roots=tuple(map(str, roots)),
        project=world.project,
        child_delegation=None if grant == "missing" else policy,
        client=client,
        wait=False,
    )
    assert result.ok is True and result.kernel_task_id, json.dumps(result.to_dict())
    parents = [
        body for method, path, body in world.calls if path == "/v1/tasks" and method == "POST"
    ]
    assert len(parents) == 1
    persisted = world.service.task(result.kernel_task_id)["task"]
    if grant != "missing":
        assert parents[0]["child_delegation"] == persisted["spec"]["child_delegation"] == policy
        assert {
            name: persisted["spec"]["child_delegation"]["limits"][name] for name in plan["limits"]
        } == plan["limits"]
    claim = world.service.claim_next(
        {"executor_id": "worker", "capability_ids": [PARENT], "runtime_epoch": 1},
        idempotency_key="m17-parent-claim",
        identity=world.identity,
    )
    assert claim["task_id"] == result.kernel_task_id
    world.client._attempt_runtime_epochs[claim["attempt_id"]] = claim["runtime_epoch"]
    parent = task_resource(world.client.task(claim["task_id"]))
    root = world.tmp / "parent"
    outputs = root / "outputs"
    outputs.mkdir(parents=True)
    if grant == "missing":
        with pytest.raises(Exception, match="missing persisted finite child policy"):
            HostChildBridge(
                host,
                parent,
                attempt_id=claim["attempt_id"],
                lease_id=claim["lease_id"],
                fence=claim["fence"],
                runtime_epoch=1,
                output_root=outputs,
                cancelled=lambda: False,
            )
        assert not [p for _, path, p in world.calls if path == "/v1/delegated-tasks"]
        return
    bridge = HostChildBridge(
        host,
        parent,
        attempt_id=claim["attempt_id"],
        lease_id=claim["lease_id"],
        fence=claim["fence"],
        runtime_epoch=1,
        output_root=outputs,
        cancelled=lambda: False,
    )
    world.bridges.append(bridge)
    bridge_requests = []
    original_dispatch = bridge.dispatch

    def dispatch(request):
        if request["op"] == "submit":
            bridge_requests.append(copy.deepcopy(request))
        return original_dispatch(request)

    monkeypatch.setattr(bridge, "dispatch", dispatch)
    stop = threading.Event()
    failures = []

    def serve():
        try:
            while not stop.is_set():
                with world.lock:
                    claim_body = {
                        "executor_id": "worker",
                        "capability_ids": [DOWNLOAD, SCENES, TRANSCRIBE],
                        "runtime_epoch": 1,
                    }
                    queued = world.service.store.conn.execute(
                        "SELECT id FROM tasks WHERE capability IN (?, ?, ?) AND status='queued' ORDER BY created_at, id LIMIT 1",
                        (DOWNLOAD, SCENES, TRANSCRIBE),
                    ).fetchone()
                    if queued is not None:
                        target = world.service.store.effective_execution_target(queued["id"])
                        if target is not None:
                            claim_body["target"] = target
                    child = world.service.claim_next(
                        claim_body,
                        idempotency_key="m17-child-" + str(time.monotonic_ns()),
                        identity=world.identity,
                    )
                if child:
                    world.client._attempt_runtime_epochs[child["attempt_id"]] = child[
                        "runtime_epoch"
                    ]
                    task = world.service.task(child["task_id"])["task"]
                    task.update(
                        runtime_epoch=child["runtime_epoch"],
                        fence=child["fence"],
                        project_id=world.project,
                        spec=child["spec"],
                        input_object_ids=child["input_object_ids"],
                        execution_binding=child.get("execution_binding"),
                        lease_id=child["lease_id"],
                        executor_id="worker",
                    )
                    route_grant = (
                        host.request_provider_route_grant({"task": task})
                        if task["capability"] in {DOWNLOAD, TRANSCRIBE}
                        else None
                    )
                    host.run_task(
                        {"task": task},
                        lease_token=child["lease_id"],
                        attempt_id=child["attempt_id"],
                        fence=child["fence"],
                        provider_route_grant=route_grant,
                    )
                else:
                    stop.wait(0.01)
        except BaseException:  # noqa: BLE001 - report thread failures to the main assertion
            failures.append(traceback.format_exc())

    worker = threading.Thread(target=serve, daemon=True)
    worker.start()
    env = {**os.environ, "PYTHONPATH": str(candidate)}
    process = subprocess.Popen(
        [
            sys.executable,
            "-c",
            DRIVER,
            str(bridge.inherited_fd()),
            json.dumps(list(map(str, roots))),
            json.dumps(cfg),
            grant,
        ],
        cwd=root,
        env=env,
        pass_fds=(bridge.inherited_fd(),),
        start_new_session=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    bridge.launched()
    timeout = None
    stdout = stderr = ""
    try:
        stdout, stderr = process.communicate(timeout=35)
    except subprocess.TimeoutExpired as exc:
        timeout = exc
        stdout = exc.stdout or ""
        stderr = exc.stderr or ""
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=5)
        stop.set()
        worker.join(timeout=5)
        bridge.revoke()
    if timeout is not None:
        worker_traceback = "\n".join(failures) or "<no worker traceback captured>"
        raise AssertionError(
            "offline inherited-child fixture timed out after 35 seconds; "
            f"worker traceback:\n{worker_traceback}\n"
            f"child stdout:\n{stdout}\nchild stderr:\n{stderr}"
        ) from timeout
    assert process.returncode == 0, (stdout, stderr, failures)
    assert not worker.is_alive() and not failures
    proof = json.loads((outputs / "proof.json").read_text())
    delegated = [body for _, path, body in world.calls if path == "/v1/delegated-tasks"]
    if grant == "insufficient":
        assert len(delegated) == 1 and proof["error"] and len(proof["submitted"]) == 2
        return
    assert proof["same_meter"] and proof["meter"]["children"] == 5
    assert proof["meter"]["retained_objects"] == 5 and proof["meter"]["registered_objects"] == 3
    assert proof["meter"]["retained_bytes"] > 22 and proof["meter"]["registered_bytes"] == 41
    assert len(delegated) == 5 and proof["error"]
    assert [body["task"]["capability_id"] for body in delegated] == [
        DOWNLOAD,
        SCENES,
        DOWNLOAD,
        SCENES,
        TRANSCRIBE,
    ]
    scene_inputs = [
        body["task"]["spec"]["inputs"]["video"]
        for body in delegated
        if body["task"]["capability_id"] == SCENES
    ]
    # SDK producer descriptors remain unchanged up to the actual host bridge.
    # Runtime receives the host's canonical uploaded object/flat filename.
    producers = [proof["submitted"][1][1]["video"], proof["submitted"][3][1]["video"]]
    scene_requests = [
        request["inputs"]["video"]
        for request in bridge_requests
        if request["child"]["capability_id"] == SCENES
    ]
    assert scene_requests[:2] == producers
    assert len(scene_requests) == 3  # final over-budget request was rejected
    transcript_request = next(
        request for request in bridge_requests if request["child"]["capability_id"] == TRANSCRIBE
    )
    assert transcript_request["inputs"]["audio"] == proof["submitted"][4][1]["audio"]
    registry = world.service.task(result.kernel_task_id)["task"]["spec"]["derived_input_registry"]
    assert set(registry) == {
        digest(b"fixture-0"),
        digest(b"fixture-1"),
        digest(b"selected clip fixture-0"),
    }
    assert sum(item["size"] for item in registry.values()) == 41
    assert [item["object_id"] for item in scene_inputs] == [
        digest(b"fixture-0"),
        digest(b"fixture-1"),
    ]
    assert all(item["filename"] == registry[item["object_id"]]["filename"] for item in scene_inputs)
    assert all(ref["parent_attempt_id"] == claim["attempt_id"] for ref in registry.values())
    media = [
        entry
        for entry in bridge._materializations.values()
        if entry["output"]["output_port"] == "media"
    ]
    assert [entry["filename"] for entry in media] == [item["filename"] for item in producers]
    download_tasks = [
        world.service.task(record["id"])["task"]
        for record in world.service.store.conn.execute(
            "SELECT id FROM tasks WHERE capability=? ORDER BY created_at, id", (DOWNLOAD,)
        )
    ]
    assert [entry["output"]["task_id"] for entry in media] == [
        task["id"] for task in download_tasks
    ]
    assert [entry["output"]["attempt_id"] for entry in media] == [
        task["attempt_id"] for task in download_tasks
    ]


@pytest.mark.parametrize("boundary", ["retain", "register"])
def test_next_object_fails_with_byte_capacity_remaining(boundary):
    m = meter(max_derived_objects=1)
    if boundary == "retain":
        m.retain(row(b""))
        with pytest.raises(RuntimeError):
            m.retain(row(b"x", association="next"))
    else:
        m.register(row(b""), producer(), name="video")
        with pytest.raises(RuntimeError):
            m.register(row(b"x"), producer(), name="video")
    assert m.retained_bytes == m.registered_bytes == 0


def test_missing_run_meter_fails_before_provider_call(tmp_path, monkeypatch):
    monkeypatch.setattr(
        youtube.sdk, "invoke", lambda *args, **kwargs: pytest.fail("provider must not start")
    )
    provider = youtube.YouTubeSourceProvider()
    with pytest.raises(ValueError, match="run-scoped"):
        list(provider.acquire({"urls": ["one"], "out_dir": str(tmp_path / "not-created")}))
    assert not (tmp_path / "not-created").exists()


def test_transcript_caller_registers_exact_attempt_clip_and_reuses_sidecar(tmp_path, monkeypatch):
    root = tmp_path / "attempt"
    clip = root / "clips" / "selected.mp4"
    clip.parent.mkdir(parents=True)
    clip.write_bytes(b"exact selected clip")
    transcript_bytes = b'{"segments":[{"start":0,"end":1,"text":"hello"}]}'
    output = {
        "association_id": "transcript-association",
        "run_id": "child-run",
        "task_id": "child-task",
        "attempt_id": "child-attempt",
        "object_id": digest(transcript_bytes),
        "digest": digest(transcript_bytes),
        "size": len(transcript_bytes),
        "filename": "transcript.json",
        "output_port": "transcript",
        "media_type": "application/json",
    }
    invocations = []

    def invoke(capability, **kwargs):
        invocations.append((capability, kwargs))

        def materialize(association):
            assert association == output["association_id"]
            path = root / "child-outputs" / association / output["filename"]
            path.parent.mkdir(parents=True)
            path.write_bytes(transcript_bytes)
            return MaterializedChildOutput(output, path.relative_to(root).as_posix())

        return SimpleNamespace(
            ok=True,
            raw_result={"state": "completed"},
            error=None,
            kernel_run_id=output["run_id"],
            kernel_task_id=output["task_id"],
            kernel_attempt_id=output["attempt_id"],
            outputs={"managed_outputs": [output]},
            materialize_output=materialize,
        )

    monkeypatch.setattr(transcript_keyword.sdk, "invoke", invoke)
    work_meter = meter()
    item = {"item_id": "selected-clip", "media_path": str(clip), "clip_start_s": 2.0, "clip_end_s": 3.0}
    config = {
        "out_dir": str(tmp_path / "transcripts"),
        "child_work_meter": work_meter,
        "attempt_output_root": root,
        "allowlist": ["hello"],
        "model": "whisper-large-v3",
        "language": "fr",
        "max_chunk_sec": 300.0,
        "no_vad_gate": True,
    }
    stage = transcript_keyword.TranscriptKeywordFilter()
    first = stage._transcript(item, config)
    assert first["segments"][0]["text"] == "hello"
    assert len(invocations) == 1 and invocations[0][0] == "editorial.transcribe"
    inputs = invocations[0][1]["inputs"]
    assert inputs["audio"] == {
        "filename": "clips/selected.mp4",
        "media_type": "video/mp4",
        "output_port": "audio",
    }
    assert inputs == {
        "audio": {
            "filename": "clips/selected.mp4",
            "media_type": "video/mp4",
            "output_port": "audio",
        },
        "model": "whisper-large-v3",
        "language": "fr",
        "max_chunk_sec": 300.0,
        "no_vad_gate": True,
    }
    assert work_meter.as_dict()["children"] == 1
    assert work_meter.as_dict()["registered_bytes"] == len(b"exact selected clip")
    second = stage._transcript(item, config)
    assert second == first and len(invocations) == 1
    assert work_meter.as_dict()["children"] == 1


def test_transcript_caller_omits_default_options(tmp_path):
    root = tmp_path / "attempt"
    clip = root / "clips" / "selected.mp4"
    clip.parent.mkdir(parents=True)
    clip.write_bytes(b"clip")
    work_meter = meter()
    inputs, _ = transcript_keyword._transcribe_inputs(
        {"item_id": "selected", "media_path": str(clip)},
        {
            "model": "whisper-1",
            "language": "en",
            "max_chunk_sec": 600.0,
            "no_vad_gate": False,
        },
        meter=work_meter,
        attempt_root=root,
        repo_root=tmp_path,
    )
    assert inputs == {
        "audio": {
            "filename": "clips/selected.mp4",
            "media_type": "video/mp4",
            "output_port": "audio",
        }
    }
    assert work_meter.as_dict()["registered_objects"] == 1


def test_replaced_materialized_media_blocks_downstream_registration(tmp_path, monkeypatch):
    result = child_result(tmp_path)
    calls = []
    monkeypatch.setattr(youtube.sdk, "invoke", lambda cap, **kw: calls.append(cap) or result)
    m = meter()
    provider = youtube.YouTubeSourceProvider(child_work_meter=m, materialization_root=tmp_path)
    provider._child_keys = ("download", "scenes")
    video = provider._download_source({"value": "one"}, source_id="source", downloads_dir=tmp_path)
    local = provider._media[video][1]
    video.unlink()
    video.write_bytes(b"replaced bytes")
    result.materialize_output = lambda association: local
    with pytest.raises(ValueError, match="size changed"):
        provider._detect_scenes(video, tmp_path / "scenes.json")
    assert calls == [DOWNLOAD] and m.registered_bytes == 0 and m.retained_bytes == 5


VISUAL_CAPTION_DRIVER = """
import json, socket, sys
from pathlib import Path
from astrid.sdk._child_bridge import _install_child_bridge
_install_child_bridge(socket.socket(fileno=int(sys.argv[1])))
from astrid import sdk
from astrid.packs.training.actions.dataset_build import phases
from astrid.packs.training.actions.dataset_build.caption_providers import understanding
from astrid.packs.training.actions.dataset_build.services import DatasetRunServices
roots = tuple(json.loads(sys.argv[2])); cfg = json.loads(sys.argv[3]); suffix = sys.argv[4]
root = Path('outputs').resolve()
clip = root / 'clips' / ('selected' + suffix)
clip.parent.mkdir(parents=True)
clip.write_bytes(b'exact selected visual clip')
item = {'item_id': 'selected-clip', 'media_path': str(clip), 'duration_s': 4.0,
        'clip_start_s': 8.0, 'clip_end_s': 12.0}
services = DatasetRunServices.from_config(cfg, attempt_output_root=root)
work_meter = services.child_work_meter
events = []; submitted = []; results = []
original_register = work_meter.register_local_file
def register(path, producer, **kwargs):
    obj = original_register(path, producer, **kwargs)
    events.append({'boundary': 'register', 'producer': producer, 'object': obj})
    return obj
work_meter.register_local_file = register
original_admit = work_meter.admit
def admit(key, capability, inputs):
    assert len(events) == 2 and all(event['boundary'] == 'register' for event in events)
    assert events[0]['producer']['filename'] == clip.relative_to(root).as_posix()
    assert events[0]['object']['size'] == len(clip.read_bytes())
    assert inputs['response_schema'] == events[1]['producer']
    events.append({'boundary': 'admit'})
    return original_admit(key, capability, inputs)
work_meter.admit = admit
original_invoke = sdk.invoke
def invoke(capability, **kwargs):
    assert events[-1]['boundary'] == 'admit'
    submitted.append([capability, kwargs])
    child = original_invoke(capability, extra_pack_roots=roots, **kwargs)
    assert child.ok and child.raw_result['state'] == 'completed'
    results.append({'task_id': child.kernel_task_id, 'attempt_id': child.kernel_attempt_id,
                    'run_id': child.kernel_run_id, 'outputs': child.outputs['managed_outputs']})
    return child
understanding.sdk.invoke = invoke
first = phases._caption_items([item], cfg, root, services)
assert first[0]['caption']['text'] == 'A selected training clip.'
assert first[0]['caption']['model'] == 'fixture-vision-model'
sidecar = root / 'captions' / 'selected-clip.caption.json'
raw = json.loads(sidecar.read_text())
assert raw['raw_response']['results'][0]['answer'] == 'A selected training clip.'
assert set(raw['hashes']) == {'prompt_hash', 'media_hash', 'schema_hash', 'config_hash'}
before = work_meter.as_dict()
second = phases._caption_items([item], cfg, root, services)
assert second == first and work_meter.as_dict() == before
assert len(submitted) == 1 and len(events) == 3
assert services.budget_tracker.total_api_calls == 1
# Credential preflight precedes even sidecar resolution, for cache and fixture
# paths alike. The credential-bearing file is never opened or submitted.
original_sidecar = understanding.caption_sidecar_path
def forbidden_sidecar(*args, **kwargs): raise AssertionError('sidecar work preceded env_file preflight')
understanding.caption_sidecar_path = forbidden_sidecar
try:
    for fixture in (False, True):
        try:
            understanding.VisualUnderstandCaptionProvider().caption(item, {
                **cfg['caption'], 'env_file': '/must-not-read.env', 'fixture_mode': fixture})
        except ValueError as exc:
            assert str(exc) == "env_file is unsupported for delegated Understanding calls; configure OPENAI_API_KEY through the execution host's managed credentials."
        else: raise AssertionError('local env_file was accepted')
finally:
    understanding.caption_sidecar_path = original_sidecar
fixture = understanding.VisualUnderstandCaptionProvider().caption(item, {
    'fixture_mode': True, 'out_dir': str(root / 'fixture-captions'),
    'fixture_captions': {'selected-clip': 'Fixture caption preserved.'}})
assert fixture.text == 'Fixture caption preserved.' and len(submitted) == 1
(root / 'visual-proof.json').write_text(json.dumps({'meter': before, 'events': events,
    'submitted': submitted, 'results': results, 'caption': first[0]['caption']}))
"""


@pytest.mark.parametrize("suffix", [".mp4", ".png"])
def test_visual_caption_phase_public_child_schema_custody_and_cache(m17_world, monkeypatch, suffix):
    from runtime_protocol.service import CHILD_LIMITS

    from astrid import sdk
    from astrid.core.execution._child_bridge import HostChildBridge, task_resource
    from astrid.core.execution.generic_host import GenericPackHost
    from astrid.packs.understanding.actions.visual_understand import run as visual_run

    world = m17_world
    capability = "understanding.visual_understand"
    candidate = Path(__file__).resolve().parents[3]
    roots = [candidate / "astrid/packs" / name for name in ("training", "understanding")]
    host = GenericPackHost(
        pack_roots=roots, client=world.client, executor_id="worker", max_concurrency=2,
        attempt_root=world.tmp / "visual-children",
    )
    host.discover()
    monkeypatch.setenv("OPENAI_API_KEY", "offline-fixture-openai-key")
    assert host.preflight(capability)[0].ready
    schema_bytes = b'{"type":"object","properties":{"caption":{"type":"string"}}}'
    schema = world.tmp / "caption-schema.json"
    schema.write_bytes(schema_bytes)
    receiver_calls = []

    def collect(args):
        media = args.video if suffix == ".mp4" else args.image[0]
        assert media.read_bytes() == b"exact selected visual clip"
        if args.video:
            assert args.at == ["2.000"]
        else:
            assert args.at is None
        return [(media, "")], media

    def inference(**kwargs):
        assert kwargs["model"] == "fixture-vision-model"
        assert kwargs["query"] == "Describe selected-clip in bucket ."
        assert kwargs["response_schema"] == json.loads(schema_bytes)
        receiver_calls.append({"model": kwargs["model"], "query": kwargs["query"]})
        return {"output_text": "A selected training clip.", "id": "fixture-response", "usage": {"input_tokens": 1}}

    monkeypatch.setattr(visual_run, "_collect_inputs", collect)
    monkeypatch.setattr(visual_run, "_call_responses_api", inference)

    def execute(record, inputs, output_root, attempt, **kwargs):
        assert record.id == capability and "env_file" not in inputs
        child_env, secret_values = host._child_environment(
            record, attempt, authority_context=kwargs.get("authority_context"),
            admission=kwargs.get("admission"), network_broker=kwargs.get("network_broker"),
            allow_runtime_connection=False,
        )
        assert "OPENAI_API_KEY" in child_env and "OPENAI_API_KEY" in secret_values
        media_args = ["--video", str(inputs["video"]), "--at", inputs["at"]] if "video" in inputs else ["--image", str(inputs["image"])]
        try:
            assert visual_run.main([
                "--query", inputs["query"], *media_args,
                "--mode", inputs["mode"], "--model", inputs["model"],
                "--response-schema", str(inputs["response_schema"]),
                "--out-dir", str(output_root), "--out", str(output_root / "result.json"),
            ]) == 0
        finally:
            secret_values.clear()
            child_env.clear()
        return SimpleNamespace(payload={"m17_offline_visual_stub": True}, returncode=0,
                               process_id=os.getpid(), output_root=output_root)

    monkeypatch.setattr(host, "_run_command_definition", execute)
    monkeypatch.setattr(host, "_start_network_broker", lambda *args, **kwargs: None)
    monkeypatch.setattr(host, "_network_evidence", lambda *args, **kwargs: None)
    for cid in (PARENT, capability):
        world.service.register_capability({"capability_id": cid, "definition_digest": host.capabilities[cid].capability_digest})
    world.service.register_executor(
        {"executor_id": "worker", "capabilities": [PARENT, capability], "max_concurrency": 2},
        idempotency_key="m17-visual-register",
    )
    cfg = {
        "sources": [], "caption": {"provider": "visual_understand", "mode": "best",
            "model": "fixture-vision-model", "prompt_template": "Describe {clip_id} in bucket {bucket}.",
            "schema_path": str(schema), "child_work_meter": "untrusted", "attempt_output_root": "/untrusted"},
        "review": {"enabled": False, "top_up": {"max_rounds": 0}},
        "budgets": {"max_api_calls": 1, "delegation": {"max_children": 1,
            "max_derived_bytes": 64 * 1024, "max_child_inputs": 2, "max_child_bytes": 4096}},
    }
    plan = delegation_preflight(cfg)
    policy = {
        "capabilities": [{"capability_id": capability, "capability_digest": host.capabilities[capability].capability_digest}],
        "targets": [{"kind": "default"}], "input_object_ids": [],
        "limits": {**CHILD_LIMITS, **plan["limits"]},
    }
    client = public_client(world, monkeypatch)
    config_path = world.tmp / "visual-config.json"
    config_path.write_text(json.dumps(cfg))
    root = world.tmp / "visual-parent"
    outputs = root / "outputs"
    result = sdk.invoke(
        PARENT, kind="action", inputs={"config": str(config_path), "out": str(outputs)},
        extra_pack_roots=tuple(map(str, roots)), project=world.project,
        child_delegation=policy, client=client, wait=False,
    )
    assert result.ok and result.kernel_task_id
    claim = world.service.claim_next(
        {"executor_id": "worker", "capability_ids": [PARENT], "runtime_epoch": 1},
        idempotency_key="m17-visual-parent-claim", identity=world.identity,
    )
    assert claim["task_id"] == result.kernel_task_id
    world.client._attempt_runtime_epochs[claim["attempt_id"]] = claim["runtime_epoch"]
    outputs.mkdir(parents=True)
    bridge = HostChildBridge(
        host, task_resource(world.client.task(claim["task_id"])),
        **{key: claim[key] for key in ("attempt_id", "lease_id", "fence")},
        runtime_epoch=1, output_root=outputs, cancelled=lambda: False,
    )
    world.bridges.append(bridge)
    requests = []
    original_dispatch = bridge.dispatch

    def dispatch(request):
        if request["op"] in {"submit", "materialize_output"}:
            requests.append(copy.deepcopy(request))
        return original_dispatch(request)

    monkeypatch.setattr(bridge, "dispatch", dispatch)
    failures = []
    stop = threading.Event()

    def serve():
        try:
            while not stop.is_set():
                with world.lock:
                    claim_body = {"executor_id": "worker", "capability_ids": [capability], "runtime_epoch": 1}
                    queued = world.service.store.conn.execute(
                        "SELECT id FROM tasks WHERE capability=? AND status='queued' ORDER BY created_at, id LIMIT 1",
                        (capability,),
                    ).fetchone()
                    if queued is not None:
                        target = world.service.store.effective_execution_target(queued["id"])
                        if target is not None:
                            claim_body["target"] = target
                    child = world.service.claim_next(
                        claim_body,
                        idempotency_key="m17-visual-child-" + str(time.monotonic_ns()), identity=world.identity,
                    )
                if not child:
                    stop.wait(0.01)
                    continue
                world.client._attempt_runtime_epochs[child["attempt_id"]] = child["runtime_epoch"]
                task = world.service.task(child["task_id"])["task"]
                task.update(
                    runtime_epoch=child["runtime_epoch"], fence=child["fence"], project_id=world.project,
                    spec=child["spec"], input_object_ids=child["input_object_ids"],
                    execution_binding=child.get("execution_binding"), lease_id=child["lease_id"], executor_id="worker",
                )
                route_grant = host.request_provider_route_grant({"task": task})
                host.run_task({"task": task}, lease_token=child["lease_id"], attempt_id=child["attempt_id"],
                              fence=child["fence"], provider_route_grant=route_grant)
        except BaseException:
            failures.append(traceback.format_exc())

    worker = threading.Thread(target=serve, daemon=True)
    worker.start()
    process = subprocess.Popen(
        [sys.executable, "-c", VISUAL_CAPTION_DRIVER, str(bridge.inherited_fd()),
         json.dumps(list(map(str, roots))), json.dumps(cfg), suffix],
        cwd=root, env={**os.environ, "PYTHONPATH": str(candidate)},
        pass_fds=(bridge.inherited_fd(),), start_new_session=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    bridge.launched()
    try:
        stdout, stderr = process.communicate(timeout=35)
        assert process.returncode == 0, (stdout, stderr, failures)
        # The parent wait boundary verifies genuine child settlement before
        # releasing its inherited authority; M19 owns strict receipt variants.
        bridge.finish()
        with world.lock:
            world.service.settle_attempt(
                claim["attempt_id"],
                {**{key: claim[key] for key in ("lease_id", "fence", "runtime_epoch")}, "outputs": []},
                idempotency_key="m17-visual-parent-settle", identity=world.identity,
            )
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=5)
        stop.set()
        worker.join(timeout=5)
        bridge.revoke()
    assert not worker.is_alive() and not failures
    assert len(receiver_calls) == 1
    proof = json.loads((outputs / "visual-proof.json").read_text())
    assert "offline-fixture-openai-key" not in json.dumps(proof)
    assert world.service.task(result.kernel_task_id)["task"]["status"] == "completed"
    assert proof["meter"]["children"] == proof["meter"]["retained_objects"] == 1
    assert proof["meter"]["registered_objects"] == 2
    assert proof["meter"]["registered_bytes"] == len(b"exact selected visual clip") + len(schema_bytes)
    submitted = proof["submitted"][0]
    assert submitted[0] == capability and submitted[1]["wait"] is True
    inputs = submitted[1]["inputs"]
    port = "video" if suffix == ".mp4" else "image"
    assert inputs[port] == {"filename": "clips/selected" + suffix,
                            "media_type": "video/mp4" if port == "video" else "image/png", "output_port": port}
    assert "env_file" not in inputs
    assert inputs["response_schema"]["filename"] == "caption-inputs/response_schema/" + hashlib.sha256(schema_bytes).hexdigest() + ".json"
    submit_request = next(request for request in requests if request["op"] == "submit")
    assert submit_request["inputs"] == inputs
    assert {binding["kind"] for binding in submit_request["input_descriptors"]} == {"producer_file"}
    delegated = [body for _, path, body in world.calls if path == "/v1/delegated-tasks"]
    assert len(delegated) == 1 and delegated[0]["task"]["capability_id"] == capability
    registry = world.service.task(result.kernel_task_id)["task"]["spec"]["derived_input_registry"]
    assert set(registry) == {digest(b"exact selected visual clip"), digest(schema_bytes)}
    assert all(entry["parent_attempt_id"] == claim["attempt_id"] for entry in registry.values())
    child_proof = proof["results"][0]
    child_task = world.service.task(child_proof["task_id"])["task"]
    assert child_task["status"] == "completed" and child_task["attempt_id"] == child_proof["attempt_id"]
    result_output = next(output for output in child_proof["outputs"] if output["output_port"] == "result")
    materialize_request = next(request for request in requests if request["op"] == "materialize_output")
    assert materialize_request["association_id"] == result_output["association_id"]
    local = bridge._materializations[result_output["association_id"]]
    payload = (outputs / local["filename"]).read_bytes()
    assert local["output"] == result_output
    assert digest(payload) == result_output["digest"] and len(payload) == result_output["size"]
    assert proof["meter"]["retained_bytes"] == len(payload)
    sidecar = json.loads((outputs / "captions/selected-clip.caption.json").read_text())
    assert sidecar["raw_response"] == json.loads(payload)
    assert sidecar["text"] == proof["caption"]["text"] == "A selected training clip."


VIDEO_CAPTION_DRIVER = """
import json, socket, sys
from pathlib import Path
from astrid.sdk._child_bridge import _install_child_bridge
_install_child_bridge(socket.socket(fileno=int(sys.argv[1])))
from astrid import sdk
from astrid.packs.training.actions.dataset_build import phases
from astrid.packs.training.actions.dataset_build.caption_providers import understanding
from astrid.packs.training.actions.dataset_build.services import DatasetRunServices
roots = tuple(json.loads(sys.argv[2])); cfg = json.loads(sys.argv[3])
root = Path('outputs').resolve()
clip = root / 'clips' / 'selected.mp4'
clip.parent.mkdir(parents=True)
clip.write_bytes(b'exact selected video clip')
item = {'item_id': 'selected-clip', 'media_path': str(clip), 'duration_s': 4.0,
        'clip_start_s': 8.1234, 'clip_end_s': 12.9876}
services = DatasetRunServices.from_config(cfg, attempt_output_root=root)
work_meter = services.child_work_meter
events = []; submitted = []; results = []
original_register = work_meter.register_local_file
def register(path, producer, **kwargs):
    obj = original_register(path, producer, **kwargs)
    events.append({'boundary': 'register', 'producer': producer, 'object': obj})
    return obj
work_meter.register_local_file = register
original_admit = work_meter.admit
def admit(key, capability, inputs):
    assert len(events) == 1 and all(event['boundary'] == 'register' for event in events)
    assert events[0]['producer']['filename'] == clip.relative_to(root).as_posix()
    assert events[0]['object']['size'] == len(clip.read_bytes())
    assert inputs['max_chunks'] == 1 and inputs['start'] == '8.123' and inputs['end'] == '12.988'
    events.append({'boundary': 'admit'})
    return original_admit(key, capability, inputs)
work_meter.admit = admit
original_invoke = sdk.invoke
def invoke(capability, **kwargs):
    assert events[-1]['boundary'] == 'admit'
    submitted.append([capability, kwargs])
    child = original_invoke(capability, extra_pack_roots=roots, **kwargs)
    assert child.ok and child.raw_result['state'] == 'completed'
    results.append({'task_id': child.kernel_task_id, 'attempt_id': child.kernel_attempt_id,
                    'run_id': child.kernel_run_id, 'outputs': child.outputs['managed_outputs']})
    return child
understanding.sdk.invoke = invoke
first = phases._caption_items([item], cfg, root, services)
assert first[0]['caption']['text'] == 'A selected training clip.'
assert first[0]['caption']['model'] == 'fixture-video-model'
sidecar = root / 'captions' / 'selected-clip.caption.json'
raw = json.loads(sidecar.read_text())
assert raw['raw_response']['results'][0]['answer'] == 'A selected training clip.'
assert set(raw['hashes']) == {'prompt_hash', 'media_hash', 'config_hash'}
before = work_meter.as_dict()
second = phases._caption_items([item], cfg, root, services)
assert second == first and work_meter.as_dict() == before
assert len(submitted) == 1 and len(events) == 2
assert services.budget_tracker.total_api_calls == 1
# Credential preflight precedes even sidecar resolution, for cache and fixture
# paths alike. The credential-bearing file is never opened or submitted.
original_sidecar = understanding.caption_sidecar_path
def forbidden_sidecar(*args, **kwargs): raise AssertionError('sidecar work preceded env_file preflight')
understanding.caption_sidecar_path = forbidden_sidecar
try:
    for fixture in (False, True):
        try:
            understanding.VideoUnderstandCaptionProvider().caption(item, {
                **cfg['caption'], 'env_file': '/must-not-read.env', 'fixture_mode': fixture})
        except ValueError as exc:
            assert str(exc) == "env_file is unsupported for delegated Understanding calls; configure GEMINI_API_KEY through the execution host's managed credentials."
        else: raise AssertionError('local env_file was accepted')
finally:
    understanding.caption_sidecar_path = original_sidecar
fixture = understanding.VideoUnderstandCaptionProvider().caption(item, {
    'fixture_mode': True, 'out_dir': str(root / 'fixture-captions'),
    'fixture_captions': {'selected-clip': 'Fixture caption preserved.'}})
assert fixture.text == 'Fixture caption preserved.' and len(submitted) == 1
(root / 'video-proof.json').write_text(json.dumps({'meter': before, 'events': events,
    'submitted': submitted, 'results': results, 'caption': first[0]['caption']}))
"""


def test_video_caption_phase_public_child_windows_custody_and_cache(m17_world, monkeypatch):
    from runtime_protocol.service import CHILD_LIMITS

    from astrid import sdk
    from astrid.core.execution._child_bridge import HostChildBridge, task_resource
    from astrid.core.execution.generic_host import GenericPackHost
    from astrid.packs.understanding.actions.video_understand import run as video_run

    world = m17_world
    capability = "understanding.video_understand"
    monkeypatch.setenv("ASTRID_ENV_FILE", str(world.tmp / "missing-astrid.env"))
    candidate = Path(__file__).resolve().parents[3]
    roots = [candidate / "astrid/packs" / name for name in ("training", "understanding")]
    host = GenericPackHost(
        pack_roots=roots, client=world.client, executor_id="worker", max_concurrency=2,
        attempt_root=world.tmp / "video-children",
    )
    host.discover()
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    assert not host.preflight(capability)[0].ready
    monkeypatch.setenv("GEMINI_API_KEY", "offline-fixture-gemini-key")
    assert host.preflight(capability)[0].ready
    receiver_calls = []
    window_bytes = b"child-owned video window"

    def probe(source, **kwargs):
        assert source.read_bytes() == b"exact selected video clip"
        return 20.0

    def extract(source, window, out_dir, **kwargs):
        assert window == {"index": 1, "start": 8.123, "end": 12.988, "label": "range", "duration": 4.865}
        path = out_dir / "video-windows/window.mp4"
        path.parent.mkdir(parents=True)
        path.write_bytes(window_bytes)
        return path

    def inference(**kwargs):
        assert kwargs["model"] == "fixture-video-model"
        assert kwargs["prompt"].startswith("Describe selected-clip in bucket .\n\n")
        assert "source-relative 8.123s to 12.988s" in kwargs["prompt"]
        assert kwargs["video_path"].read_bytes() == window_bytes
        assert kwargs["response_schema"] == video_run.RESPONSE_SCHEMA
        receiver_calls.append({"model": kwargs["model"], "prompt": kwargs["prompt"]})
        return "A selected training clip."

    monkeypatch.setattr(video_run, "ffprobe_duration_seconds", probe)
    monkeypatch.setattr(video_run, "_extract_window", extract)
    monkeypatch.setattr(video_run, "build_gemini_client", lambda env_file: SimpleNamespace(describe_video=inference))

    def execute(record, inputs, output_root, attempt, **kwargs):
        assert record.id == capability and "env_file" not in inputs
        child_env, secret_values = host._child_environment(
            record, attempt, authority_context=kwargs.get("authority_context"),
            admission=kwargs.get("admission"), network_broker=kwargs.get("network_broker"),
            allow_runtime_connection=False,
        )
        assert "GEMINI_API_KEY" in child_env and "GEMINI_API_KEY" in secret_values
        try:
            assert video_run.main([
                "--query", inputs["query"], "--video", str(inputs["video"]),
                "--max-chunks", str(inputs["max_chunks"]), "--start", inputs["start"], "--end", inputs["end"],
                "--mode", inputs["mode"], "--model", inputs["model"],
                "--out-dir", str(output_root), "--out", str(output_root / "result.json"),
            ]) == 0
        finally:
            secret_values.clear()
            child_env.clear()
        return SimpleNamespace(payload={"m17_offline_video_stub": True}, returncode=0,
                               process_id=os.getpid(), output_root=output_root)

    monkeypatch.setattr(host, "_run_command_definition", execute)
    monkeypatch.setattr(host, "_start_network_broker", lambda *args, **kwargs: None)
    monkeypatch.setattr(host, "_network_evidence", lambda *args, **kwargs: None)
    for cid in (PARENT, capability):
        world.service.register_capability({"capability_id": cid, "definition_digest": host.capabilities[cid].capability_digest})
    world.service.register_executor(
        {"executor_id": "worker", "capabilities": [PARENT, capability], "max_concurrency": 2},
        idempotency_key="m17-video-register",
    )
    cfg = {
        "sources": [], "caption": {"provider": "video_understand", "mode": "best",
            "model": "fixture-video-model", "prompt_template": "Describe {clip_id} in bucket {bucket}.",
            "child_work_meter": "untrusted", "attempt_output_root": "/untrusted"},
        "review": {"enabled": False, "top_up": {"max_rounds": 0}},
        "budgets": {"max_api_calls": 1, "delegation": {"max_children": 1,
            "max_derived_bytes": 64 * 1024, "max_child_inputs": 1, "max_child_bytes": 4096}},
    }
    plan = delegation_preflight(cfg)
    policy = {
        "capabilities": [{"capability_id": capability, "capability_digest": host.capabilities[capability].capability_digest}],
        "targets": [{"kind": "default"}], "input_object_ids": [],
        "limits": {**CHILD_LIMITS, **plan["limits"]},
    }
    client = public_client(world, monkeypatch)
    config_path = world.tmp / "video-config.json"
    config_path.write_text(json.dumps(cfg))
    root = world.tmp / "video-parent"
    outputs = root / "outputs"
    result = sdk.invoke(
        PARENT, kind="action", inputs={"config": str(config_path), "out": str(outputs)},
        extra_pack_roots=tuple(map(str, roots)), project=world.project,
        child_delegation=policy, client=client, wait=False,
    )
    assert result.ok and result.kernel_task_id
    claim = world.service.claim_next(
        {"executor_id": "worker", "capability_ids": [PARENT], "runtime_epoch": 1},
        idempotency_key="m17-video-parent-claim", identity=world.identity,
    )
    assert claim["task_id"] == result.kernel_task_id
    world.client._attempt_runtime_epochs[claim["attempt_id"]] = claim["runtime_epoch"]
    outputs.mkdir(parents=True)
    bridge = HostChildBridge(
        host, task_resource(world.client.task(claim["task_id"])),
        **{key: claim[key] for key in ("attempt_id", "lease_id", "fence")},
        runtime_epoch=1, output_root=outputs, cancelled=lambda: False,
    )
    world.bridges.append(bridge)
    requests = []
    original_dispatch = bridge.dispatch

    def dispatch(request):
        if request["op"] in {"submit", "materialize_output"}:
            requests.append(copy.deepcopy(request))
        return original_dispatch(request)

    monkeypatch.setattr(bridge, "dispatch", dispatch)
    failures = []
    stop = threading.Event()

    def serve():
        try:
            while not stop.is_set():
                with world.lock:
                    claim_body = {"executor_id": "worker", "capability_ids": [capability], "runtime_epoch": 1}
                    queued = world.service.store.conn.execute(
                        "SELECT id FROM tasks WHERE capability=? AND status='queued' ORDER BY created_at, id LIMIT 1",
                        (capability,),
                    ).fetchone()
                    if queued is not None:
                        target = world.service.store.effective_execution_target(queued["id"])
                        if target is not None:
                            claim_body["target"] = target
                    child = world.service.claim_next(
                        claim_body,
                        idempotency_key="m17-video-child-" + str(time.monotonic_ns()), identity=world.identity,
                    )
                if not child:
                    stop.wait(0.01)
                    continue
                world.client._attempt_runtime_epochs[child["attempt_id"]] = child["runtime_epoch"]
                task = world.service.task(child["task_id"])["task"]
                task.update(
                    runtime_epoch=child["runtime_epoch"], fence=child["fence"], project_id=world.project,
                    spec=child["spec"], input_object_ids=child["input_object_ids"],
                    execution_binding=child.get("execution_binding"), lease_id=child["lease_id"], executor_id="worker",
                )
                route_grant = host.request_provider_route_grant({"task": task})
                host.run_task({"task": task}, lease_token=child["lease_id"], attempt_id=child["attempt_id"],
                              fence=child["fence"], provider_route_grant=route_grant)
        except BaseException:
            failures.append(traceback.format_exc())

    worker = threading.Thread(target=serve, daemon=True)
    worker.start()
    process = subprocess.Popen(
        [sys.executable, "-c", VIDEO_CAPTION_DRIVER, str(bridge.inherited_fd()),
         json.dumps(list(map(str, roots))), json.dumps(cfg)],
        cwd=root, env={**os.environ, "PYTHONPATH": str(candidate)},
        pass_fds=(bridge.inherited_fd(),), start_new_session=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    bridge.launched()
    try:
        stdout, stderr = process.communicate(timeout=35)
        assert process.returncode == 0, (stdout, stderr, failures)
        # The parent wait boundary verifies genuine child settlement before
        # releasing its inherited authority; M19 owns strict receipt variants.
        bridge.finish()
        with world.lock:
            world.service.settle_attempt(
                claim["attempt_id"],
                {**{key: claim[key] for key in ("lease_id", "fence", "runtime_epoch")}, "outputs": []},
                idempotency_key="m17-video-parent-settle", identity=world.identity,
            )
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=5)
        stop.set()
        worker.join(timeout=5)
        bridge.revoke()
    assert not worker.is_alive() and not failures
    assert len(receiver_calls) == 1
    proof = json.loads((outputs / "video-proof.json").read_text())
    assert "offline-fixture-gemini-key" not in json.dumps(proof)
    assert world.service.task(result.kernel_task_id)["task"]["status"] == "completed"
    assert proof["meter"]["children"] == proof["meter"]["retained_objects"] == 1
    assert proof["meter"]["registered_objects"] == 1
    assert proof["meter"]["registered_bytes"] == len(b"exact selected video clip")
    submitted = proof["submitted"][0]
    assert submitted[0] == capability and submitted[1]["wait"] is True
    inputs = submitted[1]["inputs"]
    assert inputs == {"video": {"filename": "clips/selected.mp4", "media_type": "video/mp4", "output_port": "video"},
                      "query": "Describe selected-clip in bucket .", "mode": "best", "model": "fixture-video-model",
                      "max_chunks": 1, "start": "8.123", "end": "12.988"}
    identity = json.dumps(["selected-clip", inputs, [digest(b"exact selected video clip")]],
                          sort_keys=True, separators=(",", ":"), allow_nan=False)
    assert submitted[1]["child_key"] == "caption_video_" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:32]
    submit_request = next(request for request in requests if request["op"] == "submit")
    assert submit_request["inputs"] == inputs
    assert {binding["kind"] for binding in submit_request["input_descriptors"]} == {"producer_file"}
    delegated = [body for _, path, body in world.calls if path == "/v1/delegated-tasks"]
    assert len(delegated) == 1 and delegated[0]["task"]["capability_id"] == capability
    registry = world.service.task(result.kernel_task_id)["task"]["spec"]["derived_input_registry"]
    assert set(registry) == {digest(b"exact selected video clip")}
    assert all(entry["parent_attempt_id"] == claim["attempt_id"] for entry in registry.values())
    child_proof = proof["results"][0]
    child_task = world.service.task(child_proof["task_id"])["task"]
    assert child_task["status"] == "completed" and child_task["attempt_id"] == child_proof["attempt_id"]
    settled_outputs = child_task["result"]["outputs"]
    assert {
        (output["name"], output["role"], output["is_primary"])
        for output in settled_outputs
    } == {("result", "result", True), ("windows", "result", False)}
    result_output = next(output for output in child_proof["outputs"] if output["output_port"] == "result")
    materialize_request = next(request for request in requests if request["op"] == "materialize_output")
    assert materialize_request["association_id"] == result_output["association_id"]
    local = bridge._materializations[result_output["association_id"]]
    payload = (outputs / local["filename"]).read_bytes()
    assert local["output"] == result_output
    assert digest(payload) == result_output["digest"] and len(payload) == result_output["size"]
    assert proof["meter"]["retained_bytes"] == len(payload)
    sidecar = json.loads((outputs / "captions/selected-clip.caption.json").read_text())
    assert sidecar["raw_response"] == json.loads(payload)
    assert sidecar["text"] == proof["caption"]["text"] == "A selected training clip."
    windows = [output for output in child_proof["outputs"] if output["output_port"] == "windows"]
    assert len(windows) == 1 and windows[0]["digest"] == digest(window_bytes)
    assert {output["output_port"] for output in child_proof["outputs"]} == {"result", "windows"}
    materializations = [request for request in requests if request["op"] == "materialize_output"]
    assert [request["association_id"] for request in materializations] == [result_output["association_id"]]
    assert set(bridge._materializations) == {result_output["association_id"]}
    assert list((outputs / "child-outputs").rglob("*.mp4")) == []
    assert not (outputs / "video-windows").exists()
    assert not any(path.name == "window.mp4" for path in outputs.rglob("*"))
    raw = json.loads(payload)
    assert len(raw["windows"]) == len(raw["results"]) == 1
    # Paths in the receiver result remain child provenance, and are never
    # treated as parent materialization instructions.
    assert raw["windows"][0]["path"].startswith(str(host.attempt_root))
    assert not Path(raw["windows"][0]["path"]).is_relative_to(outputs)



def test_video_caption_gemini_requirements_preserve_visual_and_filter_preflight():
    from astrid.packs.training.actions.dataset_build import config as dataset_config

    assert dataset_config.API_BACKED_CAPTION_PROVIDERS == {
        "visual_understand": ("OPENAI_API_KEY",), "video_understand": ("GEMINI_API_KEY",),
    }
    requirements = dataset_config._api_requirements({
        "caption": {"provider": "video_understand"},
        "filters": {"stages": [
            {"stage_id": "semantic_visual_filter", "enabled": True},
            {"stage_id": "semantic_video_filter", "enabled": True},
            {"stage_id": "bucket_judge_filter", "enabled": True,
             "config": {"bucket_judge": {"provider": "video_understand"}}},
        ]},
    })
    assert requirements == {
        "caption.video_understand": ("GEMINI_API_KEY",),
        "bucket_judge.video_understand": ("GEMINI_API_KEY",),
        "filter.semantic_visual": ("OPENAI_API_KEY",),
        "filter.semantic_video": ("GEMINI_API_KEY",),
    }


@pytest.mark.parametrize("fixture_mode", [False, True])
def test_video_caption_env_file_rejected_before_cache_file_or_child_work(tmp_path, monkeypatch, fixture_mode):
    from astrid.packs.training.actions.dataset_build.caption_providers import understanding

    def forbidden(*args, **kwargs):
        pytest.fail("file/cache/child work preceded env_file rejection")

    monkeypatch.setattr(understanding, "caption_sidecar_path", forbidden)
    monkeypatch.setattr(understanding, "_media_path", forbidden)
    monkeypatch.setattr(understanding, "load_valid_cached_sidecar", forbidden)
    monkeypatch.setattr(understanding.sdk, "invoke", forbidden)
    monkeypatch.setattr(understanding.VideoUnderstandCaptionProvider, "_sidecar_hashes", forbidden)
    before = tuple(
        (
            path.relative_to(tmp_path).as_posix(),
            path.read_bytes() if path.is_file() else None,
        )
        for path in sorted(tmp_path.rglob("*"), key=lambda path: path.relative_to(tmp_path).as_posix())
    )
    provider = understanding.VideoUnderstandCaptionProvider(runner=forbidden)
    with pytest.raises(ValueError, match="configure GEMINI_API_KEY"):
        provider.caption({"media_path": str(tmp_path / "unread.mp4")}, {
            "env_file": str(tmp_path / "unread.env"), "fixture_mode": fixture_mode,
        })
    after = tuple(
        (
            path.relative_to(tmp_path).as_posix(),
            path.read_bytes() if path.is_file() else None,
        )
        for path in sorted(tmp_path.rglob("*"), key=lambda path: path.relative_to(tmp_path).as_posix())
    )
    assert after == before


@pytest.mark.parametrize("prebaked", [False, True])
def test_video_caption_fixture_needs_no_authority_or_provider(tmp_path, monkeypatch, prebaked):
    from astrid.packs.training.actions.dataset_build.caption_providers import understanding

    def forbidden(*args, **kwargs):
        pytest.fail("fixture caption invoked provider")

    monkeypatch.setattr(understanding.sdk, "invoke", forbidden)
    provider = understanding.VideoUnderstandCaptionProvider(runner=forbidden)
    cfg = {"fixture_mode": True, "env_file": "", "out_dir": str(tmp_path / "captions"),
           "fixture_captions": {"selected": "Offline fixture caption."}}
    if prebaked:
        source = tmp_path / "prebaked.json"
        source.write_text(json.dumps({"text": "Prebaked caption.", "model": "fixture-prebaked", "confidence": 0.5}))
        cfg["caption_file"] = str(source)
    result = provider.caption({"item_id": "selected", "media_path": "/never-read.mp4"}, cfg)
    assert result.text == ("Prebaked caption." if prebaked else "Offline fixture caption.")
    assert json.loads((tmp_path / "captions/selected.caption.json").read_text())["text"] == result.text


@pytest.mark.parametrize("start,end,expected", [
    (None, None, {}), (3, 3, {}), (5, 3, {}), ("2", "4", {}),
    (1.23456, 4.98765, {"start": "1.235", "end": "4.988"}),
])
def test_video_caption_public_inputs_preserve_range_defaults_and_one_chunk(tmp_path, start, end, expected):
    from astrid.packs.training.actions.dataset_build.caption_providers import understanding

    media = tmp_path / "selected.mp4"
    media.write_bytes(b"selected video")
    work_meter = meter()
    provider = understanding.VideoUnderstandCaptionProvider()
    inputs, objects = provider._public_inputs(
        {"item_id": "selected", "media_path": str(media), "clip_start_s": start, "clip_end_s": end},
        {"max_chunks": 99, "schema_path": "/previously-unused-schema.json"},
        meter=work_meter, attempt_root=tmp_path,
    )
    assert inputs == {
        "video": {"filename": "selected.mp4", "media_type": "video/mp4", "output_port": "video"},
        "query": understanding.DEFAULT_PROMPT, "max_chunks": 1, **expected,
    }
    assert len(objects) == 1 and objects[0]["digest"] == digest(media.read_bytes())
    assert work_meter.as_dict()["registered_objects"] == 1


@pytest.mark.parametrize("problem", ["outside", "image", "bytes"])
def test_video_caption_inputs_reject_wrong_custody_type_or_child_byte_budget(tmp_path, problem):
    from astrid.packs.training.actions.dataset_build.caption_providers import understanding

    root = tmp_path / "attempt"
    root.mkdir()
    media = (tmp_path if problem == "outside" else root) / ("clip.png" if problem == "image" else "clip.mp4")
    media.write_bytes(b"selected video")
    work_meter = meter(max_child_bytes=1 if problem == "bytes" else 1024)
    with pytest.raises((ValueError, RuntimeError)):
        understanding.VideoUnderstandCaptionProvider()._public_inputs(
            {"media_path": str(media)}, {}, meter=work_meter, attempt_root=root,
        )
    assert work_meter.as_dict()["children"] == 0


def test_video_caption_cache_hashes_preserve_prior_identity_and_ignore_runtime_authority(tmp_path):
    from astrid.packs.training.actions.dataset_build.caption_providers import understanding

    media = tmp_path / "clip.mp4"
    media.write_bytes(b"selected video")
    item = {"item_id": "clip", "media_path": str(media)}
    cfg = {"provider": "video_understand", "model": "fixture-model", "mode": "best", "prompt_template": "Describe {clip_id}."}
    provider = understanding.VideoUnderstandCaptionProvider()
    before = understanding._BaseUnderstandingCaptionProvider._sidecar_hashes(provider, item, cfg)
    assert provider._sidecar_hashes(item, {**cfg, "child_work_meter": meter(), "attempt_output_root": tmp_path}) == before
    assert provider._sidecar_hashes(item, {**cfg, "child_work_meter": "untrusted", "attempt_output_root": "/untrusted"}) == before


@pytest.mark.parametrize("problem", [
    "valid", "state", "task_missing", "attempt_missing", "run_missing",
    "no_result", "duplicate_result", "task", "attempt", "run", "size", "digest", "object_id",
    "association", "media_type", "local_descriptor", "absolute", "traversal", "outside_symlink",
    "byte_size", "byte_digest",
])
def test_video_caption_primary_receipt_and_materialized_byte_contract(tmp_path, problem):
    from astrid.packs.training.actions.dataset_build.caption_providers import understanding

    payload = b'{"results":[{"status":"ok","model":"fixture-model","answer":"A clip."}]}'
    output = row(payload, association="result-association", output_port="result",
                 media_type="application/json", filename="result.json")
    rows = [output, row(b"window", association="window-association", output_port="windows")]
    if problem in {"task", "attempt", "run"}:
        output[problem + "_id"] = "wrong-identity"
    elif problem == "size":
        output["size"] = True
    elif problem == "digest":
        output["digest"] = "not-a-digest"
    elif problem == "object_id":
        output["object_id"] = digest(b"wrong")
    elif problem == "association":
        output["association_id"] = ""
    elif problem == "media_type":
        output["media_type"] = "video/mp4"
    elif problem == "no_result":
        rows = rows[1:]
    elif problem == "duplicate_result":
        rows.append(dict(output))
    materializations = []

    def materialize(association):
        materializations.append(association)
        assert association == "result-association"
        path = tmp_path / "child-outputs/result-association/result.json"
        path.parent.mkdir(parents=True)
        local_payload = payload + b" " if problem == "byte_size" else payload
        if problem == "byte_digest":
            local_payload = payload.replace(b"A clip.", b"B clip.")
        path.write_bytes(local_payload)
        filename = path.relative_to(tmp_path).as_posix()
        if problem == "absolute":
            filename = str(path)
        elif problem == "traversal":
            filename = "../result.json"
        elif problem == "outside_symlink":
            outside = tmp_path.parent / (tmp_path.name + "-outside.json")
            outside.write_bytes(payload)
            path.unlink()
            path.symlink_to(outside)
        descriptor = {**output, "attempt_id": "changed"} if problem == "local_descriptor" else output
        return MaterializedChildOutput(descriptor, filename)

    child = SimpleNamespace(
        ok=True, raw_result={"state": "running" if problem == "state" else "completed"}, error=None,
        kernel_task_id="" if problem == "task_missing" else "task",
        kernel_attempt_id="" if problem == "attempt_missing" else "attempt",
        kernel_run_id="" if problem == "run_missing" else "run",
        outputs={"managed_outputs": rows}, materialize_output=materialize,
    )
    work_meter = meter()
    if problem == "valid":
        assert understanding._materialize_video_result(child, attempt_root=tmp_path, meter=work_meter) == json.loads(payload)
        assert materializations == ["result-association"]
        assert work_meter.as_dict()["retained_objects"] == 1
        assert work_meter.as_dict()["retained_bytes"] == len(payload)
        assert not list(tmp_path.rglob("*.mp4"))
    else:
        with pytest.raises((ValueError, RuntimeError)):
            understanding._materialize_video_result(child, attempt_root=tmp_path, meter=work_meter)
        before_materialization = {"state", "task_missing", "attempt_missing", "run_missing", "no_result", "duplicate_result",
                                  "task", "attempt", "run", "size", "digest", "object_id", "association", "media_type"}
        if problem in before_materialization:
            assert materializations == [] and work_meter.as_dict()["retained_objects"] == 0


FINITE_FILTER_ROUTES = ("semantic_visual", "semantic_video", "bucket_visual", "bucket_video")


def _finite_filter_setup(tmp_path, route):
    from astrid.packs.training.actions.dataset_build.filter_stages import bucket_judge, semantic

    root = tmp_path / "attempt"
    root.mkdir()
    media = tmp_path / "selected.mp4"
    media.write_bytes(b"selected finite filter video")
    work_meter = meter(max_child_inputs=2, max_child_bytes=8192, max_derived_bytes=8192)
    budget_calls = []
    cfg = {
        "out_dir": str(tmp_path / "cache"), "child_work_meter": work_meter,
        "attempt_output_root": root, "budget_tracker": SimpleNamespace(increment=budget_calls.append),
        "model": "previously-unused-model", "max_chunks": 99,
    }
    item = {"clip_id": "selected", "media_path": str(media), "duration_s": 6,
            "clip_start_s": 1.23456, "clip_end_s": 4.98765}

    def forbidden(*args, **kwargs):
        pytest.fail("finite filter used its private subprocess runner")

    if route.startswith("semantic"):
        cls = semantic.SemanticVisualFilter if route.endswith("visual") else semantic.SemanticVideoFilter
        stage, module = cls(runner=forbidden, repo_root=tmp_path), semantic
        cfg.update(prompt_template="Keep {clip_id}: {feedback_hints}; {missing}.",
                   top_up_feedback_hints=["more motion", "better light"])
        decision = {"accept": True, "reason": "Target / present", "score": 0.75, "details": {"motion": True}}
    else:
        stage, module = bucket_judge.BucketJudgeGate(runner=forbidden, repo_root=tmp_path), bucket_judge
        cfg.update(enabled=True, provider="visual_understand" if route.endswith("visual") else "video_understand",
                   buckets={"birds": {}}, prompt_template="Bucket {clip_id}: {buckets}; {missing}.")
        decision = {"accept": True, "bucket": "birds", "reason": "Target / present", "score": 0.75}
    return module, stage, item, cfg, decision, budget_calls


def _finite_filter_child(tmp_path, decision, *, problem=None):
    payload = json.dumps({"results": [{"status": "ok", "answer": decision}]}).encode()
    primary = row(payload, association="filter-result", output_port="result",
                  media_type="application/json", filename="result.json")
    rows = [primary, {"output_port": "windows", "association_id": "never-materialize-window"},
            {"output_port": "auxiliary", "association_id": "never-materialize-auxiliary"}]
    if problem in {"task", "attempt", "run"}:
        primary[problem + "_id"] = "wrong"
    elif problem == "duplicate":
        rows.append(dict(primary))
    elif problem == "missing":
        rows = rows[1:]
    elif problem == "size":
        primary["size"] = True
    elif problem == "object_id":
        primary["object_id"] = digest(b"wrong")
    elif problem == "media_type":
        primary["media_type"] = "video/mp4"
    materializations = []

    def materialize(association):
        materializations.append(association)
        assert association == "filter-result"
        path = tmp_path / "attempt/child-outputs/filter-result/result.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload + b" " if problem == "byte_size" else payload)
        if problem == "byte_digest":
            path.write_bytes(payload.replace(b"present", b"missing"))
        filename = path.relative_to(tmp_path / "attempt").as_posix()
        if problem == "absolute":
            filename = str(path)
        elif problem == "traversal":
            filename = "../result.json"
        elif problem == "symlink":
            outside = tmp_path / "outside-result.json"
            outside.write_bytes(payload)
            path.unlink()
            path.symlink_to(outside)
        descriptor = {**primary, "run_id": "changed"} if problem == "descriptor" else primary
        return MaterializedChildOutput(descriptor, filename)

    return SimpleNamespace(
        ok=problem != "failed", error="offline failure" if problem == "failed" else None,
        raw_result={"state": "running" if problem == "state" else "completed"},
        kernel_task_id="" if problem == "identity_missing" else "task",
        kernel_attempt_id="attempt", kernel_run_id="run", outputs={"managed_outputs": rows},
        materialize_output=materialize,
    ), materializations, payload


@pytest.mark.parametrize("route", FINITE_FILTER_ROUTES)
@pytest.mark.parametrize("accept", [True, False])
def test_finite_filter_public_children_stage_meter_primary_and_preserve_cache(tmp_path, monkeypatch, route, accept):
    module, stage, item, cfg, decision, budget_calls = _finite_filter_setup(tmp_path, route)
    decision["accept"] = accept
    child, materializations, payload = _finite_filter_child(tmp_path, decision)
    work_meter = cfg["child_work_meter"]
    root = cfg["attempt_output_root"]
    sidecar = (module.semantic_sidecar_path(item, cfg, stage_id=stage.stage_id, repo_root=tmp_path)
               if route.startswith("semantic") else module.judge_sidecar_path(item, cfg, repo_root=tmp_path))
    sidecar.parent.mkdir()
    sidecar.write_text(json.dumps({"accept": not accept, "reason": "stale", "score": 0, "bucket": "wrong"}))
    calls = []

    def invoke(capability, **kwargs):
        assert not sidecar.exists(), "stale sidecar must be removed before child invocation"
        assert work_meter.as_dict()["children"] == 1
        assert work_meter.as_dict()["registered_objects"] == 2
        inputs = kwargs["inputs"]
        assert capability == "understanding." + ("visual_understand" if route.endswith("visual") else "video_understand")
        assert kwargs["kind"] == "action" and kwargs["wait"] is True
        assert kwargs["timeout_seconds"] == 600.0 and kwargs["poll_seconds"] == 0.1
        assert set(inputs) == ({"video", "response_schema", "query", "at"} if route.endswith("visual")
                               else {"video", "response_schema", "query", "max_chunks", "start", "end"})
        assert inputs["query"] == ("Keep selected: more motion, better light; {missing}." if route.startswith("semantic")
                                   else "Bucket selected: birds; {missing}.")
        if route.endswith("visual"):
            assert inputs["at"] == ("4.235" if route.startswith("semantic") else "3.000")
        else:
            assert inputs["max_chunks"] == 1 and (inputs["start"], inputs["end"]) == ("1.235", "4.988")
        media = root / inputs["video"]["filename"]
        schema = root / inputs["response_schema"]["filename"]
        assert media.is_relative_to(root) and schema.is_relative_to(root)
        assert media.read_bytes() == Path(item["media_path"]).read_bytes()
        assert media.name == hashlib.sha256(media.read_bytes()).hexdigest() + ".mp4"
        schema_bytes = schema.read_bytes()
        expected = ({"name": stage.stage_id, "schema": module.SEMANTIC_DECISION_SCHEMA, "strict": True}
                    if route.startswith("semantic") else {"name": "bucket_judge", "schema": module.JUDGE_SCHEMA, "strict": True})
        assert json.loads(schema_bytes) == expected
        assert schema.name == hashlib.sha256(schema_bytes).hexdigest() + ".json"
        assert inputs["video"]["media_type"] == "video/mp4"
        assert inputs["response_schema"]["media_type"] == "application/json"
        assert inputs["response_schema"]["output_port"] == "response_schema"
        assert work_meter.registered_bytes == len(media.read_bytes()) + len(schema_bytes)
        calls.append((capability, kwargs))
        return child

    monkeypatch.setattr(module.sdk, "invoke", invoke)
    result = stage.apply([item], {}, cfg)
    assert len(result.passed) == int(accept) and len(result.rejected) == int(not accept)
    assert result.stats["items_in"] == 1
    assert result.stats["items_passed"] == int(accept) and result.stats["items_rejected"] == int(not accept)
    updated = (result.passed or result.rejected)[0]
    filter_result = updated["filter_results"][stage.stage_id]
    assert filter_result["passed"] is accept and filter_result["score"] == decision["score"]
    if route.startswith("semantic"):
        assert filter_result["semantic_decision"] == decision
        assert filter_result["feedback_hint"] == stage.stage_id + ":target_present"
        assert filter_result["reason"] == (decision["reason"] if accept else stage.stage_id + "_target_present")
        assert result.stats["rejection_reasons"] == ({} if accept else {stage.stage_id + "_target_present": 1})
    elif accept:
        assert updated["judge_result"] == decision
        assert updated["bucket"] == "birds"
    else:
        assert updated["judge_result"] == decision
        assert filter_result["reason"] == decision["reason"]
        assert result.stats["rejection_reasons"] == {decision["reason"]: 1}
    assert materializations == ["filter-result"]
    assert work_meter.as_dict()["retained_objects"] == 1 and work_meter.retained_bytes == len(payload)
    assert budget_calls == (["filter.semantic_visual" if route.endswith("visual") else "filter.semantic_video"]
                            if route.startswith("semantic") else ["bucket_judge." + cfg["provider"]])
    cached = json.loads(sidecar.read_text())
    assert {key: cached[key] for key in decision} == decision and "hashes" in cached
    # A valid cache keeps its old identity and needs no child authority.
    again = stage.apply([item], {}, {**cfg, "child_work_meter": None, "attempt_output_root": "/untrusted"})
    assert len(again.passed) == int(accept) and len(again.rejected) == int(not accept)
    assert len(calls) == len(budget_calls) == 1 and materializations == ["filter-result"]


@pytest.mark.parametrize("projection", ["direct", "bucket_judge", "extensions"])
def test_finite_filter_runtime_authority_overrides_nested_stage_values(tmp_path, projection):
    from astrid.packs.training.actions.dataset_build import phases
    from astrid.packs.training.actions.dataset_build.filter_stages import bucket_judge

    trusted_meter = meter(max_child_inputs=2)
    service_budget, nested_budget = object(), object()
    service_clock, nested_clock = object(), object()
    services = DatasetRunServices(budget_tracker=service_budget, child_work_meter=trusted_meter,
                                 attempt_output_root=tmp_path, clock=service_clock)
    nested = {"enabled": True, "provider": "video_understand", "buckets": ["birds"],
              "budget_tracker": nested_budget, "clock": nested_clock,
              "child_work_meter": "nested-untrusted", "attempt_output_root": "/nested-untrusted"}
    stage_cfg = dict(nested) if projection == "direct" else {
        "child_work_meter": "outer-untrusted", "attempt_output_root": "/outer-untrusted",
    }
    if projection == "bucket_judge":
        stage_cfg["bucket_judge"] = nested
    elif projection == "extensions":
        stage_cfg["extensions"] = {"bucket_judge": nested}
    before = copy.copy(stage_cfg)
    runtime = phases._stage_runtime_config(stage_cfg, services)
    assert runtime["child_work_meter"] is trusted_meter and runtime["attempt_output_root"] == tmp_path
    projected = bucket_judge._gate_config(runtime)
    assert projected["child_work_meter"] is trusted_meter and projected["attempt_output_root"] == tmp_path
    assert projected["budget_tracker"] is nested_budget and projected["clock"] is nested_clock
    assert projected["provider"] == "video_understand" and projected["buckets"] == ["birds"]
    assert stage_cfg == before and nested["child_work_meter"] == "nested-untrusted"
    if projection != "direct":
        missing = {key: value for key, value in nested.items() if key not in {"budget_tracker", "clock"}}
        outer = {**runtime, "bucket_judge": missing} if projection == "bucket_judge" else {
            **runtime, "extensions": {"bucket_judge": missing},
        }
        fallback = bucket_judge._gate_config(outer)
        if projection == "bucket_judge":
            assert fallback["budget_tracker"] is service_budget and fallback["clock"] is service_clock
        else:
            assert "budget_tracker" not in fallback and "clock" not in fallback
        no_authority = bucket_judge._gate_config({projection: nested} if projection == "bucket_judge"
                                                else {"extensions": {"bucket_judge": nested}})
        assert no_authority["child_work_meter"] is None and no_authority["attempt_output_root"] is None


@pytest.mark.parametrize("route", FINITE_FILTER_ROUTES)
def test_finite_filter_confined_media_reuses_existing_producer_and_digest_child_key(tmp_path, monkeypatch, route):
    module, stage, item, cfg, decision, _ = _finite_filter_setup(tmp_path, route)
    root, work_meter = cfg["attempt_output_root"], cfg["child_work_meter"]
    media = root / "selected.mp4"
    Path(item["media_path"]).rename(media)
    item["media_path"] = str(media)
    existing = {"filename": "selected.mp4", "media_type": "video/mp4", "output_port": "video"}
    work_meter.register_local_file(media, existing, name="video")
    child, _, _ = _finite_filter_child(tmp_path, decision)
    keys = []

    def invoke(capability, **kwargs):
        assert kwargs["inputs"]["video"] == existing
        keys.append(kwargs["child_key"])
        return child

    monkeypatch.setattr(module.sdk, "invoke", invoke)

    def decide():
        if route.startswith("semantic"):
            result = stage._decision(item, cfg)
            path = module.semantic_sidecar_path(item, cfg, stage_id=stage.stage_id, repo_root=tmp_path)
        else:
            result = stage._judge_item(item, cfg, ["birds"])
            path = module.judge_sidecar_path(item, cfg, repo_root=tmp_path)
        path.unlink()
        assert result == decision

    decide()
    decide()
    assert keys[0] == keys[1] and work_meter.as_dict()["children"] == 1
    media.write_bytes(b"changed finite filter video")
    decide()
    assert keys[2] != keys[1] and work_meter.as_dict()["children"] == 2
    assert not (root / "filter-inputs/video").exists()


@pytest.mark.parametrize("route", FINITE_FILTER_ROUTES)
@pytest.mark.parametrize("problem", ["failed", "state", "identity_missing", "task", "attempt", "run",
                                    "missing", "duplicate", "size", "object_id", "media_type",
                                    "descriptor", "absolute", "traversal", "symlink", "byte_size", "byte_digest"])
def test_finite_filter_rejects_unverified_child_output_before_decision_cache(tmp_path, monkeypatch, route, problem):
    module, stage, item, cfg, decision, budget_calls = _finite_filter_setup(tmp_path, route)
    child, materializations, _ = _finite_filter_child(tmp_path, decision, problem=problem)
    monkeypatch.setattr(module.sdk, "invoke", lambda *args, **kwargs: child)
    if route.startswith("semantic"):
        result = stage.apply([item], {}, cfg)
        assert result.passed == [] and len(result.rejected) == 1
        assert result.stats["rejection_reasons"] == {stage.stage_id + "_unavailable": 1}
        sidecar = module.semantic_sidecar_path(item, cfg, stage_id=stage.stage_id, repo_root=tmp_path)
    else:
        with pytest.raises((ValueError, RuntimeError)):
            stage.apply([item], {}, cfg)
        sidecar = module.judge_sidecar_path(item, cfg, repo_root=tmp_path)
    assert not sidecar.exists() and len(budget_calls) == 1
    before_materialization = {"failed", "state", "identity_missing", "task", "attempt", "run",
                              "missing", "duplicate", "size", "object_id", "media_type"}
    if problem in before_materialization:
        assert materializations == [] and cfg["child_work_meter"].as_dict()["retained_objects"] == 0
    else:
        assert materializations == ["filter-result"] and cfg["child_work_meter"].as_dict()["retained_objects"] == 1


@pytest.mark.parametrize("route", FINITE_FILTER_ROUTES)
@pytest.mark.parametrize("problem", ["authority", "inputs", "combined_bytes", "object_bytes", "staging_symlink",
                                    "staged_bytes", "staged_same_size", "staged_symlink", "type"])
def test_finite_filter_input_boundaries_precede_child_and_model_budget(tmp_path, monkeypatch, route, problem):
    module, stage, item, cfg, _, budget_calls = _finite_filter_setup(tmp_path, route)

    def forbidden(*args, **kwargs):
        pytest.fail("invalid finite filter input invoked a child")

    monkeypatch.setattr(module.sdk, "invoke", forbidden)
    root = cfg["attempt_output_root"]
    if problem == "authority":
        cfg["child_work_meter"] = None
    elif problem == "inputs":
        cfg["child_work_meter"] = meter(max_child_inputs=1, max_derived_bytes=8192, max_child_bytes=8192)
    elif problem == "combined_bytes":
        Path(item["media_path"]).write_bytes(b"v" * 800)
        cfg["child_work_meter"] = meter(max_child_inputs=2, max_child_bytes=1024, max_derived_bytes=8192)
    elif problem == "object_bytes":
        cfg["child_work_meter"].OBJECT_MAX_BYTES = 4
    elif problem == "staging_symlink":
        (root / "filter-inputs").symlink_to(tmp_path, target_is_directory=True)
    elif problem in {"staged_bytes", "staged_same_size", "staged_symlink"}:
        original = Path(item["media_path"])
        staged = root / "filter-inputs/video" / (hashlib.sha256(original.read_bytes()).hexdigest() + ".mp4")
        staged.parent.mkdir(parents=True)
        if problem == "staged_symlink":
            staged.symlink_to(original)
        else:
            staged.write_bytes(b"wrong" if problem == "staged_bytes" else b"x" * original.stat().st_size)
    elif problem == "type":
        source = Path(item["media_path"])
        renamed = source.with_suffix(".mp3")
        source.rename(renamed)
        item["media_path"] = str(renamed)
    if route.startswith("semantic"):
        with pytest.raises((ValueError, RuntimeError)):
            stage._decision(item, cfg)
    else:
        with pytest.raises((ValueError, RuntimeError)):
            stage._judge_item(item, cfg, ["birds"])
    assert budget_calls == []
    if cfg["child_work_meter"] is not None:
        assert cfg["child_work_meter"].as_dict()["children"] == 0


@pytest.mark.parametrize("route", FINITE_FILTER_ROUTES)
@pytest.mark.parametrize("prebaked", [False, True])
def test_finite_filter_fixture_preserves_offline_decisions_without_child_authority(tmp_path, monkeypatch, route, prebaked):
    module, stage, item, cfg, decision, budget_calls = _finite_filter_setup(tmp_path, route)
    cfg.update(fixture_mode=True, child_work_meter=None, attempt_output_root=None)
    if prebaked:
        fixture = tmp_path / "fixture-decision.json"
        fixture.write_text(json.dumps(decision))
        cfg["semantic_file" if route.startswith("semantic") else "judge_file"] = str(fixture)
    else:
        decision = {"accept": True, "reason": "fixture_default", "score": 1.0}
        decision["details" if route.startswith("semantic") else "bucket"] = ({"fixture": True}
                                                                           if route.startswith("semantic") else "birds")

    def forbidden(*args, **kwargs):
        pytest.fail("fixture filter invoked a child")

    monkeypatch.setattr(module.sdk, "invoke", forbidden)
    Path(item["media_path"]).unlink()
    result = stage.apply([item], {}, cfg)
    assert len(result.passed) == 1 and result.rejected == [] and budget_calls == []
    updated = result.passed[0]
    assert (updated["filter_results"][stage.stage_id]["semantic_decision"] if route.startswith("semantic")
            else updated["judge_result"]) == decision
    assert not list(tmp_path.rglob("*.schema.json")) and not (tmp_path / "attempt/filter-inputs").exists()


@pytest.mark.parametrize("projection", ["bucket_judge", "extensions"])
def test_finite_bucket_video_nested_child_carries_schema_and_service_authority(tmp_path, monkeypatch, projection):
    from astrid.packs.training.actions.dataset_build import phases

    module, stage, item, cfg, decision, budget_calls = _finite_filter_setup(tmp_path, "bucket_video")
    work_meter, root = cfg["child_work_meter"], cfg["attempt_output_root"]
    services = DatasetRunServices(budget_tracker=cfg["budget_tracker"], child_work_meter=work_meter,
                                 attempt_output_root=root)
    nested = {**cfg, "child_work_meter": "untrusted", "attempt_output_root": "/untrusted"}
    outer = ({"bucket_judge": nested} if projection == "bucket_judge"
             else {"extensions": {"bucket_judge": nested}})
    outer.update(child_work_meter="outer-untrusted", attempt_output_root="/outer-untrusted")
    runtime = phases._stage_runtime_config(outer, services)
    child, materializations, _ = _finite_filter_child(tmp_path, decision)
    calls = []

    def invoke(capability, **kwargs):
        assert capability == "understanding.video_understand"
        assert work_meter.as_dict()["children"] == 1 and work_meter.as_dict()["registered_objects"] == 2
        schema = kwargs["inputs"]["response_schema"]
        assert schema["media_type"] == "application/json" and schema["output_port"] == "response_schema"
        assert json.loads((root / schema["filename"]).read_text())["schema"] == module.JUDGE_SCHEMA
        assert kwargs["inputs"]["max_chunks"] == 1
        calls.append(kwargs)
        return child

    monkeypatch.setattr(module.sdk, "invoke", invoke)
    result = stage.apply([item], {}, runtime)
    assert len(result.passed) == 1 and result.rejected == []
    assert len(calls) == 1 and materializations == ["filter-result"]
    assert budget_calls == ["bucket_judge.video_understand"]
    assert nested["child_work_meter"] == "untrusted" and nested["attempt_output_root"] == "/untrusted"


@pytest.mark.parametrize("route", ["semantic_video", "bucket_video"])
@pytest.mark.parametrize("start,end", [(None, None), (3, 3), (5, 3), ("2", "4")])
def test_finite_video_filter_invalid_ranges_preserve_one_window_defaults(tmp_path, monkeypatch, route, start, end):
    module, stage, item, cfg, decision, _ = _finite_filter_setup(tmp_path, route)
    item.update(clip_start_s=start, clip_end_s=end)
    child, _, _ = _finite_filter_child(tmp_path, decision)
    calls = []

    def invoke(capability, **kwargs):
        inputs = kwargs["inputs"]
        assert capability == "understanding.video_understand" and inputs["max_chunks"] == 1
        assert "start" not in inputs and "end" not in inputs and "response_schema" in inputs
        calls.append(kwargs)
        return child

    monkeypatch.setattr(module.sdk, "invoke", invoke)
    result = stage.apply([item], {}, cfg)
    assert len(result.passed) == len(calls) == 1 and result.rejected == []


@pytest.mark.parametrize("problem", ["disabled", "provider", "bucket"])
def test_finite_bucket_judge_preserves_disabled_provider_and_invalid_bucket_semantics(tmp_path, monkeypatch, problem):
    module, stage, item, cfg, decision, budget_calls = _finite_filter_setup(tmp_path, "bucket_visual")
    calls = []
    if problem == "disabled":
        cfg["enabled"] = False
    elif problem == "provider":
        cfg["provider"] = "unsupported"
    else:
        decision["bucket"] = "wrong"
    child, _, _ = _finite_filter_child(tmp_path, decision)
    monkeypatch.setattr(module.sdk, "invoke", lambda *args, **kwargs: calls.append(kwargs) or child)
    if problem == "provider":
        with pytest.raises(ValueError, match="unsupported bucket judge provider"):
            stage.apply([item], {}, cfg)
        assert calls == budget_calls == []
    else:
        result = stage.apply([item], {}, cfg)
        if problem == "disabled":
            assert len(result.passed) == 1 and result.rejected == [] and calls == budget_calls == []
            assert result.stats["warnings"] == ["bucket_judge disabled"]
        else:
            assert result.passed == [] and len(result.rejected) == 1 and len(calls) == 1
            assert result.stats["warnings"] == ["invalid_bucket:wrong"]
            assert result.stats["rejection_reasons"] == {"invalid_bucket:wrong": 1}


# Asset-bundle caller proof: synthetic bytes only; no browser or production fit.
def _review_assets_setup(tmp_path, *, count=1, **limits):
    from astrid.packs.training.actions.dataset_build import review_assets as assets
    from astrid.packs.training.actions.dataset_build.state import make_initial_state, SCHEMAS_ROOT

    root = tmp_path / "outputs"
    (root / "clips").mkdir(parents=True)
    items = []
    for index in range(count):
        media = root / "clips" / f"clip-{index} space.mp4"
        media.write_bytes(f"synthetic clip {index}".encode())
        items.append({"item_id": f"item-{index}", "source_type": "youtube",
            "source_id": f"source-{index}", "source_url": f"https://fixture.invalid/{index}",
            "acquired_at": "2026-10-05T00:00:00Z", "content_hash": hashlib.sha256(b"source provenance").hexdigest(),
            "media_type": "video", "media_path": str(media),
            "derived_from": {"source_id": "original", "transformation": "scene_extract"},
            "review_status": "rejected" if index == 1 else "pending",
            "review_sampled": {"sampled": index == 0}})
    data_path, state_path = root / "review_data.json", root / "review_state.json"
    data_path.write_text(json.dumps({"items": items}))
    state = make_initial_state(run_id="dataset", writer_id="training.dataset_build",
        config_hash="config", status="reviewing", now="2026-10-05T00:00:00Z")
    state_path.write_text(json.dumps(state))
    work_meter = meter(**{"max_derived_objects": 32, "max_derived_bytes": 4 * 1024**2,
        "max_child_inputs": 4, "max_child_bytes": 2 * 1024**2, **limits})
    arguments = {"root": root, "run_dir": root, "data_path": data_path, "state_path": state_path,
        "ui_root": Path(assets.__file__).parent / "review_ui", "schema_root": SCHEMAS_ROOT,
        "meter": work_meter}
    return assets, arguments, items, state


def _review_assets_child(arguments, initial, *, final_changes=None, problem=None):
    root = arguments["root"]
    final = copy.deepcopy(initial)
    final.update(state_version=initial["state_version"] + 2,
                 updated_at="2026-10-05T00:01:00Z", review_decisions={"item-0": {
        "item_id": "item-0", "decision": "accept", "state_version": 1,
        "reviewed_at": "2026-10-05T00:01:00Z"}})
    final.update(final_changes or {})
    payloads = {"decisions": json.dumps({"submitted_at": "2026-10-05T00:01:00Z"}).encode(),
                "state_result": json.dumps(final).encode()}
    descriptors = {port: row(payload, association="review-" + port, output_port=port,
        media_type="application/json", filename=port + ".json") for port, payload in payloads.items()}
    if problem == "wrong_attempt":
        descriptors["state_result"]["attempt_id"] = "foreign"
    materialized = []

    def materialize(association):
        materialized.append(association)
        port = next(port for port, descriptor in descriptors.items() if descriptor["association_id"] == association)
        path = root / "child-outputs" / association / (port + ".json")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payloads[port] + (b" " if problem == "changed_bytes" else b""))
        if problem == "output_symlink":
            outside = root.parent / (port + "-outside.json")
            outside.write_bytes(payloads[port])
            path.unlink()
            path.symlink_to(outside)
        descriptor = {**descriptors[port], "digest": digest(b"foreign")} if problem == "local_descriptor" else descriptors[port]
        return MaterializedChildOutput(descriptor, path.relative_to(root).as_posix())

    child = SimpleNamespace(ok=True, raw_result={"state": "completed"}, error=None,
        kernel_task_id="task", kernel_attempt_id="attempt", kernel_run_id="run",
        outputs={"managed_outputs": list(descriptors.values())}, materialize_output=materialize)
    return child, materialized


def test_review_assets_exact_bundle_projection_provenance_and_schema_identity(tmp_path):
    import zipfile

    assets, arguments, items, _ = _review_assets_setup(tmp_path, count=3)
    canonical_before = arguments["data_path"].read_bytes()
    state_before = arguments["state_path"].read_bytes()
    inputs, _ = assets.prepare_inputs(**arguments)
    assert type(inputs["timeout"]) is int and inputs["timeout"] == 0
    assert "html" not in inputs and "serve" not in inputs and "child_delegation" not in inputs
    projected_bytes = (arguments["root"] / inputs["data"]["filename"]).read_bytes()
    projected = json.loads(projected_bytes)
    with zipfile.ZipFile(arguments["root"] / inputs["assets_bundle"]["filename"]) as archive:
        assert len(archive.infolist()) == 10  # Includes both reserved directory entries.
        assert json.loads(archive.read("human-review-assets.json")) == {"html_root": "ui", "mounts": {"/media": "media"}}
        mapping = json.loads(archive.read(assets.MAP_NAME))
        assert mapping["schema"] == "training-review-media-map/v1"
        assert mapping["data"] == {"digest": digest(projected_bytes), "size": len(projected_bytes)}
        assert mapping["canonical_data"] == {"digest": digest(canonical_before), "size": len(canonical_before)}
        assert {entry["archive_member"] for entry in mapping["items"]} == {
            name for name in archive.namelist() if name.startswith("media/") and not name.endswith("/")}
        for original, projected_item, entry in zip(items, projected["items"], mapping["items"]):
            payload = Path(original["media_path"]).read_bytes()
            assert projected_item == {**original, "media_path": entry["media_path"]}
            assert entry["media_path"].startswith("/media/clip-") and "%20" in entry["media_path"]
            assert entry["item_id"] == original["item_id"]
            assert entry["source"]["source_id"] == original["source_id"]
            assert entry["source"]["derived_from"] == original["derived_from"]
            assert entry["source"]["content_hash"] == original["content_hash"]
            assert entry["digest"] == digest(payload) and entry["size"] == len(payload)
            assert archive.read(entry["archive_member"]) == payload
            assert entry["source_media_path"] == Path(original["media_path"]).relative_to(arguments["root"]).as_posix()
        assert set(archive.namelist()) == {"ui/", "media/", "human-review-assets.json", assets.MAP_NAME,
            *("ui/" + name for name in assets.UI_NAMES), *(entry["archive_member"] for entry in mapping["items"])}
    with zipfile.ZipFile(arguments["root"] / inputs["state_schema_bundle"]["filename"]) as schemas:
        assert set(schemas.namelist()) == set(assets.SCHEMA_NAMES)
        for name in assets.SCHEMA_NAMES:
            assert schemas.read(name) == (arguments["schema_root"] / name).read_bytes()
    assert arguments["data_path"].read_bytes() == canonical_before
    assert arguments["state_path"].read_bytes() == state_before
    replay, _ = assets.prepare_inputs(**arguments)
    assert replay == inputs and arguments["meter"].as_dict()["registered_objects"] == 4


@pytest.mark.parametrize("problem", ["missing", "symlink", "directory_symlink", "traversal", "foreign",
    "duplicate_id", "duplicate_member", "source_missing", "source_hash", "nonregular", "changed", "replaced"])
def test_review_assets_invalid_capture_never_submits(tmp_path, monkeypatch, problem):
    assets, arguments, items, _ = _review_assets_setup(tmp_path, count=2)
    media = Path(items[0]["media_path"])
    if problem == "missing":
        media.unlink()
    elif problem == "symlink":
        media.unlink()
        media.symlink_to(Path(items[1]["media_path"]))
    elif problem == "directory_symlink":
        clips = arguments["root"] / "clips"
        moved = arguments["root"] / "moved-clips"
        clips.rename(moved)
        clips.symlink_to(moved, target_is_directory=True)
    elif problem == "traversal":
        items[0]["media_path"] = str(media.parent / ".." / "clips" / media.name)
    elif problem == "foreign":
        foreign = tmp_path / "foreign.mp4"
        foreign.write_bytes(b"foreign")
        items[0]["media_path"] = str(foreign)
    elif problem == "duplicate_id":
        items[1]["item_id"] = items[0]["item_id"]
    elif problem == "duplicate_member":
        items[1]["media_path"] = items[0]["media_path"]
    elif problem == "source_missing":
        items[0].pop("source_id")
    elif problem == "source_hash":
        items[0]["content_hash"] = "invalid"
    elif problem == "nonregular":
        media.unlink()
        os.mkfifo(media)
    elif problem in {"changed", "replaced"}:
        inode = media.stat().st_ino
        original_read = assets.os.read
        changed = False

        def read(fd, size):
            nonlocal changed
            if not changed and os.fstat(fd).st_ino == inode:
                changed = True
                if problem == "replaced":
                    media.rename(media.with_suffix(".old"))
                media.write_bytes(b"changed capture bytes")
            return original_read(fd, size)

        monkeypatch.setattr(assets.os, "read", read)
    arguments["data_path"].write_text(json.dumps({"items": items}))
    before = arguments["state_path"].read_bytes()
    calls = []
    monkeypatch.setattr(assets.sdk, "invoke", lambda *args, **kwargs: calls.append(kwargs))
    with pytest.raises((ValueError, RuntimeError)):
        assets.run_review(**arguments, output_dir=arguments["root"], round_index=0)
    assert calls == [] and arguments["state_path"].read_bytes() == before
    assert arguments["meter"].as_dict()["children"] == 0


@pytest.mark.parametrize("axis", ["compressed", "extracted", "entries"])
def test_review_assets_receiver_limit_exact_and_next_unit_without_large_files(axis):
    from astrid.packs.training.actions.dataset_build import review_assets as assets

    values = [assets.ARCHIVE_MAX_BYTES, assets.ARCHIVE_MAX_BYTES, assets.ARCHIVE_MAX_ENTRIES]
    assets._check_archive(*values)
    values[{"compressed": 0, "extracted": 1, "entries": 2}[axis]] += 1
    with pytest.raises(ValueError, match="archive"):
        assets._check_archive(*values)


def test_review_assets_actual_archive_measurement_and_small_boundary_fakes(monkeypatch):
    import io
    import zipfile
    from astrid.packs.training.actions.dataset_build import review_assets as assets

    payload = assets._archive({"member": b"small"})
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        assert sum(info.file_size for info in archive.infolist()) == 5
        assert len(archive.infolist()) == 1
    monkeypatch.setattr(assets, "ARCHIVE_MAX_BYTES", len(payload))
    assert assets._archive({"member": b"small"}) == payload
    monkeypatch.setattr(assets, "ARCHIVE_MAX_BYTES", len(payload) - 1)
    with pytest.raises(ValueError):
        assets._archive({"member": b"small"})
    monkeypatch.setattr(assets, "ARCHIVE_MAX_BYTES", 4)
    with pytest.raises(ValueError):
        assets._archive({"member": b"small"})
    monkeypatch.setattr(assets, "ARCHIVE_MAX_BYTES", 1024)
    monkeypatch.setattr(assets, "ARCHIVE_MAX_ENTRIES", 1)
    with pytest.raises(ValueError):
        assets._archive({"member": b"small"}, directories=("reserved/",))


@pytest.mark.parametrize("problem", ["object", "count", "aggregate", "archive_count", "unknown_meter"])
def test_review_assets_input_budgets_fail_before_submit(tmp_path, monkeypatch, problem):
    assets, arguments, _, _ = _review_assets_setup(tmp_path)
    if problem == "object":
        arguments["meter"].OBJECT_MAX_BYTES = 4
    elif problem == "count":
        arguments["meter"].limits["max_child_inputs"] = 3
    elif problem == "aggregate":
        # Measure a tiny fixture once using a separate local meter, then set
        # the aggregate cap below its exact total while each object still fits.
        inputs, _ = assets.prepare_inputs(**arguments)
        sizes = [(arguments["root"] / inputs[port]["filename"]).stat().st_size
                 for port in ("assets_bundle", "data", "state", "state_schema_bundle")]
        arguments["meter"] = meter(max_derived_objects=32, max_derived_bytes=4 * 1024**2,
            max_child_inputs=4, max_child_bytes=sum(sizes) - 1)
    elif problem == "archive_count":
        monkeypatch.setattr(assets, "ARCHIVE_MAX_ENTRIES", 7)
    else:
        arguments["meter"] = None
    calls = []
    monkeypatch.setattr(assets.sdk, "invoke", lambda *args, **kwargs: calls.append(kwargs))
    with pytest.raises((ValueError, RuntimeError)):
        assets.run_review(**arguments, output_dir=arguments["root"], round_index=0)
    assert calls == []


def test_review_assets_public_zero_timeout_retry_identity_and_final_state_merge(tmp_path, monkeypatch):
    from astrid.packs.training.actions.dataset_build.state import read_review_state

    assets, arguments, _, initial = _review_assets_setup(tmp_path)
    child, materialized = _review_assets_child(arguments, initial)
    calls = []
    before = arguments["state_path"].read_bytes()

    def invoke(capability, **kwargs):
        calls.append(copy.deepcopy(kwargs))
        assert capability == "editorial.human_review" and kwargs["kind"] == "action"
        assert kwargs["wait"] is True and type(kwargs["inputs"]["timeout"]) is int
        assert kwargs["inputs"]["timeout"] == 0 and "child_delegation" not in kwargs
        assert "html" not in kwargs["inputs"] and "serve" not in kwargs["inputs"]
        assert arguments["state_path"].read_bytes() == before
        if len(calls) == 1:
            return SimpleNamespace(ok=False, error={"code": "task_wait_timeout"}, raw_result={"state": "running"},
                kernel_task_id="task", kernel_attempt_id="attempt", kernel_run_id="run")
        return child

    monkeypatch.setattr(assets.sdk, "invoke", invoke)
    assets.run_review(**arguments, output_dir=arguments["root"], round_index=0)
    assert calls[0] == calls[1] and len(calls) == 2
    assert arguments["meter"].as_dict()["children"] == 1
    assert materialized == ["review-decisions", "review-state_result"]
    state = read_review_state(arguments["state_path"])
    assert state["review_decisions"]["item-0"]["decision"] == "accept"
    assert state["submitted"] is True and state["writer_id"] == initial["writer_id"]
    assert state["state_version"] == initial["state_version"] + 3
    assert json.loads((arguments["root"] / "review_server/human_review.final.json").read_bytes()) == {
        "submitted_at": "2026-10-05T00:01:00Z"}


@pytest.mark.parametrize("problem", ["failed", "unknown", "retry_task", "retry_attempt", "retry_run", "retry_bytes",
    "wrong_attempt", "changed_bytes", "local_descriptor", "output_symlink", "owner", "config", "version", "foreign_decision"])
def test_review_assets_child_failure_or_invalid_state_preserves_parent_checkpoint(tmp_path, monkeypatch, problem):
    assets, arguments, _, initial = _review_assets_setup(tmp_path)
    changes = {"owner": {"writer_id": "foreign"}, "config": {"config_hash": "foreign"},
        "version": {"state_version": -1}, "foreign_decision": {"review_decisions": {"foreign": {
            "item_id": "foreign", "decision": "accept", "state_version": 0,
            "reviewed_at": "2026-10-05T00:01:00Z"}}}}.get(problem)
    child, _ = _review_assets_child(arguments, initial, final_changes=changes, problem=problem)
    calls = []
    if problem in {"failed", "unknown"}:
        child.ok = False
        child.error = {"code": "task_failed" if problem == "failed" else "task_status_unavailable"}
        child.raw_result = {"state": "failed"}
    if problem in {"retry_task", "retry_attempt", "retry_run"}:
        setattr(child, "kernel_" + problem.removeprefix("retry_") + "_id", "foreign")

    def invoke(capability, **kwargs):
        calls.append(kwargs)
        if problem.startswith("retry_") and len(calls) == 1:
            if problem == "retry_bytes":
                (arguments["root"] / kwargs["inputs"]["data"]["filename"]).write_bytes(b"changed")
            return SimpleNamespace(ok=False, error={"code": "task_wait_timeout"}, raw_result={"state": "running"},
                kernel_task_id="task", kernel_attempt_id="attempt", kernel_run_id="run")
        return child

    monkeypatch.setattr(assets.sdk, "invoke", invoke)
    before = arguments["state_path"].read_bytes()
    with pytest.raises((ValueError, RuntimeError)):
        assets.run_review(**arguments, output_dir=arguments["root"], round_index=0)
    assert arguments["state_path"].read_bytes() == before
    assert not (arguments["root"] / "review_server/human_review.final.json").exists()
    if problem in {"failed", "unknown", "retry_bytes"}:
        assert len(calls) == 1


@pytest.mark.parametrize("route", ["skip", "disabled", "imported"])
def test_review_assets_noninteractive_routes_admit_no_review_child(tmp_path, monkeypatch, route):
    from astrid.packs.training.actions.dataset_build import phases
    from astrid.packs.training.actions.dataset_build.services import DatasetRunServices

    assets, arguments, items, _ = _review_assets_setup(tmp_path)
    calls = []
    monkeypatch.setattr(assets.sdk, "invoke", lambda *args, **kwargs: calls.append(kwargs))
    imported = None
    if route == "imported":
        imported = arguments["root"] / "imported.json"
        imported.write_text(json.dumps({"item-0": {"decision": "accept"}}))
    services = DatasetRunServices(budget_tracker=SimpleNamespace(), child_work_meter=arguments["meter"],
        attempt_output_root=arguments["root"])
    phases._phase_human_review(arguments["root"], arguments["state_path"], arguments["data_path"], items,
        {"review": {"enabled": route != "disabled"}}, review_decisions_path=imported,
        services=services, skip_review=route == "skip", required_decision_item_ids={"item-0"})
    assert calls == [] and arguments["meter"].as_dict()["children"] == 0


def test_review_assets_production_phase_requires_trusted_root_and_custom_runner_stays_offline(tmp_path, monkeypatch):
    from astrid.packs.training.actions.dataset_build import phases
    assets, arguments, _, _ = _review_assets_setup(tmp_path)
    calls = []
    monkeypatch.setattr(assets.sdk, "invoke", lambda *args, **kwargs: calls.append(kwargs))
    with pytest.raises(ValueError, match="trusted"):
        phases._run_human_review(arguments["root"], arguments["data_path"], arguments["state_path"], subprocess.run)
    offline = []
    phases._run_human_review(arguments["root"], arguments["data_path"], arguments["state_path"],
        lambda argv, **kwargs: offline.append((argv, kwargs)))
    assert len(offline) == 1 and calls == [] and arguments["meter"].as_dict()["children"] == 0


def test_review_assets_bundle_uses_captured_bytes_without_reopening_media(tmp_path, monkeypatch):
    import zipfile

    assets, arguments, items, _ = _review_assets_setup(tmp_path)
    media = Path(items[0]["media_path"])
    captured = media.read_bytes()
    original_archive = assets._archive

    def archive(members, **kwargs):
        if assets.MAP_NAME in members:
            media.write_bytes(b"mutable source after capture")
            assert members["media/" + media.name] == captured
        return original_archive(members, **kwargs)

    monkeypatch.setattr(assets, "_archive", archive)
    inputs, _ = assets.prepare_inputs(**arguments)
    with zipfile.ZipFile(arguments["root"] / inputs["assets_bundle"]["filename"]) as bundle:
        assert bundle.read("media/" + media.name) == captured
        mapping = json.loads(bundle.read(assets.MAP_NAME))
        assert mapping["items"][0]["digest"] == digest(captured)
    assert media.read_bytes() != captured


def test_review_assets_actual_aggregate_exact_boundary_and_next_byte(tmp_path):
    assets, arguments, _, _ = _review_assets_setup(tmp_path)
    inputs, _ = assets.prepare_inputs(**arguments)
    size = sum((arguments["root"] / inputs[port]["filename"]).stat().st_size
        for port in ("assets_bundle", "data", "state", "state_schema_bundle"))
    arguments["meter"] = meter(max_derived_objects=32, max_derived_bytes=4 * 1024**2,
        max_child_inputs=4, max_child_bytes=size)
    exact, _ = assets.prepare_inputs(**arguments)
    assert exact == inputs
    arguments["meter"] = meter(max_derived_objects=32, max_derived_bytes=4 * 1024**2,
        max_child_inputs=4, max_child_bytes=size - 1)
    with pytest.raises(RuntimeError, match="aggregate"):
        assets.prepare_inputs(**arguments)


def test_review_assets_parent_checkpoint_changes_fail_closed(tmp_path, monkeypatch):
    assets, arguments, _, initial = _review_assets_setup(tmp_path)
    child, _ = _review_assets_child(arguments, initial)
    changed = {**initial, "state_version": initial["state_version"] + 1}

    def invoke(*args, **kwargs):
        arguments["state_path"].write_text(json.dumps(changed))
        return child

    monkeypatch.setattr(assets.sdk, "invoke", invoke)
    with pytest.raises(ValueError, match="checkpoint changed"):
        assets.run_review(**arguments, output_dir=arguments["root"], round_index=0)
    assert json.loads(arguments["state_path"].read_bytes()) == changed
    assert not (arguments["root"] / "review_server/human_review.final.json").exists()


def test_review_assets_trusted_root_seed_and_conflicting_injection(tmp_path, monkeypatch):
    from astrid.packs.training.actions.dataset_build import run as dataset_run

    monkeypatch.setattr(dataset_run, "preflight_delegation_budget", lambda value: {})
    root = (tmp_path / "outputs").resolve()
    parsed = SimpleNamespace(data={"output": {"run_dir": "/untrusted-config"}}, path=tmp_path / "config.json")
    services = DatasetRunServices(budget_tracker=SimpleNamespace(), attempt_output_root=tmp_path / "foreign")
    with pytest.raises(ValueError, match="conflicts"):
        dataset_run.run_pipeline(parsed, root, services=services)
    services.attempt_output_root = None

    def stop_at_state(*args, **kwargs):
        assert services.attempt_output_root == root
        assert kwargs["config"]["output"]["run_dir"] == str(root)
        raise RuntimeError("trusted root seeded")

    monkeypatch.setattr(dataset_run, "_load_or_create_review_state", stop_at_state)
    with pytest.raises(RuntimeError, match="trusted root seeded"):
        dataset_run.run_pipeline(parsed, root, services=services)


def test_review_assets_parent_recoverable_grant_is_state_result_only():
    from astrid.sdk._child_bridge import _validate_child_policy

    capability = {"capability_id": "editorial.human_review", "capability_digest": digest(b"pinned-capability")}
    policy = {"capabilities": [capability], "targets": [{"kind": "machine", "machine_id": "fixture"}],
        "input_object_ids": [], "recoverable_outputs": [{**capability, "output_ports": ["state_result"]}]}
    assert _validate_child_policy(policy)["recoverable_outputs"] == policy["recoverable_outputs"]
    for ports in (["decisions"], ["state_result", "decisions"]):
        invalid = {**policy, "recoverable_outputs": [{**capability, "output_ports": ports}]}
        with pytest.raises(Exception, match="state_result"):
            _validate_child_policy(invalid)
