"""Adapt admitted public actions to the existing execution contracts.

Discovery reads declarations and schemas only; Python entrypoints are resolved by
an execution host after admission. Simple and composed Python actions have the
same declaration and dispatch shape.
"""
from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from .exceptions import CapabilityValidationError


# Retain the public helper at its existing import path. Core owns normalization.
from astrid.core.execution.executor.actions import action_executor_definition


def _definition_parts(definition: Any) -> tuple[str, Mapping[str, Any]]:
    if isinstance(definition, Mapping):
        return str(definition.get("id", "")), definition.get("metadata", {})
    return str(definition.id), definition.metadata


def _validate_action_schema(definition: Any, slot: str, value: Any) -> None:
    """Validate one value contract using the admitted definition, offline."""
    identity, metadata = _definition_parts(definition)
    schema_key = f"action_{slot}_schema"
    if schema_key not in metadata:
        return
    import jsonschema
    from referencing import Registry, Resource
    from referencing.jsonschema import DRAFT202012
    from astrid.core.pack.manifest import load_manifest_payload

    root = Path(metadata["pack_root"]).resolve()

    def retrieve(uri: str) -> Resource:
        parsed = urlparse(uri)
        if parsed.scheme != "file" or parsed.netloc:
            raise CapabilityValidationError("action schemas must use local pack references")
        path = Path(unquote(parsed.path)).resolve()
        if not path.is_relative_to(root):
            raise CapabilityValidationError("action schema reference escapes pack root")
        return Resource.from_contents(load_manifest_payload(path, manifest_kind="JSON Schema"),
                                      default_specification=DRAFT202012)

    try:
        schema = metadata[schema_key]
        base = metadata[f"action_{slot}_schema_base"]
        resource = Resource.from_contents(schema, default_specification=DRAFT202012)
        registry = Registry(retrieve=retrieve).with_resource(base, resource)
        validator_type = jsonschema.validators.validator_for(schema)
        validator_type.check_schema(schema)
        validator = validator_type(schema, registry=registry, _resolver=registry.resolver(base))
        validator.validate(value)
    except jsonschema.ValidationError as exc:
        location = ".".join(str(part) for part in exc.absolute_path) or slot
        raise CapabilityValidationError(f"action {identity!r} {location}: {exc.message}") from exc
    except Exception as exc:
        if isinstance(exc, CapabilityValidationError):
            raise
        label = "input" if slot == "inputs" else "output"
        raise CapabilityValidationError(f"action {identity!r} {label} schema could not be resolved: {exc}") from exc


def validate_action_inputs_definition(definition: Any, inputs: Mapping[str, Any]) -> None:
    """Validate an effective input mapping against the exact admitted schema.

    Accepts an ExecutorDefinition or its normalized mapping. The caller binds
    explicit port defaults and enforces existing Port-list checks; this helper
    does not inject inputs or invent recursive JSON Schema defaults.
    """
    _validate_action_schema(definition, "inputs", dict(inputs))


def validate_action_inputs(capability: Any, inputs: Mapping[str, Any]) -> None:
    """Keep the Capability-facing SDK seam on the shared definition validator."""
    validate_action_inputs_definition(capability.definition, inputs)


def validate_action_output_definition(definition: Any, result: Any) -> None:
    """Require a strict JSON return and enforce its admitted value schema.

    Returns are never coerced or interpreted as host status or file outputs.
    The caller preserves the validated value solely as payload.action_result.
    """
    import json
    import math

    identity, _ = _definition_parts(definition)
    ancestors: set[int] = set()

    def check(value: Any, path: str) -> None:
        value_type = type(value)
        if value is None or value_type in (str, bool, int):
            return
        if value_type is float:
            if not math.isfinite(value):
                raise ValueError(f"{path} contains a nonfinite number")
            return
        if value_type not in (dict, list):
            raise ValueError(f"{path} contains non-JSON {value_type.__name__}")
        marker = id(value)
        if marker in ancestors:
            raise ValueError(f"{path} contains a circular reference")
        ancestors.add(marker)
        try:
            if value_type is dict:
                for key, nested in value.items():
                    if type(key) is not str:
                        raise ValueError(f"{path} contains a non-string object key")
                    check(nested, f"{path}.{key}")
            else:
                for index, nested in enumerate(value):
                    check(nested, f"{path}[{index}]")
        finally:
            ancestors.remove(marker)

    try:
        check(result, "result")
        # Retain serialization as a final check without converting the value.
        json.dumps(result, allow_nan=False, ensure_ascii=False).encode("utf-8")
    except (ValueError, TypeError, RecursionError, UnicodeError) as exc:
        raise CapabilityValidationError(f"action {identity!r} must return strict JSON: {exc}") from exc
    _validate_action_schema(definition, "outputs", result)
