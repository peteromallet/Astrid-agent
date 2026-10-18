"""The promote-time sweep reports and signals only Astrid's orphaned render processes.

Every test uses a fake ``ps`` listing and a recording kill function. No process
is signalled for real, so this file cannot touch a live browser.
"""

from __future__ import annotations

import os
import signal

from astrid.core.gateway.dev_operator import (
    find_orphan_render_processes,
    reap_orphan_render_processes,
)

SERVE = "/work/astrid-local/Astrid-serve"
DEV = "/work/astrid-local/Astrid"
CHECKOUTS = [SERVE, DEV]
CHROME = (
    f"{SERVE}/remotion/node_modules/.remotion/chrome-headless-shell/mac-arm64/"
    "chrome-headless-shell-mac-arm64/chrome-headless-shell about:blank --headless=old"
)
BUNDLE = (
    "/usr/local/bin/node " + f"{SERVE}/remotion/node_modules/@remotion/cli/remotion-cli.js "
    "bundle src/index.ts --out-dir /var/folders/x/T/astrid-frame-workers/bundle-"
    "0123456789abcdef0123.tmp-4242-abcdef012345 --bundle-cache=false"
)
LISTING = "\n".join([
    "  100     1 " + CHROME,                                   # orphan Chromium from serve: match
    "  101   100 " + CHROME.replace(" about", " x about"),     # still attached to a parent: skip
    "  102     1 /Users/me/Library/Application Support/Astrid Runtime/chrome-headless-shell about:blank",
    "  103     1 " + f"{SERVE}-old/remotion/node_modules/.remotion/chrome-headless-shell/x/chrome-headless-shell",
    "  104     1 " + BUNDLE,                                     # orphan bundle from serve: match
    "  105     1 /usr/local/bin/node " + f"{SERVE}/remotion/src/astrid-frame-worker.mjs --server /tmp/astrid-rfo/owner-x.sock",
    "  106     1 /usr/local/bin/node " + f"{SERVE}/remotion/x.mjs --out-dir /Users/me/Documents/bundle-0123456789abcdef.tmp-1",
    "  107     1 /bin/sleep 600",
    "  108     1 " + f"{DEV}/remotion/node_modules/.remotion/chrome-headless-shell/x/chrome-headless-shell",
    "  109     1 " + os.path.expanduser("~/Library/Caches/astrid-remotion-browser/9.9.9/chrome-headless-shell/mac-arm64/x/chrome-headless-shell") + " about:blank",
    "  110   100 " + os.path.expanduser("~/Library/Caches/astrid-remotion-browser/9.9.9/chrome-headless-shell/mac-arm64/x/chrome-headless-shell") + " about:blank",
])


def test_finder_reports_only_orphaned_astrid_chromium_and_bundles() -> None:
    found = find_orphan_render_processes(LISTING, checkouts=CHECKOUTS)
    assert [pid for pid, _ in found] == [100, 104, 108, 109]


def test_reaper_signals_exact_pids_with_sigkill_and_nothing_else() -> None:
    killed: list[tuple[int, int]] = []
    reaped = reap_orphan_render_processes(
        checkouts=CHECKOUTS,
        list_fn=lambda: LISTING,
        kill_fn=lambda pid, sig: killed.append((pid, sig)),
    )
    assert killed == [(100, signal.SIGKILL), (104, signal.SIGKILL), (108, signal.SIGKILL), (109, signal.SIGKILL)]
    assert [row["pid"] for row in reaped] == [100, 104, 108, 109]


def test_reaper_skips_a_pid_whose_command_changed_before_the_signal() -> None:
    # The first read finds the candidates; from then on pid 100 belongs to another process.
    reads = [LISTING]

    def list_fn() -> str:
        if reads:
            return reads.pop()
        return LISTING.replace(CHROME, "/usr/bin/reused-pid-process")

    killed: list[int] = []
    reaped = reap_orphan_render_processes(
        checkouts=CHECKOUTS,
        list_fn=list_fn,
        kill_fn=lambda pid, _sig: killed.append(pid),
    )
    assert 100 not in killed
    assert killed == [104, 108, 109]
    assert all(row["pid"] != 100 for row in reaped)


def test_empty_listing_signals_nothing() -> None:
    killed: list[int] = []
    assert reap_orphan_render_processes(checkouts=CHECKOUTS, list_fn=lambda: "",
                                        kill_fn=lambda pid, _sig: killed.append(pid)) == []
    assert killed == []
