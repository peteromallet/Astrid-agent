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

## Use project documents

Project documents are reusable Runtime-owned data. Use Markdown for text you
edit by hand and JSON for structured values. Astrid stores each document under
its explicit project and kind; local files are only working copies.

```bash
# List project documents, optionally filter by exact kind
astrid documents list --project <project> --kind <kind>

# Create a Markdown or JSON document from a local file
astrid documents create --project <project> --kind notes.research --file ./research.md

# Read a document or check it out for conflict-safe editing
astrid documents show <document-id> --project <project>
astrid documents checkout <document-id> --project <project> --file ./research.md
${EDITOR:-vi} ./research.md
astrid documents checkin ./research.md
```

Checkout creates the adjacent `.astrid.json` identity/version sidecar and
refuses to overwrite either destination. Keep the file and sidecar together
until check-in succeeds. Check-in uses optimistic version checks and preserves
your local file on conflict; fetch the current version to a new path and
reconcile edits deliberately. Use `astrid documents --help` for JSON input,
idempotency, and recovery options.
