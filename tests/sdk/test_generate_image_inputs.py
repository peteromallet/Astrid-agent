"""generate_image input validation: dry_run, route-aware errors, edit size warning, references list."""

from __future__ import annotations

import json
import struct
from pathlib import Path
from types import SimpleNamespace

import pytest

import astrid.sdk as sdk
from astrid.sdk.contracts import DomainResult, ErrorObject
from astrid.sdk.media_handles import MediaHandleError, apply_media_handles, resolve_media_handle

CODEX = "generation.generate_image_codex"


def _dry(tmp_path: Path, **inputs):
    return sdk.invoke(
        "generation.generate_image",
        kind="executor",
        project="demo",
        project_root=tmp_path,
        out=tmp_path / "out",
        dry_run=True,
        inputs={"prompt": "a tiny blue teapot", **inputs},
    )


def _codex_capability():
    return sdk.get_capability(CODEX, kind="executor")


def test_dry_run_binds_codex_inputs_and_never_admits(tmp_path, monkeypatch):
    """A valid codex dry_run returns the argv and touches no client, ledger or runtime."""
    import astrid.sdk.invocation as invocation

    def _forbidden(*args, **kwargs):
        raise AssertionError("dry_run must not open a client or admit a task")

    monkeypatch.setattr(invocation, "_default_client", _forbidden)
    monkeypatch.setattr(sdk.AstridClient, "open_from_launcher", staticmethod(_forbidden), raising=False)

    result = _dry(tmp_path, model="flux-dev", mode="t2i", execution="codex", count=2)

    assert result.raw_result["dry_run"] is True
    assert result.ok is True
    assert result.run_id is None and result.kernel_task_id is None
    assert "generate_image_codex" in " ".join(result.raw_result["command"])
    assert not (tmp_path / ".astrid" / "astrid.sqlite3").exists()


def test_undeclared_n_is_refused_before_admission_and_lists_declared_inputs(tmp_path):
    with pytest.raises(sdk.CapabilityValidationError) as exc:
        sdk.invoke(
            CODEX,
            kind="executor",
            project="demo",
            project_root=tmp_path,
            dry_run=True,
            inputs={"model": "flux-dev", "mode": "t2i", "execution": "codex", "prompt": "x", "n": 4},
        )
    message = str(exc.value)
    assert "undeclared parameter(s): n" in message
    assert "Declared inputs:" in message and "count" in message
    assert "use count (not n/num_images)" in message


def test_unknown_model_on_codex_route_lists_only_codex_models(tmp_path):
    with pytest.raises(sdk.CapabilityValidationError) as exc:
        _dry(tmp_path, model="gpt-image-9", mode="t2i", execution="codex")
    message = str(exc.value)
    assert "Unknown model 'gpt-image-9' for execution 'codex'" in message
    assert "qwen-image-edit (edit)" in message and "flux-dev" in message
    # A cloud-only model must not be advertised on the codex route.
    assert "seedvr2-upscaler" not in message and "wan-2.2" not in message


def test_route_models_are_computed_from_the_catalog():
    from astrid.core.model_catalog.registry import ModelRegistry

    routes = ModelRegistry.load_default().modes_for_route("codex")
    assert routes["qwen-image-edit"] == ("edit",)
    assert "flux-dev" in routes and "seedream-v5-pro" not in routes


def test_role_conflict_lists_every_valid_role_and_slot():
    cap = _codex_capability()
    with pytest.raises(MediaHandleError) as exc:
        apply_media_handles(None, "demo", cap, {"references": [
            {"ref": "ref:A", "role": "character"}, {"ref": "ref:A", "role": "style"}]}, resolve=False)
    message = str(exc.value)
    assert "both use the style_ref slot" in message
    for pair in ("source->image_ref", "character->style_ref", "style->style_ref", "brand->brand_ref"):
        assert pair in message


def test_local_file_reference_says_import_it_first():
    cap = _codex_capability()
    with pytest.raises(MediaHandleError) as exc:
        apply_media_handles(None, "demo", cap, {"references": [
            {"ref": "/tmp/logo.png", "role": "source"}]}, resolve=False)
    assert "import it first: python -m astrid media import /tmp/logo.png --project P" in str(exc.value)


def test_plain_reference_name_is_not_mistaken_for_a_file():
    from astrid.sdk.media_handles import _local_file_hint

    assert _local_file_hint("Astrid presenter") is None
    assert _local_file_hint("sha256:" + "a" * 64) is None
    assert _local_file_hint("run:r-00/generated_images#1") is None


def test_resolving_a_handle_without_project_names_the_cli_flag():
    with pytest.raises(MediaHandleError, match="--project PROJECT"):
        resolve_media_handle(object(), "", "ref:Astrid mink")


def _png(path: Path, width: int, height: int) -> Path:
    header = b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR" + struct.pack(">II", width, height)
    path.write_bytes(header + b"\x00" * 16)
    return path


def test_edit_size_flipping_orientation_warns(tmp_path):
    from astrid.packs.generation.executors.generate_image.run import _edit_size_orientation_warning

    source = _png(tmp_path / "landscape.png", 1536, 1024)
    warning = _edit_size_orientation_warning("edit", "1024x1536", str(source))
    assert warning is not None and warning["code"] == "edit_size_orientation"
    assert "1024x1536" in warning["message"] and "1536x1024" in warning["message"]
    assert _edit_size_orientation_warning("edit", "1536x1024", str(source)) is None
    assert _edit_size_orientation_warning("t2i", "1024x1536", str(source)) is None
    assert _edit_size_orientation_warning("edit", None, str(source)) is None


def test_edit_size_warning_ignores_non_png_sources(tmp_path):
    from astrid.packs.generation.executors.generate_image.run import _edit_size_orientation_warning

    jpg = tmp_path / "source.jpg"
    jpg.write_bytes(b"\xff\xd8\xff\xe0not-a-png")
    assert _edit_size_orientation_warning("edit", "1024x1536", str(jpg)) is None


# -- references list ------------------------------------------------------

ROWS = [
    {"name": "Astrid mink", "kind": "character", "reference_id": "ref-1", "archived": False,
     "links": [], "media_references": [
         {"media_id": "sha256:" + "f" * 64, "is_primary": True, "role": "canonical"},
         {"media_id": "sha256:" + "e" * 64, "is_primary": False, "role": "depicts"}]},
    {"name": "Old set", "kind": "other", "reference_id": "ref-2", "archived": True,
     "links": [{"kind": "related_to"}], "media_references": []},
]


class _RefsClient:
    def __init__(self):
        self.calls = []
        self.references = self

    def list(self, project, **kwargs):
        self.calls.append(kwargs)
        rows = [row for row in ROWS if kwargs.get("include_archived") or not row["archived"]]
        return DomainResult.success([rows, None])


def _refs_parser(argv):
    from astrid.packs.references.cli import build_parser

    client = _RefsClient()
    parser = build_parser(client)
    parsed = parser.parse_args(argv)
    parsed.client = client
    return parsed, client


def test_references_list_default_is_a_short_human_listing(capsys):
    parsed, client = _refs_parser(["list", "--project", "demo"])
    assert parsed.handler(parsed) == 0
    out = capsys.readouterr().out.strip().splitlines()
    assert len(out) == 1 and "Astrid mink" in out[0] and "ref-1" in out[0]
    assert "canonical=sha256:ffffffffffff..." in out[0] and "media=2 links=0" in out[0]
    assert client.calls == [{}]
    assert len(out[0]) < 200


def test_references_list_name_filters_and_include_archived_is_forwarded(capsys):
    parsed, client = _refs_parser(["list", "--project", "demo", "--name", "Old set", "--include-archived"])
    assert parsed.handler(parsed) == 0
    lines = capsys.readouterr().out.strip().splitlines()
    assert len(lines) == 1 and lines[0].startswith("Old set [archived]")
    assert client.calls == [{"include_archived": True}]


def test_references_list_json_is_the_full_envelope(capsys):
    parsed, _ = _refs_parser(["list", "--project", "demo", "--json", "--include-archived"])
    assert parsed.handler(parsed) == 0
    envelope = json.loads(capsys.readouterr().out)
    assert set(envelope) == {"ok", "data", "error", "receipt", "idempotency_key"}
    assert envelope["data"][0][0]["media_references"][0]["media_id"] == "sha256:" + "f" * 64


def test_references_list_unknown_name_says_none(capsys):
    parsed, _ = _refs_parser(["list", "--project", "demo", "--name", "Nobody"])
    assert parsed.handler(parsed) == 0
    assert "no references" in capsys.readouterr().out
