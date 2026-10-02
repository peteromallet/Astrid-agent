"""Canonical executor entrypoint for H3 request preparation."""

from __future__ import annotations

import argparse
from pathlib import Path
from astrid.core._shared.result_manifest import build_manifest, write_manifest

from astrid.core.pack.entrypoint import guard_canonical_entrypoint, run_pack_main

from astrid.packs.h3_av.src.prepare import prepare_request, write_preparation
from astrid.packs.h3_av.src.request import load_request
from astrid.packs.h3_av.src.input_bundle import bundle_digest, materialize_input_bundle


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Prepare an H3 audiovisual request.")
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--input-bundle", type=Path, required=True)
    parser.add_argument("--fps", type=float, default=24.0)
    parser.add_argument("--width", type=int, default=1024)
    parser.add_argument("--height", type=int, default=576)
    parser.add_argument("--sample-rate", type=int, default=48000)
    parser.add_argument("--out", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result_manifest = args.out.parent / "manifest.json"
    if result_manifest.is_symlink():
        raise ValueError("prepare result manifest is a symlink")
    result_manifest.unlink(missing_ok=True)
    request = load_request(args.request)
    asset_map, identities = materialize_input_bundle(
        request, args.input_bundle, args.out.parent / "request-assets"
    )
    manifest = prepare_request(
        request,
        asset_map=asset_map,
        fps=args.fps,
        width=args.width,
        height=args.height,
        sample_rate=args.sample_rate,
    )
    manifest["assets"] = identities
    manifest["input_bundle_sha256"] = bundle_digest(args.input_bundle)
    write_preparation(args.out, manifest)
    write_manifest(result_manifest, build_manifest(
        kind="h3_av_prepare_result", inputs={"input_bundle_sha256": manifest["input_bundle_sha256"]},
        outputs=[{"name": "preparation", "path": args.out.name, "output_port": "preparation",
                  "ordinal": 0, "role": "result", "is_primary": True}],
        created="h3_av.prepare.v1",
    ))
    print(f"h3_av.prepare: wrote {args.out}")
    return 0


if __name__ == "__main__":
    guard_canonical_entrypoint("h3_av.prepare")
    raise SystemExit(run_pack_main("h3_av.prepare", lambda: main()))
