# Get help

Start in the Python environment you installed Astrid into.

```bash
python -m astrid doctor --json
astrid-local doctor --data-root "$ASTRID_LOCAL_DATA_ROOT" --json
```

## Command or module not found

Activate `.venv` from your installation folder. If the launcher script is missing
from `PATH`, use the installed module entrypoint:

```bash
python -m banodoco_local.entrypoint up --profile astrid --data-root "$ASTRID_LOCAL_DATA_ROOT" --json
```

`astrid-local` is the canonical command. `banodoco-local` and `astrid-runtime`
are deprecated aliases and emit a warning. A canonical and legacy environment
pair with different values fails closed; a legacy-only value is accepted with a
warning. Set `ASTRID_LOCAL_DATA_ROOT` and use the same absolute support root for
all lifecycle commands.

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

Run `astrid-local up --profile astrid --data-root "$ASTRID_LOCAL_DATA_ROOT" --json`
and read the structured error. Check the support root and installed environment;
the installed path does not depend on source folders. Do not delete workspace
state or credentials to force a fresh start.

## A tool needs a key or dependency

Read that pack’s skill and configure the executing environment. A healthy Runtime connection does not mean every optional tool is ready. See [Credentials](credentials.md).

## A run failed

```bash
python3 -m astrid runs show <run-id> --project <project> --json --evidence
python3 -m astrid runs events <run-id> --project <project> --json
```

Replace the bracketed values with your run and project. Correct the reported cause before retrying. For rendering issues, continue with the [renderer guide](../guides/debugging.md).

[Back to setup](README.md)
