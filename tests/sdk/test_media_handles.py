"""Media handles and generation ``references=[{ref, role}]`` (SDK admission).

The fake Runtime below holds one project with a character reference, a prior
generation run and its thumbnail; the tests drive the public ``sdk.invoke``
path end to end (admission -> wait -> managed outputs -> reference links) and
then materialize the admitted spec through the real generic host.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest

import astrid.sdk as sdk
from astrid.core.execution.generic_host import GenericPackHost, _assert_fixed_request_scope
from astrid.sdk.media_handles import (
    MediaHandleError,
    apply_media_handles,
    resolve_media_handle,
)
from astrid.sdk.results import InvocationResult

PRESENTER = b"presenter-png"
BASE = b"t00-base-png"
THUMB = b"t00-thumb-jpg"
NEW = [b"t01-a-png", b"t01-b-png"]


def _digest(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def ok(data):
    return SimpleNamespace(ok=True, data=data, error=None)


def fail(message):
    return SimpleNamespace(ok=False, data=None, error=SimpleNamespace(message=message))


def _row(data, *, port, role, ordinal, filename, media_type, task="t-00", run="r-00"):
    return {
        "association_id": f"a-{task}-{port}-{ordinal}", "project_id": "p", "run_id": run, "task_id": task,
        "attempt_id": "x", "output_port": port, "selector": {}, "ordinal": ordinal, "role": role,
        "filename": filename, "media_type": media_type, "size": len(data), "digest": _digest(data),
        "object_id": _digest(data), "durability": "durable",
    }


class FakeRuntime:
    def __init__(self):
        self.objects = {_digest(blob): blob for blob in (PRESENTER, BASE, THUMB, *NEW)}
        self.media_rows = {
            # The presenter was first stored as a generation output, so its
            # stored filename is the output port name (the real-world quirk).
            _digest(PRESENTER): {"digest": _digest(PRESENTER), "filename": "generated_images", "media_type": "image/png", "size": len(PRESENTER)},
            _digest(BASE): {"digest": _digest(BASE), "filename": "generated_images", "media_type": "image/png", "size": len(BASE)},
            _digest(THUMB): {"digest": _digest(THUMB), "filename": "thumbnail-0000.jpg", "media_type": "image/jpeg", "size": len(THUMB)},
        }
        self.reference = {
            "reference_id": "ref-presenter", "name": "Astrid presenter", "kind": "character", "archived": False,
            "media_references": [{"association_id": "as-0", "media_id": _digest(PRESENTER), "role": "canonical", "ordinal": 0, "is_primary": True}],
        }
        self.outputs = {"t-00": [
            _row(THUMB, port="thumbnail", role="thumbnail", ordinal=0, filename="thumbnail-0000.jpg", media_type="image/jpeg"),
            _row(BASE, port="generated_images", role="result", ordinal=1, filename="codex_001.png", media_type="image/png"),
        ]}
        self.created = []
        self.associations = []
        rt = self

        class References:
            def show(self, project, ref):
                if ref in ("ref-presenter", "Astrid presenter"):
                    return ok(rt.reference)
                return fail(f"reference {ref!r} not found")

            def associate(self, project, ref, *, media_id, role, metadata=None, idempotency_key=None):
                rt.associations.append({"ref": ref, "media_id": media_id, "role": role, "metadata": metadata, "key": idempotency_key})
                return ok({})

        class Media:
            def show(self, project, digest):
                row = rt.media_rows.get(digest)
                return ok(row) if row else fail("media object is not in the selected project")

            def read_bytes(self, digest):
                return rt.objects[digest]

        class Runs:
            def show(self, run_id):
                return ok({"task_ids": ["t-00"]}) if run_id == "r-00" else fail("run not found")

        class Tasks:
            def create(self, **kwargs):
                rt.created.append(kwargs)
                return ok({"run_id": "r-01", "task_id": "t-01", "attempt_id": "x-01"})

            def show(self, task_id):
                return ok({"state": "succeeded", "attempt_id": "x-01", "result": {}})

            def list_managed_outputs(self, task_id):
                return ok([rt.outputs.get(task_id, []), None])

        self.references, self.media, self.runs, self.tasks = References(), Media(), Runs(), Tasks()


@pytest.fixture()
def runtime():
    return FakeRuntime()


@pytest.fixture()
def view_root(tmp_path, monkeypatch):
    import astrid.sdk.media_handles as handles

    monkeypatch.setattr(handles, "_view_root", lambda project: tmp_path / "media-view" / project)
    return tmp_path / "media-view"


def test_reference_name_resolves_to_its_primary_image_with_a_usable_filename(runtime):
    descriptor, lineage = resolve_media_handle(runtime, "demo", "ref:Astrid presenter")
    assert descriptor == {"digest": _digest(PRESENTER), "filename": "Astrid-presenter.png",
                          "media_type": "image/png", "size_bytes": len(PRESENTER)}
    assert lineage["reference"]["id"] == "ref-presenter"


def test_run_handle_picks_the_image_by_ordinal_never_the_thumbnail(runtime):
    descriptor, lineage = resolve_media_handle(runtime, "demo", "run:r-00/generated_images#1")
    assert descriptor["digest"] == _digest(BASE) and descriptor["filename"] == "codex_001.png"
    assert lineage["output"] == {"run_id": "r-00", "task_id": "t-00", "output_port": "generated_images", "ordinal": 1}
    # no port: the first result row, still not the thumbnail
    assert resolve_media_handle(runtime, "demo", "run:r-00#1")[0]["digest"] == _digest(BASE)
    with pytest.raises(MediaHandleError, match="available: generated_images#1, thumbnail#0"):
        resolve_media_handle(runtime, "demo", "run:r-00/generated_images#0")


def test_digest_outside_the_project_is_refused_with_the_import_hint(runtime):
    with pytest.raises(MediaHandleError, match="media import"):
        resolve_media_handle(runtime, "demo", "sha256:" + "0" * 64)
    with pytest.raises(MediaHandleError, match="not a media handle"):
        resolve_media_handle(runtime, "demo", "/Users/me/P-01.png")


def test_roles_map_to_ports_and_conflicts_are_explained(runtime):
    cap = sdk.get_capability("generation.generate_image_codex", kind="executor")
    values, lineage, links = apply_media_handles(runtime, "demo", cap, {"references": [
        {"ref": "run:r-00/generated_images#1", "role": "source"},
        {"ref": "Astrid presenter", "role": "character", "depicts": True},  # bare name = reference
    ]})
    assert values["image_ref"]["digest"] == _digest(BASE)
    assert values["style_ref"]["digest"] == _digest(PRESENTER)
    assert "references" not in values
    assert [item["role"] for item in lineage] == ["source", "character"]
    assert links == [{"reference_id": "ref-presenter", "name": "Astrid presenter", "media_id": _digest(PRESENTER),
                      "port": "style_ref", "role": "character", "depicts": True}]
    with pytest.raises(MediaHandleError, match="'character' and 'style' both use the style_ref slot"):
        apply_media_handles(runtime, "demo", cap, {"references": [
            {"ref": "ref:Astrid presenter", "role": "character"}, {"ref": "ref:Astrid presenter", "role": "style"}]})
    with pytest.raises(MediaHandleError, match="same image"):
        apply_media_handles(runtime, "demo", cap, {"references": [
            {"ref": f"sha256:{_digest(PRESENTER)[7:]}", "role": "source"}, {"ref": "ref:Astrid presenter", "role": "character"}]})
    with pytest.raises(MediaHandleError, match="roles here: source, character, style, brand"):
        apply_media_handles(runtime, "demo", cap, {"references": [{"ref": "ref:Astrid presenter", "role": "face"}]})


def test_plain_descriptors_pass_through_and_bare_digests_become_descriptors(runtime):
    codex = sdk.get_capability("generation.generate_image_codex", kind="executor")
    descriptor = {"digest": _digest(BASE), "filename": "T-00.png", "media_type": "image/png", "size_bytes": 3}
    values, lineage, _ = apply_media_handles(runtime, "demo", codex, {"image_ref": descriptor, "prompt": "x"})
    assert values == {"image_ref": descriptor, "prompt": "x"} and lineage == []
    # pixel.snap used to fail on a bare digest ("image not found"); it now gets a named descriptor
    snap = sdk.get_capability("pixel.snap", kind="executor")
    values, lineage, _ = apply_media_handles(runtime, "demo", snap, {"image": _digest(BASE)})
    assert values["image"] == {"digest": _digest(BASE), "filename": f"media-{_digest(BASE)[7:19]}.png",
                               "media_type": "image/png", "size_bytes": len(BASE)}


def test_invoke_admits_resolved_descriptors_links_outputs_and_host_materializes(runtime, tmp_path, view_root):
    runtime.outputs["t-01"] = [
        _row(NEW[0], port="generated_images", role="result", ordinal=0, filename="codex_000.png", media_type="image/png", task="t-01", run="r-01"),
        _row(NEW[1], port="generated_images", role="result", ordinal=1, filename="codex_001.png", media_type="image/png", task="t-01", run="r-01"),
        _row(THUMB, port="thumbnail", role="thumbnail", ordinal=0, filename="thumbnail-0000.jpg", media_type="image/jpeg", task="t-01", run="r-01"),
    ]
    result = sdk.invoke(
        "generation.generate_image", kind="executor", project="demo", client=runtime, wait=True,
        inputs={"model": "qwen-image-edit", "mode": "edit", "execution": "codex", "prompt": "make it dawn", "count": 2,
                "references": [{"ref": "run:r-00/generated_images#1", "role": "source"},
                               {"ref": "ref:Astrid presenter", "role": "character", "depicts": True}]},
    )
    assert result.ok, result.error
    admitted = runtime.created[0]
    params = admitted["spec"]["params"]
    assert params["image_ref"]["digest"] == _digest(BASE) and params["style_ref"]["filename"] == "Astrid-presenter.png"
    assert admitted["input_manifest"] == [_digest(BASE), _digest(PRESENTER)]
    receipt = admitted["spec"]["authority_context"]["media_handles"]
    assert [(item["role"], item["handle"]) for item in receipt] == [
        ("source", "run:r-00/generated_images#1"), ("character", "ref:Astrid presenter")]
    # outputs feed the next call directly, thumbnails excluded
    assert [row["digest"] for row in result.output_rows("generated_images")] == [_digest(NEW[0]), _digest(NEW[1])]
    assert result.output("generated_images", 1)["filename"] == "codex_001.png"
    with pytest.raises(LookupError, match="generated_images\\[result\\]"):
        result.output("generated_images", 2)
    # lineage back onto the reference: one used_as_input, one depicts per image
    roles = [(item["role"], item["media_id"]) for item in runtime.associations]
    assert roles == [("used_as_input", _digest(PRESENTER)), ("depicts", _digest(NEW[0])), ("depicts", _digest(NEW[1]))]
    assert runtime.associations[0]["metadata"]["context_task"] == "t-01"
    assert all(link["ok"] for link in result.outputs["reference_links"])
    # a second generation with the same reference does not re-insert used_as_input
    # (the Runtime keeps one row per reference/media/role); it reports it as existing
    runtime.reference["media_references"].append(
        {"association_id": "as-1", "media_id": _digest(PRESENTER), "role": "used_as_input", "ordinal": 1, "is_primary": False})
    from astrid.sdk.media_handles import link_outputs_to_references
    again = link_outputs_to_references(runtime, "demo", [{"reference_id": "ref-presenter", "name": "Astrid presenter",
        "media_id": _digest(PRESENTER), "port": "style_ref", "role": "character", "depicts": False}],
        task_id="t-02", run_id="r-02", output_rows=[])
    assert again == [{"reference": "Astrid presenter", "role": "used_as_input", "media_id": _digest(PRESENTER), "ok": True, "existing": True}]

    # the admitted spec is byte-for-byte what the bounded host already accepts
    host = GenericPackHost(pack_roots=[Path("astrid/packs")], client=SimpleNamespace(get_object=lambda d: runtime.objects["sha256:" + d]))
    host.discover()
    record = host.capabilities["generation.generate_image_codex"]
    task = {"spec": {"spec": admitted["spec"], "input_object_ids": admitted["input_manifest"]}}
    _assert_fixed_request_scope(record, task)
    meta = record.definition.metadata
    values = host._materialize_inputs(
        task["spec"], tmp_path / "attempt", task_param_ports=meta["hc04_param_ports"],
        cas_param_ports=meta["hc04_cas_param_ports"], optional_cas_param_ports=meta["hc04_optional_cas_param_ports"],
        input_size_limits=meta["storage_input_max_bytes"], storage_estimate=admitted["storage_estimate"],
        file_input_names=frozenset(("image_ref", "style_ref", "brand_ref")),
    )
    assert Path(values["image_ref"]).read_bytes() == BASE
    assert Path(values["style_ref"]).name == "style_ref--Astrid-presenter.png"


def test_a_prior_result_row_is_itself_a_handle_for_the_next_capability(runtime):
    prior = InvocationResult(capability_id="generation.generate_image_codex", capability_type="executor",
                             native_kind="executor", ok=True, outputs={"managed_outputs": runtime.outputs["t-00"]})
    row = prior.output("generated_images")
    snap = sdk.get_capability("pixel.snap", kind="executor")
    values, lineage, _ = apply_media_handles(runtime, "demo", snap, {"image": row, "fit": "cover"})
    assert values["image"] == {"digest": _digest(BASE), "filename": "codex_001.png", "media_type": "image/png", "size_bytes": len(BASE)}
    assert lineage[0]["output"]["task_id"] == "t-00"


def test_codex_prompt_names_only_the_attached_roles(tmp_path):
    from astrid.core.generation.backends.codex import _build_codex_prompt

    character = tmp_path / "presenter.png"
    character.write_bytes(b"x")
    prompt = _build_codex_prompt({"prompt": "a new scene", "style_ref": str(character)})
    assert "1) the character/style reference" in prompt and "source image" not in prompt
    source = tmp_path / "base.png"
    source.write_bytes(b"y")
    prompt = _build_codex_prompt({"prompt": "edit", "image_ref": str(source), "style_ref": str(character)})
    assert "1) the source image" in prompt and "2) the character/style reference" in prompt


class FakeTransport:
    """The generated-client surface RemoteReferences/RemoteMedia call."""

    def __init__(self):
        self.calls = []
        self.rows = [
            {"reference_id": "ref-presenter", "name": "Astrid presenter", "archived": False},
            {"reference_id": "ref-old", "name": "Astrid mink", "archived": True},
            {"reference_id": "ref-mink", "name": "Astrid mink", "archived": False},
        ]

    def list_project_references(self, project, *, cursor=None, limit=50, include_archived=False):
        return [self.rows, None]

    def get_project_reference(self, project, ref):
        self.calls.append(("get", ref))
        return {"reference_id": ref, "version": 1}

    def associate_reference(self, project, ref, body, idempotency_key=None):
        self.calls.append(("associate", ref, body))
        return {"reference_id": ref}

    def ingest_project_object(self, project, data, *, media_type, idempotency_key, filename):
        return {"object_id": _digest(data), "digest": _digest(data), "filename": "generated_images", "media_type": media_type}


def test_reference_verbs_accept_names_and_associate_forwards_lineage():
    from astrid.sdk.remote import RemoteReferences

    transport = FakeTransport()
    refs = RemoteReferences(transport)
    assert refs.show("demo", "Astrid presenter").ok
    assert refs.show("demo", "astrid MINK").ok  # case-insensitive, the active one wins over the archived twin
    assert transport.calls[:2] == [("get", "ref-presenter"), ("get", "ref-mink")]
    missing = refs.show("demo", "Nobody")
    assert not missing.ok and missing.error.details["names"] == ["Astrid mink", "Astrid presenter"]
    assert refs.associate("demo", "Astrid presenter", media_id="sha256:" + "a" * 64, role="depicts",
                          context_task="t-1", metadata={"as": "character"}).ok
    assert transport.calls[-1] == ("associate", "ref-presenter", {
        "media_id": "sha256:" + "a" * 64, "role": "depicts", "metadata": {"as": "character", "context_task": "t-1"}})
    refused = refs.associate("demo", "Astrid presenter", media_id="sha256:" + "a" * 64, role="used_as_input")
    assert not refused.ok and "context task" in refused.error.message


def test_import_of_an_existing_object_reports_the_imported_filename(tmp_path):
    from astrid.sdk.remote import RemoteMedia

    source = tmp_path / "P-01-v1.png"
    source.write_bytes(PRESENTER)
    result = RemoteMedia(FakeTransport()).import_file(project="demo", path=source)
    assert result.ok
    assert result.data["filename"] == "P-01-v1.png" and result.data["stored_filename"] == "generated_images"



# -- BIG #2: one handle everywhere (lists, no client, results print handle + path) --

def _strip_outputs(task="t-01", run="r-01"):
    strip, meta = b"strip-png", b'{"count": 2}'
    return strip, [
        _row(strip, port="strip", role="result", ordinal=0, filename="strip.png", media_type="image/png", task=task, run=run),
        _row(meta, port="metadata", role="auxiliary", ordinal=0, filename="strip.json", media_type="application/json", task=task, run=run),
    ]


def test_a_list_of_handles_feeds_a_repeatable_input_end_to_end(runtime, view_root, tmp_path):
    # S16: pixel.strip's repeatable frame input rejected every list form
    strip, rows = _strip_outputs()
    runtime.objects[_digest(strip)] = strip
    runtime.outputs["t-01"] = rows
    result = sdk.invoke("pixel.strip", kind="executor", project="demo", client=runtime, wait=True,
                        inputs={"frame": ["run:r-00/generated_images#1", "ref:Astrid presenter", _digest(BASE)]})
    assert result.ok, result.error
    admitted = runtime.created[0]
    frames = admitted["spec"]["inputs"]["frame"]
    assert [f["digest"] for f in frames] == [_digest(BASE), _digest(PRESENTER), _digest(BASE)]  # order kept, repeats allowed
    assert admitted["input_manifest"] == [_digest(BASE), _digest(PRESENTER)]
    assert [h["index"] for h in admitted["spec"]["authority_context"]["media_handles"]] == [0, 1, 2]
    # the host materializes every frame in order and binds one --frame per item
    from astrid.core.contracts.binding import expand_command

    host = GenericPackHost(pack_roots=[Path("astrid/packs")], client=SimpleNamespace(get_object=lambda d: runtime.objects["sha256:" + d]))
    host.discover()
    record = host.capabilities["pixel.strip"]
    task = {"spec": {"spec": admitted["spec"], "input_object_ids": admitted["input_manifest"]}}
    values = host._materialize_inputs(task["spec"], tmp_path / "attempt", file_input_names=frozenset({"frame"}))
    assert [Path(v).read_bytes() for v in values["frame"]] == [BASE, PRESENTER, BASE]
    values.update(out=str(tmp_path / "out"), python_exec="python")
    for port in record.definition.inputs:
        if port.name not in values and port.default is not None:
            values[port.name] = port.default
    argv = expand_command(record.definition.command, record.definition.inputs, values, record.definition.metadata).argv
    assert argv.count("--frame") == 3
    # S18: every result row carries its handle and a viewable local path
    row = result.output("strip")
    assert row["handle"] == "run:r-01/strip#0"
    assert Path(row["path"]).read_bytes() == strip and Path(row["path"]).suffix == ".png"
    assert "path" not in result.output("metadata")  # auxiliary JSON: handle only
    assert "strip[0]" in str(result) and row["path"] in str(result)


def test_invoke_without_client_opens_one_or_fails_with_one_clear_error(runtime, view_root, monkeypatch):
    # S17: no client= used to fail twice ("requires a managed Runtime object", then "client is required")
    import astrid.sdk.invocation as invocation
    from astrid.sdk.client import AstridClient
    from astrid.sdk.exceptions import CapabilityPreconditionError

    strip, rows = _strip_outputs()
    runtime.objects[_digest(strip)] = strip
    runtime.outputs["t-01"] = rows
    monkeypatch.setattr(invocation, "_DEFAULT_CLIENT", None)
    monkeypatch.setattr(AstridClient, "open_from_launcher", classmethod(lambda cls, *a, **k: runtime))
    result = sdk.invoke("pixel.strip", kind="executor", project="demo", wait=True,
                        inputs={"frame": ["run:r-00/generated_images#1", "ref:Astrid presenter"]})
    assert result.ok and result.output("strip")["handle"] == "run:r-01/strip#0"

    def unavailable(cls, *a, **k):
        raise RuntimeError("runtime is not running")

    monkeypatch.setattr(invocation, "_DEFAULT_CLIENT", None)
    monkeypatch.setattr(AstridClient, "open_from_launcher", classmethod(unavailable))
    with pytest.raises(CapabilityPreconditionError, match=r"pass client=AstridClient\.open_from_launcher\(\)") as caught:
        sdk.invoke("pixel.strip", kind="executor", project="demo", wait=True,
                   inputs={"frame": ["run:r-00/generated_images#1", "ref:Astrid presenter"]})
    assert "runtime is not running" in str(caught.value)


def test_timeline_asset_resolver_returns_a_registry_entry(runtime):
    from astrid.sdk.media_handles import resolve_timeline_asset

    entry = resolve_timeline_asset(runtime, "demo", "ref:Astrid presenter")
    assert entry == {"key": "Astrid-presenter", "media_id": _digest(PRESENTER), "content_sha256": _digest(PRESENTER),
                     "type": "image/png", "handle": "ref:Astrid presenter", "filename": "Astrid-presenter.png"}
    assert resolve_timeline_asset(runtime, "demo", "run:r-00/generated_images#1")["key"] == "codex_001"


def test_media_open_writes_a_handle_to_a_file_and_states_the_preview_range(runtime, tmp_path, capsys):
    import argparse
    import json as _json

    from astrid.core.cli.domain_media import _cmd_open
    from astrid.sdk.media_open import open_reference

    target = tmp_path / "T-00.png"
    parsed = argparse.Namespace(client=runtime, project="demo", ref="run:r-00/generated_images#1", to=str(target),
                                force=False, materialize=False, cache_root=None, preview_bytes=None,
                                max_bytes=10 * 1024 * 1024, json=True)
    assert _cmd_open(parsed) == 0
    out = _json.loads(capsys.readouterr().out)
    assert out["data"]["local_path"] == str(target) and target.read_bytes() == BASE
    assert out["data"]["handle"] == "run:r-00/generated_images#1"
    # same bytes again: fine; different bytes: refused unless --force
    assert _cmd_open(parsed) == 0
    capsys.readouterr()
    target.write_bytes(b"other")
    assert _cmd_open(parsed) != 0
    assert "exists" in capsys.readouterr().out
    bad = open_reference(SimpleNamespace(), "demo", "sha256:" + "a" * 64, preview_bytes=-1, max_bytes=100)
    assert not bad.ok and "between 0 (no inline preview) and max_bytes (100)" in bad.error.message


def test_status_reports_an_unchecked_workspace_instead_of_crashing(monkeypatch, capsys):
    # S23: `astrid status` crashed with "'NoneType' object has no attribute 'ok'"
    import astrid.core.gateway.diagnostics as diagnostics
    import astrid.runtime_cli as runtime_cli
    import astrid.sdk.storage_root as storage_root
    from astrid.core.gateway.dispatch import _dispatch_status

    monkeypatch.setattr(storage_root, "resolve_runtime_data_root", lambda: "/tmp/astrid-data")
    monkeypatch.setattr(runtime_cli, "RuntimeCLI", lambda: object())
    observed = SimpleNamespace(ok=True, data={"status": "ready"})
    monkeypatch.setattr(diagnostics, "collect_diagnostic",
                        lambda *a, **k: ({"problemCode": "observation_timeout"}, None, observed))
    assert _dispatch_status([]) == 1
    printed = capsys.readouterr().out
    assert "workspace: not checked (observation_timeout)" in printed and "runtime: ready" in printed
    assert "python -m astrid doctor" in printed
