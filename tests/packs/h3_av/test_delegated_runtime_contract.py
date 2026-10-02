"""Delegated lineage regressions retained from the staged donor."""
from types import SimpleNamespace
import pytest
from astrid.packs.h3_av.orchestrators.transform.run import _bound_producer_ref, _managed_rows


def test_managed_compose_verify_preserve_main_algorithms_after_relocation(tmp_path):
    import hashlib
    import json
    import shutil
    import subprocess
    from astrid.packs.h3_av.executors.prepare.run import main as prepare
    from astrid.packs.h3_av.executors.compile.run import main as compile_request
    from astrid.packs.h3_av.executors.compose.run import main as compose
    from astrid.packs.h3_av.executors.verify.run import main as verify
    from astrid.packs.h3_av.executors.publication_finalizer.run import main as finalize
    from astrid.packs.h3_av.src.input_bundle import build_input_bundle
    from tests.packs.h3_av.test_runtime_contract import _request, _synthetic_av

    source = tmp_path / "caller" / "source.mp4"
    source.parent.mkdir()
    _synthetic_av(source, 2, color="blue")
    from astrid.packs.h3_av.src.request import normalize_request
    value = _request().value
    value["source"]["range"] = [0, 2]
    request = normalize_request(value)
    request_path = tmp_path / "request.json"
    request_path.write_text(json.dumps(request.value))
    bundle = build_input_bundle(request, {"source": str(source)}, tmp_path / "inputs.zip")
    prep = tmp_path / "prepare" / "preparation.json"
    assert prepare(["--request", str(request_path), "--input-bundle", str(bundle),
                    "--out", str(prep)]) == 0
    compiled = tmp_path / "compile"
    assert compile_request(["--preparation", str(prep), "--input-bundle", str(bundle),
                            "--out", str(compiled)]) == 0
    compilation = tmp_path / "compilation.json"
    shutil.copyfile(compiled / "compilation.json", compilation)
    shutil.rmtree(compiled)
    shutil.rmtree(source.parent)
    generated = tmp_path / "generated.mp4"
    _synthetic_av(generated, 2, color="red", muxed=False)
    audio = tmp_path / "generated.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                    "sine=frequency=880:sample_rate=48000", "-t", "2", str(audio)], check=True)
    composed = tmp_path / "compose"
    common = ["--preparation", str(prep), "--input-bundle", str(bundle),
              "--compilation", str(compilation)]
    assert compose([*common, "--generated", str(generated), "--generated-audio", str(audio),
                    "--out", str(composed)]) == 0
    composition = tmp_path / "composition.json"
    candidate = tmp_path / "candidate.media"
    shutil.copyfile(composed / "composition-manifest.json", composition)
    shutil.copyfile(composed / "candidate.media", candidate)
    shutil.rmtree(composed)
    verified = tmp_path / "verify"
    args = [*common, "--composition", str(composition), "--candidate", str(candidate),
            "--out", str(verified)]
    assert verify(args) == 0
    assert json.loads((verified / "verification.json").read_text())["status"] == "verified"
    final = tmp_path / "final"
    assert finalize(["--verified-candidate", str(verified / "verified-candidate.mkv"),
                     "--out", str(final)]) == 0
    assert (final / "verified-candidate.mkv").read_bytes() == candidate.read_bytes()
    expected = "sha256:" + hashlib.sha256(candidate.read_bytes()).hexdigest()
    assert json.loads((final / "manifest.json").read_text())["outputs"][0]["content_hash"] == expected
    # A failed re-verification cannot leave a harvestable stale result manifest.
    candidate.write_bytes(b"tampered")
    with pytest.raises(ValueError, match="digest"):
        verify(args)
    assert not (verified / "manifest.json").exists()

def test_bound_producer_ref_contains_runtime_lineage() -> None:
    row = {
        "task_id": "producer-task",
        "association_id": "managed-output",
        "output_port": "preparation",
    }
    assert _bound_producer_ref("preparation", "preparation", row) == {
        "name": "preparation",
        "producer_task_id": "producer-task",
        "association_id": "managed-output",
        "output_port": "preparation",
    }


def test_bound_producer_ref_rejects_unbound_output() -> None:
    with pytest.raises(RuntimeError, match="no Runtime association"):
        _bound_producer_ref("preparation", "preparation", {"task_id": "producer-task"})


def test_managed_output_row_wins_over_duplicate_legacy_artifact() -> None:
    digest = "sha256:" + "b" * 64
    result = SimpleNamespace(
        outputs={"artifacts": [{"name": "preparation", "object_id": digest, "role": "result"}]},
        raw_result={
            "managed_outputs": [{
                "name": "preparation",
                "object_id": digest,
                "role": "result",
                "task_id": "producer-task",
                "association_id": "managed-output",
            }]
        },
    )
    rows = _managed_rows(result, "preparation")
    assert len(rows) == 1
    assert rows[0]["association_id"] == "managed-output"


def test_managed_rows_preserve_distinct_associations_with_same_bytes() -> None:
    digest = "sha256:" + "c" * 64
    result = SimpleNamespace(
        outputs={
            "managed_outputs": [
                {"name": "preparation", "object_id": digest, "role": "result", "association_id": "a1"},
                {"name": "preparation", "object_id": digest, "role": "result", "association_id": "a2"},
            ]
        },
        raw_result={},
    )
    rows = _managed_rows(result, "preparation")
    assert [row["association_id"] for row in rows] == ["a1", "a2"]
