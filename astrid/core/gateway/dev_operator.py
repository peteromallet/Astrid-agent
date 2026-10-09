"""Operator commands for developing against a running Astrid runtime.

``python -m astrid dev status`` reports which checkout the runtime serves, the
git state of the served and dev trees, whether the pack host is alive and
ready, and whether this client's own code is paired with the served tree.

``python -m astrid dev promote [--ref REF] [--force]`` checks REF out (detached)
in the serve worktree, restarts only the pack host recorded in the runtime's
own state file, and reopens through the normal bootstrap.

Neither command is a pack capability. Executors run inside the pack host that
promote restarts, so a pack cannot restart its own host. Status reads state
files and never contacts the runtime or starts a host.
"""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path
from typing import Any, Callable, Mapping

PROFILE_NAME = "astrid"
TERMINAL_TASK_STATES = frozenset({"completed", "succeeded", "cancelled", "canceled", "expired", "failed"})
GIT_TIMEOUT_SECONDS = 30.0
READY_WAIT_SECONDS = 60.0
READY_POLL_SECONDS = 0.5


class DevOperatorError(RuntimeError):
    """A refused or failed operator action, with the one next step to take."""

    def __init__(self, message: str, *, next_action: str | None = None, state: str = "refused") -> None:
        super().__init__(message)
        self.next_action = next_action
        self.state = state


def dev_checkout() -> Path:
    """The checkout this client's ``astrid`` package was imported from (the dev tree)."""
    import astrid

    return Path(astrid.__file__).resolve().parents[1]


def _git(repo: Path, *args: str) -> str:
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True, text=True, timeout=GIT_TIMEOUT_SECONDS, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise DevOperatorError(f"git {' '.join(args)} failed in {repo}: {exc}", state="error") from exc
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip()[-300:]
        raise DevOperatorError(f"git {' '.join(args)} failed in {repo}: {detail}", state="error")
    return proc.stdout.strip()


def _git_branch(repo: Path) -> str | None:
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo), "symbolic-ref", "--short", "-q", "HEAD"],
            capture_output=True, text=True, timeout=GIT_TIMEOUT_SECONDS, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:  # detached HEAD: symbolic-ref exits 1
        return None
    return proc.stdout.strip() or None


def _git_state(repo: Path) -> dict[str, Any]:
    status = _git(repo, "status", "--porcelain")
    return {
        "checkout": str(repo),
        "head": _git(repo, "rev-parse", "HEAD"),
        "branch": _git_branch(repo),
        "dirty": bool(status),
        "dirty_paths": len(status.splitlines()) if status else 0,
    }


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def served_profile(data_root: Path) -> dict[str, Any]:
    """The source profile the runtime boots the pack host from."""
    path = Path(data_root) / "runtime" / "source-profiles" / f"{PROFILE_NAME}.json"
    value = _read_json(path)
    if value is None or not value.get("source_checkout"):
        raise DevOperatorError(
            f"no readable source profile at {path}",
            next_action="banodoco-local up --profile astrid --source-manifest <profile.json>",
            state="unavailable",
        )
    return {
        "profile_path": str(path),
        "source_checkout": str(Path(str(value["source_checkout"])).expanduser().resolve()),
        "runtime_checkout": value.get("runtime_checkout"),
    }


def host_state(runtime_dir: Path) -> dict[str, Any]:
    """The pack host recorded by the runtime, with pid identity and readiness."""
    from astrid.sdk.host_bootstrap import _host_identity_matches, _host_pid_alive

    state = _read_json(runtime_dir / "generic-host.json")
    ready = _read_json(runtime_dir / "generic-host.ready.json")
    if state is None:
        return {"recorded": False, "pid": None, "alive": False, "identity_verified": False, "ready": False,
                "ready_status": None, "source_checkout": None, "source_checkout_digest": None,
                "source_closure_digest": None}
    pid = state.get("pid")
    alive = bool(_host_pid_alive(pid))
    verified = bool(alive and _host_identity_matches(state))
    ready_ok = bool(alive and ready and ready.get("status") == "ready" and str(ready.get("pid")) == str(pid))
    return {
        "recorded": True,
        "pid": pid,
        "alive": alive,
        "identity_verified": verified,
        "ready": ready_ok,
        "ready_status": (ready or {}).get("status"),
        "ready_file": state.get("ready_file"),
        "source_checkout": state.get("source_checkout"),
        "source_checkout_digest": state.get("source_checkout_digest"),
        "source_closure_digest": state.get("source_closure_digest"),
    }


def _default_digests() -> tuple[Callable[[Path], str], Callable[[Path], str]]:
    from astrid.core.execution.generic_host import (
        source_checkout_closure_digest,
        source_checkout_digest,
    )

    return source_checkout_digest, source_checkout_closure_digest


def pair_report(
    *,
    served: Path,
    client: Path,
    host: Mapping[str, Any],
    digest_fn: Callable[[Path], str] | None = None,
    closure_fn: Callable[[Path], str] | None = None,
) -> dict[str, Any]:
    """Compare this client's code with the served tree and with the host's attestation.

    ``paired`` only when the client's tree and the served tree have the same pack
    digest and closure digest, and the running host attested that same served
    digest. Anything else is ``drift``; a tree that cannot be measured is ``unknown``.
    """
    default_digest, default_closure = _default_digests()
    digest_fn = digest_fn or default_digest
    closure_fn = closure_fn or default_closure
    try:
        served_digest, served_closure = digest_fn(served), closure_fn(served)
        client_digest, client_closure = digest_fn(client), closure_fn(client)
    except (OSError, ValueError) as exc:
        return {"status": "unknown", "reason": f"cannot measure a checkout: {exc}",
                "client_checkout": str(client), "served_checkout": str(served)}
    same_tree = client_digest == served_digest and client_closure == served_closure
    host_attested = host.get("source_checkout_digest")
    host_matches = host_attested == served_digest if host.get("recorded") else None
    if same_tree and host_matches is not False:
        status, reason = "paired", "client code matches the served tree"
        if host_matches is None:
            reason += "; no host recorded yet"
    elif not same_tree:
        status = "drift"
        reason = ("dev tree differs from the served tree (uncommitted or unpromoted work); "
                  "commit, then `python -m astrid dev promote`")
    else:
        status = "drift"
        reason = "the running host attested a different served tree than the one now on disk; restart it"
    return {
        "status": status,
        "reason": reason,
        "client_checkout": str(client),
        "served_checkout": str(served),
        "client_digest": client_digest,
        "served_digest": served_digest,
        "client_closure_digest": client_closure,
        "served_closure_digest": served_closure,
        "host_attested_digest": host_attested,
        "host_matches_served": host_matches,
    }


def run_status(*, data_root: Path, client: Path | None = None,
               digest_fn: Callable[[Path], str] | None = None,
               closure_fn: Callable[[Path], str] | None = None) -> dict[str, Any]:
    """Read-only report: served checkout, dev tree, host state, and the pair check."""
    client = Path(client).resolve() if client is not None else dev_checkout()
    profile = served_profile(data_root)
    served = Path(profile["source_checkout"])
    dev = _git_state(client)
    served_state = _git_state(served) if served.is_dir() else None
    host = host_state(Path(data_root) / "runtime")
    pair = pair_report(served=served, client=client, host=host, digest_fn=digest_fn, closure_fn=closure_fn)
    same_head = bool(served_state and served_state["head"] == dev["head"])
    return {
        "ok": True,
        "state": "reported",
        "profile": profile,
        "served": served_state,
        "dev": dev,
        "dev_matches_served_head": same_head,
        "host": host,
        "pair": pair,
    }


def _flatten_rows(value: Any) -> list[dict[str, Any]]:
    """Collect dict rows from the nested ``data`` lists a Runtime listing returns."""
    if isinstance(value, Mapping):
        return [dict(value)] if ("state" in value or "task_id" in value or "slug" in value or "project_id" in value) else []
    if isinstance(value, (list, tuple)):
        rows: list[dict[str, Any]] = []
        for item in value:
            rows.extend(_flatten_rows(item))
        return rows
    return []


def _listing_data(result: Any) -> Any:
    if isinstance(result, Mapping):
        if result.get("ok") is False:
            raise DevOperatorError(f"runtime listing failed: {result.get('error')}", state="unavailable")
        return result.get("data")
    if getattr(result, "ok", True) is False:
        raise DevOperatorError(f"runtime listing failed: {getattr(result, 'error', None)}", state="unavailable")
    return getattr(result, "data", result)


def _page_cursor(result: Any) -> str | None:
    """The next-page cursor a Runtime listing carries as a string item in its data."""
    data = _listing_data(result)
    if isinstance(data, (list, tuple)):
        for item in data:
            if isinstance(item, str) and item:
                return item
    return None


def _all_pages(fetch: Callable[[str | None], Any], *, max_pages: int = 1000) -> list[dict[str, Any]]:
    """Follow a Runtime listing to its last page. A listing that stops early can hide live work."""
    rows: list[dict[str, Any]] = []
    cursor: str | None = None
    seen: set[str] = set()
    for _ in range(max_pages):
        result = fetch(cursor)
        rows.extend(_flatten_rows(_listing_data(result)))
        cursor = _page_cursor(result)
        if cursor is None or cursor in seen:
            return rows
        seen.add(cursor)
    raise DevOperatorError("runtime listing did not end; cannot prove the host is idle", state="unavailable")


def in_flight_tasks(client: Any) -> list[str]:
    """Task ids the Runtime reports as not yet terminal, across every project and page."""
    projects = _all_pages(lambda cursor: client.projects.list(cursor=cursor) if cursor else client.projects.list())
    busy: list[str] = []
    for project in projects:
        project_ref = project.get("slug") or project.get("project_id")
        if not project_ref:
            continue
        tasks = _all_pages(
            lambda cursor, ref=str(project_ref): client.tasks.list(ref, cursor=cursor) if cursor
            else client.tasks.list(ref)
        )
        for task in tasks:
            if str(task.get("state", "")).lower() not in TERMINAL_TASK_STATES:
                busy.append(str(task.get("task_id") or task.get("id") or task.get("state")))
    return busy


def _last_error_line(log: Path) -> str | None:
    try:
        with log.open("rb") as handle:
            handle.seek(max(0, log.stat().st_size - 200_000))
            lines = handle.read().decode("utf-8", "replace").splitlines()
    except OSError:
        return None
    errors = [line for line in lines if "Error" in line]
    return errors[-1][-400:] if errors else None


def _require_linked_serve_worktree(dev: Path, serve: Path) -> None:
    if serve == dev:
        raise DevOperatorError(
            "the runtime serves the dev tree itself; promote needs a separate serve worktree",
            next_action="git -C <dev> worktree add --detach ../Astrid-serve HEAD, then re-point the source profile",
            state="unavailable",
        )
    common_dev = Path(_git(dev, "rev-parse", "--path-format=absolute", "--git-common-dir")).resolve()
    try:
        common_serve = Path(_git(serve, "rev-parse", "--path-format=absolute", "--git-common-dir")).resolve()
    except DevOperatorError as exc:
        raise DevOperatorError(f"served checkout {serve} is not a git worktree: {exc}", state="unavailable") from exc
    if common_dev != common_serve:
        raise DevOperatorError(
            f"served checkout {serve} belongs to a different repository than the dev tree {dev}",
            state="unavailable",
        )


def run_promote(
    *,
    data_root: Path,
    ref: str | None = None,
    force: bool = False,
    client: Path | None = None,
    open_client: Callable[..., Any] | None = None,
    terminate_host: Callable[[Mapping[str, Any]], None] | None = None,
    digest_fn: Callable[[Path], str] | None = None,
    closure_fn: Callable[[Path], str] | None = None,
    ready_wait_seconds: float = READY_WAIT_SECONDS,
) -> dict[str, Any]:
    """Move the serve worktree to REF and restart only the recorded pack host."""
    from astrid.sdk.host_bootstrap import PackHostBootstrapError, _descendant_snapshot

    if open_client is None:
        from astrid.sdk.client import AstridClient

        open_client = AstridClient.open_from_launcher
    if terminate_host is None:
        from astrid.sdk.host_bootstrap import _terminate_old_host

        terminate_host = _terminate_old_host
    default_digest, default_closure = _default_digests()
    digest_fn = digest_fn or default_digest
    closure_fn = closure_fn or default_closure

    dev = Path(client).resolve() if client is not None else dev_checkout()
    profile = served_profile(data_root)
    serve = Path(profile["source_checkout"])
    _require_linked_serve_worktree(dev, serve)
    ref_sha = _git(dev, "rev-parse", "--verify", f"{ref or 'HEAD'}^{{commit}}")
    if _git(serve, "status", "--porcelain"):
        raise DevOperatorError(
            f"serve worktree {serve} has local changes; refusing to check out over them",
            next_action=f"inspect `git -C {serve} status`; the serve worktree must hold no edits",
        )
    old_sha = _git(serve, "rev-parse", "HEAD")
    runtime_dir = Path(data_root) / "runtime"
    before = host_state(runtime_dir)

    busy: list[str] = []
    if before["alive"]:
        if not before["identity_verified"]:
            raise DevOperatorError(
                "the recorded pack host cannot be verified as this runtime's host; not touching it",
                next_action="python -m astrid doctor --json",
                state="unavailable",
            )
        children = _descendant_snapshot(int(before["pid"]))
        if children:
            busy.append(f"{len(children)} active child process(es) under host pid {before['pid']}")
    try:
        with open_client(start_pack_host=False) as runtime_client:
            busy.extend(f"task {task_id}" for task_id in in_flight_tasks(runtime_client))
    except Exception as exc:  # noqa: BLE001 - an unreachable or unreadable runtime means "cannot prove idle"
        busy.append(f"runtime unreachable or unreadable, cannot prove the host is idle ({exc})")
    if busy and not force:
        raise DevOperatorError(
            "the pack host has in-flight work: " + "; ".join(busy) + ". Promotion refused.",
            next_action="wait for it to finish, or rerun with --force (abandons that work)",
            state="busy",
        )

    _git(serve, "checkout", "--quiet", "--detach", ref_sha)
    new_sha = _git(serve, "rev-parse", "HEAD")
    if before["alive"]:
        try:
            terminate_host(_read_json(runtime_dir / "generic-host.json") or {})
        except PackHostBootstrapError as exc:
            raise DevOperatorError(
                f"could not stop host pid {before['pid']}; the serve worktree is already at {new_sha[:12]}: {exc}",
                next_action=f"tail {runtime_dir / 'generic-host.log'}, then rerun dev promote --ref {new_sha[:12]}",
                state="error",
            ) from exc

    try:
        with open_client(start_pack_host=True):
            pass
    except Exception as exc:  # noqa: BLE001 - report the runtime's own failure with the log line
        last = _last_error_line(runtime_dir / "generic-host.log")
        raise DevOperatorError(
            f"reopen through the normal bootstrap failed after promoting {old_sha[:12]} -> {new_sha[:12]}: {exc}",
            next_action=(f"fix and rerun promote; last host error: {last}" if last else "python -m astrid doctor --json"),
            state="error",
        ) from exc

    deadline = time.monotonic() + ready_wait_seconds
    after = host_state(runtime_dir)
    while time.monotonic() < deadline and not (after["ready"] and after["pid"] != before["pid"]):
        time.sleep(READY_POLL_SECONDS)
        after = host_state(runtime_dir)
    expected = digest_fn(serve)
    pair = pair_report(served=serve, client=dev, host=after, digest_fn=digest_fn, closure_fn=closure_fn)
    verified = bool(after["ready"] and after["pid"] != before["pid"] and after["source_checkout_digest"] == expected)
    if not verified:
        last = _last_error_line(runtime_dir / "generic-host.log")
        raise DevOperatorError(
            f"host not verified ready on {new_sha[:12]} (pid {after['pid']}, ready={after['ready']})",
            next_action=(f"last host error: {last}" if last else "python -m astrid doctor --json"),
            state="error",
        )
    return {
        "ok": True,
        "state": "promoted",
        "served_checkout": str(serve),
        "ref": ref or "HEAD",
        "old_sha": old_sha,
        "new_sha": new_sha,
        "host_pid_before": before["pid"],
        "host_pid_after": after["pid"],
        "ready": True,
        "source_checkout_digest": after["source_checkout_digest"],
        "pair": pair,
        "forced": bool(force and busy),
        "abandoned": busy if (force and busy) else [],
    }


def render_human(payload: Mapping[str, Any]) -> list[str]:
    """Plain-text lines for a status or promote result."""
    if "dev" in payload and "pair" in payload and payload.get("state") == "reported":
        served, dev, host, pair = payload["served"] or {}, payload["dev"], payload["host"], payload["pair"]
        return [
            f"served checkout: {payload['profile']['source_checkout']} @ {(served.get('head') or '?')[:12]}",
            f"dev tree: {dev['checkout']} @ {dev['head'][:12]} branch={dev['branch']} dirty={dev['dirty']}",
            f"dev head matches served head: {payload['dev_matches_served_head']}",
            f"pack host: pid={host.get('pid')} alive={host.get('alive')} ready={host.get('ready')}",
            f"pair: {pair['status']} ({pair['reason']})",
        ]
    lines = [f"promoted {payload['old_sha'][:12]} -> {payload['new_sha'][:12]} in {payload['served_checkout']}",
             f"host pid {payload['host_pid_before']} -> {payload['host_pid_after']}, ready={payload['ready']}",
             f"pair: {payload['pair']['status']} ({payload['pair']['reason']})"]
    if payload.get("abandoned"):
        lines.append("abandoned in-flight work (forced): " + "; ".join(payload["abandoned"]))
    return lines


__all__ = [
    "DevOperatorError",
    "dev_checkout",
    "host_state",
    "in_flight_tasks",
    "pair_report",
    "render_human",
    "run_promote",
    "run_status",
    "served_profile",
]
