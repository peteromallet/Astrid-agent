"""Caller-authorized launcher for complete draft narration synchronization."""
from __future__ import annotations

import json
from pathlib import Path

from astrid.core.pack.entrypoint import guard_canonical_entrypoint

guard_canonical_entrypoint("video_editing.sync_draft_voiceover")

from astrid.packs.video_editing.draft_voiceover import sync_draft_voiceover


def run_orchestrator(request, orchestrator):
    if request.dry_run:
        return {"returncode": None, "dry_run": True, "orchestrator_id": orchestrator.id}
    try:
        # Public parent orchestrators are local launchers. Only their executor
        # children are admitted to the Runtime worker. Retain the ordinary
        # caller credential boundary for canonical timeline publication.
        from astrid.sdk import AstridClient
        inputs = request.inputs
        with AstridClient.open_from_launcher() as client:
            result = sync_draft_voiceover(client, request.project, inputs["timeline_ref"],
                settings=json.loads(inputs.get("settings", "{}")),
                occurrence_ids=json.loads(inputs.get("occurrence_ids", "null")),
                adopt_clip_ids=json.loads(inputs.get("adopt_clip_ids", "[]")),
                timing_policy=inputs["timing_policy"],
                padding_seconds=float(inputs.get("padding_seconds", 0.15)),
                plan_only=inputs.get("plan_only", False))
        out = Path(request.out); out.mkdir(parents=True, exist_ok=True)
        (out / "report.json").write_text(json.dumps(result, indent=2) + "\n")
        (out / "receipt.json").write_text(json.dumps(result.get("receipt", {}), indent=2) + "\n")
        return {"returncode": 0, "outputs": {"report": str(out / "report.json"),
                                              "receipt": str(out / "receipt.json")}}
    except Exception as exc:  # noqa: BLE001 - public launcher translates workflow failures.
        return {"returncode": 1, "errors": [str(exc)]}
