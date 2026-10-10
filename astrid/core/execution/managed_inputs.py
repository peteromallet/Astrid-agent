"""Validation of declared file ports at managed execution boundaries."""

from __future__ import annotations

import re
from typing import Any, Mapping


def managed_file_digest(value: Any, name: str) -> str:
    """Return a canonical digest; never interpret ordinary paths or nested JSON."""
    candidates = [value]
    if isinstance(value, Mapping):
        candidates = [value[key] for key in ("digest", "object_id") if value.get(key) is not None]
    digests = set()
    for candidate in candidates:
        if not isinstance(candidate, str) or not re.fullmatch(r"(?:sha256:)?[0-9a-f]{64}", candidate):
            break
        digests.add("sha256:" + candidate.removeprefix("sha256:"))
    else:
        if len(digests) == 1:
            return digests.pop()
    raise ValueError(
        f"file input {name!r} needs a media handle, got {value!r:.80}: pass result.output(\"<port>\"), "
        "\"run:<run_id>/<port>#n\", \"ref:<reference name>\" or \"sha256:<digest>\" (a list of them for a "
        "repeatable input). Local paths don't reach executor workers: import a file first with "
        "`python -m astrid media import <file> --project <slug>` (SDK: client.media.import_file) "
        "and pass its sha256 digest."
    )
