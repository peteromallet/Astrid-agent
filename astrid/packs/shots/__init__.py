"""Runtime-backed shots product mount.

The workspace runtime owns shot persistence and migrations. This mount retains
Runtime support fixtures used by the boot-profile and generation bridges. Its
nested CLI adapter is core-owned at ``astrid.core.cli.domain_shots``; this
directory never hosts a repository, schema, or local writer.
"""

from __future__ import annotations

# No local persistence symbols are exposed from this package.
