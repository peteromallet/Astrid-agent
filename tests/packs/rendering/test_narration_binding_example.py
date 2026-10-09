from __future__ import annotations

import importlib.util
import json
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest


def _helper():
    path = Path(__file__).resolve().parents[3] / "astrid/packs/rendering/skill/scripts/timeline_document.py"
    spec = importlib.util.spec_from_file_location("timeline_document_narration_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return path, module


def _candidate(metadata=None):
    return {
        "project_id": "project-1",
        "timeline_id": "timeline-1",
        "base_parent": {"revision_id": "parent-1"},
        "shots": {"shot-1": {"payload": {"metadata": metadata or {}, "text_bindings": []}}},
    }


def _run_bind(helper, script_path, monkeypatch, file, text_file, client, *, expected_head=3):
    monkeypatch.setattr(helper.AstridClient, "open_from_launcher", lambda **kwargs: nullcontext(client))
    monkeypatch.setattr(
        "sys.argv",
        [str(script_path), "bind-script", "--file", str(file), "--shot", "shot-1",
         "--text-file", str(text_file), "--expected-head", str(expected_head),
         "--idempotency-key", "bind-script-test"],
    )
    helper.main()


def test_bind_script_passes_expected_binding_head_and_pins_returned_descriptor(tmp_path, monkeypatch):
    script_path, helper = _helper()
    file = tmp_path / "timeline.json"
    original = _candidate()
    file.write_text(json.dumps(original), encoding="utf-8")
    text_file = tmp_path / "narration.txt"
    text_file.write_text("A concise line.", encoding="utf-8")
    calls = []
    pin = {"binding_id": "binding-1", "kind": "voiceover_script", "head": 4,
           "media_id": "sha256:" + "a" * 64, "content_hash": "sha256:" + "a" * 64,
           "byte_size": 15, "event_stream_id": "binding-1:shot.text_binding"}
    client = SimpleNamespace(
        timelines=SimpleNamespace(open_composition=lambda *args: SimpleNamespace(ok=True, data={"summary": {"head_revision_id": "parent-1"}})),
        shots=SimpleNamespace(set_text_binding=lambda *args, **kwargs: calls.append((args, kwargs)) or SimpleNamespace(ok=True, data=pin)),
    )
    _run_bind(helper, script_path, monkeypatch, file, text_file, client)
    assert calls[0][1]["expected_head"] == 3
    saved = json.loads(file.read_text(encoding="utf-8"))
    assert saved["shots"]["shot-1"]["payload"]["text_bindings"] == [pin]
    assert json.loads(file.with_suffix(".text-binding.json").read_text(encoding="utf-8")) == pin


def test_bind_script_refuses_conflicting_metadata_before_runtime_mutation(tmp_path, monkeypatch):
    script_path, helper = _helper()
    file = tmp_path / "timeline.json"
    original = _candidate({"voiceover_script": "older copy"})
    encoded = json.dumps(original)
    file.write_text(encoded, encoding="utf-8")
    text_file = tmp_path / "narration.txt"
    text_file.write_text("different copy", encoding="utf-8")
    calls = []
    client = SimpleNamespace(
        timelines=SimpleNamespace(open_composition=lambda *args: calls.append("head")),
        shots=SimpleNamespace(set_text_binding=lambda *args, **kwargs: calls.append("bind")),
    )
    monkeypatch.setattr(helper.AstridClient, "open_from_launcher", lambda **kwargs: calls.append("open"))
    monkeypatch.setattr(
        "sys.argv",
        [str(script_path), "bind-script", "--file", str(file), "--shot", "shot-1",
         "--text-file", str(text_file), "--expected-head", "3", "--idempotency-key", "conflict"],
    )
    with pytest.raises(RuntimeError, match="Duplicate metadata narration conflicts"):
        helper.main()
    assert calls == []
    assert file.read_text(encoding="utf-8") == encoded
    assert not file.with_suffix(".text-binding.json").exists()


def test_bind_script_surfaces_stale_expected_head_without_rewriting_checkout(tmp_path, monkeypatch):
    script_path, helper = _helper()
    file = tmp_path / "timeline.json"
    original = _candidate()
    encoded = json.dumps(original)
    file.write_text(encoded, encoding="utf-8")
    text_file = tmp_path / "narration.txt"
    text_file.write_text("A concise line.", encoding="utf-8")
    calls = []

    def stale_binding(*args, **kwargs):
        calls.append(kwargs)
        return SimpleNamespace(ok=False, error="text binding head is stale")

    client = SimpleNamespace(
        timelines=SimpleNamespace(open_composition=lambda *args: SimpleNamespace(ok=True, data={"summary": {"head_revision_id": "parent-1"}})),
        shots=SimpleNamespace(set_text_binding=stale_binding),
    )
    with pytest.raises(RuntimeError, match="text binding head is stale"):
        _run_bind(helper, script_path, monkeypatch, file, text_file, client, expected_head=2)
    assert calls[0]["expected_head"] == 2
    assert file.read_text(encoding="utf-8") == encoded
    assert not file.with_suffix(".text-binding.json").exists()
