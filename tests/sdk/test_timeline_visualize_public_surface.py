from __future__ import annotations

from pathlib import Path

import pytest

import astrid.sdk as astrid
from astrid.sdk import discovery as sdk_discovery


ROOT = Path(__file__).resolve().parents[2]
PRIVATE_BACKEND_ID = "rendering.timeline_visualize"


def test_timeline_visualize_backend_is_not_in_public_discovery(monkeypatch):
    monkeypatch.setattr(
        sdk_discovery,
        "_build_discovery_metadata",
        lambda *_, **__: ((), (), (), (), ()),
    )
    discovered = astrid.discover(project_root=ROOT)

    assert PRIVATE_BACKEND_ID not in {capability.id for capability in discovered.capabilities}
    assert "timeline_visualize" not in {
        entry for pack in discovered.packs for entry in pack.get("normal_entrypoints", [])
    }


def test_timeline_visualize_backend_resolves_as_generic_unknown_capability():
    with pytest.raises(astrid.CapabilityNotFoundError) as private_error:
        astrid.get_capability(PRIVATE_BACKEND_ID, kind="executor", project_root=ROOT)
    with pytest.raises(astrid.CapabilityNotFoundError) as unknown_error:
        astrid.get_capability("not_a_real_capability", kind="executor", project_root=ROOT)

    assert type(private_error.value) is type(unknown_error.value)
    assert "unknown executor" in str(private_error.value)


def test_timeline_visualize_backend_invoke_result_is_generic_unknown_without_admission():
    result = astrid.invoke_result(
        PRIVATE_BACKEND_ID,
        kind="executor",
        project_root=ROOT,
        project="does-not-exist",
    )

    assert not result.ok
    assert result.error["type"] == "CapabilityNotFoundError"
    assert "unknown executor" in result.error["message"]
    assert result.run_id is None
    assert result.kernel_task_id is None


def test_public_invoke_cannot_opt_in_to_private_capabilities():
    with pytest.raises(TypeError, match="private capabilities"):
        astrid.invoke(
            PRIVATE_BACKEND_ID,
            kind="executor",
            project_root=ROOT,
            _include_internal=True,
        )
