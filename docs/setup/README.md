# Set up Astrid

Install once. Then tell your agent what you want to make.

The final-state local launcher is qualified on **macOS** with Python 3.11.16 for
Astrid/Runtime, Python 3.10.21 for the Worker profile, and Node 20.19.4/npm
10.8.2 for app checks. Linux and Windows setup have not yet been validated; this
guide does not claim support for them.

## 1. Install the pinned composition

You need Python 3.11.16 for the Astrid/Runtime install. This guide installs the
qualified source revisions directly; it does not claim that a public wheel or
hosted release artifact exists. Keep the immutable implementation commits in the
shell that performs the install:

```bash
git --version
python3.11 --version
export ASTRID_COMMIT='237d73717f00ebce43ca8ce516b7321c4ebcda7e'
export RUNTIME_COMMIT='2cfb894c40d9abc19fc89c46707162386942f74c'
[[ "$ASTRID_COMMIT" =~ ^[0-9a-f]{40}$ && "$RUNTIME_COMMIT" =~ ^[0-9a-f]{40}$ ]]
```

These are the implementation commits used for the installed qualification:
Astrid `237d73717f00ebce43ca8ce516b7321c4ebcda7e`, Runtime
`2cfb894c40d9abc19fc89c46707162386942f74c`, Worker
`935efa2f31d92507738901a020274f468ae87f10`, and App
`9d1e0b0bb7c9490457943189cf477219224b007c`. The Worker and App commits are
recorded for the cross-repository composition. Astrid/Runtime and the Worker use
separate Python environments; installing Astrid and Runtime alone does not
configure a local Worker. Documentation-only commits made after this qualification
do not change these pins or the tested artifacts.

Use a new folder for this installation. If you already have Astrid, keep that
installation and begin with [checking it](#3-check-workspace-diagnostics-and-worker). The commands
below install pinned distributions; they do not make an Astrid or Runtime
checkout part of the live import path.

```bash
mkdir astrid-local
cd astrid-local
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install "Astrid @ git+https://github.com/peteromallet/Astrid.git@${ASTRID_COMMIT}"
python -m pip install "banodoco-workspace-runtime @ git+https://github.com/banodoco/banodoco-workspace-runtime.git@${RUNTIME_COMMIT}"
export ASTRID_LOCAL_DATA_ROOT="$PWD/.astrid-data"
astrid-local --provenance
```

The shell guard rejects moving branch names and malformed refs. Do not replace
these commits with a branch name or mix the installation with unrelated
development revisions. The installed profile must not depend on a source checkout
or `PYTHONPATH`.

### Provide the independent Worker composition profile

`astrid worker start` requires an installed Worker composition profile. The
profile is an identity-bearing installation artifact: it pins the independent
Python 3.10 Worker executable, the Astrid host and engine executables, their
digests, the installed pack root, boot manifest, engine endpoint, session
configuration, and installation-owned roots. A `reigh-worker` install by itself
does not create this profile.

Install the pinned Worker distribution in its own Python 3.10 environment:

```bash
cd /absolute/path/to/astrid-local
python3.10 -m venv .worker-venv
.worker-venv/bin/python -m pip install \
  "reigh-worker @ git+https://github.com/banodoco/reigh-worker.git@935efa2f31d92507738901a020274f468ae87f10"
test "$(.worker-venv/bin/python -c 'import sys; print("%d.%d" % sys.version_info[:2])')" = 3.10
```

Your installation bundle must also provide an absolute, symlink-free installed
Worker profile whose `worker_environment` and `worker_executable` select that
environment and whose remaining artifact, engine, boot-manifest, endpoint, and
root fields describe the same installed composition. This repository does not
currently ship a public profile generator or a generic public engine artifact;
do not reuse the qualification fixture profile or invent digest values.

Given a profile supplied with the installed composition, bind it to the
installed Runtime with an explicit source manifest:

```bash
export ASTRID_WORKER_PROFILE=/absolute/path/to/installed-worker-profile.json
export ASTRID_LOCAL_SOURCE_MANIFEST="$PWD/installed-source-profile.json"
test -f "$ASTRID_WORKER_PROFILE"
python - "$ASTRID_WORKER_PROFILE" "$ASTRID_LOCAL_SOURCE_MANIFEST" <<'PY'
import json
from pathlib import Path
import sys
from banodoco_local.bootstrap import SourceProfile

worker_profile = Path(sys.argv[1]).expanduser().resolve(strict=True)
destination = Path(sys.argv[2]).expanduser().resolve()
profile = SourceProfile.installed().as_dict()
profile["worker_profile"] = str(worker_profile)
destination.write_text(json.dumps(profile, indent=2, sort_keys=True) + "\n", encoding="utf-8")
PY
```

Use that same manifest for setup and later Runtime lifecycle commands. If no
installed Worker profile is available, the supported state is Runtime-only:
setup and diagnostics can run, while `astrid worker start --json` must report
`No local Worker profile is configured; set worker_profile in the Astrid source
profile and restart the Runtime`.

## 2. Preview and apply one workspace

Use the product gateway for setup. Preview and check are nonstarting; apply
creates or attaches one explicit workspace and starts its selected Runtime:

```bash
astrid setup --create --check --source-manifest "$ASTRID_LOCAL_SOURCE_MANIFEST" --json
astrid setup --create --apply --source-manifest "$ASTRID_LOCAL_SOURCE_MANIFEST" --json
```

The guided form asks for the workspace UUID, absolute support root, and
absolute realm root. `--attach` requires an existing UUID and root; it never
copies or silently adopts a checkout. Runtime owns the selected workspace,
credentials, database, task ledger, and outputs.

`astrid-local` is the lower-level operator surface for an already selected
workspace (`up`, `connect`, `status`, `doctor`, `restart`, `down`, and
`start-worker`). It does not provide a `setup` subcommand; workspace creation and
attachment belong to the product gateway above.
`banodoco-local` and `astrid-runtime` remain deprecated aliases and print a
warning. If canonical and legacy environment values differ, setup fails
closed; a legacy-only value is accepted with a warning. Use `ASTRID_LOCAL_*`
names in new configuration.

An explicit installed source profile selects the Worker profile above. Editable
repository development uses a different source profile with absolute,
symlink-free checkout paths and may use `PYTHONPATH` only inside the development
environment.

## 3. Check workspace, diagnostics, and Worker

```bash
astrid status --json
astrid doctor --diagnostic --json
astrid worker start --json
astrid projects list --json
```

`status` and `doctor` are observation only: they do not start, repair, retry,
or migrate services. With the installed source manifest selected, `worker
start` is the explicit lifecycle action and returns a typed Runtime-owned
handoff; it launches the verified local Worker and one `GenericPackHost`. The
Worker profile uses Python 3.10.21. A new workspace may have no projects yet.
If a command fails, follow
[Troubleshooting](troubleshooting.md).

## 4. Add the default knowledge pack

The apply composition provisions the default Hivemind source. Searching its
community knowledge requires an internet connection. To preview the same pack
selection without acquisition, use the setup plan with `--offline`; keep the
same explicit input document when moving from check to apply:

```bash
astrid setup --input ./setup.json --check --offline --json
astrid setup --input ./setup.json --apply --json
```

## 5. Sync your agent’s skills

```bash
python -m astrid.skills.cli sync
python -m astrid.skills.cli sync --check
python -m astrid.skills.cli doctor --json
```

Sync installs the core skill and its pack navigation into detected supported agents: Claude Code, Codex, and Hermes. Individual pack links are optional (`sync --deep`). Keep existing user instructions; do not use `--force` to replace conflicting files.

Check the install report for the agent you use. If it was not detected, use the [skills installation guide](../guides/skills-install.md). If the agent requires a new session to discover skills, open one and check that it can read Astrid’s core skill and follow a pack link.

## 6. Check the complete setup

- `astrid --help` and `astrid --version` work from the installed environment.
- `astrid status --json`, `astrid doctor --diagnostic --json`, and
  `astrid projects list --json` return structured results.
- `astrid worker start --json` reports the Runtime-owned Worker handoff.
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
astrid projects list --json
```

[Credentials](credentials.md) · [Troubleshooting](troubleshooting.md) · [Back to Astrid](../../README.md)
