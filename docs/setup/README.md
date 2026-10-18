# Set up Astrid

Use the [current paired-release recipe](release-recipe.md) to install Astrid
from one noneditable wheelhouse/artifact bundle. The recipe is the maintained
user path; it does not add an installer framework and it does not install from
an Astrid or Runtime checkout.

The current local launcher is validated on macOS. Linux and Windows remain
unvalidated here. The final release SHA, package versions, wheel filenames and
digests are producer-owned inputs and are intentionally marked pending in the
recipe until the current source union is handed off.

To install matching local Git branches in a new isolated workspace, use the
[one-command local source route](local-source-install.md). It builds the
noneditable pair and app from recorded clean commits without changing an
existing workspace or global agent configuration. This local route does not
replace the published release qualification below.

## 1. Obtain the paired artifacts

Use one wheelhouse containing the exact Astrid and Runtime wheels plus the
locked Python dependencies and the non-Python resources named by the recipe.
Do not substitute a public registry, an ambient `omp`, a source checkout, or
an editable install. The final handoff must make the bundle obtainable and
must include its manifest and hashes.

The current publication destinations are:

| Component | Current destination |
| --- | --- |
| Astrid client/toolkit | `https://github.com/peteromallet/astrid-agent.git` |
| Astrid Runtime | `https://github.com/banodoco/astrid-runtime.git` |
| App consumer | `https://github.com/banodoco/astrid-app.git` |

These are current publication destinations, not a request to rename a
repository, package, CLI, import namespace, domain, or storage location.

## 2. Install the matched, noneditable pair

Create a virtual environment outside the source checkouts and install the two
wheels from the same wheelhouse. Set the filenames from the release manifest;
the placeholders below must not be guessed:

```bash
python3 -m venv /absolute/path/to/astrid-env
source /absolute/path/to/astrid-env/bin/activate

export ASTRID_WHEELHOUSE=/absolute/path/to/current-wheelhouse
export ASTRID_WHEEL_FILENAME=PENDING_PRODUCER_HANDOFF
export RUNTIME_WHEEL_FILENAME=PENDING_PRODUCER_HANDOFF
python3 -m pip install --no-index --no-deps \
  "$ASTRID_WHEELHOUSE/$ASTRID_WHEEL_FILENAME" \
  "$ASTRID_WHEELHOUSE/$RUNTIME_WHEEL_FILENAME"
python3 -m pip install --no-index --require-hashes \
  -r "$ASTRID_WHEELHOUSE/python-requirements.lock"
```

The exact artifact names, versions, SHA-256 values and dependency lock are in
[the maintained recipe](release-recipe.md). A source-profile manifest is not
part of this installed path: source injection would make an installed-origin
claim ambiguous.

## 3. Select one support root and preserve the Runtime realm

Use the canonical [workspace create/attach recipe](release-recipe.md#workspace-selection-create-or-attach-in-place).
Choose its explicit `python3 -m astrid.setup --create --apply` command for a
fresh install or `--attach --apply` for an existing installation, always with
`--workspace-id`, `--data-root`, `--realm-root` and `--json`. The matching
`--check --offline` form is the noninteractive read-only proposal; bare
`astrid.setup`, `workspace inspect` plus `up`, relocation, and automatic
workspace creation are not substitutes.

The stable-config fragment distinguishes the durable support-root envelope
from the selected Runtime realm id/root. For an existing installation, attach
that exact realm in place and preserve the same
`$ASTRID_WORKSPACE_DISCOVERY` path; never relocate data, derive a root from the
current directory, create an empty replacement, or disable identity checks.

## 4. Check the toolkit and Runtime

These are toolkit/runtime checks, not agent onboarding:

```bash
python3 -m astrid --help
python3 -m astrid doctor --json
python3 -m astrid projects list --json
```

`doctor --json` is read-only. It should identify missing or incompatible
Remotion, schema, browser, fonts, `ffmpeg`, `ffprobe`, OMP or native artifacts
without asking for a provider key. The final I08/I09 qualification must bind
each diagnostic to the exact artifact tuple.

## 5. Rendering prerequisites and the V3 review loop

The Astrid wheel is the single delivery owner for the canonical generated
client (`astrid_runtime_client`); the Runtime remains authoritative for
the timeline, realm, runs and managed outputs. The paired final manifest must
name the matching Remotion bundle, canonical timeline schema, browser/
Chromium executable, reviewed public fonts and provenance, `ffmpeg`,
`ffprobe`, OMP/agent/native inputs and app public inputs. Do not install a
browser or renderer from this guide, use an ambient schema or `omp`, or put
Runtime/provider credentials in app public inputs. Missing host/native inputs
must surface their existing diagnostic; the actual Astrid App launcher correction
and installed-host-origin proof remain NativeSol/heavy-end work.

After the selected project and timeline are supplied by Runtime, use the
supported V3 commands in this order. Do not invent a local timeline or a
second render authority:

```bash
python3 -m astrid projects current --json
python3 -m astrid timelines list --project <project> --json
python3 -m astrid timelines show <timeline> --project <project> --json
python3 -m astrid timelines visualize <timeline> --project <project> \
  --mode inputs --format md --format png --json
python3 -m astrid timelines render <timeline> --project <project> \
  --backend rendering.remotion --wait --json
```

`--mode inputs` is the render-free placement check. Inspect the Runtime-owned
render receipt/output after the render. Only when a video has been explicitly
materialized for validation from the source checkout or contributor tooling,
the optional existing black-frame checker is:

```bash
python3 tools/check_black_frames.py --video <render.mp4> \
  --timeline <timeline.json> --assets <assets.json>
```

The checker is not packaged in the installed wheel and is not a mandatory
installed-journey prerequisite. It validates an already-produced video; it does
not render, publish, or bypass Runtime custody. `tools/render_and_check.py` is
retired and must not be revived. The installed journey must instead record
renderer doctor results and Runtime-owned output validation.

## 6. Provision the workspace setup and agent skills

The setup module owns workspace selection, source composition, skill sync and
Runtime startup. Use the exact create/attach command in the
[canonical recipe](release-recipe.md#workspace-selection-create-or-attach-in-place);
the commands below are only the post-setup skill-sync checks, not a second
workspace recipe:

```bash
python3 -m astrid.skills.cli sync
python3 -m astrid.skills.cli sync --check
python3 -m astrid.skills.cli doctor --json
```

`--create` is only for an explicit fresh install whose realm root is absent or
empty. `--attach` preserves an existing selected realm/support root. The
offline check is read-only; only `--apply` configures state and starts Runtime.
Skill sync is agent onboarding after setup, not a replacement for workspace
selection and not provider authentication. Preserve existing instructions and
do not use `--force` to replace a conflicting file.

The packaged `astrid` profile is the default agent identity delivered with the
Astrid wheel. Direct agent launch and reconnect must use the exact agent
artifact and command recorded by the final recipe; `--help` is not evidence of
an agent launch.

## 7. Credentials are three different things

- **Toolkit help:** `python3 -m astrid --help`; no login or provider key.
- **Agent onboarding:** `python3 -m astrid.skills.cli sync`/the commands above; installs
  the agent-facing skill view and does not authenticate a provider.
- **Hivemind contributor login:** `astrid login`, `astrid status`,
  `astrid logout`, and `astrid revoke`; needed only when explicitly
  contributing knowledge. Approve the browser flow; do not paste the displayed
  code into a terminal.
- **Provider credentials:** set only the provider key required by the selected
  capability, for example `astrid-credential set runpod`. Provider credentials
  are not supplied by the agent sign-in and must never be embedded in app
  public inputs.

Optional provider dependencies must fail with their existing diagnostic when
absent. Install a provider artifact only when the recipe manifest names it;
never turn an optional provider into a base install requirement.

## 8. Complete setup

The eventual I09/I11 heavy-end journey is the authoritative proof of a fresh
home: install the exact wheelhouse, capture installed origins, start through
the selected root, perform one admitted task, save and read back its
Runtime-owned output, then exercise the existing update and rollback
procedure. This documentation records the commands and evidence inputs now;
it does not claim that heavy-end execution has already passed.

For current setup, record the result of the three commands above and follow
[Troubleshooting](troubleshooting.md) for a typed failure. A clean setup must
not be declared complete merely because help printed successfully.

[Getting Started](../getting-started.md) · [Current paired-release recipe](release-recipe.md) · [Credentials](credentials.md) · [Back to Astrid](../../README.md)
