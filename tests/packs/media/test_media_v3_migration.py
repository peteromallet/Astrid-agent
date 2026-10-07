from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from astrid.core.pack.canonical import validate_canonical_pack
from astrid.packs.media.actions.speech_repair_lavasr import run as speech_run

PACK_ROOT = Path(__file__).resolve().parents[3] / "astrid" / "packs" / "media"


def test_media_pack_uses_v3_actions_and_one_authored_skill() -> None:
    entry = validate_canonical_pack(PACK_ROOT)
    assert entry.definition.schema_version == 3
    assert sorted(entry.definition.actions) == [
        "clip_extract",
        "gif_search",
        "speech_repair_lavasr",
    ]
    assert entry.definition.to_dict()["documentation"] == {
        "kind": "skill",
        "path": "docs/SKILL.md",
    }
    assert not list(PACK_ROOT.rglob("executor.yaml"))
    assert not (PACK_ROOT / "executors").exists()
    assert [path.relative_to(PACK_ROOT).as_posix() for path in PACK_ROOT.rglob("SKILL.md")] == [
        "docs/SKILL.md"
    ]

    for local_id, declaration in entry.definition.actions.items():
        assert declaration["invocation"]["kind"] == "command"
        assert declaration["invocation"]["command"]["argv"][2].startswith(
            "astrid.packs.media.actions."
        )
        assert any(resource["kind"] == "implementation" for resource in declaration["resources"])


def test_authored_skill_has_identity_and_resolvable_relative_support_links() -> None:
    skill_path = PACK_ROOT / "docs" / "SKILL.md"
    text = skill_path.read_text(encoding="utf-8")
    assert text.startswith("---\n")
    frontmatter, body = text.split("\n---\n", 1)
    assert re.search(r"^name:\s+media\s*$", frontmatter, re.MULTILINE)
    assert re.search(r"^description:\s+", frontmatter, re.MULTILINE)

    links = re.findall(r"\[[^\]]+\]\(([^)#]+)(?:#[^)]*)?\)", body)
    assert links
    assert {"references.md", "../actions/clip_extract/STAGE.md"}.issubset(links)
    for link in links:
        assert (skill_path.parent / link).resolve().is_file(), link


def test_references_guidance_is_byte_preserved_as_ordinary_media_document() -> None:
    source = Path(__file__).resolve().parents[3] / "astrid" / "packs" / "references" / "skill" / "SKILL.md"
    destination = PACK_ROOT / "docs" / "references.md"
    source_bytes = source.read_bytes()
    old_core_link = b"../../_core/skill/creative-work/SKILL.md"
    new_core_link = b"../../_core/docs/creative-work/SKILL.md"

    assert source_bytes.count(old_core_link) == 1
    assert source_bytes.count(new_core_link) == 0
    expected_bytes = source_bytes.replace(old_core_link, new_core_link, 1)
    assert destination.read_bytes() == expected_bytes


def test_speech_repair_local_pipeline_keeps_provider_boundary(monkeypatch, tmp_path: Path) -> None:
    source = tmp_path / "source.mp4"
    source.write_bytes(b"local-fixture")
    output = tmp_path / "repaired.mp4"
    calls: list[list[str]] = []

    def fake_runner(command, **_kwargs):  # type: ignore[no-untyped-def]
        command = list(command)
        calls.append(command)
        if command[-1] != "-" and command[0] == "ffmpeg":
            Path(command[-1]).write_bytes(b"offline-stage")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="mean_volume: -30 dB\nmax_volume: -2 dB\n")

    def fake_lavasr(_input_wav, output_wav, response_json, env_file):
        assert env_file is None
        output_wav.write_bytes(b"offline-lavasr")
        response_json.write_text(json.dumps({"offline": True}) + "\n", encoding="utf-8")

    monkeypatch.setattr(speech_run, "_run_lavasr", fake_lavasr)
    assert speech_run.main(
        ["--input", str(source), "--start", "0", "--dur", "1", "--output", str(output)],
        runner=fake_runner,
    ) == 0

    assert output.is_file()
    manifest = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["kind"] == "speech_repair_lavasr"
    assert any("-c:v" in command and "copy" in command for command in calls)
    assert any(speech_run.PRELIFT_FILTER in command for command in calls if "-af" in command)
    assert any(speech_run.LOUDNESS_FILTER in command for command in calls if "-af" in command)


def test_speech_repair_optional_post_pass_remains_explicit() -> None:
    args = speech_run.build_parser().parse_args(
        [
            "--input", "source.mp4", "--start", "0", "--dur", "1", "--output", "out.mp4",
            "--deepfilternet3", "false",
        ]
    )
    assert args.deepfilternet3 is False
