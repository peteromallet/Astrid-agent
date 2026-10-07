"""Integrated closure for the current 23-root V3 portfolio.

All current manifest-backed roots use schema V3. ``_core`` is a manifestless
skill shell and the Runtime CLI mounts are not packs.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from astrid.core.element.registry import load_default_registry as load_elements
from astrid.core.execution.executor.registry import (
    load_default_registry as load_executors,
)
from astrid.core.execution.orchestrator.registry import (
    load_default_registry as load_orchestrators,
)
from astrid.core.pack import discover_packs
from astrid.core.pack.canonical import BundledCatalog, validate_canonical_pack


ROOT = Path(__file__).resolve().parents[2]
PACKS = ROOT / "astrid/packs"
LOCAL_PACK_IDS = (
    "blender",
    "comfy_wrap",
    "discord_local",
    "editorial",
    "fal",
    "foley",
    "generation",
    "h3_av",
    "iteration",
    "local",
    "media",
    "moirae",
    "rendering",
    "runpod",
    "seedance_local",
    "stream_content",
    "training",
    "typed_timeline",
    "understanding",
    "vibecomfy",
    "video_editing",
    "wan2gp",
    "youtube",
)
V3_MIGRATED_PACK_IDS = (
    "blender",
    "comfy_wrap",
    "discord_local",
    "editorial",
    "fal",
    "foley",
    "generation",
    "h3_av",
    "iteration",
    "local",
    "media",
    "moirae",
    "rendering",
    "runpod",
    "seedance_local",
    "stream_content",
    "training",
    "typed_timeline",
    "understanding",
    "vibecomfy",
    "video_editing",
    "wan2gp",
    "youtube",
)
V2_RETAINED_PACK_IDS = ()
MANIFESTLESS_ROOTS = ("_core",)
RUNTIME_CLI_MOUNTS = ("references", "shots", "timeline")
RETIRED = {"builtin", "references", "reigh", "runaway", "shots", "timeline"}


def test_current_catalog_matches_authoritative_local_portfolio() -> None:
    manifests = sorted(PACKS.glob("*/pack.yaml"))
    assert tuple(path.parent.name for path in manifests) == LOCAL_PACK_IDS
    assert tuple(pack_id for pack_id in LOCAL_PACK_IDS if pack_id in V3_MIGRATED_PACK_IDS) == V3_MIGRATED_PACK_IDS
    assert tuple(pack_id for pack_id in LOCAL_PACK_IDS if pack_id in V2_RETAINED_PACK_IDS) == V2_RETAINED_PACK_IDS
    assert set(V3_MIGRATED_PACK_IDS).isdisjoint(V2_RETAINED_PACK_IDS)
    assert set(V3_MIGRATED_PACK_IDS) | set(V2_RETAINED_PACK_IDS) == set(LOCAL_PACK_IDS)
    assert not any((PACKS / pack_id / "pack.yaml").exists() for pack_id in RETIRED)
    for root_name in MANIFESTLESS_ROOTS:
        assert not (PACKS / root_name / "pack.yaml").exists()
    assert not any((PACKS / mount / "pack.yaml").exists() for mount in RUNTIME_CLI_MOUNTS)

    catalog = BundledCatalog.from_root(PACKS)
    resources_by_pack = {
        entry.id: {str(handle.path) for handle in entry.resource_handles}
        for entry in catalog.entries
    }
    for manifest in manifests:
        data = yaml.safe_load(manifest.read_text(encoding="utf-8"))
        assert data["id"] == manifest.parent.name
        assert "database" not in data
        if manifest.parent.name in V3_MIGRATED_PACK_IDS:
            assert data["schema_version"] == 3
            assert data["documentation"] == {"kind": "skill", "path": "docs/SKILL.md"}
            assert (manifest.parent / "docs/SKILL.md").is_file()
            assert "docs/SKILL.md" in resources_by_pack[manifest.parent.name]
        else:
            assert manifest.parent.name in V2_RETAINED_PACK_IDS
            assert data["schema_version"] == 2
            assert data["documentation"] == {
                "kind": "skill",
                "path": "skill/SKILL.md",
            }
            assert (manifest.parent / "skill/SKILL.md").is_file()
            assert "skill/SKILL.md" in resources_by_pack[manifest.parent.name]
        assert validate_canonical_pack(manifest.parent).id == data["id"]


def test_catalog_preserves_stage1_capability_census() -> None:
    """Pin the current bundled mixed-v2/v3 projection, not a historical census."""
    packs = discover_packs()
    executors = load_executors()
    orchestrators = load_orchestrators(executor_registry=executors)
    elements = load_elements()
    assert tuple(pack.id for pack in packs) == LOCAL_PACK_IDS
    assert (len(packs), len(executors.list()), len(orchestrators.list()), len(elements.list())) == (
        23,
        90,
        7,
        23,
    )


def test_bundled_resources_are_confined_and_readable() -> None:
    catalog = BundledCatalog.from_root(PACKS)
    assert tuple(entry.id for entry in catalog.ordered_entries) == LOCAL_PACK_IDS
    for entry in catalog.entries:
        assert all(handle.resolved.is_relative_to(entry.root) for handle in entry.resource_handles)
        assert all(handle.resolved.exists() for handle in entry.resource_handles)


def test_positive_external_fixture_uses_admitted_v3_layout() -> None:
    manifest = ROOT / "tests/fixtures/external_pack/pack.yaml"
    data = yaml.safe_load(manifest.read_text(encoding="utf-8"))
    assert data["schema_version"] == 3
    assert data["rendering"] == {
        "effects/glow": {
            "type": "element",
            "path": "rendering/effects/glow/element.yaml",
        }
    }
    assert validate_canonical_pack(manifest.parent).id == data["id"]


def test_migrated_teaching_examples_use_v3_actions() -> None:
    """Current authoring examples use v3 while staying outside runtime discovery."""
    expected = {"file_summarizer", "media", "minimal", "text_review"}
    manifests = sorted((ROOT / "examples/packs").glob("*/pack.yaml"))
    v3_manifests = [
        manifest
        for manifest in manifests
        if yaml.safe_load(manifest.read_text(encoding="utf-8"))["schema_version"] == 3
    ]
    assert {manifest.parent.name for manifest in v3_manifests} == expected
    for manifest in v3_manifests:
        data = yaml.safe_load(manifest.read_text(encoding="utf-8"))
        assert data["id"] == manifest.parent.name
        assert data["documentation"] == {"kind": "skill", "path": "docs/SKILL.md"}
        assert (manifest.parent / "docs/SKILL.md").is_file()
        assert data["actions"]
        assert validate_canonical_pack(manifest.parent).id == data["id"]


def test_retained_v2_example_and_legacy_fixture_stay_explicitly_v2() -> None:
    """Keep only the separately owned text_digest example and v2 smoke fixture here."""
    candidates = [
        ROOT / "examples/packs/text_digest/pack.yaml",
        ROOT / "tests/fixtures/local_effect_smoke/astrid/packs/local/pack.yaml",
    ]
    for manifest in candidates:
        data = yaml.safe_load(manifest.read_text(encoding="utf-8"))
        assert data["schema_version"] == 2, manifest
        assert "database" not in data, manifest
        assert validate_canonical_pack(manifest.parent).id == data["id"]

    installed_root = ROOT / "tests/fixtures/renderer_packs/discovery/installed"
    assert not installed_root.exists()
