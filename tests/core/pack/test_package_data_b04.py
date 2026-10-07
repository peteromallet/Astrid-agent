"""B04 package-data projection for authored pack documentation."""

from __future__ import annotations

import glob
import tomllib
from fnmatch import fnmatch
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
PACKAGE_ROOT = REPO_ROOT / "astrid"
PYPROJECT = REPO_ROOT / "pyproject.toml"
LOCAL_ASSET_DIRECTORIES = (
    "packs/local/rendering/elements/effects/end-codex-transform/assets",
    "packs/local/rendering/elements/effects/end-mid-combined/assets",
    "packs/local/rendering/elements/effects/end-minimax-animate/assets",
    "packs/local/rendering/elements/effects/end-spanning-layer/assets",
    "packs/local/rendering/elements/effects/ending-carousel/assets",
    "packs/local/rendering/elements/effects/frame-overlay/assets",
)


def _package_data_config() -> tuple[list[str], list[str]]:
    config = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    tool = config["tool"]["setuptools"]
    patterns = tool["package-data"]["astrid"]
    exclusions = tool["exclude-package-data"]["astrid"]
    return patterns, exclusions


def _projected_package_data(package_root: Path) -> set[str]:
    patterns, exclusions = _package_data_config()
    projected: set[str] = set()
    for pattern in patterns:
        for candidate in glob.glob(str(package_root / pattern), recursive=True):
            path = Path(candidate)
            if not path.is_file():
                continue
            relative = path.relative_to(package_root).as_posix()
            if not any(fnmatch(relative, exclusion) for exclusion in exclusions):
                projected.add(relative)
    return projected


def _declared_local_rendering_assets() -> set[str]:
    manifest_path = PACKAGE_ROOT / "packs/local/pack.yaml"
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    prefixes = tuple(f"{directory}/" for directory in LOCAL_ASSET_DIRECTORIES)
    return {
        f"packs/local/{resource['path']}"
        for element in manifest["rendering"].values()
        for resource in element.get("resources", [])
        if resource.get("kind") == "asset"
        and any(f"packs/local/{resource['path']}".startswith(prefix) for prefix in prefixes)
    }


def test_pilot_authored_docs_and_nested_support_are_explicit_package_data() -> None:
    patterns, _ = _package_data_config()

    assert "packs/**/docs/*" in patterns
    assert "packs/**/docs/**/*" in patterns

    projected = _projected_package_data(PACKAGE_ROOT)
    expected = {
        "packs/media/docs/SKILL.md",
        "packs/media/docs/references.md",
        "packs/rendering/docs/SKILL.md",
        "packs/rendering/docs/references/live-scenes-authoring.md",
        "packs/rendering/docs/references/timeline-cookbook.md",
        "packs/rendering/docs/templates/two-shot-threejs.ts",
    }

    assert expected <= projected
    for relative in expected:
        path = PACKAGE_ROOT / relative
        assert path.resolve().is_relative_to(PACKAGE_ROOT.resolve())


def test_declared_action_stage_docs_are_projected_but_scope_remains_narrow() -> None:
    patterns, _ = _package_data_config()
    assert "packs/*/actions/*/STAGE.md" in patterns

    projected = _projected_package_data(PACKAGE_ROOT)
    expected = {
        "packs/media/actions/clip_extract/STAGE.md",
        "packs/media/actions/gif_search/STAGE.md",
        "packs/media/actions/speech_repair_lavasr/STAGE.md",
        "packs/rendering/actions/html_canvas_effect/STAGE.md",
        "packs/rendering/actions/render/STAGE.md",
        "packs/rendering/actions/sprite_sheet/STAGE.md",
        "packs/rendering/actions/timeline_storyboard/STAGE.md",
        "packs/rendering/actions/timeline_visualize/STAGE.md",
    }

    assert expected <= projected


def test_docs_projection_keeps_package_data_scoped_and_honors_exclusions(tmp_path: Path) -> None:
    package_root = tmp_path / "astrid"
    docs = package_root / "packs" / "fixture" / "docs"
    for relative in (
        "SKILL.md",
        "references/nested/guide.md",
        "templates/input.json",
        "tests/nested/fixture.md",
        "golden/nested/fixture.md",
        "build/nested/generated.js",
        "__pycache__/ignored.pyc",
    ):
        path = docs / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("fixture\n", encoding="utf-8")

    # A similarly named repository file is outside the explicit packs/**/docs
    # projection and must not become package data.
    outside = tmp_path / "docs" / "not-a-pack-resource.md"
    outside.parent.mkdir(parents=True)
    outside.write_text("outside\n", encoding="utf-8")

    projected = _projected_package_data(package_root)

    assert {
        "packs/fixture/docs/SKILL.md",
        "packs/fixture/docs/references/nested/guide.md",
        "packs/fixture/docs/templates/input.json",
    } <= projected
    for excluded in (
        "packs/fixture/docs/tests/nested/fixture.md",
        "packs/fixture/docs/golden/nested/fixture.md",
        "packs/fixture/docs/build/nested/generated.js",
        "packs/fixture/docs/__pycache__/ignored.pyc",
    ):
        assert excluded not in projected
    assert "../docs/not-a-pack-resource.md" not in projected

    assert all(
        (package_root / relative).resolve().is_relative_to(package_root.resolve())
        for relative in projected
    )


def test_action_stage_projection_rejects_unscoped_deep_and_excluded_paths(tmp_path: Path) -> None:
    package_root = tmp_path / "astrid"
    paths = {
        "packs/fixture/STAGE.md",
        "packs/fixture/actions/example/nested/STAGE.md",
        "packs/fixture/actions/tests/STAGE.md",
        "packs/fixture/actions/golden/STAGE.md",
        "packs/fixture/actions/build/STAGE.md",
        "packs/fixture/actions/__pycache__/STAGE.md",
        "packs/fixture/content/legacy/STAGE.md",
        "packs/video_editing/executors/STAGE.md",
        "packs/video_editing/executors/legacy/deep/STAGE.md",
        "packs/video_editing/orchestrators/legacy/deep/STAGE.md",
        "packs/video_editing/content/legacy/STAGE.md",
    }
    for relative in paths:
        path = package_root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("fixture\n", encoding="utf-8")

    projected = _projected_package_data(package_root)

    assert not paths & projected


def test_declared_local_rendering_assets_are_projected_without_directory_widening() -> None:
    patterns, _ = _package_data_config()
    for directory in LOCAL_ASSET_DIRECTORIES:
        assert f"{directory}/*.png" in patterns
        assert f"{directory}/*.mp4" in patterns

    declared = _declared_local_rendering_assets()
    assert len(declared) == 44
    assert all((PACKAGE_ROOT / path).is_file() for path in declared)

    projected = _projected_package_data(PACKAGE_ROOT)
    from_asset_directories = {
        path
        for path in projected
        if any(path.startswith(f"{directory}/") for directory in LOCAL_ASSET_DIRECTORIES)
    }
    assert from_asset_directories == declared


def test_video_editing_content_root_stage_docs_are_projected_but_bounded() -> None:
    patterns, _ = _package_data_config()
    assert "packs/video_editing/executors/*/STAGE.md" in patterns
    assert "packs/video_editing/orchestrators/*/STAGE.md" in patterns

    expected = {
        "packs/video_editing/executors/cut/STAGE.md",
        "packs/video_editing/orchestrators/animate_image/STAGE.md",
        "packs/video_editing/orchestrators/event_talks/STAGE.md",
        "packs/video_editing/orchestrators/hype/STAGE.md",
        "packs/video_editing/orchestrators/iteration_video/STAGE.md",
        "packs/video_editing/orchestrators/logo_ideas/STAGE.md",
        "packs/video_editing/orchestrators/thumbnail_maker/STAGE.md",
        "packs/video_editing/orchestrators/vary_grid/STAGE.md",
    }

    projected = _projected_package_data(PACKAGE_ROOT)
    projected_video_editing_stage_docs = {
        path
        for path in projected
        if path.startswith("packs/video_editing/executors/")
        or path.startswith("packs/video_editing/orchestrators/")
        if path.endswith("/STAGE.md")
    }
    assert projected_video_editing_stage_docs == expected


def test_runpod_shared_requirements_are_explicit_package_data() -> None:
    patterns, _ = _package_data_config()

    assert "packs/runpod/shared/requirements.txt" in patterns
    assert "packs/runpod/shared/requirements.txt" in _projected_package_data(PACKAGE_ROOT)


def test_existing_package_data_contract_and_core_shell_remain_present() -> None:
    config = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    tool = config["tool"]["setuptools"]
    assert tool["include-package-data"] is False

    patterns, exclusions = _package_data_config()
    assert "packs/**/skill/*" in patterns
    assert "packs/**/skill/**/*" in patterns
    assert "packs/*/actions/*/requirements.txt" in patterns
    assert "**/__pycache__/*" in exclusions
    assert "packs/**/tests/*" in exclusions
    assert "packs/**/golden/*" in exclusions
    assert "packs/**/build/*" in exclusions

    projected = _projected_package_data(PACKAGE_ROOT)
    assert "packs/_core/docs/SKILL.md" in projected
    assert "packs/_core/docs/references/capabilities.md" in projected
    assert "packs/blender/server/blender-render-api.service" in projected
    assert {
        "packs/comfy_wrap/actions/run/requirements.txt",
        "packs/moirae/actions/moirae/requirements.txt",
        "packs/vibecomfy/actions/edit/requirements.txt",
        "packs/vibecomfy/actions/import/requirements.txt",
        "packs/vibecomfy/actions/inspect/requirements.txt",
        "packs/vibecomfy/actions/run/requirements.txt",
        "packs/vibecomfy/actions/validate/requirements.txt",
    } <= projected
