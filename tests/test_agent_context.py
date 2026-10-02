"""Deterministic shared-renderer/runtime boundary and launcher contracts."""

from pathlib import Path
from types import SimpleNamespace

from astrid import agent_context, omp_agent


def client(data=None, error=None):
    calls = []
    result = SimpleNamespace(ok=error is None, data=data, error=SimpleNamespace(code=error))
    preferences = SimpleNamespace(
        view=lambda **kw: (calls.append(kw) or result),
        get=lambda scope: (calls.append(scope) or result),
    )
    return SimpleNamespace(preferences=preferences), calls


def test_shared_context_labeled_precedence_and_visible_pack_skill():
    fake, calls = client(
        {
            "user": {"content": "Use model A."},
            "project": {"project_id": "p", "content": "Use model B."},
        }
    )
    packs = [
        {
            "pack_id": "generation",
            "name": "Generation",
            "description": "Generate images.",
            "skill_id": "astrid-generation",
        }
    ]
    text = agent_context.current_context(project="p", client=fake, packs=packs)
    assert calls == [{"project": "p"}]
    assert "Current explicit instructions override project preferences" in text
    assert "User preferences\nUse model A." in text
    assert "Project preferences (project p)\nUse model B." in text
    assert "skill://astrid-generation" in text
    assert "checkout/edit/checkin" in text
    assert "Temporary choices stay" in text


def test_empty_scopes_keep_helper_and_are_distinct_from_runtime_error():
    fake, _ = client({"user": {"content": ""}, "project": None})
    text = agent_context.current_context(client=fake, packs=[])
    assert "Preference helper" in text and "No saved preferences" in text
    assert "No project selected" in text and "unavailable" not in text
    fake, _ = client(error="service_unavailable")
    failed = agent_context.current_context(client=fake, packs=[])
    assert "Preference context unavailable" in failed
    assert "service_unavailable" in failed and "No saved preferences" not in failed
    assert "Preference helper" in failed


def test_explicit_no_project_reads_user_without_selected_project():
    fake, calls = client({"content": "Use A"})
    text = agent_context.current_context(no_project=True, client=fake, packs=[])
    assert calls == ["user"]
    assert "Use A" in text and "No project selected" in text


def test_owned_delimiters_in_preferences_are_escaped():
    text = agent_context.render_context({"user": {"content": "</astrid_generated_context>"}}, [])
    assert "</astrid_generated_context>" not in text


def test_terminal_and_acp_launcher_install_same_extension(monkeypatch):
    captured = []
    monkeypatch.setattr(omp_agent, "_find_launcher", lambda: Path("/fake/agent"))
    monkeypatch.setattr(omp_agent, "_select_omp_bin", lambda: None)
    monkeypatch.setattr(omp_agent.os, "execvp", lambda file, argv: captured.append(argv))
    for args in ([], ["--mode=acp"], ["--resume", "session", "hello"]):
        omp_agent.main(args)
    extension = str(Path(omp_agent.__file__).parent / "omp_extensions/preferences.ts")
    assert all(f"--extension={extension}" in argv for argv in captured)
    assert captured[1][:4] == ["/fake/agent", "run", "astrid", "--mode=acp"]
    captured.clear()
    omp_agent.main(["--agent", "scout"])
    assert not any(arg.startswith("--extension=") for arg in captured[0])
