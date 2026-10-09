# Local-font acceptance receipt

Receipt date: 2026-08-30

The acceptance probe is committed at
`tests/fixtures/remotion-local-font-probe.json`. It renders the exact glyph
probe `Astrid 2RP — Aa 0123` through the Sixtyfour heading family while the
loader simultaneously waits for and checks Sixtyfour, Inter, and JetBrains
Mono. The probe uses no media assets, so a successful non-black text frame is
font evidence rather than an incidental source image.

## Checks

- `python3 -m pytest -q tests/test_remotion_local_fonts.py` — PASS (4 tests).
- `npm run typecheck` — PASS.
- `npm run bundle` — PASS.
- `sandbox-exec -p '(version 1) (allow default) (deny network-outbound) (allow network-outbound (remote ip "localhost:*"))' ./node_modules/.bin/remotion render src/index.ts TimelineComposition /tmp/astrid-local-font-offline.mp4 --props ../tests/fixtures/remotion-local-font-probe.json --frames=0-29 --log=verbose` — PASS (exit 0; committed network-denial receipt below).
- Offline render receipt: H.264/AAC, 1920×1080, 30 frames, 1.045333 seconds; output SHA-256 `64af64f4d9076375eb1339fcb15cce5f6da6587c217d403c6e0119ad3c058f28`.
- Frame-15 visual evidence SHA-256 `ef0b5418d465bdfaddfcf3765e7fe1bd225120d4b2f1c86d300d8453f722f05e` (Sixtyfour, Inter, and JetBrains Mono rows all visibly rendered).
- Verbose output recorded five `Loading Astrid local fonts` completions and zero Google-font host markers. The full filtered receipt is `LOCAL_FONT_NETWORK_DENY.log`.

## Offline contract

The focused test rejects `@remotion/google-fonts`, Google Fonts hosts,
`fetch()`, and `XMLHttpRequest` in the loader. The runtime loader constructs
all sources from Remotion `staticFile()` paths, passes the committed glyph
probe to `document.fonts.load()` and `document.fonts.check()`, and invokes
`cancelRender()` on any missing face. This is the fail-closed path: offline
failure cannot silently become a system-font or hosted-font fallback.

The repository's broader `npm run smoke` remains a known pre-existing failure
because its generated-types snapshot omits `derivedFrom`, `media_id`, and
`origin` while the generated source exports them; this font change does not
touch that snapshot or alter the failure.

## Re-vendor of Inter and JetBrains Mono (2026-10-09)

Why: the Inter and JetBrains Mono faces shipped since 2026-08-30 cover almost
no printable ASCII. Lowercase letters, digits and most punctuation were absent
from the cmap, so the browser silently drew them in the next family of the
stack. The loader cannot see this: `document.fonts.check` reports a loaded face
as ready regardless of its cmap, and the frame-15 visual evidence above cannot
tell a shipped face from a system fallback. The Sixtyfour, Departure Mono and
Gelasio faces already cover all 95 printable ASCII characters.

What changed: Inter 400/700 and JetBrains Mono 400/700 are now static faces
made from the official upstream releases, not the variable Google Fonts
subsets. Sources and hashes are in `FONT_PROVENANCE.md`. The subset uses the
Latin set of the Gelasio derivation plus the arrow block U+2190-2199. Filenames
and the `LOCAL_FONT_FACES` entries are unchanged.

Coverage (cmap codepoints; printable ASCII out of 95; label extras out of 17,
which are U+00B7 · U+00D7 × U+2212 − U+2192 → U+2190 ← U+2191 ↑ U+2193 ↓
U+2014 — U+2013 – U+2018 ‘ U+2019 ’ U+201C “ U+201D ” U+2026 … U+2009 thin
space U+00B0 ° U+00E9 é):

| Face | Before: cmap / ASCII / extras / bytes / fvar | After: cmap / ASCII / extras / bytes / fvar |
| --- | --- | --- |
| Inter-Regular | 157 / 2 / 0 / 27,380 / yes | 292 / 95 / 17 / 40,128 / no |
| Inter-Bold | 107 / 2 / 0 / 19,980 / yes | 292 / 95 / 17 / 41,296 / no |
| JetBrainsMono-Regular | 10 / 2 / 0 / 2,180 / yes | 249 / 95 / 16 / 35,416 / no |
| JetBrainsMono-Bold | 102 / 2 / 0 / 13,352 / yes | 249 / 95 / 16 / 36,856 / no |

Known gap, not fixed: the upstream JetBrains Mono has no U+2009 thin space, so
that character falls back in the JetBrains Mono faces. Gelasio's Google Fonts
latin subset has no U+2190 or U+2192 arrows. Neither gap affects printable
ASCII, which is what the regression test enforces.

Checks run (2026-10-09): `tests/test_remotion_local_fonts.py` passes, 6 of 6,
including every shipped face's printable ASCII coverage against
`tests/fixtures/remotion-font-coverage.json`, a recomputation from the binaries
with `fontTools` (present in the venv), and the byte/hash manifest. Negative
controls: swapping in the old Inter binary, a record that reports `a` missing,
and an unrecorded extra `.woff2` each make the test fail. Visual check: a PIL
render of the shipped woff2 files shows the new glyphs (PIL draws the old
binaries as missing-glyph boxes; a browser would show the system fallback).

Not re-run: the offline Remotion render above (output hash `64af64f4...`,
frame-15 hash `ef0b5418...`) was produced with the old Inter and JetBrains Mono
binaries and no longer describes the shipped faces. The re-render needs the
Remotion install (Node 20.19.4, `remotion/node_modules`) and is pending. Until
it is re-run, the render evidence for these two families is missing.
