---
name: human_review
description: Generic human-gate primitive. Serves a project HTML page, collects schema-validated JSON decisions, blocks until submit.
---

# Human Review

Reusable HTTP server for human-in-loop steps. Any orchestrator that needs a
human to look at something and produce a structured decision passes its own
HTML page + JSON data, and gets back validated JSON.

## Public SDK shape

```python
import astrid.sdk as sdk
result = sdk.invoke(
    "editorial.human_review",
    kind="action",
    project="demo",
    inputs={"html": "review/index.html", "data": "review/data.json"},
    dry_run=True,
)
```

## Internal runner CLI (not a public entrypoint)

The flexible HTTP flags below are retained for Astrid's internal runner and
debugging. Direct module invocation is rejected unless the internal invocation
marker is set; public callers should use the SDK shape above.

```
ASTRID_INTERNAL_INVOCATION=1 python3 -m astrid.packs.editorial.actions.human_review.run \
  --html <path>                    # file or dir; served at /
  --data <path>                    # JSON file, served at /data.json (read-only)
  --serve /prefix=<dir>            # repeatable; direct static mount
  --assets-bundle <zip>             # bounded managed HTML/mount archive
  --state <path>                    # POST /save applies diff payloads here
  --state-schema-bundle <zip>       # unchanged M17 three-schema bundle
  --state-schema-entry <name>       # defaults to run-state.schema.json
  --out <path>                      # POST /submit writes here, server exits 0
  --response-schema <path>          # optional; validates /submit only
  --port 0                          # auto-pick free port (default)
  --no-open                         # skip browser auto-launch
  --timeout 0                       # exit nonzero after N seconds if no submit (0=unlimited)
```

`--html` may be omitted only when `--assets-bundle` supplies the HTML
directory. The action prints the URL and session token on startup.

## Managed asset bundle

`--assets-bundle` is one bounded ZIP object (64 MiB compressed object and
64 MiB total uncompressed bytes, at most 4096 members). It must contain one
root-level `human-review-assets.json`:

```json
{
  "html_root": "html",
  "mounts": {"/clips": "clips", "/frames": "frames"}
}
```

`html_root` and mount destinations are archive-relative directories. The
receiver rejects traversal, absolute or backslash paths, symlinks and special
files, duplicate or overlapping destinations, invalid URL prefixes, and
oversized archives. Extracted files are written only beneath attempt-local
scratch and removed during cleanup. Direct `--html` and repeated `--serve`
remain available; bundle and direct mount prefixes must be unique.

## State schemas and snapshots

`--state-schema-bundle` is read in memory and never extracted. It must contain
the unchanged M17 `run-state.schema.json`, `review-decision.schema.json`, and
`filter-stats.schema.json` files. Only their declared `$id` values and
archive-relative names may satisfy `$ref`; unresolved and external references
fail closed. The entry defaults to `run-state.schema.json`. This bundle
validates the receiver's state input and each accepted update; it is separate
from `--response-schema`, which validates `/submit`.

With an inherited managed-child bridge, each accepted `/save` revision or
`/submit-batch` mutation is staged as the exact encoded state bytes at
`state_result.json` and sent through the private
`_child_bridge._bridge.publish_snapshot` operation on output port
`state_result`. The browser receives HTTP success only after the receipt
matches the revision and port, has the exact bytes and size, and is durable.
Publication refusal, failure, or an uncertain, malformed, mismatched, or
non-durable receipt is an error, not an acknowledgment; local state is not
advanced. A bridge without the admitted snapshot grant therefore fails
closed. `/save` and `/submit-batch` serialize through one state lock.
Standalone execution keeps local atomic saves. On successful `/submit`,
`state_result.json` contains the final state when `--state` exists; that
settlement output remains separate from recoverable snapshot acknowledgments.

## Routes

| Method | Path                    | Behavior |
|--------|-------------------------|----------|
| GET    | `/?token=<t>`           | Serves direct `--html` or the bundle HTML directory's `index.html`. |
| GET    | `/data.json`             | Read-only mount of `--data`. |
| GET    | `/state.json?token=<t>` | Returns `--state` contents (200), or 404 if absent. |
| GET    | `/<prefix>/...`          | Static mount per `--serve` or bundle manifest. Supports HTTP Range. |
| POST   | `/save`                  | Applies a dataset diff or experiment-review draft with stale-version protection. |
| POST   | `/submit-batch`          | Applies one decision to selected data items with stale-version protection. |
| POST   | `/submit`                | Validates the response body, atomically writes `--out`, writes final state output when present, and shuts down. |

All POSTs and `/state.json` require the session token. Static media GETs are
intentionally unauthenticated for normal `<video>` and `<img>` use.

## State save semantics

For dataset review, clients first read `/state.json`, then submit only changed
item revisions with the observed `base_state_version`. Full replacement state
is rejected. Batch decisions use `/submit-batch` with the same guard.
Experiment-review callers use `{base_state_version, draft}` with their
canonical `experiment_id`. The server never silently clobbers stale state.
