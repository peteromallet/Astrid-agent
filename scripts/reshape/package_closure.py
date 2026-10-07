"""Project canonical pack resources and verify staged-install parity."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Mapping

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from astrid.core.pack.canonical import BundledCatalog, CanonicalPackError


@dataclass(frozen=True)
class SourceResourceClosure:
    """The source files and digests that the existing package projection owns.

    ``paths`` remains the stable path-only projection used by the original
    closure probe.  ``digests`` is the corresponding content projection so a
    staged install can be checked without inventing a second manifest or
    storage layer.
    """

    paths: tuple[str, ...]
    errors: tuple[str, ...] = ()
    digests: Mapping[str, str] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.errors


@dataclass(frozen=True)
class StagedResourceParity:
    """Read-only comparison of one source closure with a staged install root."""

    missing: tuple[str, ...] = ()
    mismatched: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.missing and not self.mismatched and not self.errors


_EXCLUDED_TREE_PARTS = frozenset({"__pycache__", "tests", "golden", "build"})
_CONTENT_ONLY_EXCLUDED_NAMES = frozenset({".gitkeep", "STAGE.md", "requirements.txt"})


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_excluded_path(relative: Path, *, content: bool = False) -> bool:
    """Mirror the existing package-data exclusions without broad globs.

    Python sources remain part of an installed package, while content-root
    authoring material such as ``STAGE.md``, ``.gitkeep`` markers, and
    requirements files does not.
    Skill/documentation bundles are handled separately and retain their
    authored support files.
    """

    if any(part in _EXCLUDED_TREE_PARTS for part in relative.parts):
        return True
    if relative.suffix == ".pyc":
        return True
    return content and relative.name in _CONTENT_ONLY_EXCLUDED_NAMES


def _is_authoring_only(entry: object, relative: Path) -> bool:
    for exclusion in getattr(entry, "authoring_exclusions", ()):
        excluded = Path(exclusion.path)
        if relative == excluded or excluded in relative.parents:
            return True
    return False


def _packaged_path(root: Path, entry: object, relative: Path) -> str:
    pack_id = str(getattr(entry, "id"))
    return (Path("astrid") / "packs" / pack_id / relative).as_posix()


def check_source_resource_closure(repository_root: str | Path) -> SourceResourceClosure:
    root = Path(repository_root).expanduser().resolve()
    errors: list[str] = []
    digests: dict[str, str] = {}
    try:
        catalog = BundledCatalog.from_root(root / "astrid" / "packs")
    except CanonicalPackError as exc:
        return SourceResourceClosure((), (f"invalid bundled catalog: {exc}",))

    def add_file(
        entry: object,
        owner: Path,
        candidate: Path,
        *,
        relative: Path,
        content: bool = False,
        expected_digest: str | None = None,
    ) -> None:
        if _is_authoring_only(entry, relative) or _is_excluded_path(
            relative, content=content
        ):
            return
        projected = _packaged_path(root, entry, relative)
        try:
            resolved = candidate.resolve()
            if not resolved.is_relative_to(owner):
                errors.append(f"{projected}: resolved outside owner root")
                return
            if not candidate.is_file() or candidate.is_symlink():
                errors.append(f"{projected}: declared handle is not a regular file")
                return
            digest = expected_digest or _sha256_file(candidate)
        except OSError as exc:
            errors.append(f"{projected}: cannot inspect resolved handle: {exc}")
            return
        prior = digests.get(projected)
        if prior is not None and prior != digest:
            errors.append(f"{projected}: conflicting resource digests")
            return
        digests[projected] = digest

    def add_bundle(entry: object, owner: Path, bundle_root: Path) -> None:
        try:
            resolved_bundle = bundle_root.resolve()
            if not resolved_bundle.is_relative_to(owner):
                errors.append(
                    f"{getattr(entry, 'id', '<unknown>')}: "
                    f"documentation bundle escapes owner root: {bundle_root}"
                )
                return
            children = sorted(resolved_bundle.rglob("*"), key=lambda path: path.as_posix())
        except (OSError, ValueError) as exc:
            errors.append(f"documentation bundle {bundle_root}: cannot inspect: {exc}")
            return
        for child in children:
            if not child.is_file():
                continue
            try:
                relative = child.relative_to(owner)
            except ValueError:
                errors.append(f"documentation bundle {child}: resolved outside owner root")
                continue
            add_file(entry, owner, child, relative=relative)

    for entry in catalog.entries:
        owner = entry.root.resolve()
        for handle in (entry.manifest, *entry.resource_handles):
            add_file(
                entry,
                owner,
                handle.resolved,
                relative=Path(handle.path),
                content=handle.kind.startswith("content:"),
                expected_digest=handle.digest,
            )
        documentation = entry.documentation
        if documentation is not None and documentation.kind == "skill" and documentation.path:
            add_bundle(
                entry,
                owner,
                owner.joinpath(*documentation.path.split("/")).parent,
            )
    core_root = root / "astrid" / "packs" / "_core"
    core_candidates = (
        core_root / "docs" / "SKILL.md",
        core_root / "skill" / "SKILL.md",
    )
    core_skill = next((candidate for candidate in core_candidates if candidate.is_file()), core_candidates[0])
    core_root = core_skill.parent
    try:
        for child in sorted(core_root.rglob("*"), key=lambda path: path.as_posix()):
            if child.is_file():
                relative = child.relative_to(root / "astrid" / "packs" / "_core")
                projected = (Path("astrid") / "packs" / "_core" / relative).as_posix()
                if _is_excluded_path(relative):
                    continue
                if not child.is_symlink():
                    digests[projected] = _sha256_file(child)
    except OSError as exc:
        errors.append(f"{core_skill}: cannot inspect core skill bundle: {exc}")
    if not core_skill.is_file():
        errors.append(
            f"{core_skill.relative_to(root).as_posix()}: core census guidance is not a regular file"
        )
    ordered_digests = MappingProxyType(dict(sorted(digests.items())))
    return SourceResourceClosure(
        tuple(ordered_digests), tuple(sorted(set(errors))), ordered_digests
    )


def declared_source_resource_paths(repository_root: str | Path) -> tuple[str, ...]:
    closure = check_source_resource_closure(repository_root)
    if not closure.ok:
        raise ValueError("source resource closure failed: " + "; ".join(closure.errors))
    return closure.paths


def check_staged_resource_parity(
    repository_root: str | Path, staged_root: str | Path
) -> StagedResourceParity:
    """Check that every projected source file exists byte-for-byte when staged.

    ``staged_root`` may be the directory containing ``astrid/`` (for example an
    unpacked wheel root) or the installed ``astrid`` package directory.  Extra
    package files are intentionally not treated as failures: the existing
    package-data projection includes retained compatibility resources beyond
    the canonical declaration closure.
    """

    closure = check_source_resource_closure(repository_root)
    missing: list[str] = []
    mismatched: list[str] = []
    errors = list(closure.errors)
    staged = Path(staged_root).expanduser().resolve()
    for relative, expected in closure.digests.items():
        candidate = staged / relative
        if (
            not candidate.exists()
            and relative.startswith("astrid/")
            and staged.name == "astrid"
        ):
            candidate = staged / relative.removeprefix("astrid/")
        if not candidate.is_file():
            missing.append(relative)
            continue
        try:
            actual = _sha256_file(candidate)
        except OSError as exc:
            errors.append(f"{relative}: cannot inspect staged resource: {exc}")
            continue
        if actual != expected:
            mismatched.append(relative)
    return StagedResourceParity(
        tuple(sorted(missing)), tuple(sorted(mismatched)), tuple(sorted(set(errors)))
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", nargs="?", type=Path, default=Path.cwd())
    parser.add_argument(
        "--staged-root",
        type=Path,
        help="also compare the projected files with an unpacked/staged install root",
    )
    args = parser.parse_args(argv)
    closure = check_source_resource_closure(args.root)
    result = {
        "ok": closure.ok,
        "paths": closure.paths,
        "digests": dict(closure.digests),
        "errors": closure.errors,
    }
    if args.staged_root is not None:
        parity = check_staged_resource_parity(args.root, args.staged_root)
        result["staged"] = {
            "ok": parity.ok,
            "missing": parity.missing,
            "mismatched": parity.mismatched,
            "errors": parity.errors,
        }
        result["ok"] = result["ok"] and parity.ok
    print(json.dumps(result))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "SourceResourceClosure",
    "StagedResourceParity",
    "check_staged_resource_parity",
    "check_source_resource_closure",
    "declared_source_resource_paths",
]
