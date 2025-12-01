#!/usr/bin/env python3
"""Flask dashboard for ThingsBoard telemetry simulator metrics."""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Optional

from flask import Flask, jsonify, render_template_string, request
from werkzeug.serving import make_server


DASHBOARD_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <title>Telemetry Metrics Dashboard</title>
  <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
  <style>
    body { font-family: Arial, sans-serif; margin: 0; padding: 0 1.5rem 2rem; background: #101822; color: #f2f4f8; }
    h1 { margin-top: 1.5rem; }
    .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 1rem; margin-top: 1rem; }
    .card { background: #1b2533; border-radius: 8px; padding: 1rem; box-shadow: 0 4px 12px rgba(0, 0, 0, 0.25); }
    .card h2 { margin-top: 0; font-size: 1.1rem; }
    table { width: 100%; border-collapse: collapse; font-size: 0.9rem; }
    th, td { padding: 0.35rem 0.5rem; text-align: left; border-bottom: 1px solid rgba(255, 255, 255, 0.08); }
    th { font-weight: 600; }
    .status { display: flex; gap: 1rem; flex-wrap: wrap; margin: 0.5rem 0 0; align-items: center; }
    .status span { font-size: 0.95rem; }
    .pill { display: inline-block; padding: 0.2rem 0.65rem; border-radius: 999px; background: rgba(79,209,197,0.2); color: #4fd1c5; font-size: 0.85rem; font-weight: 600; }
    .chart-container { background: #1b2533; border-radius: 8px; padding: 1rem; margin-top: 1rem; box-shadow: 0 4px 12px rgba(0, 0, 0, 0.25); }
    .chart-container canvas { width: 100% !important; max-height: 320px; }
    footer { margin-top: 2rem; font-size: 0.75rem; color: #9ca6b4; }
    .control-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 0.75rem; margin-top: 0.5rem; }
    .control-grid label { display: flex; flex-direction: column; font-size: 0.9rem; color: #c3ccd8; gap: 0.25rem; }
    .control-grid input, .control-grid select { padding: 0.4rem 0.5rem; border-radius: 6px; border: 1px solid rgba(255,255,255,0.08); background: #111827; color: #f2f4f8; }
    .actions { display: flex; gap: 0.5rem; margin-top: 0.75rem; flex-wrap: wrap; }
    .btn { border: none; border-radius: 6px; padding: 0.5rem 0.9rem; font-weight: 600; cursor: pointer; }
    .btn.primary { background: #4fd1c5; color: #0b1724; }
    .btn.secondary { background: #2d3748; color: #f2f4f8; }
    .btn:disabled { opacity: 0.5; cursor: not-allowed; }
    .small { font-size: 0.85rem; color: #9ca6b4; }
    .message { margin-top: 0.4rem; min-height: 1.2rem; font-size: 0.9rem; }
    .message.error { color: #fc8181; }
    .message.ok { color: #4fd1c5; }
  </style>
</head>
<body>
  <h1>Telemetry Metrics Dashboard</h1>
  <div class="status">
    <span>Elapsed: <strong id="elapsed">--</strong></span>
    <span>Messages/sec: <strong id="mps">--</strong></span>
    <span>Bandwidth (Mbps): <strong id="bandwidth">--</strong></span>
    <span>Channels in use: <strong id="channels">--</strong></span>
    <span>Success rate: <strong id="success-pill" class="pill">--</strong></span>
  </div>

  <div class="card">
    <h2>Simulation control</h2>
    <div class="control-grid">
      <label>Devices<input type="number" id="input-devices" min="1" step="1" placeholder="e.g. 1000" /></label>
      <label>Duration (s)<input type="number" id="input-duration" min="1" step="1" placeholder="e.g. 120" /></label>
      <label>Interval (s)<input type="number" id="input-interval" min="0.1" step="0.1" placeholder="e.g. 3" /></label>
      <label>QoS
        <select id="input-qos">
          <option value="">--</option>
          <option value="0">0</option>
          <option value="1" selected>1</option>
          <option value="2">2</option>
        </select>
      </label>
    </div>
    <div class="actions">
      <button class="btn primary" id="btn-start" onclick="startRun()">Start</button>
      <button class="btn secondary" id="btn-stop" onclick="stopRun()">Stop</button>
      <span class="small" id="run-meta"></span>
    </div>
    <div id="run-status" class="small">Status: --</div>
    <div id="run-message" class="message"></div>
  </div>

  <div class="grid">
    <div class="card">
      <h2>Connections</h2>
      <table>
        <tr><th>Total devices</th><td id="total-devices">--</td></tr>
        <tr><th>Connected now</th><td id="connected">--</td></tr>
        <tr><th>Peak connected</th><td id="peak-connected">--</td></tr>
        <tr><th>Failed devices</th><td id="failed-devices">--</td></tr>
        <tr><th>Collapse time</th><td id="collapse-time">--</td></tr>
        <tr><th>Collapse reason</th><td id="collapse-reason">--</td></tr>
      </table>
    </div>
    <div class="card">
      <h2>Traffic</h2>
      <table>
        <tr><th>Successful publishes</th><td id="packets-sent">--</td></tr>
        <tr><th>Failed publishes</th><td id="packets-failed">--</td></tr>
        <tr><th>Total volume (MB)</th><td id="volume">--</td></tr>
        <tr><th>Avg msgs/device</th><td id="avg-msgs-device">--</td></tr>
        <tr><th>Avg msg rate/device</th><td id="avg-rate-device">--</td></tr>
      </table>
    </div>
    <div class="card">
      <h2>Reliability</h2>
      <table>
        <tr><th>Success rate</th><td id="success-rate">--</td></tr>
        <tr><th>Failed devices</th><td id="failed-devices-secondary">--</td></tr>
        <tr><th>Disconnect events</th><td id="disconnect-events">--</td></tr>
        <tr><th>Messages/sec</th><td id="latency-mps">--</td></tr>
        <tr><th>Bandwidth (Mbps)</th><td id="latency-bw">--</td></tr>
      </table>
    </div>
    <div class="card">
      <h2>Latency (ms)</h2>
      <table>
        <tr><th>Average</th><td id="latency-avg">--</td></tr>
        <tr><th>P50</th><td id="latency-p50">--</td></tr>
        <tr><th>P95</th><td id="latency-p95">--</td></tr>
        <tr><th>P99</th><td id="latency-p99">--</td></tr>
      </table>
    </div>
    <div class="card">
      <h2>Disconnect causes</h2>
      <table id="disconnect-table">
        <tr><th>Reason</th><th>Count</th></tr>
      </table>
    </div>
  </div>

  <div class="chart-container">
    <h2>Messages per Second</h2>
    <canvas id="messagesChart"></canvas>
  </div>

  <div class="chart-container">
    <h2>Latency Trends (ms)</h2>
    <canvas id="latencyChart"></canvas>
  </div>

  <div class="chart-container">
    <h2>Bandwidth (Mbps)</h2>
    <canvas id="bandwidthChart"></canvas>
  </div>

  <div class="card">
    <h2>Top devices (by messages sent)</h2>
    <table id="devices-table">
      <tr><th>Device</th><th>Messages</th><th>Failed</th></tr>
    </table>
  </div>

  <footer>Refresh interval: {{ refresh_interval }} ms &nbsp;|&nbsp; Port: {{ port }} | Profile target: {{ profile_id }}</footer>

  <script>
    const refreshInterval = {{ refresh_interval }};
    const runStatusInterval = 5000;
    const messagesCtx = document.getElementById('messagesChart').getContext('2d');
    const bandwidthCtx = document.getElementById('bandwidthChart').getContext('2d');
    const latencyCtx = document.getElementById('latencyChart').getContext('2d');
    const maxPoints = 60;

    const chartConfig = (label, color) => ({
      type: 'line',
      data: { labels: [], datasets: [{ label, borderColor: color, backgroundColor: color, tension: 0.2, data: [], fill: false }] },
      options: {
        animation: false,
        responsive: true,
        maintainAspectRatio: false,
        scales: {
          x: { ticks: { color: '#9ca6b4' }, grid: { color: 'rgba(255,255,255,0.05)' } },
          y: { ticks: { color: '#9ca6b4' }, grid: { color: 'rgba(255,255,255,0.05)' }, beginAtZero: true }
        },
        plugins: {
          legend: { labels: { color: '#f2f4f8' } }
        }
      }
    });

    const messagesChart = new Chart(messagesCtx, chartConfig('Messages/s', '#4fd1c5'));
    const bandwidthChart = new Chart(bandwidthCtx, chartConfig('Bandwidth Mbps', '#f6ad55'));
    const latencyChart = new Chart(latencyCtx, {
      type: 'line',
      data: {
        labels: [],
        datasets: [
          { label: 'Avg', borderColor: '#63b3ed', backgroundColor: '#63b3ed', tension: 0.2, data: [], fill: false },
          { label: 'P95', borderColor: '#f6ad55', backgroundColor: '#f6ad55', tension: 0.2, data: [], fill: false },
          { label: 'P99', borderColor: '#fc8181', backgroundColor: '#fc8181', tension: 0.2, data: [], fill: false }
        ]
      },
      options: {
        animation: false,
        responsive: true,
        maintainAspectRatio: false,
        scales: {
          x: { ticks: { color: '#9ca6b4' }, grid: { color: 'rgba(255,255,255,0.05)' } },
          y: { ticks: { color: '#9ca6b4' }, grid: { color: 'rgba(255,255,255,0.05)' }, beginAtZero: true }
        },
        plugins: {
          legend: { labels: { color: '#f2f4f8' } }
        }
      }
    });

    async function fetchMetrics() {
      try {
        const response = await fetch('/api/metrics');
        if (!response.ok) throw new Error('Network response was not ok');
        const payload = await response.json();
        updateDashboard(payload);
      } catch (err) {
        console.error('Metrics fetch failed:', err);
      }
    }

    async function fetchRunStatus() {
      try {
        const response = await fetch('/api/run/status');
        if (!response.ok) throw new Error('Status response not ok');
        const payload = await response.json();
        updateRunStatus(payload);
      } catch (err) {
        console.error('Run status fetch failed:', err);
      }
    }

    async function startRun() {
      setRunMessage('', false);
      const devices = document.getElementById('input-devices').value;
      const duration = document.getElementById('input-duration').value;
      const interval = document.getElementById('input-interval').value;
      const qos = document.getElementById('input-qos').value;
      const body = {};
      if (devices) body.device_count = Number(devices);
      if (duration) body.duration = Number(duration);
      if (interval) body.interval = Number(interval);
      if (qos) body.qos = Number(qos);
      try {
        const response = await fetch('/api/run/start', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body),
        });
        const payload = await response.json();
        if (!response.ok) {
          setRunMessage(payload.error || 'No se pudo iniciar la simulacion', true);
        } else {
          setRunMessage('Simulacion iniciada', false);
          updateRunStatus(payload);
        }
      } catch (err) {
        console.error('Run start failed:', err);
        setRunMessage('Error al iniciar la simulacion', true);
      }
    }

    async function stopRun() {
      setRunMessage('', false);
      try {
        const response = await fetch('/api/run/stop', { method: 'POST' });
        const payload = await response.json();
        if (!response.ok) {
          setRunMessage(payload.error || 'No se pudo detener la simulacion', true);
        } else {
          setRunMessage('Simulacion detenida', false);
          updateRunStatus(payload);
        }
      } catch (err) {
        console.error('Run stop failed:', err);
        setRunMessage('Error al detener la simulacion', true);
      }
    }

    function updateDashboard(data) {
      const metrics = data.metrics || {};
      const elapsedSeconds = metrics.elapsed_seconds ?? 0;
      const success = metrics.successful_publishes ?? 0;
      const failed = metrics.failed_publishes ?? 0;
      const totalMsgs = success + failed;
      const successRate = totalMsgs > 0 ? (success / totalMsgs) * 100 : null;
      const bandwidth = metrics.bandwidth_mbps ?? 0;
      const disconnectCauses = metrics.disconnect_causes || {};
      const disconnectEvents = Object.values(disconnectCauses).reduce((acc, value) => acc + Number(value || 0), 0);

      document.getElementById('elapsed').innerText = formatSeconds(elapsedSeconds);
      document.getElementById('mps').innerText = metrics.messages_per_second !== undefined ? metrics.messages_per_second.toFixed(3) : '--';
      document.getElementById('bandwidth').innerText = bandwidth.toFixed(4);
      document.getElementById('channels').innerText = metrics.channels_in_use ?? '--';
      document.getElementById('success-pill').innerText = successRate !== null ? successRate.toFixed(1) + '%' : '--';

      document.getElementById('total-devices').innerText = metrics.total_devices ?? '--';
      document.getElementById('connected').innerText = metrics.connected_devices ?? '--';
      document.getElementById('peak-connected').innerText = metrics.peak_connected_devices ?? '--';
      document.getElementById('failed-devices').innerText = metrics.failed_devices ?? '--';
      document.getElementById('collapse-time').innerText = metrics.collapse_time_seconds != null ? metrics.collapse_time_seconds.toFixed(1) + ' s' : 'N/A';
      document.getElementById('collapse-reason').innerText = metrics.collapse_reason ?? 'N/A';

      document.getElementById('packets-sent').innerText = success;
      document.getElementById('packets-failed').innerText = failed;
      document.getElementById('volume').innerText = metrics.data_volume_mb != null ? metrics.data_volume_mb.toFixed(3) : '--';
      document.getElementById('avg-msgs-device').innerText = metrics.avg_messages_per_device != null ? metrics.avg_messages_per_device.toFixed(2) : '--';
      document.getElementById('avg-rate-device').innerText = metrics.avg_send_rate_per_device != null ? metrics.avg_send_rate_per_device.toFixed(3) : '--';

      document.getElementById('success-rate').innerText = successRate !== null ? successRate.toFixed(2) + '%' : '--';
      document.getElementById('failed-devices-secondary').innerText = metrics.failed_devices ?? '--';
      document.getElementById('disconnect-events').innerText = disconnectEvents;
      document.getElementById('latency-mps').innerText = metrics.messages_per_second != null ? metrics.messages_per_second.toFixed(3) : '--';
      document.getElementById('latency-bw').innerText = bandwidth.toFixed(4);

      document.getElementById('latency-avg').innerText = metrics.avg_latency_ms != null ? metrics.avg_latency_ms.toFixed(3) : '--';
      document.getElementById('latency-p50').innerText = metrics.p50_latency_ms != null ? metrics.p50_latency_ms.toFixed(3) : '--';
      document.getElementById('latency-p95').innerText = metrics.p95_latency_ms != null ? metrics.p95_latency_ms.toFixed(3) : '--';
      document.getElementById('latency-p99').innerText = metrics.p99_latency_ms != null ? metrics.p99_latency_ms.toFixed(3) : '--';

      updateTable('disconnect-table', disconnectCauses, ['Reason', 'Count']);
      updateDeviceTable(data.devices || []);
      pushChartPoint(messagesChart, metrics.messages_per_second ?? 0);
      pushChartPoint(bandwidthChart, bandwidth);
      pushLatencyPoint(latencyChart, metrics);
    }

    function updateRunStatus(data) {
      const statusEl = document.getElementById('run-status');
      const metaEl = document.getElementById('run-meta');
      const startBtn = document.getElementById('btn-start');
      const stopBtn = document.getElementById('btn-stop');
      const running = data.running === true;
      statusEl.innerText = `Status: ${running ? 'running' : 'idle'}${data.pid ? ' (PID ' + data.pid + ')' : ''}`;
      const startedAt = data.started_at ? new Date(data.started_at * 1000).toLocaleTimeString() : '--';
      metaEl.innerText = running ? `Started: ${startedAt}` : '';
      startBtn.disabled = running;
      stopBtn.disabled = !running;
      if (data.last_error) {
        setRunMessage(data.last_error, true);
      }
    }

    function setRunMessage(msg, isError) {
      const el = document.getElementById('run-message');
      el.innerText = msg || '';
      el.classList.remove('ok', 'error');
      if (msg) {
        el.classList.add(isError ? 'error' : 'ok');
      }
    }

    function updateTable(tableId, data, headers) {
      const table = document.getElementById(tableId);
      while (table.rows.length > 1) table.deleteRow(1);
      const entries = Array.isArray(data) ? data : Object.entries(data);
      entries.forEach(entry => {
        const [key, value] = Array.isArray(entry) ? entry : [entry.device, entry.messages];
        const tr = table.insertRow();
        tr.insertCell().innerText = key;
        tr.insertCell().innerText = value;
      });
    }

    function updateDeviceTable(devices) {
      const table = document.getElementById('devices-table');
      while (table.rows.length > 1) table.deleteRow(1);
      devices.slice(0, 10).forEach(item => {
        const tr = table.insertRow();
        tr.insertCell().innerText = item.device;
        tr.insertCell().innerText = item.messages;
        tr.insertCell().innerText = item.failed_messages;
      });
    }

    function pushChartPoint(chart, value) {
      const ts = new Date().toLocaleTimeString();
      chart.data.labels.push(ts);
      chart.data.datasets[0].data.push(value);
      if (chart.data.labels.length > maxPoints) {
        chart.data.labels.shift();
        chart.data.datasets[0].data.shift();
      }
      chart.update();
    }

    function pushLatencyPoint(chart, metrics) {
      const ts = new Date().toLocaleTimeString();
      chart.data.labels.push(ts);
      chart.data.datasets[0].data.push(metrics.avg_latency_ms ?? 0);
      chart.data.datasets[1].data.push(metrics.p95_latency_ms ?? 0);
      chart.data.datasets[2].data.push(metrics.p99_latency_ms ?? 0);
      if (chart.data.labels.length > maxPoints) {
        chart.data.labels.shift();
        chart.data.datasets.forEach(dataset => dataset.data.shift());
      }
      chart.update();
    }

    function formatSeconds(seconds) {
      const safe = Number.isFinite(seconds) ? seconds : 0;
      const total = Math.floor(safe);
      const h = Math.floor(total / 3600);
      const m = Math.floor((total % 3600) / 60);
      const s = total % 60;
      return [h, m, s].map(v => String(v).padStart(2, '0')).join(':');
    }

    fetchMetrics();
    fetchRunStatus();
    setInterval(fetchMetrics, refreshInterval);
    setInterval(fetchRunStatus, runStatusInterval);
  </script>
</body>
</html>
"""

DEFAULT_SIM_SCRIPT = Path(__file__).resolve().parent / "mqtt_stress_async.py"


def _to_int(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _to_float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class SimulationController:
    """Simple orchestrator to start/stop the MQTT stress script."""

    def __init__(self, script_path: Path = DEFAULT_SIM_SCRIPT, python_executable: str | None = None) -> None:
        self.script_path = script_path
        self.python = python_executable or sys.executable
        self.process: Optional[subprocess.Popen] = None
        self.started_at: Optional[float] = None
        self.last_args: dict[str, Any] = {}
        self.last_error: Optional[str] = None

    def running(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def status(self) -> dict[str, Any]:
        return {
            "running": self.running(),
            "pid": self.process.pid if self.process and self.running() else None,
            "started_at": self.started_at,
            "last_args": self.last_args,
            "last_error": self.last_error,
            "return_code": self.process.poll() if self.process else None,
        }

    def start(
        self,
        *,
        device_count: Optional[int],
        duration: Optional[float],
        interval: Optional[float],
        qos: Optional[int],
    ) -> dict[str, Any]:
        if self.running():
            return {"error": "Simulation already running", **self.status()}
        if not self.script_path.exists():
            return {"error": f"Script not found: {self.script_path}"}

        cmd = [self.python, str(self.script_path)]
        if device_count is not None:
            if device_count <= 0:
                return {"error": "device_count debe ser > 0"}
            cmd.extend(["--device-count", str(device_count)])
        if duration is not None:
            if duration <= 0:
                return {"error": "duration debe ser > 0"}
            cmd.extend(["--duration", str(duration)])
        if interval is not None:
            if interval <= 0:
                return {"error": "interval debe ser > 0"}
            cmd.extend(["--interval", str(interval)])
        if qos is not None:
            if qos not in (0, 1, 2):
                return {"error": "qos debe ser 0, 1 o 2"}
            cmd.extend(["--qos", str(qos)])

        env = os.environ.copy()
        try:
            self.process = subprocess.Popen(cmd, env=env)
            self.started_at = time.time()
            self.last_args = {
                "device_count": device_count,
                "duration": duration,
                "interval": interval,
                "qos": qos,
            }
            self.last_error = None
        except Exception as exc:  # noqa: BLE001
            self.process = None
            self.started_at = None
            self.last_error = str(exc)
            return {"error": str(exc)}
        return self.status()

    def stop(self) -> dict[str, Any]:
        if not self.running():
            return {**self.status(), "error": "No hay simulacion en ejecucion"}
        assert self.process is not None
        try:
            if os.name == "nt":
                self.process.terminate()
            else:
                self.process.send_signal(signal.SIGTERM)
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.kill()
        except Exception as exc:  # noqa: BLE001
            self.last_error = str(exc)
            return {"error": str(exc), **self.status()}
        status = self.status()
        self.process = None
        return status


class GlobalMetricsCollector:
    """Collector that merges metrics snapshots from múltiples shards."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._snapshots: dict[str, dict[str, Any]] = {}
        self._devices_by_shard: dict[str, dict[str, dict[str, Any]]] = {}

    def ingest(self, shard_id: str, snapshot: dict[str, Any], devices: list[dict[str, Any]]) -> None:
        shard_key = shard_id or "default"
        with self._lock:
            self._snapshots[shard_key] = snapshot or {}
            device_map: dict[str, dict[str, Any]] = {}
            for item in devices or []:
                device = item.get("device")
                if not device:
                    continue
                device_map[device] = {
                    "device": device,
                    "messages": int(item.get("messages", 0)),
                    "failed_messages": int(item.get("failed_messages", 0)),
                    "bytes": int(item.get("bytes", 0)),
                }
            self._devices_by_shard[shard_key] = device_map

    def summary(self) -> dict[str, Any]:
        with self._lock:
            if not self._snapshots:
                return self._empty_summary()
            total_devices = 0
            total_connected = 0
            total_active = 0
            total_failed_devices = 0
            total_success = 0
            total_fail = 0
            total_bytes = 0
            total_bandwidth = 0.0
            total_messages_per_second = 0.0
            total_volume_mb = 0.0
            total_channels = 0
            total_peak_connected = 0
            weighted_latency = 0.0
            weighted_p50 = 0.0
            weighted_p95 = 0.0
            weighted_p99 = 0.0
            latency_weight = 0.0
            latest_timestamp: Optional[str] = None
            latest_uptime = 0.0
            latest_elapsed = 0.0
            collapse_time: Optional[float] = None
            collapse_reasons: set[str] = set()
            disconnect_counter: dict[str, int] = {}

            for snapshot in self._snapshots.values():
                total_devices += int(snapshot.get("total_devices", 0) or 0)
                total_connected += int(snapshot.get("connected_devices", 0) or 0)
                total_active += int(snapshot.get("active_clients", 0) or 0)
                total_failed_devices += int(snapshot.get("failed_devices", 0) or 0)
                successes = int(snapshot.get("successful_publishes", 0) or 0)
                failures = int(snapshot.get("failed_publishes", 0) or 0)
                total_success += successes
                total_fail += failures
                total_bytes += int(snapshot.get("bytes_sent", 0) or 0)
                total_volume_mb += float(snapshot.get("data_volume_mb", 0.0) or 0.0)
                total_channels += int(snapshot.get("channels_in_use", 0) or 0)
                total_peak_connected += int(snapshot.get("peak_connected_devices", 0) or 0)
                total_messages_per_second += float(snapshot.get("messages_per_second", 0.0) or 0.0)
                total_bandwidth += float(snapshot.get("bandwidth_mbps", 0.0) or 0.0)

                avg_latency = snapshot.get("avg_latency_ms")
                if avg_latency is not None and successes > 0:
                    weighted_latency += float(avg_latency) * successes
                    latency_weight += successes
                    p50 = snapshot.get("p50_latency_ms")
                    p95 = snapshot.get("p95_latency_ms")
                    p99 = snapshot.get("p99_latency_ms")
                    if p50 is not None:
                        weighted_p50 += float(p50) * successes
                    if p95 is not None:
                        weighted_p95 += float(p95) * successes
                    if p99 is not None:
                        weighted_p99 += float(p99) * successes

                ts = snapshot.get("timestamp")
                if ts:
                    if not latest_timestamp or ts > latest_timestamp:
                        latest_timestamp = ts
                uptime = float(snapshot.get("uptime_seconds", 0.0) or 0.0)
                elapsed = float(snapshot.get("elapsed_seconds", 0.0) or 0.0)
                latest_uptime = max(latest_uptime, uptime)
                latest_elapsed = max(latest_elapsed, elapsed)

                collapse_value = snapshot.get("collapse_time_seconds")
                if collapse_value is not None:
                    if collapse_time is None or collapse_value < collapse_time:
                        collapse_time = collapse_value
                reason = snapshot.get("collapse_reason")
                if reason:
                    collapse_reasons.add(str(reason))

                shard_disconnects = snapshot.get("disconnect_causes", {}) or {}
                for cause, count in shard_disconnects.items():
                    disconnect_counter[cause] = disconnect_counter.get(cause, 0) + int(count or 0)

            avg_latency_ms = weighted_latency / latency_weight if latency_weight else None
            p50_latency_ms = weighted_p50 / latency_weight if latency_weight else None
            p95_latency_ms = weighted_p95 / latency_weight if latency_weight else None
            p99_latency_ms = weighted_p99 / latency_weight if latency_weight else None
            avg_messages_per_device = (
                total_success / total_devices if total_devices else 0.0
            )
            avg_send_rate_per_device = (
                (total_success / latest_elapsed) / total_devices
                if latest_elapsed > 0 and total_devices > 0
                else 0.0
            )

            return {
                "timestamp": latest_timestamp,
                "uptime_seconds": latest_uptime,
                "elapsed_seconds": latest_elapsed,
                "total_devices": total_devices,
                "active_clients": total_active,
                "connected_devices": total_connected,
                "peak_connected_devices": total_peak_connected,
                "failed_devices": total_failed_devices,
                "successful_publishes": total_success,
                "failed_publishes": total_fail,
                "avg_latency_ms": avg_latency_ms,
                "p50_latency_ms": p50_latency_ms,
                "p95_latency_ms": p95_latency_ms,
                "p99_latency_ms": p99_latency_ms,
                "messages_per_second": total_messages_per_second,
                "bandwidth_mbps": total_bandwidth,
                "avg_send_rate_per_device": avg_send_rate_per_device,
                "avg_messages_per_device": avg_messages_per_device,
                "channels_in_use": total_channels,
                "bytes_sent": total_bytes,
                "data_volume_mb": total_volume_mb,
                "collapse_time_seconds": collapse_time,
                "collapse_reason": ", ".join(sorted(collapse_reasons)) if collapse_reasons else None,
                "disconnect_causes": disconnect_counter,
            }

    def _empty_summary(self) -> dict[str, Any]:
        return {
            "timestamp": None,
            "uptime_seconds": 0.0,
            "elapsed_seconds": 0.0,
            "total_devices": 0,
            "active_clients": 0,
            "connected_devices": 0,
            "peak_connected_devices": 0,
            "failed_devices": 0,
            "successful_publishes": 0,
            "failed_publishes": 0,
            "avg_latency_ms": None,
            "p50_latency_ms": None,
            "p95_latency_ms": None,
            "p99_latency_ms": None,
            "messages_per_second": 0.0,
            "bandwidth_mbps": 0.0,
            "avg_send_rate_per_device": 0.0,
            "avg_messages_per_device": 0.0,
            "channels_in_use": 0,
            "bytes_sent": 0,
            "data_volume_mb": 0.0,
            "collapse_time_seconds": None,
            "collapse_reason": None,
            "disconnect_causes": {},
        }

    def device_breakdown(self, limit: Optional[int] = None) -> list[dict[str, Any]]:
        with self._lock:
            aggregated: dict[str, dict[str, Any]] = {}
            for shard_stats in self._devices_by_shard.values():
                for device, stats in shard_stats.items():
                    total_entry = aggregated.setdefault(
                        device,
                        {
                            "device": device,
                            "messages": 0,
                            "failed_messages": 0,
                            "bytes": 0,
                        },
                    )
                    total_entry["messages"] += stats.get("messages", 0)
                    total_entry["failed_messages"] += stats.get("failed_messages", 0)
                    total_entry["bytes"] += stats.get("bytes", 0)
            devices = sorted(
                aggregated.values(),
                key=lambda item: item["messages"],
                reverse=True,
            )
            if limit is not None:
                devices = devices[:limit]
            return devices


class MetricsServer:
    """Background Flask server that exposes simulator metrics."""

    def __init__(
        self,
        collector,
        *,
        host: str = "127.0.0.1",
        port: int = 5050,
        refresh_interval_ms: int = 2000,
        profile_id: Optional[str] = None,
    ) -> None:
        self.collector = collector
        self.host = host
        self.port = port
        self.refresh_interval_ms = refresh_interval_ms
        self.profile_id = profile_id or "N/A"
        self.sim_controller = SimulationController()
        self._app = Flask(__name__)
        self._server = None
        self._thread: Optional[threading.Thread] = None
        self._setup_routes()

    def _setup_routes(self) -> None:
        app = self._app
        collector = self.collector
        refresh_interval = self.refresh_interval_ms
        profile_id = self.profile_id
        port = self.port
        sim_controller = self.sim_controller

        @app.route("/")
        def dashboard() -> str:
            return render_template_string(
                DASHBOARD_TEMPLATE,
                refresh_interval=refresh_interval,
                port=port,
                profile_id=profile_id,
            )

        @app.get("/api/metrics")
        def metrics() -> Any:
            snapshot = collector.summary()
            devices = collector.device_breakdown(limit=None)
            return jsonify({"metrics": snapshot, "devices": devices})

        @app.get("/api/run/status")
        def run_status() -> Any:
            return jsonify(sim_controller.status())

        @app.post("/api/run/start")
        def run_start() -> Any:
            payload = request.get_json(force=True) or {}
            status = sim_controller.start(
                device_count=_to_int(payload.get("device_count")),
                duration=_to_float(payload.get("duration")),
                interval=_to_float(payload.get("interval")),
                qos=_to_int(payload.get("qos")),
            )
            code = 400 if status.get("error") else 200
            return jsonify(status), code

        @app.post("/api/run/stop")
        def run_stop() -> Any:
            status = sim_controller.stop()
            code = 400 if status.get("error") else 200
            return jsonify(status), code

        if hasattr(collector, "ingest"):
            @app.post("/api/shard")
            def ingest() -> Any:
                payload = request.get_json(force=True) or {}
                shard_id = str(payload.get("shard_id", "unknown"))
                snapshot = payload.get("snapshot") or {}
                devices = payload.get("devices") or []
                collector.ingest(shard_id, snapshot, devices)
                return ("", 204)

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._server = make_server(self.host, self.port, self._app)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
        if self._thread is not None:
            self._thread.join(timeout=5)
        self._server = None
        self._thread = None
