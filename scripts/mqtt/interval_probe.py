#!/usr/bin/env python3
"""Ejecuta múltiples pruebas variando el intervalo de publicación."""
from __future__ import annotations

import argparse
import csv
import subprocess
from pathlib import Path
from typing import Dict, List

METRICS_DIR = Path(__file__).resolve().parents[2] / "data" / "metrics"
RUN_SCRIPT = Path(__file__).resolve().parents[0] / "run_stress_suite.py"


def list_csv() -> set[Path]:
    METRICS_DIR.mkdir(parents=True, exist_ok=True)
    return {p for p in METRICS_DIR.glob("*.csv") if p.is_file()}


def summarize(session_prefix: str, files: List[Path]) -> Dict[str, float]:
    records: List[Dict[str, str]] = []
    for path in files:
        with path.open(encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
        if rows:
            records.append(rows[-1])
    success = fail = rate = bw = 0.0
    weighted_lat = weighted_msgs = 0.0
    for row in records:
        s = float(row.get("successful_publishes") or 0)
        f = float(row.get("failed_publishes") or 0)
        success += s
        fail += f
        rate += float(row.get("messages_per_second") or 0)
        bw += float(row.get("bandwidth_mbps") or 0)
        lat = float(row.get("avg_latency_ms") or 0)
        weighted_lat += lat * s
        weighted_msgs += s
    total = success + fail
    return {
        "session": session_prefix,
        "success": success,
        "fail": fail,
        "total": total,
        "fail_pct": (fail / total * 100.0) if total else 0.0,
        "rate_msgs_per_s": rate,
        "bandwidth_mbps": bw,
        "avg_latency_ms": (weighted_lat / weighted_msgs) if weighted_msgs else 0.0,
    }


def detect_session(new_files: List[Path]) -> tuple[str, List[Path]]:
    if not new_files:
        raise RuntimeError("No se detectaron CSV nuevos tras la corrida")
    prefix = new_files[0].name.split("-s")[0]
    session_files = [p for p in new_files if p.name.startswith(prefix)]
    return prefix, session_files


def run_experiment(args: argparse.Namespace) -> None:
    intervals = args.intervals
    summaries: List[Dict[str, float]] = []
    for interval in intervals:
        print(f"\n=== Intervalo {interval}s ===")
        before = list_csv()
        cmd = [
            str(args.python),
            str(RUN_SCRIPT),
            "--skip-provision" if args.skip_provision else "",
            "--device-count",
            str(args.device_count),
            "--duration",
            str(args.duration),
            "--interval",
            str(interval),
            "--deactivate-after",
        ]
        cmd = [token for token in cmd if token]
        if args.extra:
            cmd.extend(args.extra)
        result = subprocess.run(cmd)
        if result.returncode != 0:
            print(f"[WARN] Corrida con intervalo {interval}s terminó con código {result.returncode}")
            continue
        after = list_csv()
        new_files = sorted(after - before)
        prefix, session_files = detect_session(new_files)
        summary = summarize(prefix, session_files)
        summary["interval"] = interval
        summaries.append(summary)
        print(
            f"Corrida {prefix}: msgs_ok={int(summary['success'])} "
            f"fallidos={int(summary['fail'])} lat_prom={summary['avg_latency_ms']:.2f}ms "
            f"throughput={summary['rate_msgs_per_s']:.2f} msg/s"
        )
    if summaries:
        print("\n=== Resumen ===")
        for item in summaries:
            print(
                f"Intervalo {item['interval']}s -> "
                f"session {item['session']} | msgs={int(item['total'])} "
                f"fallos={int(item['fail'])} ({item['fail_pct']:.2f}%) | "
                f"lat={item['avg_latency_ms']:.2f}ms | "
                f"rate={item['rate_msgs_per_s']:.2f} msg/s | "
                f"bw={item['bandwidth_mbps']:.3f} Mbps"
            )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Pruebas automáticas con múltiples intervalos")
    parser.add_argument(
        "--intervals",
        type=float,
        nargs="+",
        default=[1.0, 3.0, 10.0],
        help="Lista de intervalos en segundos (por defecto 1,3,10)",
    )
    parser.add_argument("--device-count", type=int, default=3000)
    parser.add_argument("--duration", type=float, default=120)
    parser.add_argument("--skip-provision", action="store_true", default=True)
    parser.add_argument("--python", default="python3")
    parser.add_argument("extra", nargs=argparse.REMAINDER, help="Argumentos adicionales para run_stress_suite")
    return parser.parse_args()


if __name__ == "__main__":
    run_experiment(parse_args())
