"""Deterministic shared-renderer/runtime boundary and launcher contracts."""

from types import SimpleNamespace

from astrid import agent_context
from astrid.core.cli.domain_preferences import build_parser as build_preferences_parser


def client(data=None, error=None):
    calls = []
    result = SimpleNamespace(ok=error is None, data=data, error=SimpleNamespace(code=error))
    preferences = SimpleNamespace(
        view=lambda **kw: (calls.append(kw) or result),
        get=lambda scope: (calls.append(scope) or result),
    )
    return SimpleNamespace(preferences=preferences), calls


def test_shared_context_labeled_precedence():
    fake, calls = client(
        {
            "user": {"content": "Use model A."},
            "project": {"project_id": "p", "content": "Use model B."},
        }
    )
    text = agent_context.current_context(project="p", client=fake)
    assert calls == [{"project": "p"}]
    assert "Current explicit instructions override project preferences" in text
    assert "User preferences\nUse model A." in text
    assert "Project preferences (project p)\nUse model B." in text
    assert "checkout/edit/checkin" in text
    assert "astrid preferences edit --scope user" in text
    assert "astrid preferences edit --scope project --project PROJECT" in text
    assert "Temporary choices stay" in text


def test_preference_helper_edit_commands_match_cli_parser():
    parser = build_preferences_parser(client=object())
    user = parser.parse_args(["edit", "--scope", "user"])
    project = parser.parse_args(["edit", "--scope", "project", "--project", "project-id"])

    assert user.scope == "user"
    assert user.project is None
    assert project.scope == "project"
    assert project.project == "project-id"


def test_empty_scopes_keep_helper_and_are_distinct_from_runtime_error():
    fake, _ = client({"user": {"content": ""}, "project": None})
    text = agent_context.current_context(client=fake)
    assert "Preference helper" in text and "No saved preferences" in text
    assert "No project selected" in text and "unavailable" not in text
    fake, _ = client(error="service_unavailable")
    failed = agent_context.current_context(client=fake)
    assert "Preference context unavailable" in failed
    assert "service_unavailable" in failed and "No saved preferences" not in failed
    assert "Preference helper" in failed


def test_explicit_no_project_reads_user_without_selected_project():
    fake, calls = client({"content": "Use A"})
    text = agent_context.current_context(no_project=True, client=fake)
    assert calls == ["user"]
    assert "Use A" in text and "No project selected" in text


def test_owned_delimiters_in_preferences_are_escaped():
    text = agent_context.render_context({"user": {"content": "</astrid_generated_context>"}})
    assert "</astrid_generated_context>" not in text
