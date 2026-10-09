"""Generate a WAV speech artifact with Edge TTS, plus word-level timing."""

from __future__ import annotations

from astrid.core.pack.entrypoint import guard_canonical_entrypoint, warn_if_unledgered

guard_canonical_entrypoint("generation.generate_speech")

import argparse
import asyncio
import hashlib
import math
import os
import shutil
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from astrid.core._shared.result_manifest import complete_output_metadata
from astrid.core.contracts.errors import AstridError
from astrid.core.foundation.atomic_io import write_json_atomic
from astrid.core.generation import GENERATION_RESULT_KEY
from astrid.core.generation.backends.base import GenerationResult
from astrid.core.media import ffprobe_metadata

# Edge TTS reports WordBoundary offset/duration in 100 ns ticks.
_TICKS_PER_SECOND = 10_000_000
_SYNTHESIS_TIMEOUT_SECONDS = 120


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate speech using Edge TTS.")
    parser.add_argument("--text", required=True, help="Exact text to speak.")
    parser.add_argument("--provider", default="edge-tts")
    parser.add_argument("--voice", default="en-US-ChristopherNeural")
    parser.add_argument("--rate", default="+0%")
    parser.add_argument("--volume", default="+0%")
    parser.add_argument("--pitch", default="+0Hz")
    parser.add_argument("--out", type=Path, required=True)
    return parser


def _load_edge_tts() -> Any:
    try:
        import edge_tts
    except ImportError as exc:
        raise AstridError(
            "edge-tts is not installed in this Astrid environment",
            recovery_command="pip install 'astrid[speech]' (or pip install edge-tts), then retry",
        ) from exc
    return edge_tts


async def _stream_speech(
    edge_tts: Any,
    text: str,
    *,
    voice: str,
    rate: str,
    volume: str,
    pitch: str,
    proxy: str | None,
    mp3_path: Path,
) -> list[dict[str, Any]]:
    """Stream MP3 audio to ``mp3_path`` and return raw WordBoundary events."""
    communicate = edge_tts.Communicate(
        text,
        voice,
        rate=rate,
        volume=volume,
        pitch=pitch,
        boundary="WordBoundary",
        proxy=proxy,
    )
    words: list[dict[str, Any]] = []
    with mp3_path.open("wb") as handle:
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                handle.write(chunk["data"])
            elif chunk["type"] == "WordBoundary":
                words.append(chunk)
    return words


def _word_rows(raw_words: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for raw in raw_words:
        word = str(raw.get("text", ""))
        offset = int(raw.get("offset", -1))
        duration = int(raw.get("duration", -1))
        if offset < 0 or duration < 0:
            raise RuntimeError(f"edge-tts returned invalid word timing for {word!r}")
        if not word.strip():
            continue
        rows.append({
            "word": word,
            "start_s": round(offset / _TICKS_PER_SECOND, 6),
            "end_s": round((offset + duration) / _TICKS_PER_SECOND, 6),
        })
    return rows


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def generate_core(argv: list[str] | None = None) -> GenerationResult:
    args = build_parser().parse_args(argv)
    if args.provider != "edge-tts":
        raise AstridError(
            f"Unsupported speech provider {args.provider!r}; supported provider: edge-tts",
            valid_options=["edge-tts"],
            recovery_command="retry with provider='edge-tts'",
        )
    if not args.text.strip():
        raise AstridError("text must contain at least one non-whitespace character")

    edge_tts = _load_edge_tts()
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise AstridError(
            "Missing required speech executable: ffmpeg",
            recovery_command="install ffmpeg, then retry",
        )

    out = args.out.expanduser().resolve()
    audio_dir = out / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)
    wav_path = audio_dir / "speech.wav"
    mp3_path = audio_dir / "speech.mp3"
    provenance_path = out / "speech-provenance.json"
    words_path = out / "speech-words.json"
    # Remove stale staging files before invoking external tools, so a failure
    # cannot be mistaken for a successful artifact from an earlier attempt.
    wav_path.unlink(missing_ok=True)
    mp3_path.unlink(missing_ok=True)
    provenance_path.unlink(missing_ok=True)
    words_path.unlink(missing_ok=True)
    (out / "manifest.json").unlink(missing_ok=True)
    started = time.monotonic()
    warnings: list[str] = []
    proxy = os.environ.get("ASTRID_BROKER_PROXY") or None
    try:
        raw_words = asyncio.run(asyncio.wait_for(
            _stream_speech(
                edge_tts,
                args.text,
                voice=args.voice,
                rate=args.rate,
                volume=args.volume,
                pitch=args.pitch,
                proxy=proxy,
                mp3_path=mp3_path,
            ),
            timeout=_SYNTHESIS_TIMEOUT_SECONDS,
        ))
        if not mp3_path.is_file() or mp3_path.stat().st_size == 0:
            raise RuntimeError("edge-tts completed without producing audio")
        words = _word_rows(raw_words)
        if not words:
            raise RuntimeError("edge-tts produced audio without WordBoundary timing")
        subprocess.run(
            [ffmpeg, "-nostdin", "-v", "error", "-y", "-i", str(mp3_path),
             "-acodec", "pcm_s16le", str(wav_path)],
            check=True, capture_output=True, text=True, timeout=60,
        )
        if not wav_path.is_file() or wav_path.stat().st_size <= 44:
            raise RuntimeError("ffmpeg completed without producing a valid WAV")
        probe = ffprobe_metadata(wav_path)
        duration = probe.duration_seconds
        if duration is None or not math.isfinite(duration) or duration <= 0:
            raise RuntimeError("ffprobe could not determine positive WAV duration")
        if words[-1]["end_s"] > duration + 0.05:
            warnings.append(
                f"last word ends at {words[-1]['end_s']}s after measured WAV duration {duration:.3f}s"
            )
    except Exception as exc:  # noqa: BLE001 - subprocess/probe failures need shared cleanup.
        wav_path.unlink(missing_ok=True)
        mp3_path.unlink(missing_ok=True)
        raise AstridError(
            f"Speech generation failed: {exc}",
            recovery_command="check Edge TTS connectivity and ffmpeg, then retry",
        ) from exc
    finally:
        mp3_path.unlink(missing_ok=True)

    text_sha256 = hashlib.sha256(args.text.encode("utf-8")).hexdigest()
    audio_sha256 = _sha256_file(wav_path)
    settings = {
        "provider": args.provider,
        "voice": args.voice,
        "rate": args.rate,
        "volume": args.volume,
        "pitch": args.pitch,
    }
    provenance = {"schema_version": 1, "text": args.text,
                  "text_sha256": text_sha256, "settings": settings,
                  "audio_sha256": audio_sha256, "duration_seconds": duration}
    write_json_atomic(provenance_path, provenance)
    words_doc = {
        "schema_version": 1,
        "source": "edge-tts",
        "boundary": "WordBoundary",
        "duration_seconds": duration,
        "audio_sha256": audio_sha256,
        "words": words,
    }
    write_json_atomic(words_path, words_doc)
    outputs = [{
        "path": "audio/speech.wav", "name": "speech", "ordinal": 0,
        "role": "result", "is_primary": True,
        "content_hash": f"sha256:{audio_sha256}",
        "sha256": audio_sha256,
        "bytes": wav_path.stat().st_size,
        "duration_seconds": duration,
        "media_type": "audio/wav",
    }, {
        "path": "speech-provenance.json", "name": "speech_manifest", "ordinal": 1,
        "role": "auxiliary", "is_primary": False,
        "content_hash": f"sha256:{_sha256_file(provenance_path)}",
        "bytes": provenance_path.stat().st_size, "media_type": "application/json",
    }, {
        "path": "speech-words.json", "name": "speech_words", "ordinal": 2,
        "role": "auxiliary", "is_primary": False,
        "content_hash": f"sha256:{_sha256_file(words_path)}",
        "bytes": words_path.stat().st_size, "media_type": "application/json",
    }]
    manifest: dict[str, Any] = {
        "schema_version": 1,
        "kind": "generation.generate_speech",
        "inputs": {"text": args.text, **settings},
        "created": datetime.now(timezone.utc).isoformat(),
        "text": args.text,
        "text_sha256": text_sha256,
        "settings": settings,
        "outputs": outputs,
        "audio_sha256": audio_sha256,
        "duration_seconds": duration,
        "word_count": len(words),
        "warnings": warnings,
    }
    manifest["outputs"] = complete_output_metadata(outputs, root_dir=out)
    write_json_atomic(out / "manifest.json", manifest)
    return GenerationResult(
        image_paths=[wav_path],
        model_actual=f"edge-tts/{args.voice}",
        duration_ms=int((time.monotonic() - started) * 1000),
        applied_features=["text", "provider", "voice", "rate", "volume", "pitch"],
        manifest=manifest,
        run_dir=out,
    )


def run_sdk(argv: list[str] | None = None) -> dict[str, Any]:
    try:
        return {"returncode": 0, GENERATION_RESULT_KEY: generate_core(argv)}
    except AstridError as exc:
        return {"returncode": 1, "error": {
            "type": "AstridError", "cause": exc.cause,
            "recovery_command": exc.recovery_command,
            **({"valid_options": exc.valid_options} if exc.valid_options else {}),
        }}
    except SystemExit as exc:
        return {"returncode": 1, "error": {"type": "SystemExit", "message": str(exc.code)}}
    except Exception as exc:  # noqa: BLE001 - SDK entrypoints normalize unexpected failures.
        return {"returncode": 1, "error": {"type": type(exc).__name__, "message": str(exc)}}


def main(argv: list[str] | None = None) -> int:
    warn_if_unledgered()
    generate_core(argv)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
