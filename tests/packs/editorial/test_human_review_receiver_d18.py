from __future__ import annotations

import hashlib
import io
import json
import re
import stat
import threading
import time
import urllib.request
import zipfile
from email.message import Message
from io import BytesIO
from pathlib import Path
from types import MethodType, SimpleNamespace
from unittest.mock import patch

import pytest

from astrid.packs.editorial.actions.human_review import run


class _EventStub:
    def __init__(self) -> None:
        self.was_set = False

    def set(self) -> None:
        self.was_set = True


class _Request:
    def __init__(self, handler_cls: type, method: str, path: str, *, body: object | bytes | None = None, token: str | None = None):
        payload = b"" if body is None else body if isinstance(body, bytes) else json.dumps(body).encode()
        self.request = handler_cls.__new__(handler_cls)
        self.request.path = path
        self.request.headers = Message()
        self.request.headers["Content-Length"] = str(len(payload))
        if token is not None:
            self.request.headers["X-Session-Token"] = token
        self.request.rfile = BytesIO(payload)
        self.request.wfile = BytesIO()
        self.request.status = None
        self.request.response_headers = []

        def send_response(request, status: int) -> None:
            request.status = status

        def send_header(request, key: str, value: str) -> None:
            request.response_headers.append((key, value))

        def end_headers(request) -> None:
            return None

        self.request.send_response = MethodType(send_response, self.request)
        self.request.send_header = MethodType(send_header, self.request)
        self.request.end_headers = MethodType(end_headers, self.request)
        getattr(self.request, f"do_{method}")()

    @property
    def result(self) -> tuple[int, bytes]:
        assert self.request.status is not None
        return self.request.status, self.request.wfile.getvalue()


def _state() -> dict:
    return run.make_initial_state(
        run_id="run-1",
        writer_id="writer-1",
        buckets={"bucket": 1},
        now="2026-10-03T00:00:00Z",
    )


def _handler(tmp_path: Path, *, bridge=None, state_validator=None):
    html = tmp_path / "index.html"
    data = tmp_path / "data.json"
    state = tmp_path / "state.json"
    out = tmp_path / "out" / "human_review.final.json"
    html.write_text("<html>review</html>", encoding="utf-8")
    data.write_text(json.dumps({"items": [{"item_id": "item-1"}]}), encoding="utf-8")
    run._commit_state(state, _state())
    event = _EventStub()
    handler = run.make_handler_class(
        html_path=html,
        data_path=data,
        state_path=state,
        out_path=out,
        schema_path=None,
        mounts={},
        token="token",
        shutdown_event=event,
        state_validator=state_validator,
        state_result_path=out.parent / "state_result.json",
    )
    return handler, state, out, event

def _snapshot_receipt(
    data: bytes,
    revision: int,
    *,
    output_port: str = "state_result",
    durability: str = "durable",
) -> dict[str, object]:
    return {
        "association_id": "association",
        "receipt_id": "receipt",
        "revision": revision,
        "output_port": output_port,
        "digest": "sha256:" + hashlib.sha256(data).hexdigest(),
        "size": len(data),
        "durability": durability,
    }


def _zip(path: Path, files: dict[str, bytes], *, symlink: str | None = None) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        for name, data in files.items():
            archive.writestr(name, data)
        if symlink is not None:
            info = zipfile.ZipInfo(symlink)
            info.create_system = 3
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            archive.writestr(info, b"target")


def _asset_files() -> dict[str, bytes]:
    return {
        "human-review-assets.json": json.dumps(
            {"html_root": "html", "mounts": {"/clips": "clips", "/frames": "frames"}}
        ).encode(),
        "html/index.html": b"<html>bundle</html>",
        "clips/clip.mp4": b"0123456789",
        "frames/frame.png": b"png-bytes",
    }


def test_asset_bundle_extracts_html_and_two_mounts_and_cleans(tmp_path: Path) -> None:
    archive = tmp_path / "assets.zip"
    _zip(archive, _asset_files())
    html, mounts, scratch = run._extract_assets_bundle(archive, tmp_path)
    try:
        assert html.is_dir()
        assert (html / "index.html").read_bytes() == b"<html>bundle</html>"
        assert mounts["/clips"].joinpath("clip.mp4").read_bytes() == b"0123456789"
        assert mounts["/frames"].joinpath("frame.png").read_bytes() == b"png-bytes"
        scratch_path = Path(scratch.name)
    finally:
        scratch.cleanup()
    assert not scratch_path.exists()


@pytest.mark.parametrize(
    "manifest, member_name",
    [
        ({"html_root": "../html", "mounts": {}}, "html/index.html"),
        ({"html_root": "html", "mounts": {"/../clips": "clips"}}, "clips/a"),
        ({"html_root": "html", "mounts": {"/clips": "html"}}, "html/index.html"),
    ],
)
def test_asset_bundle_rejects_unsafe_or_duplicate_destinations(
    tmp_path: Path, manifest: dict, member_name: str
) -> None:
    archive = tmp_path / "bad.zip"
    files = {
        "human-review-assets.json": json.dumps(manifest).encode(),
        "html/index.html": b"x",
        member_name: b"x",
    }
    _zip(archive, files)
    with pytest.raises(run.HumanReviewInputError):
        run._extract_assets_bundle(archive, tmp_path)


def test_asset_bundle_rejects_symlinks(tmp_path: Path) -> None:
    archive = tmp_path / "symlink.zip"
    _zip(archive, _asset_files(), symlink="html/link")
    with pytest.raises(run.HumanReviewInputError, match="symlink"):
        run._read_bounded_zip(archive)


def test_schema_bundle_resolves_only_declared_m17_references(tmp_path: Path) -> None:
    schema_root = Path("astrid/packs/training/actions/dataset_build/schemas")
    bundle = tmp_path / "schemas.zip"
    with zipfile.ZipFile(bundle, "w") as archive:
        for name in run._STATE_SCHEMA_FILES:
            archive.writestr(name, (schema_root / name).read_bytes())
    validator = run._load_state_schema_bundle(bundle, "run-state.schema.json")
    validator.validate(_state())
    invalid = dict(_state(), state_version="not-an-integer")
    with pytest.raises(run.ReviewStateError):
        validator.validate(invalid)


def test_schema_bundle_rejects_external_reference_and_wrong_entry(tmp_path: Path) -> None:
    bundle = tmp_path / "bad-schemas.zip"
    schemas = {
        name: {"$schema": "http://json-schema.org/draft-07/schema#", "$id": name, "type": "object"}
        for name in run._STATE_SCHEMA_FILES
    }
    schemas["run-state.schema.json"]["properties"] = {"x": {"$ref": "https://evil.invalid/state.json"}}
    with zipfile.ZipFile(bundle, "w") as archive:
        for name, schema in schemas.items():
            archive.writestr(name, json.dumps(schema))
    with pytest.raises(run.HumanReviewInputError, match="external or unresolved"):
        run._load_state_schema_bundle(bundle, "run-state.schema.json")
    with pytest.raises(run.HumanReviewInputError, match="missing"):
        run._load_state_schema_bundle(bundle, "missing.schema.json")


def test_direct_file_repeated_mount_and_range_behavior_is_preserved(tmp_path: Path) -> None:
    handler, _, _, _ = _handler(tmp_path)
    media = tmp_path / "media"
    media.mkdir()
    (media / "clip.mp4").write_bytes(b"0123456789")
    handler = run.make_handler_class(
        html_path=tmp_path / "index.html",
        data_path=tmp_path / "data.json",
        state_path=None,
        out_path=tmp_path / "out.json",
        schema_path=None,
        mounts={"/media": media, "/other": media},
        token="token",
        shutdown_event=_EventStub(),
    )
    status, body = _Request(handler, "GET", "/", token="token").result
    assert status == 200 and body == b"<html>review</html>"
    request = _Request(handler, "GET", "/media/clip.mp4", token="token")
    assert request.result == (200, b"0123456789")
    request = _Request(handler, "GET", "/other/clip.mp4", token="token")
    assert request.result == (200, b"0123456789")
    request = handler.__new__(handler)
    request.path = "/media/clip.mp4"
    request.headers = Message()
    request.headers["Range"] = "bytes=2-5"
    request.rfile = BytesIO()
    request.wfile = BytesIO()
    request.status = None
    request.response_headers = []
    request.send_response = MethodType(lambda self, status: setattr(self, "status", status), request)
    request.send_header = MethodType(lambda self, key, value: self.response_headers.append((key, value)), request)
    request.end_headers = MethodType(lambda self: None, request)
    request.do_GET()
    assert request.status == 206 and request.wfile.getvalue() == b"2345"


def test_static_mount_decodes_encoded_filename_exactly_once(tmp_path: Path) -> None:
    handler, _, _, _ = _handler(tmp_path)
    media = tmp_path / "media"
    media.mkdir()
    (media / "clip-0 space.mp4").write_bytes(b"space-clip")
    (media / "clip%20literal.mp4").write_bytes(b"literal-percent-clip")
    handler = run.make_handler_class(
        html_path=tmp_path / "index.html",
        data_path=tmp_path / "data.json",
        state_path=None,
        out_path=tmp_path / "out.json",
        schema_path=None,
        mounts={"/media": media},
        token="token",
        shutdown_event=_EventStub(),
    )
    assert _Request(handler, "GET", "/media/clip-0%20space.mp4").result == (200, b"space-clip")
    assert _Request(handler, "GET", "/media/clip%2520literal.mp4").result == (200, b"literal-percent-clip")


@pytest.mark.parametrize(
    "relative",
    ["%2e%2e/outside.mp4", "%2e%2e%2foutside.mp4", "%2F{absolute}", "escape.mp4"],
)
def test_static_mount_rejects_encoded_and_symlink_path_escape(tmp_path: Path, relative: str) -> None:
    handler, _, _, _ = _handler(tmp_path)
    media = tmp_path / "media"
    media.mkdir()
    outside = tmp_path / "outside.mp4"
    outside.write_bytes(b"outside-secret")
    (media / "escape.mp4").symlink_to(outside)
    handler = run.make_handler_class(
        html_path=tmp_path / "index.html",
        data_path=tmp_path / "data.json",
        state_path=None,
        out_path=tmp_path / "out.json",
        schema_path=None,
        mounts={"/media": media},
        token="token",
        shutdown_event=_EventStub(),
    )
    relative = relative.format(absolute=outside.as_posix().lstrip("/").replace("/", "%2F"))
    assert _Request(handler, "GET", "/media/" + relative).result == (403, b"Forbidden (path escape)")


def test_state_save_is_atomic_and_durable_receipt_precedes_http_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, int, bytes]] = []
    state_path = tmp_path / "state.json"

    class Bridge:
        def publish_snapshot(self, *, filename: str, output_port: str, revision: int):
            calls.append((output_port, revision, (tmp_path / "out" / filename).read_bytes()))
            data = calls[-1][2]
            return {
                "association_id": "a",
                "receipt_id": "r",
                "revision": revision,
                "output_port": output_port,
                "digest": "sha256:" + __import__("hashlib").sha256(data).hexdigest(),
                "size": len(data),
                "durability": "durable",
            }

    handler, state_path, _, _ = _handler(tmp_path, bridge=Bridge())
    import astrid.sdk._child_bridge as child_bridge
    monkeypatch.setattr(child_bridge, "_bridge", Bridge())
    body = {"base_state_version": 0, "revisions": [{"item_id": "item-1", "decision": "accept"}]}
    status, response = _Request(handler, "POST", "/save", body=body, token="token").result
    assert status == 200
    assert json.loads(response)["state_version"] == 1
    assert state_path.read_bytes() == calls[-1][2]


def test_publication_failure_or_uncertain_receipt_is_not_acknowledged(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    class BrokenBridge:
        def publish_snapshot(self, **kwargs):
            raise RuntimeError("lost response")

    handler, state_path, _, _ = _handler(tmp_path)
    import astrid.sdk._child_bridge as child_bridge
    monkeypatch.setattr(child_bridge, "_bridge", BrokenBridge())
    original = state_path.read_bytes()
    status, response = _Request(
        handler,
        "POST",
        "/save",
        body={"base_state_version": 0, "revisions": [{"item_id": "item-1", "decision": "accept"}]},
        token="token",
    ).result
    assert status == 503
    assert json.loads(response)["error"] == "save_unpublished"
    assert state_path.read_bytes() == original

def test_managed_submit_batch_publishes_exact_state_before_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[str, str, int, bytes]] = []

    class Bridge:
        def publish_snapshot(self, *, filename: str, output_port: str, revision: int):
            data = (tmp_path / "out" / filename).read_bytes()
            calls.append((filename, output_port, revision, data))
            return _snapshot_receipt(data, revision, output_port=output_port)

    handler, state_path, _, _ = _handler(tmp_path)
    import astrid.sdk._child_bridge as child_bridge
    monkeypatch.setattr(child_bridge, "_bridge", Bridge())
    body = {
        "base_state_version": 0,
        "item_ids": ["item-1"],
        "decision": "accept",
        "reject_reason": "not-used",
        "edited_caption": "accepted caption",
    }

    status, response = _Request(handler, "POST", "/submit-batch", body=body, token="token").result

    assert status == 200
    assert json.loads(response)["state_version"] == 1
    assert calls and calls[0][:3] == ("state_result.json", "state_result", 1)
    assert state_path.read_bytes() == calls[0][3]
    assert (tmp_path / "out" / "state_result.json").read_bytes() == calls[0][3]
    assert json.loads(state_path.read_text(encoding="utf-8"))["review_decisions"]["item-1"] == {
        "decision": "accept",
        "edited_caption": "accepted caption",
        "item_id": "item-1",
        "reject_reason": "not-used",
        "reviewer_id": "human_review_batch",
        "reviewed_at": json.loads(state_path.read_text(encoding="utf-8"))["review_decisions"]["item-1"]["reviewed_at"],
        "state_version": 1,
    }


@pytest.mark.parametrize("failure", ["refused", "uncertain", "mismatched", "non_durable"])
def test_managed_submit_batch_publication_failures_do_not_acknowledge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    class Bridge:
        def publish_snapshot(self, *, filename: str, output_port: str, revision: int):
            data = (tmp_path / "out" / filename).read_bytes()
            if failure == "refused":
                raise RuntimeError("publication refused")
            if failure == "uncertain":
                return None
            if failure == "mismatched":
                return _snapshot_receipt(data, revision + 1, output_port=output_port)
            return _snapshot_receipt(data, revision, output_port=output_port, durability="ephemeral")

    handler, state_path, _, _ = _handler(tmp_path)
    import astrid.sdk._child_bridge as child_bridge
    monkeypatch.setattr(child_bridge, "_bridge", Bridge())
    original = state_path.read_bytes()

    status, response = _Request(
        handler,
        "POST",
        "/submit-batch",
        body={"base_state_version": 0, "item_ids": ["item-1"], "decision": "accept"},
        token="token",
    ).result

    assert status == 503
    assert json.loads(response)["error"] == "batch_unpublished"
    assert state_path.read_bytes() == original
    assert not (tmp_path / "out" / "state_result.json").exists()


def test_managed_submit_batch_stale_revision_does_not_publish(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[int] = []

    class Bridge:
        def publish_snapshot(self, *, filename: str, output_port: str, revision: int):
            calls.append(revision)
            data = (tmp_path / "out" / filename).read_bytes()
            return _snapshot_receipt(data, revision, output_port=output_port)

    handler, state_path, _, _ = _handler(tmp_path)
    import astrid.sdk._child_bridge as child_bridge
    monkeypatch.setattr(child_bridge, "_bridge", Bridge())
    original = state_path.read_bytes()

    status, response = _Request(
        handler,
        "POST",
        "/submit-batch",
        body={"base_state_version": 99, "item_ids": ["item-1"], "decision": "accept"},
        token="token",
    ).result

    assert status == 409
    assert json.loads(response)["error"] == "stale_state"
    assert calls == []
    assert state_path.read_bytes() == original


def test_standalone_submit_batch_commits_locally(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    handler, state_path, _, _ = _handler(tmp_path)
    import astrid.sdk._child_bridge as child_bridge
    monkeypatch.setattr(child_bridge, "_bridge", None)

    status, response = _Request(
        handler,
        "POST",
        "/submit-batch",
        body={"base_state_version": 0, "item_ids": ["item-1"], "decision": "reject"},
        token="token",
    ).result

    assert status == 200
    assert json.loads(response)["state_version"] == 1
    updated = json.loads(state_path.read_text(encoding="utf-8"))
    assert updated["state_version"] == 1
    assert updated["review_decisions"]["item-1"]["decision"] == "reject"
    assert not (tmp_path / "out" / "state_result.json").exists()


def test_managed_save_then_submit_batch_publishes_ordered_cumulative_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "data.json").write_text(
        json.dumps({"items": [{"item_id": "item-1"}, {"item_id": "item-2"}]}),
        encoding="utf-8",
    )
    calls: list[tuple[int, bytes]] = []

    class Bridge:
        def publish_snapshot(self, *, filename: str, output_port: str, revision: int):
            data = (tmp_path / "out" / filename).read_bytes()
            calls.append((revision, data))
            return _snapshot_receipt(data, revision, output_port=output_port)

    handler, state_path, _, _ = _handler(tmp_path)
    import astrid.sdk._child_bridge as child_bridge
    monkeypatch.setattr(child_bridge, "_bridge", Bridge())

    save_status, _ = _Request(
        handler,
        "POST",
        "/save",
        body={"base_state_version": 0, "revisions": [{"item_id": "item-1", "decision": "accept"}]},
        token="token",
    ).result
    batch_status, batch_response = _Request(
        handler,
        "POST",
        "/submit-batch",
        body={
            "base_state_version": 1,
            "item_ids": ["item-2"],
            "decision": "reject",
            "reject_reason": "bad framing",
            "edited_caption": "rejected caption",
        },
        token="token",
    ).result

    assert save_status == 200
    assert batch_status == 200
    assert json.loads(batch_response)["state_version"] == 2
    assert [revision for revision, _ in calls] == [1, 2]
    final_state = json.loads(state_path.read_text(encoding="utf-8"))
    assert final_state["state_version"] == 2
    assert final_state["review_decisions"]["item-1"]["decision"] == "accept"
    assert final_state["review_decisions"]["item-2"] == {
        "decision": "reject",
        "edited_caption": "rejected caption",
        "item_id": "item-2",
        "reject_reason": "bad framing",
        "reviewer_id": "human_review_batch",
        "reviewed_at": final_state["review_decisions"]["item-2"]["reviewed_at"],
        "state_version": 2,
    }
    assert (tmp_path / "out" / "state_result.json").read_bytes() == calls[-1][1] == state_path.read_bytes()


def test_state_result_no_open_and_asset_scratch_cleanup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    archive = tmp_path / "assets.zip"
    _zip(archive, _asset_files())
    data = tmp_path / "data.json"
    data.write_text("[]", encoding="utf-8")
    state = tmp_path / "state.json"
    run._commit_state(state, _state())
    out = tmp_path / "out" / "human_review.final.json"
    output = io.StringIO()
    opened: list[str] = []
    result: dict[str, object] = {}

    def runner() -> None:
        with patch.object(run, "webbrowser", SimpleNamespace(open=lambda url: opened.append(url))), patch("sys.stdout", output):
            result["code"] = run.main([
                "--data", str(data), "--assets-bundle", str(archive), "--state", str(state),
                "--out", str(out), "--no-open", "--timeout", "5",
            ])

    thread = threading.Thread(target=runner)
    thread.start()
    url = None
    for _ in range(100):
        match = re.search(r"http://127\.0\.0\.1:\d+/\?token=\w+", output.getvalue())
        if match:
            url = match.group(0)
            break
        time.sleep(0.02)
    assert url is not None
    with urllib.request.urlopen(url) as response:
        assert response.read() == b"<html>bundle</html>"
    token = url.rsplit("token=", 1)[1]
    request = urllib.request.Request(
        url.split("/?", 1)[0] + "/submit?token=" + token,
        data=b"{}",
        method="POST",
        headers={"Content-Length": "2"},
    )
    with urllib.request.urlopen(request) as response:
        assert response.status == 204
    thread.join(timeout=5)
    assert result["code"] == 0
    assert opened == []
    assert json.loads(out.read_text(encoding="utf-8")) == {}
    assert json.loads((out.parent / "state_result.json").read_text(encoding="utf-8"))["run_id"] == "run-1"
    assert not list(tmp_path.glob(".human-review-assets-*"))


def _managed_file(filename: str, digit: str) -> dict[str, str]:
    digest = "sha256:" + digit * 64
    return {"object_id": digest, "digest": digest, "filename": filename}


def _invoke_public_human_review_child(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, inputs: dict[str, object]
):
    from astrid import sdk
    import astrid.sdk._child_bridge as child_bridge
    from astrid.core.pack import discovery as pack_discovery
    from astrid.core.pack.discovery import DiscoveredPack
    from astrid.core.pack.loader import load_pack_manifest
    from astrid.core.execution.executor.actions import action_executor_definition
    from astrid.core.execution.executor.registry import ExecutorRegistry
    from astrid.core.execution.orchestrator.registry import OrchestratorRegistry

    monkeypatch.setenv("ASTRID_SOURCE_STATE", str(tmp_path / "absent-source-state.json"))
    monkeypatch.delenv("ASTRID_PACKS_PATH", raising=False)
    pack_root = Path(__file__).resolve().parents[3] / "astrid/packs/editorial"
    pack = load_pack_manifest(pack_root / "pack.yaml")
    discovered = DiscoveredPack(pack, "source", 0)
    definition = action_executor_definition(discovered, "human_review", pack.actions["human_review"])
    executors = ExecutorRegistry((definition,))
    orchestrators = OrchestratorRegistry(executor_registry=executors)
    monkeypatch.setattr(pack_discovery, "discover_pack_metadata", lambda **kwargs: (discovered,))
    monkeypatch.setattr(sdk, "_load_registries", lambda **kwargs: (executors, orchestrators, None))

    requests: list[dict[str, object]] = []

    class Bridge:
        def submit(self, request: dict[str, object]) -> dict[str, str]:
            requests.append(request)
            return {"task_id": "m04-child-task", "run_id": "m04-child-run"}

    monkeypatch.setattr(child_bridge, "_bridge", Bridge())
    result = sdk.invoke(
        "editorial.human_review",
        kind="action",
        extra_pack_roots=(str(pack_root),),
        inputs=inputs,
    )
    assert result.ok
    assert len(requests) == 1
    return requests[0]


def test_public_child_omission_does_not_create_sentinel_file_authority(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    from astrid import sdk

    request = _invoke_public_human_review_child(
        monkeypatch,
        tmp_path,
        {
            "html": {"filename": "review.html", "media_type": "text/html", "output_port": "session_html"},
            "data": _managed_file("data.json", "a"),
        },
    )
    pack_root = Path(__file__).resolve().parents[3] / "astrid/packs/editorial"
    capability = sdk.get_capability(
        "editorial.human_review", kind="action", extra_pack_roots=(str(pack_root),)
    )
    file_ports = {port.name: port for port in capability.inputs if port.type == "file"}
    optional_names = {"state", "assets_bundle", "state_schema_bundle", "response_schema"}
    assert all(name in file_ports and not file_ports[name].required for name in optional_names)
    assert all(file_ports[name].default is None for name in optional_names)

    assert request["inputs"]["serve"] == "__none__"  # preserve the string sentinel contract
    assert not optional_names.intersection(request["inputs"])
    assert {row["name"] for row in request["input_descriptors"]} == {"html", "data"}
    assert {row["name"]: row["kind"] for row in request["input_descriptors"]} == {
        "html": "producer_file", "data": "object"
    }


def test_public_child_preserves_supplied_managed_and_producer_file_descriptors(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    inputs: dict[str, object] = {
        "html": {"filename": "review.html", "media_type": "text/html", "output_port": "session_html"},
        "data": _managed_file("data.json", "a"),
        "state": {"filename": "state.json", "media_type": "application/json", "output_port": "state_result"},
        "assets_bundle": _managed_file("assets.zip", "b"),
        "state_schema_bundle": _managed_file("state-schemas.zip", "c"),
        "response_schema": _managed_file("response-schema.json", "d"),
    }
    request = _invoke_public_human_review_child(monkeypatch, tmp_path, inputs)

    assert request["inputs"] == {**inputs, "serve": "__none__", "state_schema_entry": "run-state.schema.json",
                                 "port": 0, "no_open": False, "timeout": 0}
    descriptors = {row["name"]: row for row in request["input_descriptors"]}
    assert descriptors["state"] == {
        "name": "state", "kind": "producer_file", **inputs["state"]
    }
    for name in ("assets_bundle", "state_schema_bundle", "response_schema"):
        assert descriptors[name] == {"name": name, "kind": "object", "object_id": inputs[name]["digest"]}


@pytest.mark.parametrize("name", ["state", "assets_bundle", "state_schema_bundle", "response_schema"])
def test_public_child_rejects_unmanaged_optional_file_strings(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, name: str,
) -> None:
    from astrid.sdk import CapabilityValidationError

    inputs: dict[str, object] = {
        "html": {"filename": "review.html", "media_type": "text/html", "output_port": "session_html"},
        "data": _managed_file("data.json", "a"),
        name: "caller-local/path.json",
    }
    with pytest.raises(CapabilityValidationError, match="managed Runtime object"):
        _invoke_public_human_review_child(monkeypatch, tmp_path, inputs)


def test_optional_file_cli_omission_and_legacy_sentinel_are_preserved() -> None:
    args = run.build_parser().parse_args(["--data", "data.json", "--out", "result.json"])
    for name in ("state", "assets_bundle", "state_schema_bundle", "response_schema"):
        assert getattr(args, name) is None
    legacy = run.build_parser().parse_args(
        ["--data", "data.json", "--out", "result.json", "--state", "__none__",
         "--assets-bundle", "__none__", "--state-schema-bundle", "__none__", "--response-schema", "__none__"]
    )
    for name in ("state", "assets_bundle", "state_schema_bundle", "response_schema"):
        assert run._optional_path(getattr(legacy, name)) is None
    assert run._parse_mounts(["__none__"]) == {}
