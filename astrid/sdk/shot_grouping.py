"""Retired legacy shot grouping boundary.

Shot grouping used to read and rewrite mutable timeline documents.  Canonical
parent compositions are now immutable current-head data, so the old operation
is intentionally retained only as a typed 410 for callers that have not yet
removed the symbol.  It performs no Runtime reads or writes.
"""

from __future__ import annotations

import uuid

from .contracts import DomainResult, ErrorObject


def group_timeline_clips(*, idempotency_key=None, **_kwargs):
    key = idempotency_key or uuid.uuid4().hex
    return DomainResult.failure(
        ErrorObject(
            "retired_route",
            "legacy shot grouping is retired; publish a canonical parent composition",
            {
                "status": 410,
                "operation": "shots.group",
                "replacement": "publish_parent_composition",
            },
        ),
        idempotency_key=key,
    )
