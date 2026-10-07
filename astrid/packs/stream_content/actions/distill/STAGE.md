# stream_content.distill

Use this orchestrator for long recordings from events, streams, panels, demos,
or webinars that need to become reviewable publishing material.

```python
import astrid.sdk as sdk
result = sdk.invoke(
    "stream_content.distill",
    kind="action",
    project="demo",
    inputs={
        "video": "sources/event.mp4",
        "transcript": "runs/transcript.json",
        "brief": "brief.md",
        "dry_run": True,
        "no_scenes": True,
    },
)
```

Outputs:

- `segment_map.json`: gapless holding/dead/content/screening timeline.
- `segments/`: extracted `content` and `screening` files plus `segments.json`.
- `candidates.json`: scored candidate clips.
- `review.html`: static self-contained review page.

The invocation and run status are owned by the runtime kernel. This
orchestrator does not write a local `run.json` ledger.

Set the action input `inputs={"dry_run": True}` to run the action and emit
`plan.json` without executing its distillation steps. This differs from the
SDK preview keyword `sdk.invoke(..., dry_run=True)`, which previews invocation
and does not run the action or emit `plan.json`. Set the action input
`inputs={"no_scenes": True}` to skip scene detection.
