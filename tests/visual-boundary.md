# Visual boundary conformance

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
