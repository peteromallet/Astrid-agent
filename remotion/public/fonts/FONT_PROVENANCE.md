# Astrid local font provenance

These are the only font binaries used by the Remotion render. They are bundled
so an offline render never asks Google Fonts, a CDN, or another network host
for CSS or font bytes. `OFL-1.1.txt` is shipped beside these binaries and
contains the complete license text and copyright notices.

The upstream Google Fonts repository revision used for the Gelasio records
below is:

`ade3d1533e06b2b1462ffcde8e08b129627ca360`

Inter and JetBrains Mono were re-vendored from their upstream releases (the
Google Fonts copies they replace covered 2 of 95 printable ASCII characters;
see `LOCAL_FONT_ACCEPTANCE.md`).

Every family below is distributed under the SIL Open Font License, Version
1.1. The local binary SHA-256 values below are the acceptance-manifest values;
the upstream Git blob values identify the authoritative source record and are
not expected to equal a locally subsetted or format-converted binary.

## Sixtyfour

- Theme role: 2RP heading (`Sixtyfour`, variable BLED/SCAN axes).
- Local file: `Sixtyfour.woff2` (7,608 bytes).
- Local SHA-256: `0c35bb8333a12a822333f10fc4fd22e607b80a254b8b31faa8eed1cd4badc24e`.
- Upstream source: [homecomputer-fonts Sixtyfour webfont](https://github.com/jenskutilek/homecomputer-fonts/blob/22842dfb97fd3e7970383625cd3d10108edb8b5e/Sixtyfour/fonts/webfonts/Sixtyfour%5BBLED%2CSCAN%5D.woff2).
- Upstream revision: `22842dfb97fd3e7970383625cd3d10108edb8b5e`.
- Upstream Git blob: `368300391c703dcc22e6ea33fa9cff1cd377d385`.
- License: SIL Open Font License 1.1; copyright notice is `Copyright 2021 The Sixtyfour Project Authors (https://github.com/jenskutilek/homecomputer-fonts)`.
- Google Fonts curation record: `ofl/sixtyfour/` at the pinned Google Fonts revision above; its source TTF blob is `32985b4486ba627ad9b044be33b6d801b2d6d171`.

## Inter

- Theme role: 2RP body text.
- Local files: `Inter-Regular.woff2` (40,128 bytes, 400 upright), `Inter-Bold.woff2` (41,296 bytes, 700 upright). Static faces; they replace the earlier variable Google Fonts subsets.
- Local SHA-256: `5f04e9f02a74eab4ec87eda5b79b5be1c0567c868688b0a08564b2fe5c6da928` (regular); `aeaf0d0fa3262f4df2da7acf660544cb15aa24dcb63e574daed6ba7e92333a01` (bold).
- Upstream source: [rsms/inter release v4.1](https://github.com/rsms/inter/releases/tag/v4.1), asset `Inter-4.1.zip` (33,707,794 bytes, SHA-256 `9883fdd4a49d4fb66bd8177ba6625ef9a64aa45899767dde3d36aa425756b11e`). Members used: `extras/ttf/Inter-Regular.ttf` (411,640 bytes, SHA-256 `40d692fce188e4471e2b3cba937be967878f631ad3ebbbdcd587687c7ebe0c82`) and `extras/ttf/Inter-Bold.ttf` (420,428 bytes, SHA-256 `288316099b1e0a47a4716d159098005eef7c0066921f34e3200393dbdb01947f`).
- Upstream revision: tag `v4.1` at commit `e3a3d4c57d5ecc01453a575621882a384c1995a3`.
- Derivation: the official static TTFs are subset to the Latin set listed under Gelasio, plus the arrow block U+2190-2199, with all layout features kept and written as WOFF2 (`fontTools` 4.60.2, `brotli` 1.2.0). Result: 292 codepoints, 95/95 printable ASCII.
- Previous files (superseded, Google Fonts `ofl/inter` at ade3d15, variable `Inter[opsz,wght].ttf`): 27,380 and 19,980 bytes, SHA-256 `39689184...` and `47d42151...`. They covered 2 of 95 printable ASCII.
- License: SIL Open Font License 1.1; copyright notice is `Copyright (c) 2016 The Inter Project Authors (https://github.com/rsms/inter)`, as in the release `LICENSE.txt`.

## JetBrains Mono

- Theme role: 2RP monospace text.
- Local files: `JetBrainsMono-Regular.woff2` (35,416 bytes, 400 upright), `JetBrainsMono-Bold.woff2` (36,856 bytes, 700 upright). Static faces; they replace the earlier Google Fonts subsets.
- Local SHA-256: `2955337d809a64d58c56730b8344315198ec76214e02710774dd72d6a5de7b87` (regular); `a1475eef9934d6b3a6d15494f312ee1056b7556d59b1e22cb4e0c618338cd170` (bold).
- Upstream source: [JetBrains/JetBrainsMono release v2.304](https://github.com/JetBrains/JetBrainsMono/releases/tag/v2.304), asset `JetBrainsMono-2.304.zip` (5,622,857 bytes, SHA-256 `6f6376c6ed2960ea8a963cd7387ec9d76e3f629125bc33d1fdcd7eb7012f7bbf`). Members used: `fonts/ttf/JetBrainsMono-Regular.ttf` (SHA-256 `a0bf60ef0f83c5ed4d7a75d45838548b1f6873372dfac88f71804491898d138f`) and `fonts/ttf/JetBrainsMono-Bold.ttf` (SHA-256 `5590990c82e097397517f275f430af4546e1c45cff408bde4255dad142479dcb`).
- Upstream revision: tag `v2.304` at commit `cd5227bd1f61dff3bbd6c814ceaf7ffd95e947d9`.
- Derivation: the same subset as Inter. Result: 249 codepoints, 95/95 printable ASCII. The upstream font has no U+2009 thin space, so that character is not in these faces and falls back.
- Previous files (superseded, Google Fonts `ofl/jetbrainsmono` at ade3d15): 2,180 and 13,352 bytes, SHA-256 `1b535365...` and `8df3ca62...`. They covered 2 of 95 printable ASCII.
- License: SIL Open Font License 1.1; copyright notice is `Copyright 2020 The JetBrains Mono Project Authors (https://github.com/JetBrains/JetBrainsMono)`, as in the release `OFL.txt`.

## Departure Mono

- Theme role: Astrid video label face (pixel monospace, `production/STYLE.md`); family name `Departure Mono`.
- Local file: `DepartureMono-Regular.woff2` (22,496 bytes), copied byte-for-byte from the upstream release; no subsetting or conversion.
- Local SHA-256: `5b4fed1daa90708aa9c6ee1190abca9dc22164a1c1def0020386e46b61038cfb`. This is identical to the astrid.haus copy at `production/references/site/raw/DepartureMono-Regular.woff2`.
- Upstream source: [rektdeckard/departure-mono release v1.500](https://github.com/rektdeckard/departure-mono/releases/tag/v1.500), asset `DepartureMono-1.500.zip` (82,890 bytes, SHA-256 `bf3e48059aeef4617ec585bdea81dcc3491c576b3e7a472f52faf40e09ee5c3a`), member `DepartureMono-1.500/DepartureMono-Regular.woff2`.
- Upstream revision: tag `v1.500` at commit `75152a3f1e6dacdd248a6c397c97dbf27e33eea0` (released 2025-05-25). The woff2 has 1,079 codepoints and covers all 95 printable ASCII characters.
- Author: Helena Zhang.
- License: SIL Open Font License 1.1; copyright notice is `Copyright 2022–2024 Helena Zhang (helenazhang.com)`. The license text is the release archive's `LICENSE` file (reproduced in `OFL-1.1.txt`), and the font's name table records the OFL in nameID 13. The repository's root `LICENSE` file is MIT; it is not the license of the font binary and is not relied on here.

## Gelasio

- Theme role: Astrid video headline serif, a metric-compatible stand-in for Georgia (`production/STYLE.md` names Georgia as the display face and Georgia is a system font that the renderer does not load). Designer: Eben Sorkin.
- Local files: `Gelasio-Regular.woff2` (20,284 bytes, 400 upright), `Gelasio-Italic.woff2` (22,272 bytes, 400 italic), `Gelasio-Bold.woff2` (20,452 bytes, 700 upright), `Gelasio-BoldItalic.woff2` (22,476 bytes, 700 italic).
- Local SHA-256: `698c2aa61ec1960371b6ed32e6120f028fa614613256f4b9e3f5d10ceb013eb5` (regular); `2df3d4a4d0cefc6440710666fa425627e54cb73707224bec044bca8e65940222` (italic); `2fdaa469d51ef618d648e27819f10a1df08721ed2331614c3123850162cc147a` (bold); `e5b0682ff74c6798100255daafaf5d5496c5ddec596aec663fdd0afb3470b609` (bold italic).
- Upstream family: [Google Fonts `ofl/gelasio`](https://github.com/google/fonts/tree/ade3d1533e06b2b1462ffcde8e08b129627ca360/ofl/gelasio), whose variable sources are `Gelasio[wght].ttf` (168,556 bytes, SHA-256 `4daecea457258c9ebeb8bc99ed3fd24353618bfad3ea4b93fa0b5d0468fc04e4`, Git blob `23711d2fa49ab8c60301f8fd718cc10614db693a`) and `Gelasio-Italic[wght].ttf` (174,212 bytes, SHA-256 `52559e845a4d33514e5f93bb9ae7dbeae1894a53f2c565a15f18af40cd337c09`, Git blob `21e3c87ca4dbf1e298a0b4ce5e31e1fbeb0e7ea4`).
- Upstream revision: `ade3d1533e06b2b1462ffcde8e08b129627ca360` (Google Fonts). The upstream repository is [SorkinType/Gelasio](https://github.com/SorkinType/Gelasio) at commit `9228e69b160d79f33950e026293f6e13ba9780d0`; both variable TTFs there (`fonts/variable/`) are byte-identical to the Google Fonts files above (SHA-256 checked).
- Derivation: the static faces are instances of the variable TTFs at `wght=400` and `wght=700` (`fontTools` 4.60.2 `varLib.instancer --update-name-table`), then subset to the Google Fonts `latin` range (`U+0000-00FF, U+0131, U+0152-0153, U+02BB-02BC, U+02C6, U+02DA, U+02DC, U+0304, U+0308, U+0329, U+2000-206F, U+20AC, U+2122, U+2191, U+2193, U+2212, U+2215, U+FEFF, U+FFFD`) with all layout features kept, and written as WOFF2 (`brotli` 1.2.0). The subset contains 250 codepoints, including every character of the glyph probe.
- License: SIL Open Font License 1.1 (`License: SIL Open Font License 1.1`, Google Fonts `METADATA.pb` `license: "OFL"`); copyright notice is `Copyright 2022 The Gelasio Project Authors (https://github.com/SorkinType/Gelasio)`. The Google Fonts `OFL.txt` has the same license body as `OFL-1.1.txt` with different line wrapping (SHA-256 of that file `b393cb01867c919b44381512120dc3e4c954c7b47e2035c405f3a324799a4d29`).

## Acceptance contract

`src/fonts.ts` must remain the sole source of the local `@font-face` rules.
Every family named by the 2RP theme or the Astrid video brand must have a local
face in that list. The loader waits for `document.fonts.load` and verifies
`document.fonts.check` for each family/style/weight; any missing face cancels
the Remotion render. Faces with `style: "italic"` load with the `italic` font
keyword. The focused test also rejects hosted font imports, URL fetches, and
missing glyph-proof coverage.

Every shipped face must cover all 95 printable ASCII characters (U+0020-U+007E).
The loader's `document.fonts.check` probe cannot detect a missing glyph (the
browser falls through to the next family silently), so coverage is pinned in
`tests/fixtures/remotion-font-coverage.json`: per face, the SHA-256, the cmap
size and the list of missing printable ASCII. The test fails on any binary
change until the record is regenerated, on any unrecorded `.woff2`, and on any
missing ASCII. When `fontTools` and `brotli` are importable, the test also
recomputes the cmap from the binaries. To regenerate after a change:

```python
from fontTools.ttLib import TTFont
import hashlib, json, pathlib
fonts = pathlib.Path("remotion/public/fonts")
out = {"description": "Printable ASCII (U+0020-U+007E) coverage per shipped Remotion font face, generated with fontTools from the committed woff2 files.", "ascii_range": "U+0020-U+007E", "files": {}}
for path in sorted(fonts.glob("*.woff2")):
    cmap = TTFont(path).getBestCmap()
    missing = [chr(c) for c in range(0x20, 0x7F) if c not in cmap]
    out["files"][path.name] = {"sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                               "cmap_codepoints": len(cmap),
                               "ascii_covered": 95 - len(missing), "ascii_missing": missing}
with open("tests/fixtures/remotion-font-coverage.json", "w", encoding="utf-8") as fh:
    json.dump(out, fh, indent=2, ensure_ascii=False)
    fh.write("\n")
```
