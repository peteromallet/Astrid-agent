"""Runtime entrypoint for runpod.pull action.

Thin wrapper — all logic lives in ``astrid.packs.runpod.shared.common``.
"""

from __future__ import annotations

from astrid.core.pack.entrypoint import guard_canonical_entrypoint

guard_canonical_entrypoint('runpod.pull')

# Re-export everything so that ``from astrid.packs.runpod.actions.pull.run import X``
# continues to work for any callers.
from astrid.packs.runpod.shared.common import *  # noqa: F401,F403,E402
from astrid.packs.runpod.shared.common import main  # noqa: F401,E402

if __name__ == "__main__":
    raise SystemExit(main())
