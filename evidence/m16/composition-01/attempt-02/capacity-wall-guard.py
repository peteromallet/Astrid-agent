#!/usr/bin/env python3
"""Run one proof under M06 capacity stops and an independent 120s wall limit."""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

ABSOLUTE_FREE_STOP_KIB = 1_048_576
NET_DECLINE_STOP_KIB = 1_048_576
FIXTURE_LIMIT_BYTES = 128 * 1024 * 1024
WALL_SECONDS = 120.0


def free_kib(path: Path) -> int:
    return shutil.disk_usage(path).free // 1024


def tree_size(path: Path | None) -> int:
    if path is None or not path.exists():
        return 0
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file() and not item.is_symlink())


def terminate_group(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait(timeout=5)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cwd", required=True)
    parser.add_argument("--log", required=True)
    parser.add_argument("--receipt", required=True)
    parser.add_argument("--samples", required=True)
    parser.add_argument("--footprint-root", required=True)
    parser.add_argument("--footprint-limit-bytes", type=int, default=FIXTURE_LIMIT_BYTES)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        parser.error("supply a command after --")
    cwd = Path(args.cwd).resolve()
    log = Path(args.log).resolve()
    receipt = Path(args.receipt).resolve()
    samples = Path(args.samples).resolve()
    footprint = Path(args.footprint_root).resolve()
    for target in (log, receipt, samples):
        target.parent.mkdir(parents=True, exist_ok=True)
    start = dt.datetime.now(dt.timezone.utc)
    started = time.monotonic()
    baseline = free_kib(cwd)
    minimum = baseline
    peak = tree_size(footprint)
    trigger = None
    sample_count = 0
    with log.open("wb") as output, samples.open("w", newline="") as sample_file:
        writer = csv.writer(sample_file)
        writer.writerow(["timestamp_utc", "elapsed_seconds", "free_kib", "net_decline_kib", "fixture_bytes"])
        sample_file.flush()
        process = subprocess.Popen(command, cwd=cwd, stdout=output, stderr=subprocess.STDOUT, start_new_session=True, text=False)
        while True:
            current = free_kib(cwd)
            size = tree_size(footprint)
            minimum = min(minimum, current)
            peak = max(peak, size)
            elapsed = time.monotonic() - started
            decline = baseline - current
            sample_count += 1
            writer.writerow([dt.datetime.now(dt.timezone.utc).isoformat(), f"{elapsed:.3f}", current, decline, size])
            sample_file.flush()
            if current <= ABSOLUTE_FREE_STOP_KIB:
                trigger = "absolute_free_space_floor"
            elif decline >= NET_DECLINE_STOP_KIB:
                trigger = "net_decline_from_launch"
            elif size >= args.footprint_limit_bytes:
                trigger = "fixture_footprint_limit"
            elif elapsed >= WALL_SECONDS:
                trigger = "wall_clock_timeout"
            if trigger is not None and process.poll() is None:
                terminate_group(process)
                break
            if process.poll() is not None:
                break
            time.sleep(min(0.25, max(0.0, WALL_SECONDS - (time.monotonic() - started))))
        exit_code = process.wait()
    ended = dt.datetime.now(dt.timezone.utc)
    record = {
        "schema": "m16-composition-attempt-02-guard/v1",
        "started_at_utc": start.isoformat(),
        "ended_at_utc": ended.isoformat(),
        "duration_seconds": round((ended - start).total_seconds(), 3),
        "wall_limit_seconds": WALL_SECONDS,
        "cwd": str(cwd),
        "command": command,
        "exit_code": exit_code,
        "capacity_guard_triggered": trigger,
        "baseline_free_kib": baseline,
        "final_free_kib": free_kib(cwd),
        "minimum_observed_free_kib": minimum,
        "maximum_net_decline_kib": max(0, baseline - minimum),
        "absolute_free_stop_kib": ABSOLUTE_FREE_STOP_KIB,
        "net_decline_stop_kib": NET_DECLINE_STOP_KIB,
        "fixture_root": str(footprint),
        "fixture_footprint_limit_bytes": args.footprint_limit_bytes,
        "peak_fixture_footprint_bytes": peak,
        "sample_interval_seconds": 0.25,
        "sample_count": sample_count,
        "log": str(log),
        "samples": str(samples),
    }
    receipt.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(record, indent=2), flush=True)
    return 124 if trigger else exit_code


if __name__ == "__main__":
    raise SystemExit(main())
