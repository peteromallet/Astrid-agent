"""One Node resolver: the Remotion project's pin decides, ASTRID_NODE_EXECUTABLE only overrides."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from astrid.core.rendering import node_pin


def _node(path: Path, version: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"#!/bin/sh\necho {version}\n", encoding="utf-8")
    path.chmod(0o755)
    return path


def _project(root: Path, pin: str | None = "=20.19.4") -> Path:
    project = root / "astrid-local" / "Astrid-serve" / "remotion"
    (project / "node_modules").mkdir(parents=True)
    (project / "package.json").write_text(json.dumps({"engines": {"node": pin}} if pin else {}), encoding="utf-8")
    return project


def test_the_pin_is_read_from_the_project():
    assert node_pin.node_pin(Path("/nonexistent")) is None


def test_a_different_node_on_path_is_skipped_and_the_pinned_one_found_beside_the_checkout(tmp_path):
    project = _project(tmp_path)
    path_node = _node(tmp_path / "homebrew" / "node", "v26.7.0")
    pinned = _node(tmp_path / "tools" / "node-v20.19.4-darwin-arm64" / "bin" / "node", "v20.19.4")
    found = node_pin.resolve_pinned_node(project, search_path=str(path_node.parent), home=tmp_path / "home")
    assert found.ok and found.path == pinned.resolve() and found.version == "v20.19.4"
    assert found.seen == (f"v26.7.0 at {path_node} (PATH)",) and "tools beside the checkout" in found.source


def test_an_override_must_match_the_pin_and_never_falls_back(tmp_path):
    project = _project(tmp_path)
    wrong = _node(tmp_path / "homebrew" / "node", "v26.7.0")
    _node(tmp_path / "tools" / "node-v20.19.4-darwin-arm64" / "bin" / "node", "v20.19.4")
    refused = node_pin.resolve_pinned_node(project, override=str(wrong), search_path="", home=tmp_path / "home")
    assert not refused.ok and refused.path is None
    assert "is Node v26.7.0" in refused.problem and "pins v20.19.4" in refused.problem
    right = _node(tmp_path / "custom" / "node", "v20.19.4")
    assert node_pin.resolve_pinned_node(project, override=str(right), search_path="").path == right.resolve()


def test_no_pinned_node_anywhere_says_what_it_found_and_where_to_put_it(tmp_path):
    project = _project(tmp_path)
    path_node = _node(tmp_path / "homebrew" / "node", "v26.7.0")
    missing = node_pin.resolve_pinned_node(project, search_path=str(path_node.parent), home=tmp_path / "home")
    assert not missing.ok and "no Node v20.19.4" in missing.problem and "found only v26.7.0" in missing.problem
    assert "tools/ folder beside the checkout" in missing.problem


def test_an_unpinned_project_takes_the_first_node_on_path(tmp_path):
    project = _project(tmp_path, pin=None)
    path_node = _node(tmp_path / "bin" / "node", "v22.1.0")
    found = node_pin.resolve_pinned_node(project, search_path=str(path_node.parent), home=tmp_path / "home")
    assert found.ok and found.path == path_node.resolve() and found.pin is None


def test_the_host_launch_refuses_a_mismatched_node_and_resolves_the_pinned_one(tmp_path):
    from astrid.sdk.host_bootstrap import PackHostBootstrapError, _provision_render_runtime_env

    project = _project(tmp_path)
    source = project.parent
    wrong = _node(tmp_path / "homebrew" / "node", "v26.7.0")
    pinned = _node(tmp_path / "tools" / "node-v20.19.4-darwin-arm64" / "bin" / "node", "v20.19.4")
    env = {"PATH": str(wrong.parent)}
    _provision_render_runtime_env(source, env)  # the shell's Node 26 does not matter
    assert env["ASTRID_NODE_EXECUTABLE"] == str(pinned.resolve())
    with pytest.raises(PackHostBootstrapError, match="is Node v26.7.0, but .* pins v20.19.4"):
        _provision_render_runtime_env(source, {"PATH": "", "ASTRID_NODE_EXECUTABLE": str(wrong)})


def test_doctor_reports_the_node_in_use_against_the_pin(tmp_path):
    project = _project(tmp_path)
    wrong = _node(tmp_path / "homebrew" / "node", "v26.7.0")
    report = node_pin.node_report(project, str(wrong))
    assert report["status"] == "mismatch" and report["pin"] == "v20.19.4" and "dev promote" in report["fix"]
    pinned = _node(tmp_path / "tools" / "node-v20.19.4-darwin-arm64" / "bin" / "node", "v20.19.4")
    assert node_pin.node_report(project, str(pinned))["status"] == "ok"
    assert node_pin.node_report(project, None)["status"] == "unknown"


def test_a_running_process_environment_is_read(monkeypatch):
    class Done:
        stdout = "python -m astrid.core.execution.generic_host run A=1 ASTRID_NODE_EXECUTABLE=/x/node B=2"

    monkeypatch.setattr(node_pin.sys, "platform", "darwin")
    monkeypatch.setattr(node_pin.subprocess, "run", lambda *a, **k: Done())
    assert node_pin.process_env(42, ["ASTRID_NODE_EXECUTABLE", "MISSING"]) == {"ASTRID_NODE_EXECUTABLE": "/x/node"}


def test_doctor_reads_the_running_hosts_node(tmp_path, monkeypatch):
    project = _project(tmp_path)
    wrong = _node(tmp_path / "homebrew" / "node", "v26.7.0")
    (tmp_path / "data" / "runtime").mkdir(parents=True)
    (tmp_path / "data" / "runtime" / "generic-host.json").write_text(
        json.dumps({"source_checkout": str(project.parent), "pid": __import__("os").getpid()}))
    monkeypatch.setattr(node_pin, "process_env", lambda pid, names: {"ASTRID_NODE_EXECUTABLE": str(wrong)})
    section = node_pin.doctor_section(tmp_path / "data")
    assert section["status"] == "mismatch" and section["host_pid"] and "pins v20.19.4" in section["detail"]
    assert node_pin.doctor_section(tmp_path / "nothing")["status"] == "unknown"
