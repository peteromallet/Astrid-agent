from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from astrid.core.execution.generic_host import GenericPackHost, _capability_digest, _source_digest_for_roots
from astrid.core.foundation.hash import capability_identity_projection, executor_definition_digest


def test_capability_identity_ignores_discovery_locations_but_keeps_contract() -> None:
    base = {
        "id": "h3_av.transform",
        "version": "0.1.1",
        "runtime": {"kind": "python", "module": "astrid.packs.h3_av.orchestrators.transform.run"},
        "inputs": [{"name": "request", "type": "file", "required": True}],
        "metadata": {
            "source": "pack",
            "source_pack": "h3_av",
            "priority": 30,
            "content_root": "/local/checkout/astrid/packs/h3_av/orchestrators/transform",
            "manifest_file": "/local/checkout/astrid/packs/h3_av/orchestrators/transform/orchestrator.yaml",
            "stage_file": "/local/checkout/astrid/packs/h3_av/orchestrators/transform/STAGE.md",
            "runtime_module": "astrid.packs.h3_av.orchestrators.transform.run",
        },
    }
    relocated = {
        **base,
        "metadata": {
            **base["metadata"],
            "source": "folder",
            "pack_root": "/workspace/release/astrid/packs/h3_av",
            "orchestrator_root": "/workspace/release/astrid/packs/h3_av/orchestrators/transform",
            "content_root": "/workspace/release/astrid/packs/h3_av/orchestrators/transform",
            "manifest_file": "/workspace/release/astrid/packs/h3_av/orchestrators/transform/orchestrator.yaml",
            "stage_file": "/workspace/release/astrid/packs/h3_av/orchestrators/transform/STAGE.md",
        },
    }

    assert capability_identity_projection(base) == capability_identity_projection(relocated)
    assert _capability_digest(base) == _capability_digest(relocated)

    changed = {**relocated, "version": "0.1.2"}
    assert _capability_digest(changed) != _capability_digest(relocated)


def test_source_identity_is_stable_when_roots_are_relocated(tmp_path: Path) -> None:
    first = tmp_path / "first" / "pack"
    second = tmp_path / "second" / "pack"
    first.mkdir(parents=True)
    second.mkdir(parents=True)
    (first / "run.py").write_text("print('same')\n", encoding="utf-8")
    (second / "run.py").write_text("print('same')\n", encoding="utf-8")

    assert _source_digest_for_roots([first]) == _source_digest_for_roots([second])


def test_executor_definition_digest_uses_portable_identity_projection() -> None:
    class FirstDefinition:
        def to_dict(self):
            return {"id": "demo", "metadata": {"manifest_file": "/one/demo.yaml", "source": "pack"}}

    class SecondDefinition:
        def to_dict(self):
            return {"id": "demo", "metadata": {"manifest_file": "/two/demo.yaml", "source": "folder"}}

    assert executor_definition_digest(FirstDefinition()) == executor_definition_digest(SecondDefinition())


@pytest.mark.parametrize("changed_contract", [False, True])
def test_worker_checks_the_host_admitted_portable_capability_digest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, changed_contract: bool,
) -> None:
    from astrid.core.execution import generic_host_worker
    from astrid.core.execution.executor import runner

    host = GenericPackHost(pack_roots=[Path(__file__).resolve().parents[2] / "astrid/packs/wan2gp"])
    host.discover()
    record = host.capabilities["wan2gp.validate_settings"]
    definition = record.definition.to_dict()
    # Discovery locations may differ between host and worker; execution
    # semantics must still match the immutable admission.
    definition["metadata"]["manifest_file"] = "/relocated/executor.yaml"
    if changed_contract:
        definition["version"] = "unadmitted-version"
    dispatched = []

    def run_executor(request, registry):
        dispatched.append(request.executor_id)
        return SimpleNamespace(ok=True, returncode=0, outputs={}, payload={})

    monkeypatch.setattr(runner, "run_executor", run_executor)
    payload = tmp_path / "request.json"
    result = tmp_path / "result.json"
    payload.write_text(json.dumps({
        "capability_kind": "executor",
        "capability_id": record.id,
        "request": {"inputs": {}, "out": str(tmp_path)},
        "definition": definition,
        "admission": {"capability_digest": record.capability_digest},
        "result_path": str(result),
    }))
    if changed_contract:
        with pytest.raises(ValueError, match="admitted capability definition digest changed"):
            generic_host_worker.run(payload)
        assert dispatched == []
        assert not result.exists()
    else:
        assert generic_host_worker.run(payload) == 0
        assert dispatched == [record.id]
        assert json.loads(result.read_text())["ok"] is True
