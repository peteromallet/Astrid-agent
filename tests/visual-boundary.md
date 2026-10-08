# Visual boundary conformance

Portable intent is stored as a `canonical` record beside existing editor intent
bytes: `{contextVersion: "visual-seam/v1", frame, kind, participants, context}`.
`seam-intent.ts` defines the cue IDs and JSON context; Runtime's `seam_intent.py`
mirrors it. IDs are timeline-local owner paths plus cue kind, ID and frame.
Context includes fps, frame, owner spans, source bindings and relevant authored
parameters; derived reports and acknowledgements are excluded. Runtime recomputes
this context from its pinned closure. Only `synchronized` grants its named extra
cues. Old bytes survive; malformed/stale canonical records never downgrade to a
legacy acknowledgement. The Reigh cross-repository test executes the actual
Reigh authoring helper and evaluates the result in Runtime.

`fixtures/visual-boundary-v1.json` is the deterministic `visual-seam/v1` vector
set shared with Runtime. It records expected disclosure values; the old-timeline
vectors are synthetic timing evidence unless a vector explicitly carries pinned
historical provenance. The historical Runtime closure fixture separately records
`mediaLoaded: false`, so it is metadata evidence only.

When the renderer TypeScript dependency is installed, run the pure disclosure
tests and strict static check from the Astrid checkout:

```sh
node --test tests/visual-boundary.test.mjs
./remotion/node_modules/.bin/tsc --strict --target ES2020 --module commonjs --noEmit astrid/packs/local/elements/boundary.ts
```

If the compiler is available elsewhere, set `TS_COMPILER` to its
`typescript/bin/tsc` path for the Node test. This checkout currently has no
TypeScript compiler installed; the JSON remains independently parseable with:

```sh
node -e 'const f=JSON.parse(require("node:fs").readFileSync("tests/fixtures/visual-boundary-v1.json","utf8")); if(f.version!==1||!f.vectors.length) process.exit(1); console.log(`${f.vectors.length} visual boundary vectors`)'
```
