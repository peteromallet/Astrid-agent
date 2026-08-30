"""Shipped Astrid pack manifests and authoring content.

Pack discovery and schema validation remain available to SDK pack authors.
Runtime ownership and persistence are supplied by the neutral workspace
daemon; this package intentionally has no application or storage composer.
"""

from astrid.core.schema_packs.standard import (
    STANDARD_SCHEMA_PACKS,
    build_standard_registry,
)

__all__ = ["STANDARD_SCHEMA_PACKS", "build_standard_registry"]
