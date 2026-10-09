# Astrid local font provenance

These are the only font binaries used by the Remotion render. They are bundled
so an offline render never asks Google Fonts, a CDN, or another network host
for CSS or font bytes. `OFL-1.1.txt` is shipped beside these binaries and
contains the complete license text and copyright notices.

The upstream Google Fonts repository revision used for the family and license
records of Inter, JetBrains Mono and Gelasio below is:

`ade3d1533e06b2b1462ffcde8e08b129627ca360`

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
- Local files: `Inter-Regular.woff2` (27,380 bytes), `Inter-Bold.woff2` (19,980 bytes).
- Local SHA-256: `39689184132e9fba8fb1066f429125d14445352a566f47f4edcae7c3c90e486d` (regular); `47d42151dff6d13f1c2b9a1f278290f625593c1f01c89612ee4ae7f063167f7a` (bold).
- Upstream family: [rsms/inter](https://github.com/rsms/inter); curated license/source record is [Google Fonts `ofl/inter`](https://github.com/google/fonts/tree/ade3d1533e06b2b1462ffcde8e08b129627ca360/ofl/inter).
- Upstream revision: `ade3d1533e06b2b1462ffcde8e08b129627ca360`.
- Upstream Git source blob: `047c92f6e2212473dc436020afed689527076d44` (`Inter[opsz,wght].ttf`).
- License: SIL Open Font License 1.1; copyright notice is `Copyright 2020 The Inter Project Authors (https://github.com/rsms/inter)`.

## JetBrains Mono

- Theme role: 2RP monospace text.
- Local files: `JetBrainsMono-Regular.woff2` (2,180 bytes), `JetBrainsMono-Bold.woff2` (13,352 bytes).
- Local SHA-256: `1b53536573e8f2e886848fee9a53c278a8f92b02ac794a83437ad9277120df47` (regular); `8df3ca627bd8e1cb0e5414f7429fe7a2cf82732b0fc43f2d05bc2c471b64fcfc` (bold).
- Upstream family: [JetBrains/JetBrainsMono](https://github.com/JetBrains/JetBrainsMono); curated license/source record is [Google Fonts `ofl/jetbrainsmono`](https://github.com/google/fonts/tree/ade3d1533e06b2b1462ffcde8e08b129627ca360/ofl/jetbrainsmono).
- Upstream revision: `ade3d1533e06b2b1462ffcde8e08b129627ca360`.
- Upstream Git source blob: `aa310be8b717fe3774f9444dd89d5f4101cc6d10` (`JetBrainsMono[wght].ttf`).
- License: SIL Open Font License 1.1; copyright notice is `Copyright 2020 The JetBrains Mono Project Authors (https://github.com/JetBrains/JetBrainsMono)`.

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
