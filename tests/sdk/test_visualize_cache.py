import contextlib
import io
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from astrid.core.gateway import dispatch
from astrid.sdk import invocation, visualize_cache

MB = 1024 * 1024
NOW = 10_000_000.0


def _digest(n: int) -> str:
    return f"{n:064x}"


def _capture(base: Path, project: str, n: int, *, size_mb: float, mtime: float) -> Path:
    path = base / project / _digest(n)
    path.mkdir(parents=True)
    (path / "page.png").write_bytes(b"x" * int(size_mb * MB))
    os.utime(path, (mtime, mtime))
    return path


def test_eviction_is_least_recently_used_first(tmp_path):
    base = tmp_path / "timeline-visualize"
    old = _capture(base, "almost-ready", 1, size_mb=4, mtime=NOW - 3600)
    mid = _capture(base, "almost-ready", 2, size_mb=4, mtime=NOW - 2000)
    new = _capture(base, "almost-ready", 3, size_mb=4, mtime=NOW - 1000)

    report = visualize_cache.prune_visualize_cache(
        base, max_bytes=9 * MB, now=NOW, protect=[], guard_seconds=900
    )

    assert [item.path for item in report.removed] == [old]
    assert not old.exists() and mid.exists() and new.exists()
    assert report.freed_bytes == 4 * MB
    assert report.after_bytes == 8 * MB


def test_evicts_until_both_byte_and_dir_limits_hold(tmp_path):
    base = tmp_path / "timeline-visualize"
    dirs = [_capture(base, "p", n, size_mb=1, mtime=NOW - 3600 + n) for n in range(5)]

    report = visualize_cache.prune_visualize_cache(
        base, max_bytes=100 * MB, max_dirs=3, now=NOW
    )

    assert [item.path for item in report.removed] == dirs[:2]
    assert report.after_dirs == 3


def test_newest_capture_and_protected_capture_are_never_evicted(tmp_path):
    base = tmp_path / "timeline-visualize"
    recent = _capture(base, "p", 1, size_mb=5, mtime=NOW - 60)  # inside the 15-minute guard
    protected = _capture(base, "p", 2, size_mb=5, mtime=NOW - 7200)  # just produced, but old mtime
    evictable = _capture(base, "p", 3, size_mb=5, mtime=NOW - 7000)

    report = visualize_cache.prune_visualize_cache(
        base, max_bytes=1 * MB, now=NOW, protect=[protected]
    )

    assert recent.exists() and protected.exists()
    assert not evictable.exists()
    assert {item.path for item in report.guarded} == {recent, protected}
    assert report.freed_bytes == 5 * MB


def test_only_digest_capture_dirs_are_candidates(tmp_path):
    base = tmp_path / "timeline-visualize"
    stray = base / "p" / "not-a-digest"
    stray.mkdir(parents=True)
    (stray / "keep.txt").write_bytes(b"x" * MB)
    os.utime(stray, (NOW - 9000, NOW - 9000))
    staging = base / "p" / ".abcdef123456-tmp"
    staging.mkdir()
    (staging / "partial").write_bytes(b"x" * MB)

    report = visualize_cache.prune_visualize_cache(base, max_bytes=0, max_dirs=0, now=NOW)

    assert report.removed == []
    assert stray.exists() and staging.exists()


def test_cap_env_var_and_override(monkeypatch):
    monkeypatch.delenv(visualize_cache.CACHE_MB_ENV, raising=False)
    assert visualize_cache.max_cache_bytes() == 500 * MB
    monkeypatch.setenv(visualize_cache.CACHE_MB_ENV, "120")
    assert visualize_cache.max_cache_bytes() == 120 * MB
    assert visualize_cache.max_cache_bytes(override_mb=7) == 7 * MB
    monkeypatch.setenv(visualize_cache.CACHE_MB_ENV, "lots")
    with pytest.raises(ValueError, match="ASTRID_VISUALIZE_CACHE_MB"):
        visualize_cache.max_cache_bytes()


def test_automatic_cap_protects_the_capture_just_produced(tmp_path, monkeypatch):
    base = tmp_path / "timeline-visualize"
    monkeypatch.setattr(invocation, "_visualize_cache_base", lambda cache_root=None: base)
    monkeypatch.setenv(visualize_cache.CACHE_MB_ENV, "1")
    stale = _capture(base, "p", 1, size_mb=3, mtime=0)
    produced = _capture(base, "p", 2, size_mb=3, mtime=0)

    invocation._cap_filmstrip_cache({"outputs": {"pack_root": str(produced)}}, project="p")

    assert produced.exists()
    assert not stale.exists()


def test_doctor_reports_cache_size_and_limits(tmp_path, monkeypatch):
    base = tmp_path / "timeline-visualize"
    _capture(base, "p", 1, size_mb=2, mtime=0)

    summary = visualize_cache.cache_size(base, max_bytes=500 * MB, max_dirs=200)

    assert summary["bytes"] == 2 * MB
    assert summary["dirs"] == 1
    assert summary["over_limit"] is False
    assert summary["limit_bytes"] == 500 * MB


def test_prune_verb_prints_removed_captures_and_bytes_freed(tmp_path, monkeypatch, capsys):
    base = tmp_path / "timeline-visualize"
    monkeypatch.setattr(invocation, "_visualize_cache_base", lambda cache_root=None: base)
    monkeypatch.delenv(visualize_cache.CACHE_MB_ENV, raising=False)
    old = _capture(base, "p", 1, size_mb=2, mtime=0)
    recent = _capture(base, "p", 2, size_mb=2, mtime=NOW + 3500)
    monkeypatch.setattr(visualize_cache, "time", SimpleNamespace(time=lambda: NOW + 3600))

    stdout = io.StringIO()
    with contextlib.redirect_stdout(stdout):
        code = dispatch._dispatch_dev(
            ["cache", "prune", "--visualize", "--keep-mb", "1", "--json"]
        )

    payload = json.loads(stdout.getvalue())
    assert code == 0
    assert payload["ok"] is True
    assert payload["freed_bytes"] == 2 * MB
    assert [entry["path"] for entry in payload["removed"]] == [str(old)]
    assert payload["after"] == {"bytes": 2 * MB, "dirs": 1}
    assert not old.exists() and recent.exists()


def test_prune_verb_human_output_names_bytes_freed(tmp_path, monkeypatch):
    base = tmp_path / "timeline-visualize"
    monkeypatch.setattr(invocation, "_visualize_cache_base", lambda cache_root=None: base)
    monkeypatch.setattr(visualize_cache, "time", SimpleNamespace(time=lambda: NOW))
    _capture(base, "p", 1, size_mb=3, mtime=0)

    stdout = io.StringIO()
    with contextlib.redirect_stdout(stdout):
        code = dispatch._dispatch_dev(["cache", "prune", "--visualize", "--keep-mb", "0"])

    text = stdout.getvalue()
    assert code == 0
    assert "removed:" in text
    assert "freed: 3.0 MB" in text
