from __future__ import annotations

from pathlib import Path

import pytest

from astrid.core.generation.features import (
    load_default_generation_taxonomy_registry,
)
from astrid.core.model_catalog.taxonomy import (
    BUILTIN_GENERATION_BACKEND_IDS,
    CANONICAL_IMAGE_MODES,
    CANONICAL_VIDEO_MODES,
    GENERATION_TAXONOMY,
    IMAGE_FEATURES,
    VIDEO_FEATURES,
    GenerationBackendIdDescriptor,
    GenerationFeatureDescriptor,
    GenerationModeDescriptor,
    GenerationTaxonomyRegistry,
)


def _write(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def test_builtin_generation_taxonomy_preserves_existing_constants() -> None:
    feature_ids = GENERATION_TAXONOMY.feature_ids()
    assert feature_ids[: len(VIDEO_FEATURES)] == VIDEO_FEATURES
    assert feature_ids.count("mask_ref") == 1
    assert GENERATION_TAXONOMY.mode_ids("image") == CANONICAL_IMAGE_MODES
    assert GENERATION_TAXONOMY.mode_ids("video") == CANONICAL_VIDEO_MODES
    assert GENERATION_TAXONOMY.backend_ids() == BUILTIN_GENERATION_BACKEND_IDS
    assert set(IMAGE_FEATURES).issubset(set(GENERATION_TAXONOMY.feature_ids()))
    descriptor = next(
        descriptor
        for descriptor in GENERATION_TAXONOMY.feature_descriptors()
        if descriptor.id == "mask_ref"
    )
    assert descriptor.label == "Mask reference"
    assert descriptor.description == (
        "Typed CAS-backed image mask used by bounded image inpainting."
    )


def test_registry_accepts_pack_like_feature_mode_and_backend_ids() -> None:
    registry = GenerationTaxonomyRegistry(
        feature_descriptors=(GenerationFeatureDescriptor(id="vendor_mask_ref"),),
        mode_descriptors=(GenerationModeDescriptor(id="storyboard"),),
        backend_descriptors=(GenerationBackendIdDescriptor(id="studio"),),
    )

    assert "vendor_mask_ref" in registry.feature_ids()
    assert "storyboard" in registry.mode_ids("image")
    assert "storyboard" in registry.mode_ids("video")
    assert "studio" in registry.backend_ids()
    assert registry.require_feature("vendor_mask_ref", path="supports[0]") == "vendor_mask_ref"
    assert registry.require_mode("image", "storyboard", path="modes['storyboard']") == "storyboard"


def test_registry_rejects_duplicate_ids_within_each_taxonomy() -> None:
    with pytest.raises(ValueError, match="duplicate generation feature"):
        GenerationTaxonomyRegistry(
            feature_descriptors=(GenerationFeatureDescriptor(id="prompt"),)
        )
    with pytest.raises(ValueError, match="duplicate generation mode"):
        GenerationTaxonomyRegistry(
            mode_descriptors=(GenerationModeDescriptor(id="t2i"),)
        )
    with pytest.raises(ValueError, match="duplicate generation backend id"):
        GenerationTaxonomyRegistry(
            backend_descriptors=(GenerationBackendIdDescriptor(id="local"),)
        )


def test_load_default_generation_taxonomy_registry_adds_pack_declared_ids(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Keep this synthetic extension test isolated from the still-v2 Generation
    # pack, whose real mask_ref declaration is intentionally pending M07's
    # paired removal.
    monkeypatch.setattr(
        "astrid.core.generation.features.discover_packs",
        lambda root=None: (),
    )
    extra_root = tmp_path / "extra-packs"
    pack_root = extra_root / "vendor_pack"
    _write(
        pack_root / "pack.yaml",
        """schema_version: 2
id: vendor_pack
name: Vendor Pack
version: 0.1.0
extensions:
  generation:
    features:
      - id: vendor_mask_ref
    modes:
      - id: storyboard
    backends:
      - id: studio
        module: vendor.backend
        class: StudioBackend
""",
    )

    registry = load_default_generation_taxonomy_registry(
        project_root=tmp_path,
        extra_pack_roots=(str(extra_root),),
    )

    assert "vendor_mask_ref" in registry.feature_ids()
    assert "storyboard" in registry.mode_ids("image")
    assert "storyboard" in registry.mode_ids("video")
    assert "studio" in registry.backend_ids()
