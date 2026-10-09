# capture.web_page

## Purpose

Capture a real https web page as a PNG and a receipt that records what was captured,
from where, when, at what viewport, with what HTTP status and user agent. Use it when a
film or doc must show a real page, not a mock.

## Inputs

- `url` (string, required): an https URL. http is refused except loopback in tests; credentials are refused.
- `viewport` (default `1600x1000`): CSS pixels.
- `device_scale` (default `2`): 1 to 3. The PNG is `viewport x device_scale`.
- `full_page` (default `false`): capture the full scrollable page.
- `wait_ms` (default `1500`): settle time after the load event.
- `clip` (optional json `{x, y, w, h}`): CSS pixels of the viewport. Wins over `full_page`.
- `dark_mode` (default `false`): emulate `prefers-color-scheme: dark`.

## Outputs

- `screenshot`: PNG, managed image artifact.
- `receipt`: JSON `{url, final_url, title, captured_at, viewport, http_status, user_agent, device_scale, full_page, clip, dark_mode, wait_ms, image}`.
  `captured_at` is UTC ISO 8601. `http_status` is the status of the final document response (null if none observed).
  `user_agent` is the browser's own string (it says HeadlessChrome), so the receipt is honest about how the page was seen.

## Browser

Headless Chromium, driven over `--remote-debugging-pipe` with the Python standard library
(no Playwright, no websocket package). The binary is, in order: `ASTRID_CAPTURE_CHROME`, then
Remotion's managed `chrome-headless-shell` under `remotion/node_modules/.remotion/`. Run
`npx remotion browser ensure` in `remotion/` if it is missing.

## Network

Declared as `network_policy.dynamic_url_inputs: [url]` with `broker` host-managed: the host
admits only the `https://<host>:443` route of the requested URL, through its broker proxy
(`ASTRID_BROKER_PROXY`, passed to Chromium as `--proxy-server`). `allow_redirects: false`, so a
redirect to another host is refused by the broker and shows as a navigation error. The
Python-side hook cannot see Chromium's own sockets, so the broker is the enforcement point.

## Canonical command

```python
import astrid.sdk as sdk
from astrid.sdk import AstridClient

with AstridClient.open_from_launcher() as client:
    result = sdk.invoke(
        "capture.web_page",
        kind="executor",
        project="almost-ready",
        client=client,
        wait=True,
        inputs={"url": "https://github.com/peteromallet/dataclaw", "viewport": "1600x1000", "device_scale": 2},
    )
    png = result.output("screenshot")
    receipt = result.output("receipt")
```

## Failure modes

- Login or bot wall: the page is captured as served (for example a sign-in page). The receipt's
  `title` and `final_url` show it; do not work around it.
- Navigation error (DNS, refused, blocked by the broker): the run fails with the browser's error text.
- No Chromium: the run fails with a recovery command.

## Dependencies

- Headless Chromium (Remotion's managed copy or `ASTRID_CAPTURE_CHROME`). Standard library otherwise.
