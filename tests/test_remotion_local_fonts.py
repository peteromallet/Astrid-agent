"""Focused proof for the clean Remotion local-font port.

This test intentionally stays source-level so it can run without starting a
browser or downloading dependencies. The real FontFaceSet/render proof is a
separate Remotion acceptance gate.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pytest


ROOT = Path(__file__).parents[1]
REMOTION = ROOT / "remotion"
FONTS = REMOTION / "public" / "fonts"
COVERAGE_RECORD = ROOT / "tests" / "fixtures" / "remotion-font-coverage.json"
PRINTABLE_ASCII = "".join(chr(code) for code in range(0x20, 0x7F))

EXPECTED = {
    "Sixtyfour.woff2": (7_608, "0c35bb8333a12a822333f10fc4fd22e607b80a254b8b31faa8eed1cd4badc24e"),
    "Inter-Bold.woff2": (41_296, "aeaf0d0fa3262f4df2da7acf660544cb15aa24dcb63e574daed6ba7e92333a01"),
    "Inter-Regular.woff2": (40_128, "5f04e9f02a74eab4ec87eda5b79b5be1c0567c868688b0a08564b2fe5c6da928"),
    "JetBrainsMono-Bold.woff2": (36_856, "a1475eef9934d6b3a6d15494f312ee1056b7556d59b1e22cb4e0c618338cd170"),
    "JetBrainsMono-Regular.woff2": (35_416, "2955337d809a64d58c56730b8344315198ec76214e02710774dd72d6a5de7b87"),
    "DepartureMono-Regular.woff2": (22_496, "5b4fed1daa90708aa9c6ee1190abca9dc22164a1c1def0020386e46b61038cfb"),
    "Gelasio-Regular.woff2": (20_284, "698c2aa61ec1960371b6ed32e6120f028fa614613256f4b9e3f5d10ceb013eb5"),
    "Gelasio-Italic.woff2": (22_272, "2df3d4a4d0cefc6440710666fa425627e54cb73707224bec044bca8e65940222"),
    "Gelasio-Bold.woff2": (20_452, "2fdaa469d51ef618d648e27819f10a1df08721ed2331614c3123850162cc147a"),
    "Gelasio-BoldItalic.woff2": (22_476, "e5b0682ff74c6798100255daafaf5d5496c5ddec596aec663fdd0afb3470b609"),
}


def test_shipped_font_bytes_match_reviewed_manifest() -> None:
    for name, (size, digest) in EXPECTED.items():
        path = FONTS / name
        assert path.is_file(), name
        assert path.stat().st_size == size
        assert hashlib.sha256(path.read_bytes()).hexdigest() == digest


def _shipped_font_files() -> list[Path]:
    return sorted(FONTS.glob("*.woff2"))


def test_every_vendored_face_covers_all_printable_ascii() -> None:
    # A face missing printable ASCII renders those characters in the next
    # family of the stack without any error, and the loader's probe check cannot
    # see that. This record makes the coverage a reviewed, byte-pinned fact.
    record = json.loads(COVERAGE_RECORD.read_text(encoding="utf-8"))
    assert record["ascii_range"] == "U+0020-U+007E"
    recorded = record["files"]
    assert set(recorded) == {path.name for path in _shipped_font_files()}, (
        "every shipped woff2 needs a coverage record; regenerate tests/fixtures/remotion-font-coverage.json"
    )
    assert len(PRINTABLE_ASCII) == 95
    for name, entry in recorded.items():
        assert entry["ascii_missing"] == [], f"{name} lacks printable ASCII: {entry['ascii_missing']}"
        assert entry["ascii_covered"] == 95, name
        digest = hashlib.sha256((FONTS / name).read_bytes()).hexdigest()
        assert entry["sha256"] == digest, f"{name} changed; regenerate the coverage record"


def test_recorded_coverage_matches_binaries_when_fonttools_is_available() -> None:
    ttlib = pytest.importorskip("fontTools.ttLib")
    pytest.importorskip("brotli")
    recorded = json.loads(COVERAGE_RECORD.read_text(encoding="utf-8"))["files"]
    for path in _shipped_font_files():
        cmap = ttlib.TTFont(str(path)).getBestCmap()
        assert len(cmap) == recorded[path.name]["cmap_codepoints"], path.name
        missing = [char for char in PRINTABLE_ASCII if ord(char) not in cmap]
        assert missing == [], f"{path.name} lacks printable ASCII: {missing}"


def test_loader_is_local_typed_and_has_one_face_per_family_style_weight() -> None:
    source = (REMOTION / "src" / "fonts.ts").read_text(encoding="utf-8")
    assert "@remotion/google-fonts" not in source
    assert "window.location" not in source
    assert "fonts.googleapis.com" not in source
    assert "fonts.gstatic.com" not in source
    assert "fetch(" not in source
    assert "XMLHttpRequest" not in source
    assert "font-display: block" in source
    assert 'document.fonts.load' in source
    assert 'document.fonts.check' in source
    assert 'LOCAL_FONT_GLYPH_PROBE = "Astrid 2RP — Aa 0123"' in source
    assert "FontProvider" in (REMOTION / "src" / "Root.tsx").read_text(encoding="utf-8")

    pairs = re.findall(
        r"family:\s*[\"']([^\"']+)[\"'],\s*file:\s*[\"']([^\"']+)[\"'],\s*weight:\s*(\d+)"
        r"(?:,\s*style:\s*[\"'](\w+)[\"'])?",
        source,
    )
    assert len(pairs) == 10
    families = [family for family, _, _, _ in pairs]
    assert families.count("Sixtyfour") == 1
    assert families.count("Inter") == 2
    assert families.count("JetBrains Mono") == 2
    assert families.count("Departure Mono") == 1
    assert families.count("Gelasio") == 4
    faces = {(family, style or "normal", weight) for family, _, weight, style in pairs}
    assert len(faces) == len(pairs)
    assert sorted(style for *_, style in pairs if style) == ["italic", "italic"]
    for _, file, _, _ in pairs:
        assert (REMOTION / "public" / file).is_file(), file


def test_provenance_and_license_cover_every_theme_family() -> None:
    provenance = (FONTS / "FONT_PROVENANCE.md").read_text(encoding="utf-8")
    license_text = (FONTS / "OFL-1.1.txt").read_text(encoding="utf-8")
    for family in ("Sixtyfour", "Inter", "JetBrains Mono", "Departure Mono", "Gelasio"):
        assert f"## {family}" in provenance
        assert "Upstream revision:" in provenance
        assert "Local SHA-256:" in provenance
        assert "License: SIL Open Font License 1.1" in provenance
    assert "SIL OPEN FONT LICENSE Version 1.1" in license_text
    assert "Inter Project Authors" in license_text
    assert "JetBrains Mono Project Authors" in license_text
    assert "Sixtyfour Project Authors" in license_text
    assert "Helena Zhang" in license_text
    assert "Gelasio Project Authors" in license_text


def test_committed_receipt_records_real_network_denial() -> None:
    probe = (ROOT / "tests" / "fixtures" / "remotion-local-font-probe.json").read_text(
        encoding="utf-8"
    )
    receipt = (FONTS / "LOCAL_FONT_NETWORK_DENY.log").read_text(encoding="utf-8")
    for family in ("Sixtyfour", "Inter", "JetBrains Mono"):
        assert family in probe
    assert "Astrid 2RP — Aa 0123" in probe
    assert "fontFamily" in probe
    assert "deny network-outbound" in receipt
    assert "allow network-outbound (remote ip \"localhost:*\")" in receipt
    assert "exit=0" in receipt
    assert "hosted-font-request-lines=0" in receipt
    assert "font-loader-completions=5" in receipt
