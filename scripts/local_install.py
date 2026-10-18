#!/usr/bin/env python3
"""Build the selected local sources, install a private pair, and launch Astrid App.

This is a local source installation, not the published release qualification.
Only a new installation root is accepted; existing realms are never upgraded.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import tarfile
import time
import urllib.error
import urllib.request
import uuid
import zipfile


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def run(argv: list[str | Path], *, cwd: Path, env: dict[str, str], capture: bool = False) -> str:
    result = subprocess.run([str(x) for x in argv], cwd=cwd, env=env, check=True,
                            text=True, stdout=subprocess.PIPE if capture else None)
    return result.stdout.strip() if capture else ""


def safe_path(value: str | Path) -> Path:
    path = Path(value).expanduser().absolute()
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError(f"path must not contain a symlink: {path}")
    return path


def source_identity(path: Path, env: dict[str, str]) -> dict[str, str]:
    if Path(run(["git", "rev-parse", "--show-toplevel"], cwd=path, env=env, capture=True)).resolve() != path.resolve():
        raise ValueError(f"source path must be a repository root: {path}")
    if run(["git", "status", "--porcelain", "--untracked-files=normal"], cwd=path, env=env, capture=True):
        raise ValueError(f"source checkout must be clean; commit or preserve your changes first: {path}")
    return {"path": str(path), "sha": run(["git", "rev-parse", "HEAD"], cwd=path, env=env, capture=True)}


def media_tools(env: dict[str, str], overrides: dict[str, str | None]) -> dict[str, dict[str, str]]:
    """Resolve and measure the actual local decoders before creating any state."""
    tools = {}
    for name in ("ffmpeg", "ffprobe"):
        selected = overrides.get(name) or shutil.which(name, path=env.get("PATH"))
        if not selected:
            raise ValueError(f"{name} is required for media import, including images. Install FFmpeg (for example: brew install ffmpeg), or supply --{name} /absolute/path/to/{name}")
        path = Path(selected).expanduser().resolve(strict=True)
        if not path.is_file() or not os.access(path, os.X_OK):
            raise ValueError(f"{name} must identify an executable file: {path}")
        version = run([path, "-version"], cwd=path.parent, env=env, capture=True).splitlines()[0]
        if not version.startswith(name + " version "):
            raise ValueError(f"{name} did not report its expected version identity: {path}")
        tools[name] = {"path": str(path), "sha256": digest(path), "version": version}
    return tools



def agent_tools(env: dict[str, str], overrides: dict[str, str | None]) -> dict[str, dict[str, str]]:
    """Bind existing OMP/Bun resources without changing the user's global profile."""
    paths = {}
    for name in ("bun", "omp"):
        selected = overrides.get(name) or shutil.which(name, path=env.get("PATH"))
        if not selected:
            raise ValueError(f"{name} is required for Astrid chat. Install the supported Bun/OMP prerequisites, or supply --{name} /absolute/path/to/{name}")
        path = Path(selected).expanduser().resolve(strict=True)
        if not path.is_file() or not os.access(path, os.X_OK):
            raise ValueError(f"{name} must identify an executable file: {path}")
        paths[name] = path
    probe_env = {**env, "PATH": str(paths["bun"].parent) + os.pathsep + env.get("PATH", "")}
    return {name: {"path": str(path), "sha256": digest(path),
                   "version": run([path, "--version"], cwd=path.parent, env=probe_env, capture=True)}
            for name, path in paths.items()}



def check_acp(url: str, root: Path) -> None:
    """Exercise the actual app bridge with one explicitly documented text turn."""
    def request(route: str, body=None, method=None):
        payload = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url + "/api/astrid/acp" + route, data=payload,
                                     method=method, headers={"Content-Type": "application/json", "Origin": url})
        with urllib.request.urlopen(req, timeout=120) as response:
            return json.load(response)
    connection = request("/connect", {})
    connection_id = connection["connection_id"]
    try:
        project = json.loads((root / "runtime-receipt.json").read_text())["project_create_readback"]["project_id"]
        state = request(f"/projects/{project}/chat/sessions", {
            "connection_id": connection_id, "mode": "new", "operation_id": str(uuid.uuid4())})
        session_id = state["operation_session_id"]
        prompted = request(f"/projects/{project}/chat/prompt", {
            "connection_id": connection_id, "session_id": session_id,
            "prompt": [{"type": "text", "text": "Reply only OK."}]})
        events = request(f"/{connection_id}/events")
        chunks = [event.get("params", {}).get("update", {}).get("content", {}).get("text", "")
                  for event in events.get("notifications", [])
                  if event.get("params", {}).get("update", {}).get("sessionUpdate") == "agent_message_chunk"]
        text = "".join(chunks).strip()
        if events.get("disconnected") or text != "OK":
            raise RuntimeError("ACP smoke did not complete the requested text-only reply; check provider authentication")
        (root / "acp-receipt.json").write_text(json.dumps({
            "initialize": connection["initialize"], "session_id": session_id,
            "prompt": "Reply only OK.", "assistant_text": text, "result": prompted,
            "disconnected": False}, indent=2) + "\n")
        print("Fresh ACP connect, session creation, and text reply passed", flush=True)
    finally:
        request(f"/{connection_id}", method="DELETE")



def owned_group_alive(group: int) -> bool:
    """Darwin can report EPERM while an exiting group has no signalable member."""
    try:
        os.killpg(group, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        # EPERM alone is not evidence of completion. Inspect only group IDs
        # and process states; zombies cannot execute or hold server sockets.
        rows = subprocess.check_output(["/bin/ps", "-axo", "pgid=,stat="], text=True)
        return any(int(fields[0]) == group and not fields[1].startswith("Z")
                   for line in rows.splitlines() if len(fields := line.split()) == 2)


def check_app(argv: list[str], *, cwd: Path, env: dict[str, str], root: Path, url: str,
              runtime_instance_id: str, timeout: float = 30.0) -> None:
    """Exercise actual local startup, then retire only this owned process group."""
    log = root / "app-startup.log"
    with log.open("w") as output:
        process = subprocess.Popen(argv, cwd=cwd, env=env, stdout=output, stderr=subprocess.STDOUT,
                                   start_new_session=True)
        try:
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError(f"app startup exited with {process.returncode}; see {log}")
                try:
                    with urllib.request.urlopen(url, timeout=1) as response:
                        if response.status != 200:
                            raise ValueError("app did not return HTTP 200")
                    with urllib.request.urlopen(url + "/api/runtime/v1/health", timeout=1) as response:
                        health = json.load(response)
                    if health.get("status") != "ok" or health.get("runtime_instance_id") != runtime_instance_id:
                        raise ValueError("app proxy reached a different or unhealthy runtime")
                    (root / "app-receipt.json").write_text(json.dumps({
                        "url": url, "status": "ready", "runtime_instance_id": runtime_instance_id,
                        "proxy_health": health, "process_group": process.pid,
                    }, indent=2) + "\n")
                    print("Fresh app startup and Runtime proxy passed", flush=True)
                    break
                except (OSError, ValueError, urllib.error.URLError):
                    time.sleep(0.2)
            else:
                raise RuntimeError(f"app startup did not become ready within {timeout}s; see {log}")
            check_acp(url, root)
        finally:
            # Every child of dev:local belongs to this new group; the user app
            # and separately managed Runtime do not belong to it.
            for sig, wait in ((signal.SIGINT, 10), (signal.SIGTERM, 5), (signal.SIGKILL, 5)):
                try:
                    os.killpg(process.pid, sig)
                except ProcessLookupError:
                    break
                except PermissionError:
                    if not owned_group_alive(process.pid):
                        break
                    raise
                try:
                    process.wait(timeout=wait)
                except subprocess.TimeoutExpired:
                    continue
                if not owned_group_alive(process.pid):
                    break
            else:
                raise RuntimeError("could not retire the proof app process group")


def snapshot(identity: dict[str, str], target: Path, env: dict[str, str]) -> None:
    target.mkdir(parents=True)
    archive = target.parent / (target.name + ".tar")
    with archive.open("wb") as output:
        subprocess.run(["git", "archive", "--format=tar", identity["sha"]],
                       cwd=identity["path"], env=env, stdout=output, check=True)
    with tarfile.open(archive) as bundle:
        bundle.extractall(target, filter="data")
    archive.unlink()
    # A build directory is generated input, even if an older commit tracked it.
    shutil.rmtree(target / "build", ignore_errors=True)


def wheel_parity(wheel: Path, source: Path, prefixes: tuple[str, ...]) -> dict[str, object]:
    with zipfile.ZipFile(wheel) as bundle:
        members = [n for n in bundle.namelist() if n.startswith(prefixes) and not n.endswith("/")]
        if not members:
            raise ValueError(f"wheel contains no expected package: {wheel}")
        wrong = [n for n in members if not (source / n).is_file() or bundle.read(n) != (source / n).read_bytes()]
    if wrong:
        raise ValueError(f"wheel does not match selected source: {wheel.name}: {wrong}")
    expected_python = {str(p.relative_to(source)) for prefix in prefixes
                       for p in (source / prefix).rglob("*.py")
                       if not {"tests", "golden", "__pycache__"}.intersection(p.relative_to(source).parts)}
    missing = expected_python - set(members)
    if missing:
        raise ValueError(f"wheel omits source modules: {wheel.name}: {sorted(missing)}")
    return {"filename": wheel.name, "sha256": digest(wheel), "matched_members": len(members)}


# This code executes only inside the noneditable environment, from its unrelated
# working directory. Runtime remains the sole owner of creation and discovery.
START_RUNTIME = r'''
import dataclasses, importlib, importlib.metadata, json, pathlib, sys
from astrid.runtime_cli import RuntimeCLI, validate_selected_workspace
from astrid.core.execution.generic_host import source_checkout_digest
from astrid_runtime_client import WorkspaceClient
from astrid_runtime_client.contract_metadata import SCHEMA_DIGEST
from runtime_protocol.store import SCHEMA_VERSION
root=pathlib.Path(sys.argv[1]); identity=json.loads((root/'install.json').read_text())
site=pathlib.Path(importlib.import_module('astrid').__file__).parent.parent
origins={name:importlib.import_module(name).__file__ for name in ('astrid','astrid_runtime','runtime_protocol','astrid_runtime_client')}
for name,origin in origins.items():
    if not pathlib.Path(origin).is_relative_to(site): raise RuntimeError('foreign installed origin: '+name)
for name in ('astrid','astrid-runtime'):
    direct=json.loads(importlib.metadata.distribution(name).read_text('direct_url.json'))
    if direct.get('dir_info',{}).get('editable'): raise RuntimeError('editable distribution')
profile={'profile':'astrid','runtime_checkout':str(site),'source_checkout':str(site),
         'runtime_environment':sys.prefix,'protocol_version':'workspace.v1',
         'schema_version':'workspace-schema-v1','source_digest':source_checkout_digest(site)}
manifest=root/'profile.json'; manifest.write_text(json.dumps(profile,indent=2)+'\n')
cli=RuntimeCLI(command=[str(pathlib.Path(sys.prefix)/'bin/astrid-runtime')])
args={'support_root':root/'support','realm_root':root/'support/runtime/realms/local',
      'expected_realm_id':identity['workspace_id']}
observed=cli.inspect(support_root=args['support_root'])
if observed.data.get('state')!='workspace_missing': raise RuntimeError('fresh support root is already selected')
created=cli.configure('create',support_root=args['support_root'],realm_root=args['realm_root'],
                      realm_id=identity['workspace_id'],display_name='Local workspace',source_manifest=manifest)
if not created.ok: raise RuntimeError(created.data)
validate_selected_workspace(created.data,expected_support_root=args['support_root'],
                            expected_realm_root=args['realm_root'],expected_realm_id=args['expected_realm_id'])
started=cli.up(**args)
if not started.ok: raise RuntimeError(started.data)
credential=json.loads(pathlib.Path(started.data['credential_file']).read_text())
client=WorkspaceClient(started.data['endpoint'],credential['token'])
health=client.health(); handshake=client.handshake('local-install','1',['projects:read'])
if health.status!='ok' or health.schema_digest!=SCHEMA_DIGEST or handshake.realm_id!=identity['workspace_id']:
    raise RuntimeError('runtime/client identity mismatch')
projects,cursor=client.list_projects()
if projects: raise RuntimeError('new workspace unexpectedly contains projects')
inspection=cli.inspect(**args)
if not inspection.ok or inspection.data['inspection']['schema_version']!=SCHEMA_VERSION:
    raise RuntimeError('new runtime schema is unhealthy')
receipt={'start':started.data,'health':dataclasses.asdict(health),'handshake':dataclasses.asdict(handshake),
         'schema_version':SCHEMA_VERSION,'projects':[],'installed_origins':origins,'interpreter':sys.executable}
(root/'runtime-receipt.json').write_text(json.dumps(receipt,indent=2)+'\n')
print('Runtime ready: '+started.data['endpoint'])
'''

CHECK_PROJECT = r'''
import dataclasses, hashlib, json, pathlib, sys, uuid
from PIL import Image
from astrid_runtime_client import WorkspaceClient
root=pathlib.Path(sys.argv[1]); receipt=json.loads((root/'runtime-receipt.json').read_text())
credential=json.loads(pathlib.Path(receipt['start']['credential_file']).read_text())
client=WorkspaceClient(receipt['start']['endpoint'],credential['token'])
created=client.create_project('Installation check',slug='installation-check',idempotency_key=str(uuid.uuid4()))
project=client.get_project(created['project_id'])
projects,_=client.list_projects()
if len(projects)!=1 or projects[0].project_id!=project.project_id:
    raise RuntimeError('created project did not read back from the same runtime')
receipt['project_create_readback']=dataclasses.asdict(project)
fixture=root/'work/image-import-check.png'
Image.new('RGB',(32,24),(64,128,192)).save(fixture)
data=fixture.read_bytes()
imported=client.import_project_media(project.project_id,data,media_type='image/png',
    filename=fixture.name,width=32,height=24,idempotency_key=str(uuid.uuid4()))
readback=client.get_project_media_import(project.project_id,imported['import_operation_id'])
if imported['status']!='completed' or readback['asset_id']!='sha256:'+hashlib.sha256(data).hexdigest():
    raise RuntimeError('image import did not complete and read back the supplied image')
receipt['image_import_readback']=readback
(root/'runtime-receipt.json').write_text(json.dumps(receipt,indent=2)+'\n')
print('Fresh project create/readback passed')
print('Fresh PNG import/readback passed')
'''


def install(args: argparse.Namespace) -> None:
    if sys.platform != "darwin" or sys.version_info[:2] not in {(3, 11), (3, 12)}:
        raise ValueError("local installation is supported here on macOS with CPython 3.11 or 3.12")
    root = safe_path(args.root)
    sources = {"astrid": safe_path(args.astrid_root), "runtime": safe_path(args.runtime_root), "app": safe_path(args.app_root)}
    for source in sources.values():
        if root.is_relative_to(source) or source.is_relative_to(root):
            raise ValueError("installation root must be outside every source checkout")
    if root.exists() and (not root.is_dir() or any(root.iterdir())):
        raise ValueError("installation root must be absent or empty; existing installs are never replaced")
    ports = (args.port, args.proxy_port, args.acp_port)
    if len(set(ports)) != 3 or any(not 1 <= port <= 65535 for port in ports):
        raise ValueError("app, proxy, and ACP ports must be distinct valid ports")
    for port in ports:
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", port))
    # Preserve HOME unchanged; isolate application state and caches, not the
    # person's account or installed agent harnesses. Provider keys are unused.
    env = {k: v for k, v in os.environ.items() if k in {"PATH", "HOME", "LANG", "LC_ALL", "TMPDIR", "SYSTEMROOT"}}
    node = run(["node", "--version"], cwd=sources["app"], env=env, capture=True)
    if tuple(int(n) for n in node.lstrip("v").split(".")[:2]) < (20, 19):
        raise ValueError("Astrid App requires Node 20.19 or newer")
    decoders = media_tools(env, {"ffmpeg": args.ffmpeg, "ffprobe": args.ffprobe})
    agents = agent_tools(env, {"omp": args.omp, "bun": args.bun})
    identity = {name: source_identity(path, env) for name, path in sources.items()}
    root.mkdir(parents=True, exist_ok=True)
    root.chmod(0o700)
    work = root / "work"; work.mkdir()
    env.update({"PIP_CACHE_DIR": str(root / "cache/pip"), "npm_config_cache": str(root / "cache/npm"),
                "XDG_CACHE_HOME": str(root / "cache"), "XDG_CONFIG_HOME": str(root / "config"),
                "XDG_STATE_HOME": str(root / "state"), "XDG_DATA_HOME": str(root / "data"),
                "ASTRID_STATE_HOME": str(root / "state/astrid"),
                "ASTRID_SOURCE_STATE": str(root / "state/pack-sources.json"),
                "ASTRID_SOURCE_DATA": str(root / "data/packs"), "ASTRID_NO_NUDGE": "1",
                "ASTRID_SUPPORT_ROOT": str(root / "support"), "ASTRID_RUNTIME_DATA_ROOT": str(root / "support"),
                "ASTRID_WORKSPACE_DISCOVERY": str(root / "support/runtime/discovery.json"),
                "ASTRID_HOST_EXECUTION_PROFILE": "installed", "PYTHONNOUSERSITE": "1", "PYTHONSAFEPATH": "1"})
    copies = {name: root / "sources" / name for name in sources}
    for name, copy in copies.items():
        snapshot(identity[name], copy, env)
    locks = {name: copies["astrid"] / "requirements" / (name + ".lock") for name in ("build", "runtime")}
    wheels = root / "wheelhouse"; wheels.mkdir()
    cache = safe_path(args.wheelhouse) if args.wheelhouse else wheels
    build_python = root / "build-env/bin/python"
    python = root / "venv/bin/python"
    run([sys.executable, "-m", "venv", root / "build-env"], cwd=work, env=env)
    run([sys.executable, "-m", "venv", root / "venv"], cwd=work, env=env)
    # The Runtime decodes through PATH. Bind only the measured decoders into
    # its private executable directory, even when the caller's PATH is narrow.
    for name, tool in {**decoders, **agents}.items():
        (root / "venv/bin" / name).symlink_to(tool["path"])
    run([build_python, "-m", "pip", "install", "--require-hashes", "-r", locks["build"]], cwd=work, env=env)
    products = []
    for name, pattern, prefixes in (("astrid", "astrid-*.whl", ("astrid/", "astrid_runtime_client/")),
                                    ("runtime", "astrid_runtime-*.whl", ("astrid_runtime/", "runtime_protocol/"))):
        run([build_python, "-m", "build", "--wheel", "--no-isolation", "--outdir", wheels, copies[name]], cwd=work, env=env)
        selected = list(wheels.glob(pattern))
        if len(selected) != 1:
            raise ValueError(f"expected exactly one {name} wheel")
        products.append(wheel_parity(selected[0], sources[name], prefixes))
    # Compare the two authoritative generated contracts before starting either
    # server or app. A dependency cache cannot substitute a stale product wheel.
    if (copies["astrid"] / "astrid_runtime_client/contract_metadata.py").read_bytes() != (copies["runtime"] / "runtime_protocol/contract_metadata.py").read_bytes():
        raise ValueError("Astrid and Runtime generated contracts do not match")
    if not args.wheelhouse:
        run([python, "-m", "pip", "download", "--require-hashes", "--only-binary=:all:", "--dest", cache, "-r", locks["runtime"]], cwd=work, env=env)
    run([python, "-m", "pip", "install", "--no-index", "--require-hashes", "--find-links", cache, "-r", locks["runtime"]], cwd=work, env=env)
    run([python, "-m", "pip", "install", "--no-index", "--no-deps", *[wheels / p["filename"] for p in products]], cwd=work, env=env)
    run([python, "-m", "pip", "check"], cwd=work, env=env)
    env.update({"PATH": str(root / "venv/bin") + os.pathsep + env["PATH"],
                "ASTRID_PACK_HOST_PYTHON": str(python), "ASTRID_RUNTIME_CLI": str(root / "venv/bin/astrid-runtime"),
                "ASTRID_RUNTIME_SOURCE_MANIFEST": str(root / "profile.json"),
                "ASTRID_ACP_COMMAND": str(root / "venv/bin/astrid"),
                "OMP_BIN": str(root / "venv/bin/omp"),
                "ASTRID_CHECKOUT": str(copies["astrid"]),
                "ASTRID_PUBLIC_CHECKOUT": str(copies["app"] / "vendor/astrid-build-inputs/public"),
                "PORT": str(args.port), "VITE_ASTRID_DYNAMIC_PROXY_PORT": str(args.proxy_port),
                "VITE_ASTRID_ACP_BRIDGE_PORT": str(args.acp_port)})
    receipt = {"schema": "astrid.local-source-install.v1", "sources": identity, "wheels": products,
               "locks": {name: digest(path) for name, path in locks.items()},
               "workspace_id": str(uuid.uuid4()), "root": str(root), "python": sys.version,
               "node": node, "npm": run(["npm", "--version"], cwd=work, env=env, capture=True),
               "app_lock_sha256": digest(copies["app"] / "package-lock.json"),
               "url": f"http://localhost:{args.port}", "optional_capabilities": "rendering and creative provider credentials require separate qualification; chat reuses existing provider authentication"}
    receipt["tools"] = {name: {"path": str(Path(shutil.which(name)).resolve()),
                                "sha256": digest(Path(shutil.which(name)).resolve())}
                        for name in ("node", "npm", "git")}
    receipt["tools"]["python"] = {"path": sys.executable, "sha256": digest(Path(sys.executable))}
    receipt["tools"].update({name: {**tool, "invoked_path": str(root / "venv/bin" / name)} for name, tool in {**decoders, **agents}.items()})
    (root / "install.json").write_text(json.dumps(receipt, indent=2) + "\n")
    script = ["#!/bin/sh", "unset PYTHONPATH PYTHONHOME ASTRID_PACKS_PATH ASTRID_WORKSPACE_ENDPOINT ASTRID_WORKSPACE_CREDENTIAL ASTRID_SDK_ENABLE_ROOT_FALLBACK"]
    script += [f"export {k}={shlex.quote(v)}" for k, v in env.items() if k not in {"HOME", "LANG", "LC_ALL", "TMPDIR", "SYSTEMROOT"}]
    script += [f"cd {shlex.quote(str(work))}", 'exec "$@"']
    wrapper = root / "with-env"; wrapper.write_text("\n".join(script) + "\n"); wrapper.chmod(0o700)
    run(["npm", "ci"], cwd=copies["app"], env=env)
    run(["npm", "run", "build"], cwd=copies["app"], env=env)
    try:
        run([python, "-I", "-c", START_RUNTIME, root], cwd=work, env=env)
        run(["npm", "run", "dev:local", "--", "--check"], cwd=copies["app"], env=env)
        if args.check_only:
            run([python, "-I", "-c", CHECK_PROJECT, root], cwd=work, env=env)
            runtime = json.loads((root / "runtime-receipt.json").read_text())
            check_app(["npm", "run", "dev:local"], cwd=copies["app"], env=env, root=root,
                      url=receipt["url"], runtime_instance_id=runtime["health"]["runtime_instance_id"])
        print(f"Installed local workspace: {root}\nApp: {receipt['url']}", flush=True)
        if not args.check_only:
            run(["npm", "run", "dev:local"], cwd=copies["app"], env=env)
    finally:
        # This lifecycle only targets the explicitly created private root.
        subprocess.run([str(python), "-I", "-m", "astrid_runtime", "down", "--profile", "astrid", "--data-root", str(root / "support"), "--json"], cwd=work, env=env, check=False)


def main() -> int:
    repo = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, help="new installation directory, outside all checkouts")
    parser.add_argument("--astrid-root", default=str(repo))
    parser.add_argument("--runtime-root", required=True)
    parser.add_argument("--app-root", required=True)
    parser.add_argument("--wheelhouse", help="optional cached locked dependency wheels; product wheels are always rebuilt")
    parser.add_argument("--ffmpeg", help="explicit FFmpeg executable (otherwise resolve from PATH)")
    parser.add_argument("--ffprobe", help="explicit FFprobe executable (otherwise resolve from PATH)")
    parser.add_argument("--omp", help="existing supported OMP executable (otherwise resolve from PATH)")
    parser.add_argument("--bun", help="existing Bun executable (otherwise resolve from PATH)")
    parser.add_argument("--port", type=int, default=2222)
    parser.add_argument("--proxy-port", type=int, default=17336)
    parser.add_argument("--acp-port", type=int, default=17335)
    parser.add_argument("--check-only", action="store_true", help="install, build, verify app binding, then stop the new runtime")
    args = parser.parse_args()
    try:
        install(args)
    except (ValueError, OSError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"Local install failed: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
