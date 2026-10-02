"""Copy an already verified H3 object into the publication attempt spool."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path

from astrid.core.pack.entrypoint import guard_canonical_entrypoint, run_pack_main


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    candidates = parser.add_mutually_exclusive_group(required=True)
    candidates.add_argument("--verified-candidate", type=Path)
    candidates.add_argument("--candidate", type=Path, help="Legacy direct transform verified input.")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    source = (args.verified_candidate or args.candidate).expanduser()
    variant = "original" if args.verified_candidate else "final-composition"
    if source.is_symlink() or not source.is_file():
        raise ValueError("verified candidate must be a regular managed file")
    out = args.out.expanduser().resolve()
    out.mkdir(parents=True, exist_ok=True)
    manifest_path = out / "manifest.json"
    if manifest_path.is_symlink():
        raise ValueError("publication manifest is a symlink")
    manifest_path.unlink(missing_ok=True)
    candidate = out / "verified-candidate.mkv"
    if candidate.is_symlink():
        raise ValueError("publication candidate is a symlink")
    if source.resolve(strict=True) == candidate:
        raise ValueError("publication input must be distinct from the output spool")
    shutil.copyfile(source, candidate)
    digest = "sha256:" + hashlib.sha256(candidate.read_bytes()).hexdigest()
    if digest != "sha256:" + hashlib.sha256(source.read_bytes()).hexdigest():
        raise ValueError("verified candidate changed during publication finalization")
    manifest = {
        "schema_version": 1,
        "kind": "h3_av_publication_finalizer_result",
        "created": "h3_av.publication_finalizer.v1",
        "inputs": {"verified_candidate_sha256": digest},
        "warnings": [],
        "outputs": [{
            "name": "verified_candidate",
            "path": candidate.name,
            "ordinal": 0,
            "content_hash": digest,
            "bytes": candidate.stat().st_size,
            "media_type": "video/x-matroska",
            "output_port": "verified_candidate",
            "group_key": "main",
            "variant_key": variant,
            "selector": {"group_key": "main", "variant_key": variant},
            "role": "result",
            "is_primary": True,
        }],
    }
    temporary = out / ".manifest.json.tmp"
    temporary.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(manifest_path)
    return 0


if __name__ == "__main__":
    guard_canonical_entrypoint("h3_av.publication_finalizer")
    raise SystemExit(run_pack_main("h3_av.publication_finalizer", lambda: main()))
