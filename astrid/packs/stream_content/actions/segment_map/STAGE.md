# stream_content.segment_map

Use this action to turn a long stream recording into a complete labeled
timeline.

```python
import astrid.sdk as sdk
result = sdk.invoke(
    "stream_content.segment_map",
        kind="action", project="demo",
    inputs={
        "video": "source.mp4",
        "transcript": "transcript.json",
        "scenes": "scenes.json",
    },
)
```

It writes `{out}/segment_map.json` (or the explicit `--out` file) with
`version`, `source`, `duration`, and gapless `segments`.
