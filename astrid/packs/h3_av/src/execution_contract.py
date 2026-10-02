"""Pure, request-schema-aware child execution contracts for H3 AV."""

from __future__ import annotations

from typing import Any, Mapping


def _root(name: str) -> dict[str, str]:
    return {"name": name, "root_input": name}


def _producer(name: str, stage: str, port: str) -> dict[str, str]:
    return {"name": name, "producer_stage": stage, "output_port": port}


def build_child_execution_contract(
    *,
    project_id: str,
    request_schema: int,
    request_object_id: str,
    input_bundle_object_id: str,
    operation: str | None = None,
    run_target: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the exact Runtime child policy for one normalized H3 request.

    Runtime receives one concrete policy; it must not be asked to guess
    between v1/v2 stage shapes or muxed/separate audiovisual output layouts.
    Native continuation uses one muxed ``vibecomfy_run`` artifact; the
    legacy edit/LanPaint route is the genuine separate video+audio case.
    """

    if not project_id or request_schema not in {1, 2}:
        raise ValueError("H3 staged publication needs a project and supported request schema")
    roots = {"request": request_object_id, "input_bundle": input_bundle_object_id}
    workflow_ports = ["python", "companion", "source"]
    workflow_inputs = [
        _producer(name, "compile", name)
        for name in workflow_ports
    ]
    separate_audio = request_schema == 1 and operation in {None, "edit"}
    # Main's host settles all VibeComfy media at this capability port.
    # Producer video/audio roles remain in metadata; association IDs bind
    # the distinct bytes consumed by compose.
    run_port = "vibecomfy_run"
    compose_inputs = [
        _producer("preparation", "prepare", "preparation"),
        _producer("compilation", "compile", "compilation"),
        _producer("generated", "run", run_port),
        _root("input_bundle"),
    ]
    if separate_audio:
        compose_inputs.append(_producer("generated_audio", "run", run_port))
    validate_inputs = list(workflow_inputs)
    if request_schema in {1, 2}:
        # Canonical Python validation must see the same managed media archive
        # and concrete workflow_inputs that the subsequent run stage sees.
        validate_inputs.append(_producer("managed_assets", "compile", "managed_assets"))
    stages = [
        {"name": "prepare", "capability_id": "h3_av.prepare", "inputs": [_root("request"), _root("input_bundle")]},
        {"name": "compile", "capability_id": "h3_av.compile", "inputs": [_producer("preparation", "prepare", "preparation"), _root("input_bundle")]},
        {"name": "validate", "capability_id": "vibecomfy.validate", "inputs": validate_inputs},
        {
            "name": "run",
            "capability_id": "vibecomfy.run",
            "target": dict(run_target or {"kind": "default"}),
            "inputs": [*workflow_inputs, _producer("managed_assets", "compile", "managed_assets")],
        },
        {"name": "compose", "capability_id": "h3_av.compose", "inputs": compose_inputs},
        {
            "name": "verify",
            "capability_id": "h3_av.verify",
            "inputs": [
                _producer("preparation", "prepare", "preparation"),
                _producer("compilation", "compile", "compilation"),
                _producer("composition", "compose", "composition"),
                _producer("candidate", "compose", "candidate"),
                _root("input_bundle"),
            ],
        },
        {
            "name": "finalize",
            "capability_id": "h3_av.publication_finalizer",
            "inputs": [_producer("verified_candidate", "verify", "verified_candidate")],
        },
    ]
    return {
        "capability_ids": [stage["capability_id"] for stage in stages],
        "root_inputs": roots,
        "stages": stages,
        "final_publication": {
            "stage": "finalize",
            "verify_stage": "verify",
            "verify_output_port": "verified_candidate",
            "effect": {
                "effect_type": "generation.publish_v1",
                "target_id": project_id,
                "payload": {
                    "version": 1,
                    "modality": "video",
                    "generation_type": "h3_av.publication_finalizer",
                    "metadata": {"source_capability": "h3_av.transform"},
                    "partial_success_policy": "reject",
                    "groups": [{"group_key": "main", "selectors": [{
                        "selector": "main-0",
                        "ordinal": 0,
                        "variant_key": "original",
                        "output_port": "verified_candidate",
                        "required": True,
                    }]}],
                },
            },
        },
    }
