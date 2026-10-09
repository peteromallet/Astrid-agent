#!/usr/bin/env python3
"""Release one exact, settled prepared RunPod worker and preserve its volume."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from astrid.packs.runpod.prepared_task import release_prepared_worker
from astrid.sdk import AstridClient


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selection", required=True, help="prepared-worker selection JSON")
    parser.add_argument("--task-id", required=True, help="settled Runtime vibecomfy.run task ID")
    parser.add_argument(
        "--resume", action="store_true",
        help="resume the exact P1 release already marked release_pending/cleanup_pending",
    )
    args = parser.parse_args()
    with AstridClient.open_from_launcher(start_pack_host=False) as client:
        result = release_prepared_worker(
            client, args.selection, args.task_id, resume=args.resume,
        )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
