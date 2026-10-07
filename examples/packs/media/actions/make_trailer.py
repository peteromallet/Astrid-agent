"""Public composed action for the Media teaching pack."""

from __future__ import annotations

import astrid


def run(source: str, brief: str | None = None) -> dict[str, object]:
    """Call the public ingestion action and produce a trailer plan."""
    child = astrid.invoke(
        "media.ingest_assets",
        kind="action",
        inputs={"source": source},
        wait=True,
    )
    if not child.ok:
        raise RuntimeError(f"media.ingest_assets failed: {child.error or 'unknown error'}")
    raw = child.raw_result if isinstance(child.raw_result, dict) else {}
    payload = raw.get("payload", {})
    assets = payload.get("action_result", {}) if isinstance(payload, dict) else {}
    if not isinstance(assets, dict):
        raise RuntimeError("media.ingest_assets returned an invalid action result")

    lines = ["# Trailer Build Plan"]
    if brief:
        lines.append(f"Brief: {brief.strip()[:200]}")
    lines.append(f"Assets: {assets.get('file_count', 0)}")
    lines.append("Scenes: [project-title-card, ...]")
    return {"asset_manifest": assets, "trailer_manifest": "\n".join(lines) + "\n"}
