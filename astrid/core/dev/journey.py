"""Real-process merge gate for a stopped film workspace (``astrid dev journey``).

Run only on a machine allowed to start Runtime, Remotion and process groups.
No Runtime or renderer layer is mocked here.  The reference is never opened for
writing; workspace adoption receives a private filesystem clone.

``--tier smoke`` renders a short excerpt and proves the host-lifecycle fixes keep
holding: an outdated idle host replaces itself, a host started inside a capacity job
outlives it, duplicate PATH entries reuse the host, a kill -9 of the idle host recovers
by itself, and a kill -9 mid-render leaves no orphaned render group.  ``--tier full``
adds the real user journey (Codex image, sprites, timeline edit/publish, full render).
"""
from __future__ import annotations

import argparse
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import uuid
from typing import Any


# No phase is judged by how long it takes: durations and machine load are recorded for information.
# A command is stopped only when it is genuinely hung, with no exit within a ceiling many times its
# usual duration on a loaded Mac (renders: ~1-2 min usual, 30 min ceiling).
HANG_SECONDS = {
    "reference": 60, "adopt": 900, "up": 300, "first_open": 900, "doctor": 300,
    "render_1": 1800, "source_change": 60, "replace_host": 1800, "heavy_survival": 120,
    "render_2": 1800, "path_reuse": 600, "kill_idle": 900, "kill_render": 1800, "render_after": 1800,
    "down": 300, "cleanup": 120,
    # full tier only
    "image": 3600, "sprites": 1800, "edit": 1800, "render_full": 7200,
}
SMOKE_PHASES = ("reference", "adopt", "up", "first_open", "doctor", "render_1", "source_change",
                "replace_host", "heavy_survival", "render_2", "path_reuse", "kill_idle", "kill_render",
                "render_after", "down", "cleanup")
TERMINAL_TASK_STATES = {"succeeded", "completed", "failed", "cancelled", "canceled", "expired"}
# Resource growth is behaviour (a leak), not time: these stay assertions.
DISK_BUDGET = 4 * 1024**3
RECEIPT_BUDGET = 20 * 1024**2
LOG_BUDGET = 100 * 1024**2


class JourneyFailure(RuntimeError):
    def __init__(self, phase: str, message: str) -> None:
        super().__init__(f"{phase}: {message}")
        self.phase = phase


def select_reference_realm(reference: Path) -> tuple[str, Path]:
    """Validate the stopped reference and return its realm; never copy it.

    `workspace adopt` clones the realm itself while holding the source's
    owner fence, so the journey doesn't clone a second time.
    """
    catalog = json.loads((reference / "runtime" / "catalog.json").read_text())
    realm_id = catalog.get("selected_realm_id")
    rows = catalog.get("realms") or []
    if not isinstance(realm_id, str) or len(rows) != 1 or rows[0].get("realm_id") != realm_id:
        raise JourneyFailure("reference", "reference must select exactly one realm")
    source = reference / "runtime" / "realms" / realm_id
    if (source.is_symlink() or not source.resolve().is_relative_to(reference)
            or source.resolve() != Path(rows[0]["data_root"]).resolve()
            or not (source / "realm.sqlite3").is_file()):
        raise JourneyFailure("reference", "catalog realm is outside the reference or has no database")
    return realm_id, source


def _ps_rows() -> list[dict[str, Any]]:
    ps = subprocess.run(["ps", "-axo", "pid=,ppid=,pgid=,command="], capture_output=True, text=True, timeout=8)
    if ps.returncode:
        raise JourneyFailure("cleanup", f"ps failed: {ps.stderr.strip()}")
    result: list[dict[str, Any]] = []
    for line in ps.stdout.splitlines():
        parts = line.strip().split(None, 3)
        if len(parts) != 4:
            continue
        pid, ppid, pgid, command = parts
        if pid.isdigit() and ppid.isdigit() and pgid.isdigit():
            result.append({"pid": int(pid), "ppid": int(ppid), "pgid": int(pgid), "command": command})
    return result


def _processes(root: Path) -> list[dict[str, Any]]:
    rows = _ps_rows()
    owned = {row["pid"] for row in rows if str(root) in row["command"] and
             any(token in row["command"] for token in ("runtime_protocol", "generic_host", "node"))}
    while True:
        expanded = owned | {row["pid"] for row in rows if row["ppid"] in owned}
        if expanded == owned:
            break
        owned = expanded
    return [row for row in rows if row["pid"] in owned]


def _reap_owned_groups(root: Path) -> list[dict[str, Any]]:
    """Signal only groups whose every listed member is rooted in our temp realm."""
    rows = _processes(root)
    if not rows:
        return []
    all_ps = _ps_rows()
    groups = {row["pgid"] for row in rows}
    safe = set(groups)
    owned_pids = {row["pid"] for row in rows}
    for row in all_ps:
        if row["pgid"] in groups and row["pid"] not in owned_pids:
            safe.discard(row["pgid"])
    for group in safe:
        if group != os.getpgrp():
            try:
                os.killpg(group, signal.SIGTERM)
            except ProcessLookupError:
                pass
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        remaining = _processes(root)
        if not remaining:
            return []
        time.sleep(0.2)
    return _processes(root)


def _files(root: Path):
    """Files under root; directories the host removes while we walk (attempt scratch) are skipped, not errors."""
    for folder, _dirs, names in os.walk(root):
        for name in names:
            path = Path(folder) / name
            if path.is_file():
                yield path


def _tree_bytes(root: Path, predicate) -> int:
    total = 0
    for path in _files(root):
        if predicate(path):
            try:
                total += path.stat().st_size
            except FileNotFoundError:
                pass
    return total


def machine_load() -> dict[str, Any]:
    """1-minute load and (macOS) memory pressure, recorded beside each phase's duration."""
    load = round(os.getloadavg()[0], 2)
    pressure = None
    if sys.platform == "darwin":
        try:
            level = subprocess.run(["sysctl", "-n", "kern.memorystatus_vm_pressure_level"], capture_output=True,
                                   text=True, timeout=5, check=False).stdout.strip()
            pressure = {"1": "normal", "2": "warn", "4": "critical"}.get(level, level or None)
        except (OSError, subprocess.SubprocessError):
            pressure = None
    return {"load_1m": load, "memory_pressure": pressure}


class Runner:
    def __init__(self, root: Path, summary: Path, tier: str = "smoke") -> None:
        self.root, self.summary, self.tier = root, summary, tier
        self.started = time.monotonic()
        self.loads: dict[str, dict[str, Any]] = {}
        self.free_before = shutil.disk_usage(root).free
        self.disk_high_water = 0
        self.phases: dict[str, float] = {}
        self.details: dict[str, Any] = {}

    def sample(self) -> None:
        used = max(0, self.free_before - shutil.disk_usage(self.root).free)
        self.disk_high_water = max(self.disk_high_water, used)
        if self.disk_high_water > DISK_BUDGET:
            raise JourneyFailure("disk_budget", f"high water {self.disk_high_water} > {DISK_BUDGET} bytes")

    def phase(self, name: str, operation):
        started = time.monotonic()
        try:
            result = operation()
            self.sample()
        except JourneyFailure:
            raise
        except Exception as exc:
            raise JourneyFailure(name, str(exc)) from exc
        finally:
            elapsed = time.monotonic() - started
            self.phases[name] = round(elapsed, 3)
            self.loads[name] = machine_load()
        return result

    def command(self, phase: str, argv: list[str], env: dict[str, str], *, json_result: bool = True):
        process = subprocess.Popen(argv, cwd=Path(__file__).resolve().parents[3], env=env,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                   start_new_session=True)
        hang_limit = HANG_SECONDS[phase]
        deadline = time.monotonic() + hang_limit
        try:
            while True:
                try:
                    out, err = process.communicate(timeout=min(1, max(0.1, deadline - time.monotonic())))
                    break
                except subprocess.TimeoutExpired:
                    self.sample()
                    if time.monotonic() >= deadline:
                        raise JourneyFailure(phase, f"command hung (no exit after {hang_limit}s): {argv!r}")
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
        if process.returncode:
            # Both streams: stderr may hold only progress diagnostics while the refusal is on stdout.
            told = [f"{label}: {text.strip()[-1500:]}" for label, text in (("stderr", err), ("stdout", out)) if text and text.strip()]
            raise JourneyFailure(phase, f"exit {process.returncode}: " + (" | ".join(told) or "no output"))
        if not json_result:
            return out
        try:
            value = parse_json_output(out)
        except json.JSONDecodeError as exc:
            raise JourneyFailure(phase, f"invalid JSON: {out[-1000:]}") from exc
        if isinstance(value, dict) and value.get("ok") is False:
            raise JourneyFailure(phase, str(value.get("error") or value))
        return value

    def write_summary(self, *, error: str | None = None) -> None:
        elapsed = round(time.monotonic() - self.started, 3)
        payload = {"ok": error is None, "error": error, "phase_seconds": self.phases, "phase_load": self.loads,
                   "total_seconds": elapsed, "disk_high_water_bytes": self.disk_high_water,
                   "limits": {"hang_seconds": HANG_SECONDS, "tier": self.tier,
                               "disk_bytes": DISK_BUDGET, "receipts_bytes": RECEIPT_BUDGET,
                               "logs_bytes": LOG_BUDGET}, **self.details}
        self.summary.parent.mkdir(parents=True, exist_ok=True)
        self.summary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
        lines = [f"astrid dev journey ({self.tier}): {'PASS' if error is None else 'FAIL'}", f"Total: {elapsed}s"]
        for name, seconds in self.phases.items():
            load = self.loads.get(name) or {}
            lines.append(f"{name}: {seconds}s (load {load.get('load_1m', '?')}, memory {load.get('memory_pressure', '?')})")
        lines += [f"Disk high water: {self.disk_high_water} / {DISK_BUDGET} bytes",
                  f"Receipts: {self.details.get('receipts_bytes', '?')} / {RECEIPT_BUDGET} bytes",
                  f"Logs: {self.details.get('logs_bytes', '?')} / {LOG_BUDGET} bytes"]
        if error:
            lines.append(error)
        self.summary.with_suffix(".txt").write_text("\n".join(lines) + "\n")


def _doctor_ready(value: dict[str, Any]) -> bool:
    """Doctor must call the runtime healthy and the pack host's renderer Node usable."""
    if value.get("ok") is False or value.get("healthy") is False:
        return False
    return (value.get("renderer_node") or {}).get("status") == "ok"


def parse_json_output(out: str) -> Any:
    """Parse stdout as one JSON document, tolerating a text preamble (output may be large)."""
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        starts = [0] + [i + 1 for i, ch in enumerate(out) if ch == "\n" and out[i + 1:i + 2] == "{"]
        for start in starts:
            try:
                return json.JSONDecoder().raw_decode(out[start:])[0]
            except json.JSONDecodeError:
                continue
        raise


def find_run_id(value: Any) -> str | None:
    """First 32-hex ``run_id`` anywhere in a result document."""
    stack = [value]
    while stack:
        item = stack.pop(0)
        if isinstance(item, dict):
            run_id = item.get("run_id")
            if isinstance(run_id, str) and len(run_id) == 32 and all(c in "0123456789abcdef" for c in run_id):
                return run_id
            stack.extend(item.values())
        elif isinstance(item, list):
            stack.extend(item)
    return None


def descendants(rows: list[dict[str, Any]], pid: int) -> list[dict[str, Any]]:
    """Every process below ``pid`` in the ps table."""
    owned = {pid}
    while True:
        grown = owned | {row["pid"] for row in rows if row["ppid"] in owned}
        if grown == owned:
            break
        owned = grown
    return [row for row in rows if row["pid"] in owned and row["pid"] != pid]


RENDER_MARKERS = ("remotion", "chrome", "ffmpeg")


def render_processes(rows: list[dict[str, Any]], host_pid: int) -> list[dict[str, Any]]:
    """The host's render work: descendants that are Remotion, a headless browser or ffmpeg."""
    return [row for row in descendants(rows, host_pid)
            if any(marker in row["command"].lower() for marker in RENDER_MARKERS)]


def with_duplicate_path(path: str) -> str:
    """A PATH whose every entry appears twice (what a nested shell profile produces)."""
    return os.pathsep.join([path, path]) if path else path


def read_host(root: Path) -> dict[str, Any]:
    try:
        return json.loads((root / "data" / "runtime" / "generic-host.json").read_text())
    except (OSError, ValueError):
        return {}


def pid_alive(pid: int | None) -> bool:
    if not isinstance(pid, int):
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    state = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()
    return bool(state) and not state.startswith("Z")


def wait_for(predicate, seconds: float, interval: float = 0.2):
    deadline = time.monotonic() + seconds
    while True:
        value = predicate()
        if value:
            return value
        if time.monotonic() >= deadline:
            return value
        time.sleep(interval)


def _find_output(root: Path, name: str, *, exclude: set[Path] | None = None) -> Path:
    matches = [path for path in _files(root) if path.name.endswith(name) and path not in (exclude or set())]
    if len(matches) != 1:
        raise JourneyFailure("render", f"expected one {name} under temp root, found {len(matches)}")
    return matches[0]


def _duration(path: Path, runner: Runner, env: dict[str, str], phase: str) -> float:
    out = runner.command(phase, ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                                 "-of", "default=noprint_wrappers=1:nokey=1", str(path)], env, json_result=False)
    return float(out.strip())




OPEN_CLIENT = "from astrid.sdk import AstridClient\nwith AstridClient.open_from_launcher():\n    pass\n"
IMAGE_PROMPT = (
    "A single pixel-art sticker: a small handheld bug-catching net that has just caught one tiny, friendly "
    "glowing beetle. Retro 16-bit game sprite, about 44 pixels wide, every pixel a perfect square of identical "
    "size, crisp 1-pixel dark charcoal (#22211E) outline, limited palette: warm cream #F3EEE4, bright orange "
    "#FF7A2E, a little soft yellow for the beetle's glow. No anti-aliasing, no gradients, no text. "
    "Centered on a flat, perfectly uniform pure magenta (#FF00FF) background for chroma keying."
)
HEAVY_LAUNCHER = Path.home() / "Developer" / "otto-claude-host" / "bin" / "otto-heavy"


def _sdk_script(body: str) -> str:
    return "import json\nfrom astrid.sdk import AstridClient\n" + body


def _invoke_script(capability: str, kind: str, project: str, inputs: dict[str, Any]) -> str:
    return _sdk_script(
        "with AstridClient.open_from_launcher() as client:\n"
        f"    result = client.invoke_result({capability!r}, kind={kind!r}, project={project!r}, inputs={inputs!r}, wait=True)\n"
        "    print(json.dumps({'ok': result.ok, 'run_id': getattr(result, 'run_id', None), "
        "'error': str(getattr(result, 'error', None) or '')}, default=str))\n")


class Journey:
    """One disposable-realm journey; each method is a phase body or a pure helper for it."""

    def __init__(self, *, runner: Runner, root: Path, env: dict[str, str], project: str, timeline: str,
                 start: float, end: float, kill_start: float, checkout: Path) -> None:
        self.r, self.root, self.env, self.project, self.timeline = runner, root, env, project, timeline
        self.start, self.end, self.kill_start, self.checkout = start, end, kill_start, checkout
        self.astrid = [sys.executable, "-m", "astrid"]

    # -- helpers --------------------------------------------------------------------------
    def open_client(self, phase: str, *, env: dict[str, str] | None = None, heavy: bool = False) -> None:
        argv = [sys.executable, "-c", OPEN_CLIENT]
        use = dict(env or self.env)
        if heavy:
            # A fresh capacity lease around the client: the host it starts must outlive the job.
            use.pop("OTTO_HEAVY_LEASE", None)
            argv = [str(HEAVY_LAUNCHER), "--weight", "1", "--purpose", "astrid dev journey: host start inside a job",
                    "--max-minutes", "3", "--"] + argv
        self.r.command(phase, argv, use, json_result=False)

    def host_pid(self, phase: str) -> int:
        pid = read_host(self.root).get("pid")
        if not isinstance(pid, int):
            raise JourneyFailure(phase, f"no pack host recorded in {self.root / 'data' / 'runtime' / 'generic-host.json'}")
        return pid

    def rooted_host(self, phase: str) -> int:
        """The pid our private Runtime recorded, only if it is alive and really a pack host."""
        pid = self.host_pid(phase)
        if not any(row["pid"] == pid and "generic_host" in row["command"] for row in _ps_rows()):
            raise JourneyFailure(phase, f"recorded host pid {pid} is not a running pack host; refusing to signal it")
        return pid

    def render(self, phase: str, tag: str, start: float, end: float, *, detach: bool = False,
               review: bool = False) -> tuple[Any, str]:
        name = f"v6-{tag}-{uuid.uuid4().hex[:8]}.mp4"
        argv = self.astrid + ["timelines", "render", self.timeline, "--project", self.project, "--range", str(start), str(end),
                              "--backend", "rendering.remotion", "--output-name", name,
                              *(["--review"] if review else []),
                              "--detach" if detach else "--wait", "--json"]
        if not detach:
            argv[-1:-1] = ["--timeout-seconds", str(HANG_SECONDS[phase])]
        return self.r.command(phase, argv, self.env), name

    def excerpt(self, phase: str, tag: str, ids: list[str]) -> None:
        result, name = self.render(phase, tag, self.start, self.end)
        data = result.get("data") or {}
        task_id = data.get("kernel_task_id")
        if not task_id or task_id in ids or data.get("state") not in ("completed", "succeeded"):
            raise JourneyFailure(phase, f"no distinct completed task: {data}")
        ids.append(task_id)
        output = _find_output(self.root / "data", name)
        seconds = _duration(output, self.r, self.env, phase)
        want = self.end - self.start
        if abs(seconds - want) > 0.5:
            raise JourneyFailure(phase, f"{output} duration {seconds}s, expected {want}s +-0.5")
        # The task scope removes its attempt scratch just after the task settles, so allow it a moment.
        frames = lambda: [p for p in _files(self.root / "tmp") if p.suffix in (".png", ".jpg")]
        if not wait_for(lambda: not frames(), 10):
            raise JourneyFailure(phase, f"frame-sequence files remain in TMPDIR: {[str(p) for p in frames()[:3]]}")
        self.r.details[phase] = {"task_id": task_id, "output_bytes": output.stat().st_size, "duration_seconds": seconds}

    # -- recovery phases ------------------------------------------------------------------
    def replace_host(self, old: int) -> int:
        """After a source change the idle outdated host is replaced by the next client open, unattended."""
        if HEAVY_LAUNCHER.exists():
            self.open_client("replace_host", heavy=True)
            self.r.details["replace_host_in_heavy_job"] = True
        else:
            self.open_client("replace_host")
            self.r.details["replace_host_in_heavy_job"] = False
        new = self.host_pid("replace_host")
        if new == old:
            raise JourneyFailure("replace_host", f"outdated host {old} was not replaced after a source change")
        if not wait_for(lambda: not pid_alive(old), 15):
            raise JourneyFailure("replace_host", f"outdated host {old} is still running")
        if not pid_alive(new):
            raise JourneyFailure("replace_host", f"replacement host {new} is not running")
        return new

    def survives_job(self, new: int) -> None:
        time.sleep(3)  # an end-of-job sweep would run about now
        if not pid_alive(new) or read_host(self.root).get("pid") != new:
            raise JourneyFailure("heavy_survival", f"host {new} did not outlive the capacity job that started it")

    def path_reuse(self, host: int) -> None:
        env = dict(self.env, PATH=with_duplicate_path(self.env.get("PATH", "")))
        self.open_client("path_reuse", env=env)
        if read_host(self.root).get("pid") != host or not pid_alive(host):
            raise JourneyFailure("path_reuse", f"a duplicated PATH made the client replace or refuse host {host}")

    def kill_idle(self) -> int:
        old = self.rooted_host("kill_idle")
        os.kill(old, signal.SIGKILL)
        if not wait_for(lambda: not pid_alive(old), 10):
            raise JourneyFailure("kill_idle", f"host {old} survived SIGKILL")
        self.open_client("kill_idle")
        new = self.host_pid("kill_idle")
        if new == old or not pid_alive(new):
            raise JourneyFailure("kill_idle", f"client open did not recover with a new host (old {old}, now {new})")
        return new

    def kill_render(self) -> int:
        host = self.rooted_host("kill_render")
        # A review-size render: this phase proves crash custody, not output fidelity, and a full-HD 32 s
        # window needs ~10 GiB of scratch (the Runtime rightly refuses it on a nearly full disk).
        result, _name = self.render("kill_render", "killed", self.kill_start, self.end, detach=True, review=True)
        found = wait_for(lambda: render_processes(_ps_rows(), host), 90)
        if not found:
            raise JourneyFailure("kill_render", f"no render processes appeared under host {host} (render finished too early or never started): {result}")
        pids = sorted({row["pid"] for row in found} | {row["pid"] for row in descendants(_ps_rows(), host)})
        os.kill(host, signal.SIGKILL)
        if not wait_for(lambda: not pid_alive(host), 10):
            raise JourneyFailure("kill_render", f"host {host} survived SIGKILL")
        journal = self.root / "data" / "runtime" / "generic-host.task-scopes.json"
        self.r.details["task_scope_journal_bytes"] = journal.stat().st_size if journal.exists() else None
        self.open_client("kill_render")
        new = self.host_pid("kill_render")
        if new == host or not pid_alive(new):
            raise JourneyFailure("kill_render", f"no new host after killing {host}")
        survivors = wait_for(lambda: not [p for p in pids if pid_alive(p) and p != new], 20)
        if not survivors:
            raise JourneyFailure("kill_render", f"orphaned render processes survived the next host start: {[p for p in pids if pid_alive(p)]}")
        self.r.details["reaped_orphan_pids"] = pids
        # The killed task must not be stranded: the Runtime gives it back (requeued, maybe re-run by the
        # new host). Cancel it and see it settle, so later phases start from an idle realm.
        task_id = (result.get("data") or {}).get("kernel_task_id") if isinstance(result, dict) else None
        if not task_id:
            raise JourneyFailure("kill_render", f"the killed render returned no task id: {result}")
        self.r.details["killed_task_after_recovery"] = self.task_state(task_id)
        if self.task_state(task_id) not in TERMINAL_TASK_STATES:
            self.r.command("kill_render", self.astrid + ["tasks", "cancel", task_id, "--project", self.project, "--json"], self.env)
        if not wait_for(lambda: self.task_state(task_id) in TERMINAL_TASK_STATES, HANG_SECONDS["kill_render"], interval=2):
            raise JourneyFailure("kill_render", f"killed task {task_id} never settled: {self.task_state(task_id)}")
        self.r.details["killed_task_final"] = self.task_state(task_id)
        return new

    def task_state(self, task_id: str) -> str | None:
        shown = self.r.command("kill_render", self.astrid + ["tasks", "show", task_id, "--project", self.project, "--json"], self.env)
        data = shown.get("data") if isinstance(shown, dict) else None
        if isinstance(data, dict) and isinstance(data.get("task"), dict):
            data = data["task"]
        return data.get("state") if isinstance(data, dict) else None

    # -- full tier ------------------------------------------------------------------------
    def full(self, replace_slot: str, expect_duration: float | None) -> None:
        r = self.r
        image = r.phase("image", lambda: self._run_id(
            "image", _invoke_script("generation.generate_image", "executor", self.project,
                                    {"model": "flux-dev", "mode": "t2i", "execution": "codex", "prompt": IMAGE_PROMPT, "count": 1})))
        sprites = r.phase("sprites", lambda: self._run_id(
            "sprites", _invoke_script("pixel.sprites", "action", self.project,
                                      {"image": f"run:{image}/generated_images#0", "mode": "chroma", "scale": 5})))
        key = f"v6-{uuid.uuid4().hex[:8]}"
        def edit():
            base = self.astrid + ["timelines"]
            tl = ["--project", self.project]
            r.command("edit", base + ["checkout", self.timeline, *tl, "--draft", key, "--fresh", "--json"], self.env)
            r.command("edit", base + ["edit", self.timeline, *tl, "--replace-asset", f"{replace_slot}=run:{sprites}/sprites#0", "--json"], self.env)
            r.command("edit", base + ["check", self.timeline, *tl, "--no-sweep", "--json"], self.env)
            r.command("edit", base + ["publish", self.timeline, *tl, "-m", f"journey {key}", "--idempotency-key", key, "--json"], self.env)
        r.phase("edit", edit)
        def full_render():
            name = f"v6-full-{key}.mp4"
            r.command("render_full", self.astrid + ["timelines", "render", self.timeline, "--project", self.project, "--review",
                                                    "--output-name", name, "--wait", "--timeout-seconds", str(HANG_SECONDS["render_full"]),
                                                    "--json"], self.env)
            output = _find_output(self.root / "data", name)
            seconds = _duration(output, r, self.env, "render_full")
            if seconds < 10 or (expect_duration is not None and abs(seconds - expect_duration) > 1.0):
                raise JourneyFailure("render_full", f"full render is {seconds}s (expected {expect_duration or '>= 10'})")
            r.details["render_full"] = {"output_bytes": output.stat().st_size, "duration_seconds": seconds,
                                        "image_run": image, "sprites_run": sprites}
        r.phase("render_full", full_render)

    def _run_id(self, phase: str, script: str) -> str:
        result = self.r.command(phase, [sys.executable, "-c", script], self.env)
        run_id = find_run_id(result)
        if not result.get("ok") or not run_id:
            raise JourneyFailure(phase, f"no successful run: {str(result)[:600]}")
        return run_id


def run(reference: Path, timeline: str, summary: Path, *, tier: str = "smoke", start: float = 144.0, end: float = 152.0,
        replace_slot: str = "BUGNET", expect_duration: float | None = None) -> int:
    reference = reference.resolve(strict=True)
    summary = summary.resolve()
    if summary.is_relative_to(reference):
        raise JourneyFailure("preflight", "summary must be outside the reference")
    if "/" not in timeline:
        raise JourneyFailure("preflight", "timeline must be PROJECT/TIMELINE")
    project, source_timeline = timeline.split("/", 1)
    if not project or not source_timeline:
        raise JourneyFailure("preflight", "timeline must be PROJECT/TIMELINE")
    if not 0 <= start < end:
        raise JourneyFailure("preflight", "excerpt needs 0 <= start < end")
    checkout = Path(__file__).resolve().parents[3]
    # The pack host needs the renderer's pinned Node; find out now, not after a 40 s adopt.
    # (The journey drops ASTRID_NODE_EXECUTABLE below, so the host searches exactly as this does.)
    from astrid.core.rendering.node_pin import resolve_pinned_node
    node = resolve_pinned_node(checkout / "remotion", search_path=os.environ.get("PATH", ""))
    if not node.ok:
        raise JourneyFailure("preflight", node.problem or "no Node for the renderer")
    # This module sits in the host's measured core source closure: appending a comment changes the
    # closure identity (an outdated host) without changing renderer behavior.
    source_probe = Path(__file__)
    original = source_probe.read_bytes()
    probe_bytes = original + b"\n# journey source-change probe\n"
    # macOS temp dirs live under /var -> /private/var; the Runtime refuses symlinked support paths.
    root = Path(tempfile.mkdtemp(prefix="astrid-v6-")).resolve()
    runner = Runner(root, summary, tier)
    env = os.environ.copy()
    for key in list(env):
        if key in ("ASTRID_NODE_EXECUTABLE", "ASTRID_TIMELINE_SCHEMA_PYTHONPATH", "OTTO_HEAVY_LEASE") or key.startswith(("ASTRID_REMOTION_", "ASTRID_RENDER_")):
            env.pop(key)
    env["ASTRID_RUNTIME_DATA_ROOT"] = str(root / "data")
    env["TMPDIR"] = str(root / "tmp")
    (root / "tmp").mkdir()
    changed = False
    adopt_attempted = False
    failure: str | None = None
    try:
        realm_id, reference_realm = runner.phase("reference", lambda: select_reference_realm(reference))
        manifest = json.loads((reference / "runtime" / "source-profiles" / "astrid.json").read_text())
        manifest["source_checkout"] = str(checkout)
        # Test the Runtime this interpreter imports, not the one the reference was adopted with.
        runtime_spec = importlib.util.find_spec("runtime_protocol")
        if runtime_spec is None or runtime_spec.origin is None:
            raise JourneyFailure("reference", "runtime_protocol is not importable from this environment")
        manifest["runtime_checkout"] = str(Path(runtime_spec.origin).resolve().parents[1])
        manifest["runtime_environment"] = sys.prefix
        manifest_path = root / "source-profile.json"
        manifest_path.write_text(json.dumps(manifest))
        env["ASTRID_RUNTIME_SOURCE_MANIFEST"] = str(manifest_path)
        local = [sys.executable, "-m", "astrid_runtime"]
        common = ["--profile", "astrid", "--data-root", str(root / "data"), "--json"]
        adopt_attempted = True
        runner.phase("adopt", lambda: runner.command("adopt", local + ["workspace", "adopt", "--from", str(reference_realm), "--realm-id", realm_id, "--source-manifest", str(manifest_path)] + common, env))
        runner.phase("up", lambda: runner.command("up", local + ["up", "--source-manifest", str(manifest_path)] + common, env))
        j = Journey(runner=runner, root=root, env=env, project=project, timeline=source_timeline,
                    start=start, end=end, kill_start=max(0.0, end - 32), checkout=checkout)
        # The render killed mid-way is a 32 s window ending where the excerpt ends: long enough to be
        # caught running, and inside the film (a window past the end is refused, as it should be).
        # The first client open starts the pack host, which resolves the renderer (no separate setup step).
        runner.phase("first_open", lambda: j.open_client("first_open"))
        doctor = runner.phase("doctor", lambda: runner.command("doctor", j.astrid + ["doctor", "--json"], env))
        if not _doctor_ready(doctor):
            raise JourneyFailure("doctor", f"not healthy: healthy={doctor.get('healthy')} issues={doctor.get('issues')} renderer_node={doctor.get('renderer_node')}")
        ids: list[str] = []
        runner.phase("render_1", lambda: j.excerpt("render_1", "1", ids))
        old_host = j.host_pid("source_change")
        def touch():
            nonlocal changed
            if source_probe.read_bytes() != original:
                raise JourneyFailure("source_change", "journey source changed concurrently; refusing to overwrite it")
            source_probe.write_bytes(probe_bytes)
            changed = True
        runner.phase("source_change", touch)
        host = runner.phase("replace_host", lambda: j.replace_host(old_host))
        runner.phase("heavy_survival", lambda: j.survives_job(host))
        runner.phase("render_2", lambda: j.excerpt("render_2", "2", ids))
        runner.phase("path_reuse", lambda: j.path_reuse(host))
        runner.phase("kill_idle", j.kill_idle)
        runner.phase("kill_render", j.kill_render)
        runner.phase("render_after", lambda: j.excerpt("render_after", "after", ids))
        runner.details["task_ids"] = ids
        if tier == "full":
            j.full(replace_slot, expect_duration)
    except Exception as exc:
        failure = str(exc)
    finally:
        if changed:
            if source_probe.read_bytes() == probe_bytes:
                source_probe.write_bytes(original)
            else:
                failure = (failure + "; " if failure else "") + "source_change: journey source changed concurrently; manual restore required"
        if adopt_attempted:
            try:
                runner.phase("down", lambda: runner.command("down", [sys.executable, "-m", "astrid_runtime", "down", "--source-manifest", str(manifest_path)] + common, env))
            except Exception as exc:
                failure = (failure + "; " if failure else "") + str(exc)
        try:
            leftovers = runner.phase("cleanup", lambda: _reap_owned_groups(root))
            if leftovers:
                failure = (failure + "; " if failure else "") + f"cleanup: rooted processes remain: {leftovers}"
        except Exception as exc:
            failure = (failure + "; " if failure else "") + str(exc)
        runner.details["receipts_bytes"] = _tree_bytes(root, lambda p: "receipt" in p.name.lower())
        runner.details["logs_bytes"] = _tree_bytes(root, lambda p: ".log" in p.name)
        if runner.details["receipts_bytes"] > RECEIPT_BUDGET or runner.details["logs_bytes"] > LOG_BUDGET:
            failure = (failure + "; " if failure else "") + "growth_budget: receipts or logs exceeded budget"
        if failure is not None:
            runner.details["evidence"] = _keep_failure_logs(root, summary)
        runner.write_summary(error=failure)
        shutil.rmtree(root, ignore_errors=True)
    return 0 if failure is None else 1


EVIDENCE_TAIL_BYTES = 256 * 1024


def _keep_failure_logs(root: Path, summary: Path) -> str | None:
    """Before the disposable root goes, keep the tail of each runtime/host log beside the summary.

    Only ``*.log`` files under the data root's ``runtime`` directory are kept (never credentials or
    the realm), each cut to its last 256 KiB, so a failed run can still be diagnosed.
    """
    logs = sorted((root / "data" / "runtime").glob("*.log"))
    if not logs:
        return None
    target = summary.with_name(summary.stem + "-evidence")
    target.mkdir(parents=True, exist_ok=True)
    for log in logs:
        with log.open("rb") as stream:
            stream.seek(max(0, log.stat().st_size - EVIDENCE_TAIL_BYTES))
            (target / log.name).write_bytes(stream.read())
    return str(target)


def main(args: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="astrid dev journey", description="Real-film journey in a disposable realm")
    parser.add_argument("--tier", choices=["smoke", "full"], required=True,
                        help="smoke: excerpt + host-lifecycle recovery (~4 min); full: smoke + Codex image, sprites, edit/publish, full render (~20 min)")
    parser.add_argument("--reference", type=Path, required=True, help="a stopped data root holding the film realm")
    parser.add_argument("--timeline", required=True, help="PROJECT/TIMELINE")
    parser.add_argument("--summary", type=Path, required=True, help="JSON summary path; adjacent .txt is also written")
    parser.add_argument("--range", nargs=2, type=float, metavar=("START", "END"), default=[144.0, 152.0],
                        help="excerpt window in seconds (default 144 152)")
    parser.add_argument("--replace-slot", default="BUGNET", help="full tier: asset slot the generated sprite replaces")
    parser.add_argument("--expect-duration", type=float, default=None, help="full tier: expected full-film seconds (+-1)")
    parsed = parser.parse_args(args)
    try:
        status = run(parsed.reference, parsed.timeline, parsed.summary, tier=parsed.tier, start=parsed.range[0],
                     end=parsed.range[1], replace_slot=parsed.replace_slot, expect_duration=parsed.expect_duration)
    except Exception as exc:
        summary = parsed.summary.resolve()
        summary.parent.mkdir(parents=True, exist_ok=True)
        summary.write_text(json.dumps({"ok": False, "error": str(exc), "phase_seconds": {}}, indent=2) + "\n")
        summary.with_suffix(".txt").write_text(f"astrid dev journey ({parsed.tier}): FAIL\n{exc}\n")
        print(f"journey: {exc}", file=sys.stderr)
        return 1
    report = json.loads(parsed.summary.read_text())
    print(json.dumps(report, sort_keys=True))
    return status


if __name__ == "__main__":
    raise SystemExit(main())
