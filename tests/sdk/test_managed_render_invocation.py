from __future__ import annotations

from types import SimpleNamespace

import pytest

from astrid.sdk import invocation
import astrid.core.rendering.storage as storage_module


VIDEO_DIGEST = "sha256:" + "d" * 64


def _filmstrip_invoke_fixtures(monkeypatch, authority):
    capability = SimpleNamespace(
        id="rendering.timeline_visualize",
        capability_type="executor",
        native_kind="built_in",
        inputs=(),
        definition={"id": "rendering.timeline_visualize", "metadata": {}},
    )
    executor_definition = SimpleNamespace(to_dict=lambda: {"id": capability.id})
    fake_sdk = SimpleNamespace(
        _load_registries=lambda **_: ({capability.id: executor_definition}, None, None),
        get_capability=lambda *_, **__: capability,
    )
    monkeypatch.setattr(invocation, "_sdk_module", lambda: fake_sdk)
    monkeypatch.setattr(invocation, "_validate_timeline_visualize_inputs", lambda *_, **__: authority)
    return capability


def test_private_filmstrip_backend_preflight_enrichment_reaches_kernel(monkeypatch):
    authority = {
        "mode": "filmstrip",
        "video_object_id": VIDEO_DIGEST,
        "video_digest": VIDEO_DIGEST,
        "filmstrip_snapshot": {"project_slug": "project-1"},
    }
    _filmstrip_invoke_fixtures(monkeypatch, authority)
    captured = {}

    def fake_kernel(capability, **kwargs):
        captured.update(kwargs)
        return "run-1", "task-1", "attempt-1", None, {"ok": True}, True, None

    monkeypatch.setattr(invocation, "_kernel_invoke", fake_kernel)
    result = invocation._invoke_internal_result(
        "rendering.timeline_visualize",
        kind="executor",
        project="project-1",
        inputs={"view": "filmstrip"},
        client=SimpleNamespace(),
    )

    assert result.ok
    assert captured["inputs"]["rendered_video"] == {
        "digest": VIDEO_DIGEST,
        "object_id": VIDEO_DIGEST,
    }
    assert "filmstrip_authority" in captured["inputs"]
    assert captured["idempotency_context"]["video_object_id"] == VIDEO_DIGEST


def test_private_backend_input_only_preflight_forwards_snapshot_without_video(monkeypatch):
    authority = {
        "mode": "input_only",
        "input_snapshot": {"project_slug": "project-1"},
    }
    _filmstrip_invoke_fixtures(monkeypatch, authority)
    captured = {}

    def fake_kernel(capability, **kwargs):
        captured.update(kwargs)
        return "run-1", "task-1", "attempt-1", None, {"ok": True}, True, None

    monkeypatch.setattr(invocation, "_kernel_invoke", fake_kernel)
    result = invocation._invoke_internal_result(
        "rendering.timeline_visualize",
        kind="executor",
        project="project-1",
        inputs={"view": "filmstrip", "hide": ["output"]},
        client=SimpleNamespace(),
    )

    assert result.ok
    assert captured["inputs"]["project_slug"] == "project-1"
    assert "rendered_video" not in captured["inputs"]
    assert "filmstrip_authority" in captured["inputs"]


def test_private_filmstrip_backend_keeps_strict_video_identity_rejection(monkeypatch):
    authority = {
        "mode": "filmstrip",
        "video_object_id": VIDEO_DIGEST,
        "video_digest": "sha256:" + "e" * 64,
        "filmstrip_snapshot": {"project_slug": "project-1"},
    }
    _filmstrip_invoke_fixtures(monkeypatch, authority)

    result = invocation._invoke_internal_result(
        "rendering.timeline_visualize",
        kind="executor",
        project="project-1",
        inputs={"view": "filmstrip"},
        client=SimpleNamespace(),
    )

    assert not result.ok
    assert result.error["type"] == "CapabilityValidationError"
    assert "identity mismatch" in result.error["message"]


def test_managed_render_snapshot_is_forwarded_to_runtime_task(monkeypatch) -> None:
    """The post-preflight snapshot must reach Runtime admission."""

    capability = SimpleNamespace(
        id="rendering.render",
        capability_type="executor",
        native_kind="built_in",
        inputs=(),
        definition={"id": "rendering.render", "metadata": {}},
    )
    executor_definition = SimpleNamespace(to_dict=lambda: {"id": "rendering.render"})
    fake_sdk = SimpleNamespace(
        _load_registries=lambda **_: ({"rendering.render": executor_definition}, None, None),
        get_capability=lambda *_, **__: capability,
    )
    monkeypatch.setattr(invocation, "_sdk_module", lambda: fake_sdk)

    snapshot = {
        "timeline_id": "timeline-1",
        "project_id": "project-1",
        "config_version": 2,
        "config": {"clips": []},
        "registry": {"assets": {}},
    }
    prepared = {
        "timeline_ref": "timeline-1",
        "timeline_snapshot": snapshot,
        "timeline_authority": {"timeline_id": "timeline-1", "config_version": 2},
    }
    monkeypatch.setattr(
        invocation,
        "_prepare_managed_render_inputs",
        lambda inputs, **_: (prepared, {"timeline_id": "timeline-1"}),
    )

    monkeypatch.setattr(storage_module, "managed_object_sizes", lambda *_: {})
    monkeypatch.setattr(storage_module, "used_effect_asset_sizes", lambda *_: {})
    monkeypatch.setattr(
        storage_module,
        "estimate_managed_render_storage",
        lambda **_: {"estimated_scratch_bytes": 0, "estimated_output_bytes": 0},
    )

    captured: dict[str, object] = {}

    def fake_kernel(capability, **kwargs):
        captured.update(kwargs)
        return "run-1", "task-1", "attempt-1", None, {"ok": True}, True, None

    monkeypatch.setattr(invocation, "_kernel_invoke", fake_kernel)

    client = SimpleNamespace(
        media=SimpleNamespace(list=lambda *_args, **_kwargs: [[{"digest": "sha256:" + "a" * 64, "size": 1}], None])
    )
    result = invocation.invoke(
        "rendering.render",
        kind="executor",
        project="project-1",
        inputs={"timeline_ref": "timeline-1"},
        client=client,
    )

    assert result.ok
    assert captured["inputs"] == prepared


def _file_render_invoke_fixture(monkeypatch):
    definition = {"id": "rendering.render", "metadata": {}}
    capability = SimpleNamespace(
        id="rendering.render", capability_type="executor", native_kind="built_in",
        definition=definition,
        inputs=tuple(SimpleNamespace(name=name, type="file", default=None)
                     for name in ("timeline", "assets_registry")),
    )
    executor = SimpleNamespace(to_dict=lambda: definition)
    monkeypatch.setattr(invocation, "_sdk_module", lambda: SimpleNamespace(
        _load_registries=lambda **_: ({capability.id: executor}, None, None),
        get_capability=lambda *_, **__: capability,
    ))
    captured = {}

    def create_task(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(ok=True, data={
            "run_id": "run-file", "task_id": "task-file", "attempt_id": "attempt-file",
        })

    def unexpected_runtime_read(*_args, **_kwargs):
        pytest.fail("file render admission must not resolve scope, list media, or fetch JSON bytes")

    client = SimpleNamespace(
        tasks=SimpleNamespace(create=create_task),
        timelines=SimpleNamespace(resolve_scope=unexpected_runtime_read),
        projects=SimpleNamespace(show=unexpected_runtime_read),
        media=SimpleNamespace(list=unexpected_runtime_read),
        get_object=unexpected_runtime_read,
    )
    return client, captured


@pytest.mark.parametrize("with_registry", [False, True])
def test_public_file_render_uses_existing_cas_admission_without_canonical_snapshot(monkeypatch, with_registry):
    client, captured = _file_render_invoke_fixture(monkeypatch)
    timeline_digest = "sha256:" + "a" * 64
    registry_digest = "sha256:" + "b" * 64
    inputs = {"timeline": {"digest": timeline_digest, "object_id": timeline_digest,
                           "filename": "timeline.json"}, "output_name": "iteration.mp4"}
    if with_registry:
        inputs["assets_registry"] = {"digest": registry_digest, "object_id": registry_digest,
                                     "filename": "assets.json"}
    result = invocation.invoke(
        "rendering.render", kind="executor", project="demo", inputs=inputs, client=client,
    )
    assert result.ok
    spec = captured["spec"]
    assert spec["inputs"] == inputs
    assert set(captured["input_manifest"]) == ({timeline_digest, registry_digest} if with_registry else {timeline_digest})
    assert {item["name"]: item["digest"] for item in spec["input_digests"]} == (
        {"timeline": timeline_digest, "assets_registry": registry_digest}
        if with_registry else {"timeline": timeline_digest}
    )
    assert set(spec["authority_context"]) == {"executor_version"}
    assert "admission_metadata" not in spec
    assert captured["storage_estimate"] is None
    assert "timeline_authority" not in spec["inputs"]
    assert "timeline_snapshot" not in spec["inputs"]


@pytest.mark.parametrize("extra, message", [
    ({"timeline_ref": "main"}, "mutually exclusive"),
    ({"expected_version": 1}, "expected_version is only valid"),
    ({"timeline_authority": {"authority": "kernel"}}, "caller-supplied timeline_authority"),
    ({"timeline_snapshot": {"registry": {"assets": {"hero": {"file": "/tmp/hero.mp4"}}}}},
     "caller-supplied timeline_snapshot"),
    ({"assets_registry": "/tmp/assets.json"}, "requires a managed Runtime object"),
])
def test_public_file_render_rejects_invalid_authority_before_admission(monkeypatch, extra, message):
    client, captured = _file_render_invoke_fixture(monkeypatch)
    with pytest.raises(invocation.CapabilityValidationError, match=message):
        invocation.invoke(
            "rendering.render", kind="executor", project="demo",
            inputs={"timeline": "sha256:" + "a" * 64, **extra}, client=client,
        )
    assert captured == {}


def test_delegated_file_render_rejects_forged_authority_before_bridge_dispatch(monkeypatch):
    from astrid.sdk import _child_bridge

    _file_render_invoke_fixture(monkeypatch)
    monkeypatch.setattr(_child_bridge, "_bridge", object())
    with pytest.raises(invocation.CapabilityValidationError, match="caller-supplied timeline_authority"):
        invocation.invoke(
            "rendering.render", kind="executor",
            inputs={"timeline": "producer-timeline.json", "timeline_authority": {"authority": "kernel"}},
        )
