#!/usr/bin/env python3
"""High pressure ThingsBoard telemetry simulator with synchronized bursts."""
from __future__ import annotations

import json
import os
import random
import signal
import string
import threading
import time
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from threading import Barrier, BrokenBarrierError
from typing import Dict, Optional

import paho.mqtt.client as mqtt
import requests
from dotenv import load_dotenv
from flask import Flask, jsonify, render_template_string
from werkzeug.serving import make_server

from tb import TB, TBError

load_dotenv(override=True)

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
TOKENS_FILE = DATA_DIR / "tokens.json"

RUNNING = True


@dataclass(frozen=True)
class SimulatorConfig:
    """Runtime configuration resolved from environment variables."""

    tb_url: str
    tb_username: str
    tb_password: str
    device_prefix: str = "sim"
    device_count: int = 500
    device_label: str = "sim-lab"
    device_type: str = "sensor"
    device_profile_id: Optional[str] = None
    mqtt_host: str = "127.0.0.1"
    mqtt_port: int = 1883
    mqtt_tls: bool = False
    publish_interval: float = 1.0
    dashboard_host: str = "0.0.0.0"
    dashboard_port: int = 5000
    dashboard_refresh_ms: int = 2000
    provision_retries: int = 5
    provision_retry_delay: float = 2.0
    start_lead_time: float = 0.3

    @staticmethod
    def load() -> "SimulatorConfig":
        max_devices = 500
        requested = int(os.getenv("DEVICE_COUNT", str(max_devices)))
        device_count = min(requested, max_devices)
        return SimulatorConfig(
            tb_url=os.getenv("TB_URL", "").rstrip("/"),
            tb_username=os.getenv("TB_USERNAME", ""),
            tb_password=os.getenv("TB_PASSWORD", ""),
            device_prefix=os.getenv("DEVICE_PREFIX", "sim"),
            device_count=device_count,
            device_label=os.getenv("DEVICE_LABEL", "sim-lab"),
            device_type=os.getenv("DEVICE_TYPE", "sensor"),
            device_profile_id=os.getenv("DEVICE_PROFILE_ID") or None,
            mqtt_host=os.getenv("MQTT_HOST", "127.0.0.1"),
            mqtt_port=int(os.getenv("MQTT_PORT", "1883")),
            mqtt_tls=os.getenv("MQTT_TLS", "0") == "1",
            publish_interval=float(os.getenv("PUBLISH_INTERVAL_SEC", "1")),
            dashboard_host=os.getenv("DASHBOARD_HOST", "0.0.0.0"),
            dashboard_port=int(os.getenv("DASHBOARD_PORT", "5000")),
            dashboard_refresh_ms=int(os.getenv("DASHBOARD_REFRESH_MS", "2000")),
            provision_retries=int(os.getenv("PROVISION_RETRIES", "5")),
            provision_retry_delay=float(os.getenv("PROVISION_RETRY_DELAY", "2.0")),
            start_lead_time=float(os.getenv("SIM_START_LEAD_TIME", "0.3")),
        )


DASHBOARD_TEMPLATE = """
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Telemetry Metrics Dashboard</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>
    :root {
      color-scheme: light dark;
      --bg: #f4f6fb;
      --bg-card: #ffffff;
      --bg-alt: #20232a;
      --text: #1b1b1b;
      --text-alt: #ffffff;
      --accent: #2c7be5;
      --danger: #e55353;
      --warn: #ffb020;
      --muted: #6b7280;
    }
    body { margin: 0; font-family: "Segoe UI", Roboto, sans-serif; background: var(--bg); color: var(--text); }
    header { background: var(--bg-alt); color: var(--text-alt); padding: 16px 24px; }
    h1 { margin: 0; font-size: 1.6rem; }
    main { padding: 24px; display: flex; flex-direction: column; gap: 24px; }
    .card { background: var(--bg-card); border-radius: 12px; box-shadow: 0 2px 6px rgba(0, 0, 0, 0.08); padding: 24px; }
    .card h2 { margin-top: 0; font-size: 1.2rem; }
    canvas { max-width: 100%; }
    table { width: 100%; border-collapse: collapse; border-spacing: 0; }
    th, td { padding: 8px 12px; text-align: left; border-bottom: 1px solid #e1e5ee; }
    th { background: #f9fafc; font-weight: 600; }
    .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 16px; }
    .metric { background: #f0f4ff; border-radius: 10px; padding: 12px 16px; border-left: 4px solid var(--accent); }
    .metric h3 { margin: 0; font-size: 0.9rem; color: #4c5c96; }
    .metric span { font-weight: 600; font-size: 1.15rem; }
    .pill { display: inline-flex; align-items: center; padding: 4px 10px; border-radius: 999px; font-size: 0.8rem; font-weight: 600; }
    .pill.ok { background: rgba(34, 197, 94, 0.12); color: #15803d; }
    .pill.warn { background: rgba(234, 179, 8, 0.12); color: #b45309; }
    .pill.danger { background: rgba(220, 38, 38, 0.12); color: #B91C1C; }
    .muted { color: var(--muted); font-size: 0.85rem; }
    .table-note { margin-top: 8px; color: var(--muted); font-size: 0.85rem; }
    @media (max-width: 720px) {
      main { padding: 16px; }
      .grid { grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); }
    }
  </style>
  <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
</head>
<body>
  <header>
    <h1>Telemetry Metrics Dashboard</h1>
  </header>
  <main>
    <section class="card">
      <canvas id="connectedChart" height="120"></canvas>
    </section>
    <section class="card">
      <h2>Current Metrics</h2>
      <div class="grid">
        <div class="metric"><h3>Requested Devices</h3><span id="requested_devices">0</span></div>
        <div class="metric"><h3>Connected</h3><span id="connected_devices">0</span></div>
        <div class="metric"><h3>Disconnected</h3><span id="disconnected_devices">0</span></div>
        <div class="metric"><h3>Failed</h3><span id="failed_devices">0</span></div>
        <div class="metric"><h3>Packets Sent</h3><span id="total_packets_sent">0</span></div>
        <div class="metric"><h3>Packets Failed</h3><span id="total_packets_failed">0</span></div>
        <div class="metric"><h3>Total Volume (MB)</h3><span id="total_volume_mb">0.000</span></div>
        <div class="metric"><h3>Avg Rate (msg/s)</h3><span id="avg_rate">0.000</span></div>
        <div class="metric"><h3>Bandwidth (Mbps)</h3><span id="bandwidth">0.000</span></div>
        <div class="metric"><h3>Active Channels</h3><span id="active_channels">0</span></div>
        <div class="metric"><h3>Interval (s)</h3><span id="interval_sec">0.0</span></div>
        <div class="metric"><h3>Collapse Time</h3><span id="collapse_time">n/a</span></div>
      </div>
      <p class="table-note">Last update: <span id="last_update">n/a</span></p>
    </section>
    <section class="card">
      <h2>Failure Breakdown</h2>
      <table>
        <thead>
          <tr>
            <th>Reason</th>
            <th>Occurrences</th>
          </tr>
        </thead>
        <tbody id="failure_table"></tbody>
      </table>
      <p class="table-note">Reasons group similar transport issues to highlight bottlenecks.</p>
    </section>
    <section class="card">
      <h2>Device Health Snapshot</h2>
      <table>
        <thead>
          <tr>
            <th>Device</th>
            <th>Status</th>
            <th>Stage</th>
            <th>Last Telemetry</th>
            <th>Last Failure</th>
            <th>Cause</th>
            <th>Details</th>
          </tr>
        </thead>
        <tbody id="device_table"></tbody>
      </table>
      <p class="table-note">Showing devices with recent issues (up to 40 entries). Times are local to your browser.</p>
    </section>
  </main>
  <script>
    const refreshMs = {{ refresh_ms }};
    const maxPoints = 240;
    const ctx = document.getElementById('connectedChart').getContext('2d');
    const chartConfig = {
      type: 'line',
      data: {
        labels: [],
        datasets: [
          {
            label: 'Connected Devices',
            data: [],
            borderColor: '#2c7be5',
            backgroundColor: 'rgba(44, 123, 229, 0.15)',
            tension: 0.25
          },
          {
            label: 'Failed or Disconnected',
            data: [],
            borderColor: '#e55353',
            backgroundColor: 'rgba(229, 83, 83, 0.15)',
            tension: 0.25
          }
        ]
      },
      options: {
        responsive: true,
        interaction: { mode: 'index', intersect: false },
        scales: {
          y: { beginAtZero: true, ticks: { precision: 0 } }
        }
      }
    };
    const metricChart = new Chart(ctx, chartConfig);

    function toLocalTime(ts) {
      if (ts === null || ts === undefined) { return 'n/a'; }
      return new Date(ts * 1000).toLocaleString();
    }

    function formatNumber(value, digits = 3) {
      return Number.parseFloat(value).toFixed(digits);
    }

    function updateChart(snapshot) {
      const label = new Date(snapshot.timestamp * 1000).toLocaleTimeString();
      metricChart.data.labels.push(label);
      metricChart.data.datasets[0].data.push(snapshot.connected_devices);
      metricChart.data.datasets[1].data.push(snapshot.failed_or_disconnected);
      if (metricChart.data.labels.length > maxPoints) {
        metricChart.data.labels.shift();
        metricChart.data.datasets.forEach(ds => ds.data.shift());
      }
      metricChart.update('none');
    }

    function updateMetrics(snapshot) {
      document.getElementById('requested_devices').textContent = snapshot.device_count;
      document.getElementById('connected_devices').textContent = snapshot.connected_devices;
      document.getElementById('disconnected_devices').textContent = snapshot.disconnected_devices;
      document.getElementById('failed_devices').textContent = snapshot.failed_devices;
      document.getElementById('total_packets_sent').textContent = snapshot.total_packets_sent;
      document.getElementById('total_packets_failed').textContent = snapshot.total_packets_failed;
      document.getElementById('total_volume_mb').textContent = formatNumber(snapshot.total_volume_mb);
      document.getElementById('avg_rate').textContent = formatNumber(snapshot.avg_send_rate_per_device);
      document.getElementById('bandwidth').textContent = formatNumber(snapshot.bandwidth_mbps);
      document.getElementById('active_channels').textContent = snapshot.active_channels;
      document.getElementById('interval_sec').textContent = formatNumber(snapshot.interval_sec, 2);
      document.getElementById('collapse_time').textContent = snapshot.collapse_time === null ? 'n/a' : formatNumber(snapshot.collapse_time, 2) + ' s';
      document.getElementById('last_update').textContent = toLocalTime(snapshot.timestamp);
    }

    function updateFailureTable(snapshot) {
      const body = document.getElementById('failure_table');
      body.innerHTML = '';
      const rows = Object.entries(snapshot.failure_breakdown || {}).sort((a, b) => b[1] - a[1]);
      if (rows.length === 0) {
        const row = document.createElement('tr');
        row.innerHTML = '<td colspan="2" class="muted">No failures recorded</td>';
        body.appendChild(row);
        return;
      }
      rows.forEach(([reason, count]) => {
        const row = document.createElement('tr');
        row.innerHTML = `<td>${reason}</td><td>${count}</td>`;
        body.appendChild(row);
      });
    }

    function statusPill(status) {
      const span = document.createElement('span');
      span.classList.add('pill');
      if (status === 'connected') {
        span.classList.add('ok');
      } else if (status === 'disconnected') {
        span.classList.add('warn');
      } else {
        span.classList.add('danger');
      }
      span.textContent = status;
      return span;
    }

    function updateDeviceTable(snapshot) {
      const body = document.getElementById('device_table');
      body.innerHTML = '';
      const troubled = (snapshot.devices || []).filter(d => d.status !== 'connected');
      troubled.sort((a, b) => {
        const ta = a.last_failure || 0;
        const tb = b.last_failure || 0;
        return tb - ta;
      });
      const visible = troubled.slice(0, 40);
      if (visible.length === 0) {
        const row = document.createElement('tr');
        row.innerHTML = '<td colspan="7" class="muted">All devices are healthy</td>';
        body.appendChild(row);
        return;
      }
      visible.forEach(device => {
        const row = document.createElement('tr');
        const statusCell = document.createElement('td');
        statusCell.appendChild(statusPill(device.status));
        row.appendChild(document.createElement('td')).textContent = device.name;
        row.appendChild(statusCell);
        row.appendChild(document.createElement('td')).textContent = device.last_stage || '-';
        row.appendChild(document.createElement('td')).textContent = toLocalTime(device.last_seen);
        row.appendChild(document.createElement('td')).textContent = toLocalTime(device.last_failure);
        row.appendChild(document.createElement('td')).textContent = device.failure_reason || '-';
        row.appendChild(document.createElement('td')).textContent = device.failure_detail || '-';
        body.appendChild(row);
      });
    }

    async function refreshMetrics() {
      try {
        const response = await fetch('/metrics');
        if (!response.ok) {
          throw new Error('Failed to fetch metrics');
        }
        const payload = await response.json();
        updateChart(payload);
        updateMetrics(payload);
        updateFailureTable(payload);
        updateDeviceTable(payload);
      } catch (error) {
        console.error('Metrics refresh error:', error);
      }
    }

    refreshMetrics();
    setInterval(refreshMetrics, refreshMs);
  </script>
</body>
</html>
"""


@dataclass
class DeviceStats:
    """Mutable in-memory view for a single simulated device."""

    name: str
    status: str = "pending"
    last_stage: str = "startup"
    last_seen: Optional[float] = None
    last_failure: Optional[float] = None
    failure_reason: Optional[str] = None
    failure_detail: Optional[str] = None
    disconnect_code: Optional[int] = None
    total_packets: int = 0
    total_failures: int = 0


@dataclass(frozen=True)
class DeviceStatusSnapshot:
    """Serialized snapshot pushed to the dashboard API."""

    name: str
    status: str
    last_stage: str
    last_seen: Optional[float]
    last_failure: Optional[float]
    failure_reason: Optional[str]
    failure_detail: Optional[str]
    disconnect_code: Optional[int]


@dataclass(frozen=True)
class MetricsSnapshot:
    timestamp: float
    device_count: int
    interval_sec: float
    connected_devices: int
    disconnected_devices: int
    failed_devices: int
    failed_or_disconnected: int
    active_channels: int
    total_packets_sent: int
    total_packets_failed: int
    total_volume_mb: float
    avg_send_rate_per_device: float
    bandwidth_mbps: float
    collapse_time: Optional[float]
    failure_breakdown: Dict[str, int]
    devices: list[DeviceStatusSnapshot]


def classify_failure(stage: str, rc: int | mqtt.MQTTErrorCode | None = None, exc: Exception | None = None) -> tuple[str, str]:
    """Group low level error codes into human readable buckets."""

    if exc is not None:
        if isinstance(exc, MemoryError):
            return ("client-memory", "MemoryError while handling payload")
        if isinstance(exc, TimeoutError):
            return ("network-timeout", f"Timeout: {exc}")
        if isinstance(exc, OSError):
            return ("network", f"{exc.__class__.__name__}: {exc}")
        return ("internal-error", f"{exc.__class__.__name__}: {exc}")

    if rc is None:
        return ("unknown", "Unknown failure cause")

    rc_val = int(rc)

    connect_reasons = {
        1: ("protocol", "Unacceptable protocol version"),
        2: ("client-id", "Client identifier rejected"),
        3: ("broker", "Server unavailable"),
        4: ("auth", "Bad username or password"),
        5: ("auth", "Not authorized"),
    }

    error_map = {
        int(mqtt.MQTT_ERR_AGAIN): ("network", "Resource temporarily unavailable"),
        int(mqtt.MQTT_ERR_CONN_LOST): ("network", "Connection lost"),
        int(mqtt.MQTT_ERR_CONN_REFUSED): ("network", "Connection refused"),
        int(mqtt.MQTT_ERR_NO_CONN): ("network", "Client not connected"),
        int(mqtt.MQTT_ERR_PROTOCOL): ("protocol", "Protocol error"),
        int(mqtt.MQTT_ERR_NOMEM): ("client-memory", "Out of memory"),
        int(mqtt.MQTT_ERR_PAYLOAD_SIZE): ("payload", "Payload too large for broker"),
        int(mqtt.MQTT_ERR_QUEUE_SIZE): ("client-backpressure", "Local queue is full"),
        int(mqtt.MQTT_ERR_TLS): ("tls", "TLS handshake failed"),
        int(mqtt.MQTT_ERR_AUTH): ("auth", "Authentication error"),
        int(mqtt.MQTT_ERR_ACL_DENIED): ("auth", "ACL denied"),
        int(mqtt.MQTT_ERR_NOT_SUPPORTED): ("client", "Operation not supported"),
        int(mqtt.MQTT_ERR_KEEPALIVE): ("network", "Keepalive failure"),
        int(mqtt.MQTT_ERR_ERRNO): ("network", "System socket error"),
    }

    if stage == "connect":
        if rc_val == 0:
            return ("ok", "Connected successfully")
        if rc_val in connect_reasons:
            label, detail = connect_reasons[rc_val]
            return (label, detail)

    if rc_val in error_map:
        label, detail = error_map[rc_val]
        return (label, detail)

    if stage == "disconnect" and rc_val == 0:
        return ("client-request", "Client requested disconnect")

    if stage == "connect" and hasattr(mqtt, "connack_string"):
        return ("broker", mqtt.connack_string(rc_val))

    if hasattr(mqtt, "error_string"):
        return ("broker", mqtt.error_string(rc_val))

    return ("broker", f"rc={rc_val}")


class MetricsCollector:
    """Thread-safe aggregator tracking global and per-device statistics."""

    def __init__(self, device_count: int, interval: float) -> None:
        self._device_count = device_count
        self._interval = interval
        self._lock = threading.Lock()
        self._start_ts = time.perf_counter()
        self._start_wall = time.time()
        self._registered_devices: set[str] = set()
        self._connected: set[str] = set()
        self._failed_devices: set[str] = set()
        self._devices: dict[str, DeviceStats] = {}
        self._total_packets_sent = 0
        self._total_packets_failed = 0
        self._total_bytes = 0
        self._collapse_time: float | None = None
        self._failure_breakdown: Counter[str] = Counter()

    def register_device(self, device: str) -> None:
        with self._lock:
            self._registered_devices.add(device)
            self._devices.setdefault(device, DeviceStats(name=device))

    def record_connect(self, device: str) -> None:
        with self._lock:
            stats = self._devices.setdefault(device, DeviceStats(name=device))
            stats.status = "connected"
            stats.last_stage = "connect"
            stats.disconnect_code = 0
            self._connected.add(device)
            self._failed_devices.discard(device)

    def record_connect_failure(self, device: str, rc: int) -> None:
        reason, detail = classify_failure("connect", rc=rc)
        self.record_failure(device, reason, detail, stage="connect", disconnect_code=rc)

    def record_disconnect(self, device: str, rc: int) -> None:
        rc_val = int(rc)
        if rc_val != 0:
            reason, detail = classify_failure("disconnect", rc=rc_val)
            self.record_failure(device, reason, detail, stage="disconnect", disconnect_code=rc_val)
            return
        with self._lock:
            stats = self._devices.setdefault(device, DeviceStats(name=device))
            stats.status = "disconnected"
            stats.last_stage = "disconnect"
            stats.disconnect_code = 0
            self._connected.discard(device)
            self._failed_devices.discard(device)
            if RUNNING and not self._connected and self._collapse_time is None:
                self._collapse_time = time.perf_counter() - self._start_ts

    def record_publish(self, device: str, payload_size: int, success: bool, rc: int | None = None) -> None:
        now = time.time()
        with self._lock:
            stats = self._devices.setdefault(device, DeviceStats(name=device))
            stats.total_packets += 1
            self._total_packets_sent += 1
            self._total_bytes += payload_size
            if success:
                stats.status = "connected"
                stats.last_stage = "publish"
                stats.last_seen = now
                self._connected.add(device)
                self._failed_devices.discard(device)
            else:
                self._total_packets_failed += 1
                reason, detail = classify_failure("publish", rc=rc)
                self._mark_failure_locked(device, reason, detail, "publish", None, now)

    def record_exception(self, device: str, exc: Exception) -> None:
        reason, detail = classify_failure("exception", exc=exc)
        self.record_failure(device, reason, detail, stage="exception")

    def record_failure(
        self,
        device: str,
        reason: str,
        detail: str,
        *,
        stage: str,
        disconnect_code: int | None = None,
    ) -> None:
        now = time.time()
        with self._lock:
            self._mark_failure_locked(device, reason, detail, stage, disconnect_code, now)

    def _mark_failure_locked(
        self,
        device: str,
        reason: str,
        detail: str,
        stage: str,
        disconnect_code: int | None,
        timestamp: float,
    ) -> None:
        stats = self._devices.setdefault(device, DeviceStats(name=device))
        stats.status = "failed"
        stats.last_stage = stage
        stats.last_failure = timestamp
        stats.failure_reason = reason
        stats.failure_detail = detail
        stats.disconnect_code = disconnect_code
        stats.total_failures += 1
        self._failure_breakdown[reason] += 1
        self._failed_devices.add(device)
        self._connected.discard(device)
        if RUNNING and not self._connected and self._collapse_time is None:
            self._collapse_time = time.perf_counter() - self._start_ts

    def snapshot(self) -> MetricsSnapshot:
        with self._lock:
            elapsed = max(time.perf_counter() - self._start_ts, 1e-6)
            success_packets = self._total_packets_sent - self._total_packets_failed
            base_devices = max(self._device_count, 1)
            avg_rate = (success_packets / elapsed) / base_devices
            total_mb = self._total_bytes / (1024 * 1024)
            connected = len(self._connected)
            registered = set(self._registered_devices)
            disconnected_set = registered - self._connected
            failed = len(self._failed_devices)
            failed_or_disconnected = len(disconnected_set | self._failed_devices)
            bandwidth_mbps = ((self._total_bytes * 8) / elapsed) / 1_000_000
            devices = [
                DeviceStatusSnapshot(
                    name=name,
                    status=stats.status,
                    last_stage=stats.last_stage,
                    last_seen=stats.last_seen,
                    last_failure=stats.last_failure,
                    failure_reason=stats.failure_reason,
                    failure_detail=stats.failure_detail,
                    disconnect_code=stats.disconnect_code,
                )
                for name, stats in sorted(self._devices.items())
            ]
            failure_breakdown = dict(self._failure_breakdown)
            collapse_time = self._collapse_time
        return MetricsSnapshot(
            timestamp=time.time(),
            device_count=self._device_count,
            interval_sec=self._interval,
            connected_devices=connected,
            disconnected_devices=len(disconnected_set),
            failed_devices=failed,
            failed_or_disconnected=failed_or_disconnected,
            active_channels=connected,
            total_packets_sent=self._total_packets_sent,
            total_packets_failed=self._total_packets_failed,
            total_volume_mb=total_mb,
            avg_send_rate_per_device=avg_rate,
            bandwidth_mbps=bandwidth_mbps,
            collapse_time=collapse_time,
            failure_breakdown=failure_breakdown,
            devices=devices,
        )

    def summary_lines(self) -> list[str]:
        snap = self.snapshot()
        lines = [
            f"  Inicio de simulacion: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(self._start_wall))}",
            f"  Dispositivos solicitados: {snap.device_count}",
            f"  Dispositivos conectados: {snap.connected_devices}",
            f"  Dispositivos desconectados: {snap.disconnected_devices}",
            f"  Dispositivos con fallas: {snap.failed_devices}",
            f"  Fallidos o desconectados: {snap.failed_or_disconnected}",
            f"  Canales activos: {snap.active_channels}",
            f"  Paquetes enviados: {snap.total_packets_sent}",
            f"  Paquetes fallidos: {snap.total_packets_failed}",
            f"  Volumen total: {snap.total_volume_mb:.3f} MB",
            f"  Tasa promedio por dispositivo: {snap.avg_send_rate_per_device:.3f} msg/s",
            f"  Ancho de banda: {snap.bandwidth_mbps:.3f} Mbps",
        ]
        if snap.collapse_time is not None:
            lines.append(f"  Tiempo hasta colapso: {snap.collapse_time:.2f} s")
        else:
            lines.append("  Tiempo hasta colapso: no se registro")
        if snap.failure_breakdown:
            lines.append("  Principales causas registradas:")
            for reason, count in sorted(snap.failure_breakdown.items(), key=lambda item: item[1], reverse=True):
                lines.append(f"    - {reason}: {count}")
        else:
            lines.append("  Principales causas registradas: ninguna")
        return lines


class MetricsReporter(threading.Thread):
    """Periodic logger that streams metrics to stdout while the simulator runs."""

    def __init__(self, collector: MetricsCollector, interval: float = 5.0) -> None:
        super().__init__(daemon=True)
        self._collector = collector
        self._interval = interval
        self._stop_event = threading.Event()

    def run(self) -> None:
        while not self._stop_event.wait(self._interval):
            snap = self._collector.snapshot()
            breakdown = ", ".join(
                f"{reason}:{count}"
                for reason, count in sorted(snap.failure_breakdown.items(), key=lambda item: item[1], reverse=True)[:4]
            )
            if not breakdown:
                breakdown = "none"
            print(
                "[METRICS] "
                f"connected={snap.connected_devices} "
                f"disconnected={snap.disconnected_devices} "
                f"failed={snap.failed_devices} "
                f"sent={snap.total_packets_sent} "
                f"failed_packets={snap.total_packets_failed} "
                f"volume_mb={snap.total_volume_mb:.3f} "
                f"bandwidth_mbps={snap.bandwidth_mbps:.3f} "
                f"avg_rate={snap.avg_send_rate_per_device:.3f}/s "
                f"collapse={'{:.2f}s'.format(snap.collapse_time) if snap.collapse_time is not None else 'n/a'} "
                f"top_causes={breakdown}"
            )

    def stop(self) -> None:
        self._stop_event.set()


def create_dashboard_app(metrics: MetricsCollector, refresh_ms: int) -> Flask:
    app = Flask(__name__)

    @app.route("/")
    def index() -> str:
        return render_template_string(DASHBOARD_TEMPLATE, refresh_ms=refresh_ms)

    @app.route("/metrics")
    def metrics_view():
        snap = metrics.snapshot()
        return jsonify(
            {
                "timestamp": snap.timestamp,
                "device_count": snap.device_count,
                "interval_sec": snap.interval_sec,
                "connected_devices": snap.connected_devices,
                "disconnected_devices": snap.disconnected_devices,
                "failed_devices": snap.failed_devices,
                "failed_or_disconnected": snap.failed_or_disconnected,
                "active_channels": snap.active_channels,
                "total_packets_sent": snap.total_packets_sent,
                "total_packets_failed": snap.total_packets_failed,
                "total_volume_mb": snap.total_volume_mb,
                "avg_send_rate_per_device": snap.avg_send_rate_per_device,
                "bandwidth_mbps": snap.bandwidth_mbps,
                "collapse_time": snap.collapse_time,
                "failure_breakdown": snap.failure_breakdown,
                "devices": [asdict(device) for device in snap.devices],
            }
        )

    return app


class DashboardServer(threading.Thread):
    """Background thread that hosts the Flask dashboard without blocking the simulation."""

    def __init__(self, app: Flask, host: str, port: int) -> None:
        super().__init__(daemon=True)
        self._server = make_server(host, port, app)
        self._context = app.app_context()
        self._context.push()

    def run(self) -> None:
        self._server.serve_forever()

    def shutdown(self) -> None:
        self._server.shutdown()


def provision_device_with_retry(
    api: TB,
    name: str,
    label: str,
    dev_type: str,
    profile_id: Optional[str],
    metrics: MetricsCollector,
    *,
    retries: int,
    delay: float,
) -> tuple[str, str]:
    """Create or fetch a device and return (device_id, token) with retries."""
    last_exc: Optional[Exception] = None
    attempt_delay = max(delay, 0.5)
    for attempt in range(1, retries + 1):
        try:
            device = api.save_device(
                name,
                label=label,
                dev_type=dev_type,
                profile_id=profile_id,
            )
            dev_id = device["id"]["id"]
            token = api.token(dev_id)
            metrics.register_device(name)
            return dev_id, token
        except (TBError, requests.RequestException) as exc:
            last_exc = exc
            print(f"[WARN] Provision intento {attempt} para '{name}' fallo: {exc}")
            if attempt < retries:
                time.sleep(attempt_delay)
                attempt_delay = min(attempt_delay * 1.5, 30.0)
    raise TBError(f"No se pudo provisionar '{name}' tras {retries} intentos: {last_exc}")


def stop(_sig, _frame) -> None:
    global RUNNING
    RUNNING = False
    print("\n[INFO] Senal recibida: cerrando...")


signal.signal(signal.SIGINT, stop)
signal.signal(signal.SIGTERM, stop)


def fail(msg: str, code: int = 1) -> None:
    print(f"[ERROR] {msg}")
    raise SystemExit(code)


class StartCoordinator:
    """Distribute a shared start timestamp so every device fires on the same cadence."""

    def __init__(self, lead_time: float) -> None:
        self._event = threading.Event()
        self._start_time = 0.0
        self._lead_time = max(lead_time, 0.05)

    def release(self) -> float:
        self._start_time = time.perf_counter() + self._lead_time
        self._event.set()
        return self._start_time

    def wait(self) -> float:
        self._event.wait()
        return self._start_time


class SimLoop(threading.Thread):
    def __init__(
        self,
        name: str,
        token: str,
        barrier: Barrier,
        metrics: MetricsCollector,
        start_coordinator: StartCoordinator,
        config: SimulatorConfig,
    ) -> None:
        super().__init__(daemon=True, name=f"SimLoop-{name}")
        self.name = name
        self.token = token
        self.barrier = barrier
        self.metrics = metrics
        self.start_coordinator = start_coordinator
        self.config = config
        suffix = "".join(random.choices(string.ascii_letters + string.digits, k=6))
        self.client = mqtt.Client(client_id=f"sim-{name}-{suffix}", clean_session=True)
        self.client.username_pw_set(self.token)
        if self.config.mqtt_tls:
            try:
                self.client.tls_set()
            except Exception:
                pass
        self.client.on_connect = self.on_connect
        self.client.on_disconnect = self.on_disconnect
        self.client.max_queued_messages_set(0)

    def on_connect(self, _client, _userdata, _flags, rc) -> None:
        rc_val = int(rc)
        if rc_val == 0:
            print(f"[MQTT] {self.name} conectado")
            self.metrics.record_connect(self.name)
        else:
            reason, detail = classify_failure("connect", rc=rc_val)
            print(f"[MQTT] {self.name} error de conexion rc={rc_val} ({detail})")
            self.metrics.record_connect_failure(self.name, rc_val)

    def on_disconnect(self, _client, _userdata, rc) -> None:
        rc_val = int(rc)
        print(f"[MQTT] {self.name} desconectado rc={rc_val}")
        self.metrics.record_disconnect(self.name, rc_val)

    def run(self) -> None:
        try:
            rc = self.client.connect(self.config.mqtt_host, self.config.mqtt_port, keepalive=60)
            if int(rc) != int(mqtt.MQTT_ERR_SUCCESS):
                reason, detail = classify_failure("connect", rc=rc)
                print(f"[ERR] {self.name}: fallo en connect ({detail})")
                self.metrics.record_connect_failure(self.name, int(rc))
                return
            self.client.loop_start()
            try:
                self.barrier.wait()
            except BrokenBarrierError:
                return
            start_time = self.start_coordinator.wait()
            next_tick = start_time
            tick_index = 0
            while RUNNING:
                wait_time = next_tick - time.perf_counter()
                if wait_time > 0:
                    time.sleep(wait_time)
                payload = self.payload()
                payload_json = json.dumps(payload)
                info = self.client.publish("v1/devices/me/telemetry", payload_json, qos=1)
                payload_size = len(payload_json.encode("utf-8"))
                success = int(info.rc) == int(mqtt.MQTT_ERR_SUCCESS)
                self.metrics.record_publish(self.name, payload_size, success, rc=int(info.rc))
                tick_index += 1
                next_tick = start_time + (tick_index + 1) * self.config.publish_interval
        except Exception as exc:  # noqa: BLE001
            print(f"[ERR] {self.name}: {exc}")
            self.metrics.record_exception(self.name, exc)
        finally:
            try:
                self.client.loop_stop()
                self.client.disconnect()
            except Exception:
                pass

    def payload(self) -> Dict:
        return {
            "temperature": round(random.uniform(20.0, 30.0), 2),
            "humidity": random.randint(35, 65),
            "battery": round(random.uniform(3.6, 4.2), 2),
            "status": random.choice(["ok", "ok", "ok", "warn"]),
            "device": self.name,
        }


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def truncate_tokens_file(tokens: Dict[str, str]) -> Dict[str, str]:
    """Keep only the first 500 tokens to avoid oversized local state."""
    limited = dict(list(tokens.items())[:500])
    if len(limited) < len(tokens):
        print(f"[INFO] tokens.json recortado a {len(limited)} dispositivos (500 maximos).")
    return limited


def main() -> None:
    config = SimulatorConfig.load()
    if not config.tb_url or not config.tb_username or not config.tb_password:
        fail("Faltan TB_URL/TB_USERNAME/TB_PASSWORD en .env")
    if config.device_count <= 0:
        fail("DEVICE_COUNT debe ser mayor a 0 (maximo 500)")
    requested = int(os.getenv("DEVICE_COUNT", str(config.device_count)))
    if requested > config.device_count:
        print(f"[WARN] DEVICE_COUNT solicitado mayor a 500; se ejecutara con {config.device_count}")

    ensure_dir(DATA_DIR)
    if TOKENS_FILE.exists():
        try:
            trimmed = truncate_tokens_file(json.loads(TOKENS_FILE.read_text(encoding="utf-8")))
            TOKENS_FILE.write_text(json.dumps(trimmed, ensure_ascii=False, indent=2), encoding="utf-8")
        except json.JSONDecodeError:
            print("[WARN] tokens.json corrupto; se generara uno nuevo.")

    barrier = Barrier(config.device_count + 1)
    start_coordinator = StartCoordinator(config.start_lead_time)
    metrics = MetricsCollector(config.device_count, config.publish_interval)
    reporter = MetricsReporter(metrics)
    dashboard_app = create_dashboard_app(metrics, config.dashboard_refresh_ms)
    dashboard = DashboardServer(dashboard_app, config.dashboard_host, config.dashboard_port)
    dashboard.start()
    print(f"[INFO] Dashboard disponible en http://{config.dashboard_host}:{config.dashboard_port}")

    workers: list[SimLoop] = []
    tokens_map: dict[str, str] = {}

    try:
        with TB(config.tb_url, config.tb_username, config.tb_password) as api:
            api.login()
            profile_id = config.device_profile_id or api.default_profile()
            if profile_id:
                msg = "fijado" if config.device_profile_id else "por defecto"
                print(f"[INFO] Device Profile {msg}: {profile_id}")
            else:
                print("[INFO] No se encontro Device Profile por defecto")

            for idx in range(1, config.device_count + 1):
                name = f"{config.device_prefix}-{idx:03d}"
                print(f"[INFO] Preparando '{name}'...")
                dev_id, token = provision_device_with_retry(
                    api,
                    name,
                    config.device_label,
                    config.device_type,
                    profile_id,
                    metrics,
                    retries=config.provision_retries,
                    delay=config.provision_retry_delay,
                )
                tokens_map[name] = token
                worker = SimLoop(name, token, barrier, metrics, start_coordinator, config)
                worker.start()
                workers.append(worker)
                api.set_attrs(
                    dev_id,
                    {
                        "batch": "sim-" + time.strftime("%Y%m%d"),
                        "group": config.device_prefix,
                        "index": idx,
                    },
                )
                time.sleep(0.01)
    except (TBError, requests.RequestException) as exc:
        barrier.abort()
        fail(str(exc))

    TOKENS_FILE.write_text(json.dumps(tokens_map, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[INFO] tokens.json actualizado con {len(tokens_map)} dispositivos.")

    print(
        f"[INFO] Lanzados {len(workers)} workers. Enviando telemetria a "
        f"{config.mqtt_host}:{config.mqtt_port} (TLS={config.mqtt_tls})"
    )

    reporter.start()

    try:
        start_index = barrier.wait()
        if start_index == 0:
            start_time = start_coordinator.release()
            wall_time = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time() + config.start_lead_time))
            print(f"[INFO] Primera rafaga sincronizada en {wall_time} (lead {config.start_lead_time:.2f}s).")
        else:
            start_coordinator.wait()
    except BrokenBarrierError:
        print("[WARN] No todos los workers se sincronizaron; revisa los logs de MQTT.")

    try:
        while RUNNING:
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        if reporter.is_alive():
            reporter.stop()
            reporter.join(timeout=2)
        if dashboard.is_alive():
            dashboard.shutdown()
            dashboard.join(timeout=2)
        print("[INFO] Deteniendo workers...")
        for worker in workers:
            worker.join(timeout=2)
        print("[OK] Simulacion finalizada.")
        print("[METRICS] Resumen final:")
        for line in metrics.summary_lines():
            print(line)


if __name__ == "__main__":
    main()
