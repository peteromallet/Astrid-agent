# Get help

Start in the Python environment where Astrid is installed.

```bash
astrid status --json
astrid doctor --diagnostic --json
```

These are observer commands. They do not start, reconnect, repair, retry, or
migrate a service. Use `astrid setup` for workspace configuration and
`astrid worker start --json` for the explicit Worker lifecycle action.

## Command or module not found

Activate `.venv` from the installation folder. If the `astrid` script is
missing from `PATH`, use the installed module entrypoint:

```bash
python -m astrid status --json
python -m astrid doctor --diagnostic --json
```

`astrid` is the product command. `astrid-local` is the lower-level operator
launcher; `banodoco-local` and `astrid-runtime` are deprecated aliases and emit
a warning. A canonical and legacy environment pair with different values fails
closed; a legacy-only value is accepted with a warning. Set
`ASTRID_LOCAL_DATA_ROOT` and use the same absolute support root for all
lifecycle commands.

## Installed provenance or source profile

The installed closeout profile does not require a source manifest. Check the
bounded installed identity first:

```bash
astrid-local --provenance
astrid-local workspace inspect --data-root "$ASTRID_LOCAL_DATA_ROOT" --json
```

For editable repository development only, set
`ASTRID_LOCAL_SOURCE_MANIFEST`. The manifest must use absolute paths to the
actual checkouts and must not be symlinked. If it already exists, inspect its
paths instead of overwriting it.

## Runtime cannot connect

Run `astrid setup --attach --apply --json` for an existing selected workspace,
or `astrid setup --create --apply --json` for a new one, and read the
structured error. If setup has already recorded a workspace but Runtime is
stopped, use the proposed `astrid-local up --data-root
"$ASTRID_LOCAL_DATA_ROOT" --json` operator action. Check the support root and
installed environment; the installed path does not depend on source folders.
Do not delete workspace state or credentials to force a fresh start.

## Worker cannot start

Start the selected Worker only through the product gateway:

```bash
astrid worker start --json
```

The response identifies the Runtime-owned handoff and the next action. Runtime
owns worker registration, lease/fence state, credentials, task admission, and
settlement. A direct `run_worker.py`, `worker.py`, or database/task loop is not
an Astrid route and cannot establish workspace ownership. If the Worker profile
is unavailable, keep the typed failure and fix the reported installation or
capability issue before retrying the explicit start command.

## Migration or upgrade issue

Use the selected support root and the Runtime-owned upgrade command:

```bash
astrid-upgrade
astrid-local upgrade --data-root "$ASTRID_LOCAL_DATA_ROOT" --json
```

Keep the migration archive and the original workspace until the upgrade
reports success. Do not open SQLite/CAS files, copy an old root by hand, or
add `PYTHONPATH` or a sibling checkout to make an installed failure disappear.
If active work is present, wait for it to settle; migration refuses to
interrupt it.

## A tool needs a key or dependency

Read that pack’s skill and configure the executing environment. A healthy
Runtime connection does not mean every optional tool is ready. See
[Credentials](credentials.md).

## A run failed

```bash
astrid runs show <run-id> --project <project> --json --evidence
astrid runs events <run-id> --project <project> --json
```

Replace the bracketed values with your run and project. Correct the reported
cause before retrying. For rendering issues, continue with the [renderer
guide](../guides/debugging.md).

[Back to setup](README.md)
