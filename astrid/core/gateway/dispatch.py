"""Focused dispatch for the product and operational gateway families."""

from __future__ import annotations

from typing import Any

from astrid.core.contracts.errors import AstridError


def _dispatch(raw: list[str]) -> int:
    from . import _print_entrypoint_help

    if not raw:
        _print_entrypoint_help()
        return 0

    first = raw[0]
    try:
        if first not in _CORE_ROUTE_NAMES:
            _register_installed_pack_routes()
        if first not in frozenset(_TOP_LEVEL_HANDLERS):
            raise AstridError(
                f"unknown command '{first}'",
                valid_options=sorted(_TOP_LEVEL_HANDLERS),
                recovery_command="astrid --help",
                state_snapshot={"command": first},
            )
        parser = _build_dispatch_parser()
        parsed, tail = parser.parse_known_args(raw)
        try:
            return int(parsed.handler(tail))
        except SystemExit as exc:
            # Nested product parsers use argparse's normal help/usage exit.
            # Keep the Python gateway boundary integer-returning while
            # preserving argparse's documented 0/2 status codes.
            return int(exc.code) if isinstance(exc.code, int) else 1
    finally:
        # Pack routes are request-local discovery results. Keeping them in the
        # process-global core table makes later help/tests depend on which
        # command happened to run first.
        _clear_dynamic_pack_routes()


def _top_level_commands() -> frozenset[str]:
    """Return the stable seven-family gateway census, excluding pack routes."""
    return _CORE_ROUTE_NAMES


def _build_dispatch_parser() -> Any:
    import argparse

    parser = argparse.ArgumentParser(prog="astrid", add_help=False)
    sub = parser.add_subparsers(dest="command", required=True)
    for command, handler in _TOP_LEVEL_HANDLERS.items():
        command_parser = sub.add_parser(command, add_help=False)
        command_parser.set_defaults(handler=handler)
    return parser


def _dispatch_projects(args: list[str]) -> int:
    return _dispatch_product(["projects", *args])


def _dispatch_timelines(args: list[str]) -> int:
    if args and args[0] == "inspect":
        from astrid.packs.timeline.cli import offline_inspect_main
        return offline_inspect_main(args[1:])
    return _dispatch_product(["timelines", *args])


def _dispatch_media(args: list[str]) -> int:
    return _dispatch_product(["media", *args])


def _dispatch_setup(args: list[str]) -> int:
    """Use the shared setup plan/apply core; preview remains the default."""
    from astrid.setup import main

    return int(main(args))


def _dispatch_status(args: list[str]) -> int:
    """Observe workspace/Runtime/readiness without launcher or provisioning."""
    import argparse
    import json

    parser = argparse.ArgumentParser(prog="astrid status", description="Read-only workspace and Runtime status.")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--diagnostic", action="store_true", help="emit the strict C2 diagnostic document")
    parser.add_argument("--shared", action="store_true", help="redact the diagnostic for shared transport")
    if any(token in {"-h", "--help"} for token in args):
        parser.print_help()
        return 0
    parsed = parser.parse_args(args)
    from astrid.core.auth import contributor_key_present
    from astrid.core.gateway.diagnostics import collect_diagnostic
    from astrid.runtime_cli import RuntimeCLI, RuntimeCLIError
    from astrid.sdk.storage_root import resolve_runtime_data_root

    try:
        support_root = resolve_runtime_data_root()
        runtime = RuntimeCLI()
        diagnostic, workspace, status = collect_diagnostic(
            runtime,
            support_root=support_root,
            mode="shared" if parsed.shared else "local",
            command="status",
        )
    except (RuntimeCLIError, ValueError) as exc:
        payload = {
            "ok": False,
            "problem_code": getattr(exc, "code", "runtime_unavailable"),
            "error": str(exc),
            "effects": ["observe"],
            "authorization_required": False,
            "next_action": "astrid setup",
        }
        if parsed.diagnostic:
            from astrid.core.gateway.diagnostics import build_diagnostic, _failure_from
            code, boundary = _failure_from(exc)
            payload = build_diagnostic(
                mode="shared" if parsed.shared else "local",
                facts={},
                problem_code=code,
                failure_boundary=boundary,
                next_actions=[{
                    "commandId": "runtime-up",
                    "arguments": ["--data-root", "<redacted:path>"] if parsed.shared else [],
                    "effects": ["start-stop-local-service", "configure-install", "write-relocate-change-data"],
                    "authorizationRequired": True,
                    "executable": not parsed.shared,
                }],
            )
            print(json.dumps(payload, indent=2, sort_keys=True))
            return 1
        if parsed.json:
            print(json.dumps(payload, indent=2, sort_keys=True))
        else:
            print("Astrid status\nruntime: unavailable\nnext action: astrid setup")
        return 1
    if parsed.diagnostic:
        print(json.dumps(diagnostic, indent=2, sort_keys=True))
        return 0 if diagnostic["problemCode"] is None else 1
    from astrid.core.gateway.diagnostics import redact_for_shared
    workspace_data = redact_for_shared(dict(workspace.data)) if parsed.shared and workspace else (dict(workspace.data) if workspace else {})
    status_data = redact_for_shared(dict(status.data)) if parsed.shared and status else (dict(status.data) if status else {})
    def readiness(result: Any) -> str:
        # An observation can time out before it returns (status has a 5 s
        # budget): say "not checked" with the reason instead of crashing.
        if result is None:
            reason = diagnostic.get("problemCode") if isinstance(diagnostic, dict) else None
            return f"not checked ({reason or 'no result'})"
        if getattr(result, "ok", False):
            return "ready"
        data = getattr(result, "data", None)
        error = data.get("error") if isinstance(data, dict) else None
        return f"unavailable ({error})" if error else "unavailable"

    payload = {
        "ok": bool(workspace and workspace.ok and status and status.ok),
        "workspace": workspace_data,
        "runtime": status_data,
        "readiness": {
            "workspace": readiness(workspace),
            "runtime": readiness(status),
            "compute_worker": "unavailable",
            "optional_contribution_auth": "local-present-unverified" if contributor_key_present() else "no-local-key",
        },
        "effects": ["observe"],
        "authorization_required": False,
        "diagnostic": diagnostic,
    }
    if parsed.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print("Astrid status")
        for key, value in payload["readiness"].items():
            print(f"{key.replace('_', ' ')}: {value}")
        if not payload["ok"]:
            if status is not None and getattr(status, "ok", False):
                # The runtime answers; only the workspace check failed or ran out of time.
                print("next action: python -m astrid doctor (runtime is up; the workspace check "
                      + ("timed out" if workspace is None else "failed") + ")")
            else:
                print("next action: astrid setup")
    return 0 if payload["ok"] else 1


def _dispatch_doctor(args: list[str]) -> int:
    """Read the Runtime observer directly; never connect, start or provision."""
    import argparse
    import json
    import sys

    parser = argparse.ArgumentParser(prog="astrid doctor", description="Read-only runtime health check.")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--diagnostic", action="store_true", help="emit the strict C2 diagnostic document")
    parser.add_argument("--shared", action="store_true", help="redact the diagnostic for shared transport")
    if any(token in {"-h", "--help"} for token in args):
        parser.print_help()
        return 0
    parsed = parser.parse_args(args)
    try:
        from astrid.core.gateway.diagnostics import collect_diagnostic
        from astrid.runtime_cli import RuntimeCLI, RuntimeCLIError
        from astrid.sdk.storage_root import resolve_runtime_data_root

        runtime = RuntimeCLI()
        support_root = resolve_runtime_data_root()
        diagnostic, _workspace, observed = collect_diagnostic(
            runtime,
            support_root=support_root,
            mode="shared" if parsed.shared else "local",
            command="doctor",
        )
        from astrid.core.gateway.diagnostics import redact_for_shared
        report = dict(redact_for_shared(dict(observed.data))) if parsed.shared and observed else (dict(observed.data) if observed else {"ok": False})
        result_code = observed.returncode if observed else 1
    except (RuntimeCLIError, ValueError) as exc:
        payload = {
            "ok": False,
            "problem_code": getattr(exc, "code", "runtime_unavailable"),
            "state": "runtime_unavailable",
            "next_action": "astrid-runtime up",
            "effects": ["observe"],
            "authorization_required": False,
            "error": str(exc),
        }
        if not parsed.diagnostic:
            from astrid.core.gateway.diagnostics import expected_runtime_pairing

            payload["runtime_compatibility"] = {
                "status": "unknown",
                "expected": expected_runtime_pairing(),
                "observed": None,
                "fix": None,
                "reason": "runtime is not reachable",
            }
        if parsed.diagnostic:
            from astrid.core.gateway.diagnostics import build_diagnostic, _failure_from
            code, boundary = _failure_from(exc)
            payload = build_diagnostic(
                mode="shared" if parsed.shared else "local",
                facts={},
                problem_code=code,
                failure_boundary=boundary,
                next_actions=[{
                    "commandId": "runtime-up",
                    "arguments": ["--data-root", "<redacted:path>"] if parsed.shared else [],
                    "effects": ["start-stop-local-service", "configure-install", "write-relocate-change-data"],
                    "authorizationRequired": True,
                    "executable": not parsed.shared,
                }],
            )
            print(json.dumps(payload, indent=2, sort_keys=True))
            return 1
        if parsed.json:
            print(json.dumps(payload, indent=2, sort_keys=True))
        else:
            print(f"Astrid doctor: {payload['error']}", file=sys.stderr)
            print(f"next action: {payload['next_action']}", file=sys.stderr)
        return 1
    report.setdefault("effects", ["observe"])
    report.setdefault("authorization_required", False)
    report["diagnostic"] = diagnostic
    try:
        from astrid.sdk import visualize_cache
        from astrid.sdk.invocation import _visualize_cache_base

        report["visualize_cache"] = visualize_cache.cache_size(_visualize_cache_base())
    except (OSError, ValueError) as exc:
        report["visualize_cache"] = {"error": str(exc)}
    from pathlib import Path

    from astrid.core.execution.process_group import (
        CLEANUP_LATCH_NAME,
        describe_cleanup_latch,
        read_cleanup_latch,
    )

    pack_host_cleanup = read_cleanup_latch(Path(support_root) / CLEANUP_LATCH_NAME)
    report["pack_host_cleanup"] = pack_host_cleanup
    if pack_host_cleanup is not None:
        # A host blocked by cleanup uncertainty admits no work: not healthy.
        report["healthy"] = False
        report["issues"] = [*report.get("issues", []), describe_cleanup_latch(pack_host_cleanup)]
        result_code = 1
    from astrid.core.pack.loader import pack_quarantine_section

    report["pack_quarantine"] = pack_quarantine_section()
    from astrid.core.element.registry import element_skip_section

    report["element_skips"] = element_skip_section()
    from astrid.core.execution.generic_host import executor_skip_section

    report["executor_skips"] = executor_skip_section()
    from astrid.core.execution.guards import EVIDENCE_STATUS_NAME, read_evidence_status

    report["evidence_budget"] = read_evidence_status(Path(support_root) / EVIDENCE_STATUS_NAME)
    if parsed.diagnostic:
        print(json.dumps(diagnostic, indent=2, sort_keys=True))
        return 0 if diagnostic["problemCode"] is None else 1
    from astrid.core.gateway.diagnostics import runtime_compatibility

    compatibility = runtime_compatibility(runtime, support_root=str(support_root))
    report["runtime_compatibility"] = compatibility
    if compatibility["status"] == "mismatch":
        # A mismatched pair is not a healthy setup even when the Runtime itself is ready.
        report["healthy"] = False
        report["issues"] = [*report.get("issues", []), compatibility["reason"] + f"; fix: {compatibility['fix']}"]
        result_code = 1
    if parsed.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        state = report.get("state", "ready") if isinstance(report, dict) else "ready"
        print(f"Astrid doctor\nstate: {state}")
        for record in report.get("pack_quarantine", {}).get("quarantined", []):
            print(f"quarantined pack {record['pack_id']}: {record['error']}; fix: {record['fix']}")
        budget = report.get("evidence_budget")
        if budget:
            host = "alive" if budget.get("host_alive") else "host not running"
            print(
                f"evidence budget: {budget.get('charged_bytes')} of {budget.get('cap_bytes')} bytes "
                f"held by {budget.get('live_attempts')} live attempt(s) "
                f"(pack host pid {budget.get('pid')}, {host}, as of {budget.get('updated_at')})"
            )
        else:
            print("evidence budget: no pack host has reported yet")
        print(f"runtime compatibility: {compatibility['status']}")
        if compatibility["status"] != "ok":
            print(f"compatibility detail: {compatibility['reason']}")
        if compatibility["fix"]:
            print(f"fix: {compatibility['fix']}")
        if isinstance(report, dict) and report.get("pack_host_cleanup"):
            print(describe_cleanup_latch(report["pack_host_cleanup"]))
        if isinstance(report, dict) and report.get("recovery_action"):
            print(f"recovery action: {report['recovery_action']}")
    return result_code


def _dispatch_backup(args: list[str]) -> int:
    """Dispatch online backup, restore, export, and realm lifecycle routes."""
    import argparse
    import json
    import sys

    parser = argparse.ArgumentParser(prog="astrid backup", add_help=False)
    parser.add_argument("--json", action="store_true")
    sub = parser.add_subparsers(dest="operation", required=False)
    create = sub.add_parser("create", add_help=False)
    create.add_argument("destination", nargs="?")
    create.add_argument("--out", dest="out", default=None)
    create.add_argument("--json", action="store_true")
    restore = sub.add_parser("restore", add_help=False)
    restore.add_argument("backup")
    restore.add_argument("destination", nargs="?")
    restore.add_argument("--destination", dest="destination_flag", default=None)
    restore.add_argument("--json", action="store_true")
    export = sub.add_parser("export", add_help=False)
    export.add_argument("--out", dest="out", default=None)
    export.add_argument("--json", action="store_true")
    tombstone = sub.add_parser("tombstone", add_help=False)
    tombstone.add_argument("--reason", default=None)
    tombstone.add_argument("--expected-version", type=int, default=None)
    tombstone.add_argument("--json", action="store_true")
    recover = sub.add_parser("recover", add_help=False)
    recover.add_argument("--expected-realm-id", required=True)
    recover.add_argument("--expected-version", type=int, default=None)
    recover.add_argument("--json", action="store_true")
    purge = sub.add_parser("purge", add_help=False)
    purge.add_argument("confirmation")
    purge.add_argument("--json", action="store_true")

    if any(token in {"-h", "--help"} for token in args):
        parser.print_help()
        return 0
    parsed = parser.parse_args(args)
    json_mode = "--json" in args
    if parsed.operation is None:
        payload = {"ok": False, "state": "unavailable", "next_action": "banodoco-local up --profile astrid", "error": "backup operation is required"}
        if json_mode:
            print(json.dumps(payload, indent=2, sort_keys=True))
        else:
            print(f"Astrid backup: {payload['error']}", file=sys.stderr)
            print(f"next action: {payload['next_action']}", file=sys.stderr)
        return 1
    try:
        from astrid.sdk.client import AstridClient
        from astrid.sdk.exceptions import ServiceUnavailableError
        from astrid.sdk.workspace_client import WorkspaceClientError

        # Backup is a workspace-service operation.  It must remain available
        # while the optional pack worker is busy/down and must not reconcile a
        # caller's source checkout or interpreter.
        with AstridClient.open_from_launcher(start_pack_host=False) as client:
            if parsed.operation == "create":
                destination = parsed.destination or parsed.out
                if not destination:
                    parser.error("backup create requires DESTINATION or --out")
                result = client.create_backup(destination)
            elif parsed.operation == "restore":
                destination = parsed.destination_flag or parsed.destination
                if not destination:
                    parser.error("backup restore requires DESTINATION or --destination")
                result = client.restore_backup(parsed.backup, destination)
            elif parsed.operation == "export":
                result = client.export_realm()
            elif parsed.operation == "tombstone":
                result = client.tombstone_realm(reason=parsed.reason, expected_version=parsed.expected_version)
            elif parsed.operation == "recover":
                result = client.recover_realm(expected_realm_id=parsed.expected_realm_id, expected_version=parsed.expected_version)
            else:
                result = client.purge_realm(parsed.confirmation)
    except ServiceUnavailableError as exc:
        payload = {
            "ok": False,
            "state": "unavailable",
            "next_action": exc.details.get(
                "next_action", "banodoco-local up --profile astrid"
            ),
            "error": str(exc),
        }
        print(json.dumps(payload, indent=2, sort_keys=True) if json_mode else f"Astrid backup: {payload['error']}\nnext action: {payload['next_action']}", file=None if json_mode else sys.stderr)
        return 1
    except WorkspaceClientError as exc:
        payload = {"ok": False, "error": exc.code, "detail": exc.message, "details": exc.details}
        print(json.dumps(payload, indent=2, sort_keys=True) if json_mode else f"Astrid backup: {exc.message}", file=None if json_mode else sys.stderr)
        return 1

    payload = {"ok": True, **(result if isinstance(result, dict) else dict(result))}
    print(json.dumps(payload, indent=2, sort_keys=True) if json_mode else f"Astrid backup: {parsed.operation} complete")
    return 0


def _dispatch_pack(pack: str, args: list[str]) -> int:
    """Run a declared external-pack command through the Astrid runtime."""
    from .hivemind import dispatch

    return dispatch(pack, args)


def _dispatch_product(args: list[str]) -> int:
    """Run one product-family command through the remote SDK boundary."""
    from astrid.core.cli.domain_product import PRODUCT_FAMILY_SET, run_product_family

    if not args:
        raise AstridError(
            "a product family is required",
            valid_options=sorted(PRODUCT_FAMILY_SET),
            recovery_command="astrid projects --help",
            state_snapshot={"command": "product"},
        )
    family, rest = args[0], args[1:]
    if family not in PRODUCT_FAMILY_SET:
        raise AstridError(
            f"unknown product command '{family}'",
            valid_options=sorted(PRODUCT_FAMILY_SET),
            recovery_command="astrid --help",
            state_snapshot={"command": family},
        )

    if any(token in {"-h", "--help"} for token in rest):
        return run_product_family(family, rest, client=None)

    from astrid.sdk.client import AstridClient

    try:
        with AstridClient.open_from_launcher(
            start_pack_host=_product_command_needs_pack_host(family, rest)
        ) as client:
            return run_product_family(family, rest, client=client)
    except Exception as exc:
        from astrid.sdk.exceptions import ServiceUnavailableError

        if not isinstance(exc, ServiceUnavailableError):
            raise
        from astrid.core.cli.domain_output import print_result
        from astrid.sdk.contracts import DomainResult

        return print_result(
            DomainResult.failure(exc.to_error_object()),
            as_json="--json" in rest,
        )


def _dispatch_dev(args: list[str]) -> int:
    """Operator commands for developing against a running runtime: status and promote."""
    import argparse
    import json
    import sys
    from pathlib import Path

    parser = argparse.ArgumentParser(
        prog="astrid dev",
        description="Develop against a running runtime: report what it serves, or promote a ref to it.",
    )
    operations = parser.add_subparsers(dest="operation", required=True)
    status = operations.add_parser("status", help="served checkout, dev tree, pack host, and client/served pair check")
    status.add_argument("--json", action="store_true")
    promote = operations.add_parser(
        "promote",
        help="check REF out in the serve worktree and restart only the recorded pack host",
    )
    promote.add_argument("--ref", default=None, help="commit-ish to serve (default: the dev tree's HEAD)")
    promote.add_argument("--force", action="store_true", help="promote even with in-flight work (abandons it)")
    promote.add_argument("--json", action="store_true")
    cache = operations.add_parser("cache", help="reclaim durable evidence caches (timelines visualize)")
    cache_operations = cache.add_subparsers(dest="cache_operation", required=True)
    cache_prune = cache_operations.add_parser(
        "prune",
        help="evict least-recently-used visualize captures until within the cache limit",
    )
    cache_prune.add_argument("--visualize", action="store_true", required=True,
                             help="prune the timelines visualize evidence cache")
    cache_prune.add_argument("--keep-mb", type=float, default=None,
                             help="byte cap for this run in MB (default: ASTRID_VISUALIZE_CACHE_MB or 500)")
    cache_prune.add_argument("--json", action="store_true")
    parsed = parser.parse_args(args)

    if parsed.operation == "cache":
        return _dev_cache_prune(parsed)

    from astrid.core.gateway.dev_operator import (
        DevOperatorError,
        render_human,
        run_promote,
        run_status,
    )
    from astrid.sdk.storage_root import resolve_runtime_data_root

    try:
        data_root = Path(resolve_runtime_data_root())
        if parsed.operation == "status":
            payload = run_status(data_root=data_root)
        else:
            payload = run_promote(data_root=data_root, ref=parsed.ref, force=parsed.force)
    except (DevOperatorError, ValueError) as exc:
        payload = {
            "ok": False,
            "state": getattr(exc, "state", "error"),
            "error": str(exc),
            "next_action": getattr(exc, "next_action", None),
        }
    if parsed.json:
        print(json.dumps(payload, indent=2, sort_keys=True, default=str))
    elif payload.get("ok"):
        for line in render_human(payload):
            print(line)
    else:
        print(f"Astrid dev {parsed.operation}: {payload['error']}", file=sys.stderr)
        if payload.get("next_action"):
            print(f"next action: {payload['next_action']}", file=sys.stderr)
    return 0 if payload.get("ok") else 1


def _dev_cache_prune(parsed: Any) -> int:
    """`astrid dev cache prune --visualize`: reclaim the visualize evidence cache.

    Filesystem-only: it never connects to or starts the runtime.
    """
    import json
    import sys
    from astrid.sdk import visualize_cache
    from astrid.sdk.invocation import _visualize_cache_base

    try:
        limit = visualize_cache.max_cache_bytes(override_mb=parsed.keep_mb)
        report = visualize_cache.prune_visualize_cache(
            _visualize_cache_base(),
            max_bytes=limit,
            max_dirs=visualize_cache.DEFAULT_MAX_DIRS,
        )
    except (OSError, ValueError) as exc:
        payload = {"ok": False, "error": str(exc)}
        if parsed.json:
            print(json.dumps(payload, indent=2, sort_keys=True))
        else:
            print(f"Astrid dev cache prune: {exc}", file=sys.stderr)
        return 1
    if parsed.json:
        print(json.dumps({"ok": True, **report.as_dict()}, indent=2, sort_keys=True))
    else:
        for line in visualize_cache.format_report(report):
            print(line)
    return 0


def _product_command_needs_pack_host(family: str, args: list[str]) -> bool:
    """Return the declarative worker-host requirement for one product route.

    The command registry is the dependency source of truth: workspace CRUD
    and reads default to no pack host, while a capability that actually runs
    pack code opts in on its ``CommandSpec``. Unknown routes fail closed until
    argparse can return their normal usage error.
    """
    from astrid.core.cli.domain_product import command_requires_pack_host

    return command_requires_pack_host(family, args)


def _product_top_level_commands() -> frozenset[str]:
    from astrid.core.cli.domain_product import product_top_level_commands

    return product_top_level_commands()


_TOP_LEVEL_HANDLERS = {
    "projects": _dispatch_projects,
    "timelines": _dispatch_timelines,
    "media": _dispatch_media,
    "tasks": lambda args: _dispatch_product(["tasks", *args]),
    "runs": lambda args: _dispatch_product(["runs", *args]),
    "doctor": _dispatch_doctor,
    "backup": _dispatch_backup,
    "dev": _dispatch_dev,
}
_CORE_ROUTE_NAMES = frozenset(_TOP_LEVEL_HANDLERS)

# External packs opt into the CLI through the same declarative registry. This
# keeps pack installation and command exposure separate: a pack is available
# to the runtime without automatically claiming a top-level command.
from .hivemind import PACK_COMMANDS as _PACK_COMMANDS, installed_pack_ids as _installed_pack_ids

# Pack ids are discoverable, but never allowed to shadow a core family or an
# outer launcher command. This is a blocklist/precedence rule, not a second
# hand-maintained pack allowlist.
_PACK_ROUTE_BLOCKLIST = frozenset(
    {"agent", "auth", "help", "login", "setup", "status", "logout", "revoke", "--help", "--version"}
)
_DYNAMIC_PACK_ROUTE_NAMES: set[str] = set()


def _register_installed_pack_routes() -> None:
    """Register discovered pack ids lazily, preserving core route precedence."""
    for pack_name in _DYNAMIC_PACK_ROUTE_NAMES:
        _TOP_LEVEL_HANDLERS.pop(pack_name, None)
    _DYNAMIC_PACK_ROUTE_NAMES.clear()
    core_names = _CORE_ROUTE_NAMES
    for pack_name in sorted({spec.pack for spec in _PACK_COMMANDS} | _installed_pack_ids()):
        if pack_name in _PACK_ROUTE_BLOCKLIST or pack_name in core_names:
            continue
        _TOP_LEVEL_HANDLERS[pack_name] = (
            lambda args, pack=pack_name: _dispatch_pack(pack, args)
        )
        _DYNAMIC_PACK_ROUTE_NAMES.add(pack_name)


def _clear_dynamic_pack_routes() -> None:
    for pack_name in _DYNAMIC_PACK_ROUTE_NAMES:
        _TOP_LEVEL_HANDLERS.pop(pack_name, None)
    _DYNAMIC_PACK_ROUTE_NAMES.clear()


def compose_profile_handoff(
    manifest_path: str | "Path",
    *,
    support_root: str | "Path",
    registry: "Mapping[str, Any] | None" = None,
    fixtures: "Iterable[Any] | None" = None,
) -> dict[str, Any]:
    """Verify-or-stamp the B-6 handoff at the application composition root.

    This is intentionally separate from transport dispatch.  The generic host
    consumes the resulting stamp, but never discovers profiles or emits it.
    """

    from astrid.core._shared.boot_manifest import (
        manifest_hash,
        stamp_boot_manifest,
        validate_manifest_path,
    )
    from astrid.packs.shots.conformance import (
        VIBE_PROFILE_REGISTRY,
        vibe_profile_specs,
    )
    validated_manifest_path = validate_manifest_path(
        manifest_path, support_root, require_existing=False
    )

    active_registry = VIBE_PROFILE_REGISTRY if registry is None else registry
    active_fixtures = vibe_profile_specs() if fixtures is None else fixtures
    manifest = stamp_boot_manifest(
        validated_manifest_path,
        support_root=support_root,
        registry=active_registry,
        fixtures=active_fixtures,
    )
    return {
        "path": str(validated_manifest_path),
        "sha256": manifest_hash(manifest),
        "manifest": manifest,
    }


def emit_boot_manifest(
    manifest_path: str | "Path",
    *,
    support_root: str | "Path",
    registry: "Mapping[str, Any] | None" = None,
    fixtures: "Iterable[Any] | None" = None,
) -> dict[str, Any]:
    """Explicit composition-root name for callers that do not need routing."""
    return compose_profile_handoff(
        manifest_path,
        support_root=support_root,
        registry=registry,
        fixtures=fixtures,
    )
