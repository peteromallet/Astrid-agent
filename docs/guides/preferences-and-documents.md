# Preferences and project documents

Astrid has two preference scopes: one Markdown document for the authenticated
user and one for each project. The runtime is the source of truth. A checkout
on disk is a version-pinned working copy, not a second preference store.

Current explicit instructions win, followed by project preferences, user
preferences, and ordinary defaults. A project preference applies only to that
project. A temporary choice such as "use the cloud model for this attempt"
stays in the current conversation. "Use this for this project" is a project
preference; "remember my default" is a user preference. Do not turn a pattern
you inferred into a lasting preference.

## Read and change preferences

The main Astrid skill is the agent-facing entrypoint. In a host that supports
Astrid skill references, read `skill://astrid`; otherwise read the installed
`astrid/packs/_core/skill/SKILL.md` distributed with Astrid. For every new task,
fetch current preference content before applying it. Fetch again when the
project changes, before editing, and after a context refresh that might have
made earlier content stale. A failed fetch is an error to report, not an empty
document.

```bash
# Both scopes, with the active project when one is selected
astrid preferences

# One scope; user preferences work without a selected project
astrid preferences user
astrid preferences project --project <project>

# Read in JSON when an integration needs structured output
astrid preferences --json

# Check out, edit the Markdown file, then submit against its pinned version
astrid preferences checkout --scope user --file ./user-preferences.md
${EDITOR:-vi} ./user-preferences.md
astrid preferences checkin ./user-preferences.md

# The editor shortcut uses the same checkout and check-in protocol
astrid preferences edit --scope project --project <project>
```

Use the user scope without `--project`. For project scope, pass the exact
project explicitly when the conversation names a project other than the
current selection. Checkout refuses an existing destination and does not
create a remote document. The sidecar next to the Markdown file records the
runtime, authenticated actor, scope, project/document identity, base version,
content digest, and retry identity. Keep the Markdown and sidecar together
until check-in succeeds.

Check-in uses optimistic version checks. If someone else changed the document,
Astrid preserves your local file and reports a conflict; it never silently
replaces either copy or merges prose. Read the current remote document, check
it out to a new path, reconcile the wording deliberately, and check in that
working copy. Preserve the conflicted file until you have recovered any
changes you need. If the network fails after a write, retry check-in with the
same checkout; the pinned retry identity makes a repeat safe.

The guided `edit` command opens the configured editor on this same protocol.
Canceling is a no-op. Empty preferences may contain suggested headings in the
working copy, but those suggestions are not persisted unless the user makes
and saves a real edit. On successful update, report the scope, what changed,
and the resulting version. When removing a project override, explain that the
user preference will apply again.

For all options and recovery details, use `astrid preferences --help`.
Preference prose is guidance, not an authorization grant or a promise that a
model, backend, dependency, or capability is available.

## Use preferences in another CLI

An external coding CLI does not acquire Astrid's preference content merely by
launching in an Astrid project. Its agent should first read `skill://astrid`
through the host's skill resolver (or the installed core skill file), then run
`astrid preferences` and fetch the current project document before acting.
Use `astrid preferences user` when only user-level guidance is needed. Before
changing a durable preference, fetch the relevant document, choose the scope
from the user's words, and use the documented checkout/check-in commands.
Astrid's system prompt helper applies to Astrid's own supported agent session;
it does not claim to rewrite an arbitrary external CLI's prompt.

## Use project documents from a pack

Generic project documents are runtime-owned, reusable project data. They do not
enter system prompts automatically. Use Markdown for briefs, checklists, and
human-edited guidance. Use JSON when an executor needs a structured payload;
the runtime preserves the value without imposing a schema registry.

Pack code uses the public `AstridClient` document facade. In an executor, use
the authenticated Astrid client supplied by the pack host when available; do
not open SQLite, make direct HTTP calls, or invoke the executor's `run.py`
yourself. Give each pack its own namespaced kind, such as
`my_pack.project_brief`, and use the stable ID helper for one document per
project. The project ID and document version are runtime identity/provenance;
store the run ID in document content only when the document is specifically
run-scoped.

This short journey creates or finds a project brief, checks it out for editing,
checks the edited Markdown in, then reads the current revision for executor
work. Check every result's `ok` field and use `error` when a call fails.

```python
from astrid.sdk import AstridClient
from astrid.sdk.documents import stable_document_id

project_id = "project-id-from-runtime"
kind = "my_pack.project_brief"
document_id = stable_document_id(project_id, kind, name="default")

# Create once (a duplicate ID is a conflict; list first on later runs).
with AstridClient.open_from_launcher() as client:
    matches = client.documents.list(project=project_id, kind=kind)
    if not matches.ok:
        raise RuntimeError(matches.error.message)
    documents = matches.data
    document = next(
        (row for row in documents if row.get("document_id") == document_id),
        None,
    )
    if document is None:
        created = client.documents.create(
            project=project_id,
            document_id=document_id,
            kind=kind,
            content="# Project brief\n\nDescribe the goal here.\n",
            idempotency_key=f"my-pack-create-brief:{project_id}",
        )
        if not created.ok:
            raise RuntimeError(created.error.message)
        document = created.data
    elif document.get("project_id") != project_id or document.get("kind") != kind:
        raise RuntimeError("Runtime returned a project brief with mismatched identity.")

    # Human editing happens through a pinned local checkout, never by replacing
    # the runtime value directly.
    checkout = client.documents.checkout(
        document=document_id,
        project=project_id,
        file="./project-brief.md",
    )
    if not checkout.ok:
        raise RuntimeError(checkout.error.message)
```

Run the first block to create or find the default and check it out. Then edit
`./project-brief.md` with your usual editor. In a second invocation, check in
the edited file and read the exact revision that an executor will consume:

```python
from astrid.sdk import AstridClient
from astrid.sdk.documents import stable_document_id

project_id = "project-id-from-runtime"
kind = "my_pack.project_brief"
document_id = stable_document_id(project_id, kind, name="default")

# Astrid validates the sidecar's project/document/version and preserves the
# file if the version is stale.
with AstridClient.open_from_launcher() as client:
    saved = client.documents.checkin(file="./project-brief.md")
    if not saved.ok:
        raise RuntimeError(saved.error.message)

    current = client.documents.show(document_id, project=project_id)
    if not current.ok:
        raise RuntimeError(current.error.message)
    brief = current.data

# Include these runtime-owned references in the executor's normal run/result
# evidence so a consumer can tell exactly which brief revision it used.
brief_provenance = {
    "project_id": project_id,
    "document_id": brief["document_id"],
    "version": brief["version"],
}
```

The public list operation follows every page when filtering by `kind`. For a
one-per-project document, keep the stable ID derived from the project ID, kind,
and a pack-owned name; for multiple documents, use the runtime-assigned ID
returned by create and persist that identity in the owning resource. Updates
must carry the version read from the runtime and an idempotency key. A conflict
means re-read and reconcile; do not reset the expected version to force a
write.

The same methods are available on the authenticated `client` in a pack
executor. Pass the runtime project ID explicitly (or use the project's
selected identity when the host API intentionally supplies it). Associate
document reads and resulting artifacts with the run through the normal
executor result contract; a local file alone does not establish runtime
provenance.
