#!/usr/bin/env python3
"""Serve the telemetry metrics dashboard as an independent process."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

from flask import Flask, jsonify, render_template_string

from simcore import METRICS_FILE, SimulatorConfig, ensure_data_dir

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
    .pill.running { background: rgba(34, 197, 94, 0.12); color: #15803d; }
    .pill.idle { background: rgba(107, 114, 128, 0.12); color: #374151; }
    .pill.stopping, .pill.stopped { background: rgba(229, 83, 83, 0.12); color: #B91C1C; }
    .muted { color: var(--muted); font-size: 0.9rem; }
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
      <h2>Estado</h2>
      <p>
        <span id="status-pill" class="pill idle">idle</span>
        <span class="muted" id="status-message">Sin datos disponibles.</span>
      </p>
      <p class="muted">Última actualización: <span id="last-update">n/a</span></p>
    </section>
    <section class="card">
      <canvas id="connectedChart" height="120"></canvas>
    </section>
    <section class="card">
      <h2>Métricas actuales</h2>
      <div class="grid">
        <div class="metric"><h3>Dispositivos solicitados</h3><span id="requested_devices">0</span></div>
        <div class="metric"><h3>Conectados</h3><span id="connected_devices">0</span></div>
        <div class="metric"><h3>Desconectados</h3><span id="disconnected_devices">0</span></div>
        <div class="metric"><h3>Fallidos</h3><span id="failed_devices">0</span></div>
        <div class="metric"><h3>Fallidos o desconectados</h3><span id="failed_or_disconnected">0</span></div>
        <div class="metric"><h3>Paquetes enviados</h3><span id="total_packets_sent">0</span></div>
        <div class="metric"><h3>Paquetes fallidos</h3><span id="total_packets_failed">0</span></div>
        <div class="metric"><h3>Volumen total (MB)</h3><span id="total_volume_mb">0.000</span></div>
        <div class="metric"><h3>Tasa promedio (msg/s)</h3><span id="avg_rate">0.000</span></div>
        <div class="metric"><h3>Ancho de banda (Mbps)</h3><span id="bandwidth">0.000</span></div>
        <div class="metric"><h3>Canales activos</h3><span id="active_channels">0</span></div>
        <div class="metric"><h3>Intervalo (s)</h3><span id="interval_sec">0.0</span></div>
        <div class="metric"><h3>Tiempo de colapso</h3><span id="collapse_time">n/a</span></div>
      </div>
    </section>
    <section class="card">
      <h2>Principales causas de fallo</h2>
      <table>
        <thead>
          <tr>
            <th>Causa</th>
            <th>Ocurrencias</th>
          </tr>
        </thead>
        <tbody id="failure_table"></tbody>
      </table>
      <p class="table-note">Agrupación de errores de MQTT, red y servidor detectados durante la ejecución.</p>
    </section>
    <section class="card">
      <h2>Dispositivos con incidencias</h2>
      <table>
        <thead>
          <tr>
            <th>Dispositivo</th>
            <th>Estado</th>
            <th>Etapa</th>
            <th>Última telemetría</th>
            <th>Último fallo</th>
            <th>Causa</th>
            <th>Detalle</th>
          </tr>
        </thead>
        <tbody id="device_table"></tbody>
      </table>
      <p class="table-note">Se muestran hasta 40 dispositivos con eventos recientes.</p>
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
            label: 'Conectados',
            data: [],
            borderColor: '#2c7be5',
            backgroundColor: 'rgba(44, 123, 229, 0.15)',
            tension: 0.25
          },
          {
            label: 'Fallidos o desconectados',
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
        scales: { y: { beginAtZero: true, ticks: { precision: 0 } } }
      }
    };
    const metricChart = new Chart(ctx, chartConfig);

    function updateStatus(status, message, timestamp) {
      const pill = document.getElementById('status-pill');
      pill.className = 'pill ' + status;
      pill.textContent = status;
      document.getElementById('status-message').textContent = message || '';
      document.getElementById('last-update').textContent = timestamp ? new Date(timestamp * 1000).toLocaleString() : 'n/a';
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
      const fmt = (value, digits = 3) => Number.parseFloat(value).toFixed(digits);
      document.getElementById('requested_devices').textContent = snapshot.device_count;
      document.getElementById('connected_devices').textContent = snapshot.connected_devices;
      document.getElementById('disconnected_devices').textContent = snapshot.disconnected_devices;
      document.getElementById('failed_devices').textContent = snapshot.failed_devices;
      document.getElementById('failed_or_disconnected').textContent = snapshot.failed_or_disconnected;
      document.getElementById('total_packets_sent').textContent = snapshot.total_packets_sent;
      document.getElementById('total_packets_failed').textContent = snapshot.total_packets_failed;
      document.getElementById('total_volume_mb').textContent = fmt(snapshot.total_volume_mb);
      document.getElementById('avg_rate').textContent = fmt(snapshot.avg_send_rate_per_device);
      document.getElementById('bandwidth').textContent = fmt(snapshot.bandwidth_mbps);
      document.getElementById('active_channels').textContent = snapshot.active_channels;
      document.getElementById('interval_sec').textContent = fmt(snapshot.interval_sec, 2);
      document.getElementById('collapse_time').textContent = snapshot.collapse_time === null ? 'n/a' : fmt(snapshot.collapse_time, 2) + ' s';
    }

    function updateFailureTable(snapshot) {
      const body = document.getElementById('failure_table');
      body.innerHTML = '';
      const entries = Object.entries(snapshot.failure_breakdown || {}).sort((a, b) => b[1] - a[1]);
      if (entries.length === 0) {
        const row = document.createElement('tr');
        row.innerHTML = '<td colspan="2" class="muted">Sin registros.</td>';
        body.appendChild(row);
        return;
      }
      entries.forEach(([reason, count]) => {
        const row = document.createElement('tr');
        row.innerHTML = `<td>${reason}</td><td>${count}</td>`;
        body.appendChild(row);
      });
    }

    function updateDeviceTable(snapshot) {
      const body = document.getElementById('device_table');
      body.innerHTML = '';
      const troubled = (snapshot.devices || []).filter(device => device.status !== 'connected');
      troubled.sort((a, b) => (b.last_failure || 0) - (a.last_failure || 0));
      const visible = troubled.slice(0, 40);
      if (visible.length === 0) {
        const row = document.createElement('tr');
        row.innerHTML = '<td colspan="7" class="muted">Todos los dispositivos operan normalmente.</td>';
        body.appendChild(row);
        return;
      }
      visible.forEach(device => {
        const row = document.createElement('tr');
        row.innerHTML = `
          <td>${device.name}</td>
          <td>${device.status}</td>
          <td>${device.last_stage || '-'}</td>
          <td>${device.last_seen ? new Date(device.last_seen * 1000).toLocaleString() : '-'}</td>
          <td>${device.last_failure ? new Date(device.last_failure * 1000).toLocaleString() : '-'}</td>
          <td>${device.failure_reason || '-'}</td>
          <td>${device.failure_detail || '-'}</td>
        `;
        body.appendChild(row);
      });
    }

    async function refreshMetrics() {
      try {
        const response = await fetch('/metrics');
        if (!response.ok) {
          throw new Error('No se pudo obtener métricas');
        }
        const snapshot = await response.json();
        updateStatus(snapshot.status || 'idle', snapshot.message || '', snapshot.timestamp);
        updateChart(snapshot);
        updateMetrics(snapshot);
        updateFailureTable(snapshot);
        updateDeviceTable(snapshot);
      } catch (error) {
        console.error('Error actualizando métricas:', error);
      }
    }

    refreshMetrics();
    setInterval(refreshMetrics, refreshMs);
  </script>
</body>
</html>
"""


def load_metrics() -> Dict[str, Any]:
    ensure_data_dir()
    if not METRICS_FILE.exists():
        return {
            "status": "idle",
            "message": "Sin datos: ejecuta run_telemetry.py para generar métricas.",
            "timestamp": None,
            "device_count": 0,
            "interval_sec": 0.0,
            "connected_devices": 0,
            "disconnected_devices": 0,
            "failed_devices": 0,
            "failed_or_disconnected": 0,
            "active_channels": 0,
            "total_packets_sent": 0,
            "total_packets_failed": 0,
            "total_volume_mb": 0.0,
            "avg_send_rate_per_device": 0.0,
            "bandwidth_mbps": 0.0,
            "collapse_time": None,
            "failure_breakdown": {},
            "devices": [],
        }
    try:
        data = json.loads(METRICS_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        data = {
            "status": "idle",
            "message": "metrics.json corrupto. Ejecuta run_telemetry.py para regenerarlo.",
            "timestamp": None,
            "device_count": 0,
            "interval_sec": 0.0,
            "connected_devices": 0,
            "disconnected_devices": 0,
            "failed_devices": 0,
            "failed_or_disconnected": 0,
            "active_channels": 0,
            "total_packets_sent": 0,
            "total_packets_failed": 0,
            "total_volume_mb": 0.0,
            "avg_send_rate_per_device": 0.0,
            "bandwidth_mbps": 0.0,
            "collapse_time": None,
            "failure_breakdown": {},
            "devices": [],
        }
    data.setdefault("status", "idle")
    data.setdefault("message", "")
    data.setdefault("timestamp", None)
    data.setdefault("failure_breakdown", {})
    data.setdefault("devices", [])
    return data


def create_app(refresh_ms: int) -> Flask:
    app = Flask(__name__)

    @app.route("/")
    def index() -> str:
        return render_template_string(DASHBOARD_TEMPLATE, refresh_ms=refresh_ms)

    @app.route("/metrics")
    def metrics_view():
        return jsonify(load_metrics())

    return app


def main() -> None:
    config = SimulatorConfig.load()
    refresh_ms = max(500, config.dashboard_refresh_ms)
    app = create_app(refresh_ms=refresh_ms)
    print(
        f"[INFO] Dashboard disponible en http://{config.dashboard_host}:{config.dashboard_port} "
        "(Ctrl+C para detener)."
    )
    app.run(host=config.dashboard_host, port=config.dashboard_port, use_reloader=False)


if __name__ == "__main__":
    main()
