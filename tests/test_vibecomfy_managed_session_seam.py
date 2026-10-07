from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from astrid.core.execution.generic_host import (
    GenericPackHost,
    _ManagedVibeSessionAdapter,
    _ManagedWanChildAdapter,
)
from astrid.core.execution.managed_tool_session import (
    CapabilityDescriptor,
    ManagedToolSession,
    SessionBinding,
    StaleAdmissionError,
)
from astrid.core.execution import generic_host
from astrid.core.pack.loader import load_pack_manifest
from astrid.sdk.actions import action_executor_definition
from tests.test_generic_host import FakeRuntime
from tests.test_generic_host_vibecomfy_lifecycle import (
    _FakeCheckout,
    _install_fixture,
    _profile,
    _task,
)


class _FakeVibeBackend:
    def __init__(self) -> None:
        self.events: list[str] = []

    def _revalidate_host_session(self) -> None:
        self.events.append("observe")

    def cancel(self) -> dict[str, object]:
        self.events.append("cancel")
        return {"ok": True, "cancelled": True}

    def release(self, *, reason: str) -> dict[str, object]:
        self.events.append(f"release:{reason}")
        return {"ok": True, "released": True}


def _binding(*, session_id: str, engine_birth_id: str) -> SessionBinding:
    return SessionBinding(
        session_id=session_id,
        runtime_instance_id="runtime-1",
        process_birth_id=f"host-{engine_birth_id}",
        endpoint=f"pipe:{session_id}",
        source_digest=f"source-{engine_birth_id}",
        config_digest=f"config-{engine_birth_id}",
        execution_identity=f"resident-{engine_birth_id}",
        runtime_epoch=1,
        launch_generation=f"launch-{engine_birth_id}",
        engine_birth_id=engine_birth_id,
    )


def _vibe_capability() -> CapabilityDescriptor:
    return CapabilityDescriptor(
        "vibecomfy.run",
        residency_support="observable_releasable",
        resources_claimed=("gpu", "ports", "files"),
        warm_reuse_expected=True,
    )


def test_vibe_is_admitted_with_exact_binding_and_fenced_lifecycle() -> None:
    backend = _FakeVibeBackend()
    adapter = _ManagedVibeSessionAdapter(backend)
    binding = _binding(session_id="/owned/vibe-session", engine_birth_id="comfy-a")
    manager = ManagedToolSession(manager_id="worker:executor")

    manager.open(capability=_vibe_capability(), binding=binding, adapter=adapter)
    token = manager.admit(
        capability_id="vibecomfy.run",
        invocation_id="task-vibe:attempt-vibe:3",
    )

    assert token.session_id == binding.session_id
    assert token.invocation_id == "task-vibe:attempt-vibe:3"
    assert token.binding_identity == binding.identity_key
    assert manager.adapter_for(token, begin=True) is adapter
    manager.observe(binding)
    settled = manager.settle(
        token,
        result_evidence={
            "token_id": token.token_id,
            "invocation_id": token.invocation_id,
            "generation": token.generation,
            "binding_identity": list(binding.identity_key),
            "outputs": [{"digest": "sha256:" + "a" * 64}],
        },
    )

    assert settled.state == "settled"
    assert settled.binding == binding
    manager.release(reason="task_settled")
    assert backend.events == ["observe", "cancel", "release:task_settled"]


def test_vibe_failure_fences_token_before_release() -> None:
    backend = _FakeVibeBackend()
    binding = _binding(session_id="/owned/vibe-session", engine_birth_id="comfy-fail")
    manager = ManagedToolSession(manager_id="worker:executor")
    manager.open(
        capability=_vibe_capability(),
        binding=binding,
        adapter=_ManagedVibeSessionAdapter(backend),
    )
    token = manager.admit(
        capability_id="vibecomfy.run",
        invocation_id="task-failed:attempt-failed:1",
    )

    manager.fence(reason="task_failed")
    with pytest.raises(StaleAdmissionError):
        manager.settle(
            token,
            result_evidence={
                "token_id": token.token_id,
                "invocation_id": token.invocation_id,
                "generation": token.generation,
                "binding_identity": list(binding.identity_key),
                "outputs": [],
            },
        )
    manager.release(reason="task_failed")

    assert backend.events == ["cancel", "cancel", "release:task_failed"]


def test_generic_host_uses_native_vibe_adapter_and_preserves_task_attempt_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workflow_bytes = b"canonical workflow fixture\n"
    digest, data = _install_fixture(tmp_path)
    assert hashlib.sha256(data).hexdigest() == digest == hashlib.sha256(workflow_bytes).hexdigest()
    runtime = FakeRuntime()
    runtime.get_object = lambda requested: data  # type: ignore[method-assign]
    monkeypatch.setattr(generic_host, "_read_readiness_profile_document", _profile)
    monkeypatch.setattr(
        generic_host,
        "_prepare_vibecomfy_execution_identity",
        lambda *args: (
            "execution-vibe-a",
            "model-a",
            "template-a",
            {"model_id": "model-a", "resident_metadata": {"model_assets": ["model-a"]}},
        ),
    )

    captured: list[dict[str, object]] = []
    events: list[str] = []

    def bind_native(cls: object, **kwargs: object) -> _FakeCheckout:
        del cls
        captured.append(kwargs)
        return _FakeCheckout(events, spawn_child=False)

    from astrid.core.generation.backends import vibecomfy

    monkeypatch.setattr(
        vibecomfy.CheckoutServerAdapter,
        "from_host_session",
        classmethod(bind_native),
    )

    host = GenericPackHost(pack_roots=[tmp_path], client=runtime)
    host.discover()
    task_id = "task-native-vibe"
    runtime.tasks[task_id] = _task(task_id, digest)
    try:
        result = host.run_task(runtime.tasks[task_id], lease_token="lease-native-vibe")
    finally:
        host.shutdown()

    assert result["task"]["status"] == "completed"
    assert captured[0]["invocation_identity"] == f"{task_id}:{task_id}-attempt:1"
    managed = runtime.settlements[0][2]["result"]["managed_tool_session"]
    assert managed["capability"]["capability_id"] == "vibecomfy.run"
    assert managed["binding"]["session_id"] == "/tmp/session"
    assert managed["terminal"]["invocation_id"] == f"{task_id}:{task_id}-attempt:1"
    assert "observe" in events


def test_vibe_and_wan_use_distinct_native_adapter_and_residency_identity(tmp_path: Path) -> None:
    vibe_binding = _binding(session_id="/owned/vibe-session", engine_birth_id="comfy-a")
    wan_binding = _binding(session_id="/owned/wan-session", engine_birth_id="wan-a")
    vibe_adapter = _ManagedVibeSessionAdapter(_FakeVibeBackend())
    wan_adapter = _ManagedWanChildAdapter(
        spec={"owner_dir": str(tmp_path), "init": {"output_dir": str(tmp_path / "spool")}},
        binding=wan_binding,
        readiness_timeout=1.0,
        release_timeout=1.0,
        track=lambda process: None,
        untrack=lambda process: None,
    )

    assert type(vibe_adapter) is not type(wan_adapter)
    assert vibe_binding.identity_key != wan_binding.identity_key
    assert vibe_binding.engine_birth_id != wan_binding.engine_birth_id
    assert vibe_binding.session_id != wan_binding.session_id


def test_vibe_manifest_keeps_native_executor_and_production_backend() -> None:
    PACK = Path(__file__).resolve().parents[1] / "astrid/packs/vibecomfy"
    pack = load_pack_manifest(PACK / "pack.yaml")
    definition = action_executor_definition(pack, "run", pack.actions["run"])
    argv = definition.command.argv
    run_source = (PACK / "actions/run/run.py").read_text(
        encoding="utf-8"
    )

    assert argv[:3] == ("{python_exec}", "-m", "astrid.packs.vibecomfy.actions.run.run")
    assert definition.metadata["cli_module"] == "vibecomfy.cli"
    assert "production_engine" in run_source
    assert "run_workflow_result_path" in run_source
