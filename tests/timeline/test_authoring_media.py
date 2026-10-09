from __future__ import annotations

import copy
import importlib.util
import json
import shutil
import wave
from pathlib import Path

import pytest
from PIL import Image

from astrid.sdk.authoring_bundle import (
    diff_authoring_candidate,
    import_authoring_media,
    open_authoring_bundle,
    plan_authoring_media,
    publish_authoring_candidate,
)
from tests.timeline.test_authoring_bundle import AUDIO, OLD, _closure


def bundle():
    parent, shots, internals = _closure(shared=False)
    return open_authoring_bundle(
        parent, shot_revisions=shots, internal_timeline_revisions=internals
    )


def picture(tmp_path, name="new.png", colour="red"):
    path = tmp_path / name
    Image.new("RGB", (16, 12), colour).save(path)
    return path


def edit(work, path):
    shot = next(iter(work["shots"].values()))
    shot["internal_timeline"]["registry"]["assets"]["old-picture"]["local_path"] = path
    return shot


class Catalog:
    def __init__(self):
        self.calls = []
        self.generations = []
        self.variants = {}

    def list_generations(self, project_id, **kwargs):
        return self.generations, None

    def list_variants(self, generation_id, **kwargs):
        return self.variants[generation_id], None

    def import_project_media(self, project_id, payload, **kwargs):
        self.calls.append((project_id, payload, kwargs))
        gid = f"generation-{len(self.calls)}"
        variant = {
            "variant_id": "variant-" + gid,
            "object_id": kwargs["expected_digest"],
            "variant_type": "original",
        }
        self.generations.append(
            {
                "generation_id": gid,
                "metadata": {
                    "provenance": {"origin": "imported", "source": "external_upload"},
                    "params": {
                        "mime_type": kwargs["media_type"],
                        **{
                            key: kwargs.get(key)
                            for key in ("filename", "width", "height", "duration_seconds")
                        },
                    },
                },
            }
        )
        self.variants[gid] = [variant]
        return {
            "data": {
                "status": "completed",
                "asset_id": kwargs["expected_digest"],
                "generation_id": gid,
                "variant_id": variant["variant_id"],
            }
        }


def test_unchanged_json_roundtrip_has_empty_diff(tmp_path):
    work = json.loads(json.dumps(bundle()))
    assert diff_authoring_candidate(work)["change_count"] == 0
    plan = plan_authoring_media(work, base_directory=tmp_path)
    assert plan["imports"] == []
    assert plan["candidate"] == work
    work["placements"][0]["duration_ms"] += 100
    assert diff_authoring_candidate(work)["change_count"] > 0


def test_local_registry_replacement_is_readonly_and_strips_stale_authority(tmp_path):
    path = picture(tmp_path)
    work = bundle()
    shot = edit(work, path.name)
    entry = shot["internal_timeline"]["registry"]["assets"]["old-picture"]
    entry.update(
        content_sha256=OLD.removeprefix("sha256:"), source={"object_id": OLD}, src="/obsolete.png"
    )
    shot["payload"]["items"][0]["media_id"] = OLD.removeprefix("sha256:")
    shot["payload"]["assets"] = [
        {
            "asset_id": "old-picture",
            "object_id": OLD,
            "digest": OLD,
            "role": "image",
            "scope": {"project_id": work["project_id"]},
            "source": {
                "media_id": OLD,
                "content_sha256": OLD.removeprefix("sha256:"),
                "type": "image/png",
                "file": "/obsolete.png",
            },
        }
    ]
    original = copy.deepcopy(work)
    plan = plan_authoring_media(work, base_directory=tmp_path)
    assert work == original
    row = plan["imports"][0]
    prepared = next(iter(plan["candidate"]["shots"].values()))
    rewritten = prepared["internal_timeline"]["registry"]["assets"]["old-picture"]
    assert rewritten["media_id"] == row["digest"]
    assert rewritten["type"] == "image"
    assert rewritten["opaque_asset"] == "keep"
    assert not set(("file", "local_path", "src", "source", "content_sha256")) & rewritten.keys()
    assert prepared["payload"]["items"][0]["media_id"] == row["digest"]
    descriptor = prepared["payload"]["assets"][0]
    assert descriptor["object_id"] == descriptor["digest"] == row["digest"]
    assert descriptor["source"]["media_id"] == row["digest"]
    assert descriptor["source"]["content_sha256"] == row["digest"].removeprefix("sha256:")
    assert "file" not in descriptor["source"]
    assert prepared["payload"]["generation_inputs"]["visual"]["object_id"] == OLD
    assert prepared["base_payload"] == original["shots"][prepared["shot_id"]]["base_payload"]


def test_import_reuses_catalog_across_retry_keys_and_preserves_distinct_names(tmp_path):
    path = picture(tmp_path)
    work = bundle()
    edit(work, path.name)
    plan = plan_authoring_media(work, base_directory=tmp_path)
    catalog = Catalog()
    first = import_authoring_media(plan, catalog)
    replay = import_authoring_media(plan, catalog)
    assert len(catalog.calls) == 1
    assert replay["imports"][0]["reused"] is True
    assert first["candidate"] == replay["candidate"]
    assert catalog.calls[0][2]["idempotency_key"] == plan["imports"][0]["import_key"]
    renamed = tmp_path / "different-name.png"
    shutil.copyfile(path, renamed)
    other = bundle()
    edit(other, renamed.name)
    import_authoring_media(plan_authoring_media(other, base_directory=tmp_path), catalog)
    assert len(catalog.calls) == 2
    assert catalog.calls[0][2]["expected_digest"] == catalog.calls[1][2]["expected_digest"]
    assert catalog.calls[0][2]["idempotency_key"] != catalog.calls[1][2]["idempotency_key"]


@pytest.mark.parametrize(
    "name,content", [("missing.png", None), ("unsupported.txt", b"text"), ("bad.wav", b"invalid")]
)
def test_all_sources_preflight_before_any_import_or_publication(tmp_path, name, content):
    good = picture(tmp_path)
    if content is not None:
        (tmp_path / name).write_bytes(content)
    work = bundle()
    shot = edit(work, good.name)
    shot["internal_timeline"]["registry"]["assets"]["voice"]["local_path"] = name
    catalog = Catalog()
    with pytest.raises((ValueError, FileNotFoundError, RuntimeError)):
        plan_authoring_media(work, base_directory=tmp_path)
    assert catalog.calls == []
    assert work["base_parent"]["revision_id"] == "parent-1"


def test_audio_local_selector_is_probed_and_keeps_audio_mirror(tmp_path):
    path = tmp_path / "voice.wav"
    with wave.open(str(path), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(8000)
        audio.writeframes(b"\0\0" * 8000)
    work = bundle()
    shot = next(iter(work["shots"].values()))
    shot["internal_timeline"]["clips"][1]["asset"] = "./voice.wav"
    plan = plan_authoring_media(work, base_directory=tmp_path)
    row = plan["imports"][0]
    assert row["media_type"].startswith("audio/")
    assert row["duration_seconds"] == 1.0
    assert "width" not in row
    prepared = next(iter(plan["candidate"]["shots"].values()))
    clip = prepared["internal_timeline"]["clips"][1]
    assert prepared["internal_timeline"]["registry"]["assets"][clip["asset"]]["type"] == "audio"
    assert prepared["payload"]["items"][1]["media_id"] == row["digest"]
    assert prepared["payload"]["audio_bindings"][0]["object_id"] == row["digest"]
    assert prepared["base_payload"]["items"][1]["media_id"] == AUDIO


def test_changed_file_after_preflight_imports_nothing(tmp_path):
    path = picture(tmp_path)
    work = bundle()
    edit(work, path.name)
    plan = plan_authoring_media(work, base_directory=tmp_path)
    picture(tmp_path, colour="blue")
    catalog = Catalog()
    with pytest.raises(ValueError, match="changed after check"):
        import_authoring_media(plan, catalog)
    assert catalog.calls == []


def test_import_failure_cannot_reach_publication(tmp_path):
    path = picture(tmp_path)
    work = bundle()
    edit(work, path.name)
    plan = plan_authoring_media(work, base_directory=tmp_path)
    catalog = Catalog()
    catalog.import_project_media = lambda *args, **kwargs: {"status": "pending"}
    with pytest.raises(ValueError, match="completed managed catalog pair"):
        import_authoring_media(plan, catalog)


def test_document_publish_stale_head_prevents_import(tmp_path, monkeypatch):
    import sys
    from contextlib import nullcontext
    from types import SimpleNamespace

    script = Path(__file__).parents[2] / "astrid/packs/rendering/skill/scripts/timeline_document.py"
    spec = importlib.util.spec_from_file_location("timeline_document_test", script)
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    path = picture(tmp_path)
    work = bundle()
    edit(work, path.name)
    document = tmp_path / "timeline.json"
    document.write_text(json.dumps(work))
    shown = SimpleNamespace(ok=True, data={"summary": {"head_revision_id": "advanced-head"}})
    client = SimpleNamespace(timelines=SimpleNamespace(open_composition=lambda *args: shown))
    monkeypatch.setattr(
        helper.AstridClient, "open_from_launcher", lambda **kwargs: nullcontext(client)
    )
    monkeypatch.setattr(
        helper, "workspace", lambda: pytest.fail("stale check must precede import connection")
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [str(script), "publish", "--file", str(document), "--idempotency-key", "stale-edit"],
    )
    with pytest.raises(RuntimeError, match="Stale head"):
        helper.main()
    assert document.read_text() == json.dumps(work)
    assert not document.with_suffix(".publication.json").exists()


def test_real_runtime_import_and_parent_publication_roundtrip(tmp_path):
    service_module = pytest.importorskip("runtime_protocol.service")
    store_module = pytest.importorskip("runtime_protocol.store")
    root = tmp_path / "isolated-realm"
    store_module.RealmStore.initialize(root).close()
    service = service_module.RuntimeService(root)
    try:
        project = service.create_project(
            {"slug": "checkin-test", "name": "Checkin test"}, idempotency_key="project"
        )
        project_id = project["id"]
        service.create_timeline(project_id, "main", idempotency_key="timeline")
        parent, shots, internals = _closure(shared=False)
        old = service.ingest(
            project_id, b"old-picture", media_type="image/png", idempotency_key="old-picture"
        )["data"]["object_id"]
        old_audio = service.ingest(
            project_id, b"old-audio", media_type="audio/wav", idempotency_key="old-audio"
        )["data"]["object_id"]
        # Fixture identities describe a detached shape; use actual sandbox
        # project objects and let Runtime produce authoritative revision digests.
        publication = {
            "project_id": project_id,
            "timeline_id": "main",
            "expected_head": None,
            "parent_revision_id": "parent-1",
            "parent_composition": parent["payload"],
            "shot_revisions": [
                {
                    "shot_id": shots[0]["shot_id"],
                    "revision_id": shots[0]["revision_id"],
                    "internal_timeline_revision_id": "internal-1",
                    "payload": shots[0]["payload"],
                }
            ],
            "internal_timeline_revisions": [
                {
                    "timeline_id": "main",
                    "revision_id": "internal-1",
                    "payload": internals[0]["payload"],
                }
            ],
        }
        publication = json.loads(
            json.dumps(publication).replace(OLD, old).replace(AUDIO, old_audio)
        )
        service.publish_parent_composition(project_id, "main", publication, idempotency_key="seed")
        work = open_authoring_bundle(
            service.get_project_parent_composition_revision(project_id, "main", "parent-1"),
            shot_revisions=[
                service.get_project_shot_revision(project_id, "shot-shared", "shot-rev-1")
            ],
            internal_timeline_revisions=[
                service.get_project_timeline_revision(project_id, "main", "internal-1")
            ],
        )
        image = picture(tmp_path)
        edit(work, image.name)
        audio_path = tmp_path / "voice.wav"
        with wave.open(str(audio_path), "wb") as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(8000)
            audio.writeframes(b"\0\0" * 8000)
        shot = next(iter(work["shots"].values()))
        shot["internal_timeline"]["registry"]["assets"]["voice"]["local_path"] = audio_path.name
        plan = plan_authoring_media(work, base_directory=tmp_path)

        class Transport:
            def list_generations(self, project_id, **kwargs):
                page = service.list_generations(project_id, **kwargs)
                return page["items"], page.get("next_cursor")

            def list_variants(self, generation_id, **kwargs):
                page = service.list_variants(generation_id, **kwargs)
                return page["items"], page.get("next_cursor")

            def import_project_media(self, project_id, payload, **kwargs):
                kwargs["original_name"] = kwargs.pop("filename")
                return service.import_media(project_id, payload, actor_id="test-user", **kwargs)

            def publish_parent_composition(self, project_id, timeline_id, payload, **kwargs):
                return service.publish_parent_composition(
                    project_id, timeline_id, payload, **kwargs
                )["data"]

        transport = Transport()
        imported = import_authoring_media(plan, transport)
        assert len(service.list_generations(project_id)["items"]) == 2
        assert {row["type"] for row in service.list_generations(project_id)["items"]} == {
            "image",
            "audio",
        }
        replay = import_authoring_media(plan, transport)
        assert all(row["reused"] for row in replay["imports"])
        assert len(service.list_generations(project_id)["items"]) == 2
        receipt = publish_authoring_candidate(
            imported["candidate"], transport, idempotency_key="checkin"
        )
        head = receipt["publication"]["new_head"]
        saved_parent = service.get_project_parent_composition_revision(project_id, "main", head)
        occurrence = saved_parent["payload"]["occurrences"][0]
        saved_shot = service.get_project_shot_revision(
            project_id, occurrence["shot_id"], occurrence["shot_revision_id"]
        )
        saved_internal = service.get_project_timeline_revision(
            project_id, "main", saved_shot["internal_timeline_revision_id"]
        )
        selected = {item["media_id"] for item in saved_shot["payload"]["items"]}
        assert selected == {row["digest"] for row in plan["imports"]}
        assert selected <= {
            row["media_id"] for row in receipt["publication"]["dependency_manifest"]["media"]
        }
        assert "local_path" not in json.dumps(saved_internal["payload"])
        assert str(tmp_path) not in json.dumps(saved_internal["payload"])
        reopened = open_authoring_bundle(
            saved_parent, shot_revisions=[saved_shot], internal_timeline_revisions=[saved_internal]
        )
        assert diff_authoring_candidate(reopened)["change_count"] == 0
    finally:
        service.close()
