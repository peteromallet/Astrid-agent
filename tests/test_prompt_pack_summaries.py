from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from astrid.core.pack import discovery as pack_discovery
from astrid.skills import discovery as skill_discovery


def test_visible_pack_summaries_are_compact_and_use_canonical_skills(monkeypatch) -> None:
    def pack(pack_id: str, *, visibility: str = "visible", status: str = "active", purpose: str = ""):
        return SimpleNamespace(
            id=pack_id,
            name=pack_id.title(),
            visibility=visibility,
            status=status,
            description=f"Description for {pack_id}",
            agent={"purpose": purpose},
        )

    packs = [
        SimpleNamespace(pack=pack("zeta")),
        SimpleNamespace(pack=pack("alpha", purpose="  Canonical   manifest purpose.  ")),
        SimpleNamespace(pack=pack("hidden", visibility="hidden")),
        SimpleNamespace(pack=pack("retired", status="deprecated")),
        SimpleNamespace(pack=pack("_core")),
    ]
    descriptor = SimpleNamespace(
        pack_id="zeta",
        short_description="A concise skill description.",
        skill_md=Path("/private/absolute/path/SKILL.md"),
    )
    monkeypatch.setattr(pack_discovery, "discover_pack_metadata", lambda: packs)
    monkeypatch.setattr(skill_discovery, "list_skills", lambda: [descriptor])

    summaries = skill_discovery.list_visible_pack_summaries()

    assert summaries == [
        {
            "pack_id": "alpha",
            "name": "Alpha",
            "description": "Canonical manifest purpose.",
        },
        {
            "pack_id": "zeta",
            "name": "Zeta",
            "description": "A concise skill description.",
            "skill_id": "astrid-zeta",
        },
    ]
