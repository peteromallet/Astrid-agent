"""B06 contract tests for one authored skill per pack."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import yaml

from astrid import skills
from astrid.skills import discovery, registry, state
from astrid.skills import view as skill_view
from astrid.skills.view import compose_view

from tests.test_skills import _Tmp


def _write_pack(
    root: Path,
    pack_id: str = "demo",
    *,
    schema_version: int = 3,
    documentation: dict | None = None,
    skill_text: str | None = None,
    supporting: dict[str, str] | None = None,
) -> Path:
    pack = root / pack_id
    pack.mkdir(parents=True)
    manifest = {
        "schema_version": schema_version,
        "id": pack_id,
        "name": pack_id.title(),
        "version": "1.0.0",
        "description": f"The {pack_id} pack.",
        "documentation": documentation
        if documentation is not None
        else {"kind": "skill", "path": "docs/SKILL.md"},
    }
    if schema_version == 2 and documentation is None:
        manifest["documentation"] = {"kind": "skill", "path": "skill/SKILL.md"}
    (pack / "pack.yaml").write_text(yaml.safe_dump(manifest), encoding="utf-8")
    if skill_text is not None:
        relative = Path(manifest["documentation"]["path"])
        (pack / relative).parent.mkdir(parents=True, exist_ok=True)
        (pack / relative).write_text(skill_text, encoding="utf-8")
    for relative, text in (supporting or {}).items():
        (pack / relative).parent.mkdir(parents=True, exist_ok=True)
        (pack / relative).write_text(text, encoding="utf-8")
    return pack


def _descriptor(root: Path, pack_id: str, *, guide: str) -> discovery.SkillDescriptor:
    docs = root / pack_id / "docs"
    docs.mkdir(parents=True, exist_ok=True)
    skill_md = docs / "SKILL.md"
    skill_md.write_text(
        f"---\nname: {pack_id}\ndescription: {pack_id} guide.\n---\n\n{guide}\n",
        encoding="utf-8",
    )
    return discovery.SkillDescriptor(
        pack_id=pack_id,
        name=pack_id,
        description=f"{pack_id} guide.",
        short_description=f"{pack_id} guide.",
        skill_dir=docs,
        skill_md=skill_md,
    )


def test_manifest_declared_source_preserves_frontmatter_body_and_adjacent_links(tmp_path: Path) -> None:
    body = "# Authored guide\n\n[Support](support.md)\n"
    pack = _write_pack(
        tmp_path,
        skill_text=(
            "---\nname: Authored Demo\ndescription: Keep this authored text.\n"
            "short_description: Short authored text.\n---\n\n" + body
        ),
        supporting={"docs/support.md": "supporting authored bytes\n"},
    )

    descriptors = discovery.list_skills(tmp_path)

    assert len(descriptors) == 1
    descriptor = descriptors[0]
    assert descriptor.pack_id == "demo"
    assert descriptor.name == "Authored Demo"
    assert descriptor.description == "Keep this authored text."
    assert descriptor.skill_md == (pack / "docs/SKILL.md").resolve()
    assert descriptor.skill_md.read_text(encoding="utf-8").endswith(body)

    core = next(d for d in discovery.list_skills(discovery.PACKS_DIR) if d.pack_id == "_core")
    view_root = tmp_path / "view"
    _gateway, _steps, _view_packs = compose_view(view_root, core, descriptors)
    installed = view_root / "packs" / "demo"
    assert installed.is_symlink()
    assert (installed / "SKILL.md").read_bytes() == descriptor.skill_md.read_bytes()
    assert (installed / "support.md").resolve() == (pack / "docs/support.md").resolve()


def test_missing_declared_file_and_invalid_frontmatter_are_not_skills(tmp_path: Path) -> None:
    _write_pack(tmp_path, pack_id="missing", skill_text=None)
    _write_pack(
        tmp_path,
        pack_id="invalid",
        skill_text="---\nname: [not valid\n---\nbody\n",
    )

    assert {d.pack_id for d in discovery.list_skills(tmp_path)} == set()


def test_nested_undeclared_guides_do_not_become_component_skills(tmp_path: Path) -> None:
    pack = _write_pack(
        tmp_path,
        schema_version=2,
        skill_text="---\nname: Demo\ndescription: Demo guide.\n---\n\n# Demo\n",
    )
    nested = pack / "executors" / "widget" / "skill" / "SKILL.md"
    nested.parent.mkdir(parents=True)
    nested.write_text(
        "---\nname: widget\ndescription: Nested guide.\n---\n\n# Widget\n",
        encoding="utf-8",
    )

    descriptors = discovery.list_skills(tmp_path)

    assert [descriptor.pack_id for descriptor in descriptors] == ["demo"]
    assert all("widget" not in descriptor.pack_id for descriptor in descriptors)


def test_video_editing_canonical_doc_route_survives_fresh_view_composition(tmp_path: Path) -> None:
    packs = discovery.list_skills(discovery.PACKS_DIR)
    core = next(item for item in packs if item.pack_id == "_core")
    video_editing = next(item for item in packs if item.pack_id == "video_editing")
    view_root = tmp_path / "view"

    compose_view(view_root, core, [video_editing])

    installed_route = (view_root / "creative-work" / "SKILL.md").read_text(encoding="utf-8")
    assert "[video editing](../packs/video_editing/SKILL.md)" in installed_route
    assert "../../../video_editing/docs/SKILL.md" not in installed_route
    assert (view_root / "packs" / "video_editing" / "SKILL.md").read_bytes() == (
        video_editing.skill_md.read_bytes()
    )
    assert skill_view._rewrite(
        "../../video_editing/docs/SKILL.md", skill_view._ROOT_ROUTE_REWRITES
    ) == "packs/video_editing/SKILL.md"
    assert skill_view._rewrite(
        "../../video_editing/skill/SKILL.md", skill_view._ROOT_ROUTE_REWRITES
    ) == "packs/video_editing/SKILL.md"


def test_sync_retires_separate_component_links_and_records_without_touching_optouts_or_foreign_files(
    tmp_path: Path,
) -> None:
    fx = _Tmp()
    try:
        core = next(d for d in discovery.list_skills(discovery.PACKS_DIR) if d.pack_id == "_core")
        media = _descriptor(tmp_path, "media", guide="[References](references.md)")
        references = _descriptor(tmp_path, "astrid-references", guide="legacy references")
        image = _descriptor(tmp_path, "generation.generate_image", guide="legacy image guidance")
        all_before = [core, media, references, image]
        all_after = [core, media]

        # The owning guide is linked before the old component installs are
        # retired; no guidance disappears with the old IDs.
        (media.skill_dir / "references.md").write_text("References content\n", encoding="utf-8")
        assert "references.md" in media.skill_md.read_text(encoding="utf-8")

        with mock.patch.object(skills, "list_skills", return_value=all_before):
            skills.sync(deep=True, state_path=fx.state_path)

        current = state.load(fx.state_path)
        current["setup_selection"] = {
            "integrations": ["media", "astrid-references", "generation.generate_image"]
        }
        for harness in state.HARNESSES:
            current["disabled_defaults"][harness] = [
                "astrid-references",
                "generation.generate_image",
            ]
        state.save(current, fx.state_path)
        foreign = fx.home / ".claude" / "skills" / "image-generation"
        foreign.mkdir(parents=True)
        (foreign / "SKILL.md").write_text("foreign\n", encoding="utf-8")

        with mock.patch.object(skills, "list_skills", return_value=all_after):
            skills.sync(deep=True, state_path=fx.state_path)

        after = state.load(fx.state_path)
        for harness in state.HARNESSES:
            assert "astrid-references" not in after["installs"][harness]
            assert "generation.generate_image" not in after["installs"][harness]
            assert after["disabled_defaults"][harness] == [
                "astrid-references",
                "generation.generate_image",
            ]
        assert after["setup_selection"] == current["setup_selection"]
        assert (foreign / "SKILL.md").read_text(encoding="utf-8") == "foreign\n"
    finally:
        fx.close()


def test_hermes_external_dir_sync_registers_disposable_composed_view(tmp_path: Path) -> None:
    fx = _Tmp()
    try:
        core = next(d for d in discovery.list_skills(discovery.PACKS_DIR) if d.pack_id == "_core")
        media = _descriptor(tmp_path, "media", guide="# Media\n")
        with mock.patch.object(skills, "list_skills", return_value=[core, media]):
            skills.sync(
                mechanism="external-dir",
                deep=True,
                state_path=fx.state_path,
            )

            config = yaml.safe_load((fx.home / ".hermes" / "config.yaml").read_text(encoding="utf-8"))
            expected = fx.state_path.parent / "skills" / "hermes" / "packs"
            assert str(expected.resolve()) in config["skills"]["external_dirs"]
            assert (expected / "media" / "SKILL.md").resolve() == media.skill_md.resolve()
            assert (expected / "media" / "SKILL.md").read_bytes() == media.skill_md.read_bytes()
            assert not skills.check(deep=True, state_path=fx.state_path)["has_drift"]
    finally:
        fx.close()


def test_registry_exposes_one_row_per_declared_pack_skill() -> None:
    descriptors = [
        SimpleNamespace(
            pack_id="media",
            name="media",
            description="media guide",
            short_description="media guide",
            skill_md=Path("/source/media/docs/SKILL.md"),
        ),
        SimpleNamespace(
            pack_id="rendering",
            name="rendering",
            description="rendering guide",
            short_description="rendering guide",
            skill_md=Path("/source/rendering/docs/SKILL.md"),
        ),
    ]

    rendered = registry.render_registry_block(descriptors)

    assert rendered.count("| media |") == 1
    assert rendered.count("| rendering |") == 1
    assert "generation.generate_image" not in rendered
