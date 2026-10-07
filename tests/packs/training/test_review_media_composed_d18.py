"""Offline composition of Training custody, real Review and parent merge.

Reuse the disposable D18 realm/transport and real receiver helpers. The caller
runs in this process; the source-owned Review action runs in its normal host
subprocess. No provider, GPU, live Runtime, or acknowledged-save restart UI.
"""
from __future__ import annotations

import copy
import hashlib
import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from astrid.core.execution._child_bridge import HostChildBridge, task_resource
from astrid.core.execution.generic_host import (
    GenericPackHost, HostError, _training_review_authority,
    _training_review_media_scope,
)
from astrid.core.execution.executor.registry import ExecutorRegistry
from astrid.packs.training.actions.dataset_build import review_assets as assets
from astrid.packs.training.actions.dataset_build.state import read_review_state
from astrid.sdk._child_bridge import ChildBridge
from astrid.sdk.discovery import _capability_from_executor
from astrid.sdk.invocation import _invoke_bridge_child
from tests.core.execution.test_generic_host_child_bridge_d18 import (
    world, _real_receiver_host, _real_receiver_url, _real_review_http, _snapshot_task,
)
from tests.packs.training.test_dataset_build_child_actions import _review_assets_setup


PARENT, REVIEW = "training.dataset_build", "editorial.human_review"


def _wire_existing(world, monkeypatch):
    # Source-owned declarations, one existing D18 realm, ordinary admission.
    candidate = Path(__file__).resolve().parents[3]
    world.root = candidate / "astrid/packs/training"
    world.review_root = candidate / "astrid/packs/editorial"
    host = GenericPackHost(pack_roots=[world.root, world.review_root],
        client=world.client, executor_id="worker", max_concurrency=2)
    host.discover()
    for name in (PARENT, REVIEW):
        record = host.capabilities[name]
        world.service.register_capability({"capability_id": name,
            "definition_digest": record.capability_digest})
    world.service.register_executor({"executor_id": "worker",
        "capabilities": [PARENT, REVIEW], "max_concurrency": 2},
        idempotency_key="composed-register")
    _, arguments, items, initial = _review_assets_setup(world.tmp / "caller", count=2)
    # Representative bounded deterministic bytes: 32 KiB + 48 KiB clips.
    for index, item in enumerate(items):
        Path(item["media_path"]).write_bytes(bytes(range(256)) * (128 + index * 64))
    inputs, _ = assets.prepare_inputs(**arguments)
    sizes = {name: (arguments["root"] / inputs[name]["filename"]).stat().st_size
        for name in ("assets_bundle", "data", "state", "state_schema_bundle")}
    cap = {"capability_id": REVIEW,
        "capability_digest": host.capabilities[REVIEW].capability_digest}
    limits = {**arguments["meter"].limits, "max_child_bytes": sum(sizes.values())}
    arguments["meter"].limits["max_child_bytes"] = limits["max_child_bytes"]
    policy = {"capabilities": [cap], "targets": [{"kind": "default"}],
        "input_object_ids": [], "recoverable_outputs": [{**cap,
            "output_ports": ["state_result"]}], "limits": limits}
    target = world.identity["execution_binding"]["actual"]
    world.service.create_task({"project": world.project, "capability_id": PARENT,
        "capability_digest": host.capabilities[PARENT].capability_digest,
        "input_object_ids": [], "spec": {"inputs": {
            "config": str(arguments["root"] / "config.json"),
            "out": str(arguments["root"])}}, "child_delegation": policy,
        "execution_request": {"schema_version": 1, "target": target},
        "idempotency_key": "composed-parent"}, enforce_readiness=True)
    claim = world.service.claim_next({"executor_id": "worker",
        "capability_ids": [PARENT], "runtime_epoch": 1, "target": target},
        idempotency_key="composed-parent-claim", identity=world.identity)
    world.client._attempt_runtime_epochs[claim["attempt_id"]] = claim["runtime_epoch"]
    parent = task_resource(world.client.task(claim["task_id"]))
    b = HostChildBridge(host, parent, attempt_id=claim["attempt_id"],
        lease_id=claim["lease_id"], fence=claim["fence"], runtime_epoch=claim["runtime_epoch"],
        output_root=arguments["root"], cancelled=lambda: False)
    world.bridges.append(b)
    sdk = ChildBridge(b.child_channel)
    record = host.capabilities[REVIEW]
    registry = ExecutorRegistry([record.definition])
    capability = _capability_from_executor(record.definition, registry)
    invocations = []

    def invoke(name, **kwargs):
        assert name == REVIEW and kwargs["kind"] == "action"
        result = _invoke_bridge_child(capability, registries=(registry,),
            inputs=kwargs["inputs"], bridge=sdk, child_key=kwargs["child_key"],
            wait=True, timeout_seconds=12, poll_seconds=0.01)
        invocations.append(result)
        return result

    monkeypatch.setattr(assets.sdk, "invoke", invoke)
    return host, parent, claim, b, sdk, arguments, items, initial, sizes, invocations


@pytest.mark.parametrize("end", ["submit", "cancel"])
def test_training_review_media_custody_save_submit_merge_and_containment(world, monkeypatch, end):
    host, parent, pc, b, sdk, args, items, initial, sizes, results = _wire_existing(world, monkeypatch)
    before = args["state_path"].read_bytes()
    receiver, launched, processes = _real_receiver_host(world, monkeypatch, "composed-child")
    child_claim = []

    def serve_child():
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            with world.lock:
                queued = world.service.store.conn.execute(
                    "SELECT id FROM tasks WHERE capability=? AND status='queued' LIMIT 1", (REVIEW,)).fetchone()
                if queued:
                    c = world.service.claim_next({"executor_id": "worker", "capability_ids": [REVIEW],
                        "runtime_epoch": 1, "target": world.identity["execution_binding"]["actual"]},
                        idempotency_key="composed-child-claim", identity=world.identity)
                    child_claim.append(c)
                    world.client._attempt_runtime_epochs[c["attempt_id"]] = c["runtime_epoch"]
                    child = _snapshot_task(world, c)
                    child["execution_binding"] = c["execution_binding"]
                    break
            time.sleep(0.01)
        else:
            raise AssertionError("Training caller did not admit Review")
        return receiver.run_task({"task": child}, lease_token=c["lease_id"],
            attempt_id=c["attempt_id"], fence=c["fence"])

    with ThreadPoolExecutor(max_workers=2) as pool:
        caller = pool.submit(assets.run_review, **args, output_dir=args["root"], round_index=0)
        child_future = pool.submit(serve_child)
        try:
            url = _real_receiver_url(launched, processes, child_future)
            cc = child_claim[0]
            child = task_resource(world.client.task(cc["task_id"]))
            admission, = b.admitted_children_snapshot().children
            assert json.loads(admission.parent_context_json) == b.context
            assert json.loads(admission.task_json)["task_id"] == cc["task_id"]
            descriptors = json.loads(admission.descriptors_json)
            assert sum(row["size"] for row in descriptors) == sum(sizes.values())
            assert len(descriptors) == 4 == args["meter"].limits["max_child_inputs"]
            ports = [port for port in host.capabilities[REVIEW].definition.inputs if port.type == "file"]
            scope = _training_review_media_scope(parent, child, admission, host.client, ports)
            assert scope is not None
            accounting = _training_review_authority(host, parent, host.capabilities[PARENT], b,
                attempt_id=pc["attempt_id"], lease_id=pc["lease_id"], fence=pc["fence"],
                runtime_epoch=pc["runtime_epoch"])
            assert accounting is not None and accounting.verify()
            status, projected = _real_review_http(url, "/data.json")
            assert status == 200
            for original, projected_item in zip(items, json.loads(projected)["items"]):
                assert _real_review_http(url, projected_item["media_path"]) == (200, Path(original["media_path"]).read_bytes())
            assert _real_review_http(url, "/save", {"base_state_version": 0,
                "revisions": {"item-0": {"decision": "accept"}}})[0] == 200
            status, saved = _real_review_http(url, "/state.json")
            assert status == 200 and json.loads(saved)["state_version"] == 1
            snapshots = world.service.store.recoverable_snapshots(attempt_id=cc["attempt_id"])
            assert len(snapshots) == 1 and snapshots[0]["provenance"]["revision"] == 1
            assert args["state_path"].read_bytes() == before
            if end == "submit":
                assert _real_review_http(url, "/submit", {"decisions": json.loads(saved)["review_decisions"]}) == (204, b"")
                assert child_future.result(timeout=8)
                assert caller.result(timeout=8) is None
                assert world.service.task(cc["task_id"])["task"]["status"] == "completed"
                final = read_review_state(args["state_path"])
                assert final["submitted"] is True and final["writer_id"] == initial["writer_id"]
                assert final["state_version"] == 2 and final["review_decisions"]["item-0"]["decision"] == "accept"
                rows = results[0].outputs["managed_outputs"]
                assert {row["output_port"] for row in rows} == {"decisions", "state_result"}
                assert all((row["task_id"], row["attempt_id"], row["run_id"]) ==
                    (cc["task_id"], cc["attempt_id"], cc["run_id"]) for row in rows)
                assert args["meter"].as_dict()["retained_bytes"] == sum(row["size"] for row in rows)
            else:
                with world.lock:
                    world.service.cancel_task_canonical(pc["task_id"], {}, idempotency_key="composed-cancel-parent")
                assert child_future.result(timeout=8)["status"] == "cancelled"
                with pytest.raises(RuntimeError):
                    caller.result(timeout=8)
                with pytest.raises(HostError):
                    accounting.verify()
                assert args["state_path"].read_bytes() == before
                assert not (args["root"] / "review_server/human_review.final.json").exists()
                assert args["meter"].as_dict()["retained_objects"] == 0
                assert world.service.task(cc["task_id"])["task"]["status"] != "completed"
        finally:
            if not caller.done() or not child_future.done():
                with world.lock:
                    world.service.cancel_task_canonical(pc["task_id"], {}, idempotency_key="composed-cleanup")
                try:
                    child_future.result(timeout=8)
                except Exception:
                    pass
                try:
                    caller.result(timeout=8)
                except Exception:
                    pass
            sdk.close()
            receiver.shutdown()
            host.shutdown()
    assert args["meter"].as_dict()["children"] == 1
    assert all(process.poll() is not None for process in processes)
