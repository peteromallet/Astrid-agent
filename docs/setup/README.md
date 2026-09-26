# Set up Astrid

Install once. Then tell your agent what you want to make.

The final-state local launcher is qualified on **macOS** with Python 3.11.16 for
Astrid/Runtime, Python 3.10.21 for the Worker profile, and Node 20.19.4/npm
10.8.2 for app checks. Linux and Windows setup have not yet been validated; this
guide does not claim support for them.

## 1. Install

You need Python 3.11.16 for the Astrid/Runtime install. Git is only needed when
installing directly from the pinned public revisions. Check them in Terminal:

```bash
git --version
python3.11 --version
```

Use a new folder for this installation. If you already have Astrid, keep that
installation and begin with [checking it](#3-check-your-workspace). The commands
below install pinned distributions; they do not make an Astrid or Runtime
checkout part of the live import path.

```bash
mkdir astrid-local
cd astrid-local
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install 'Astrid @ git+https://github.com/peteromallet/Astrid.git@astrid-plan-a-final-state-closeout-20260925'
python -m pip install 'banodoco-workspace-runtime @ git+https://github.com/banodoco/banodoco-workspace-runtime.git@astrid-plan-a-final-state-closeout-20260925'
export ASTRID_LOCAL_DATA_ROOT="$PWD/.astrid-data"
astrid-local --provenance
```

These commands use the named closeout branch for each pinned public repository;
the final implementation commit SHAs are recorded in the release provenance
once the branches are published. Do not mix an existing installation with
unrelated development revisions. The installed profile must not depend on a
source checkout or `PYTHONPATH`.

## 2. Start Runtime once

From the same `astrid-local` folder, start or reconnect the selected Runtime.
The installed profile derives its bounded provenance from the installed module;
no source manifest is required:

```bash
astrid-local up --profile astrid --data-root "$ASTRID_LOCAL_DATA_ROOT" --json
```

This is a one-time configuration for this installation. Runtime creates or
reconnects the local realm and credentials under the support root. `astrid-local`
is the canonical launcher; `banodoco-local` and `astrid-runtime` remain
deprecated aliases and print a warning. If both a canonical and a legacy
environment variable are set to different values, setup fails closed. A
legacy-only value is accepted with a warning; use `ASTRID_LOCAL_*` names in new
configuration.

For editable repository development, create an explicit source profile and set
`ASTRID_LOCAL_SOURCE_MANIFEST`. Keep that workflow separate from this installed
closeout path; it requires absolute, symlink-free checkout paths and may use
`PYTHONPATH` only inside the development environment.

## 3. Check your workspace

```bash
python -m astrid doctor --json
python -m astrid projects list --json
```

Both commands should succeed. A new workspace may have no projects yet. If either fails, follow [Troubleshooting](troubleshooting.md).

## 4. Add the default knowledge pack

From the Astrid checkout, using the same environment:

```bash
cd Astrid
python -m astrid.setup
python -m astrid.setup --check --offline
```

Setup provisions the default Hivemind source. Searching its community knowledge requires an internet connection.

## 5. Sync your agent’s skills

```bash
python -m astrid.skills.cli sync
python -m astrid.skills.cli sync --check
python -m astrid.skills.cli doctor --json
```

Sync installs the core skill and its pack navigation into detected supported agents: Claude Code, Codex, and Hermes. Individual pack links are optional (`sync --deep`). Keep existing user instructions; do not use `--force` to replace conflicting files.

Check the install report for the agent you use. If it was not detected, use the [skills installation guide](../guides/skills-install.md). If the agent requires a new session to discover skills, open one and check that it can read Astrid’s core skill and follow a pack link.

## 6. Check the complete setup

- `python -m astrid --help` opens the command help.
- `python -m astrid doctor --json` and `projects list --json` succeed.
- Skill sync reports no drift, and the skills doctor succeeds.
- Your agent can find the core skill and a linked pack skill.

For a direct interactive launcher, also verify that launching Astrid actually opens an agent, that the agent can load the skills and read the workspace, and that closing and reopening it reconnects successfully. CLI help alone does not pass this check.

The inspected source exposes the Astrid command gateway; a direct interactive-agent launch command and its authentication reuse are not yet verified. Until they are, use Astrid through your existing agent and keep its existing sign-in. That sign-in does not automatically supply credentials to every creative provider.

## 7. Choose how to continue

Once the checks pass, your setup agent should tell you what is ready and ask what you want to make.

If direct agent launch and authentication reuse both passed, it should offer:

> Astrid is ready. You can launch it directly with the verified command and use the existing sign-in supported by that agent, or keep using Astrid here. What would you like to make?

Include the actual tested launch command and agent name. If that path is unavailable, say simply:

> Astrid is ready to use here through your current agent. Direct agent launch is not verified in this version. What would you like to make?

[How Astrid works](../guides/how-it-works.md) · [Credentials](credentials.md)

## Come back later

You do not need to recreate credentials. From your `astrid-local` folder:

```bash
source .venv/bin/activate
export ASTRID_LOCAL_DATA_ROOT="$PWD/.astrid-data"
astrid-local status --data-root "$ASTRID_LOCAL_DATA_ROOT" --json
python -m astrid projects list
```

[Credentials](credentials.md) · [Troubleshooting](troubleshooting.md) · [Back to Astrid](../../README.md)
