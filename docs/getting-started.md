# Getting Started with Astrid

New installation? Start with [Set up Astrid](setup/README.md), or give your agent the [setup checklist](setup/SKILL.md). The reference below covers additional SDK and rendering details.

Astrid is a Python SDK and harness toolkit for building and running
agentic UXes — pipelines where agents and humans collaborate to make art.

## Prerequisites

Astrid requires Python 3.11.16 for the closeout qualification. The runtime is a
separate local service that owns durable workspace state; Astrid is a client and
pack source, not the state store. The closeout app/runtime pair is qualified with
Node 20.19.4 and npm 10.8.2 when the app is included.

Install both distributions from the exact implementation commits used for the
qualified closeout. This is the installed path: it does not import either
repository from a checkout or set `PYTHONPATH`, and it does not claim a public
wheel or hosted release artifact:

```bash
python3.11 -m venv .venv
source .venv/bin/activate
export ASTRID_COMMIT='237d73717f00ebce43ca8ce516b7321c4ebcda7e'
export RUNTIME_COMMIT='ae7764756be89dd586552e712d1d004648dbab40'
[[ "$ASTRID_COMMIT" =~ ^[0-9a-f]{40}$ && "$RUNTIME_COMMIT" =~ ^[0-9a-f]{40}$ ]]
python -m pip install "Astrid @ git+https://github.com/peteromallet/Astrid.git@${ASTRID_COMMIT}"
python -m pip install "banodoco-workspace-runtime @ git+https://github.com/banodoco/banodoco-workspace-runtime.git@${RUNTIME_COMMIT}"
export ASTRID_LOCAL_DATA_ROOT="$PWD/.astrid-data"
astrid-local --provenance
```

The local Worker is a separate installed composition. Install the pinned Worker
distribution in an independent Python 3.10 environment, obtain the matching
installed Worker profile from the installation bundle, and generate an
installed `SourceProfile` that sets its absolute `worker_profile` path as shown
in [Set up Astrid](setup/README.md#provide-the-independent-worker-composition-profile).
The repository does not currently publish a generic Worker-profile generator or
engine artifact; the qualification fixture profile is not a user setup artifact.

```bash
export ASTRID_LOCAL_SOURCE_MANIFEST=/absolute/path/to/installed-source-profile.json
astrid setup --create --check --source-manifest "$ASTRID_LOCAL_SOURCE_MANIFEST" --json
astrid setup --create --apply --source-manifest "$ASTRID_LOCAL_SOURCE_MANIFEST" --json
astrid status --json
astrid doctor --diagnostic --json
astrid worker start --json
astrid projects list --json
```

The qualified cross-repository implementation tuple is Astrid
`237d73717f00ebce43ca8ce516b7321c4ebcda7e`, Runtime
`ae7764756be89dd586552e712d1d004648dbab40`, Worker
`2c5c633b4a40681d25cda19b11043c09548dd13c`, and App
`9d1e0b0bb7c9490457943189cf477219224b007c`. The Worker and App identities are
composition provenance; the first environment installs Astrid and Runtime only.
Later documentation-only commits are separate from this tested tuple.

The support root contains the launcher catalog, credentials, and runtime realm
directory; Runtime continues to own the database and content-addressed objects.
Set `ASTRID_LOCAL_DATA_ROOT` or pass `--data-root /absolute/path` to
`astrid-local` for an installation-owned location. The compatibility layer still
accepts `BANODOCO_LOCAL_DATA_ROOT` and other `BANODOCO_*` spellings during the
migration window, emits a deprecation warning for a legacy-only value, and fails
closed when canonical and legacy values conflict. Prefer the `ASTRID_LOCAL_*`
names in new shells and scripts.

### Optional provider credentials

If you use provider-backed tools, set each API key once with the hidden prompt:

```bash
astrid-credential set runpod
```

Astrid stores it in `~/.astrid/astrid.env`. This is one file on this computer
for your current login, shared across your Astrid projects and checkouts. See
[Credential setup](reference/credentials.md) for supported providers,
overrides, and deployment guidance.

### Default Hivemind pack

Astrid setup provisions the canonical Hivemind repository at an immutable
revision, validates its strict v2 pack manifest, and composes its skill view.
The same managed source inventory is used by discovery and the generic host:

`astrid setup --create --apply` composes the default Hivemind source as part of
the selected workspace. Use `astrid setup --disable-pack hivemind` or
`astrid setup --restore-pack hivemind` only as an explicit setup change; keep
the same setup input and review it with `--check` before `--apply`.

### Optional Hivemind contributor login

Public Hivemind search and ordinary Astrid work do not require login. Login is
only needed when an agent is asked to contribute or ingest knowledge:

```bash
python3 -m astrid auth login
python3 -m astrid auth status
```

`python3 -m astrid auth login` opens the Banodoco approval page and polls automatically. Approve
the Discord connection in the browser; do not type the displayed approval code
into the terminal. After the thank-you page, the terminal saves the contributor
credential at `~/.hivemind/key` with owner-only permissions. The machine label
and code shown on the page are for reference only.

Authenticated contributors can submit resources and propose revisions. Accepting
or rejecting revisions and marking guides canonical are editor-only actions; a
non-editor receives a clear `403 forbidden` response rather than being asked to
log in again.

To manage the local credential:

```bash
python3 -m astrid auth logout   # remove only the local credential
python3 -m astrid auth revoke   # revoke the server-side credential
python3 -m astrid auth logout   # remove the revoked local credential
```

If `python3 -m astrid auth status` reports `active` but a contribution returns `401
unauthorized`, login succeeded but the deployed Hivemind contribution function
or its database migration is not ready. Treat that as a deployment issue and
do not publish a real resource until the write path has been verified.

Use `ASTRID_SOURCE_DECLARATIONS` or `--declarations` for a local Git mirror
when developing offline. Skill sync is read-only with respect to source
acquisition; Hivemind corpus search and retrieval still require network access.

Use that environment for later commands too. The render worker checks its
dependencies in an isolated Python process, so user-site-only installations
are insufficient; install rendering dependencies into the active environment.

The installed launcher records bounded provenance (`implementation_owner`,
module origin, artifact digest, distribution version, and support root) without
emitting credentials. Astrid uses the installed Runtime artifact and does not
require a sibling checkout or `PYTHONPATH`.

`ASTRID_LOCAL_SOURCE_MANIFEST` also selects the installed Worker profile for the
supported installed composition. Repository development uses a distinct
editable source profile with absolute, symlink-free checkout paths. The legacy
`BANODOCO_LOCAL_SOURCE_MANIFEST` spelling is accepted with a warning; it is not
the canonical setup path. `banodoco-local` and `astrid-runtime` are also
deprecated aliases for `astrid-local` and print a warning when invoked; the
legacy `banodoco-local up --profile astrid` invocation is retained only for
migration compatibility.

## Your First Command

From any shell with the runtime configured, check health:

```bash
astrid status --json
astrid doctor --diagnostic --json
```

Other useful zero-secret commands:

```bash
astrid projects list --json
astrid projects show demo --json
```

Start the Runtime-owned local Worker only when work is ready to execute:

```bash
astrid worker start --json
```

The product command requests one verified Worker handoff through Runtime and
one `GenericPackHost`. It does not authorize GPU, RunPod, or provider work.

After a project has a successful timeline render, verify and open the newest
runtime render on macOS with:

```bash
python3 -m astrid projects select demo
python3 -m astrid runs open
```

Pass a run id positionally (`runs open <run-id>`) to open an exact successful
render, or use `--project <project>` to override the current project. The
command does not guess from checkout filenames or modification times.

If using the SDK directly, pass the loopback runtime endpoint and credential to
`AstridClient.open(...)` together with the runtime realm and actor identity.
Never point Astrid at a local SQLite/CAS directory; the runtime owns those
details.

`astrid status` and `astrid doctor` remain read-only diagnostics and do not
create support state, start services, repair state, retry work, or migrate a
workspace. Product commands perform the bounded neutral launch/reconnect
handoff through `AstridClient.open_from_launcher()`. Ordinary SDK
`AstridClient.open()` calls remain explicit and never launch a process.

The seven top-level gateway families are `projects`, `timelines`, `media`,
`tasks`, `runs`, `doctor`, and `backup`; `timelines shots` and `media
references` are nested mounts. `doctor`
and `backup` use runtime routes. Backup supports create/restore/export and realm
lifecycle operations, with `--json` for a machine-readable result.

Runtime health, project identity, media objects, timeline versions, task/run
state, receipts, and events are authoritative only in the workspace runtime.
The SDK never owns or queries a local database or object-store index. It can
read a file location that the runtime has authenticated and verified.

### Upgrading an existing workspace

After installing a newer immutable Astrid and Runtime pair, run:

```bash
astrid-upgrade
```

The command locates the selected workspace, refuses to interrupt active work,
applies the Runtime-owned migrations, and restarts only the services it owns.
It preserves project identities and media, retains migration archives for
recovery, and can be rerun safely. Do not open SQLite/CAS files, copy an old
root into place, or use a checkout as an implicit workspace.

For direct Runtime lifecycle work, use the canonical launcher and the same
support root:

```bash
astrid-local upgrade --data-root "$ASTRID_LOCAL_DATA_ROOT" --json
```

Runtime owns migration and realm state. The deprecated `banodoco-local` and
`astrid-runtime` names remain readable during migration and emit a warning.

For the complete project, timeline, media, recovery, and failure journeys,
continue with [CLI journeys](guides/cli-journeys.md). For renderer-specific
diagnostics, see [Debugging](guides/debugging.md).

### Developer-only canonical timeline schema

The canonical `banodoco_timeline_schema` package is an optional external
dependency. Astrid's default Python install intentionally does not vendor or
declare this private Banodoco workspace package, so clean installs can use the
non-schema surfaces and import Astrid without that checkout. Timeline document
validation, managed rendering/visualization, and the exact canonical-schema
parity assertions require it and fail closed when it is unavailable.

The `dev` extra pins a compatible public source revision so the repository-wide
test suite and CI validate one deterministic schema. This does not add the
schema to Astrid's base runtime dependencies or change the installed Runtime
authority path.

Install the package from a compatible Banodoco workspace checkout with the same
interpreter used to run Astrid:

```bash
python -m pip install -e /path/to/banodoco-workspace/packages/timeline-schema/python
python -c "import banodoco_timeline_schema; print('timeline schema available')"
```

The Astrid checkout does not contain `packages/timeline-schema/python`; replace
the placeholder with the path to the external workspace. If the package is not
installed, timeline validation fails closed with the installation message above.

### First visible timeline render

When hand-authoring a timeline, start with root `clips`, a `visual` track,
structured text marked explicitly as `clipType: "text"`, and an MP4 output:

```json
{"tracks":[{"id":"cards","kind":"visual","label":"Cards"}],
 "clips":[{"id":"title","at":0,"track":"cards","clipType":"text","hold":2,
   "text":{"content":"HELLO ASTRID","fontSize":64,"color":"#ffffff","align":"center"}}],
 "output":{"resolution":"640x360","fps":30,"file":"title.mp4"}}
```

The renderer rejects a structured `text` clip without `clipType: "text"` and
checks the default `.mp4` output suffix before starting a render, so these
mistakes do not consume a failed attempt.

## Where to Go Next

- **SDK tutorial** — Walk through the full discover → inspect → invoke →
  read-events loop: [Build Your First Agentic UX](guides/build-your-first-agentic-ux.md).
- **Pack authoring** — Build your own executors, orchestrators, and
  elements: start with [Pack Documentation](packs/) and
  [Creating Packs](packs/creating-packs.md).
- **Contracts index** — Normative contracts that define the SDK surface,
  CLI behavior, error model, output format, and run ledger:
  [Contracts Index](contracts/README.md).
- **Full SDK reference** — DTO catalog and exception hierarchy:
  [SDK Reference](reference/sdk.md).
- **Discovery for agents** — How AI agents consume the capability
  registry: [Discovery for Agents](guides/discovery-for-agents.md).
