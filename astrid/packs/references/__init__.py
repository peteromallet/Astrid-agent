"""Runtime-backed references product mount.

The workspace runtime owns reference persistence and migrations. Its nested CLI
adapter is core-owned at ``astrid.core.cli.domain_references``; this mount
directory retains only separately owned guidance and never hosts a repository,
schema, or local writer.
"""

from __future__ import annotations

# No local persistence symbols are exposed from this package.
