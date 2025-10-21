#!/usr/bin/env python3
"""Render a summary of the most recent telemetry simulation run."""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Dict, List, Tuple

ROOT = Path(__file__).resolve().parents[1]
RUNS_DIR = ROOT / "data" / "runs"
LATEST_FILE = RUNS_DIR / "latest.json"


def load_summary() -> Dict:
    if not LATEST_FILE.exists():
        print("No existe data/runs/latest.json. Ejecuta scripts/send_telemetry.py primero.")
        raise SystemExit(1)
    return json.loads(LATEST_FILE.read_text(encoding="utf-8"))


def format_duration(seconds: float) -> str:
    mins, sec = divmod(int(seconds), 60)
    hrs, mins = divmod(mins, 60)
    if hrs:
        return f"{hrs}h {mins}m {sec}s"
    if mins:
        return f"{mins}m {sec}s"
    return f"{sec}s"


def top_items(counter: Counter, limit: int = 5) -> List[Tuple[str, int]]:
    return counter.most_common(limit)


def main() -> int:
    summary = load_summary()

    print("=" * 70)
    print("TELEMETRY SESSION SUMMARY")
    print("=" * 70)
    print(f"Session: {summary.get('session_id')}")
    print(f"Started: {summary.get('started_at')}")
    print(f"Ended  : {summary.get('ended_at')}")
    print(f"Duration: {format_duration(summary.get('duration_seconds', 0.0))}")
    mqtt_info = summary.get("mqtt", {})
    print(
        f"MQTT: {mqtt_info.get('host')}:{mqtt_info.get('port')} "
        f"(TLS={'on' if mqtt_info.get('tls') else 'off'})"
    )
    print(f"Interval: {summary.get('interval_seconds')}s")
    print(f"Devices: {summary.get('device_count')} | Messages: {summary.get('messages_sent')}")
    print(f"Log file: {summary.get('log_file')}")
    print()

    metrics = summary.get("metrics", {})
    if metrics:
        print("Global metrics:")
        connected_current = metrics.get("connected_devices", 0)
        connected_peak = metrics.get("peak_connected_devices", 0)
        print(f"  - Connected (current/peak): {connected_current}/{connected_peak}")
        channels_in_use = metrics.get("channels_in_use", connected_current)
        print(f"  - Channels in use: {channels_in_use}")
        failed_devices = metrics.get("failed_devices", 0)
        total_devices = metrics.get("total_devices", 0)
        print(f"  - Failed devices: {failed_devices} of {total_devices}")
        packets_sent = metrics.get("messages_sent", 0)
        packets_failed = metrics.get("messages_failed", 0)
        print(f"  - Packets sent/failed: {packets_sent} / {packets_failed}")
        data_volume_mb = float(metrics.get("data_volume_mb", 0.0))
        bytes_sent = metrics.get("bytes_sent", 0)
        print(f"  - Data volume: {data_volume_mb:.2f} MB ({bytes_sent} bytes)")
        bandwidth_mbps = float(metrics.get("bandwidth_mbps", 0.0))
        print(f"  - Bandwidth: {bandwidth_mbps:.3f} Mbps")
        msgs_per_second = float(metrics.get("messages_per_second", 0.0))
        print(f"  - Messages per second: {msgs_per_second:.3f}")
        avg_messages = float(metrics.get("avg_messages_per_device", 0.0))
        print(f"  - Average messages per device: {avg_messages:.2f}")
        avg_rate = float(metrics.get("avg_send_rate_per_device", 0.0))
        print(f"  - Average send rate per device: {avg_rate:.3f} msg/s")
        collapse_secs = metrics.get("collapse_time_seconds")
        collapse_reason = metrics.get("collapse_reason")
        if collapse_secs is not None:
            reason_label = f" due to {collapse_reason}" if collapse_reason else ""
            print(f"  - Collapse after: {collapse_secs:.1f}s{reason_label}")
        else:
            print("  - Collapse: not detected")
        disconnect_causes = metrics.get("disconnect_causes") or {}
        if disconnect_causes:
            print("  - Disconnect causes:")
            for reason, count in disconnect_causes.items():
                print(f"      * {reason}: {count}")
        print()

    device_data: Dict[str, Dict] = summary.get("devices", {})
    if not device_data:
        print("No per-device metrics found.")
        return 0

    issues_counter: Counter = Counter()
    error_counter: Counter = Counter()

    delayed_start: List[str] = []
    stalled_devices: List[str] = []

    for device, metrics in device_data.items():
        issues = metrics.get("last_issue")
        if issues:
            issues_counter[issues] += 1
        errors = metrics.get("errors", [])
        if errors:
            error_counter[device] = len(errors)
        if not metrics.get("first_publish_at"):
            stalled_devices.append(device)
        elif metrics.get("first_publish_at") == metrics.get("last_publish_at"):
            stalled_devices.append(device)
        connected_at = metrics.get("connected_at")
        first_publish_at = metrics.get("first_publish_at")
        if connected_at and first_publish_at and connected_at != first_publish_at:
            delayed_start.append(device)

    if issues_counter:
        print("Last reported issues (top 5):")
        for issue, count in top_items(issues_counter):
            print(f"  - {issue}: {count} devices")
        print()

    if error_counter:
        print("Devices with recorded errors:")
        for device, count in error_counter.most_common():
            print(f"  - {device}: {count} error events")
        print()

    if stalled_devices:
        print("Devices with stalled publishing (no telemetry or only one message):")
        for device in stalled_devices:
            print(f"  - {device}")
        print()

    if delayed_start:
        print("Devices with delayed first publish after connection:")
        for device in delayed_start:
            print(f"  - {device}")
        print()

    print("Per-device totals (subset):")
    print(f"{'Device':<15}{'Messages':>10}{'Disconnects':>15}{'Errors':>10}")
    print("-" * 50)
    for device, metrics in sorted(device_data.items())[:20]:
        disconnects = len(metrics.get("disconnects", []))
        errors = len(metrics.get("errors", []))
        messages = metrics.get("messages_sent", 0)
        print(f"{device:<15}{messages:>10}{disconnects:>15}{errors:>10}")

    remaining = len(device_data) - min(20, len(device_data))
    if remaining > 0:
        print(f"... ({remaining} dispositivos adicionales no listados)")

    print()
    print("Consulta detallada:")
    print(f"  - Reporte JSON completo: {summary.get('log_file', 'logs')} / data/runs/latest.json")
    print("  - Para inspeccionar un dispositivo especifico, revisa la seccion 'devices'")
    print()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
