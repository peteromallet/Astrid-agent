# Getting Started with Astrid

New installations start with [Set up Astrid](setup/README.md) and its
[current paired-release recipe](setup/release-recipe.md). The reference below
explains the installed boundary, Runtime ownership, rendering inputs and the
credential distinctions. It is not a second installation path.

Astrid is a Python toolkit and client for building and running agentic UXes.
The neutral Workspace Runtime owns durable workspace state; an Astrid wheel
is an installed client and capability-pack product, not a database or a source
provenance checkout.

## Prerequisites and the installed boundary

The supported local path requires Python 3.11 or newer and the current
noneditable paired wheelhouse. Install the exact filenames and locked
dependencies from [the recipe](setup/release-recipe.md):

```bash
python3 -m venv /absolute/path/to/astrid-env
source /absolute/path/to/astrid-env/bin/activate
export ASTRID_WHEELHOUSE=/absolute/path/to/current-wheelhouse
python3 -m pip install --no-index --no-deps \
  "$ASTRID_WHEELHOUSE/$ASTRID_WHEEL_FILENAME" \
  "$ASTRID_WHEELHOUSE/$RUNTIME_WHEEL_FILENAME"
python3 -m pip install --no-index --require-hashes \
  -r "$ASTRID_WHEELHOUSE/python-requirements.lock"
```

The final Astrid and Runtime SHA/version slots remain
`PENDING_PRODUCER_HANDOFF`; do not replace them with a guessed tag, wheel,
registry project, or domain. The canonical client delivery owner is the
Astrid wheel's `astrid_runtime_client` package. Runtime owns the neutral
service/protocol and its lifecycle; do not install an order-dependent second
client owner. I08/I09 must verify the installed import origin and generated
client/schema digest for the final tuple.

Current publication destinations are the Astrid-agent client repository,
astrid-agent, astrid-runtime and astrid-app repositories:

```text
https://github.com/peteromallet/astrid-agent.git
https://github.com/banodoco/astrid-runtime.git
https://github.com/banodoco/astrid-app.git
```

No repository, package, CLI, import, protocol, domain, or storage rename is
part of this setup update.

## Select one Runtime support root and preserve the realm

Use the canonical [workspace create/attach recipe](setup/release-recipe.md#workspace-selection-create-or-attach-in-place).
It is the only setup path: `python3 -m astrid.setup --create --apply` is for
an explicit fresh install, and `--attach --apply` preserves an existing
selected realm/support root. Both require the exact `--workspace-id`,
`--data-root`, `--realm-root` and `--json` arguments; `--check --offline` is
the noninteractive read-only proposal.

The stable-config fragment distinguishes the durable support-root envelope from
the Runtime realm id/root. The app and delivered preview consume the same
`ASTRID_WORKSPACE_DISCOVERY` path. Do not relocate data, infer a root from the
current directory, create an automatic replacement, or disable realm/actor
identity checks. NativeSol owns the actual Astrid App launcher correction and
installed-host-origin proof; no installed journey is claimed here.

The Runtime remains authoritative for the database, realm, credentials,
managed outputs, media objects, timeline versions, tasks, runs and receipts.
If an existing installation has an ambiguous or missing realm, stop and use
the explicit attach/backup procedure rather than launching a blank replacement.

## Toolkit help versus agent onboarding

Toolkit help is documentation and is safe without a Runtime session:

```bash
python3 -m astrid --help
```

Runtime health and workspace discovery are separate:

```bash
python3 -m astrid doctor --json
python3 -m astrid projects list --json
```

Agent onboarding is the skills layer, not toolkit installation:

```bash
python3 -m astrid.skills.cli sync
python3 -m astrid.skills.cli sync --check
python3 -m astrid.skills.cli doctor --json
```

The wheel carries the default `astrid` agent profile. A direct agent launch,
reconnect and authentication-reuse claim requires the exact OMP/agent artifact
and command recorded in the final recipe and verified by existing I09/I11
work; command help alone does not establish it.

## Rendering and app inputs

Rendering is a paired resource boundary. The final manifest must name and
hash, for the same release tuple:

- the Remotion bundle and matching canonical timeline schema;
- the browser/Chromium executable used by the frame worker;
- the reviewed public fonts and their provenance/license records;
- `ffmpeg` and `ffprobe` media tools;
- the OMP artifact, agent launcher/profile and required native dependencies;
- the app build, catalogs and other public inputs consumed by the app and
  Remotion.

The app's public inputs may contain catalogs, fonts and build-time public
configuration, but never Runtime bearer credentials or provider secrets.
Runtime credentials are injected server-side by the existing connector. A
missing optional renderer, schema, browser, media tool, OMP, native or
provider artifact must produce its existing diagnostic; it must not silently
fall back to a source checkout or ambient installation.

The canonical schema is optional for base import/help surfaces but required by
timeline validation and managed rendering/visualization. Install it from the
exact artifact named by the paired recipe; do not use an unrelated sibling
checkout or an older ambient schema.

The operational V3 review loop is Runtime-owned and uses the Astrid wheel's
single canonical client (`astrid_runtime_client`):

```bash
python3 -m astrid projects current --json
python3 -m astrid timelines list --project <project> --json
python3 -m astrid timelines show <timeline> --project <project> --json
python3 -m astrid timelines visualize <timeline> --project <project> \
  --mode inputs --format md --format png --json
python3 -m astrid timelines render <timeline> --project <project> \
  --backend rendering.remotion --wait --json
```

Use `--mode inputs` to inspect declared placements before spending a render
attempt. After rendering, inspect the managed output and receipt. If a video
has been explicitly materialized for validation from the source checkout or
contributor tooling, the existing source-tree checker is:

```bash
python3 tools/check_black_frames.py --video <render.mp4> \
  --timeline <timeline.json> --assets <assets.json>
```

The checker is contributor-only: `tools/` is not packaged in the installed
wheel, so it is not a mandatory installed-journey prerequisite. It validates
an already-produced video and never renders or bypasses Runtime custody. The
installed journey instead records renderer-doctor results, the managed output
receipt and Runtime-owned output validation. The obsolete
`tools/render_and_check.py` wrapper must not be revived. Missing
Remotion/schema/browser/font/`ffmpeg`/`ffprobe`/OMP/native inputs remain
diagnostics; this guide does not claim an installed rendering journey.

## Credentials and optional providers

Public Hivemind search and ordinary Astrid work do not require contributor
login. Contribution-only access uses:

```bash
astrid login
astrid status
```

Approve the browser flow and keep the credential local. `astrid logout` removes
the local credential; `astrid revoke` revokes the server-side credential.

Provider credentials are independent:

```bash
astrid-credential set runpod
```

Only add a provider dependency when the selected capability requires it. An
agent subscription or Hivemind contributor credential does not supply a
creative-provider API key. See [Credential setup](reference/credentials.md)
for the supported provider-specific overrides.

## First command and a visible render

From any shell using the installed environment:

```bash
python3 -m astrid doctor --json
```

After a project has a successful timeline render, select it and open the
Runtime-owned output:

```bash
python3 -m astrid projects select demo
python3 -m astrid runs open
```

The command uses the selected Runtime project and successful render receipt; it
does not infer state from checkout names, modification times, a local database
or an event-log file.

For direct SDK use, pass the loopback Runtime endpoint, credential, selected
realm and actor explicitly through `AstridClient.open(...)`. Ordinary SDK
calls do not launch a process; product commands use the existing launcher
boundary.

## Supported predecessor → current final release transition

The only predecessor covered by this recipe is the recorded current
noneditable I11 candidate pair, not an arbitrary historical installation:

- Astrid candidate wheel: `astrid-0.1.0-py3-none-any.whl`, SHA-256
  `47d56e391f9b41d82a714e0ef7cbd9ab76caa9c0137088e5530bc3fe55a497eb`;
- Runtime candidate wheel: `astrid_runtime-0.1.0-py3-none-any.whl`,
  SHA-256 `4c3363b3d9f7694a8c63567b659f3299d62f43efb290a56207a0dcf620caced7`.

These are candidate evidence artifacts, not a claim that a public release or
registry entry exists. The current final release fields are filled only by the
producer handoff.

For an existing installation, use this exact sequence:

1. Stop active work through the supported lifecycle boundary. Create a
   Runtime backup before changing packages:

   ```bash
   python3 -m astrid backup create /absolute/path/to/pre-update-backup --json
   ```

2. Record the selected realm/root and the predecessor artifact manifest. Put
   the current final wheelhouse beside the existing installation; do not
   overwrite the predecessor record.
3. Follow the [canonical recipe's exact same-version replacement block](setup/release-recipe.md#supported-predecessor-populations--current-final-release)
   to install the current matched wheels noneditably from the wheelhouse. It
   uses `pip --force-reinstall --no-deps` for the selected wheel artifacts,
   with locked dependencies and all named renderer/app/OMP artifacts.
4. Run the existing upgrade contract with the same selected root:

   ```bash
   astrid-upgrade --data-root "$ASTRID_RUNTIME_DATA_ROOT" --json
   ```

   The command stops the idle pack host, asks Runtime to apply its supported
   schema/storage transition, preserves the selected realm, restarts and
   verifies the normal launcher handoff. It must refuse active work or a
   missing/ambiguous realm rather than creating an empty one.
5. Re-run `doctor --json`, perform the existing admitted output journey, and
   verify project IDs, media hashes, preferences/documents, credentials,
   managed output identity and app readback under the same root.

Rollback has two distinct meanings:

- **Package rollback:** if activation fails before the Runtime schema/data
  contract changes, reinstall the recorded predecessor wheelhouse and restart
  through the same root. Preserve the failed activation receipt. Do not
  uninstall a shared client distribution by guesswork.
- **Data/schema rollback:** if Runtime has activated a schema/data transition,
  do not downgrade packages and pretend that data was reverted. Use the
  existing Runtime backup/restore contract (`astrid backup restore` or the
  explicit Runtime restore route) to restore a verified backup into a selected
  destination, then relaunch and verify realm identity. A Git revert is not a
  database downgrade.

No arbitrary old package or schema versions are supported. A predecessor is
supported only when its exact artifact manifest and the existing schema/backup
contract say it can open the selected state.

## Evidence and heavy-end qualification

The current docs consume the existing manager handoff; they do not create a
new gate or repeat the source audit. The heavy end of I09/I11 must execute the
recipe from a fresh home and unrelated working directory, capture installed
origins, perform one admitted task, capture Runtime-owned output and readback,
then demonstrate the owned update and rollback procedure. Missing optional
dependencies must be captured as diagnostics. No such heavy-end pass is claimed
by this document.

The maintained recipe reuses the packaging, rendering-resource, OMP and
publication fragments indexed by:

`receiving/coordination/evidence/v3-manager-dispatch-20261009/source-interface-artifact-index.json`

The exact naming migration is a **NAM-00–NAM-11 planning-only follow-on**
after validated current publication. It is not an I03/I08/I09/I11 gate and it
does not authorize a rename now. Reuse the current publication destinations
above, then consult the plan and report:

- [naming report](../../../receiving/coordination/evidence/astrid-naming-migration-20261009/report.md)
- [naming plan](../../../receiving/coordination/evidence/astrid-naming-migration-20261009/plan.md)

The integration index, root README and Makefile remain the owners of their
existing hand-pointer fragments; this setup surface does not edit them.

## Canonical timeline schema

The exact `banodoco_timeline_schema` artifact is selected by the release
manifest. Timeline validation, managed rendering and visualization fail closed
when it is unavailable or does not match the recorded digest. Do not install
it from an unrelated checkout or use a source path to override the installed
product.

## Where to go next

- [SDK tutorial](guides/build-your-first-agentic-ux.md)
- [Pack documentation](packs/)
- [Contracts index](contracts/README.md)
- [SDK reference](reference/sdk.md)
- [Discovery for agents](guides/discovery-for-agents.md)
