# Draft voiceover sync

Invoke the complete workflow through the public SDK:

```python
import astrid.sdk as sdk
result = sdk.invoke(
    "video_editing.sync_draft_voiceover", kind="orchestrator",
    project="my-project", out="/tmp/draft-voiceover-review",
    inputs={"timeline_ref": "my-timeline", "timing_policy": "preserve",
            "plan_only": True},
)
```

Inspect `report.json` for changed, reused, empty and protected passages. Run
with `plan_only=False` to generate changed draft audio and publish one validated
canonical authoring revision. Choose `timing_policy="preserve"` or `"ripple"`
explicitly. Legacy audio needs explicit JSON `adopt_clip_ids`; narrow with JSON
`occurrence_ids` if clip IDs are ambiguous. Adoption grants replacement and
never certifies the old words. Locked, final and recorded narration is protected.

Parent orchestrators run as caller-authorized local launchers; their child
`generation.generate_speech` executor tasks use the normal connected Runtime.
The pack Python function `sync_draft_voiceover(client, project, timeline, ...)`
accepts an already-bound caller client. The pure `plan_draft_voiceover` and
`prepare_draft_voiceover` functions inspect and prepare a detached candidate;
preparation itself never publishes. A source-head conflict requires restarting
from the new head.

No video render or background trigger is involved. Synthesis failures preserve
published timeline state. Completed child generation outputs can remain in
managed run history after a later generation or CAS failure.
