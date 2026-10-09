# Capture — Agent Guide

## When to Use This Pack

Use it when a film, doc or deck must show a real web page (a GitHub repo, a Hugging Face
search, a site home) as an image, with proof of what was captured and when. The receipt is the
provenance: URL, final URL after redirects, title, UTC capture time, viewport, HTTP status and the
browser user agent.

Do not use it to mock up a UI (use a design or pixel pack), to capture pages behind a login or a
bot wall (report the block instead), or to download files.

## Entrypoint

`capture.web_page` (see `executors/web_page/STAGE.md` for the full contract).

```python
import astrid.sdk as sdk
from astrid.sdk import AstridClient

with AstridClient.open_from_launcher() as client:  # sdk.invoke needs an explicit client
    result = sdk.invoke("capture.web_page", kind="executor", project="almost-ready", client=client, wait=True,
                        inputs={"url": "https://astrid.haus", "full_page": False})
    png = result.output("screenshot")
    receipt = result.output("receipt")
```

## Network and browser

Only the requested https host is admitted (through the host broker). The browser is Remotion's
managed headless Chromium, or the path in `ASTRID_CAPTURE_CHROME`. No Playwright, no new package.
