"""Composed public action for the Minimal teaching pack."""

from __future__ import annotations

import astrid


def run(source: str = ".", brief: str | None = None) -> dict[str, object]:
    """Call the pack's declared ingestion action and produce a small plan."""
    child = astrid.invoke(
        "minimal.ingest_assets",
        kind="action",
        inputs={"source": source},
        wait=True,
    )
    if not child.ok:
        raise RuntimeError(f"minimal.ingest_assets failed: {child.error or 'unknown error'}")
    raw = child.raw_result if isinstance(child.raw_result, dict) else {}
    payload = raw.get("payload", {})
    assets = payload.get("action_result", {}) if isinstance(payload, dict) else {}
    if not isinstance(assets, dict):
        raise RuntimeError("minimal.ingest_assets returned an invalid action result")

    lines = ["Trailer build plan"]
    if brief:
        lines.append(f"Brief: {brief.strip()[:200]}")
    lines.append(f"Assets: {assets.get('item_count', 0)}")
    return {"asset_manifest": assets, "trailer_manifest": "\n".join(lines) + "\n"}
