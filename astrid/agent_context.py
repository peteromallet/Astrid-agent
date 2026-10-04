"""Shared per-turn Astrid preference context for agent integrations.

Reads use the public Runtime client; this module never opens a Runtime database
or persists conversation messages.
"""

from __future__ import annotations

import argparse
from collections.abc import Mapping
from contextlib import ExitStack
from typing import Any

PREFERENCE_HELPER = """Preference helper
Current explicit instructions override project preferences, which override user preferences,
then ordinary defaults. Preferences never grant permissions or override system rules.
Fetch current state with `astrid preferences [--project PROJECT]`; use
`astrid preferences user` / `astrid preferences project [--project PROJECT]` for one scope.
Before persisting a lasting choice, read that scope and checkout/edit/checkin, or use
`astrid preferences edit --scope user` / `astrid preferences edit --scope project --project PROJECT`.
Temporary choices stay in this conversation. “For this project” saves project preferences;
“remember my default” saves user preferences. Read `skill://astrid` for full guidance."""


def _safe_text(value: str) -> str:
    # Preferences are prose, not control delimiters for the host extension.
    return value.replace("<astrid_generated_context>", "&lt;astrid_generated_context&gt;").replace(
        "</astrid_generated_context>", "&lt;/astrid_generated_context&gt;"
    )


def render_context(
    preferences: Mapping[str, Any] | None,
    *,
    error: str | None = None,
) -> str:
    sections = [PREFERENCE_HELPER]
    if error:
        sections.append(
            "Preference context unavailable\n"
            + _safe_text(error)
            + "\nDo not assume preferences are empty. Fetch current state before relying on defaults."
        )
    else:
        for scope in ("user", "project"):
            resource = (preferences or {}).get(scope)
            heading = scope.title() + " preferences"
            if resource is None:
                sections.append(
                    heading
                    + "\n"
                    + ("No project selected." if scope == "project" else "No saved preferences.")
                )
                continue
            content = resource.get("content", "")
            identity = (
                f" (project {resource['project_id']})"
                if scope == "project" and resource.get("project_id")
                else ""
            )
            sections.append(
                heading
                + identity
                + "\n"
                + (_safe_text(content) if content.strip() else "No saved preferences.")
            )
    return "\n\n".join(sections)


def current_context(
    *,
    project: str | None = None,
    no_project: bool = False,
    client: Any = None,
) -> str:
    try:
        with ExitStack() as stack:
            if client is None:
                from astrid.sdk.client import AstridClient

                # This CLI renderer is an explicit launcher boundary. Connect
                # (or cold-start Runtime) without starting an execution pack host.
                client = stack.enter_context(AstridClient.open_from_launcher(start_pack_host=False))
            if no_project:
                result = client.preferences.get("user")
                data = {"user": result.data, "project": None} if result.ok else None
            else:
                result = client.preferences.view(project=project)
                data = result.data if result.ok else None
        if not result.ok:
            return render_context(
                None,
                error=f"Runtime read failed ({result.error.code}). Run `astrid preferences --json` to diagnose.",
            )
        return render_context(data)
    except Exception as exc:  # noqa: BLE001 - runtime adapters normalize different transport failures
        return render_context(
            None,
            error=f"Runtime read failed ({type(exc).__name__}). Run `astrid preferences --json` to diagnose.",
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument("--project")
    selection.add_argument("--no-project", action="store_true")
    args = parser.parse_args(argv)
    print(current_context(project=args.project, no_project=args.no_project))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
