# B01 / D16 arrangement compiler handoff

Date: 2026-10-02
Worktree HEAD at preflight: `9be144c4f2f63216e693a1a13e42cd79d3845845`

## Delivered contract

The pure `compile_arrangement_plan` implementation and its policy constants now
live in `astrid/core/timeline/arrangement_compiler.py` and are exported from
`astrid.core.timeline`:

- `compile_arrangement_plan`
- `ROLE_DURATION_BOUNDS`
- `TOTAL_DURATION_BOUNDS`
- `TRIM_BOUND_EXTENSION_SEC`
- `MIN_OVERLAY_COVERAGE_SEC`
- `MAX_VISUAL_HOLD_RATIO`

The implementation is an exact source move from the preflight Editorial source:

```text
source equivalence: diff -u <(git show HEAD:astrid/packs/editorial/hype/arrangement_rules.py) astrid/core/timeline/arrangement_compiler.py
result: exit 0, no output
HEAD source Git blob: 1bd8eb0348c8f34c0352599b7f225fda107e929b
core source Git blob: 1bd8eb0348c8f34c0352599b7f225fda107e929b
core source SHA-256: ec4e0a6e6d7ea565d129a252a96962c23ce302ba2dc4d6e4f4869c322fe5f036
```

The temporary Editorial compatibility shim at
`astrid/packs/editorial/shared/hype/arrangement_rules.py` contains only imports
and `__all__`; it has no compiler definition. Its Git blob is
`f602d8515aefd2c9281fa68f1844cb1c05982c4d` and its SHA-256 is
`cfa70a1a4a29e112e44e4c9b0a1a25664d13e1026e1626ce5ae0af69fc7231ca`.

The public export and shim identity check passed:

```text
python3 -c 'from astrid.core import timeline; from astrid.core.timeline import arrangement_compiler; from astrid.packs.editorial.shared.hype import arrangement_rules; assert timeline.compile_arrangement_plan is arrangement_compiler.compile_arrangement_plan is arrangement_rules.compile_arrangement_plan; assert arrangement_rules.ROLE_DURATION_BOUNDS is timeline.ROLE_DURATION_BOUNDS; print("public-and-shim-identity=ok")'
result: public-and-shim-identity=ok
```

Compiler total bounds remain inclusive `70.0`–`95.0` seconds. The focused
tests also confirm the separate validator defaults remain `75.0`–`90.0`.

## Verification

```text
python3 -m pytest -q tests/timeline/test_arrangement_compiler.py
result: 6 passed in 0.13s

git diff --check -- astrid/core/timeline/__init__.py astrid/packs/editorial/shared/hype/arrangement_rules.py
result: exit 0

git diff --no-index --check /dev/null astrid/core/timeline/arrangement_compiler.py
result: exit 1 with no whitespace diagnostics (expected because the file is untracked)

git diff --no-index --check /dev/null tests/timeline/test_arrangement_compiler.py
result: exit 1 with no whitespace diagnostics (expected because the file is untracked)
```

The focused tests cover exact valid ordering/output, exact representative
validation errors, generative-only slot allocation, shared object identity, and
the compiler's 70/95 boundaries.

## Existing-test limitation

The requested directly affected suites were attempted without changing their
callers:

```text
python3 -m pytest -q tests/timeline/test_multitrack_cut.py
result: collection error, ModuleNotFoundError: No module named 'astrid.packs.editorial.hype'

python3 -m pytest -q tests/packs/video_editing/test_arrange.py
result: collection error, ModuleNotFoundError: No module named 'astrid.packs.editorial.executors'
```

Those package paths are already deleted in the dirty preflight worktree and are
outside this reservation. Restoring or rewriting those caller-owned paths would
violate the exact write set. M04 owns the sequential Editorial caller/resource
follow-up and M21 owns the two Video Editing imports; no callers, manifests,
resources, Rendering/runtime-stitch code, or unrelated paths were changed here.

The Editorial shared shim is handed to the coordinator for audit. It is not
released to M04 until that audit, and this handoff creates no new task or review
stage. Runtime-stitch remains open under the separate B01/M13/M21 closure.
