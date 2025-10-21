#!/usr/bin/env python3
"""Flask dashboard for ThingsBoard telemetry simulator metrics."""
from __future__ import annotations

import threading
from typing import Any, Optional

from flask import Flask, jsonify, render_template_string
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
    .status { display: flex; gap: 1rem; flex-wrap: wrap; margin: 0.5rem 0 0; }
    .status span { font-size: 0.95rem; }
    .chart-container { background: #1b2533; border-radius: 8px; padding: 1rem; margin-top: 1rem; box-shadow: 0 4px 12px rgba(0, 0, 0, 0.25); }
    .chart-container canvas { width: 100% !important; max-height: 320px; }
    footer { margin-top: 2rem; font-size: 0.75rem; color: #9ca6b4; }
  </style>
</head>
<body>
  <h1>Telemetry Metrics Dashboard</h1>
  <div class="status">
    <span>Elapsed: <strong id="elapsed">--</strong></span>
    <span>Messages/sec: <strong id="mps">--</strong></span>
    <span>Bandwidth (Mbps): <strong id="bandwidth">--</strong></span>
    <span>Channels in use: <strong id="channels">--</strong></span>
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
        <tr><th>Packets sent</th><td id="packets-sent">--</td></tr>
        <tr><th>Packets failed</th><td id="packets-failed">--</td></tr>
        <tr><th>Total volume (MB)</th><td id="volume">--</td></tr>
        <tr><th>Avg msgs/device</th><td id="avg-msgs-device">--</td></tr>
        <tr><th>Avg msg rate/device</th><td id="avg-rate-device">--</td></tr>
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
    const messagesCtx = document.getElementById('messagesChart').getContext('2d');
    const bandwidthCtx = document.getElementById('bandwidthChart').getContext('2d');
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

    function updateDashboard(data) {
      const metrics = data.metrics || {};
      document.getElementById('elapsed').innerText = formatSeconds(metrics.elapsed_seconds);
      document.getElementById('mps').innerText = metrics.messages_per_second?.toFixed(2) ?? '--';
      document.getElementById('bandwidth').innerText = metrics.bandwidth_mbps?.toFixed(3) ?? '--';
      document.getElementById('channels').innerText = metrics.channels_in_use ?? '--';

      document.getElementById('total-devices').innerText = metrics.total_devices ?? '--';
      document.getElementById('connected').innerText = metrics.connected_devices ?? '--';
      document.getElementById('peak-connected').innerText = metrics.peak_connected_devices ?? '--';
      document.getElementById('failed-devices').innerText = metrics.failed_devices ?? '--';
      document.getElementById('collapse-time').innerText = metrics.collapse_time_seconds !== null && metrics.collapse_time_seconds !== undefined
        ? metrics.collapse_time_seconds.toFixed(1) + ' s'
        : 'N/A';
      document.getElementById('collapse-reason').innerText = metrics.collapse_reason ?? 'N/A';

      document.getElementById('packets-sent').innerText = metrics.messages_sent ?? '--';
      document.getElementById('packets-failed').innerText = metrics.messages_failed ?? '--';
      document.getElementById('volume').innerText = metrics.data_volume_mb ? metrics.data_volume_mb.toFixed(3) : '--';
      document.getElementById('avg-msgs-device').innerText = metrics.avg_messages_per_device ? metrics.avg_messages_per_device.toFixed(2) : '--';
      document.getElementById('avg-rate-device').innerText = metrics.avg_send_rate_per_device ? metrics.avg_send_rate_per_device.toFixed(3) : '--';

      updateTable('disconnect-table', metrics.disconnect_causes || {}, ['Reason', 'Count']);
      updateDeviceTable(data.devices || []);
      pushChartPoint(messagesChart, metrics.messages_per_second ?? 0);
      pushChartPoint(bandwidthChart, metrics.bandwidth_mbps ?? 0);
    }

    function updateTable(tableId, data, headers) {
      const table = document.getElementById(tableId);
      while (table.rows.length > 1) table.deleteRow(1);
      if (Array.isArray(data)) {
        data.forEach(row => {
          const tr = table.insertRow();
          row.forEach(cell => {
            const td = tr.insertCell();
            td.innerText = cell;
          });
        });
      } else {
        Object.entries(data).forEach(([key, value]) => {
          const tr = table.insertRow();
          tr.insertCell().innerText = key;
          tr.insertCell().innerText = value;
        });
      }
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

    function formatSeconds(seconds) {
      const total = Math.floor(seconds);
      const h = Math.floor(total / 3600);
      const m = Math.floor((total % 3600) / 60);
      const s = total % 60;
      return [h, m, s].map(v => String(v).padStart(2, '0')).join(':');
    }

    fetchMetrics();
    setInterval(fetchMetrics, refreshInterval);
  </script>
</body>
</html>
"""


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
