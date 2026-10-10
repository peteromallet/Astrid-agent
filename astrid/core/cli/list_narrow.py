"""Narrow a project-scoped run/task list: one capability, the newest N (``runs list``, ``tasks list``)."""
from __future__ import annotations

from typing import Any, Mapping


def _capability(row: Mapping[str, Any]) -> str:
    spec = row.get("spec") if isinstance(row.get("spec"), Mapping) else {}
    inner = spec.get("spec") if isinstance(spec.get("spec"), Mapping) else {}
    return str(row.get("capability") or row.get("capability_id") or inner.get("capability_id") or "")


def narrow(result: Any, *, capability: str | None, limit: int | None, as_json: bool) -> Any:
    """The same result with only matching rows, newest first, at most ``limit`` (a footer says how many)."""
    if not getattr(result, "ok", False):
        return result
    data = result.data
    rows, cursor = (data[0], data[1]) if isinstance(data, list) and len(data) == 2 and isinstance(data[0], list) else (data, None)
    if not isinstance(rows, list):
        return result
    if capability:
        rows = [r for r in rows if isinstance(r, Mapping) and (_capability(r) == capability or _capability(r).startswith(capability))]
    rows = sorted(rows, key=lambda r: str(r.get("created_at") or "") if isinstance(r, Mapping) else "", reverse=True)
    total = len(rows)
    if limit is None:
        limit = 0 if as_json else 50
    if limit and limit > 0:
        rows = rows[:limit]
    try:
        from dataclasses import replace

        narrowed = replace(result, data=[rows, cursor] if cursor is not None else rows)
    except TypeError:
        return result
    if not as_json and len(rows) < total:
        print(f"showing the newest {len(rows)} of {total} (--limit N, --limit 0 for all, --capability ID to filter)")
    return narrowed


def all_pages(reader: Any, project: str, *, max_pages: int = 60) -> Any:
    """Every page of a cursor-paged list as one result (bounded)."""
    first = reader(project, limit=50)
    if not getattr(first, "ok", False):
        return first
    rows: list[Any] = []
    result, cursor = first, None
    for _ in range(max_pages):
        data = result.data
        page, cursor = (data[0], data[1]) if isinstance(data, list) and len(data) == 2 and isinstance(data[0], list) else (data, None)
        rows += list(page or [])
        if not cursor:
            break
        result = reader(project, cursor=cursor, limit=50)
        if not getattr(result, "ok", False):
            break
    from dataclasses import replace

    return replace(first, data=rows)
