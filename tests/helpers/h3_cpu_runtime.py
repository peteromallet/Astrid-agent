"""Fixture-owned Runtime and managed VibeComfy session for H3 CPU evidence."""

from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import io
import tarfile
from typing import Any, Mapping

from astrid.core.generation.model_root import canonical_model_inventory_digest
from astrid.packs.h3_av.src.timing import plan_continuation

ASTRID_ROOT = Path(__file__).resolve().parents[2]


def _repository_evidence_root(source_root: Path) -> Path:
    """Locate shared fixture evidence for normal checkouts and nested worktrees."""
    result = subprocess.run(
        ["git", "-C", str(source_root), "rev-parse", "--path-format=absolute", "--git-common-dir"],
        capture_output=True,
        text=True,
        check=True,
    )
    return Path(result.stdout.strip()).resolve().parent


WORKSPACE_ROOT = _repository_evidence_root(ASTRID_ROOT)
_runtime_candidate = ASTRID_ROOT.parent / "Runtime"
RUNTIME_ROOT = Path(
    os.environ.get("ASTRID_STAGE1_RUNTIME_CHECKOUT")
    or (_runtime_candidate if _runtime_candidate.is_dir() else WORKSPACE_ROOT.parent / "banodoco-workspace-runtime")
).resolve()
VIBECOMFY_ROOT = Path(
    os.environ.get("ASTRID_VIBECOMFY_CHECKOUT") or WORKSPACE_ROOT.parent / "vibecomfy"
).resolve()
VIBECOMFY_SOURCE_ROOT = VIBECOMFY_ROOT
# The CPU candidate includes the source-derived ModelAttentionBackend schema.
# Its measured local_snapshot readiness profile selects this revision without
# advancing Astrid's production VIBECOMFY_ENGINE_REVISION.
CPU_VIBECOMFY_CANDIDATE_REVISION = "b554ed14dbb481130b96dd0c927e1fde4f02e447"
H3_CAPTURED_VIBECOMFY_REVISION = "01f38461d633651c8857712b2331650aedaee461"
H3_RUNPOD_SCHEMA_SHA256 = "1133daa166d646db626e05d9aba1c8d5b689fa0a19216e3d10ea08e7a9fb6810"
H3_RUNPOD_GRAPH_CLOSURE_SHA256 = "af9271339890a3dcc36f1068a51eadb4f5a3ccb67652e7ec63c3ec41926b0e55"
H3_RUNPOD_OVERLAY_SHA256 = "50f38e439f2c3945f0fad13b11e565defb72433aabd92fe9d0dd379a014bd020"
APPROVED_CPU_MODEL_BOUNDARY = "approved_t9_v1"
CPU_MODEL_BOUNDARY_PURPOSE = "h3_character_swap_public_runtime_cpu_acceptance_v1"
H3_REQUIRED_OBJECT_INFO_CLASSES = frozenset(
    {
        "MiniMaxH3AVExtensionController",
        "MiniMaxH3AVSourceAudioModeParam",
        "MiniMaxH3AVStartModeParam",
        "MiniMaxH3AudioVAECompatibility",
        "MiniMaxH3CropTo32",
        "MiniMaxH3CustomKeyframes",
        "MiniMaxH3FinalizeVHSOutput",
        "MiniMaxH3GeneratedAVMaskedContext",
        "MiniMaxH3LastActiveVHSPreviewBarrier",
        "MiniMaxH3SourceAudioPolicy",
        "MiniMaxH3SourceAudioRegenLength",
        "MiniMaxH3SourceAudioRegenMask",
        "MiniMaxH3StartCanvasSelector",
        "MiniMaxH3StartMaskedContext",
        "MiniMaxH3StreamLiveExtensionAVToVHS",
        "MiniMaxH3Validate24FPSVideo",
    }
)
H3_GRAPH_CLOSURE_CLASSES = H3_REQUIRED_OBJECT_INFO_CLASSES | frozenset(
    {"MiniMaxH3ReferenceToVideo", "MiniMaxH3SigmaShift"}
)
T9_PROFILE = (
    WORKSPACE_ROOT
    / ".otto"
    / "runs"
    / "h3-transform-pack-20260923"
    / "fixtures"
    / "t9-public-h3-20260925-9"
    / "support"
    / "runtime"
    / "t9-vibecomfy-readiness.json"
)

# Match host_bootstrap: the selected Astrid source owns the vendored client;
# Runtime implementation modules come from the explicitly selected checkout.
_import_roots = [str(root) for root in (ASTRID_ROOT, RUNTIME_ROOT, RUNTIME_ROOT / "packages" / "python")]
sys.path[:] = _import_roots + [path for path in sys.path if path not in _import_roots]

from runtime_protocol.daemon import RuntimeDaemon  # noqa: E402
from runtime_protocol.store import RealmStore  # noqa: E402

from astrid.core.execution.generic_host import RuntimeProtocolClient, source_checkout_digest  # noqa: E402
from astrid.core.generation.backends.vibecomfy import (  # noqa: E402
    _canonical_sha256,
    _owner_session_command,
    _session_registry_binding,
    _verify_owned_vibe_session,
)
from astrid.core.pack.source_setup import active_source_inventory  # noqa: E402
from astrid.packs.h3_av.src.request import normalize_request  # noqa: E402
from astrid.packs.vibecomfy.asset_manifest import read_archive  # noqa: E402
from astrid.sdk.client import AstridClient  # noqa: E402
from astrid.sdk.host_bootstrap import PACK_HOST_ACTOR, PACK_HOST_SCOPES, ensure_pack_host  # noqa: E402
from astrid.sdk.workspace_client import SCHEMA_DIGEST  # noqa: E402


_SERVER_CODE = r"""
import hashlib
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
import subprocess
import sys
import threading
import time

if sys.argv[1] == "serve":
    def option(name):
        return sys.argv[sys.argv.index(name) + 1]

    port = int(option("--port"))
    schema_path = Path(os.environ["ASTRID_C11_SCHEMA_PATH"])
    expected_path = Path(os.environ["ASTRID_C11_EXPECTED_PATH"])
    evidence_path = Path(os.environ["ASTRID_C11_EVIDENCE_PATH"])
    config = {
        "input_directory": option("--input-directory"),
        "output_directory": option("--output-directory"),
    }
else:
    port = int(sys.argv[1])
    schema_path = Path(sys.argv[2])
    config_path = Path(sys.argv[3])
    expected_path = Path(sys.argv[4])
    evidence_path = Path(sys.argv[5])
    config = json.loads(config_path.read_text(encoding="utf-8"))
schema = json.loads(schema_path.read_text(encoding="utf-8"))
input_directory = Path(config["input_directory"])
output_directory = Path(config["output_directory"])
session_dir = Path(os.environ["ASTRID_C11_SESSION_DIR"]) if os.environ.get("ASTRID_C11_SESSION_DIR") else None
histories = {}
queue_lock = threading.Lock()

class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def respond(self, status, payload):
        body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        # Let the client close first so the fixed fixture port is available
        # when the owner immediately restarts this session.
        self.close_connection = False

    def do_GET(self):
        parsed = urlsplit(self.path)
        if parsed.path == "/view":
            try:
                query = parse_qs(parsed.query, keep_blank_values=True, strict_parsing=True)
            except ValueError:
                self.send_error(400)
                return
            if set(query) != {"filename", "subfolder", "type"} or any(
                len(values) != 1 for values in query.values()
            ):
                self.send_error(400)
                return
            filename = query["filename"][0]
            subfolder = query["subfolder"][0]
            media_type = query["type"][0]
            descriptor = {
                "filename": filename,
                "subfolder": subfolder,
                "type": media_type,
            }
            published = any(
                item == descriptor
                for history in histories.values()
                for output in history.get("outputs", {}).values()
                for key in ("gifs", "images")
                for item in output.get(key, [])
                if isinstance(item, dict)
            )
            if not published or media_type != "output":
                self.send_error(404)
                return
            relative = Path(subfolder) / filename
            if (
                not filename
                or Path(filename).name != filename
                or Path(filename).is_absolute()
                or any(part in {".", ".."} for part in relative.parts)
                or relative.is_absolute()
            ):
                self.send_error(400)
                return
            root = output_directory.resolve()
            candidate = output_directory / relative
            resolved = candidate.resolve(strict=False)
            try:
                resolved.relative_to(root)
            except ValueError:
                self.send_error(403)
                return
            if candidate.is_symlink() or not resolved.is_file():
                self.send_error(404)
                return
            try:
                body = resolved.read_bytes()
            except OSError:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", "video/mp4")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            self.close_connection = False
            return
        if parsed.path == "/object_info" and not parsed.query:
            payload = schema
        elif parsed.path == "/system_stats" and not parsed.query:
            # ServerSession probes health before publishing pid. Once pid is
            # published, the owner CLI must wait for the subsequent source
            # attestation before it can declare the registry ready.
            if session_dir is not None and (session_dir / "pid").is_file():
                attestations = (session_dir / "source_revision", session_dir / "source_content_digest")
                deadline = time.monotonic() + 10
                while not all(path.is_file() and path.read_text(encoding="utf-8").strip() for path in attestations):
                    if time.monotonic() >= deadline:
                        self.send_error(503, "fixture owner source attestation was not published")
                        return
                    time.sleep(0.02)
            payload = {
                "system": {
                    "comfyui_version": "0.36.0",
                    "comfyui_frontend_package": "1.39.19",
                    "python_version": sys.version.split()[0],
                    "argv": ["h3-c11-fixture"],
                    "embedded_python": False,
                },
                "devices": [],
            }
        elif parsed.path.startswith("/history/") and not parsed.query:
            prompt_id = parsed.path.removeprefix("/history/")
            payload = {prompt_id: histories[prompt_id]} if prompt_id in histories else {}
        else:
            self.send_error(404)
            return
        self.respond(200, payload)

    def do_POST(self):
        if self.path in {"/queue", "/api/free", "/interrupt"}:
            with queue_lock:
                # The fixture completes generation before acknowledging
                # /prompt, so no active or pending work remains to release.
                self.respond(200, {"ok": True, "completed": True, "status": "completed"})
            return
        if self.path != "/prompt":
            self.send_error(404)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length < 1 or length > 2_000_000:
                raise ValueError("prompt body length is invalid")
            request = json.loads(self.rfile.read(length))
            prompt = request["prompt"]
            expected = json.loads(expected_path.read_text(encoding="utf-8"))
            loader = prompt["99"]
            basename = loader["inputs"]["video"]
            if loader["class_type"] != "VHS_LoadVideoFFmpeg" or basename != expected["basename"]:
                raise ValueError("compiled node 99 video differs from the managed source basename")
            if not isinstance(basename, str) or Path(basename).name != basename:
                raise ValueError("compiled source video is not a basename")
            staged = input_directory / basename
            if staged.is_symlink() or not staged.is_file():
                raise ValueError("managed source video is absent from configured input_directory")
            digest = hashlib.sha256(staged.read_bytes()).hexdigest()
            if digest != expected["sha256"] or staged.stat().st_size != expected["size"]:
                raise ValueError("staged source video differs from the managed archive digest")
            sinks = [node_id for node_id, node in prompt.items()
                     if node.get("class_type") == "MiniMaxH3StreamLiveExtensionAVToVHS"]
            if len(sinks) != 1:
                raise ValueError("fixture requires exactly one declared H3 video sink")
            with queue_lock:
                prompt_id = f"h3-c11-fixture-{len(histories) + 1:04d}"
                filename = f"{prompt_id}.mp4"
                output = output_directory / filename
                duration = float(expected["generated_seconds"])
                subprocess.run([
                    "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                    "-f", "lavfi", "-i", f"color=c=red:s=64x64:r=24:d={duration}",
                    "-f", "lavfi", "-i", f"sine=frequency=880:sample_rate=48000:duration={duration}",
                    "-map", "0:v:0", "-map", "1:a:0", "-c:v", "libx264",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(output),
                ], check=True, capture_output=True)
                histories[prompt_id] = {
                    "status": {"status_str": "success", "completed": True, "messages": []},
                    "outputs": {sinks[0]: {"gifs": [{
                        "filename": filename, "subfolder": "", "type": "output",
                    }]}},
                }
                evidence_path.write_text(json.dumps({
                    "prompt_id": prompt_id,
                    "node_id": "99",
                    "video": basename,
                    "sha256": digest,
                    "size": staged.stat().st_size,
                    "output_node": sinks[0],
                    "output_filename": filename,
                }, sort_keys=True), encoding="utf-8")
            self.respond(200, {"prompt_id": prompt_id, "number": len(histories), "node_errors": {}})
        except (KeyError, TypeError, ValueError, OSError, subprocess.CalledProcessError) as exc:
            self.respond(400, {"error": f"C11 fixture rejected prompt: {exc}"})

    def log_message(self, *_args):
        pass

ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
"""


def _canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _prepare_clean_vibecomfy_checkout(root: Path) -> Path:
    """Clone the pinned VibeComfy source into the fixture's temporary root."""
    source_root = VIBECOMFY_SOURCE_ROOT
    if not (source_root / "vibecomfy" / "__init__.py").is_file():
        raise RuntimeError(f"VibeComfy source package is unavailable: {source_root}")
    pinned_revision = subprocess.run(
        ["git", "-C", str(source_root), "rev-parse", "--verify", f"{CPU_VIBECOMFY_CANDIDATE_REVISION}^{{commit}}"],
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    ).stdout.strip()
    if pinned_revision != CPU_VIBECOMFY_CANDIDATE_REVISION:
        raise RuntimeError(
            "C11 requires the pinned VibeComfy source revision "
            f"{CPU_VIBECOMFY_CANDIDATE_REVISION}, got {pinned_revision}"
        )

    checkout = (root / "dependencies" / "vibecomfy").resolve()
    checkout.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "git",
            "clone",
            "--shared",
            "--quiet",
            "--no-checkout",
            str(source_root),
            str(checkout),
        ],
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    archive_bytes = subprocess.check_output(
        ["git", "-C", str(checkout), "archive", pinned_revision], timeout=20,
    )
    with tarfile.open(fileobj=io.BytesIO(archive_bytes)) as archive:
        archive.extractall(checkout, filter="data")
    subprocess.run(
        ["git", "-C", str(checkout), "update-ref", "--no-deref", "HEAD", pinned_revision],
        capture_output=True, text=True, check=True, timeout=20,
    )
    subprocess.run(
        ["git", "-C", str(checkout), "read-tree", pinned_revision],
        capture_output=True, text=True, check=True, timeout=20,
    )
    actual_revision = subprocess.run(
        ["git", "-C", str(checkout), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    ).stdout.strip()
    branch = subprocess.run(
        ["git", "-C", str(checkout), "symbolic-ref", "--quiet", "--short", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    status = subprocess.run(
        ["git", "-C", str(checkout), "status", "--porcelain"],
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    ).stdout
    if actual_revision != CPU_VIBECOMFY_CANDIDATE_REVISION or branch.returncode == 0 or status:
        raise RuntimeError(
            "C11 VibeComfy clone is not a clean detached checkout: "
            f"revision={actual_revision!r}, branch={branch.stdout.strip()!r}, status={status!r}"
        )
    if not (checkout / "vibecomfy" / "__init__.py").is_file():
        raise RuntimeError(f"C11 VibeComfy clone has no package initializer: {checkout}")
    return checkout


def _vibecomfy_child_pythonpath(checkout: Path) -> str:
    """Return the explicit import roots for fixture-owned child processes."""
    child_paths = (
        ASTRID_ROOT,
        RUNTIME_ROOT,
        RUNTIME_ROOT / "packages" / "python",
        checkout.resolve(),
    )
    return os.pathsep.join(str(path) for path in child_paths)


def _vibecomfy_child_environment(
    checkout: Path,
    *,
    extra: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Build a child-only environment for the clean detached checkout."""
    environment = dict(os.environ)
    environment["ASTRID_VIBECOMFY_CHECKOUT"] = str(checkout.resolve())
    environment["PYTHONPATH"] = _vibecomfy_child_pythonpath(checkout)
    if extra:
        environment.update({str(key): str(value) for key, value in extra.items()})
    return environment


@contextmanager
def _temporary_environment(values: Mapping[str, str]):
    """Temporarily expose exact child launch values without leaking them."""
    prior = {key: os.environ.get(key) for key in values}
    try:
        os.environ.update({str(key): str(value) for key, value in values.items()})
        yield
    finally:
        for key, value in prior.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _probe_vibecomfy_checkout(checkout: Path) -> dict[str, str]:
    """Attest the selected package in a fresh child interpreter."""
    source = checkout.resolve()
    probe = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import json; from pathlib import Path; import vibecomfy; "
                "from vibecomfy.runtime.session import "
                "current_source_content_digest,current_source_revision; "
                "print(json.dumps({'origin':str(Path(vibecomfy.__file__).resolve()),"
                "'revision':current_source_revision(),"
                "'content_digest':current_source_content_digest()},sort_keys=True))"
            ),
        ],
        cwd=str(source),
        env=_vibecomfy_child_environment(source),
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    if probe.returncode != 0:
        raise RuntimeError(
            "could not attest the selected clean VibeComfy checkout: "
            + probe.stderr[-2000:]
        )
    try:
        identity = json.loads(probe.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("clean VibeComfy checkout probe returned malformed evidence") from exc
    origin = Path(str(identity.get("origin", ""))).resolve()
    revision = str(identity.get("revision", ""))
    content_digest = str(identity.get("content_digest", ""))
    digest_hex = content_digest.removeprefix("sha256:")
    if (
        not origin.is_relative_to(source)
        or revision != CPU_VIBECOMFY_CANDIDATE_REVISION
        or not content_digest.startswith("sha256:")
        or len(digest_hex) != 64
        or any(character not in "0123456789abcdef" for character in digest_hex)
    ):
        raise RuntimeError(
            "could not attest the selected clean VibeComfy checkout: "
            f"origin={origin!s}, revision={revision!r}, digest={content_digest!r}"
        )
    return {
        "origin": str(origin),
        "revision": revision,
        "content_digest": content_digest,
    }


def _object_info_from_checkout(checkout: Path) -> dict[str, Any]:
    cache_root = checkout.resolve() / "vibecomfy" / "porting" / "cache" / "object_info"
    index = json.loads((cache_root / "index.json").read_text(encoding="utf-8"))
    payloads: dict[str, dict[str, Any]] = {}
    result: dict[str, Any] = {}

    def normalize_row(row: Mapping[str, Any]) -> dict[str, Any]:
        normalized = dict(row)
        if "input" not in normalized and isinstance(normalized.get("inputs"), Mapping):
            normalized["input"] = normalized["inputs"]
        if "output" not in normalized and isinstance(normalized.get("outputs"), list):
            normalized["output"] = [
                [item.get("type", "*"), item.get("name", "output")]
                for item in normalized["outputs"]
                if isinstance(item, Mapping)
            ]
        normalized.pop("inputs", None)
        normalized.pop("outputs", None)
        return normalized

    def payload_for(path: Path) -> dict[str, Any]:
        filename = path.name
        if filename not in payloads:
            raw = json.loads(path.read_text(encoding="utf-8"))
            payloads[filename] = raw if isinstance(raw, dict) else {}
        return payloads[filename]

    for class_type, filename in sorted(index.items()):
        if not isinstance(class_type, str) or not isinstance(filename, str):
            continue
        pack_path = (cache_root / filename).resolve()
        if not pack_path.is_relative_to(cache_root.resolve()) or not pack_path.is_file():
            continue
        row = payload_for(pack_path).get(class_type)
        if isinstance(row, Mapping):
            # TargetSchemaProvider consumes the same per-node schema shape as
            # ComfyUI's /object_info endpoint; the payload comes from this
            # explicit dirty checkout's indexed schema corpus.
            result[class_type] = normalize_row(row)

    # The index is intentionally authoritative for its existing rows, but
    # source-derived cache payloads can contain real node metadata that was
    # omitted from that index.  Add those rows without inventing schemas or
    # allowing a fallback payload to replace an indexed row.
    for pack_path in sorted(cache_root.glob("*.json")):
        if pack_path.name in {"index.json", "provenance.json"}:
            continue
        resolved = pack_path.resolve()
        if not resolved.is_relative_to(cache_root.resolve()) or not resolved.is_file():
            continue
        for class_type, row in sorted(payload_for(resolved).items()):
            if isinstance(class_type, str) and isinstance(row, Mapping):
                result.setdefault(class_type, normalize_row(row))

    fixture_root = ASTRID_ROOT / "tests" / "fixtures" / "h3_cpu"
    legacy_artifact = json.loads(
        (fixture_root / "object_info_legacy_import.json").read_text(encoding="utf-8")
    )
    assert hashlib.sha256(_canonical_json(legacy_artifact)).hexdigest() == (
        "0539a41691a06eb4a5209a20c4dbcc0a860eb1e15e670dab76f28fe71cf5202d"
    )
    assert "ModelAttentionBackend" in legacy_artifact
    for class_type, row in sorted(legacy_artifact.items()):
        if isinstance(class_type, str) and isinstance(row, Mapping):
            result.setdefault(class_type, normalize_row(row))

    h3_artifact = json.loads(
        (fixture_root / "object_info_h3_runpod.json").read_text(encoding="utf-8")
    )
    provenance = json.loads(
        (fixture_root / "object_info_h3_runpod.provenance.json").read_text(encoding="utf-8")
    )
    h3_digest = hashlib.sha256(_canonical_json(h3_artifact)).hexdigest()
    assert h3_digest == H3_RUNPOD_SCHEMA_SHA256
    assert provenance["filtered_object_info_sha256"] == H3_RUNPOD_SCHEMA_SHA256
    assert provenance["raw_object_info_sha256"] == (
        "505f7c31ebea6a48ea3db3ef77184923a529a6073fd373f780d48f693463154b"
    )
    assert provenance["vibecomfy_revision"] == H3_CAPTURED_VIBECOMFY_REVISION
    assert provenance["h3_pack"]["revision"] == (
        "361624fb406b63eb6694442eac6c895fc1533a70"
    )
    assert set(h3_artifact) == H3_REQUIRED_OBJECT_INFO_CLASSES
    assert provenance["required_class_count"] == len(H3_REQUIRED_OBJECT_INFO_CLASSES)
    assert set(provenance["required_classes"]) == H3_REQUIRED_OBJECT_INFO_CLASSES
    for class_type, row in sorted(h3_artifact.items()):
        if isinstance(class_type, str) and isinstance(row, Mapping):
            result.setdefault(class_type, normalize_row(row))

    closure_artifact = json.loads(
        (fixture_root / "object_info_h3_runpod_graph_closure.json").read_text(encoding="utf-8")
    )
    closure_provenance = json.loads(
        (fixture_root / "object_info_h3_runpod_graph_closure.provenance.json").read_text(encoding="utf-8")
    )
    assert hashlib.sha256(_canonical_json(closure_artifact)).hexdigest() == (
        H3_RUNPOD_GRAPH_CLOSURE_SHA256
    )
    assert closure_provenance["filtered_object_info_sha256"] == H3_RUNPOD_GRAPH_CLOSURE_SHA256
    assert closure_provenance["parent_filtered_object_info_sha256"] == H3_RUNPOD_SCHEMA_SHA256
    assert closure_provenance["raw_object_info_sha256"] == provenance["raw_object_info_sha256"]
    assert closure_provenance["vibecomfy_revision"] == provenance["vibecomfy_revision"]
    assert closure_provenance["h3_pack"]["revision"] == provenance["h3_pack"]["revision"]
    assert set(closure_artifact) == H3_GRAPH_CLOSURE_CLASSES
    assert closure_provenance["required_class_count"] == len(H3_GRAPH_CLOSURE_CLASSES)
    assert set(closure_provenance["required_classes"]) == H3_GRAPH_CLOSURE_CLASSES
    assert set(closure_provenance["added_classes"]) == {
        "MiniMaxH3ReferenceToVideo",
        "MiniMaxH3SigmaShift",
    }
    for class_type, row in sorted(closure_artifact.items()):
        if isinstance(class_type, str) and isinstance(row, Mapping):
            result.setdefault(class_type, normalize_row(row))

    overlay_artifact = json.loads(
        (fixture_root / "object_info_h3_runpod_overlay.json").read_text(encoding="utf-8")
    )
    overlay_provenance = json.loads(
        (fixture_root / "object_info_h3_runpod_overlay.provenance.json").read_text(encoding="utf-8")
    )
    assert hashlib.sha256(_canonical_json(overlay_artifact)).hexdigest() == (
        H3_RUNPOD_OVERLAY_SHA256
    )
    assert set(overlay_artifact) == {"CLIPLoader"}
    assert overlay_provenance["filtered_object_info_sha256"] == H3_RUNPOD_OVERLAY_SHA256
    assert overlay_provenance["raw_object_info_sha256"] == (
        "505f7c31ebea6a48ea3db3ef77184923a529a6073fd373f780d48f693463154b"
    )
    assert overlay_provenance["filtered_closure_sha256"] == H3_RUNPOD_GRAPH_CLOSURE_SHA256
    assert overlay_provenance["vibecomfy_revision"] == H3_CAPTURED_VIBECOMFY_REVISION
    assert overlay_provenance["h3_pack_revision"] == closure_provenance["h3_pack"]["revision"]
    assert overlay_provenance["comfy_revision"] == provenance["comfy_revision"]
    assert overlay_provenance["overlaid_class"] == "CLIPLoader"
    assert overlay_provenance["overlay_mode"] == "explicit_replace_after_cache_and_h3_closure"
    captured_clip_loader = overlay_artifact["CLIPLoader"]
    assert isinstance(captured_clip_loader, Mapping)
    result["CLIPLoader"] = dict(captured_clip_loader)
    assert result["CLIPLoader"] == captured_clip_loader
    clip_loader_choices = result["CLIPLoader"]["input"]["required"]["type"][0]
    assert "minimax" in clip_loader_choices
    return result


class ManagedSchemaSession:
    """A pinned VibeComfy owner session with a fixture-owned Comfy executable."""

    def __init__(
        self,
        root: Path,
        source_revision: str,
        source_digest: str,
        *,
        vibecomfy_checkout: Path,
    ) -> None:
        self.root = root.resolve()
        self.session_dir = self.root / "out" / "sessions" / "h3-c11"
        self.output_root = self.root / "output"
        self.input_root = self.root / "input"
        self.temp_root = self.root / "temp"
        for path in (self.output_root, self.input_root, self.temp_root):
            path.mkdir(parents=True)
        self.schema_path = self.root / "object-info.json"
        self.expected_path = self.root / "expected-source.json"
        self.queue_evidence_path = self.root / "queue-evidence.json"
        self.schema_path.write_bytes(
            _canonical_json(_object_info_from_checkout(vibecomfy_checkout))
        )
        executable_dir = self.root / "bin"
        executable_dir.mkdir(parents=True)
        executable = executable_dir / "comfyui"
        executable.write_text(f"#!{sys.executable}\n{_SERVER_CODE}", encoding="utf-8")
        executable.chmod(0o755)
        self.child_environment = _vibecomfy_child_environment(
            vibecomfy_checkout,
            extra={
                "PATH": str(executable_dir) + os.pathsep + os.environ.get("PATH", ""),
                "ASTRID_C11_SCHEMA_PATH": str(self.schema_path),
                "ASTRID_C11_EXPECTED_PATH": str(self.expected_path),
                "ASTRID_C11_EVIDENCE_PATH": str(self.queue_evidence_path),
                "ASTRID_C11_SESSION_DIR": str(self.session_dir),
            },
        )
        try:
            # vibecomfy.run's declared loopback network policy admits this port.
            port = 8188
            start = subprocess.run(
                [
                    sys.executable, "-m", "vibecomfy.cli", "--quiet", "session", "start",
                    "--id", self.session_dir.name, "--runtime-root", str(self.root),
                    "--port", str(port), "--ready-timeout-sec", "20",
                    "--input-directory", str(self.input_root),
                    "--output-directory", str(self.output_root),
                    "--temp-directory", str(self.temp_root),
                ],
                env=self.child_environment,
                capture_output=True, text=True, timeout=35,
            )
            if start.returncode != 0:
                log = self.session_dir / "daemon.log"
                detail = log.read_text(encoding="utf-8") if log.is_file() else start.stderr
                raise RuntimeError(f"managed schema session failed to start: {detail[-2000:]}")
            binding = self.registry_binding()
            self.server_url = binding["server_url"]
            self.pid = binding["pid"]
            self.comfy_pid = binding["comfy_pid"]
            _verify_owned_vibe_session(self.session_dir, self.pid)
            # The CLI can observe pid/url readiness just before the daemon
            # finishes publishing its source attestation files.
            source_paths = (self.session_dir / "source_revision", self.session_dir / "source_content_digest")
            deadline = time.monotonic() + 15
            while not all(path.is_file() for path in source_paths):
                if time.monotonic() >= deadline:
                    raise RuntimeError("managed schema session did not publish source attestation")
                time.sleep(0.05)
            config_bytes = (self.session_dir / "config.json").read_bytes()
            config = json.loads(config_bytes)
            if (
                config["runtime_root"] != str(self.root)
                or config["input_directory"] != str(self.input_root)
                or config["output_directory"] != str(self.output_root)
                or config["temp_directory"] != str(self.temp_root)
                or config["port"] != port
            ):
                raise RuntimeError("managed schema session configuration differs from its fixture roots")
            observed_revision = (self.session_dir / "source_revision").read_text(encoding="utf-8").strip()
            observed_digest = (self.session_dir / "source_content_digest").read_text(encoding="utf-8").strip()
            if (observed_revision, observed_digest) != (source_revision, source_digest):
                raise RuntimeError("managed schema session source identity differs from pinned checkout")
            self.session_binding = {
                "session_dir": str(self.session_dir),
                **binding,
                "source_revision": observed_revision,
                "source_content_digest": observed_digest,
                "config_digest": "sha256:" + hashlib.sha256(config_bytes).hexdigest(),
            }
        except BaseException:
            self.close()
            raise

    def registry_binding(self) -> dict[str, Any]:
        return _session_registry_binding(self.session_dir)

    def close(self) -> None:
        if (self.session_dir / "pid").is_file():
            stop = subprocess.run(
                _owner_session_command(self.session_dir, "stop"),
                env=self.child_environment,
                capture_output=True, text=True, timeout=20,
            )
            if stop.returncode != 0:
                raise RuntimeError(f"managed schema session stop failed: {stop.stderr}")
        if (self.session_dir / "pid").exists() or (self.session_dir / "comfy_pid").exists():
            raise RuntimeError("managed schema session owner did not clear its process registry")


class H3CpuRuntime:
    """Runtime daemon, real pack host, readiness profile, and fixture session."""

    def __init__(self, root: Path, *, cpu_model_boundary: str | None = None,
                 source_capsule: Path | None = None) -> None:
        # Use the real producer's custody gate before creating any Runtime,
        # session or host. Never substitute a successful source measurement.
        self.source_identity = None
        if source_capsule is not None:
            from scripts.run_h3_canonical_on_runpod import assert_product_source_identity

            self.source_identity = assert_product_source_identity(capsule=source_capsule)
        if cpu_model_boundary not in {None, APPROVED_CPU_MODEL_BOUNDARY}:
            raise ValueError(f"unsupported CPU model boundary: {cpu_model_boundary!r}")
        if not RUNTIME_ROOT.is_dir():
            raise RuntimeError(f"Runtime checkout is unavailable: {RUNTIME_ROOT}")
        if not T9_PROFILE.is_file():
            raise RuntimeError(f"approved T9 readiness profile is unavailable: {T9_PROFILE}")
        if not VIBECOMFY_SOURCE_ROOT.is_dir():
            raise RuntimeError(f"VibeComfy source checkout is unavailable: {VIBECOMFY_SOURCE_ROOT}")
        self.root = root.resolve()
        self.cpu_model_boundary = cpu_model_boundary
        self.cpu_model_boundary_evidence_path = self.root / "cpu-model-boundary-evidence.json"
        self._prior_sys_path = tuple(sys.path)
        self._prior_environment = {
            key: os.environ.get(key)
            for key in (
                "ASTRID_C11_EVIDENCE_PATH",
                "ASTRID_C11_EXPECTED_PATH",
                "ASTRID_C11_SCHEMA_PATH",
                "ASTRID_C11_SESSION_DIR",
                "ASTRID_HOST_READINESS_PROFILE_HASH",
                "ASTRID_HOST_READINESS_PROFILE_PATH",
                "ASTRID_VIBECOMFY_CHECKOUT",
                "BANODOCO_LOCAL_DATA_ROOT",
                "BANODOCO_RUNTIME_CREDENTIAL",
                "BANODOCO_RUNTIME_ENDPOINT",
                "PATH",
                "PYTHONPATH",
            )
        }
        self._prior_vibecomfy_modules = {
            name: module
            for name, module in sys.modules.items()
            if name == "vibecomfy" or name.startswith("vibecomfy.")
        }
        self._closed = False
        self.daemon: RuntimeDaemon | None = None
        self.schema_session: ManagedSchemaSession | None = None
        self.host: Mapping[str, Any] | None = None
        self.host_pid: int | None = None
        self.host_state_path: Path | None = None
        self.host_errors: list[BaseException] = []
        self.claimed_lanes: list[str | None] = []
        try:
            self.vibecomfy_root = _prepare_clean_vibecomfy_checkout(self.root)
            if not self.vibecomfy_root.is_relative_to(self.root):
                raise RuntimeError("C11 VibeComfy checkout escaped the fixture temporary root")
            self.vibecomfy_source_identity = _probe_vibecomfy_checkout(
                self.vibecomfy_root
            )
            source_revision = self.vibecomfy_source_identity["revision"]
            source_digest = self.vibecomfy_source_identity["content_digest"]
            self.data_root = (self.root / "runtime-data").resolve()
            self.support_root = self.data_root / "runtime"
            self.data_root.mkdir(parents=True, exist_ok=True)
            self.support_root.mkdir(parents=True, exist_ok=True)
            self.models_root = (self.data_root / "models").resolve()
            if self.models_root.is_symlink():
                raise RuntimeError("C11 model root must not be a symlink")
            self.models_root.mkdir(parents=True, exist_ok=True)
            model_paths = (
                "vae/minimax_h3_audio_vae_fp32.safetensors",
                "vae/minimax_h3_video_vae_int8_convrot.safetensors",
                "loras/minimax_h3_fl2v_turbo_8step_v1.0_comfyui_bf16.safetensors",
                "diffusion_models/minimax_h3_ref2va_pruned_int8_convrot.safetensors",
                "text_encoders/qwen3vl_32b_minimax_h3_int8_convrot.safetensors",
            )
            for relative in model_paths:
                model_path = self.models_root / relative
                model_path.parent.mkdir(parents=True, exist_ok=True)
                model_path.write_bytes(f"c11-model-fixture:{relative}\n".encode("utf-8"))
                if model_path.is_symlink() or not model_path.is_file() or model_path.stat().st_size == 0:
                    raise RuntimeError(f"C11 model fixture is not a regular non-empty file: {model_path}")
            model_inventory = []
            for model_path in sorted(self.models_root.rglob("*")):
                if not model_path.is_file() or model_path.is_symlink():
                    continue
                relative = model_path.relative_to(self.models_root)
                model_inventory.append(
                    {
                        "name": relative.name,
                        "sha256": "sha256:" + hashlib.sha256(model_path.read_bytes()).hexdigest(),
                        "size": model_path.stat().st_size,
                        "subdir": "" if relative.parent.as_posix() == "." else relative.parent.as_posix(),
                    }
                )
            model_inventory.sort(key=lambda entry: (entry["subdir"], entry["name"]))
            if not self.root.joinpath("realm").exists():
                RealmStore.initialize(self.root / "realm").close()
            with _temporary_environment(
                {"BANODOCO_LOCAL_DATA_ROOT": str(self.data_root)}
            ):
                self.daemon = RuntimeDaemon(
                    self.root / "realm",
                    support_root=self.support_root,
                    production_worker_credentials=True,
                ).start()
            self.execution_target = {
                "kind": "machine",
                "id": f"h3-cpu-{self.daemon.instance_id}",
            }
            self.executor_incarnation = f"{PACK_HOST_ACTOR}/{self.daemon.instance_id}"
            self.schema_session = ManagedSchemaSession(
                self.root / "managed-session",
                source_revision,
                source_digest,
                vibecomfy_checkout=self.vibecomfy_root,
            )
            self.readiness_profile_path = self.support_root / "readiness-profile.json"
            approved = json.loads(T9_PROFILE.read_text(encoding="utf-8"))
            facts = {
                "exact": {
                    "interpreter": str(Path(sys.executable).resolve()),
                    "runtime_lock": "sha256:" + "1" * 64,
                    "engine_lock": "sha256:" + "2" * 64,
                    "model_digest": "sha256:" + "3" * 64,
                    "custom_node_digest": "sha256:" + "4" * 64,
                    "driver": "cpu-fixture",
                    "root": "sha256:" + "5" * 64,
                    "port": int(self.schema_session.server_url.rsplit(":", 1)[1]),
                },
                "minimum": {"vram_bytes": 1, "scratch_bytes": 1},
            }
            profile = {
                **{key: value for key, value in approved.items() if key not in {"vibecomfy_candidate", "t9_model_substitute"}},
                "schema_version": "hc03-worker-readiness.v1",
                "status": "ready",
                "verified_facts": facts,
                "verified_facts_digest": _canonical_sha256(facts),
                "runtime": {
                    "runtime_instance_id": self.daemon.instance_id,
                    "comfyui_version": "==0.36.0",
                },
                "launch": {
                    "output_root": str(self.schema_session.output_root),
                    "model_root": {
                        "schema_version": 1,
                        "path": str(self.models_root),
                        "inventory": model_inventory,
                        "inventory_digest": canonical_model_inventory_digest(model_inventory),
                    },
                },
                "vibecomfy_candidate": {
                    "kind": "local_snapshot",
                    "checkout": str(self.vibecomfy_root),
                    "revision": source_revision,
                    "source_content_digest": source_digest,
                    "source_worktree_dirty": False,
                    "installation_mode": "explicit_checkout",
                },
                "vibecomfy_session": self.schema_session.session_binding,
            }
            if self.cpu_model_boundary == APPROVED_CPU_MODEL_BOUNDARY:
                boundary_source = (
                    ASTRID_ROOT / "tests" / "fixtures" / "h3_cpu"
                    / "approved_character_swap_cpu_boundary.py"
                ).resolve()
                if boundary_source.is_symlink() or not boundary_source.is_file():
                    raise RuntimeError("approved Character Swap CPU boundary source is unavailable")
                profile["t9_model_substitute"] = {
                    "approved": True,
                    "mode": "deterministic_cpu_model_boundary_v1",
                    "purpose": CPU_MODEL_BOUNDARY_PURPOSE,
                    "source_path": str(boundary_source),
                    "source_sha256": "sha256:" + hashlib.sha256(boundary_source.read_bytes()).hexdigest(),
                    "evidence_path": str(self.cpu_model_boundary_evidence_path),
                }
            self.readiness_profile_path.write_bytes(_canonical_json(profile))
            self.readiness_profile_hash = "sha256:" + hashlib.sha256(self.readiness_profile_path.read_bytes()).hexdigest()
            owner_token = Path(self.daemon.credential_path).read_text(encoding="utf-8").strip()
            worker_token, worker_credential_path = self.daemon.credentials.provision(
                PACK_HOST_ACTOR,
                list(PACK_HOST_SCOPES),
                metadata={
                    "execution_binding": {
                        "actual": self.execution_target,
                        "verification": {
                            "method": "credential_claim",
                            "evidence_digest": _canonical_sha256(self.execution_target),
                            "verified": True,
                        },
                        "executor_incarnation": self.executor_incarnation,
                    },
                },
            )
            self.owner = AstridClient.open(
                endpoint=str(self.daemon.endpoint),
                credential=owner_token,
                realm_id=str(self.daemon.service.realm["id"]),
                actor_id="owner",
                client_name="h3-av-c11-cpu-e2e",
                client_version="1",
                protocol_version="workspace.v1",
            )
            project = self.owner.projects.create(
                slug="h3-c11-cpu",
                name="H3 C11 CPU integrated evidence",
                idempotency_key="h3-c11-project",
            )
            if not project.ok:
                raise RuntimeError(f"could not create C11 fixture project: {project.error}")
            self.project_id = str(project.data["project_id"])
            self.project_slug = "h3-c11-cpu"
            worker_client = RuntimeProtocolClient(self.daemon.endpoint, worker_token)
            health = worker_client.health()
            health_value = dict(health) if isinstance(health, Mapping) else {
                "status": getattr(health, "status", None),
                "runtime_instance_id": getattr(health, "runtime_instance_id", None),
                "runtime_epoch": getattr(health, "runtime_epoch", None),
                "runtime_session_id": getattr(health, "runtime_session_id", None),
                "schema_digest": getattr(health, "schema_digest", None),
            }
            runtime_instance_id = str(health_value.get("runtime_instance_id") or "")
            runtime_epoch = health_value.get("runtime_epoch")
            schema_digest = str(health_value.get("schema_digest") or "")
            if (
                health_value.get("status") != "ok"
                or not runtime_instance_id
                or isinstance(runtime_epoch, bool)
                or not isinstance(runtime_epoch, int)
                or runtime_epoch < 1
                or schema_digest != SCHEMA_DIGEST
            ):
                raise RuntimeError(f"C11 runtime health is not a valid bootstrap identity: {health_value!r}")
            inventory = active_source_inventory()
            if source_capsule is not None:
                self.source_identity = assert_product_source_identity(capsule=source_capsule)
            launcher_value = {
                "endpoint": str(self.daemon.endpoint),
                "worker_credential_file": str(worker_credential_path.resolve()),
                "worker_actor": PACK_HOST_ACTOR,
                "worker_scopes": PACK_HOST_SCOPES,
                "execution_target": self.execution_target,
                "runtime_instance_id": runtime_instance_id,
                "runtime_session_id": health_value["runtime_session_id"],
                "runtime_epoch": runtime_epoch,
                "schema_digest": schema_digest,
                "source_checkout": str(ASTRID_ROOT.resolve()),
                "source_checkout_digest": (
                    self.source_identity["scoped_source_digest"] if self.source_identity
                    else source_checkout_digest(ASTRID_ROOT)
                ),
                "source_inventory_identity": inventory.identity if inventory.sources else "",
                "readiness_profile_path": str(self.readiness_profile_path.resolve()),
                "readiness_profile_hash": self.readiness_profile_hash,
            }
            self.launcher_value = launcher_value
            host_environment = {
                key: self.schema_session.child_environment[key]
                for key in (
                    "ASTRID_C11_EVIDENCE_PATH",
                    "ASTRID_C11_EXPECTED_PATH",
                    "ASTRID_C11_SCHEMA_PATH",
                    "ASTRID_C11_SESSION_DIR",
                    "PATH",
                )
            }
            host_environment.update(
                {
                    "ASTRID_HOST_READINESS_PROFILE_PATH": str(self.readiness_profile_path),
                    "ASTRID_HOST_READINESS_PROFILE_HASH": self.readiness_profile_hash,
                    "ASTRID_VIBECOMFY_CHECKOUT": str(self.vibecomfy_root),
                    "ASTRID_VIBECOMFY_CANDIDATE_KIND": "local_snapshot",
                    "ASTRID_VIBECOMFY_CANDIDATE_REVISION": source_revision,
                    "ASTRID_VIBECOMFY_CANDIDATE_CONTENT_DIGEST": source_digest,
                    "BANODOCO_LOCAL_DATA_ROOT": str(self.data_root),
                    "BANODOCO_RUNTIME_ENDPOINT": str(self.daemon.endpoint),
                    "BANODOCO_RUNTIME_CREDENTIAL": str(self.daemon.credential_path),
                    "PYTHONPATH": _vibecomfy_child_pythonpath(self.vibecomfy_root),
                }
            )
            with _temporary_environment(host_environment):
                self.host = ensure_pack_host(
                    launcher_value,
                    reconfigure_action="repair C11 fixture host startup",
                )
            if self.host.get("host_status") != "ready":
                raise RuntimeError(f"C11 canonical host bootstrap did not become ready: {self.host!r}")
            self.host_pid = int(self.host["host_pid"])
            self.host_state_path = Path(self.host["host_ready_file"]).resolve().with_name("generic-host.json")
            ready = json.loads(Path(self.host["host_ready_file"]).read_text(encoding="utf-8"))
            attestation = ready.get("identity_attestation")
            if not isinstance(attestation, Mapping) or attestation.get("target") != self.execution_target:
                raise RuntimeError("C11 host readiness target differs from its fixture machine target")
            capacity = self.host.get("effective_capacity")
            lanes = capacity.get("lanes") if isinstance(capacity, Mapping) else None
            if not isinstance(lanes, Mapping) or not {"orchestration", "executor"}.issubset(lanes):
                raise RuntimeError(f"C11 canonical host did not acknowledge both supervised lanes: {capacity!r}")
            self.claimed_lanes.extend(("orchestration", "executor"))
        except BaseException:
            try:
                self.close()
            except BaseException:
                pass
            raise
        finally:
            self._restore_parent_state()

    def _restore_parent_state(self) -> None:
        """Restore the exact parent import and environment identities."""
        sys.path[:] = self._prior_sys_path
        prior_names = set(self._prior_vibecomfy_modules)
        for name in tuple(sys.modules):
            if (
                name == "vibecomfy" or name.startswith("vibecomfy.")
            ) and name not in prior_names:
                sys.modules.pop(name, None)
        for name, module in self._prior_vibecomfy_modules.items():
            sys.modules[name] = module
        for key, value in self._prior_environment.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def close(self) -> None:
        if self._closed:
            self._restore_parent_state()
            return
        self._closed = True
        cleanup_errors: list[BaseException] = []
        try:
            host_pid = self.host_pid
            if host_pid is not None:
                try:
                    state = json.loads(self.host_state_path.read_text(encoding="utf-8")) if self.host_state_path else {}
                    if int(state.get("pid")) == host_pid:
                        try:
                            if os.getpgid(host_pid) == host_pid:
                                os.killpg(host_pid, signal.SIGTERM)
                            else:
                                os.kill(host_pid, signal.SIGTERM)
                        except (OSError, ProcessLookupError):
                            pass
                        deadline = time.monotonic() + 8
                        while time.monotonic() < deadline:
                            try:
                                os.kill(host_pid, 0)
                            except OSError:
                                break
                            time.sleep(0.05)
                        else:
                            try:
                                if os.getpgid(host_pid) == host_pid:
                                    os.killpg(host_pid, signal.SIGKILL)
                                else:
                                    os.kill(host_pid, signal.SIGKILL)
                            except (OSError, ProcessLookupError):
                                pass
                except (OSError, TypeError, ValueError, json.JSONDecodeError):
                    pass
        except BaseException as exc:
            cleanup_errors.append(exc)
        try:
            if self.schema_session is not None:
                self.schema_session.close()
        except BaseException as exc:
            cleanup_errors.append(exc)
        try:
            if self.daemon is not None:
                self.daemon.stop()
        except BaseException as exc:
            cleanup_errors.append(exc)
        finally:
            self._restore_parent_state()
        if cleanup_errors:
            raise RuntimeError("C11 fixture cleanup was incomplete") from cleanup_errors[0]

    def create_project_asset(self, path: Path, *, idempotency_key: str) -> str:
        imported = self.owner.media.import_file(
            project=self.project_id,
            realm="managed_local",
            path=path,
            idempotency_key=idempotency_key,
        )
        if not imported.ok:
            raise RuntimeError(f"could not import C11 managed input {path.name}: {imported.error}")
        return str(imported.data["object_id"])

    def read_cpu_model_boundary_evidence(self) -> dict[str, Any]:
        if self.cpu_model_boundary != APPROVED_CPU_MODEL_BOUNDARY:
            raise RuntimeError("the Runtime was not configured with the approved CPU model boundary")
        try:
            value = json.loads(self.cpu_model_boundary_evidence_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeError("approved CPU model-boundary evidence is unavailable") from exc
        if not isinstance(value, dict):
            raise RuntimeError("approved CPU model-boundary evidence is malformed")
        return value

    def run_public_transform(self, request_path: Path, bundle_path: Path, *, out: Path, key: str):
        request = normalize_request(json.loads(request_path.read_text(encoding="utf-8"))).value
        if int(request.get("version", 0)) == 1:
            source_binding = str(request["source"]["asset"])
        else:
            timeline_videos = [
                row for row in request.get("media", [])
                if isinstance(row, Mapping)
                and row.get("role") == "timeline"
                and row.get("modality") == "video"
            ]
            video_media = [
                row for row in request.get("media", [])
                if isinstance(row, Mapping) and row.get("modality") == "video"
            ]
            if len(timeline_videos) == 1:
                source_binding = str(timeline_videos[0]["asset"])
            elif len(video_media) == 1:
                source_binding = str(video_media[0]["asset"])
            else:
                raise AssertionError("C11 request does not identify exactly one source video")
        source_records = [
            record for record in read_archive(bundle_path).manifest["assets"]
            if record["binding"] == source_binding
        ]
        if len(source_records) != 1 or self.schema_session is None:
            raise AssertionError("C11 requires one bundled source and a managed session")
        source_record = source_records[0]
        self.schema_session.expected_path.write_bytes(_canonical_json({
            "basename": Path(source_record["member"]).name,
            "sha256": source_record["sha256"],
            "size": source_record["size"],
            "generated_seconds": (
                plan_continuation(
                    source_end=float(request["source"]["range"][1]),
                    output_duration=float(request["output"]["duration"]),
                ).expected_graph_output_duration
                if int(request.get("version", 0)) == 1 else 13
            ),
        }))
        safe_key = key.replace(":", "-")
        request_object_id = self.create_project_asset(
            request_path,
            idempotency_key=f"{safe_key}-request",
        )
        bundle_object_id = self.create_project_asset(
            bundle_path,
            idempotency_key=f"{safe_key}-input-bundle",
        )
        return self.owner.invoke_result(
            "h3_av.transform",
            kind="orchestrator",
            project=self.project_id,
            out=out,
            execution_request={
                "schema_version": 1,
                "target": self.execution_target,
                "limits": {"max_queue_seconds": 120, "max_runtime_seconds": 1200},
            },
            inputs={
                "request": {
                    "object_id": request_object_id,
                    "digest": request_object_id,
                    "filename": request_path.name,
                    "required": True,
                },
                "input_bundle": {
                    "object_id": bundle_object_id,
                    "digest": bundle_object_id,
                    "filename": bundle_path.name,
                    "required": True,
                },
            },
            idempotency_key=key,
            wait=True,
            timeout_seconds=(
                600
                if self.cpu_model_boundary == APPROVED_CPU_MODEL_BOUNDARY
                else 180
            ),
            poll_seconds=0.05,
        )


__all__ = [
    "APPROVED_CPU_MODEL_BOUNDARY",
    "ASTRID_ROOT",
    "H3CpuRuntime",
    "RUNTIME_ROOT",
    "VIBECOMFY_ROOT",
]
