"""Sprint 9 Phase 8 — portfolio-wide parity tests.

For every shipped pack id in ``PORTFOLIO_PACK_IDS`` we prove:

* Resolution through :func:`discover_packs` — same code path user-external
  packs use.
* Validation through ``validate_pack`` (the :class:`PackValidator` wrapper)
  — same code path user-external packs use.
* The pack's representative executor dispatches through
  :func:`_run_external_executor` (the same path external packs
  use). We verify the dispatch boundary by stubbing that subprocess
  entrypoint.
* Current v3 pack declarations resolve their documentation, action, UI,
  rendering, and resource references without legacy component manifests.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from unittest import mock

import pytest
import yaml

from astrid.core.execution.executor.registry import load_default_registry as load_executor_registry
from astrid.core.pack import discover_packs
from astrid.core.pack.validate import validate_pack

# ---------------------------------------------------------------------------
# Fixtures + helpers
# ---------------------------------------------------------------------------


REPO_ROOT = Path(__file__).resolve().parents[2]
PACKS_DIR = REPO_ROOT / "astrid" / "packs"

PORTFOLIO_PACK_IDS = [
    "rendering",
    "media",
    "training",
    "iteration",
    "youtube",
    "vibecomfy",
    "moirae",
    "runpod",
]


# One command action per current v3 pack exercises the external dispatch
# boundary. These are projections of pack.yaml actions, not provider calls.
REPRESENTATIVE_EXECUTORS: dict[str, str] = {
    "rendering": "rendering.render",
    "media": "media.clip_extract",
    "training": "training.search_loras",
    "iteration": "iteration.assemble",
    "youtube": "youtube.youtube_audio",
    "vibecomfy": "vibecomfy.validate",
    "moirae": "moirae.moirae",
    "runpod": "runpod.session",
}
DISPATCH_PACK_IDS = list(PORTFOLIO_PACK_IDS)


def _load_manifest(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    # Some manifests in the portfolio are JSON-with-a-.yaml suffix; try
    # JSON first so we never mis-parse a true JSON document via yaml's
    # tolerant loader.
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return yaml.safe_load(text)


def _iter_component_manifests(pack_root: Path) -> list[Path]:
    out: list[Path] = []
    for name in ("executor.yaml", "executor.yml", "executor.json",
                 "orchestrator.yaml", "orchestrator.yml", "orchestrator.json"):
        out.extend(sorted(pack_root.rglob(name)))
    return out


def _iter_declared_resource_paths(value: object):
    """Yield every pack-local path listed in nested v3 ``resources`` blocks."""
    if isinstance(value, dict):
        resources = value.get("resources")
        if isinstance(resources, list):
            for resource in resources:
                if isinstance(resource, dict) and isinstance(resource.get("path"), str):
                    yield resource["path"]
        for nested in value.values():
            yield from _iter_declared_resource_paths(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from _iter_declared_resource_paths(nested)


# ---------------------------------------------------------------------------
# Resolver + validator parity
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def packs_index() -> dict[str, object]:
    """Build a pack-id → PackDefinition lookup via discover_packs()."""
    return {p.id: p for p in discover_packs(str(PACKS_DIR))}


@pytest.mark.parametrize("pack_id", PORTFOLIO_PACK_IDS)
def test_resolver_discovers_pack(packs_index: dict, pack_id: str) -> None:
    """Every portfolio pack is resolvable via discover_packs."""
    pack = packs_index.get(pack_id)
    assert pack is not None, f"pack {pack_id!r} not discovered"
    assert pack.id == pack_id
    assert pack.root.is_dir()
    assert pack.schema_version == "3"
    assert pack.actions or pack.ui or pack.rendering or pack.documents


@pytest.mark.parametrize("pack_id", PORTFOLIO_PACK_IDS)
def test_validator_accepts_pack(pack_id: str) -> None:
    """Every portfolio pack validates cleanly through validate_pack."""
    errors, _warnings = validate_pack(PACKS_DIR / pack_id)
    assert errors == [], (
        f"validate_pack reported errors for {pack_id!r}: {errors}"
    )


# ---------------------------------------------------------------------------
# Current v3 pack and component/resource declarations
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("pack_id", PORTFOLIO_PACK_IDS)
def test_pack_manifest_uses_admitted_layout(pack_id: str) -> None:
    """Portfolio representatives use the current v3 declaration contract."""
    pack_yaml = PACKS_DIR / pack_id / "pack.yaml"
    pack_root = PACKS_DIR / pack_id
    doc = _load_manifest(pack_yaml)
    assert doc.get("schema_version") == 3, (
        f"{pack_yaml}: schema_version must be 3, got {doc.get('schema_version')!r}"
    )
    assert doc.get("documentation") == {"kind": "skill", "path": "docs/SKILL.md"}
    assert (pack_root / "docs/SKILL.md").is_file()
    assert not doc.get("content"), f"{pack_yaml}: v3 pack must not declare legacy content roots"
    assert any(doc.get(section) for section in ("actions", "ui", "rendering", "documents"))

    declared_resources = list(_iter_declared_resource_paths(doc))
    missing_resources = [path for path in declared_resources if not (pack_root / path).exists()]
    assert not missing_resources, (
        f"{pack_yaml}: declared v3 resources are missing: {missing_resources}"
    )


@pytest.mark.parametrize("pack_id", PORTFOLIO_PACK_IDS)
def test_component_manifests_v1_compliant(pack_id: str) -> None:
    """V3 components come from pack.yaml action/UI/rendering declarations."""
    pack_root = PACKS_DIR / pack_id
    manifests = _iter_component_manifests(pack_root)
    assert manifests == [], (
        f"v3 pack {pack_id!r} must declare components in pack.yaml, found {manifests}"
    )


# ---------------------------------------------------------------------------
# Dispatch path parity — every pack's representative executor goes through
# _run_external_executor.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("pack_id", DISPATCH_PACK_IDS)
def test_representative_executor_dispatches_external(pack_id: str) -> None:
    """The pack's representative executor goes through the external path.

    Patch the generic subprocess dispatch entrypoint and prove that the
    representative command reaches it.
    """
    from astrid.core.execution.executor import runner as runner_mod
    from astrid.core.execution.executor.runner import ExecutorRunRequest, ExecutorRunResult

    executor_id = REPRESENTATIVE_EXECUTORS[pack_id]
    registry = load_executor_registry()
    executor = registry.get(executor_id)

    external_called: dict[str, bool] = {"hit": False}

    def _fake_external(exe, request, values):
        external_called["hit"] = True
        return ExecutorRunResult(
            executor_id=exe.id,
            kind=exe.kind,
            command=("/bin/true",),
            payload={"executor_id": exe.id, "returncode": 0},
            returncode=0,
        )

    # Build a minimal request that passes input validation for each command
    # action. The stub intercepts the external boundary before any provider or
    # subprocess work can occur.
    inputs: dict[str, object] = {}
    for port in executor.inputs:
        if not port.required:
            continue
        inputs[port.name] = inputs.get(port.name, "x")
    if executor_id == "youtube.youtube_audio":
        inputs["query"] = "x"
    request = ExecutorRunRequest(
        executor_id=executor_id,
        out=Path(tempfile.mkdtemp()),
        project="demo",
        inputs=inputs,
        dry_run=True,
        python_exec=sys.executable,
    )

    with mock.patch.object(runner_mod, "_run_external_executor", _fake_external):
        runner_mod.run_executor(request, registry)

    assert external_called["hit"], (
        f"{executor_id} did not dispatch through _run_external_executor"
    )
