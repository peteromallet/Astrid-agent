"""Markdown link extraction, rewriting, and integrity checks for skill views.

Links are read the way a reader's tools resolve them.  A relative target is
resolved from the directory that holds the link, with symlinked *directories*
followed physically (``packs/<id>`` is a directory symlink into the checkout)
but a symlinked *file* not followed, because the OS resolves ``..`` from the
directory that contains the link.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator
from urllib.parse import unquote

# Inline links and images: ``[text](target "title")``.  Targets may be wrapped
# in angle brackets.  Parentheses inside targets are not supported (none occur
# in the skill sources).
_INLINE_RE = re.compile(
    r"\[(?P<text>[^\]\n]*)\]\(\s*"
    r"(?P<target><[^<>\n]*>|[^()\s<>]+)"
    r"(?P<title>\s+(?:\"[^\"\n]*\"|'[^'\n]*'))?\s*\)"
)
# Reference definitions: ``[label]: target``.
_REFDEF_RE = re.compile(r"^(?P<lead>\s{0,3}\[[^\]\n]+\]:\s*)(?P<target><[^<>\n]*>|\S+)")
_FENCE_RE = re.compile(r"^\s*(```|~~~)")
_CODE_SPAN_RE = re.compile(r"`[^`\n]*`")
_SCHEME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")
_SKIP_DIRS = frozenset({"node_modules", "__pycache__", ".git", ".venv"})

# Returned by a rewrite callback to drop link markup while keeping the text.
DROP = object()


def split_target(raw: str) -> tuple[str | None, str]:
    """Split a raw link target into ``(local path, '#fragment' or '?query')``.

    Returns ``(None, "")`` for targets that are not local files: external URLs,
    ``mailto:``-style schemes, empty targets, and same-page anchors.
    """
    target = raw.strip()
    if target.startswith("<") and target.endswith(">"):
        target = target[1:-1]
    if not target or target.startswith("#") or _SCHEME_RE.match(target):
        return None, ""
    cut = len(target)
    for marker in ("#", "?"):
        index = target.find(marker)
        if index != -1:
            cut = min(cut, index)
    path, tail = target[:cut], target[cut:]
    if not path:
        return None, ""
    return unquote(path), tail


def _mask(line: str) -> str:
    """Blank inline code spans so match positions still line up with *line*."""
    return _CODE_SPAN_RE.sub(lambda m: " " * len(m.group(0)), line)


def _iter_prose_lines(text: str) -> Iterator[tuple[int, str]]:
    """Yield ``(line number, line)`` for lines outside fenced code blocks."""
    in_fence = False
    for number, line in enumerate(text.splitlines(keepends=True), 1):
        if _FENCE_RE.match(line):
            in_fence = not in_fence
            continue
        if not in_fence:
            yield number, line


def iter_link_targets(text: str) -> Iterator[tuple[int, str]]:
    """Yield ``(line number, raw target)`` for every link outside code."""
    for number, line in _iter_prose_lines(text):
        masked = _mask(line)
        for match in _INLINE_RE.finditer(masked):
            yield number, line[slice(*match.span("target"))]
        ref = _REFDEF_RE.match(masked)
        if ref:
            yield number, line[slice(*ref.span("target"))]


def rewrite_links(text: str, rewrite: Callable[[str], object]) -> str:
    """Return *text* with link targets rewritten by *rewrite*.

    ``rewrite(raw_target)`` returns the replacement target text, ``None`` to keep
    the link as written, or :data:`DROP` to keep only the link text.  External
    URLs, anchors, and anything inside code are never passed to *rewrite*.
    """
    out: list[str] = []
    in_fence = False
    for line in text.splitlines(keepends=True):
        if _FENCE_RE.match(line):
            in_fence = not in_fence
            out.append(line)
            continue
        if in_fence:
            out.append(line)
            continue
        masked = _mask(line)
        edits: list[tuple[int, int, str]] = []
        for match in _INLINE_RE.finditer(masked):
            start, end = match.span("target")
            result = rewrite(line[start:end])
            if result is None:
                continue
            if result is DROP:
                text_start, text_end = match.span("text")
                edits.append((match.start(), match.end(), line[text_start:text_end]))
            else:
                edits.append((start, end, str(result)))
        ref = _REFDEF_RE.match(masked)
        if ref:
            start, end = ref.span("target")
            result = rewrite(line[start:end])
            if isinstance(result, str):
                edits.append((start, end, result))
        for start, end, replacement in sorted(edits, reverse=True):
            line = line[:start] + replacement + line[end:]
        out.append(line)
    return "".join(out)


def iter_markdown_files(root: Path) -> Iterator[Path]:
    """Yield every ``.md`` file under *root*, following symlinked directories."""
    seen: set[str] = set()
    for dirpath, dirnames, filenames in os.walk(root, followlinks=True):
        real = os.path.realpath(dirpath)
        if real in seen:
            dirnames[:] = []
            continue
        seen.add(real)
        dirnames[:] = sorted(name for name in dirnames if name not in _SKIP_DIRS)
        for name in sorted(filenames):
            if name.endswith(".md"):
                yield Path(dirpath) / name


@dataclass(frozen=True)
class BrokenLink:
    file: str
    line: int
    target: str
    missing: str


@dataclass(frozen=True)
class LinkScan:
    root: str
    files: int
    checked: int
    broken: tuple[BrokenLink, ...]


def scan_links(root: Path) -> LinkScan:
    """Check every local markdown link target under *root* exists on disk."""
    files = 0
    checked = 0
    broken: list[BrokenLink] = []
    for md in iter_markdown_files(root):
        files += 1
        try:
            text = md.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            broken.append(BrokenLink(str(md), 0, "", f"unreadable: {exc}"))
            continue
        # Resolve from the physical directory that holds the link (the file's
        # own symlink, if any, is not followed).
        base = Path(os.path.realpath(md.parent))
        for line, raw in iter_link_targets(text):
            path, _tail = split_target(raw)
            if path is None:
                continue
            checked += 1
            target = Path(path) if os.path.isabs(path) else base / path
            if not os.path.exists(target):
                broken.append(BrokenLink(str(md), line, raw, os.path.normpath(str(target))))
    return LinkScan(str(root), files, checked, tuple(broken))


__all__ = [
    "DROP",
    "BrokenLink",
    "LinkScan",
    "iter_link_targets",
    "iter_markdown_files",
    "rewrite_links",
    "scan_links",
    "split_target",
]
