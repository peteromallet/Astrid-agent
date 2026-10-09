"""Offline receipt contract proof; expensive H3 domain work is mocked.

All calls execute the canonical main() wrappers and real shared receipt writer,
strict reader, and harvester. No GenericPackHost, Runtime, GPU, or provider call.
"""
from __future__ import annotations

import hashlib
import importlib
import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from astrid.core._shared import result_manifest as receipts

KINDS = ("prepare", "compile", "compose", "verify")
OUTPUTS = {
    "prepare": (("preparation", "preparation.json"),),
    "compile": (("compilation", "compilation.json"), ("managed_assets", "managed-assets.zip"),
                ("python", "workflow.py"), ("companion", "workflow.vibe.json"), ("source", "source.json")),
    "compose": (("candidate", "candidate.media"), ("composition", "composition-manifest.json")),
    "verify": (("verification", "verification.json"),),
}
DOMAIN_FUNCTION = {"prepare": "prepare_request", "compile": "compile_preparation",
                   "compose": "compose_candidate", "verify": "verify_candidate"}


def _json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")


@pytest.fixture
def scenario(tmp_path, monkeypatch):
    def make(kind):
        module = importlib.import_module(f"astrid.packs.h3_av.executors.{kind}.run")
        root = tmp_path / kind / "assigned"
        inputs = tmp_path / kind / "inputs"
        inputs.mkdir(parents=True)
        request, preparation, composition = [inputs / f"{name}.json" for name in ("request", "preparation", "composition")]
        for path in (request, preparation, composition):
            _json(path, {"kind": "mock-domain-input"})
        bundle, generated, candidate = [inputs / name for name in ("inputs.zip", "generated.zip", "candidate.media")]
        for path in (bundle, generated, candidate):
            path.write_bytes(b"mock-domain-input-bytes")
        expected = {}
        domain_calls = []
        if kind == "prepare":
            monkeypatch.setattr(module, "load_request", lambda _path: {"request": "mock"})
            def materialize(_request, _bundle, destination):
                destination.mkdir(parents=True)
                (destination / "unlisted-input.media").write_bytes(b"materialized input")
                return {}, {"source": {"sha256": "mock-input-identity"}}
            monkeypatch.setattr(module, "materialize_input_bundle", materialize)
            monkeypatch.setattr(module, "bundle_digest", lambda _path: "mock-bundle-digest")
            def domain(*_args, **_kwargs):
                domain_calls.append(True)
                result = {"kind": "h3_av_preparation", "preserved": "domain-schema"}
                final = dict(result, assets={"source": {"sha256": "mock-input-identity"}}, input_bundle_sha256="mock-bundle-digest")
                expected["preparation.json"] = (json.dumps(final, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode()
                return result
            argv = ["--request", str(request), "--input-bundle", str(bundle), "--out", str(root / "preparation.json")]
        elif kind == "compile":
            monkeypatch.setattr(module, "resolve_preparation_assets", lambda preparation, _bundle, _destination: preparation)
            def domain(_preparation, *, out_dir):
                domain_calls.append(True)
                out_dir.mkdir(parents=True)
                members = out_dir / "workflow-bundle"
                members.mkdir()
                workflow = {}
                for filename in ("workflow.py", "workflow.vibe.json", "source.json"):
                    content = f"canonical {filename}\n".encode()
                    path = members / filename
                    path.write_bytes(content)
                    expected[filename] = content
                    workflow[filename] = {"path": str(path), "sha256": hashlib.sha256(content).hexdigest()}
                expected["compilation.json"] = b'{"kind":"h3_av_compilation","preserved":"domain-schema"}\n'
                expected["managed-assets.zip"] = b"mock-managed-archive"
                for filename in ("compilation.json", "managed-assets.zip"):
                    (out_dir / filename).write_bytes(expected[filename])
                return {"manifest_path": str(out_dir / "compilation.json"),
                        "managed_assets": {"path": str(out_dir / "managed-assets.zip")}, "workflow": workflow}
            argv = ["--preparation", str(preparation), "--input-bundle", str(bundle), "--out", str(root)]
        elif kind == "compose":
            def domain(**kwargs):
                domain_calls.append(True)
                out_dir = kwargs["out_dir"]
                out_dir.mkdir(parents=True)
                original = out_dir / "original.mock-container"
                original.write_bytes(b"unaltered candidate container bytes")
                result = {"kind": "h3_av_composition", "preserved": "domain-schema", "candidate": {"path": str(original)}}
                _json(out_dir / "composition-manifest.json", result)
                final = dict(result, candidate={"path": str((out_dir / "candidate.media").resolve())})
                expected["candidate.media"] = original.read_bytes()
                expected["composition-manifest.json"] = (json.dumps(final, indent=2, sort_keys=True) + "\n").encode()
                return result
            argv = ["--preparation", str(preparation), "--generated", str(generated), "--out", str(root)]
        else:
            def domain(**_kwargs):
                domain_calls.append(True)
                result = {"kind": "h3_av_verification", "preserved": "domain-schema", "status": "verified"}
                expected["verification.json"] = (json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode()
                return result
            argv = ["--preparation", str(preparation), "--composition", str(composition), "--candidate", str(candidate), "--out", str(root / "verification.json")]
        monkeypatch.setattr(module, DOMAIN_FUNCTION[kind], domain)
        return SimpleNamespace(kind=kind, module=module, root=root, argv=argv, expected=expected, domain=domain, calls=domain_calls)
    return make


def _harvest(case):
    definition_path = Path(case.module.__file__).with_name("executor.yaml")
    definition_data = yaml.safe_load(definition_path.read_text(encoding="utf-8"))
    assert definition_data["metadata"]["output_result_manifest"] is True
    assert tuple(output["name"] for output in definition_data["outputs"]) == tuple(name for name, _ in OUTPUTS[case.kind])
    definition = SimpleNamespace(metadata=definition_data["metadata"], outputs=tuple(SimpleNamespace(**output) for output in definition_data["outputs"]))
    return receipts.harvest_staged_outputs(case.root, definition=definition, require=True)


@pytest.mark.parametrize("kind", KINDS)
def test_actual_main_emits_verified_declared_receipt(kind, scenario):
    case = scenario(kind)
    assert case.module.main(case.argv) == 0
    manifest_path = case.root / "manifest.json"
    raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    validated = receipts.read_result_manifest(manifest_path, staging_root=case.root)
    assert raw["kind"] == f"h3_av.{kind}"
    assert raw["schema_version"] == 1
    assert datetime.fromisoformat(raw["created"]).utcoffset().total_seconds() == 0
    assert raw["warnings"] == []
    assert raw["inputs"]
    assert [(entry["name"], entry["path"], entry["ordinal"]) for entry in raw["outputs"]] == [(name, path, i) for i, (name, path) in enumerate(OUTPUTS[kind])]
    assert [entry.ordinal for entry in validated.outputs] == list(range(len(OUTPUTS[kind])))
    before = {path: (case.root / path).read_bytes() for _, path in OUTPUTS[kind]}
    assert before == case.expected  # domain schemas/bytes and final copy/rewrite survive
    for entry in raw["outputs"]:
        assert entry["content_hash"] == "sha256:" + hashlib.sha256(before[entry["path"]]).hexdigest()
        assert entry["bytes"] == len(before[entry["path"]])
    # Neither receipt itself nor implementation input/workflow/old candidate files are outputs.
    (case.root / "undeclared.extra").write_bytes(b"must never become a result")
    harvested = _harvest(case)
    assert [(entry["name"], Path(entry["path"]).name, entry["ordinal"]) for entry in harvested] == [(name, path, i) for i, (name, path) in enumerate(OUTPUTS[kind])]
    assert all(entry["ordinal_explicit"] and entry["role"] == "result" for entry in harvested)
    assert {path: (case.root / path).read_bytes() for _, path in OUTPUTS[kind]} == before
    if kind == "compile":
        assert all((case.root / name).read_bytes() == (case.root / "workflow-bundle" / name).read_bytes() for name in ("workflow.py", "workflow.vibe.json", "source.json"))
    if kind == "compose":
        assert (case.root / "original.mock-container").read_bytes() == before["candidate.media"]
        assert json.loads(before["composition-manifest.json"])["candidate"]["path"] == str((case.root / "candidate.media").resolve())


@pytest.mark.parametrize("kind", KINDS)
def test_domain_failure_never_emits_success_receipt(kind, scenario, monkeypatch):
    case = scenario(kind)
    error = RuntimeError("domain failure")
    def fail(*_args, **_kwargs):
        raise error
    monkeypatch.setattr(case.module, DOMAIN_FUNCTION[kind], fail)
    with pytest.raises(RuntimeError, match="domain failure") as raised:
        case.module.main(case.argv)
    assert raised.value is error
    assert not (case.root / "manifest.json").exists()
    with pytest.raises(receipts.HarvestError):
        _harvest(case)


@pytest.mark.parametrize("kind", KINDS)
def test_receipt_write_failure_fails_closed(kind, scenario, monkeypatch, capsys):
    case = scenario(kind)
    def fail(_path, _payload):
        raise OSError("receipt persistence failed")
    monkeypatch.setattr(receipts, "_atomic_write_json", fail)
    with pytest.raises(receipts.AstridError, match="receipt persistence failed"):
        case.module.main(case.argv)
    assert capsys.readouterr().out == ""
    assert not (case.root / "manifest.json").exists()
    with pytest.raises(receipts.HarvestError):
        _harvest(case)


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("symlink", (False, True))
def test_stale_receipt_is_preserved_and_rejected_before_domain_work(kind, symlink, scenario, tmp_path):
    case = scenario(kind)
    case.root.mkdir(parents=True)
    stale = case.root / "manifest.json"
    if symlink:
        stale.symlink_to(tmp_path / "absent-receipt-target")  # broken symlink also blocks
    else:
        stale.write_bytes(b"previous attempt receipt")
    with pytest.raises(FileExistsError, match="fresh output root"):
        case.module.main(case.argv)
    assert case.calls == []
    if symlink:
        assert stale.is_symlink()
    else:
        assert stale.read_bytes() == b"previous attempt receipt"


@pytest.mark.parametrize("kind", ("prepare", "verify"))
def test_file_output_cannot_overwrite_receipt_name(kind, scenario):
    case = scenario(kind)
    case.argv[-1] = str(case.root / "manifest.json")
    with pytest.raises(ValueError, match="reserved result receipt filename"):
        case.module.main(case.argv)
    assert case.calls == []
    assert not (case.root / "manifest.json").exists()


@pytest.mark.parametrize("kind", KINDS)
def test_altered_declared_bytes_are_rejected_by_reader_and_harvester(kind, scenario):
    case = scenario(kind)
    assert case.module.main(case.argv) == 0
    victim = case.root / OUTPUTS[kind][0][1]
    original = victim.read_bytes()
    victim.write_bytes(bytes([original[0] ^ 1]) + original[1:])  # same size, distinct hash
    with pytest.raises(receipts.ResultManifestError, match="hashes to"):
        receipts.read_result_manifest(case.root / "manifest.json", staging_root=case.root)
    with pytest.raises(receipts.HarvestError, match="hashes to"):
        _harvest(case)


def test_compile_member_hash_guard_precedes_receipt(scenario, monkeypatch):
    case = scenario("compile")
    def corrupt(*args, **kwargs):
        result = case.domain(*args, **kwargs)
        result["workflow"]["workflow.py"]["sha256"] = "0" * 64
        return result
    monkeypatch.setattr(case.module, "compile_preparation", corrupt)
    with pytest.raises(RuntimeError, match="failed hash verification"):
        case.module.main(case.argv)
    assert not (case.root / "manifest.json").exists()


def test_compose_final_copy_failure_precedes_receipt(scenario, monkeypatch):
    case = scenario("compose")
    def fail(*_args, **_kwargs):
        raise OSError("candidate copy failed")
    monkeypatch.setattr(case.module.shutil, "copyfile", fail)
    with pytest.raises(OSError, match="candidate copy failed"):
        case.module.main(case.argv)
    assert not (case.root / "manifest.json").exists()


@pytest.mark.parametrize("kind", ("prepare", "verify"))
def test_file_valued_out_uses_parent_root_and_actual_filename(kind, scenario):
    case = scenario(kind)
    custom = case.root / "custom-domain-result.json"
    case.argv[-1] = str(custom)
    assert case.module.main(case.argv) == 0
    raw = json.loads((case.root / "manifest.json").read_text(encoding="utf-8"))
    assert [(entry["name"], entry["path"]) for entry in raw["outputs"]] == [(OUTPUTS[kind][0][0], custom.name)]
    validated = receipts.read_result_manifest(case.root / "manifest.json", staging_root=case.root)
    assert validated.outputs[0].content_hash == "sha256:" + hashlib.sha256(custom.read_bytes()).hexdigest()
    harvested = _harvest(case)
    assert harvested[0]["path"] == str(custom.resolve())
    assert harvested[0]["name"] == OUTPUTS[kind][0][0]


@pytest.mark.parametrize("kind", ("prepare", "verify"))
@pytest.mark.parametrize("dangling_receipt_alias", (True, False))
def test_file_output_alias_is_rejected_before_domain_work(kind, dangling_receipt_alias, scenario):
    case = scenario(kind)
    case.root.mkdir(parents=True)
    output = Path(case.argv[-1])
    target = case.root / ("manifest.json" if dangling_receipt_alias else "existing-domain.json")
    preserved_bytes = b"pre-existing aliased domain bytes"
    if not dangling_receipt_alias:
        target.write_bytes(preserved_bytes)
    output.symlink_to(target)
    alias_before = output.readlink()
    with pytest.raises(ValueError, match="must not be a symbolic link"):
        case.module.main(case.argv)
    assert case.calls == []
    assert output.is_symlink() and output.readlink() == alias_before
    assert not (case.root / "manifest.json").exists()
    if not dangling_receipt_alias:
        assert target.read_bytes() == preserved_bytes


@pytest.mark.parametrize("kind", ("prepare", "verify"))
@pytest.mark.parametrize("basename", ("MANIFEST.JSON", "Manifest.Json"))
def test_file_output_reserved_case_variant_is_rejected_before_domain_work(kind, basename, scenario):
    case = scenario(kind)
    case.argv[-1] = str(case.root / basename)
    with pytest.raises(ValueError, match="reserved result receipt filename"):
        case.module.main(case.argv)
    assert case.calls == []
    assert not (case.root / "manifest.json").exists()
    assert not Path(case.argv[-1]).exists()
