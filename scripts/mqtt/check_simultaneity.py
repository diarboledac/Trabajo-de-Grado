#!/usr/bin/env python3
"""Analyze publish timestamps to estimate simultaneity across devices.

Reads an events.jsonl produced by mqtt_stress_async.py and reports, for a
given window size (in milliseconds), how many unique devices publish within
the same window. Useful to confirm whether devices are effectively sending
"at the same time" to stress the edge broker.

Usage examples:
  py -3 scripts/mqtt/check_simultaneity.py \
      --events data/logs/async-run-YYYYmmdd-HHMMSS-...-events.jsonl \
      --window-ms 100

  # Tighter window (50 ms) over the first 60 seconds of the file
  py -3 scripts/mqtt/check_simultaneity.py --events <file> --window-ms 50 --limit-sec 60
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, Tuple


@dataclass
class Stats:
    total_publish: int = 0
    unique_devices: int = 0
    best_window_devices: int = 0
    best_window_key: str = ""


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Check simultaneity of publish events by time window")
    p.add_argument("--events", type=Path, required=True, help="Path to events.jsonl from mqtt_stress_async")
    p.add_argument("--window-ms", type=int, default=100, help="Time window size in milliseconds (default 100)")
    p.add_argument(
        "--limit-sec",
        type=float,
        default=None,
        help="Optional cap on seconds to analyze from the start of the file",
    )
    return p.parse_args()


def iso_to_ms_key(ts: str, window_ms: int) -> Tuple[int, str]:
    # Fast ISO8601 slice to HH:MM, seconds, and microseconds part
    # Format example: 2025-10-29T23:01:26.343946+00:00
    base = ts.split("+")[0]
    hhmm = base[11:16]  # HH:MM
    sec = int(base[17:19])
    usec = int(base[20:26]) if len(base) >= 26 else 0
    total_ms = sec * 1000 + usec // 1000
    bucket = total_ms // max(1, window_ms)
    return bucket, f"{hhmm}:{sec:02d}.{bucket % (1000 // max(1, window_ms))}"


def analyze(events_path: Path, window_ms: int, limit_sec: float | None) -> Tuple[Stats, Dict[str, int]]:
    if not events_path.exists():
        raise SystemExit(f"No existe {events_path}")
    by_window: Dict[str, set[str]] = defaultdict(set)
    devices_seen: set[str] = set()
    stats = Stats()
    first_bucket: int | None = None

    with events_path.open(encoding="utf-8") as fh:
        for line in fh:
            try:
                rec = json.loads(line)
            except Exception:
                continue
            if rec.get("event") != "publish" or rec.get("status") != "success":
                continue
            ts = rec.get("timestamp")
            dev = rec.get("device")
            if not ts or not dev:
                continue
            bucket, key = iso_to_ms_key(ts, window_ms)
            if first_bucket is None:
                first_bucket = bucket
            if limit_sec is not None:
                # Stop if we moved beyond requested seconds from the start
                elapsed_ms = (bucket - first_bucket) * window_ms
                if elapsed_ms > limit_sec * 1000:
                    break
            by_window[key].add(str(dev))
            devices_seen.add(str(dev))
            stats.total_publish += 1

    # Aggregate
    per_window_counts = {k: len(v) for k, v in by_window.items()}
    if per_window_counts:
        best_key = max(per_window_counts, key=lambda k: per_window_counts[k])
        stats.best_window_key = best_key
        stats.best_window_devices = per_window_counts[best_key]
    stats.unique_devices = len(devices_seen)
    return stats, per_window_counts


def pct(n: int, d: int) -> float:
    return (100.0 * n / d) if d else 0.0


def main() -> None:
    args = parse_args()
    stats, per_window_counts = analyze(args.events, args.window_ms, args.limit_sec)

    # Distribution summary
    sorted_counts: Iterable[Tuple[str, int]] = sorted(
        per_window_counts.items(), key=lambda kv: kv[1], reverse=True
    )
    top = list(sorted_counts)[:5]
    share_full = sum(1 for _, c in per_window_counts.items() if c >= stats.unique_devices)
    share_90 = sum(1 for _, c in per_window_counts.items() if c >= max(1, int(0.9 * stats.unique_devices)))
    share_50 = sum(1 for _, c in per_window_counts.items() if c >= max(1, int(0.5 * stats.unique_devices)))

    print("Simultaneity check")
    print(f"  file            : {args.events}")
    print(f"  window size     : {args.window_ms} ms")
    if args.limit_sec is not None:
        print(f"  analyzed span   : first {args.limit_sec:.1f} s")
    print(f"  publish samples : {stats.total_publish}")
    print(f"  unique devices  : {stats.unique_devices}")
    print(f"  best window     : {stats.best_window_key} -> {stats.best_window_devices} devices" )
    if stats.unique_devices:
        print(
            f"  best coverage   : {pct(stats.best_window_devices, stats.unique_devices):.1f}% of devices"
        )

    if top:
        print("Top windows:")
        for key, cnt in top:
            print(f"  - {key}: {cnt} devices")

    total_windows = max(1, len(per_window_counts))
    print("Coverage (by window):")
    print(f"  >=100% devices   : {share_full}/{total_windows} windows ({pct(share_full, total_windows):.1f}%)")
    print(f"  >=90% devices    : {share_90}/{total_windows} windows ({pct(share_90, total_windows):.1f}%)")
    print(f"  >=50% devices    : {share_50}/{total_windows} windows ({pct(share_50, total_windows):.1f}%)")


if __name__ == "__main__":
    main()

