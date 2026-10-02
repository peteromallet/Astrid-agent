"""Approved deterministic CPU boundary for H3 Character Swap acceptance.

This fixture replaces inference only.  It never acquires model weights, never
claims that fixture bytes match a production checksum, and never submits a
prompt to ComfyUI.  The caller still compiles and transports the production
workflow and its checksum-pinned model requirements through Runtime.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
from typing import Any, Mapping

from astrid.packs.vibecomfy.production_engine import ProductionEngineError, ProductionRunResult


ASTRID_T9_MODEL_SUBSTITUTE_ACTIVE = True
BOUNDARY_MODE = "deterministic_cpu_model_boundary_v1"
BOUNDARY_PURPOSE = "h3_character_swap_public_runtime_cpu_acceptance_v1"
_MEDIA_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".mp4", ".mkv", ".mov", ".wav", ".flac"}


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _boundary_profile(profile: Any) -> Mapping[str, Any]:
    if not isinstance(profile, Mapping):
        raise ProductionEngineError("approved CPU boundary requires its readiness profile")
    boundary = profile.get("t9_model_substitute")
    if (
        not isinstance(boundary, Mapping)
        or boundary.get("approved") is not True
        or boundary.get("mode") != BOUNDARY_MODE
        or boundary.get("purpose") != BOUNDARY_PURPOSE
    ):
        raise ProductionEngineError("approved CPU boundary readiness identity differs")
    return boundary


def _workflow_members(workflow_path: str | Path) -> tuple[dict[str, Any], dict[str, str]]:
    source = Path(workflow_path).resolve(strict=True)
    members = [source]
    if source.suffix.lower() == ".py":
        members.extend((source.with_suffix(".vibe.json"), source.with_name("source.json")))
    for member in members:
        if member.is_symlink() or not member.is_file():
            raise ProductionEngineError(f"approved CPU boundary lacks sealed workflow member {member.name!r}")
    digests = {member.name: _sha256(member) for member in members}
    companion = source.with_suffix(".vibe.json")
    canonical_companion = (
        json.loads(companion.read_text(encoding="utf-8")) if companion in members else {}
    )
    if not isinstance(canonical_companion, dict):
        raise ProductionEngineError("approved CPU boundary companion is not an object")
    return canonical_companion, digests


def _managed_inputs(
    destination: str | Path,
    bindings: Mapping[str, Any] | None,
    profile: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], bytes]:
    session = profile.get("vibecomfy_session")
    if isinstance(session, Mapping):
        raw_session_dir = session.get("session_dir")
        if not isinstance(raw_session_dir, str) or not Path(raw_session_dir).is_absolute():
            raise ProductionEngineError("approved CPU boundary session identity is malformed")
        config_path = Path(raw_session_dir) / "config.json"
        try:
            config = json.loads(config_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ProductionEngineError("approved CPU boundary session config is unreadable") from exc
        raw_root = config.get("input_directory") if isinstance(config, Mapping) else None
        if not isinstance(raw_root, str) or not Path(raw_root).is_absolute():
            raise ProductionEngineError("approved CPU boundary session input root is malformed")
        root = Path(raw_root).resolve()
    else:
        root = Path(destination).resolve() / "engine-input"
    if root.is_symlink() or not root.is_dir():
        raise ProductionEngineError("approved CPU boundary managed input root is unavailable")
    material = bytearray(_canonical(dict(bindings or {})))
    descriptors: list[dict[str, Any]] = []
    for binding, value in sorted((bindings or {}).items()):
        if not isinstance(value, str) or Path(value).suffix.lower() not in _MEDIA_SUFFIXES:
            continue
        candidate = Path(value)
        if not candidate.is_absolute():
            candidate = root / candidate
        if candidate.is_symlink() or not candidate.is_file():
            raise ProductionEngineError(
                f"approved CPU boundary could not resolve managed binding {binding!r}"
            )
        resolved = candidate.resolve(strict=True)
        payload = resolved.read_bytes()
        material.extend(binding.encode())
        material.extend(payload)
        descriptors.append({
            "binding": binding,
            "member": resolved.name,
            "sha256": hashlib.sha256(payload).hexdigest(),
            "size": len(payload),
        })
    return descriptors, bytes(material)


def _stub_run(
    workflow_path: str | Path,
    destination: str | Path,
    *,
    task_identity: str,
    profile_id: str,
    model_id: str = "vibecomfy",
    template_id: str = "vibecomfy.run",
    hc03_profile: Any = None,
    expected_execution_identity: str | None = None,
    attempt_identity: str | None = None,
    workflow_input_bindings: Mapping[str, Any] | None = None,
) -> ProductionRunResult:
    del model_id, template_id, expected_execution_identity
    if profile_id != "t9_cpu_stub":
        raise ProductionEngineError("approved CPU boundary received an unexpected runtime profile")
    boundary = _boundary_profile(hc03_profile)
    evidence_path = Path(str(boundary.get("evidence_path", "")))
    if not evidence_path.is_absolute() or evidence_path.is_symlink():
        raise ProductionEngineError("approved CPU boundary evidence path is invalid")

    companion, workflow_digests = _workflow_members(workflow_path)
    managed_inputs, binding_material = _managed_inputs(
        destination, workflow_input_bindings, hc03_profile,
    )
    graph_binding_sha256 = hashlib.sha256(
        _canonical(workflow_digests) + binding_material
    ).hexdigest()
    marker_path = Path(os.environ["ASTRID_T9_MODEL_SUBSTITUTE_MARKER"])
    marker = json.loads(marker_path.read_text(encoding="utf-8"))
    if marker.get("pid") != os.getpid():
        raise ProductionEngineError("approved CPU boundary activation marker has another child identity")

    evidence = {
        "schema_version": "h3-character-swap-cpu-boundary.v1",
        "boundary_mode": BOUNDARY_MODE,
        "boundary_purpose": BOUNDARY_PURPOSE,
        "synthetic_inference": True,
        "queued_to_comfy": False,
        "production_model_bytes_observed": False,
        "task_id": task_identity,
        "attempt_id": attempt_identity,
        "child_pid": os.getpid(),
        "activation_marker": marker,
        "graph_binding_sha256": graph_binding_sha256,
        "sealed_workflow_members": workflow_digests,
        "managed_inputs": managed_inputs,
        "workflow_input_bindings": dict(workflow_input_bindings or {}),
        "canonical_companion": companion,
    }
    evidence_path.parent.mkdir(parents=True, exist_ok=True)
    evidence_path.write_bytes(_canonical(evidence))

    calls = Path(os.environ["ASTRID_T9_MODEL_SUBSTITUTE_CALLS"])
    calls.parent.mkdir(parents=True, exist_ok=True)
    with calls.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({
            "task_id": task_identity,
            "attempt_id": attempt_identity,
            "child_pid": os.getpid(),
            "graph_binding_sha256": graph_binding_sha256,
        }, sort_keys=True) + "\n")

    output_root = Path(destination).resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    audiovisual = output_root / "t9-deterministic-av.mp4"
    color = "#" + graph_binding_sha256[:6]
    frequency = str(220 + int(graph_binding_sha256[6:10], 16) % 660)
    subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", f"color=c={color}:s=1024x576:r=24:d=15",
        "-f", "lavfi", "-i", f"sine=frequency={frequency}:sample_rate=48000:duration=15",
        "-map", "0:v:0", "-map", "1:a:0", "-c:v", "libx264", "-preset", "ultrafast",
        "-crf", "0", "-pix_fmt", "yuv420p", "-c:a", "alac", "-ar", "48000",
        "-ac", "2", "-shortest", "-movflags", "+faststart", str(audiovisual),
    ], check=True, capture_output=True, text=True)
    return ProductionRunResult(outputs=(audiovisual,))


from astrid.packs.vibecomfy import production_engine as _production_engine

_production_engine.run_workflow_result_path = _stub_run
