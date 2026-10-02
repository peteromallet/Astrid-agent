"""Portable provenance for the current canonical H3 compiler."""
from __future__ import annotations
import hashlib
import json
import re
from typing import Any, Mapping

def compilation_provenance(preparation: Mapping[str, Any], compilation: Mapping[str, Any]) -> dict[str, Any]:
    request_digest = preparation.get("request_digest")
    if not isinstance(request_digest, str) or request_digest != compilation.get("request_digest"):
        raise ValueError("H3 request provenance does not match the selected compilation")
    compilation_digest = compilation.get("compilation_digest")
    if not isinstance(compilation_digest, str) or re.fullmatch(r"[0-9a-f]{64}", compilation_digest) is None:
        raise ValueError("H3 compilation has no valid compilation digest")
    if compilation.get("schema_version") == 1:
        workflow_rows = compilation.get("workflow")
        assets_row = compilation.get("managed_assets")
        if not isinstance(workflow_rows, Mapping) or not isinstance(assets_row, Mapping):
            raise ValueError("legacy H3 compilation is missing digest members")
        digest_payload = {
            **{key: value for key, value in compilation.items()
               if key not in {"workflow", "managed_assets", "compilation_digest", "manifest_path"}},
            "workflow": {name: {"sha256": row["sha256"]} for name, row in workflow_rows.items()},
            "managed_assets": {"sha256": assets_row["sha256"], "manifest": assets_row["manifest"]},
        }
    elif compilation.get("schema_version") == 2:
        binding = compilation.get("graph_binding")
        assets_row = compilation.get("managed_assets")
        if not isinstance(binding, Mapping) or not isinstance(assets_row, Mapping):
            raise ValueError("H3 compilation is missing graph binding or assets")
        digest_payload = {key: value for key, value in compilation.items()
                          if key not in {"graph", "graph_binding", "managed_assets", "prepared_artifact",
                                         "compilation_digest", "manifest_path"}}
        digest_payload["graph_binding"] = {"bundle_identity": binding["bundle_identity"]}
        digest_payload["managed_assets"] = {"sha256": assets_row["sha256"], "manifest": assets_row["manifest"]}
        prepared = compilation.get("prepared_artifact")
        if isinstance(prepared, Mapping):
            digest_payload["prepared_artifact"] = {"sha256": prepared["sha256"], "artifact_digest": prepared["artifact_digest"]}
    else:
        raise ValueError("H3 compilation schema version is unsupported")
    actual_digest = hashlib.sha256(json.dumps(digest_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    if actual_digest != compilation_digest:
        raise ValueError("H3 compilation digest does not match its request/artifact contract")
    workflow = compilation.get("workflow")
    graph_binding = compilation.get("graph_binding")
    managed_assets = compilation.get("managed_assets")
    if not isinstance(managed_assets, Mapping) or not isinstance(managed_assets.get("sha256"), str):
        raise ValueError("H3 compilation is missing managed asset provenance")
    if not isinstance(workflow, Mapping) and not isinstance(graph_binding, Mapping):
        raise ValueError("H3 compilation is missing workflow/graph provenance")
    return {
        "schema_version": 1,
        "request_digest": request_digest,
        "schedule_digest": preparation.get("mask_schedule", {}).get("digest"),
        "assets": [dict(item) for item in preparation.get("assets", [])],
        "graph": {
            "profile": compilation.get("profile"),
            **({"effective_configuration": compilation["effective_configuration"]}
               if "effective_configuration" in compilation else {}),
            "compilation_digest": compilation_digest,
            "workflow": dict(workflow) if isinstance(workflow, Mapping) else None,
            "graph": dict(compilation["graph"]) if isinstance(compilation.get("graph"), Mapping) else None,
            "graph_binding": dict(graph_binding) if isinstance(graph_binding, Mapping) else None,
            "managed_assets": dict(managed_assets),
        },
    }


def require_compilation_provenance(
    preparation: Mapping[str, Any], compilation: Mapping[str, Any], composition: Mapping[str, Any]
) -> None:
    expected = compilation_provenance(preparation, compilation)
    actual = composition.get("provenance")
    if not isinstance(actual, Mapping):
        raise ValueError("composition is missing compilation provenance")

    def portable(value: Any) -> Any:
        if isinstance(value, Mapping):
            return {key: portable(item) for key, item in value.items()
                    if key not in {"path", "local_path", "manifest_path", "relative_path"}}
        if isinstance(value, list):
            return [portable(item) for item in value]
        return value

    if portable(actual.get("graph")) != portable(expected["graph"]):
        raise ValueError("composition compilation digest/artifact provenance does not match compilation")
    if actual.get("request_digest") != expected["request_digest"]:
        raise ValueError("composition request provenance does not match compilation")
    if portable(actual.get("assets")) != portable(expected["assets"]):
        raise ValueError("composition asset provenance does not match compilation")
